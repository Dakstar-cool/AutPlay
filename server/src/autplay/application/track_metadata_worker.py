"""Durable embedded-first metadata enrichment with fenced, short DB transactions."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from autplay.adapters.media.tools import ChromaprintTool
from autplay.adapters.media.track_metadata import FfmpegMetadataReader
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.adapters.public_track_metadata import MusicBrainzMetadataProvider
from autplay.application.job_worker import JobExecutionContext
from autplay.application.track_metadata import TrackMetadataService, document_from
from autplay.application.vault_uploads import VaultNotFoundError, VaultPrincipal
from autplay.domain.jobs import JobLease, RetryableJobError, TerminalJobError
from autplay.domain.track_metadata import (
    FieldEvidence,
    MetadataCandidate,
    MetadataQuery,
    automatic_candidate,
)
from autplay.domain.vault import ByteRange, MediaValidationError
from autplay.ports.track_metadata import MetadataProviderError
from autplay.ports.vault import VaultStorage


class TrackMetadataHandler:
    def __init__(
        self,
        service: TrackMetadataService,
        storage: VaultStorage,
        reader: FfmpegMetadataReader,
        provider: MusicBrainzMetadataProvider,
        *,
        acoustid_key: str = "",
        fingerprint: ChromaprintTool | None = None,
    ) -> None:
        self.service, self.storage, self.reader, self.provider = service, storage, reader, provider
        self.acoustid_key, self.fingerprint = acoustid_key, fingerprint

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        ref_id = UUID(str(lease.payload["user_track_ref_id"]))
        generation = int(str(lease.payload["generation"]))
        try:
            self._run(context, ref_id, generation)
        except MetadataProviderError as error:
            retry = error.retryable and context.fence.attempt_no < 6
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
            runtime = PostgresVaultRuntime(session)
            try:
                principal = VaultPrincipal(ref.user_id, UUID(int=0))
                variant_id = runtime.resolve_playback_variant(principal, ref_id)
                stream = runtime.resolve_stream(principal, variant_id)
            except VaultNotFoundError:
                stream = None
        with tempfile.TemporaryDirectory(prefix="autplay-metadata-") as directory:
            local = Path(directory) / "audio"
            if stream is not None:
                self.service.apply_worker(ref_id, generation, context, audio_variant_id=variant_id)
                digest = hashlib.sha256()
                source = self.storage.open_range(
                    stream.storage_key,
                    ByteRange(0, stream.byte_size - 1),
                    expected_size=stream.byte_size,
                    verified_at=stream.verified_at,
                )
                try:
                    with local.open("wb") as target:
                        for chunk in source:
                            context.raise_if_cancelled()
                            digest.update(chunk)
                            target.write(chunk)
                finally:
                    source.close()
                if digest.digest() != stream.sha256.value:
                    raise MetadataProviderError("metadata_audio_integrity", retryable=False)
                embedded = self.reader.read(local)
                evidence = FieldEvidence(
                    "EMBEDDED", f"sha256:{digest.hexdigest()}", datetime.now(UTC).isoformat()
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
                if not candidates and stream is not None and self.acoustid_key and self.fingerprint:
                    fingerprint = self.fingerprint.fingerprint(local)
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
                    artwork = self.reader.normalize_artwork(cover)
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
