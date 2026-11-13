"""Pure, closed B1 wire models; parsing and signatures confer no authority.

The future application service owns real access authentication/verified claims,
current account/device/session/key/block/role checks after authority locks, complete
typed historical evidence, cursor absence/prior use/epoch/replay, quotas, flags,
the shared D1+B1 revision and fresh trusted time after blocking reads. It must use
the read-only M5 accessor, not an identity/revision allocator. No database, local
clock, bearer decoder, enrollment, ROOT proof or bind signing exists here.

Negotiation verification requires independently trusted current M5 inputs. Its
embedded public key cannot establish its own trust. Origin syntax is checked here;
release transport PKI/confirmed SPKI and debug HTTP policy belong to the caller.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Final, cast
from uuid import UUID

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from autplay.domain.canonical_json import canonical_json_bytes
from autplay.domain.profile_pairing import ProfilePairingError, canonical_sha256, verify_p1363

MAX_INTEGER: Final = 9_007_199_254_740_991
MAX_BODY_BYTES: Final = 16_384
DISCOVERY_MAX_BYTES: Final = 16_384
CAPABILITIES_MAX_BYTES: Final = 65_536
MAX_DEPTH: Final = 4
DISCOVERY_DOMAIN: Final = "autplay:sync-binding:negotiation:discovery:v2\n"
CAPABILITIES_DOMAIN: Final = "autplay:sync-binding:negotiation:capabilities:v2\n"
# Independent from D1's eight-family registry. Source authoring is not qualification.
IMPLEMENTED_AND_QUALIFIED_PLATFORMS: Final[frozenset[str]] = frozenset({"ANDROID", "MACOS"})
SYNC_BINDING_V2_ENABLED_DEFAULT: Final = False


class SyncBindingV2Error(RuntimeError):
    """Only a stable code/status; raw documents and exception causes are suppressed."""

    def __init__(self, code: str = "sync_binding_invalid", status: int = 400) -> None:
        self.code, self.status = code, status
        self.retryable = code in {"sync_binding_unavailable", "sync_binding_capability_changed"}
        super().__init__(code)


class BindingPlatform(Enum):
    ANDROID = "ANDROID"
    MACOS = "MACOS"


class NegotiationKind(Enum):
    """Selected by a trusted route/call site, never by a response's domain field."""

    DISCOVERY = "B1_DISCOVERY"
    CAPABILITIES = "B1_CAPABILITIES"


