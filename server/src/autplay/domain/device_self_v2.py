"""Pure D1 self-pairing v2 body, recipient proof and comparison-code policy.

Parsing is not authorization. START/DECISION name the SOURCE platform but have
no device proof; their bearer, exact device/session family and authority generation
belong to the future service. CLAIM/POLL/EXCHANGE name the RECIPIENT platform.
Their verified proof establishes only possession of an explicitly supplied key.
For poll/exchange that key/platform MUST come from locked original claim evidence.

The service also owns path/body equality, identity/origin trust (including release
HTTPS policy), original claim/version/platform agreement, nonce/time freshness,
secret-header possession, state/revision/SAS approval, account/role/capacity/rates,
expiry, revocation and flag-off/terminal replay rules. No header credentials enter
this module. self.status is a source-authorized GET, with no body or proof here.

Returned snapshots are nonsecret original-request evidence, not receipts granting
authority. Retention and replay still require service checks: reparse/reverify the
complete incoming original and compare full digest, exact proof and locked facts.
Do not strip a signature or verify a redacted projection. K custody is not defined.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Final, Literal, overload
from uuid import UUID

import rfc8785
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    verify_p1363,
)
from autplay.domain.self_device_pairing import PairingCommand, SelfDevicePairing, SelfPairingError

MAX_BODY_BYTES: Final = 8192
_MAX_INTEGER: Final = 9007199254740991
_SAS_DOMAIN: Final = b"autplay:device-compatibility:self:sas:v2\n"
_HASH_OMISSIONS: Final = frozenset({"request_sha256", "device_signature_b64url"})


class SelfOperation(Enum):
    """Chosen by a trusted route, never derived from a caller's domain string."""

    START = "self.start"
    CLAIM = "self.claim"
    POLL = "self.poll"
    DECISION = "self.decision"
    EXCHANGE = "self.exchange"


class SelfPlatform(Enum):
    ANDROID = "ANDROID"
    MACOS = "MACOS"


class SelfPlatformRole(Enum):
    """Describes the field's role, not the caller's authority."""

    SOURCE = "SOURCE"
    RECIPIENT = "RECIPIENT"


_DOMAINS: Final = MappingProxyType(
    {
        SelfOperation.CLAIM: "autplay:device-compatibility:self:claim:v2\n",
        SelfOperation.POLL: "autplay:device-compatibility:self:poll:v2\n",
        SelfOperation.EXCHANGE: "autplay:device-compatibility:self:exchange:v2\n",
    }
)
_TRUST: Final = frozenset(
    {
        "expected_server_instance_id",
        "expected_identity_epoch",
        "expected_identity_thumbprint_sha256",
        "expected_api_origin",
        "expected_stream_origin",
    }
)
_COMMON: Final = _TRUST | frozenset(
    {
        "contract_version",
        "schema_version",
        "platform",
        "ceremony_id",
        "requested_at",
        "request_sha256",
    }
)
_CLAIM_REF: Final = frozenset({"claim_id", "claim_request_sha256"})
_FIELDS: Final = MappingProxyType(
    {
        SelfOperation.START: frozenset({"operation_id", "rendezvous_secret_sha256"}),
        SelfOperation.CLAIM: frozenset(
            {
                "claim_id",
                "poll_secret_sha256",
                "device_public_key_spki_b64",
                "device_key_thumbprint_sha256",
                "device_name",
                "app_version",
                "device_signature_b64url",
            }
        ),
        SelfOperation.POLL: _CLAIM_REF | {"device_signature_b64url"},
        SelfOperation.DECISION: _CLAIM_REF
        | {
            "operation_id",
            "action",
            "expected_revision",
            "comparison_code",
        },
        SelfOperation.EXCHANGE: _CLAIM_REF
        | {
            "exchange_id",
            "binding_commit_id",
            "approval_operation_id",
            "confirmed_account_id",
            "next_refresh_token_sha256",
            "device_signature_b64url",
        },
    }
)


class SelfV2Error(RuntimeError):
    """Stable, payload-free error; no parser/crypto exception is exposed."""

    def __init__(self, *, proof: bool = False, oversized: bool = False) -> None:
        self.code = "self_pairing_unavailable" if proof else "device_contract_invalid"
        self.http_status = 401 if proof else (413 if oversized else 400)
        super().__init__(self.code)

    def __repr__(self) -> str:
        return "SelfV2Error(<redacted>)"


