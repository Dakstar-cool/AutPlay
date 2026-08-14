"""Atomic persistence commands for immutable identity decisions.

This module deliberately does not evaluate or rank candidates.  It only owns
the transaction boundary needed to persist an already-built decision graph.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Final

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autplay.application.identity_evidence import (
    IdentityDocumentError,
    JsonValue,
    candidate_aggregate_sha256,
    canonical_candidate_evidence,
    canonical_query_snapshot,
    validate_candidate_evidence_total,
)

from .models import MatchCandidateEvidenceRow, MatchDecisionRow, RecordingRow

_IDEMPOTENCY_CONSTRAINT: Final = "uq_match_decision_idempotency"
_IDENTITY_CONSTRAINTS: Final = (
    "identity.tr_match_decision_validate, "
    "identity.tr_match_candidate_evidence_validate, "
    "importing.tr_import_entry_match_projection, "
    "library.tr_user_track_ref_match_projection"
)
_SET_IDENTITY_CONSTRAINTS_IMMEDIATE: Final = text(
    f"SET CONSTRAINTS {_IDENTITY_CONSTRAINTS} IMMEDIATE"
)
_SET_IDENTITY_CONSTRAINTS_DEFERRED: Final = text(
    f"SET CONSTRAINTS {_IDENTITY_CONSTRAINTS} DEFERRED"
)

IdentityDecisionWriter = Callable[[Session], MatchDecisionRow]
OwnerProjectionWriter = Callable[[Session, MatchDecisionRow, RecordingRow], None]


class IdentityDecisionIdempotencyConflict(RuntimeError):
    """The same idempotency key was reused for a different canonical request."""

    code: ClassVar[str] = "identity_decision_idempotency_conflict"

    def __init__(self) -> None:
        super().__init__(self.code)


class IdentityDecisionCommandContractError(ValueError):
    """A caller supplied rows that cannot represent the requested command."""


@dataclass(frozen=True, slots=True)
class IdentityDecisionCommandResult:
    """The durable decision selected by an idempotent command attempt."""

    decision: MatchDecisionRow
    replayed: bool


@dataclass(frozen=True, slots=True)
class CreateRecordingReviewCommand:
    """Prebuilt rows and owner-projection callback for ``CREATE_RECORDING``.

    ``recording``, ``decision``, and every candidate-evidence row must be
    transient.  The helper assigns the new recording/decision identifiers and
    invokes ``project_owner`` inside the same savepoint.  The callback may only
    project the supplied review onto its existing ImportEntry or UserTrackRef;
    global Recording merge behavior is intentionally outside this seam.
    """

    recording: RecordingRow
    decision: MatchDecisionRow
    candidate_evidence: tuple[MatchCandidateEvidenceRow, ...]
    project_owner: OwnerProjectionWriter


def execute_identity_decision_command(
    session: Session,
    *,
    idempotency_scope: str,
    idempotency_key: str,
    request_sha256: bytes,
    write: IdentityDecisionWriter,
) -> IdentityDecisionCommandResult:
    """Persist one complete decision graph under caller-owned transaction control.

    The write callback runs inside a nested transaction so a uniqueness race
    cannot leave partial command rows in the caller's transaction.  Deferred
    identity/projection triggers are made immediate before the savepoint is
    released, then restored to their schema-default deferred mode.

    Only the named decision idempotency constraint is interpreted.  Every
    other database error is re-raised unchanged.  The function never commits
    the outer transaction.
    """

    _require_clean_session(session)
    try:
        with session.begin_nested():
            decision = write(session)
            _require_idempotency_identity(
                decision,
                idempotency_scope=idempotency_scope,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
            )
            session.add(decision)
            session.flush()
            _require_canonical_decision_graph(
                decision,
                _candidate_evidence_for_decision(session, decision),
            )
            session.execute(_SET_IDENTITY_CONSTRAINTS_IMMEDIATE)
            session.execute(_SET_IDENTITY_CONSTRAINTS_DEFERRED)
    except IntegrityError as error:
        if _constraint_name(error) != _IDEMPOTENCY_CONSTRAINT:
            raise
        stored = session.scalar(
            select(MatchDecisionRow).where(
                MatchDecisionRow.idempotency_scope == idempotency_scope,
                MatchDecisionRow.idempotency_key == idempotency_key,
            )
        )
        if stored is None:
            raise
        _require_canonical_decision_graph(
            stored,
            _candidate_evidence_for_decision(session, stored),
        )
        if not hmac.compare_digest(stored.request_sha256, request_sha256):
            raise IdentityDecisionIdempotencyConflict from None
        return IdentityDecisionCommandResult(decision=stored, replayed=True)

    return IdentityDecisionCommandResult(decision=decision, replayed=False)


def execute_create_recording_review(
    session: Session,
    command: CreateRecordingReviewCommand,
) -> IdentityDecisionCommandResult:
    """Atomically create a Recording, append its review, and project the owner."""

    _require_create_recording_command(command)
    decision = command.decision
    idempotency_scope = decision.idempotency_scope
    idempotency_key = decision.idempotency_key
    request_sha256 = decision.request_sha256

    def write(active_session: Session) -> MatchDecisionRow:
        active_session.add(command.recording)
        active_session.flush([command.recording])
        recording_id = command.recording.recording_id

        supplied_target = decision.candidate_recording_id
        if supplied_target is not None and supplied_target != recording_id:
            raise IdentityDecisionCommandContractError(
                "CREATE_RECORDING target must be the supplied transient Recording"
            )
        decision.candidate_recording_id = recording_id
        active_session.add(decision)
        active_session.flush([decision])
        decision_id = decision.decision_id

        for evidence in command.candidate_evidence:
            supplied_decision_id = evidence.decision_id
            if supplied_decision_id is not None and supplied_decision_id != decision_id:
                raise IdentityDecisionCommandContractError(
                    "candidate evidence belongs to another decision"
                )
            evidence.decision_id = decision_id
        active_session.add_all(command.candidate_evidence)
        command.project_owner(active_session, decision, command.recording)
        return decision

    result = execute_identity_decision_command(
        session,
        idempotency_scope=idempotency_scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        write=write,
    )
    _require_create_recording_decision(result.decision, len(command.candidate_evidence))
    return result


def _require_clean_session(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        raise IdentityDecisionCommandContractError(
            "identity command requires a clean Session at entry"
        )


def _require_idempotency_identity(
    decision: MatchDecisionRow,
    *,
    idempotency_scope: str,
    idempotency_key: str,
    request_sha256: bytes,
) -> None:
    if (
        decision.idempotency_scope != idempotency_scope
        or decision.idempotency_key != idempotency_key
        or not hmac.compare_digest(decision.request_sha256, request_sha256)
    ):
        raise IdentityDecisionCommandContractError(
            "decision idempotency fields do not match the command envelope"
        )


def _require_create_recording_command(command: CreateRecordingReviewCommand) -> None:
    if not inspect(command.recording).transient:
        raise IdentityDecisionCommandContractError("Recording row must be transient")
    if not inspect(command.decision).transient:
        raise IdentityDecisionCommandContractError("review decision row must be transient")
    if any(not inspect(evidence).transient for evidence in command.candidate_evidence):
        raise IdentityDecisionCommandContractError("candidate-evidence rows must be transient")
    _require_canonical_decision_graph(command.decision, command.candidate_evidence)
    _require_create_recording_decision(command.decision, len(command.candidate_evidence))


def _candidate_evidence_for_decision(
    session: Session,
    decision: MatchDecisionRow,
) -> tuple[MatchCandidateEvidenceRow, ...]:
    return tuple(
        session.scalars(
            select(MatchCandidateEvidenceRow)
            .where(MatchCandidateEvidenceRow.decision_id == decision.decision_id)
            .order_by(MatchCandidateEvidenceRow.rank)
        ).all()
    )


def _require_canonical_decision_graph(
    decision: MatchDecisionRow,
    candidate_evidence: Sequence[MatchCandidateEvidenceRow],
) -> None:
    try:
        if (
            decision.query_snapshot_schema_version != "1"
            or decision.snapshot_canonicalization_version != "RFC8785"
        ):
            raise IdentityDocumentError("decision requires query snapshot schema 1 / RFC8785")
        if not isinstance(decision.query_snapshot, dict):
            raise IdentityDocumentError("query_snapshot must be an object")
        query_document = canonical_query_snapshot(decision.query_snapshot)
        if not hmac.compare_digest(query_document.sha256, decision.query_snapshot_sha256):
            raise IdentityDocumentError("query snapshot SHA-256 does not match canonical bytes")

        ranked_hashes: list[tuple[int, bytes]] = []
        evidence_sizes: list[int] = []
        for evidence in sorted(candidate_evidence, key=lambda row: row.rank):
            if evidence.evidence_schema_version != "1":
                raise IdentityDocumentError("candidate evidence requires schema version 1")
            evidence_document = canonical_candidate_evidence(
                {
                    "recording_id": str(evidence.recording_id),
                    "raw_score": _json_score(evidence.raw_score),
                    "confidence": _json_score(evidence.confidence),
                    "evidence_tier": evidence.evidence_tier,
                    "feature_scores": evidence.feature_scores,
                    "hard_conflicts": evidence.hard_conflicts,
                    "candidate_origins": evidence.candidate_origins,
                    "extractor_versions": evidence.extractor_versions,
                }
            )
            if not hmac.compare_digest(evidence_document.sha256, evidence.evidence_sha256):
                raise IdentityDocumentError(
                    "candidate evidence SHA-256 does not match canonical bytes"
                )
            if evidence_document.byte_size != evidence.evidence_document_size_bytes:
                raise IdentityDocumentError(
                    "candidate evidence byte size does not match canonical bytes"
                )
            ranked_hashes.append((evidence.rank, evidence.evidence_sha256))
            evidence_sizes.append(evidence.evidence_document_size_bytes)

        aggregate_sha256, _ = candidate_aggregate_sha256(ranked_hashes)
        evidence_total = validate_candidate_evidence_total(evidence_sizes)
        if decision.candidate_count != len(candidate_evidence):
            raise IdentityDocumentError("decision candidate count does not match supplied rows")
        if not hmac.compare_digest(
            aggregate_sha256,
            decision.candidate_evidence_sha256,
        ):
            raise IdentityDocumentError("decision candidate aggregate SHA-256 does not match")
        if decision.candidate_evidence_size_bytes != evidence_total:
            raise IdentityDocumentError(
                "decision candidate-evidence byte total does not match supplied rows"
            )
    except IdentityDocumentError as error:
        raise IdentityDecisionCommandContractError(str(error)) from error


def _json_score(value: Decimal | None) -> JsonValue:
    return float(value) if value is not None else None


def _require_create_recording_decision(
    decision: MatchDecisionRow,
    evidence_count: int,
) -> None:
    if (
        decision.decision_kind != "REVIEW_ACTION"
        or decision.execution_mode != "APPLIED"
        or decision.review_action != "CREATE_RECORDING"
        or decision.reviewed_candidate_evidence_id is not None
        or decision.supersedes_decision_id is None
        or decision.query_type not in {"IMPORT_ENTRY", "USER_TRACK_REF"}
        or decision.candidate_count != evidence_count
    ):
        raise IdentityDecisionCommandContractError(
            "rows do not describe an applied CREATE_RECORDING owner review"
        )


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostics = getattr(error.orig, "diag", None)
    constraint_name = getattr(diagnostics, "constraint_name", None)
    return constraint_name if isinstance(constraint_name, str) else None


__all__ = (
    "CreateRecordingReviewCommand",
    "IdentityDecisionCommandContractError",
    "IdentityDecisionCommandResult",
    "IdentityDecisionIdempotencyConflict",
    "execute_create_recording_review",
    "execute_identity_decision_command",
)
