"""The expanded catalogue codec enforces canonical byte and stream boundaries."""

import base64
import zlib
from hashlib import sha256

import pytest
import rfc8785

from autplay.application import sona_native_catalogue as catalogue
from autplay.domain.recommendations import JsonValue


def test_extended_catalogue_roundtrip_limits_and_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    original: dict[str, JsonValue] = {"tracks": [], "schema": "fixture"}
    raw = rfc8785.dumps(original)
    packed, digest = catalogue.pack_extended_catalogue(original)
    assert digest == sha256(raw).hexdigest()
    assert catalogue.unpack_extended_catalogue(packed, digest) == original
    monkeypatch.setattr(catalogue, "MAX_CATALOGUE_BYTES", len(raw))
    assert catalogue.pack_extended_catalogue(original) == (packed, digest)
    for damaged in (
        {**packed, "canonical_bytes": len(raw) - 1},
        {**packed, "canonical_bytes": len(raw) + 1},
        {**packed, "data": base64.b64encode(zlib.compress(raw) + b"extra").decode()},
        {**packed, "data": base64.b64encode(zlib.compress(raw)[:-1]).decode()},
        {**packed, "data": "invalid"},
    ):
        with pytest.raises(ValueError):
            catalogue.unpack_extended_catalogue(damaged, digest)
    with pytest.raises(ValueError):
        catalogue.unpack_extended_catalogue(packed, "0" * 64)
    monkeypatch.setattr(catalogue, "MAX_CATALOGUE_BYTES", len(raw) - 1)
    with pytest.raises(catalogue.CatalogueSizeExceeded):
        catalogue.pack_extended_catalogue(original)
