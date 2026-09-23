"""Golden LP bytes and hostile JSON pre-SQL boundaries for serving v2."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from autplay.domain.canonical_json_lp import (
    CanonicalJsonLpError,
    CanonicalJsonLpSchema,
    encode_canonical_json_lp,
    parse_and_encode_canonical_json_lp,
)

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/ml/canonical-json-lp-v1.json"


def _golden() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def _schema() -> CanonicalJsonLpSchema:
    return CanonicalJsonLpSchema({"$": frozenset({"a", "b"})})


def test_checked_in_typed_lp_vectors_are_exact() -> None:
    fixture = _golden()
    assert fixture["kind"] == "CANONICAL_JSON_LP_V1_GOLDEN"
    schema = CanonicalJsonLpSchema(
        {path: frozenset(keys) for path, keys in fixture["object_keys"].items()}
    )
    for case in fixture["cases"]:
        encoded = encode_canonical_json_lp(case["value"], schema)
        assert encoded.hex() == case["hex"], case["name"]
        if "sha256" in case:
            assert sha256(encoded).hexdigest() == case["sha256"]
    assert encode_canonical_json_lp({"a": [True, None], "b": "é"}, schema) == bytes.fromhex(
        fixture["cases"][-1]["hex"]
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":1,"\\u0061":2}',
        b'{"unknown":1}',
        b'{"a":1.0}',
        b'{"a":1e0}',
        b'{"a":NaN}',
        b'{"a":9223372036854775808}',
        b'{"a":-9223372036854775809}',
        b'{"a":"e\\u0301"}',
        b'{"a":{"nested":1}}',
        b'{"\xff":1}',
    ],
)
def test_hostile_json_is_rejected_before_storage(raw: bytes) -> None:
    with pytest.raises(CanonicalJsonLpError):
        parse_and_encode_canonical_json_lp(raw, _schema())


def test_nested_object_requires_declared_path_and_ascii_keys() -> None:
    with pytest.raises(CanonicalJsonLpError):
        encode_canonical_json_lp({"a": {"x": 1}}, _schema())
    schema = CanonicalJsonLpSchema({"$": frozenset({"a"}), "$.a": frozenset({"x"})})
    assert encode_canonical_json_lp({"a": {"x": 1}}, schema).startswith(b"o\x00\x00\x00\x01")
    with pytest.raises(CanonicalJsonLpError):
        CanonicalJsonLpSchema({"$": frozenset({"é"})})


def test_depth_string_and_total_bounds_reject_without_truncation() -> None:
    with pytest.raises(CanonicalJsonLpError):
        encode_canonical_json_lp(2**63, _schema())
    with pytest.raises(CanonicalJsonLpError):
        encode_canonical_json_lp("x" * 4097, _schema())
    with pytest.raises(CanonicalJsonLpError):
        encode_canonical_json_lp(["x" * 4096] * 17, _schema())
    nested: object = None
    for _ in range(13):
        nested = [nested]
    with pytest.raises(CanonicalJsonLpError):
        encode_canonical_json_lp(nested, _schema())


def test_boolean_and_integer_tags_cannot_collide() -> None:
    assert encode_canonical_json_lp(True, _schema()) != encode_canonical_json_lp(1, _schema())
    assert encode_canonical_json_lp(False, _schema()) != encode_canonical_json_lp(0, _schema())
