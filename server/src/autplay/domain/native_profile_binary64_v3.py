"""Streaming independent identity for an already fact-validated numeric R1 profile.

This hash is not an authority or a validator of provenance. For AdaptiveProfile,
the caller supplies the old numeric R1 SHA from its unchanged canonical serializer.
For a scoped numeric dictionary, its existing self-SHA must match that supplied
SHA. Neither branch recomputes old RFC8785 bytes or runs the R1 projector.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from typing import Any, Final, Protocol, cast
from uuid import UUID

from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    AdaptiveDimensionState,
    AdaptiveProfile,
    DimensionKind,
    Horizon,
    HorizonValue,
    MaturityState,
    TemporalDimension,
)

NATIVE_PROFILE_BINARY64_V3_DOMAIN: Final = "autplay.original-r1-profile-binary64.v3"
_DOMAIN: Final = b"autplay.original-r1-profile-binary64.v3"
NATIVE_PROFILE_BINARY64_V3_MATURITY_FIELDS: Final = (
    ("score", "unit"),
    ("effective_signal_mass", "nonnegative"),
    ("recency", "unit"),
    ("consistency", "unit"),
    ("organic_share", "unit"),
    ("plasticity_multiplier", "plasticity"),
)
_HORIZONS: Final = (Horizon.EPISODE, Horizon.H12, Horizon.H24, Horizon.D3, Horizon.D7)
_KINDS: Final = frozenset(item.value for item in DimensionKind)
_POLICY_KEYS: Final = frozenset(("key", "version", "content_sha256"))
_RECENT_KEYS: Final = frozenset(h.value for h in _HORIZONS)
_DIMENSION_ACCESS: Final = (
    *(
        ("long_term", 0, name, rule)
        for name, rule in (
            ("score", "signed"),
            ("confidence", "unit"),
            ("evidence_mass", "nonnegative"),
        )
    ),
    *(
        ("recent", index, name, rule)
        for index in range(5)
        for name, rule in (
            ("score", "signed"),
            ("confidence", "unit"),
            ("evidence_mass", "nonnegative"),
        )
    ),
    ("row", 0, "persistence", "unit"),
    ("row", 0, "momentum", "signed"),
    ("row", 0, "fatigue", "unit"),
    ("row", 0, "recent_adjustment", "signed"),
)
NATIVE_PROFILE_BINARY64_V3_DIMENSION_FIELDS: Final = (
    *(
        (prefix + "." + name, rule)
        for prefix in ("long_term", *("recent." + h.value for h in _HORIZONS))
        for name, rule in (
            ("score", "signed"),
            ("confidence", "unit"),
            ("evidence_mass", "nonnegative"),
        )
    ),
    ("persistence", "unit"),
    ("momentum", "signed"),
    ("fatigue", "unit"),
    ("recent_adjustment", "signed"),
)
_PROFILE_KEYS: Final = frozenset(
    (
        "schema_version",
        "owner_user_id",
        "cutoff_at_ms",
        "interaction_watermark",
        "feature_policy",
        "maturity",
        "dimensions",
        "profile_sha256",
    )
)
_MATURITY_KEYS: Final = frozenset(
    (
        *(name for name, _ in NATIVE_PROFILE_BINARY64_V3_MATURITY_FIELDS),
        "track_coverage",
        "artist_coverage",
        "observation_span_ms",
        "recency_age_ms",
    )
)
_DIMENSION_KEYS: Final = frozenset(
    (
        "kind",
        "key",
        "long_term",
        "recent",
        "persistence",
        "momentum",
        "fatigue",
        "recent_adjustment",
        "source_event_ids",
    )
)
_HORIZON_KEYS: Final = frozenset(("score", "confidence", "evidence_mass"))
_MAX_I64: Final = (1 << 63) - 1
_MAX_SAFE53: Final = (1 << 53) - 1
_TEMP_DIGEST_BYTES: Final = 192


class NativeProfileBinary64V3Error(ValueError):
    """A safe stable refusal code without profile content."""


class NativeProfileBinary64V3Meter(Protocol):
    """Structural interface to the caller's same admitted G work/digest lease."""

    def reserve(self, *, byte_work_bound: int, node_visit_bound: int) -> None: ...

    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None: ...

    def retain_digests(self, n: int) -> None: ...

    def release_digests(self, n: int) -> None: ...


def _fail(code: str) -> None:
    raise NativeProfileBinary64V3Error(code)


