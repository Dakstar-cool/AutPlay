"""Published acquisition handoff, edition grouping and stale-result database proof."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_child import ChildProtocolError
from autplay.adapters.postgresql.catalogue_context import PostgresCatalogueContextRepository
from autplay.adapters.postgresql.internet_acquisition import PostgresInternetAcquisitionRepository
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.metadata_authority import metadata_audio
from autplay.adapters.postgresql.models import (
    JobRow,
    SyncEventRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.track_metadata import (
    MetadataArtworkRow,
    TrackMetadataRevisionRow,
    TrackMetadataRow,
)
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.adapters.public_track_metadata import MusicBrainzMetadataProvider
from autplay.application.internet_acquisition import InternetAcquisitionTarget
from autplay.application.internet_music import INTERNET_ACQUIRE_JOB, InternetMusicService
from autplay.application.job_worker import JobExecutionContext, JobLeaseLost
from autplay.application.music_library import MusicError, MusicLibraryService
from autplay.application.sync import _bootstrap_projections
from autplay.application.track_metadata import (
    METADATA_JOB,
    TrackMetadataService,
    acquisition_sha256,
)
from autplay.application.track_metadata_worker import TrackMetadataHandler
from autplay.application.vault_ingest import VaultIngestHandler
from autplay.domain.auth import Principal
from autplay.domain.catalogue_context import CatalogueContextEntity, CatalogueTrackCard
from autplay.domain.jobs import JobError, JobKey, JobLease, RetryableJobError
from autplay.domain.metadata_execution import MetadataAudioTarget
from autplay.domain.resource_admission import AcquisitionClaim
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.track_metadata import (
    FieldEvidence,
    MetadataCandidate,
    MetadataQuery,
    album_group_document,
    album_group_for,
)
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    OpaqueStorageKey,
    Sha256Digest,
    VerifiedStagedFile,
)
from autplay.ports.track_metadata import EmbeddedMetadata, MetadataProviderError
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from .test_acquisition_authority import Provider as SearchProvider
from .test_internet_handoff import ready
from .test_internet_ingest_authority import PAYLOAD
from .test_internet_vault_publication import Fingerprints, Media
from .test_music_library import owner
from .test_resource_admission_runtime import AdmissionHarness, admission, fence
from .test_track_metadata import setup

__all__ = ["admission"]


def publish_audio(sessions: sessionmaker[Session], principal: Principal, ref_id: UUID) -> UUID:
    with sessions.begin() as session:
        recording = MusicLibraryService.prepare_in_transaction(session, principal.user_id, ref_id)
        object_id, variant_id = uuid4(), uuid4()
        session.execute(
            text("""INSERT INTO vault.vault_object
            (vault_object_id,sha256,byte_size,detected_mime_type,commit_status,committed_at)
            VALUES (:id,:sha,1024,'audio/flac','COMMITTED',now())"""),
            {"id": object_id, "sha": hashlib.sha256(object_id.bytes).digest()},
        )
        session.execute(
            text("""INSERT INTO vault.vault_replica
            (vault_object_id,storage_backend,storage_key,replica_status,verified_at)
            VALUES (:id,'LOCAL_FILESYSTEM',:key,'AVAILABLE',now())"""),
            {"id": object_id, "key": object_id.hex},
        )
        session.execute(
            text("""INSERT INTO vault.audio_variant
            (audio_variant_id,recording_id,vault_object_id,codec,container,sample_rate_hz,
             channels,duration_ms,validation_status)
            VALUES (:id,:recording,:object,'flac','flac',44100,2,180000,'VALID')"""),
            {"id": variant_id, "recording": recording, "object": object_id},
        )
        session.execute(
            text("""INSERT INTO vault.recording_canonical_variant
            (recording_id,audio_variant_id,policy_version) VALUES (:recording,:id,'test-v1')"""),
            {"recording": recording, "id": variant_id},
        )
        session.execute(
            text(
                "UPDATE library.library_entry SET availability_status='VAULT' "
                "WHERE user_track_ref_id=:id"
            ),
            {"id": ref_id},
        )
        return variant_id


def native(album: str = "Album", album_id: str = "42") -> dict[str, object]:
    return {
        "schema_version": 1,
        "provider": "JAMENDO",
        "source_id": "track:17",
        "fields": {
            "title": "Original",
            "artist": "Artist",
            "album": album,
            "album_artist": "Artist",
            "release_date": "2001",
            "genres": ["Rock"],
        },
        "external_ids": {"native_album_id": album_id},
        "artwork": [],
    }


def claim(sessions: sessionmaker[Session]) -> tuple[JobLease, JobExecutionContext]:
    with sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="album-proof",
            supported=(METADATA_JOB,),
            lease_interval=timedelta(seconds=60),
            limit=1,
        )[0]
    return lease, JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
        fence=lease.fence,
        lease_interval=timedelta(seconds=60),
    )


class Work:
    def read_audio(self) -> EmbeddedMetadata:
        return EmbeddedMetadata({})

    def fingerprint(self) -> ChromaprintEvidence:
        raise AssertionError("fingerprint should not run")

    def normalize_artwork(self, payload: bytes) -> bytes:
        return payload


class Provider(MusicBrainzMetadataProvider):
    def __init__(self, candidate: MetadataCandidate | None = None) -> None:
        self.candidate = candidate
        self.query: MetadataQuery | None = None
        self.cover_error: MetadataProviderError | None = None
        self.search_error: MetadataProviderError | None = None

    def search(self, query: MetadataQuery) -> tuple[MetadataCandidate, ...]:
        self.query = query
        if self.search_error:
            raise self.search_error
        return (self.candidate,) if self.candidate else ()

    def release(
        self, candidate: MetadataCandidate, *, release_track_mbid: str | None = None
    ) -> MetadataCandidate:
        return candidate

    def cover(self, release_id: str) -> bytes | None:
        if self.cover_error:
            raise self.cover_error
        return b"edition-cover"


def candidate() -> MetadataCandidate:
    release = str(uuid4())
    return MetadataCandidate(
        "edition",
        {
            "title": "Original",
            "artist": "Artist",
            "album": "Album",
            "album_artist": "Artist",
            "mb_release_id": release,
            "release_date": "2001",
            "track_number": 2,
            "disc_number": 1,
        },
        180000,
        100,
        f"musicbrainz:release:{release}",
        release_status="Official",
        release_hydrated=True,
    )


def run(
    service: TrackMetadataService, lease: JobLease, context: JobExecutionContext, provider: Provider
) -> None:
    TrackMetadataHandler(service, Work(), provider, audio=None, freeze_renewals=lambda: None)(
        context, lease
    )


def test_handoff_sanitizes_optional_leaves_and_concurrent_receipts(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    publish_audio(sessions, principal, ref_id)
    evidence = native()
    evidence["artwork"] = [
        {"kind": "album", "url": "https://127.0.0.1/private", "source_id": "art"}
    ]
    operation = uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: service.accept_acquisition(
                    principal, ref_id, operation_id=operation, evidence=evidence
                ),
                range(2),
            )
        )
    assert results[0] == results[1]
    service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence=native())
    with sessions() as session:
        row = session.get(TrackMetadataRow, ref_id)
        assert row is not None and row.generation == 1
        assert row.document["acquisition_evidence"]["fields"]["genres"] == ["Rock"]
        assert row.document["acquisition_evidence"]["artwork"] == []
        assert len(list(session.scalars(select(JobRow)))) == 1
        assert len(list(session.scalars(select(TrackMetadataRevisionRow)))) == 2
    with pytest.raises(MusicError, match="metadata_operation_conflict"):
        service.accept_acquisition(
            principal, ref_id, operation_id=operation, evidence=native("Other")
        )
    with pytest.raises(MusicError) as denied:
        service.accept_acquisition(owner(sessions), ref_id, operation_id=uuid4(), evidence=native())
    assert denied.value.status_code == 404
    engine.dispose()


def test_handoff_requires_live_published_audio(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    with pytest.raises(MusicError, match="metadata_audio_unpublished"):
        service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence={})
    publish_audio(sessions, principal, ref_id)
    with sessions.begin() as session:
        session.execute(text("UPDATE vault.vault_object SET commit_status='QUARANTINED'"))
    with pytest.raises(MusicError, match="metadata_audio_unpublished"):
        service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence={})
    with sessions.begin() as session:
        session.execute(text("UPDATE library.library_entry SET removed_at=now()"))
    with pytest.raises(MusicError) as removed:
        service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence={})
    assert removed.value.status_code == 404
    engine.dispose()


def test_empty_sweep_first_handoff_stale_failure_veto_and_finished_followup(
    database_url: str,
) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    variant = publish_audio(sessions, principal, ref_id)
    assert service.enqueue_missing() == 1
    lease, context = claim(sessions)
    with sessions() as session:
        row = session.get(TrackMetadataRow, ref_id)
        assert row is not None
        old_digest = acquisition_sha256(row.document)
    service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence={})
    service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence=native())
    with pytest.raises(RetryableJobError, match="metadata_evidence_changed"):
        service.apply_worker(
            ref_id,
            1,
            context,
            state="FAILED",
            error_code="stale-error",
            expected_acquisition_sha256=old_digest,
        )
    with sessions.begin() as session:
        PostgresJobRepository(session).complete(lease.fence)
        row = session.get(TrackMetadataRow, ref_id)
        assert row is not None and row.generation == 1
        row.document = {**row.document, "audio_variant_id": str(variant)}
    assert service.enqueue_missing() == 1
    assert service.enqueue_missing() == 0
    second, current = claim(sessions)
    run(service, second, current, Provider())
    assert service.get(principal, ref_id)["album_group_v1"]["key"] == "native:jamendo:album:42"
    with sessions.begin() as session:
        PostgresJobRepository(session).complete(second.fence)
    assert service.enqueue_missing() == 0
    engine.dispose()


@pytest.mark.parametrize("failure", [False, True])
def test_native_group_and_provenance_survive_missing_catalogue(
    database_url: str, failure: bool
) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    publish_audio(sessions, principal, ref_id)
    service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence=native())
    lease, context = claim(sessions)
    provider = Provider()
    if failure:
        provider.search_error = MetadataProviderError("provider_busy")
        with pytest.raises(RetryableJobError):
            run(service, lease, context, provider)
    else:
        run(service, lease, context, provider)
    view = service.get(principal, ref_id)
    assert view["fields"]["genres"] == ["Rock"]
    assert view["provenance"]["album"]["source"] == "SOURCE_NATIVE"
    assert view["album_group_v1"]["key"] == "native:jamendo:album:42"
    assert provider.query is not None and provider.query.release_date == "2001"
    assert provider.query.source_metadata is not None
    with sessions() as session:
        boot = _bootstrap_projections(session, principal.user_id)
        assert next(item[3] for item in boot if item[0] == "USER_TRACK_REF")["metadata_v1"] == view
        event = session.scalar(
            select(SyncEventRow)
            .where(SyncEventRow.aggregate_id == ref_id)
            .order_by(SyncEventRow.server_row_version.desc())
        )
        assert event is not None and isinstance(event.payload, dict)
        assert event.payload["metadata_v1"] == view
    engine.dispose()


def test_confirmed_edition_publishes_before_optional_cover_retry(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    with sessions.begin() as session:
        session.execute(text("UPDATE library.user_track_ref SET raw_duration_ms=180000"))
    service.enqueue_missing()
    lease, context = claim(sessions)
    provider = Provider(candidate())
    provider.cover_error = MetadataProviderError("cover_busy", retry_after_seconds=2)
    with pytest.raises(RetryableJobError, match="cover_busy"):
        run(service, lease, context, provider)
    view = service.get(principal, ref_id)
    assert view["state"] == "READY" and view["album_group_v1"] is not None
    assert view["artwork_sha256"] is None
    provider.cover_error = None
    run(service, lease, context, provider)
    assert service.get(principal, ref_id)["artwork_sha256"] is not None
    engine.dispose()


def test_selected_edition_manual_clear_genres_and_group_removal(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    service.enqueue_missing()
    _lease, context = claim(sessions)
    service.apply_worker(
        ref_id,
        1,
        context,
        fields={"genres": ["Jazz"], "album": "Old"},
        evidence=FieldEvidence("EMBEDDED", "bytes", datetime.now(UTC).isoformat()),
    )
    view = service.get(principal, ref_id)
    service.command(
        principal,
        ref_id,
        operation_id=uuid4(),
        expected_revision=view["revision"],
        action="EDIT",
        fields={"release_date": None},
    )
    chosen = candidate()
    proof = FieldEvidence(
        "MUSICBRAINZ", chosen.source_id, datetime.now(UTC).isoformat(), locked=True
    )
    group = album_group_for(chosen, proof)
    assert group is not None
    service.apply_worker(
        ref_id,
        1,
        context,
        fields=chosen.fields,
        evidence=proof,
        artwork=b"selected-art",
        album_group=album_group_document(group),
        explicit_selection=True,
        state="READY",
    )
    selected = service.get(principal, ref_id)
    assert selected["fields"]["genres"] == ["Jazz"] and selected["fields"]["release_date"] is None
    assert selected["album_group_v1"]["release_date"] is None
    cleared = service.command(
        principal,
        ref_id,
        operation_id=uuid4(),
        expected_revision=selected["revision"],
        action="EDIT",
        fields={"album": None},
    )
    assert cleared["album_group_v1"] is None and cleared["artwork_sha256"] is None
    engine.dispose()


def test_embedded_edition_conflict_withholds_group_and_external_cover(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    service.enqueue_missing()
    _lease, context = claim(sessions)
    service.apply_worker(
        ref_id,
        1,
        context,
        fields={"album": "Different", "release_date": "1990"},
        evidence=FieldEvidence("EMBEDDED", "bytes", datetime.now(UTC).isoformat()),
    )
    chosen = candidate()
    proof = FieldEvidence("MUSICBRAINZ", chosen.source_id, datetime.now(UTC).isoformat())
    group = album_group_for(chosen, proof)
    assert group is not None
    service.apply_worker(
        ref_id,
        1,
        context,
        fields=chosen.fields,
        evidence=proof,
        artwork=b"wrong-art",
        album_group=album_group_document(group),
        state="READY",
    )
    view = service.get(principal, ref_id)
    assert view["fields"]["album"] == "Different"
    assert view["album_group_v1"] is None and view["artwork_sha256"] is None
    engine.dispose()


def test_old_terminal_vault_metadata_backfills_once(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    variant = publish_audio(sessions, principal, ref_id)
    service.enqueue_missing()
    lease, context = claim(sessions)
    service.apply_worker(ref_id, 1, context, state="NOT_FOUND", audio_variant_id=variant)
    with sessions.begin() as session:
        PostgresJobRepository(session).complete(lease.fence)
        row = session.get(TrackMetadataRow, ref_id)
        assert row is not None
        row.document = {k: v for k, v in row.document.items() if k != "enrichment_version"}
    assert service.enqueue_missing() == 1
    second, second_context = claim(sessions)
    service.apply_worker(ref_id, 2, second_context, state="FAILED")
    with sessions.begin() as session:
        PostgresJobRepository(session).complete(second.fence)
    assert service.enqueue_missing() == 0
    engine.dispose()


def test_changed_native_snapshot_replaces_its_old_group_without_media_mutation(
    database_url: str,
) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    variant = publish_audio(sessions, principal, ref_id)
    service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence=native())
    lease, context = claim(sessions)
    run(service, lease, context, Provider())
    with sessions.begin() as session:
        PostgresJobRepository(session).complete(lease.fence)
    service.accept_acquisition(
        principal, ref_id, operation_id=uuid4(), evidence=native("Correct", "43")
    )
    assert service.get(principal, ref_id)["album_group_v1"] is None
    second, second_context = claim(sessions)
    run(service, second, second_context, Provider())
    result = service.get(principal, ref_id)
    assert result["fields"]["album"] == "Correct"
    assert result["album_group_v1"]["key"] == "native:jamendo:album:43"
    with sessions() as session:
        assert (
            session.scalar(text("SELECT audio_variant_id FROM vault.recording_canonical_variant"))
            == variant
        )
    engine.dispose()


def test_lower_trust_handoff_and_refresh_preserve_selected_group_and_cover(
    database_url: str,
) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    publish_audio(sessions, principal, ref_id)
    service.enqueue_missing()
    _lease, context = claim(sessions)
    chosen = candidate()
    service.apply_worker(ref_id, 1, context, state="REVIEW", candidates=[asdict(chosen)])
    view = service.get(principal, ref_id)
    service.command(
        principal,
        ref_id,
        operation_id=uuid4(),
        expected_revision=view["revision"],
        action="SELECT",
        candidate_id=chosen.candidate_id,
    )
    second, second_context = claim(sessions)
    run(service, second, second_context, Provider(chosen))
    selected = service.get(principal, ref_id)
    with sessions.begin() as session:
        PostgresJobRepository(session).complete(second.fence)
    accepted = service.accept_acquisition(
        principal, ref_id, operation_id=uuid4(), evidence=native("Other", "77")
    )
    assert accepted["album_group_v1"] == selected["album_group_v1"]
    assert accepted["artwork_sha256"] == selected["artwork_sha256"]
    third, third_context = claim(sessions)
    run(service, third, third_context, Provider(chosen))
    result = service.get(principal, ref_id)
    assert result["fields"]["album"] == "Album"
    assert result["album_group_v1"]["key"] == selected["album_group_v1"]["key"]
    assert result["artwork_sha256"] == selected["artwork_sha256"]
    refreshed = service.command(
        principal,
        ref_id,
        operation_id=uuid4(),
        expected_revision=result["revision"],
        action="REFRESH",
    )
    assert refreshed["album_group_v1"]["key"] == selected["album_group_v1"]["key"]
    engine.dispose()


def test_native_album_without_artist_keeps_truthful_unknown_credit(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    publish_audio(sessions, principal, ref_id)
    evidence = native()
    fields = evidence["fields"]
    assert isinstance(fields, dict)
    del fields["album_artist"]
    service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence=evidence)
    lease, context = claim(sessions)
    run(service, lease, context, Provider())
    assert service.get(principal, ref_id)["album_group_v1"]["album_artist"] is None
    engine.dispose()


def test_worker_late_provider_error_cannot_clear_new_acquisition(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    publish_audio(sessions, principal, ref_id)
    service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence=native())
    lease, context = claim(sessions)

    class ChangedProvider(Provider):
        def search(self, query: MetadataQuery) -> tuple[MetadataCandidate, ...]:
            service.accept_acquisition(
                principal, ref_id, operation_id=uuid4(), evidence=native("New", "43")
            )
            raise MetadataProviderError("old_failure", retryable=False)

    with pytest.raises(RetryableJobError, match="metadata_evidence_changed"):
        run(service, lease, context, ChangedProvider())
    view = service.get(principal, ref_id)
    assert view["error_code"] is None and view["album_group_v1"] is None
    with sessions() as session:
        row = session.get(TrackMetadataRow, ref_id)
        assert row is not None and row.document["acquisition_refresh_pending"]
        assert row.generation == 1
    engine.dispose()


def test_worker_uses_measured_audio_duration_and_preserves_embedded_priority(
    database_url: str,
) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    variant = publish_audio(sessions, principal, ref_id)
    service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence=native())
    lease, context = claim(sessions)

    class EmbeddedWork(Work):
        def read_audio(self) -> EmbeddedMetadata:
            return EmbeddedMetadata({"album": "Embedded", "release_date": "1990"}, None, 180000)

    provider = Provider()
    audio = MetadataAudioTarget(
        uuid4(),
        variant,
        uuid4(),
        OpaqueStorageKey("synthetic"),
        VerifiedStagedFile(1024, Sha256Digest(b"x" * 32)),
    )
    TrackMetadataHandler(
        service, EmbeddedWork(), provider, audio=audio, freeze_renewals=lambda: None
    )(context, lease)
    assert provider.query is not None and provider.query.duration_ms == 180000
    assert provider.query.album == "Embedded" and provider.query.release_date == "1990"
    view = service.get(principal, ref_id)
    assert view["fields"]["album"] == "Embedded" and view["album_group_v1"] is None
    assert view["provenance"]["album"]["source"] == "EMBEDDED"
    engine.dispose()


def test_optional_art_protocol_failure_retains_confirmed_edition(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    with sessions.begin() as session:
        session.execute(text("UPDATE library.user_track_ref SET raw_duration_ms=180000"))
    service.enqueue_missing()
    lease, context = claim(sessions)

    class BrokenWork(Work):
        def normalize_artwork(self, payload: bytes) -> bytes:
            raise ChildProtocolError()

    chosen = candidate()
    with pytest.raises(ChildProtocolError):
        TrackMetadataHandler(
            service, BrokenWork(), Provider(chosen), audio=None, freeze_renewals=lambda: None
        )(context, lease)
    view = service.get(principal, ref_id)
    assert view["state"] == "QUEUED"
    assert view["album_group_v1"]["key"] == chosen.source_id
    assert view["fields"]["release_date"] == "2001" and view["artwork_sha256"] is None
    with sessions.begin() as session:
        PostgresJobRepository(session).fail_terminal(
            lease.fence, JobError("metadata_protocol_failure", {})
        )
    service.reconcile_finished(principal.user_id)
    failed = service.get(principal, ref_id)
    assert failed["state"] == "FAILED" and failed["album_group_v1"] is not None
    engine.dispose()


def test_native_edition_change_drops_stale_public_date_positions_and_recording(
    database_url: str,
) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    publish_audio(sessions, principal, ref_id)
    service.enqueue_missing()
    lease, context = claim(sessions)
    old = candidate()
    service.apply_worker(
        ref_id,
        1,
        context,
        fields={**old.fields, "mb_recording_id": str(uuid4())},
        evidence=FieldEvidence("MUSICBRAINZ", old.source_id, datetime.now(UTC).isoformat()),
        state="READY",
    )
    with sessions.begin() as session:
        PostgresJobRepository(session).complete(lease.fence)
    evidence = native("New Album", "99")
    fields = evidence["fields"]
    assert isinstance(fields, dict)
    fields["title"] = "Corrected Title"
    del fields["release_date"]
    service.accept_acquisition(principal, ref_id, operation_id=uuid4(), evidence=evidence)
    second, second_context = claim(sessions)
    provider = Provider()
    run(service, second, second_context, provider)
    view = service.get(principal, ref_id)
    assert view["album_group_v1"]["key"] == "native:jamendo:album:99"
    assert view["album_group_v1"]["release_date"] is None
    assert view["album_group_v1"]["track_number"] is None
    assert view["album_group_v1"]["disc_number"] is None
    assert "release_date" not in view["fields"] and "mb_recording_id" not in view["fields"]
    assert provider.query is not None and provider.query.release_date is None
    assert provider.query.track_number is None and provider.query.disc_number is None
    assert provider.query.recording_mbid is None
    engine.dispose()


def test_compatible_embedded_year_retains_precision_in_confirmed_group(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    service.enqueue_missing()
    _lease, context = claim(sessions)
    service.apply_worker(
        ref_id,
        1,
        context,
        fields={"release_date": "2001"},
        evidence=FieldEvidence("EMBEDDED", "bytes", datetime.now(UTC).isoformat()),
    )
    chosen = candidate()
    chosen.fields["release_date"] = "2001-04-21"
    proof = FieldEvidence("MUSICBRAINZ", chosen.source_id, datetime.now(UTC).isoformat())
    group = album_group_for(chosen, proof)
    assert group is not None
    service.apply_worker(
        ref_id,
        1,
        context,
        fields=chosen.fields,
        evidence=proof,
        album_group=album_group_document(group),
        state="READY",
    )
    view = service.get(principal, ref_id)
    assert view["fields"]["release_date"] == "2001"
    assert view["album_group_v1"]["release_date"] == "2001"
    engine.dispose()


@pytest.mark.parametrize("old_cover", [None, "automatic", "selected"])
def test_exited_app_handoff_stages_until_real_ingest_then_sweep_groups(
    admission: AdmissionHarness,
    tmp_path: Path,
    old_cover: str | None,
) -> None:
    repository, owned, target, verified = ready(admission, tmp_path)
    evidence = native()
    evidence["provider"], evidence["source_id"] = "YOUTUBE", target.candidate_id
    service = TrackMetadataService(admission.sessions)
    previous_group = None
    previous_sha = hashlib.sha256(b"old-cover").hexdigest()
    if old_cover is not None:
        old = candidate()
        old.fields["album"] = "Previous edition"
        proof = FieldEvidence(
            "MUSICBRAINZ",
            old.source_id,
            datetime.now(UTC).isoformat(),
            locked=old_cover == "selected",
        )
        group = album_group_for(old, proof)
        assert group is not None
        previous_group = album_group_document(group)
        with admission.sessions.begin() as session:
            session.add(MetadataArtworkRow(sha256=previous_sha, content=b"old-cover"))
            session.flush()
            session.add(
                TrackMetadataRow(
                    user_track_ref_id=target.ref_id,
                    revision=1,
                    generation=1,
                    state="READY",
                    candidates=[],
                    artwork_sha256=previous_sha,
                    updated_at=datetime.now(UTC),
                    document={
                        "fields": old.fields,
                        "provenance": {key: asdict(proof) for key in old.fields},
                        "artwork_source": asdict(proof),
                        "album_group_v1": previous_group,
                        "selection": asdict(old) if old_cover == "selected" else None,
                    },
                )
            )
    receipt = repository.handoff(
        owned.claim, target, owned.ticket.execution_id, verified, source_metadata=evidence
    )
    with admission.sessions() as session:
        row = session.get(TrackMetadataRow, target.ref_id)
        assert row is not None and row.job_id is None
        if old_cover is None:
            assert "fields" not in row.document and "album_group_v1" not in row.document
        else:
            assert row.document["fields"]["album"] == "Previous edition"
        assert row.document["pending_acquisition_evidence"]["fields"]["genres"] == ["Rock"]
        assert (
            session.scalar(select(JobRow.job_id).where(JobRow.job_type == "music.metadata.enrich"))
            is None
        )
    assert service.enqueue_missing(target.user_id) == 0
    admission.service.release(owned.claim, owned.ticket.permit.fence)
    with admission.sessions.begin() as session:
        jobs = PostgresJobRepository(session)
        jobs.complete(owned.claim.fence)
        ingest_lease = jobs.claim(
            worker_id="metadata-ingest-proof",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
        assert ingest_lease.fence.job_id == receipt.ingest_job_id
    ingest_context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(admission.sessions),
        fence=ingest_lease.fence,
        lease_interval=timedelta(minutes=2),
    )
    VaultIngestHandler(
        repository=TransactionalIngestRepository(
            SqlAlchemyVaultUnitOfWorkFactory(admission.sessions)
        ),
        storage=FilesystemVaultStorage(tmp_path),
        media=Media(),
        fingerprints=Fingerprints(),
    )(ingest_context, ingest_lease)
    assert service.enqueue_missing(target.user_id) == 1
    lease, context = claim(admission.sessions)
    run(service, lease, context, Provider())
    principal = admission.actor(target.user_id)
    result = service.get(principal, target.ref_id)
    if old_cover == "selected":
        assert previous_group is not None
        assert result["album_group_v1"]["key"] == previous_group["key"]
        assert result["artwork_sha256"] == previous_sha
    else:
        assert result["album_group_v1"]["key"] == "native:youtube:album:42"
        assert result["artwork_sha256"] is None
    assert result["fields"]["artist"] == "Artist" and result["fields"]["genres"] == ["Rock"]
    with admission.sessions() as session:
        row = session.get(TrackMetadataRow, target.ref_id)
        assert row is not None and "pending_acquisition_evidence" not in row.document


def test_staged_producer_fence_replay_and_rollback(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    _repository, owned, target, _verified = ready(admission, tmp_path)
    with pytest.raises(RuntimeError, match="rollback"), admission.sessions.begin() as session:
        TrackMetadataService.stage_acquisition_in_transaction(
            session,
            claim=owned.claim,
            target=target,
            execution_id=owned.ticket.execution_id,
            evidence=native(),
        )
        raise RuntimeError("rollback")
    with admission.sessions() as session:
        assert session.get(TrackMetadataRow, target.ref_id) is None
    for _ in range(2):
        with admission.sessions.begin() as session:
            TrackMetadataService.stage_acquisition_in_transaction(
                session,
                claim=owned.claim,
                target=target,
                execution_id=owned.ticket.execution_id,
                evidence=native(),
            )
    with (
        pytest.raises(MusicError, match="metadata_operation_conflict"),
        admission.sessions.begin() as session,
    ):
        TrackMetadataService.stage_acquisition_in_transaction(
            session,
            claim=owned.claim,
            target=target,
            execution_id=owned.ticket.execution_id,
            evidence=native("Other"),
        )
    stale = replace(owned.claim, fence=replace(owned.claim.fence, worker_id="stale"))
    with pytest.raises(JobLeaseLost), admission.sessions.begin() as session:
        TrackMetadataService.stage_acquisition_in_transaction(
            session,
            claim=stale,
            target=target,
            execution_id=owned.ticket.execution_id,
            evidence=native(),
        )


def test_actual_app_missing_credit_does_not_use_raw_uploader_for_lookup(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    service.enqueue_missing()
    with sessions.begin() as session:
        row = session.get(TrackMetadataRow, ref_id)
        assert row is not None
        row.document = {**row.document, "source_artist_explicit": False}
    lease, context = claim(sessions)
    provider = Provider(candidate())
    service.apply_worker(
        ref_id,
        1,
        context,
        fields={"title": "Original", "artist": "Artist"},
        evidence=FieldEvidence("MUSICBRAINZ", "old-public", datetime.now(UTC).isoformat()),
    )
    run(service, lease, context, provider)
    assert provider.query is not None and provider.query.artist == ""
    assert service.get(principal, ref_id)["album_group_v1"] is None
    engine.dispose()


class ContextMedia(Media):
    def inspect(self, path: Path) -> AudioTechnicalMetadata:
        return replace(super().inspect(path), duration_ms=180000)


class ContextFingerprints(Fingerprints):
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    def fingerprint(self, path: Path) -> ChromaprintEvidence:
        evidence = super().fingerprint(path)
        return replace(
            evidence,
            duration_ms=180000,
            payload=evidence.payload + b":" + self.source_id.encode("ascii"),
        )


def publish_context_acquisition(
    harness: AdmissionHarness,
    tmp_path: Path,
    card: CatalogueTrackCard,
    evidence: dict[str, object],
    *,
    actor: Principal | None = None,
    candidate_id: str = "candidate00",
) -> tuple[Principal, UUID, TrackMetadataService, MetadataAudioTarget]:
    """Original contextual search, exited handoff and real transactional Vault publication."""
    harness.budget()
    actor = actor or harness.actor()
    with harness.sessions.begin() as session:
        context = PostgresCatalogueContextRepository(session).create(
            actor.user_id, uuid4(), card, datetime.now(UTC)
        )
    music = InternetMusicService(harness.sessions, object(), SearchProvider())
    search = music.search(actor, "Original Artist", uuid4(), context.context_id)
    acquisition = music.select(actor, UUID(search["search_id"]), candidate_id)
    with harness.sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="context-source-proof",
            supported=(INTERNET_ACQUIRE_JOB,),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
    claim = AcquisitionClaim(
        lease.fence, "INTERNET_ACQUISITION", UUID(acquisition["acquisition_id"])
    )
    active = harness.service.acquire_worker(claim)
    permit = harness.service.open_io(claim, fence(active), claim.acquisition_id)
    ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, claim.acquisition_id)
    harness.service.prepare_execution(claim, ticket)
    child = ProcessIdentity(34567, b"c" * 32)
    harness.service.start_execution(claim, ticket, child)
    repository = PostgresInternetAcquisitionRepository(harness.sessions)
    target = repository.prepare(claim)
    assert isinstance(target, InternetAcquisitionTarget)
    storage = FilesystemVaultStorage(tmp_path)
    key = OpaqueStorageKey(f"provider-{ticket.execution_id.hex}")
    storage.create_staging(key)
    payload = PAYLOAD + candidate_id.encode("ascii")
    storage.write_chunk(
        key,
        offset=0,
        payload=payload,
        payload_sha256=Sha256Digest(hashlib.sha256(payload).digest()),
    )
    verified = storage.verify_staging(key)
    harness.service.confirm_execution_exit(
        ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, child)
    )
    harness.service.close_io(permit)
    repository.handoff(
        claim,
        target,
        ticket.execution_id,
        verified,
        source_metadata={**evidence, "provider": "YOUTUBE", "source_id": target.candidate_id},
    )
    harness.service.release(claim, permit.fence)
    with harness.sessions.begin() as session:
        jobs = PostgresJobRepository(session)
        jobs.complete(claim.fence)
        ingest = jobs.claim(
            worker_id="context-ingest-proof",
            supported=(JobKey("vault.ingest", 1),),
            lease_interval=timedelta(minutes=2),
            limit=1,
        )[0]
    ingest_context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(harness.sessions),
        fence=ingest.fence,
        lease_interval=timedelta(minutes=2),
    )
    VaultIngestHandler(
        repository=TransactionalIngestRepository(
            SqlAlchemyVaultUnitOfWorkFactory(harness.sessions)
        ),
        storage=storage,
        media=ContextMedia(),
        fingerprints=ContextFingerprints(candidate_id),
    )(ingest_context, ingest)
    service = TrackMetadataService(harness.sessions)
    assert service.enqueue_missing(actor.user_id) == 1
    with harness.sessions() as session:
        ref = session.get(UserTrackRefRow, target.ref_id)
        assert ref is not None
        audio = metadata_audio(session, ref)
        assert audio is not None
    return actor, target.ref_id, service, audio


class ContextWork(Work):
    def __init__(self, duration_ms: int | None = 180000) -> None:
        self.duration_ms = duration_ms

    def read_audio(self) -> EmbeddedMetadata:
        return EmbeddedMetadata({}, duration_ms=self.duration_ms)


class ContextProvider(Provider):
    def __init__(self, card: CatalogueTrackCard) -> None:
        super().__init__()
        fields = {
            **candidate().fields,
            "mb_recording_id": str(card.recording_mbid),
            "mb_release_id": str(card.release_mbid),
        }
        self.chosen = replace(
            candidate(),
            candidate_id="selected-occurrence",
            fields=fields,
            source_id=f"musicbrainz:release:{card.release_mbid}",
            mb_release_track_id=str(card.entity_id),
        )
        other_release = str(uuid4())
        self.options = (
            replace(
                self.chosen,
                candidate_id="other-edition",
                fields={**fields, "mb_release_id": other_release},
                source_id=f"musicbrainz:release:{other_release}",
                mb_release_track_id=str(uuid4()),
            ),
            replace(
                self.chosen,
                candidate_id="other-occurrence",
                fields={**fields, "track_number": 8},
                mb_release_track_id=str(uuid4()),
            ),
            self.chosen,
        )

    def search(self, query: MetadataQuery) -> tuple[MetadataCandidate, ...]:
        self.query = query
        return self.options


def context_card() -> CatalogueTrackCard:
    return CatalogueTrackCard(
        CatalogueContextEntity.RELEASE_TRACK,
        uuid4(),
        uuid4(),
        "Original",
        "Artist",
        uuid4(),
        "Album",
        "2001",
        180000,
        1,
        2,
    )


def test_original_catalogue_context_bounds_exact_edition_and_occurrence_after_publication(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    card = context_card()
    actor, ref_id, service, audio = publish_context_acquisition(admission, tmp_path, card, native())
    lease, job_context = claim(admission.sessions)
    provider = ContextProvider(card)
    with admission.sessions() as session:
        ref = session.get(UserTrackRefRow, ref_id)
        row = session.get(TrackMetadataRow, ref_id)
        assert ref is not None and row is not None
        assert service.catalogue_context(session, ref, row, audio_variant_id=uuid4()) is None
        assert service.catalogue_context(session, ref, row, audio_variant_id=None) is None
        lookup = service.catalogue_context(
            session, ref, row, audio_variant_id=audio.audio_variant_id
        )
        assert lookup is not None and lookup.card == card
        recording_id = ref.recording_id
    TrackMetadataHandler(
        service, ContextWork(), provider, audio=audio, freeze_renewals=lambda: None
    )(job_context, lease)
    assert provider.query is not None
    assert provider.query.recording_mbid == str(card.recording_mbid)
    assert provider.query.release_mbid == str(card.release_mbid)
    assert provider.query.release_track_mbid == str(card.entity_id)
    result = service.get(actor, ref_id)
    assert result["state"] == "READY"
    assert result["album_group_v1"]["key"] == f"musicbrainz:release:{card.release_mbid}"
    assert result["album_group_v1"]["track_number"] == 2
    with admission.sessions() as session:
        ref = session.get(UserTrackRefRow, ref_id)
        row = session.get(TrackMetadataRow, ref_id)
        assert ref is not None and ref.recording_id == recording_id == audio.recording_id
        assert row is not None and row.document.get("selection") is None
        assert row.document["acquisition_evidence"]["fields"] == native()["fields"]


@pytest.mark.parametrize(
    "conflict",
    ["unproved_credit", "missing_measured", "duration", "version", "album", "date", "position"],
)
def test_catalogue_context_cannot_supply_missing_or_conflicting_source_facts(
    admission: AdmissionHarness, tmp_path: Path, conflict: str
) -> None:
    evidence = native()
    assert isinstance(evidence["fields"], dict)
    fields = dict(evidence["fields"])
    duration_ms: int | None = 180000
    if conflict == "unproved_credit":
        fields.pop("artist")
    elif conflict == "missing_measured":
        duration_ms = None
    elif conflict == "duration":
        duration_ms = 190000
    elif conflict == "version":
        fields["title"] = "Original (live)"
    elif conflict == "album":
        fields["album"] = "Different Album"
    elif conflict == "date":
        fields["release_date"] = "2002"
    elif conflict == "position":
        fields["track_number"] = 9
    evidence["fields"] = fields
    card = context_card()
    actor, ref_id, service, audio = publish_context_acquisition(admission, tmp_path, card, evidence)
    # Raw approximate duration and stale PUBLIC artist are deliberately plausible.
    with admission.sessions.begin() as session:
        ref = session.get(UserTrackRefRow, ref_id)
        row = session.get(TrackMetadataRow, ref_id)
        assert ref is not None and row is not None
        ref.raw_duration_ms = 180000
        if conflict == "unproved_credit":
            ref.raw_artist = "Artist"
            proof = FieldEvidence("MUSICBRAINZ", "old-public", datetime.now(UTC).isoformat())
            row.document = {
                **row.document,
                "fields": {"artist": "Artist"},
                "provenance": {"artist": asdict(proof)},
            }
    lease, job_context = claim(admission.sessions)
    provider = ContextProvider(card)
    TrackMetadataHandler(
        service, ContextWork(duration_ms), provider, audio=audio, freeze_renewals=lambda: None
    )(job_context, lease)
    assert provider.query is not None
    assert provider.query.recording_mbid is None
    assert provider.query.release_mbid is None
    assert provider.query.release_track_mbid is None
    if conflict == "unproved_credit":
        assert provider.query.artist == ""
    result = service.get(actor, ref_id)
    assert result["state"] == "REVIEW"
    assert result["album_group_v1"]["key"] == "native:youtube:album:42"
    assert "mb_release_id" not in result["fields"]
    assert result["fields"]["title"] == fields["title"]


def test_two_closed_sources_publish_one_native_album_with_independent_embedded_credit(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    class EmbeddedAlbumWork(ContextWork):
        def read_audio(self) -> EmbeddedMetadata:
            return EmbeddedMetadata(
                {"album": "Album", "album_artist": "Known Artist"}, duration_ms=180000
            )

    evidence = native("ALBUM")
    assert isinstance(evidence["fields"], dict)
    del evidence["fields"]["album_artist"]
    actor = admission.actor()
    refs = []
    for candidate_id in ("candidate00", "candidate01"):
        _, ref_id, service, audio = publish_context_acquisition(
            admission, tmp_path, context_card(), evidence, actor=actor, candidate_id=candidate_id
        )
        refs.append(ref_id)
        lease, job_context = claim(admission.sessions)
        TrackMetadataHandler(
            service, EmbeddedAlbumWork(), Provider(), audio=audio, freeze_renewals=lambda: None
        )(job_context, lease)
        with admission.sessions.begin() as session:
            PostgresJobRepository(session).complete(lease.fence)
        view = service.get(actor, ref_id)
        assert view["state"] == "NOT_FOUND"
        assert view["fields"]["album"] == "Album"
        assert view["fields"]["album_artist"] == "Known Artist"
        group = view["album_group_v1"]
        assert group["title"] == "ALBUM" and group["album_artist"] is None
        assert group["key"] == "native:youtube:album:42"
        with admission.sessions() as session:
            event = session.scalar(
                select(SyncEventRow)
                .where(SyncEventRow.aggregate_id == ref_id)
                .order_by(SyncEventRow.server_row_version.desc())
            )
            assert event is not None and isinstance(event.payload, dict)
            assert event.payload["metadata_v1"] == view
    assert len(set(refs)) == 2
    with admission.sessions() as session:
        projected = [
            item[3]["metadata_v1"]["album_group_v1"]
            for item in _bootstrap_projections(session, actor.user_id)
            if item[0] == "USER_TRACK_REF"
        ]
        assert len(projected) == 2
        assert {group["key"] for group in projected} == {"native:youtube:album:42"}
        assert all(group["album_artist"] is None for group in projected)
