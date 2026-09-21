"""Explicit owner commands for preparing and publishing phone/Vault transfers."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.identity_decisions import (
    CreateRecordingReviewCommand,
    execute_create_recording_review,
)
from autplay.adapters.postgresql.import_runtime import PostgresImportRepository
from autplay.adapters.postgresql.models import (
    ArtistCreditNameRow,
    ArtistCreditRow,
    ArtistRow,
    LibraryEntryRow,
    MatchCandidateEvidenceRow,
    MatchDecisionRow,
    RecordingCanonicalVariantRow,
    RecordingRow,
    SyncEventRow,
    UploadSessionRow,
    UserTrackRefRow,
)
from autplay.application.identity_evidence import (
    candidate_aggregate_sha256,
    canonical_query_snapshot,
)
from autplay.application.sync import CatalogArtistSyncPublisher, _acquire_sync_owner_publish_lock
from autplay.domain.auth import Principal
from autplay.domain.import_identity import (
    CANDIDATE_GENERATION_VERSION,
    FEATURE_EXTRACTOR_VERSIONS,
    MATCHER_VERSION,
    NORMALIZATION_VERSION,
    normalize_text,
)


class MusicError(RuntimeError):
    """Safe application failure translated by the HTTP boundary."""

    def __init__(
        self, code: str, message: str, status_code: int, *, retryable: bool = False
    ) -> None:
        self.code, self.message, self.status_code, self.retryable = (
            code,
            message,
            status_code,
            retryable,
        )
        super().__init__(code)


class MusicLibraryService:
    """Keep UserTrackRef identity and publish only verified, owner-authorized audio."""

    def __init__(self, sessions: sessionmaker[Session], vault: Any) -> None:
        self.sessions = sessions
        self.vault = vault

    def prepare(self, principal: Principal, ref_id: UUID) -> UUID:
        with self.sessions.begin() as session:
            return self.prepare_in_transaction(session, principal.user_id, ref_id)

    @staticmethod
    def prepare_in_transaction(session: Session, owner_user_id: UUID, ref_id: UUID) -> UUID:
        """Prepare owned identity after the caller validates authority in this transaction."""
        _acquire_sync_owner_publish_lock(session, owner_user_id)
        ref = MusicLibraryService._owned_ref(session, owner_user_id, ref_id)
        if ref.recording_id is not None:
            return ref.recording_id
        predecessor = (
            session.get(MatchDecisionRow, ref.current_match_decision_id)
            if ref.current_match_decision_id
            else None
        )
        if predecessor is None:
            PostgresImportRepository(session).ensure_matcher_release()
            predecessor = _predecessor(ref, owner_user_id)
            session.add(predecessor)
            session.flush()
        if predecessor.owner_user_id != owner_user_id or predecessor.user_track_ref_id != ref_id:
            raise MusicError("music_identity_conflict", "The track needs identity review.", 409)
        artist_name = ref.raw_artist or "Unknown artist"
        artist = ArtistRow(
            name=artist_name,
            sort_name=artist_name,
            normalized_name=normalize_text(artist_name),
            identity_status="PROVISIONAL",
        )
        credit = ArtistCreditRow(
            display_name=artist_name, normalized_name=normalize_text(artist_name)
        )
        session.add_all([artist, credit])
        session.flush()
        session.add(
            ArtistCreditNameRow(
                artist_credit_id=credit.artist_credit_id,
                position=0,
                artist_id=artist.artist_id,
                credited_name=artist_name,
                join_phrase="",
                role="PRIMARY",
            )
        )
        title = ref.raw_title or "Audio"
        recording = RecordingRow(
            artist_credit_id=credit.artist_credit_id,
            title=title,
            normalized_title=normalize_text(title),
            duration_ms=ref.raw_duration_ms,
            recording_kind="UNKNOWN",
            identity_status="PROVISIONAL",
        )
        values = {
            c.name: deepcopy(getattr(predecessor, c.name))
            for c in MatchDecisionRow.__table__.columns
            if c.name not in {"decision_id", "created_at"}
        }
        values.update(
            decision_kind="REVIEW_ACTION",
            execution_mode="APPLIED",
            review_action="CREATE_RECORDING",
            reviewed_candidate_evidence_id=None,
            candidate_recording_id=None,
            actor_type="USER",
            actor_user_id=owner_user_id,
            idempotency_scope=f"music-prepare:{owner_user_id}",
            idempotency_key=str(ref_id),
            request_sha256=hashlib.sha256(
                predecessor.request_sha256 + b"\0music-create-recording-v1"
            ).digest(),
            supersedes_decision_id=predecessor.decision_id,
            supersession_reason="Explicit owner selection for Vault upload",
            decided_at=max(datetime.now(UTC), predecessor.decided_at + timedelta(microseconds=1)),
        )
        review = MatchDecisionRow(**values)
        evidence = tuple(
            MatchCandidateEvidenceRow(
                **{
                    c.name: deepcopy(getattr(row, c.name))
                    for c in MatchCandidateEvidenceRow.__table__.columns
                    if c.name not in {"match_candidate_evidence_id", "decision_id", "created_at"}
                }
            )
            for row in session.scalars(
                select(MatchCandidateEvidenceRow).where(
                    MatchCandidateEvidenceRow.decision_id == predecessor.decision_id
                )
            ).all()
        )

        def project(active: Session, decision: MatchDecisionRow, created: RecordingRow) -> None:
            ref.recording_id = created.recording_id
            ref.resolution_status = "RESOLVED"
            ref.current_match_decision_id = decision.decision_id
            ref.resolved_at = decision.decided_at
            ref.resolution_confidence = decision.confidence
            ref.row_version += 1
            ref.updated_at = datetime.now(UTC)

        session.flush()
        execute_create_recording_review(
            session, CreateRecordingReviewCommand(recording, review, evidence, project)
        )
        session.flush()
        CatalogArtistSyncPublisher().publish(session, owner_user_id, ref_ids=(ref_id,))
        return recording.recording_id

    def publish(self, principal: Principal, ref_id: UUID, upload_id: UUID) -> UUID:
        with self.sessions.begin() as session:
            self._prepare_publication(
                session, principal.user_id, ref_id, upload_id, principal.device_id
            )
        variant = self.vault.resolve_playback_variant(principal, ref_id)
        with self.sessions.begin() as session:
            self._project_publication(session, principal.user_id, ref_id, upload_id)
        return UUID(str(variant))

    @staticmethod
    def publish_in_transaction(
        session: Session,
        owner_user_id: UUID,
        ref_id: UUID,
        upload_id: UUID,
        *,
        resolve_variant: Callable[[UUID, UUID], UUID],
    ) -> UUID:
        """Publish authorized Internet ingest in the caller's transaction, including sync."""
        MusicLibraryService._prepare_publication(session, owner_user_id, ref_id, upload_id, None)
        session.flush()
        variant = resolve_variant(owner_user_id, ref_id)
        MusicLibraryService._project_publication(session, owner_user_id, ref_id, upload_id)
        return variant

    @staticmethod
    def _prepare_publication(
        session: Session,
        owner_user_id: UUID,
        ref_id: UUID,
        upload_id: UUID,
        device_id: UUID | None,
    ) -> None:
        _acquire_sync_owner_publish_lock(session, owner_user_id)
        ref = MusicLibraryService._owned_ref(session, owner_user_id, ref_id)
        upload = session.get(UploadSessionRow, upload_id)
        if (
            upload is None
            or upload.user_id != owner_user_id
            or (device_id is None and upload.actor_kind != "INTERNET")
            or (
                device_id is not None
                and (upload.actor_kind != "DEVICE" or upload.device_id != device_id)
            )
        ):
            raise MusicError("music_upload_not_found", "The upload is unavailable.", 404)
        if upload.target_recording_id != ref.recording_id or upload.state not in {
            "COMMITTED",
            "REUSED",
        }:
            raise MusicError(
                "music_upload_not_ready", "The upload is not ready.", 409, retryable=True
            )
        if upload.computed_sha256 is None or upload.audio_variant_id is None:
            raise MusicError("music_upload_not_verified", "The upload is not verified.", 409)
        session.execute(
            insert(RecordingCanonicalVariantRow)
            .values(
                recording_id=ref.recording_id,
                audio_variant_id=upload.audio_variant_id,
                policy_version="owner-verified-upload-v1",
                reason={"upload_session_id": str(upload_id)},
            )
            .on_conflict_do_nothing(index_elements=[RecordingCanonicalVariantRow.recording_id])
        )

    @staticmethod
    def _project_publication(
        session: Session,
        owner_user_id: UUID,
        ref_id: UUID,
        upload_id: UUID,
    ) -> None:
        _acquire_sync_owner_publish_lock(session, owner_user_id)
        MusicLibraryService._owned_ref(session, owner_user_id, ref_id)
        entry = session.scalar(
            select(LibraryEntryRow)
            .where(
                LibraryEntryRow.user_id == owner_user_id,
                LibraryEntryRow.user_track_ref_id == ref_id,
                LibraryEntryRow.removed_at.is_(None),
            )
            .with_for_update()
        )
        if entry is None:
            raise MusicError("music_track_not_found", "The track is unavailable.", 404)
        if entry.availability_status != "VAULT":
            entry.availability_status = "VAULT"
            entry.row_version += 1
            entry.updated_at = datetime.now(UTC)
        CatalogArtistSyncPublisher().publish(session, owner_user_id, ref_ids=(ref_id,))
        session.execute(
            insert(SyncEventRow)
            .values(
                event_id=uuid5(
                    NAMESPACE_URL,
                    f"music-publish:{owner_user_id}:{ref_id}:{upload_id}:{entry.row_version}",
                ),
                user_id=owner_user_id,
                origin_device_id=None,
                event_type="LIBRARY_ENTRY_UPSERTED",
                schema_version=1,
                aggregate_type="LIBRARY_ENTRY",
                aggregate_id=entry.library_entry_id,
                payload={
                    "server_user_track_ref_id": str(ref_id),
                    "source": entry.source,
                    "availability_status": "VAULT",
                },
                operation="UPSERT",
                server_row_version=entry.row_version,
            )
            .on_conflict_do_nothing(index_elements=[SyncEventRow.event_id])
        )

    @staticmethod
    def _owned_ref(session: Session, owner_user_id: UUID, ref_id: UUID) -> UserTrackRefRow:
        ref = session.scalar(
            select(UserTrackRefRow)
            .where(
                UserTrackRefRow.user_track_ref_id == ref_id,
                UserTrackRefRow.user_id == owner_user_id,
                UserTrackRefRow.deleted_at.is_(None),
            )
            .with_for_update()
        )
        entry = session.scalar(
            select(LibraryEntryRow.library_entry_id).where(
                LibraryEntryRow.user_id == owner_user_id,
                LibraryEntryRow.user_track_ref_id == ref_id,
                LibraryEntryRow.removed_at.is_(None),
            )
        )
        if ref is None or entry is None:
            raise MusicError("music_track_not_found", "The track is unavailable.", 404)
        return ref


