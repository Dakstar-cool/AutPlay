"""Closed RFC8785 original documents, independent of received request roots.

Only scalar tokens are decoded during preflight. Object/array hydration belongs
to the owning application after every byte and closed role has been checked.
"""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, NoReturn, Protocol
from uuid import UUID

from autplay.domain.native_connected_request_v3 import (
    MAX_CONNECTED_RECORD_BYTES,
    MAX_SAFE_INTEGER,
    NativeConnectedRangeV3,
    NativeConnectedV3Error,
)
from autplay.domain.native_original_capture_v3 import MAX_ORIGINAL_BASELINE_V4_BYTES
from autplay.domain.recommendations import SUPPORTED_CONTEXTS
from autplay.domain.score_e8 import score_e8_decimal, score_e8_from_binary64


class OriginalDocumentKindV3(StrEnum):
    REQUEST = "REQUEST"
    BASELINE = "BASELINE"
    RANKING = "RANKING"
    PIPELINE = "PIPELINE"


type OriginalCanonicalizerV3 = Callable[[object], bytes]


class NativeConnectedOriginalMeterV3(Protocol):
    def reserve(self, *, byte_work_bound: int, node_visit_bound: int) -> None: ...
    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None: ...
    def retain_digests(self, amount: int) -> None: ...
    def release_digests(self, amount: int) -> None: ...
    def retain_scratch(self, amount: int) -> None: ...
    def release_scratch(self, amount: int) -> None: ...


def _fail(code: str) -> NoReturn:
    raise NativeConnectedV3Error("CONNECTED_V3_ORIGINAL_" + code)


def _fields(value: str) -> dict[str, str]:
    return dict(pair.split(":", 1) for pair in value.split())


_OBJECTS = {
    "REQUEST": _fields(
        "schema_version:one canonicalization_version:one user_id:uuid surface:surface "
        "context:context limit:limit exploration:unit seed:integer shadow:false "
        "pipeline:request_pipeline snapshot:snapshot wave_session:wave_session"
    ),
    "wave_session": _fields(
        "version:one session_id:uuid device_id:uuid local_date:local_date? local_hour:hour?"
    ),
    "request_pipeline": _fields("key:text version:text manifest_sha256:digest"),
    "snapshot": _fields(
        "snapshot_id:uuid input_snapshot_sha256:digest interaction_watermark:natural "
        "catalog_snapshot:natural availability_snapshot:digest policy_snapshot_sha256:digest"
    ),
    "BASELINE": _fields(
        "schema_version:one user_id:uuid interaction_watermark:natural "
        "selection_policy:selection max_tracks:five_thousand tracks:tracks"
    ),
    "track": _fields(
        "recording_id:uuid user_track_ref_id:uuid? artist_key:text release_key:text? "
        "metadata_tokens:strings availability:text authorized:boolean identity_status:text "
        "preference:text excluded:boolean play_count:natural organic_play_count:natural "
        "recommended_play_count:natural last_played_at_ms:integer? added_at_ms:integer "
        "release_date_ordinal:integer?"
    ),
    "RANKING": _fields("schema_version:one kind:ranking_kind items:items"),
    "item": _fields(
        "recording_id:uuid source_rank:limit score:score reason_code:text reason_codes:strings "
        "contributions:contributions artist_key:text release_key:text? section:text"
    ),
    "contribution": _fields(
        "source_key:source source_version:text source_rank:source_rank raw_score:score "
        "provenance:provenance"
    ),
    "preferences": _fields("preference:like"),
    "history": _fields(
        "organic_play_count:natural recommended_play_count_ignored_for_affinity:natural "
        "forgotten:boolean"
    ),
    "artist_release_metadata": _fields(
        "artist_affinity:boolean metadata_token_overlap:natural release_key:text?"
    ),
    "freshness": _fields(
        "release_date_ordinal:integer? artist_affinity:boolean cold_start_fallback:boolean"
    ),
    "exploration": _fields("seeded:true previously_unplayed:boolean"),
    "PIPELINE": _fields(
        "pipeline_key:text version:text implementation_revision:text request_schema_version:one "
        "canonicalization_version:one components:components generator_budgets:budgets "
        "max_artist_repeat:positive max_release_repeat:positive"
    ),
    "component": _fields("key:text kind:text version:text config_sha256:digest"),
}
_PROVENANCE = ("preferences", "history", "artist_release_metadata", "freshness", "exploration")
_PROVENANCE_FIELDS = {key: rule for role in _PROVENANCE for key, rule in _OBJECTS[role].items()}
_ARRAYS = {
    "tracks": (0, 5000, "track"),
    "items": (0, 100, "item"),
    "contributions": (1, 5, "contribution"),
    "strings": (0, 4096, "text"),
    "components": (9, 9, "component"),
    "budgets": (5, 5, "budget_pair"),
}
_LITERALS: dict[str, object] = {
    "one": 1,
    "five_thousand": 5000,
    "false": False,
    "true": True,
    "like": "LIKE",
    "selection": "most_recent_added_then_recording_id_v1",
    "ranking_kind": "RECOMMENDATION_RANKING_V1",
}

