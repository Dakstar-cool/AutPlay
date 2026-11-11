"""Distinct, content-addressed native provenance for the existing quality gate.

This artifact is review material. Only the independently signed source approval
bound to it can admit a source; constructing it is not that approval.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from autplay.application.adaptive_recommendations import canonical_sha256
from autplay.application.sona import sona_inference_request_document
from autplay.application.sona_native_dataset import (
    ADAPTER_VERSION,
    NativeTrainingCase,
    verify_native_training_case,
)
from autplay.application.sona_source_acceptance import (
    SONA_SOURCE_ACCEPTANCE_MAX_BYTES,
    SonaSourceProvenanceAcceptance,
    load_sona_source_provenance_acceptance,
    verify_sona_source_provenance_acceptance,
)
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import SonaInferenceRequest

NATIVE_ACCEPTANCE_KIND = "SONA_NATIVE_CAPTURE_PROVENANCE_V1"
NATIVE_TEMPORAL_KIND = "DERIVED_FROM_ORIGINAL_P11_NATIVE_CAPTURE_V1"
NATIVE_PROFILE_SCHEME = "UUID5_NATIVE_OFFLINE_OWNER_V1"


@dataclass(frozen=True, slots=True)
class NativeSourceProvenance:
    document: dict[str, JsonValue]
    acceptance_sha256: str


type TrainingSourceProvenance = SonaSourceProvenanceAcceptance | NativeSourceProvenance


def _set_digest(values: set[str]) -> str:
    return canonical_sha256(cast(list[JsonValue], sorted(values)))


def build_native_source_provenance(
    cases: Sequence[NativeTrainingCase],
    requests: Sequence[SonaInferenceRequest],
    *,
    recorded_at_ms: int,
) -> NativeSourceProvenance:
    """Bind review provenance to exact materialized requests and native source bytes."""
    if not 1 <= len(cases) == len(requests) <= 4096:
        raise ValueError("Native provenance request count is invalid")
    request_ids = {r.request_sha256 for r in requests}
    if len(request_ids) != len(requests):
        raise ValueError("Native provenance contains duplicate requests")
    outcomes = {(c.outcome_event.owner_user_id, c.outcome_event.source_event_id) for c in cases}
    if len(outcomes) != len(cases):
        raise ValueError("Native provenance reuses an outcome event")
    for case, request in zip(cases, requests, strict=True):
        verify_native_training_case(case)
        if (
            canonical_sha256(sona_inference_request_document(request)) != request.request_sha256
            or str(request.temporal_snapshot_id) != case.temporal_document["snapshot_id"]
            or request.cutoff_at_ms != case.temporal_document["cutoff_at_ms"]
            or request.owner_user_id != case.capture.row.owner_user_id
            or request.baseline_snapshot_id != case.capture.baseline.reference.snapshot_id
            or case.provenance_document["adapter_version"] != ADAPTER_VERSION
            or case.capture.blockers
        ):
            raise ValueError("Native provenance request ancestry mismatch")
    capture_hashes = {
        value.row.payload_sha256 for case in cases for value in (case.capture, case.outcome_capture)
    }
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "acceptance_kind": NATIVE_ACCEPTANCE_KIND,
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "adapter_version": ADAPTER_VERSION,
        "temporal_provenance_kind": NATIVE_TEMPORAL_KIND,
        "original_persisted_temporal_snapshots_available": False,
        "server_profile_replacement_scheme": NATIVE_PROFILE_SCHEME,
        "quality_eligible": False,
        "contains_owner_identifiers": False,
        "capture_count": len(capture_hashes),
        "capture_set_sha256": _set_digest(capture_hashes),
        "request_count": len(request_ids),
        "request_set_sha256": _set_digest(request_ids),
        "split_request_set_sha256": {
            split: _set_digest(
                {
                    request.request_sha256
                    for case, request in zip(cases, requests, strict=True)
                    if case.split == split
                }
            )
            for split in ("train", "validation", "test")
        },
        "case_provenance_set_sha256": _set_digest(
            {canonical_sha256(c.provenance_document) for c in cases}
        ),
        "temporal_snapshot_set_sha256": _set_digest(
            {canonical_sha256(c.temporal_document) for c in cases}
        ),
        "recorded_at_ms": recorded_at_ms,
    }
    value = NativeSourceProvenance(document, canonical_sha256(document))
    verify_training_source_provenance(value)
    return value


def verify_training_source_provenance(value: TrainingSourceProvenance) -> None:
    if isinstance(value, SonaSourceProvenanceAcceptance):
        verify_sona_source_provenance_acceptance(value)
        return
    doc = value.document
    constants: dict[str, JsonValue] = {
        "schema_version": 1,
        "acceptance_kind": NATIVE_ACCEPTANCE_KIND,
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "adapter_version": ADAPTER_VERSION,
        "temporal_provenance_kind": NATIVE_TEMPORAL_KIND,
        "original_persisted_temporal_snapshots_available": False,
        "server_profile_replacement_scheme": NATIVE_PROFILE_SCHEME,
        "quality_eligible": False,
        "contains_owner_identifiers": False,
    }
    hashes = {
        "capture_set_sha256",
        "request_set_sha256",
        "case_provenance_set_sha256",
        "temporal_snapshot_set_sha256",
    }
    if set(doc) != set(constants) | hashes | {
        "capture_count",
        "request_count",
        "recorded_at_ms",
        "split_request_set_sha256",
    }:
        raise ValueError("Native provenance fields are invalid")
    if any(type(doc[k]) is not type(v) or doc[k] != v for k, v in constants.items()):
        raise ValueError("Native provenance semantics are invalid")
    splits = doc["split_request_set_sha256"]
    if not isinstance(splits, dict) or set(splits) != {"train", "validation", "test"}:
        raise ValueError("Native provenance splits are invalid")
    for digest in [*(doc[field] for field in hashes), *splits.values()]:
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("Native provenance digest is invalid")
    for field, lower, upper in (
        ("capture_count", 1, 8192),
        ("request_count", 1, 4096),
        ("recorded_at_ms", 0, 9007199254740991),
    ):
        number = doc[field]
        if type(number) is not int or not lower <= number <= upper:
            raise ValueError("Native provenance bound is invalid")
    if canonical_sha256(doc) != value.acceptance_sha256:
        raise ValueError("Native provenance hash mismatch")


def verify_native_plan_membership(
    value: TrainingSourceProvenance,
    plan: dict[str, JsonValue],
) -> None:
    """Keep the exact purged membership through P11-to-Sona re-key and approval."""
    if not isinstance(value, NativeSourceProvenance):
        return
    verify_training_source_provenance(value)
    splits = plan.get("splits")
    if not isinstance(splits, list) or len(splits) != 3:
        raise ValueError("Native re-key split membership is missing")
    actual: dict[str, JsonValue] = {}
    for item in splits:
        if not isinstance(item, dict) or not isinstance(item.get("split"), str):
            raise ValueError("Native re-key split membership is invalid")
        actual[str(item["split"])] = item.get("request_set_sha256")
    if (
        actual != value.document["split_request_set_sha256"]
        or plan.get("request_set_sha256") != value.document["request_set_sha256"]
        or plan.get("selected_request_count") != value.document["request_count"]
    ):
        raise ValueError("Native provenance re-key membership mismatch")


def load_training_source_provenance(path: Path) -> TrainingSourceProvenance:
    """Explicit dispatch: native input can never fall through as a 0026 restore."""
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > SONA_SOURCE_ACCEPTANCE_MAX_BYTES
    ):
        raise ValueError("Training provenance path is invalid")
    raw = json.loads(path.read_bytes())
    if not isinstance(raw, dict) or set(raw) != {"acceptance", "acceptance_sha256"}:
        raise ValueError("Training provenance envelope is invalid")
    document = raw["acceptance"]
    if not isinstance(document, dict):
        raise ValueError("Training provenance document is invalid")
    if document.get("acceptance_kind") != NATIVE_ACCEPTANCE_KIND:
        return load_sona_source_provenance_acceptance(path)
    value = NativeSourceProvenance(document, raw["acceptance_sha256"])
    verify_training_source_provenance(value)
    return value