_VERSION: Final = frozenset(
    {
        "contract_version",
        "schema_version",
        "binding_contract_version",
        "protocol_version",
    }
)
_OWNER: Final = frozenset({"user_id", "device_id"})
_LINEAGE: Final = _OWNER | {"server_profile_id", "journal_epoch"}
_IDENTITY: Final = frozenset(
    {
        "server_instance_id",
        "identity_epoch",
        "identity_thumbprint_sha256",
        "api_origin",
        "stream_origin",
    }
)
_EXPECTED: Final = frozenset("expected_" + key for key in _IDENTITY)
_REQUEST: Final = (
    _VERSION
    | _LINEAGE
    | _EXPECTED
    | {
        "supported_event_schema_versions",
        "expected_capability_revision",
    }
)
_RESULT: Final = (
    _VERSION
    | _LINEAGE
    | _IDENTITY
    | {
        "supported_event_schema_versions",
        "state",
        "platform",
    }
)
_DISCOVERY: Final = (
    _VERSION
    | _IDENTITY
    | {
        "api_major",
        "identity_public_key_spki_b64",
        "binding_available",
        "platforms",
        "issued_at",
        "expires_at",
    }
)
_CAPABILITIES: Final = _DISCOVERY | _OWNER | {"platform", "capability_revision"}
_ENVELOPE: Final = frozenset(
    {
        "contract_version",
        "schema_version",
        "payload",
        "payload_sha256",
        "signature_algorithm",
        "signature_b64url",
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class BindingRequest:
    """Validated immutable canonical bytes; document returns a defensive fresh copy."""

    canonical_document: bytes = field(repr=False)

    def __post_init__(self) -> None:
        document = _decode(self.canonical_document, MAX_BODY_BYTES)
        _sanitize_validation(_validate_request, document)
        object.__setattr__(self, "canonical_document", _canonical(document, MAX_BODY_BYTES))

    @property
    def document(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.canonical_document))

    def __repr__(self) -> str:
        return "BindingRequest(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BindingResult:
    """Exact 200 BOUND shape, not proof of current authority or completed bootstrap."""

    canonical_document: bytes = field(repr=False)

    def __post_init__(self) -> None:
        document = _decode(self.canonical_document, MAX_BODY_BYTES)
        _sanitize_validation(_validate_result, document)
        object.__setattr__(self, "canonical_document", _canonical(document, MAX_BODY_BYTES))

    @property
    def document(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.canonical_document))

    def __repr__(self) -> str:
        return "BindingResult(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class NegotiationEnvelope:
    """Structurally valid envelope. Use verify_negotiation with independent trust."""

    kind: NegotiationKind
    canonical_document: bytes = field(repr=False)

    def __post_init__(self) -> None:
        maximum = _maximum(self.kind)
        document = _decode(self.canonical_document, maximum)
        try:
            _validate_envelope(self.kind, document)
        except ValueError, TypeError, KeyError, UnsupportedAlgorithm:
            raise SyncBindingV2Error() from None
        object.__setattr__(self, "canonical_document", _canonical(document, maximum))

    @property
    def document(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.canonical_document))

    @property
    def payload(self) -> dict[str, object]:
        return cast(dict[str, object], self.document["payload"])

    def __repr__(self) -> str:
        return "NegotiationEnvelope(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class TrustedBindingIdentity:
    """Explicit current trust input, supplied by M5 custody; never extracted as trust."""

    server_instance_id: UUID
    identity_epoch: int
    public_spki: bytes = field(repr=False)
    thumbprint_sha256: str = field(repr=False)
    api_origin: str = field(repr=False)
    stream_origin: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            if type(self.server_instance_id) is not UUID or type(self.public_spki) is not bytes:
                raise ValueError("trust type")
            _positive(self.identity_epoch)
            _spki(self.public_spki)
            _hash(self.thumbprint_sha256)
            if not hmac.compare_digest(
                hashlib.sha256(self.public_spki).hexdigest(), self.thumbprint_sha256
            ):
                raise ValueError("trust key")
            _origin(self.api_origin)
            _origin(self.stream_origin)
        except ValueError, TypeError, UnsupportedAlgorithm:
            raise SyncBindingV2Error("sync_binding_unavailable", 503) from None

    def fields(self) -> dict[str, object]:
        return {
            "server_instance_id": str(self.server_instance_id),
            "identity_epoch": self.identity_epoch,
            "identity_thumbprint_sha256": self.thumbprint_sha256,
            "api_origin": self.api_origin,
            "stream_origin": self.stream_origin,
        }


@dataclass(frozen=True, slots=True, repr=False)
class ExpectedBindingActor:
    """Captured owner/device/platform only; no claim to authenticate the bearer."""

    user_id: UUID
    device_id: UUID
    platform: BindingPlatform

    def __post_init__(self) -> None:
        if (
            type(self.user_id) is not UUID
            or type(self.device_id) is not UUID
            or type(self.platform) is not BindingPlatform
        ):
            raise SyncBindingV2Error() from None

    def fields(self) -> dict[str, object]:
        return {
            "user_id": str(self.user_id),
            "device_id": str(self.device_id),
            "platform": self.platform.value,
        }


def parse_binding_request(raw_body: bytes) -> BindingRequest:
    """No bearer input, no authorization and no defaults/normalization."""
    return BindingRequest(raw_body)


