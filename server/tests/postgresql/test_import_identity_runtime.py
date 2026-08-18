"""Real-PostgreSQL P10 import, review, resume, and change-set evidence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import autplay.adapters.postgresql.import_runtime as import_runtime
import pytest
from autplay.adapters.postgresql.catalog_changes import (
    CatalogChangeConflict,
    PostgresCatalogChangeRepository,
)
from autplay.adapters.postgresql.import_runtime import (
    IMPORT_JOB_KEY,
    ImportReviewConflictError,
    PostgresImportRepository,
)
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    AuditEventRow,
    CatalogChangeSetRow,
    ImportEntryRow,
    MatchDecisionRow,
    MatcherReleaseRow,
    RecordingRedirectRow,
    RecordingRow,
    UserTrackRefRow,
)
from autplay.application.imports import ImportJobHandler, ImportService
from autplay.application.job_worker import (
    JobHandlerRegistry,
    JobWorker,
    JobWorkerSettings,
    WorkerOutcome,
)
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.import_identity import CatalogCandidate, IdentityTrack, ParsedImportRow
from autplay.domain.jobs import CancelRequestResult, RetryPolicy
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker


def test_probabilistic_import_stays_shadow_until_explicit_manual_resolution(
    database_url: str,
) -> None:
    engine, sessions, principal = _runtime(database_url)
    try:
        recording_id = _insert_recording(engine, "Song", "Artist")
        service = ImportService(sessions)
        payload = (
            b"title,artist,duration_ms,playlist,source_url,password,unknown\n"
            b"Song,Artist,180000,one,https://private.invalid/item,do-not-report,a\n"
            b"Song,Artist,180000,two,https://private.invalid/item,do-not-report,b\n"
            b"Broken,columns\n"
        )

        created = service.start(
            principal,
            payload=payload,
            format_name="CSV",
            schema_version="1",
            mode="LIBRARY_ONLY",
        )
        replayed = service.start(
            principal,
            payload=payload,
            format_name="CSV",
            schema_version="1",
            mode="LIBRARY_ONLY",
        )
        assert replayed.replayed is True
        assert replayed.import_job_id == created.import_job_id
        assert _run_import_worker(sessions) is WorkerOutcome.COMPLETED

        report = service.report(principal, created.import_job_id)
        assert report.state == "COMPLETED"
        assert report.progress_current == report.progress_total == 3
        assert report.counts == {"PENDING": 2, "REJECTED": 1}
        assert [item.status for item in report.entries].count("PENDING") == 2
        assert all(item.resolver_state != "AUTO_MATCH" for item in report.entries)
        serialized_report = repr(report)
        assert "do-not-report" not in serialized_report
        assert "private.invalid" not in serialized_report

        reviewable = next(
            item for item in report.entries if item.resolver_state == "REVIEW_REQUIRED"
        )
        assert reviewable.decision_id is not None and reviewable.candidate_count >= 1
        with Session(engine) as session:
            shadow_entry = session.get(ImportEntryRow, reviewable.import_entry_id)
            shadow_decision = session.get(MatchDecisionRow, reviewable.decision_id)
            assert shadow_entry is not None and shadow_decision is not None
            assert shadow_decision.execution_mode == "SHADOW"
            assert shadow_entry.match_status == "PENDING"
            assert shadow_entry.current_match_decision_id is None
            assert shadow_entry.selected_recording_id is None
            assert shadow_entry.user_track_ref_id is None
        reviewed = service.review(
            principal,
            created.import_job_id,
            reviewable.import_entry_id,
            predecessor_decision_id=reviewable.decision_id,
            action="ACCEPT",
            selected_rank=1,
            idempotency_key="accept-first-row",
        )
        assert reviewed.status == "MANUAL_MATCH"
        assert reviewed.recording_id == recording_id
        other = next(
            item
            for item in report.entries
            if item.resolver_state == "REVIEW_REQUIRED"
            and item.import_entry_id != reviewable.import_entry_id
        )
        assert other.decision_id is not None
        with pytest.raises(ImportReviewConflictError):
            service.review(
                principal,
                created.import_job_id,
                other.import_entry_id,
                predecessor_decision_id=other.decision_id,
                action="ACCEPT",
                selected_rank=1,
                idempotency_key="accept-first-row",
            )
        reviewed_other = service.review(
            principal,
            created.import_job_id,
            other.import_entry_id,
            predecessor_decision_id=other.decision_id,
            action="ACCEPT",
            selected_rank=1,
            idempotency_key="accept-second-row",
        )
        assert reviewed_other.status == "MANUAL_MATCH"
        assert reviewed_other.recording_id == recording_id

        with Session(engine) as session:
            entry = session.get(ImportEntryRow, reviewable.import_entry_id)
            other_entry = session.get(ImportEntryRow, other.import_entry_id)
            review_decision = session.get(MatchDecisionRow, reviewed.decision_id)
            other_review_decision = session.get(MatchDecisionRow, reviewed_other.decision_id)
            assert entry is not None and other_entry is not None
            assert review_decision is not None and other_review_decision is not None
            assert entry.current_match_decision_id == reviewed.decision_id
            assert entry.selected_recording_id == recording_id
            assert entry.match_status == "MANUAL_MATCH"
            assert entry.user_track_ref_id is not None
            assert other_entry.current_match_decision_id == reviewed_other.decision_id
            assert other_entry.selected_recording_id == recording_id
            assert other_entry.match_status == "MANUAL_MATCH"
            assert other_entry.user_track_ref_id == entry.user_track_ref_id
            assert reviewed_other.decision_id != reviewed.decision_id
            assert other_review_decision.query_type == "IMPORT_ENTRY"
            assert other_review_decision.decision_kind == "REVIEW_ACTION"
            assert other_review_decision.supersedes_decision_id == other.decision_id
            assert review_decision.execution_mode == "APPLIED"
            assert review_decision.decision_kind == "REVIEW_ACTION"
            assert review_decision.review_action == "ACCEPT"
            assert review_decision.supersedes_decision_id == reviewable.decision_id
            user_ref = session.get(UserTrackRefRow, entry.user_track_ref_id)
            assert user_ref is not None
            assert user_ref.recording_id == recording_id
            assert user_ref.resolution_status == "RESOLVED"
            assert user_ref.current_match_decision_id is not None
            user_review = session.get(MatchDecisionRow, user_ref.current_match_decision_id)
            assert user_review is not None
            assert user_review.query_type == "USER_TRACK_REF"
            assert user_review.execution_mode == "APPLIED"
            assert user_review.decision_kind == "REVIEW_ACTION"
            assert user_review.review_action == "ACCEPT"
            assert user_review.decision_id != review_decision.decision_id
            assert user_review.decision_id != other_review_decision.decision_id
            assert user_review.supersedes_decision_id is not None
            user_evaluation = session.get(MatchDecisionRow, user_review.supersedes_decision_id)
            assert user_evaluation is not None
            assert user_evaluation.query_type == "USER_TRACK_REF"
            assert user_evaluation.execution_mode == "SHADOW"
            assert user_evaluation.decision_kind == "EVALUATION"
            assert user_evaluation.supersedes_decision_id is None
            user_lineage = tuple(
                session.scalars(
                    select(MatchDecisionRow).where(
                        MatchDecisionRow.query_type == "USER_TRACK_REF",
                        MatchDecisionRow.user_track_ref_id == user_ref.user_track_ref_id,
                    )
                ).all()
            )
            user_evaluations = {
                decision.decision_id
                for decision in user_lineage
                if decision.decision_kind == "EVALUATION"
            }
            user_reviews = tuple(
                decision for decision in user_lineage if decision.decision_kind == "REVIEW_ACTION"
            )
            assert len(user_evaluations) == 2
            assert len(user_reviews) == 2
            assert {decision.supersedes_decision_id for decision in user_reviews} == (
                user_evaluations
            )
            assert all(
                decision.execution_mode == "SHADOW"
                for decision in user_lineage
                if decision.decision_kind == "EVALUATION"
            )
            assert all(decision.execution_mode == "APPLIED" for decision in user_reviews)
            activation_count = session.scalar(
                text("SELECT count(*) FROM identity.match_policy_activation")
            )
            raw_payload = entry.raw_payload
            assert isinstance(raw_payload, dict)
            raw_fields = raw_payload["fields"]
            assert isinstance(raw_fields, dict)
            assert raw_fields["password"] == "do-not-report"
            playlist_values: set[str] = set()
            for imported in (entry, other_entry):
                imported_payload = imported.raw_payload
                assert isinstance(imported_payload, dict)
                imported_fields = imported_payload["fields"]
                assert isinstance(imported_fields, dict)
                playlist = imported_fields["playlist"]
                assert isinstance(playlist, str)
                playlist_values.add(playlist)
            assert playlist_values == {"one", "two"}
            active_user_refs = session.scalars(
                select(UserTrackRefRow).where(
                    UserTrackRefRow.user_id == principal.user_id,
                    UserTrackRefRow.recording_id == recording_id,
                    UserTrackRefRow.deleted_at.is_(None),
                )
            ).all()
            assert [row.user_track_ref_id for row in active_user_refs] == [entry.user_track_ref_id]
        assert activation_count == 0
    finally:
        engine.dispose()


def test_exact_identifier_and_fingerprint_candidates_bypass_bad_metadata(
    database_url: str,
) -> None:
    engine, sessions, principal = _runtime(database_url)
    try:
        identifier_recording_id = _insert_recording(engine, "Known Identifier", "Known Artist")
        fingerprint_recording_id = _insert_recording(engine, "Known Audio", "Known Artist")
        _insert_identifier(engine, identifier_recording_id, "ISRC", "USAAA2400001")
        fingerprint = bytes.fromhex("0123456789abcdef0123456789abcdef")
        _insert_fingerprint(engine, fingerprint_recording_id, fingerprint)
        service = ImportService(sessions)
        created = service.start(
            principal,
            payload=(
                b"title,artist,isrc,fingerprint,fingerprint_algorithm,"
                b"fingerprint_version,fingerprint_coverage\n"
                b"Unrelated title,Wrong artist,USAAA2400001,,,,\n"
                b"Other title,Other artist,,0123456789abcdef0123456789abcdef,"
                b"CHROMAPRINT,1,1.0\n"
            ),
            format_name="CSV",
            schema_version="1",
            mode="LIBRARY_ONLY",
        )
        assert _run_import_worker(sessions) is WorkerOutcome.COMPLETED
        report = service.report(principal, created.import_job_id)
        assert len(report.entries) == 2
        assert all(entry.status == "PENDING" for entry in report.entries)
        assert all(entry.candidate_count == 1 for entry in report.entries)
        assert all(entry.resolver_state == "REVIEW_REQUIRED" for entry in report.entries)
        with Session(engine) as session:
            imported_entries = tuple(
                session.scalars(
                    select(ImportEntryRow).where(
                        ImportEntryRow.import_job_id == created.import_job_id
                    )
                ).all()
            )
            assert all(entry.current_match_decision_id is None for entry in imported_entries)
            assert all(entry.selected_recording_id is None for entry in imported_entries)
            assert all(entry.user_track_ref_id is None for entry in imported_entries)
            candidate_ids = set(
                session.scalars(
                    text(
                        "SELECT recording_id FROM identity.match_candidate_evidence "
                        "ORDER BY recording_id"
                    )
                ).all()
            )
        assert candidate_ids == {identifier_recording_id, fingerprint_recording_id}
    finally:
        engine.dispose()


def test_matcher_release_registration_is_concurrent_and_idempotent(database_url: str) -> None:
    engine, sessions, _ = _runtime(database_url)
    try:

        def register() -> None:
            with sessions() as session:
                PostgresImportRepository(session).ensure_matcher_release()
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(register) for _ in range(2)]
            for future in futures:
                future.result(timeout=10)
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(MatcherReleaseRow)) == 1
    finally:
        engine.dispose()


def test_t4_shadow_decision_never_projects_import_or_user_ref(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, sessions, principal = _runtime(database_url)
    try:
        recording_id = _insert_recording(engine, "Known Bytes", "Known Artist")
        verified_sha = "a1" * 32
        service = ImportService(sessions)
        created = service.start(
            principal,
            payload=b"title,artist\nKnown Bytes,Known Artist\n",
            format_name="CSV",
            schema_version="1",
            mode="LIBRARY_ONLY",
        )

        def trusted_query(row: ParsedImportRow) -> IdentityTrack:
            del row
            return IdentityTrack(
                "Known Bytes",
                ("Known Artist",),
                duration_ms=180_000,
                verified_vault_sha256=verified_sha,
            )

        def deterministic_candidate(
            repository: PostgresImportRepository, query: IdentityTrack
        ) -> tuple[CatalogCandidate, ...]:
            del repository, query
            return (
                CatalogCandidate(
                    recording_id,
                    "Known Bytes",
                    ("Known Artist",),
                    duration_ms=180_000,
                    verified_vault_sha256=verified_sha,
                    origins=("EXACT_SHA256",),
                ),
            )

        monkeypatch.setattr(import_runtime, "identity_track_from_row", trusted_query)
        monkeypatch.setattr(
            PostgresImportRepository, "_catalog_candidates", deterministic_candidate
        )
        with sessions() as session:
            repository = PostgresImportRepository(session)
            repository.ensure_matcher_release()
            progress = repository.process_next(import_job_id=created.import_job_id)
            session.commit()
        assert progress.finished is True
        report = service.report(principal, created.import_job_id)
        assert report.entries[0].status == "PENDING"
        assert report.entries[0].resolver_state == "REVIEW_REQUIRED"
        with Session(engine) as session:
            entry = session.get(ImportEntryRow, report.entries[0].import_entry_id)
            decision = session.get(MatchDecisionRow, report.entries[0].decision_id)
            assert entry is not None and decision is not None
            assert entry.current_match_decision_id is None
            assert entry.user_track_ref_id is None
            assert entry.selected_recording_id is None
            assert decision.execution_mode == "SHADOW"
            assert decision.evidence_mode == "DETERMINISTIC_BYTES"
            assert decision.candidate_recording_id == recording_id
    finally:
        engine.dispose()


def test_t0_metadata_only_evaluation_remains_shadow_without_projection(
    database_url: str,
) -> None:
    engine, sessions, principal = _runtime(database_url)
    try:
        recording_id = _insert_recording(engine, "Metadata Only", "Known Artist")
        service = ImportService(sessions)
        created = service.start(
            principal,
            payload=b"title,artist\nMetadata Only,Known Artist\n",
            format_name="CSV",
            schema_version="1",
            mode="LIBRARY_ONLY",
        )
        assert _run_import_worker(sessions) is WorkerOutcome.COMPLETED
        report = service.report(principal, created.import_job_id)
        assert len(report.entries) == 1
        reported = report.entries[0]
        assert reported.status == "PENDING"
        assert reported.resolver_state == "REVIEW_REQUIRED"
        assert reported.decision_id is not None

        with Session(engine) as session:
            entry = session.get(ImportEntryRow, reported.import_entry_id)
            decision = session.get(MatchDecisionRow, reported.decision_id)
            assert entry is not None and decision is not None
            assert decision.evidence_tier == "T0"
            assert decision.execution_mode == "SHADOW"
            assert decision.candidate_recording_id == recording_id
            assert entry.match_status == "PENDING"
            assert entry.current_match_decision_id is None
            assert entry.selected_recording_id is None
            assert entry.user_track_ref_id is None
            assert session.scalar(select(func.count()).select_from(UserTrackRefRow)) == 0
    finally:
        engine.dispose()


def test_cancelled_import_resumes_from_committed_entry_checkpoint(database_url: str) -> None:
    engine, sessions, principal = _runtime(database_url)
    try:
        _insert_recording(engine, "Song", "Artist")
        service = ImportService(sessions)
        payload = (
            b"title,artist,duration_ms\n"
            b"Song,Artist,180000\n"
            b"Song,Artist,180000\n"
            b"Missing,Artist,190000\n"
        )
        created = service.start(
            principal,
            payload=payload,
            format_name="CSV",
            schema_version="1",
            mode="LIBRARY_ONLY",
        )
        with sessions() as session:
            repository = PostgresImportRepository(session)
            repository.ensure_matcher_release()
            first = repository.process_next(import_job_id=created.import_job_id)
            session.commit()
        assert first.processed_rows == 1 and first.finished is False
        assert service.cancel(principal, created.import_job_id) is CancelRequestResult.CANCELLED

        resumed = service.resume(principal, created.import_job_id)
        assert resumed.delivery_job_id != created.delivery_job_id
        retried = service.start(
            principal,
            payload=payload,
            format_name="CSV",
            schema_version="1",
            mode="LIBRARY_ONLY",
        )
        assert retried.replayed is True
        assert retried.delivery_job_id == resumed.delivery_job_id
        resumed_report = service.report(principal, created.import_job_id)
        assert resumed_report.progress_current == 1
        assert _run_import_worker(sessions) is WorkerOutcome.COMPLETED

        final_report = service.report(principal, created.import_job_id)
        assert final_report.progress_current == final_report.progress_total == 3
        with Session(engine) as session:
            decision_count = session.scalar(
                select(func.count())
                .select_from(MatchDecisionRow)
                .where(MatchDecisionRow.query_type == "IMPORT_ENTRY")
            )
        assert decision_count == 3
    finally:
        engine.dispose()


def test_merge_change_set_apply_and_undo_are_explicit_and_audited(database_url: str) -> None:
    engine, sessions, principal = _runtime(database_url)
    try:
        source_id = _insert_recording(engine, "Source", "Artist")
        target_id = _insert_recording(engine, "Target", "Artist")
        now = datetime.now(UTC)
        with sessions() as session:
            repository = PostgresCatalogChangeRepository(session)
            proposed = repository.propose_recording_change(
                principal=principal,
                operation_type="MERGE",
                source_recording_id=source_id,
                target_recording_id=target_id,
                reason="Explicit P10 merge review",
                now=now,
            )
            session.commit()
        with sessions() as session:
            applied = PostgresCatalogChangeRepository(session).apply(
                principal=principal,
                change_set_id=proposed.change_set_id,
                now=now + timedelta(seconds=1),
            )
            session.commit()
        assert applied.status == "APPLIED"
        with Session(engine) as session:
            source = session.get(RecordingRow, source_id)
            redirect = session.get(RecordingRedirectRow, source_id)
            assert source is not None and source.identity_status == "MERGED"
            assert redirect is not None and redirect.target_recording_id == target_id

        with sessions() as session:
            undone = PostgresCatalogChangeRepository(session).undo(
                principal=principal,
                change_set_id=proposed.change_set_id,
                now=now + timedelta(seconds=2),
            )
            session.commit()
        assert undone.status == "REVERTED" and undone.inverse_change_set_id is not None
        with Session(engine) as session:
            source = session.get(RecordingRow, source_id)
            assert source is not None and source.identity_status == "ACTIVE"
            assert session.get(RecordingRedirectRow, source_id) is None
            original = session.get(CatalogChangeSetRow, proposed.change_set_id)
            inverse = session.get(CatalogChangeSetRow, undone.inverse_change_set_id)
            assert original is not None and original.status == "REVERTED"
            assert inverse is not None and inverse.operation_type == "UNDO"
            audit_count = session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.target_type == "CATALOG_CHANGE_SET")
            )
        assert audit_count == 3
    finally:
        engine.dispose()


def test_split_undo_restores_redirect_with_inverse_change_set_lineage(
    database_url: str,
) -> None:
    engine, sessions, principal = _runtime(database_url)
    try:
        source_id = _insert_recording(engine, "Split Source", "Artist")
        target_id = _insert_recording(engine, "Split Target", "Artist")
        now = datetime.now(UTC)
        with sessions() as session:
            repository = PostgresCatalogChangeRepository(session)
            merge = repository.propose_recording_change(
                principal=principal,
                operation_type="MERGE",
                source_recording_id=source_id,
                target_recording_id=target_id,
                reason="Prepare explicit split fixture",
                now=now,
            )
            session.commit()
        with sessions() as session:
            PostgresCatalogChangeRepository(session).apply(
                principal=principal,
                change_set_id=merge.change_set_id,
                now=now + timedelta(seconds=1),
            )
            session.commit()
        with sessions() as session:
            repository = PostgresCatalogChangeRepository(session)
            split = repository.propose_recording_change(
                principal=principal,
                operation_type="SPLIT",
                source_recording_id=source_id,
                target_recording_id=target_id,
                reason="Explicit split review",
                now=now + timedelta(seconds=2),
            )
            session.commit()
        with sessions() as session:
            PostgresCatalogChangeRepository(session).apply(
                principal=principal,
                change_set_id=split.change_set_id,
                now=now + timedelta(seconds=3),
            )
            session.commit()
        with Session(engine) as session:
            assert session.get(RecordingRedirectRow, source_id) is None

        with sessions() as session:
            undone = PostgresCatalogChangeRepository(session).undo(
                principal=principal,
                change_set_id=split.change_set_id,
                now=now + timedelta(seconds=4),
            )
            session.commit()
        assert undone.inverse_change_set_id is not None
        with Session(engine) as session:
            redirect = session.get(RecordingRedirectRow, source_id)
            source = session.get(RecordingRow, source_id)
            assert redirect is not None and source is not None
            assert source.identity_status == "MERGED"
            assert redirect.target_recording_id == target_id
            assert redirect.change_set_id == undone.inverse_change_set_id
            assert redirect.change_set_id != split.change_set_id
    finally:
        engine.dispose()


def test_overlapping_merges_cannot_leave_redirect_to_merged_target(
    database_url: str,
) -> None:
    engine, sessions, principal = _runtime(database_url)
    try:
        recording_a = _insert_recording(engine, "Concurrent A", "Artist")
        recording_b = _insert_recording(engine, "Concurrent B", "Artist")
        recording_c = _insert_recording(engine, "Concurrent C", "Artist")
        now = datetime.now(UTC)
        proposals = []
        for source_id, target_id in (
            (recording_a, recording_b),
            (recording_b, recording_c),
        ):
            with sessions() as session:
                proposal = PostgresCatalogChangeRepository(session).propose_recording_change(
                    principal=principal,
                    operation_type="MERGE",
                    source_recording_id=source_id,
                    target_recording_id=target_id,
                    reason="Concurrent overlap review",
                    now=now,
                )
                session.commit()
                proposals.append(proposal.change_set_id)

        barrier = Barrier(2)

        def apply(change_set_id: UUID) -> str:
            barrier.wait(timeout=10)
            with sessions() as session:
                try:
                    PostgresCatalogChangeRepository(session).apply(
                        principal=principal,
                        change_set_id=change_set_id,
                        now=now + timedelta(seconds=1),
                    )
                    session.commit()
                    return "APPLIED"
                except CatalogChangeConflict:
                    session.rollback()
                    return "CONFLICT"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(apply, proposals))
        assert sorted(results) == ["APPLIED", "CONFLICT"]
        with Session(engine) as session:
            invalid_redirects = session.scalar(
                text(
                    "SELECT count(*) FROM identity.recording_redirect rr "
                    "JOIN catalog.recording target "
                    "ON target.recording_id = rr.target_recording_id "
                    "WHERE target.identity_status = 'MERGED'"
                )
            )
        assert invalid_redirects == 0
    finally:
        engine.dispose()


def _runtime(
    database_url: str,
) -> tuple[Engine, sessionmaker[Session], Principal]:
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    with Session(engine) as session, session.begin():
        user_id = _uuid(
            session,
            """
            INSERT INTO account.user_account (display_name, role)
            VALUES (:name, 'OWNER') RETURNING user_id
            """,
            {"name": f"p10-owner-{uuid4().hex[:10]}"},
        )
        device_id = _uuid(
            session,
            """
            INSERT INTO account.device (user_id, device_name, platform, app_version)
            VALUES (:user_id, :name, 'ANDROID', 'p10') RETURNING device_id
            """,
            {"user_id": user_id, "name": f"p10-device-{uuid4().hex[:8]}"},
        )
    return engine, sessions, Principal(user_id, device_id, uuid4(), AccountRole.OWNER)


def _insert_recording(engine: Engine, title: str, artist: str) -> UUID:
    with Session(engine) as session, session.begin():
        credit_id = _uuid(
            session,
            """
            INSERT INTO catalog.artist_credit (display_name, normalized_name)
            VALUES (:artist, lower(:artist)) RETURNING artist_credit_id
            """,
            {"artist": artist},
        )
        return _uuid(
            session,
            """
            INSERT INTO catalog.recording (
                artist_credit_id, title, normalized_title, duration_ms, identity_status
            ) VALUES (:credit_id, :title, lower(:title), 180000, 'ACTIVE')
            RETURNING recording_id
            """,
            {"credit_id": credit_id, "title": title},
        )


def _insert_identifier(engine: Engine, recording_id: UUID, scheme: str, value: str) -> None:
    with Session(engine) as session, session.begin():
        session.execute(
            text(
                "INSERT INTO identity.recording_identifier "
                "(recording_id, scheme, value, confidence, verified) "
                "VALUES (:recording_id, :scheme, :value, 1, true)"
            ),
            {"recording_id": recording_id, "scheme": scheme, "value": value},
        )


def _insert_fingerprint(engine: Engine, recording_id: UUID, fingerprint: bytes) -> None:
    with Session(engine) as session, session.begin():
        vault_object_id = _uuid(
            session,
            """
            INSERT INTO vault.vault_object (
                sha256, byte_size, detected_mime_type, commit_status, committed_at
            ) VALUES (:sha256, 1024, 'audio/flac', 'COMMITTED', now())
            RETURNING vault_object_id
            """,
            {"sha256": bytes.fromhex(uuid4().hex + uuid4().hex)},
        )
        audio_variant_id = _uuid(
            session,
            """
            INSERT INTO vault.audio_variant (
                recording_id, vault_object_id, codec, container,
                sample_rate_hz, channels, duration_ms, validation_status
            ) VALUES (
                :recording_id, :vault_object_id, 'FLAC', 'FLAC',
                44100, 2, 180000, 'VALID'
            ) RETURNING audio_variant_id
            """,
            {"recording_id": recording_id, "vault_object_id": vault_object_id},
        )
        session.execute(
            text(
                "INSERT INTO vault.audio_fingerprint "
                "(audio_variant_id, algorithm, algorithm_version, duration_ms, fingerprint_hash) "
                "VALUES (:audio_variant_id, 'CHROMAPRINT', '1', 180000, :fingerprint)"
            ),
            {"audio_variant_id": audio_variant_id, "fingerprint": fingerprint},
        )


def _run_import_worker(sessions: sessionmaker[Session]) -> WorkerOutcome:
    worker = JobWorker(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
        worker_id=f"p10-test-{uuid4()}",
        registry=JobHandlerRegistry({IMPORT_JOB_KEY: ImportJobHandler(sessions)}),
        settings=JobWorkerSettings(
            lease_interval=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=1),
            idle_poll_interval=timedelta(milliseconds=10),
            retry_policy=RetryPolicy(max_attempts=3),
        ),
    )
    return worker.run_once().outcome


def _uuid(
    session: Session,
    statement: str,
    values: dict[str, object],
) -> UUID:
    value = session.execute(text(statement), values).scalar_one()
    if not isinstance(value, UUID):
        raise AssertionError("expected UUID from fixture insert")
    return value
