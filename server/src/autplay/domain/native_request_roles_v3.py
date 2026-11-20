"""Closed, deeply immutable roles for native request identity V3.

These values bind structure only. In particular, neither an origin tag nor a
validated role establishes consent, persistence or serving authority.
"""

from __future__ import annotations

import struct
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

from autplay.domain.canonical_json_lp import CanonicalJsonLpSchema, encode_canonical_json_lp
from autplay.domain.native_request_budget_v3 import NativeRequestV3Error
from autplay.domain.native_request_meter_v3 import NativeRequestV3MeterProtocol
from autplay.domain.native_served_authority import encode_serving_lp, integer_bytes
from autplay.domain.recommendations import SUPPORTED_CONTEXTS

type FrozenValueV3 = bool | int | str | tuple[FrozenValueV3, ...] | FrozenObjectV3 | None
type SetRoleV3 = Literal["snapshot-track", "policy-track", "availability-track", "candidate"]


@dataclass(frozen=True, slots=True)
class FrozenObjectV3:
    members: tuple[tuple[str, FrozenValueV3], ...]

    def __post_init__(self) -> None:
        if type(self.members) is not tuple or len(self.members) > 4096:
            raise NativeRequestV3Error("V3_FROZEN_OBJECT")
        previous = ""
        for pair in self.members:
            if type(pair) is not tuple or len(pair) != 2:
                raise NativeRequestV3Error("V3_FROZEN_OBJECT")
            key = pair[0]
            if type(key) is not str or not key or not key.isascii() or key <= previous:
                raise NativeRequestV3Error("V3_FROZEN_KEYS")
            previous = key
        bound_frozen_v3(self)

    def to_dict(self) -> dict[str, Any]:
        """Return fresh mutable copies, never borrowed internal containers."""
        return {key: thaw_v3(value) for key, value in self.members}


def bound_frozen_v3(value: FrozenValueV3) -> int:
    """Bound depth and exact wire size before mutable expansion is possible."""
    total = 0

    def add(size: int) -> None:
        nonlocal total
        total += size
        if total > 2_097_152:
            raise NativeRequestV3Error("V3_RECORD_BYTES")

    def text(value: str) -> None:
        if len(value) > 4096 or unicodedata.normalize("NFC", value) != value:
            raise NativeRequestV3Error("V3_ROLE_TEXT")
        size, utf8 = 2, 0
        for char in value:
            code = ord(char)
            if 0xD800 <= code <= 0xDFFF:
                raise NativeRequestV3Error("V3_ROLE_TEXT")
            width = 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
            utf8 += width
            size += 2 if code in (8, 9, 10, 12, 13, 34, 92) else 6 if code < 32 else width
        if utf8 > 4096:
            raise NativeRequestV3Error("V3_ROLE_TEXT")
        add(size)

    def visit(value: FrozenValueV3, depth: int) -> None:
        if depth > 12:
            raise NativeRequestV3Error("V3_DEPTH")
        if value is None:
            add(4)
        elif type(value) is bool:
            add(4 if value else 5)
        elif type(value) is int:
            integer_bytes(value)
            add(len(str(value)))
        elif type(value) is str:
            text(value)
        elif type(value) is tuple:
            if len(value) > 5000:
                raise NativeRequestV3Error("V3_ARRAY_COUNT")
            add(2 + max(0, len(value) - 1))
            for child in value:
                visit(child, depth + 1)
        elif type(value) is FrozenObjectV3:
            add(2 + max(0, len(value.members) - 1))
            for key, child in value.members:
                text(key)
                add(1)
                visit(child, depth + 1)
        else:
            raise NativeRequestV3Error("V3_MUTABLE_VALUE")

    visit(value, 0)
    return total


def freeze_v3(value: object, depth: int = 0) -> FrozenValueV3:
    """Seal already-owned values; raw wire must pass preflight before this call."""
    if depth > 12:
        raise NativeRequestV3Error("V3_DEPTH")
    if value is None or type(value) in (bool, int, str):
        return value  # type: ignore[return-value]
    if type(value) is dict:
        if len(value) > 4096 or any(type(key) is not str or not key.isascii() for key in value):
            raise NativeRequestV3Error("V3_OBJECT_KEYS")
        return FrozenObjectV3(
            tuple((key, freeze_v3(value[key], depth + 1)) for key in sorted(value))
        )
    if isinstance(value, (list, tuple)) and type(value) in (list, tuple):
        if len(value) > 5000:
            raise NativeRequestV3Error("V3_ARRAY_COUNT")
        return tuple(freeze_v3(child, depth + 1) for child in value)
    raise NativeRequestV3Error("V3_VALUE_TYPE")