def parse_binding_result(raw_body: bytes) -> BindingResult:
    return BindingResult(raw_body)


def parse_negotiation(kind: NegotiationKind, raw_body: bytes) -> NegotiationEnvelope:
    return NegotiationEnvelope(kind, raw_body)


def verify_negotiation(
    envelope: NegotiationEnvelope,
    *,
    trusted_identity: TrustedBindingIdentity,
    trusted_now: datetime,
    expected_actor: ExpectedBindingActor | None = None,
) -> NegotiationEnvelope:
    """Verify kind-specific MESSAGE/P1363, exact trust, actor and fresh UTC time.

    No low-S normalization/restriction. Discovery verifies identity consistency,
    not account authority. Capabilities require explicit current captured actor.
    Service/client still owns high-water, same-kind content and active authority.
    """
    if (
        type(envelope) is not NegotiationEnvelope
        or type(trusted_identity) is not TrustedBindingIdentity
    ):
        raise SyncBindingV2Error()
    # Reparse at the verification boundary; a parsed document is never a trust token.
    parsed = parse_negotiation(envelope.kind, envelope.canonical_document)
    document, payload = parsed.document, parsed.payload
    try:
        if any(payload[key] != value for key, value in trusted_identity.fields().items()):
            raise ValueError("trust mismatch")
        if _decode_spki(payload["identity_public_key_spki_b64"]) != trusted_identity.public_spki:
            raise ValueError("trusted key mismatch")
        if parsed.kind is NegotiationKind.CAPABILITIES:
            if type(expected_actor) is not ExpectedBindingActor:
                raise ValueError("actor required")
            if any(payload[key] != value for key, value in expected_actor.fields().items()):
                raise ValueError("actor mismatch")
        elif expected_actor is not None:
            raise ValueError("discovery has no actor")
        now = _clock_ns(trusted_now)
        issued, expires = _timestamp_ns(payload["issued_at"]), _timestamp_ns(payload["expires_at"])
        if issued > now + 120 * 10**9 or expires <= now:
            raise ValueError("freshness")
        digest = canonical_sha256(payload)
        if not hmac.compare_digest(digest.hex(), cast(str, document["payload_sha256"])):
            raise ValueError("digest")
        domain = (
            DISCOVERY_DOMAIN if parsed.kind is NegotiationKind.DISCOVERY else CAPABILITIES_DOMAIN
        )
        verify_p1363(
            trusted_identity.public_spki, domain, digest, cast(str, document["signature_b64url"])
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        UnsupportedAlgorithm,
        ProfilePairingError,
    ):
        raise SyncBindingV2Error() from None
    return parsed


def verify_binding_result(
    result: BindingResult,
    *,
    request: BindingRequest,
    trusted_identity: TrustedBindingIdentity,
    expected_actor: ExpectedBindingActor,
) -> BindingResult:
    """Exact captured echo check only; durable K/J reconciliation remains required."""
    if (
        type(result) is not BindingResult
        or type(request) is not BindingRequest
        or type(trusted_identity) is not TrustedBindingIdentity
        or type(expected_actor) is not ExpectedBindingActor
    ):
        raise SyncBindingV2Error()
    response, original = result.document, request.document
    if (
        any(response[key] != original[key] for key in _LINEAGE)
        or any(response[key] != value for key, value in expected_actor.fields().items())
        or any(
            response[key] != value or original["expected_" + key] != value
            for key, value in trusted_identity.fields().items()
        )
    ):
        raise SyncBindingV2Error()
    return result


def _maximum(kind: NegotiationKind) -> int:
    if type(kind) is not NegotiationKind:
        raise SyncBindingV2Error()
    return DISCOVERY_MAX_BYTES if kind is NegotiationKind.DISCOVERY else CAPABILITIES_MAX_BYTES


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate")
        document[key] = value
    return document