def _predecessor(ref: UserTrackRefRow, owner_user_id: UUID) -> MatchDecisionRow:
    snapshot = canonical_query_snapshot(
        {
            "normalized_title": normalize_text(ref.raw_title or "Audio"),
            "normalized_artists": [normalize_text(ref.raw_artist or "Unknown artist")],
            "duration_ms": ref.raw_duration_ms,
            "version_markers": [],
            "market_scope": "GLOBAL",
            "evidence_ids": [f"user-track-ref:{ref.user_track_ref_id}"],
        }
    )
    aggregate, _ = candidate_aggregate_sha256([])
    return MatchDecisionRow(
        query_type="USER_TRACK_REF",
        owner_user_id=owner_user_id,
        device_id=None,
        user_track_ref_id=ref.user_track_ref_id,
        query_snapshot=snapshot.value,
        query_snapshot_schema_version="1",
        snapshot_canonicalization_version="RFC8785",
        query_snapshot_sha256=snapshot.sha256,
        decision_kind="EVALUATION",
        execution_mode="SHADOW",
        decision_state="DEFERRED_EVIDENCE",
        candidate_count=0,
        candidate_evidence_sha256=aggregate,
        candidate_evidence_size_bytes=0,
        evidence_mode="METADATA_ONLY",
        candidate_generation_version=CANDIDATE_GENERATION_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        feature_extractor_versions=dict(FEATURE_EXTRACTOR_VERSIONS),
        matcher_version=MATCHER_VERSION,
        feature_scores=[],
        hard_conflicts=[],
        candidate_origins=[],
        explanation_schema_version="1",
        actor_type="SYSTEM",
        idempotency_scope="music-owner-evaluation",
        idempotency_key=str(ref.user_track_ref_id),
        request_sha256=hashlib.sha256(
            snapshot.canonical_bytes + b"\0" + aggregate + b"\0SHADOW"
        ).digest(),
        decided_at=datetime.now(UTC),
    )
