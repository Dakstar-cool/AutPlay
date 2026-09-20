"""Bounded account recovery documents, code verifiers and new-device proofs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives import serialization

from .profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    public_key_thumbprint,
    verify_p1363,
)

CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MAX_FILE_BYTES = 4096
COMMON_FIELDS = frozenset(
    {
        "contract_version",
        "schema_version",
        "operation_id",
        "requested_at",
        "request_sha256",
        "expected_server_instance_id",
        "expected_identity_epoch",
        "expected_identity_thumbprint_sha256",
        "expected_api_origin",
        "expected_stream_origin",
        "account_id",
    }
)
DEVICE_FIELDS = frozenset(
    {
        "device_public_key_spki_b64",
        "device_key_thumbprint_sha256",
        "device_name",
        "platform",
        "app_version",
        "device_signature_b64url",
    }
)
REQUEST_FIELDS = {
    "configure": COMMON_FIELDS | {"expected_code_generation", "next_code_verifier_sha256"},
    "preview": COMMON_FIELDS | DEVICE_FIELDS,
    "recover": COMMON_FIELDS
    | DEVICE_FIELDS
    | {
        "expected_code_generation",
        "next_code_verifier_sha256",
        "next_refresh_token_sha256",
        "binding_commit_id",
        "confirmed_account_id",
    },
}


class AccountRecoveryError(RuntimeError):
    def __init__(self, code: str = "account_recovery_unavailable") -> None:
        self.code = code
        super().__init__(code)


def normalize_code(value: str) -> str:
    if not isinstance(value, str) or len(value) > 96 or not value.isascii():
        raise AccountRecoveryError("recovery_code_invalid")
    normalized = re.sub(r"[ \t\r\n-]", "", value).upper()
    if len(normalized) != 32 or any(char not in CODE_ALPHABET for char in normalized):
        raise AccountRecoveryError("recovery_code_invalid")
    return normalized


def new_code() -> str:
    value = "".join(secrets.choice(CODE_ALPHABET) for _ in range(32))
    return "-".join(value[index : index + 4] for index in range(0, 32, 4))


def code_verifier(server_id: UUID, account_id: UUID, code: str) -> bytes:
    return hashlib.sha256(
        b"autplay:account-recovery-code:v1\x00"
        + server_id.bytes
        + account_id.bytes
        + normalize_code(code).encode("ascii")
    ).digest()


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def valid_origin(value: str) -> bool:
    try:
        url = urlsplit(value)
        return bool(
            value.isascii()
            and len(value) <= 2048
            and url.scheme in {"https", "http"}
            and url.hostname
            and url.port != 0
            and not url.username
            and not url.password
            and url.path == ""
            and not url.query
            and not url.fragment
            and not any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in value)
        )
    except ValueError:
        return False


def parse_request(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    try:
        if kind not in REQUEST_FIELDS or set(raw) != REQUEST_FIELDS[kind]:
            raise ValueError("fields")
        if (
            raw["contract_version"] != "v1"
            or type(raw["schema_version"]) is not int
            or raw["schema_version"] != 1
        ):
            raise ValueError("version")
        for key, value in raw.items():
            if key in {"schema_version", "expected_identity_epoch", "expected_code_generation"}:
                minimum = 0 if key == "expected_code_generation" and kind == "configure" else 1
                if type(value) is not int or not minimum <= value <= 2**53 - 1:
                    raise ValueError("integer")
            elif not isinstance(value, str) or not 1 <= len(value) <= 2048:
                raise ValueError("string")
            elif key.endswith("_id") and str(UUID(value)) != value:
                raise ValueError("uuid")
            elif key.endswith("_sha256") and re.fullmatch(r"[a-f0-9]{64}", value) is None:
                raise ValueError("digest")
            if isinstance(value, str):
                value.encode("utf-8")
        if not all(
            valid_origin(raw[key]) for key in ("expected_api_origin", "expected_stream_origin")
        ):
            raise ValueError("origin")
        requested_at(raw)
        if kind != "configure":
            if (
                raw["platform"] != "ANDROID"
                or len(raw["device_name"]) > 120
                or len(raw["app_version"]) > 32
            ):
                raise ValueError("device")
            if any(
                ord(char) < 32 or ord(char) == 127
                for key in ("device_name", "app_version")
                for char in raw[key]
            ):
                raise ValueError("device text")
            if re.fullmatch(r"[A-Za-z0-9_-]{86}", raw["device_signature_b64url"]) is None:
                raise ValueError("signature")
        digest = canonical_sha256(
            raw, omit=frozenset({"request_sha256", "device_signature_b64url"})
        )
        if not hmac.compare_digest(digest.hex(), raw["request_sha256"]):
            raise ValueError("request digest")
        if kind == "recover" and raw["confirmed_account_id"] != raw["account_id"]:
            raise ValueError("confirmation")
        return dict(raw)
    except ValueError, TypeError, KeyError, OverflowError:
        raise AccountRecoveryError("recovery_request_invalid") from None


def requested_at(raw: dict[str, Any]) -> datetime:
    value = raw["requested_at"]
    if (
        not isinstance(value, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z", value) is None
    ):
        raise ValueError("UTC required")
    return datetime.fromisoformat(value).astimezone(UTC)


def require_fresh(raw: dict[str, Any], now: datetime) -> None:
    if abs((now - requested_at(raw)).total_seconds()) > 120:
        raise AccountRecoveryError()


def verify_device(kind: str, raw: dict[str, Any]) -> bytes:
    try:
        key = base64.b64decode(raw["device_public_key_spki_b64"], validate=True)
        if (
            len(key) > 256
            or public_key_thumbprint(key).hex() != raw["device_key_thumbprint_sha256"]
        ):
            raise ValueError("key")
        canonical = serialization.load_der_public_key(key).public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if key != canonical or base64.b64encode(key).decode() != raw["device_public_key_spki_b64"]:
            raise ValueError("key encoding")
        verify_p1363(
            key,
            f"autplay:account-recovery:{kind}:v1\n",
            bytes.fromhex(raw["request_sha256"]),
            raw["device_signature_b64url"],
        )
        return key
    except ValueError, TypeError, KeyError, ProfilePairingError:
        raise AccountRecoveryError() from None


@dataclass(frozen=True)
class RecoveryFile:
    server_instance_id: UUID
    identity_epoch: int
    identity_thumbprint_sha256: str
    api_origin: str = field(repr=False)
    stream_origin: str = field(repr=False)
    account_id: UUID
    account_label: str
    code: str = field(repr=False)

    def encode(self) -> bytes:
        value = {
            "format": "autplay-account-recovery",
            "version": 1,
            "server_instance_id": str(self.server_instance_id),
            "identity_epoch": self.identity_epoch,
            "identity_thumbprint_sha256": self.identity_thumbprint_sha256,
            "api_origin": self.api_origin,
            "stream_origin": self.stream_origin,
            "account_id": str(self.account_id),
            "account_label": self.account_label,
            "code": normalize_code(self.code),
        }
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
        self.decode(payload)
        return payload

    @classmethod
    def decode(cls, payload: bytes) -> RecoveryFile:
        try:
            if not 1 <= len(payload) <= MAX_FILE_BYTES:
                raise ValueError("size")
            raw = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_pairs)
            if not isinstance(raw, dict) or set(raw) != {
                "format",
                "version",
                "server_instance_id",
                "identity_epoch",
                "identity_thumbprint_sha256",
                "api_origin",
                "stream_origin",
                "account_id",
                "account_label",
                "code",
            }:
                raise ValueError("shape")
            if (
                raw.pop("format") != "autplay-account-recovery"
                or type(raw["version"]) is not int
                or raw.pop("version") != 1
            ):
                raise ValueError("version")
            if (
                type(raw["identity_epoch"]) is not int
                or not 1 <= raw["identity_epoch"] <= 2**53 - 1
            ):
                raise ValueError("epoch")
            if not all(isinstance(raw[key], str) for key in raw if key != "identity_epoch"):
                raise ValueError("string")
            for value in raw.values():
                if isinstance(value, str):
                    value.encode("utf-8")
            if re.fullmatch(r"[a-f0-9]{64}", raw["identity_thumbprint_sha256"]) is None:
                raise ValueError("thumbprint")
            if not all(valid_origin(raw[key]) for key in ("api_origin", "stream_origin")):
                raise ValueError("origin")
            label = raw["account_label"]
            if not 1 <= len(label) <= 200 or any(ord(char) < 32 for char in label):
                raise ValueError("label")
            for key in ("server_instance_id", "account_id"):
                value = UUID(raw[key])
                if str(value) != raw[key]:
                    raise ValueError("UUID")
                raw[key] = value
            raw["code"] = normalize_code(raw["code"])
            return cls(**raw)
        except ValueError, TypeError, KeyError, UnicodeError, RecursionError, AccountRecoveryError:
            raise AccountRecoveryError("recovery_file_invalid") from None
