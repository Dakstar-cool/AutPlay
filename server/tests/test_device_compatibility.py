"""D1 negotiation source fixtures. No precomputed vector or execution claim."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
import rfc8785
from autplay.domain.canonical_json import JsonValue
from autplay.domain.device_compatibility import (
    CAPABILITIES_DOMAIN,
    CAPABILITIES_MAX_BYTES,
    CAPABILITIES_TTL,
    DISCOVERY_DOMAIN,
    DISCOVERY_MAX_BYTES,
    DISCOVERY_TTL,
    MAX_REVISION,
    DeviceCompatibilityError,
    NegotiationIdentity,
    NegotiationPolicy,
    issued_payload,
    signed_capabilities,
    signed_discovery,
)
from autplay.domain.profile_pairing import public_key_thumbprint, public_spki
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from device_qualification import set_device_qualification

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


def test_no_v1_path_or_operator_gate_qualifies_a_v2_family(monkeypatch: pytest.MonkeyPatch) -> None:
    set_device_qualification(monkeypatch)
    for enabled in (False, True):
        policy = NegotiationPolicy(
            enabled=enabled,
            portable_enabled=True,
            self_enabled=True,
            recovery_enabled=True,
            deletion_enabled=True,
            deletion_evidence_ready=True,
        )
        assert policy.families() == []
        assert policy.gates()["device_compatibility_v2_enabled"] is enabled


@pytest.mark.parametrize("kind", ["discovery", "capabilities"])
def test_exact_envelope_members_and_message_signature_recipe(kind: str) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    identity = NegotiationIdentity(
        UUID("11111111-1111-4111-8111-111111111111"),
        1,
        spki,
        public_key_thumbprint(spki),
        "https://api.test.invalid",
        "https://stream.test.invalid",
    )
    effective = {**identity.fields(), "families": []}
    if kind == "discovery":
        effective["platforms"] = []
        ttl, domain, maximum = DISCOVERY_TTL, DISCOVERY_DOMAIN, DISCOVERY_MAX_BYTES
        signer = signed_discovery
    else:
        effective.update(
            {
                "platform": "MACOS",
                "user_id": "22222222-2222-4222-8222-222222222222",
                "device_id": "33333333-3333-4333-8333-333333333333",
                "capability_revision": 1,
            }
        )
        ttl, domain, maximum = CAPABILITIES_TTL, CAPABILITIES_DOMAIN, CAPABILITIES_MAX_BYTES
        signer = signed_capabilities
    payload = issued_payload(effective, NOW, ttl)
    envelope = signer(key, payload)
    assert set(envelope) == {
        "contract_version",
        "schema_version",
        "payload",
        "payload_sha256",
        "signature_algorithm",
        "signature_b64url",
    }
    assert envelope["contract_version"] == "v2"
    assert type(envelope["schema_version"]) is int and envelope["schema_version"] == 2
    assert envelope["signature_algorithm"] == "ES256-P1363"
    assert len(rfc8785.dumps(cast(JsonValue, envelope))) <= maximum
    assert datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00")) - NOW == ttl
    digest = hashlib.sha256(rfc8785.dumps(cast(JsonValue, payload))).digest()
    assert envelope["payload_sha256"] == digest.hex()
    encoded = str(envelope["signature_b64url"])
    signature = base64.urlsafe_b64decode(encoded + "==")
    assert len(signature) == 64 and len(encoded) == 86
    assert base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii") == encoded
    der = utils.encode_dss_signature(
        int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    )
    key.public_key().verify(der, domain.encode("ascii") + digest, ec.ECDSA(hashes.SHA256()))
    for wrong_domain in (
        CAPABILITIES_DOMAIN if kind == "discovery" else DISCOVERY_DOMAIN,
        "AutPlay discovery v1\n",
        "AutPlay capabilities v1\n",
        domain.replace("\n", "\\n"),
    ):
        with pytest.raises(InvalidSignature):
            key.public_key().verify(
                der, wrong_domain.encode("ascii") + digest, ec.ECDSA(hashes.SHA256())
            )
    # Hex text and Prehashed(payload digest) are distinct protocols.
    with pytest.raises(InvalidSignature):
        key.public_key().verify(
            der, domain.encode("ascii") + digest.hex().encode("ascii"), ec.ECDSA(hashes.SHA256())
        )
    with pytest.raises(InvalidSignature):
        key.public_key().verify(der, digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))


@pytest.mark.parametrize(
    "signer,maximum",
    [
        (signed_discovery, 16384),
        (signed_capabilities, 65536),
    ],
)
def test_byte_limit_covers_the_entire_envelope(
    signer: Callable[[ec.EllipticCurvePrivateKey, dict[str, object]], dict[str, object]],
    maximum: int,
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    payload: dict[str, object] = {"padding": "a" * (maximum - 32)}
    assert len(rfc8785.dumps(cast(JsonValue, payload))) < maximum
    with pytest.raises(DeviceCompatibilityError):
        signer(key, payload)


@pytest.mark.parametrize("epoch", [True, 0, -1, MAX_REVISION + 1])
def test_identity_epoch_is_a_positive_safe_integer(epoch: int) -> None:
    identity = NegotiationIdentity(
        UUID("11111111-1111-4111-8111-111111111111"),
        epoch,
        b"unused",
        b"t" * 32,
        "https://api.test.invalid",
        "https://stream.test.invalid",
    )
    with pytest.raises(DeviceCompatibilityError):
        identity.fields()
