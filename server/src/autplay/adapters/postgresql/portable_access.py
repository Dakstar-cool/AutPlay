"""R1 exact original-grant reader; no new connection, credential or authority."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from autplay.adapters.portable_account_crypto import canonical_request, validate_account_document
from autplay.domain.auth import (
    AccessTokenClaims,
    AccountRole,
    InvalidAccessTokenError,
    PortableAccessAdmission,
    Principal,
)
from autplay.domain.device_compatibility import DeviceCompatibilityError, NegotiationIdentity

from .device_platform_evidence import has_v2_key_evidence_conflict
from .models.account import DeviceRow, UserAccountRow, UserSessionRow
from .models.portable_account import PortableAccountChallengeRow, PortableAccountRow
from .models.profile_pairing import DeviceKeyBlockRow, TrustedDeviceKeyRow
from .models.public_id import PublicIdRegistrationRow
from .resource_limits import lock_resource_admission
from .sync_binding_v2 import current_deadline

_RECEIPT = frozenset(
    {
        "contract_version",
        "schema_version",
        "platform",
        "account_id",
        "device_id",
        "session_id",
        "session_family_id",
        "generation",
        "binding_commit_id",
        "token_id",
        "issued_at",
        "access_expires_at",
        "refresh_expires_at",
    }
)
_EVIDENCE = frozenset(
    {
        "purpose",
        "contract_version",
        "schema_version",
        "platform",
        "domain",
        "signature_algorithm",
        "signature_encoding",
        "request_sha256",
        "original_document_sha256",
        "root_proof_sha256",
        "account_id",
        "public_id",
        "root_spki_hex",
        "root_thumbprint_sha256",
        "device_spki_hex",
        "device_thumbprint_sha256",
        "binding_commit_id",
        "next_refresh_token_sha256",
        "device_name",
        "app_version",
        "account_role",
    }
)
_CHALLENGE = frozenset(
    {
        "contract_version",
        "schema_version",
        "platform",
        "challenge_id",
        "purpose",
        "account_id",
        "document_sha256",
        "server_instance_id",
        "identity_epoch",
        "identity_thumbprint_sha256",
        "api_origin",
        "stream_origin",
        "nonce",
        "expires_at",
        "actor_device_id",
        "actor_session_id",
        "action",
        "operation_id",
        "target_device_id",
    }
)


class SqlAlchemyPortableAccessReader:
    """Use the already owned auth Session, including B1's bounded serving session.

    M5 identity -> resource admission -> account/root/public ID -> receipt ->
    key/trust/block -> device/session follows the portable owner lock order.
    Only one exact original session is accepted; successors are never read.
    """

    def __init__(
        self,
        session: Session,
        identity: Callable[[Session], NegotiationIdentity] | None,
        *,
        read_only: bool = False,
    ) -> None:
        self._session, self._identity = session, identity
        self._read_only = read_only

    def authorize_original(self, claims: AccessTokenClaims) -> PortableAccessAdmission:
        if self._identity is None:
            raise InvalidAccessTokenError
        s, budget = self._session, current_deadline()
        try:
            budget.check()
            # M1 cannot take FOR UPDATE/advisory/resource locks in READ ONLY.
            # Keep all original predicates in ONE repeatable-read observation,
            # using its owned pool and committed public M5 callback. Ordinary
            # R1/B1 keeps the original READ COMMITTED lock order by default.
            # https://www.postgresql.org/docs/current/transaction-iso.html
            connection = s.connection(
                execution_options={
                    "isolation_level": "REPEATABLE READ" if self._read_only else "READ COMMITTED"
                }
            )
            budget.transaction_timeout_installed = False
            # The same deadline is inherited inside the two-slot B1 runtime.
            # A local owner additionally installs SQL bounds for ordinary auth.
            if self._read_only:
                connection.execute(text("SET TRANSACTION READ ONLY"))
            else:
                event.listen(connection, "before_cursor_execute", budget.before_statement)
            try:
                identity = self._identity(s)
                if not self._read_only:
                    lock_resource_admission(s)
                admission = self._read(claims, identity)
                budget.check()
                return admission
            finally:
                if not self._read_only:
                    event.remove(connection, "before_cursor_execute", budget.before_statement)
        except InvalidAccessTokenError:
            raise
        except (
            SQLAlchemyError,
            DeviceCompatibilityError,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            AttributeError,
            RuntimeError,
            UnsupportedAlgorithm,
        ):
            pass
        # Discard database/decoder details, including statement parameters.
        raise InvalidAccessTokenError from None

    def _read(
        self, claims: AccessTokenClaims, identity: NegotiationIdentity
    ) -> PortableAccessAdmission:
        s = self._session
        account = s.get(
            UserAccountRow,
            claims.user_id,
            with_for_update=not self._read_only,
            populate_existing=True,
        )
        root = s.get(
            PortableAccountRow,
            claims.user_id,
            with_for_update=not self._read_only,
            populate_existing=True,
        )
        registration = s.get(
            PublicIdRegistrationRow,
            claims.user_id,
            with_for_update=not self._read_only,
            populate_existing=True,
        )
        if not (account is not None and root is not None and registration is not None):
            raise InvalidAccessTokenError
        if not (
            account.status == "ACTIVE"
            and account.deleted_at is None
            and account.role in {"USER", "ADMIN", "OWNER"}
            and type(account.authority_generation) is int
            and account.authority_generation >= 1
            and type(root.revision) is int
            and root.revision >= 1
        ):
            raise InvalidAccessTokenError
        # Fetch at most two: both a missing and an ambiguous original fail closed.
        receipts = (
            select(PortableAccountChallengeRow)
            .where(
                PortableAccountChallengeRow.account_id == claims.user_id,
                PortableAccountChallengeRow.contract_version == "v2",
                PortableAccountChallengeRow.purpose.in_(("REGISTER", "LOGIN")),
                PortableAccountChallengeRow.receipt["session_id"].astext == str(claims.session_id),
            )
            .limit(2)
            .execution_options(populate_existing=True)
        )
        if not self._read_only:
            receipts = receipts.with_for_update()
        rows = list(s.scalars(receipts))
        if not (len(rows) == 1):
            raise InvalidAccessTokenError
        row = rows[0]
        receipt, evidence, challenge = row.receipt, row.original_request_evidence, row.document
        if not (
            type(receipt) is dict
            and set(receipt) == _RECEIPT
            and type(evidence) is dict
            and set(evidence) == _EVIDENCE
            and type(challenge) is dict
            and set(challenge) == _CHALLENGE
        ):
            raise InvalidAccessTokenError
        if not (
            len(canonical_request(receipt)) <= 4096 and len(canonical_request(evidence)) <= 8192
        ):
            raise InvalidAccessTokenError
        if not (
            row.platform in {"ANDROID", "MACOS"}
            and row.actor_device_id is None
            and row.actor_session_id is None
            and row.binding_commit_id is not None
            and row.authority_generation == account.authority_generation
            and row.root_revision == root.revision
        ):
            raise InvalidAccessTokenError
        for document in (receipt, evidence, challenge):
            if not (
                document["contract_version"] == "v2"
                and type(document["schema_version"]) is int
                and document["schema_version"] == 2
                and document["platform"] == row.platform
                and document["account_id"] == str(claims.user_id)
            ):
                raise InvalidAccessTokenError
        if not (
            receipt["device_id"] == str(claims.device_id)
            and receipt["session_id"] == str(claims.session_id)
            and receipt["session_family_id"] == str(claims.session_id)
            and type(receipt["generation"]) is int
            and receipt["generation"] == 0
            and receipt["token_id"] == str(claims.token_id)
            and receipt["binding_commit_id"] == str(row.binding_commit_id)
            and evidence["binding_commit_id"] == str(row.binding_commit_id)
            and evidence["purpose"] == row.purpose
            and challenge["purpose"] == row.purpose
            and evidence["account_role"] == account.role
            and (row.purpose != "REGISTER" or account.role == "USER")
            and evidence["domain"] == "autplay:portable-account:" + row.purpose.lower() + ":v2\n"
            and evidence["signature_algorithm"] == "ECDSA-P256-SHA256"
            and evidence["signature_encoding"] == "DER/standard-padded-base64"
        ):
            raise InvalidAccessTokenError
        for name, digest in (
            ("request_sha256", row.request_hash),
            ("original_document_sha256", row.original_document_sha256),
            ("root_proof_sha256", row.root_proof_sha256),
        ):
            if not (
                type(digest) is bytes and len(digest) == 32 and _hex(evidence[name], 32) == digest
            ):
                raise InvalidAccessTokenError
        root_key, root_thumb = (
            _hex(evidence["root_spki_hex"], 91),
            _hex(evidence["root_thumbprint_sha256"], 32),
        )
        device_key, device_thumb = (
            _hex(evidence["device_spki_hex"], 91),
            _hex(evidence["device_thumbprint_sha256"], 32),
        )
        refresh_digest = _hex(evidence["next_refresh_token_sha256"], 32)
        public = serialization.load_der_public_key(device_key)
        if not (
            isinstance(public, ec.EllipticCurvePublicKey)
            and isinstance(public.curve, ec.SECP256R1)
            and public.public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            == device_key
        ):
            raise InvalidAccessTokenError
        if not (
            hashlib.sha256(root_key).digest() == root_thumb
            and hashlib.sha256(device_key).digest() == device_thumb
            and root.public_key == root_key
            and root.key_thumbprint == root_thumb
        ):
            raise InvalidAccessTokenError
        account_id, public_id, passport_key = validate_account_document(root.document)
        if not (
            account_id == claims.user_id
            and public_id == registration.public_id
            and public_id == evidence["public_id"]
            and passport_key == root_key
            and _hex(challenge["document_sha256"], 32)
            == hashlib.sha256(canonical_request(root.document)).digest()
        ):
            raise InvalidAccessTokenError
        if not (
            challenge["challenge_id"] == str(row.challenge_id)
            and challenge["server_instance_id"] == str(identity.server_instance_id)
            and type(challenge["identity_epoch"]) is int
            and challenge["identity_epoch"] == identity.identity_epoch
            and _hex(challenge["identity_thumbprint_sha256"], 32) == identity.thumbprint
            and challenge["api_origin"] == identity.api_origin
            and challenge["stream_origin"] == identity.stream_origin
            and all(
                challenge[name] is None
                for name in (
                    "actor_device_id",
                    "actor_session_id",
                    "action",
                    "operation_id",
                    "target_device_id",
                )
            )
            and type(challenge["nonce"]) is str
            and re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge["nonce"]) is not None
            and len(base64.urlsafe_b64decode(challenge["nonce"] + "=")) == 32
            and _time(challenge["expires_at"]) == _utc(row.expires_at)
            and _utc(row.expires_at) == _utc(row.created_at) + timedelta(seconds=120)
        ):
            raise InvalidAccessTokenError
        if not (
            type(evidence["device_name"]) is str
            and 1 <= len(evidence["device_name"]) <= 120
            and type(evidence["app_version"]) is str
            and 1 <= len(evidence["app_version"]) <= 32
        ):
            raise InvalidAccessTokenError
        # Correlate persisted public evidence for integrity only. This is never
        # a reconstructed ROOT proof or a replacement for JWT/receipt authority.
        request = {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": row.platform,
            "challenge": challenge,
            "account": root.document,
            "device_public_key_spki_b64": base64.b64encode(device_key).decode("ascii"),
            "device_name": evidence["device_name"],
            "app_version": evidence["app_version"],
            "next_refresh_token_sha256": evidence["next_refresh_token_sha256"],
            "binding_commit_id": evidence["binding_commit_id"],
        }
        if not (hashlib.sha256(canonical_request(request)).digest() == row.request_hash):
            raise InvalidAccessTokenError
        block = s.get(
            DeviceKeyBlockRow,
            (claims.user_id, device_thumb),
            with_for_update=not self._read_only,
            populate_existing=True,
        )
        trust = s.get(
            TrustedDeviceKeyRow,
            (claims.user_id, device_thumb),
            with_for_update=not self._read_only,
            populate_existing=True,
        )
        if not (
            (block is None or block.unblocked_at is not None)
            and (
                trust is None
                or (
                    trust.removed_at is None
                    and trust.revision >= 1
                    and trust.device_public_key_spki == device_key
                    and (
                        (trust.contract_version == "v1" and trust.platform is None)
                        or (trust.contract_version == "v2" and trust.platform == row.platform)
                    )
                )
            )
            and not has_v2_key_evidence_conflict(
                s, device_thumb, public_key=device_key, platform=row.platform
            )
        ):
            raise InvalidAccessTokenError
        device = s.get(
            DeviceRow, claims.device_id, with_for_update=not self._read_only, populate_existing=True
        )
        session = s.get(
            UserSessionRow,
            claims.session_id,
            with_for_update=not self._read_only,
            populate_existing=True,
        )
        if not (
            device is not None
            and session is not None
            and device.user_id == claims.user_id
            and device.platform == row.platform
            and device.public_key == device_key
            and device.public_key_thumbprint_sha256 == device_thumb
            and device.revoked_at is None
            and session.user_id == claims.user_id
            and session.device_id == claims.device_id
            and session.session_mode == "V2"
            and session.family_id == session.session_id
            and type(session.generation) is int
            and session.generation == 0
            and session.refresh_token_hash == refresh_digest
            and session.revoked_at is None
        ):
            raise InvalidAccessTokenError
        issued, expires, refresh_expires = (
            _time(receipt[name])
            for name in ("issued_at", "access_expires_at", "refresh_expires_at")
        )
        if not (
            issued == _utc(session.issued_at)
            and refresh_expires == _utc(session.expires_at)
            and _utc(session.last_rotated_at) == issued
            and _utc(device.created_at) <= issued
            and _utc(row.created_at) <= issued < _utc(row.expires_at)
            and expires == _utc(row.receipt_expires_at)
            and refresh_expires == issued + timedelta(days=90)
            and timedelta(0) < expires - issued <= timedelta(seconds=900)
            and claims.issued_at == _seconds(issued)
            and claims.not_before == _seconds(issued)
            and claims.expires_at == _seconds(expires)
        ):
            raise InvalidAccessTokenError
        principal = Principal(
            claims.user_id, claims.device_id, claims.session_id, AccountRole(account.role)
        )
        admission = PortableAccessAdmission(
            principal,
            claims.token_id,
            claims.issued_at,
            claims.not_before,
            claims.expires_at,
            expires,
            refresh_expires,
        )
        # Last blocking read, after every identity/account/receipt/key/session lock.
        now = _utc(s.scalar(select(func.clock_timestamp())))
        if not (admission.accepts(principal, claims, now)):
            raise InvalidAccessTokenError
        return admission


def _hex(value: object, size: int) -> bytes:
    if not (
        type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(size * 2) + r"}", value) is not None
    ):
        raise InvalidAccessTokenError
    return bytes.fromhex(value)


def _utc(value: object) -> datetime:
    if not (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    ):
        raise InvalidAccessTokenError
    return value.astimezone(UTC)


def _time(value: object) -> datetime:
    if not (type(value) is str and 1 <= len(value) <= 30):
        raise InvalidAccessTokenError
    return _utc(datetime.fromisoformat(value))


def _seconds(value: datetime) -> datetime:
    # Match Hs256AccessTokenCodec.issue exactly, including integer projection.
    return datetime.fromtimestamp(int(value.timestamp()), UTC)
