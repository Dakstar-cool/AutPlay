"""Strict canonical JSON transport for the isolated Sona-Lite process."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import cast
from uuid import UUID

import rfc8785

from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import (
    SonaAction,
    SonaCandidate,
    SonaGeneratedCandidate,
    SonaHistoryEvent,
    SonaInferenceOutput,
    SonaInferenceRequest,
    SonaOrigin,
    SonaRankedCandidate,
    SonaSemanticId,
)

SONA_MAX_TRANSPORT_BYTES = 2_097_152


def sona_request_document(request: SonaInferenceRequest) -> dict[str, JsonValue]:
    """Return the exact request body whose RFC 8785 hash is request_sha256."""

    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "architecture": "SONA_LITE_SHARED_ENCODER_V1",
        "owner_user_id": str(request.owner_user_id),
        "temporal_snapshot_id": str(request.temporal_snapshot_id),
        "baseline_snapshot_id": str(request.baseline_snapshot_id),
        "cutoff_at_ms": request.cutoff_at_ms,
        "interaction_watermark": request.interaction_watermark,
        "tokenizer_sha256": request.tokenizer_sha256,
        "model_manifest_sha256": request.model_manifest_sha256,
        "seed": request.seed,
        "history": [
            {
                "evidence_id": str(value.evidence_id),
                "recording_id": str(value.recording_id),
                "semantic_id": list(value.semantic_id.values),
                "action": int(value.action),
                "origin": int(value.origin),
                "age_bucket": value.age_bucket,
                "effective_at_ms": value.effective_at_ms,
                "server_sequence": value.server_sequence,
            }
            for value in request.history
        ],
        "candidates": [
            {
                "recording_id": str(value.recording_id),
                "semantic_id": list(value.semantic_id.values),
            }
            for value in request.candidates
        ],
    }
    if _digest(document) != request.request_sha256:
        raise ValueError("Sona request object does not match its canonical hash")
    return document


def sona_request_envelope(request: SonaInferenceRequest) -> dict[str, JsonValue]:
    return {
        "request": sona_request_document(request),
        "request_sha256": request.request_sha256,
    }


def sona_request_from_envelope(value: Mapping[str, object]) -> SonaInferenceRequest:
    """Parse an exact-key bounded request and verify its canonical self-hash."""

    _exact_keys(value, {"request", "request_sha256"}, "Sona request envelope")
    document = _mapping(value, "request")
    declared_hash = _string(value, "request_sha256")
    _exact_keys(
        document,
        {
            "schema_version",
            "architecture",
            "owner_user_id",
            "temporal_snapshot_id",
            "baseline_snapshot_id",
            "cutoff_at_ms",
            "interaction_watermark",
            "tokenizer_sha256",
            "model_manifest_sha256",
            "seed",
            "history",
            "candidates",
        },
        "Sona request",
    )
    if (
        _integer(document, "schema_version") != 1
        or _string(document, "architecture") != "SONA_LITE_SHARED_ENCODER_V1"
    ):
        raise ValueError("Sona request schema is unsupported")
    if _digest(cast(dict[str, JsonValue], document)) != declared_hash:
        raise ValueError("Sona request canonical hash mismatch")
    history = tuple(_history_event(_as_mapping(item)) for item in _sequence(document, "history"))
    candidates = tuple(_candidate(_as_mapping(item)) for item in _sequence(document, "candidates"))
    return SonaInferenceRequest(
        owner_user_id=_uuid(document, "owner_user_id"),
        temporal_snapshot_id=_uuid(document, "temporal_snapshot_id"),
        baseline_snapshot_id=_uuid(document, "baseline_snapshot_id"),
        cutoff_at_ms=_integer(document, "cutoff_at_ms"),
        interaction_watermark=_integer(document, "interaction_watermark"),
        tokenizer_sha256=_string(document, "tokenizer_sha256"),
        model_manifest_sha256=_string(document, "model_manifest_sha256"),
        seed=_integer(document, "seed"),
        history=history,
        candidates=candidates,
        request_sha256=declared_hash,
    )


def sona_output_document(output: SonaInferenceOutput) -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "request_sha256": output.request_sha256,
        "generated": [
            {
                "semantic_id": list(value.semantic_id.values),
                "log_probability": value.log_probability,
            }
            for value in output.generated
        ],
        "ranked": [
            {
                "recording_id": str(value.recording_id),
                "head_scores": list(value.head_scores),
                "combined_score": value.combined_score,
            }
            for value in output.ranked
        ],
    }


def sona_output_sha256(output: SonaInferenceOutput) -> str:
    return _digest(sona_output_document(output))


def sona_output_envelope(output: SonaInferenceOutput) -> dict[str, JsonValue]:
    document = sona_output_document(output)
    return {"output": document, "output_sha256": _digest(document)}


def sona_output_from_envelope(value: Mapping[str, object]) -> SonaInferenceOutput:
    """Parse a bounded result and reject tampered scores, IDs, order or hash."""

    _exact_keys(value, {"output", "output_sha256"}, "Sona output envelope")
    document = _mapping(value, "output")
    declared_hash = _string(value, "output_sha256")
    _exact_keys(
        document,
        {"schema_version", "request_sha256", "generated", "ranked"},
        "Sona output",
    )
    if _integer(document, "schema_version") != 1:
        raise ValueError("Sona output schema is unsupported")
    if _digest(cast(dict[str, JsonValue], document)) != declared_hash:
        raise ValueError("Sona output canonical hash mismatch")
    generated = tuple(_generated(_as_mapping(item)) for item in _sequence(document, "generated"))
    ranked = tuple(_ranked(_as_mapping(item)) for item in _sequence(document, "ranked"))
    return SonaInferenceOutput(_string(document, "request_sha256"), generated, ranked)


def _history_event(value: Mapping[str, object]) -> SonaHistoryEvent:
    _exact_keys(
        value,
        {
            "evidence_id",
            "recording_id",
            "semantic_id",
            "action",
            "origin",
            "age_bucket",
            "effective_at_ms",
            "server_sequence",
        },
        "Sona history event",
    )
    return SonaHistoryEvent(
        evidence_id=_uuid(value, "evidence_id"),
        recording_id=_uuid(value, "recording_id"),
        semantic_id=_semantic_id(value, "semantic_id"),
        action=SonaAction(_integer(value, "action")),
        origin=SonaOrigin(_integer(value, "origin")),
        age_bucket=_integer(value, "age_bucket"),
        effective_at_ms=_integer(value, "effective_at_ms"),
        server_sequence=_integer(value, "server_sequence"),
    )


def _candidate(value: Mapping[str, object]) -> SonaCandidate:
    _exact_keys(value, {"recording_id", "semantic_id"}, "Sona candidate")
    return SonaCandidate(_uuid(value, "recording_id"), _semantic_id(value, "semantic_id"))


def _generated(value: Mapping[str, object]) -> SonaGeneratedCandidate:
    _exact_keys(value, {"semantic_id", "log_probability"}, "Sona generated candidate")
    return SonaGeneratedCandidate(
        _semantic_id(value, "semantic_id"), _number(value, "log_probability")
    )


def _ranked(value: Mapping[str, object]) -> SonaRankedCandidate:
    _exact_keys(
        value,
        {"recording_id", "head_scores", "combined_score"},
        "Sona ranked candidate",
    )
    scores = tuple(
        _number_item(item, "Sona ranking head score") for item in _sequence(value, "head_scores")
    )
    if len(scores) != 4:
        raise ValueError("Sona ranking head count is invalid")
    return SonaRankedCandidate(
        _uuid(value, "recording_id"),
        scores,
        _number(value, "combined_score"),
    )


def _semantic_id(value: Mapping[str, object], key: str) -> SonaSemanticId:
    items = tuple(_integer_item(item, f"Sona {key}") for item in _sequence(value, key))
    if len(items) != 3:
        raise ValueError("Sona Semantic ID depth is invalid")
    return SonaSemanticId(*items)


def _digest(document: JsonValue) -> str:
    try:
        return sha256(rfc8785.dumps(document)).hexdigest()
    except rfc8785.CanonicalizationError as error:
        raise ValueError("Sona document exceeds the canonical JSON domain") from error


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} keys are invalid")


def _mapping(value: Mapping[str, object], key: str) -> dict[str, object]:
    item = value.get(key)
    if not isinstance(item, dict) or any(not isinstance(name, str) for name in item):
        raise ValueError(f"Sona {key} must be an object")
    return cast(dict[str, object], item)


def _as_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(name, str) for name in value):
        raise ValueError("Sona sequence item must be an object")
    return cast(dict[str, object], value)


def _sequence(value: Mapping[str, object], key: str) -> Sequence[object]:
    item = value.get(key)
    if not isinstance(item, list):
        raise ValueError(f"Sona {key} must be an array")
    return cast(Sequence[object], item)


def _string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"Sona {key} must be a string")
    return item


def _integer(value: Mapping[str, object], key: str) -> int:
    return _integer_item(value.get(key), f"Sona {key}")


def _integer_item(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _number(value: Mapping[str, object], key: str) -> float:
    return _number_item(value.get(key), f"Sona {key}")


def _number_item(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _uuid(value: Mapping[str, object], key: str) -> UUID:
    try:
        return UUID(_string(value, key))
    except ValueError as error:
        raise ValueError(f"Sona {key} must be a UUID") from error


__all__ = (
    "SONA_MAX_TRANSPORT_BYTES",
    "sona_output_document",
    "sona_output_envelope",
    "sona_output_from_envelope",
    "sona_output_sha256",
    "sona_request_document",
    "sona_request_envelope",
    "sona_request_from_envelope",
)
