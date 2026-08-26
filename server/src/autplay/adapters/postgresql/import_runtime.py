"""PostgreSQL runtime for resumable P10 imports and immutable review decisions."""

from __future__ import annotations

import hashlib
import hmac
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar, cast
from uuid import UUID

import rfc8785
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autplay.application.identity_evidence import (
    JsonValue,
    candidate_aggregate_sha256,
    canonical_candidate_evidence,
    canonical_query_snapshot,
)
from autplay.domain.auth import Principal
from autplay.domain.import_identity import (
    CANDIDATE_GENERATION_VERSION,
    FEATURE_EXTRACTOR_VERSIONS,
    MATCHER_VERSION,
    MAX_CANDIDATES,
    NORMALIZATION_VERSION,
    CatalogCandidate,
    FingerprintEvidence,
    IdentityTrack,
    ParsedImport,
    ParsedImportRow,
    ScoredCandidate,
    evaluate_identity,
    extract_version_markers,
    identity_track_from_row,
    normalize_text,
)
from autplay.domain.jobs import CancelRequestResult, JobKey
from autplay.ports.jobs import EnqueueJob

from .identity_decisions import (
    IdentityDecisionIdempotencyConflict,
    execute_identity_decision_command,
)
from .jobs_runtime import PostgresJobRepository
from .models import (
    ArtistCreditRow,
    AudioFingerprintRow,
    AudioVariantRow,
    ImportEntryRow,
    ImportJobRow,
    JobRow,
    MatchCandidateEvidenceRow,
    MatchDecisionRow,
    MatcherReleaseRow,
    RecordingIdentifierRow,
    RecordingRow,
    SourceProviderRow,
    UserTrackRefRow,
    WebImportOperationReceiptRow,
)

IMPORT_JOB_KEY = JobKey("library.import", 1)


class ImportRuntimeError(RuntimeError):
    """Base user-safe P10 runtime error."""

    code: ClassVar[str] = "import.runtime_error"

    def __init__(self, code: str | None = None) -> None:
        super().__init__(code or self.code)


class ImportNotFoundError(ImportRuntimeError):
    code = "import.not_found"


class ImportStateConflictError(ImportRuntimeError):
    code = "import.state_conflict"


class ImportReviewConflictError(ImportRuntimeError):
    code = "import.review_conflict"


class ImportOperationConflictError(ImportRuntimeError):
    code = "operation_conflict"


@dataclass(frozen=True, slots=True)
class ImportStartResult:
    """Stable identity returned by an idempotent import creation."""

    import_job_id: UUID
    delivery_job_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class ImportCollectionArtist:
    """One bounded artist name/count projection from an owner TXT import."""

    name: str
    track_count: int


@dataclass(frozen=True, slots=True)
class ImportEntryReport:
    """Redacted per-row report; raw user payload never enters this view."""

    source_row_key: str
    import_entry_id: UUID
    status: str
    resolver_state: str | None
    decision_id: UUID | None
    candidate_count: int
    unknown_field_count: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ImportJobReport:
    """Owner-scoped deterministic import report."""

    import_job_id: UUID
    delivery_job_id: UUID
    state: str
    progress_current: int
    progress_total: int
    adapter_id: str
    adapter_version: str
    input_schema_version: str | None
    counts: dict[str, int]
    entries: tuple[ImportEntryReport, ...]
    next_after: str | None


@dataclass(frozen=True, slots=True)
class ImportProcessProgress:
    """One committed worker step and its safe checkpoint."""

    source_row_key: str | None
    processed_rows: int
    total_rows: int
    finished: bool


@dataclass(frozen=True, slots=True)
class ImportReviewResult:
    """Applied manual decision without an implicit global merge."""

    decision_id: UUID
    import_entry_id: UUID
    status: str
    recording_id: UUID | None
    replayed: bool