def _integer(token: str) -> int:
    if len(token) > 16:
        raise ValueError("integer")
    value = int(token)
    _positive(value)
    return value


def _reject_number(token: str) -> None:
    raise ValueError("number")


def _decode(raw: bytes, maximum: int) -> dict[str, object]:
    if type(raw) is not bytes:
        raise SyncBindingV2Error()
    if len(raw) > maximum:
        raise SyncBindingV2Error("request_body_too_large", 413)
    try:
        text = raw.decode("utf-8", errors="strict")
        # Bound nesting before the recursive JSON decoder, ignoring quoted braces.
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > MAX_DEPTH:
                    raise ValueError("depth")
            elif char in "]}":
                depth -= 1
        document = json.loads(
            text,
            object_pairs_hook=_unique,
            parse_int=_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        if type(document) is not dict:
            raise ValueError("object")
        _unicode(document)
        return document
    except ValueError, TypeError, RecursionError:
        raise SyncBindingV2Error() from None


def _unicode(value: object) -> None:
    if type(value) is str:
        value.encode("utf-8", errors="strict")
    elif type(value) is dict:
        for key, item in value.items():
            key.encode("utf-8", errors="strict")
            _unicode(item)
    elif type(value) is list:
        for item in value:
            _unicode(item)


def _canonical(document: dict[str, object], maximum: int) -> bytes:
    try:
        result = canonical_json_bytes(document)
    except ValueError, TypeError:
        raise SyncBindingV2Error() from None
    if len(result) > maximum:
        raise SyncBindingV2Error("request_body_too_large", 413)
    return result


def _sanitize_validation(
    validator: Callable[[dict[str, object]], None],
    document: dict[str, object],
) -> None:
    try:
        validator(document)
    except ValueError, TypeError, KeyError, UnsupportedAlgorithm:
        raise SyncBindingV2Error() from None


def _closed(document: object, fields: frozenset[str]) -> dict[str, object]:
    if type(document) is not dict or set(document) != fields:
        raise ValueError("fields")
    return document


def _version(document: dict[str, object]) -> None:
    if document["contract_version"] != "v2":
        raise ValueError("version")
    for key, value in (
        ("schema_version", 2),
        ("binding_contract_version", 2),
        ("protocol_version", 1),
    ):
        if type(document[key]) is not int or document[key] != value:
            raise ValueError("version integer")


def _positive(value: object) -> None:
    if type(value) is not int or not 1 <= value <= MAX_INTEGER:
        raise ValueError("positive safe integer")


def _uuid(value: object) -> None:
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("UUID")


def _hash(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("hash")


def _origin(value: object) -> None:
    if type(value) is not str or not 1 <= len(value) <= 2048 or not value.isascii():
        raise ValueError("origin")
    match = re.fullmatch(r"https?://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?", value)
    if match is None:
        raise ValueError("origin")
    host, port = match.groups()
    if port is not None and not 1 <= int(port) <= 65535:
        raise ValueError("port")
    if host.startswith("["):
        ipaddress.IPv6Address(host[1:-1])
    else:
        name = host[:-1] if host.endswith(".") else host
        if len(name) > 253 or any(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) is None
            for label in name.split(".")
        ):
            raise ValueError("host")


def _identity(document: dict[str, object], prefix: str = "") -> None:
    _uuid(document[prefix + "server_instance_id"])
    _positive(document[prefix + "identity_epoch"])
    _hash(document[prefix + "identity_thumbprint_sha256"])
    _origin(document[prefix + "api_origin"])
    _origin(document[prefix + "stream_origin"])


def _lineage(document: dict[str, object]) -> None:
    for key in _LINEAGE:
        _uuid(document[key])
    versions = document["supported_event_schema_versions"]
    if (
        type(versions) is not list
        or len(versions) != 1
        or type(versions[0]) is not int
        or versions[0] != 1
    ):
        raise ValueError("event versions")


def _validate_request(document: dict[str, object]) -> None:
    _closed(document, _REQUEST)
    _version(document)
    _lineage(document)
    _identity(document, "expected_")
    _positive(document["expected_capability_revision"])


def _validate_result(document: dict[str, object]) -> None:
    _closed(document, _RESULT)
    _version(document)
    _lineage(document)
    _identity(document)
    BindingPlatform(document["platform"])
    if document["state"] != "BOUND":
        raise ValueError("state")


def _spki(raw: bytes) -> None:
    if not 1 <= len(raw) <= 192:
        raise ValueError("SPKI bound")
    key = serialization.load_der_public_key(raw)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("P-256")
    if (
        key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        != raw
    ):
        raise ValueError("canonical DER")


def _decode_spki(value: object) -> bytes:
    if type(value) is not str or not 1 <= len(value) <= 256:
        raise ValueError("SPKI encoding")
    raw = base64.b64decode(value, validate=True)
    if base64.b64encode(raw).decode("ascii") != value:
        raise ValueError("canonical base64")
    _spki(raw)
    return raw


def _signature(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{86}", value) is None:
        raise ValueError("P1363")
    raw = base64.urlsafe_b64decode(value + "==")
    if len(raw) != 64 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise ValueError("canonical P1363")


def _timestamp_ns(value: object) -> int:
    if type(value) is not str:
        raise ValueError("time")
    match = re.fullmatch(
        r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?Z", value
    )
    if match is None:
        raise ValueError("UTC time")
    seconds, fraction = match.groups()
    instant = datetime.fromisoformat(seconds).replace(tzinfo=UTC)
    delta = instant - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 10**9 + int((fraction or "0").ljust(9, "0"))


def _clock_ns(value: datetime) -> int:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("trusted aware clock required")
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 10**9 + delta.microseconds * 1000


def _validate_envelope(kind: NegotiationKind, document: dict[str, object]) -> None:
    _closed(document, _ENVELOPE)
    if (
        document["contract_version"] != "v2"
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 2
        or document["signature_algorithm"] != "ES256-P1363"
    ):
        raise ValueError("envelope version")
    _hash(document["payload_sha256"])
    _signature(document["signature_b64url"])
    payload = document["payload"]
    payload = _closed(payload, _DISCOVERY if kind is NegotiationKind.DISCOVERY else _CAPABILITIES)
    _version(payload)
    _identity(payload)
    if type(payload["api_major"]) is not int or payload["api_major"] != 1:
        raise ValueError("API major")
    if type(payload["binding_available"]) is not bool:
        raise ValueError("available")
    platforms = payload["platforms"]
    if type(platforms) is not list or platforms != (
        ["ANDROID", "MACOS"] if payload["binding_available"] else []
    ):
        raise ValueError("platforms")
    spki = _decode_spki(payload["identity_public_key_spki_b64"])
    if not hmac.compare_digest(
        hashlib.sha256(spki).hexdigest(), cast(str, payload["identity_thumbprint_sha256"])
    ):
        raise ValueError("key thumbprint")
    issued, expires = _timestamp_ns(payload["issued_at"]), _timestamp_ns(payload["expires_at"])
    ttl = 600 if kind is NegotiationKind.DISCOVERY else 3600
    if not 0 < expires - issued <= ttl * 10**9:
        raise ValueError("TTL")
    if kind is NegotiationKind.CAPABILITIES:
        for key in _OWNER:
            _uuid(payload[key])
        BindingPlatform(payload["platform"])
        _positive(payload["capability_revision"])


__all__ = (
    "BindingPlatform",
    "BindingRequest",
    "BindingResult",
    "ExpectedBindingActor",
    "NegotiationEnvelope",
    "NegotiationKind",
    "SyncBindingV2Error",
    "TrustedBindingIdentity",
    "parse_binding_request",
    "parse_binding_result",
    "parse_negotiation",
    "verify_binding_result",
    "verify_negotiation",
)