def _nodes(meter: NativeProfileBinary64V3Meter, count: int = 1) -> None:
    meter.reserve(byte_work_bound=0, node_visit_bound=count)
    meter.charge(node_visits=count)


def _closed(value: Any, keys: frozenset[str], meter: NativeProfileBinary64V3Meter) -> None:
    _nodes(meter, 1 + len(keys))
    if type(value) is not dict or len(value) != len(keys) or any(k not in value for k in keys):
        _fail("PROFILE_BINARY64_KEYS")


def _member(value: Any, key: str, meter: NativeProfileBinary64V3Meter) -> Any:
    _nodes(meter)
    return value[key] if type(value) is dict else getattr(value, key)


def _integer(value: Any, meter: NativeProfileBinary64V3Meter) -> int:
    _nodes(meter)
    if type(value) is not int or not 0 <= value <= _MAX_I64:
        _fail("PROFILE_BINARY64_INTEGER")
    return cast(int, value)


def _raw32(value: object, meter: NativeProfileBinary64V3Meter) -> bytes:
    meter.reserve(byte_work_bound=32, node_visit_bound=1)
    meter.charge(bytes_read=32, node_visits=1)
    if type(value) is not bytes or len(value) != 32:
        _fail("PROFILE_BINARY64_DIGEST")
    return cast(bytes, value)


def _hex32(value: Any, meter: NativeProfileBinary64V3Meter) -> bytes:
    # Lowercase validation and decoding each inspect the 64 input characters.
    meter.reserve(byte_work_bound=160, node_visit_bound=129)
    meter.charge(bytes_read=128, bytes_written=32, node_visits=129)
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        _fail("PROFILE_BINARY64_DIGEST")
    return bytes.fromhex(value)


def _owner(value: Any, typed: bool, meter: NativeProfileBinary64V3Meter) -> UUID:
    if typed:
        _nodes(meter)
        if type(value) is not UUID:
            _fail("PROFILE_BINARY64_OWNER")
        return cast(UUID, value)
    # UUID's bounded parser/normalizer creates short stripped/hex/text copies.
    meter.reserve(byte_work_bound=512, node_visit_bound=128)
    meter.charge(bytes_read=256, bytes_written=256, node_visits=128)
    if type(value) is not str or len(value) != 36:
        _fail("PROFILE_BINARY64_OWNER")
    try:
        result = UUID(value)
    except ValueError, AttributeError:
        raise NativeProfileBinary64V3Error("PROFILE_BINARY64_OWNER") from None
    if str(result) != value:
        _fail("PROFILE_BINARY64_OWNER")
    return result


def _utf8(value: Any, meter: NativeProfileBinary64V3Meter) -> bytes:
    _nodes(meter)
    if type(value) is not str or not 1 <= len(value) <= 200:
        _fail("PROFILE_BINARY64_STRING")
    # Admit each bounded string's scan and worst-case UTF8 allocation before
    # encoding. The conservative read bound includes sort comparisons as well.
    upper = 4 * len(value)
    meter.reserve(byte_work_bound=4 * upper, node_visit_bound=3 * len(value))
    meter.charge(bytes_read=3 * upper, bytes_written=upper, node_visits=3 * len(value))
    try:
        return cast(str, value).encode("utf-8")
    except UnicodeError:
        raise NativeProfileBinary64V3Error("PROFILE_BINARY64_STRING") from None


def _number(value: Any, rule: str, meter: NativeProfileBinary64V3Meter) -> bytes:
    meter.reserve(byte_work_bound=16, node_visit_bound=4)
    meter.charge(bytes_read=8, bytes_written=8, node_visits=4)
    if type(value) not in (float, int) or (type(value) is int and abs(value) > _MAX_SAFE53):
        _fail("PROFILE_BINARY64_NUMBER")
    number = float(value)
    lower = -1.0 if rule == "signed" else 0.5 if rule == "plasticity" else 0.0
    upper = 2.0 if rule == "plasticity" else math.inf if rule == "nonnegative" else 1.0
    if not math.isfinite(number) or not lower <= number <= upper:
        _fail("PROFILE_BINARY64_NUMBER")
    return struct.pack(">d", number)