def frozen_object_v3(value: object) -> FrozenObjectV3:
    result = freeze_v3(value)
    if type(result) is not FrozenObjectV3:
        raise NativeRequestV3Error("V3_OBJECT_REQUIRED")
    return result


def thaw_v3(value: FrozenValueV3) -> Any:
    if type(value) is FrozenObjectV3:
        return value.to_dict()
    if type(value) is tuple:
        return [thaw_v3(child) for child in value]
    return value


_FIELDS = {
    "identity": "owner_user_id:U device_id:U journal_epoch:U server_instance_id:U identity_epoch:P",
    "metadata": "surface:S context:S seed:I effective_limit:L endpoint_max:I cutoff_at_ms:Z "
    "pipeline_key:S pipeline_version:S pipeline_manifest_sha256:D model_bundle_version:S "
    "candidate_policy_version:S filter_policy_version:S reranker_version:S "
    "interaction_watermark:Z catalog_snapshot:Z availability_snapshot_ref:D",
    "query": "schema_version:I canonicalization_version:I user_id:U surface:S context:S seed:I "
    "shadow:B limit:L exploration_units_e8:E",
    "features": "limit:L exploration_units_e8:E",
    "snapshot-header": "schema_version:I user_id:U interaction_watermark:Z selection_policy:S "
    "max_tracks:I",
    "snapshot-track": "recording_id:U user_track_ref_id:U? artist_key:S release_key:S? "
    "metadata_tokens:A availability:S authorized:B identity_status:S preference:S excluded:B "
    "play_count:Z organic_play_count:Z recommended_play_count:Z last_played_at_ms:I? "
    "added_at_ms:I release_date_ordinal:I?",
    "policy-track": "recording_id:U identity_status:S preference:S excluded:B",
    "availability-track": "recording_id:U availability:S authorized:B",
    "candidate": "recording_id:U",
    "p11-item": "rank:L recording_id:U score_units_e8:I candidate_sources:A explanation_code:S "
    "availability_snapshot:O contributions:C reason_codes:A item_provenance:O",
    "served-item": "source_rank:L recording_id:U score_units_e8:I score_kind:S "
    "attribution_source:S "
    "reasons:A contributions:C original_p11_rank:L origin:S",
    "availability-reference": "snapshot_sha256:D",
    "contribution": "source_key:S source_version:S source_rank:R raw_score_units_e8:I provenance:O",
    "item-provenance": "artist_key:S release_key:S? section:S",
    "preferences": "preference:S",
    "history": "organic_play_count:Z recommended_play_count_ignored_for_affinity:Z forgotten:B",
    "artist_release_metadata": "artist_affinity:B metadata_token_overlap:Z release_key:S?",
    "freshness": "release_date_ordinal:I? artist_affinity:B cold_start_fallback:B",
    "exploration": "seeded:B previously_unplayed:B",
    "root-header": "kind:S hash_format_version:I request_id:U identity_sha256:D metadata_sha256:D "
    "query_document_sha256:D request_features_sha256:D snapshot_header_sha256:D "
    "snapshot_track_count:T snapshot_tracks_sha256:D policy_tracks_sha256:D "
    "availability_tracks_sha256:D candidate_universe_count:T candidate_universe_sha256:D "
    "p11_item_count:N p11_items_sha256:D served_item_count:N served_items_sha256:D "
    "original_capture_binding_sha256:D?",
}
ROLE_FIELDS_V3 = MappingProxyType(
    {
        role: MappingProxyType(dict(field.split(":") for field in fields.split()))
        for role, fields in _FIELDS.items()
    }
)


