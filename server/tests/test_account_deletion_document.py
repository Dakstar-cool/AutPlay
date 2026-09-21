"""Shared deletion proofs are valid only for their exact purpose."""

import base64
import json
from pathlib import Path

import pytest
from autplay.domain.account_deletion import AccountDeletionError, parse_request, verify_device

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("kind", ["request", "preview", "cancel"])
def test_shared_deletion_proofs_and_wrong_purpose(kind: str) -> None:
    vectors = json.loads(
        (ROOT / "tests/fixtures/account-deletion/v1/proof-vectors.json").read_text()
    )
    request = parse_request(kind, vectors["requests"][kind])
    assert verify_device(kind, request) == base64.b64decode(vectors["public_key_spki_b64"])
    with pytest.raises(AccountDeletionError):
        verify_device("cancel" if kind != "cancel" else "request", request)
    with pytest.raises(AccountDeletionError):
        parse_request(kind, {**request, "extra": "rejected"})