class _Stream:
    def __init__(self, fields: int, meter: NativeProfileBinary64V3Meter) -> None:
        self.meter, self.fields, self.seen = meter, fields, 0
        size = len(_DOMAIN) + 5
        meter.reserve(byte_work_bound=2 * size + 4, node_visit_bound=3)
        meter.charge(bytes_read=size, bytes_written=4, sha_input_bytes=size, node_visits=3)
        self.hash = sha256()
        self.hash.update(_DOMAIN)
        self.hash.update(b"\0")
        self.hash.update(struct.pack(">I", fields))

    def field(self, raw: bytes) -> None:
        self.meter.reserve(byte_work_bound=10 + 2 * len(raw), node_visit_bound=2)
        self.meter.charge(
            bytes_read=len(raw),
            bytes_written=5,
            sha_input_bytes=5 + len(raw),
            node_visits=2,
        )
        self.hash.update(struct.pack(">BI", 1, len(raw)))
        self.hash.update(raw)
        self.seen += 1

    def integer(self, value: Any) -> None:
        number = _integer(value, self.meter)
        self.meter.reserve(byte_work_bound=8, node_visit_bound=0)
        self.meter.charge(bytes_written=8)
        self.field(struct.pack(">q", number))

    def finish(self) -> bytes:
        if self.seen != self.fields:
            _fail("PROFILE_BINARY64_FIELDS")
        self.meter.reserve(byte_work_bound=32, node_visit_bound=1)
        self.meter.charge(bytes_written=32, node_visits=1)
        return self.hash.digest()


def _references(value: Any, typed: bool, meter: NativeProfileBinary64V3Meter) -> None:
    _nodes(meter)
    if type(value) is not (tuple if typed else list) or len(value) > 64:
        _fail("PROFILE_BINARY64_REFERENCES")
    for item in value:
        _owner(item, typed, meter)


def _horizon(value: Any, typed: bool, meter: NativeProfileBinary64V3Meter) -> None:
    if typed:
        _nodes(meter)
        if type(value) is not HorizonValue:
            _fail("PROFILE_BINARY64_HORIZON")
    else:
        _closed(value, _HORIZON_KEYS, meter)


