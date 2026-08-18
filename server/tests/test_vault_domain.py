"""Unit tests for pure Vault values and stable error conditions."""

from __future__ import annotations

import pytest
from autplay.domain.vault import ByteRange, InvalidStorageKeyError, OpaqueStorageKey, Sha256Digest


def test_opaque_storage_key_rejects_path_and_empty_values() -> None:
    with pytest.raises(InvalidStorageKeyError):
        OpaqueStorageKey("../private")
    with pytest.raises(InvalidStorageKeyError):
        OpaqueStorageKey("")


def test_sha_and_range_are_strictly_bounded_values() -> None:
    digest = Sha256Digest(b"x" * 32)
    assert digest.hex == "78" * 32
    assert ByteRange(2, 4).length == 3
    with pytest.raises(ValueError):
        Sha256Digest(b"x")
    with pytest.raises(ValueError):
        ByteRange(3, 2)
