"""Real PostgreSQL owner receipts, search replay and delayed lookup boundaries."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from autplay.adapters.internet_music import InternetMusicProvider
from autplay.adapters.postgresql.catalogue_context import PostgresCatalogueContextRepository
from autplay.adapters.postgresql.models import (
    AudioVariantRow,
    ListeningEventRow,
    RecordingCanonicalVariantRow,
    RecordingRow,
    UserSessionRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.catalogue_context import (
    InternetCatalogueContextRow,
    InternetSearchContextRow,
)
from autplay.adapters.postgresql.models.internet_music import (
    InternetAcquisitionRow,
    InternetSearchRow,
)
from autplay.application.catalogue_context import CatalogueContextService
from autplay.application.internet_music import InternetMusicService
from autplay.application.music_library import MusicError
from autplay.domain.auth import Principal
from autplay.domain.catalogue_context import CatalogueContextEntity, CatalogueTrackCard
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from .conftest import DatabaseHarness
from .test_account_deletion import PairingHarness
from .test_account_deletion import base_pair as base_pair
from .test_account_deletion import pair as pair
from .test_acquisition_authority import Provider
from .test_music_library import owner
from .test_privacy_purge import overdue
from .test_resource_admission_runtime import AdmissionHarness, present
from .test_resource_admission_runtime import admission as admission


def card() -> CatalogueTrackCard:
    return CatalogueTrackCard(
        CatalogueContextEntity.RELEASE_TRACK,
        uuid4(),
        uuid4(),
        "Song",
        "Artist",
        uuid4(),
        "Edition",
        "2001",
        180000,
        1,
        2,
        "Song",
    )


def context(admission: AdmissionHarness, actor: Principal, *, expired: bool = False) -> UUID:
    now = datetime.now(UTC) - (timedelta(days=2) if expired else timedelta())
    identity = uuid4()
    with admission.sessions.begin() as session:
        PostgresCatalogueContextRepository(session).create(actor.user_id, identity, card(), now)
    return identity


def test_private_catalogue_handle_without_audio_is_idempotent(admission: AdmissionHarness) -> None:
    actor = admission.actor()
    other = admission.actor()
    receipt = context(admission, actor)
    service = CatalogueContextService(
        admission.sessions, lambda _: pytest.fail("Unexpected catalogue HTTP request")
    )
    first = service.track_reference(actor, receipt)
    assert first == service.track_reference(actor, receipt)
    assert first["recording_id"] is None and first["title"] == "Song"
    with pytest.raises(MusicError, match="music_catalogue_context_not_found"):
        service.track_reference(other, receipt)
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(InternetAcquisitionRow)) == 0
        assert (
            session.get_one(UserTrackRefRow, UUID(str(first["user_track_ref_id"]))).user_id
            == actor.user_id
        )


@pytest.mark.parametrize("explicit_download", [False, True])
def test_saved_catalogue_target_changes_with_owner_audio_and_stays_private(
    admission: AdmissionHarness,
    explicit_download: bool,
) -> None:
    from autplay.adapters.postgresql.catalog_pages import CatalogPageCache
    from autplay.adapters.postgresql.confirmed_edition import musicbrainz_reference
    from autplay.adapters.postgresql.shared_catalog import (
        playlist_catalog_handles,
        refresh_playlist_catalog_handles,
    )
    from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
    from autplay.application.vault_uploads import VaultNotFoundError

    actor, other = admission.actor(), admission.actor()
    selection = card()
    receipts = []
    private_refs = []
    service = CatalogueContextService(
        admission.sessions, lambda _: pytest.fail("Unexpected catalogue HTTP request")
    )
    for principal in (actor, other):
        receipt = uuid4()
        receipts.append(receipt)
        with admission.sessions.begin() as session:
            PostgresCatalogueContextRepository(session).create(
                principal.user_id, receipt, selection, datetime.now(UTC)
            )
        ref = service.track_reference(principal, receipt)
        assert ref["resolution_status"] == "CATALOG_ONLY"
        private_refs.append(UUID(str(ref["user_track_ref_id"])))
    variant_id = admission.variant(actor)
    with admission.sessions.begin() as session:
        variant = session.get_one(AudioVariantRow, variant_id)
        variant.validation_status = "VALID"
        session.add(
            RecordingCanonicalVariantRow(
                recording_id=variant.recording_id,
                audio_variant_id=variant_id,
                policy_version="owner-verified-upload-v1",
                reason={"test": "catalog"},
            )
        )
        if not explicit_download:
            musicbrainz_reference(
                session, "RECORDING", str(selection.recording_mbid)
            ).recording_id = variant.recording_id
    if explicit_download:
        from autplay.adapters.postgresql.models import CatalogDownloadRow

        sources = InternetMusicService(admission.sessions, object(), Provider())
        lookup = sources.search(actor, "Artist Song", uuid4(), receipts[0])
        selected = sources.select(actor, UUID(lookup["search_id"]), "candidate00")
        acq_id = UUID(selected["acquisition_id"])
        with admission.sessions.begin() as session:
            acquired = session.get_one(InternetAcquisitionRow, acq_id)
            variant = session.get_one(AudioVariantRow, variant_id)
            acquired.user_track_ref_id = session.scalars(
                select(UserTrackRefRow.user_track_ref_id).where(
                    UserTrackRefRow.user_id == actor.user_id,
                    UserTrackRefRow.recording_id == variant.recording_id,
                )
            ).one()
            acquired.audio_variant_id = variant_id
            acquired.state = "READY"
        mbid = str(selection.recording_mbid)
        assert not CatalogPageCache(admission.sessions).available_recordings(actor.user_id, [mbid])
        with admission.sessions.begin() as session:
            session.add(
                CatalogDownloadRow(
                    operation_id=uuid4(),
                    user_id=actor.user_id,
                    document={"recordings": {mbid: {"acquisition": str(acq_id)}}},
                )
            )
    with admission.sessions.begin() as session:
        refresh_playlist_catalog_handles(session, actor.user_id)
        refresh_playlist_catalog_handles(session, actor.user_id)
        assert (
            playlist_catalog_handles(session, actor.user_id)[private_refs[0]]["resolution_status"]
            == "RESOLVED"
        )
        assert (
            playlist_catalog_handles(session, other.user_id)[private_refs[1]]["resolution_status"]
            == "CATALOG_ONLY"
        )
        vault = PostgresVaultRuntime(session)
        assert (
            vault.resolve_owner_playback_variant(
                actor.user_id, private_refs[0], include_catalog=True
            )
            == variant_id
        )
        with pytest.raises(VaultNotFoundError):
            vault.resolve_owner_playback_variant(
                other.user_id, private_refs[1], include_catalog=True
            )
    pages = CatalogPageCache(admission.sessions)
    assert pages.available_recordings(actor.user_id, [str(selection.recording_mbid)])
    assert not pages.available_recordings(other.user_id, [str(selection.recording_mbid)])
    with admission.sessions.begin() as session:
        session.get_one(AudioVariantRow, variant_id).deleted_at = datetime.now(UTC)
    with admission.sessions.begin() as session:
        refresh_playlist_catalog_handles(session, actor.user_id)
        assert (
            playlist_catalog_handles(session, actor.user_id)[private_refs[0]]["resolution_status"]
            == "CATALOG_ONLY"
        )


def test_artist_download_previews_full_editions_and_guest_tracks_before_idempotent_start(
    admission: AdmissionHarness,
) -> None:
    from autplay.application.catalog_downloads import CatalogDownloads
    from autplay.application.music_discovery import MusicDiscoveryService

    actor = admission.actor()
    other = admission.actor()
    artist, shared, guest, edition, deluxe, group = (uuid4() for _ in range(6))
    missing = [uuid4() for _ in range(15)]
    release_calls = []

    def page(items: list[dict[str, object]], next_offset: int | None = None) -> dict[str, object]:
        return {"items": items, "next_offset": next_offset, "truncated": False}

    def track(identity: UUID, name: str, duration: int | None = None) -> dict[str, object]:
        return {
            "recording_id": str(identity),
            "artist": "Artist",
            "title": name,
            "duration_ms": duration,
            "download_search_query": f"Artist {name}",
        }

    class Discography:
        def artist_releases(
            self, owner: Principal, identity: UUID, *, limit: int, offset: int
        ) -> dict[str, object]:
            assert owner == actor and identity == artist
            if offset == 0:
                return page(
                    [
                        {
                            "entity_id": str(deluxe),
                            "release_group_id": str(group),
                            "release_date": "2023",
                        }
                    ],
                    1,
                )
            return page(
                [
                    {
                        "entity_id": str(edition),
                        "release_group_id": str(group),
                        "release_date": "2020",
                    }
                ]
            )

        def artist_tracks(
            self, owner: Principal, identity: UUID, *, limit: int, offset: int
        ) -> dict[str, object]:
            return page(
                [
                    {**track(guest, "Track 1", 150000), "guest_artist_ids": [str(artist)]},
                    track(uuid4(), "Different edition", 180000),
                ]
            )

        def release_tracks(
            self, owner: Principal, identity: UUID, *, limit: int, offset: int
        ) -> dict[str, object]:
            release_calls.append(identity)
            if offset == 0:
                return page(
                    [track(shared, "Track 0", 150000)] + [track(i, "Missing") for i in missing], 16
                )
            return page([track(shared, "Track 0", 150000)])

    def service() -> CatalogDownloads:
        fixture = Discography()
        discovery = Mock(spec_set=MusicDiscoveryService(lambda _: pytest.fail("Unexpected HTTP")))
        discovery.page_cache = None
        discovery.artist_releases.side_effect = fixture.artist_releases
        discovery.artist_tracks.side_effect = fixture.artist_tracks
        discovery.release_tracks.side_effect = fixture.release_tracks
        return CatalogDownloads(
            admission.sessions,
            discovery,
            CatalogueContextService(admission.sessions, lambda _: pytest.fail("Unexpected HTTP")),
            InternetMusicService(admission.sessions, object(), Provider()),
        )

    operation = uuid4()
    service().preview(actor, operation, "artist", artist, False)
    with pytest.raises(MusicError, match="catalog_preview_incomplete"):
        service().command(actor, operation, "start")
    with pytest.raises(MusicError, match="catalog_download_missing"):
        service().command(other, operation, "status")
    for _ in range(30):
        state = service().command(actor, operation, "advance")
        if state["phase"] == "preview":
            break
    assert (
        state["total"] == 17
        and state["counts"]["available"] == 2
        and state["counts"]["missing"] == 15
    )
    assert release_calls == [edition, edition]
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(InternetAcquisitionRow)) == 0
    for _ in range(2):
        assert service().command(actor, operation, "start")["phase"] == "downloading"
    service().command(actor, operation, "advance")
    assert service().command(actor, operation, "advance")["counts"]["queued"] == 2
    repeated = uuid4()
    service().preview(actor, repeated, "release", edition, False)
    for _ in range(28):
        state = service().command(actor, repeated, "advance")
        if state["phase"] == "preview":
            break
    assert state["counts"]["queued"] == 1 and state["counts"]["available"] == 0
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(InternetAcquisitionRow)) == 2


@pytest.mark.parametrize("revoked", [False, True])
def test_receipt_uses_principal_bound_server_hydration_and_current_authority(
    admission: AdmissionHarness,
    revoked: bool,
) -> None:
    actor = admission.actor()
    recording_id = uuid4()

    class Http:
        def json(self, url: str) -> dict[str, Any]:
            assert str(recording_id) in url
            if revoked:
                with admission.sessions.begin() as session:
                    present(
                        session.get(UserSessionRow, actor.session_id)
                    ).revoked_at = datetime.now(UTC)
            return {
                "id": str(recording_id),
                "title": "Server title",
                "length": 180000,
                "artist-credit": [{"name": "Server performer"}],
                "first-release-date": "2001",
            }

    bound: list[Principal] = []

    def factory(principal: Principal) -> nullcontext[Http]:
        bound.append(principal)
        return nullcontext(Http())

    service = CatalogueContextService(admission.sessions, factory)
    if revoked:
        with pytest.raises(MusicError, match="auth_invalid"):
            service.create(actor, CatalogueContextEntity.RECORDING, recording_id)
    else:
        result = service.create(actor, CatalogueContextEntity.RECORDING, recording_id)
        assert result["acquisition_allowed"] is False
        assert str(result["expires_at"]).endswith("+00:00")
        identity = UUID(str(result["catalogue_context_id"]))
        with admission.sessions() as session:
            saved = PostgresCatalogueContextRepository(session).load(
                actor.user_id, identity, admission=True
            )
            assert saved.card.title == "Server title" and saved.card.artist == "Server performer"
            assert saved.card.album is None and saved.card.release_mbid is None
    assert bound == [actor]
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(InternetCatalogueContextRow)) == (
            0 if revoked else 1
        )


class EmptyProvider(InternetMusicProvider):
    def __init__(self, barrier: Barrier | None = None) -> None:
        self.calls = 0
        self.barrier = barrier

    def search(self, query: str) -> list[dict[str, Any]]:
        self.calls += 1
        if self.barrier:
            self.barrier.wait(5)
        return []


def test_empty_snapshot_binds_context_and_absent_null_replay(admission: AdmissionHarness) -> None:
    actor = admission.actor()
    first, other = context(admission, actor), context(admission, actor)
    provider = EmptyProvider()
    service = InternetMusicService(admission.sessions, object(), provider)
    operation = uuid4()
    result = service.search(actor, "  Artist   Song  ", operation, first)
    assert result["candidates"] == []
    assert service.search(actor, "Artist Song", operation, first) == result
    for changed in (other, None):
        with pytest.raises(MusicError, match="music_operation_conflict"):
            service.search(actor, "Artist Song", operation, changed)
    plain = uuid4()
    no_context = service.search(actor, "plain", plain)
    assert service.search(actor, "plain", plain, None) == no_context
    with pytest.raises(MusicError, match="music_operation_conflict"):
        service.search(actor, "plain", plain, first)
    assert provider.calls == 2
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(InternetSearchContextRow)) == 2
        saved = present(session.get(InternetSearchRow, operation))
        assert saved.candidates == [] and saved.snapshot_sha256 == hashlib.sha256(b"[]").digest()


def test_foreign_unknown_and_expired_context_never_call_source(admission: AdmissionHarness) -> None:
    actor, foreign = admission.actor(), admission.actor()
    provider = EmptyProvider()
    service = InternetMusicService(admission.sessions, object(), provider)
    for identity in (context(admission, foreign), uuid4()):
        with pytest.raises(MusicError, match="music_catalogue_context_not_found") as error:
            service.search(actor, "query", uuid4(), identity)
        assert error.value.status_code == 404
    with pytest.raises(MusicError, match="music_catalogue_context_expired"):
        service.search(actor, "query", uuid4(), context(admission, actor, expired=True))
    assert provider.calls == 0


def test_concurrent_empty_results_bind_one_immutable_operation(admission: AdmissionHarness) -> None:
    actor = admission.actor()
    first, other = context(admission, actor), context(admission, actor)
    provider = EmptyProvider(Barrier(2))
    service = InternetMusicService(admission.sessions, object(), provider)
    operation = uuid4()

    def search(identity: UUID) -> object:
        try:
            return service.search(actor, "query", operation, identity)
        except MusicError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(search, (first, other)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("music_operation_conflict") == 1
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(InternetSearchRow)) == 1
        assert session.scalar(select(func.count()).select_from(InternetSearchContextRow)) == 1


def test_legacy_search_cannot_gain_context_on_replay(admission: AdmissionHarness) -> None:
    actor = admission.actor()
    operation = uuid4()
    with admission.sessions.begin() as session:
        session.add(
            InternetSearchRow(
                search_id=operation,
                user_id=actor.user_id,
                query="legacy",
                candidates=[],
                snapshot_sha256=hashlib.sha256(b"[]").digest(),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
    provider = EmptyProvider()
    service = InternetMusicService(admission.sessions, object(), provider)
    assert service.search(actor, "legacy", operation)["candidates"] == []
    with pytest.raises(MusicError, match="music_operation_conflict"):
        service.search(actor, "legacy", operation, context(admission, actor))
    assert provider.calls == 0


def test_delayed_download_reads_original_context_and_reuse_keeps_old_search(
    admission: AdmissionHarness,
) -> None:
    actor, stranger = admission.actor(), admission.actor()
    old_context, new_context = context(admission, actor), context(admission, actor)
    service = InternetMusicService(admission.sessions, object(), Provider())
    old_search, new_search = uuid4(), uuid4()
    service.search(actor, "old", old_search, old_context)
    original = UUID(service.select(actor, old_search, "candidate00")["acquisition_id"])
    service.search(actor, "new", new_search, new_context)
    reused = UUID(service.select(actor, new_search, "candidate00")["acquisition_id"])
    assert reused == original
    with admission.sessions() as session:
        saved = present(session.get(InternetAcquisitionRow, original))
        assert saved.search_id == old_search
        repository = PostgresCatalogueContextRepository(session)
        assert (
            present(repository.for_acquisition(actor.user_id, original)).context_id == old_context
        )
        assert repository.for_acquisition(stranger.user_id, original) is None
        assert saved.selected_snapshot["candidate_id"] == "candidate00"
    # Admission expiration is independent of an already persisted accepted search.
    expired = context(admission, actor, expired=True)
    expired_search = uuid4()
    with admission.sessions.begin() as session:
        session.add(
            InternetSearchRow(
                search_id=expired_search,
                user_id=actor.user_id,
                query="accepted before expiry",
                candidates=[],
                snapshot_sha256=hashlib.sha256(b"[]").digest(),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        session.flush()
        repository = PostgresCatalogueContextRepository(session)
        retained = repository.load(actor.user_id, expired, admission=False)
        repository.bind_search(actor.user_id, expired_search, "accepted before expiry", retained)
    assert (
        service.search(actor, "accepted before expiry", expired_search, expired)["candidates"] == []
    )


def test_ready_reuse_keeps_original_context_and_source_snapshot(
    admission: AdmissionHarness,
) -> None:
    actor = admission.actor()
    first, other = context(admission, actor), context(admission, actor)
    service = InternetMusicService(admission.sessions, object(), Provider())
    original_search, other_search = uuid4(), uuid4()
    service.search(actor, "first", original_search, first)
    acquisition = UUID(service.select(actor, original_search, "candidate00")["acquisition_id"])
    variant_id = admission.variant(actor)
    with admission.sessions.begin() as session:
        variant = present(session.get(AudioVariantRow, variant_id))
        variant.validation_status = "VALID"
        session.add(
            RecordingCanonicalVariantRow(
                recording_id=variant.recording_id,
                audio_variant_id=variant_id,
                policy_version="owner-verified-upload-v1",
                reason={"test": "ready"},
            )
        )
        ref = present(
            session.scalar(
                select(UserTrackRefRow).where(
                    UserTrackRefRow.user_id == actor.user_id,
                    UserTrackRefRow.recording_id == variant.recording_id,
                )
            )
        )
        saved = present(session.get(InternetAcquisitionRow, acquisition))
        saved.state, saved.user_track_ref_id, saved.audio_variant_id = (
            "READY",
            ref.user_track_ref_id,
            variant_id,
        )
        snapshot = dict(saved.selected_snapshot)
        session.add(
            ListeningEventRow(
                user_id=actor.user_id,
                device_id=actor.device_id,
                user_track_ref_id=ref.user_track_ref_id,
                recording_id=variant.recording_id,
                started_at=datetime.now(UTC),
                played_ms=1000,
                track_duration_ms=1000,
            )
        )
        session.flush()
        recording_before = session.execute(select(*RecordingRow.__table__.columns)).all()
        listening_before = session.execute(select(*ListeningEventRow.__table__.columns)).all()
    service.search(actor, "second", other_search, other)
    reused = service.select(actor, other_search, "candidate00")
    assert reused["acquisition_id"] == str(acquisition) and reused["state"] == "READY"
    with admission.sessions() as session:
        saved = present(session.get(InternetAcquisitionRow, acquisition))
        assert saved.search_id == original_search and saved.selected_snapshot == snapshot
        assert (
            present(
                PostgresCatalogueContextRepository(session).for_acquisition(
                    actor.user_id, acquisition
                )
            ).context_id
            == first
        )
        assert session.execute(select(*RecordingRow.__table__.columns)).all() == recording_before
        assert (
            session.execute(select(*ListeningEventRow.__table__.columns)).all() == listening_before
        )


def test_cross_owner_fk_and_immutable_guards(admission: AdmissionHarness) -> None:
    actor, other = admission.actor(), admission.actor()
    identity = context(admission, actor)
    service = InternetMusicService(admission.sessions, object(), EmptyProvider())
    search_id = uuid4()
    service.search(actor, "query", search_id, identity)
    for table, key, value in (
        ("internet_catalogue_context", "context_id", identity),
        ("internet_search_context", "search_id", search_id),
    ):
        for mutation in (
            f"DELETE FROM discovery.{table} WHERE {key}=:id",
            f"UPDATE discovery.{table} SET user_id=:owner WHERE {key}=:id",
        ):
            with (
                pytest.raises(DBAPIError, match="catalogue_context_immutable"),
                admission.engine.begin() as conn,
            ):
                conn.execute(text(mutation), {"id": value, "owner": other.user_id})
    other_search = uuid4()
    with pytest.raises(DBAPIError), admission.sessions.begin() as session:
        session.add(
            InternetSearchRow(
                search_id=other_search,
                user_id=other.user_id,
                query="other",
                candidates=[],
                snapshot_sha256=hashlib.sha256(b"[]").digest(),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        session.flush()
        session.add(
            InternetSearchContextRow(
                search_id=other_search,
                user_id=other.user_id,
                context_id=identity,
                request_sha256=b"h" * 32,
            )
        )
        session.flush()


def test_owner_purge_removes_only_its_context_and_search_bindings(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    other = owner(sessions)
    own_context, other_context = uuid4(), uuid4()
    with sessions.begin() as session:
        repo = PostgresCatalogueContextRepository(session)
        repo.create(pair.actor.user_id, own_context, card(), datetime.now(UTC))
        repo.create(other.user_id, other_context, card(), datetime.now(UTC))
    service = InternetMusicService(sessions, object(), EmptyProvider())
    service.search(pair.actor, "private", uuid4(), own_context)
    service.search(pair.actor, "private second", uuid4(), own_context)
    other_search = uuid4()
    service.search(other, "other private", other_search, other_context)
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as conn:
        conn.execute(
            text("SELECT account.purge_account(:owner,:operation)"),
            {"owner": pair.actor.user_id, "operation": operation},
        )
        assert conn.scalar(text("SELECT count(*) FROM discovery.internet_catalogue_context")) == 1
        assert conn.scalar(text("SELECT count(*) FROM discovery.internet_search_context")) == 1
    with sessions() as session:
        assert session.get(InternetCatalogueContextRow, own_context) is None
        assert (
            present(session.get(InternetCatalogueContextRow, other_context)).user_id
            == other.user_id
        )
        assert (
            present(session.get(InternetSearchContextRow, other_search)).context_id == other_context
        )


def test_migration_preserves_old_snapshot_and_refuses_receipt_loss(
    database_harness: DatabaseHarness,
    empty_database_name: str,
) -> None:
    database_harness.upgrade(empty_database_name, "0064_metadata_catalog_gate")
    owner, search_id = uuid4(), uuid4()
    with database_harness.connect(empty_database_name) as conn:
        conn.execute(
            "INSERT INTO account.user_account(user_id,display_name,role) VALUES(%s,'Old','USER')",
            (owner,),
        )
        conn.execute(
            "INSERT INTO discovery.internet_search "
            "VALUES(%s,%s,'old','[]',%s,now(),now()+interval '24 hours')",
            (search_id, owner, hashlib.sha256(b"[]").digest()),
        )
        before = conn.execute("SELECT * FROM discovery.internet_search").fetchall()
        conn.commit()
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as conn:
        assert conn.execute("SELECT * FROM discovery.internet_search").fetchall() == before
        assert conn.execute(
            "SELECT count(*) FROM discovery.internet_search_context"
        ).fetchone() == (0,)
    database_harness.downgrade(empty_database_name, "0064_metadata_catalog_gate")
    database_harness.upgrade(empty_database_name)
    # Populated receipts must prevent a lossy downgrade; the failed transaction preserves them.
    with database_harness.connect(empty_database_name) as conn:
        conn.execute(
            "INSERT INTO discovery.internet_search_context(search_id,user_id,request_sha256) "
            "VALUES(%s,%s,%s)",
            (search_id, owner, b"r" * 32),
        )
        head_before = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert head_before == ("0078_playlist_snapshots",)
        purge_before = conn.execute(
            "SELECT pg_get_functiondef('account.purge_account(uuid,uuid)'::regprocedure)"
        ).fetchone()
        receipts_before = conn.execute("SELECT * FROM discovery.internet_search_context").fetchall()
        conn.commit()
    with pytest.raises(DBAPIError, match="refusing to discard catalogue context"):
        database_harness.downgrade(empty_database_name, "0064_metadata_catalog_gate")
    with database_harness.connect(empty_database_name) as conn:
        # The 0065 refusal rolls back the preceding empty 0066 downgrade in the same transaction.
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == head_before
        assert (
            conn.execute(
                "SELECT pg_get_functiondef('account.purge_account(uuid,uuid)'::regprocedure)"
            ).fetchone()
            == purge_before
        )
        assert (
            conn.execute("SELECT * FROM discovery.internet_search_context").fetchall()
            == receipts_before
        )
        assert conn.execute("SELECT * FROM discovery.internet_search").fetchall() == before
        assert conn.execute(
            "SELECT count(*) FROM discovery.internet_search_context"
        ).fetchone() == (1,)
