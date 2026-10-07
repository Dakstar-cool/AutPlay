"""Real PostgreSQL owner receipts, search replay and delayed lookup boundaries."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.internet_music import InternetMusicProvider
from autplay.adapters.postgresql.catalogue_context import PostgresCatalogueContextRepository
from autplay.adapters.postgresql.models import (
    AudioVariantRow,
    ListeningEventRow,
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
        conn.commit()
    with pytest.raises(DBAPIError, match="refusing to discard catalogue context"):
        database_harness.downgrade(empty_database_name, "0064_metadata_catalog_gate")
    with database_harness.connect(empty_database_name) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0065_internet_catalogue_context",
        )
        assert conn.execute("SELECT * FROM discovery.internet_search").fetchall() == before
        assert conn.execute(
            "SELECT count(*) FROM discovery.internet_search_context"
        ).fetchone() == (1,)
