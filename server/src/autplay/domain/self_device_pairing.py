"""Account-bound phone pairing values and exact proof validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives import serialization

from autplay.domain.auth import AccountRole
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    public_key_thumbprint,
    verify_p1363,
)


class SelfPairingError(RuntimeError):
    def __init__(self, code: str = "self_pairing_unavailable") -> None:
        self.code = code
        super().__init__(code)


COMMON = frozenset(
    {
        "contract_version",
        "schema_version",
        "ceremony_id",
        "requested_at",
        "request_sha256",
        "expected_server_instance_id",
        "expected_identity_epoch",
        "expected_identity_thumbprint_sha256",
        "expected_api_origin",
        "expected_stream_origin",
    }
)
FIELDS = {
    "start": {"operation_id", "rendezvous_secret_sha256"},
    "claim": {
        "claim_id",
        "poll_secret_sha256",
        "device_public_key_spki_b64",
        "device_key_thumbprint_sha256",
        "device_name",
        "platform",
        "app_version",
        "device_signature_b64url",
    },
    "poll": {"claim_id", "claim_request_sha256", "device_signature_b64url"},
    "decision": {
        "operation_id",
        "action",
        "expected_revision",
        "claim_id",
        "claim_request_sha256",
        "comparison_code",
    },
    "exchange": {
        "exchange_id",
        "binding_commit_id",
        "claim_id",
        "claim_request_sha256",
        "approval_operation_id",
        "confirmed_account_id",
        "next_refresh_token_sha256",
        "device_signature_b64url",
    },
}


def parse_request(kind: str, document: dict[str, Any]) -> dict[str, Any]:
    """Validate again at the application boundary, including non-HTTP callers."""
    try:
        if kind not in FIELDS or set(document) != COMMON | FIELDS[kind]:
            raise ValueError("fields")
        if document["contract_version"] != "v1" or type(document["schema_version"]) is not int:
            raise ValueError("version")
        if document["schema_version"] != 1:
            raise ValueError("version")
        for key, value in document.items():
            if (
                value is None
                and kind == "decision"
                and key
                in {
                    "claim_id",
                    "claim_request_sha256",
                    "comparison_code",
                }
            ):
                continue
            if key in {"schema_version", "expected_identity_epoch", "expected_revision"}:
                if type(value) is not int or not 1 <= value <= 2**53 - 1:
                    raise ValueError("integer")
            elif not isinstance(value, str) or not 1 <= len(value) <= 2048:
                raise ValueError("string")
            elif key.endswith("_id") and str(UUID(value)) != value:
                raise ValueError("uuid")
            elif key.endswith("_sha256") and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("hash")
        requested_at(document)
        if kind == "claim" and (
            document["platform"] != "ANDROID"
            or len(document["device_name"]) > 120
            or len(document["app_version"]) > 32
            or any(ord(char) < 32 for char in document["device_name"])
        ):
            raise ValueError("device")
        if kind == "decision" and document["action"] not in {"APPROVE", "REJECT", "CANCEL"}:
            raise ValueError("action")
        if "device_signature_b64url" in document and not re.fullmatch(
            r"[A-Za-z0-9_-]{86}",
            document["device_signature_b64url"],
        ):
            raise ValueError("signature encoding")
        digest = canonical_sha256(
            document,
            omit=frozenset({"request_sha256", "device_signature_b64url"}),
        )
        if not hmac.compare_digest(digest.hex(), document["request_sha256"]):
            raise ValueError("digest")
        return dict(document)
    except KeyError, TypeError, ValueError:
        raise SelfPairingError("self_pairing_request_invalid") from None


def requested_at(document: dict[str, Any]) -> datetime:
    value = document["requested_at"]
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z", value
    ):
        raise ValueError("UTC required")
    return datetime.fromisoformat(value).astimezone(UTC)


def fresh(document: dict[str, Any], now: datetime) -> None:
    if abs((now - requested_at(document)).total_seconds()) > 120:
        raise SelfPairingError()


def secret_hash(secret: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret):
        raise SelfPairingError()
    raw = base64.urlsafe_b64decode(secret + "=")
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != secret:
        raise SelfPairingError()
    # Match the existing M5 convention: SHA-256 of the encoded bearer bytes.
    return hashlib.sha256(secret.encode("ascii")).digest()


def verify_proof(kind: str, document: dict[str, Any], claim: dict[str, Any]) -> None:
    try:
        key = base64.b64decode(claim["device_public_key_spki_b64"], validate=True)
        if (
            len(key) > 256
            or public_key_thumbprint(key).hex() != claim["device_key_thumbprint_sha256"]
        ):
            raise SelfPairingError()
        canonical = serialization.load_der_public_key(key).public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if (
            canonical != key
            or base64.b64encode(key).decode() != claim["device_public_key_spki_b64"]
        ):
            raise SelfPairingError()
        verify_p1363(
            key,
            f"autplay:self-device-pairing:{kind}:v1\n",
            bytes.fromhex(document["request_sha256"]),
            document["device_signature_b64url"],
        )
    except ValueError, TypeError, KeyError, ProfilePairingError:
        raise SelfPairingError() from None


def public_document(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != "device_signature_b64url"}


@dataclass(frozen=True)
class PairingAccount:
    user_id: UUID
    authority_generation: int
    label: str
    role: AccountRole


@dataclass(frozen=True)
class PairingIdentity:
    server_instance_id: UUID
    epoch: int
    thumbprint: bytes
    api_origin: str = field(repr=False)
    stream_origin: str = field(repr=False)

    def matches(self, document: dict[str, Any]) -> bool:
        return bool(
            document["expected_server_instance_id"] == str(self.server_instance_id)
            and document["expected_identity_epoch"] == self.epoch
            and document["expected_identity_thumbprint_sha256"] == self.thumbprint.hex()
            and document["expected_api_origin"] == self.api_origin
            and document["expected_stream_origin"] == self.stream_origin
        )


@dataclass
class SelfDevicePairing:
    ceremony_id: UUID
    server_instance_id: UUID
    user_id: UUID
    authority_generation: int
    source_device_id: UUID
    source_family_id: UUID
    start_operation_id: UUID
    start_document: dict[str, Any] = field(repr=False)
    state: str
    revision: int
    created_at: datetime
    expires_at: datetime
    claim_id: UUID | None = None
    claim_document: dict[str, Any] | None = field(default=None, repr=False)
    approval_operation_id: UUID | None = None
    last_polled_at: datetime | None = None
    exchange_id: UUID | None = None
    exchange_document: dict[str, Any] | None = field(default=None, repr=False)
    result_device_id: UUID | None = None
    result_session_id: UUID | None = None
    receipt_expires_at: datetime | None = None

    def comparison_code(self) -> str:
        if self.claim_document is None:
            raise SelfPairingError()
        payload = {
            "server_instance_id": str(self.server_instance_id),
            "identity_epoch": self.start_document["expected_identity_epoch"],
            "ceremony_id": str(self.ceremony_id),
            "claim_request_sha256": self.claim_document["request_sha256"],
            "key_thumbprint": self.claim_document["device_key_thumbprint_sha256"],
        }
        digest = hashlib.sha256(b"autplay:self-device-pairing:sas:v1\n" + canonical_sha256(payload))
        return f"{int.from_bytes(digest.digest()[:8], 'big') % 10**12:012d}"


@dataclass(frozen=True)
class PairingCommand:
    operation_id: UUID
    ceremony_id: UUID
    user_id: UUID
    device_id: UUID
    family_id: UUID
    request_hash: bytes
    result: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class PairingBinding:
    device_id: UUID
    session_id: UUID
    expires_at: datetime
