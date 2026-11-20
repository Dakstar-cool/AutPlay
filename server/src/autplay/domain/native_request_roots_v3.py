"""Pure P11 request roots V3; deliberately uninstalled in storage and serving.

The owning application supplies an actual P11 response and the actual mandatory
filter. This module converts and compares them; it never ranks or grants trust.
"""

from __future__ import annotations

import sys
from bisect import bisect_left
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import IntEnum
from functools import cmp_to_key
from hashlib import sha256
from itertools import pairwise
from typing import TYPE_CHECKING, Any, Protocol, cast, overload
from uuid import UUID

from autplay.domain.canonical_json_lp import CanonicalJsonLpSchema, encode_canonical_json_lp
from autplay.domain.native_original_ownership_v3 import (
    NativeImmutableBaselineBorrowV3,
    _NativeImmutableBaselineOwnerV3,
    _retain_native_original_failure_v3,
    _verify_native_original_inputs_v3,
    borrow_native_immutable_baseline_v3,
)
from autplay.domain.native_request_budget_v3 import (
    MAX_PROOF_BYTES,
    MAX_RECORD_BYTES,
    NativeRequestV3Budget,
    NativeRequestV3Error,
)
from autplay.domain.native_request_builder_meter_v3 import NativeRequestV3BuildMeterProtocol
from autplay.domain.native_request_expected_meter_v3 import (
    NativeRequestV3DocumentOutputLeaseProtocol,
    NativeRequestV3ExpectedMeterProtocol,
)
from autplay.domain.native_request_meter_v3 import (
    NativeRequestV3FinalizationProtocol,
    NativeRequestV3MeterProtocol,
)
from autplay.domain.native_request_roles_v3 import (
    ROLE_FIELDS_V3,
    FrozenObjectV3,
    RoleSetHasherV3,
    bound_frozen_v3,
    frozen_object_v3,
    lp_digest_v3,
    projected_role_keys,
    node_count_v3,
    outer_digest_v3,
    role_schema_v3,
    validate_role_v3,
)
from autplay.domain.native_request_wire_v3 import (
    _Encoder,
    _Scanner,
    canonical_native_json_v3,
    measure_native_json_v3,
    preflight_admitted_native_json_v3,
    preflight_native_json_v3,
)
from autplay.domain.native_served_authority import (
    baseline_item_digest,
    encode_serving_lp,
    integer_bytes,
    ordered_set_digest,
    served_item_digest,
)
from autplay.domain.native_served_authority_values import NativeServedIdentity
from autplay.domain.recommendations import (
    ComponentVersionRef,
    PipelineDefinition,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationResponse,
    RecommendationSnapshotRef,
    RecommendationSurface,
    SnapshotTrack,
)
from autplay.domain.score_e8 import score_e8_decimal, score_e8_from_binary64

if TYPE_CHECKING:
    from autplay.domain.native_connected_budget_v3 import NativeConnectedRecordAdmissionV3
    from autplay.domain.native_connected_request_v3 import NativeP11UnvalidatedHeadV3

_ZERO = "0" * 64
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_HORIZON_US = 180 * 86_400 * 1_000_000
_BODY_FIELDS = frozenset(
    [
        "request_id",
        "identity",
        "metadata",
        "request_document",
        "request_features",
        "snapshot_header",
        "snapshot_tracks",
        "p11_items",
        "origin",
    ]
)


class NativeRequestHashFormat(IntEnum):
    FROZEN_LP_V2 = 2
    ROOT_V3 = 3


class NativeP11CandidateAuthorityV3(Protocol):
    def filter_snapshot_tracks(
        self, query: RecommendationQuery, snapshot: RecommendationInputSnapshot
    ) -> tuple[SnapshotTrack, ...]: ...


def _uuid(value: UUID) -> UUID:
    if type(value) is not UUID:
        raise NativeRequestV3Error("V3_UUID")
    return value


def _digest(value: str) -> bytes:
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise NativeRequestV3Error("V3_DIGEST")
    return bytes.fromhex(value)


def _time_us(value: datetime) -> int:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise NativeRequestV3Error("V3_TIME")
    result = (value.astimezone(UTC) - _EPOCH) // timedelta(microseconds=1)
    integer_bytes(result)
    return result


def _exact_units(raw: float) -> int:
    units = score_e8_from_binary64(raw)
    if Decimal(str(raw)) != score_e8_decimal(units):
        raise NativeRequestV3Error("V3_SCORE_PRECISION")
    return units


@dataclass(frozen=True, slots=True)
class StandaloneP11OriginV3:
    def to_dict(self) -> dict[str, Any]:
        return {"kind": "STANDALONE_P11_V3"}


@dataclass(frozen=True, slots=True)
class ConnectedP11InputOriginV3:
    original_request_id: UUID
    capture_sha256: str
    consent_generation: int
    consent_receipt_sha256: str

    def __post_init__(self) -> None:
        _uuid(self.original_request_id)
        _digest(self.capture_sha256)
        _digest(self.consent_receipt_sha256)
        if (
            type(self.consent_generation) is not int
            or not 1 <= self.consent_generation <= 9_007_199_254_740_991
        ):
            raise NativeRequestV3Error("V3_CONSENT_GENERATION")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "ORIGINAL_CAPTURE_P11_V3",
            "original_request_id": str(self.original_request_id),
            "capture_sha256": self.capture_sha256,
            "consent_generation": self.consent_generation,
            "consent_receipt_sha256": self.consent_receipt_sha256,
        }