def _scalar(value: object, kind: str) -> None:
    if kind.endswith("?"):
        if value is None:
            return
        kind = kind[:-1]
    if kind in ("I", "Z", "P", "L", "E", "R", "T", "N"):
        bounds = {
            "I": (-(2**63), 2**63 - 1),
            "Z": (0, 2**63 - 1),
            "P": (1, 2**63 - 1),
            "L": (1, 100),
            "E": (0, 100_000_000),
            "R": (1, 1000),
            "T": (0, 5000),
            "N": (0, 100),
        }
        low, high = bounds[kind]
        if type(value) is not int or not low <= value <= high:
            raise NativeRequestV3Error("V3_ROLE_INTEGER")
    elif kind == "B":
        if type(value) is not bool:
            raise NativeRequestV3Error("V3_ROLE_BOOLEAN")
    elif kind in ("S", "U", "D"):
        if (
            type(value) is not str
            or len(value) > 4096
            or unicodedata.normalize("NFC", value) != value
        ):
            raise NativeRequestV3Error("V3_ROLE_TEXT")
        try:
            if len(value.encode("utf-8")) > 4096:
                raise NativeRequestV3Error("V3_ROLE_TEXT")
        except UnicodeError:
            raise NativeRequestV3Error("V3_ROLE_TEXT") from None
        if kind == "D" and (len(value) != 64 or any(c not in "0123456789abcdef" for c in value)):
            raise NativeRequestV3Error("V3_ROLE_DIGEST")
        if kind == "U":
            try:
                if str(UUID(value)) != value:
                    raise NativeRequestV3Error("V3_ROLE_UUID")
            except ValueError, AttributeError:
                raise NativeRequestV3Error("V3_ROLE_UUID") from None
    elif kind in ("A", "C"):
        if type(value) is not list or len(value) > 4096:
            raise NativeRequestV3Error("V3_ROLE_ARRAY")
        if kind == "A":
            for child in value:
                _scalar(child, "S")
    elif kind == "O" and type(value) is not dict:
        raise NativeRequestV3Error("V3_ROLE_OBJECT")


_OWNERSHIP_FIELDS = {
    "snapshot-track": ("owner_identity_decision_id", "verified_audio_variant_id"),
    "policy-track": ("owner_identity_decision_id",),
    "availability-track": ("verified_audio_variant_id",),
}


def projected_role_keys(role: str, track: dict[str, Any]) -> tuple[str, ...]:
    return (*ROLE_FIELDS_V3[role], *(key for key in _OWNERSHIP_FIELDS.get(role, ()) if key in track))


def validate_role_v3(role: str, value: object, meter: NativeRequestV3MeterProtocol | None) -> None:
    fields = ROLE_FIELDS_V3.get(role)
    if fields is None or type(value) is not dict or value.keys() != set(projected_role_keys(role, value)):
        raise NativeRequestV3Error("V3_ROLE_KEYS")
    if meter is not None:
        meter.charge_nodes(1 + len(fields))
    for key, kind in fields.items():
        _scalar(value[key], kind)
    for key in _OWNERSHIP_FIELDS.get(role, ()):
        if key in value:
            _scalar(value[key], "U")
    if role in ("metadata", "query") and (
        value["surface"] not in ("recommendations", "home", "offline_pack")
        or value["context"] not in SUPPORTED_CONTEXTS
    ):
        raise NativeRequestV3Error("V3_ROLE_QUERY")
    literals: dict[str, dict[str, object]] = {
        "metadata": {"endpoint_max": 100, "pipeline_key": "cpu-baseline"},
        "query": {"schema_version": 2, "canonicalization_version": 1, "shadow": False},
        "snapshot-header": {
            "schema_version": 2 if value.get("schema_version") == 2 else 1,
            "selection_policy": "most_recent_added_then_recording_id_v2" if value.get("schema_version") == 2 else "most_recent_added_then_recording_id_v1",
            "max_tracks": 1024 if value.get("schema_version") == 2 else 5000,
        },
        "preferences": {"preference": "LIKE"},
        "exploration": {"seeded": True},
        "served-item": {"score_kind": "heuristic", "attribution_source": "P11", "origin": "P11"},
        "root-header": {"kind": "NATIVE_SERVED_REQUEST_ROOT_V3", "hash_format_version": 3},
    }
    if any(value[key] != literal for key, literal in literals.get(role, {}).items()):
        raise NativeRequestV3Error("V3_ROLE_LITERAL")
    if role in ("p11-item", "served-item"):
        for contribution in value["contributions"]:
            validate_role_v3("contribution", contribution, meter)
        if role == "p11-item":
            validate_role_v3("availability-reference", value["availability_snapshot"], meter)
            validate_role_v3("item-provenance", value["item_provenance"], meter)
    if role == "contribution":
        if value["source_key"] not in (
            "preferences",
            "history",
            "artist_release_metadata",
            "freshness",
            "exploration",
        ):
            raise NativeRequestV3Error("V3_PROVENANCE_SOURCE")
        validate_role_v3(value["source_key"], value["provenance"], meter)


