"""Durable embedded-first metadata enrichment with fenced, short DB transactions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID

from autplay.adapters.public_track_metadata import MusicBrainzMetadataProvider
from autplay.application.job_worker import JobExecutionContext
from autplay.application.track_metadata import TrackMetadataService, document_from
from autplay.domain.jobs import JobLease, RetryableJobError, TerminalJobError
from autplay.domain.metadata_execution import MetadataAudioTarget
from autplay.domain.track_metadata import (
    FieldEvidence,
    MetadataCandidate,
    MetadataQuery,
    automatic_candidate,
)
from autplay.domain.vault import MediaValidationError
from autplay.ports.track_metadata import MetadataByteWork, MetadataProviderError


class TrackMetadataHandler:
    def __init__(
        self,
        service: TrackMetadataService,
        work: MetadataByteWork,
        provider: MusicBrainzMetadataProvider,
        *,
        audio: MetadataAudioTarget | None,
        freeze_renewals: Callable[[], None],
        acoustid_key: str = "",
    ) -> None:
        self.service, self.work, self.provider = service, work, provider
        self.audio, self.freeze_renewals, self.acoustid_key = audio, freeze_renewals, acoustid_key

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        ref_id = UUID(str(lease.payload["user_track_ref_id"]))
        generation = int(str(lease.payload["generation"]))
        try:
            self._run(context, ref_id, generation)
        except MetadataProviderError as error:
            retry = error.retryable and context.fence.attempt_no < 6
            self.freeze_renewals()
            self.service.apply_worker(
                ref_id,
                generation,
                context,
                state="RETRY" if retry else "FAILED",
                error_code=error.code,
            )
            if retry:
                raise RetryableJobError(
                    error.code, {"retry_after_seconds": error.retry_after_seconds}
                ) from error
            raise TerminalJobError(error.code) from error
        except MediaValidationError as error:
            self.freeze_renewals()
            self.service.apply_worker(
                ref_id, generation, context, state="FAILED", error_code="metadata_media_unreadable"
            )
            raise TerminalJobError("metadata_media_unreadable") from error

    def _run(self, context: JobExecutionContext, ref_id: UUID, generation: int) -> None:
        with self.service.sessions.begin() as session:
            ref, row = self.service.locked_job(session, ref_id, generation, context)
            doc = document_from(row)
            query = MetadataQuery(
                str(doc.fields.get("title") or ref.raw_title or ""),
                str(doc.fields.get("artist") or ref.raw_artist or ""),
                str(doc.fields.get("album") or ref.raw_album or "") or None,
                ref.raw_duration_ms,
                str(doc.fields["mb_recording_id"]) if doc.fields.get("mb_recording_id") else None,
            )
            selection = row.document.get("selection")
            artwork_exists = row.artwork_sha256 is not None
        if self.audio is not None:
            self.service.apply_worker(
                ref_id,
                generation,
                context,
                audio_variant_id=self.audio.audio_variant_id,
            )
            embedded = self.work.read_audio()
            evidence = FieldEvidence(
                "EMBEDDED",
                f"sha256:{self.audio.expected.sha256.hex}",
                datetime.now(UTC).isoformat(),
            )
            self.service.apply_worker(
                ref_id,
                generation,
                context,
                fields=embedded.fields,
                evidence=evidence,
                artwork=embedded.artwork,
            )
            artwork_exists = artwork_exists or embedded.artwork is not None
            with self.service.sessions.begin() as session:
                _, current = self.service.locked_job(session, ref_id, generation, context)
                effective = document_from(current).fields
                query = replace(
                    query,
                    title=str(effective.get("title") or query.title),
                    artist=str(effective.get("artist") or query.artist),
                    album=str(effective.get("album") or query.album or "") or None,
                    recording_mbid=str(
                        effective.get("mb_recording_id") or query.recording_mbid or ""
                    )
                    or None,
                )
        context.raise_if_cancelled()
        chosen: MetadataCandidate | None
        candidates: tuple[MetadataCandidate, ...]
        if selection:
            chosen = MetadataCandidate(**selection)
            candidates = (chosen,)
        else:
            search_error = None
            try:
                candidates = self.provider.search(query)
            except MetadataProviderError as error:
                if not error.retryable:
                    raise
                search_error, candidates = error, ()
            if not candidates and self.audio is not None and self.acoustid_key:
                fingerprint = self.work.fingerprint()
                recordings = self.provider.acoustid(
                    fingerprint.payload.decode("ascii"),
                    fingerprint.duration_ms // 1000,
                    self.acoustid_key,
                )
                found: dict[str, MetadataCandidate] = {}
                for recording in recordings:
                    for item in self.provider.search(replace(query, recording_mbid=recording)):
                        # Fingerprints identify recordings, not a unique album edition.
                        found[item.candidate_id] = replace(item, auto_eligible=False)
                candidates = tuple(found.values())[:5]
            if not candidates and search_error is not None:
                raise search_error
            chosen = automatic_candidate(query, candidates)
        if chosen is None:
            self.freeze_renewals()
            self.service.apply_worker(
                ref_id,
                generation,
                context,
                state="REVIEW" if candidates else "NOT_FOUND",
                candidates=[asdict(item) for item in candidates],
            )
            return
        chosen = self.provider.release(chosen)
        evidence = FieldEvidence(
            "MUSICBRAINZ",
            chosen.source_id,
            datetime.now(UTC).isoformat(),
            locked=bool(selection),
        )
        artwork = None
        if selection or not artwork_exists:
            cover = self.provider.cover(str(chosen.fields["mb_release_id"]))
            if cover is not None:
                context.raise_if_cancelled()
                artwork = self.work.normalize_artwork(cover)
        self.freeze_renewals()
        self.service.apply_worker(
            ref_id,
            generation,
            context,
            fields=chosen.fields,
            evidence=evidence,
            artwork=artwork,
            explicit_selection=bool(selection),
            candidates=[asdict(item) for item in candidates],
            state="READY",
        )
