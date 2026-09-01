"""PA2 account invitation authority, isolated from M5 existing-account enrollment."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models.account import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.audit import AuditEventRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.public_access import (
    AccountInvitationRow,
    AccountProvisioningLinkRow,
    AccountProvisioningOperationReceiptRow,
    AccountProvisioningRateWindowRow,
    AccountRegistrationReceiptRow,
)
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    iso8601,
    public_key_thumbprint,
    verify_p1363,
)

_DOMAIN: Final = "AutPlay account registration v1\n"
_NO_TOKEN: Final = "public_access_unavailable"
_SESSION_TTL: Final = timedelta(days=90)
_BIDI_CONTROL_CODEPOINTS: Final = frozenset(
    {
        0x061C,
        0x200E,
        0x200F,
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    }
)


class PublicAccessError(RuntimeError):
    """Deliberately non-disclosing public-access failure."""

    def __init__(self, code: str = _NO_TOKEN) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PublicAccessService:
    sessions: sessionmaker[Session]
    access_tokens: Hs256AccessTokenCodec
    access_ttl: timedelta
    source_hmac_secret: bytes

    def create_invitation(
        self, actor: Principal, body: dict[str, Any]
    ) -> tuple[dict[str, object], bool]:
        body = dict(body)
        try:
            body["account_display_name"] = normalize_account_display_name(
                body["account_display_name"]
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PublicAccessError() from error
        now = _now()
        operation_id = UUID(str(body["operation_id"]))
        command_hash = canonical_sha256(body)
        # Exact replays do not normally consume issuance budget. The check is repeated under the
        # same operation lock below so a concurrent first application remains exact.
        with self.sessions.begin() as session:
            self._require_bootstrap_owner(session, actor)
            _lock_uuid(session, b"OWNER_OPERATION", operation_id)
            existing = session.get(
                AccountProvisioningOperationReceiptRow, operation_id, with_for_update=True
            )
            if existing is not None:
                self._exact_operation(existing, actor.user_id, "CREATE", command_hash)
                invitation = session.get(AccountInvitationRow, existing.target_id)
                if invitation is None:
                    raise PublicAccessError()
                return _invitation_view(invitation), True
        self._issue_gate(actor, now)
        with self.sessions.begin() as session:
            self._require_bootstrap_owner(session, actor)
            _lock_uuid(session, b"OWNER_OPERATION", operation_id)
            existing = session.get(
                AccountProvisioningOperationReceiptRow, operation_id, with_for_update=True
            )
            if existing is not None:
                self._exact_operation(existing, actor.user_id, "CREATE", command_hash)
                invitation = session.get(AccountInvitationRow, existing.target_id)
                if invitation is None:
                    raise PublicAccessError()
                return _invitation_view(invitation), True
            # Serialize the server-wide invitation cap before counting. Different operation IDs
            # otherwise hold independent advisory locks and could both admit the fifth row.
            server = self._server(session)
            active = session.scalar(
                select(func.count())
                .select_from(AccountInvitationRow)
                .where(
                    AccountInvitationRow.cancelled_at.is_(None),
                    AccountInvitationRow.consumed_at.is_(None),
                    AccountInvitationRow.expires_at > now,
                )
            )
            if int(active or 0) >= 5:
                raise PublicAccessError("invitation_limit_reached")
            invitation_id, secret = uuid4(), _new_bearer()
            row = AccountInvitationRow(
                invitation_id=invitation_id,
                issued_by_user_id=actor.user_id,
                display_name=str(body["account_display_name"]),
                secret_sha256=_sha(secret.encode("ascii")),
                issued_at=now,
                expires_at=now + timedelta(seconds=int(body["expires_in_seconds"])),
            )
            session.add(row)
            result = _invitation_document(row, server, secret)
            replay_view = dict(result)
            replay_view.pop("invitation_secret")
            session.add(
                AccountProvisioningOperationReceiptRow(
                    operation_id=operation_id,
                    actor_user_id=actor.user_id,
                    action="CREATE",
                    target_id=invitation_id,
                    command_sha256=command_hash,
                    outcome="APPLIED",
                    result_json=json.dumps(replay_view, sort_keys=True, separators=(",", ":")),
                    created_at=now,
                )
            )
            self._audit(
                session,
                now,
                "ADMIN",
                actor.user_id,
                None,
                "public_access.invitation_created",
                "ACCOUNT_INVITATION",
                invitation_id,
                operation_id,
            )
            return result, False

    def list_invitations(
        self, actor: Principal, limit: int, cursor: str | None = None
    ) -> dict[str, object]:
        if not 1 <= limit <= 100:
            raise PublicAccessError("invalid_cursor")
        with self.sessions() as session:
            self._require_bootstrap_owner(session, actor)
            statement = select(AccountInvitationRow).where(
                AccountInvitationRow.issued_by_user_id == actor.user_id
            )
            if cursor is not None:
                issued_at, invitation_id = _decode_cursor(
                    self.source_hmac_secret, cursor, "INVITATIONS", actor.user_id
                )
                statement = statement.where(
                    or_(
                        AccountInvitationRow.issued_at < issued_at,
                        and_(
                            AccountInvitationRow.issued_at == issued_at,
                            AccountInvitationRow.invitation_id < invitation_id,
                        ),
                    )
                )
            rows = list(
                session.scalars(
                    statement.order_by(
                        AccountInvitationRow.issued_at.desc(),
                        AccountInvitationRow.invitation_id.desc(),
                    ).limit(limit + 1)
                ).all()
            )
            next_cursor = None
            if len(rows) > limit:
                rows.pop()
                marker = rows[-1]
                next_cursor = _encode_cursor(
                    self.source_hmac_secret,
                    "INVITATIONS",
                    actor.user_id,
                    marker.issued_at,
                    marker.invitation_id,
                )
            links = {
                invitation_id: user_id
                for invitation_id, user_id in session.execute(
                    select(
                        AccountProvisioningLinkRow.invitation_id,
                        AccountProvisioningLinkRow.user_id,
                    ).where(
                        AccountProvisioningLinkRow.invitation_id.in_(
                            [row.invitation_id for row in rows]
                        )
                    )
                ).all()
            }
            return {
                "contract_version": "v1",
                "schema_version": 1,
                "items": [
                    _invitation_view(row, invited_user_id=links.get(row.invitation_id))
                    for row in rows
                ],
                "next_cursor": next_cursor,
            }

    def cancel_invitation(
        self, actor: Principal, invitation_id: UUID, body: dict[str, Any]
    ) -> dict[str, object]:
        return self._lifecycle(actor, invitation_id, body, "CANCEL")

    def list_accounts(
        self, actor: Principal, limit: int, cursor: str | None = None
    ) -> dict[str, object]:
        if not 1 <= limit <= 100:
            raise PublicAccessError("invalid_cursor")
        with self.sessions() as session:
            self._require_bootstrap_owner(session, actor)
            statement = (
                select(AccountProvisioningLinkRow, UserAccountRow)
                .join(UserAccountRow, UserAccountRow.user_id == AccountProvisioningLinkRow.user_id)
                .where(AccountProvisioningLinkRow.issued_by_user_id == actor.user_id)
            )
            if cursor is not None:
                created_at, user_id = _decode_cursor(
                    self.source_hmac_secret, cursor, "ACCOUNTS", actor.user_id
                )
                statement = statement.where(
                    or_(
                        AccountProvisioningLinkRow.created_at < created_at,
                        and_(
                            AccountProvisioningLinkRow.created_at == created_at,
                            AccountProvisioningLinkRow.user_id < user_id,
                        ),
                    )
                )
            rows = list(
                session.execute(
                    statement.order_by(
                        AccountProvisioningLinkRow.created_at.desc(),
                        AccountProvisioningLinkRow.user_id.desc(),
                    ).limit(limit + 1)
                ).all()
            )
            next_cursor = None
            if len(rows) > limit:
                rows.pop()
                marker_link, _ = rows[-1]
                next_cursor = _encode_cursor(
                    self.source_hmac_secret,
                    "ACCOUNTS",
                    actor.user_id,
                    marker_link.created_at,
                    marker_link.user_id,
                )
            return {
                "contract_version": "v1",
                "schema_version": 1,
                "items": [
                    {
                        "user_id": str(account.user_id),
                        "provisioning_invitation_id": str(link.invitation_id),
                        "display_name": account.display_name,
                        "role": "USER",
                        "status": account.status,
                        "created_at": iso8601(link.created_at),
                        "disabled_at": iso8601(account.updated_at)
                        if account.status == "DISABLED"
                        else None,
                    }
                    for link, account in rows
                ],
                "next_cursor": next_cursor,
            }

    def disable_account(
        self, actor: Principal, user_id: UUID, body: dict[str, Any]
    ) -> dict[str, object]:
        return self._lifecycle(actor, user_id, body, "DISABLE")

    def redeem(self, body: dict[str, Any], source: str | None) -> tuple[dict[str, object], bool]:
        now = _now()
        try:
            invitation_id = UUID(str(body["invitation_id"]))
        except KeyError, ValueError, TypeError:
            self._failed_server_rate(now)
            raise PublicAccessError() from None
        # Persist every applicable budget before cryptographic or authority checks. A canonical
        # source is optional until PA3 configures an exact trusted edge; forwarded headers are
        # never accepted here.
        self._redeem_gates(invitation_id, source, now)
        try:
            secret = str(body["invitation_secret"])
            secret_hash = _sha(secret.encode("ascii"))
            request_hash = canonical_sha256(
                body, omit=frozenset({"request_sha256", "device_signature_b64url"})
            )
            if request_hash.hex() != str(body["request_sha256"]):
                raise ValueError
            spki = base64.b64decode(str(body["device_public_key_spki_b64"]), validate=True)
            thumb = public_key_thumbprint(spki)
            if thumb.hex() != str(body["device_key_thumbprint_sha256"]):
                raise ValueError
            verify_p1363(spki, _DOMAIN, request_hash, str(body["device_signature_b64url"]))
            refresh_hash = bytes.fromhex(str(body["next_refresh_token_sha256"]))
            if len(refresh_hash) != 32:
                raise ValueError
        except KeyError, ValueError, TypeError, ProfilePairingError, PublicAccessError:
            raise PublicAccessError() from None
        registration_id = UUID(str(body["registration_id"]))
        with self.sessions.begin() as session:
            _lock_uuid(session, b"REGISTRATION", registration_id)
            receipt = session.get(
                AccountRegistrationReceiptRow, registration_id, with_for_update=True
            )
            if receipt is not None:
                if (
                    receipt.request_sha256 != request_hash
                    or receipt.invitation_secret_sha256 != secret_hash
                    or receipt.device_key_thumbprint_sha256 != thumb
                ):
                    raise PublicAccessError("registration_conflict")
                return self._registration_response(session, receipt, replayed=True), True
            invitation = session.get(AccountInvitationRow, invitation_id, with_for_update=True)
            if (
                invitation is None
                or invitation.secret_sha256 != secret_hash
                or invitation.cancelled_at is not None
                or invitation.consumed_at is not None
                or invitation.expires_at <= now
            ):
                raise PublicAccessError()
            server = self._server(session)
            if not _matches_server(body, server, invitation.display_name):
                raise PublicAccessError()
            account_count = session.scalar(
                select(func.count())
                .select_from(UserAccountRow)
                .where(UserAccountRow.status == "ACTIVE")
            )
            if int(account_count or 0) >= 20:
                raise PublicAccessError("account_limit_reached")
            user_id, device_id, session_id = uuid4(), uuid4(), uuid4()
            account = UserAccountRow(
                user_id=user_id,
                display_name=invitation.display_name,
                role="USER",
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            )
            # These storage rows deliberately have no ORM relationships.  Flush
            # the account first so PostgreSQL's immediate device FK is satisfied
            # while the complete registration still commits atomically below.
            session.add(account)
            session.flush()
            device = DeviceRow(
                device_id=device_id,
                user_id=user_id,
                device_name=str(body["device_name"]),
                platform="ANDROID",
                app_version=str(body["app_version"]),
                public_key=spki,
                public_key_thumbprint_sha256=thumb,
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
            user_session = UserSessionRow(
                session_id=session_id,
                user_id=user_id,
                device_id=device_id,
                refresh_token_hash=refresh_hash,
                issued_at=now,
                expires_at=now + _SESSION_TTL,
                last_rotated_at=now,
                family_id=session_id,
                generation=0,
                session_mode="V2",
            )
            receipt = AccountRegistrationReceiptRow(
                registration_id=registration_id,
                invitation_id=invitation_id,
                invitation_secret_sha256=secret_hash,
                request_sha256=request_hash,
                device_key_thumbprint_sha256=thumb,
                user_id=user_id,
                device_id=device_id,
                session_id=session_id,
                binding_commit_id=UUID(str(body["binding_commit_id"])),
                receipt_expires_at=user_session.expires_at + timedelta(minutes=5),
                created_at=now,
            )
            invitation.consumed_at = now
            session.add_all(
                (
                    device,
                    user_session,
                    AccountProvisioningLinkRow(
                        user_id=user_id,
                        invitation_id=invitation_id,
                        issued_by_user_id=invitation.issued_by_user_id,
                        created_at=now,
                    ),
                    receipt,
                )
            )
            self._audit(
                session,
                now,
                "SYSTEM",
                None,
                device_id,
                "public_access.account_registered",
                "USER_ACCOUNT",
                user_id,
                registration_id,
            )
            session.flush()
            return self._registration_response(session, receipt, replayed=False), False

    def cleanup(self, limit: int = 500) -> int:
        return cleanup_expired_public_access(self.sessions, limit=limit)

    def _lifecycle(
        self, actor: Principal, target_id: UUID, body: dict[str, Any], action: str
    ) -> dict[str, object]:
        now, operation_id, command_hash = (
            _now(),
            UUID(str(body["operation_id"])),
            canonical_sha256(body),
        )
        with self.sessions.begin() as session:
            self._require_bootstrap_owner(session, actor)
            _lock_uuid(session, b"OWNER_OPERATION", operation_id)
            receipt = session.get(
                AccountProvisioningOperationReceiptRow, operation_id, with_for_update=True
            )
            if receipt is not None:
                self._exact_operation(receipt, actor.user_id, action, command_hash, target_id)
                return cast(dict[str, object], json.loads(receipt.result_json))
            if action == "CANCEL":
                row = session.get(AccountInvitationRow, target_id, with_for_update=True)
                if row is None or row.issued_by_user_id != actor.user_id:
                    raise PublicAccessError("not_found")
                if row.cancelled_at is None and row.consumed_at is None:
                    row.cancelled_at = now
                result: dict[str, object] = {
                    "contract_version": "v1",
                    "schema_version": 1,
                    "operation_id": str(operation_id),
                    "target_type": "ACCOUNT_INVITATION",
                    "target_id": str(target_id),
                    "outcome": "APPLIED" if row.cancelled_at == now else "ALREADY_TERMINAL",
                    "terminal_state": "CANCELLED",
                    "occurred_at": iso8601(now),
                }
            else:
                link = session.get(AccountProvisioningLinkRow, target_id, with_for_update=True)
                account = session.get(UserAccountRow, target_id, with_for_update=True)
                if link is None or account is None or link.issued_by_user_id != actor.user_id:
                    raise PublicAccessError("not_found")
                if account.status == "ACTIVE":
                    account.status, account.updated_at = "DISABLED", now
                    session.query(UserSessionRow).filter(
                        UserSessionRow.user_id == target_id, UserSessionRow.revoked_at.is_(None)
                    ).update({UserSessionRow.revoked_at: now}, synchronize_session=False)
                    outcome = "APPLIED"
                else:
                    outcome = "ALREADY_TERMINAL"
                result = {
                    "contract_version": "v1",
                    "schema_version": 1,
                    "operation_id": str(operation_id),
                    "target_type": "INVITED_ACCOUNT",
                    "target_id": str(target_id),
                    "outcome": outcome,
                    "terminal_state": "DISABLED",
                    "occurred_at": iso8601(now),
                }
            session.add(
                AccountProvisioningOperationReceiptRow(
                    operation_id=operation_id,
                    actor_user_id=actor.user_id,
                    action=action,
                    target_id=target_id,
                    command_sha256=command_hash,
                    outcome=str(result["outcome"]),
                    result_json=json.dumps(result, sort_keys=True, separators=(",", ":")),
                    created_at=now,
                )
            )
            self._audit(
                session,
                now,
                "ADMIN",
                actor.user_id,
                None,
                "public_access.invitation_cancelled"
                if action == "CANCEL"
                else "public_access.account_disabled",
                "ACCOUNT_INVITATION" if action == "CANCEL" else "USER_ACCOUNT",
                target_id,
                operation_id,
            )
            return result

    def _require_bootstrap_owner(self, session: Session, actor: Principal) -> None:
        if actor.role is not AccountRole.OWNER:
            raise PublicAccessError("unauthorized")
        owners = session.scalars(
            select(UserAccountRow)
            .where(UserAccountRow.role == "OWNER", UserAccountRow.status == "ACTIVE")
            .with_for_update()
        ).all()
        if len(owners) != 1 or owners[0].user_id != actor.user_id:
            raise PublicAccessError("unauthorized")

    def _exact_operation(
        self,
        row: AccountProvisioningOperationReceiptRow,
        actor: UUID,
        action: str,
        digest: bytes,
        target: UUID | None = None,
    ) -> None:
        if (
            row.actor_user_id != actor
            or row.action != action
            or row.command_sha256 != digest
            or (target is not None and row.target_id != target)
        ):
            raise PublicAccessError("operation_conflict")

    def _server(self, session: Session) -> ServerInstanceRow:
        row = session.scalar(select(ServerInstanceRow).with_for_update())
        if row is None:
            raise PublicAccessError()
        return row

    def _consume_rate(
        self,
        session: Session,
        scope: str,
        material: bytes,
        maximum: int,
        window: timedelta,
        now: datetime,
    ) -> bool:
        key = _sha(scope.encode() + material)
        # Serialize the first insert as well as increments. PostgreSQL advisory locks
        # are transaction-scoped and avoid a select-then-insert unique-key race.
        lock_key = int.from_bytes(key[:8], byteorder="big", signed=True)
        session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        row = session.get(AccountProvisioningRateWindowRow, key, with_for_update=True)
        if row is None or row.expires_at <= now:
            if row is None:
                row = AccountProvisioningRateWindowRow(
                    rate_key_sha256=key,
                    scope=scope,
                    window_started_at=now,
                    expires_at=now + window,
                    attempt_count=1,
                )
                session.add(row)
            else:
                row.window_started_at, row.expires_at, row.attempt_count = now, now + window, 1
        else:
            row.attempt_count += 1
        return row.attempt_count > maximum

    def _failed_server_rate(self, now: datetime) -> None:
        with self.sessions.begin() as session:
            self._consume_rate(session, "REDEEM_SERVER", b"server", 30, timedelta(minutes=15), now)

    def _issue_gate(self, actor: Principal, now: datetime) -> None:
        with self.sessions.begin() as session:
            self._require_bootstrap_owner(session, actor)
            exceeded = self._consume_rate(
                session, "ISSUE_OWNER", actor.user_id.bytes, 10, timedelta(hours=1), now
            )
        if exceeded:
            raise PublicAccessError("registration_rate_limited")

    def _redeem_gates(self, invitation_id: UUID, source: str | None, now: datetime) -> None:
        with self.sessions.begin() as session:
            exceeded = self._consume_rate(
                session, "REDEEM_SERVER", b"server", 30, timedelta(minutes=15), now
            )
            if source is not None:
                exceeded = (
                    self._consume_rate(
                        session,
                        "REDEEM_SOURCE",
                        _source_token(self.source_hmac_secret, source),
                        10,
                        timedelta(minutes=15),
                        now,
                    )
                    or exceeded
                )
            exceeded = (
                self._consume_rate(
                    session,
                    "REDEEM_INVITATION",
                    invitation_id.bytes,
                    5,
                    timedelta(minutes=15),
                    now,
                )
                or exceeded
            )
        if exceeded:
            raise PublicAccessError("registration_rate_limited")

    def _registration_response(
        self, session: Session, receipt: AccountRegistrationReceiptRow, *, replayed: bool
    ) -> dict[str, object]:
        # Lock and reload mutable authority before minting any first/replayed bearer. This
        # serializes account disable and session/device revocation against token issuance.
        account = session.get(UserAccountRow, receipt.user_id, with_for_update=True)
        device = session.get(DeviceRow, receipt.device_id, with_for_update=True)
        user_session = session.get(UserSessionRow, receipt.session_id, with_for_update=True)
        server = self._server(session)
        if (
            account is None
            or device is None
            or user_session is None
            or account.status != "ACTIVE"
            or device.revoked_at is not None
            or user_session.revoked_at is not None
            or user_session.expires_at <= _now()
            or receipt.receipt_expires_at <= _now()
        ):
            raise PublicAccessError()
        now = _now()
        principal = Principal(
            account.user_id, device.device_id, user_session.session_id, AccountRole.USER
        )
        expires = now + self.access_ttl
        token = self.access_tokens.issue(
            principal, token_id=uuid4(), issued_at=now, expires_at=expires
        )
        return {
            "contract_version": "v1",
            "schema_version": 1,
            "registration_id": str(receipt.registration_id),
            "binding_commit_id": str(receipt.binding_commit_id),
            "server_instance_id": str(server.server_instance_id),
            "user_id": str(account.user_id),
            "account_display_name": account.display_name,
            "account_role": "USER",
            "device_id": str(device.device_id),
            "session_id": str(user_session.session_id),
            "refresh_generation": 0,
            "refresh_absolute_expires_at": iso8601(user_session.expires_at),
            "receipt_expires_at": iso8601(receipt.receipt_expires_at),
            "access_token": token,
            "access_expires_at": iso8601(expires),
            "replayed": replayed,
        }

    @staticmethod
    def _audit(
        session: Session,
        now: datetime,
        actor_type: str,
        actor_user_id: UUID | None,
        actor_device_id: UUID | None,
        action: str,
        target_type: str,
        target_id: UUID,
        request_id: UUID,
    ) -> None:
        session.add(
            AuditEventRow(
                audit_event_id=uuid4(),
                occurred_at=now,
                actor_type=actor_type,
                actor_user_id=actor_user_id,
                actor_device_id=actor_device_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                request_id=request_id,
                metadata_sanitized={"public_access": True},
            )
        )


def normalize_account_display_name(value: object) -> str:
    """Apply the exact PA1 display-name boundary before hashing or persistence."""

    if not isinstance(value, str):
        raise TypeError("account display name must be a string")
    normalized = value.strip()
    if not 1 <= len(normalized) <= 120:
        raise ValueError("account display name length is invalid")
    for character in normalized:
        if unicodedata.category(character) in {"Cc", "Cs"}:
            raise ValueError("account display name contains a control character")
        if ord(character) in _BIDI_CONTROL_CODEPOINTS:
            raise ValueError("account display name contains a bidi control character")
    return normalized


def _now() -> datetime:
    return datetime.now(UTC)


def _sha(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _new_bearer() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def _source_token(secret: bytes, source: str) -> bytes:
    return hmac.new(
        secret,
        b"AutPlay public-access source v1\x00" + source.encode("utf-8", "replace"),
        hashlib.sha256,
    ).digest()


def _lock_uuid(session: Session, domain: bytes, value: UUID) -> None:
    digest = _sha(b"AutPlay public-access advisory lock v1\x00" + domain + value.bytes)
    session.execute(
        select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], byteorder="big", signed=True)))
    )


def _encode_cursor(
    secret: bytes, kind: str, actor_user_id: UUID, instant: datetime, target_id: UUID
) -> str:
    payload = json.dumps(
        {
            "actor": str(actor_user_id),
            "id": str(target_id),
            "kind": kind,
            "time": iso8601(instant),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    signature = hmac.new(
        secret, b"AutPlay public-access cursor v1\x00" + payload, hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def _decode_cursor(
    secret: bytes, value: str, kind: str, actor_user_id: UUID
) -> tuple[datetime, UUID]:
    try:
        encoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload, signature = encoded[:-32], encoded[-32:]
        expected = hmac.new(
            secret, b"AutPlay public-access cursor v1\x00" + payload, hashlib.sha256
        ).digest()
        document = json.loads(payload)
        if (
            not hmac.compare_digest(signature, expected)
            or not isinstance(document, dict)
            or document.get("kind") != kind
            or document.get("actor") != str(actor_user_id)
        ):
            raise ValueError
        instant = datetime.fromisoformat(str(document["time"]).replace("Z", "+00:00"))
        target_id = UUID(str(document["id"]))
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError
        return instant, target_id
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise PublicAccessError("invalid_cursor") from error


def _matches_server(body: dict[str, Any], server: ServerInstanceRow, display_name: str) -> bool:
    return (
        str(body["expected_server_instance_id"]) == str(server.server_instance_id)
        and int(body["expected_identity_epoch"]) == server.identity_epoch
        and str(body["expected_identity_thumbprint_sha256"])
        == server.identity_thumbprint_sha256.hex()
        and str(body["expected_api_origin"]) == server.api_origin
        and str(body["expected_stream_origin"]) == server.stream_origin
        and str(body["expected_account_display_name"]) == display_name
    )


def _invitation_document(
    row: AccountInvitationRow, server: ServerInstanceRow, secret: str
) -> dict[str, object]:
    return {
        "contract_version": "v1",
        "schema_version": 1,
        "invitation_id": str(row.invitation_id),
        "server_instance_id": str(server.server_instance_id),
        "identity_epoch": server.identity_epoch,
        "identity_thumbprint_sha256": server.identity_thumbprint_sha256.hex(),
        "api_origin": server.api_origin,
        "stream_origin": server.stream_origin,
        "account_display_name": row.display_name,
        "account_role": "USER",
        "issued_at": iso8601(row.issued_at),
        "expires_at": iso8601(row.expires_at),
        "invitation_secret": secret,
        "secret_handling": "DISPLAY_ONCE_QR_OR_AUTPLAYINVITE_NO_URL_NO_CLIPBOARD_NO_LOG",
    }


def _invitation_view(
    row: AccountInvitationRow, *, invited_user_id: UUID | None = None
) -> dict[str, object]:
    state = (
        "CONSUMED"
        if row.consumed_at
        else "CANCELLED"
        if row.cancelled_at
        else "EXPIRED"
        if row.expires_at <= _now()
        else "ACTIVE"
    )
    terminal_at = row.consumed_at or row.cancelled_at
    return {
        "contract_version": "v1",
        "schema_version": 1,
        "invitation_id": str(row.invitation_id),
        "account_display_name": row.display_name,
        "account_role": "USER",
        "state": state,
        "issued_at": iso8601(row.issued_at),
        "expires_at": iso8601(row.expires_at),
        "terminal_at": iso8601(terminal_at) if terminal_at is not None else None,
        "invited_user_id": str(invited_user_id) if invited_user_id is not None else None,
    }


def cleanup_expired_public_access(sessions: sessionmaker[Session], *, limit: int = 500) -> int:
    """Delete one bounded batch of expired retry/rate and terminal invitation evidence."""
    if not 1 <= limit <= 10_000:
        raise ValueError("cleanup limit must be within 1..10000")
    now, total = _now(), 0
    with sessions.begin() as session:
        for model, column in (
            (AccountRegistrationReceiptRow, AccountRegistrationReceiptRow.receipt_expires_at),
            (AccountProvisioningRateWindowRow, AccountProvisioningRateWindowRow.expires_at),
        ):
            remaining = limit - total
            if remaining <= 0:
                break
            rows = session.scalars(
                select(model).where(column <= now).order_by(column).limit(remaining)
            ).all()
            for row in rows:
                session.delete(row)
            total += len(rows)
        remaining = limit - total
        if remaining > 0:
            cutoff = now - timedelta(days=30)
            # Consumed invitations are retained through their provisioning link. Only cancelled
            # or never-consumed expired invitations are eligible, and FK-linked rows fail closed.
            invitations = session.scalars(
                select(AccountInvitationRow)
                .where(
                    AccountInvitationRow.consumed_at.is_(None),
                    (
                        (AccountInvitationRow.cancelled_at.is_not(None))
                        & (AccountInvitationRow.cancelled_at <= cutoff)
                    )
                    | (
                        AccountInvitationRow.cancelled_at.is_(None)
                        & (AccountInvitationRow.expires_at <= cutoff)
                    ),
                    ~select(AccountRegistrationReceiptRow.registration_id)
                    .where(
                        AccountRegistrationReceiptRow.invitation_id
                        == AccountInvitationRow.invitation_id
                    )
                    .exists(),
                    ~select(AccountProvisioningLinkRow.user_id)
                    .where(
                        AccountProvisioningLinkRow.invitation_id
                        == AccountInvitationRow.invitation_id
                    )
                    .exists(),
                )
                .order_by(AccountInvitationRow.expires_at, AccountInvitationRow.invitation_id)
                .limit(remaining)
                .with_for_update(skip_locked=True)
            ).all()
            for invitation in invitations:
                session.delete(invitation)
            total += len(invitations)
    return total


__all__ = ("PublicAccessError", "PublicAccessService", "cleanup_expired_public_access")