def role_schema_v3(role: str, value: object) -> CanonicalJsonLpSchema:
    """The schema only describes paths; required sets/types are checked separately."""
    paths = {"$": frozenset(ROLE_FIELDS_V3[role]).union(_OWNERSHIP_FIELDS.get(role, ()))}
    if role in ("p11-item", "served-item"):
        paths["$.contributions[]"] = frozenset(ROLE_FIELDS_V3["contribution"])
        # All five variants share a path; validation above chooses its exact set.
        paths["$.contributions[].provenance"] = frozenset().union(
            *(
                frozenset(ROLE_FIELDS_V3[key])
                for key in (
                    "preferences",
                    "history",
                    "artist_release_metadata",
                    "freshness",
                    "exploration",
                )
            )
        )
        if role == "p11-item":
            paths["$.availability_snapshot"] = frozenset(ROLE_FIELDS_V3["availability-reference"])
            paths["$.item_provenance"] = frozenset(ROLE_FIELDS_V3["item-provenance"])
    return CanonicalJsonLpSchema(paths)


def node_count_v3(value: object) -> int:
    if type(value) is dict:
        return 1 + sum(1 + node_count_v3(child) for child in value.values())
    if type(value) is list:
        return 1 + sum(1 + node_count_v3(child) for child in value)
    return 1


def lp_digest_v3(
    value: object, schema: CanonicalJsonLpSchema, meter: NativeRequestV3MeterProtocol
) -> bytes:
    meter.charge_nodes(node_count_v3(value))
    encoded = encode_canonical_json_lp(value, schema)
    meter.charge_bytes(2 * len(encoded))
    return sha256(encoded).digest()


def outer_digest_v3(
    domain: str, fields: tuple[bytes | None, ...], meter: NativeRequestV3MeterProtocol
) -> bytes:
    meter.charge_nodes(1 + len(fields))
    encoded = encode_serving_lp(domain, fields)
    meter.charge_bytes(2 * len(encoded))
    return sha256(encoded).digest()


class RoleSetHasherV3:
    """Stream one closed ordered set without retaining its full preimage."""

    def __init__(
        self, role: SetRoleV3, request_id: UUID, count: int, meter: NativeRequestV3MeterProtocol
    ) -> None:
        if (
            role not in ("snapshot-track", "policy-track", "availability-track", "candidate")
            or type(request_id) is not UUID
            or type(count) is not int
            or not 0 <= count <= 5000
        ):
            raise NativeRequestV3Error("V3_SET_INPUT")
        self.role, self.request_id, self.count, self.meter = role, request_id, count, meter
        self.ordinal = 0
        self._hash = sha256()
        domain = f"autplay.recommendation-{role}-set.v3"
        logical = len(domain) + 5 + 21 + 13 + 45 * count
        if logical > 262_144:
            raise NativeRequestV3Error("V3_SET_BYTES")
        self._write(domain.encode("ascii") + b"\0" + struct.pack(">I", count + 2))
        self._write(b"\x01\0\0\0\x10" + request_id.bytes)
        self._write(b"\x01\0\0\0\x08" + integer_bytes(count))

    def _write(self, raw: bytes) -> None:
        self.meter.charge_bytes(2 * len(raw))
        self.meter.charge_nodes()
        self._hash.update(raw)

    def add(self, leaf: dict[str, Any]) -> None:
        if self.ordinal >= self.count:
            raise NativeRequestV3Error("V3_SET_COUNT")
        self.meter.charge_leaf()
        validate_role_v3(self.role, leaf, self.meter)
        digest = lp_digest_v3(leaf, role_schema_v3(self.role, leaf), self.meter)
        digest = outer_digest_v3(
            f"autplay.recommendation-{self.role}-leaf.v3",
            (self.request_id.bytes, integer_bytes(self.ordinal + 1), digest),
            self.meter,
        )
        self._write(b"\x01\0\0\0\x28" + integer_bytes(self.ordinal + 1) + digest)
        self.ordinal += 1

    def finish(self) -> bytes:
        if self.ordinal != self.count:
            raise NativeRequestV3Error("V3_SET_COUNT")
        return self._hash.digest()


def role_set_digest_v3(
    role: SetRoleV3,
    request_id: UUID,
    leaves: Iterable[dict[str, Any]],
    *,
    meter: NativeRequestV3MeterProtocol,
) -> bytes:
    """Vector helper for sized immutable sequences; production streams four roots."""
    if type(leaves) is not tuple:
        raise NativeRequestV3Error("V3_SET_SEQUENCE")
    state = RoleSetHasherV3(role, request_id, len(leaves), meter)
    for leaf in leaves:
        state.add(leaf)
    return state.finish()
