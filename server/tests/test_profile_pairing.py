"""Focused non-database evidence for M5B cryptographic and HTTP boundaries."""

from __future__ import annotations

import hashlib

import pytest
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    public_key_thumbprint,
    public_spki,
    sign_p1363,
    verify_p1363,
)
from autplay.entrypoints.profile_pairing_http import create_profile_pairing_router
from autplay.runtime.http import install_error_handlers
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_p256_p1363_proof_is_domain_bound() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    digest = hashlib.sha256(b"canonical request").digest()
    signature = sign_p1363(key, "AutPlay discovery v1\n", digest)

    verify_p1363(spki, "AutPlay discovery v1\n", digest, signature)
    assert len(public_key_thumbprint(spki)) == 32
    with pytest.raises(ProfilePairingError):
        verify_p1363(spki, "AutPlay capabilities v1\n", digest, signature)


def test_unconfigured_pairing_routes_fail_closed_with_no_store() -> None:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_profile_pairing_router(None, authenticated=lambda _: None))

    response = TestClient(app).get("/pairing/discovery")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capability_missing"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