_Scalar = str | int | None


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class _DocumentView(Mapping[str, _Scalar]):
    """Immutable projection whose repr also excludes origins and personal fields."""

    _items: tuple[tuple[str, _Scalar], ...]

    @overload
    def __getitem__(
        self, key: Literal["expected_identity_epoch", "expected_revision", "schema_version"]
    ) -> int: ...

    @overload
    def __getitem__(
        self, key: Literal["claim_id", "claim_request_sha256", "comparison_code"]
    ) -> str | None: ...

    @overload
    def __getitem__(
        self,
        key: Literal[
            "action",
            "app_version",
            "approval_operation_id",
            "binding_commit_id",
            "ceremony_id",
            "confirmed_account_id",
            "contract_version",
            "device_key_thumbprint_sha256",
            "device_name",
            "device_public_key_spki_b64",
            "device_signature_b64url",
            "exchange_id",
            "expected_api_origin",
            "expected_identity_thumbprint_sha256",
            "expected_server_instance_id",
            "expected_stream_origin",
            "next_refresh_token_sha256",
            "operation_id",
            "platform",
            "poll_secret_sha256",
            "rendezvous_secret_sha256",
            "request_sha256",
            "requested_at",
        ],
    ) -> str: ...

    @overload
    def __getitem__(self, key: str) -> _Scalar: ...

    def __getitem__(self, key: str) -> _Scalar:
        for name, value in self._items:
            if name == key:
                return value
        raise KeyError("unknown self member")

    def __iter__(self) -> Iterator[str]:
        return (name for name, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return "SelfDocument(<redacted>)"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ParsedSelfRequest:
    """Immutable flat-model snapshot, with its own hash checked, not authorized.

    canonical_document is JCS of the FULL original including hash and signature.
    original_document_sha256 hashes those bytes; request_sha256 is the distinct
    registry digest with exactly the prescribed omissions. Both are replay evidence.
    No nested containers pass the exact self models, so the tuple is deeply immutable.
    Use parse_self_request rather than constructing this result directly.
    """

    operation: SelfOperation
    _items: tuple[tuple[str, _Scalar], ...]
    canonical_document: bytes
    request_sha256: bytes
    original_document_sha256: bytes
    signature: bytes | None

    @property
    def document(self) -> _DocumentView:
        return _DocumentView(self._items)

    @property
    def platform(self) -> SelfPlatform:
        return SelfPlatform(_text(self.document["platform"]))

    @property
    def platform_role(self) -> SelfPlatformRole:
        return SelfPlatformRole.RECIPIENT if self.operation in _DOMAINS else SelfPlatformRole.SOURCE

    def __repr__(self) -> str:
        return "ParsedSelfRequest(<redacted>)"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class VerifiedRecipientProof:
    """Key possession only; approved key/platform are explicit service inputs.

    Retain request's complete evidence and exact signature, this canonical key,
    thumbprint, fixed domain and algorithm under service retention bounds. Never
    treat this result as source approval, secret possession or session authority.
    """

    request: ParsedSelfRequest
    approved_public_key_spki: bytes
    approved_key_thumbprint_sha256: bytes
    platform: SelfPlatform

    @property
    def proof_domain(self) -> str:
        return _DOMAINS[self.request.operation]

    @property
    def signature_algorithm(self) -> str:
        return "ES256-P1363"

    def __repr__(self) -> str:
        return "VerifiedRecipientProof(<redacted>)"


def parse_self_request(operation: SelfOperation, raw_body: bytes) -> ParsedSelfRequest:
    """Parse exact raw UTF-8 JSON for one of FIVE closed body operations.

    Reject duplicate members at every depth, floats (even 2.0/2e0), bool integers,
    unsafe numbers, malformed Unicode and any unknown/absent/null-invalid member.
    No defaults, normalization, secret headers, inferred platform or v1 parser.
    """
    if type(operation) is not SelfOperation or type(raw_body) is not bytes:
        raise SelfV2Error()
    if len(raw_body) > MAX_BODY_BYTES:
        raise SelfV2Error(oversized=True)
    try:
        document = json.loads(
            raw_body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_int=_safe_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        _validate_model(operation, document)
        canonical = rfc8785.dumps(document)
        if len(canonical) > MAX_BODY_BYTES:
            raise SelfV2Error(oversized=True)
        omissions = _HASH_OMISSIONS if operation in _DOMAINS else frozenset({"request_sha256"})
        digest = canonical_sha256(document, omit=omissions)
        if not hmac.compare_digest(digest.hex(), document["request_sha256"]):
            raise ValueError("digest")
        signature = (
            _decode_signature(document["device_signature_b64url"])
            if operation in _DOMAINS
            else None
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        UnsupportedAlgorithm,
        ProfilePairingError,
    ):
        raise SelfV2Error() from None
    result = object.__new__(ParsedSelfRequest)
    for name, value in (
        ("operation", operation),
        ("_items", tuple(sorted(document.items()))),
        ("canonical_document", canonical),
        ("request_sha256", digest),
        ("original_document_sha256", hashlib.sha256(canonical).digest()),
        ("signature", signature),
    ):
        object.__setattr__(result, name, value)
    return result


def verify_recipient_proof(
    request: ParsedSelfRequest,
    *,
    approved_public_key_spki: bytes,
    approved_platform: SelfPlatform,
) -> VerifiedRecipientProof:
    """Verify P1363 using the ECDSA MESSAGE API and a fixed v2 domain || raw D.

    For CLAIM compare any supplied key/thumbprint BEFORE signature verification.
    For POLL/EXCHANGE the service resolves these required inputs from locked original
    ceremony/claim evidence; no missing-key or Android fallback exists here.
    """
    if (
        type(request) is not ParsedSelfRequest
        or request.operation not in _DOMAINS
        or type(approved_platform) is not SelfPlatform
        or type(approved_public_key_spki) is not bytes
    ):
        raise SelfV2Error(proof=True)
    try:
        _canonical_spki(approved_public_key_spki)
        if request.platform is not approved_platform:
            raise ValueError("platform")
        thumbprint = hashlib.sha256(approved_public_key_spki).digest()
        document = request.document
        if request.operation is SelfOperation.CLAIM:
            supplied = _decode_spki(_text(document["device_public_key_spki_b64"]))
            if not hmac.compare_digest(
                supplied, approved_public_key_spki
            ) or not hmac.compare_digest(
                _text(document["device_key_thumbprint_sha256"]), thumbprint.hex()
            ):
                raise ValueError("key")
        verify_p1363(
            approved_public_key_spki,
            _DOMAINS[request.operation],
            request.request_sha256,
            _text(document["device_signature_b64url"]),
        )
    except ValueError, TypeError, KeyError, UnsupportedAlgorithm, ProfilePairingError:
        raise SelfV2Error(proof=True) from None
    result = object.__new__(VerifiedRecipientProof)
    for name, value in (
        ("request", request),
        ("approved_public_key_spki", approved_public_key_spki),
        ("approved_key_thumbprint_sha256", thumbprint),
        ("platform", approved_platform),
    ):
        object.__setattr__(result, name, value)
    return result


def comparison_code(start: ParsedSelfRequest, claim: VerifiedRecipientProof) -> str:
    """Compute the NEW README SAS recipe for the exact original verified claim.

    Internal trust/ceremony consistency is checked, not external authentication.
    SOURCE and RECIPIENT platforms may differ. The claim digest already includes
    its version/platform; never strip, normalize or re-sign that claim. Source must
    explicitly compare and approve this code with current claim/hash/revision.
    """
    if (
        type(start) is not ParsedSelfRequest
        or start.operation is not SelfOperation.START
        or type(claim) is not VerifiedRecipientProof
        or claim.request.operation is not SelfOperation.CLAIM
    ):
        raise SelfV2Error()
    source, recipient = start.document, claim.request.document
    if any(source[key] != recipient[key] for key in _TRUST | {"ceremony_id"}):
        raise SelfV2Error()
    payload = {
        "server_instance_id": source["expected_server_instance_id"],
        "identity_epoch": source["expected_identity_epoch"],
        "ceremony_id": source["ceremony_id"],
        "claim_request_sha256": recipient["request_sha256"],
        "key_thumbprint": recipient["device_key_thumbprint_sha256"],
    }
    digest = hashlib.sha256(_SAS_DOMAIN + canonical_sha256(payload)).digest()
    return f"{int.from_bytes(digest[:8], 'big', signed=False) % 10**12:012d}"


def _text(value: _Scalar) -> str:
    if type(value) is not str:
        raise ValueError("string")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _safe_integer(token: str) -> int:
    if len(token) > 16:
        raise ValueError("integer")
    value = int(token)
    if not 0 <= value <= _MAX_INTEGER:
        raise ValueError("integer")
    return value


def _reject_number(token: str) -> None:
    raise ValueError("number")


def _validate_model(operation: SelfOperation, document: object) -> None:
    if type(document) is not dict or set(document) != _COMMON | _FIELDS[operation]:
        raise ValueError("fields")
    for key, value in document.items():
        if (
            operation is SelfOperation.DECISION
            and key in _CLAIM_REF | {"comparison_code"}
            and value is None
        ):
            continue
        if key in {"schema_version", "expected_identity_epoch", "expected_revision"}:
            if type(value) is not int or not 1 <= value <= _MAX_INTEGER:
                raise ValueError("integer")
            continue
        if type(value) is not str or not 1 <= len(value) <= 2048:
            raise ValueError("string")
        value.encode("utf-8", errors="strict")
        if key.endswith("_id") and str(UUID(value)) != value:
            raise ValueError("uuid")
        if key.endswith("_sha256") and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("hash")
    if document["contract_version"] != "v2" or document["schema_version"] != 2:
        raise ValueError("version")
    SelfPlatform(document["platform"])
    _timestamp(document["requested_at"])
    _origin(document["expected_api_origin"])
    _origin(document["expected_stream_origin"])
    if operation is SelfOperation.CLAIM:
        for key, limit in (("device_name", 120), ("app_version", 32)):
            value = document[key]
            if len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError("display text")
        spki = _decode_spki(document["device_public_key_spki_b64"])
        if not hmac.compare_digest(
            hashlib.sha256(spki).hexdigest(), document["device_key_thumbprint_sha256"]
        ):
            raise ValueError("thumbprint")
    if operation in _DOMAINS:
        _decode_signature(document["device_signature_b64url"])
    if operation is SelfOperation.DECISION:
        action = document["action"]
        claim_id, claim_hash, code = (
            document["claim_id"],
            document["claim_request_sha256"],
            document["comparison_code"],
        )
        if action not in {"APPROVE", "REJECT", "CANCEL"}:
            raise ValueError("action")
        if (claim_id is None) != (claim_hash is None):
            raise ValueError("claim reference")
        if action in {"APPROVE", "REJECT"} and claim_id is None:
            raise ValueError("claim required")
        if action == "APPROVE":
            if type(code) is not str or not re.fullmatch(r"[0-9]{12}", code):
                raise ValueError("code")
        elif code is not None:
            raise ValueError("code must be null")


def _timestamp(value: str) -> None:
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):"
        r"[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,9})?Z",
        value,
    ):
        raise ValueError("UTC")
    # Validate calendar/time without truncating or rewriting the retained nanoseconds.
    # The wire excludes ISO 8601's 24:00 accepted by fromisoformat.
    datetime.fromisoformat(value[:19])


