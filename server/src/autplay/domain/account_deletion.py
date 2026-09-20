"""Purpose-bound account deletion and cancellation requests."""

from __future__ import annotations

import base64
import hmac
import re
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives import serialization

from .account_recovery import COMMON_FIELDS, DEVICE_FIELDS, requested_at, valid_origin
from .profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    public_key_thumbprint,
    verify_p1363,
)

REQUEST_FIELDS = {
    "request": COMMON_FIELDS
    | DEVICE_FIELDS
    | {
        "confirmed_account_id",
        "expected_code_generation",
        "expected_authority_generation",
    },
    "preview": COMMON_FIELDS | DEVICE_FIELDS,
    "cancel": COMMON_FIELDS
    | DEVICE_FIELDS
    | {
        "confirmed_account_id",
        "deletion_request_id",
        "expected_revision",
        "expected_code_generation",
        "next_code_verifier_sha256",
        "next_refresh_token_sha256",
        "binding_commit_id",
    },
}


class AccountDeletionError(RuntimeError):
    def __init__(self, code: str = "account_deletion_unavailable") -> None:
        self.code = code
        super().__init__(code)


def parse_request(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    try:
        if kind not in REQUEST_FIELDS or set(raw) != REQUEST_FIELDS[kind]:
            raise ValueError("fields")
        if raw["contract_version"] != "v1" or raw["schema_version"] != 1:
            raise ValueError("version")
        for name, value in raw.items():
            if name in {
                "schema_version",
                "expected_identity_epoch",
                "expected_code_generation",
                "expected_authority_generation",
                "expected_revision",
            }:
                if type(value) is not int or not 1 <= value <= 2**53 - 1:
                    raise ValueError("integer")
            elif not isinstance(value, str) or not 1 <= len(value) <= 2048:
                raise ValueError("string")
            elif name.endswith("_id") and str(UUID(value)) != value:
                raise ValueError("uuid")
            elif name.endswith("_sha256") and re.fullmatch(r"[a-f0-9]{64}", value) is None:
                raise ValueError("digest")
            if isinstance(value, str):
                value.encode("utf-8")
        if not all(
            valid_origin(raw[name]) for name in ("expected_api_origin", "expected_stream_origin")
        ):
            raise ValueError("origin")
        requested_at(raw)
        if (
            raw["platform"] != "ANDROID"
            or len(raw["device_name"]) > 120
            or len(raw["app_version"]) > 32
        ):
            raise ValueError("device")
        if any(
            ord(char) < 32 or ord(char) == 127
            for name in ("device_name", "app_version")
            for char in raw[name]
        ):
            raise ValueError("device text")
        if re.fullmatch(r"[A-Za-z0-9_-]{86}", raw["device_signature_b64url"]) is None:
            raise ValueError("signature")
        digest = canonical_sha256(
            raw, omit=frozenset({"request_sha256", "device_signature_b64url"})
        )
        if not hmac.compare_digest(digest.hex(), raw["request_sha256"]):
            raise ValueError("digest")
        if kind != "preview" and raw["confirmed_account_id"] != raw["account_id"]:
            raise ValueError("confirmation")
        return dict(raw)
    except ValueError, TypeError, KeyError, OverflowError:
        raise AccountDeletionError("deletion_request_invalid") from None


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
            raise ValueError("encoding")
        verify_p1363(
            key,
            f"autplay:account-deletion:{kind}:v1\n",
            bytes.fromhex(raw["request_sha256"]),
            raw["device_signature_b64url"],
        )
        return key
    except ValueError, TypeError, KeyError, ProfilePairingError:
        raise AccountDeletionError() from None
