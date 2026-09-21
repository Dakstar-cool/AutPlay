"""Server verifies the same real P-256 vectors consumed by Android."""

import json
from pathlib import Path
from typing import Any

import pytest
from autplay.domain.self_device_pairing import SelfPairingError, parse_request, verify_proof

VECTORS = Path(__file__).resolve().parents[2] / "tests/fixtures/self-device-pairing/v1"


@pytest.mark.parametrize("kind", ["claim", "poll", "exchange"])
def test_shared_real_signature_and_wrong_domain(kind: str) -> None:
    fixture: dict[str, Any] = json.loads((VECTORS / "proof-vectors.json").read_text("utf-8"))
    request = fixture["requests"][kind]["request"]
    claim = fixture["requests"]["claim"]["request"]
    parse_request(kind, request)
    verify_proof(kind, request, claim)
    with pytest.raises(SelfPairingError):
        verify_proof("wrong-domain", request, claim)
    changed = {**request, "device_signature_b64url": "A" * 86}
    with pytest.raises(SelfPairingError):
        verify_proof(kind, changed, claim)