def _origin(value: str) -> None:
    """Check ASCII scheme/host/port syntax only, retaining the exact signed text."""
    value.encode("ascii", errors="strict")
    match = re.fullmatch(r"https?://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?", value)
    if match is None:
        raise ValueError("origin")
    host, port = match.groups()
    if port is not None and not 0 <= int(port) <= 65535:
        raise ValueError("port")
    if host.startswith("["):
        ipaddress.IPv6Address(host[1:-1])
    else:
        name = host[:-1] if host.endswith(".") else host
        if len(name) > 253 or any(
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in name.split(".")
        ):
            raise ValueError("host")


def _canonical_spki(raw: bytes) -> None:
    if not 1 <= len(raw) <= 192:
        raise ValueError("SPKI bound")
    key = serialization.load_der_public_key(raw)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("P-256")
    canonical = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    if canonical != raw:
        raise ValueError("canonical DER")


def _decode_spki(value: str) -> bytes:
    if not 32 <= len(value) <= 256:
        raise ValueError("SPKI bound")
    raw = base64.b64decode(value, validate=True)
    if base64.b64encode(raw).decode("ascii") != value:
        raise ValueError("canonical base64")
    _canonical_spki(raw)
    return raw


def _decode_signature(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]{86}", value):
        raise ValueError("P1363")
    raw = base64.urlsafe_b64decode(value + "==")
    if len(raw) != 64 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise ValueError("canonical P1363")
    return raw