_LAYOUT_SCRATCH_BYTES = 72


@dataclass(frozen=True, slots=True)
class OriginalDocumentLayoutV3:
    kind: OriginalDocumentKindV3
    byte_length: int
    node_visits: int
    rows: NativeConnectedRangeV3 | None
    row_count: int
    digest_payload_bytes: int
    value_text_characters: int
    _meter: NativeConnectedOriginalMeterV3 = field(repr=False, compare=False)
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def close(self) -> None:
        if not self._closed:
            self._meter.release_scratch(_LAYOUT_SCRATCH_BYTES)
            object.__setattr__(self, "_closed", True)


class _Scanner:
    def __init__(
        self,
        raw: bytes,
        meter: NativeConnectedOriginalMeterV3,
        canonicalize: OriginalCanonicalizerV3,
    ) -> None:
        self.raw, self.meter, self.at = raw, meter, 0
        self.canonicalize = canonicalize
        self.nodes = self.digests = self.row_count = self.value_text_characters = 0
        self.rows: NativeConnectedRangeV3 | None = None

    def byte(self, value: int) -> None:
        if self.at >= len(self.raw) or self.raw[self.at] != value:
            _fail("JSON")
        self.at += 1

    def scalar(self) -> Any:
        start = self.at
        if self.at >= len(self.raw):
            _fail("JSON")
        quoted = self.raw[self.at] == 34
        if quoted:
            self.at += 1
            escaped = False
            while self.at < len(self.raw):
                byte = self.raw[self.at]
                self.at += 1
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    break
                if self.at - start > 24_578:
                    _fail("STRING_BOUND")
            else:
                _fail("JSON")
        else:
            while self.at < len(self.raw) and self.raw[self.at] not in b",]}":
                self.at += 1
                if self.at - start > 64:
                    _fail("SCALAR_BOUND")
        token_size = self.at - start
        scratch = 3 * token_size + 8192
        self.meter.retain_scratch(scratch)
        try:
            return self.decode_scalar(start)
        finally:
            self.meter.release_scratch(scratch)

    def decode_scalar(self, start: int) -> Any:
        token_size = self.at - start
        self.meter.charge(bytes_read=token_size, bytes_written=2 * token_size)
        token = self.raw[start : self.at]
        try:
            value = json.loads(token)
        except ValueError, UnicodeError:
            _fail("JSON")
        if type(value) not in (str, bool, int, float, type(None)):
            _fail("SCALAR")
        if type(value) is int and not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            _fail("SAFE53")
        if type(value) is float and not math.isfinite(value):
            _fail("FINITE")
        if type(value) is str:
            if (
                len(value) > 4096
                or unicodedata.normalize("NFC", value) != value
                or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
            ):
                _fail("STRING_BOUND")
            encoded = value.encode("utf-8")
            if len(encoded) > 4096:
                _fail("STRING_BOUND")
            if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
                self.digests += 64
        try:
            canonical = self.canonicalize(value)
        except ValueError, UnicodeError:
            _fail("CANONICAL")
        self.meter.charge(bytes_written=len(canonical))
        if canonical != token:
            _fail("CANONICAL")
        return value

    def value(self, rule: str, depth: int = 0) -> Any:
        if depth > 12:
            _fail("DEPTH")
        self.nodes += 1
        self.meter.charge(node_visits=1)
        if rule in _OBJECTS or rule == "provenance":
            return self.object(rule, depth)
        if rule in _ARRAYS or rule == "budget_pair":
            return self.array(rule, depth)
        value = self.scalar()
        if type(value) is str:
            self.value_text_characters += len(value)
        if rule.endswith("?"):
            if value is None:
                return value
            rule = rule[:-1]
        if rule in _LITERALS:
            expected = _LITERALS[rule]
            if type(value) is not type(expected) or value != expected:
                _fail("LITERAL")
        elif rule in ("integer", "natural", "positive", "limit", "source_rank"):
            lower = 0 if rule == "natural" else 1 if rule != "integer" else -MAX_SAFE_INTEGER
            upper = 100 if rule == "limit" else 1000 if rule == "source_rank" else MAX_SAFE_INTEGER
            if type(value) is not int or not lower <= value <= upper:
                _fail("INTEGER")
        elif rule == "hour":
            if type(value) is not int or not 0 <= value <= 23:
                _fail("HOUR")
        elif rule == "local_date":
            if type(value) is not str or len(value) != 10:
                _fail("LOCAL_DATE")
            try:
                if date.fromisoformat(value).isoformat() != value:
                    _fail("LOCAL_DATE")
            except ValueError:
                _fail("LOCAL_DATE")
        elif rule == "boolean":
            if type(value) is not bool:
                _fail("BOOLEAN")
        elif rule in ("text", "uuid", "digest", "surface", "context", "source"):
            if type(value) is not str:
                _fail("TEXT")
            if rule == "uuid":
                try:
                    if str(UUID(value)) != value:
                        _fail("UUID")
                except ValueError:
                    _fail("UUID")
            if rule == "digest" and (
                len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            ):
                _fail("DIGEST")
            if rule == "surface" and value not in ("recommendations", "home", "offline_pack"):
                _fail("SURFACE")
            if rule == "context" and value not in SUPPORTED_CONTEXTS:
                _fail("CONTEXT")
            if rule == "source" and value not in _PROVENANCE:
                _fail("SOURCE")
        elif rule in ("unit", "score"):
            if type(value) not in (float, int):
                _fail("NUMBER")
            number = float(value)
            if not math.isfinite(number) or (rule == "unit" and not 0 <= number <= 1):
                _fail("NUMBER")
            try:
                units = score_e8_from_binary64(number)
            except ValueError:
                _fail("SCORE")
            if Decimal(str(number)) != score_e8_decimal(units):
                _fail("SCORE_PRECISION")
        else:
            _fail("ROLE")
        return value

    def object(self, role: str, depth: int) -> str | None:
        fields = _PROVENANCE_FIELDS if role == "provenance" else _OBJECTS[role]
        self.byte(123)
        seen: set[str] = set()
        previous = ""
        source: str | None = None
        provenance: str | None = None
        while self.at < len(self.raw) and self.raw[self.at] != 125:
            if seen:
                self.byte(44)
            key = self.scalar()
            if type(key) is not str or key not in fields or key <= previous:
                _fail("KEYS")
            previous = key
            seen.add(key)
            self.byte(58)
            value = self.value(fields[key], depth + 1)
            if role == "contribution" and key == "source_key":
                source = value
            elif role == "contribution" and key == "provenance":
                provenance = value
        self.byte(125)
        if role == "provenance":
            for variant in _PROVENANCE:
                if seen == _OBJECTS[variant].keys():
                    return variant
            _fail("PROVENANCE_KEYS")
        required = fields.keys() - {"wave_session"} if role == "REQUEST" else fields.keys()
        if not required <= seen:
            _fail("KEYS")
        if role == "contribution" and source != provenance:
            _fail("PROVENANCE_SOURCE")
        return None

    def array(self, role: str, depth: int) -> None:
        start = self.at
        self.byte(91)
        lower, upper, child = (2, 2, "text") if role == "budget_pair" else _ARRAYS[role]
        count = 0
        while self.at < len(self.raw) and self.raw[self.at] != 93:
            if count:
                self.byte(44)
            if count >= upper:
                _fail("COUNT")
            leaf_start = self.at
            self.value("positive" if role == "budget_pair" and count == 1 else child, depth + 1)
            if role in ("tracks", "items") and self.at - leaf_start > 65_536:
                _fail("LEAF_BOUND")
            count += 1
        self.byte(93)
        if count < lower:
            _fail("COUNT")
        if role in ("tracks", "items"):
            self.rows = NativeConnectedRangeV3(start, self.at)
            self.row_count = count


