"""Pure M5B profile-pairing validation and signature helpers."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ES256_P1363: Final = "ES256-P1363"


class ProfilePairingError(RuntimeError):
    """A stable error intentionally safe for remote callers."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def canonical_sha256(value: Mapping[str, Any], *, omit: frozenset[str] = frozenset()) -> bytes:
    """Hash exact RFC8785 bytes after omitting contract-specified fields."""
    return hashlib.sha256(
        rfc8785.dumps({key: item for key, item in value.items() if key not in omit})
    ).digest()


def public_key_thumbprint(spki: bytes) -> bytes:
    """Return SHA-256 over the canonical DER SubjectPublicKeyInfo evidence."""
    _load_p256_public_key(spki)
    return hashlib.sha256(spki).digest()


def verify_p1363(spki: bytes, domain: str, request_hash: bytes, signature_b64url: str) -> None:
    """Verify an exact P-256/SHA-256 P1363 proof without JSON reserialization."""
    signature = _b64url_decode(signature_b64url)
    if len(signature) != 64:
        raise ProfilePairingError("enrollment_invitation_unavailable")
    der = _p1363_to_der(signature)
    try:
        _load_p256_public_key(spki).verify(
            der, domain.encode("ascii") + request_hash, ec.ECDSA(hashes.SHA256())
        )
    except InvalidSignature as error:
        raise ProfilePairingError("enrollment_invitation_unavailable") from error


def sign_p1363(private_key: ec.EllipticCurvePrivateKey, domain: str, digest: bytes) -> str:
    """Sign a contract document using fixed-width P1363 encoding."""
    from cryptography.hazmat.primitives.asymmetric import utils

    r, s = utils.decode_dss_signature(
        private_key.sign(domain.encode("ascii") + digest, ec.ECDSA(hashes.SHA256()))
    )
    return _b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def load_private_key(pem: bytes) -> ec.EllipticCurvePrivateKey:
    """Load only an EC P-256 private key from the operator secret boundary."""
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("profile identity key must be P-256")
    return key


def public_spki(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def iso8601(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _load_p256_public_key(spki: bytes) -> ec.EllipticCurvePublicKey:
    key = serialization.load_der_public_key(spki)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ProfilePairingError("enrollment_invitation_unavailable")
    return key


def _p1363_to_der(signature: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import utils

    return utils.encode_dss_signature(
        int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    )


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError as error:
        raise ProfilePairingError("enrollment_invitation_unavailable") from error


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


__all__ = (
    "ES256_P1363",
    "ProfilePairingError",
    "canonical_sha256",
    "iso8601",
    "load_private_key",
    "public_key_thumbprint",
    "public_spki",
    "sign_p1363",
    "verify_p1363",
)
