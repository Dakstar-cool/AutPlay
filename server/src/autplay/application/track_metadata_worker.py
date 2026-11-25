"""Durable embedded-first metadata enrichment with fenced, short DB transactions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import exists, select

from autplay.adapters.postgresql.models import InternetAcquisitionRow
from autplay.adapters.postgresql.models.catalog import RecordingRow
from autplay.adapters.public_track_metadata import MusicBrainzMetadataProvider
from autplay.application.job_worker import JobExecutionContext
from autplay.application.track_metadata import (
    TrackMetadataService,
    acquisition_sha256,
    document_from,
)
from autplay.domain.catalog_artwork import label, native_image_host
from autplay.domain.original_artwork import original_artwork_source
from autplay.domain.web_artwork import archived_editions
from autplay.domain.recovery_artwork import selected_group_art
from autplay.domain.catalogue_context import CatalogueContextEntity
from autplay.domain.jobs import JobLease, RetryableJobError, TerminalJobError
from autplay.domain.metadata_execution import MetadataAudioTarget
from autplay.domain.track_metadata import (
    FieldEvidence,
    MetadataCandidate,
    MetadataQuery,
    album_group_document,
    album_group_for,
    automatic_candidate,
    native_album_group_for,
    parse_source_metadata,
)
from autplay.domain.vault import MediaValidationError
from autplay.ports.track_metadata import MetadataByteWork, MetadataProviderError


def _review_document(candidate: MetadataCandidate) -> dict[str, Any]:
    # A subsequent selection hydrates its exact release again.
    document = asdict(candidate)
    for key in ("recording_members", "track_members", "release_members"):
        document.pop(key)
    return document


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
        self._acquisition_sha256: str | None = None
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
                expected_acquisition_sha256=self._acquisition_sha256,
            )
            if retry:
                raise RetryableJobError(
                    error.code, {"retry_after_seconds": error.retry_after_seconds}
                ) from error
            raise TerminalJobError(error.code) from error
        except MediaValidationError as error:
            self.freeze_renewals()
            self.service.apply_worker(
                ref_id,
                generation,
                context,
                state="FAILED",
                error_code="metadata_media_unreadable",
                expected_acquisition_sha256=self._acquisition_sha256,
            )
            raise TerminalJobError("metadata_media_unreadable") from error
        except RetryableJobError:
            self.freeze_renewals()
            raise

    def _run(self, context: JobExecutionContext, ref_id: UUID, generation: int) -> None:
        with self.service.sessions.begin() as session:
            ref, row = self.service.locked_job(session, ref_id, generation, context)
            doc = document_from(row)
            self._acquisition_sha256 = acquisition_sha256(row.document)
            raw_native = row.document.get("acquisition_evidence")
            native = parse_source_metadata(raw_native) if raw_native else None
            internet_origin = bool(
                session.scalar(
                    select(
                        exists().where(
                            InternetAcquisitionRow.user_track_ref_id == ref_id,
                            InternetAcquisitionRow.user_id == ref.user_id,
                        )
                    )
                )
            )
            unproved_source_artist = row.document.get("source_artist_explicit") is False or (
                internet_origin and row.document.get("source_artist_explicit") is not True
            )
            query = MetadataQuery(
                str(doc.fields.get("title") or ref.raw_title or ""),
                str(
                    doc.fields.get("artist")
                    or (ref.raw_artist if not unproved_source_artist else "")
                    or ""
                ),
                str(doc.fields.get("album") or ref.raw_album or "") or None,
                ref.raw_duration_ms,
                str(doc.fields["mb_recording_id"]) if doc.fields.get("mb_recording_id") else None,
            )
            selection = row.document.get("selection")
            edition_locked = any(doc.provenance[key].locked for key in ("album", "mb_release_id") if key in doc.provenance)
            artwork_exists = row.artwork_sha256 is not None and (row.document.get("artwork_source") or {}).get("source") != "PUBLIC_CATALOG"
            original_exists = bool(row.artwork_sha256 and original_artwork_source(row.document.get("artwork_source")))
            fallback_cover_exists = row.artwork_sha256 is not None and not original_exists
            performance = row.document.get("musicbrainz_performance_v1")
            version = None
            if ref.recording_id and not (selection or edition_locked or fallback_cover_exists):
                version = session.execute(select(RecordingRow.recording_kind, RecordingRow.version_text)
                                          .where(RecordingRow.recording_id == ref.recording_id)).one_or_none()
            saved_portrait = row.document.get("catalog_artist_image_v1") or {}
        self.service.apply_worker(
            ref_id,
            generation,
            context,
            expected_acquisition_sha256=self._acquisition_sha256,
        )
        measured_duration_ms: int | None = None
        if self.audio is not None:
            self.service.apply_worker(
                ref_id,
                generation,
                context,
                audio_variant_id=self.audio.audio_variant_id,
                expected_acquisition_sha256=self._acquisition_sha256,
            )
            embedded = self.work.read_audio()
            measured_duration_ms = embedded.duration_ms
            if embedded.duration_ms is not None:
                query = replace(query, duration_ms=embedded.duration_ms)
            evidence = FieldEvidence(
                "EMBEDDED",
                f"sha256:{self.audio.expected.sha256.hex}",
                datetime.now(UTC).isoformat(),
            )
            self.service.apply_worker(
                ref_id,
                generation,
                context,
                audio_variant_id=self.audio.audio_variant_id,
                loudness=embedded.loudness,
                fields=embedded.fields,
                evidence=evidence,
                artwork=embedded.artwork,
                expected_acquisition_sha256=self._acquisition_sha256,
            )
            artwork_exists = artwork_exists or embedded.artwork is not None
        native_group: dict[str, object] | None = None
        if native is not None:
            native_evidence = FieldEvidence(
                "SOURCE_NATIVE",
                f"{native.provider}:{native.source_id}",
                datetime.now(UTC).isoformat(),
            )
            if (group := native_album_group_for(native, native_evidence)) is not None:
                native_group = album_group_document(group)
            self.service.apply_worker(
                ref_id,
                generation,
                context,
                fields=native.fields,
                evidence=native_evidence,
                album_group=native_group,
                expected_acquisition_sha256=self._acquisition_sha256,
            )
            if not artwork_exists and not selection and not edition_locked and isinstance(self.provider, MusicBrainzMetadataProvider):
                album_id = native.external_ids.get("native_album_id")
                for art in sorted(native.artwork, key=lambda item: item.kind != "album"):
                    bound = (art.kind == "track" and art.source_id == native.source_id or
                             art.kind == "album" and album_id is not None and art.source_id == f"album:{album_id}")
                    if not bound or native_image_host(art.url) != native.provider:
                        continue
                    try:
                        if payload := self.provider.public_image(art.url):
                            self.service.apply_worker(ref_id, generation, context, evidence=native_evidence,
                                artwork=self.work.normalize_artwork(payload), expected_acquisition_sha256=self._acquisition_sha256)
                            artwork_exists = True
                            break
                    except (MetadataProviderError, MediaValidationError):
                        continue
        with self.service.sessions.begin() as session:
            ref, current = self.service.locked_job(session, ref_id, generation, context)
            effective_document = document_from(current)
            original_exists = bool(current.artwork_sha256 and original_artwork_source(current.document.get("artwork_source")))
            fallback_cover_exists = bool(current.artwork_sha256 and not original_exists)
            effective = effective_document.fields
            artist_proof = effective_document.provenance.get("artist")
            actual_artist = effective.get("artist", query.artist)
            if (
                unproved_source_artist
                and artist_proof is not None
                and artist_proof.source == "MUSICBRAINZ"
                and not artist_proof.locked
            ):
                actual_artist = ""
            query = replace(
                query,
                title=str(effective.get("title", query.title) or ""),
                artist=str(actual_artist or ""),
                album=str(effective.get("album", query.album) or "") or None,
                recording_mbid=str(effective.get("mb_recording_id") or "") or None,
                release_mbid=str(effective.get("mb_release_id") or "") or None,
                release_date=str(effective.get("release_date") or "") or None,
                track_number=(n if isinstance(n := effective.get("track_number"), int) else None),
                disc_number=(n if isinstance(n := effective.get("disc_number"), int) else None),
                source_metadata=native,
            )
            lookup = self.service.catalogue_context(
                session,
                ref,
                current,
                audio_variant_id=self.audio.audio_variant_id if self.audio is not None else None,
            )
            proof_fields = tuple(
                effective_document.provenance.get(key) for key in ("title", "artist")
            )
            if (
                not selection
                and lookup is not None
                and all(
                    proof is not None and proof.source in {"EMBEDDED", "SOURCE_NATIVE"}
                    for proof in proof_fields
                )
                and lookup.corroborates(replace(query, duration_ms=measured_duration_ms))
            ):
                query = replace(
                    query,
                    recording_mbid=str(lookup.card.recording_mbid),
                    release_mbid=str(lookup.card.release_mbid)
                    if lookup.card.release_mbid is not None
                    else query.release_mbid,
                    release_track_mbid=(
                        str(lookup.card.entity_id)
                        if lookup.card.entity_type == CatalogueContextEntity.RELEASE_TRACK
                        else None
                    ),
                )

        def supplemental(need_cover: bool, art_query: MetadataQuery, review=()):
            need_cover = need_cover and not (fallback_cover_exists or edition_locked or selection)
            portrait_exists = bool(saved_portrait) and label(saved_portrait.get("credit") or saved_portrait.get("name") or "") == label(art_query.artist)
            if (not need_cover and portrait_exists) or not isinstance(self.provider, MusicBrainzMetadataProvider):
                return None
            if unproved_source_artist and not (artist_proof and artist_proof.source in {"EMBEDDED", "SOURCE_NATIVE", "USER"}):
                return None
            if not art_query.artist or not art_query.title or not art_query.duration_ms:
                return None
            need_portrait = not portrait_exists
            pending_error = None
            result = None

            def save_result():
                if result:
                    self.service.apply_worker(ref_id, generation, context, public_images=result,
                                              expected_acquisition_sha256=self._acquisition_sha256)

            def image(url: str | None, *, is_cover=False) -> bytes | None:
                nonlocal pending_error
                if not url:
                    return None
                context.raise_if_cancelled()
                try:
                    data = self.provider.public_image(url)
                    if data and is_cover:
                        return self.work.normalize_artwork(data, catalog_cover=True)
                    return self.work.normalize_artwork(data) if data else None
                except MetadataProviderError as error:
                    if error.retryable:
                        pending_error = error
                except MediaValidationError:
                    pass
                return None

            if need_cover and review:
                archived = archived_editions([_review_document(candidate) for candidate in review], art_query.title,
                                            art_query.artist, art_query.album, art_query.duration_ms)
                for candidate in archived:
                    if art_query.release_mbid and (candidate.entity != "release" or candidate.album_id != art_query.release_mbid):
                        continue
                    if cover := image(candidate.cover, is_cover=True):
                        result = (candidate, cover, None)
                        need_cover = False
                        break

            for source in (self.provider.find_deezer_artwork, self.provider.find_yandex_artwork, self.provider.find_listenbrainz_cover,
                           self.provider.find_publisher_cover, self.provider.find_archive_cover):
                if not need_cover and (not need_portrait or source in (self.provider.find_listenbrainz_cover, self.provider.find_publisher_cover, self.provider.find_archive_cover)):
                    break
                context.raise_if_cancelled()
                try:
                    match = source(art_query, review) if source == self.provider.find_listenbrainz_cover else source(art_query)
                except MetadataProviderError as error:
                    if error.retryable:
                        pending_error = error
                    continue
                if not match:
                    continue
                correct_edition = not art_query.release_mbid or (
                    match.provider == "COVER_ART_ARCHIVE" and match.entity == "release" and match.album_id == art_query.release_mbid)
                cover = image(match.cover, is_cover=True) if need_cover and correct_edition else None
                face = image(match.portrait) if need_portrait else None
                if cover or face:
                    save_result()
                    result = (match, cover, face)
                    need_cover = need_cover and cover is None
                    need_portrait = need_portrait and face is None
            if need_cover and not original_exists and (not pending_error or context.fence.attempt_no >= 3):
                context.raise_if_cancelled()
                try:
                    match = self.provider.find_original_artwork(art_query, performance, *(version or (None, None)))
                    if match and (cover := image(match.cover, is_cover=True)):
                        save_result()
                        result = (match, cover, None)
                        need_cover = False
                except MetadataProviderError as error:
                    if error.retryable:
                        pending_error = error
            if pending_error and (need_cover or need_portrait) and context.fence.attempt_no < 3:
                save_result()
                raise pending_error
            return result

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
                if images := supplemental(not artwork_exists, query):
                    self.service.apply_worker(ref_id, generation, context, public_images=images,
                                              expected_acquisition_sha256=self._acquisition_sha256)
                raise search_error
            chosen = automatic_candidate(query, candidates)
        if chosen is None:
            images = supplemental(not artwork_exists, query, candidates)
            self.freeze_renewals()
            self.service.apply_worker(
                ref_id,
                generation,
                context,
                public_images=images,
                state="REVIEW" if candidates else "NOT_FOUND",
                candidates=[_review_document(item) for item in candidates],
                album_group=native_group,
                expected_acquisition_sha256=self._acquisition_sha256,
            )
            return
        if not chosen.release_hydrated or (selection and not chosen.release_members):
            chosen = self.provider.release(chosen)
        if not selection and not chosen.exact_for(query):
            images = supplemental(not artwork_exists, query, (chosen,))
            self.freeze_renewals()
            self.service.apply_worker(
                ref_id,
                generation,
                context,
                state="REVIEW",
                public_images=images,
                candidates=[_review_document(chosen)],
                album_group=native_group,
                expected_acquisition_sha256=self._acquisition_sha256,
            )
            return
        evidence = FieldEvidence(
            "MUSICBRAINZ",
            chosen.source_id,
            datetime.now(UTC).isoformat(),
            locked=bool(selection),
        )
        group_document = (
            album_group_document(group)
            if (group := album_group_for(chosen, evidence)) is not None
            else None
        )
        # Persist confirmed description before optional decoding/network work.
        # A child-protocol failure still follows the normal retained-tree failure
        # path, while the already authorized edition snapshot remains visible.
        self.service.apply_worker(
            ref_id,
            generation,
            context,
            fields=chosen.fields,
            evidence=evidence,
            explicit_selection=bool(selection),
            candidates=[_review_document(item) for item in candidates],
            album_group=group_document,
            catalog_candidate=chosen,
            clear_album_group=True,
            expected_acquisition_sha256=self._acquisition_sha256,
        )
        artwork = None
        group_artwork = False
        artwork_error: MetadataProviderError | None = None
        if selection or not artwork_exists:
            try:
                cover = self.provider.cover(str(chosen.fields["mb_release_id"]))
                if (cover is None and not selection and chosen.fields.get("mb_release_group_id")
                        and isinstance(self.provider, MusicBrainzMetadataProvider)):
                    cover = self.provider.group_cover(str(chosen.fields["mb_release_group_id"]))
                    group_artwork = cover is not None
                if cover is not None:
                    context.raise_if_cancelled()
                    artwork = self.work.normalize_artwork(cover)
            except MetadataProviderError as error:
                artwork_error = error
            except MediaValidationError:
                artwork_error = MetadataProviderError(
                    "metadata_artwork_unreadable", retryable=False
                )
        with self.service.sessions.begin() as session:
            _, current = self.service.locked_job(session, ref_id, generation, context)
            fields = document_from(current).fields
            original_exists = bool(current.artwork_sha256 and original_artwork_source(current.document.get("artwork_source")))
            fallback_cover_exists = bool(current.artwork_sha256 and not original_exists)
            art_query = replace(query, title=str(fields.get("title") or query.title),
                                artist=str(fields.get("artist") or query.artist),
                                recording_mbid=str(fields.get("mb_recording_id") or "") or query.recording_mbid,
                                release_mbid=str(fields.get("mb_release_id") or "") or query.release_mbid,
                                album=str(fields.get("album") or query.album or "") or None)
        if group_artwork and artwork:
            group_match = selected_group_art(chosen.fields, art_query.title, art_query.artist)
            if group_match:
                self.service.apply_worker(ref_id, generation, context, public_images=(group_match, artwork, None),
                                          expected_acquisition_sha256=self._acquisition_sha256)
                artwork_exists = True
            artwork = None
        images = supplemental(not (artwork_exists or artwork or selection), art_query)
        self.freeze_renewals()
        self.service.apply_worker(
            ref_id,
            generation,
            context,
            public_images=images,
            fields=chosen.fields,
            evidence=evidence,
            artwork=artwork,
            explicit_selection=False,
            candidates=[_review_document(item) for item in candidates],
            state="READY",
            album_group=group_document,
            catalog_candidate=chosen,
            error_code=artwork_error.code if artwork_error else None,
            expected_acquisition_sha256=self._acquisition_sha256,
            clear_album_group=True,
        )
        if artwork_error is not None and artwork_error.retryable and context.fence.attempt_no < 3:
            # Confirmed description is already visible while optional art retries.
            raise RetryableJobError(
                artwork_error.code, {"retry_after_seconds": artwork_error.retry_after_seconds}
            )