def _digest(
    profile: AdaptiveProfile | dict[str, Any],
    source: bytes,
    old: bytes,
    meter: NativeProfileBinary64V3Meter,
) -> bytes:
    source, old = _raw32(source, meter), _raw32(old, meter)
    _nodes(meter)
    typed = type(profile) is AdaptiveProfile
    if typed:
        value = cast(AdaptiveProfile, profile)
        policy_key, policy_version, policy_sha = (
            value.feature_policy_key,
            value.feature_policy_version,
            value.feature_policy_sha256,
        )
        dimensions, maturity = value.dimensions, value.maturity
        _nodes(meter, 7)
        if type(maturity) is not MaturityState or type(dimensions) is not tuple:
            _fail("PROFILE_BINARY64_SHAPE")
    else:
        _closed(profile, _PROFILE_KEYS, meter)
        document = cast(dict[str, Any], profile)
        if type(document["schema_version"]) is not int or document["schema_version"] != 1:
            _fail("PROFILE_BINARY64_IDENTITY")
        _closed(document["feature_policy"], _POLICY_KEYS, meter)
        policy_key, policy_version, policy_sha = (
            document["feature_policy"][key] for key in ("key", "version", "content_sha256")
        )
        dimensions, maturity = document["dimensions"], document["maturity"]
        _closed(maturity, _MATURITY_KEYS, meter)
        if type(dimensions) is not list:
            _fail("PROFILE_BINARY64_IDENTITY")
        old_decoded = _hex32(document["profile_sha256"], meter)
        meter.reserve(byte_work_bound=64, node_visit_bound=1)
        meter.charge(bytes_read=64, node_visits=1)
        if old_decoded != old:
            _fail("PROFILE_BINARY64_IDENTITY")
        del old_decoded
    if len(dimensions) > 512:
        _fail("PROFILE_BINARY64_COUNT")
    policy = DEFAULT_ADAPTIVE_FEATURE_POLICY
    policy_size = len(policy.key) + len(policy.version) + 64
    meter.reserve(byte_work_bound=2 * policy_size, node_visit_bound=3 + policy_size)
    meter.charge(bytes_read=2 * policy_size, node_visits=3 + policy_size)
    if (
        type(policy_key) is not str
        or policy_key != policy.key
        or type(policy_version) is not str
        or policy_version != policy.version
        or type(policy_sha) is not str
        or policy_sha != policy.content_sha256
    ):
        _fail("PROFILE_BINARY64_POLICY")
    owner = _owner(_member(profile, "owner_user_id", meter), typed, meter)
    cutoff = _integer(_member(profile, "cutoff_at_ms", meter), meter)
    watermark = _integer(_member(profile, "interaction_watermark", meter), meter)
    for key in ("track_coverage", "artist_coverage", "observation_span_ms", "recency_age_ms"):
        _integer(_member(maturity, key, meter), meter)
    stream = _Stream(13 + 24 * len(dimensions), meter)
    meter.reserve(byte_work_bound=16, node_visit_bound=0)
    meter.charge(bytes_written=16)
    stream.field(owner.bytes)
    stream.integer(cutoff)
    stream.integer(watermark)
    stream.field(_hex32(policy_sha, meter))
    stream.field(source)
    stream.field(old)
    stream.integer(len(dimensions))
    for name, rule in NATIVE_PROFILE_BINARY64_V3_MATURITY_FIELDS:
        stream.field(_number(_member(maturity, name, meter), rule, meter))
    previous: tuple[str, str] | None = None
    for row in dimensions:
        _nodes(meter)
        if typed:
            if (
                type(row) is not AdaptiveDimensionState
                or type(row.dimension) is not TemporalDimension
            ):
                _fail("PROFILE_BINARY64_DIMENSION")
            if type(row.dimension.kind) is not DimensionKind:
                _fail("PROFILE_BINARY64_DIMENSION")
            kind, key = row.dimension.kind.value, row.dimension.key
        else:
            _closed(row, _DIMENSION_KEYS, meter)
            dict_row = cast(dict[str, Any], row)
            kind, key = dict_row["kind"], dict_row["key"]
            if type(kind) is not str or kind not in _KINDS:
                _fail("PROFILE_BINARY64_DIMENSION")
        kind_raw, key_raw = _utf8(kind, meter), _utf8(key, meter)
        current = (kind, key)
        if previous is not None and current <= previous:
            _fail("PROFILE_BINARY64_ORDER")
        previous = current
        stream.field(kind_raw)
        stream.field(key_raw)
        _references(_member(row, "source_event_ids", meter), typed, meter)
        long_term = _member(row, "long_term", meter)
        _horizon(long_term, typed, meter)
        recent = _member(row, "recent", meter)
        if typed:
            _nodes(meter, 1 + len(_HORIZONS))
            if type(recent) is not tuple or len(recent) != 5:
                _fail("PROFILE_BINARY64_HORIZON")
            for index, expected in enumerate(_HORIZONS):
                pair = recent[index]
                if type(pair) is not tuple or len(pair) != 2 or pair[0] is not expected:
                    _fail("PROFILE_BINARY64_HORIZON")
                _horizon(pair[1], True, meter)
        else:
            _closed(recent, _RECENT_KEYS, meter)
            for expected in _HORIZONS:
                _horizon(recent[expected.value], False, meter)
        for area, index, name, rule in _DIMENSION_ACCESS:
            target: Any = row
            if area == "long_term":
                target = long_term
            elif area == "recent":
                target = recent[index][1] if typed else recent[_HORIZONS[index].value]
            stream.field(_number(_member(target, name, meter), rule, meter))
    return stream.finish()


@contextmanager
def native_profile_binary64_digest_v3(
    profile: AdaptiveProfile | dict[str, Any],
    *,
    source_membership_sha256: bytes,
    original_profile_sha256: bytes,
    meter: NativeProfileBinary64V3Meter,
) -> Iterator[bytes]:
    """Borrow a 32-byte S digest only until this scope exits.

    The numeric dictionary must come from a scoped independently fact-validated
    source projection. A typed profile requires the independently computed old
    numeric SHA. Input digests/trees are borrowed from the caller's ownership.
    Retain the caller's own 64-byte ownership BEFORE hex conversion into its
    carrier, and do not keep the yielded raw-byte alias beyond this scope.
    """
    # SHA state/finalization copies and at most two decoded digest temporaries
    # fit this fixed admission; output32 transfers from it without double count.
    meter.retain_digests(_TEMP_DIGEST_BYTES)
    result: bytes | None = None
    error: BaseException | None = None
    try:
        result = _digest(profile, source_membership_sha256, original_profile_sha256, meter)
    except BaseException as caught:
        caught.__traceback__ = None
        caught.__context__ = None
        caught.__cause__ = None
        error = caught
    if error is not None:
        meter.release_digests(_TEMP_DIGEST_BYTES)
        raise error
    meter.release_digests(_TEMP_DIGEST_BYTES - 32)
    try:
        yield cast(bytes, result)
    finally:
        result = None
        meter.release_digests(32)