class PostgresImportRepository:
    """Perform P10 persistence inside a caller-owned SQLAlchemy transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def start(
        self,
        *,
        owner_user_id: UUID,
        parsed: ParsedImport,
        mode: str,
    ) -> ImportStartResult:
        """Atomically persist every parsed intent and enqueue its durable worker."""

        if mode not in {"LIBRARY_ONLY", "MATERIALIZE"}:
            raise ValueError("import mode is invalid")
        envelope = parsed.envelope
        identity = hashlib.sha256(
            b"\0".join(
                (
                    str(owner_user_id).encode(),
                    envelope.adapter_id.encode(),
                    envelope.adapter_version.encode(),
                    envelope.schema_version.encode(),
                    envelope.format.value.encode(),
                    mode.encode(),
                    envelope.input_sha256,
                )
            )
        ).hexdigest()
        # The existing job repository compares payloads on idempotent replay. Derive
        # this projection identifier from the same canonical identity so both the
        # key and payload remain stable when a client safely retries an upload.
        import_job_id = UUID(bytes=bytes.fromhex(identity)[:16], version=5)
        payload: dict[str, JsonValue] = {
            "import_job_id": str(import_job_id),
            "adapter_id": envelope.adapter_id,
            "adapter_version": envelope.adapter_version,
            "schema_version": envelope.schema_version,
            "format": envelope.format.value,
        }
        enqueued = PostgresJobRepository(self._session).enqueue(
            EnqueueJob(
                key=IMPORT_JOB_KEY,
                user_id=owner_user_id,
                payload=payload,
                priority=3,
                idempotency_scope=f"library-import:{owner_user_id}",
                idempotency_key=identity,
            )
        )
        if enqueued.replayed:
            stored = self._session.get(ImportJobRow, import_job_id)
            if stored is None:
                raise ImportRuntimeError("import.idempotency_projection_missing")
            return ImportStartResult(stored.import_job_id, stored.job_id, replayed=True)

        # These existing P02 mappings intentionally expose UUID foreign keys without
        # ORM relationships, so make the database dependency order explicit while
        # retaining one atomic transaction: durable job -> import job -> entries.
        self._session.flush()
        job = self._session.get(JobRow, enqueued.job_id)
        if job is None:
            raise ImportRuntimeError("import.delivery_job_missing")
        job.progress_current = 0
        job.progress_total = len(parsed.rows)

        import_job = ImportJobRow(
            import_job_id=import_job_id,
            job_id=enqueued.job_id,
            user_id=owner_user_id,
            adapter_id=envelope.adapter_id,
            adapter_version=envelope.adapter_version,
            input_sha256=envelope.input_sha256,
            input_schema_version=envelope.schema_version,
            mode=mode,
            checkpoint={
                "schema_version": 1,
                "last_source_row_key": "",
                "processed_rows": 0,
            },
            summary=_initial_summary(parsed),
        )
        self._session.add(import_job)
        self._session.flush((import_job,))
        self._session.add_all([_entry_row(import_job_id, parsed, row) for row in parsed.rows])
        return ImportStartResult(import_job_id, enqueued.job_id, replayed=False)

    def start_for_web(
        self,
        *,
        owner_user_id: UUID,
        operation_id: UUID,
        parsed: ParsedImport,
    ) -> ImportStartResult:
        """Bind one multipart operation UUID to its canonical request and import."""

        request_sha256 = hashlib.sha256(
            rfc8785.dumps(
                {
                    "action": "web_txt_import",
                    "adapter_id": parsed.envelope.adapter_id,
                    "adapter_version": parsed.envelope.adapter_version,
                    "format": parsed.envelope.format.value,
                    "input_sha256": parsed.envelope.input_sha256.hex(),
                    "mode": "LIBRARY_ONLY",
                    "operation_id": str(operation_id),
                    "schema_version": parsed.envelope.schema_version,
                }
            )
        ).digest()
        lock_key = int.from_bytes(
            hashlib.sha256(owner_user_id.bytes + operation_id.bytes).digest()[:8],
            "big",
            signed=True,
        )
        self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        receipt = self._session.get(
            WebImportOperationReceiptRow,
            {"user_id": owner_user_id, "operation_id": operation_id},
        )
        if receipt is not None:
            if not hmac.compare_digest(receipt.request_sha256, request_sha256):
                raise ImportOperationConflictError
            stored = self._session.get(ImportJobRow, receipt.import_job_id)
            if stored is None:
                raise ImportRuntimeError("import.idempotency_projection_missing")
            return ImportStartResult(stored.import_job_id, stored.job_id, replayed=True)
        result = self.start(owner_user_id=owner_user_id, parsed=parsed, mode="LIBRARY_ONLY")
        self._session.add(
            WebImportOperationReceiptRow(
                user_id=owner_user_id,
                operation_id=operation_id,
                request_sha256=request_sha256,
                import_job_id=result.import_job_id,
            )
        )
        self._session.flush()
        return result

    def collection_artists(
        self,
        *,
        owner_user_id: UUID,
        import_job_id: UUID,
        limit: int = 100,
    ) -> tuple[ImportCollectionArtist, ...]:
        """Aggregate artist display names from one owner-scoped TXT import."""

        if not 1 <= limit <= 100:
            raise ValueError("collection artist limit is invalid")
        job = self._session.scalar(
            select(ImportJobRow).where(
                ImportJobRow.import_job_id == import_job_id,
                ImportJobRow.user_id == owner_user_id,
            )
        )
        if job is None or _summary(job.summary).get("format") != "TXT":
            raise ImportNotFoundError
        count = func.count(ImportEntryRow.import_entry_id)
        rows = self._session.execute(
            select(ImportEntryRow.raw_artist, count.label("track_count"))
            .where(
                ImportEntryRow.import_job_id == import_job_id,
                ImportEntryRow.raw_artist != "",
            )
            .group_by(ImportEntryRow.raw_artist)
        ).all()
        aggregated: dict[str, tuple[str, int]] = {}
        for raw_name, raw_count in rows:
            name = str(raw_name)
            normalized = normalize_text(name)
            if not normalized:
                continue
            display, current_count = aggregated.get(normalized, (name, 0))
            if (name.casefold(), name) < (display.casefold(), display):
                display = name
            aggregated[normalized] = (display, current_count + int(raw_count))
        ordered = sorted(
            aggregated.items(),
            key=lambda item: (-item[1][1], item[0], item[1][0]),
        )
        return tuple(
            ImportCollectionArtist(display, track_count)
            for _, (display, track_count) in ordered[:limit]
        )

    def report(
        self,
        *,
        owner_user_id: UUID,
        import_job_id: UUID,
        limit: int = 200,
        after: str | None = None,
    ) -> ImportJobReport:
        """Return a bounded report that contains no raw fields or private locators."""

        if not 1 <= limit <= 1_000:
            raise ValueError("report limit must be between one and one thousand")
        pair = self._session.execute(
            select(ImportJobRow, JobRow)
            .join(JobRow, JobRow.job_id == ImportJobRow.job_id)
            .where(
                ImportJobRow.import_job_id == import_job_id,
                ImportJobRow.user_id == owner_user_id,
            )
        ).one_or_none()
        if pair is None:
            raise ImportNotFoundError
        import_job, job = pair
        statement = select(ImportEntryRow).where(ImportEntryRow.import_job_id == import_job_id)
        if after is not None:
            statement = statement.where(ImportEntryRow.source_row_key > after)
        entry_rows = tuple(
            self._session.scalars(
                statement.order_by(ImportEntryRow.source_row_key).limit(limit + 1)
            ).all()
        )
        page = entry_rows[:limit]
        reports = tuple(self._entry_report(row) for row in page)
        counts = Counter(
            self._session.scalars(
                select(ImportEntryRow.match_status).where(
                    ImportEntryRow.import_job_id == import_job_id
                )
            ).all()
        )
        return ImportJobReport(
            import_job_id=import_job.import_job_id,
            delivery_job_id=job.job_id,
            state=job.state,
            progress_current=job.progress_current or 0,
            progress_total=job.progress_total or len(reports),
            adapter_id=import_job.adapter_id,
            adapter_version=import_job.adapter_version,
            input_schema_version=import_job.input_schema_version,
            counts=dict(sorted(counts.items())),
            entries=reports,
            next_after=(page[-1].source_row_key if len(entry_rows) > limit and page else None),
        )

    def cancel(self, *, owner_user_id: UUID, import_job_id: UUID) -> CancelRequestResult:
        """Request owner-scoped cancellation through the existing job seam."""

        job_id = self._session.scalar(
            select(ImportJobRow.job_id).where(
                ImportJobRow.import_job_id == import_job_id,
                ImportJobRow.user_id == owner_user_id,
            )
        )
        if job_id is None:
            return CancelRequestResult.NOT_FOUND
        return PostgresJobRepository(self._session).request_cancel_for_owner(
            job_id=job_id, owner_user_id=owner_user_id
        )

    def resume(self, *, owner_user_id: UUID, import_job_id: UUID) -> ImportStartResult:
        """Attach a new durable delivery to a cancelled/failed import checkpoint."""

        pair = self._session.execute(
            select(ImportJobRow, JobRow)
            .join(JobRow, JobRow.job_id == ImportJobRow.job_id)
            .where(
                ImportJobRow.import_job_id == import_job_id,
                ImportJobRow.user_id == owner_user_id,
            )
            .with_for_update()
        ).one_or_none()
        if pair is None:
            raise ImportNotFoundError
        import_job, current_job = pair
        if current_job.state in {"QUEUED", "RUNNING", "RETRY_WAIT"}:
            return ImportStartResult(import_job_id, current_job.job_id, replayed=True)
        if current_job.state not in {"CANCELLED", "FAILED", "PAUSED"}:
            raise ImportStateConflictError
        checkpoint = _checkpoint(import_job.checkpoint)
        generation = _coerce_int(_summary(import_job.summary).get("resume_count")) + 1
        result = PostgresJobRepository(self._session).enqueue(
            EnqueueJob(
                key=IMPORT_JOB_KEY,
                user_id=owner_user_id,
                payload={
                    "import_job_id": str(import_job_id),
                    "adapter_id": import_job.adapter_id,
                    "adapter_version": import_job.adapter_version,
                    "schema_version": import_job.input_schema_version or "1",
                    "resume_generation": generation,
                },
                priority=3,
                idempotency_scope=f"library-import-resume:{owner_user_id}",
                idempotency_key=f"{import_job_id}:{generation}",
            )
        )
        import_job.job_id = result.job_id
        summary = _summary(import_job.summary)
        summary["resume_count"] = generation
        import_job.summary = summary
        resumed = self._session.get(JobRow, result.job_id)
        if resumed is None:
            raise ImportRuntimeError("import.delivery_job_missing")
        resumed.progress_current = _coerce_int(checkpoint.get("processed_rows"))
        resumed.progress_total = self._entry_count(import_job_id)
        return ImportStartResult(import_job_id, result.job_id, replayed=result.replayed)

    def ensure_matcher_release(self) -> None:
        """Install the immutable shadow release without activating any policy."""

        manifest = rfc8785.dumps(
            {
                "matcher_version": MATCHER_VERSION,
                "candidate_generation_version": CANDIDATE_GENERATION_VERSION,
                "normalization_version": NORMALIZATION_VERSION,
                "feature_extractor_versions": FEATURE_EXTRACTOR_VERSIONS,
                "auto_match_enabled": False,
            }
        )
        manifest_sha256 = hashlib.sha256(manifest).digest()
        self._session.execute(
            insert(MatcherReleaseRow)
            .values(
                matcher_version=MATCHER_VERSION,
                candidate_generation_version=CANDIDATE_GENERATION_VERSION,
                normalization_version=NORMALIZATION_VERSION,
                feature_extractor_versions=dict(FEATURE_EXTRACTOR_VERSIONS),
                feature_schema_version="1",
                manifest_sha256=manifest_sha256,
            )
            .on_conflict_do_nothing()
        )
        stored = self._session.get(MatcherReleaseRow, MATCHER_VERSION)
        if (
            stored is None
            or stored.candidate_generation_version != CANDIDATE_GENERATION_VERSION
            or stored.normalization_version != NORMALIZATION_VERSION
            or stored.feature_extractor_versions != FEATURE_EXTRACTOR_VERSIONS
            or stored.feature_schema_version != "1"
            or not hmac.compare_digest(stored.manifest_sha256, manifest_sha256)
        ):
            raise ImportRuntimeError("import.matcher_release_conflict")

    def process_next(self, *, import_job_id: UUID) -> ImportProcessProgress:
        """Commit one row outcome and the import checkpoint in the same transaction."""

        import_job = self._session.scalar(
            select(ImportJobRow)
            .where(ImportJobRow.import_job_id == import_job_id)
            .with_for_update()
        )
        if import_job is None:
            raise ImportNotFoundError
        checkpoint = _checkpoint(import_job.checkpoint)
        last_key = str(checkpoint.get("last_source_row_key", ""))
        entry = self._session.scalar(
            select(ImportEntryRow)
            .where(
                ImportEntryRow.import_job_id == import_job_id,
                ImportEntryRow.source_row_key > last_key,
            )
            .order_by(ImportEntryRow.source_row_key)
            .limit(1)
        )
        total = self._entry_count(import_job_id)
        if entry is None:
            self._finalize_summary(import_job)
            return ImportProcessProgress(last_key or None, total, total, finished=True)
        if entry.match_status != "REJECTED":
            self._evaluate_entry(import_job, entry)
        processed = _coerce_int(checkpoint.get("processed_rows")) + 1
        import_job.checkpoint = {
            "schema_version": 1,
            "last_source_row_key": entry.source_row_key,
            "processed_rows": processed,
        }
        if processed == total:
            self._finalize_summary(import_job)
        return ImportProcessProgress(entry.source_row_key, processed, total, processed == total)

    def review(
        self,
        *,
        principal: Principal,
        import_job_id: UUID,
        import_entry_id: UUID,
        predecessor_decision_id: UUID,
        action: str,
        selected_rank: int | None,
        idempotency_key: str,
    ) -> ImportReviewResult:
        """Append an applied manual review and project only the ImportEntry lineage."""

        if action not in {"ACCEPT", "REJECT", "KEEP_UNRESOLVED", "CREATE_RECORDING"}:
            raise ValueError("review action is invalid")
        if not 1 <= len(idempotency_key) <= 120:
            raise ValueError("review idempotency key is invalid")
        pair = self._session.execute(
            select(ImportEntryRow, ImportJobRow)
            .join(ImportJobRow, ImportJobRow.import_job_id == ImportEntryRow.import_job_id)
            .where(
                ImportEntryRow.import_job_id == import_job_id,
                ImportEntryRow.import_entry_id == import_entry_id,
                ImportJobRow.user_id == principal.user_id,
            )
            .with_for_update(of=ImportEntryRow)
        ).one_or_none()
        predecessor = self._session.get(MatchDecisionRow, predecessor_decision_id)
        if pair is None or predecessor is None:
            raise ImportNotFoundError
        entry, _ = pair
        if (
            predecessor.query_type != "IMPORT_ENTRY"
            or predecessor.import_entry_id != import_entry_id
            or predecessor.owner_user_id != principal.user_id
        ):
            raise ImportReviewConflictError
        if predecessor.decision_state == "INTEGRITY_CONFLICT" and action != "KEEP_UNRESOLVED":
            raise ImportReviewConflictError
        successor = self._session.scalar(
            select(MatchDecisionRow.decision_id).where(
                MatchDecisionRow.supersedes_decision_id == predecessor_decision_id
            )
        )
        if successor is not None:
            existing = self._session.get(MatchDecisionRow, successor)
            if existing is None:
                raise ImportReviewConflictError
            expected_hash = _review_request_hash(
                import_entry_id, predecessor_decision_id, action, selected_rank
            )
            if not hmac.compare_digest(existing.request_sha256, expected_hash):
                raise ImportReviewConflictError
            return ImportReviewResult(
                existing.decision_id,
                import_entry_id,
                entry.match_status,
                entry.selected_recording_id,
                replayed=True,
            )
        predecessor_evidence = tuple(
            self._session.scalars(
                select(MatchCandidateEvidenceRow)
                .where(MatchCandidateEvidenceRow.decision_id == predecessor_decision_id)
                .order_by(MatchCandidateEvidenceRow.rank)
            ).all()
        )
        selected = next((row for row in predecessor_evidence if row.rank == selected_rank), None)
        if action in {"ACCEPT", "REJECT"} and selected is None:
            raise ImportReviewConflictError
        if action in {"KEEP_UNRESOLVED", "CREATE_RECORDING"} and selected_rank is not None:
            raise ImportReviewConflictError
        request_hash = _review_request_hash(
            import_entry_id, predecessor_decision_id, action, selected_rank
        )
        new_decision = _review_decision(
            predecessor,
            action=action,
            selected=selected,
            actor_user_id=principal.user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        evidence_copies = tuple(_copy_evidence(row) for row in predecessor_evidence)
        new_credit: ArtistCreditRow | None = None
        new_recording: RecordingRow | None = None
        if action == "CREATE_RECORDING":
            display_artist = entry.raw_artist.strip() or "Unknown artist"
            display_title = entry.raw_title.strip() or "Unknown title"
            new_credit = ArtistCreditRow(
                display_name=display_artist,
                normalized_name=normalize_text(display_artist),
            )
            new_recording = RecordingRow(
                artist_credit_id=UUID(int=0),
                title=display_title,
                normalized_title=normalize_text(display_title),
                duration_ms=entry.raw_duration_ms,
                recording_kind="UNKNOWN",
                identity_status="PROVISIONAL",
                metadata_confidence=Decimal("0"),
            )

        def write(active: Session) -> MatchDecisionRow:
            if new_credit is not None and new_recording is not None:
                active.add(new_credit)
                active.flush([new_credit])
                new_recording.artist_credit_id = new_credit.artist_credit_id
                active.add(new_recording)
                active.flush([new_recording])
                new_decision.candidate_recording_id = new_recording.recording_id
            if action in {"ACCEPT", "CREATE_RECORDING"}:
                target_recording_id = new_decision.candidate_recording_id
                if target_recording_id is None:
                    raise ImportReviewConflictError
                user_ref = _append_resolved_user_track_lineage(
                    active,
                    entry=entry,
                    predecessor=predecessor,
                    predecessor_evidence=predecessor_evidence,
                    action=action,
                    selected_rank=selected_rank,
                    actor_user_id=principal.user_id,
                    target_recording_id=target_recording_id,
                    idempotency_key=idempotency_key,
                )
                entry.user_track_ref_id = user_ref.user_track_ref_id
            active.add(new_decision)
            active.flush([new_decision])
            for evidence in evidence_copies:
                evidence.decision_id = new_decision.decision_id
            active.add_all(evidence_copies)
            entry.current_match_decision_id = new_decision.decision_id
            if action in {"ACCEPT", "CREATE_RECORDING"}:
                entry.match_status = "MANUAL_MATCH"
                entry.selected_recording_id = new_decision.candidate_recording_id
            elif action == "KEEP_UNRESOLVED":
                entry.match_status = "MANUAL_UNRESOLVED"
                entry.selected_recording_id = None
            else:
                entry.match_status = "REVIEW_REQUIRED"
                entry.selected_recording_id = None
            return new_decision

        try:
            result = execute_identity_decision_command(
                self._session,
                idempotency_scope=f"p10-import-review:{principal.user_id}",
                idempotency_key=idempotency_key,
                request_sha256=request_hash,
                write=write,
            )
        except IdentityDecisionIdempotencyConflict as error:
            raise ImportReviewConflictError from error
        return ImportReviewResult(
            result.decision.decision_id,
            import_entry_id,
            entry.match_status,
            entry.selected_recording_id,
            replayed=result.replayed,
        )

    def _evaluate_entry(self, import_job: ImportJobRow, entry: ImportEntryRow) -> None:
        existing = self._session.scalar(
            select(MatchDecisionRow).where(
                MatchDecisionRow.import_entry_id == entry.import_entry_id,
                MatchDecisionRow.matcher_version == MATCHER_VERSION,
            )
        )
        if existing is not None:
            return
        parsed_row = _parsed_row(entry)
        query = identity_track_from_row(parsed_row)
        evaluation = evaluate_identity(query, self._catalog_candidates(query))
        query_snapshot_values: dict[str, JsonValue] = {
            "normalized_title": normalize_text(query.title),
            "normalized_artists": [normalize_text(value) for value in query.artists],
            "duration_ms": query.duration_ms,
            "version_markers": list(query.version_markers),
            "market_scope": "GLOBAL",
            "evidence_ids": [f"import-entry:{entry.import_entry_id}"],
        }
        if query.album:
            query_snapshot_values["normalized_release"] = normalize_text(query.album)
        snapshot = canonical_query_snapshot(query_snapshot_values)
        evidence_rows = _evidence_rows(evaluation.candidates)
        aggregate_hash, _ = candidate_aggregate_sha256(
            [(item.rank, item.evidence_sha256) for item in evidence_rows]
        )
        evidence_size = sum(item.evidence_document_size_bytes for item in evidence_rows)
        top = evaluation.candidates[0] if evaluation.candidates else None
        top2 = evaluation.candidates[1] if len(evaluation.candidates) > 1 else None
        execution_mode = "SHADOW"
        decision_state = "DEFERRED_EVIDENCE" if evaluation.state == "NO_MATCH" else evaluation.state
        selected = (
            top.recording_id if top is not None and decision_state == "REVIEW_REQUIRED" else None
        )
        request_hash = hashlib.sha256(
            snapshot.canonical_bytes + b"\0" + aggregate_hash + b"\0" + execution_mode.encode()
        ).digest()
        decision = MatchDecisionRow(
            query_type="IMPORT_ENTRY",
            owner_user_id=import_job.user_id,
            device_id=None,
            import_entry_id=entry.import_entry_id,
            user_track_ref_id=None,
            local_audio_id=None,
            external_reference_id=None,
            vault_object_id=None,
            audio_variant_id=None,
            query_snapshot=snapshot.value,
            query_snapshot_schema_version="1",
            snapshot_canonicalization_version="RFC8785",
            query_snapshot_sha256=snapshot.sha256,
            decision_kind="EVALUATION",
            execution_mode=execution_mode,
            review_action=None,
            reviewed_candidate_evidence_id=None,
            candidate_recording_id=selected,
            decision_state=decision_state,
            candidate_count=len(evidence_rows),
            candidate_evidence_sha256=aggregate_hash,
            candidate_evidence_size_bytes=evidence_size,
            evidence_mode=evaluation.evidence_mode,
            candidate_generation_version=CANDIDATE_GENERATION_VERSION,
            normalization_version=NORMALIZATION_VERSION,
            feature_extractor_versions=dict(FEATURE_EXTRACTOR_VERSIONS),
            matcher_version=MATCHER_VERSION,
            calibrator_version=None,
            threshold_set_version=None,
            raw_score=_decimal(top.raw_score if top else None),
            confidence=_decimal(top.confidence if top else None),
            top2_confidence=_decimal(top2.confidence if top2 else None),
            margin=_decimal(evaluation.margin),
            evidence_tier=top.evidence_tier if top else None,
            feature_scores=list(top.feature_scores) if top else [],
            hard_conflicts=list(top.hard_conflicts) if top else [],
            candidate_origins=list(top.candidate_origins) if top else [],
            explanation_schema_version="1",
            actor_type="SYSTEM",
            actor_user_id=None,
            idempotency_scope="p10-import-evaluation",
            idempotency_key=f"{entry.import_entry_id}:{MATCHER_VERSION}",
            request_sha256=request_hash,
            supersedes_decision_id=None,
            supersession_reason=None,
        )

        def write(active: Session) -> MatchDecisionRow:
            active.add(decision)
            active.flush([decision])
            for evidence in evidence_rows:
                evidence.decision_id = decision.decision_id
            active.add_all(evidence_rows)
            return decision

        execute_identity_decision_command(
            self._session,
            idempotency_scope="p10-import-evaluation",
            idempotency_key=f"{entry.import_entry_id}:{MATCHER_VERSION}",
            request_sha256=request_hash,
            write=write,
        )

    def _catalog_candidates(self, query: IdentityTrack) -> tuple[CatalogCandidate, ...]:
        title = normalize_text(query.title)
        artist = normalize_text(query.artists[0])
        origins_by_recording: dict[UUID, set[str]] = {}

        def add_candidates(recording_ids: Sequence[UUID], origin: str) -> None:
            for recording_id in recording_ids:
                if len(origins_by_recording) >= MAX_CANDIDATES:
                    break
                origins_by_recording.setdefault(recording_id, set()).add(origin)

        for identifier_key, identifier_value in query.identifiers.items():
            identifier_statement = (
                select(RecordingIdentifierRow.recording_id)
                .join(
                    RecordingRow,
                    RecordingRow.recording_id == RecordingIdentifierRow.recording_id,
                )
                .where(
                    RecordingIdentifierRow.value == identifier_value,
                    RecordingRow.deleted_at.is_(None),
                    RecordingRow.identity_status.in_(("ACTIVE", "PROVISIONAL")),
                )
            )
            if identifier_key in {"isrc", "mbid"}:
                identifier_statement = identifier_statement.where(
                    RecordingIdentifierRow.scheme == identifier_key.upper()
                )
            elif identifier_key.startswith("provider:"):
                identifier_statement = identifier_statement.join(
                    SourceProviderRow,
                    SourceProviderRow.provider_id == RecordingIdentifierRow.provider_id,
                ).where(
                    RecordingIdentifierRow.scheme == "OTHER",
                    SourceProviderRow.provider_key == identifier_key.removeprefix("provider:"),
                    SourceProviderRow.deleted_at.is_(None),
                )
            else:
                continue
            add_candidates(
                tuple(
                    self._session.scalars(
                        identifier_statement.order_by(RecordingIdentifierRow.recording_id).limit(
                            MAX_CANDIDATES
                        )
                    ).all()
                ),
                "IDENTIFIER",
            )

        query_fingerprint = query.fingerprint
        fingerprint_bytes = (
            _fingerprint_bytes(query_fingerprint.value_hash)
            if query_fingerprint is not None
            else None
        )
        if query_fingerprint is not None and fingerprint_bytes is not None:
            add_candidates(
                tuple(
                    self._session.scalars(
                        select(AudioVariantRow.recording_id)
                        .join(
                            AudioFingerprintRow,
                            AudioFingerprintRow.audio_variant_id
                            == AudioVariantRow.audio_variant_id,
                        )
                        .join(
                            RecordingRow,
                            RecordingRow.recording_id == AudioVariantRow.recording_id,
                        )
                        .where(
                            AudioVariantRow.validation_status == "VALID",
                            AudioVariantRow.deleted_at.is_(None),
                            RecordingRow.deleted_at.is_(None),
                            RecordingRow.identity_status.in_(("ACTIVE", "PROVISIONAL")),
                            AudioFingerprintRow.algorithm == query_fingerprint.algorithm,
                            AudioFingerprintRow.algorithm_version
                            == query_fingerprint.algorithm_version,
                            or_(
                                AudioFingerprintRow.fingerprint_hash == fingerprint_bytes,
                                AudioFingerprintRow.fingerprint_payload == fingerprint_bytes,
                            ),
                        )
                        .order_by(AudioVariantRow.recording_id)
                        .limit(MAX_CANDIDATES)
                    ).all()
                ),
                "FINGERPRINT",
            )

        metadata_ids = tuple(
            self._session.scalars(
                select(RecordingRow.recording_id)
                .join(
                    ArtistCreditRow,
                    ArtistCreditRow.artist_credit_id == RecordingRow.artist_credit_id,
                )
                .where(
                    RecordingRow.deleted_at.is_(None),
                    RecordingRow.identity_status.in_(("ACTIVE", "PROVISIONAL")),
                    func.similarity(RecordingRow.normalized_title, title) >= 0.25,
                    func.similarity(ArtistCreditRow.normalized_name, artist) >= 0.25,
                )
                .order_by(
                    (
                        func.similarity(RecordingRow.normalized_title, title)
                        + func.similarity(ArtistCreditRow.normalized_name, artist)
                    ).desc(),
                    RecordingRow.recording_id,
                )
                .limit(MAX_CANDIDATES)
            ).all()
        )
        add_candidates(metadata_ids, "NORMALIZED_METADATA")
        recording_ids = tuple(origins_by_recording)
        if not recording_ids:
            return ()
        pairs = self._session.execute(
            select(RecordingRow, ArtistCreditRow)
            .join(
                ArtistCreditRow,
                ArtistCreditRow.artist_credit_id == RecordingRow.artist_credit_id,
            )
            .where(RecordingRow.recording_id.in_(recording_ids))
            .order_by(RecordingRow.recording_id)
        ).all()
        identifiers: dict[UUID, dict[str, str]] = {item: {} for item in recording_ids}
        for row, provider_key in self._session.execute(
            select(RecordingIdentifierRow, SourceProviderRow.provider_key)
            .outerjoin(
                SourceProviderRow,
                SourceProviderRow.provider_id == RecordingIdentifierRow.provider_id,
            )
            .where(RecordingIdentifierRow.recording_id.in_(recording_ids))
            .order_by(RecordingIdentifierRow.recording_identifier_id)
        ):
            key = (
                f"provider:{normalize_text(provider_key)}"
                if row.scheme == "OTHER" and provider_key is not None
                else row.scheme.casefold()
            )
            if query.identifiers.get(key) == row.value or key not in identifiers[row.recording_id]:
                identifiers[row.recording_id][key] = row.value
        fingerprint_by_recording: dict[UUID, FingerprintEvidence] = {}
        if query_fingerprint is not None:
            fingerprint_rows = self._session.execute(
                select(AudioVariantRow.recording_id, AudioFingerprintRow)
                .join(
                    AudioFingerprintRow,
                    AudioFingerprintRow.audio_variant_id == AudioVariantRow.audio_variant_id,
                )
                .where(
                    AudioVariantRow.recording_id.in_(recording_ids),
                    AudioVariantRow.validation_status == "VALID",
                    AudioVariantRow.deleted_at.is_(None),
                    AudioFingerprintRow.algorithm == query_fingerprint.algorithm,
                    AudioFingerprintRow.algorithm_version == query_fingerprint.algorithm_version,
                )
            ).all()
            for recording_id, fingerprint in fingerprint_rows:
                payload = fingerprint.fingerprint_hash or fingerprint.fingerprint_payload
                if payload is None:
                    continue
                evidence = FingerprintEvidence(
                    fingerprint.algorithm,
                    fingerprint.algorithm_version,
                    payload.hex(),
                    1.0,
                )
                if (
                    evidence.value_hash == query_fingerprint.value_hash
                    or recording_id not in fingerprint_by_recording
                ):
                    fingerprint_by_recording[recording_id] = evidence
        candidates: list[CatalogCandidate] = []
        for recording, credit in pairs:
            origins = tuple(sorted(origins_by_recording[recording.recording_id]))
            candidates.append(
                CatalogCandidate(
                    recording_id=recording.recording_id,
                    title=recording.title,
                    artists=(credit.display_name,),
                    duration_ms=recording.duration_ms,
                    version_markers=extract_version_markers(
                        " ".join(
                            filter(
                                None,
                                (recording.title, recording.version_text, recording.recording_kind),
                            )
                        )
                    ),
                    identifiers=identifiers[recording.recording_id],
                    fingerprint=fingerprint_by_recording.get(recording.recording_id),
                    origins=origins,
                )
            )
        return tuple(candidates)

    def _entry_report(self, entry: ImportEntryRow) -> ImportEntryReport:
        decision = self._session.scalar(
            select(MatchDecisionRow)
            .where(MatchDecisionRow.import_entry_id == entry.import_entry_id)
            .order_by(MatchDecisionRow.decided_at.desc(), MatchDecisionRow.decision_id.desc())
            .limit(1)
        )
        raw_payload = _object(entry.raw_payload)
        return ImportEntryReport(
            source_row_key=entry.source_row_key,
            import_entry_id=entry.import_entry_id,
            status=entry.match_status,
            resolver_state=decision.decision_state if decision is not None else None,
            decision_id=decision.decision_id if decision is not None else None,
            candidate_count=decision.candidate_count if decision is not None else 0,
            unknown_field_count=_safe_int(raw_payload.get("unknown_field_count")),
            error_code=_safe_text(raw_payload.get("parse_error")),
        )

    def _entry_count(self, import_job_id: UUID) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(ImportEntryRow)
                .where(ImportEntryRow.import_job_id == import_job_id)
            )
            or 0
        )

    def _finalize_summary(self, import_job: ImportJobRow) -> None:
        statuses = Counter(
            self._session.scalars(
                select(ImportEntryRow.match_status).where(
                    ImportEntryRow.import_job_id == import_job.import_job_id
                )
            ).all()
        )
        summary = _summary(import_job.summary)
        summary["status_counts"] = dict(sorted(statuses.items()))
        summary["complete"] = True
        import_job.summary = summary


def _initial_summary(parsed: ParsedImport) -> dict[str, JsonValue]:
    issues = Counter(row.error_code for row in parsed.rows if row.error_code is not None)
    return {
        "schema_version": 1,
        "format": parsed.envelope.format.value,
        "total_rows": len(parsed.rows),
        "valid_rows": parsed.valid_count,
        "malformed_rows": parsed.malformed_count,
        "unavailable_rows": sum(row.availability == "UNAVAILABLE" for row in parsed.rows),
        "issue_counts": {str(key): value for key, value in sorted(issues.items())},
        "resume_count": 0,
        "complete": False,
    }


def _entry_row(import_job_id: UUID, parsed: ParsedImport, row: ParsedImportRow) -> ImportEntryRow:
    payload: dict[str, JsonValue] = {
        "provenance": {
            "schema_version": 1,
            "adapter_id": parsed.envelope.adapter_id,
            "adapter_version": parsed.envelope.adapter_version,
            "input_schema_version": parsed.envelope.schema_version,
            "format": parsed.envelope.format.value,
            "encoding": parsed.encoding,
            "row_number": row.row_number,
        },
        "fields": row.raw_fields,
        "availability": row.availability,
        "unknown_field_count": row.unknown_field_count,
        "parse_error": row.error_code,
    }
    return ImportEntryRow(
        import_job_id=import_job_id,
        source_row_key=row.source_row_key,
        raw_title=row.title,
        raw_artist=row.artist,
        raw_album=row.album,
        raw_duration_ms=row.duration_ms,
        raw_external_id=row.external_id,
        raw_payload=payload,
        match_status="PENDING" if row.valid else "REJECTED",
        current_match_decision_id=None,
        selected_recording_id=None,
        user_track_ref_id=None,
    )


def _parsed_row(entry: ImportEntryRow) -> ParsedImportRow:
    payload = _object(entry.raw_payload)
    fields = _object(payload.get("fields"))
    provenance = _object(payload.get("provenance"))
    return ParsedImportRow(
        source_row_key=entry.source_row_key,
        row_number=_safe_int(provenance.get("row_number")),
        raw_fields=fields,
        title=entry.raw_title,
        artist=entry.raw_artist,
        album=entry.raw_album,
        duration_ms=entry.raw_duration_ms,
        external_id=entry.raw_external_id,
        availability=_safe_text(payload.get("availability")) or "UNKNOWN",
        unknown_field_count=_safe_int(payload.get("unknown_field_count")),
    )


def _evidence_rows(
    candidates: Sequence[ScoredCandidate],
) -> tuple[MatchCandidateEvidenceRow, ...]:
    rows: list[MatchCandidateEvidenceRow] = []
    for rank, candidate in enumerate(candidates, start=1):
        feature_scores = cast(list[JsonValue], list(candidate.feature_scores))
        hard_conflicts = cast(list[JsonValue], list(candidate.hard_conflicts))
        origins = cast(list[JsonValue], list(candidate.candidate_origins))
        document = canonical_candidate_evidence(
            {
                "recording_id": str(candidate.recording_id),
                "raw_score": candidate.raw_score,
                "confidence": candidate.confidence,
                "evidence_tier": candidate.evidence_tier,
                "feature_scores": feature_scores,
                "hard_conflicts": hard_conflicts,
                "candidate_origins": origins,
                "extractor_versions": FEATURE_EXTRACTOR_VERSIONS,
            }
        )
        rows.append(
            MatchCandidateEvidenceRow(
                decision_id=UUID(int=0),
                recording_id=candidate.recording_id,
                rank=rank,
                raw_score=_decimal(candidate.raw_score),
                confidence=_decimal(candidate.confidence),
                evidence_tier=candidate.evidence_tier,
                feature_scores=feature_scores,
                hard_conflicts=hard_conflicts,
                candidate_origins=origins,
                extractor_versions=dict(FEATURE_EXTRACTOR_VERSIONS),
                evidence_schema_version="1",
                evidence_sha256=document.sha256,
                evidence_document_size_bytes=document.byte_size,
            )
        )
    return tuple(rows)


def _append_resolved_user_track_lineage(
    session: Session,
    *,
    entry: ImportEntryRow,
    predecessor: MatchDecisionRow,
    predecessor_evidence: Sequence[MatchCandidateEvidenceRow],
    action: str,
    selected_rank: int | None,
    actor_user_id: UUID,
    target_recording_id: UUID,
    idempotency_key: str,
) -> UserTrackRefRow:
    target_exists = session.scalar(
        select(RecordingRow.recording_id)
        .where(RecordingRow.recording_id == target_recording_id)
        .with_for_update()
    )
    if target_exists is None:
        raise ImportReviewConflictError
    user_ref = session.scalar(
        select(UserTrackRefRow)
        .where(
            UserTrackRefRow.user_id == actor_user_id,
            UserTrackRefRow.recording_id == target_recording_id,
            UserTrackRefRow.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if user_ref is None:
        user_ref = UserTrackRefRow(
            user_id=actor_user_id,
            recording_id=None,
            resolution_status="UNRESOLVED",
            raw_title=entry.raw_title,
            raw_artist=entry.raw_artist,
            raw_album=entry.raw_album,
            raw_duration_ms=entry.raw_duration_ms,
            current_match_decision_id=None,
            resolved_at=None,
            resolution_confidence=None,
        )
        session.add(user_ref)
        session.flush([user_ref])

    evaluation = _user_track_evaluation(
        predecessor,
        user_track_ref_id=user_ref.user_track_ref_id,
    )
    session.add(evaluation)
    session.flush([evaluation])
    evaluation_evidence = tuple(_copy_evidence(row) for row in predecessor_evidence)
    for evidence in evaluation_evidence:
        evidence.decision_id = evaluation.decision_id
    session.add_all(evaluation_evidence)
    if evaluation_evidence:
        session.flush(evaluation_evidence)
    selected = next(
        (row for row in evaluation_evidence if row.rank == selected_rank),
        None,
    )
    if action == "ACCEPT" and selected is None:
        raise ImportReviewConflictError
    request_hash = hashlib.sha256(
        rfc8785.dumps(
            {
                "schema_version": 1,
                "user_track_ref_id": str(user_ref.user_track_ref_id),
                "predecessor_decision_id": str(evaluation.decision_id),
                "action": action,
                "selected_rank": selected_rank,
                "target_recording_id": str(target_recording_id),
            }
        )
    ).digest()
    review = _review_decision(
        evaluation,
        action=action,
        selected=selected,
        actor_user_id=actor_user_id,
        idempotency_key=f"{idempotency_key}:user-track",
        request_hash=request_hash,
    )
    review.idempotency_scope = f"p10-user-track-review:{actor_user_id}"
    if action == "CREATE_RECORDING":
        review.candidate_recording_id = target_recording_id
    session.add(review)
    session.flush([review])
    review_evidence = tuple(_copy_evidence(row) for row in evaluation_evidence)
    for evidence in review_evidence:
        evidence.decision_id = review.decision_id
    session.add_all(review_evidence)

    user_ref.recording_id = target_recording_id
    user_ref.resolution_status = "RESOLVED"
    user_ref.current_match_decision_id = review.decision_id
    user_ref.resolved_at = datetime.now(UTC)
    user_ref.resolution_confidence = review.confidence
    return user_ref


def _user_track_evaluation(
    predecessor: MatchDecisionRow,
    *,
    user_track_ref_id: UUID,
) -> MatchDecisionRow:
    request_hash = hashlib.sha256(
        predecessor.request_sha256
        + predecessor.decision_id.bytes
        + user_track_ref_id.bytes
        + b"USER_TRACK_REF:EVALUATION"
    ).digest()
    return MatchDecisionRow(
        query_type="USER_TRACK_REF",
        owner_user_id=predecessor.owner_user_id,
        device_id=None,
        import_entry_id=None,
        user_track_ref_id=user_track_ref_id,
        local_audio_id=None,
        external_reference_id=None,
        vault_object_id=predecessor.vault_object_id,
        audio_variant_id=predecessor.audio_variant_id,
        query_snapshot=predecessor.query_snapshot,
        query_snapshot_schema_version=predecessor.query_snapshot_schema_version,
        snapshot_canonicalization_version=predecessor.snapshot_canonicalization_version,
        query_snapshot_sha256=predecessor.query_snapshot_sha256,
        decision_kind="EVALUATION",
        execution_mode="SHADOW",
        review_action=None,
        reviewed_candidate_evidence_id=None,
        candidate_recording_id=predecessor.candidate_recording_id,
        decision_state=predecessor.decision_state,
        candidate_count=predecessor.candidate_count,
        candidate_evidence_sha256=predecessor.candidate_evidence_sha256,
        candidate_evidence_size_bytes=predecessor.candidate_evidence_size_bytes,
        evidence_mode=predecessor.evidence_mode,
        candidate_generation_version=predecessor.candidate_generation_version,
        normalization_version=predecessor.normalization_version,
        feature_extractor_versions=predecessor.feature_extractor_versions,
        matcher_version=predecessor.matcher_version,
        calibrator_version=predecessor.calibrator_version,
        threshold_set_version=predecessor.threshold_set_version,
        raw_score=predecessor.raw_score,
        confidence=predecessor.confidence,
        top2_confidence=predecessor.top2_confidence,
        margin=predecessor.margin,
        evidence_tier=predecessor.evidence_tier,
        feature_scores=predecessor.feature_scores,
        hard_conflicts=predecessor.hard_conflicts,
        candidate_origins=predecessor.candidate_origins,
        explanation_schema_version=predecessor.explanation_schema_version,
        actor_type="SYSTEM",
        actor_user_id=None,
        idempotency_scope="p10-user-track-evaluation",
        idempotency_key=(
            f"{user_track_ref_id}:{predecessor.decision_id}:{predecessor.matcher_version}"
        ),
        request_sha256=request_hash,
        supersedes_decision_id=None,
        supersession_reason=None,
    )


def _review_decision(
    predecessor: MatchDecisionRow,
    *,
    action: str,
    selected: MatchCandidateEvidenceRow | None,
    actor_user_id: UUID,
    idempotency_key: str,
    request_hash: bytes,
) -> MatchDecisionRow:
    decided_at = datetime.now(UTC)
    if decided_at <= predecessor.decided_at:
        decided_at = predecessor.decided_at + timedelta(microseconds=1)
    return MatchDecisionRow(
        query_type=predecessor.query_type,
        owner_user_id=predecessor.owner_user_id,
        device_id=predecessor.device_id,
        import_entry_id=predecessor.import_entry_id,
        user_track_ref_id=predecessor.user_track_ref_id,
        local_audio_id=predecessor.local_audio_id,
        external_reference_id=predecessor.external_reference_id,
        vault_object_id=predecessor.vault_object_id,
        audio_variant_id=predecessor.audio_variant_id,
        query_snapshot=predecessor.query_snapshot,
        query_snapshot_schema_version=predecessor.query_snapshot_schema_version,
        snapshot_canonicalization_version=predecessor.snapshot_canonicalization_version,
        query_snapshot_sha256=predecessor.query_snapshot_sha256,
        decision_kind="REVIEW_ACTION",
        execution_mode="APPLIED",
        review_action=action,
        reviewed_candidate_evidence_id=(
            selected.match_candidate_evidence_id
            if action in {"ACCEPT", "REJECT"} and selected
            else None
        ),
        candidate_recording_id=(
            selected.recording_id if action in {"ACCEPT", "REJECT"} and selected else None
        ),
        decision_state=predecessor.decision_state,
        candidate_count=predecessor.candidate_count,
        candidate_evidence_sha256=predecessor.candidate_evidence_sha256,
        candidate_evidence_size_bytes=predecessor.candidate_evidence_size_bytes,
        evidence_mode=predecessor.evidence_mode,
        candidate_generation_version=predecessor.candidate_generation_version,
        normalization_version=predecessor.normalization_version,
        feature_extractor_versions=predecessor.feature_extractor_versions,
        matcher_version=predecessor.matcher_version,
        calibrator_version=predecessor.calibrator_version,
        threshold_set_version=predecessor.threshold_set_version,
        raw_score=predecessor.raw_score,
        confidence=predecessor.confidence,
        top2_confidence=predecessor.top2_confidence,
        margin=predecessor.margin,
        evidence_tier=predecessor.evidence_tier,
        feature_scores=predecessor.feature_scores,
        hard_conflicts=predecessor.hard_conflicts,
        candidate_origins=predecessor.candidate_origins,
        explanation_schema_version=predecessor.explanation_schema_version,
        actor_type="USER",
        actor_user_id=actor_user_id,
        idempotency_scope=f"p10-import-review:{actor_user_id}",
        idempotency_key=idempotency_key,
        request_sha256=request_hash,
        supersedes_decision_id=predecessor.decision_id,
        supersession_reason="P10 explicit manual import review",
        decided_at=decided_at,
    )


def _copy_evidence(row: MatchCandidateEvidenceRow) -> MatchCandidateEvidenceRow:
    return MatchCandidateEvidenceRow(
        decision_id=UUID(int=0),
        recording_id=row.recording_id,
        rank=row.rank,
        raw_score=row.raw_score,
        confidence=row.confidence,
        evidence_tier=row.evidence_tier,
        feature_scores=row.feature_scores,
        hard_conflicts=row.hard_conflicts,
        candidate_origins=row.candidate_origins,
        extractor_versions=row.extractor_versions,
        evidence_schema_version=row.evidence_schema_version,
        evidence_sha256=row.evidence_sha256,
        evidence_document_size_bytes=row.evidence_document_size_bytes,
    )


def _review_request_hash(
    entry_id: UUID,
    predecessor_id: UUID,
    action: str,
    selected_rank: int | None,
) -> bytes:
    return hashlib.sha256(
        rfc8785.dumps(
            {
                "schema_version": 1,
                "import_entry_id": str(entry_id),
                "predecessor_decision_id": str(predecessor_id),
                "action": action,
                "selected_rank": selected_rank,
            }
        )
    ).digest()


def _checkpoint(value: JsonValue | None) -> dict[str, JsonValue]:
    return _object(value)


def _summary(value: JsonValue) -> dict[str, JsonValue]:
    return _object(value)


def _object(value: JsonValue | object) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): cast(JsonValue, item) for key, item in value.items()}


def _safe_int(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _coerce_int(value: JsonValue | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _safe_text(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) and value else None


def _fingerprint_bytes(value: str) -> bytes | None:
    try:
        decoded = bytes.fromhex(value)
    except ValueError:
        return None
    return decoded or None


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(f"{value:.6f}")


__all__ = (
    "IMPORT_JOB_KEY",
    "ImportCollectionArtist",
    "ImportEntryReport",
    "ImportJobReport",
    "ImportNotFoundError",
    "ImportOperationConflictError",
    "ImportProcessProgress",
    "ImportReviewConflictError",
    "ImportReviewResult",
    "ImportRuntimeError",
    "ImportStartResult",
    "ImportStateConflictError",
    "PostgresImportRepository",
)
