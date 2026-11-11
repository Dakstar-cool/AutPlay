"""Account-serialized PostgreSQL self-service device pairing."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from types import TracebackType
from typing import cast
from uuid import UUID, uuid4

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.auth import AccountRole, Principal
from autplay.domain.device_compatibility import DeviceCompatibilityError, NegotiationIdentity
from autplay.domain.device_self_v2 import (
    CurrentSourceAuthoritySnapshot,
    SelfCeremonyV2,
    SelfCommandV2,
    SelfV2Error,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.self_device_pairing import (
    PairingAccount,
    PairingBinding,
    PairingCommand,
    PairingIdentity,
    SelfDevicePairing,
    SelfPairingError,
    fresh,
)
from autplay.ports.self_device_pairing import (
    SelfPairingRepository,
    SelfPairingRepositoryV2,
    SelfPairingUnitOfWork,
)

from .device_identifier_reservations import (
    device_identifier_reserved,
    device_v2_identifier_reserved,
)
from .device_platform_evidence import (
    device_has_macos_evidence,
    has_macos_key_evidence,
    has_v2_key_evidence_conflict,
)
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

    def database_now(self) -> datetime:
        with self._s.no_autoflush:
            now = self._s.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise SelfPairingError()
        return now

    def find(self, ceremony_id: UUID, *, lock: bool = False) -> SelfDevicePairing | None:
        query = select(SelfDevicePairingRow).where(SelfDevicePairingRow.ceremony_id == ceremony_id)
        row = self._s.scalar(
            (query.with_for_update() if lock else query).execution_options(populate_existing=True)
        )
        if row is not None and row.contract_version != "v1":
            raise SelfPairingError("operation_conflict")
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
        if device_has_macos_evidence(self._s, row):
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
        now = self.database_now()
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
        now = self.database_now()
        if active is None or active.expires_at <= now:
            raise SelfPairingError()

    def require_unique(self, field: str, value: UUID, ceremony_id: UUID) -> None:
        if field not in {"start_operation_id", "claim_id", "exchange_id"}:
            raise ValueError("unknown pairing identifier")
        if any(
            device_v2_identifier_reserved(self._s, identifier)
            for identifier in (
                value,
                ceremony_id,
            )
        ):
            raise SelfPairingError("operation_conflict")
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
        if row is not None and row.contract_version != getattr(ceremony, "contract_version", "v1"):
            raise SelfPairingError("operation_conflict")
        if row is None:
            self._s.add(SelfDevicePairingRow(**asdict(ceremony)))
        else:
            for key, value in asdict(ceremony).items():
                setattr(row, key, value)
        self._s.flush()

    def command(self, operation_id: UUID) -> PairingCommand | None:
        row = self._s.get(SelfPairingCommandRow, operation_id)
        if row is not None and row.contract_version != "v1":
            raise SelfPairingError("operation_conflict")
        # lock_account holds admission serialization before any decision mutation.
        # An existing command still takes the application's exact v1 replay path.
        if device_v2_identifier_reserved(self._s, operation_id):
            raise SelfPairingError("operation_conflict")
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
        if claim is None:
            raise SelfPairingError()
        if claim is None or exchange is None:
            raise SelfPairingError()
        self._require_v1_binding_ids(exchange)
        thumbprint = bytes.fromhex(claim["device_key_thumbprint_sha256"])
        key = base64.b64decode(claim["device_public_key_spki_b64"], validate=True)
        if claim["platform"] != "ANDROID" or has_macos_key_evidence(
            self._s, thumbprint, public_key=key
        ):
            raise SelfPairingError()
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
        self.require_source(ceremony, now)
        now = self.database_now()
        if ceremony.expires_at <= now:
            raise SelfPairingError()
        fresh(exchange, now)
        device_id, session_id = uuid4(), uuid4()
        expiry = now + timedelta(days=90)
        self._s.add(
            DeviceRow(
                device_id=device_id,
                user_id=ceremony.user_id,
                device_name=claim["device_name"],
                platform="ANDROID",
                app_version=claim["app_version"],
                public_key=key,
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
        if ceremony.exchange_document is None:
            raise SelfPairingError()
        self._require_v1_binding_ids(ceremony.exchange_document)
        claim = ceremony.claim_document
        if (
            claim is None
            or claim["platform"] != "ANDROID"
            or has_macos_key_evidence(
                self._s,
                bytes.fromhex(claim["device_key_thumbprint_sha256"]),
                public_key=base64.b64decode(claim["device_public_key_spki_b64"], validate=True),
            )
        ):
            raise SelfPairingError()
        self._device(ceremony.user_id, ceremony.result_device_id)
        row = self._s.scalar(
            select(UserSessionRow)
            .where(UserSessionRow.session_id == ceremony.result_session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = self.database_now()
        if ceremony.receipt_expires_at is None or ceremony.receipt_expires_at <= now:
            raise SelfPairingError()
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

    def _require_v1_binding_ids(self, exchange: dict[str, object]) -> None:
        # lock_account holds admission serialization for first issuance and replay.
        if any(
            device_v2_identifier_reserved(self._s, UUID(str(exchange[name])))
            for name in (
                "exchange_id",
                "binding_commit_id",
            )
        ):
            raise SelfPairingError("operation_conflict")

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


class SqlAlchemySelfPairingRepositoryV2(SqlAlchemySelfPairingRepository):
    """V2-only snapshots over the shared self tables and admission lock graph."""

    def __init__(
        self, session: Session, identity: Callable[[Session], NegotiationIdentity]
    ) -> None:
        super().__init__(session)
        self._identity = identity
        # Strong references prevent Session.get/expired identity-map reloads after
        # the application's last clock sample. Every save must follow a locator.
        self._ceremonies: dict[UUID, SelfDevicePairingRow | None] = {}

    def lock_identity(self, server_id: UUID) -> PairingIdentity:
        try:
            current = self._identity(self._s)
        except RuntimeError, ValueError:
            # M5 configured-key/persisted-evidence mismatch is unavailable. Keep
            # normalization at the callback boundary, outside unrelated SQL work.
            raise DeviceCompatibilityError() from None
        current.fields()
        if current.server_instance_id != server_id:
            raise SelfV2Error(proof=True)
        return PairingIdentity(
            current.server_instance_id,
            current.identity_epoch,
            current.thumbprint,
            current.api_origin,
            current.stream_origin,
        )

    def find(self, ceremony_id: UUID, *, lock: bool = False) -> SelfCeremonyV2 | None:
        query = select(SelfDevicePairingRow).where(SelfDevicePairingRow.ceremony_id == ceremony_id)
        row = self._s.scalar(
            (query.with_for_update() if lock else query).execution_options(populate_existing=True)
        )
        self._ceremonies[ceremony_id] = row
        if row is None:
            return None
        if row.contract_version != "v2":
            raise SelfPairingError("operation_conflict")
        return SelfCeremonyV2(
            **{item.name: getattr(row, item.name) for item in fields(SelfCeremonyV2)}
        )

    def save(self, ceremony: SelfDevicePairing) -> None:
        if not isinstance(ceremony, SelfCeremonyV2):
            raise SelfPairingError("operation_conflict")
        # No storage read between final validation and the first INSERT/UPDATE.
        row = self._ceremonies[ceremony.ceremony_id]
        if row is None:
            row = SelfDevicePairingRow(**asdict(ceremony))
            self._ceremonies[ceremony.ceremony_id] = row
            self._s.add(row)
        else:
            if row.contract_version != "v2":
                raise SelfPairingError("operation_conflict")
            for key, value in asdict(ceremony).items():
                setattr(row, key, value)
        self._s.flush()

    def _key(self, user_id: UUID, key: bytes, thumb: bytes, platform: str) -> None:
        try:
            parsed = serialization.load_der_public_key(key)
            if (
                not isinstance(parsed, ec.EllipticCurvePublicKey)
                or not isinstance(parsed.curve, ec.SECP256R1)
                or parsed.public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                )
                != key
                or hashlib.sha256(key).digest() != thumb
            ):
                raise ValueError("key")
        except ValueError, TypeError, UnsupportedAlgorithm:
            raise SelfPairingError() from None
        if has_v2_key_evidence_conflict(self._s, thumb, public_key=key, platform=platform):
            raise DeviceCompatibilityError("device_platform_conflict", 409)
        block = self._s.get(DeviceKeyBlockRow, (user_id, thumb), with_for_update=True)
        if block is not None and block.unblocked_at is None:
            raise SelfPairingError()

    def _device(self, user_id: UUID, device_id: UUID) -> DeviceRow:
        row = self._s.scalar(
            select(DeviceRow)
            .where(
                DeviceRow.user_id == user_id,
                DeviceRow.device_id == device_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.revoked_at is not None
            or row.platform not in {"ANDROID", "MACOS"}
            or row.public_key is None
            or row.public_key_thumbprint_sha256 is None
        ):
            raise SelfPairingError()
        self._key(user_id, row.public_key, row.public_key_thumbprint_sha256, row.platform)
        return row

    def source_key(self, actor: Principal) -> tuple[str, bytes, bytes]:
        row = self._device(actor.user_id, actor.device_id)
        return (
            row.platform,
            cast(bytes, row.public_key),
            cast(bytes, row.public_key_thumbprint_sha256),
        )

    def require_source(self, ceremony: SelfDevicePairing, now: datetime) -> None:
        if not isinstance(ceremony, SelfCeremonyV2):
            raise SelfPairingError()
        row = self._device(ceremony.user_id, ceremony.source_device_id)
        if (
            row.platform != ceremony.source_platform
            or row.public_key != ceremony.source_public_key_spki
            or row.public_key_thumbprint_sha256 != ceremony.source_key_thumbprint_sha256
        ):
            raise SelfPairingError()
        super().require_source(ceremony, now)

    def current_source_authority(
        self,
        ceremony: SelfCeremonyV2,
        *,
        actor: Principal | None = None,
    ) -> CurrentSourceAuthoritySnapshot:
        # Admission/account serialization is already held. Re-read fully loaded
        # facts in the existing account -> device -> session order and retain
        # these locks until commit/rollback. No time filtering hides the deadline.
        account = self._s.scalar(
            select(UserAccountRow)
            .where(
                UserAccountRow.user_id == ceremony.user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise SelfPairingError()
        device = self._device(ceremony.user_id, ceremony.source_device_id)
        query = select(UserSessionRow).where(
            UserSessionRow.user_id == ceremony.user_id,
            UserSessionRow.device_id == ceremony.source_device_id,
            func.coalesce(UserSessionRow.family_id, UserSessionRow.session_id)
            == ceremony.source_family_id,
            UserSessionRow.session_mode == "V2",
            UserSessionRow.revoked_at.is_(None),
        )
        if actor is not None:
            query = query.where(UserSessionRow.session_id == actor.session_id)
        # Any active session in the original family suffices without an actor;
        # choosing its latest deadline preserves that existing existential rule.
        session = self._s.scalar(
            query.order_by(
                UserSessionRow.expires_at.desc(),
                UserSessionRow.session_id,
            )
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if session is None:
            raise SelfPairingError()
        return CurrentSourceAuthoritySnapshot(
            account.user_id,
            account.authority_generation,
            account.status,
            account.deleted_at,
            device.device_id,
            device.revoked_at,
            device.platform,
            cast(bytes, device.public_key),
            cast(bytes, device.public_key_thumbprint_sha256),
            session.session_id,
            session.family_id or session.session_id,
            session.session_mode,
            session.revoked_at,
            session.expires_at,
        )

    def require_recipient_key(self, ceremony: SelfCeremonyV2) -> None:
        claim = ceremony.claim_document
        if (
            claim is None
            or ceremony.recipient_platform is None
            or ceremony.recipient_platform != claim["platform"]
        ):
            raise SelfPairingError()
        self._key(
            ceremony.user_id,
            base64.b64decode(claim["device_public_key_spki_b64"], validate=True),
            bytes.fromhex(claim["device_key_thumbprint_sha256"]),
            ceremony.recipient_platform,
        )

    def reserve(
        self, identifiers: dict[str, UUID], *, ceremony_id: UUID, command_id: UUID | None = None
    ) -> None:
        if len(set(identifiers.values())) != len(identifiers):
            raise SelfPairingError("operation_conflict")
        own = self.find(ceremony_id)
        columns = {
            "ceremony_id",
            "start_operation_id",
            "claim_id",
            "approval_operation_id",
            "exchange_id",
            "binding_commit_id",
        }
        for field, value in identifiers.items():
            if field not in columns | {"decision_operation_id"}:
                raise ValueError("unknown self reservation")
            # Decision's approval_operation_id is the same command, not a new reservation.
            if (
                own is not None
                and any(getattr(own, name) == value and name != field for name in columns)
                and not (
                    field == "decision_operation_id"
                    and own.approval_operation_id == value
                    and all(
                        getattr(own, name) != value for name in columns - {"approval_operation_id"}
                    )
                )
            ):
                raise SelfPairingError("operation_conflict")
            if device_identifier_reserved(
                self._s, value, own_self_ceremony_id=ceremony_id, own_self_command_id=command_id
            ):
                raise SelfPairingError("operation_conflict")

    def command(self, operation_id: UUID) -> SelfCommandV2 | None:
        row = self._s.get(SelfPairingCommandRow, operation_id)
        if row is None:
            return None
        if row.contract_version != "v2":
            raise SelfPairingError("operation_conflict")
        return SelfCommandV2(
            **{item.name: getattr(row, item.name) for item in fields(SelfCommandV2)}
        )

    def create_binding(
        self,
        ceremony: SelfDevicePairing,
        now: datetime,
        *,
        before_write: Callable[[], datetime] | None = None,
    ) -> PairingBinding:
        if (
            not isinstance(ceremony, SelfCeremonyV2)
            or ceremony.exchange_document is None
            or ceremony.claim_document is None
        ):
            raise SelfPairingError()
        self.require_recipient_key(ceremony)
        claim, exchange = ceremony.claim_document, ceremony.exchange_document
        if claim is None:
            raise SelfPairingError()
        key = base64.b64decode(claim["device_public_key_spki_b64"], validate=True)
        thumb = bytes.fromhex(claim["device_key_thumbprint_sha256"])
        active = self._s.scalar(
            select(DeviceRow.device_id)
            .where(
                DeviceRow.user_id == ceremony.user_id,
                DeviceRow.revoked_at.is_(None),
                or_(DeviceRow.public_key_thumbprint_sha256 == thumb, DeviceRow.public_key == key),
            )
            .limit(1)
        )
        if active is not None:
            raise SelfPairingError("self_pairing_key_already_bound")
        try:
            require_device_capacity(self._s, ceremony.user_id)
        except ResourceAdmissionError as error:
            raise SelfPairingError(error.code) from error
        refresh = bytes.fromhex(exchange["next_refresh_token_sha256"])
        if (
            self._s.scalar(
                select(UserSessionRow.session_id).where(
                    UserSessionRow.refresh_token_hash == refresh
                )
            )
            is not None
        ):
            raise SelfPairingError("operation_conflict")
        device_id, session_id = uuid4(), uuid4()
        if before_write is None:
            raise DeviceCompatibilityError()
        now = before_write()  # last locked snapshot, then clock and pure checks; no later read
        expiry = now + timedelta(days=90)
        self._s.add(
            DeviceRow(
                device_id=device_id,
                user_id=ceremony.user_id,
                device_name=claim["device_name"],
                platform=ceremony.recipient_platform,
                app_version=claim["app_version"],
                public_key=key,
                public_key_thumbprint_sha256=thumb,
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
                refresh_token_hash=refresh,
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
        if (
            not isinstance(ceremony, SelfCeremonyV2)
            or ceremony.exchange_document is None
            or ceremony.claim_document is None
        ):
            raise SelfPairingError()
        self.require_recipient_key(ceremony)
        if ceremony.result_device_id is None:
            raise SelfPairingError()
        device = self._device(ceremony.user_id, ceremony.result_device_id)
        row = self._s.scalar(
            select(UserSessionRow)
            .where(
                UserSessionRow.session_id == ceremony.result_session_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = self.database_now()
        if ceremony.receipt_expires_at is None or ceremony.receipt_expires_at <= now:
            raise SelfPairingError()
        if (
            row is None
            or row.user_id != ceremony.user_id
            or row.device_id != device.device_id
            or row.session_mode != "V2"
            or row.revoked_at is not None
            or row.expires_at <= now
            or row.family_id != row.session_id
            or row.generation != 0
            or row.refresh_token_hash.hex()
            != ceremony.exchange_document["next_refresh_token_sha256"]
            or ceremony.receipt_expires_at != row.expires_at + timedelta(seconds=300)
            or device.platform != ceremony.recipient_platform
            or cast(bytes, device.public_key_thumbprint_sha256).hex()
            != ceremony.claim_document["device_key_thumbprint_sha256"]
            or base64.b64encode(cast(bytes, device.public_key)).decode("ascii")
            != ceremony.claim_document["device_public_key_spki_b64"]
        ):
            raise SelfPairingError()
        return PairingBinding(device.device_id, row.session_id, row.expires_at)


class SqlAlchemySelfPairingV2UnitOfWork(SqlAlchemySelfPairingUnitOfWork):
    pairing: SelfPairingRepositoryV2

    def __init__(
        self, sessions: sessionmaker[Session], identity: Callable[[Session], NegotiationIdentity]
    ) -> None:
        super().__init__(sessions)
        self._identity = identity

    def __enter__(self) -> SqlAlchemySelfPairingV2UnitOfWork:
        self._session = self._sessions()
        self.pairing = SqlAlchemySelfPairingRepositoryV2(self._session, self._identity)
        return self


class SqlAlchemySelfPairingV2UnitOfWorkFactory:
    def __init__(
        self, sessions: sessionmaker[Session], identity: Callable[[Session], NegotiationIdentity]
    ) -> None:
        self._sessions, self._identity = sessions, identity

    def __call__(self) -> SqlAlchemySelfPairingV2UnitOfWork:
        return SqlAlchemySelfPairingV2UnitOfWork(self._sessions, self._identity)