@dataclass(frozen=True, slots=True)
class NativeP11BodyV3:
    request_id: str
    identity: FrozenObjectV3
    metadata: FrozenObjectV3
    request_document: FrozenObjectV3
    request_features: FrozenObjectV3
    snapshot_header: FrozenObjectV3
    snapshot_tracks: tuple[FrozenObjectV3, ...]
    p11_items: tuple[FrozenObjectV3, ...]
    origin: FrozenObjectV3

    def __post_init__(self) -> None:
        if type(self.request_id) is not str:
            raise NativeRequestV3Error("V3_BODY_REQUEST")
        for name in (
            "identity",
            "metadata",
            "request_document",
            "request_features",
            "snapshot_header",
            "origin",
        ):
            if type(getattr(self, name)) is not FrozenObjectV3:
                raise NativeRequestV3Error("V3_BODY_MUTABLE")
        for name, bound in (("snapshot_tracks", 5000), ("p11_items", 100)):
            sequence = getattr(self, name)
            if (
                type(sequence) is not tuple
                or len(sequence) > bound
                or any(type(v) is not FrozenObjectV3 for v in sequence)
            ):
                raise NativeRequestV3Error("V3_BODY_SEQUENCE")

        # Bound all nine members before a caller can expand them with to_dict.
        FrozenObjectV3(
            tuple(
                sorted(
                    (
                        ("request_id", self.request_id),
                        ("identity", self.identity),
                        ("metadata", self.metadata),
                        ("request_document", self.request_document),
                        ("request_features", self.request_features),
                        ("snapshot_header", self.snapshot_header),
                        ("snapshot_tracks", self.snapshot_tracks),
                        ("p11_items", self.p11_items),
                        ("origin", self.origin),
                    )
                )
            )
        )

    @classmethod
    def from_dict(
        cls, value: dict[str, Any], *, meter: NativeRequestV3MeterProtocol | None = None
    ) -> NativeP11BodyV3:
        """Seal already-owned data; this is not the untrusted wire entry point."""
        if type(value) is not dict or value.keys() != _BODY_FIELDS:
            raise NativeRequestV3Error("V3_BODY_KEYS")
        # The wrapper selects the closed body array bounds while measuring
        # before freeze can copy any caller-supplied nested collection.
        measure_native_json_v3({"body": value}, meter=meter)
        return cls(
            value["request_id"],
            frozen_object_v3(value["identity"]),
            frozen_object_v3(value["metadata"]),
            frozen_object_v3(value["request_document"]),
            frozen_object_v3(value["request_features"]),
            frozen_object_v3(value["snapshot_header"]),
            tuple(frozen_object_v3(track) for track in value["snapshot_tracks"]),
            tuple(frozen_object_v3(item) for item in value["p11_items"]),
            frozen_object_v3(value["origin"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "identity": self.identity.to_dict(),
            "metadata": self.metadata.to_dict(),
            "request_document": self.request_document.to_dict(),
            "request_features": self.request_features.to_dict(),
            "snapshot_header": self.snapshot_header.to_dict(),
            "snapshot_tracks": [track.to_dict() for track in self.snapshot_tracks],
            "p11_items": [item.to_dict() for item in self.p11_items],
            "origin": self.origin.to_dict(),
        }


def _track_document(track: SnapshotTrack) -> dict[str, Any]:
    if type(track) is not SnapshotTrack or type(track.metadata_tokens) is not tuple:
        raise NativeRequestV3Error("V3_EXPECTED_TRACK")
    result = {field.name: getattr(track, field.name) for field in fields(SnapshotTrack)}
    result["recording_id"] = str(_uuid(track.recording_id))
    result["user_track_ref_id"] = (
        None if track.user_track_ref_id is None else str(_uuid(track.user_track_ref_id))
    )
    result["metadata_tokens"] = list(track.metadata_tokens)
    for key in ("owner_identity_decision_id", "verified_audio_variant_id"):
        value = result.pop(key)
        if value is not None:
            result[key] = str(_uuid(value))
    validate_role_v3("snapshot-track", result, None)
    return result


def _pipeline_immutable(pipeline: PipelineDefinition) -> None:
    if (
        type(pipeline) is not PipelineDefinition
        or type(pipeline.components) is not tuple
        or type(pipeline.generator_budgets) is not tuple
    ):
        raise NativeRequestV3Error("V3_EXPECTED_PIPELINE")
    for field in fields(PipelineDefinition):
        if field.name not in ("components", "generator_budgets") and type(
            getattr(pipeline, field.name)
        ) not in (int, str):
            raise NativeRequestV3Error("V3_EXPECTED_PIPELINE")
    for component in pipeline.components:
        if type(component) is not ComponentVersionRef or any(
            type(getattr(component, f.name)) is not str for f in fields(ComponentVersionRef)
        ):
            raise NativeRequestV3Error("V3_EXPECTED_PIPELINE")
    for pair in pipeline.generator_budgets:
        if (
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not int
        ):
            raise NativeRequestV3Error("V3_EXPECTED_PIPELINE")


@dataclass(frozen=True, slots=True, init=False)
class NativeP11ExpectedValuesV3:
    """Sealed caller-owned inputs, including a copy of mutable legacy provenance."""

    served_request_id: UUID
    identity: NativeServedIdentity
    query: RecommendationQuery
    baseline: RecommendationInputSnapshot
    pipeline: PipelineDefinition
    origin: StandaloneP11OriginV3 | ConnectedP11InputOriginV3
    _template: NativeP11BodyV3
    _reference_snapshot: bool
    _created_at_us: int

    def __init__(
        self,
        served_request_id: UUID,
        identity: NativeServedIdentity,
        query: RecommendationQuery,
        baseline: RecommendationInputSnapshot,
        actual_p11_response: RecommendationResponse,
        pipeline: PipelineDefinition,
        origin: StandaloneP11OriginV3 | ConnectedP11InputOriginV3,
        *,
        reference_snapshot: bool = False,
    ) -> None:
        self._initialize(
            served_request_id,
            identity,
            query,
            baseline,
            actual_p11_response,
            pipeline,
            origin,
            _owned=None,
            reference_snapshot=reference_snapshot,
        )

    @classmethod
    def from_admitted(
        cls,
        served_request_id: UUID,
        identity: NativeServedIdentity,
        query: RecommendationQuery,
        baseline: RecommendationInputSnapshot,
        actual_p11_response: RecommendationResponse,
        pipeline: PipelineDefinition,
        origin: StandaloneP11OriginV3 | ConnectedP11InputOriginV3,
        *,
        meter: NativeRequestV3OwnedMeterProtocol,
    ) -> NativeP11ExpectedLeaseV3:
        try:
            return NativeP11ExpectedLeaseV3.from_admitted(
                served_request_id,
                identity,
                query,
                baseline,
                actual_p11_response,
                pipeline,
                origin,
                meter=meter,
            )
        except BaseException as error:
            if not _clear_owned_factory_error_v3(error, meter):
                _retain_native_original_failure_v3(baseline, meter=meter)
            # A newly raised traceback keeps this executing wrapper alive.
            # Drop every borrowed input before propagating the same exception.
            del served_request_id, identity, query, baseline, actual_p11_response
            del pipeline, origin, meter
            raise error from None

    def _initialize(
        self,
        served_request_id: UUID,
        identity: NativeServedIdentity,
        query: RecommendationQuery,
        baseline: RecommendationInputSnapshot,
        actual_p11_response: RecommendationResponse,
        pipeline: PipelineDefinition,
        origin: StandaloneP11OriginV3 | ConnectedP11InputOriginV3,
        *,
        _owned: _ExpectedFactoryAccounting | None,
        reference_snapshot: bool = False,
    ) -> None:
        if type(reference_snapshot) is not bool or (
            reference_snapshot and type(origin) is not ConnectedP11InputOriginV3
        ):
            raise NativeRequestV3Error("V4_ORIGINAL_REQUIRED")
        _uuid(served_request_id)
        if type(identity) is not NativeServedIdentity:
            raise NativeRequestV3Error("V3_EXPECTED_IDENTITY")
        identity.validate()
        _pipeline_immutable(pipeline)
        if (
            type(query) is not RecommendationQuery
            or type(query.surface) is not RecommendationSurface
            or type(query.user_id) is not UUID
            or type(query.context) is not str
            or type(query.seed) is not int
            or type(query.limit) is not int
            or type(query.schema_version) is not int
            or query.schema_version != 1
            or type(query.canonicalization_version) is not int
            or query.canonicalization_version != 1
            or query.shadow is not False
            or query.user_id != identity.owner
        ):
            raise NativeRequestV3Error("V3_EXPECTED_QUERY")
        integer_bytes(query.seed)
        if type(query.exploration) not in (int, float):
            raise NativeRequestV3Error("V3_EXPECTED_QUERY")
        exploration = _exact_units(float(query.exploration))
        if (
            type(baseline) is not RecommendationInputSnapshot
            or type(baseline.tracks) is not tuple
            or len(baseline.tracks) > 5000
            or type(baseline.reference) is not RecommendationSnapshotRef
        ):
            raise NativeRequestV3Error("V3_EXPECTED_SNAPSHOT")
        if (
            type(actual_p11_response) is not RecommendationResponse
            or type(actual_p11_response.items) is not tuple
            or len(actual_p11_response.items) > query.limit
        ):
            raise NativeRequestV3Error("V3_EXPECTED_RESPONSE")
        if type(origin) not in (StandaloneP11OriginV3, ConnectedP11InputOriginV3):
            raise NativeRequestV3Error("V3_EXPECTED_ORIGIN")
        expected_response_id = (
            origin.original_request_id
            if type(origin) is ConnectedP11InputOriginV3
            else served_request_id
        )
        trace = actual_p11_response.request
        if (
            trace.query != query
            or trace.pipeline != pipeline
            or trace.snapshot != baseline.reference
            or trace.recommendation_request_id != expected_response_id
            or (
                type(origin) is ConnectedP11InputOriginV3
                and expected_response_id == served_request_id
            )
        ):
            raise NativeRequestV3Error("V3_EXPECTED_TRACE")
        created_us = _time_us(trace.created_at)
        if _time_us(baseline.retained_until) < created_us + _HORIZON_US:
            raise NativeRequestV3Error("V3_EXPECTED_RETENTION")
        if _owned is None:
            ids = sorted(_uuid(track.recording_id).bytes for track in baseline.tracks)
            if any(left == right for left, right in pairwise(ids)):
                raise NativeRequestV3Error("V3_DUPLICATE_TRACK")
            # Seal every field before retaining the domain snapshot for the filter.
            sealed_tracks = (
                () if reference_snapshot
                else tuple(frozen_object_v3(_track_document(track)) for track in baseline.tracks)
            )
            cloned_tracks = tuple(
                replace(track, metadata_tokens=tuple(track.metadata_tokens))
                for track in baseline.tracks
            )
            sealed_baseline = replace(
                baseline,
                tracks=cloned_tracks,
                retained_until=_EPOCH + timedelta(microseconds=_time_us(baseline.retained_until)),
            )
        else:
            sealed_tracks = _owned.seal_tracks(baseline, reference_snapshot=reference_snapshot)
            sealed_baseline = baseline
        reference = baseline.reference
        if (
            any(
                type(getattr(reference, name)) is not int
                for name in ("interaction_watermark", "catalog_snapshot")
            )
            or any(
                type(getattr(reference, name)) is not str
                for name in (
                    "input_snapshot_sha256",
                    "availability_snapshot",
                    "policy_snapshot_sha256",
                )
            )
            or type(reference.snapshot_id) is not UUID
        ):
            raise NativeRequestV3Error("V3_EXPECTED_REFERENCE")
        labels = {
            kind: ",".join(
                f"{c.key}:{c.version}:{c.config_sha256}"
                for c in pipeline.components
                if c.kind == kind
            )
            for kind in ("candidate_generator", "filter", "reranker")
        }
        metadata = {
            "surface": query.surface.value,
            "context": query.context,
            "seed": query.seed,
            "effective_limit": query.limit,
            "endpoint_max": 100,
            "cutoff_at_ms": created_us // 1000,
            "pipeline_key": pipeline.pipeline_key,
            "pipeline_version": pipeline.version,
            "pipeline_manifest_sha256": pipeline.manifest_sha256,
            "model_bundle_version": f"{pipeline.pipeline_key}:{pipeline.version}",
            "candidate_policy_version": labels["candidate_generator"]
            if type(origin) is ConnectedP11InputOriginV3
            else "1",
            "filter_policy_version": labels["filter"]
            if type(origin) is ConnectedP11InputOriginV3
            else "1",
            "reranker_version": labels["reranker"]
            if type(origin) is ConnectedP11InputOriginV3
            else "1",
            "interaction_watermark": reference.interaction_watermark,
            "catalog_snapshot": reference.catalog_snapshot
            if type(origin) is ConnectedP11InputOriginV3
            else max(
                (max(t.added_at_ms, t.last_played_at_ms or 0) for t in baseline.tracks), default=0
            ),
            "availability_snapshot_ref": _ZERO,
        }
        query_document = {
            "schema_version": 2,
            "canonicalization_version": 1,
            "user_id": str(query.user_id),
            "surface": query.surface.value,
            "context": query.context,
            "seed": query.seed,
            "shadow": False,
            "limit": query.limit,
            "exploration_units_e8": exploration,
        }
        items: list[Any] = []
        for item in actual_p11_response.items:
            if type(item.contributions) is not tuple or type(item.reason_codes) is not tuple:
                raise NativeRequestV3Error("V3_EXPECTED_ITEM")
            if _owned is not None:
                _owned.begin_item(item)
            item_document = {
                "rank": item.source_rank,
                "recording_id": str(_uuid(item.recording_id)),
                "score_units_e8": _exact_units(item.score),
                "candidate_sources": [c.source_key for c in item.contributions],
                "explanation_code": item.reason_code,
                "availability_snapshot": {"snapshot_sha256": _ZERO},
                "contributions": [
                    {
                        "source_key": c.source_key,
                        "source_version": c.source_version,
                        "source_rank": c.source_rank,
                        "raw_score_units_e8": _exact_units(c.raw_score),
                        "provenance": c.provenance
                        if _owned is None
                        else _owned.copy_provenance(c.provenance),
                    }
                    for c in item.contributions
                ],
                "reason_codes": list(item.reason_codes),
                "item_provenance": {
                    "artist_key": item.artist_key,
                    "release_key": item.release_key,
                    "section": item.section,
                },
            }
            if _owned is None:
                items.append(item_document)
            else:
                items.append(_owned.seal_leaf(item_document))
        make_body = NativeP11BodyV3 if _owned is None else _owned.body
        body = make_body(
            str(served_request_id),
            frozen_object_v3(
                {
                    "owner_user_id": str(identity.owner),
                    "device_id": str(identity.device),
                    "journal_epoch": str(identity.journal_epoch),
                    "server_instance_id": str(identity.server_instance),
                    "identity_epoch": identity.identity_epoch,
                }
            ),
            frozen_object_v3(metadata),
            frozen_object_v3(query_document),
            frozen_object_v3({"limit": query.limit, "exploration_units_e8": exploration}),
            frozen_object_v3(
                {
                    "schema_version": int(baseline.policy_version),
                    "user_id": str(query.user_id),
                    "interaction_watermark": reference.interaction_watermark,
                    "selection_policy": f"most_recent_added_then_recording_id_v{baseline.policy_version}",
                    "max_tracks": 1024 if baseline.policy_version == "2" else 5000,
                }
            ),
            sealed_tracks,
            tuple(frozen_object_v3(item) for item in items) if _owned is None else tuple(items),
            frozen_object_v3(origin.to_dict()),
        )
        for name, value in (
            ("served_request_id", served_request_id),
            ("identity", identity),
            ("query", query),
            ("baseline", sealed_baseline),
            ("pipeline", pipeline),
            ("origin", origin),
            ("_template", body),
            ("_reference_snapshot", reference_snapshot),
            ("_created_at_us", created_us),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class NativeServedRequestProofV3:
    document: bytes
    proof_sha256: str

    def __post_init__(self) -> None:
        if type(self.document) is not bytes:
            raise NativeRequestV3Error("V3_PROOF_TYPE")
        _digest(self.proof_sha256)


@dataclass(frozen=True, slots=True)
class VerifiedNativeP11RootV3:
    body: NativeP11BodyV3
    root_header: FrozenObjectV3
    root_sha256: str
    _created_at_us: int

    def __post_init__(self) -> None:
        if type(self.body) is not NativeP11BodyV3 or type(self.root_header) is not FrozenObjectV3:
            raise NativeRequestV3Error("V3_VERIFIED_MUTABLE")
        _digest(self.root_sha256)
        integer_bytes(self._created_at_us)


def _candidates(
    expected: NativeP11ExpectedValuesV3,
    authority: NativeP11CandidateAuthorityV3 | NativeP11AdmittedCandidateAuthorityV3,
    meter: NativeRequestV3MeterProtocol,
    *,
    _owned: _OwnedOperation | None = None,
) -> tuple[SnapshotTrack, ...]:
    meter.charge_nodes(32 * len(expected.baseline.tracks))
    if _owned is None:
        allowed = cast(NativeP11CandidateAuthorityV3, authority).filter_snapshot_tracks(
            expected.query,
            expected.baseline,
        )
        return _candidate_values(expected, allowed, meter)
    scope = cast(NativeP11AdmittedCandidateAuthorityV3, authority).filter_snapshot_tracks_admitted(
        expected.query,
        expected.baseline,
        _owned.source_index(),
        meter=_owned.meter,
    )
    try:
        scope.check_current()
        _owned.reserve_candidate_sort(len(scope))
        return _candidate_values(expected, scope, meter, _owned=_owned)
    finally:
        scope.close()


def _candidate_values(
    expected: NativeP11ExpectedValuesV3,
    allowed: tuple[SnapshotTrack, ...] | NativeP11AdmittedCandidateScopeV3,
    meter: NativeRequestV3MeterProtocol,
    *,
    _owned: _OwnedOperation | None = None,
) -> tuple[SnapshotTrack, ...]:
    if (_owned is None and type(allowed) is not tuple) or len(allowed) > len(
        expected.baseline.tracks
    ):
        raise NativeRequestV3Error("V3_CANDIDATE_AUTHORITY")
    for track in allowed:
        meter.charge_nodes(2)
        if type(track) is not SnapshotTrack or type(track.recording_id) is not UUID:
            raise NativeRequestV3Error("V3_CANDIDATE_AUTHORITY")

    def compare(left: SnapshotTrack, right: SnapshotTrack) -> int:
        meter.charge_nodes(3)
        meter.charge_bytes(32)
        # Integer order equals unsigned big-endian UUID-byte order, without
        # retaining temporary T-sized raw UUID byte arrays during sorting.
        return (left.recording_id.int > right.recording_id.int) - (
            left.recording_id.int < right.recording_id.int
        )

    source = (
        sorted(expected.baseline.tracks, key=cmp_to_key(compare))
        if _owned is None
        else _owned.source_index()
    )
    result = tuple(sorted(allowed, key=cmp_to_key(compare)))
    previous: int | None = None
    position = 0
    for track in result:
        meter.charge_nodes(2)
        if previous == track.recording_id.int:
            raise NativeRequestV3Error("V3_CANDIDATE_AUTHORITY")
        previous = track.recording_id.int
        while position < len(source):
            meter.charge_nodes(3)
            if source[position].recording_id.int >= previous:
                break
            position += 1
        if position == len(source) or source[position] is not track:
            raise NativeRequestV3Error("V3_CANDIDATE_AUTHORITY")
    return result


def _header_template(body: dict[str, Any], candidates: int, connected: bool) -> dict[str, Any]:
    result: dict[str, Any] = {key: _ZERO for key in ROLE_FIELDS_V3["root-header"]}
    result.update(
        kind="NATIVE_SERVED_REQUEST_ROOT_V3",
        hash_format_version=3,
        request_id=body["request_id"],
        snapshot_track_count=len(body["snapshot_tracks"]),
        candidate_universe_count=candidates,
        p11_item_count=len(body["p11_items"]),
        served_item_count=len(body["p11_items"]),
        original_capture_binding_sha256=_ZERO if connected else None,
    )
    return result


def _baseline_reference(expected: NativeP11ExpectedValuesV3) -> dict[str, Any]:
    ref = expected.baseline.reference
    return {
        "owner_user_id": str(expected.identity.owner),
        "snapshot_id": str(ref.snapshot_id),
        "sha256": ref.input_snapshot_sha256,
        "track_count": len(expected.baseline.tracks),
    }


def _envelope(
    body: dict[str, Any], header: dict[str, Any], root: str,
    expected: NativeP11ExpectedValuesV3,
) -> dict[str, Any]:
    result = {
        "kind": "NATIVE_SERVED_REQUEST_PROOF_V3",
        "proof_version": 3,
        "hash_format_version": 3,
        "body": body,
        "root_header": header,
        "root_sha256": root,
    }
    if expected._reference_snapshot:
        result.update(kind="NATIVE_SERVED_REQUEST_PROOF_V4", proof_version=4)
        result["snapshot_ref"] = _baseline_reference(expected)
    return result


def _validate_body(body: dict[str, Any], meter: NativeRequestV3MeterProtocol) -> None:
    if body.keys() != _BODY_FIELDS:
        raise NativeRequestV3Error("V3_BODY_KEYS")
    request = body["request_id"]
    if type(request) is not str or str(UUID(request)) != request:
        raise NativeRequestV3Error("V3_BODY_REQUEST")
    for role, key in (
        ("identity", "identity"),
        ("metadata", "metadata"),
        ("query", "request_document"),
        ("features", "request_features"),
        ("snapshot-header", "snapshot_header"),
    ):
        validate_role_v3(role, body[key], meter)
    for key, role, bound in (
        ("snapshot_tracks", "snapshot-track", 5000),
        ("p11_items", "p11-item", 100),
    ):
        values = body[key]
        if type(values) is not list or len(values) > bound:
            raise NativeRequestV3Error("V3_BODY_SEQUENCE")
        for value in values:
            validate_role_v3(role, value, meter)
    origin = body["origin"]
    if type(origin) is not dict or type(origin.get("kind")) is not str:
        raise NativeRequestV3Error("V3_ORIGIN")
    if origin["kind"] == "STANDALONE_P11_V3":
        if origin.keys() != {"kind"}:
            raise NativeRequestV3Error("V3_ORIGIN")
    elif origin["kind"] == "ORIGINAL_CAPTURE_P11_V3":
        if origin.keys() != {
            "kind",
            "original_request_id",
            "capture_sha256",
            "consent_generation",
            "consent_receipt_sha256",
        }:
            raise NativeRequestV3Error("V3_ORIGIN")
        original = origin["original_request_id"]
        if type(original) is not str or str(UUID(original)) != original or original == request:
            raise NativeRequestV3Error("V3_ORIGIN")
        ConnectedP11InputOriginV3(
            UUID(original),
            origin["capture_sha256"],
            origin["consent_generation"],
            origin["consent_receipt_sha256"],
        )
    else:
        raise NativeRequestV3Error("V3_ORIGIN")
    meter.charge_nodes(1 + 2 * len(origin))


def _component(
    value: object, paths: dict[str, frozenset[str]], meter: NativeRequestV3MeterProtocol
) -> bytes:
    return lp_digest_v3(value, CanonicalJsonLpSchema(paths), meter)


def _charge_outer(
    domain: str, fields_: tuple[bytes | None, ...], meter: NativeRequestV3MeterProtocol
) -> None:
    length = len(domain) + 5 + sum(1 if value is None else 5 + len(value) for value in fields_)
    meter.charge_bytes(2 * length)
    meter.charge_nodes(1 + len(fields_))


def _item_sets(body: dict[str, Any], meter: NativeRequestV3MeterProtocol) -> tuple[bytes, bytes]:
    request_id = UUID(body["request_id"])
    p11_pairs: list[tuple[int, bytes]] = []
    served_pairs: list[tuple[int, bytes]] = []
    for item in body["p11_items"]:
        meter.charge_leaf(2)
        validate_role_v3("p11-item", item, meter)
        lp_digest_v3(item, role_schema_v3("p11-item", item), meter)
        contributions_schema = role_schema_v3("p11-item", item)
        contributions = _component(
            item["contributions"],
            {
                "$[]": contributions_schema.object_keys["$.contributions[]"],
                "$[].provenance": contributions_schema.object_keys["$.contributions[].provenance"],
            },
            meter,
        )
        sources = _component(item["candidate_sources"], {"$": frozenset()}, meter)
        availability = _component(
            item["availability_snapshot"], {"$": frozenset({"snapshot_sha256"})}, meter
        )
        reasons = _component(item["reason_codes"], {"$": frozenset()}, meter)
        provenance = _component(
            item["item_provenance"], {"$": frozenset(ROLE_FIELDS_V3["item-provenance"])}, meter
        )
        rank, recording, units = item["rank"], UUID(item["recording_id"]), item["score_units_e8"]
        digest = baseline_item_digest(
            request_id,
            rank,
            recording,
            units,
            sources,
            item["explanation_code"],
            availability,
            contributions,
            reasons,
            provenance,
        )
        _charge_outer(
            "autplay.recommendation-p11-item.v2",
            (
                request_id.bytes,
                integer_bytes(rank),
                recording.bytes,
                integer_bytes(units),
                sources,
                item["explanation_code"].encode("utf-8"),
                availability,
                contributions,
                reasons,
                provenance,
            ),
            meter,
        )
        p11_pairs.append((rank, digest))
        served = {
            "source_rank": rank,
            "recording_id": str(recording),
            "score_units_e8": units,
            "score_kind": "heuristic",
            "attribution_source": "P11",
            "reasons": item["reason_codes"],
            "contributions": item["contributions"],
            "original_p11_rank": rank,
            "origin": "P11",
        }
        validate_role_v3("served-item", served, meter)
        lp_digest_v3(served, role_schema_v3("served-item", served), meter)
        # The same exact bounded values feed the unchanged V2 component hashes.
        served_reasons = _component(served["reasons"], {"$": frozenset()}, meter)
        served_contributions = _component(
            served["contributions"],
            {
                "$[]": contributions_schema.object_keys["$.contributions[]"],
                "$[].provenance": contributions_schema.object_keys["$.contributions[].provenance"],
            },
            meter,
        )
        digest = served_item_digest(
            request_id,
            rank,
            recording,
            units,
            "heuristic",
            "P11",
            served_reasons,
            served_contributions,
            rank,
            "P11",
        )
        _charge_outer(
            "autplay.recommendation-served-item.v2",
            (
                request_id.bytes,
                integer_bytes(rank),
                recording.bytes,
                integer_bytes(units),
                b"heuristic",
                b"P11",
                served_reasons,
                served_contributions,
                integer_bytes(rank),
                b"P11",
            ),
            meter,
        )
        served_pairs.append((rank, digest))
    for kind, pairs in (("p11", p11_pairs), ("served", served_pairs)):
        _charge_outer(
            f"autplay.recommendation-{kind}-set.v2",
            (
                integer_bytes(len(pairs)),
                *(field for rank, digest in pairs for field in (integer_bytes(rank), digest)),
            ),
            meter,
        )
    return ordered_set_digest("p11", p11_pairs), ordered_set_digest("served", served_pairs)


def _calculate(
    expected: NativeP11ExpectedValuesV3,
    candidates: tuple[SnapshotTrack, ...],
    meter: NativeRequestV3MeterProtocol,
    *,
    _owned: _OwnedOperation | None = None,
) -> tuple[NativeP11BodyV3, dict[str, Any], bytes]:
    body = expected._template.to_dict() if _owned is None else _owned.projection(expected._template)
    if _owned is not None:
        _owned.keep(body)
    meter.charge_nodes(node_count_v3(body))
    count = len(expected.baseline.tracks)
    snapshot = RoleSetHasherV3("snapshot-track", expected.served_request_id, count, meter)
    policy = RoleSetHasherV3("policy-track", expected.served_request_id, count, meter)
    availability = RoleSetHasherV3("availability-track", expected.served_request_id, count, meter)
    candidate = RoleSetHasherV3("candidate", expected.served_request_id, len(candidates), meter)
    if expected._reference_snapshot:
        tracks = (
            (_track_document(track) for track in expected.baseline.tracks)
            if _owned is None
            else _owned.reference_tracks(expected.baseline)
        )
    else:
        tracks = body["snapshot_tracks"]
    for document in tracks:
        snapshot.add(document)
        policy.add({key: document[key] for key in projected_role_keys("policy-track", document)})
        availability.add({key: document[key] for key in projected_role_keys("availability-track", document)})
    availability_digest = availability.finish()
    availability_hex = availability_digest.hex() if _owned is not None else None
    body["metadata"]["availability_snapshot_ref"] = (
        availability_digest.hex() if availability_hex is None else availability_hex
    )
    for item in body["p11_items"]:
        item["availability_snapshot"]["snapshot_sha256"] = (
            availability_digest.hex() if availability_hex is None else availability_hex
        )
    for track in candidates:
        candidate.add({"recording_id": str(track.recording_id)})
    for role, name in (
        ("identity", "identity"),
        ("metadata", "metadata"),
        ("query", "request_document"),
        ("features", "request_features"),
        ("snapshot-header", "snapshot_header"),
    ):
        validate_role_v3(role, body[name], meter)
    selected_ids: list[bytes] = []
    versions = tuple(
        (component.key, component.version)
        for component in expected.pipeline.components
        if component.kind == "candidate_generator"
    )
    for rank, item in enumerate(body["p11_items"], 1):
        recording = UUID(item["recording_id"])

        def key(track: SnapshotTrack) -> int:
            meter.charge_nodes(2)
            meter.charge_bytes(16)
            return track.recording_id.int

        position = bisect_left(candidates, recording.int, key=key)
        if (
            item["rank"] != rank
            or position == len(candidates)
            or candidates[position].recording_id != recording
        ):
            raise NativeRequestV3Error("V3_ITEM_MEMBERSHIP")
        selected_ids.append(recording.bytes)
        track = candidates[position]
        if (
            item["item_provenance"]["artist_key"] != track.artist_key
            or item["item_provenance"]["release_key"] != track.release_key
        ):
            raise NativeRequestV3Error("V3_ITEM_TRACK")
        prior = -1
        for contribution in item["contributions"]:
            meter.charge_nodes(3 * len(versions))
            pair = contribution["source_key"], contribution["source_version"]
            if pair not in versions or versions.index(pair) <= prior:
                raise NativeRequestV3Error("V3_ITEM_PIPELINE")
            prior = versions.index(pair)
    ordered_ids = sorted(selected_ids)
    if any(a == b for a, b in pairwise(ordered_ids)):
        raise NativeRequestV3Error("V3_DUPLICATE_ITEM")
    identity = body["identity"]
    identity_digest = outer_digest_v3(
        "autplay.recommendation-identity.v3",
        (
            UUID(identity["owner_user_id"]).bytes,
            UUID(identity["device_id"]).bytes,
            UUID(identity["journal_epoch"]).bytes,
            UUID(identity["server_instance_id"]).bytes,
            integer_bytes(identity["identity_epoch"]),
        ),
        meter,
    )

    def small(role: str, key: str, domain: str | None) -> bytes:
        digest = lp_digest_v3(body[key], role_schema_v3(role, body[key]), meter)
        return digest if domain is None else outer_digest_v3(domain, (digest,), meter)

    origin_digest: bytes | None = None
    if type(expected.origin) is ConnectedP11InputOriginV3:
        origin = expected.origin
        origin_digest = outer_digest_v3(
            "autplay.recommendation-input-origin.v3",
            (
                expected.served_request_id.bytes,
                origin.original_request_id.bytes,
                identity_digest,
                _digest(origin.capture_sha256),
                integer_bytes(origin.consent_generation),
                _digest(origin.consent_receipt_sha256),
            ),
            meter,
        )
    p11, served = _item_sets(body, meter)
    header = _header_template(body, len(candidates), origin_digest is not None)
    header.update(
        snapshot_track_count=count,
        identity_sha256=identity_digest.hex(),
        metadata_sha256=small(
            "metadata", "metadata", "autplay.recommendation-request-metadata.v3"
        ).hex(),
        query_document_sha256=small("query", "request_document", None).hex(),
        request_features_sha256=small(
            "features", "request_features", "autplay.recommendation-request-features.v3"
        ).hex(),
        snapshot_header_sha256=small(
            "snapshot-header", "snapshot_header", "autplay.recommendation-snapshot-header.v3"
        ).hex(),
        snapshot_tracks_sha256=snapshot.finish().hex(),
        policy_tracks_sha256=policy.finish().hex(),
        availability_tracks_sha256=availability_digest.hex()
        if availability_hex is None
        else availability_hex,
        candidate_universe_sha256=candidate.finish().hex(),
        p11_items_sha256=p11.hex(),
        served_items_sha256=served.hex(),
        original_capture_binding_sha256=None if origin_digest is None else origin_digest.hex(),
    )
    validate_role_v3("root-header", header, meter)
    encoded = encode_canonical_json_lp(header, role_schema_v3("root-header", header))
    if len(encoded) > 4096:
        raise NativeRequestV3Error("V3_HEADER_BYTES")
    meter.charge_bytes(2 * len(encoded))
    meter.charge_nodes(node_count_v3(header))
    root = outer_digest_v3(
        "autplay.recommendation-request-root.v3", (sha256(encoded).digest(),), meter
    )
    meter.charge_nodes(node_count_v3(body))
    result = (
        NativeP11BodyV3.from_dict(body, meter=meter)
        if _owned is None
        else _owned.freeze_calculated(body, expected._template)
    )
    if _owned is not None:
        _owned.keep(result)
        _owned.keep(header)
    return result, header, root


def _payload_sizes(
    expected: NativeP11ExpectedValuesV3, candidates: tuple[SnapshotTrack, ...]
) -> tuple[int, int]:
    return 2048 + 80 * len(expected._template.p11_items), 16 * len(candidates) + 8 * (
        len(expected.baseline.tracks) + len(expected._template.p11_items)
    )


def build_native_p11_request_proof_v3(
    body: NativeP11BodyV3 | None = None,
    *,
    expected: NativeP11ExpectedValuesV3,
    budget: NativeRequestV3Budget,
    candidate_authority: NativeP11CandidateAuthorityV3,
) -> NativeServedRequestProofV3:
    if type(expected) is not NativeP11ExpectedValuesV3 or (
        body is not None and type(body) is not NativeP11BodyV3
    ):
        raise NativeRequestV3Error("V3_TYPED_INPUT")
    # Admit before expanding or measuring any body. Failed attempts consume
    # their provisional record; successful attempts narrow to exact envelope B.
    upper_size = min(MAX_RECORD_BYTES, MAX_PROOF_BYTES - budget.usage.raw_bytes)
    meter = budget.begin_record(
        upper_size, len(expected.baseline.tracks), len(expected._template.p11_items)
    )
    try:
        return _build_native_p11_request_proof_v3(
            body,
            expected=expected,
            meter=meter,
            candidate_authority=candidate_authority,
            upper_size=upper_size,
        )
    finally:
        meter.finish_record()


def build_admitted_native_p11_request_proof_v3(
    body: NativeP11BodyV3 | None = None,
    *,
    expected: NativeP11ExpectedValuesV3,
    meter: NativeRequestV3BuildMeterProtocol,
    candidate_authority: NativeP11CandidateAuthorityV3,
    max_bytes: int = MAX_RECORD_BYTES,
) -> NativeServedRequestProofV3:
    """Build inside an existing creation lease; never admit or close a record."""
    if type(expected) is not NativeP11ExpectedValuesV3 or (
        body is not None and type(body) is not NativeP11BodyV3
    ):
        raise NativeRequestV3Error("V3_TYPED_INPUT")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_RECORD_BYTES:
        raise NativeRequestV3Error("V3_RECORD_BYTES")
    meter.set_counts(len(expected.baseline.tracks), len(expected._template.p11_items))
    return _build_native_p11_request_proof_v3(
        body,
        expected=expected,
        meter=meter,
        candidate_authority=candidate_authority,
        upper_size=max_bytes,
    )


def _build_native_p11_request_proof_v3(
    body: NativeP11BodyV3 | None,
    *,
    expected: NativeP11ExpectedValuesV3,
    meter: NativeRequestV3BuildMeterProtocol,
    candidate_authority: NativeP11CandidateAuthorityV3 | NativeP11AdmittedCandidateAuthorityV3,
    upper_size: int,
    _owned: _OwnedOperation | None = None,
) -> NativeServedRequestProofV3:
    digests = scratch = 0
    try:
        template = (
            expected._template.to_dict()
            if _owned is None
            else _owned.projection(expected._template)
        )
        if _owned is not None:
            _owned.keep(template)
        meter.charge_nodes(node_count_v3(template))
        placeholder_header = _header_template(template, 0, type(expected.origin) is ConnectedP11InputOriginV3)
        placeholder_header["snapshot_track_count"] = len(expected.baseline.tracks)
        placeholder = _envelope(template, placeholder_header, _ZERO, expected)
        minimum_size, _ = (
            measure_native_json_v3(placeholder, meter=meter, max_bytes=upper_size)
            if _owned is None
            else _owned.measure(placeholder, upper_size)
        )
        if _owned is not None:
            _owned.retain_candidate_scratch()
        candidates = _candidates(expected, candidate_authority, meter, _owned=_owned)
        if _owned is not None:
            _clear_owned_mutable(placeholder)
            del template, placeholder
        size = minimum_size + len(str(len(candidates))) - 1
        if size > upper_size:
            raise NativeRequestV3Error("V3_RECORD_BYTES")
        meter.set_raw_bytes(size)
        required_digests, required_scratch = _payload_sizes(expected, candidates)
        if _owned is not None:
            required_scratch = _owned.transfer_candidate_scratch(required_scratch)
        meter.retain_digests(required_digests)
        digests = required_digests
        meter.retain_scratch(required_scratch)
        scratch = required_scratch
        actual, header, root = _calculate(expected, candidates, meter, _owned=_owned)
        if body is not None:
            supplied = body.to_dict()
            if _owned is not None:
                _owned.keep(supplied)
            _validate_body(supplied, meter)
            meter.charge_nodes(node_count_v3(supplied))
            if body != actual:
                raise NativeRequestV3Error("V3_BODY_EXPECTED_MISMATCH")
        output_body = actual.to_dict() if _owned is None else _owned.projection(actual)
        if _owned is not None:
            _owned.keep(output_body)
        envelope = _envelope(output_body, header, root.hex(), expected)
        document = (
            canonical_native_json_v3(envelope, meter=meter)
            if _owned is None
            else _owned.encode(envelope, upper_size)
        )
        if len(document) != size:
            raise NativeRequestV3Error("V3_ENVELOPE_SIZE")
        meter.charge_bytes(len(document))
        return NativeServedRequestProofV3(document, sha256(document).hexdigest())
    finally:
        meter.release_scratch(scratch)
        meter.release_digests(digests)


def verify_native_p11_request_proof_v3(
    proof: NativeServedRequestProofV3,
    *,
    expected: NativeP11ExpectedValuesV3,
    budget: NativeRequestV3Budget,
    candidate_authority: NativeP11CandidateAuthorityV3,
) -> VerifiedNativeP11RootV3:
    if (
        type(proof) is not NativeServedRequestProofV3
        or type(expected) is not NativeP11ExpectedValuesV3
    ):
        raise NativeRequestV3Error("V3_TYPED_INPUT")
    value, meter = preflight_native_json_v3(proof.document, budget=budget)
    try:
        return _verify_native_p11_value_v3(
            proof, value, expected=expected, meter=meter, candidate_authority=candidate_authority
        )
    finally:
        meter.finish_record()


def verify_admitted_native_p11_request_proof_v3(
    proof: NativeServedRequestProofV3,
    *,
    expected: NativeP11ExpectedValuesV3,
    meter: NativeRequestV3MeterProtocol,
    candidate_authority: NativeP11CandidateAuthorityV3,
) -> VerifiedNativeP11RootV3:
    """Use an owning caller's existing admission without creating or closing one."""
    if (
        type(proof) is not NativeServedRequestProofV3
        or type(expected) is not NativeP11ExpectedValuesV3
    ):
        raise NativeRequestV3Error("V3_TYPED_INPUT")
    value = preflight_admitted_native_json_v3(proof.document, meter=meter)
    return _verify_native_p11_value_v3(
        proof, value, expected=expected, meter=meter, candidate_authority=candidate_authority
    )


def _verify_native_p11_value_v3(
    proof: NativeServedRequestProofV3,
    value: object,
    *,
    expected: NativeP11ExpectedValuesV3,
    meter: NativeRequestV3MeterProtocol,
    candidate_authority: NativeP11CandidateAuthorityV3,
    _owned: _OwnedOperation | None = None,
) -> VerifiedNativeP11RootV3:
    digests = scratch = 0
    try:
        meter.charge_bytes(len(proof.document))
        if sha256(proof.document).hexdigest() != proof.proof_sha256:
            raise NativeRequestV3Error("V3_PROOF_DIGEST")
        version = 4 if expected._reference_snapshot else 3
        if (
            type(value) is not dict
            or value["kind"] != f"NATIVE_SERVED_REQUEST_PROOF_V{version}"
            or type(value["proof_version"]) is not int
            or value["proof_version"] != version
            or type(value["hash_format_version"]) is not int
            or value["hash_format_version"] != 3
        ):
            raise NativeRequestV3Error("V3_PROOF_FORMAT")
        if expected._reference_snapshot:
            ref = value.get("snapshot_ref")
            if ref != _baseline_reference(expected) or type(ref["track_count"]) is not int:
                raise NativeRequestV3Error("V4_SNAPSHOT_REFERENCE")
        elif "snapshot_ref" in value:
            raise NativeRequestV3Error("V3_PROOF_FORMAT")
        if len(value["body"]["snapshot_tracks"]) != len(expected._template.snapshot_tracks) or len(
            value["body"]["p11_items"]
        ) != len(expected._template.p11_items):
            raise NativeRequestV3Error("V3_EXPECTED_COUNT")
        if _owned is not None:
            _owned.retain_candidate_scratch()
        candidates = _candidates(expected, candidate_authority, meter, _owned=_owned)
        required_digests, required_scratch = _payload_sizes(expected, candidates)
        if _owned is not None:
            required_scratch = _owned.transfer_candidate_scratch(required_scratch)
        meter.retain_digests(required_digests)
        digests = required_digests
        meter.retain_scratch(required_scratch)
        scratch = required_scratch
        meter.charge_nodes(
            node_count_v3(value["body"]) + len(expected.baseline.tracks) + len(candidates)
        )
        actual, header, root = _calculate(expected, candidates, meter, _owned=_owned)
        # Strict deep frozen equality also distinguishes bool from integer via
        # preflight/role validation of the supplied object before comparison.
        supplied = value["body"]
        _validate_body(supplied, meter)
        validate_role_v3("root-header", value["root_header"], meter)
        comparison = NativeP11BodyV3.from_dict(supplied, meter=meter)
        if _owned is not None:
            _owned.keep(comparison)
        if (
            comparison != actual
            or value["root_header"] != header
            or value["root_sha256"] != root.hex()
        ):
            raise NativeRequestV3Error("V3_EXPECTED_MISMATCH")
        return VerifiedNativeP11RootV3(
            actual, frozen_object_v3(header), root.hex(), expected._created_at_us
        )
    finally:
        meter.release_scratch(scratch)
        meter.release_digests(digests)


def native_p11_decision_digest_v3(
    verified: VerifiedNativeP11RootV3,
    *,
    created_at: datetime,
    served_truth_expires_at: datetime,
    budget: NativeRequestV3FinalizationProtocol,
) -> bytes:
    if type(verified) is not VerifiedNativeP11RootV3:
        raise NativeRequestV3Error("V3_VERIFIED_TYPE")
    created, expires = _time_us(created_at), _time_us(served_truth_expires_at)
    if created != verified._created_at_us or expires != created + _HORIZON_US:
        raise NativeRequestV3Error("V3_DECISION_TIME")
    header = verified.root_header.to_dict()
    identity, metadata = verified.body.identity.to_dict(), verified.body.metadata.to_dict()
    values = (
        integer_bytes(3),
        UUID(verified.body.request_id).bytes,
        UUID(identity["owner_user_id"]).bytes,
        UUID(identity["device_id"]).bytes,
        UUID(identity["journal_epoch"]).bytes,
        UUID(identity["server_instance_id"]).bytes,
        integer_bytes(identity["identity_epoch"]),
        _digest(header["query_document_sha256"]),
        _digest(verified.root_sha256),
        metadata["surface"].encode("utf-8"),
        metadata["pipeline_key"].encode("utf-8"),
        metadata["pipeline_version"].encode("utf-8"),
        _digest(metadata["pipeline_manifest_sha256"]),
        integer_bytes(metadata["cutoff_at_ms"]),
        b"COMMITTED_P11",
        b"P11",
        None,
        integer_bytes(header["p11_item_count"]),
        _digest(header["p11_items_sha256"]),
        integer_bytes(header["served_item_count"]),
        _digest(header["served_items_sha256"]),
        integer_bytes(created),
        integer_bytes(expires),
    )
    raw = encode_serving_lp("autplay.recommendation-decision.v3", values)
    budget.charge_finalization(2 * len(raw), len(values) + 1)
    return sha256(raw).digest()


def origin_connection_seal_v3(
    verified: VerifiedNativeP11RootV3,
    decision_sha256: bytes,
    *,
    budget: NativeRequestV3FinalizationProtocol,
) -> bytes:
    if (
        type(verified) is not VerifiedNativeP11RootV3
        or type(decision_sha256) is not bytes
        or len(decision_sha256) != 32
    ):
        raise NativeRequestV3Error("V3_CONNECTION_INPUT")
    origin = verified.body.origin.to_dict()
    header = verified.root_header.to_dict()
    if (
        origin["kind"] != "ORIGINAL_CAPTURE_P11_V3"
        or header["original_capture_binding_sha256"] is None
    ):
        raise NativeRequestV3Error("V3_CONNECTION_ORIGIN")
    raw = encode_serving_lp(
        "autplay.recommendation-origin-connection.v3",
        (
            UUID(origin["original_request_id"]).bytes,
            UUID(verified.body.request_id).bytes,
            _digest(header["original_capture_binding_sha256"]),
            _digest(verified.root_sha256),
            decision_sha256,
        ),
    )
    budget.charge_finalization(2 * len(raw), 6)
    return sha256(raw).digest()


class NativeRequestV3OwnedMeterProtocol(Protocol):
    """One existing work counter and phase-neutral output owner; no quota."""

    def reserve(self, *, byte_work_bound: int, node_visit_bound: int) -> None: ...
    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None: ...
    def charge_bytes(self, n: int) -> None: ...
    def charge_nodes(self, n: int = 1) -> None: ...
    def charge_leaf(self, n: int = 1) -> None: ...
    def retain_digests(self, n: int) -> None: ...
    def release_digests(self, n: int) -> None: ...
    def retain_scratch(self, n: int) -> None: ...
    def release_scratch(self, n: int) -> None: ...
    def set_counts(self, tracks: int, items: int) -> None: ...
    def set_raw_bytes(self, raw_bytes: int) -> None: ...
    def retain_document_output(
        self,
        *,
        canonical_byte_upper: int,
        owned_payload_upper: int,
        clear_callback: Callable[[], None],
    ) -> NativeRequestV3DocumentOutputLeaseProtocol: ...


class NativeP11AdmittedCandidateScopeV3(Protocol):
    def check_current(self) -> None: ...
    def __len__(self) -> int: ...
    def __getitem__(self, index: int) -> SnapshotTrack: ...
    def __iter__(self) -> Iterator[SnapshotTrack]: ...
    def close(self) -> None: ...


class NativeP11AdmittedCandidateAuthorityV3(Protocol):
    def filter_snapshot_tracks_admitted(
        self,
        query: RecommendationQuery,
        snapshot: RecommendationInputSnapshot,
        ordered_tracks: Sequence[SnapshotTrack],
        *,
        meter: NativeRequestV3ExpectedMeterProtocol,
    ) -> NativeP11AdmittedCandidateScopeV3: ...


def _clear_owned_mutable(value: object) -> None:
    if type(value) is dict:
        for child in value.values():
            _clear_owned_mutable(child)
        value.clear()
    elif type(value) is list:
        for child in value:
            _clear_owned_mutable(child)
        value.clear()


class _OwnedCleanupIncompleteV3(Exception):
    """Bounded private control failure; never a caller exception graph."""


def _clear_owned_factory_error_v3(
    error: BaseException,
    meter: NativeRequestV3OwnedMeterProtocol,
) -> bool:
    """Scrub a bounded cause/context closure, never caller or paused frames.

    False requires retained-output poisoning BEFORE any affected refund. The
    Exact-capacity reference arrays avoid unbounded DFS and resize copies.
    448 pointer slots + three headers <4096B; allocation AND final clearing
    are prepaid8192B before allocation under the existing8192B live scratch.
    """
    trace_member = BaseException.__dict__["__traceback__"]
    cause_member = BaseException.__dict__["__cause__"]
    context_member = BaseException.__dict__["__context__"]
    pending: list[BaseException | None] | None = None
    exceptions: list[BaseException | None] | None = None
    frames: list[Any] | None = None
    current: Any = None
    related: Any = None
    trace: Any = None
    frame: Any = None
    known: Any = None
    scratch = False
    complete = False
    executing: Any = sys._getframe(1)
    try:
        meter.reserve(byte_work_bound=2_097_152, node_visit_bound=100_000)
        meter.retain_scratch(8192)
        scratch = True
        meter.charge(bytes_read=8192, bytes_written=8192, node_visits=1024)
        pending, exceptions, frames = [None] * 128, [None] * 64, [None] * 256
        pending[0] = error
        pending_count, exception_count, frame_count = 1, 0, 0
        visits = 0
        while pending_count:
            pending_count -= 1
            current = pending[pending_count]
            meter.charge(bytes_written=16, node_visits=1)
            pending[pending_count] = None
            duplicate = False
            for index in range(exception_count):
                meter.charge(bytes_read=16, node_visits=1)
                known = exceptions[index]
                if current is known:
                    duplicate = True
                    break
            if duplicate:
                continue
            if exception_count >= 64:
                raise _OwnedCleanupIncompleteV3
            meter.charge(bytes_written=16, node_visits=1)
            exceptions[exception_count] = current
            exception_count += 1
            related = cause_member.__get__(current, BaseException)
            if related is not None:
                if pending_count >= 128:
                    raise _OwnedCleanupIncompleteV3 from None
                meter.charge(bytes_written=16, node_visits=1)
                pending[pending_count] = related
                pending_count += 1
            related = context_member.__get__(current, BaseException)
            if related is not None:
                if pending_count >= 128:
                    raise _OwnedCleanupIncompleteV3
                meter.charge(bytes_written=16, node_visits=1)
                pending[pending_count] = related
                pending_count += 1
            trace = trace_member.__get__(current, BaseException)
            while trace is not None:
                if visits >= 256:
                    raise _OwnedCleanupIncompleteV3
                visits += 1
                meter.charge(bytes_read=64, node_visits=1)
                frame = trace.tb_frame
                trace = trace.tb_next
                duplicate = False
                for index in range(frame_count):
                    meter.charge(bytes_read=16, node_visits=1)
                    known = frames[index]
                    if frame is known:
                        duplicate = True
                        break
                if not duplicate:
                    meter.charge(bytes_written=16, node_visits=1)
                    frames[frame_count] = frame
                    frame_count += 1
        # Discover the COMPLETE bounded closure before changing any frame.
        for index in range(frame_count):
            frame = frames[index]
            meter.charge(bytes_read=64, node_visits=1)
            if frame is executing or (
                frame.f_globals is not globals()
                and frame.f_globals is not _verify_native_original_inputs_v3.__globals__
            ):
                continue
            if frame.f_code.co_flags & (0x20 | 0x80 | 0x200) and frame.f_generator is not None:
                # Never close a paused owned generator and its active charges.
                raise _OwnedCleanupIncompleteV3
            try:
                frame.clear()
            except RuntimeError:
                # Known active public wrappers scrub their own locals before
                # their finally/refund. Unknown executing code is fail closed.
                if frame.f_code.co_name not in (
                    "from_admitted",
                    "_from_original_borrow",
                    "from_admitted_document",
                    "build_owned_admitted_native_p11_request_proof_v3",
                    "verify_owned_admitted_native_p11_request_proof_v3",
                    "_verify_owned_streamed",
                    "_owned_finalization",
                    "decision_digest",
                    "connection_seal",
                    "attest_owned_native_p11_completion_v3",
                    "_attest_owned_content_v3",
                ):
                    raise _OwnedCleanupIncompleteV3 from None
        for index in range(exception_count):
            current = exceptions[index]
            meter.charge(bytes_written=24, node_visits=1)
            trace_member.__set__(current, None)
            cause_member.__set__(current, None)
            context_member.__set__(current, None)
        complete = True
    except BaseException:
        complete = False
    finally:
        if pending is not None:
            pending.clear()
        if exceptions is not None:
            exceptions.clear()
        if frames is not None:
            frames.clear()
        current = related = trace = frame = known = executing = None
        if scratch:
            try:
                meter.release_scratch(8192)
            except BaseException:
                # No refund is safe until the affected owner is held/poisoned.
                complete = False
    return complete


class _OwnedGraph:
    """Private graph slot: the accounting callback really drops the graph."""

    def __init__(
        self, meter: NativeRequestV3OwnedMeterProtocol, *, canonical: int, footprint: int
    ) -> None:
        self._meter = meter
        self._value: Any = None
        self._dependents = 0
        self._parents: list[_OwnedGraph] = []
        self._closed = False
        self._cleanup_failed = False
        self._facade: object | None = None
        self._track_index: tuple[tuple[int, int, SnapshotTrack], ...] | None = None
        self._original_borrow: NativeImmutableBaselineBorrowV3 | None = None
        self._lease = meter.retain_document_output(
            canonical_byte_upper=canonical,
            owned_payload_upper=footprint,
            clear_callback=self._clear,
        )

    def _retain_failed_cleanup(self) -> None:
        self._cleanup_failed = True
        self._lease.close()

    def _clear(self) -> None:
        if self._cleanup_failed:
            raise NativeRequestV3Error("V3_FAILURE_CLEANUP_RETAINED")
        if self._dependents:
            raise NativeRequestV3Error("V3_OWNED_BORROW_ACTIVE")
        _clear_owned_mutable(self._value)
        self._value = None
        self._facade = None
        self._track_index = None
        self._original_borrow = None
        for parent in self._parents:
            parent._dependents -= 1
        self._parents.clear()
        self._closed = True

    def check(self, meter: NativeRequestV3OwnedMeterProtocol | None = None) -> None:
        self._lease.check_current()
        if self._closed or (meter is not None and meter is not self._meter):
            raise NativeRequestV3Error("V3_OWNED_STATE")
        if self._original_borrow is not None:
            self._original_borrow.check_current()
        for parent in self._parents:
            parent.check()

    def borrow(self, parent: _OwnedGraph) -> None:
        self.check(parent._meter)
        parent.check()
        parent._dependents += 1
        self._parents.append(parent)

    def close(self) -> None:
        if self._closed:
            return
        self.check()
        # Check before entering a ledger callback: this is an ordinary lifetime
        # ordering error, not a failed clearing callback which poisons the parent.
        if self._dependents:
            raise NativeRequestV3Error("V3_OWNED_BORROW_ACTIVE")
        self._lease.close()


def _owned_footprint(
    value: object, meter: NativeRequestV3OwnedMeterProtocol, depth: int = 0
) -> int:
    """Conservative owned-container/string bound, independently from wire size.

    A scalar reference which would be borrowed is conservatively paid again.
    No dictionary/list/frozen projection is materialized by this traversal.
    """
    meter.charge(node_visits=1)
    if depth > 12:
        raise NativeRequestV3Error("V3_DEPTH")
    if value is None or type(value) in (bool, int, float, UUID, datetime):
        return 128
    if type(value) is str:
        if len(value) > 4096:
            raise NativeRequestV3Error("V3_ROLE_TEXT")
        meter.charge(bytes_read=4 * len(value))
        return 96 + 4 * len(value)
    if type(value) in (list, tuple):
        sequence = cast(list[object] | tuple[object, ...], value)
        if len(sequence) > 5000:
            raise NativeRequestV3Error("V3_ARRAY_COUNT")
        return (
            128
            + 16 * len(sequence)
            + sum(_owned_footprint(child, meter, depth + 1) for child in sequence)
        )
    if type(value) is dict:
        if len(value) > 4096 or any(type(key) is not str for key in value):
            raise NativeRequestV3Error("V3_ROLE_KEYS")
        return (
            256
            + 128 * len(value)
            + sum(
                _owned_footprint(key, meter, depth + 1) + _owned_footprint(child, meter, depth + 1)
                for key, child in value.items()
            )
        )
    if type(value) is FrozenObjectV3:
        return (
            256
            + 128 * len(value.members)
            + sum(
                _owned_footprint(key, meter, depth + 1) + _owned_footprint(child, meter, depth + 1)
                for key, child in value.members
            )
        )
    raise NativeRequestV3Error("V3_OWNED_INPUT")


def _body_footprint(body: NativeP11BodyV3, meter: NativeRequestV3OwnedMeterProtocol) -> int:
    return 2048 + sum(
        _new_mutable_containers(getattr(body, field.name), meter)
        for field in fields(NativeP11BodyV3)
        if field.name != "snapshot_tracks"
    )


class _ExpectedFactoryAccounting:
    def __init__(self, meter: NativeRequestV3OwnedMeterProtocol) -> None:
        self.meter = meter
        self.track_bytes = 0
        self.pending_scratch = 0
        self.sorted_tracks: tuple[tuple[int, int, SnapshotTrack], ...] = ()

    def _begin_leaf(self, footprint: int) -> None:
        if self.pending_scratch or footprint > 65_536:
            raise NativeRequestV3Error("V3_OWNED_LEAF")
        self.meter.retain_scratch(65_536)
        self.pending_scratch = 65_536
        self.meter.charge(bytes_written=footprint, node_visits=footprint // 8 + 1)

    def begin_item(self, item: Any) -> None:
        footprint = 8192 + _owned_footprint(item.reason_codes, self.meter)
        for contribution in item.contributions:
            footprint += 2048 + _owned_footprint(contribution.provenance, self.meter)
        self._begin_leaf(footprint)

    def copy_provenance(self, value: Any) -> Any:
        if type(value) is dict:
            return {key: self.copy_provenance(child) for key, child in value.items()}
        if type(value) in (tuple, list):
            return [self.copy_provenance(child) for child in value]
        return value

    def body(self, *values: Any) -> NativeP11BodyV3:
        # All fragments are immutable and individually bounded. Check the exact
        # whole canonical body BEFORE allocating its final aggregate carrier.
        body_fields = fields(NativeP11BodyV3)
        width = 2 + len(body_fields) - 1
        for field, value in zip(body_fields, values, strict=True):
            width += len(field.name) + 3 + bound_frozen_v3(value)
        if width > MAX_RECORD_BYTES:
            raise NativeRequestV3Error("V3_RECORD_BYTES")
        self.meter.charge(node_visits=len(body_fields), bytes_written=256)
        return NativeP11BodyV3(*values)

    def seal_leaf(self, document: dict[str, Any]) -> FrozenObjectV3:
        # One mutable leaf only. The frozen result is paid by the final output;
        # its source dictionaries are a separately retained <=64KiB scope.
        width, nodes = measure_native_json_v3(document, meter=self.meter, max_bytes=65_536)
        footprint = _owned_footprint(document, self.meter)
        if footprint > 65_536:
            raise NativeRequestV3Error("V3_OWNED_LEAF")
        if footprint > self.pending_scratch:
            raise NativeRequestV3Error("V3_OWNED_LEAF")
        try:
            self.meter.charge(bytes_written=width, node_visits=nodes)
            return frozen_object_v3(document)
        finally:
            _clear_owned_mutable(document)
            self.meter.release_scratch(self.pending_scratch)
            self.pending_scratch = 0

    def seal_tracks(
        self, baseline: RecommendationInputSnapshot, *, reference_snapshot: bool
    ) -> tuple[FrozenObjectV3, ...]:
        # The original decoder's exact immutable baseline can be borrowed after
        # every value has passed the same role checks, without cloning T tracks.
        if type(baseline.retained_until.tzinfo) is not type(UTC):
            raise NativeRequestV3Error("V3_EXPECTED_SNAPSHOT")
        scratch = 16 * len(baseline.tracks) + 1024
        self.meter.retain_scratch(scratch)
        try:
            self.meter.charge(
                bytes_written=16 * len(baseline.tracks),
                node_visits=1 + len(baseline.tracks) * len(baseline.tracks).bit_length(),
            )
            tracks = sorted(
                (_uuid(track.recording_id).int, ordinal, track)
                for ordinal, track in enumerate(baseline.tracks)
            )
            if any(left[0] == right[0] for left, right in pairwise(tracks)):
                raise NativeRequestV3Error("V3_DUPLICATE_TRACK")
            self.sorted_tracks = tuple(tracks)
            tracks.clear()
        finally:
            self.meter.release_scratch(scratch)
        if reference_snapshot:
            return ()
        pointers = 8 * len(baseline.tracks) + 128
        self.meter.retain_scratch(pointers)
        try:
            sealed: list[FrozenObjectV3] = []
            for track in baseline.tracks:
                footprint = 4096 + sum(
                    _owned_footprint(getattr(track, field.name), self.meter)
                    for field in fields(SnapshotTrack)
                )
                self._begin_leaf(footprint)
                document = _track_document(track)
                frozen = self.seal_leaf(document)
                self.track_bytes += bound_frozen_v3(frozen)
                if self.track_bytes > MAX_RECORD_BYTES:
                    raise NativeRequestV3Error("V3_RECORD_BYTES")
                sealed.append(frozen)
            return tuple(sealed)
        finally:
            self.meter.release_scratch(pointers)


class NativeP11ExpectedLeaseV3:
    """Checked facade. Exact legacy expected values never leave this module."""

    __slots__ = ("_graph",)
    _graph: _OwnedGraph

    def __init__(self) -> None:
        raise NativeRequestV3Error("V3_PRIVATE_OWNER")

    @classmethod
    def from_admitted(
        cls,
        served_request_id: UUID,
        identity: NativeServedIdentity,
        query: RecommendationQuery,
        baseline: RecommendationInputSnapshot,
        actual_p11_response: RecommendationResponse,
        pipeline: PipelineDefinition,
        origin: StandaloneP11OriginV3 | ConnectedP11InputOriginV3,
        *,
        meter: NativeRequestV3OwnedMeterProtocol,
    ) -> NativeP11ExpectedLeaseV3:
        # Issued original ownership is checked before any baseline traversal.
        original_borrow: NativeImmutableBaselineBorrowV3 | None = None
        try:
            original_borrow = borrow_native_immutable_baseline_v3(baseline, meter=meter)
            _verify_native_original_inputs_v3(original_borrow, query, actual_p11_response, pipeline)
            return cls._from_original_borrow(
                served_request_id,
                identity,
                query,
                baseline,
                actual_p11_response,
                pipeline,
                origin,
                meter=meter,
                original_borrow=original_borrow,
            )
        except BaseException as error:
            if not _clear_owned_factory_error_v3(error, meter):
                if original_borrow is not None:
                    original_borrow._retain_failed_cleanup()
                _retain_native_original_failure_v3(baseline, meter=meter)
            del served_request_id, identity, query, baseline, actual_p11_response
            del pipeline, origin, meter
            if original_borrow is not None:
                original_borrow.close()
            del original_borrow
            raise error from None

    @classmethod
    def _from_original_borrow(
        cls,
        served_request_id: UUID,
        identity: NativeServedIdentity,
        query: RecommendationQuery,
        baseline: RecommendationInputSnapshot,
        actual_p11_response: RecommendationResponse,
        pipeline: PipelineDefinition,
        origin: StandaloneP11OriginV3 | ConnectedP11InputOriginV3,
        *,
        meter: NativeRequestV3OwnedMeterProtocol,
        original_borrow: NativeImmutableBaselineBorrowV3,
    ) -> NativeP11ExpectedLeaseV3:
        graph: _OwnedGraph | None = None
        accounting: _ExpectedFactoryAccounting | None = None
        track: SnapshotTrack | None = None
        item: Any = None
        contribution: Any = None
        expected: NativeP11ExpectedValuesV3 | None = None
        result: NativeP11ExpectedLeaseV3 | None = None
        try:
            original_borrow.check_current()
            _verify_native_original_inputs_v3(original_borrow, query, actual_p11_response, pipeline)
            if (
                type(baseline) is not RecommendationInputSnapshot
                or type(baseline.tracks) is not tuple
            ):
                raise NativeRequestV3Error("V3_EXPECTED_SNAPSHOT")
            if (
                type(actual_p11_response) is not RecommendationResponse
                or type(actual_p11_response.items) is not tuple
            ):
                raise NativeRequestV3Error("V3_EXPECTED_RESPONSE")
            if len(baseline.tracks) > 5000 or len(actual_p11_response.items) > 100:
                raise NativeRequestV3Error("V3_EXPECTED_COUNT")
            if type(pipeline) is not PipelineDefinition or len(pipeline.components) > 4096:
                raise NativeRequestV3Error("V3_EXPECTED_PIPELINE")
            meter.charge(node_visits=16 * len(pipeline.components) + 16)
            _pipeline_immutable(pipeline)
            label_characters = 0
            for kind in ("candidate_generator", "filter", "reranker"):
                label_length = sum(
                    len(c.key) + len(c.version) + len(c.config_sha256) + 3
                    for c in pipeline.components
                    if c.kind == kind
                )
                if label_length > 4096:
                    raise NativeRequestV3Error("V3_ROLE_TEXT")
                label_characters += label_length
            model_characters = len(pipeline.pipeline_key) + len(pipeline.version) + 1
            if model_characters > 4096:
                raise NativeRequestV3Error("V3_ROLE_TEXT")
            # Bound final frozen containers and all generated UUID/label strings
            # BEFORE constructing any mutable track/item dictionary.
            # New frozen containers and generated UUID/label strings only. Role
            # keys and deeply validated immutable baseline scalar objects are
            # borrowed, not allocated again by frozen_object_v3.
            footprint = 16_384 + 4 * (label_characters + model_characters)
            reference_snapshot = original_borrow.reference_snapshot
            footprint += 48 + (0 if reference_snapshot else 8 * len(baseline.tracks))
            footprint += 48 + 112 * len(baseline.tracks)  # Paid immutable UUID-sorted source index.
            for track in baseline.tracks:
                if type(track) is not SnapshotTrack:
                    raise NativeRequestV3Error("V3_EXPECTED_TRACK")
                if type(track.metadata_tokens) is not tuple:
                    raise NativeRequestV3Error("V3_EXPECTED_TRACK")
                if not reference_snapshot:
                    footprint += 48 + 48 + 72 * len(fields(SnapshotTrack))
                    footprint += 48 + 8 * len(track.metadata_tokens)
                    footprint += 96 * (1 + int(track.user_track_ref_id is not None))
                # Traverse original scalar values for bounds/work before borrowing;
                # their string payload belongs to the original decoder owner.
                for field in fields(SnapshotTrack):
                    _owned_footprint(getattr(track, field.name), meter)
            for item in actual_p11_response.items:
                if (
                    type(item.contributions) is not tuple
                    or type(item.reason_codes) is not tuple
                    or len(item.contributions) > 4096
                    or len(item.reason_codes) > 4096
                ):
                    raise NativeRequestV3Error("V3_EXPECTED_ITEM")
                footprint += 8192 + _owned_footprint(item.reason_codes, meter)
                for contribution in item.contributions:
                    footprint += 4096 + _owned_footprint(contribution.provenance, meter)
            graph = _OwnedGraph(meter, canonical=MAX_RECORD_BYTES, footprint=footprint)
            graph._original_borrow = original_borrow
            accounting = _ExpectedFactoryAccounting(meter)
            meter.charge(bytes_written=footprint, node_visits=footprint // 8 + 1)
            # Legitimate internal construction of the exact class; callers never
            # receive an uninitialized object or a private-field write seam.
            expected = NativeP11ExpectedValuesV3.__new__(NativeP11ExpectedValuesV3)
            graph._value = expected
            expected._initialize(
                served_request_id,
                identity,
                query,
                baseline,
                actual_p11_response,
                pipeline,
                origin,
                _owned=accounting,
                reference_snapshot=reference_snapshot,
            )
            graph._track_index = accounting.sorted_tracks
            result = cls.__new__(cls)
            result._graph = graph
            graph._facade = result
            return result
        except BaseException as error:
            # Unwound initializer/freeze frames can own projections, sorted
            # source indexes, track aliases and scalar/tuple borrows. Clear the
            # actual frames before dropping their output reservations.
            if not _clear_owned_factory_error_v3(error, meter):
                if graph is not None:
                    graph._retain_failed_cleanup()
                original_borrow._retain_failed_cleanup()
            track = item = contribution = expected = result = None
            del served_request_id, identity, query, baseline, actual_p11_response
            del pipeline, origin
            if accounting is not None:
                accounting.sorted_tracks = ()
                if accounting.pending_scratch:
                    meter.release_scratch(accounting.pending_scratch)
                    accounting.pending_scratch = 0
            if graph is not None:
                graph.close()
            del graph, accounting, original_borrow, meter
            raise error from None

    def check_current(self) -> None:
        _check_owned_facade(self._graph, self)

    @property
    def snapshot_track_count(self) -> int:
        self.check_current()
        return len(self._graph._value.baseline.tracks)

    @property
    def p11_item_count(self) -> int:
        self.check_current()
        return len(self._graph._value._template.p11_items)

    @property
    def created_at_us(self) -> int:
        self.check_current()
        return cast(int, self._graph._value._created_at_us)

    def close(self) -> None:
        if self._graph._closed:
            return
        self.check_current()
        original_borrow = self._graph._original_borrow
        self._graph.close()
        if original_borrow is not None:
            original_borrow.close()


def _borrow_owned_expected(
    expected: NativeP11ExpectedLeaseV3,
    meter: NativeRequestV3OwnedMeterProtocol,
) -> NativeP11ExpectedValuesV3:
    if type(expected) is not NativeP11ExpectedLeaseV3:
        raise NativeRequestV3Error("V3_TYPED_INPUT")
    expected.check_current()
    expected._graph.check(meter)
    return cast(NativeP11ExpectedValuesV3, expected._graph._value)


class _OwnedOperation:
    """Conservative aggregate of independently bounded simultaneous documents.

    This first staged branch deliberately keeps conservative output reservations
    through result close. It makes no near-cap acceptance claim.
    """

    def __init__(
        self,
        expected: NativeP11ExpectedLeaseV3,
        meter: NativeRequestV3OwnedMeterProtocol,
        raw_upper: int,
        input_footprint: int = 0,
        *,
        encoding: bool = True,
    ) -> None:
        graph: _OwnedGraph | None = None
        value: NativeP11ExpectedValuesV3 | None = None
        try:
            value = _borrow_owned_expected(expected, meter)
            self.meter = meter
            self.tracks = len(value.baseline.tracks)
            footprint = _body_footprint(value._template, meter)
            # Thaw/freeze/encoding payloads overlap; retain them independently.
            aggregate = (
                5 * footprint + 2 * input_footprint + (2 * raw_upper if encoding else 0) + 65_536
            )
            graph = _OwnedGraph(meter, canonical=MAX_RECORD_BYTES, footprint=aggregate)
            self.graph = graph
            self.graph._value = []
            self.candidate_scratch = 0
            self.graph.borrow(expected._graph)
            self.graph._track_index = expected._graph._track_index
            self.graph._lease.retain_new_digests(1280)
            # These conservative copy visits/writes are consumed before any
            # thaw, hydration, freeze or encoder buffer allocation. Existing
            # kernel charges still consume the same meter; no prepayment reset.
            meter.charge(bytes_written=aggregate, node_visits=aggregate // 8 + 1)
        except BaseException as error:
            # Never detach a saved external cause before the bounded walk.
            # The exact expected owner anchors failures before graph exists.
            if not _clear_owned_factory_error_v3(error, meter):
                (graph or expected._graph)._retain_failed_cleanup()
            value = None
            del expected, raw_upper, input_footprint, encoding
            if graph is not None:
                graph.close()
            graph = None
            del meter
            raise error from None

    def keep(self, value: object) -> None:
        self.graph._value.append(value)

    def reference_tracks(self, baseline: RecommendationInputSnapshot) -> Iterator[dict[str, Any]]:
        # Only the current projection is owned. The decoder retains the immutable source.
        for track in baseline.tracks:
            footprint = 4096 + sum(
                _owned_footprint(getattr(track, field.name), self.meter)
                for field in fields(SnapshotTrack)
            )
            if footprint > 65_536:
                raise NativeRequestV3Error("V3_OWNED_LEAF")
            self.meter.retain_scratch(footprint)
            leaf = None
            try:
                self.meter.charge(bytes_written=footprint, node_visits=footprint // 8 + 1)
                leaf = _track_document(track)
                yield leaf
            finally:
                _clear_owned_mutable(leaf)
                self.meter.release_scratch(footprint)

    def source_index(self) -> _SortedTrackIndexView:
        self.graph.check()
        if self.graph._track_index is None:
            raise NativeRequestV3Error("V3_OWNED_INDEX")
        return _SortedTrackIndexView(self.graph._track_index, self.graph)

    def projection(self, body: NativeP11BodyV3) -> dict[str, Any]:
        return _stream_body_projection(body, self.meter)

    def freeze_calculated(self, body: dict[str, Any], template: NativeP11BodyV3) -> NativeP11BodyV3:
        return _freeze_stream_calculated(body, template, self.meter)

    def measure(self, value: dict[str, Any], maximum: int) -> tuple[int, int]:
        encoder = _StreamEncoder(self.meter, maximum, retain=False)
        encoder.encode(value)
        return encoder.size, encoder.nodes

    def encode(self, value: dict[str, Any], maximum: int) -> bytes:
        encoder = _StreamEncoder(self.meter, maximum, retain=True)
        encoder.encode(value)
        self.meter.charge_bytes(encoder.size)
        return bytes(encoder.output)

    @classmethod
    def for_document(cls, meter: NativeRequestV3OwnedMeterProtocol, length: int) -> _OwnedOperation:
        result = cls.__new__(cls)
        result.meter, result.tracks, result.candidate_scratch = meter, 0, 0
        result.graph = _OwnedGraph(meter, canonical=length, footprint=length + 512)
        result.graph._value = []
        return result

    def retain_candidate_scratch(self) -> None:
        if self.candidate_scratch:
            raise NativeRequestV3Error("V3_OWNED_SCRATCH")
        # cmp_to_key allocates comparator wrappers, source/result lists, and
        # sort workspace, all before _candidates can call the actual filter.
        required = 1024
        self.meter.retain_scratch(required)
        self.candidate_scratch = required

    def reserve_candidate_sort(self, count: int) -> None:
        required = 80 * count + 1024
        if required > self.candidate_scratch:
            self.meter.retain_scratch(required - self.candidate_scratch)
            self.candidate_scratch = required

    def transfer_candidate_scratch(self, required: int) -> int:
        if required > self.candidate_scratch:
            self.meter.retain_scratch(required - self.candidate_scratch)
        elif required < self.candidate_scratch:
            self.meter.release_scratch(self.candidate_scratch - required)
        self.candidate_scratch = 0
        # Common helper will retain this after the construction reservation is
        # removed. No allocations occur in the exact transfer gap.
        self.meter.release_scratch(required)
        return required

    def close(self) -> None:
        if self.candidate_scratch:
            self.meter.release_scratch(self.candidate_scratch)
            self.candidate_scratch = 0
        self.graph.close()


def _raw_hydration_footprint(raw: bytes, meter: NativeRequestV3OwnedMeterProtocol) -> int:
    """No hydration: conservatively count lexical container/string allocation.

    The unchanged closed scanner subsequently rejects syntax/role/depth faults
    before json.loads. Escaped text is paid at four times its raw token width,
    keys are counted again in dict slots, and every scalar gets an object bound.
    """
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RECORD_BYTES:
        raise NativeRequestV3Error("V3_RECORD_BYTES")
    meter.charge(bytes_read=len(raw), node_visits=len(raw))
    total, in_string, escaped, scalar = 0, False, False, False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
                total += 4
            elif byte == 92:
                escaped = True
                total += 4
            elif byte == 34:
                in_string = False
            else:
                total += 4
        elif byte == 34:
            in_string, scalar = True, False
            total += 96
        elif byte == 123:
            scalar = False
            total += 256
        elif byte == 91 or byte == 58:
            scalar = False
            total += 128
        elif byte == 44:
            scalar = False
            total += 16
        elif byte in (125, 93, 32, 9, 10, 13):
            scalar = False
        elif not scalar:
            scalar = True
            total += 128
    return total + 2048


class NativeP11ProofLeaseV3:
    """Owned proof, with explicit trusted-driver borrow; bytes are not revocable."""

    __slots__ = ("_operation",)
    _operation: _OwnedOperation

    def __init__(self) -> None:
        raise NativeRequestV3Error("V3_PRIVATE_OWNER")

    @classmethod
    def from_admitted_document(
        cls,
        document: bytes,
        *,
        expected_sha256: bytes,
        expected: NativeP11ExpectedLeaseV3,
        meter: NativeRequestV3OwnedMeterProtocol,
    ) -> NativeP11ProofLeaseV3:
        """Retain existing admitted bytes; no SQL/input admission is granted.

        The guarded reader already owns the permanent input and its actual
        bound digest. This facade conservatively pays the retained payload
        again, drops only its reference on close, and leaves permanent-input
        accounting intact. The verifier independently hashes every byte.
        """
        operation: _OwnedOperation | None = None
        result: NativeP11ProofLeaseV3 | None = None
        anchored = False
        try:
            # The actual expected owner anchors failures even before a proof
            # output can be allocated. It grants no permanent-input authority.
            if type(expected) is not NativeP11ExpectedLeaseV3:
                raise NativeRequestV3Error("V3_TYPED_INPUT")
            expected.check_current()
            expected._graph.check(meter)
            anchored = True
            if type(document) is not bytes or not 1 <= len(document) <= MAX_RECORD_BYTES:
                raise NativeRequestV3Error("V3_RECORD_BYTES")
            if type(expected_sha256) is not bytes or len(expected_sha256) != 32:
                raise NativeRequestV3Error("V3_DIGEST")
            operation = _OwnedOperation.for_document(meter, len(document))
            operation.graph.borrow(expected._graph)
            operation.graph._lease.retain_new_digests(96)
            meter.charge(bytes_written=64, node_visits=16)
            operation.keep(NativeServedRequestProofV3(document, expected_sha256.hex()))
            result = cls.__new__(cls)
            result._operation = operation
            operation.graph._facade = result
            return result
        except BaseException as error:
            if not anchored:
                # No owned allocation or payload traversal preceded this
                # refusal. Do not poison another admission's expected owner.
                del document, expected_sha256, expected, meter
                raise error from None
            if not _clear_owned_factory_error_v3(error, meter):
                if operation is not None:
                    operation.graph._retain_failed_cleanup()
                if type(expected) is NativeP11ExpectedLeaseV3:
                    expected._graph._retain_failed_cleanup()
                raise NativeRequestV3Error("V3_FAILURE_CLEANUP_RETAINED") from None
            result = None
            del document, expected_sha256, expected
            if operation is not None:
                operation.close()
            operation = None
            del meter
            raise error from None

    @property
    def _proof(self) -> NativeServedRequestProofV3:
        return cast(NativeServedRequestProofV3, self._operation.graph._value[-1])

    def check_current(self) -> None:
        _check_owned_facade(self._operation.graph, self)

    @property
    def byte_length(self) -> int:
        self.check_current()
        return len(self._proof.document)

    def proof_digest_equals(self, expected_raw32: bytes) -> bool:
        self.check_current()
        if type(expected_raw32) is not bytes or len(expected_raw32) != 32:
            raise NativeRequestV3Error("V3_DIGEST")
        return _owned_digest_equals(self._proof.proof_sha256, expected_raw32, self._operation.meter)

    @contextmanager
    def borrow_document(self) -> Iterator[bytes]:
        """Trusted driver borrows the existing bytes while this owner stays open.

        A consumer retaining/copying these bytes must first transfer ownership
        to an admitted permanent input or pay a separate output copy lease.
        """
        self.check_current()
        self._operation.graph._dependents += 1
        try:
            yield self._proof.document
        finally:
            self._operation.graph._dependents -= 1

    def close(self) -> None:
        if self._operation.graph._closed:
            return
        self.check_current()
        if self._operation.graph._dependents:
            raise NativeRequestV3Error("V3_OWNED_BORROW_ACTIVE")
        self._operation.close()


class NativeP11VerifiedLeaseV3:
    """Guarded verified result. No naked frozen role or legacy value escapes."""

    __slots__ = ("_expected_owner", "_operation", "_proof_owner")
    _operation: _OwnedOperation
    _proof_owner: NativeP11ProofLeaseV3
    _expected_owner: NativeP11ExpectedLeaseV3

    def __init__(self) -> None:
        raise NativeRequestV3Error("V3_PRIVATE_OWNER")

    @property
    def _verified(self) -> VerifiedNativeP11RootV3:
        return cast(VerifiedNativeP11RootV3, self._operation.graph._value[-1])

    def check_current(self) -> None:
        _check_owned_facade(self._operation.graph, self)

    @property
    def p11_item_count(self) -> int:
        self.check_current()
        return len(self._verified.body.p11_items)

    @property
    def created_at_us(self) -> int:
        self.check_current()
        return self._verified._created_at_us

    def root_digest_equals(self, expected_raw32: bytes) -> bool:
        self.check_current()
        if type(expected_raw32) is not bytes or len(expected_raw32) != 32:
            raise NativeRequestV3Error("V3_DIGEST")
        return _owned_digest_equals(
            self._verified.root_sha256, expected_raw32, self._operation.meter
        )

    @contextmanager
    def item_projection(self, ordinal: int) -> Iterator[dict[str, object]]:
        self.check_current()
        if type(ordinal) is not int or not 0 <= ordinal < len(self._verified.body.p11_items):
            raise NativeRequestV3Error("V3_ITEM_ORDINAL")
        frozen = self._verified.body.p11_items[ordinal]
        scope: _OwnedGraph | None = None
        try:
            footprint = _owned_footprint(frozen, self._operation.meter)
            scope = _OwnedGraph(
                self._operation.meter, canonical=bound_frozen_v3(frozen), footprint=footprint
            )
            scope.borrow(self._operation.graph)
            self._operation.meter.charge(bytes_written=footprint, node_visits=footprint // 8 + 1)
            scope._value = frozen.to_dict()
            yield scope._value
        except BaseException as error:
            if not _clear_owned_factory_error_v3(error, self._operation.meter):
                (scope or self._operation.graph)._retain_failed_cleanup()
            del frozen
            raise error from None
        finally:
            if scope is not None:
                scope.close()

    @contextmanager
    def header_projection(self) -> Iterator[dict[str, object]]:
        self.check_current()
        frozen = self._verified.root_header
        scope: _OwnedGraph | None = None
        try:
            footprint = _owned_footprint(frozen, self._operation.meter)
            scope = _OwnedGraph(
                self._operation.meter, canonical=bound_frozen_v3(frozen), footprint=footprint
            )
            scope.borrow(self._operation.graph)
            self._operation.meter.charge(bytes_written=footprint, node_visits=footprint // 8 + 1)
            scope._value = frozen.to_dict()
            yield scope._value
        except BaseException as error:
            if not _clear_owned_factory_error_v3(error, self._operation.meter):
                (scope or self._operation.graph)._retain_failed_cleanup()
            del frozen
            raise error from None
        finally:
            if scope is not None:
                scope.close()

    def close(self) -> None:
        if self._operation.graph._closed:
            return
        self.check_current()
        if self._operation.graph._dependents:
            raise NativeRequestV3Error("V3_OWNED_BORROW_ACTIVE")
        self._operation.close()

    def decision_digest(
        self, *, created_at: datetime, served_truth_expires_at: datetime
    ) -> NativeP11DigestLeaseV3:
        self.check_current()
        return _owned_finalization(
            self,
            None,
            created_at=created_at,
            served_truth_expires_at=served_truth_expires_at,
        )

    def connection_seal(self, decision: NativeP11DigestLeaseV3) -> NativeP11DigestLeaseV3:
        self.check_current()
        return _owned_finalization(self, decision)


def _owned_digest_equals(stored: str, raw: bytes, meter: NativeRequestV3OwnedMeterProtocol) -> bool:
    meter.retain_digests(64)
    try:
        meter.charge(bytes_written=64, node_visits=32)
        return stored == raw.hex()
    finally:
        meter.release_digests(64)


class NativeP11DigestLeaseV3:
    """One derived raw32 receipt; no naked bytes property."""

    __slots__ = ("_graph", "_identity", "_kind", "_producer")
    _graph: _OwnedGraph
    _identity: int
    _kind: str
    _producer: NativeP11VerifiedLeaseV3

    def __init__(self) -> None:
        raise NativeRequestV3Error("V3_PRIVATE_OWNER")

    def check_current(self) -> None:
        if type(self) is not NativeP11DigestLeaseV3 or id(self) != self._identity:
            raise NativeRequestV3Error("V3_OWNED_FACADE")
        _check_owned_facade(self._graph, self)
        self._producer.check_current()

    def digest_equals(self, raw: bytes) -> bool:
        self.check_current()
        if type(raw) is not bytes or len(raw) != 32:
            raise NativeRequestV3Error("V3_DIGEST")
        self._graph._meter.charge(bytes_read=32, node_visits=1)
        return bool(self._graph._value == raw)

    @contextmanager
    def borrow_digest(self) -> Iterator[bytes]:
        self.check_current()
        self._graph._dependents += 1
        try:
            yield self._graph._value
        finally:
            self._graph._dependents -= 1

    def close(self) -> None:
        if not self._graph._closed:
            self.check_current()
        self._graph.close()


def _check_owned_facade(graph: _OwnedGraph, facade: object) -> None:
    graph.check()
    if graph._facade is not facade:
        raise NativeRequestV3Error("V3_OWNED_FACADE")


class _PrechargedFinalization:
    """Debits one conservative prepaid operation, never creates allowance."""

    def __init__(self, byte_work: int, nodes: int) -> None:
        self._byte_work, self._nodes = byte_work, nodes

    def charge_finalization(self, byte_work: int, node_visits: int = 1) -> None:
        if (
            type(byte_work) is not int
            or type(node_visits) is not int
            or not 0 <= byte_work <= self._byte_work
            or not 0 <= node_visits <= self._nodes
        ):
            raise NativeRequestV3Error("V3_FINALIZATION_BOUND")
        self._byte_work -= byte_work
        self._nodes -= node_visits


def _owned_finalization(
    verified: NativeP11VerifiedLeaseV3,
    decision: NativeP11DigestLeaseV3 | None,
    *,
    created_at: datetime | None = None,
    served_truth_expires_at: datetime | None = None,
) -> NativeP11DigestLeaseV3:
    meter = verified._operation.meter
    value: VerifiedNativeP11RootV3 | None = None
    temporary: _OwnedGraph | None = None
    result_graph: _OwnedGraph | None = None
    result: NativeP11DigestLeaseV3 | None = None
    try:
        verified.check_current()
        if decision is not None:
            if type(decision) is not NativeP11DigestLeaseV3:
                raise NativeRequestV3Error("V3_DECISION_INPUT")
            decision.check_current()
            if decision._kind != "decision" or decision._producer is not verified:
                raise NativeRequestV3Error("V3_DECISION_INPUT")
            decision._graph.check(meter)
        value = verified._verified
        preimage_upper = 10_240 if decision is None else 1024
        projection_upper = 4096 + _owned_footprint(value.root_header, meter)
        if decision is None:
            projection_upper += _owned_footprint(value.body.identity, meter)
            projection_upper += _owned_footprint(value.body.metadata, meter)
        else:
            projection_upper += _owned_footprint(value.body.origin, meter)
        # Result root precedes temporary child, so both source and shared-parent
        # output LIFO enforce the same lifetime. The constant1KiB pays facade,
        # raw32, graph controls and its at-most-two producer references.
        result_graph = _OwnedGraph(meter, canonical=32, footprint=1024)
        result_graph.borrow(verified._operation.graph)
        if decision is not None:
            result_graph.borrow(decision._graph)
        result_graph._lease.retain_new_digests(32)
        temporary = _OwnedGraph(
            meter, canonical=MAX_RECORD_BYTES, footprint=projection_upper + 3 * preimage_upper
        )
        temporary._lease.retain_new_digests(1024)
        meter.charge(
            bytes_written=projection_upper + preimage_upper,
            sha_input_bytes=preimage_upper,
            node_visits=512,
        )
        prepaid = _PrechargedFinalization(2 * preimage_upper, 512)
        if decision is None:
            if created_at is None or served_truth_expires_at is None:
                raise NativeRequestV3Error("V3_DECISION_TIME")
            result_graph._value = native_p11_decision_digest_v3(
                value,
                created_at=created_at,
                served_truth_expires_at=served_truth_expires_at,
                budget=prepaid,
            )
        else:
            result_graph._value = origin_connection_seal_v3(
                value, decision._graph._value, budget=prepaid
            )
        result = NativeP11DigestLeaseV3.__new__(NativeP11DigestLeaseV3)
        result._graph, result._producer = result_graph, verified
        result._identity = id(result)
        result._kind = "decision" if decision is None else "seal"
        result_graph._facade = result
        temporary.close()
        return result
    except BaseException as error:
        if not _clear_owned_factory_error_v3(error, meter):
            (temporary or result_graph or verified._operation.graph)._retain_failed_cleanup()
        value = result = None
        del verified, decision, created_at, served_truth_expires_at
        if temporary is not None:
            temporary.close()
        if result_graph is not None:
            result_graph.close()
        del temporary, result_graph, meter
        raise error from None


def build_owned_admitted_native_p11_request_proof_v3(
    *,
    expected: NativeP11ExpectedLeaseV3,
    meter: NativeRequestV3OwnedMeterProtocol,
    candidate_authority: NativeP11AdmittedCandidateAuthorityV3,
    max_bytes: int = MAX_RECORD_BYTES,
) -> NativeP11ProofLeaseV3:
    value: NativeP11ExpectedValuesV3 | None = None
    operation: _OwnedOperation | None = None
    returned: _OwnedOperation | None = None
    proof: NativeServedRequestProofV3 | None = None
    result: NativeP11ProofLeaseV3 | None = None
    try:
        value = _borrow_owned_expected(expected, meter)
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_RECORD_BYTES:
            raise NativeRequestV3Error("V3_RECORD_BYTES")
        meter.set_counts(len(value.baseline.tracks), len(value._template.p11_items))
        # Keep final bytes under a prepaid root, with construction as its LIFO
        # child. This never refunds an operation while its returned copy lives.
        returned = _OwnedOperation.for_document(meter, max_bytes)
        operation = _OwnedOperation(expected, meter, max_bytes)
        proof = _build_native_p11_request_proof_v3(
            None,
            expected=value,
            meter=meter,
            candidate_authority=candidate_authority,
            upper_size=max_bytes,
            _owned=operation,
        )
        returned.graph._lease.retain_new_digests(64)
        returned.keep(proof)
        operation.close()
        result = NativeP11ProofLeaseV3.__new__(NativeP11ProofLeaseV3)
        result._operation = returned
        returned.graph._facade = result
        return result
    except BaseException as error:
        if not _clear_owned_factory_error_v3(error, meter):
            (
                operation.graph
                if operation is not None
                else returned.graph
                if returned is not None
                else expected._graph
            )._retain_failed_cleanup()
        value = proof = result = None
        del expected, candidate_authority, max_bytes
        if operation is not None:
            operation.close()
        if returned is not None:
            returned.close()
        del operation, returned, meter
        raise error from None


def verify_owned_admitted_native_p11_request_proof_v3(
    proof: NativeP11ProofLeaseV3,
    *,
    expected: NativeP11ExpectedLeaseV3,
    meter: NativeRequestV3OwnedMeterProtocol,
    candidate_authority: NativeP11AdmittedCandidateAuthorityV3,
) -> NativeP11VerifiedLeaseV3:
    try:
        return _verify_owned_streamed(
            proof, expected=expected, meter=meter, candidate_authority=candidate_authority
        )
    except BaseException as error:
        if not _clear_owned_factory_error_v3(error, meter):
            expected._graph._retain_failed_cleanup()
        del proof, expected, candidate_authority, meter
        raise error from None


class _SortedTrackIndexView(Sequence[SnapshotTrack]):
    def __init__(
        self,
        index: tuple[tuple[int, int, SnapshotTrack], ...],
        graph: _OwnedGraph,
        positions: range | None = None,
    ) -> None:
        self._index, self._graph = index, graph
        self._positions = range(len(index)) if positions is None else positions

    def check_current(self) -> None:
        self._graph.check()

    def __len__(self) -> int:
        self.check_current()
        return len(self._positions)

    @overload
    def __getitem__(self, ordinal: int) -> SnapshotTrack: ...
    @overload
    def __getitem__(self, ordinal: slice) -> _SortedTrackIndexView: ...
    def __getitem__(self, ordinal: int | slice) -> SnapshotTrack | _SortedTrackIndexView:
        self.check_current()
        if type(ordinal) is slice:
            return _SortedTrackIndexView(self._index, self._graph, self._positions[ordinal])
        return self._index[self._positions[ordinal]][2]


def _new_mutable_containers(value: object, meter: NativeRequestV3OwnedMeterProtocol) -> int:
    meter.charge(node_visits=1)
    if type(value) is FrozenObjectV3:
        # Conservative dict table and slots; keys/value strings are borrowed
        # from the exact earlier immutable owner throughout this leaf scope.
        return (
            128
            + 64 * len(value.members)
            + sum(_new_mutable_containers(child, meter) for _, child in value.members)
        )
    if type(value) is tuple:
        return 64 + 16 * len(value) + sum(_new_mutable_containers(child, meter) for child in value)
    return 0


def _frozen_nodes(value: object, meter: NativeRequestV3OwnedMeterProtocol) -> int:
    meter.charge(node_visits=1)
    if type(value) is FrozenObjectV3:
        return 1 + sum(1 + _frozen_nodes(child, meter) for _, child in value.members)
    if type(value) is tuple:
        return 1 + sum(1 + _frozen_nodes(child, meter) for child in value)
    return 1


class _SnapshotTrackView:
    """One independently charged mutable leaf; no T-sized mutable projection."""

    def __init__(
        self, tracks: tuple[FrozenObjectV3, ...], meter: NativeRequestV3OwnedMeterProtocol
    ) -> None:
        self._tracks, self._meter = tracks, meter

    def __len__(self) -> int:
        return len(self._tracks)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for frozen in self._tracks:
            width = bound_frozen_v3(frozen)
            scratch = _new_mutable_containers(frozen, self._meter) + 4 * width + 512
            if width > 65_536 or scratch > 65_536:
                raise NativeRequestV3Error("V3_OWNED_LEAF")
            self._meter.retain_scratch(scratch)
            leaf: dict[str, Any] | None = None
            try:
                self._meter.charge(
                    bytes_written=width, node_visits=2 * (1 + _frozen_nodes(frozen, self._meter))
                )
                leaf = frozen.to_dict()
                yield leaf
            finally:
                _clear_owned_mutable(leaf)
                leaf = None
                self._meter.release_scratch(scratch)


def _stream_body_projection(
    body: NativeP11BodyV3, meter: NativeRequestV3OwnedMeterProtocol
) -> dict[str, Any]:
    return {
        "request_id": body.request_id,
        "identity": body.identity.to_dict(),
        "metadata": body.metadata.to_dict(),
        "request_document": body.request_document.to_dict(),
        "request_features": body.request_features.to_dict(),
        "snapshot_header": body.snapshot_header.to_dict(),
        "snapshot_tracks": _SnapshotTrackView(body.snapshot_tracks, meter),
        "p11_items": [item.to_dict() for item in body.p11_items],
        "origin": body.origin.to_dict(),
    }


def _freeze_stream_calculated(
    body: dict[str, Any], template: NativeP11BodyV3, meter: NativeRequestV3OwnedMeterProtocol
) -> NativeP11BodyV3:
    # Only metadata availability and the N item availability fields change.
    # The operation explicitly borrows the expected owner, so all reused frozen
    # containers/strings remain owned until every dependent result closes.
    return _ExpectedFactoryAccounting(meter).body(
        template.request_id,
        template.identity,
        frozen_object_v3(body["metadata"]),
        template.request_document,
        template.request_features,
        template.snapshot_header,
        template.snapshot_tracks,
        tuple(frozen_object_v3(item) for item in body["p11_items"]),
        template.origin,
    )


class _StreamEncoder(_Encoder):
    """Use the unchanged canonical encoder; add only a lazy known track array."""

    def encode(self, value: object, path: str = "$", depth: int = 0) -> None:
        if type(value) is not _SnapshotTrackView:
            super().encode(value, path, depth)
            return
        self._node()
        if depth > 12 or path != "$.body.snapshot_tracks" or len(value) > 5000:
            raise NativeRequestV3Error("V3_ARRAY_COUNT")
        self._write(b"[")
        for ordinal, leaf in enumerate(value):
            self._node()
            if ordinal:
                self._write(b",")
            self.encode(leaf, path + "[]", depth + 1)
        self._write(b"]")


class _CompareEncoder(_StreamEncoder):
    def __init__(self, meter: NativeRequestV3OwnedMeterProtocol, raw: bytes) -> None:
        super().__init__(meter, len(raw), retain=False)
        self._raw = memoryview(raw)

    def _write(self, chunk: bytes) -> None:
        start = self.size
        self._write_size(len(chunk))
        if self._raw[start : self.size] != chunk:
            raise NativeRequestV3Error("V3_EXPECTED_MISMATCH")

    def _write_string(self, value: str) -> None:
        # The old encoder's non-retaining branch measures string widths only;
        # comparison must run its actual canonical string encoder instead.
        from autplay.domain.native_request_wire_v3 import _string

        self._write(_string(value, self.meter))


def _verify_owned_streamed(
    proof: NativeP11ProofLeaseV3,
    *,
    expected: NativeP11ExpectedLeaseV3,
    meter: NativeRequestV3OwnedMeterProtocol,
    candidate_authority: NativeP11AdmittedCandidateAuthorityV3,
) -> NativeP11VerifiedLeaseV3:
    value = _borrow_owned_expected(expected, meter)
    if type(proof) is not NativeP11ProofLeaseV3:
        raise NativeRequestV3Error("V3_TYPED_INPUT")
    proof.check_current()
    proof._operation.graph.check(meter)
    operation = _OwnedOperation(expected, meter, proof.byte_length, encoding=False)
    proof._operation.graph._dependents += 1
    digests = scratch = scan_scratch = 0
    actual: NativeP11BodyV3 | None = None
    header: dict[str, Any] | None = None
    root: bytes | None = None
    supplied: dict[str, Any] | None = None
    verified: VerifiedNativeP11RootV3 | None = None
    comparator: _CompareEncoder | None = None
    candidates: tuple[SnapshotTrack, ...] | None = None
    raw: bytes | None = None
    scanner: _Scanner | None = None
    result: NativeP11VerifiedLeaseV3 | None = None
    try:
        raw = proof._proof.document
        # Same strict closed scanner and canonical tokens as old preflight,
        # with its largest one-string scope retained BEFORE it runs. No complete
        # json.loads tree, record-range array or comparison clone is allocated.
        meter.retain_scratch(65_536)
        scan_scratch = 65_536
        meter.charge(bytes_read=len(raw), node_visits=len(raw))
        scanner = _Scanner(raw, meter)
        scanner.value()
        if scanner.position != len(raw):
            raise NativeRequestV3Error("V3_JSON_CANONICAL")
        meter.set_counts(scanner.tracks, scanner.items)
        if scanner.tracks != len(value.baseline.tracks) or scanner.items != len(
            value._template.p11_items
        ):
            raise NativeRequestV3Error("V3_EXPECTED_COUNT")
        meter.release_scratch(scan_scratch)
        scan_scratch = 0
        meter.retain_digests(64)
        try:
            meter.charge_bytes(len(raw))
            if sha256(raw).hexdigest() != proof._proof.proof_sha256:
                raise NativeRequestV3Error("V3_PROOF_DIGEST")
        finally:
            meter.release_digests(64)
        operation.retain_candidate_scratch()
        candidates = _candidates(value, candidate_authority, meter, _owned=operation)
        required_digests, required_scratch = _payload_sizes(value, candidates)
        required_scratch = operation.transfer_candidate_scratch(required_scratch)
        meter.retain_digests(required_digests)
        digests = required_digests
        meter.retain_scratch(required_scratch)
        scratch = required_scratch
        actual, header, root = _calculate(value, candidates, meter, _owned=operation)
        supplied = operation.projection(actual)
        operation.keep(supplied)
        comparator = _CompareEncoder(meter, raw)
        comparator.encode(_envelope(supplied, header, root.hex(), value))
        if comparator.size != len(raw):
            raise NativeRequestV3Error("V3_EXPECTED_MISMATCH")
        verified = VerifiedNativeP11RootV3(
            actual,
            frozen_object_v3(header),
            root.hex(),
            value._created_at_us,
        )
        operation.keep(verified)
        result = NativeP11VerifiedLeaseV3.__new__(NativeP11VerifiedLeaseV3)
        result._operation = operation
        result._proof_owner = proof
        result._expected_owner = expected
        operation.graph._facade = result
        return result
    except BaseException as error:
        if not _clear_owned_factory_error_v3(error, meter):
            operation.graph._retain_failed_cleanup()
        actual = header = root = supplied = verified = comparator = candidates = None
        raw = scanner = result = None
        del value, expected, candidate_authority
        operation.close()
        raise error from None
    finally:
        meter.release_scratch(scan_scratch)
        meter.release_scratch(scratch)
        meter.release_digests(digests)
        proof._operation.graph._dependents -= 1


"""Staged fragment appended to the private root kernel, never imported alone."""


_COMPLETIONS_V3: dict[int, NativeP11CompletionAttestationV3] = {}
_COMPLETION_GRAPHS_V3: dict[int, NativeP11CompletionAttestationV3] = {}


@dataclass(frozen=True, slots=True)
class _CompletionStateV3:
    admission: NativeConnectedRecordAdmissionV3
    head: NativeP11UnvalidatedHeadV3
    transaction: tuple[int, int]
    ordinal: int
    raw_sha256: bytes
    verified: NativeP11VerifiedLeaseV3
    proof: NativeP11ProofLeaseV3
    expected: NativeP11ExpectedLeaseV3
    original: _NativeImmutableBaselineOwnerV3


class NativeP11CompletionAttestationV3:
    """One current owned verification, consumed only by its exact admission.

    This receipt has no SQL or source authority. It transports checked content
    completion across the output-close barrier without exposing a legacy DTO.
    """

    __slots__ = ("_cleanup_failed", "_identity", "_output", "_payload")
    _cleanup_failed: bool
    _identity: int
    _output: NativeRequestV3DocumentOutputLeaseProtocol
    _payload: _CompletionStateV3 | None

    def __new__(cls) -> NativeP11CompletionAttestationV3:
        raise NativeRequestV3Error("V3_PRIVATE_COMPLETION")

    def check_current(self) -> None:
        if (
            type(self) is not NativeP11CompletionAttestationV3
            or id(self) != self._identity
            or _COMPLETIONS_V3.get(id(self)) is not self
        ):
            raise NativeRequestV3Error("V3_COMPLETION_STATE")
        self._output.check_current()
        state = self._payload
        if state is None:
            raise NativeRequestV3Error("V3_COMPLETION_STATE")
        state.verified.check_current()
        state.proof.check_current()
        state.expected.check_current()
        state.original.check_current()
        if state.admission.bound_record_sha256 is not state.raw_sha256:
            raise NativeRequestV3Error("V3_COMPLETION_BINDING")

    def _clear(self) -> None:
        # SELF output is clearing: registry-only cleanup, never SELF.current().
        if self._cleanup_failed:
            raise NativeRequestV3Error("V3_FAILURE_CLEANUP_RETAINED")
        _COMPLETIONS_V3.pop(id(self), None)
        if self._payload is not None:
            _COMPLETION_GRAPHS_V3.pop(id(self._payload.verified), None)
        self._payload = None

    def _retain_failed_cleanup(self) -> None:
        self._cleanup_failed = True
        self._output.close()

    def close(self) -> None:
        self.check_current()
        self._output.close()


def attest_owned_native_p11_completion_v3(
    verified: NativeP11VerifiedLeaseV3,
    *,
    admission: NativeConnectedRecordAdmissionV3,
    head: NativeP11UnvalidatedHeadV3,
) -> NativeP11CompletionAttestationV3:
    """Retain the bounded control frame before comparisons or receipt allocation."""
    from autplay.domain.native_connected_budget_v3 import NativeConnectedRecordAdmissionV3

    if type(admission) is not NativeConnectedRecordAdmissionV3:
        raise NativeRequestV3Error("V3_COMPLETION_TYPE")
    meter = admission.expected_meter()
    # Preserve the existing constant duplicate-control refusal. No borrowed
    # payload, new receipt, work allocation or scratch precedes this check.
    if type(verified) is NativeP11VerifiedLeaseV3 and id(verified) in _COMPLETION_GRAPHS_V3:
        verified.check_current()
        verified._operation.graph.check(meter)
        raise NativeRequestV3Error("V3_COMPLETION_ACTIVE")
    scratch = 0
    anchored = False
    pending: list[NativeP11CompletionAttestationV3 | None] | None = None
    try:
        if type(verified) is not NativeP11VerifiedLeaseV3:
            raise NativeRequestV3Error("V3_COMPLETION_TYPE")
        verified.check_current()
        verified._operation.graph.check(meter)
        anchored = True
        # Both original1024B scopes overlap. Keep them paid until full-chain
        # cleanup and provisional receipt teardown, rather than refund inside
        # the helper before its external saved causes have been inspected.
        meter.retain_scratch(2048)
        scratch = 2048
        meter.charge(bytes_written=256, node_visits=16)
        pending = [None]
        result = _attest_owned_content_v3(
            verified, admission=admission, head=head, _pending=pending
        )
        pending[0] = None
        return result
    except BaseException as error:
        if not anchored:
            del verified, admission, head
            raise error from None
        if not _clear_owned_factory_error_v3(error, meter):
            if pending is not None and pending[0] is not None:
                retained = pending[0]
                if hasattr(retained, "_output"):
                    retained._retain_failed_cleanup()
            verified._operation.graph._retain_failed_cleanup()
        if pending is not None and pending[0] is not None:
            provisional = pending[0]
            if hasattr(provisional, "_output"):
                provisional._output.close()
            else:
                provisional._payload = None
            pending[0] = None
            del provisional
        del verified, admission, head
        raise error from None
    finally:
        if pending is not None:
            pending.clear()
        if scratch:
            meter.release_scratch(scratch)


def _attest_owned_content_v3(
    verified: NativeP11VerifiedLeaseV3,
    *,
    admission: NativeConnectedRecordAdmissionV3,
    head: NativeP11UnvalidatedHeadV3,
    _pending: list[NativeP11CompletionAttestationV3 | None],
) -> NativeP11CompletionAttestationV3:
    """Compare current owned root/decision/seal and bind the exact admission.

    The owning reader independently supplies its locked head and source closure;
    this function performs content checks and creates no database authorization.
    """
    from autplay.domain.native_connected_budget_v3 import NativeConnectedRecordAdmissionV3
    from autplay.domain.native_connected_request_v3 import NativeP11UnvalidatedHeadV3

    if (
        type(verified) is not NativeP11VerifiedLeaseV3
        or type(admission) is not NativeConnectedRecordAdmissionV3
        or type(head) is not NativeP11UnvalidatedHeadV3
    ):
        raise NativeRequestV3Error("V3_COMPLETION_TYPE")
    verified.check_current()
    if id(verified) in _COMPLETION_GRAPHS_V3:
        raise NativeRequestV3Error("V3_COMPLETION_ACTIVE")
    proof, expected = verified._proof_owner, verified._expected_owner
    proof.check_current()
    expected.check_current()
    meter = admission.expected_meter()
    # Fixed comparisons/time objects and the eventual receipt are prepaid on
    # this existing record. All variable root/finalization work stays metered
    # by the existing kernel callbacks below, without a new work allowance.
    meter.reserve(byte_work_bound=4096, node_visit_bound=128)
    meter.charge(bytes_written=2048, node_visits=64)
    verified._operation.graph.check(meter)
    proof._operation.graph.check(meter)
    expected._graph.check(meter)
    borrow = expected._graph._original_borrow
    if borrow is None or borrow._owner is None:
        raise NativeRequestV3Error("V3_COMPLETION_ORIGINAL")
    borrow.check_current()
    original = borrow._owner
    transaction = admission.transaction_binding
    admission.authorize_root_input(head, proof._proof.document, transaction_binding=transaction)
    if (
        verified._verified.body.request_id != str(head.served_request_id)
        or not verified.root_digest_equals(head.request_root_sha256)
        or not proof.proof_digest_equals(head.root_proof_sha256)
        or expected._graph._value.origin.original_request_id != head.original_request_id
        or original._baseline is not expected._graph._value.baseline
    ):
        raise NativeRequestV3Error("V3_COMPLETION_CONTENT")
    created = _EPOCH + timedelta(microseconds=head.served_created_at_us)
    expires = _EPOCH + timedelta(microseconds=head.served_truth_expires_at_us)
    decision = verified.decision_digest(created_at=created, served_truth_expires_at=expires)
    try:
        if not decision.digest_equals(head.decision_sha256):
            raise NativeRequestV3Error("V3_COMPLETION_DECISION")
        seal = verified.connection_seal(decision)
        try:
            if not seal.digest_equals(head.connection_seal_sha256):
                raise NativeRequestV3Error("V3_COMPLETION_SEAL")
        finally:
            seal.close()
    finally:
        decision.close()
    meter.charge(bytes_written=1024, node_visits=16)
    value = object.__new__(NativeP11CompletionAttestationV3)
    value._cleanup_failed = False
    _pending[0] = value
    value._identity = id(value)
    value._payload = _CompletionStateV3(
        admission,
        head,
        transaction,
        admission.record_ordinal,
        admission.bound_record_sha256,
        verified,
        proof,
        expected,
        original,
    )
    value._output = meter.retain_document_output(
        canonical_byte_upper=1, owned_payload_upper=1024, clear_callback=value._clear
    )
    _COMPLETIONS_V3[id(value)] = value
    _COMPLETION_GRAPHS_V3[id(verified)] = value
    return value


def _consume_owned_native_p11_completion_v3(
    completion: NativeP11CompletionAttestationV3,
    *,
    admission: NativeConnectedRecordAdmissionV3,
) -> tuple[NativeP11UnvalidatedHeadV3, tuple[int, int], int, bytes]:
    """Internal one-shot handoff; close every graph before physical completion."""
    if type(completion) is not NativeP11CompletionAttestationV3:
        raise NativeRequestV3Error("V3_COMPLETION_TYPE")
    completion.check_current()
    state = completion._payload
    assert state is not None
    if state.admission is not admission:
        raise NativeRequestV3Error("V3_COMPLETION_ADMISSION")
    # These locals borrow existing paid objects. All graph references disappear
    # before each corresponding output refund; the immutable head is independent.
    meter = admission.expected_meter()
    meter.retain_scratch(1024)
    try:
        meter.charge(bytes_read=1024, node_visits=16)
        completion._output.close()
        state.verified.close()
        state.proof.close()
        state.expected.close()
        state.original.check_current()
        state.original._output.close()
        return state.head, state.transaction, state.ordinal, state.raw_sha256
    finally:
        meter.release_scratch(1024)