@dataclass
class SelfCeremonyV2(SelfDevicePairing):
    """Typed nonsecret storage snapshot; original documents retain exact proofs."""

    contract_version: str = "v2"
    source_platform: str = ""
    recipient_platform: str | None = None
    source_public_key_spki: bytes = field(default=b"", repr=False)
    source_key_thumbprint_sha256: bytes = field(default=b"", repr=False)
    start_request_sha256: bytes = field(default=b"", repr=False)
    start_original_document_sha256: bytes = field(default=b"", repr=False)
    claim_request_sha256: bytes | None = field(default=None, repr=False)
    claim_original_document_sha256: bytes | None = field(default=None, repr=False)
    exchange_request_sha256: bytes | None = field(default=None, repr=False)
    exchange_original_document_sha256: bytes | None = field(default=None, repr=False)
    binding_commit_id: UUID | None = None

    def comparison_code(self) -> str:
        if self.claim_document is None or self.recipient_platform is None:
            raise SelfV2Error(proof=True)
        start = parse_self_request(SelfOperation.START, rfc8785.dumps(self.start_document))
        claim = parse_self_request(SelfOperation.CLAIM, rfc8785.dumps(self.claim_document))
        proof = verify_recipient_proof(
            claim,
            approved_public_key_spki=_decode_spki(
                self.claim_document["device_public_key_spki_b64"]
            ),
            approved_platform=SelfPlatform(self.recipient_platform),
        )
        return comparison_code(start, proof)


