"""M5B application boundary for signed identity and bounded invitations."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import (
    AuditEventRow,
    DeviceRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.models.profile_pairing import (
    EnrollmentExchangeReceiptRow,
    EnrollmentInvitationRow,
    ProfileLifecycleCommandRow,
    ServerInstanceRow,
    SessionRotationReceiptRow,
)
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    iso8601,
    public_key_thumbprint,
    public_spki,
    sign_p1363,
    verify_p1363,
)
from autplay.ports.auth import AccessTokenCodec

_DISCOVERY_DOMAIN = "AutPlay discovery v1\n"
_CAPABILITIES_DOMAIN = "AutPlay capabilities v1\n"
_EXCHANGE_DOMAIN = "AutPlay enrollment exchange v1\n"
_ROTATION_DOMAIN = "AutPlay session rotation v1\n"

# Stable signed int64 key spelling "AUTPLAY" plus the M5 identity boundary.
# It serializes the empty-table singleton decision and has no authorization meaning.
SERVER_INSTANCE_ADVISORY_LOCK = 0x415554504C415905


class ProfilePairingService:
    """Short PostgreSQL transactions; identity private key is never persisted."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        private_key: ec.EllipticCurvePrivateKey,
        label_hint: str,
        api_origin: str,
        stream_origin: str,
        access_tokens: AccessTokenCodec,
        access_ttl: timedelta,
    ) -> None:
        self._sessions, self._key, self._label, self._api, self._stream = (
            sessions,
            private_key,
            label_hint,
            api_origin,
            stream_origin,
        )
        self._access, self._access_ttl = access_tokens, access_ttl

    def discovery(self) -> dict[str, object]:
        now = _now()
        with self._sessions.begin() as s:
            instance = self._instance(s, now)
            payload: dict[str, object] = {
                "server_instance_id": str(instance.server_instance_id),
                "identity_epoch": instance.identity_epoch,
                "identity_public_key_spki_b64": _b64(instance.identity_public_key_spki),
                "identity_thumbprint_sha256": instance.identity_thumbprint_sha256.hex(),
                "label_hint": instance.label_hint,
                "api_origin": instance.api_origin,
                "stream_origin": instance.stream_origin,
                "supported_api_majors": [1],
                "enrollment_protocol_available": True,
                "issued_at": iso8601(now),
                "expires_at": iso8601(now + timedelta(minutes=10)),
            }
            return self._signed(payload, _DISCOVERY_DOMAIN)

    def capabilities(self, principal: Principal) -> dict[str, object]:
        now = _now()
        with self._sessions.begin() as s:
            instance = self._instance(s, now)
            payload: dict[str, object] = {
                "server_instance_id": str(instance.server_instance_id),
                "identity_epoch": instance.identity_epoch,
                "user_id": str(principal.user_id),
                "device_id": str(principal.device_id),
                "product_version": "0.0.0",
                "api_major": 1,
                "capability_revision": instance.capability_revision,
                "operations": _capability_operations(principal.role),
                "limits": {
                    "device_list_max": 100,
                    "session_list_max": 200,
                    "invitation_active_max": 5,
                    "invitation_ttl_max_seconds": 1800,
                },
                "issued_at": iso8601(now),
                "expires_at": iso8601(now + timedelta(minutes=60)),
            }
            return self._signed(payload, _CAPABILITIES_DOMAIN)

    def issue_invitation(
        self, principal: Principal, operation_id: UUID, expires_in_seconds: int
    ) -> dict[str, object]:
        return self._issue_invitation(
            principal, operation_id, expires_in_seconds, audit_as_system=False
        )

    def _issue_invitation(
        self,
        principal: Principal,
        operation_id: UUID,
        expires_in_seconds: int,
        *,
        audit_as_system: bool,
    ) -> dict[str, object]:
        if principal.role not in {AccountRole.OWNER, AccountRole.ADMIN}:
            raise ProfilePairingError("unauthorized")
        if not 1 <= expires_in_seconds <= 1800:
            raise ProfilePairingError("enrollment_rate_limited")
        now = _now()
        secret = secrets.token_urlsafe(32)
        digest = hashlib.sha256(secret.encode()).digest()
        with self._sessions.begin() as s:
            instance = self._instance(s, now)
            account = s.get(UserAccountRow, principal.user_id, with_for_update=True)
            if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
                raise ProfilePairingError("auth_attention_required")
            active = (
                s.scalar(
                    select(func.count())
                    .select_from(EnrollmentInvitationRow)
                    .where(
                        EnrollmentInvitationRow.user_id == principal.user_id,
                        EnrollmentInvitationRow.cancelled_at.is_(None),
                        EnrollmentInvitationRow.consumed_at.is_(None),
                        EnrollmentInvitationRow.expires_at > now,
                    )
                )
                or 0
            )
            hourly = (
                s.scalar(
                    select(func.count())
                    .select_from(EnrollmentInvitationRow)
                    .where(
                        EnrollmentInvitationRow.issued_by_user_id == principal.user_id,
                        EnrollmentInvitationRow.issued_at > now - timedelta(hours=1),
                    )
                )
                or 0
            )
            if active >= 5 or hourly >= 10:
                raise ProfilePairingError("enrollment_rate_limited", retryable=True)
            invitation = EnrollmentInvitationRow(
                invitation_id=operation_id,
                server_instance_id=instance.server_instance_id,
                user_id=principal.user_id,
                issued_by_user_id=principal.user_id,
                invitation_secret_hash=digest,
                issued_at=now,
                expires_at=now + timedelta(seconds=expires_in_seconds),
                cancelled_at=None,
                consumed_at=None,
            )
            s.add(invitation)
            self._audit(
                s,
                now,
                principal,
                "profile.invitation_issued",
                "ENROLLMENT_INVITATION",
                operation_id,
                as_system=audit_as_system,
            )
            return {
                "contract_version": "v1",
                "schema_version": 1,
                "invitation_id": str(operation_id),
                "server_instance_id": str(instance.server_instance_id),
                "identity_epoch": instance.identity_epoch,
                "identity_thumbprint_sha256": instance.identity_thumbprint_sha256.hex(),
                "api_origin": instance.api_origin,
                "stream_origin": instance.stream_origin,
                "user_id": str(principal.user_id),
                "account_display_name": account.display_name,
                "issued_at": iso8601(now),
                "expires_at": iso8601(invitation.expires_at),
                "invitation_secret": secret,
                "secret_handling": "DISPLAY_ONCE_NO_CLIPBOARD_NO_LOG_NO_EXPORT",
            }

    def issue_recovery_invitation(
        self, user_id: UUID, operation_id: UUID, expires_in_seconds: int
    ) -> dict[str, object]:
        """Trusted local recovery path; it never creates an account or selects a role."""
        return self._issue_invitation(
            Principal(user_id, UUID(int=0), UUID(int=0), AccountRole.OWNER),
            operation_id,
            expires_in_seconds,
            audit_as_system=True,
        )

    def list_devices(self, principal: Principal) -> dict[str, object]:
        with self._sessions() as s:
            rows = s.scalars(
                select(DeviceRow)
                .where(DeviceRow.user_id == principal.user_id)
                .order_by(DeviceRow.created_at.desc())
                .limit(100)
            ).all()
            return {
                "contract_version": "v1",
                "schema_version": 1,
                "devices": [
                    {
                        "device_id": str(r.device_id),
                        "device_name": r.device_name,
                        "platform": r.platform,
                        "key_thumbprint_sha256": None
                        if r.public_key_thumbprint_sha256 is None
                        else r.public_key_thumbprint_sha256.hex(),
                        "created_at": iso8601(r.created_at),
                        "revoked_at": None if r.revoked_at is None else iso8601(r.revoked_at),
                        "last_seen_at": None if r.last_seen_at is None else iso8601(r.last_seen_at),
                    }
                    for r in rows
                ],
            }

    def cleanup_expired_receipts(self, limit: int = 100) -> int:
        """Run one bounded receipt-maintenance batch in this service's database."""
        return cleanup_expired_pairing_receipts(self._sessions, limit=limit)

    def list_sessions(self, principal: Principal) -> dict[str, object]:
        with self._sessions() as s:
            rows = s.scalars(
                select(UserSessionRow)
                .where(UserSessionRow.user_id == principal.user_id)
                .order_by(UserSessionRow.issued_at.desc())
                .limit(200)
            ).all()
            return {
                "contract_version": "v1",
                "schema_version": 1,
                "sessions": [
                    {
                        "session_id": str(row.session_id),
                        "device_id": str(row.device_id),
                        "family_id": str(row.family_id or row.session_id),
                        "generation": row.generation or 0,
                        "issued_at": iso8601(row.issued_at),
                        "absolute_expires_at": iso8601(row.expires_at),
                        "revoked_at": None if row.revoked_at is None else iso8601(row.revoked_at),
                        "current": row.session_id == principal.session_id,
                    }
                    for row in rows
                ],
            }

    def cancel_invitation(
        self,
        principal: Principal,
        invitation_id: UUID,
        operation_id: UUID,
        reason_code: str | None = None,
    ) -> dict[str, object]:
        now = _now()
        with self._sessions.begin() as s:
            invitation = s.scalar(
                select(EnrollmentInvitationRow)
                .where(EnrollmentInvitationRow.invitation_id == invitation_id)
                .with_for_update()
            )
            if invitation is None or invitation.user_id != principal.user_id:
                raise ProfilePairingError("unauthorized")
            if (
                principal.role is not AccountRole.OWNER
                and invitation.issued_by_user_id != principal.user_id
            ):
                raise ProfilePairingError("unauthorized")
            already_terminal = (
                invitation.cancelled_at is not None
                or invitation.consumed_at is not None
                or invitation.expires_at <= now
            )
            terminal_at = _invitation_terminal_at(invitation, now)
            existing = self._existing_lifecycle(
                s,
                principal,
                operation_id,
                "profile.invitation_cancelled",
                "ENROLLMENT_INVITATION",
                invitation_id,
                reason_code,
            )
            if existing is not None:
                return existing
            if not already_terminal:
                invitation.cancelled_at = now
                terminal_at = now
            result = _lifecycle(operation_id, terminal_at, already_terminal)
            self._store_lifecycle(
                s,
                principal,
                "profile.invitation_cancelled",
                "ENROLLMENT_INVITATION",
                invitation_id,
                reason_code,
                result,
                now,
            )
            self._audit(
                s,
                now,
                principal,
                "profile.invitation_cancelled",
                "ENROLLMENT_INVITATION",
                invitation_id,
                request_id=operation_id,
                reason_code=reason_code,
            )
            return result

    def logout_current(
        self,
        principal: Principal,
        operation_id: UUID,
        reason_code: str | None = None,
        access_token_id: UUID | None = None,
    ) -> dict[str, object]:
        now = _now()
        with self._sessions.begin() as s:
            row = s.scalar(
                select(UserSessionRow)
                .where(
                    UserSessionRow.session_id == principal.session_id,
                    UserSessionRow.user_id == principal.user_id,
                    UserSessionRow.device_id == principal.device_id,
                )
                .with_for_update()
            )
            if row is None or row.session_mode != "V2":
                raise ProfilePairingError("session_revoked")
            already_terminal = row.revoked_at is not None
            existing = self._existing_lifecycle(
                s,
                principal,
                operation_id,
                "profile.session_logged_out",
                "USER_SESSION",
                row.session_id,
                reason_code,
                access_token_id,
            )
            if existing is not None:
                return existing
            terminal_at = row.revoked_at or now
            if not already_terminal:
                row.revoked_at = now
                terminal_at = now
            result = _lifecycle(operation_id, terminal_at, already_terminal)
            self._store_lifecycle(
                s,
                principal,
                "profile.session_logged_out",
                "USER_SESSION",
                row.session_id,
                reason_code,
                result,
                now,
                access_token_id,
            )
            self._audit(
                s,
                now,
                principal,
                "profile.session_logged_out",
                "USER_SESSION",
                row.session_id,
                request_id=operation_id,
                reason_code=reason_code,
            )
            return result

    def logout_all(
        self,
        principal: Principal,
        operation_id: UUID,
        reason_code: str | None = None,
        access_token_id: UUID | None = None,
    ) -> dict[str, object]:
        now = _now()
        with self._sessions.begin() as s:
            account = s.get(UserAccountRow, principal.user_id, with_for_update=True)
            if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
                raise ProfilePairingError("auth_attention_required")
            actor_session = s.get(UserSessionRow, principal.session_id, with_for_update=True)
            if (
                actor_session is None
                or actor_session.user_id != principal.user_id
                or actor_session.device_id != principal.device_id
                or actor_session.session_mode != "V2"
            ):
                raise ProfilePairingError("session_revoked")
            rows = s.scalars(
                select(UserSessionRow)
                .where(
                    UserSessionRow.user_id == principal.user_id, UserSessionRow.revoked_at.is_(None)
                )
                .with_for_update()
            ).all()
            existing = self._existing_lifecycle(
                s,
                principal,
                operation_id,
                "profile.all_sessions_logged_out",
                "USER_ACCOUNT",
                principal.user_id,
                reason_code,
                access_token_id,
            )
            if existing is not None:
                return existing
            for row in rows:
                row.revoked_at = now
            result = _lifecycle(operation_id, now, not rows)
            self._store_lifecycle(
                s,
                principal,
                "profile.all_sessions_logged_out",
                "USER_ACCOUNT",
                principal.user_id,
                reason_code,
                result,
                now,
                access_token_id,
            )
            self._audit(
                s,
                now,
                principal,
                "profile.all_sessions_logged_out",
                "USER_ACCOUNT",
                principal.user_id,
                request_id=operation_id,
                reason_code=reason_code,
            )
            return result

    def revoke_device(
        self,
        principal: Principal,
        device_id: UUID,
        operation_id: UUID,
        reason_code: str | None = None,
        access_token_id: UUID | None = None,
    ) -> dict[str, object]:
        now = _now()
        with self._sessions.begin() as s:
            device = s.scalar(
                select(DeviceRow)
                .where(DeviceRow.user_id == principal.user_id, DeviceRow.device_id == device_id)
                .with_for_update()
            )
            if device is None:
                raise ProfilePairingError("unauthorized")
            actor_session = s.get(UserSessionRow, principal.session_id, with_for_update=True)
            if (
                actor_session is None
                or actor_session.user_id != principal.user_id
                or actor_session.device_id != principal.device_id
                or actor_session.session_mode != "V2"
            ):
                raise ProfilePairingError("session_revoked")
            already_terminal = device.revoked_at is not None
            existing = self._existing_lifecycle(
                s,
                principal,
                operation_id,
                "profile.device_revoked",
                "DEVICE",
                device_id,
                reason_code,
                access_token_id,
            )
            if existing is not None:
                return existing
            terminal_at = device.revoked_at or now
            if not already_terminal:
                device.revoked_at = now
                self._revoke_device_sessions(s, principal.user_id, device_id, now)
                terminal_at = now
            result = _lifecycle(operation_id, terminal_at, already_terminal)
            self._store_lifecycle(
                s,
                principal,
                "profile.device_revoked",
                "DEVICE",
                device_id,
                reason_code,
                result,
                now,
                access_token_id,
            )
            self._audit(
                s,
                now,
                principal,
                "profile.device_revoked",
                "DEVICE",
                device_id,
                request_id=operation_id,
                reason_code=reason_code,
            )
            return result

    def exchange(self, request: dict[str, object]) -> tuple[dict[str, object], bool]:
        """Consume one invitation or return only its exact device-PoP replay."""
        now = _now()
        exchange_id = UUID(str(request["exchange_id"]))
        request_hash = _request_hash(request)
        device_spki = base64.b64decode(str(request["device_public_key_spki_b64"]), validate=True)
        device_thumb = public_key_thumbprint(device_spki)
        if device_thumb.hex() != request["device_key_thumbprint_sha256"]:
            raise ProfilePairingError("enrollment_invitation_unavailable")
        verify_p1363(
            device_spki, _EXCHANGE_DOMAIN, request_hash, str(request["device_signature_b64url"])
        )
        invitation_id = UUID(str(request["invitation_id"]))
        account_id = self._exchange_account_id(exchange_id, invitation_id)
        with self._sessions.begin() as s:
            # All M5B writers acquire mutable rows in this order: instance,
            # account, invitation/receipt, device, session.  Immutable IDs are
            # discovered before this transaction and every value is revalidated.
            instance = self._instance(s, now)
            if account_id is None:
                raise ProfilePairingError("enrollment_invitation_unavailable")
            account = s.get(UserAccountRow, account_id, with_for_update=True)
            if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
                raise ProfilePairingError("auth_attention_required")
            invitation = s.scalar(
                select(EnrollmentInvitationRow)
                .where(EnrollmentInvitationRow.invitation_id == invitation_id)
                .with_for_update()
            )
            receipt = s.scalar(
                select(EnrollmentExchangeReceiptRow)
                .where(EnrollmentExchangeReceiptRow.exchange_id == exchange_id)
                .with_for_update()
            )
            if receipt is not None:
                if (
                    receipt.invitation_id != invitation_id
                    or receipt.request_sha256 != request_hash
                    or receipt.device_key_thumbprint_sha256 != device_thumb
                    or receipt.receipt_expires_at <= now
                ):
                    raise ProfilePairingError("enrollment_invitation_unavailable")
                return self._exchange_replay(s, receipt, now, account), True
            if (
                invitation is None
                or invitation.invitation_secret_hash
                != hashlib.sha256(str(request["invitation_secret"]).encode("ascii")).digest()
            ):
                raise ProfilePairingError("enrollment_invitation_unavailable")
            if invitation.user_id != account.user_id or not _exchange_matches(
                request, invitation, instance, now
            ):
                raise ProfilePairingError("enrollment_invitation_unavailable")
            device_id, session_id = uuid4(), uuid4()
            refresh_hash = bytes.fromhex(str(request["next_refresh_token_sha256"]))
            expires_at = now + timedelta(days=90)
            device = DeviceRow(
                device_id=device_id,
                user_id=account.user_id,
                device_name=str(request["device_name"]),
                platform="ANDROID",
                app_version=str(request["app_version"]),
                public_key=device_spki,
                public_key_thumbprint_sha256=device_thumb,
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
            session = UserSessionRow(
                session_id=session_id,
                user_id=account.user_id,
                device_id=device_id,
                refresh_token_hash=refresh_hash,
                issued_at=now,
                expires_at=expires_at,
                last_rotated_at=now,
                family_id=session_id,
                generation=0,
                session_mode="V2",
            )
            invitation.consumed_at = now
            s.add_all((device, session))
            receipt_expires_at = expires_at + timedelta(minutes=5)
            receipt = EnrollmentExchangeReceiptRow(
                exchange_id=exchange_id,
                invitation_id=invitation_id,
                request_sha256=request_hash,
                device_key_thumbprint_sha256=device_thumb,
                device_id=device_id,
                session_id=session_id,
                binding_commit_id=UUID(str(request["binding_commit_id"])),
                receipt_expires_at=receipt_expires_at,
                created_at=now,
            )
            s.add(receipt)
            self._audit(
                s,
                now,
                Principal(account.user_id, device_id, session_id, AccountRole(account.role)),
                "profile.enrollment_exchanged",
                "DEVICE",
                device_id,
            )
            return self._exchange_response(
                account.role,
                account.user_id,
                receipt,
                session,
                instance.server_instance_id,
                now,
                False,
            ), False

    def rotate(self, request: dict[str, object]) -> tuple[dict[str, object], bool]:
        """Perform additive M5 v2 client-secret-hash rotation with replay receipts."""
        now = _now()
        rotation_id = UUID(str(request["rotation_id"]))
        request_hash = _request_hash(request)
        candidate = self._rotation_parent(rotation_id, UUID(str(request["parent_session_id"])))
        if candidate is None:
            raise ProfilePairingError("session_revoked")
        with self._sessions.begin() as s:
            instance = self._instance(s, now)
            account = s.get(UserAccountRow, candidate.user_id, with_for_update=True)
            if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
                raise ProfilePairingError("auth_attention_required")
            receipt = s.scalar(
                select(SessionRotationReceiptRow)
                .where(SessionRotationReceiptRow.rotation_id == rotation_id)
                .with_for_update()
            )
            parent_id = candidate.session_id if receipt is None else receipt.parent_session_id
            device = s.get(DeviceRow, candidate.device_id, with_for_update=True)
            parent = s.get(UserSessionRow, parent_id, with_for_update=True)
            if (
                parent is None
                or parent.user_id != account.user_id
                or parent.device_id != candidate.device_id
                or parent.session_mode != "V2"
                or device is None
            ):
                raise ProfilePairingError("session_revoked")
            if receipt is not None:
                if receipt.receipt_expires_at <= now:
                    raise ProfilePairingError("session_revoked")
                if (
                    device.public_key is None
                    or device.public_key_thumbprint_sha256 != receipt.device_key_thumbprint_sha256
                ):
                    raise ProfilePairingError("session_revoked")
                verify_p1363(
                    device.public_key,
                    _ROTATION_DOMAIN,
                    request_hash,
                    str(request["device_signature_b64url"]),
                )
                if receipt.request_sha256 != request_hash:
                    self._revoke_device_sessions(s, parent.user_id, parent.device_id, now)
                    device.revoked_at = now
                    self._audit(
                        s,
                        now,
                        Principal(
                            parent.user_id,
                            parent.device_id,
                            parent.session_id,
                            AccountRole(account.role),
                        ),
                        "profile.rotation_replay_revoked",
                        "DEVICE",
                        parent.device_id,
                    )
                    # The response is an error, but the security side effect
                    # must survive it; the surrounding transaction context
                    # would otherwise roll this revocation back on raise.
                    s.commit()
                    raise ProfilePairingError("session_revoked")
                successor = s.get(
                    UserSessionRow, receipt.successor_session_id, with_for_update=True
                )
                if successor is None:
                    raise ProfilePairingError("session_revoked")
                return self._rotation_response(s, receipt, successor, now, True), True
            current_hash = hashlib.sha256(
                str(request["current_refresh_token"]).encode("ascii")
            ).digest()
            if (
                parent.session_id != UUID(str(request["parent_session_id"]))
                or parent.refresh_token_hash != current_hash
                or parent.revoked_at is not None
                or parent.expires_at <= now
            ):
                raise ProfilePairingError("session_revoked")
            if device.revoked_at is not None:
                raise ProfilePairingError("device_revoked")
            if device.public_key is None or device.public_key_thumbprint_sha256 is None:
                raise ProfilePairingError("session_revoked")
            verify_p1363(
                device.public_key,
                _ROTATION_DOMAIN,
                request_hash,
                str(request["device_signature_b64url"]),
            )
            if (
                str(request["device_id"]) != str(parent.device_id)
                or int(str(request["current_generation"])) != parent.generation
            ):
                self._revoke_device_sessions(s, parent.user_id, parent.device_id, now)
                device.revoked_at = now
                self._audit(
                    s,
                    now,
                    Principal(
                        parent.user_id,
                        parent.device_id,
                        parent.session_id,
                        AccountRole(account.role),
                    ),
                    "profile.rotation_replay_revoked",
                    "DEVICE",
                    parent.device_id,
                )
                s.commit()
                raise ProfilePairingError("session_revoked")
            if (
                str(request["expected_server_instance_id"]) != str(instance.server_instance_id)
                or int(str(request["expected_identity_epoch"])) != instance.identity_epoch
            ):
                raise ProfilePairingError("session_revoked")
            successor_id = uuid4()
            successor = UserSessionRow(
                session_id=successor_id,
                user_id=parent.user_id,
                device_id=parent.device_id,
                refresh_token_hash=bytes.fromhex(str(request["next_refresh_token_sha256"])),
                issued_at=now,
                expires_at=parent.expires_at,
                last_rotated_at=now,
                family_id=parent.family_id or parent.session_id,
                generation=(parent.generation or 0) + 1,
                session_mode="V2",
            )
            parent.revoked_at = now
            s.add(successor)
            receipt = SessionRotationReceiptRow(
                rotation_id=rotation_id,
                parent_session_id=parent.session_id,
                successor_session_id=successor_id,
                request_sha256=request_hash,
                device_key_thumbprint_sha256=device.public_key_thumbprint_sha256,
                receipt_expires_at=parent.expires_at + timedelta(minutes=5),
                created_at=now,
            )
            s.add(receipt)
            self._audit(
                s,
                now,
                Principal(
                    parent.user_id, parent.device_id, parent.session_id, AccountRole(account.role)
                ),
                "profile.session_rotated",
                "USER_SESSION",
                successor_id,
            )
            return self._rotation_response(s, receipt, successor, now, False), False

    def _exchange_replay(
        self,
        s: Session,
        receipt: EnrollmentExchangeReceiptRow,
        now: datetime,
        account: UserAccountRow,
    ) -> dict[str, object]:
        device = s.get(DeviceRow, receipt.device_id, with_for_update=True)
        session = s.get(UserSessionRow, receipt.session_id, with_for_update=True)
        if session is None or device is None or session.user_id != account.user_id:
            raise ProfilePairingError("enrollment_invitation_unavailable")
        if account.status != "ACTIVE" or account.deleted_at is not None:
            raise ProfilePairingError("auth_attention_required")
        if device.revoked_at is not None:
            raise ProfilePairingError("device_revoked")
        if session.revoked_at is not None or session.expires_at <= now:
            raise ProfilePairingError("session_revoked")
        invitation = s.get(EnrollmentInvitationRow, receipt.invitation_id, with_for_update=True)
        if invitation is None:
            raise ProfilePairingError("enrollment_invitation_unavailable")
        self._audit(
            s,
            now,
            Principal(
                session.user_id, session.device_id, session.session_id, AccountRole(account.role)
            ),
            "profile.enrollment_exchange_replayed",
            "DEVICE",
            session.device_id,
        )
        return self._exchange_response(
            account.role,
            account.user_id,
            receipt,
            session,
            invitation.server_instance_id,
            now,
            True,
        )

    def _exchange_account_id(self, exchange_id: UUID, invitation_id: UUID) -> UUID | None:
        """Discover the account before taking any mutable locks."""
        with self._sessions() as s:
            receipt = s.get(EnrollmentExchangeReceiptRow, exchange_id)
            if receipt is not None:
                session = s.get(UserSessionRow, receipt.session_id)
                return None if session is None else session.user_id
            invitation = s.get(EnrollmentInvitationRow, invitation_id)
            return None if invitation is None else invitation.user_id

    def _rotation_parent(
        self, rotation_id: UUID, requested_parent_id: UUID
    ) -> UserSessionRow | None:
        """Discover immutable rotation ownership before acquiring writer locks."""
        with self._sessions() as s:
            receipt = s.get(SessionRotationReceiptRow, rotation_id)
            parent_id = requested_parent_id if receipt is None else receipt.parent_session_id
            return s.get(UserSessionRow, parent_id)

    def _exchange_response(
        self,
        role: str,
        user_id: UUID,
        receipt: EnrollmentExchangeReceiptRow,
        session: UserSessionRow,
        server_instance_id: UUID,
        now: datetime,
        replayed: bool,
    ) -> dict[str, object]:
        principal = Principal(user_id, session.device_id, session.session_id, AccountRole(role))
        access_expires = now + self._access_ttl
        return {
            "contract_version": "v1",
            "schema_version": 1,
            "exchange_id": str(receipt.exchange_id),
            "binding_commit_id": str(receipt.binding_commit_id),
            "server_instance_id": str(server_instance_id),
            "user_id": str(user_id),
            "device_id": str(session.device_id),
            "session_id": str(session.session_id),
            "refresh_generation": 0,
            "refresh_absolute_expires_at": iso8601(session.expires_at),
            "receipt_expires_at": iso8601(receipt.receipt_expires_at),
            "access_token": self._access.issue(
                principal, token_id=uuid4(), issued_at=now, expires_at=access_expires
            ),
            "access_expires_at": iso8601(access_expires),
            "replayed": replayed,
        }

    def _rotation_response(
        self,
        s: Session,
        receipt: SessionRotationReceiptRow,
        successor: UserSessionRow,
        now: datetime,
        replayed: bool,
    ) -> dict[str, object]:
        account = s.get(UserAccountRow, successor.user_id, with_for_update=True)
        device = s.get(DeviceRow, successor.device_id, with_for_update=True)
        if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
            raise ProfilePairingError("auth_attention_required")
        if device is None or device.revoked_at is not None:
            raise ProfilePairingError("device_revoked")
        if successor.revoked_at is not None or successor.expires_at <= now:
            raise ProfilePairingError("session_revoked")
        principal = Principal(
            successor.user_id, successor.device_id, successor.session_id, AccountRole(account.role)
        )
        access_expires = now + self._access_ttl
        return {
            "contract_version": "v1",
            "schema_version": 1,
            "rotation_id": str(receipt.rotation_id),
            "session_id": str(successor.session_id),
            "parent_session_id": str(receipt.parent_session_id),
            "family_id": str(successor.family_id or successor.session_id),
            "generation": successor.generation,
            "refresh_absolute_expires_at": iso8601(successor.expires_at),
            "receipt_expires_at": iso8601(receipt.receipt_expires_at),
            "access_token": self._access.issue(
                principal, token_id=uuid4(), issued_at=now, expires_at=access_expires
            ),
            "access_expires_at": iso8601(access_expires),
            "replayed": replayed,
        }

    def _revoke_device_sessions(
        self, s: Session, user_id: UUID, device_id: UUID, now: datetime
    ) -> None:
        rows = s.scalars(
            select(UserSessionRow)
            .where(
                UserSessionRow.user_id == user_id,
                UserSessionRow.device_id == device_id,
                UserSessionRow.revoked_at.is_(None),
            )
            .with_for_update()
        ).all()
        for row in rows:
            row.revoked_at = now

    def _existing_lifecycle(
        self,
        s: Session,
        principal: Principal,
        operation_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID,
        reason_code: str | None,
        access_token_id: UUID | None = None,
    ) -> dict[str, object] | None:
        """Claim an operation ID or return its immutable prior terminal response."""
        claimed = s.scalar(
            insert(ProfileLifecycleCommandRow)
            .values(
                operation_id=operation_id,
                actor_user_id=principal.user_id,
                actor_device_id=principal.device_id,
                actor_session_id=principal.session_id,
                actor_access_token_id=access_token_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                reason_code=reason_code,
                outcome="PENDING",
                terminal_at=_now(),
                created_at=_now(),
            )
            .on_conflict_do_nothing(index_elements=(ProfileLifecycleCommandRow.operation_id,))
            .returning(ProfileLifecycleCommandRow.operation_id)
        )
        if claimed is not None:
            # The pending row is materialized by _store_lifecycle in this same transaction.
            return None
        existing = s.scalar(
            select(ProfileLifecycleCommandRow)
            .where(ProfileLifecycleCommandRow.operation_id == operation_id)
            .with_for_update()
        )
        if existing is None:
            raise RuntimeError("profile lifecycle operation disappeared after conflict")
        if (
            existing.actor_user_id != principal.user_id
            or existing.actor_device_id != principal.device_id
            or existing.actor_session_id != principal.session_id
            or existing.actor_access_token_id != access_token_id
            or existing.action != action
            or existing.target_type != target_type
            or existing.target_id != target_id
            or existing.reason_code != reason_code
        ):
            raise ProfilePairingError("unauthorized")
        return {
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": str(existing.operation_id),
            "outcome": existing.outcome,
            "terminal_at": iso8601(existing.terminal_at),
        }

    @staticmethod
    def _store_lifecycle(
        s: Session,
        principal: Principal,
        action: str,
        target_type: str,
        target_id: UUID,
        reason_code: str | None,
        result: dict[str, object],
        now: datetime,
        access_token_id: UUID | None = None,
    ) -> None:
        """Finalize the just-claimed command without changing its request identity."""
        operation_id = UUID(str(result["operation_id"]))
        row = s.get(ProfileLifecycleCommandRow, operation_id, with_for_update=True)
        if row is None:
            raise RuntimeError("profile lifecycle operation was not claimed")
        if (
            row.actor_user_id != principal.user_id
            or row.actor_device_id != principal.device_id
            or row.actor_session_id != principal.session_id
            or row.actor_access_token_id != access_token_id
            or row.action != action
            or row.target_type != target_type
            or row.target_id != target_id
            or row.reason_code != reason_code
            or row.outcome != "PENDING"
        ):
            raise RuntimeError("profile lifecycle operation identity changed")
        row.outcome = str(result["outcome"])
        row.terminal_at = datetime.fromisoformat(str(result["terminal_at"]).replace("Z", "+00:00"))
        row.created_at = now

    def lifecycle_retry(
        self,
        *,
        principal: Principal,
        access_token_id: UUID,
        operation_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID,
        reason_code: str | None,
    ) -> dict[str, object] | None:
        """Read only the exact terminal command authorized by its original JWT."""
        with self._sessions() as s:
            row = s.scalar(
                select(ProfileLifecycleCommandRow).where(
                    ProfileLifecycleCommandRow.operation_id == operation_id,
                    ProfileLifecycleCommandRow.actor_user_id == principal.user_id,
                    ProfileLifecycleCommandRow.actor_device_id == principal.device_id,
                    ProfileLifecycleCommandRow.actor_session_id == principal.session_id,
                    ProfileLifecycleCommandRow.actor_access_token_id == access_token_id,
                    ProfileLifecycleCommandRow.action == action,
                    ProfileLifecycleCommandRow.target_type == target_type,
                    ProfileLifecycleCommandRow.target_id == target_id,
                    ProfileLifecycleCommandRow.reason_code == reason_code,
                    ProfileLifecycleCommandRow.outcome.in_(("APPLIED", "ALREADY_TERMINAL")),
                )
            )
            if row is None:
                return None
            return _lifecycle(row.operation_id, row.terminal_at, row.outcome == "ALREADY_TERMINAL")

    def _audit(
        self,
        s: Session,
        now: datetime,
        principal: Principal,
        action: str,
        target_type: str,
        target_id: UUID,
        *,
        as_system: bool = False,
        request_id: UUID | None = None,
        reason_code: str | None = None,
    ) -> None:
        """Store only opaque bounded action facts, never secrets or request payloads."""
        actor_device_id: UUID | None = None
        if not as_system and principal.device_id.int != 0:
            actor_device = s.get(DeviceRow, principal.device_id)
            if actor_device is not None and actor_device.user_id == principal.user_id:
                actor_device_id = principal.device_id
        s.add(
            AuditEventRow(
                occurred_at=now,
                actor_type=(
                    "SYSTEM"
                    if as_system
                    else "ADMIN"
                    if principal.role in {AccountRole.OWNER, AccountRole.ADMIN}
                    else "USER"
                ),
                actor_user_id=None if as_system else principal.user_id,
                actor_device_id=actor_device_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                request_id=request_id,
                reason_code=reason_code,
                metadata_sanitized={},
            )
        )

    def _instance(self, s: Session, now: datetime) -> ServerInstanceRow:
        s.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": SERVER_INSTANCE_ADVISORY_LOCK},
        )
        row = s.scalar(select(ServerInstanceRow).with_for_update())
        spki = public_spki(self._key)
        thumb = public_key_thumbprint(spki)
        if row is None:
            row = ServerInstanceRow(
                server_instance_id=uuid4(),
                identity_epoch=1,
                identity_public_key_spki=spki,
                identity_thumbprint_sha256=thumb,
                label_hint=self._label,
                api_origin=self._api,
                stream_origin=self._stream,
                capability_revision=1,
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            s.flush()
        elif row.identity_public_key_spki != spki:
            raise RuntimeError("profile identity key differs from persisted public evidence")
        elif (
            row.label_hint != self._label
            or row.api_origin != self._api
            or row.stream_origin != self._stream
        ):
            row.label_hint = self._label
            row.api_origin = self._api
            row.stream_origin = self._stream
            row.capability_revision += 1
            row.updated_at = now
            s.flush()
        return row

    def _signed(self, payload: dict[str, object], domain: str) -> dict[str, object]:
        digest = canonical_sha256(payload)
        return {
            "contract_version": "v1",
            "schema_version": 1,
            "payload": payload,
            "payload_sha256": digest.hex(),
            "signature_algorithm": "ES256-P1363",
            "signature_b64url": sign_p1363(self._key, domain, digest),
        }


def _now() -> datetime:
    return datetime.now(UTC)


def cleanup_expired_pairing_receipts(
    sessions: sessionmaker[Session], *, limit: int = 10_000, now: datetime | None = None
) -> int:
    """Delete one bounded batch of receipts that passed their grace boundary.

    The CPU worker invokes this at least every configured maintenance interval
    (bounded to one hour), so an expired receipt is removed no later than the
    accepted 24-hour cleanup window without a broker or external scheduler.
    ``SKIP LOCKED`` lets a second process safely make progress on another batch.
    """
    if not 1 <= limit <= 10_000:
        raise ValueError("receipt cleanup limit must be within 1..10000")
    cutoff = _now() if now is None else now
    deleted = 0
    with sessions.begin() as s:
        for row_type, key in (
            (EnrollmentExchangeReceiptRow, EnrollmentExchangeReceiptRow.exchange_id),
            (SessionRotationReceiptRow, SessionRotationReceiptRow.rotation_id),
        ):
            remaining = limit - deleted
            if remaining <= 0:
                break
            identifiers = s.scalars(
                select(key)
                .where(row_type.receipt_expires_at <= cutoff)
                .order_by(row_type.receipt_expires_at, key)
                .limit(remaining)
                .with_for_update(skip_locked=True)
            ).all()
            for identifier in identifiers:
                row = s.get(row_type, identifier)
                if row is not None and row.receipt_expires_at <= cutoff:  # type: ignore[attr-defined]
                    s.delete(row)
                    deleted += 1
    return deleted


def _capability_operations(role: AccountRole) -> list[str]:
    """Advertise only executable API operations for this authenticated role."""
    operations = [
        "getCapabilities",
        "listDevices",
        "listSessions",
        "rotateDeviceSession",
        "logoutCurrentSession",
        "logoutAllSessions",
        "revokeDevice",
    ]
    if role in {AccountRole.OWNER, AccountRole.ADMIN}:
        operations.extend(("createEnrollmentInvitation", "cancelEnrollmentInvitation"))
    return operations


def _b64(v: bytes) -> str:
    return base64.b64encode(v).decode("ascii")


def _request_hash(request: dict[str, object]) -> bytes:
    supplied = str(request.get("request_sha256", ""))
    digest = canonical_sha256(
        request, omit=frozenset({"request_sha256", "device_signature_b64url"})
    )
    if digest.hex() != supplied:
        raise ProfilePairingError("enrollment_invitation_unavailable")
    return digest


def _exchange_matches(
    request: dict[str, object],
    invitation: EnrollmentInvitationRow,
    instance: ServerInstanceRow,
    now: datetime,
) -> bool:
    return (
        invitation.cancelled_at is None
        and invitation.consumed_at is None
        and invitation.expires_at > now
        and str(request["expected_server_instance_id"]) == str(instance.server_instance_id)
        and int(str(request["expected_identity_epoch"])) == instance.identity_epoch
        and request["expected_identity_thumbprint_sha256"]
        == instance.identity_thumbprint_sha256.hex()
        and request["expected_api_origin"] == instance.api_origin
        and request["expected_stream_origin"] == instance.stream_origin
        and str(request["expected_user_id"]) == str(invitation.user_id)
        and invitation.server_instance_id == instance.server_instance_id
    )


def _lifecycle(
    operation_id: UUID, terminal_at: datetime, already_terminal: bool
) -> dict[str, object]:
    return {
        "contract_version": "v1",
        "schema_version": 1,
        "operation_id": str(operation_id),
        "outcome": "ALREADY_TERMINAL" if already_terminal else "APPLIED",
        "terminal_at": iso8601(terminal_at),
    }


def _invitation_terminal_at(invitation: EnrollmentInvitationRow, now: datetime) -> datetime:
    """Return the authoritative terminal instant for a set-like invite cancellation."""
    if invitation.cancelled_at is not None:
        return invitation.cancelled_at
    if invitation.consumed_at is not None:
        return invitation.consumed_at
    return invitation.expires_at if invitation.expires_at <= now else now


__all__ = ("ProfilePairingService",)
