"""Owner-scoped descriptive metadata commands, revision history and job fencing."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import rfc8785
from sqlalchemy import String, cast, exists, func, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.acquisition_authority import require_acquisition_session
from autplay.adapters.postgresql.catalogue_context import PostgresCatalogueContextRepository
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.metadata_authority import lock_metadata_job
from autplay.adapters.postgresql.metadata_execution import (
    MetadataStatus,
    require_metadata_execution,
)
from autplay.adapters.postgresql.models import (
    InternetAcquisitionRow,
    JobRow,
    LibraryEntryRow,
    ProviderStagingRow,
    RecordingCanonicalVariantRow,
    SyncEventRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.account import UserAccountRow
from autplay.adapters.postgresql.models.metadata_execution import MetadataExecutionRow
from autplay.adapters.postgresql.models.resource_admission import ResourceIoExecutionRow
from autplay.adapters.postgresql.models.track_metadata import (
    MetadataArtworkRow,
    TrackMetadataRevisionRow,
    TrackMetadataRow,
)
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.application.internet_acquisition import InternetAcquisitionTarget
from autplay.application.job_worker import JobExecutionContext, JobLeaseLost
from autplay.application.metadata_projection import coherent_album_group, metadata_view
from autplay.application.music_library import MusicError, MusicLibraryService
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.application.vault_uploads import VaultNotFoundError
from autplay.domain.auth import Principal
from autplay.domain.catalogue_context import CatalogueLookupContext
from autplay.domain.jobs import JobKey, RetryableJobError
from autplay.domain.resource_admission import AcquisitionClaim, ResourceAdmissionError
from autplay.domain.track_metadata import (
    FieldEvidence,
    MetadataDocument,
    MetadataFields,
    sanitize_source_metadata,
    source_metadata_document,
    validate_fields,
)
from autplay.ports.jobs import EnqueueJob

METADATA_JOB = JobKey("music.metadata.enrich", 1)
METADATA_ENRICHMENT_VERSION = 2


def acquisition_sha256(document: dict[str, Any]) -> str:
    """A worker result belongs to the acquisition evidence it actually observed."""
    return hashlib.sha256(rfc8785.dumps(document.get("acquisition_evidence", {}))).hexdigest()


def _group_locked(group: dict[str, object] | None) -> bool:
    proof = group.get("evidence") if group is not None else None
    return isinstance(proof, dict) and proof.get("locked") is True


def document_from(row: TrackMetadataRow) -> MetadataDocument:
    return MetadataDocument(
        validate_fields(row.document.get("fields", {})),
        {key: FieldEvidence(**value) for key, value in row.document.get("provenance", {}).items()},
    )


class TrackMetadataService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        execution: MetadataStatus | None = None,
    ) -> None:
        self.sessions, self.execution = sessions, execution

    @staticmethod
    def catalogue_context(
        session: Session,
        ref: UserTrackRefRow,
        row: TrackMetadataRow,
        *,
        audio_variant_id: UUID | None,
    ) -> CatalogueLookupContext | None:
        """Read lookup bounds only through the actual published original acquisition."""
        identity = row.document.get("published_acquisition_id")
        if not identity or audio_variant_id is None:
            return None
        try:
            acquisition_id = UUID(str(identity))
        except ValueError:
            return None
        source = session.get(InternetAcquisitionRow, acquisition_id)
        if (
            source is None
            or source.user_id != ref.user_id
            or source.user_track_ref_id != ref.user_track_ref_id
            or source.state != "READY"
            or source.audio_variant_id != audio_variant_id
        ):
            return None
        try:
            return PostgresCatalogueContextRepository(session).for_acquisition(
                ref.user_id, acquisition_id
            )
        except MusicError:
            # A corrupt optional catalogue receipt cannot fabricate source facts.
            return None

    @staticmethod
    def _set_acquisition_evidence(row: TrackMetadataRow, normalized: dict[str, object]) -> bool:
        changed = (
            "acquisition_evidence" not in row.document
            or row.document.get("acquisition_evidence") != normalized
        )
        if not changed:
            return False
        previous_group = coherent_album_group(row.document)
        selected_group = previous_group if _group_locked(previous_group) else None
        fields, provenance = row.document.get("fields", {}), row.document.get("provenance", {})
        stale_native = {
            key
            for key, proof in provenance.items()
            if proof.get("source") == "SOURCE_NATIVE"
            and not proof.get("locked")
            and key != "genres"
        }
        row.document = {
            **row.document,
            "fields": {key: value for key, value in fields.items() if key not in stale_native},
            "provenance": {
                key: value for key, value in provenance.items() if key not in stale_native
            },
            "acquisition_evidence": normalized,
            "acquisition_refresh_pending": True,
            "album_group_v1": selected_group,
        }
        art = row.document.get("artwork_source") or {}
        if selected_group is None and art.get("source") == "MUSICBRAINZ" and not art.get("locked"):
            row.artwork_sha256 = None
            row.document = {**row.document, "artwork_source": None}
        return True

    @staticmethod
    def stage_acquisition_in_transaction(
        session: Session,
        *,
        claim: AcquisitionClaim,
        target: InternetAcquisitionTarget,
        execution_id: UUID,
        evidence: dict[str, object],
    ) -> None:
        """Retain exited producer evidence atomically with its internal ingest handoff."""
        _acquire_sync_owner_publish_lock(session, target.user_id)
        job = session.get(JobRow, claim.fence.job_id, with_for_update=True, populate_existing=True)
        source = session.get(InternetAcquisitionRow, target.acquisition_id, with_for_update=True)
        account = session.get(UserAccountRow, target.user_id, populate_existing=True)
        now = session.scalar(select(func.clock_timestamp()))
        if (
            not isinstance(now, datetime)
            or claim.resource_type != "INTERNET_ACQUISITION"
            or claim.acquisition_id != target.acquisition_id
            or job is None
            or source is None
            or account is None
            or account.status != "ACTIVE"
            or account.deleted_at is not None
            or source.authority_generation != account.authority_generation
            or job.user_id != target.user_id
            or job.job_type != "music.internet.acquire"
            or job.schema_version != 1
            or job.state != "RUNNING"
            or job.lease_owner != claim.fence.worker_id
            or job.attempt_count != claim.fence.attempt_no
            or job.lease_deadline is None
            or job.lease_deadline <= now
            or job.cancel_requested_at is not None
            or not isinstance(job.payload, dict)
            or job.payload.get("acquisition_id") != str(target.acquisition_id)
            or source.job_id != job.job_id
            or source.user_id != target.user_id
            or source.user_track_ref_id != target.ref_id
            or source.candidate_id != target.candidate_id
            or source.state != "DOWNLOADING"
        ):
            raise JobLeaseLost
        require_acquisition_session(
            session,
            user_id=target.user_id,
            device_id=source.device_id,
            family_id=source.source_session_family_id,
            mode=source.source_session_mode,
            now=now,
        )
        ref = MusicLibraryService._owned_ref(session, target.user_id, target.ref_id)
        if ref.recording_id != target.recording_id or ref.resolution_status != "RESOLVED":
            raise JobLeaseLost
        staging = session.get(ProviderStagingRow, execution_id, with_for_update=True)
        execution = session.get(ResourceIoExecutionRow, execution_id)
        if (
            staging is None
            or staging.state != "EXITED"
            or staging.exit_code != 0
            or staging.closed_at is None
            or staging.closure_kind not in {"PROCESS_EXIT", "SUPERVISOR_EXIT"}
            or (
                staging.user_id,
                staging.resource_type,
                staging.acquisition_id,
                staging.job_id,
                staging.job_worker_id,
                staging.job_attempt,
            )
            != (
                target.user_id,
                claim.resource_type,
                target.acquisition_id,
                claim.fence.job_id,
                claim.fence.worker_id,
                claim.fence.attempt_no,
            )
            or (
                execution is not None
                and (
                    execution.closed_at is None
                    or execution.state != "CLOSED"
                    or execution.kind != "PROVIDER"
                    or execution.actual_target_id != target.acquisition_id
                    or execution.owner_run_id != staging.owner_run_id
                )
            )
            or session.scalar(
                select(
                    exists().where(
                        ResourceIoExecutionRow.kind == "PROVIDER",
                        ResourceIoExecutionRow.actual_target_id == target.acquisition_id,
                        ResourceIoExecutionRow.closed_at.is_(None),
                    )
                )
            )
        ):
            raise JobLeaseLost
        try:
            normalized = (
                source_metadata_document(sanitize_source_metadata(evidence)) if evidence else {}
            )
        except ValueError:
            normalized = {}
        row = session.get(TrackMetadataRow, target.ref_id, with_for_update=True)
        if row is None:
            row = TrackMetadataRow(
                user_track_ref_id=target.ref_id,
                revision=1,
                generation=1,
                document={},
                candidates=[],
                state="QUEUED",
                updated_at=now,
            )
            session.add(row)
        if row.document.get("pending_acquisition_execution_id") == str(execution_id):
            if row.document.get("pending_acquisition_evidence") != normalized:
                raise MusicError("metadata_operation_conflict", "The operation conflicts.", 409)
            return
        row.document = {
            **row.document,
            "pending_acquisition_evidence": normalized,
            "pending_acquisition_execution_id": str(execution_id),
            "pending_acquisition_id": str(target.acquisition_id),
            "source_artist_explicit": False,
        }
        row.updated_at = now

    @staticmethod
    def _activate_staged_acquisition(
        session: Session, ref: UserTrackRefRow, row: TrackMetadataRow
    ) -> None:
        source_id = row.document.get("pending_acquisition_id")
        if not source_id:
            return
        source = session.scalar(
            select(InternetAcquisitionRow).where(
                cast(InternetAcquisitionRow.acquisition_id, String) == source_id,
                InternetAcquisitionRow.user_id == ref.user_id,
                InternetAcquisitionRow.user_track_ref_id == ref.user_track_ref_id,
                InternetAcquisitionRow.state == "READY",
                InternetAcquisitionRow.audio_variant_id.is_not(None),
            )
        )
        if source is None:
            return
        try:
            variant = PostgresVaultRuntime(session).resolve_owner_playback_variant(
                ref.user_id, ref.user_track_ref_id
            )
        except VaultNotFoundError:
            return
        if variant != source.audio_variant_id:
            return
        native = row.document.get("pending_acquisition_evidence", {})
        TrackMetadataService._set_acquisition_evidence(row, native)
        if not (native.get("fields") or {}).get("artist"):
            artist_proof = (row.document.get("provenance") or {}).get("artist") or {}
            if artist_proof.get("source") == "MUSICBRAINZ" and not artist_proof.get("locked"):
                row.document = {
                    **row.document,
                    "fields": {
                        key: value
                        for key, value in row.document.get("fields", {}).items()
                        if key != "artist"
                    },
                    "provenance": {
                        key: value
                        for key, value in row.document.get("provenance", {}).items()
                        if key != "artist"
                    },
                }
        row.document = {
            **{
                key: value
                for key, value in row.document.items()
                if key
                not in {
                    "pending_acquisition_evidence",
                    "pending_acquisition_execution_id",
                    "pending_acquisition_id",
                }
            },
            "acquisition_evidence": native,
            "published_acquisition_id": str(source.acquisition_id),
            "source_artist_explicit": bool((native.get("fields") or {}).get("artist")),
            "acquisition_refresh_pending": True,
        }

    def accept_acquisition(
        self,
        principal: Principal,
        ref_id: UUID,
        *,
        operation_id: UUID,
        evidence: dict[str, object],
    ) -> dict[str, Any]:
        """Accept bounded evidence after publication, without provider or filesystem I/O."""
        try:
            normalized = (
                source_metadata_document(sanitize_source_metadata(evidence)) if evidence else {}
            )
        except ValueError:
            # Optional native evidence cannot turn published audio into a failure.
            normalized = {}
        request: dict[str, Any] = {"action": "ACCEPT_ACQUISITION", "evidence": normalized}
        digest = hashlib.sha256(rfc8785.dumps(request)).digest()
        with self.sessions.begin() as session:
            _acquire_sync_owner_publish_lock(session, principal.user_id)
            ref = MusicLibraryService._owned_ref(session, principal.user_id, ref_id)
            account = session.get(UserAccountRow, principal.user_id)
            entry = session.scalar(
                select(LibraryEntryRow).where(
                    LibraryEntryRow.user_track_ref_id == ref_id,
                    LibraryEntryRow.user_id == principal.user_id,
                    LibraryEntryRow.removed_at.is_(None),
                    LibraryEntryRow.availability_status == "VAULT",
                )
            )
            if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
                raise MusicError("music_track_not_found", "The track is unavailable.", 404)
            if entry is None:
                raise MusicError("metadata_audio_unpublished", "Published audio is required.", 409)
            try:
                PostgresVaultRuntime(session).resolve_owner_playback_variant(
                    principal.user_id, ref_id
                )
            except VaultNotFoundError as error:
                raise MusicError(
                    "metadata_audio_unpublished", "Published audio is required.", 409
                ) from error
            receipt = session.scalar(
                select(TrackMetadataRevisionRow).where(
                    TrackMetadataRevisionRow.user_track_ref_id == ref_id,
                    TrackMetadataRevisionRow.operation_id == operation_id,
                )
            )
            if receipt is not None:
                if receipt.request_sha256 != digest:
                    raise MusicError("metadata_operation_conflict", "The operation conflicts.", 409)
                return receipt.snapshot
            row = session.get(TrackMetadataRow, ref_id, with_for_update=True)
            new = row is None
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
            changed = self._set_acquisition_evidence(row, normalized)
            if new or changed:
                job = session.get(JobRow, row.job_id) if row.job_id else None
                if job is None or job.state in {"COMPLETED", "FAILED", "CANCELLED"}:
                    self._enqueue(session, ref, row, row.document.get("selection"))
            self._publish(session, ref, row, operation_id=operation_id, digest=digest)
            return metadata_view(row)

    def get(self, principal: Principal, ref_id: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            MusicLibraryService._owned_ref(session, principal.user_id, ref_id)
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
            ref = MusicLibraryService._owned_ref(session, principal.user_id, ref_id)
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
                self._enqueue(
                    session,
                    ref,
                    row,
                    selection if action == "SELECT" else row.document.get("selection"),
                    interactive=True,
                )
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
                            & (
                                TrackMetadataRow.document[
                                    "audio_variant_id"
                                ].astext.is_distinct_from(
                                    cast(RecordingCanonicalVariantRow.audio_variant_id, String)
                                )
                                | (
                                    TrackMetadataRow.document[
                                        "acquisition_refresh_pending"
                                    ].as_boolean()
                                    == true()
                                )
                                | TrackMetadataRow.document["enrichment_version"]
                                .as_integer()
                                .is_distinct_from(METADATA_ENRICHMENT_VERSION)
                                | exists().where(
                                    cast(InternetAcquisitionRow.acquisition_id, String)
                                    == TrackMetadataRow.document["pending_acquisition_id"].astext,
                                    InternetAcquisitionRow.user_id == UserTrackRefRow.user_id,
                                    InternetAcquisitionRow.user_track_ref_id
                                    == UserTrackRefRow.user_track_ref_id,
                                    InternetAcquisitionRow.state == "READY",
                                    InternetAcquisitionRow.audio_variant_id
                                    == RecordingCanonicalVariantRow.audio_variant_id,
                                )
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
                    self._activate_staged_acquisition(session, ref, row)
                    if (
                        variant is None
                        or entry.availability_status != "VAULT"
                        or (
                            row.document.get("audio_variant_id") == str(variant.audio_variant_id)
                            and not row.document.get("acquisition_refresh_pending")
                            and row.document.get("enrichment_version")
                            == METADATA_ENRICHMENT_VERSION
                        )
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
                self._enqueue(session, ref, row, row.document.get("selection"))
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
                    if row.document.get("worker_acquisition_sha256") == acquisition_sha256(
                        row.document
                    ):
                        row.document = {
                            **row.document,
                            "processed_acquisition_sha256": acquisition_sha256(row.document),
                            "acquisition_refresh_pending": False,
                        }
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
        TrackMetadataService._activate_staged_acquisition(session, ref, row)
        row.generation += 1
        # New rows must satisfy constraints when enqueue performs its database flush.
        row.revision = max(1, row.revision)
        row.state, row.error_code = "QUEUED", None
        row.document = {
            **row.document,
            "selection": selection,
            "enrichment_version": METADATA_ENRICHMENT_VERSION,
        }
        row.job_id = (
            PostgresJobRepository(session)
            .enqueue(
                EnqueueJob(
                    key=METADATA_JOB,
                    user_id=ref.user_id,
                    payload={
                        "user_track_ref_id": str(ref.user_track_ref_id),
                        "generation": row.generation,
                        "authority_generation": session.scalar(
                            select(UserAccountRow.authority_generation).where(
                                UserAccountRow.user_id == ref.user_id,
                                UserAccountRow.status == "ACTIVE",
                                UserAccountRow.deleted_at.is_(None),
                            )
                        ),
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
            MusicLibraryService._owned_ref(session, principal.user_id, ref_id)
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
        if row.document.get("album_group_v1") is not None:
            row.document = {**row.document, "album_group_v1": coherent_album_group(row.document)}
            if (
                row.document["album_group_v1"] is None
                and (row.document.get("artwork_source") or {}).get("source") == "MUSICBRAINZ"
            ):
                row.artwork_sha256 = None
                row.document = {**row.document, "artwork_source": None}
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

    def locked_job(
        self,
        session: Session,
        ref_id: UUID,
        generation: int,
        context: JobExecutionContext,
    ) -> tuple[UserTrackRefRow, TrackMetadataRow]:
        ref, row, _ = lock_metadata_job(session, ref_id, generation, context.fence)
        if self.execution is not None:
            ticket = self.execution.ticket
            if (ticket.user_track_ref_id, ticket.generation, ticket.fence) != (
                ref_id,
                generation,
                context.fence,
            ):
                raise JobLeaseLost
            require_metadata_execution(session, self.execution)
        elif (
            session.scalar(
                select(MetadataExecutionRow.execution_id)
                .where(
                    MetadataExecutionRow.user_track_ref_id == ref_id,
                    MetadataExecutionRow.closed_at.is_(None),
                )
                .limit(1)
            )
            is not None
        ):
            raise ResourceAdmissionError("metadata_execution_busy")
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
        album_group: dict[str, object] | None = None,
        expected_acquisition_sha256: str | None = None,
        clear_album_group: bool = False,
    ) -> None:
        with self.sessions.begin() as session:
            ref, row = self.locked_job(session, ref_id, generation, context)
            if (
                expected_acquisition_sha256 is not None
                and acquisition_sha256(row.document) != expected_acquisition_sha256
            ):
                raise RetryableJobError("metadata_evidence_changed", {"retry_after_seconds": 1})
            if expected_acquisition_sha256 is not None:
                row.document = {
                    **row.document,
                    "worker_acquisition_sha256": expected_acquisition_sha256,
                }
            if audio_variant_id is not None:
                row.document = {**row.document, "audio_variant_id": str(audio_variant_id)}
            native_changed = False
            if fields is not None and evidence is not None:
                previous = document_from(row)
                if evidence.source == "SOURCE_NATIVE":
                    # A refreshed source snapshot replaces its old observations
                    # and public fills for supplied fields. Embedded/user/locked
                    # values remain authoritative.
                    has_native_album = (
                        (row.document.get("acquisition_evidence") or {})
                        .get("external_ids", {})
                        .get("native_album_id")
                    )
                    native_changed = row.document.get(
                        "native_acquisition_sha256"
                    ) != acquisition_sha256(row.document)
                    recording_changed = any(
                        key in fields and previous.fields.get(key) != fields[key]
                        for key in ("title", "artist")
                    )
                    stale = {
                        key
                        for key, old in previous.provenance.items()
                        if not old.locked
                        and (
                            (old.source == "SOURCE_NATIVE" and (key != "genres" or key in fields))
                            or (
                                old.source == "MUSICBRAINZ"
                                and (
                                    key in fields
                                    or (
                                        has_native_album
                                        and native_changed
                                        and key
                                        in {
                                            "album",
                                            "album_artist",
                                            "release_date",
                                            "original_release_date",
                                            "track_number",
                                            "disc_number",
                                            "mb_release_id",
                                            "mb_release_group_id",
                                        }
                                    )
                                    or (recording_changed and key == "mb_recording_id")
                                )
                            )
                        )
                    }
                    previous = MetadataDocument(
                        {key: value for key, value in previous.fields.items() if key not in stale},
                        {
                            key: value
                            for key, value in previous.provenance.items()
                            if key not in stale
                        },
                    )
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
                # Edition replacement retains independently observed genres when
                # the chosen release has no genre data of its own.
                if explicit_selection and "genres" not in fields:
                    old = document_from(row)
                    if "genres" in old.fields:
                        previous = MetadataDocument(
                            {**previous.fields, "genres": old.fields["genres"]},
                            {**previous.provenance, "genres": old.provenance["genres"]},
                        )
                doc = previous.merge(
                    fields,
                    evidence,
                    fill_only=(
                        evidence.source in {"MUSICBRAINZ", "SOURCE_NATIVE"}
                        and not explicit_selection
                    ),
                )
                row.document = {
                    **row.document,
                    "fields": doc.fields,
                    "provenance": {key: asdict(value) for key, value in doc.provenance.items()},
                }
                if evidence.source == "SOURCE_NATIVE":
                    row.document = {
                        **row.document,
                        "native_acquisition_sha256": acquisition_sha256(row.document),
                    }
            if explicit_selection and not (
                artwork is None
                and evidence is not None
                and (row.document.get("artwork_source") or {}).get("source") == "MUSICBRAINZ"
                and (row.document.get("artwork_source") or {}).get("source_id")
                == evidence.source_id
            ):
                row.artwork_sha256 = None
                row.document = {**row.document, "artwork_source": None}
            current_group = coherent_album_group(row.document)
            preserve_selected_group = not explicit_selection and (
                _group_locked(current_group)
                or (
                    evidence is not None
                    and evidence.source == "SOURCE_NATIVE"
                    and not native_changed
                    and current_group is not None
                    and current_group.get("provider") == "MUSICBRAINZ"
                )
            )
            if album_group is not None and not preserve_selected_group:
                row.document = {**row.document, "album_group_v1": album_group}
            elif not preserve_selected_group and (
                clear_album_group or state in {"REVIEW", "NOT_FOUND"} or explicit_selection
            ):
                row.document = {**row.document, "album_group_v1": None}
            if (
                album_group is not None
                and coherent_album_group(row.document) is None
                and evidence is not None
                and evidence.source == "MUSICBRAINZ"
            ):
                # Do not attach selected-release art to a contradictory embedded album.
                artwork = None
            if (
                artwork is not None
                and evidence is not None
                and evidence.source == "MUSICBRAINZ"
                and fields is not None
            ):
                actual_fields = row.document.get("fields", {})
                if any(
                    actual_fields.get(key) != fields.get(key) for key in ("album", "mb_release_id")
                ):
                    artwork = None
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
                if state in {"READY", "REVIEW", "NOT_FOUND", "FAILED"}:
                    row.document = {
                        **row.document,
                        "processed_acquisition_sha256": acquisition_sha256(row.document),
                        "acquisition_refresh_pending": False,
                    }
            row.error_code = error_code
            self._publish(session, ref, row)
            if self.execution is not None:
                # Flush all publication first, then run the SQL authority/expiry
                # guard without extending the grant. A late failure rolls it all back.
                session.flush()
                session.execute(
                    update(MetadataExecutionRow)
                    .where(MetadataExecutionRow.execution_id == self.execution.ticket.execution_id)
                    .values(io_deadline_at=MetadataExecutionRow.io_deadline_at)
                )
