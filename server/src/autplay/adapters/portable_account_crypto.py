"""Bounded portable-account public documents and purpose-bound root-key proofs."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

DOCUMENT_MEMBERS = frozenset(
    {
        "format",
        "version",
        "account_id",
        "public_id",
        "public_key_spki_b64",
        "kdf",
        "iterations",
        "password_salt_b64",
        "password_nonce_b64",
        "password_ciphertext_b64",
        "recovery_nonce_b64",
        "recovery_ciphertext_b64",
    }
)


def validate_account_document(document: Mapping[str, object]) -> tuple[UUID, str, bytes]:
    """Return only public identity; the server never decrypts either key envelope."""
    if (
        set(document) != DOCUMENT_MEMBERS
        or len(rfc8785.dumps(cast(dict[str, Any], dict(document)))) > 4096
    ):
        raise ValueError("account_document_invalid")
    if (
        document["format"] != "autplay-portable-account"
        or type(document["version"]) is not int
        or document["version"] != 1
        or document["kdf"] != "PBKDF2-HMAC-SHA256"
        or type(document["iterations"]) is not int
        or document["iterations"] != 600_000
    ):
        raise ValueError("account_document_invalid")
    account_raw, login = document["account_id"], document["public_id"]
    if (
        not isinstance(account_raw, str)
        or str(UUID(account_raw)) != account_raw
        or not isinstance(login, str)
        or re.fullmatch(r"[a-z0-9_]{3,24}", login) is None
    ):
        raise ValueError("account_document_invalid")
    key = decode_b64(document["public_key_spki_b64"], 91, 91)
    public = serialization.load_der_public_key(key)
    if not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(
        public.curve, ec.SECP256R1
    ):
        raise ValueError("account_document_invalid")
    for name, size in (
        ("password_salt_b64", 16),
        ("password_nonce_b64", 12),
        ("recovery_nonce_b64", 12),
    ):
        decode_b64(document[name], size, size)
    for name in ("password_ciphertext_b64", "recovery_ciphertext_b64"):
        decode_b64(document[name], 80, 512)
    return UUID(account_raw), login, key


def decode_b64(value: object, minimum: int, maximum: int) -> bytes:
    if not isinstance(value, str) or len(value) > (maximum + 2) * 4 // 3 + 4:
        raise ValueError("account_document_invalid")
    decoded = base64.b64decode(value, validate=True)
    if not minimum <= len(decoded) <= maximum or base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("account_document_invalid")
    return decoded


def canonical_request(body: Mapping[str, object]) -> bytes:
    result = rfc8785.dumps(cast(dict[str, Any], dict(body)))
    if not 2 <= len(result) <= 16_384:
        raise ValueError("account_request_invalid")
    return result


def verify_account_proof(
    public_spki: bytes, purpose: str, body: Mapping[str, object], proof: object
) -> bytes:
    if purpose not in {
        "login",
        "register",
        "claim",
        "reauth",
        "backup",
        "password-change",
        "rotate",
    }:
        raise ValueError("account_request_invalid")
    public = serialization.load_der_public_key(public_spki)
    if not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(
        public.curve, ec.SECP256R1
    ):
        raise ValueError("account_authentication_failed")
    payload = canonical_request(body)
    # A P-256 DER signature has at most two 33-byte INTEGERs plus six framing bytes.
    signature = decode_b64(proof, 8, 72)
    try:
        public.verify(
            signature,
            f"autplay:portable-account:{purpose}:v1\n".encode() + payload,
            ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature as error:
        raise ValueError("account_authentication_failed") from error
    return hashlib.sha256(payload).digest()
