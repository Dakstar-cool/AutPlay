"""D1 portable ROOT transactions; persistence retains hash-only authorization.

The complete incoming original is parsed and ROOT-verified on every invocation.
Only safe_evidence() and original grant identities/times reach the receipt row.
No stored digest, reconstructed request or redacted body is an authorization.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from cryptography.exceptions import UnsupportedAlgorithm
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from autplay.adapters.portable_account_crypto import canonical_request, validate_account_document
from autplay.adapters.portable_account_crypto_v2 import (
    MAX_DOCUMENT_BYTES,
    MAX_SAFE_INTEGER,
    ParsedPortableAccountV2,
    PortableAccountPurposeV2,
    PortableAccountV2Error,
    PortableReceiptEvidenceV2,
    parse_portable_account_v2,
    verify_portable_account_v2,
)
from autplay.adapters.postgresql.device_identifier_reservations import device_identifier_reserved
from autplay.adapters.postgresql.device_platform_evidence import has_v2_key_evidence_conflict
from autplay.adapters.postgresql.models.account import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.portable_account import (
    PortableAccountChallengeRow,
    PortableAccountRow,
)
from autplay.adapters.postgresql.models.profile_pairing import DeviceKeyBlockRow
from autplay.adapters.postgresql.models.public_id import PublicIdRegistrationRow
from autplay.adapters.postgresql.resource_limits import (
    lock_resource_admission,
    require_device_capacity,
    terminate_retired_session,
)
from autplay.application.portable_account import (
    PortableAccountService,
    _database_now,
    portable_v2_identifier_reserved,
)
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.device_compatibility import DeviceCompatibilityError, NegotiationIdentity
from autplay.domain.profile_pairing import ProfilePairingError, iso8601


class DevicePortableServiceV2:
    def __init__(
        self,
        portable: PortableAccountService,
        *,
        identity: ProfilePairingService,
        enabled: Callable[[], bool],
        portable_enabled: Callable[[], bool],
    ) -> None:
        self._portable, self._identity = portable, identity
        self._enabled, self._portable_enabled = enabled, portable_enabled
        if not timedelta(0) < portable.access_ttl <= timedelta(seconds=3600):
            raise ValueError("portable access TTL is outside the contract bound")

    def register_challenge(self, raw: bytes, *, source: str = "internal") -> dict[str, object]:
        return self._challenge(PortableAccountPurposeV2.REGISTER, raw, source)

    def login_challenge(self, raw: bytes, *, source: str = "internal") -> dict[str, object]:
        return self._challenge(PortableAccountPurposeV2.LOGIN, raw, source)

    def register(self, raw: bytes, *, source: str = "internal") -> dict[str, object]:
        return self._complete(PortableAccountPurposeV2.REGISTER, raw, source)

    def login(self, raw: bytes, *, source: str = "internal") -> dict[str, object]:
        return self._complete(PortableAccountPurposeV2.LOGIN, raw, source)

    def _locked_identity(self, s: Session) -> NegotiationIdentity:
        try:
            return self._identity.device_contract_identity(s, _now())
        except RuntimeError, ValueError:
            pass
        raise DeviceCompatibilityError()

    def _require_enabled(self) -> None:
        if not self._enabled() or not self._portable_enabled():
            raise DeviceCompatibilityError()

    def _challenge(
        self,
        purpose: PortableAccountPurposeV2,
        raw: bytes,
        source: str,
    ) -> dict[str, object]:
        # Exactly the v1 source HMAC/window/table and independently committed
        # counter: rejected syntax/lookups/proofs cannot refund an attempt.
        self._portable._admit_attempt(source)
        body = parse_portable_challenge_v2(purpose, raw)
        self._require_enabled()
        with self._portable.sessions.begin() as s:
            identity = self._locked_identity(s)
            lock_resource_admission(s)
            now = _now()
            self._portable._prune(s, now)
            if purpose is PortableAccountPurposeV2.LOGIN:
                registration = s.scalar(
                    select(PublicIdRegistrationRow)
                    .where(
                        PublicIdRegistrationRow.public_id == body["public_id"],
                    )
                    .with_for_update()
                )
                root = (
                    None
                    if registration is None
                    else s.get(
                        PortableAccountRow,
                        registration.user_id,
                        with_for_update=True,
                    )
                )
                if root is None:
                    raise ProfilePairingError("account_authentication_failed")
                passport = root.document
            else:
                passport = cast(dict[str, object], body["account"])
            account_id, public_id, root_key = validate_account_document(passport)
            _authority(s, purpose, account_id, public_id, root_key, canonical_request(passport))
            challenge_id = uuid4()
            require_portable_ids_available(s, challenge_id)
            self._require_enabled()
            now = _database_now(s)
            challenge: dict[str, object] = {
                "contract_version": "v2",
                "schema_version": 2,
                "platform": body["platform"],
                "challenge_id": str(challenge_id),
                "purpose": purpose.value,
                "account_id": str(account_id),
                "document_sha256": hashlib.sha256(canonical_request(passport)).hexdigest(),
                "server_instance_id": str(identity.server_instance_id),
                "identity_epoch": identity.identity_epoch,
                "identity_thumbprint_sha256": identity.thumbprint.hex(),
                "api_origin": identity.api_origin,
                "stream_origin": identity.stream_origin,
                "nonce": secrets.token_urlsafe(32),
                "expires_at": iso8601(now + timedelta(seconds=120)),
                "actor_device_id": None,
                "actor_session_id": None,
                "action": None,
                "operation_id": None,
                "target_device_id": None,
            }
            s.add(
                PortableAccountChallengeRow(
                    challenge_id=challenge_id,
                    purpose=purpose.value,
                    source_key=hmac.digest(self._portable.source_secret, source.encode(), "sha256"),
                    account_id=account_id,
                    actor_device_id=None,
                    actor_session_id=None,
                    document=challenge,
                    created_at=now,
                    expires_at=now + timedelta(seconds=120),
                    contract_version="v2",
                    platform=body["platform"],
                )
            )
            s.flush()
            return _bounded(
                {
                    "contract_version": "v2",
                    "schema_version": 2,
                    "platform": body["platform"],
                    "challenge": challenge,
                    "account": passport,
                }
            )

    def _complete(
        self,
        purpose: PortableAccountPurposeV2,
        raw: bytes,
        source: str,
    ) -> dict[str, object]:
        self._portable._admit_attempt(source)
        parsed = parse_portable_account_v2(purpose, raw)
        expired = False
        result: dict[str, object] | None = None
        with self._portable.sessions.begin() as s:
            identity = self._locked_identity(s)
            lock_resource_admission(s)
            _require_identity(parsed, identity)
            account, root = _authority(
                s,
                purpose,
                parsed.account_id,
                parsed.public_id,
                parsed.root_spki,
                parsed.canonical_passport_for_storage(),
            )
            row = s.get(
                PortableAccountChallengeRow, parsed.challenge.challenge_id, with_for_update=True
            )
            if row is None:
                if datetime.fromisoformat(parsed.challenge.expires_at) <= _now():
                    raise ProfilePairingError("account_challenge_expired")
                raise ProfilePairingError("account_authentication_failed")
            if (
                row.contract_version != "v2"
                or row.platform != parsed.platform.value
                or row.purpose != purpose.value
                or row.account_id != parsed.account_id
                or row.actor_device_id is not None
                or row.actor_session_id is not None
            ):
                raise ProfilePairingError("account_authentication_failed")
            require_portable_ids_available(s, row.challenge_id, own_challenge_id=row.challenge_id)
            # Current root/passport equality is checked above, before ROOT verification.
            # This receives the original incoming proof, never a stored projection.
            evidence = verify_portable_account_v2(
                parsed, canonical_request(row.document)
            ).safe_evidence()
            _require_key(s, evidence)
            now = _database_now(s)
            if row.receipt is not None:
                if account is None or root is None:
                    raise ProfilePairingError("account_authentication_failed")
                _require_exact(row, evidence, account, root)
                require_portable_ids_available(
                    s, evidence.binding_commit_id, own_challenge_id=row.challenge_id
                )
                device, credential = _result_binding(s, row, evidence, account)
                now = _database_now(s)
                if row.receipt_expires_at is None:
                    raise ProfilePairingError("operation_conflict")
                if row.receipt_expires_at <= now:
                    # Commit family retirement before raising the fixed terminal error.
                    # Rotation can revoke the original generation; all successors still
                    # belong to this exact result family. No access grant is produced.
                    _retire_family(s, account.user_id, device.device_id, credential.session_id, now)
                    expired = True
                else:
                    if credential.revoked_at is not None or credential.expires_at <= now:
                        raise ProfilePairingError("account_authentication_failed")
                    result = self._result(row, account, credential, now)
            else:
                self._require_enabled()
                if row.expires_at <= now:
                    raise ProfilePairingError("account_challenge_expired")
                require_portable_ids_available(s, evidence.binding_commit_id)
                _require_refresh_unused(s, evidence.next_refresh_token_sha256)
                if account is None:
                    count = (
                        s.scalar(
                            select(func.count())
                            .select_from(UserAccountRow)
                            .where(
                                UserAccountRow.status == "ACTIVE",
                            )
                        )
                        or 0
                    )
                    if count >= 20:
                        raise ProfilePairingError("account_limit_reached")
                existing_device = _existing_device(s, evidence)
                if existing_device is None:
                    require_device_capacity(s, evidence.account_id)
                # Replay/retirement deliberately precede both independent gates.
                self._require_enabled()
                now = _database_now(s)
                if row.expires_at <= now:
                    raise ProfilePairingError("account_challenge_expired")
                if account is None:
                    account = UserAccountRow(
                        user_id=evidence.account_id,
                        display_name=evidence.public_id,
                        role="USER",
                        status="ACTIVE",
                        created_at=now,
                        updated_at=now,
                    )
                    s.add(account)
                    s.flush()
                if root is None:
                    # The sole passport copy uses the existing unchanged passport-v1
                    # authority, independently of the hash-only receipt projection.
                    root = PortableAccountRow(
                        user_id=account.user_id,
                        public_key=evidence.root_spki,
                        key_thumbprint=evidence.root_thumbprint_sha256,
                        document=json.loads(parsed.canonical_passport_for_storage()),
                        revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                    s.add(root)
                if s.get(PublicIdRegistrationRow, account.user_id) is None:
                    s.add(
                        PublicIdRegistrationRow(
                            user_id=account.user_id,
                            public_id=evidence.public_id,
                            created_at=now,
                        )
                    )
                credential = self._bind(s, account, existing_device, evidence, now)
                row.request_hash = evidence.request_sha256
                row.original_document_sha256 = evidence.original_document_sha256
                row.root_proof_sha256 = evidence.root_proof_sha256
                row.binding_commit_id = evidence.binding_commit_id
                row.authority_generation, row.root_revision = (
                    account.authority_generation,
                    root.revision,
                )
                row.original_request_evidence = _storage_evidence(evidence, account.role)
                row.receipt_expires_at = now + self._portable.access_ttl
                row.receipt = {
                    "contract_version": "v2",
                    "schema_version": 2,
                    "platform": evidence.platform.value,
                    "account_id": str(account.user_id),
                    "device_id": str(credential.device_id),
                    "session_id": str(credential.session_id),
                    "session_family_id": str(credential.session_id),
                    "generation": 0,
                    "binding_commit_id": str(evidence.binding_commit_id),
                    "token_id": str(uuid4()),
                    "issued_at": iso8601(now),
                    "access_expires_at": iso8601(row.receipt_expires_at),
                    "refresh_expires_at": iso8601(credential.expires_at),
                }
                s.flush()
                self._identity._audit(
                    s,
                    now,
                    _principal(account, credential),
                    "portable_account." + purpose.value.lower(),
                    "USER_ACCOUNT",
                    account.user_id,
                )
                now = _database_now(s)
                if row.expires_at <= now:
                    raise ProfilePairingError("account_challenge_expired")
                result = self._result(row, account, credential, now)
        if expired:
            raise ProfilePairingError("account_receipt_expired")
        if result is None:
            raise DeviceCompatibilityError()
        return result

    @staticmethod
    def _bind(
        s: Session,
        account: UserAccountRow,
        device: DeviceRow | None,
        evidence: PortableReceiptEvidenceV2,
        now: datetime,
    ) -> UserSessionRow:
        if device is None:
            device = DeviceRow(
                user_id=account.user_id,
                device_id=uuid4(),
                device_name=evidence.device_name,
                platform=evidence.platform.value,
                app_version=evidence.app_version,
                public_key=evidence.device_spki,
                public_key_thumbprint_sha256=evidence.device_thumbprint_sha256,
                created_at=now,
                updated_at=now,
            )
            s.add(device)
            s.flush()
        prior = s.scalars(
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
        for previous in prior:
            terminate_retired_session(s, previous, now)
        session_id = uuid4()
        credential = UserSessionRow(
            user_id=account.user_id,
            device_id=device.device_id,
            session_id=session_id,
            refresh_token_hash=evidence.next_refresh_token_sha256,
            issued_at=now,
            expires_at=now + timedelta(days=90),
            last_rotated_at=now,
            session_mode="V2",
            family_id=session_id,
            generation=0,
        )
        s.add(credential)
        s.flush()
        return credential

    def _result(
        self,
        row: PortableAccountChallengeRow,
        account: UserAccountRow,
        credential: UserSessionRow,
        now: datetime,
    ) -> dict[str, object]:
        if row.receipt is None or row.receipt_expires_at is None or row.receipt_expires_at <= now:
            raise ProfilePairingError("account_receipt_expired")
        result = dict(row.receipt)
        # Original times and token ID survive restarts and configuration changes.
        # Never persist the resulting access token or choose another refresh secret.
        result["access_token"] = self._portable.access_tokens.issue(
            _principal(account, credential),
            token_id=UUID(str(result.pop("token_id"))),
            issued_at=datetime.fromisoformat(str(result["issued_at"])),
            expires_at=datetime.fromisoformat(str(result["access_expires_at"])),
        )
        if not 32 <= len(str(result["access_token"])) <= 8192:
            raise DeviceCompatibilityError()
        return _bounded(result)


def _authority(
    s: Session,
    purpose: PortableAccountPurposeV2,
    account_id: UUID,
    public_id: str,
    root_key: bytes,
    passport_bytes: bytes,
) -> tuple[UserAccountRow | None, PortableAccountRow | None]:
    account = s.get(UserAccountRow, account_id, with_for_update=True)
    root = s.get(PortableAccountRow, account_id, with_for_update=True)
    if account is not None and (
        account.status != "ACTIVE"
        or account.deleted_at is not None
        or account.role not in {"USER", "ADMIN", "OWNER"}
    ):
        raise ProfilePairingError("account_authentication_failed")
    if (
        purpose is PortableAccountPurposeV2.REGISTER
        and account is not None
        and account.role != "USER"
    ):
        raise ProfilePairingError("account_authentication_failed")
    if account is not None and root is None:
        raise ProfilePairingError("account_claim_required")
    if purpose is PortableAccountPurposeV2.LOGIN and (account is None or root is None):
        raise ProfilePairingError("account_authentication_failed")
    if root is not None and (
        root.public_key != root_key
        or canonical_request(root.document) != passport_bytes
        or root.key_thumbprint != hashlib.sha256(root_key).digest()
    ):
        raise ProfilePairingError("account_identity_conflict")
    registration = s.get(PublicIdRegistrationRow, account_id, with_for_update=True)
    collision = s.scalar(
        select(PublicIdRegistrationRow)
        .where(
            PublicIdRegistrationRow.public_id == public_id,
        )
        .with_for_update()
    )
    if collision is not None and collision.user_id != account_id:
        raise ProfilePairingError("public_id_unavailable")
    if registration is not None and registration.public_id != public_id:
        raise ProfilePairingError("account_identity_conflict")
    if root is not None and registration is None:
        raise ProfilePairingError("account_identity_conflict")
    return account, root


def _require_identity(parsed: ParsedPortableAccountV2, identity: NegotiationIdentity) -> None:
    expected = parsed.challenge.server
    if (
        expected.server_instance_id != identity.server_instance_id
        or expected.identity_epoch != identity.identity_epoch
        or expected.identity_thumbprint_sha256 != identity.thumbprint
        or expected.api_origin != identity.api_origin
        or expected.stream_origin != identity.stream_origin
    ):
        raise ProfilePairingError("identity_changed")


def _require_key(s: Session, evidence: PortableReceiptEvidenceV2) -> None:
    block = s.get(
        DeviceKeyBlockRow,
        (evidence.account_id, evidence.device_thumbprint_sha256),
        with_for_update=True,
    )
    if block is not None and block.unblocked_at is None:
        raise ProfilePairingError("device_key_blocked")
    if has_v2_key_evidence_conflict(
        s,
        evidence.device_thumbprint_sha256,
        public_key=evidence.device_spki,
        platform=evidence.platform.value,
    ):
        raise ProfilePairingError("account_authentication_failed")


def _existing_device(s: Session, evidence: PortableReceiptEvidenceV2) -> DeviceRow | None:
    devices = s.scalars(
        select(DeviceRow)
        .where(
            DeviceRow.user_id == evidence.account_id,
            or_(
                DeviceRow.public_key_thumbprint_sha256 == evidence.device_thumbprint_sha256,
                DeviceRow.public_key == evidence.device_spki,
            ),
            DeviceRow.revoked_at.is_(None),
        )
        .with_for_update()
    ).all()
    if len(devices) > 1:
        raise ProfilePairingError("account_authentication_failed")
    device = devices[0] if devices else None
    if device is not None and (
        device.public_key != evidence.device_spki
        or device.platform != evidence.platform.value
        or device.public_key_thumbprint_sha256 != evidence.device_thumbprint_sha256
    ):
        raise ProfilePairingError("account_authentication_failed")
    return device


def _storage_evidence(e: PortableReceiptEvidenceV2, role: str) -> dict[str, object]:
    # Explicit scalar allowlist. Do not replace with asdict(), __dict__, parsed,
    # raw request, proof, proof_b64, signature bytes or an encrypted fallback.
    return {
        "purpose": e.purpose.value,
        "contract_version": e.contract_version,
        "schema_version": e.schema_version,
        "platform": e.platform.value,
        "domain": e.domain.decode("ascii"),
        "signature_algorithm": e.signature_algorithm,
        "signature_encoding": e.signature_encoding,
        "request_sha256": e.request_sha256.hex(),
        "original_document_sha256": e.original_document_sha256.hex(),
        "root_proof_sha256": e.root_proof_sha256.hex(),
        "account_id": str(e.account_id),
        "public_id": e.public_id,
        "root_spki_hex": e.root_spki.hex(),
        "root_thumbprint_sha256": e.root_thumbprint_sha256.hex(),
        "device_spki_hex": e.device_spki.hex(),
        "device_thumbprint_sha256": e.device_thumbprint_sha256.hex(),
        "binding_commit_id": str(e.binding_commit_id),
        "next_refresh_token_sha256": e.next_refresh_token_sha256.hex(),
        "device_name": e.device_name,
        "app_version": e.app_version,
        "account_role": role,
    }


def _require_exact(
    row: PortableAccountChallengeRow,
    evidence: PortableReceiptEvidenceV2,
    account: UserAccountRow,
    root: PortableAccountRow,
) -> None:
    if (
        row.request_hash != evidence.request_sha256
        or row.original_document_sha256 != evidence.original_document_sha256
        or row.root_proof_sha256 != evidence.root_proof_sha256
        or row.binding_commit_id != evidence.binding_commit_id
        or row.authority_generation != account.authority_generation
        or row.root_revision != root.revision
        or row.original_request_evidence != _storage_evidence(evidence, account.role)
    ):
        raise ProfilePairingError("operation_conflict")


def _result_binding(
    s: Session,
    row: PortableAccountChallengeRow,
    e: PortableReceiptEvidenceV2,
    account: UserAccountRow,
) -> tuple[DeviceRow, UserSessionRow]:
    receipt = row.receipt
    if receipt is None:
        raise ProfilePairingError("account_authentication_failed")
    device = s.get(DeviceRow, UUID(str(receipt["device_id"])), with_for_update=True)
    credential = s.get(UserSessionRow, UUID(str(receipt["session_id"])), with_for_update=True)
    if (
        device is None
        or device.user_id != account.user_id
        or device.revoked_at is not None
        or device.platform != e.platform.value
        or device.public_key != e.device_spki
        or device.public_key_thumbprint_sha256 != e.device_thumbprint_sha256
        or credential is None
        or credential.user_id != account.user_id
        or credential.device_id != device.device_id
        or credential.session_mode != "V2"
        or credential.family_id != credential.session_id
        or credential.generation != 0
        or credential.refresh_token_hash != e.next_refresh_token_sha256
        or receipt["account_id"] != str(account.user_id)
        or receipt["platform"] != e.platform.value
        or receipt["contract_version"] != "v2"
        or type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != 2
        or receipt["session_family_id"] != str(credential.session_id)
        or receipt["binding_commit_id"] != str(e.binding_commit_id)
        or receipt["generation"] != 0
        or type(receipt["generation"]) is not int
        or datetime.fromisoformat(str(receipt["issued_at"])) != credential.issued_at
        or datetime.fromisoformat(str(receipt["refresh_expires_at"])) != credential.expires_at
        or datetime.fromisoformat(str(receipt["access_expires_at"])) != row.receipt_expires_at
        or credential.expires_at != credential.issued_at + timedelta(days=90)
        or row.receipt_expires_at is None
        or not credential.issued_at < row.receipt_expires_at
        or row.receipt_expires_at > credential.issued_at + timedelta(seconds=3600)
    ):
        raise ProfilePairingError("account_authentication_failed")
    return device, credential


def _retire_family(
    s: Session, account_id: UUID, device_id: UUID, family_id: UUID, now: datetime
) -> None:
    credentials = s.scalars(
        select(UserSessionRow)
        .where(
            UserSessionRow.user_id == account_id,
            UserSessionRow.device_id == device_id,
            UserSessionRow.session_mode == "V2",
            or_(UserSessionRow.session_id == family_id, UserSessionRow.family_id == family_id),
            UserSessionRow.revoked_at.is_(None),
        )
        .with_for_update()
    ).all()
    for credential in credentials:
        credential.revoked_at = now
    for credential in credentials:
        terminate_retired_session(s, credential, now)


def require_portable_ids_available(
    s: Session,
    identifier: UUID,
    *,
    own_challenge_id: UUID | None = None,
) -> None:
    """Use the common SQL + independent-ledger policy under the admission lock.

    Only this exact challenge/result may be excluded. ROOT verification, original
    digest/proof-hash equality and all current authority checks remain in _complete.
    """
    if device_identifier_reserved(s, identifier, own_portable_challenge_id=own_challenge_id):
        raise ProfilePairingError("operation_conflict")


def _require_refresh_unused(s: Session, digest: bytes) -> None:
    if (
        s.scalar(
            select(UserSessionRow.session_id).where(UserSessionRow.refresh_token_hash == digest)
        )
        is not None
    ):
        raise ProfilePairingError("operation_conflict")


def parse_portable_challenge_v2(purpose: PortableAccountPurposeV2, raw: bytes) -> dict[str, object]:
    """Closed, raw duplicate-safe public challenge parser; no v1 body stripping."""
    status = 400
    try:
        if type(raw) is not bytes or type(purpose) is not PortableAccountPurposeV2:
            raise ValueError()
        if len(raw) > MAX_DOCUMENT_BYTES:
            status = 413
            raise ValueError()
        body = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_int=_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        field = "account" if purpose is PortableAccountPurposeV2.REGISTER else "public_id"
        if type(body) is not dict or set(body) != {
            "contract_version",
            "schema_version",
            "platform",
            field,
        }:
            raise ValueError()
        if (
            body["contract_version"] != "v2"
            or type(body["schema_version"]) is not int
            or body["schema_version"] != 2
            or body["platform"] not in {"ANDROID", "MACOS"}
        ):
            raise ValueError()
        if purpose is PortableAccountPurposeV2.REGISTER:
            if type(body["account"]) is not dict:
                raise ValueError()
            validate_account_document(body["account"])
        elif (
            type(body["public_id"]) is not str
            or re.fullmatch(r"[a-z0-9_]{3,24}", body["public_id"]) is None
        ):
            raise ValueError()
        canonical_request(body)  # Also rejects invalid Unicode at every value/depth.
        return body
    except ValueError, TypeError, KeyError, OverflowError, RecursionError, UnsupportedAlgorithm:
        pass
    # Do not keep JSONDecodeError.doc or decoder details in exception context.
    raise PortableAccountV2Error(status)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        name.encode("utf-8", errors="strict")
        if name in result:
            raise ValueError()
        result[name] = value
    return result


def _integer(token: str) -> int:
    if len(token) > 16 or not 0 <= int(token) <= MAX_SAFE_INTEGER:
        raise ValueError()
    return int(token)


def _reject_number(token: str) -> Any:
    raise ValueError()


def _principal(account: UserAccountRow, credential: UserSessionRow) -> Principal:
    return Principal(
        account.user_id, credential.device_id, credential.session_id, AccountRole(account.role)
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _bounded(result: dict[str, object]) -> dict[str, object]:
    if len(canonical_request(result)) > MAX_DOCUMENT_BYTES:
        raise DeviceCompatibilityError()
    return result


__all__ = (
    "DevicePortableServiceV2",
    "parse_portable_challenge_v2",
    "portable_v2_identifier_reserved",
    "require_portable_ids_available",
)
