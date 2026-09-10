"""Aggregate-only admission preflight for an authorized Sona quality source restore."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Final, Protocol, cast

import rfc8785

from autplay.domain.recommendations import JsonValue
from autplay.domain.sona_approval import SONA_LABEL_DELAY_EMBARGO_MS

from .sona_source_acceptance import (
    SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
    SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
    SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
    SonaSourceProvenanceAcceptance,
    verify_sona_source_provenance_acceptance,
)
from .sona_source_planning import SONA_MAX_QUALITY_SOURCE_REQUESTS

SONA_SOURCE_PREFLIGHT_SCHEMA_VERSION: Final = 1
SONA_SOURCE_PREFLIGHT_KIND: Final = "SONA_SOURCE_PREFLIGHT_V1"
SONA_SOURCE_PREFLIGHT_PRIVACY_BOUNDARY: Final = "AGGREGATE_COUNTS_ONLY_V1"
SONA_SOURCE_PREFLIGHT_SPLIT_PROBE: Final = "GLOBAL_TIME_60_20_20_WITH_7D_EMBARGO_V1"


@dataclass(frozen=True, slots=True)
class SonaSourceAggregateCounts:
    """Non-identifying counts returned by one read-only source snapshot."""

    replay_request_count: int
    candidate_bounded_request_count: int
    mature_attributed_request_count: int
    history_bound_request_count: int
    fully_bound_request_count: int
    eligible_owner_count: int
    eligible_recording_count: int
    approved_embedding_model_count: int
    embedded_recording_count: int
    train_request_count: int
    validation_request_count: int
    test_request_count: int
    eligible_span_ms: int
    owner_isolation_violation_count: int
    attribution_violation_count: int
    source_chain_violation_count: int
    hash_or_snapshot_integrity_gap_count: int

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                self.replay_request_count,
                self.candidate_bounded_request_count,
                self.mature_attributed_request_count,
                self.history_bound_request_count,
                self.fully_bound_request_count,
                self.eligible_owner_count,
                self.eligible_recording_count,
                self.approved_embedding_model_count,
                self.embedded_recording_count,
                self.train_request_count,
                self.validation_request_count,
                self.test_request_count,
                self.eligible_span_ms,
                self.owner_isolation_violation_count,
                self.attribution_violation_count,
                self.source_chain_violation_count,
                self.hash_or_snapshot_integrity_gap_count,
            )
        ):
            raise ValueError("Sona source preflight counts must be non-negative integers")
        if self.fully_bound_request_count > self.mature_attributed_request_count:
            raise ValueError("Sona fully-bound count exceeds mature attributed requests")
        if self.eligible_owner_count > self.fully_bound_request_count:
            raise ValueError("Sona eligible owner count exceeds fully-bound requests")
        if (
            self.train_request_count + self.validation_request_count + self.test_request_count
            > self.fully_bound_request_count
        ):
            raise ValueError("Sona split probe exceeds fully-bound requests")


@dataclass(frozen=True, slots=True)
class SonaSourceAggregateSnapshot:
    """One aggregate database observation, with no owner or recording identifiers."""

    observed_alembic_head: str
    transaction_read_only: bool
    transaction_isolation: str
    counts: SonaSourceAggregateCounts

    def __post_init__(self) -> None:
        if not self.observed_alembic_head or len(self.observed_alembic_head) > 200:
            raise ValueError("Sona source Alembic head is invalid")
        if self.transaction_isolation != "repeatable read":
            raise ValueError("Sona source preflight requires repeatable-read isolation")
        if not self.transaction_read_only:
            raise ValueError("Sona source preflight requires a read-only transaction")


@dataclass(frozen=True, slots=True)
class SonaSourcePreflightResult:
    """Canonical counts-only report and its content identity."""

    document: dict[str, JsonValue]
    report_sha256: str
    ready_for_authorized_extraction: bool


class SonaSourceAggregateReader(Protocol):
    """Read the aggregate source observation inside an already-safe transaction."""

    def read(self, *, source_captured_at_ms: int) -> SonaSourceAggregateSnapshot: ...


def build_sona_source_preflight_result(
    snapshot: SonaSourceAggregateSnapshot,
    *,
    generation_id: str,
    encrypted_archive_sha256: str,
    source_captured_at_ms: int,
    declared_alembic_head: str,
    provenance_acceptance: SonaSourceProvenanceAcceptance | None = None,
) -> SonaSourcePreflightResult:
    """Build a fail-closed report that contains aggregate counts only."""

    _validate_label(generation_id, "generation_id")
    _validate_sha256(encrypted_archive_sha256, "encrypted_archive_sha256")
    _validate_label(declared_alembic_head, "declared_alembic_head")
    if source_captured_at_ms < 0:
        raise ValueError("Sona source capture time is invalid")
    if provenance_acceptance is not None:
        verify_sona_source_provenance_acceptance(provenance_acceptance)
        if (
            provenance_acceptance.generation_id != generation_id
            or provenance_acceptance.encrypted_archive_sha256 != encrypted_archive_sha256
        ):
            raise ValueError("Sona source provenance acceptance does not match the source archive")

    counts = snapshot.counts
    blockers: list[str] = []
    if (
        declared_alembic_head != SONA_SOURCE_EXPECTED_ALEMBIC_HEAD
        or snapshot.observed_alembic_head != declared_alembic_head
    ):
        blockers.append("UNSUPPORTED_OR_MISMATCHED_SCHEMA_REVISION")
    elif provenance_acceptance is None:
        # Schema 0026 predates the retained R1A temporal snapshot tables.  It also
        # cannot contain server_profile_id because that identifier is intentionally
        # Android-local.  Aggregate sufficiency must therefore never be confused
        # with approval of reconstructed provenance.
        blockers.extend(
            (
                "ORIGINAL_TEMPORAL_SNAPSHOTS_UNAVAILABLE",
                "SERVER_PROFILE_BINDING_UNAVAILABLE",
            )
        )
    if counts.replay_request_count == 0:
        blockers.append("NO_REPLAY_COMPLETE_P11_REQUESTS")
    if counts.candidate_bounded_request_count == 0:
        blockers.append("NO_BOUNDED_MANDATORY_FILTER_CANDIDATE_SETS")
    if counts.mature_attributed_request_count == 0:
        blockers.append("NO_MATURE_ATTRIBUTED_LABELS")
    if counts.history_bound_request_count == 0:
        blockers.append("NO_REPLAY_BOUND_TEMPORAL_HISTORY")
    if counts.approved_embedding_model_count == 0:
        blockers.append("NO_PROVENANCE_APPROVED_ACTIVE_EMBEDDING_MODEL")
    elif counts.approved_embedding_model_count > 1:
        blockers.append("AMBIGUOUS_PROVENANCE_APPROVED_ACTIVE_EMBEDDING_MODEL")
    if counts.fully_bound_request_count == 0:
        blockers.append("NO_FULLY_SOURCE_BOUND_REQUESTS")
    elif counts.fully_bound_request_count > SONA_MAX_QUALITY_SOURCE_REQUESTS:
        blockers.append("SOURCE_REQUEST_COUNT_EXCEEDS_MATERIALIZER_BOUND")
    if (
        min(
            counts.train_request_count,
            counts.validation_request_count,
            counts.test_request_count,
        )
        == 0
    ):
        blockers.append("TIME_SPLIT_WITH_7D_EMBARGO_INFEASIBLE")
    if counts.owner_isolation_violation_count:
        blockers.append("OWNER_ISOLATION_VIOLATION")
    if counts.attribution_violation_count:
        blockers.append("OUTCOME_ATTRIBUTION_VIOLATION")
    if counts.source_chain_violation_count:
        blockers.append("SOURCE_EVENT_CHAIN_VIOLATION")
    if counts.hash_or_snapshot_integrity_gap_count:
        blockers.append("HASH_OR_SNAPSHOT_INTEGRITY_GAP")

    ready = not blockers
    document: dict[str, JsonValue] = {
        "schema_version": SONA_SOURCE_PREFLIGHT_SCHEMA_VERSION,
        "report_kind": SONA_SOURCE_PREFLIGHT_KIND,
        "privacy_boundary": SONA_SOURCE_PREFLIGHT_PRIVACY_BOUNDARY,
        "generation_id": generation_id,
        "encrypted_archive_sha256": encrypted_archive_sha256,
        "source_captured_at_ms": source_captured_at_ms,
        "declared_alembic_head": declared_alembic_head,
        "observed_alembic_head": snapshot.observed_alembic_head,
        "temporal_provenance": {
            "kind": SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
            "original_persisted_temporal_snapshots_available": False,
            "server_profile_binding_available": False,
            "server_profile_replacement_scheme": SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
            "review_acceptance_recorded": provenance_acceptance is not None,
            "review_acceptance_sha256": (
                provenance_acceptance.acceptance_sha256
                if provenance_acceptance is not None
                else None
            ),
        },
        "transaction_read_only": snapshot.transaction_read_only,
        "transaction_isolation": snapshot.transaction_isolation,
        "split_probe": {
            "policy": SONA_SOURCE_PREFLIGHT_SPLIT_PROBE,
            "label_delay_embargo_ms": SONA_LABEL_DELAY_EMBARGO_MS,
            "train_request_count": counts.train_request_count,
            "validation_request_count": counts.validation_request_count,
            "test_request_count": counts.test_request_count,
            "eligible_span_ms": counts.eligible_span_ms,
        },
        "counts": {
            "replay_request_count": counts.replay_request_count,
            "candidate_bounded_request_count": counts.candidate_bounded_request_count,
            "mature_attributed_request_count": counts.mature_attributed_request_count,
            "history_bound_request_count": counts.history_bound_request_count,
            "fully_bound_request_count": counts.fully_bound_request_count,
            "eligible_owner_count": counts.eligible_owner_count,
            "eligible_recording_count": counts.eligible_recording_count,
            "approved_embedding_model_count": counts.approved_embedding_model_count,
            "embedded_recording_count": counts.embedded_recording_count,
            "owner_isolation_violation_count": counts.owner_isolation_violation_count,
            "attribution_violation_count": counts.attribution_violation_count,
            "source_chain_violation_count": counts.source_chain_violation_count,
            "hash_or_snapshot_integrity_gap_count": (counts.hash_or_snapshot_integrity_gap_count),
        },
        "ready_for_authorized_extraction": ready,
        "blockers": cast(list[JsonValue], blockers),
        "quality_eligible": False,
    }
    digest = sha256(rfc8785.dumps(document)).hexdigest()
    return SonaSourcePreflightResult(document, digest, ready)


def _validate_label(value: str, field: str) -> None:
    if not 1 <= len(value) <= 200 or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in value
    ):
        raise ValueError(f"Sona source {field} is invalid")


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"Sona source {field} is not a lowercase SHA-256 digest")


__all__ = (
    "SONA_SOURCE_EXPECTED_ALEMBIC_HEAD",
    "SONA_SOURCE_PREFLIGHT_KIND",
    "SONA_SOURCE_PREFLIGHT_PRIVACY_BOUNDARY",
    "SONA_SOURCE_PREFLIGHT_SCHEMA_VERSION",
    "SONA_SOURCE_PREFLIGHT_SPLIT_PROBE",
    "SONA_SOURCE_TEMPORAL_PROVENANCE_KIND",
    "SonaSourceAggregateCounts",
    "SonaSourceAggregateReader",
    "SonaSourceAggregateSnapshot",
    "SonaSourcePreflightResult",
    "build_sona_source_preflight_result",
)