@dataclass(frozen=True)
class CurrentSourceAuthoritySnapshot:
    """Fully loaded locked facts; validation never reads storage or the clock."""

    user_id: UUID
    authority_generation: int
    account_status: str
    account_deleted_at: datetime | None
    device_id: UUID
    device_revoked_at: datetime | None
    platform: str
    public_key_spki: bytes = field(repr=False)
    key_thumbprint_sha256: bytes = field(repr=False)
    session_id: UUID
    family_id: UUID
    session_mode: str
    session_revoked_at: datetime | None
    session_expires_at: datetime

    def require_current(
        self, ceremony: SelfCeremonyV2, now: datetime, *, actor: Principal | None = None
    ) -> None:
        if self.authority_generation != ceremony.authority_generation:
            raise SelfPairingError("operation_conflict")
        if (
            self.user_id != ceremony.user_id
            or self.account_status != "ACTIVE"
            or self.account_deleted_at is not None
            or self.device_id != ceremony.source_device_id
            or self.device_revoked_at is not None
            or self.platform != ceremony.source_platform
            or self.public_key_spki != ceremony.source_public_key_spki
            or self.key_thumbprint_sha256 != ceremony.source_key_thumbprint_sha256
            or self.family_id != ceremony.source_family_id
            or self.session_mode != "V2"
            or self.session_revoked_at is not None
            or self.session_expires_at <= now
        ):
            raise SelfPairingError()
        if actor is not None and (
            actor.user_id != self.user_id
            or actor.device_id != self.device_id
            or actor.session_id != self.session_id
        ):
            raise SelfPairingError()


@dataclass(frozen=True)
class SelfCommandV2(PairingCommand):
    contract_version: str = "v2"
    source_platform: str = ""
    original_document: dict[str, object] = field(default_factory=dict, repr=False)
    original_document_sha256: bytes = field(default=b"", repr=False)


__all__ = (
    "MAX_BODY_BYTES",
    "CurrentSourceAuthoritySnapshot",
    "ParsedSelfRequest",
    "SelfCeremonyV2",
    "SelfCommandV2",
    "SelfOperation",
    "SelfPlatform",
    "SelfPlatformRole",
    "SelfV2Error",
    "VerifiedRecipientProof",
    "comparison_code",
    "parse_self_request",
    "verify_recipient_proof",
)
