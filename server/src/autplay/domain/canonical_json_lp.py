"""CANONICAL_JSON_LP_V1: typed length-prefixed nested serving evidence.

The codec is deliberately independent of JSONB's text representation. Every
object path needs a declared ASCII-key set before data can be hashed or stored.
"""

from __future__ import annotations

import json
import struct
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

MAX_LP_BYTES: Final = 65_536
MAX_LP_DEPTH: Final = 12
MAX_LP_MEMBERS: Final = 4_096
MAX_LP_STRING_BYTES: Final = 4_096
_I64_MIN: Final = -(2**63)
_I64_MAX: Final = 2**63 - 1
_U32 = struct.Struct(">I")
_I64 = struct.Struct(">q")


class CanonicalJsonLpError(ValueError):
    """A nested document cannot be an item-hash input."""


@dataclass(frozen=True, slots=True)
class CanonicalJsonLpSchema:
    """Allowed object keys at `$`, `.member`, and `[]` array-element paths."""

    object_keys: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        if not self.object_keys:
            raise CanonicalJsonLpError("LP schema must declare object paths")
        copied: dict[str, frozenset[str]] = {}
        for path, keys in self.object_keys.items():
            if not isinstance(path, str) or not path.startswith("$"):
                raise CanonicalJsonLpError("invalid LP schema path")
            if any(not isinstance(key, str) or not key.isascii() or not key for key in keys):
                raise CanonicalJsonLpError("LP schema keys must be ASCII")
            copied[path] = frozenset(keys)
        object.__setattr__(self, "object_keys", MappingProxyType(copied))


def encode_canonical_json_lp(value: object, schema: CanonicalJsonLpSchema) -> bytes:
    """Encode one exact typed document; no float or implicit NFC conversion."""

    def child(value: object, path: str, depth: int) -> bytes:
        if value is None:
            result = b"n"
        elif value is False:
            result = b"f"
        elif value is True:
            result = b"t"
        elif type(value) is int:
            if not _I64_MIN <= value <= _I64_MAX:
                raise CanonicalJsonLpError("LP integer exceeds int64")
            result = b"i" + _I64.pack(value)
        elif isinstance(value, str):
            encoded = _utf8(value)
            result = b"s" + _U32.pack(len(encoded)) + encoded
        elif isinstance(value, list):
            if depth >= MAX_LP_DEPTH or len(value) > MAX_LP_MEMBERS:
                raise CanonicalJsonLpError("LP array bound exceeded")
            members = [_length(child(item, path + "[]", depth + 1)) for item in value]
            result = b"a" + _U32.pack(len(members)) + b"".join(members)
        elif isinstance(value, dict):
            if depth >= MAX_LP_DEPTH or len(value) > MAX_LP_MEMBERS:
                raise CanonicalJsonLpError("LP object bound exceeded")
            allowed = schema.object_keys.get(path)
            if allowed is None or not set(value).issubset(allowed):
                raise CanonicalJsonLpError("LP object has undeclared keys or path")
            members = []
            for key in sorted(value, key=lambda name: name.encode("ascii")):
                key_bytes = _ascii_key(key)
                members.append(
                    _length(key_bytes) + _length(child(value[key], path + "." + key, depth + 1))
                )
            result = b"o" + _U32.pack(len(members)) + b"".join(members)
        else:
            raise CanonicalJsonLpError("LP value has unsupported type")
        if len(result) > MAX_LP_BYTES:
            raise CanonicalJsonLpError("LP document exceeds byte bound")
        return result

    return child(value, "$", 0)


def parse_and_encode_canonical_json_lp(raw: bytes, schema: CanonicalJsonLpSchema) -> bytes:
    """Reject duplicate keys, non-integer numbers, invalid UTF-8 and unknown fields."""

    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_LP_BYTES:
        raise CanonicalJsonLpError("LP JSON input exceeds byte bound")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise CanonicalJsonLpError("LP JSON has duplicate keys")
            value[key] = item
        return value

    def invalid_number(_token: str) -> None:
        raise CanonicalJsonLpError("LP JSON requires int64 numbers")

    def integer(token: str) -> int:
        if len(token) > 20:
            raise CanonicalJsonLpError("LP JSON integer exceeds int64")
        value = int(token)
        if not _I64_MIN <= value <= _I64_MAX:
            raise CanonicalJsonLpError("LP JSON integer exceeds int64")
        return value

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_int=integer,
            parse_float=invalid_number,
            parse_constant=invalid_number,
        )
        return encode_canonical_json_lp(value, schema)
    except (UnicodeDecodeError, json.JSONDecodeError, OverflowError, RecursionError) as error:
        raise CanonicalJsonLpError("invalid LP JSON document") from error


def _utf8(value: str) -> bytes:
    if unicodedata.normalize("NFC", value) != value:
        raise CanonicalJsonLpError("LP string is not NFC")
    try:
        result = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise CanonicalJsonLpError("LP string is not valid UTF-8") from error
    if len(result) > MAX_LP_STRING_BYTES:
        raise CanonicalJsonLpError("LP string exceeds byte bound")
    return result


def _ascii_key(value: str) -> bytes:
    if not value.isascii() or not value or len(value) > MAX_LP_STRING_BYTES:
        raise CanonicalJsonLpError("LP object key is not schema ASCII")
    return value.encode("ascii")


def _length(value: bytes) -> bytes:
    return _U32.pack(len(value)) + value
