"""Source-only D1 profile proof boundary; no HTTP, persistence or authority grants.

Trusted routes select DeviceProfileOperation directly, never from body/path text.
parse_device_profile_v2 validates exact bytes/models and returns an immutable,
redacted in-memory snapshot. verify_device_profile_v2 requires an explicit P-256
SPKI: services must obtain the current key/platform/account evidence under their
locks (poll/recover/rotation have no body key). For a new admission the caller may
explicitly pass parsed.body_spki; that proves possession, NOT prior trust.

Services still own identity/origin equality, account/key/platform authorization,
nonce consumption, freshness, quotas, gates, row versions and replay retention.
Never persist these in-memory objects or their private canonical bytes. Persist
only verified.safe_evidence(), then verify the COMPLETE incoming original again
on replay. The projection is not a signed request and cannot reconstruct one.
No raw credential headers are accepted or retained here. Frozen v1 is untouched.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID

import rfc8785
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

MAX_DOCUMENT_BYTES: Final = 16384
MAX_SAFE_INTEGER: Final = 9007199254740991
MAX_JSON_DEPTH: Final = 8
SIGNATURE_ALGORITHM: Final = "ES256-P1363"


class DeviceProfileOperation(Enum):
    ENROLLMENT_EXCHANGE = "enrollment.exchange"
    ADMISSION_SUBMIT = "admission.submit"
    ADMISSION_POLL = "admission.poll"
    ADMISSION_RECOVER = "admission.recover"
    ADMISSION_EXCHANGE = "admission.exchange"
    TRUSTED_CHALLENGE = "trusted.challenge"
    TRUSTED_EXCHANGE = "trusted.exchange"
    SESSION_ROTATE = "session.rotate"


class DevicePlatformV2(Enum):
    ANDROID = "ANDROID"
    MACOS = "MACOS"


class DeviceProfileV2Error(ValueError):
    """Fixed public failure, without input, parser/crypto cause or raw secrets."""

    code = "device_contract_invalid"

    def __init__(self, status: int = 400) -> None:
        self.status = status
        super().__init__(self.code)


class _Invalid(Exception):
    pass


class _TooLarge(_Invalid):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class DeviceTrustV2:
    server_instance_id: UUID
    identity_epoch: int
    identity_thumbprint_sha256: bytes
    api_origin: str
    stream_origin: str


@dataclass(frozen=True, slots=True, repr=False)
class DeviceKeyEvidenceV2:
    """Proof key evidence only; neither field attests service authorization."""

    spki: bytes
    thumbprint_sha256: bytes
    matched_body_key: bool


type WireValue = str | int | Mapping[str, str]
type SafeValue = str | int


@dataclass(frozen=True, slots=True, repr=False)
class DeviceReplayEvidenceV2:
    """Explicit nonsecret storage projection, never a substitute wire document.

    Credential hashes use the existing SHA256(encoded ASCII text) recipe.
    Public nonces remain in fields, separate from credentials. The full-original
    digest includes the original hash AND signature members, unlike proof_digest.
    Private origins/identifiers still require protected storage, not ordinary logs.
    """

    operation: DeviceProfileOperation
    contract_version: str
    schema_version: int
    platform: DevicePlatformV2
    trust: DeviceTrustV2
    key: DeviceKeyEvidenceV2
    domain: bytes
    signature_algorithm: str
    proof_digest: bytes
    original_document_sha256: bytes
    signature: bytes
    fields: tuple[tuple[str, SafeValue], ...]
    credential_hashes: tuple[tuple[str, bytes], ...]


class _Immutable:
    __slots__ = ()

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("immutable device profile snapshot")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("immutable device profile snapshot")


class ParsedDeviceProfileV2(_Immutable):
    """Transient snapshot with explicit field access; no dict/asdict/body export.

    field() may return a credential for immediate service processing. It must
    never be logged/persisted. Even repr of the containing object is redacted.
    The sole nested wire object (JWK) is also copied and immutable.
    """

    __slots__ = (
        "_canonical",
        "_values",
        "body_spki",
        "operation",
        "original_document_sha256",
        "platform",
        "proof_digest",
        "signature",
        "trust",
    )
    operation: DeviceProfileOperation
    platform: DevicePlatformV2
    trust: DeviceTrustV2
    proof_digest: bytes
    original_document_sha256: bytes
    signature: bytes
    body_spki: bytes | None
    _values: Mapping[str, WireValue]
    _canonical: bytes

    def __init__(self) -> None:
        raise TypeError("use parse_device_profile_v2")

    def __repr__(self) -> str:
        return "<ParsedDeviceProfileV2 redacted>"

    def field(self, name: str) -> WireValue:
        """Read one exact original value in memory; absent optional fields raise KeyError."""
        return self._values[name]


class VerifiedDeviceProfileV2(_Immutable):
    """Cryptographically verified possession, NOT account/service authorization."""

    __slots__ = ("key", "parsed")
    parsed: ParsedDeviceProfileV2
    key: DeviceKeyEvidenceV2

    def __init__(self) -> None:
        raise TypeError("use verify_device_profile_v2")

    def __repr__(self) -> str:
        return "<VerifiedDeviceProfileV2 redacted>"

    def safe_evidence(self) -> DeviceReplayEvidenceV2:
        parsed = self.parsed
        spec = _SPECS[parsed.operation]
        fields: list[tuple[str, SafeValue]] = []
        for name in spec.replay_fields:
            if name in parsed._values:
                value = parsed._values[name]
                # This explicit allowlist has only scalar nonsecret values.
                if not isinstance(value, (str, int)):
                    raise DeviceProfileV2Error()
                fields.append((name, value))
        credentials: tuple[tuple[str, bytes], ...] = ()
        if spec.credential is not None:
            value = parsed.field(spec.credential)
            if type(value) is not str:
                raise DeviceProfileV2Error()
            credential_hash = hashlib.sha256(value.encode("ascii")).digest()
            credentials = ((spec.credential + "_sha256", credential_hash),)
        return DeviceReplayEvidenceV2(
            parsed.operation,
            "v2",
            2,
            parsed.platform,
            parsed.trust,
            self.key,
            spec.domain,
            SIGNATURE_ALGORITHM,
            parsed.proof_digest,
            parsed.original_document_sha256,
            parsed.signature,
            tuple(fields),
            credentials,
        )


@dataclass(frozen=True, slots=True)
class _Spec:
    domain: bytes
    required: tuple[tuple[str, str], ...]
    replay_fields: tuple[str, ...]
    optional: tuple[tuple[str, str], ...] = ()
    own_hash: bool = False
    credential: str | None = None


_COMMON: Final = (
    ("contract_version", "v2"),
    ("schema_version", "two"),
    ("platform", "platform"),
    ("expected_server_instance_id", "uuid"),
    ("expected_identity_epoch", "positive"),
    ("expected_identity_thumbprint_sha256", "hash"),
    ("expected_api_origin", "origin"),
    ("expected_stream_origin", "origin"),
)
_KEY: Final = (("device_key_thumbprint_sha256", "hash"), ("device_public_key_jwk", "jwk"))
_PROOF: Final = (("proof_b64url", "signature"),)
_OWN: Final = (
    ("request_sha256", "hash"),
    ("device_signature_b64url", "signature"),
    ("signature_algorithm", "algorithm"),
)

# Exactly the eight named registry operations; no family/text/domain fallback.
# Replay allowlists are separate from wire schemas and never infer storage safety.
_SPECS: Final[Mapping[DeviceProfileOperation, _Spec]] = MappingProxyType(
    {
        DeviceProfileOperation.ENROLLMENT_EXCHANGE: _Spec(
            b"autplay:device-compatibility:enrollment:exchange:v2\n",
            _COMMON
            + _OWN
            + (
                ("device_public_key_spki_b64", "spki"),
                ("device_key_thumbprint_sha256", "hash"),
                ("device_name", "name"),
                ("app_version", "app"),
                ("exchange_id", "uuid"),
                ("binding_commit_id", "uuid"),
                ("invitation_id", "uuid"),
                ("invitation_secret", "b32"),
                ("expected_user_id", "uuid"),
                ("next_refresh_token_sha256", "hash"),
                ("client_nonce_b64url", "b16"),
            ),
            (
                "exchange_id",
                "binding_commit_id",
                "invitation_id",
                "expected_user_id",
                "device_name",
                "app_version",
                "next_refresh_token_sha256",
                "client_nonce_b64url",
                "request_sha256",
            ),
            own_hash=True,
            credential="invitation_secret",
        ),
        DeviceProfileOperation.ADMISSION_SUBMIT: _Spec(
            b"autplay:device-compatibility:admission:submit:v2\n",
            _COMMON
            + _KEY
            + _PROOF
            + (
                ("requested_at", "time"),
                ("request_id", "uuid"),
                ("client_nonce_b64url", "b32"),
                ("api_major", "one"),
                ("nickname", "name"),
                ("app_version", "app"),
            ),
            (
                "request_id",
                "client_nonce_b64url",
                "api_major",
                "nickname",
                "app_version",
                "requested_at",
                "device_model_hint",
            ),
            optional=(("device_model_hint", "hint"),),
        ),
        DeviceProfileOperation.ADMISSION_POLL: _Spec(
            b"autplay:device-compatibility:admission:poll:v2\n",
            _COMMON
            + _PROOF
            + (
                ("request_id", "uuid"),
                ("device_key_thumbprint_sha256", "hash"),
                ("client_nonce_b64url", "b32"),
            ),
            ("request_id", "client_nonce_b64url"),
        ),
        DeviceProfileOperation.ADMISSION_RECOVER: _Spec(
            b"autplay:device-compatibility:admission:recover:v2\n",
            _COMMON
            + _PROOF
            + (
                ("request_id", "uuid"),
                ("request_sha256", "hash"),
                ("device_key_thumbprint_sha256", "hash"),
                ("recovery_nonce_b64url", "b32"),
            ),
            ("request_id", "request_sha256", "recovery_nonce_b64url"),
        ),
        DeviceProfileOperation.ADMISSION_EXCHANGE: _Spec(
            b"autplay:device-compatibility:admission:exchange:v2\n",
            _COMMON
            + _KEY
            + _PROOF
            + (
                ("request_id", "uuid"),
                ("request_sha256", "hash"),
                ("exchange_id", "uuid"),
                ("binding_commit_id", "uuid"),
                ("poll_bearer_sha256", "hash"),
                ("approved_account_id", "uuid"),
                ("client_nonce_b64url", "b32"),
                ("next_refresh_token_sha256", "hash"),
            ),
            (
                "request_id",
                "request_sha256",
                "exchange_id",
                "binding_commit_id",
                "poll_bearer_sha256",
                "approved_account_id",
                "client_nonce_b64url",
                "next_refresh_token_sha256",
            ),
        ),
        DeviceProfileOperation.TRUSTED_CHALLENGE: _Spec(
            b"autplay:device-compatibility:trusted:challenge:v2\n",
            _COMMON
            + _KEY
            + _PROOF
            + (
                ("challenge_request_id", "uuid"),
                ("account_id", "uuid"),
                ("client_nonce_b64url", "b32"),
            ),
            ("challenge_request_id", "account_id", "client_nonce_b64url"),
        ),
        DeviceProfileOperation.TRUSTED_EXCHANGE: _Spec(
            b"autplay:device-compatibility:trusted:exchange:v2\n",
            _COMMON
            + _KEY
            + _PROOF
            + (
                ("challenge_id", "uuid"),
                ("challenge", "b16"),
                ("exchange_id", "uuid"),
                ("binding_commit_id", "uuid"),
                ("account_id", "uuid"),
                ("next_refresh_token_sha256", "hash"),
                ("client_nonce_b64url", "b32"),
            ),
            (
                "challenge_id",
                "exchange_id",
                "binding_commit_id",
                "account_id",
                "next_refresh_token_sha256",
                "client_nonce_b64url",
            ),
            credential="challenge",
        ),
        DeviceProfileOperation.SESSION_ROTATE: _Spec(
            b"autplay:device-compatibility:session:rotate:v2\n",
            _COMMON
            + _OWN
            + (
                ("rotation_id", "uuid"),
                ("device_id", "uuid"),
                ("parent_session_id", "uuid"),
                ("current_generation", "natural"),
                ("current_refresh_token", "b32"),
                ("next_refresh_token_sha256", "hash"),
            ),
            (
                "rotation_id",
                "device_id",
                "parent_session_id",
                "current_generation",
                "next_refresh_token_sha256",
                "request_sha256",
            ),
            own_hash=True,
            credential="current_refresh_token",
        ),
    }
)


def parse_device_profile_v2(
    operation: DeviceProfileOperation,
    raw: bytes,
) -> ParsedDeviceProfileV2:
    """Validate exact raw UTF-8 JSON and proof inputs; does not verify possession.

    Both raw and full canonical limits are 16384 bytes. Oversize status is 413;
    all other malformed inputs have the same sanitized 400 error. No chained
    decoder exception (which could contain a credential document) escapes.
    """
    status = 400
    try:
        return _parse(operation, raw)
    except _TooLarge:
        status = 413
    except _Invalid, ValueError, TypeError, OverflowError, RecursionError, UnsupportedAlgorithm:
        pass
    # Raise outside except: even __context__ must not retain a JSONDecodeError.doc.
    raise DeviceProfileV2Error(status)


def verify_device_profile_v2(
    parsed: ParsedDeviceProfileV2,
    approved_spki: bytes,
) -> VerifiedDeviceProfileV2:
    """Verify P1363 over MESSAGE(domain || raw SHA256(JCS(exact omissions))).

    Never Prehashed(D), hex(D), or a v1 domain. Body SPKI/JWK, when present,
    must equal the explicit approved key; poll/recover must match its thumbprint.
    Rotation's key MUST come from the locked current session/device service row.
    No Android default or authority fallback is supplied by this pure boundary.
    """
    try:
        if type(parsed) is not ParsedDeviceProfileV2 or type(approved_spki) is not bytes:
            raise _Invalid()
        key = _load_key(approved_spki)
        thumbprint = hashlib.sha256(approved_spki).digest()
        if parsed.body_spki is not None and parsed.body_spki != approved_spki:
            raise _Invalid()
        if (
            "device_key_thumbprint_sha256" in parsed._values
            and parsed.field("device_key_thumbprint_sha256") != thumbprint.hex()
        ):
            raise _Invalid()
        signature = parsed.signature
        der = utils.encode_dss_signature(
            int.from_bytes(signature[:32], "big"),
            int.from_bytes(signature[32:], "big"),
        )
        key.verify(
            der, _SPECS[parsed.operation].domain + parsed.proof_digest, ec.ECDSA(hashes.SHA256())
        )
        verified = object.__new__(VerifiedDeviceProfileV2)
        object.__setattr__(verified, "parsed", parsed)
        object.__setattr__(
            verified,
            "key",
            DeviceKeyEvidenceV2(
                approved_spki,
                thumbprint,
                parsed.body_spki is not None,
            ),
        )
        return verified
    except _Invalid, ValueError, TypeError, OverflowError, InvalidSignature, UnsupportedAlgorithm:
        pass
    raise DeviceProfileV2Error(401)


def _parse(operation: DeviceProfileOperation, raw: bytes) -> ParsedDeviceProfileV2:
    if type(operation) is not DeviceProfileOperation or type(raw) is not bytes:
        raise _Invalid()
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise _TooLarge()
    text = raw.decode("utf-8", errors="strict")
    _bound_depth(text)
    document = json.loads(
        text,
        object_pairs_hook=_pairs,
        parse_int=_integer,
        parse_float=_reject_number,
        parse_constant=_reject_number,
    )
    if type(document) is not dict:
        raise _Invalid()
    spec = _SPECS[operation]
    required, optional = dict(spec.required), dict(spec.optional)
    if not set(required) <= document.keys() or document.keys() - (
        required.keys() | optional.keys()
    ):
        raise _Invalid()
    for name, value in document.items():
        _validate(value, required[name] if name in required else optional[name])
    canonical = rfc8785.dumps(document)
    if len(canonical) > MAX_DOCUMENT_BYTES:
        raise _TooLarge()
    omitted = {"request_sha256", "device_signature_b64url"} if spec.own_hash else {"proof_b64url"}
    digest = hashlib.sha256(
        rfc8785.dumps({name: value for name, value in document.items() if name not in omitted})
    ).digest()
    if spec.own_hash and document["request_sha256"] != digest.hex():
        raise _Invalid()
    body_spki = None
    if "device_public_key_spki_b64" in document:
        body_spki = _standard_b64(document["device_public_key_spki_b64"])
    elif "device_public_key_jwk" in document:
        body_spki = _jwk_spki(document["device_public_key_jwk"])
    if (
        body_spki is not None
        and document["device_key_thumbprint_sha256"] != hashlib.sha256(body_spki).hexdigest()
    ):
        raise _Invalid()
    trust = DeviceTrustV2(
        UUID(document["expected_server_instance_id"]),
        document["expected_identity_epoch"],
        bytes.fromhex(document["expected_identity_thumbprint_sha256"]),
        document["expected_api_origin"],
        document["expected_stream_origin"],
    )
    snapshot = object.__new__(ParsedDeviceProfileV2)
    frozen = {
        name: MappingProxyType(dict(value)) if type(value) is dict else value
        for name, value in document.items()
    }
    signature_field = "device_signature_b64url" if spec.own_hash else "proof_b64url"
    for name, value in (
        ("operation", operation),
        ("platform", DevicePlatformV2(document["platform"])),
        ("trust", trust),
        ("proof_digest", digest),
        ("original_document_sha256", hashlib.sha256(canonical).digest()),
        ("signature", _url_b64(document[signature_field], 64)),
        ("body_spki", body_spki),
        ("_values", MappingProxyType(frozen)),
        ("_canonical", canonical),
    ):
        object.__setattr__(snapshot, name, value)
    return snapshot


def _bound_depth(text: str) -> None:
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
            if depth > MAX_JSON_DEPTH:
                raise _Invalid()
        elif character in "]}":
            depth -= 1


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
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


def _reject_number(token: str) -> None:
    raise _Invalid()


def _url_b64(value: str, size: int) -> bytes:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise _Invalid()
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if (
        len(decoded) != size
        or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value
    ):
        raise _Invalid()
    return decoded


def _standard_b64(value: str) -> bytes:
    decoded = base64.b64decode(value, validate=True)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise _Invalid()
    return decoded


def _load_key(spki: bytes) -> ec.EllipticCurvePublicKey:
    key = serialization.load_der_public_key(spki)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise _Invalid()
    canonical = key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if canonical != spki:
        raise _Invalid()
    return key


def _jwk_spki(value: dict[str, object]) -> bytes:
    if set(value) != {"kty", "crv", "x", "y"} or value["kty"] != "EC" or value["crv"] != "P-256":
        raise _Invalid()
    if type(value["x"]) is not str or type(value["y"]) is not str:
        raise _Invalid()
    key = ec.EllipticCurvePublicNumbers(
        int.from_bytes(_url_b64(value["x"], 32), "big"),
        int.from_bytes(_url_b64(value["y"], 32), "big"),
        ec.SECP256R1(),
    ).public_key()
    return key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _origin(value: str) -> None:
    if not 1 <= len(value) <= 2048 or not value.isascii():
        raise _Invalid()
    if any(ord(character) <= 32 or ord(character) == 127 for character in value):
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
        labels = host.removesuffix(".").split(".")
        if len(host) > 253 or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None for label in labels
        ):
            raise _Invalid()
        canonical_host = host
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise _Invalid()
    if parsed.netloc != canonical_host + (":" + str(port) if port is not None else ""):
        raise _Invalid()


def _validate(value: object, kind: str) -> None:
    if kind in ("positive", "natural", "one", "two"):
        if (
            type(value) is not int
            or not (0 if kind == "natural" else 1) <= value <= MAX_SAFE_INTEGER
        ):
            raise _Invalid()
        if kind in ("one", "two") and value != (1 if kind == "one" else 2):
            raise _Invalid()
        return
    if kind == "jwk":
        if type(value) is not dict:
            raise _Invalid()
        _jwk_spki(value)
        return
    if type(value) is not str:
        raise _Invalid()
    value.encode("utf-8", errors="strict")
    if kind in ("v2", "algorithm", "platform"):
        allowed = {
            "v2": ("v2",),
            "algorithm": (SIGNATURE_ALGORITHM,),
            "platform": ("ANDROID", "MACOS"),
        }
        if value not in allowed[kind]:
            raise _Invalid()
    elif kind in ("name", "app", "hint"):
        if not 1 <= len(value) <= {"name": 120, "app": 32, "hint": 96}[kind]:
            raise _Invalid()
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise _Invalid()
    elif kind == "uuid":
        if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is None:
            raise _Invalid()
    elif kind == "hash":
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise _Invalid()
    elif kind == "time":
        pattern = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z"
        if re.fullmatch(pattern, value) is None:
            raise _Invalid()
        datetime(
            int(value[:4]),
            int(value[5:7]),
            int(value[8:10]),
            int(value[11:13]),
            int(value[14:16]),
            int(value[17:19]),
        )
    elif kind == "origin":
        _origin(value)
    elif kind == "spki":
        if not 32 <= len(value) <= 256:
            raise _Invalid()
        _load_key(_standard_b64(value))
    elif kind in ("b16", "b32", "signature"):
        _url_b64(value, {"b16": 16, "b32": 32, "signature": 64}[kind])
    else:
        raise _Invalid()


__all__ = (
    "DeviceKeyEvidenceV2",
    "DevicePlatformV2",
    "DeviceProfileOperation",
    "DeviceProfileV2Error",
    "DeviceReplayEvidenceV2",
    "DeviceTrustV2",
    "ParsedDeviceProfileV2",
    "VerifiedDeviceProfileV2",
    "parse_device_profile_v2",
    "verify_device_profile_v2",
)
