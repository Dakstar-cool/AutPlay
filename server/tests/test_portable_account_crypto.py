"""Portable identity parsing and signatures reject cross-purpose or changed requests."""

import base64
from typing import Any
from uuid import uuid4

import pytest
import rfc8785
from autplay.adapters.portable_account_crypto import validate_account_document, verify_account_proof
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _document(public: bytes) -> dict[str, Any]:
    def b64(size: int) -> str:
        return base64.b64encode(bytes(size)).decode()

    return {
        "format": "autplay-portable-account",
        "version": 1,
        "account_id": str(uuid4()),
        "public_id": "test_account",
        "public_key_spki_b64": base64.b64encode(public).decode(),
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": 600_000,
        "password_salt_b64": b64(16),
        "password_nonce_b64": b64(12),
        "password_ciphertext_b64": b64(100),
        "recovery_nonce_b64": b64(12),
        "recovery_ciphertext_b64": b64(100),
    }


def test_valid_public_document_and_purpose_bound_root_proof() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    doc = _document(public)
    account, login, parsed = validate_account_document(doc)
    assert str(account) == doc["account_id"] and login == "test_account" and parsed == public
    body: dict[str, Any] = {"challenge_id": str(uuid4()), "nonce": "a-server-bound-test-nonce"}
    signature = base64.b64encode(
        key.sign(
            b"autplay:portable-account:login:v1\n" + rfc8785.dumps(body), ec.ECDSA(hashes.SHA256())
        )
    ).decode()
    assert len(verify_account_proof(public, "login", body, signature)) == 32
    with pytest.raises(ValueError, match="account_authentication_failed"):
        verify_account_proof(public, "register", body, signature)
    with pytest.raises(ValueError, match="account_authentication_failed"):
        verify_account_proof(public, "login", {**body, "nonce": "different"}, signature)


@pytest.mark.parametrize(
    "change",
    [
        {"iterations": 600_000_000},
        {"version": True},
        {"public_id": "INVALID"},
        {"extra": "unexpected"},
        {"password_salt_b64": "AA=="},
        {"password_nonce_b64": "AA=="},
    ],
)
def test_malformed_or_expensive_envelopes_are_rejected(change: dict[str, object]) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    doc = _document(
        key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    with pytest.raises(ValueError):
        validate_account_document({**doc, **change})


def test_rotation_proof_rejects_cross_purpose_wrong_curve_and_oversized_der() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    body = {"replacement_account": _document(public)}
    signed = base64.b64encode(
        key.sign(
            b"autplay:portable-account:rotate:v1\n" + rfc8785.dumps(body), ec.ECDSA(hashes.SHA256())
        )
    ).decode()
    assert len(verify_account_proof(public, "rotate", body, signed)) == 32
    with pytest.raises(ValueError):
        verify_account_proof(public, "login", body, signed)
    with pytest.raises(ValueError):
        verify_account_proof(public, "rotate", body, base64.b64encode(bytes(73)).decode())
    other_curve = (
        ec.generate_private_key(ec.SECP256K1())
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    with pytest.raises(ValueError):
        validate_account_document(_document(other_curve))
