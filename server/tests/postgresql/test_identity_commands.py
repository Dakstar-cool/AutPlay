"""Production identity-decision persistence commands against real PostgreSQL."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.identity_decisions import (
    CreateRecordingReviewCommand,
    IdentityDecisionCommandContractError,
    IdentityDecisionIdempotencyConflict,
    execute_create_recording_review,
)
from autplay.adapters.postgresql.models import (
    MatchCandidateEvidenceRow,
    MatchDecisionRow,
    RecordingRow,
    UserTrackRefRow,
)
from autplay.application.identity_evidence import (
    candidate_aggregate_sha256,
    canonical_candidate_evidence,
    canonical_query_snapshot,
)
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

ProjectOwner = Callable[[Session, MatchDecisionRow, RecordingRow], None]


@dataclass(frozen=True, slots=True)
class IdentityFixture:
    engine: Engine
    user_id: UUID
    artist_credit_id: UUID
    user_track_ref_id: UUID
    predecessor_decision_id: UUID


@dataclass(frozen=True, slots=True)
class DatabaseCounts:
    recordings: int
    decisions: int
    candidate_evidence: int
    redirects: int


@pytest.fixture
def identity_fixture(database_url: str) -> Iterator[IdentityFixture]:
    """Seed one valid applied review-required UserTrackRef lineage."""

    engine = create_engine(database_url)
    suffix = uuid4().hex
    matcher_version = f"matcher-{suffix}"
    candidate_generation_version = f"generator-{suffix}"
    normalization_version = f"normalizer-{suffix}"

    try:
        with Session(engine) as session, session.begin():
            user_id = _uuid_scalar(
                session,
                """
                INSERT INTO account.user_account (display_name, role)
                VALUES (:display_name, 'USER') RETURNING user_id
                """,
                {"display_name": f"identity-user-{suffix}"},
            )
            artist_credit_id = _uuid_scalar(
                session,
                """
                INSERT INTO catalog.artist_credit (display_name, normalized_name)
                VALUES (:display_name, :normalized_name) RETURNING artist_credit_id
                """,
                {
                    "display_name": f"Identity Artist {suffix}",
                    "normalized_name": f"identity artist {suffix}",
                },
            )
            candidate_recording_id = _uuid_scalar(
                session,
                """
                INSERT INTO catalog.recording (
                    artist_credit_id, title, normalized_title, identity_status
                ) VALUES (
                    :artist_credit_id, :title, :normalized_title, 'PROVISIONAL'
                ) RETURNING recording_id
                """,
                {
                    "artist_credit_id": artist_credit_id,
                    "title": f"Candidate {suffix}",
                    "normalized_title": f"candidate {suffix}",
                },
            )
            query_document = canonical_query_snapshot({})
            evidence_document = canonical_candidate_evidence(
                {
                    "recording_id": str(candidate_recording_id),
                    "raw_score": 0.8,
                    "confidence": 0.8,
                    "evidence_tier": "T0",
                    "feature_scores": [],
                    "hard_conflicts": [],
                    "candidate_origins": [],
                    "extractor_versions": {},
                }
            )
            aggregate_sha256, _ = candidate_aggregate_sha256([(1, evidence_document.sha256)])
            user_track_ref_id = _uuid_scalar(
                session,
                """
                INSERT INTO library.user_track_ref (user_id, raw_title, raw_artist)
                VALUES (:user_id, :raw_title, :raw_artist)
                RETURNING user_track_ref_id
                """,
                {
                    "user_id": user_id,
                    "raw_title": f"Unresolved {suffix}",
                    "raw_artist": f"Identity Artist {suffix}",
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO identity.matcher_release (
                        matcher_version, candidate_generation_version,
                        normalization_version, feature_extractor_versions,
                        feature_schema_version, manifest_sha256
                    ) VALUES (
                        :matcher_version, :candidate_generation_version,
                        :normalization_version, '{}'::jsonb, '1', :manifest_sha256
                    )
                    """
                ),
                {
                    "matcher_version": matcher_version,
                    "candidate_generation_version": candidate_generation_version,
                    "normalization_version": normalization_version,
                    "manifest_sha256": hashlib.sha256(f"manifest-{suffix}".encode()).digest(),
                },
            )
            predecessor_decision_id = _uuid_scalar(
                session,
                """
                INSERT INTO identity.match_decision (
                    query_type, owner_user_id, user_track_ref_id, query_snapshot,
                    query_snapshot_schema_version, snapshot_canonicalization_version,
                    query_snapshot_sha256, decision_kind, execution_mode,
                    candidate_recording_id, decision_state, candidate_count,
                    candidate_evidence_sha256, candidate_evidence_size_bytes,
                    evidence_mode, candidate_generation_version, normalization_version,
                    feature_extractor_versions, matcher_version, raw_score, confidence,
                    evidence_tier, feature_scores, hard_conflicts, candidate_origins,
                    explanation_schema_version, actor_type, idempotency_scope,
                    idempotency_key, request_sha256, decided_at
                ) VALUES (
                    'USER_TRACK_REF', :user_id, :user_track_ref_id, '{}'::jsonb,
                    '1', 'RFC8785', :query_snapshot_sha256, 'EVALUATION', 'APPLIED',
                    :candidate_recording_id, 'REVIEW_REQUIRED', 1,
                    :candidate_evidence_sha256, :candidate_evidence_size_bytes,
                    'METADATA_ONLY',
                    :candidate_generation_version, :normalization_version,
                    '{}'::jsonb, :matcher_version, 0.8, 0.8, 'T0', '[]'::jsonb,
                    '[]'::jsonb, '[]'::jsonb, '1', 'SYSTEM', 'p02-fixture',
                    :idempotency_key, :request_sha256, :decided_at
                ) RETURNING decision_id
                """,
                {
                    "user_id": user_id,
                    "user_track_ref_id": user_track_ref_id,
                    "query_snapshot_sha256": query_document.sha256,
                    "candidate_recording_id": candidate_recording_id,
                    "candidate_evidence_sha256": aggregate_sha256,
                    "candidate_evidence_size_bytes": evidence_document.byte_size,
                    "candidate_generation_version": candidate_generation_version,
                    "normalization_version": normalization_version,
                    "matcher_version": matcher_version,
                    "idempotency_key": f"predecessor-{suffix}",
                    "request_sha256": hashlib.sha256(f"predecessor-{suffix}".encode()).digest(),
                    "decided_at": datetime.now(UTC) - timedelta(minutes=1),
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO identity.match_candidate_evidence (
                        decision_id, recording_id, rank, raw_score, confidence,
                        evidence_tier, feature_scores, hard_conflicts,
                        candidate_origins, extractor_versions,
                        evidence_schema_version, evidence_sha256,
                        evidence_document_size_bytes
                    ) VALUES (
                        :decision_id, :recording_id, 1, 0.8, 0.8, 'T0',
                        '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb,
                        '1', :evidence_sha256, :evidence_document_size_bytes
                    )
                    """
                ),
                {
                    "decision_id": predecessor_decision_id,
                    "recording_id": candidate_recording_id,
                    "evidence_sha256": evidence_document.sha256,
                    "evidence_document_size_bytes": evidence_document.byte_size,
                },
            )
            session.execute(
                text(
                    """
                    UPDATE library.user_track_ref
                    SET resolution_status = 'CANDIDATES',
                        resolution_confidence = 0.8,
                        current_match_decision_id = :decision_id
                    WHERE user_track_ref_id = :user_track_ref_id
                    """
                ),
                {
                    "decision_id": predecessor_decision_id,
                    "user_track_ref_id": user_track_ref_id,
                },
            )
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            session.execute(text("SET CONSTRAINTS ALL DEFERRED"))

        yield IdentityFixture(
            engine=engine,
            user_id=user_id,
            artist_credit_id=artist_credit_id,
            user_track_ref_id=user_track_ref_id,
            predecessor_decision_id=predecessor_decision_id,
        )
    finally:
        engine.dispose()


def test_create_recording_review_is_atomic_and_does_not_merge(
    identity_fixture: IdentityFixture,
) -> None:
    request_sha256 = hashlib.sha256(b"create-recording-success").digest()
    with Session(identity_fixture.engine) as session, session.begin():
        before = _counts(session)
        command = _create_recording_command(
            session,
            identity_fixture,
            idempotency_scope="p02-create-recording",
            idempotency_key="success",
            request_sha256=request_sha256,
        )

        result = execute_create_recording_review(session, command)

        assert result.replayed is False
        assert result.decision.decision_id == command.decision.decision_id
        assert result.decision.candidate_recording_id == command.recording.recording_id
        assert _counts(session) == DatabaseCounts(
            recordings=before.recordings + 1,
            decisions=before.decisions + 1,
            candidate_evidence=before.candidate_evidence + 1,
            redirects=0,
        )
        owner = session.get(UserTrackRefRow, identity_fixture.user_track_ref_id)
        assert owner is not None
        assert owner.current_match_decision_id == result.decision.decision_id
        assert owner.recording_id == command.recording.recording_id
        assert owner.resolution_status == "RESOLVED"


def test_same_request_hash_returns_stored_row_and_rolls_back_retry_rows(
    identity_fixture: IdentityFixture,
) -> None:
    scope = "p02-create-recording"
    key = "same-hash"
    request_sha256 = hashlib.sha256(b"same-hash").digest()
    with Session(identity_fixture.engine) as session, session.begin():
        first_command = _create_recording_command(
            session,
            identity_fixture,
            idempotency_scope=scope,
            idempotency_key=key,
            request_sha256=request_sha256,
        )
        first = execute_create_recording_review(session, first_command)
        after_first = _counts(session)
        projection_calls = 0

        def count_projection(
            _session: Session,
            _decision: MatchDecisionRow,
            _recording: RecordingRow,
        ) -> None:
            nonlocal projection_calls
            projection_calls += 1

        retry_command = _create_recording_command(
            session,
            identity_fixture,
            predecessor_decision_id=first.decision.decision_id,
            idempotency_scope=scope,
            idempotency_key=key,
            request_sha256=request_sha256,
            project_owner=count_projection,
        )
        replay = execute_create_recording_review(session, retry_command)

        assert replay.replayed is True
        assert replay.decision.decision_id == first.decision.decision_id
        assert projection_calls == 0
        assert _counts(session) == after_first
        assert after_first.redirects == 0


def test_different_request_hash_raises_stable_conflict_without_partial_rows(
    identity_fixture: IdentityFixture,
) -> None:
    scope = "p02-create-recording"
    key = "different-hash"
    with Session(identity_fixture.engine) as session, session.begin():
        first = execute_create_recording_review(
            session,
            _create_recording_command(
                session,
                identity_fixture,
                idempotency_scope=scope,
                idempotency_key=key,
                request_sha256=hashlib.sha256(b"request-a").digest(),
            ),
        )
        after_first = _counts(session)
        projection_calls = 0

        def count_projection(
            _session: Session,
            _decision: MatchDecisionRow,
            _recording: RecordingRow,
        ) -> None:
            nonlocal projection_calls
            projection_calls += 1

        conflicting_command = _create_recording_command(
            session,
            identity_fixture,
            predecessor_decision_id=first.decision.decision_id,
            idempotency_scope=scope,
            idempotency_key=key,
            request_sha256=hashlib.sha256(b"request-b").digest(),
            project_owner=count_projection,
        )

        with pytest.raises(IdentityDecisionIdempotencyConflict) as exc_info:
            execute_create_recording_review(session, conflicting_command)

        assert exc_info.value.code == "identity_decision_idempotency_conflict"
        assert str(exc_info.value) == exc_info.value.code
        assert projection_calls == 0
        assert _counts(session) == after_first
        assert after_first.redirects == 0


def test_projection_failure_rolls_back_recording_decision_and_evidence(
    identity_fixture: IdentityFixture,
) -> None:
    def omit_projection(
        _session: Session,
        _decision: MatchDecisionRow,
        _recording: RecordingRow,
    ) -> None:
        return None

    with Session(identity_fixture.engine) as session, session.begin():
        before = _counts(session)
        command = _create_recording_command(
            session,
            identity_fixture,
            idempotency_scope="p02-create-recording",
            idempotency_key="missing-projection",
            request_sha256=hashlib.sha256(b"missing-projection").digest(),
            project_owner=omit_projection,
        )

        with pytest.raises(DBAPIError) as exc_info:
            execute_create_recording_review(session, command)

        assert "projected atomically" in str(exc_info.value.orig)
        assert _counts(session) == before
        owner = session.get(UserTrackRefRow, identity_fixture.user_track_ref_id)
        assert owner is not None
        assert owner.current_match_decision_id == identity_fixture.predecessor_decision_id
        assert owner.recording_id is None
        assert before.redirects == 0


def test_non_idempotency_integrity_error_propagates_unchanged(
    identity_fixture: IdentityFixture,
) -> None:
    with Session(identity_fixture.engine) as session, session.begin():
        before = _counts(session)
        command = _create_recording_command(
            session,
            identity_fixture,
            artist_credit_id=uuid4(),
            idempotency_scope="p02-create-recording",
            idempotency_key="bad-artist-credit",
            request_sha256=hashlib.sha256(b"bad-artist-credit").digest(),
        )

        with pytest.raises(IntegrityError) as exc_info:
            execute_create_recording_review(session, command)

        diagnostics = getattr(exc_info.value.orig, "diag", None)
        assert getattr(diagnostics, "constraint_name", None) == "recording_artist_credit_id_fkey"
        assert _counts(session) == before


@pytest.mark.parametrize(
    "tamper",
    (
        "query_hash",
        "query_sensitive",
        "evidence_hash",
        "evidence_sensitive",
        "evidence_size",
    ),
)
def test_command_rejects_noncanonical_or_sensitive_documents_before_write(
    identity_fixture: IdentityFixture,
    tamper: str,
) -> None:
    with Session(identity_fixture.engine) as session, session.begin():
        before = _counts(session)
        command = _create_recording_command(
            session,
            identity_fixture,
            idempotency_scope="p02-create-recording",
            idempotency_key=f"canonical-{tamper}",
            request_sha256=hashlib.sha256(f"canonical-{tamper}".encode()).digest(),
        )
        evidence = command.candidate_evidence[0]
        if tamper == "query_hash":
            command.decision.query_snapshot_sha256 = bytes(32)
        elif tamper == "query_sensitive":
            command.decision.query_snapshot = {
                "evidence_ids": [{"privateUrl": "https://secret.invalid"}]
            }
        elif tamper == "evidence_hash":
            evidence.evidence_sha256 = bytes(32)
        elif tamper == "evidence_sensitive":
            evidence.candidate_origins = [{"source": "fixture", "nested": {"apiKey": "secret"}}]
        else:
            evidence.evidence_document_size_bytes += 1

        with pytest.raises(IdentityDecisionCommandContractError):
            execute_create_recording_review(session, command)

        assert _counts(session) == before
        assert not session.new
        assert not session.dirty


def _create_recording_command(
    session: Session,
    fixture: IdentityFixture,
    *,
    idempotency_scope: str,
    idempotency_key: str,
    request_sha256: bytes,
    predecessor_decision_id: UUID | None = None,
    artist_credit_id: UUID | None = None,
    project_owner: ProjectOwner | None = None,
) -> CreateRecordingReviewCommand:
    predecessor = session.get(
        MatchDecisionRow,
        predecessor_decision_id or fixture.predecessor_decision_id,
    )
    if predecessor is None:
        raise AssertionError("identity fixture predecessor is missing")
    predecessor_evidence = tuple(
        session.scalars(
            select(MatchCandidateEvidenceRow)
            .where(MatchCandidateEvidenceRow.decision_id == predecessor.decision_id)
            .order_by(MatchCandidateEvidenceRow.rank)
        ).all()
    )
    title = f"Created Recording {uuid4().hex}"
    recording = RecordingRow(
        artist_credit_id=artist_credit_id or fixture.artist_credit_id,
        title=title,
        normalized_title=title.lower(),
        identity_status="PROVISIONAL",
    )
    decision = MatchDecisionRow(
        query_type=predecessor.query_type,
        owner_user_id=predecessor.owner_user_id,
        device_id=predecessor.device_id,
        import_entry_id=predecessor.import_entry_id,
        user_track_ref_id=predecessor.user_track_ref_id,
        local_audio_id=predecessor.local_audio_id,
        external_reference_id=predecessor.external_reference_id,
        vault_object_id=predecessor.vault_object_id,
        audio_variant_id=predecessor.audio_variant_id,
        query_snapshot=deepcopy(predecessor.query_snapshot),
        query_snapshot_schema_version=predecessor.query_snapshot_schema_version,
        snapshot_canonicalization_version=predecessor.snapshot_canonicalization_version,
        query_snapshot_sha256=predecessor.query_snapshot_sha256,
        decision_kind="REVIEW_ACTION",
        execution_mode="APPLIED",
        review_action="CREATE_RECORDING",
        reviewed_candidate_evidence_id=None,
        candidate_recording_id=None,
        decision_state=predecessor.decision_state,
        candidate_count=predecessor.candidate_count,
        candidate_evidence_sha256=predecessor.candidate_evidence_sha256,
        candidate_evidence_size_bytes=predecessor.candidate_evidence_size_bytes,
        evidence_mode=predecessor.evidence_mode,
        candidate_generation_version=predecessor.candidate_generation_version,
        normalization_version=predecessor.normalization_version,
        feature_extractor_versions=deepcopy(predecessor.feature_extractor_versions),
        matcher_version=predecessor.matcher_version,
        calibrator_version=predecessor.calibrator_version,
        threshold_set_version=predecessor.threshold_set_version,
        raw_score=predecessor.raw_score,
        confidence=predecessor.confidence,
        top2_confidence=predecessor.top2_confidence,
        margin=predecessor.margin,
        evidence_tier=predecessor.evidence_tier,
        feature_scores=deepcopy(predecessor.feature_scores),
        hard_conflicts=deepcopy(predecessor.hard_conflicts),
        candidate_origins=deepcopy(predecessor.candidate_origins),
        explanation_schema_version=predecessor.explanation_schema_version,
        actor_type="USER",
        actor_user_id=fixture.user_id,
        idempotency_scope=idempotency_scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        supersedes_decision_id=predecessor.decision_id,
        supersession_reason="P02 CREATE_RECORDING command test",
        decided_at=predecessor.decided_at + timedelta(microseconds=1),
    )
    copied_evidence = tuple(
        MatchCandidateEvidenceRow(
            recording_id=evidence.recording_id,
            rank=evidence.rank,
            raw_score=evidence.raw_score,
            confidence=evidence.confidence,
            evidence_tier=evidence.evidence_tier,
            feature_scores=deepcopy(evidence.feature_scores),
            hard_conflicts=deepcopy(evidence.hard_conflicts),
            candidate_origins=deepcopy(evidence.candidate_origins),
            extractor_versions=deepcopy(evidence.extractor_versions),
            evidence_schema_version=evidence.evidence_schema_version,
            evidence_sha256=evidence.evidence_sha256,
            evidence_document_size_bytes=evidence.evidence_document_size_bytes,
        )
        for evidence in predecessor_evidence
    )

    if project_owner is None:

        def project_owner(
            active_session: Session,
            persisted_decision: MatchDecisionRow,
            created_recording: RecordingRow,
        ) -> None:
            owner = active_session.get(UserTrackRefRow, fixture.user_track_ref_id)
            if owner is None:
                raise AssertionError("identity fixture owner projection is missing")
            owner.recording_id = created_recording.recording_id
            owner.resolution_status = "RESOLVED"
            owner.current_match_decision_id = persisted_decision.decision_id
            owner.resolved_at = persisted_decision.decided_at
            owner.resolution_confidence = persisted_decision.confidence

    return CreateRecordingReviewCommand(
        recording=recording,
        decision=decision,
        candidate_evidence=copied_evidence,
        project_owner=project_owner,
    )


def _uuid_scalar(
    session: Session,
    statement: str,
    parameters: Mapping[str, object],
) -> UUID:
    value = session.execute(text(statement), parameters).scalar_one()
    if not isinstance(value, UUID):
        raise AssertionError("INSERT ... RETURNING did not return a UUID")
    return value


def _counts(session: Session) -> DatabaseCounts:
    return DatabaseCounts(
        recordings=_count(session, "SELECT count(*) FROM catalog.recording"),
        decisions=_count(session, "SELECT count(*) FROM identity.match_decision"),
        candidate_evidence=_count(
            session,
            "SELECT count(*) FROM identity.match_candidate_evidence",
        ),
        redirects=_count(session, "SELECT count(*) FROM identity.recording_redirect"),
    )


def _count(session: Session, statement: str) -> int:
    value = session.scalar(text(statement))
    if not isinstance(value, int):
        raise AssertionError("COUNT query did not return an integer")
    return value
