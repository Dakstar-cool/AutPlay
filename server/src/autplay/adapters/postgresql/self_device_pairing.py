"""Account-serialized PostgreSQL self-service device pairing."""

from __future__ import annotations

import base64
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from types import TracebackType
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.auth import AccountRole, Principal
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.self_device_pairing import (
    PairingAccount,
    PairingBinding,
    PairingCommand,
    PairingIdentity,
    SelfDevicePairing,
    SelfPairingError,
)
from autplay.ports.self_device_pairing import SelfPairingRepository, SelfPairingUnitOfWork

from .models import AuditEventRow, DeviceRow, UserAccountRow, UserSessionRow
from .models.profile_pairing import DeviceKeyBlockRow, ServerInstanceRow
from .models.self_device_pairing import (
    SelfDevicePairingRow,
    SelfPairingCommandRow,
    SelfPairingRateRow,
)
from .resource_limits import lock_resource_admission, require_device_capacity


class SqlAlchemySelfPairingRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def find(self, ceremony_id: UUID, *, lock: bool = False) -> SelfDevicePairing | None:
        query = select(SelfDevicePairingRow).where(SelfDevicePairingRow.ceremony_id == ceremony_id)
        row = self._s.scalar(
            (query.with_for_update() if lock else query).execution_options(populate_existing=True)
        )
        return (
            None
            if row is None
            else SelfDevicePairing(
                **{item.name: getattr(row, item.name) for item in fields(SelfDevicePairing)}
            )
        )

    def lock_identity(self, server_id: UUID) -> PairingIdentity:
        row = self._s.scalar(
            select(ServerInstanceRow)
            .where(ServerInstanceRow.server_instance_id == server_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise SelfPairingError()
        return PairingIdentity(
            row.server_instance_id,
            row.identity_epoch,
            row.identity_thumbprint_sha256,
            row.api_origin,
            row.stream_origin,
        )

    def lock_account(self, user_id: UUID) -> PairingAccount:
        lock_resource_admission(self._s)
        row = self._s.scalar(
            select(UserAccountRow)
            .where(UserAccountRow.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.status != "ACTIVE" or row.deleted_at is not None:
            raise SelfPairingError()
        return PairingAccount(
            row.user_id, row.authority_generation, row.display_name, AccountRole(row.role)
        )

    def _device(self, user_id: UUID, device_id: UUID) -> DeviceRow:
        row = self._s.scalar(
            select(DeviceRow)
            .where(DeviceRow.user_id == user_id, DeviceRow.device_id == device_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.revoked_at is not None or row.platform != "ANDROID":
            raise SelfPairingError()
        return row

    def actor_family(self, actor: Principal, now: datetime) -> UUID:
        self._device(actor.user_id, actor.device_id)
        session = self._s.scalar(
            select(UserSessionRow)
            .where(UserSessionRow.session_id == actor.session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            session is None
            or session.user_id != actor.user_id
            or session.device_id != actor.device_id
            or session.session_mode != "V2"
            or session.revoked_at is not None
            or session.expires_at <= now
        ):
            raise SelfPairingError()
        return session.family_id or session.session_id

    def require_source(self, ceremony: SelfDevicePairing, now: datetime) -> None:
        self._device(ceremony.user_id, ceremony.source_device_id)
        active = self._s.scalar(
            select(UserSessionRow)
            .where(
                UserSessionRow.user_id == ceremony.user_id,
                UserSessionRow.device_id == ceremony.source_device_id,
                func.coalesce(UserSessionRow.family_id, UserSessionRow.session_id)
                == ceremony.source_family_id,
                UserSessionRow.session_mode == "V2",
                UserSessionRow.revoked_at.is_(None),
                UserSessionRow.expires_at > now,
            )
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if active is None:
            raise SelfPairingError()

    def require_unique(self, field: str, value: UUID, ceremony_id: UUID) -> None:
        if field not in {"start_operation_id", "claim_id", "exchange_id"}:
            raise ValueError("unknown pairing identifier")
        existing = self._s.scalar(
            select(SelfDevicePairingRow.ceremony_id).where(
                getattr(SelfDevicePairingRow, field) == value
            )
        )
        if existing is not None and existing != ceremony_id:
            raise SelfPairingError("operation_conflict")

    def pending_count(self, user_id: UUID, now: datetime) -> int:
        return (
            self._s.scalar(
                select(func.count())
                .select_from(SelfDevicePairingRow)
                .where(
                    SelfDevicePairingRow.user_id == user_id,
                    SelfDevicePairingRow.expires_at > now,
                    SelfDevicePairingRow.state.in_(("OPEN", "CLAIMED", "APPROVED")),
                )
            )
            or 0
        )

    def save(self, ceremony: SelfDevicePairing) -> None:
        row = self._s.get(SelfDevicePairingRow, ceremony.ceremony_id)
        if row is None:
            self._s.add(SelfDevicePairingRow(**asdict(ceremony)))
        else:
            for key, value in asdict(ceremony).items():
                setattr(row, key, value)
        self._s.flush()

    def command(self, operation_id: UUID) -> PairingCommand | None:
        row = self._s.get(SelfPairingCommandRow, operation_id)
        return (
            None
            if row is None
            else PairingCommand(
                **{item.name: getattr(row, item.name) for item in fields(PairingCommand)}
            )
        )

    def save_command(self, command: PairingCommand) -> None:
        self._s.add(SelfPairingCommandRow(**asdict(command)))

    def create_binding(self, ceremony: SelfDevicePairing, now: datetime) -> PairingBinding:
        claim, exchange = ceremony.claim_document, ceremony.exchange_document
        if claim is None or exchange is None:
            raise SelfPairingError()
        thumbprint = bytes.fromhex(claim["device_key_thumbprint_sha256"])
        block = self._s.get(DeviceKeyBlockRow, (ceremony.user_id, thumbprint))
        if block is not None and block.unblocked_at is None:
            raise SelfPairingError()
        active = self._s.scalars(
            select(DeviceRow)
            .where(DeviceRow.user_id == ceremony.user_id, DeviceRow.revoked_at.is_(None))
            .order_by(DeviceRow.device_id)
        ).all()
        if any(row.public_key_thumbprint_sha256 == thumbprint for row in active):
            raise SelfPairingError("self_pairing_key_already_bound")
        try:
            require_device_capacity(self._s, ceremony.user_id)
        except ResourceAdmissionError as error:
            raise SelfPairingError(error.code) from error
        refresh_hash = bytes.fromhex(exchange["next_refresh_token_sha256"])
        if (
            self._s.scalar(
                select(UserSessionRow.session_id).where(
                    UserSessionRow.refresh_token_hash == refresh_hash
                )
            )
            is not None
        ):
            raise SelfPairingError("operation_conflict")
        device_id, session_id = uuid4(), uuid4()
        expiry = now + timedelta(days=90)
        self._s.add(
            DeviceRow(
                device_id=device_id,
                user_id=ceremony.user_id,
                device_name=claim["device_name"],
                platform="ANDROID",
                app_version=claim["app_version"],
                public_key=base64.b64decode(claim["device_public_key_spki_b64"], validate=True),
                public_key_thumbprint_sha256=thumbprint,
                created_at=now,
                updated_at=now,
            )
        )
        self._s.flush()
        self._s.add(
            UserSessionRow(
                session_id=session_id,
                user_id=ceremony.user_id,
                device_id=device_id,
                refresh_token_hash=refresh_hash,
                issued_at=now,
                expires_at=expiry,
                last_rotated_at=now,
                family_id=session_id,
                generation=0,
                session_mode="V2",
            )
        )
        self._s.flush()
        return PairingBinding(device_id, session_id, expiry)

    def active_binding(self, ceremony: SelfDevicePairing, now: datetime) -> PairingBinding:
        if ceremony.result_device_id is None or ceremony.result_session_id is None:
            raise SelfPairingError()
        self._device(ceremony.user_id, ceremony.result_device_id)
        row = self._s.scalar(
            select(UserSessionRow)
            .where(UserSessionRow.session_id == ceremony.result_session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.user_id != ceremony.user_id
            or row.device_id != ceremony.result_device_id
            or row.session_mode != "V2"
            or row.revoked_at is not None
            or row.expires_at <= now
        ):
            raise SelfPairingError()
        return PairingBinding(row.device_id, row.session_id, row.expires_at)

    def audit(
        self, ceremony: SelfDevicePairing, action: str, operation_id: UUID, now: datetime
    ) -> None:
        self._s.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="USER",
                actor_user_id=ceremony.user_id,
                actor_device_id=ceremony.source_device_id,
                action=action,
                target_type="SELF_DEVICE_PAIRING",
                target_id=ceremony.ceremony_id,
                request_id=operation_id,
                reason_code=None,
                metadata_sanitized={"outcome": ceremony.state},
            )
        )

    def rate_gate(self, key: bytes, maximum: int, now: datetime) -> bool:
        self._s.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": int.from_bytes(key[:8], "big", signed=True)},
        )
        row = self._s.get(SelfPairingRateRow, key, with_for_update=True)
        if row is None:
            self._s.add(
                SelfPairingRateRow(key_hash=key, expires_at=now + timedelta(minutes=15), count=1)
            )
            return True
        if row.expires_at <= now:
            row.count, row.expires_at = 1, now + timedelta(minutes=15)
        else:
            row.count = min(maximum + 1, row.count + 1)
        return row.count <= maximum

    def cleanup(self, now: datetime, limit: int) -> int:
        ids = self._s.scalars(
            select(SelfDevicePairingRow.ceremony_id)
            .where(
                or_(
                    (SelfDevicePairingRow.state != "EXCHANGED")
                    & (SelfDevicePairingRow.expires_at < now - timedelta(days=1)),
                    (SelfDevicePairingRow.state == "EXCHANGED")
                    & (SelfDevicePairingRow.receipt_expires_at < now),
                )
            )
            .order_by(SelfDevicePairingRow.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        if ids:
            self._s.execute(
                delete(SelfDevicePairingRow).where(SelfDevicePairingRow.ceremony_id.in_(ids))
            )
        rate_keys = self._s.scalars(
            select(SelfPairingRateRow.key_hash)
            .where(SelfPairingRateRow.expires_at < now)
            .order_by(SelfPairingRateRow.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        if rate_keys:
            self._s.execute(
                delete(SelfPairingRateRow).where(SelfPairingRateRow.key_hash.in_(rate_keys))
            )
        return len(ids)


class SqlAlchemySelfPairingUnitOfWork:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions
        self._session: Session | None = None
        self.pairing: SelfPairingRepository

    def __enter__(self) -> SqlAlchemySelfPairingUnitOfWork:
        self._session = self._sessions()
        self.pairing = SqlAlchemySelfPairingRepository(self._session)
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("self-pairing unit of work inactive")
        self._session.commit()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        if self._session is not None:
            self._session.rollback()
            self._session.close()
            self._session = None


class SqlAlchemySelfPairingUnitOfWorkFactory:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def __call__(self) -> SelfPairingUnitOfWork:
        return SqlAlchemySelfPairingUnitOfWork(self._sessions)
