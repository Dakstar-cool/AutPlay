"""Frozen D1 REGISTER/LOGIN proof API; source-only, without service authority.

Trusted routes select PortableAccountPurposeV2. parse_portable_account_v2 accepts
only raw UTF-8 outer {request,proof}; verify_portable_account_v2 additionally
requires the separately loaded ORIGINAL stored v2 challenge as UTF-8 JSON bytes.
It compares their full JCS bytes, then verifies ROOT DER over domain || JCS(request).
No device signature, prehash, private-key operation or v1 fallback exists here.

Snapshots are transient and redacted, with no raw-request serializer. Only the
explicit canonical_passport_for_storage() accessor exposes the unchanged passport
for its EXISTING protected canonical storage authority. safe_evidence() is a
receipt projection, never a signed request, log/export document or account grant.
Grant-producing ROOT proofs are retained HASH-ONLY; no raw/encrypted proof copy.
Replay requires the complete incoming original, strict parsing and ROOT verification
against current authority, then full-original digest and DER-proof hash comparison
under service locks. A stored hash is neither authorization nor a signing input.
Services must revalidate current server identity/origins, current passport/root,
expiry, account state/role/admission, quotas, gates, binding commit, replay/receipt
retention and transaction authority. No custody K, HTTP, DB or issuance is added.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, SupportsIndex, cast
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from autplay.adapters.portable_account_crypto import (
    canonical_request,
    decode_b64,
    validate_account_document,
)

MAX_DOCUMENT_BYTES: Final = 16_384
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
_VERSION: Final = frozenset({"contract_version", "schema_version", "platform"})
_NULLS: Final = frozenset(
    {
        "actor_device_id",
        "actor_session_id",
        "action",
        "operation_id",
        "target_device_id",
    }
)
_CHALLENGE: Final = (
    _VERSION
    | _NULLS
    | {
        "server_instance_id",
        "identity_epoch",
        "identity_thumbprint_sha256",
        "api_origin",
        "stream_origin",
        "challenge_id",
        "purpose",
        "account_id",
        "document_sha256",
        "nonce",
        "expires_at",
    }
)
_REQUEST: Final = _VERSION | {
    "challenge",
    "account",
    "device_public_key_spki_b64",
    "device_name",
    "app_version",
    "next_refresh_token_sha256",
    "binding_commit_id",
}


class PortableAccountPurposeV2(Enum):
    REGISTER = "REGISTER"
    LOGIN = "LOGIN"


class PortableAccountPlatformV2(Enum):
    ANDROID = "ANDROID"
    MACOS = "MACOS"


_DOMAINS: Final = MappingProxyType(
    {
        PortableAccountPurposeV2.REGISTER: b"autplay:portable-account:register:v2\n",
        PortableAccountPurposeV2.LOGIN: b"autplay:portable-account:login:v2\n",
    }
)


class PortableAccountV2Error(ValueError):
    """Fixed redacted error; no decoder/crypto exception is retained as context."""

    def __init__(self, status: int = 400) -> None:
        self.status = status
        self.code = "account_authentication_failed" if status == 401 else "device_contract_invalid"
        super().__init__(self.code)


class _Invalid(Exception):
    pass


class _TooLarge(_Invalid):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class PortableServerEvidenceV2:
    server_instance_id: UUID
    identity_epoch: int
    identity_thumbprint_sha256: bytes
    api_origin: str
    stream_origin: str


@dataclass(frozen=True, slots=True, repr=False)
class PortableChallengeEvidenceV2:
    contract_version: str
    schema_version: int
    platform: PortableAccountPlatformV2
    purpose: PortableAccountPurposeV2
    server: PortableServerEvidenceV2
    challenge_id: UUID
    account_id: UUID
    document_sha256: bytes
    nonce: str
    expires_at: str


@dataclass(frozen=True, slots=True, repr=False)
class PortableReceiptEvidenceV2:
    """Allowlisted receipt data, without raw ROOT proof or passport/body copies.

    original_document_sha256 hashes the FULL original outer {request,proof}.
    request_sha256 hashes full JCS(request); neither digest is the signing input.
    root_proof_sha256 hashes the original canonical DER signature bytes, never
    its base64 text. It cannot authorize replay or reconstruct a ROOT signature.
    Raw device/recovery/deletion proofs keep their established historical role;
    grant-producing portable REGISTER/LOGIN ROOT proofs are HASH-ONLY on server.
    challenge preserves public nonce/identity/null-field semantics. Canonical
    passport storage stays separate under its existing authority and retention.
    No resulting device/session IDs or grants can be invented by this policy.
    """

    purpose: PortableAccountPurposeV2
    contract_version: str
    schema_version: int
    platform: PortableAccountPlatformV2
    domain: bytes
    signature_algorithm: str
    signature_encoding: str
    request_sha256: bytes
    original_document_sha256: bytes
    root_proof_sha256: bytes
    challenge: PortableChallengeEvidenceV2
    canonical_challenge: bytes
    account_id: UUID
    public_id: str
    root_spki: bytes
    root_thumbprint_sha256: bytes
    device_spki: bytes
    device_thumbprint_sha256: bytes
    binding_commit_id: UUID
    next_refresh_token_sha256: bytes
    device_name: str
    app_version: str


class _Immutable:
    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("portable snapshot requires policy parsing")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("immutable portable snapshot")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("immutable portable snapshot")

    def __repr__(self) -> str:
        return "<portable-account-v2 redacted>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        raise TypeError("portable snapshot serialization forbidden")


class ParsedPortableAccountV2(_Immutable):
    """Immutable syntax evidence, NOT a verified proof or current authority."""

    __slots__ = (
        "_canonical_challenge",
        "_canonical_passport",
        "_canonical_request",
        "account_id",
        "app_version",
        "binding_commit_id",
        "challenge",
        "device_name",
        "device_spki",
        "next_refresh_token_sha256",
        "original_document_sha256",
        "platform",
        "proof",
        "proof_b64",
        "public_id",
        "purpose",
        "request_sha256",
        "root_spki",
    )
    purpose: PortableAccountPurposeV2
    platform: PortableAccountPlatformV2
    challenge: PortableChallengeEvidenceV2
    account_id: UUID
    public_id: str
    root_spki: bytes
    device_spki: bytes
    binding_commit_id: UUID
    next_refresh_token_sha256: bytes
    device_name: str
    app_version: str
    proof: bytes
    proof_b64: str
    request_sha256: bytes
    original_document_sha256: bytes
    _canonical_request: bytes
    _canonical_challenge: bytes
    _canonical_passport: bytes

    def canonical_passport_for_storage(self) -> bytes:
        """Sensitive passport-v1 JCS, only for existing protected storage authority.

        Never log or include in ordinary exports. This is not a serializer for
        the outer signed request and grants no permission to persist a receipt.
        """
        return self._canonical_passport


class VerifiedPortableAccountV2(_Immutable):
    """ROOT possession and exact stored challenge only; no service account grant."""

    __slots__ = ("parsed",)
    parsed: ParsedPortableAccountV2

    def safe_evidence(self) -> PortableReceiptEvidenceV2:
        """Hash-only ROOT retention; never persist this object's transient parent.

        Replay must parse and ROOT-verify the complete incoming original against
        current authority, then compare full-original digest and DER-proof hash
        under service locks before any grant or expired-family retirement.
        """
        p = self.parsed
        return PortableReceiptEvidenceV2(
            purpose=p.purpose,
            contract_version="v2",
            schema_version=2,
            platform=p.platform,
            domain=_DOMAINS[p.purpose],
            signature_algorithm="ECDSA-P256-SHA256",
            signature_encoding="DER/standard-padded-base64",
            request_sha256=p.request_sha256,
            original_document_sha256=p.original_document_sha256,
            root_proof_sha256=hashlib.sha256(p.proof).digest(),
            challenge=p.challenge,
            canonical_challenge=p._canonical_challenge,
            account_id=p.account_id,
            public_id=p.public_id,
            root_spki=p.root_spki,
            root_thumbprint_sha256=hashlib.sha256(p.root_spki).digest(),
            device_spki=p.device_spki,
            device_thumbprint_sha256=hashlib.sha256(p.device_spki).digest(),
            binding_commit_id=p.binding_commit_id,
            next_refresh_token_sha256=p.next_refresh_token_sha256,
            device_name=p.device_name,
            app_version=p.app_version,
        )


def _snapshot[Snapshot: _Immutable](kind: type[Snapshot], **values: object) -> Snapshot:
    result = object.__new__(kind)
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result


def parse_portable_account_v2(
    purpose: PortableAccountPurposeV2,
    raw: bytes,
) -> ParsedPortableAccountV2:
    """Frozen API: trusted route enum plus <=16384 raw bytes, never a mapping.

    All parse/Unicode/crypto failures become fixed 400, oversize becomes 413.
    No defaults, normalization, coercion or hash/signature omissions are applied.
    """
    status = 400
    try:
        if type(purpose) is not PortableAccountPurposeV2:
            raise _Invalid()
        outer = _object(_json(raw), {"request", "proof"})
        request = _object(outer["request"], _REQUEST)
        platform = _version(request)
        challenge, challenge_bytes = _challenge(request["challenge"], purpose)
        if challenge.platform is not platform:
            raise _Invalid()
        passport = _object(request["account"])
        # Use the unchanged v1 validator and canonicalization, not a new passport recipe.
        account_id, public_id, root_spki = validate_account_document(passport)
        _key(root_spki)
        passport_bytes = canonical_request(passport)
        if (
            account_id != challenge.account_id
            or hashlib.sha256(passport_bytes).digest() != challenge.document_sha256
        ):
            raise _Invalid()
        device_spki = decode_b64(request["device_public_key_spki_b64"], 91, 91)
        _key(device_spki)
        device_name = _text(request["device_name"], 120)
        app_version = _text(request["app_version"], 32)
        binding_commit_id = _uuid(request["binding_commit_id"])
        refresh_hash = _hash(request["next_refresh_token_sha256"])
        signature = decode_b64(outer["proof"], 8, 72)
        r, s = utils.decode_dss_signature(signature)
        if utils.encode_dss_signature(r, s) != signature:
            raise _Invalid()
        payload = canonical_request(request)
        full = canonical_request(outer)
        return _snapshot(
            ParsedPortableAccountV2,
            purpose=purpose,
            platform=platform,
            challenge=challenge,
            account_id=account_id,
            public_id=public_id,
            root_spki=root_spki,
            device_spki=device_spki,
            binding_commit_id=binding_commit_id,
            next_refresh_token_sha256=refresh_hash,
            device_name=device_name,
            app_version=app_version,
            proof=signature,
            proof_b64=outer["proof"],
            request_sha256=hashlib.sha256(payload).digest(),
            original_document_sha256=hashlib.sha256(full).digest(),
            _canonical_request=payload,
            _canonical_challenge=challenge_bytes,
            _canonical_passport=passport_bytes,
        )
    except _TooLarge:
        status = 413
    except _Invalid, ValueError, TypeError, OverflowError, RecursionError, UnsupportedAlgorithm:
        pass
    # Outside except: even __context__ must not retain JSONDecodeError.doc.
    raise PortableAccountV2Error(status)


def verify_portable_account_v2(
    parsed: ParsedPortableAccountV2,
    stored_challenge_raw: bytes,
) -> VerifiedPortableAccountV2:
    """Frozen API: compare independently stored v2 challenge, then ROOT proof.

    Stored evidence must come from the service's authoritative challenge row,
    never the incoming request. Both challenges are strictly parsed. Compare
    byte-for-byte after JCS, including version/platform/purpose and all nulls.
    Expiry/current authority/first-commit and receipt gates belong to the service.
    Verification uses ECDSA MESSAGE(domain || full JCS(request)), never a digest.
    """
    try:
        if type(parsed) is not ParsedPortableAccountV2:
            raise _Invalid()
        _, stored_bytes = _challenge(_json(stored_challenge_raw), parsed.purpose)
        if stored_bytes != parsed._canonical_challenge:
            raise _Invalid()
        _key(parsed.root_spki).verify(
            parsed.proof,
            _DOMAINS[parsed.purpose] + parsed._canonical_request,
            ec.ECDSA(hashes.SHA256()),
        )
        return _snapshot(VerifiedPortableAccountV2, parsed=parsed)
    except (
        _Invalid,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
        InvalidSignature,
        UnsupportedAlgorithm,
    ):
        pass
    raise PortableAccountV2Error(401)


def _json(raw: bytes) -> Any:
    if type(raw) is not bytes:
        raise _Invalid()
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _TooLarge()
    text = raw.decode("utf-8", errors="strict")
    depth, quoted, escaped = 0, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > 4:
                raise _Invalid()
        elif character in "]}":
            depth -= 1
    return json.loads(
        text,
        object_pairs_hook=_pairs,
        parse_int=_integer,
        parse_float=_reject_number,
        parse_constant=_reject_number,
    )


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        name.encode("utf-8", errors="strict")
        if name in result:
            raise _Invalid()
        result[name] = value
    return result


def _integer(token: str) -> int:
    if len(token) > 16 or not 0 <= int(token) <= MAX_SAFE_INTEGER:
        raise _Invalid()
    return int(token)


def _reject_number(token: str) -> Any:
    raise _Invalid()


def _object(value: Any, members: set[str] | frozenset[str] | None = None) -> dict[str, Any]:
    if type(value) is not dict or (members is not None and set(value) != members):
        raise _Invalid()
    return value


def _version(document: dict[str, Any]) -> PortableAccountPlatformV2:
    if (
        document["contract_version"] != "v2"
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 2
        or type(document["platform"]) is not str
    ):
        raise _Invalid()
    return PortableAccountPlatformV2(document["platform"])


def _text(value: Any, maximum: int) -> str:
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise _Invalid()
    value.encode("utf-8", errors="strict")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _Invalid()
    return value


def _uuid(value: Any) -> UUID:
    if type(value) is not str or str(UUID(value)) != value:
        raise _Invalid()
    return UUID(value)


def _hash(value: Any) -> bytes:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise _Invalid()
    return bytes.fromhex(value)


def _key(spki: bytes) -> ec.EllipticCurvePublicKey:
    key = serialization.load_der_public_key(spki)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise _Invalid()
    if (
        key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        != spki
    ):
        raise _Invalid()
    return key


def _origin(value: Any) -> str:
    value = _text(value, 2048)
    if not value.isascii() or any(character.isspace() for character in value):
        raise _Invalid()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(character in value for character in "?#@\\%")
        or value != parsed.scheme + "://" + parsed.netloc
    ):
        raise _Invalid()
    host = parsed.hostname
    if ":" in host:
        canonical_host = "[" + ipaddress.IPv6Address(host).compressed + "]"
    elif all(character in "0123456789." for character in host):
        canonical_host = str(ipaddress.IPv4Address(host))
    else:
        if len(host) > 253 or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
            for label in host.rstrip(".").split(".")
        ):
            raise _Invalid()
        canonical_host = host
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise _Invalid()
    if parsed.netloc != canonical_host + (":" + str(port) if port is not None else ""):
        raise _Invalid()
    # HTTPS/release vs opt-in private debug HTTP is a service transport gate.
    return cast(str, value)


def _challenge(
    value: Any,
    purpose: PortableAccountPurposeV2,
) -> tuple[PortableChallengeEvidenceV2, bytes]:
    document = _object(value, _CHALLENGE)
    platform = _version(document)
    if document["purpose"] != purpose.value or any(document[name] is not None for name in _NULLS):
        raise _Invalid()
    epoch = document["identity_epoch"]
    if type(epoch) is not int or not 1 <= epoch <= MAX_SAFE_INTEGER:
        raise _Invalid()
    expires = _text(document["expires_at"], 30)
    if (
        re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z", expires
        )
        is None
    ):
        raise _Invalid()
    datetime(
        int(expires[:4]),
        int(expires[5:7]),
        int(expires[8:10]),
        int(expires[11:13]),
        int(expires[14:16]),
        int(expires[17:19]),
    )
    nonce = document["nonce"]
    if type(nonce) is not str or re.fullmatch(r"[A-Za-z0-9_-]{43}", nonce) is None:
        raise _Invalid()
    decoded = base64.b64decode(nonce + "=", altchars=b"-_", validate=True)
    if (
        len(decoded) != 32
        or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != nonce
    ):
        raise _Invalid()
    server = PortableServerEvidenceV2(
        _uuid(document["server_instance_id"]),
        epoch,
        _hash(document["identity_thumbprint_sha256"]),
        _origin(document["api_origin"]),
        _origin(document["stream_origin"]),
    )
    evidence = PortableChallengeEvidenceV2(
        "v2",
        2,
        platform,
        purpose,
        server,
        _uuid(document["challenge_id"]),
        _uuid(document["account_id"]),
        _hash(document["document_sha256"]),
        nonce,
        expires,
    )
    return evidence, canonical_request(document)


__all__ = (
    "ParsedPortableAccountV2",
    "PortableAccountPlatformV2",
    "PortableAccountPurposeV2",
    "PortableAccountV2Error",
    "PortableChallengeEvidenceV2",
    "PortableReceiptEvidenceV2",
    "PortableServerEvidenceV2",
    "VerifiedPortableAccountV2",
    "parse_portable_account_v2",
    "verify_portable_account_v2",
)
