"""Pure server-bound D1 recovery proof prerequisites; SOURCE ONLY, UNVERIFIED.

Three route operations use TWO signed models. OUTCOME verifies the ORIGINAL
RecoveryCommit under recovery:recover, never an outcome-domain signature.
The immutable full canonical original, exact proof, digest, version/platform and
identity/key/binding facts are nonsecret historical evidence to retain in protected
typed history. Code/refresh headers are deliberately absent from this API.

Proof possession is not current service authority. Future services own freshness
for first effects (+/-120s), current identity/transport trust, account/root/code
generation and the EXISTING account_recovery.code_verifier recipe (including the
server/account prefix, normalization and separate keyed rate HMACs). No SHA256(code)
replacement or recovery/export format is defined here. Services also own current
target identity, key blocks/platform conflicts, active result session, exact chosen
refresh header, binding/operation locks, one-day receipt versus retained outcome,
expiry/retention, gates and quotas shared with v1. No token or effect is authorized
by these results. K, J, retained grants and media authority are outside this module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Final, Literal, overload
from uuid import UUID

import rfc8785
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from autplay.domain.profile_pairing import ProfilePairingError, canonical_sha256, verify_p1363

MAX_BODY_BYTES: Final = 8192
_MAX_INTEGER: Final = 9007199254740991
_OMIT: Final = frozenset({"request_sha256", "device_signature_b64url"})
_TRUST_FIELDS: Final = frozenset(
    {
        "expected_server_instance_id",
        "expected_identity_epoch",
        "expected_identity_thumbprint_sha256",
        "expected_api_origin",
        "expected_stream_origin",
    }
)
_COMMON_FIELDS: Final = _TRUST_FIELDS | frozenset(
    {
        "contract_version",
        "schema_version",
        "platform",
        "operation_id",
        "account_id",
        "requested_at",
        "request_sha256",
        "device_public_key_spki_b64",
        "device_key_thumbprint_sha256",
        "device_name",
        "app_version",
        "device_signature_b64url",
    }
)
_COMMIT_FIELDS: Final = frozenset(
    {
        "expected_code_generation",
        "next_code_verifier_sha256",
        "next_refresh_token_sha256",
        "binding_commit_id",
        "confirmed_account_id",
    }
)


class RecoveryOperation(Enum):
    PREVIEW = "recovery.preview"
    RECOVER = "recovery.recover"
    OUTCOME = "recovery.outcome"


class DevicePlatform(Enum):
    ANDROID = "ANDROID"
    MACOS = "MACOS"


class KeyEvidenceSource(Enum):
    """Service assertion of provenance, never OS attestation or session authority."""

    CLIENT_SUPPLIED = "CLIENT_SUPPLIED"
    LOCKED_APPROVED = "LOCKED_APPROVED"
    HISTORICAL_APPROVED = "HISTORICAL_APPROVED"


@dataclass(frozen=True, slots=True, repr=False)
class DeviceKeyEvidence:
    """Explicit inputs; the verifier validates key/enum types and exact agreement.

    Resolve approved/historical values from authoritative locked evidence. Merely
    constructing this value does not authenticate its source or revive a key.
    """

    public_key_spki: bytes
    platform: DevicePlatform
    source: KeyEvidenceSource

    def __repr__(self) -> str:
        return "DeviceKeyEvidence(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DeviceTrustEvidence:
    """Explicit exact expected identity, from service authority or typed history.

    Historical identity may differ from current identity for an already SEALED
    deletion veto. Choosing that exception remains the service's responsibility.
    Origins are compared byte-for-byte; release HTTPS/PKI policy remains external.
    """

    server_instance_id: str
    identity_epoch: int
    identity_thumbprint_sha256: str
    api_origin: str
    stream_origin: str

    def __repr__(self) -> str:
        return "DeviceTrustEvidence(<redacted>)"


class RecoveryV2Error(RuntimeError):
    """Stable payload-free error; exception context is suppressed at boundaries."""

    def __init__(self, *, proof: bool = False, oversized: bool = False) -> None:
        self.code = "account_recovery_unavailable" if proof else "device_contract_invalid"
        self.http_status = 401 if proof else (413 if oversized else 400)
        super().__init__(self.code)

    def __repr__(self) -> str:
        return "RecoveryV2Error(<redacted>)"


_Scalar = str | int


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class _DocumentView(Mapping[str, _Scalar]):
    _items: tuple[tuple[str, _Scalar], ...]

    @overload
    def __getitem__(
        self,
        key: Literal[
            "expected_authority_generation",
            "expected_code_generation",
            "expected_identity_epoch",
            "expected_revision",
            "schema_version",
        ],
    ) -> int: ...

    @overload
    def __getitem__(
        self,
        key: Literal[
            "account_id",
            "app_version",
            "binding_commit_id",
            "confirmed_account_id",
            "contract_version",
            "deletion_request_id",
            "device_key_thumbprint_sha256",
            "device_name",
            "device_public_key_spki_b64",
            "device_signature_b64url",
            "expected_api_origin",
            "expected_identity_thumbprint_sha256",
            "expected_server_instance_id",
            "expected_stream_origin",
            "next_code_verifier_sha256",
            "next_refresh_token_sha256",
            "operation_id",
            "platform",
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
        raise KeyError("unknown proof member")

    def __iter__(self) -> Iterator[str]:
        return (name for name, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return "DeviceProofDocument(<redacted>)"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _ParsedDeviceDocument:
    operation: Enum
    _items: tuple[tuple[str, _Scalar], ...]
    canonical_document: bytes
    request_sha256: bytes
    original_document_sha256: bytes
    signature: bytes
    public_key_spki: bytes

    def __new__(cls, *args: object, **kwargs: object) -> _ParsedDeviceDocument:
        raise TypeError("use the typed request parser")

    @property
    def document(self) -> _DocumentView:
        return _DocumentView(self._items)

    @property
    def platform(self) -> DevicePlatform:
        return DevicePlatform(self.document["platform"])

    @property
    def trust(self) -> DeviceTrustEvidence:
        doc = self.document
        return DeviceTrustEvidence(
            _text(doc["expected_server_instance_id"]),
            _integer(doc["expected_identity_epoch"]),
            _text(doc["expected_identity_thumbprint_sha256"]),
            _text(doc["expected_api_origin"]),
            _text(doc["expected_stream_origin"]),
        )

    def __repr__(self) -> str:
        return "ParsedDeviceProofDocument(<redacted>)"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ParsedRecoveryRequest(_ParsedDeviceDocument):
    """Parser-produced immutable FULL original, including original hash/signature.

    request_sha256 is the registry digest D. original_document_sha256 hashes full
    JCS, including proof and hash. canonical_document retains all original values,
    with no text normalization, secret placeholder or defaults. JSON whitespace is
    not signed; full JCS is retained rather than a raw transport capture.
    """

    operation: RecoveryOperation


_DOMAINS: Final = MappingProxyType(
    {
        RecoveryOperation.PREVIEW: "autplay:device-compatibility:recovery:preview:v2\n",
        RecoveryOperation.RECOVER: "autplay:device-compatibility:recovery:recover:v2\n",
        RecoveryOperation.OUTCOME: "autplay:device-compatibility:recovery:recover:v2\n",
    }
)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class VerifiedRecoveryProof:
    """Proof verification only, with explicit key/platform/trust provenance.

    This is not a binding, receipt, code verification or access grant. Retain the
    complete nonsecret request and exact signature for historical outcomes.
    """

    request: ParsedRecoveryRequest
    key_evidence: DeviceKeyEvidence
    trust_evidence: DeviceTrustEvidence

    def __new__(cls, *args: object, **kwargs: object) -> VerifiedRecoveryProof:
        raise TypeError("use verify_recovery_proof")

    @property
    def proof_domain(self) -> str:
        return _DOMAINS[self.request.operation]

    @property
    def signature_algorithm(self) -> str:
        return "ES256-P1363"

    def __repr__(self) -> str:
        return "VerifiedRecoveryProof(<redacted>)"


def parse_recovery_request(operation: RecoveryOperation, raw_body: bytes) -> ParsedRecoveryRequest:
    """Strict raw JSON parser for RecoveryPreview or exact RecoveryCommit.

    No recovery configure/status, file/export parser, defaults or v1 conversion.
    OUTCOME accepts the same model as RECOVER, including its original timestamp.
    """
    if type(operation) is not RecoveryOperation:
        raise RecoveryV2Error()
    fields = _COMMON_FIELDS | (
        frozenset() if operation is RecoveryOperation.PREVIEW else _COMMIT_FIELDS
    )
    return _parse_device_document(
        operation, raw_body, fields, ParsedRecoveryRequest, RecoveryV2Error
    )


def verify_recovery_proof(
    request: ParsedRecoveryRequest,
    *,
    key_evidence: DeviceKeyEvidence,
    expected_trust: DeviceTrustEvidence,
    retained_original: ParsedRecoveryRequest | None = None,
) -> VerifiedRecoveryProof:
    """Verify original-domain MESSAGE P1363; never require or grant a session here.

    OUTCOME requires full retained RECOVER evidence and a locked/historical key.
    Reparse retained full canonical bytes with RECOVER when loading typed history;
    never reconstruct from a digest, receipt or redacted projection. Other routes
    may supply an original for exact replay comparison; all authority is external.
    """
    if (
        type(request) is not ParsedRecoveryRequest
        or type(request.operation) is not RecoveryOperation
    ):
        raise RecoveryV2Error(proof=True)
    try:
        if request.operation is RecoveryOperation.OUTCOME:
            if (
                type(retained_original) is not ParsedRecoveryRequest
                or retained_original.operation is not RecoveryOperation.RECOVER
                or type(key_evidence) is not DeviceKeyEvidence
                or key_evidence.source
                not in {
                    KeyEvidenceSource.LOCKED_APPROVED,
                    KeyEvidenceSource.HISTORICAL_APPROVED,
                }
            ):
                raise ValueError("original evidence")
        elif (
            type(key_evidence) is DeviceKeyEvidence
            and key_evidence.source is KeyEvidenceSource.HISTORICAL_APPROVED
        ):
            raise ValueError("historical key")
        if retained_original is not None:
            if type(retained_original) is not ParsedRecoveryRequest:
                raise ValueError("original type")
            _require_original(request, retained_original)
        _verify_device_document(request, key_evidence, expected_trust, _DOMAINS[request.operation])
    except ValueError, TypeError, KeyError, UnsupportedAlgorithm, ProfilePairingError:
        raise RecoveryV2Error(proof=True) from None
    result = object.__new__(VerifiedRecoveryProof)
    object.__setattr__(result, "request", request)
    object.__setattr__(result, "key_evidence", key_evidence)
    object.__setattr__(result, "trust_evidence", expected_trust)
    return result


# Shared implementation with device_deletion_v2 only. Neither helper dispatches
# wire operations; the two public parsers each enforce their own closed registry.
def _parse_device_document[Parsed: _ParsedDeviceDocument](
    operation: Enum,
    raw_body: bytes,
    fields: frozenset[str],
    result_type: type[Parsed],
    error_type: type[RecoveryV2Error],
) -> Parsed:
    if type(raw_body) is not bytes:
        raise error_type()
    if len(raw_body) > MAX_BODY_BYTES:
        raise error_type(oversized=True)
    try:
        doc = json.loads(
            raw_body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_int=_safe_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        _validate_document(doc, fields)
        canonical = rfc8785.dumps(doc)
        if len(canonical) > MAX_BODY_BYTES:
            raise error_type(oversized=True)
        digest = canonical_sha256(doc, omit=_OMIT)
        if not hmac.compare_digest(digest.hex(), doc["request_sha256"]):
            raise ValueError("digest")
        signature = _decode_signature(doc["device_signature_b64url"])
        spki = _decode_spki(doc["device_public_key_spki_b64"])
    except (
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        UnsupportedAlgorithm,
        ProfilePairingError,
    ):
        raise error_type() from None
    result = object.__new__(result_type)
    for name, value in (
        ("operation", operation),
        ("_items", tuple(sorted(doc.items()))),
        ("canonical_document", canonical),
        ("request_sha256", digest),
        ("original_document_sha256", hashlib.sha256(canonical).digest()),
        ("signature", signature),
        ("public_key_spki", spki),
    ):
        object.__setattr__(result, name, value)
    return result


def _text(value: _Scalar) -> str:
    if type(value) is not str:
        raise ValueError("string")
    return value


def _integer(value: _Scalar) -> int:
    if type(value) is not int:
        raise ValueError("integer")
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


def _validate_document(doc: object, fields: frozenset[str]) -> None:
    if type(doc) is not dict or set(doc) != fields:
        raise ValueError("members")
    for name, value in doc.items():
        if name in {
            "schema_version",
            "expected_identity_epoch",
            "expected_code_generation",
            "expected_authority_generation",
            "expected_revision",
        }:
            if type(value) is not int or not 1 <= value <= _MAX_INTEGER:
                raise ValueError("integer")
            continue
        if type(value) is not str or not 1 <= len(value) <= 2048:
            raise ValueError("string")
        value.encode("utf-8", errors="strict")
        if name.endswith("_id") and str(UUID(value)) != value:
            raise ValueError("uuid")
        if name.endswith("_sha256") and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("hash")
    if doc["contract_version"] != "v2" or doc["schema_version"] != 2:
        raise ValueError("version")
    DevicePlatform(doc["platform"])
    _timestamp(doc["requested_at"])
    _origin(doc["expected_api_origin"])
    _origin(doc["expected_stream_origin"])
    for name, limit in (("device_name", 120), ("app_version", 32)):
        value = doc[name]
        if len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("display text")
    spki = _decode_spki(doc["device_public_key_spki_b64"])
    if not hmac.compare_digest(
        hashlib.sha256(spki).hexdigest(), doc["device_key_thumbprint_sha256"]
    ):
        raise ValueError("thumbprint")
    _decode_signature(doc["device_signature_b64url"])
    if "confirmed_account_id" in doc and doc["confirmed_account_id"] != doc["account_id"]:
        raise ValueError("confirmation")


def _timestamp(value: str) -> None:
    if (
        re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):"
            r"[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,9})?Z",
            value,
        )
        is None
    ):
        raise ValueError("UTC")
    # Keep wire time bounds explicit: fromisoformat also accepts ISO 8601's 24:00.
    datetime.fromisoformat(value[:19])  # Calendar check only; retain exact nanoseconds.


def _origin(value: str) -> None:
    value.encode("ascii", errors="strict")
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


def _canonical_spki(raw: bytes) -> None:
    if type(raw) is not bytes or not 1 <= len(raw) <= 192:
        raise ValueError("SPKI")
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


def _decode_spki(value: str) -> bytes:
    if not 32 <= len(value) <= 256:
        raise ValueError("SPKI bound")
    raw = base64.b64decode(value, validate=True)
    if base64.b64encode(raw).decode("ascii") != value:
        raise ValueError("canonical base64")
    _canonical_spki(raw)
    return raw


def _decode_signature(value: str) -> bytes:
    if re.fullmatch(r"[A-Za-z0-9_-]{86}", value) is None:
        raise ValueError("P1363")
    raw = base64.urlsafe_b64decode(value + "==")
    if len(raw) != 64 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise ValueError("canonical P1363")
    return raw


def _require_original(request: _ParsedDeviceDocument, original: _ParsedDeviceDocument) -> None:
    # Full original bytes (including proof) matter, not just the proof digest D.
    if not hmac.compare_digest(request.canonical_document, original.canonical_document):
        raise ValueError("original changed")


def _verify_device_document(
    request: _ParsedDeviceDocument,
    key: DeviceKeyEvidence,
    trust: DeviceTrustEvidence,
    domain: str,
) -> None:
    if (
        type(key) is not DeviceKeyEvidence
        or type(key.platform) is not DevicePlatform
        or type(key.source) is not KeyEvidenceSource
        or type(trust) is not DeviceTrustEvidence
        or type(trust.identity_epoch) is not int
        or any(
            type(getattr(trust, name)) is not str
            for name in (
                "server_instance_id",
                "identity_thumbprint_sha256",
                "api_origin",
                "stream_origin",
            )
        )
    ):
        raise ValueError("evidence")
    _canonical_spki(key.public_key_spki)
    if (
        request.platform is not key.platform
        or request.trust != trust
        or not hmac.compare_digest(request.public_key_spki, key.public_key_spki)
    ):
        raise ValueError("key/platform/trust")
    verify_p1363(
        key.public_key_spki,
        domain,
        request.request_sha256,
        _text(request.document["device_signature_b64url"]),
    )


__all__ = (
    "MAX_BODY_BYTES",
    "DeviceKeyEvidence",
    "DevicePlatform",
    "DeviceTrustEvidence",
    "KeyEvidenceSource",
    "ParsedRecoveryRequest",
    "RecoveryOperation",
    "RecoveryV2Error",
    "VerifiedRecoveryProof",
    "parse_recovery_request",
    "verify_recovery_proof",
)
