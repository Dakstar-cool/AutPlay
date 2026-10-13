"""Portable identities authorize bounded server copies and ordinary device sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Text, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.portable_account_crypto import (
    canonical_request,
    decode_b64,
    validate_account_document,
    verify_account_proof,
)
from autplay.adapters.postgresql.account_recovery import revoke_account_authority
from autplay.adapters.postgresql.models import (
    AccountRecoveryCredentialRow,
    AuditEventRow,
    DeviceKeyBlockRow,
    DeviceRow,
    PortableAccountChallengeRow,
    PortableAccountRow,
    PublicIdRegistrationRow,
    SelfPairingRateRow,
    ServerInstanceRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.resource_limits import (
    lock_resource_admission,
    require_device_capacity,
    terminate_retired_session,
)
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import ProfilePairingError, iso8601
from autplay.ports.auth import AccessTokenCodec


class PortableAccountService:
    """Passwords stay on the client; account-key possession proves authorization."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        access_tokens: AccessTokenCodec,
        access_ttl: timedelta,
        source_secret: bytes,
    ) -> None:
        self.sessions, self.access_tokens, self.access_ttl = sessions, access_tokens, access_ttl
        self.source_secret = source_secret

    def document(self, actor: Principal) -> dict[str, object] | None:
        with self.sessions.begin() as session:
            lock_resource_admission(session)
            self._actor(session, actor, datetime.now(UTC))
            row = session.get(PortableAccountRow, actor.user_id)
            return None if row is None else row.document

    def public_id(self, actor: Principal) -> str | None:
        with self.sessions.begin() as session:
            lock_resource_admission(session)
            self._actor(session, actor, datetime.now(UTC))
            row = session.get(PublicIdRegistrationRow, actor.user_id)
            return None if row is None else row.public_id

    def challenge(
        self,
        purpose: str,
        source: str,
        *,
        login: str | None = None,
        document: dict[str, object] | None = None,
        actor: Principal | None = None,
        action: str | None = None,
        operation_id: UUID | None = None,
        target_device_id: UUID | None = None,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        source_key = hmac.digest(self.source_secret, source.encode(), "sha256")
        self._admit_attempt(source)
        with self.sessions.begin() as session:
            # One short shared lock serializes source rate admission and first account claims.
            lock_resource_admission(session)
            self._prune(session, now)
            passport: dict[str, object]
            if purpose == "LOGIN":
                registration = session.scalar(
                    select(PublicIdRegistrationRow).where(
                        PublicIdRegistrationRow.public_id == login
                    )
                )
                root = (
                    None
                    if registration is None
                    else session.get(PortableAccountRow, registration.user_id)
                )
                if root is None:
                    raise ProfilePairingError("account_authentication_failed")
                passport, account_id = root.document, root.user_id
                self._account(session, account_id)
            elif purpose in {"REGISTER", "CLAIM"}:
                if document is None:
                    raise ProfilePairingError("account_document_invalid")
                account_id, public_id, _ = validate_account_document(document)
                passport = document
                existing = session.get(PortableAccountRow, account_id)
                if purpose == "CLAIM":
                    if actor is None or actor.user_id != account_id:
                        raise ProfilePairingError("account_identity_conflict")
                    self._actor(session, actor, now)
                    registration = session.get(PublicIdRegistrationRow, account_id)
                    if registration is not None and registration.public_id != public_id:
                        raise ProfilePairingError("account_identity_conflict")
                elif session.get(UserAccountRow, account_id) is not None and existing is None:
                    raise ProfilePairingError("account_claim_required")
                if existing is not None and existing.document != passport:
                    raise ProfilePairingError("account_identity_conflict")
            elif purpose == "ROTATE":
                if document is None:
                    raise ProfilePairingError("account_document_invalid")
                account_id, _, _ = validate_account_document(document)
                self._account(session, account_id)
                root = session.get(PortableAccountRow, account_id)
                if root is None or root.document != document:
                    raise ProfilePairingError("account_identity_conflict")
                passport = root.document
            elif purpose == "REAUTH":
                if (
                    actor is None
                    or operation_id is None
                    or action
                    not in {"LOGOUT_CURRENT", "LOGOUT_ALL", "REVOKE_DEVICE", "DISCONNECT_LOCAL"}
                ):
                    raise ProfilePairingError("account_request_invalid")
                self._actor(session, actor, now)
                root = session.get(PortableAccountRow, actor.user_id)
                if root is None:
                    raise ProfilePairingError("account_password_setup_required")
                passport, account_id = root.document, root.user_id
            else:
                raise ProfilePairingError("account_request_invalid")
            instance = session.scalar(select(ServerInstanceRow))
            if instance is None:
                raise ProfilePairingError("capability_missing")
            challenge_id = uuid4()
            body: dict[str, object] = {
                "challenge_id": str(challenge_id),
                "purpose": purpose,
                "account_id": str(account_id),
                "document_sha256": hashlib.sha256(canonical_request(passport)).hexdigest(),
                "server_instance_id": str(instance.server_instance_id),
                "identity_epoch": instance.identity_epoch,
                "identity_thumbprint_sha256": instance.identity_thumbprint_sha256.hex(),
                "api_origin": instance.api_origin,
                "stream_origin": instance.stream_origin,
                "nonce": secrets.token_urlsafe(32),
                "expires_at": iso8601(now + timedelta(minutes=2)),
                "actor_device_id": str(actor.device_id) if actor else None,
                "actor_session_id": str(actor.session_id) if actor else None,
                "action": action,
                "operation_id": str(operation_id) if operation_id else None,
                "target_device_id": str(target_device_id) if target_device_id else None,
            }
            session.add(
                PortableAccountChallengeRow(
                    challenge_id=challenge_id,
                    purpose=purpose,
                    source_key=source_key,
                    account_id=account_id,
                    actor_device_id=actor.device_id if actor else None,
                    actor_session_id=actor.session_id if actor else None,
                    document=body,
                    created_at=now,
                    expires_at=now + timedelta(minutes=2),
                )
            )
            return {"challenge": body, "account": passport}

    def complete(
        self,
        body: dict[str, object],
        proof: str,
        *,
        actor: Principal | None = None,
        replacement_proof: str | None = None,
        source: str = "internal",
    ) -> dict[str, object]:
        """Commit exact proof once; replay never extends the original access grant."""
        now = datetime.now(UTC)
        self._admit_attempt(source)
        self._retire_expired_receipt(body, proof, now)
        with self.sessions.begin() as session:
            # Rotation uses the existing recovery lock order to serialize sync and pairing.
            challenge = body.get("challenge")
            if isinstance(challenge, dict) and challenge.get("purpose") == "ROTATE":
                session.scalar(select(ServerInstanceRow).with_for_update())
                lock_resource_admission(session)
                _acquire_sync_owner_publish_lock(session, UUID(str(challenge.get("account_id"))))
            lock_resource_admission(session)
            row, passport, digest = self._proof(session, body, proof, now, actor)
            if row.purpose == "REAUTH":
                raise ProfilePairingError("account_request_invalid")
            if row.purpose == "ROTATE":
                return self._rotate(session, row, body, passport, digest, replacement_proof, now)
            if row.receipt is not None:
                if row.request_hash != digest or row.receipt_expires_at is None:
                    raise ProfilePairingError("operation_conflict")
                return self._receipt(session, row, now)
            account_id, login, public_key = validate_account_document(passport)
            account = session.get(UserAccountRow, account_id, with_for_update=True)
            root = session.get(PortableAccountRow, account_id, with_for_update=True)
            if root is not None and (root.public_key != public_key or root.document != passport):
                raise ProfilePairingError("account_identity_conflict")
            if account is None:
                if row.purpose != "REGISTER":
                    raise ProfilePairingError("account_authentication_failed")
                count = (
                    session.scalar(
                        select(func.count())
                        .select_from(UserAccountRow)
                        .where(UserAccountRow.status == "ACTIVE")
                    )
                    or 0
                )
                if count >= 20:
                    raise ProfilePairingError("account_limit_reached")
                account = UserAccountRow(
                    user_id=account_id,
                    display_name=login,
                    role="USER",
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
                session.add(account)
                session.flush()
            else:
                self._account(session, account_id)
                if root is None and row.purpose != "CLAIM":
                    raise ProfilePairingError("account_claim_required")
            registration = session.get(PublicIdRegistrationRow, account_id)
            collision = session.scalar(
                select(PublicIdRegistrationRow).where(PublicIdRegistrationRow.public_id == login)
            )
            if collision is not None and collision.user_id != account_id:
                raise ProfilePairingError("public_id_unavailable")
            if registration is None:
                session.add(
                    PublicIdRegistrationRow(user_id=account_id, public_id=login, created_at=now)
                )
            elif registration.public_id != login:
                raise ProfilePairingError("account_identity_conflict")
            if root is None:
                session.add(
                    PortableAccountRow(
                        user_id=account_id,
                        public_key=public_key,
                        key_thumbprint=hashlib.sha256(public_key).digest(),
                        document=passport,
                        revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
            if row.purpose == "CLAIM":
                if actor is None:
                    raise ProfilePairingError("account_authentication_failed")
                self._actor(session, actor, now)
                result: dict[str, object] = {"account_id": str(account_id), "claimed": True}
            else:
                result = self._bind(session, account, body, now)
            row.request_hash, row.receipt = digest, result
            row.receipt_expires_at = now + self.access_ttl
            session.add(
                AuditEventRow(
                    occurred_at=now,
                    actor_type="USER",
                    actor_user_id=account_id,
                    actor_device_id=actor.device_id if actor else UUID(str(result["device_id"])),
                    action="portable_account." + row.purpose.lower(),
                    target_type="USER_ACCOUNT",
                    target_id=account_id,
                    request_id=row.challenge_id,
                )
            )
            session.flush()
            return self._receipt(session, row, now)

    @staticmethod
    def _proof(
        session: Session,
        body: dict[str, object],
        proof: str,
        now: datetime,
        actor: Principal | None,
    ) -> tuple[PortableAccountChallengeRow, dict[str, object], bytes]:
        challenge = body.get("challenge")
        passport = body.get("account")
        if not isinstance(challenge, dict) or not isinstance(passport, dict):
            raise ProfilePairingError("account_request_invalid")
        row = session.get(
            PortableAccountChallengeRow,
            UUID(str(challenge.get("challenge_id"))),
            with_for_update=True,
        )
        if row is None:
            # Pruned exact pending commands still receive a terminal outcome. No grant exists
            # after retention; the client can now release its encrypted pending enrollment.
            expiry = datetime.fromisoformat(str(challenge.get("expires_at")))
            if expiry.tzinfo is not None and expiry <= now:
                raise ProfilePairingError("account_challenge_expired")
            raise ProfilePairingError("account_authentication_failed")
        if row.document != challenge:
            raise ProfilePairingError("account_authentication_failed")
        if row.receipt is None and row.expires_at <= now:
            raise ProfilePairingError("account_challenge_expired")
        if row.actor_session_id is not None and (
            actor is None
            or (
                actor.user_id != row.account_id
                or actor.device_id != row.actor_device_id
                or actor.session_id != row.actor_session_id
            )
        ):
            raise ProfilePairingError("account_authentication_failed")
        account_id, _, key = validate_account_document(passport)
        if (
            account_id != row.account_id
            or hashlib.sha256(canonical_request(passport)).hexdigest()
            != (challenge["document_sha256"])
        ):
            raise ProfilePairingError("account_identity_conflict")
        root = session.get(PortableAccountRow, account_id)
        # Rotation receipts require both keys and separately match their replacement below.
        if not (row.purpose == "ROTATE" and row.receipt is not None) and (
            (root is not None and root.document != passport)
            or (root is None and row.purpose not in {"REGISTER", "CLAIM"})
        ):
            raise ProfilePairingError("account_identity_conflict")
        instance = session.scalar(select(ServerInstanceRow))
        if instance is None or (
            str(instance.server_instance_id) != challenge["server_instance_id"]
            or instance.identity_epoch != challenge["identity_epoch"]
            or instance.identity_thumbprint_sha256.hex() != challenge["identity_thumbprint_sha256"]
            or instance.api_origin != challenge["api_origin"]
            or instance.stream_origin != challenge["stream_origin"]
        ):
            raise ProfilePairingError("identity_changed")
        try:
            digest = verify_account_proof(key, row.purpose.lower(), body, proof)
        except ValueError:
            raise ProfilePairingError("account_authentication_failed") from None
        return row, cast(dict[str, object], passport), digest

    def _rotate(
        self,
        session: Session,
        row: PortableAccountChallengeRow,
        body: dict[str, object],
        passport: dict[str, object],
        digest: bytes,
        replacement_proof: str | None,
        now: datetime,
    ) -> dict[str, object]:
        replacement = body.get("replacement_account")
        if set(body) != {"challenge", "account", "replacement_account"} or not isinstance(
            replacement, dict
        ):
            raise ProfilePairingError("account_request_invalid")
        account_id, login, public_key = validate_account_document(replacement)
        if (
            account_id != row.account_id
            or login != passport["public_id"]
            or replacement["public_key_spki_b64"] == passport["public_key_spki_b64"]
        ):
            raise ProfilePairingError("account_identity_conflict")
        try:
            verify_account_proof(public_key, "rotate", body, replacement_proof)
        except ValueError:
            raise ProfilePairingError("account_authentication_failed") from None
        account = self._account(session, account_id)
        root = session.get(PortableAccountRow, account_id, with_for_update=True)
        if root is None:
            raise ProfilePairingError("account_identity_conflict")
        if row.receipt is not None:
            if row.request_hash != digest:
                raise ProfilePairingError("operation_conflict")
            if root.document != replacement:
                raise ProfilePairingError("account_identity_conflict")
            return self._receipt(session, row, now)
        if root.document != passport:
            raise ProfilePairingError("account_identity_conflict")
        revoke_account_authority(session, account, now)
        # Legacy server recovery codes must not restore retired device authority either.
        legacy_recovery = session.get(
            AccountRecoveryCredentialRow, account_id, with_for_update=True
        )
        if legacy_recovery is not None:
            legacy_recovery.verifier_sha256 = secrets.token_bytes(32)
            legacy_recovery.generation += 1
            legacy_recovery.updated_at = now
        root.public_key, root.key_thumbprint = public_key, hashlib.sha256(public_key).digest()
        root.document, root.revision, root.updated_at = replacement, root.revision + 1, now
        session.execute(
            delete(PortableAccountChallengeRow).where(
                PortableAccountChallengeRow.account_id == account_id,
                PortableAccountChallengeRow.challenge_id != row.challenge_id,
            )
        )
        row.request_hash, row.receipt = (
            digest,
            {
                "account_id": str(account_id),
                "rotated": True,
                "account": replacement,
                "revision": root.revision,
            },
        )
        row.receipt_expires_at = now + self.access_ttl
        session.add(
            AuditEventRow(
                occurred_at=now,
                actor_type="USER",
                actor_user_id=account_id,
                action="portable_account.rotate",
                target_type="USER_ACCOUNT",
                target_id=account_id,
                request_id=row.challenge_id,
            )
        )
        session.flush()
        return self._receipt(session, row, now)

    def _bind(
        self, session: Session, account: UserAccountRow, body: Mapping[str, object], now: datetime
    ) -> dict[str, object]:
        key = decode_b64(body.get("device_public_key_spki_b64"), 91, 91)
        # Reuse the already bounded P-256 passport parser to validate the device public key.
        device_passport = dict(cast(dict[str, object], body["account"]))
        device_passport["public_key_spki_b64"] = body["device_public_key_spki_b64"]
        validate_account_document(device_passport)
        thumbprint = hashlib.sha256(key).digest()
        block = session.get(DeviceKeyBlockRow, (account.user_id, thumbprint))
        if block is not None and block.unblocked_at is None:
            raise ProfilePairingError("device_key_blocked")
        device = session.scalar(
            select(DeviceRow)
            .where(
                DeviceRow.user_id == account.user_id,
                DeviceRow.public_key_thumbprint_sha256 == thumbprint,
                DeviceRow.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if device is None:
            require_device_capacity(session, account.user_id)
            device = DeviceRow(
                user_id=account.user_id,
                device_id=uuid4(),
                device_name=str(body["device_name"]),
                platform="ANDROID",
                app_version=str(body["app_version"]),
                public_key=key,
                public_key_thumbprint_sha256=thumbprint,
                created_at=now,
                updated_at=now,
            )
            session.add(device)
            session.flush()
        session_id, token_id = uuid4(), uuid4()
        refresh_hash = bytes.fromhex(str(body["next_refresh_token_sha256"]))
        if (
            len(refresh_hash) != 32
            or session.scalar(
                select(UserSessionRow.session_id).where(
                    UserSessionRow.refresh_token_hash == refresh_hash
                )
            )
            is not None
        ):
            raise ProfilePairingError("operation_conflict")
        expires = now + timedelta(days=90)
        prior = session.scalars(
            select(UserSessionRow)
            .where(
                UserSessionRow.user_id == account.user_id,
                UserSessionRow.device_id == device.device_id,
                UserSessionRow.revoked_at.is_(None),
            )
            .with_for_update()
        ).all()
        for previous in prior:
            previous.revoked_at = now
            terminate_retired_session(session, previous, now)
        session.add(
            UserSessionRow(
                user_id=account.user_id,
                device_id=device.device_id,
                session_id=session_id,
                refresh_token_hash=refresh_hash,
                issued_at=now,
                expires_at=expires,
                last_rotated_at=now,
                session_mode="V2",
                family_id=session_id,
                generation=0,
            )
        )
        return {
            "account_id": str(account.user_id),
            "device_id": str(device.device_id),
            "session_id": str(session_id),
            "session_family_id": str(session_id),
            "generation": 0,
            "binding_commit_id": str(UUID(str(body["binding_commit_id"]))),
            "token_id": str(token_id),
            "issued_at": iso8601(now),
            "access_expires_at": iso8601(now + self.access_ttl),
            "refresh_expires_at": iso8601(expires),
        }

    def _receipt(
        self, session: Session, row: PortableAccountChallengeRow, now: datetime
    ) -> dict[str, object]:
        # A rotation receipt grants no session. Keep exact crash recovery available for as
        # long as its replacement root is current; _rotate verifies both possession proofs.
        if row.purpose == "ROTATE" and row.receipt is not None:
            self._account(session, row.account_id)
            return dict(row.receipt)
        if row.receipt_expires_at is None or row.receipt_expires_at <= now or row.receipt is None:
            raise ProfilePairingError("account_receipt_expired")
        self._account(session, row.account_id)
        result = dict(row.receipt)
        if row.purpose == "CLAIM":
            if row.actor_device_id is None or row.actor_session_id is None:
                raise ProfilePairingError("account_authentication_failed")
            self._actor(
                session,
                Principal(
                    row.account_id, row.actor_device_id, row.actor_session_id, AccountRole.USER
                ),
                now,
            )
        else:
            account = self._account(session, row.account_id)
            actor = Principal(
                row.account_id,
                UUID(str(result["device_id"])),
                UUID(str(result["session_id"])),
                AccountRole(account.role),
            )
            self._actor(session, actor, now)
            result["access_token"] = self.access_tokens.issue(
                actor,
                token_id=UUID(str(result.pop("token_id"))),
                issued_at=datetime.fromisoformat(str(result["issued_at"])),
                expires_at=datetime.fromisoformat(str(result["access_expires_at"])),
            )
        return result

    @staticmethod
    def _account(session: Session, account_id: UUID) -> UserAccountRow:
        row = session.get(UserAccountRow, account_id, with_for_update=True)
        if row is None or row.status != "ACTIVE" or row.deleted_at is not None:
            raise ProfilePairingError("account_authentication_failed")
        return row

    @classmethod
    def _actor(cls, session: Session, actor: Principal, now: datetime) -> None:
        cls._account(session, actor.user_id)
        device = session.get(DeviceRow, actor.device_id, with_for_update=True)
        row = session.get(UserSessionRow, actor.session_id, with_for_update=True)
        if (
            device is None
            or device.user_id != actor.user_id
            or device.revoked_at is not None
            or row is None
            or row.user_id != actor.user_id
            or row.device_id != actor.device_id
            or row.revoked_at is not None
            or row.expires_at <= now
        ):
            raise ProfilePairingError("account_authentication_failed")

    def _retire_expired_receipt(self, body: dict[str, object], proof: str, now: datetime) -> None:
        """Commit expiry retirement before the rejecting proof transaction can roll back."""
        challenge = body.get("challenge")
        if not isinstance(challenge, dict):
            return
        challenge_id = UUID(str(challenge.get("challenge_id")))
        with self.sessions.begin() as session:
            lock_resource_admission(session)
            row = session.get(PortableAccountChallengeRow, challenge_id, with_for_update=True)
            if (
                row is None
                or row.purpose not in {"REGISTER", "LOGIN"}
                or row.receipt is None
                or row.receipt_expires_at is None
                or row.receipt_expires_at > now
            ):
                return
            _, _, digest = self._proof(session, body, proof, now, None)
            if row.request_hash != digest:
                raise ProfilePairingError("operation_conflict")
            # Retire the whole refresh family, including a successor issued before a crash.
            session_id = UUID(str(row.receipt["session_id"]))
            credentials = session.scalars(
                select(UserSessionRow)
                .where(
                    UserSessionRow.user_id == row.account_id,
                    (UserSessionRow.session_id == session_id)
                    | (UserSessionRow.family_id == session_id),
                    UserSessionRow.revoked_at.is_(None),
                )
                .with_for_update()
            ).all()
            for credential in credentials:
                credential.revoked_at = now
            for credential in credentials:
                terminate_retired_session(session, credential, now)

    def _admit_attempt(self, source: str) -> None:
        """Commit before lookup/proof work, so rejected attempts cannot roll back the limit."""
        now = datetime.now(UTC)
        key = hmac.digest(
            self.source_secret, b"portable-attempt-v1\x00" + source.encode(), "sha256"
        )
        with self.sessions.begin() as session:
            lock_resource_admission(session)
            row = session.get(SelfPairingRateRow, key, with_for_update=True)
            if row is None:
                session.add(
                    SelfPairingRateRow(
                        key_hash=key, count=1, expires_at=now + timedelta(minutes=10)
                    )
                )
            elif row.expires_at <= now:
                row.count, row.expires_at = 1, now + timedelta(minutes=10)
            elif row.count >= 60:
                raise ProfilePairingError("rate_limited")
            else:
                row.count += 1
            old_rates = (
                select(SelfPairingRateRow.key_hash)
                .where(SelfPairingRateRow.expires_at <= now)
                .limit(100)
            )
            session.execute(
                delete(SelfPairingRateRow).where(SelfPairingRateRow.key_hash.in_(old_rates))
            )

    @staticmethod
    def _prune(session: Session, now: datetime) -> None:
        # Bound expired personal payload retention even when the optional worker is disabled.
        # Keep receipts while their refresh family is alive so a pending exact retry can
        # durably retire it. Live session/device quotas bound retained enrollment receipts.
        live_family = (
            select(UserSessionRow.session_id)
            .where(
                UserSessionRow.user_id == PortableAccountChallengeRow.account_id,
                func.coalesce(UserSessionRow.family_id, UserSessionRow.session_id).cast(Text)
                == PortableAccountChallengeRow.receipt["session_id"].astext,
                UserSessionRow.revoked_at.is_(None),
                UserSessionRow.expires_at > now,
            )
            .exists()
        )
        old = (
            select(PortableAccountChallengeRow.challenge_id)
            .where(
                PortableAccountChallengeRow.expires_at < now - timedelta(minutes=10),
                PortableAccountChallengeRow.receipt.is_(None)
                | (
                    (PortableAccountChallengeRow.purpose != "ROTATE")
                    & (PortableAccountChallengeRow.receipt_expires_at < now)
                    & ~live_family
                ),
            )
            .limit(100)
        )
        session.execute(
            delete(PortableAccountChallengeRow).where(
                PortableAccountChallengeRow.challenge_id.in_(old)
            )
        )


def require_portable_reauthentication(
    session: Session,
    actor: Principal,
    action: str,
    operation_id: UUID,
    authorization: dict[str, object] | None,
    *,
    target_device_id: UUID | None = None,
) -> None:
    """Consume a session/action-bound proof in the same transaction as the lifecycle change."""
    root = session.get(PortableAccountRow, actor.user_id)
    if root is None:
        return
    if authorization is None or set(authorization) != {"request", "proof"}:
        raise ProfilePairingError("account_reauthentication_required")
    body, proof = authorization["request"], authorization["proof"]
    if not isinstance(body, dict) or not isinstance(proof, str):
        raise ProfilePairingError("account_reauthentication_required")
    now = datetime.now(UTC)
    row, passport, digest = PortableAccountService._proof(session, body, proof, now, actor)
    if (
        row.purpose != "REAUTH"
        or passport != root.document
        or (
            row.document["action"] != action
            or row.document["operation_id"] != str(operation_id)
            or row.document["target_device_id"]
            != (str(target_device_id) if target_device_id else None)
        )
    ):
        raise ProfilePairingError("account_reauthentication_required")
    if row.request_hash is not None and row.request_hash != digest:
        raise ProfilePairingError("operation_conflict")
    row.request_hash, row.receipt = digest, {"operation_id": str(operation_id), "action": action}
    row.receipt_expires_at = row.expires_at
