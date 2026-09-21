"""Atomic recovery code consumption, authority replacement and exact retry."""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.account_recovery import revoke_account_authority
from autplay.adapters.postgresql.models import (
    AuditEventRow,
    DeviceRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.models.account_recovery import (
    AccountRecoveryCredentialRow,
    AccountRecoveryOperationRow,
)
from autplay.adapters.postgresql.models.profile_pairing import DeviceKeyBlockRow, ServerInstanceRow
from autplay.adapters.postgresql.resource_limits import (
    lock_resource_admission,
    require_device_capacity,
)
from autplay.adapters.postgresql.self_device_pairing import SqlAlchemySelfPairingRepository
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.account_recovery import (
    AccountRecoveryError,
    code_verifier,
    parse_request,
    require_fresh,
    verify_device,
)
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import iso8601
from autplay.ports.auth import AccessTokenCodec


def database_now(session: Session) -> datetime:
    with session.no_autoflush:
        now = session.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise AccountRecoveryError()
    return now


class AccountRecoveryService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        access: AccessTokenCodec,
        access_ttl: timedelta,
        source_secret: bytes,
    ) -> None:
        if len(source_secret) < 32 or not timedelta(seconds=1) <= access_ttl <= timedelta(
            minutes=15
        ):
            raise ValueError("invalid account recovery configuration")
        self.sessions, self.access, self.ttl, self.secret = (
            sessions,
            access,
            access_ttl,
            source_secret,
        )

    def rate_gate(self, source: str, account_id: UUID | None = None) -> None:
        source = source if 1 <= len(source) <= 255 else "unknown"
        labels = (
            [(b"global", 3000), (b"source\x00" + source.encode(), 60)]
            if account_id is None
            else [(b"account\x00" + account_id.bytes, 30)]
        )
        with self.sessions.begin() as session:
            repo = SqlAlchemySelfPairingRepository(session)
            now = database_now(session)
            allowed = True
            for label, maximum in labels:
                key = hmac.digest(self.secret, b"account-recovery-rate:v1\x00" + label, "sha256")
                if not repo.rate_gate(key, maximum, now):
                    allowed = False
                    break
        if not allowed:
            raise AccountRecoveryError("account_recovery_rate_limited")

    @staticmethod
    def _lock(
        session: Session, request: dict[str, Any]
    ) -> tuple[ServerInstanceRow, UserAccountRow]:
        identity = session.get(
            ServerInstanceRow,
            UUID(request["expected_server_instance_id"]),
            with_for_update=True,
            populate_existing=True,
        )
        if identity is None or (
            identity.identity_epoch != request["expected_identity_epoch"]
            or identity.identity_thumbprint_sha256.hex()
            != request["expected_identity_thumbprint_sha256"]
            or identity.api_origin != request["expected_api_origin"]
            or identity.stream_origin != request["expected_stream_origin"]
        ):
            raise AccountRecoveryError()
        user_id = UUID(request["account_id"])
        lock_resource_admission(session)
        _acquire_sync_owner_publish_lock(session, user_id)
        account = session.get(UserAccountRow, user_id, with_for_update=True, populate_existing=True)
        if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
            raise AccountRecoveryError()
        return identity, account

    @staticmethod
    def _credential(
        session: Session, identity: ServerInstanceRow, account: UserAccountRow
    ) -> AccountRecoveryCredentialRow:
        row = session.get(
            AccountRecoveryCredentialRow,
            account.user_id,
            with_for_update=True,
            populate_existing=True,
        )
        if row is None or (
            row.server_instance_id,
            row.identity_epoch,
            row.identity_thumbprint_sha256,
        ) != (
            identity.server_instance_id,
            identity.identity_epoch,
            identity.identity_thumbprint_sha256,
        ):
            raise AccountRecoveryError()
        return row

    @staticmethod
    def _actor(session: Session, account: UserAccountRow, actor: Principal) -> UUID:
        device = session.get(
            DeviceRow, actor.device_id, with_for_update=True, populate_existing=True
        )
        credential = session.get(
            UserSessionRow, actor.session_id, with_for_update=True, populate_existing=True
        )
        now = database_now(session)
        if (
            actor.user_id != account.user_id
            or actor.role.value != account.role
            or device is None
            or device.user_id != account.user_id
            or device.revoked_at is not None
            or credential is None
            or credential.user_id != account.user_id
            or credential.device_id != actor.device_id
            or credential.session_mode != "V2"
            or credential.revoked_at is not None
            or credential.expires_at <= now
        ):
            raise AccountRecoveryError()
        return credential.family_id or credential.session_id

    @staticmethod
    def _receipt(
        session: Session, kind: str, request: dict[str, Any], account: UserAccountRow, now: datetime
    ) -> AccountRecoveryOperationRow | None:
        row = AccountRecoveryService._exact_receipt(session, kind, request, account)
        if row is not None and row.expires_at <= now:
            raise AccountRecoveryError()
        return row

    @staticmethod
    def _exact_receipt(
        session: Session, kind: str, request: dict[str, Any], account: UserAccountRow
    ) -> AccountRecoveryOperationRow | None:
        row = session.get(
            AccountRecoveryOperationRow,
            UUID(request["operation_id"]),
            with_for_update=True,
            populate_existing=True,
        )
        if row is not None and (
            row.kind != kind
            or row.user_id != account.user_id
            or row.server_instance_id != UUID(request["expected_server_instance_id"])
            or row.identity_epoch != request["expected_identity_epoch"]
            or row.request_sha256.hex() != request["request_sha256"]
        ):
            raise AccountRecoveryError("operation_conflict")
        if row is not None and row.authority_generation != account.authority_generation:
            raise AccountRecoveryError()
        return row

    @staticmethod
    def _current_receipt(
        credential: AccountRecoveryCredentialRow, receipt: AccountRecoveryOperationRow
    ) -> None:
        if credential.generation != receipt.result_generation or not hmac.compare_digest(
            credential.verifier_sha256, receipt.next_verifier_sha256
        ):
            raise AccountRecoveryError("recovery_operation_superseded")

    def status(self, actor: Principal) -> dict[str, Any]:
        with self.sessions.begin() as session:
            identity = session.scalar(select(ServerInstanceRow).with_for_update())
            lock_resource_admission(session)
            _acquire_sync_owner_publish_lock(session, actor.user_id)
            account = session.get(
                UserAccountRow, actor.user_id, with_for_update=True, populate_existing=True
            )
            if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
                raise AccountRecoveryError()
            self._actor(session, account, actor)
            credential = session.get(AccountRecoveryCredentialRow, account.user_id)
            configured = (
                credential is not None
                and identity is not None
                and (
                    credential.server_instance_id,
                    credential.identity_epoch,
                    credential.identity_thumbprint_sha256,
                )
                == (
                    identity.server_instance_id,
                    identity.identity_epoch,
                    identity.identity_thumbprint_sha256,
                )
            )
            return {
                "contract_version": "v1",
                "schema_version": 1,
                "account_id": str(account.user_id),
                "account_label": account.display_name,
                "configured": configured,
                "code_generation": 0 if credential is None else credential.generation,
            }

    def configure(self, actor: Principal, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("configure", document)
        if request["account_id"] != str(actor.user_id):
            raise AccountRecoveryError()
        with self.sessions.begin() as session:
            identity, account = self._lock(session, request)
            family = self._actor(session, account, actor)
            now = database_now(session)
            old = self._receipt(session, "CONFIGURE", request, account, now)
            credential = session.get(
                AccountRecoveryCredentialRow,
                account.user_id,
                with_for_update=True,
                populate_existing=True,
            )
            if old is not None:
                if credential is None or (old.actor_device_id, old.actor_family_id) != (
                    actor.device_id,
                    family,
                ):
                    raise AccountRecoveryError()
                self._current_receipt(credential, old)
                return self._configured(old, replayed=True)
            require_fresh(request, now)
            generation = 0 if credential is None else credential.generation
            if generation != request["expected_code_generation"]:
                raise AccountRecoveryError("recovery_generation_conflict")
            verifier = bytes.fromhex(request["next_code_verifier_sha256"])
            if credential is not None and hmac.compare_digest(credential.verifier_sha256, verifier):
                raise AccountRecoveryError("recovery_code_unchanged")
            receipt = self._new_receipt("CONFIGURE", request, account, generation, now)
            receipt.actor_device_id, receipt.actor_family_id = actor.device_id, family
            self._replace_credential(
                session, identity, account, credential, generation + 1, verifier, now
            )
            session.add(receipt)
            self._audit(session, account.user_id, actor.device_id, receipt, now)
            session.flush()
            return self._configured(receipt, replayed=False)

    def preview(self, code: str, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("preview", document)
        self.rate_gate("", UUID(request["account_id"]))
        verify_device("preview", request)
        with self.sessions.begin() as session:
            identity, account = self._lock(session, request)
            credential = self._credential(session, identity, account)
            require_fresh(request, database_now(session))
            self._possession(identity, account, credential.verifier_sha256, code)
            return {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": request["operation_id"],
                "server_instance_id": str(identity.server_instance_id),
                "identity_epoch": identity.identity_epoch,
                "account_id": str(account.user_id),
                "account_label": account.display_name,
                "role": account.role,
                "code_generation": credential.generation,
                "confirmation_required": True,
            }

    def recover(self, code: str, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("recover", document)
        self.rate_gate("", UUID(request["account_id"]))
        key = verify_device("recover", request)
        with self.sessions.begin() as session:
            identity, account = self._lock(session, request)
            return self.replace_binding(
                session, identity, account, request, key, code, kind="RECOVER"
            )

    def recover_outcome(self, refresh: str, document: dict[str, Any]) -> dict[str, Any]:
        """Recover an exact committed binding after its one-day reply receipt expired."""
        request = parse_request("recover", document)
        self.rate_gate("", UUID(request["account_id"]))
        key = verify_device("recover", request)
        try:
            refresh_bytes = refresh.encode("ascii")
        except UnicodeEncodeError as error:
            raise AccountRecoveryError() from error
        if not 32 <= len(refresh_bytes) <= 128:
            raise AccountRecoveryError()
        refresh_hash = sha256(refresh_bytes).digest()
        if not hmac.compare_digest(
            refresh_hash, bytes.fromhex(request["next_refresh_token_sha256"])
        ):
            raise AccountRecoveryError()
        with self.sessions.begin() as session:
            identity, account = self._lock(session, request)
            receipt = self._exact_receipt(session, "RECOVER", request, account)
            if receipt is None or receipt.spent_verifier_sha256 is None:
                raise AccountRecoveryError()
            credential = self._credential(session, identity, account)
            self._current_receipt(credential, receipt)
            return self._binding_response(
                session,
                account,
                receipt,
                database_now(session),
                replayed=True,
                expected_key=key,
                expected_refresh_hash=refresh_hash,
                recovered_outcome=True,
            )

    def replace_binding(
        self,
        session: Session,
        identity: ServerInstanceRow,
        account: UserAccountRow,
        request: dict[str, Any],
        key: bytes,
        code: str,
        *,
        kind: str,
    ) -> dict[str, Any]:
        """Caller verifies the purpose-bound proof and holds all lifecycle locks."""
        if (
            kind not in {"RECOVER", "DELETE_CANCEL"}
            or account.status != "ACTIVE"
            or account.deleted_at is not None
        ):
            raise AccountRecoveryError()
        credential = self._credential(session, identity, account)
        now = database_now(session)
        old = self._receipt(session, kind, request, account, now)
        if old is not None:
            if old.spent_verifier_sha256 is None:
                raise AccountRecoveryError()
            self._possession(identity, account, old.spent_verifier_sha256, code)
            self._current_receipt(credential, old)
            return self._binding_response(session, account, old, now, replayed=True)
        require_fresh(request, now)
        self._possession(identity, account, credential.verifier_sha256, code)
        if request["expected_code_generation"] != credential.generation:
            raise AccountRecoveryError("recovery_generation_conflict")
        verifier = bytes.fromhex(request["next_code_verifier_sha256"])
        if hmac.compare_digest(credential.verifier_sha256, verifier):
            raise AccountRecoveryError("recovery_code_unchanged")
        thumbprint = bytes.fromhex(request["device_key_thumbprint_sha256"])
        block = session.get(DeviceKeyBlockRow, (account.user_id, thumbprint))
        if block is not None and block.unblocked_at is None:
            raise AccountRecoveryError()
        if (
            session.scalar(
                select(DeviceRow.device_id)
                .where(
                    DeviceRow.user_id == account.user_id,
                    DeviceRow.public_key_thumbprint_sha256 == thumbprint,
                )
                .limit(1)
            )
            is not None
        ):
            raise AccountRecoveryError("recovery_key_already_bound")
        refresh_hash = bytes.fromhex(request["next_refresh_token_sha256"])
        if (
            session.scalar(
                select(UserSessionRow.session_id)
                .where(UserSessionRow.refresh_token_hash == refresh_hash)
                .limit(1)
            )
            is not None
        ):
            raise AccountRecoveryError("operation_conflict")
        generation, spent = credential.generation, credential.verifier_sha256
        if (
            session.scalar(
                select(AccountRecoveryOperationRow.operation_id)
                .where(
                    AccountRecoveryOperationRow.binding_commit_id
                    == UUID(request["binding_commit_id"])
                )
                .limit(1)
            )
            is not None
        ):
            raise AccountRecoveryError("operation_conflict")
        revoke_account_authority(session, account, now)
        session.flush()
        require_device_capacity(session, account.user_id)
        device_id, session_id = uuid4(), uuid4()
        device = DeviceRow(
            device_id=device_id,
            user_id=account.user_id,
            device_name=request["device_name"],
            platform="ANDROID",
            app_version=request["app_version"],
            public_key=key,
            public_key_thumbprint_sha256=thumbprint,
            created_at=now,
            updated_at=now,
        )
        session.add(device)
        session.flush()
        session.add(
            UserSessionRow(
                session_id=session_id,
                user_id=account.user_id,
                device_id=device_id,
                refresh_token_hash=refresh_hash,
                issued_at=now,
                expires_at=now + timedelta(days=90),
                last_rotated_at=now,
                family_id=session_id,
                generation=0,
                session_mode="V2",
            )
        )
        session.flush()
        receipt = self._new_receipt(kind, request, account, generation, now)
        receipt.spent_verifier_sha256 = spent
        receipt.result_device_id, receipt.result_session_id = device_id, session_id
        receipt.binding_commit_id = UUID(request["binding_commit_id"])
        self._replace_credential(
            session, identity, account, credential, generation + 1, verifier, now
        )
        session.add(receipt)
        self._audit(session, account.user_id, device_id, receipt, now)
        session.flush()
        return self._binding_response(session, account, receipt, now, replayed=False)

    @staticmethod
    def _possession(
        identity: ServerInstanceRow, account: UserAccountRow, verifier: bytes, code: str
    ) -> None:
        if not hmac.compare_digest(
            code_verifier(identity.server_instance_id, account.user_id, code), verifier
        ):
            raise AccountRecoveryError()

    @staticmethod
    def _replace_credential(
        session: Session,
        identity: ServerInstanceRow,
        account: UserAccountRow,
        old: AccountRecoveryCredentialRow | None,
        generation: int,
        verifier: bytes,
        now: datetime,
    ) -> None:
        row = old or AccountRecoveryCredentialRow(user_id=account.user_id, created_at=now)
        row.server_instance_id, row.identity_epoch = (
            identity.server_instance_id,
            identity.identity_epoch,
        )
        row.identity_thumbprint_sha256 = identity.identity_thumbprint_sha256
        row.generation, row.verifier_sha256, row.updated_at = generation, verifier, now
        session.add(row)

    @staticmethod
    def _new_receipt(
        kind: str, request: dict[str, Any], account: UserAccountRow, generation: int, now: datetime
    ) -> AccountRecoveryOperationRow:
        return AccountRecoveryOperationRow(
            operation_id=UUID(request["operation_id"]),
            kind=kind,
            user_id=account.user_id,
            server_instance_id=UUID(request["expected_server_instance_id"]),
            identity_epoch=request["expected_identity_epoch"],
            request_sha256=bytes.fromhex(request["request_sha256"]),
            previous_generation=generation,
            result_generation=generation + 1,
            authority_generation=account.authority_generation,
            next_verifier_sha256=bytes.fromhex(request["next_code_verifier_sha256"]),
            created_at=now,
            expires_at=now + timedelta(days=1),
        )

    @staticmethod
    def _configured(receipt: AccountRecoveryOperationRow, *, replayed: bool) -> dict[str, Any]:
        return {
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": str(receipt.operation_id),
            "account_id": str(receipt.user_id),
            "code_generation": receipt.result_generation,
            "configured": True,
            "replayed": replayed,
        }

    def _binding_response(
        self,
        session: Session,
        account: UserAccountRow,
        receipt: AccountRecoveryOperationRow,
        now: datetime,
        *,
        replayed: bool,
        expected_key: bytes | None = None,
        expected_refresh_hash: bytes | None = None,
        recovered_outcome: bool = False,
    ) -> dict[str, Any]:
        if receipt.result_device_id is None or receipt.result_session_id is None:
            raise AccountRecoveryError()
        device = session.get(
            DeviceRow, receipt.result_device_id, with_for_update=True, populate_existing=True
        )
        binding = session.get(
            UserSessionRow, receipt.result_session_id, with_for_update=True, populate_existing=True
        )
        if (
            device is None
            or device.user_id != account.user_id
            or device.revoked_at is not None
            or binding is None
            or binding.user_id != account.user_id
            or binding.device_id != device.device_id
            or binding.revoked_at is not None
            or binding.expires_at <= now
            or binding.session_mode != "V2"
            or (
                expected_key is not None
                and not hmac.compare_digest(device.public_key or b"", expected_key)
            )
            or (
                expected_refresh_hash is not None
                and not hmac.compare_digest(binding.refresh_token_hash, expected_refresh_hash)
            )
        ):
            raise AccountRecoveryError()
        principal = Principal(
            account.user_id, device.device_id, binding.session_id, AccountRole(account.role)
        )
        expires = now + self.ttl
        result = {
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": str(receipt.operation_id),
            "binding_commit_id": str(receipt.binding_commit_id),
            "server_instance_id": str(receipt.server_instance_id),
            "user_id": str(account.user_id),
            "account_label": account.display_name,
            "device_id": str(device.device_id),
            "session_id": str(binding.session_id),
            "refresh_generation": 0,
            "refresh_absolute_expires_at": iso8601(binding.expires_at),
            "receipt_expires_at": iso8601(receipt.expires_at),
            "code_generation": receipt.result_generation,
            "access_token": self.access.issue(
                principal, token_id=uuid4(), issued_at=now, expires_at=expires
            ),
            "access_expires_at": iso8601(expires),
            "replayed": replayed,
        }
        if recovered_outcome:
            result["outcome_recovered"] = True
        return result

    @staticmethod
    def _audit(
        session: Session,
        user_id: UUID,
        device_id: UUID,
        receipt: AccountRecoveryOperationRow,
        now: datetime,
    ) -> None:
        session.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="USER",
                actor_user_id=user_id,
                actor_device_id=device_id,
                action="account_recovery." + receipt.kind.lower(),
                target_type="USER_ACCOUNT",
                target_id=user_id,
                request_id=receipt.operation_id,
                reason_code="OWNER_PROVED_RECOVERY",
                metadata_sanitized={
                    "code_generation": receipt.result_generation,
                    "authority_generation": receipt.authority_generation,
                },
            )
        )


def cleanup_expired_recovery_operations(sessions: sessionmaker[Session], limit: int = 1000) -> int:
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("recovery cleanup bound")
    with sessions.begin() as session:
        active_result_binding = (
            select(UserSessionRow.session_id)
            .join(
                DeviceRow,
                (DeviceRow.user_id == UserSessionRow.user_id)
                & (DeviceRow.device_id == UserSessionRow.device_id),
            )
            .where(
                UserSessionRow.session_id == AccountRecoveryOperationRow.result_session_id,
                UserSessionRow.revoked_at.is_(None),
                UserSessionRow.expires_at > func.clock_timestamp(),
                DeviceRow.revoked_at.is_(None),
            )
            .exists()
        )
        identifiers = session.scalars(
            select(AccountRecoveryOperationRow.operation_id)
            .where(
                AccountRecoveryOperationRow.expires_at < func.clock_timestamp(),
                (AccountRecoveryOperationRow.kind == "CONFIGURE") | ~active_result_binding,
            )
            .order_by(AccountRecoveryOperationRow.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        if identifiers:
            session.execute(
                delete(AccountRecoveryOperationRow).where(
                    AccountRecoveryOperationRow.operation_id.in_(identifiers)
                )
            )
        return len(identifiers)
