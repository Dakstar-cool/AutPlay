"""Owner-scoped descriptive metadata commands, revision history and job fencing."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import rfc8785
from sqlalchemy import String, cast, func, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import (
    JobRow,
    LibraryEntryRow,
    RecordingCanonicalVariantRow,
    SyncEventRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.track_metadata import (
    MetadataArtworkRow,
    TrackMetadataRevisionRow,
    TrackMetadataRow,
)
from autplay.application.job_worker import JobExecutionContext, JobLeaseLost
from autplay.application.metadata_projection import metadata_view
from autplay.application.music_library import MusicError, MusicLibraryService
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.auth import Principal
from autplay.domain.jobs import JobKey
from autplay.domain.track_metadata import (
    FieldEvidence,
    MetadataDocument,
    MetadataFields,
    validate_fields,
)
from autplay.ports.jobs import EnqueueJob

METADATA_JOB = JobKey("music.metadata.enrich", 1)


def document_from(row: TrackMetadataRow) -> MetadataDocument:
    return MetadataDocument(
        validate_fields(row.document.get("fields", {})),
        {key: FieldEvidence(**value) for key, value in row.document.get("provenance", {}).items()},
    )


class TrackMetadataService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def get(self, principal: Principal, ref_id: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            MusicLibraryService._owned_ref(session, principal, ref_id)
            row = session.get(TrackMetadataRow, ref_id)
            return (
                metadata_view(row)
                if row
                else {
                    "revision": 0,
                    "state": "MISSING",
                    "fields": {},
                    "provenance": {},
                    "candidates": [],
                }
            )

    def command(
        self,
        principal: Principal,
        ref_id: UUID,
        *,
        operation_id: UUID,
        expected_revision: int,
        action: str,
        fields: dict[str, Any] | None = None,
        candidate_id: str | None = None,
    ) -> dict[str, Any]:
        if action not in {"REFRESH", "EDIT", "SELECT"}:
            raise MusicError("metadata_action_invalid", "Unknown metadata action.", 422)
        try:
            validated = validate_fields(fields or {}, manual=True)
        except ValueError as error:
            raise MusicError(str(error), "Check the metadata fields.", 422) from error
        digest = hashlib.sha256(
            rfc8785.dumps(
                {
                    "action": action,
                    "fields": validated,
                    "candidate_id": candidate_id,
                    "expected_revision": expected_revision,
                }
            )
        ).digest()
        with self.sessions.begin() as session:
            _acquire_sync_owner_publish_lock(session, principal.user_id)
            ref = MusicLibraryService._owned_ref(session, principal, ref_id)
            receipt = session.scalar(
                select(TrackMetadataRevisionRow).where(
                    TrackMetadataRevisionRow.user_track_ref_id == ref_id,
                    TrackMetadataRevisionRow.operation_id == operation_id,
                )
            )
            if receipt:
                if receipt.request_sha256 != digest:
                    raise MusicError("metadata_operation_conflict", "The operation conflicts.", 409)
                return receipt.snapshot
            row = session.get(TrackMetadataRow, ref_id)
            if (row.revision if row else 0) != expected_revision:
                raise MusicError(
                    "metadata_revision_conflict", "Metadata changed; reload before editing.", 409
                )
            if row is None:
                row = TrackMetadataRow(
                    user_track_ref_id=ref_id,
                    revision=0,
                    generation=0,
                    document={},
                    candidates=[],
                    state="QUEUED",
                    updated_at=datetime.now(UTC),
                )
                session.add(row)
            if action == "EDIT":
                doc = document_from(row).merge(
                    validated,
                    FieldEvidence("USER", f"edit:{operation_id}", datetime.now(UTC).isoformat()),
                )
                row.document = {
                    **row.document,
                    "fields": doc.fields,
                    "provenance": {key: asdict(value) for key, value in doc.provenance.items()},
                }
                row.generation = max(1, row.generation)
                if row.job_id is None:
                    row.state = "READY"
            else:
                selection = next(
                    (item for item in row.candidates if item.get("candidate_id") == candidate_id),
                    None,
                )
                if action == "SELECT" and selection is None:
                    raise MusicError(
                        "metadata_candidate_missing", "Choose a current metadata result.", 409
                    )
                self._enqueue(session, ref, row, selection, interactive=True)
            self._publish(session, ref, row, operation_id=operation_id, digest=digest)
            return metadata_view(row)

    def enqueue_missing(self, owner_id: UUID | None = None, *, limit: int = 30) -> int:
        # Repeated bounded sweep also observes imports from older acquisition workers.
        self.reconcile_finished(owner_id)
        with self.sessions() as session:
            refs = list(
                session.execute(
                    select(UserTrackRefRow.user_track_ref_id, UserTrackRefRow.user_id)
                    .join(
                        LibraryEntryRow,
                        LibraryEntryRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
                    )
                    .outerjoin(
                        TrackMetadataRow,
                        TrackMetadataRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
                    )
                    .outerjoin(
                        RecordingCanonicalVariantRow,
                        RecordingCanonicalVariantRow.recording_id == UserTrackRefRow.recording_id,
                    )
                    .outerjoin(JobRow, JobRow.job_id == TrackMetadataRow.job_id)
                    .where(
                        UserTrackRefRow.deleted_at.is_(None),
                        LibraryEntryRow.removed_at.is_(None),
                        LibraryEntryRow.user_id == UserTrackRefRow.user_id,
                        true() if owner_id is None else UserTrackRefRow.user_id == owner_id,
                        (TrackMetadataRow.user_track_ref_id.is_(None))
                        | (
                            RecordingCanonicalVariantRow.audio_variant_id.is_not(None)
                            & (LibraryEntryRow.availability_status == "VAULT")
                            & TrackMetadataRow.document["audio_variant_id"].astext.is_distinct_from(
                                cast(RecordingCanonicalVariantRow.audio_variant_id, String)
                            )
                            & (
                                JobRow.job_id.is_(None)
                                | JobRow.state.in_(("COMPLETED", "FAILED", "CANCELLED"))
                            )
                        ),
                    )
                    .order_by(UserTrackRefRow.created_at.desc())
                    .limit(max(1, min(limit, 100)))
                )
            )
        count = 0
        for ref_id, user_id in refs:
            with self.sessions.begin() as session:
                _acquire_sync_owner_publish_lock(session, user_id)
                ref = session.get(UserTrackRefRow, ref_id)
                row = session.get(TrackMetadataRow, ref_id)
                if ref is None or ref.deleted_at is not None:
                    continue
                entry = session.scalar(
                    select(LibraryEntryRow).where(
                        LibraryEntryRow.user_track_ref_id == ref_id,
                        LibraryEntryRow.user_id == user_id,
                        LibraryEntryRow.removed_at.is_(None),
                    )
                )
                if entry is None:
                    continue
                if row is not None:
                    job = session.get(JobRow, row.job_id) if row.job_id else None
                    variant = (
                        session.get(RecordingCanonicalVariantRow, ref.recording_id)
                        if ref.recording_id
                        else None
                    )
                    if job is not None and job.state not in {"COMPLETED", "FAILED", "CANCELLED"}:
                        continue
                    if (
                        variant is None
                        or entry.availability_status != "VAULT"
                        or row.document.get("audio_variant_id") == str(variant.audio_variant_id)
                    ):
                        continue
                else:
                    row = TrackMetadataRow(
                        user_track_ref_id=ref_id,
                        revision=0,
                        generation=0,
                        document={},
                        candidates=[],
                        state="QUEUED",
                        updated_at=datetime.now(UTC),
                    )
                    session.add(row)
                self._enqueue(session, ref, row, None)
                self._publish(session, ref, row)
                count += 1
        return count

    def reconcile_finished(self, owner_id: UUID | None = None) -> None:
        with self.sessions() as session:
            refs = list(
                session.execute(
                    select(UserTrackRefRow.user_track_ref_id, UserTrackRefRow.user_id)
                    .join(
                        TrackMetadataRow,
                        TrackMetadataRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
                    )
                    .join(JobRow, JobRow.job_id == TrackMetadataRow.job_id)
                    .where(
                        TrackMetadataRow.state.in_(("QUEUED", "RETRY")),
                        JobRow.state.in_(("FAILED", "CANCELLED", "COMPLETED")),
                        UserTrackRefRow.deleted_at.is_(None),
                        true() if owner_id is None else UserTrackRefRow.user_id == owner_id,
                    )
                    .limit(100)
                )
            )
        for ref_id, user_id in refs:
            with self.sessions.begin() as session:
                _acquire_sync_owner_publish_lock(session, user_id)
                ref = session.get(UserTrackRefRow, ref_id)
                row = session.get(TrackMetadataRow, ref_id)
                job = session.get(JobRow, row.job_id) if row and row.job_id else None
                if (
                    ref is not None
                    and row is not None
                    and job is not None
                    and row.state in {"QUEUED", "RETRY"}
                    and job.state in {"FAILED", "CANCELLED", "COMPLETED"}
                ):
                    row.state, row.error_code = "FAILED", "metadata_job_ended"
                    self._publish(session, ref, row)

    @staticmethod
    def _enqueue(
        session: Session,
        ref: UserTrackRefRow,
        row: TrackMetadataRow,
        selection: dict[str, Any] | None,
        *,
        interactive: bool = False,
    ) -> None:
        row.generation += 1
        # New rows must satisfy constraints when enqueue performs its database flush.
        row.revision = max(1, row.revision)
        row.state, row.error_code = "QUEUED", None
        row.document = {**row.document, "selection": selection}
        row.job_id = (
            PostgresJobRepository(session)
            .enqueue(
                EnqueueJob(
                    key=METADATA_JOB,
                    user_id=ref.user_id,
                    payload={
                        "user_track_ref_id": str(ref.user_track_ref_id),
                        "generation": row.generation,
                    },
                    priority=0 if interactive else 3,
                    idempotency_scope=f"metadata:{ref.user_track_ref_id}",
                    idempotency_key=str(row.generation),
                )
            )
            .job_id
        )

    def artwork(self, principal: Principal, ref_id: UUID, sha256: str) -> bytes:
        with self.sessions.begin() as session:
            MusicLibraryService._owned_ref(session, principal, ref_id)
            row = session.get(TrackMetadataRow, ref_id)
            if row is None or row.artwork_sha256 != sha256:
                raise MusicError("metadata_artwork_missing", "The artwork is unavailable.", 404)
            art = session.get(MetadataArtworkRow, sha256)
            if art is None:
                raise MusicError("metadata_artwork_missing", "The artwork is unavailable.", 404)
            return art.content

    @staticmethod
    def _publish(
        session: Session,
        ref: UserTrackRefRow,
        row: TrackMetadataRow,
        *,
        operation_id: UUID | None = None,
        digest: bytes | None = None,
    ) -> None:
        row.revision += 1
        row.updated_at = datetime.now(UTC)
        ref.row_version += 1
        ref.updated_at = row.updated_at
        view = metadata_view(row)
        session.flush()
        session.add(
            TrackMetadataRevisionRow(
                user_track_ref_id=ref.user_track_ref_id,
                revision=row.revision,
                snapshot=view,
                operation_id=operation_id,
                request_sha256=digest,
            )
        )
        session.add(
            SyncEventRow(
                event_id=uuid5(NAMESPACE_URL, f"metadata:{ref.user_track_ref_id}:{row.revision}"),
                user_id=ref.user_id,
                origin_device_id=None,
                event_type="USER_TRACK_REF_PATCHED",
                schema_version=1,
                aggregate_type="USER_TRACK_REF",
                aggregate_id=ref.user_track_ref_id,
                operation="UPSERT",
                server_row_version=ref.row_version,
                payload={
                    "recording_id": str(ref.recording_id) if ref.recording_id else None,
                    "title": ref.raw_title,
                    "artist": ref.raw_artist,
                    "album": ref.raw_album,
                    "duration_ms": ref.raw_duration_ms,
                    "resolution_status": ref.resolution_status,
                    "metadata_v1": view,
                },
            )
        )

    @staticmethod
    def locked_job(
        session: Session, ref_id: UUID, generation: int, context: JobExecutionContext
    ) -> tuple[UserTrackRefRow, TrackMetadataRow]:
        fence = context.fence
        # Same order as commands: owner publication lock, then job/track locks.
        user_id = session.scalar(select(JobRow.user_id).where(JobRow.job_id == fence.job_id))
        if user_id is None:
            raise JobLeaseLost
        _acquire_sync_owner_publish_lock(session, user_id)
        job = session.scalar(
            select(JobRow)
            .where(
                JobRow.job_id == fence.job_id,
                JobRow.state == "RUNNING",
                JobRow.lease_owner == fence.worker_id,
                JobRow.attempt_count == fence.attempt_no,
                JobRow.lease_deadline > func.now(),
                JobRow.cancel_requested_at.is_(None),
            )
            .with_for_update()
        )
        ref = session.scalar(
            select(UserTrackRefRow)
            .join(
                LibraryEntryRow,
                LibraryEntryRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
            )
            .where(
                UserTrackRefRow.user_track_ref_id == ref_id,
                UserTrackRefRow.user_id == user_id,
                UserTrackRefRow.deleted_at.is_(None),
                LibraryEntryRow.user_id == user_id,
                LibraryEntryRow.removed_at.is_(None),
            )
            .with_for_update()
        )
        row = session.get(TrackMetadataRow, ref_id)
        if (
            job is None
            or ref is None
            or row is None
            or row.job_id != fence.job_id
            or row.generation != generation
        ):
            raise JobLeaseLost
        return ref, row

    def apply_worker(
        self,
        ref_id: UUID,
        generation: int,
        context: JobExecutionContext,
        *,
        fields: MetadataFields | None = None,
        evidence: FieldEvidence | None = None,
        artwork: bytes | None = None,
        state: str | None = None,
        candidates: list[dict[str, Any]] | None = None,
        error_code: str | None = None,
        explicit_selection: bool = False,
        audio_variant_id: UUID | None = None,
    ) -> None:
        with self.sessions.begin() as session:
            ref, row = self.locked_job(session, ref_id, generation, context)
            if audio_variant_id is not None:
                row.document = {**row.document, "audio_variant_id": str(audio_variant_id)}
            if fields is not None and evidence is not None:
                previous = document_from(row)
                if explicit_selection:
                    # A chosen edition replaces all automatic description as one snapshot.
                    # User values, including explicit clears, survive that choice.
                    previous = MetadataDocument(
                        {
                            k: v
                            for k, v in previous.fields.items()
                            if previous.provenance[k].source == "USER"
                        },
                        {k: v for k, v in previous.provenance.items() if v.source == "USER"},
                    )
                doc = previous.merge(
                    fields,
                    evidence,
                    fill_only=evidence.source == "MUSICBRAINZ" and not explicit_selection,
                )
                row.document = {
                    **row.document,
                    "fields": doc.fields,
                    "provenance": {key: asdict(value) for key, value in doc.provenance.items()},
                }
            if explicit_selection:
                row.artwork_sha256 = None
                row.document = {**row.document, "artwork_source": None}
            if artwork is not None and evidence is not None:
                sha = hashlib.sha256(artwork).hexdigest()
                session.execute(
                    insert(MetadataArtworkRow)
                    .values(sha256=sha, content=artwork)
                    .on_conflict_do_nothing()
                )
                # Embedded cover stays preferred over external art on background refresh.
                if row.artwork_sha256 is None or (
                    evidence.source == "EMBEDDED"
                    and not (row.document.get("artwork_source") or {}).get("locked")
                ):
                    row.artwork_sha256 = sha
                    row.document = {**row.document, "artwork_source": asdict(evidence)}
            if candidates is not None:
                row.candidates = candidates
            if state is not None:
                row.state = state
            row.error_code = error_code
            self._publish(session, ref, row)