def preflight_native_connected_original_v3(
    raw: bytes,
    *,
    kind: OriginalDocumentKindV3,
    meter: NativeConnectedOriginalMeterV3,
    canonicalize: OriginalCanonicalizerV3,
) -> OriginalDocumentLayoutV3:
    """Check closed canonical bytes without ever hydrating a container."""
    if type(kind) is not OriginalDocumentKindV3:
        _fail("KIND")
    maximum = (
        MAX_ORIGINAL_BASELINE_V4_BYTES if kind is OriginalDocumentKindV3.BASELINE
        else MAX_CONNECTED_RECORD_BYTES
    )
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        _fail("LENGTH")
    if (
        kind in (OriginalDocumentKindV3.REQUEST, OriginalDocumentKindV3.PIPELINE)
        and len(raw) > 65_536
    ):
        _fail("LEAF_BOUND")
    meter.reserve(byte_work_bound=12 * len(raw) + 4096, node_visit_bound=4 * len(raw) + 4096)
    meter.retain_scratch(8192)
    try:
        meter.charge(bytes_read=len(raw))
        scanner = _Scanner(raw, meter, canonicalize)
        scanner.value(kind.value)
        if scanner.at != len(raw):
            _fail("TRAILING")
        # Test the complete future digest ownership before the first container
        # hydration, including hexadecimal strings in otherwise textual roles.
        meter.retain_digests(scanner.digests)
        meter.release_digests(scanner.digests)
        meter.retain_scratch(_LAYOUT_SCRATCH_BYTES)
        return OriginalDocumentLayoutV3(
            kind,
            len(raw),
            scanner.nodes,
            scanner.rows,
            scanner.row_count,
            scanner.digests,
            scanner.value_text_characters,
            meter,
        )
    finally:
        meter.release_scratch(8192)


