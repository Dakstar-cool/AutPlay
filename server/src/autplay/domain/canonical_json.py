"""Typed bridge to the canonical serializer; serialization still validates values."""

from collections.abc import Mapping, Sequence
from typing import cast

import rfc8785

type JsonValue = bool | int | float | str | Sequence[JsonValue] | Mapping[str, JsonValue] | None


def canonical_json_bytes(value: object) -> bytes:
    """Serialize without coercion; unsupported objects retain the serializer's errors."""
    return rfc8785.dumps(cast(JsonValue, value))