def iter_native_connected_original_rows_v3(
    raw: bytes,
    layout: OriginalDocumentLayoutV3,
    *,
    meter: NativeConnectedOriginalMeterV3,
    canonicalize: OriginalCanonicalizerV3,
) -> Iterator[NativeConnectedRangeV3]:
    """Yield one checked original leaf at a time; never build a T-entry index."""
    if (
        layout._closed
        or layout._meter is not meter
        or layout.rows is None
        or type(raw) is not bytes
        or len(raw) != layout.byte_length
        or layout.kind not in (OriginalDocumentKindV3.BASELINE, OriginalDocumentKindV3.RANKING)
    ):
        _fail("LAYOUT")
    meter.reserve(byte_work_bound=12 * len(raw) + 4096, node_visit_bound=4 * len(raw) + 4096)
    meter.retain_scratch(8192)
    try:
        scanner = _Scanner(raw, meter, canonicalize)
        scanner.at = layout.rows.start + 1
        for index in range(layout.row_count):
            if index:
                scanner.byte(44)
            start = scanner.at
            scanner.value("track" if layout.kind is OriginalDocumentKindV3.BASELINE else "item")
            yield NativeConnectedRangeV3(start, scanner.at)
        if scanner.at != layout.rows.stop - 1:
            _fail("ROW_COVERAGE")
    finally:
        meter.release_scratch(8192)
