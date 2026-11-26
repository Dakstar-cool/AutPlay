from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.external_collections import CollectionReader
from autplay.adapters.postgresql.models import (
    CollectionImportRow,
    LibraryEntryRow,
    PlaylistEntryRow,
    UserTrackPreferenceRow,
)
from autplay.application.external_collections import ExternalCollections
from autplay.domain.external_collections import CollectionError, CollectionLink
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from .test_sync_runtime import _principal


def test_preview_confirm_replay_and_owner_edits(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    track = {"id": "12", "title": "Home", "artists": [{"name": "Example"}], "durationMs": 1234}
    tracks = [{"track": track}, {"track": track}]

    def fetch(provider: str, path: str, token: str | None) -> dict[str, Any]:
        assert provider == "yandex" and token is None
        if path == "/albums/55/with-tracks":
            return {"result": {"id": "55", "title": "Edition", "volumes": [[track], [track]]}}
        return {"result": {"title": "A mix", "tracks": tracks, "trackCount": len(tracks)}}

    service = ExternalCollections(sessions, CollectionReader(fetch))
    with sessions.begin() as session:
        owner = _principal(session, "Owner").user_id
        other = _principal(session, "Other").user_id
    op = uuid4()
    link = "https://music.yandex.ru/users/test/playlists/8?utm_campaign=redacted"

    def preview(key: UUID, favorites: bool = False) -> dict[str, Any]:
        return service.start(owner, key, link, favorites=favorites, new_copy=False)

    first = preview(op)
    assert first["state"] == "PREVIEW" and first["read"] == 2 and first["complete"]
    with pytest.raises(CollectionError, match="import_not_found"):
        service.report(other, op)
    with pytest.raises(CollectionError, match="stale_preview"):
        service.confirm(owner, op, first["revision"] + 1)
    with sessions() as session:
        assert not session.scalars(select(PlaylistEntryRow)).all()
        stored = session.get(CollectionImportRow, (owner, op))
        assert stored is not None
        assert "redacted" not in str(stored.snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.confirm(owner, op, first["revision"]), range(2)))
    assert results[0]["import_job_id"] == results[1]["import_job_id"]
    with sessions() as session:
        rows = session.scalars(
            select(PlaylistEntryRow).order_by(PlaylistEntryRow.position_key)
        ).all()
        assert len(rows) == 2 and rows[0].user_track_ref_id == rows[1].user_track_ref_id
        assert not session.scalars(select(UserTrackPreferenceRow)).all()
        assert not session.scalars(select(LibraryEntryRow)).all()
        assert not session.scalar(
            text("SELECT count(*) FROM jobs.job WHERE job_type LIKE 'music.playlist.%'")
        )
    repeat = uuid4()
    assert preview(repeat)["collection_id"] == first["collection_id"]
    service.confirm(owner, repeat, 1)
    favorite = uuid4()
    preview(favorite, True)
    service.confirm(owner, favorite, 1)
    with sessions.begin() as session:
        pref = session.scalars(select(UserTrackPreferenceRow)).one()
        assert pref.preference == "LIKED"
        pref.preference = "NEUTRAL"
    another = uuid4()
    preview(another, True)
    service.confirm(owner, another, 1)
    album = uuid4()
    doc = service.start(
        owner, album, "https://music.yandex.ru/album/55", favorites=False, new_copy=False
    )
    assert [e["disc"] for e in doc["entries"]] == [1, 2]
    service.confirm(owner, album, doc["revision"])
    with sessions() as session:
        assert len(session.scalars(select(PlaylistEntryRow)).all()) == 2
        assert session.scalars(select(UserTrackPreferenceRow)).one().preference == "NEUTRAL"
        assert session.scalar(text("SELECT count(*) FROM library.listening_event")) == 0
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM library.track_metadata WHERE document ? 'album_group_v1'"
                )
            )
            == 2
        )
    # A manually removed occurrence stays removed when the source grows and shrinks.
    with sessions.begin() as session:
        from datetime import UTC, datetime

        members = session.scalars(
            select(PlaylistEntryRow).order_by(PlaylistEntryRow.position_key)
        ).all()
        members[0].removed_at = datetime.now(UTC)
    tracks.append({"track": {**track, "id": "13", "title": "Another track"}})
    updated = uuid4()
    changed = preview(updated)
    service.confirm(owner, updated, changed["revision"])
    tracks.pop(0)
    final_op = uuid4()
    changed = preview(final_op)
    assert service.confirm(owner, final_op, changed["revision"])["missing_occurrences"] == 1
    with sessions() as session:
        active_entries = session.scalars(
            select(PlaylistEntryRow).where(PlaylistEntryRow.removed_at.is_(None))
        ).all()
        assert len(active_entries) == 2
    engine.dispose()


def test_paged_retry_and_session_only_soundcloud(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autplay.adapters import collection_access
    from autplay.domain.external_collections import CollectionPage, SourceTrack

    sessions = sessionmaker(create_engine(database_url), expire_on_commit=False)
    with sessions.begin() as session:
        owner = _principal(session, "Paged").user_id
    monkeypatch.setattr(collection_access, "access_token", lambda *_: "fixture")

    class Pages(CollectionReader):
        interrupt = True

        def page(
            self,
            link: CollectionLink,
            favorites: bool,
            cursor: str | None,
            token: str | None = None,
        ) -> CollectionPage:
            if link.provider == "soundcloud":
                if cursor:
                    return CollectionPage(
                        "SC profile",
                        886,
                        [],
                        sets=[(str(i), "Set") for i in range(63)],
                        expected_sets=63,
                    )
                return CollectionPage(
                    "SC profile",
                    886,
                    [SourceTrack(str(i), "Session only", "Private") for i in range(808)],
                    "sets",
                )
            if cursor and self.interrupt:
                self.interrupt = False
                raise CollectionError("source_unavailable", 503)
            return CollectionPage(
                "Long playlist",
                1000,
                [
                    SourceTrack(str(i), "Track", "Artist")
                    for i in range(50 if cursor else 0, 100 if cursor else 50)
                ],
                None if cursor else "50",
                "v1",
            )

    service = ExternalCollections(sessions, Pages())
    key = uuid4()
    first = service.start(
        owner, key, "https://open.spotify.com/playlist/" + "a" * 22, favorites=False, new_copy=False
    )
    assert first["state"] == "READING" and first["read"] == 50
    with pytest.raises(CollectionError, match="source_unavailable"):
        service.advance(owner, key)
    assert service.report(owner, key)["state"] == "PARTIAL"
    result = service.advance(owner, key)
    assert (result["state"], result["read"], result["expected"], result["complete"]) == (
        "PREVIEW",
        100,
        1000,
        False,
    )
    assert service.report(owner, key, 50)["entries"][0]["track_id"] == "50"
    sc = uuid4()
    result = service.start(
        owner, sc, "https://soundcloud.com/example/likes", favorites=True, new_copy=False
    )
    assert (result["read"], result["expected"], result["read_sets"], result["complete"]) == (
        808,
        886,
        63,
        False,
    )
    with pytest.raises(CollectionError, match="preview_required"):
        service.confirm(owner, sc, result["revision"])
    with sessions() as session:
        stored = session.get(CollectionImportRow, (owner, sc))
        assert stored is not None
        assert stored.snapshot == {}
        assert not session.scalars(select(UserTrackPreferenceRow)).all()


def test_soundcloud_can_confirm_only_independent_playable_catalog_entries(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autplay.adapters import collection_access
    from autplay.domain.external_collections import CollectionPage, SourceTrack

    from .test_track_metadata import setup
    from .test_track_metadata_acquisition import publish_audio

    engine, sessions, owner, ref, _ = setup(database_url)
    publish_audio(sessions, owner, ref)
    with sessions.begin() as session:
        recording = session.scalar(
            text("SELECT recording_id FROM library.user_track_ref WHERE user_track_ref_id=:id"),
            {"id": ref},
        )
        provider = session.scalar(
            text("""INSERT INTO identity.source_provider
                (provider_key,display_name,adapter_id,adapter_version)
            VALUES('soundcloud','SoundCloud','fixture','1') RETURNING provider_id""")
        )
        session.execute(
            text("""INSERT INTO identity.external_reference
                    (provider_id,external_entity_type,external_id,recording_id)
                    VALUES(:provider,'RECORDING','fixture-id',:recording)"""),
            {"provider": provider, "recording": recording},
        )

    class Reader(CollectionReader):
        def page(
            self,
            link: CollectionLink,
            favorites: bool,
            cursor: str | None,
            token: str | None = None,
        ) -> CollectionPage:
            return CollectionPage(
                "session title",
                2,
                [
                    SourceTrack("fixture-id", "other source title", "session artist"),
                    SourceTrack("unknown", "never persisted", "session artist"),
                ],
            )

    monkeypatch.setattr(collection_access, "access_token", lambda *_: "fixture")
    service = ExternalCollections(sessions, Reader())
    op = uuid4()
    page = service.start(
        owner.user_id, op, "https://soundcloud.com/example/likes", favorites=True, new_copy=False
    )
    assert page["read"] == 2 and page["playable"] == 1 and page["selection_key"]
    saved = service.confirm(owner.user_id, op, page["revision"], page["selection_key"])
    assert saved["state"] == "SAVED" and saved["read"] == 1
    with sessions() as session:
        stored = session.get(CollectionImportRow, (owner.user_id, op))
        assert stored is not None
        doc = stored.snapshot
        assert (
            "session artist" not in str(doc)
            and "fixture-id" not in str(doc)
            and "never persisted" not in str(doc)
        )
        preference = session.get(UserTrackPreferenceRow, ref)
        assert preference is not None and preference.preference == "LIKED"
    engine.dispose()


def test_http_authorization_callback_and_revocation(
    database_url: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from urllib.parse import parse_qs, urlsplit

    from autplay.adapters import collection_access
    from autplay.domain.auth import InvalidAccessTokenError, Principal
    from autplay.domain.external_collections import CollectionPage, SourceTrack
    from autplay.entrypoints.api import create_app
    from autplay.runtime.settings import ApiSettings
    from cryptography.fernet import Fernet
    from pydantic import SecretStr
    from starlette.testclient import TestClient

    sessions = sessionmaker(create_engine(database_url))
    with sessions.begin() as session:
        owner, other = _principal(session, "HTTP"), _principal(session, "Other")

    class Auth:
        def authenticate_access(self, token: str) -> Principal:
            if token == "owner":
                return owner
            if token == "other":
                return other
            raise InvalidAccessTokenError()

    secret_file = tmp_path / "key"
    key = Fernet.generate_key()
    secret_file.write_bytes(key)
    monkeypatch.setenv("AUTPLAY_IMPORT_KEY_FILE", str(secret_file))
    monkeypatch.setenv("AUTPLAY_IMPORT_SPOTIFY_CLIENT_ID", "test")
    monkeypatch.setenv(
        "AUTPLAY_IMPORT_SPOTIFY_REDIRECT_URI",
        "https://test.example/api/v1/collection-imports/spotify/callback",
    )
    monkeypatch.setattr(
        collection_access,
        "spotify_exchange",
        lambda code, verifier: Fernet(key).encrypt(b"fixture").decode(),
    )
    monkeypatch.setattr(
        CollectionReader,
        "page",
        lambda *_: CollectionPage("Private tracks", 1, [SourceTrack("one", "Track", "Example")]),
    )

    def revoked(*args: Any) -> None:
        raise CollectionError("source_access_required", 403)

    monkeypatch.setattr(CollectionReader, "check_spotify_access", revoked)
    settings = ApiSettings(
        database_url=SecretStr(database_url),
        auth_signing_secret=SecretStr("runtime-test-signing-secret-at-least-32-bytes"),
        public_access_source_hmac_secret=SecretStr(
            "public-access-source-hmac-secret-at-least-32-bytes"
        ),
    )
    endpoint = "/api/v1/collection-imports"
    op = uuid4()
    body = {
        "operation_id": str(op),
        "url": "https://open.spotify.com/collection/tracks",
        "favorites": True,
    }
    headers = {"Authorization": "Bearer owner"}
    with TestClient(create_app(settings, auth_service=Auth())) as client:  # type: ignore[arg-type]
        assert client.post(endpoint, json=body).status_code == 401
        assert (
            client.post(
                endpoint, json={**body, "owner": str(other.user_id)}, headers=headers
            ).status_code
            == 422
        )
        result = client.post(endpoint, json=body, headers=headers)
        assert result.status_code == 200 and "no-store" in result.headers["cache-control"]
        query = parse_qs(urlsplit(result.json()["authorization_url"]).query)
        assert query["scope"] == ["user-library-read"] and query["code_challenge_method"] == [
            "S256"
        ]
        params = {"code": "fixture", "state": query["state"][0]}
        assert client.get(endpoint + "/spotify/callback", params=params).status_code == 200
        assert client.get(endpoint + "/spotify/callback", params=params).status_code == 403
        report = f"{endpoint}/{op}"
        assert client.get(report, headers={"Authorization": "Bearer other"}).status_code == 404
        preview = client.post(report + "/resume", headers=headers).json()
        assert preview["state"] == "PREVIEW" and preview["read"] == 1
        assert (
            client.post(
                report + "/confirm", json={"revision": preview["revision"]}, headers=headers
            ).status_code
            == 403
        )
        assert client.get(report, headers=headers).json()["read"] == 0
    with sessions.begin() as session:
        row = session.get(CollectionImportRow, (owner.user_id, op))
        assert row and row.snapshot == {} and row.state == "PARTIAL"
        session.execute(
            text(
                "UPDATE importing.collection_import SET created_at=now()-interval '2 hours' "
                "WHERE operation_id=:op"
            ),
            {"op": op},
        )
    assert ExternalCollections(sessions).cleanup_expired() == 1


def test_album_uses_confirmed_edition_and_rechecks_vault(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autplay.adapters import collection_access

    from .test_track_metadata import setup
    from .test_track_metadata_acquisition import publish_audio

    engine, sessions, owner, ref, _ = setup(database_url)
    publish_audio(sessions, owner, ref)
    with sessions.begin() as session:
        track = session.execute(
            text("""SELECT r.recording_id,r.artist_credit_id,r.title,a.display_name
            FROM library.user_track_ref u JOIN catalog.recording r USING(recording_id)
            JOIN catalog.artist_credit a ON a.artist_credit_id=r.artist_credit_id
            WHERE u.user_track_ref_id=:ref"""),
            {"ref": ref},
        ).one()
        session.execute(
            text(
                "UPDATE catalog.recording SET duration_ms=180000, explicit=NULL "
                "WHERE recording_id=:id"
            ),
            {"id": track.recording_id},
        )
    source = {
        "id": "777",
        "title": track.title,
        "artists": [{"name": track.display_name}],
        "durationMs": 180000,
    }

    def fetch(provider: str, path: str, token: str | None) -> dict[str, Any]:
        return {
            "result": {
                "id": "55",
                "title": "Original edition",
                "trackCount": 1,
                "volumes": [[source]],
            }
        }

    service = ExternalCollections(sessions, CollectionReader(fetch))

    def start() -> tuple[UUID, dict[str, Any]]:
        op = uuid4()
        return op, service.start(
            owner.user_id, op, "https://music.yandex.ru/album/55", favorites=False, new_copy=False
        )

    op, preview = start()
    saved = service.confirm(owner.user_id, op, preview["revision"])
    assert saved["playable"] == 0
    with sessions.begin() as session:
        args = {
            "artist": track.artist_credit_id,
            "recording": track.recording_id,
            "title": track.title,
        }
        group = session.scalar(
            text("""INSERT INTO catalog.release_group
                (artist_credit_id,title,normalized_title,primary_type)
            VALUES(:artist,'Edition','edition','ALBUM') RETURNING release_group_id"""),
            args,
        )
        release = session.scalar(
            text("""INSERT INTO catalog.release(release_group_id,artist_credit_id,title)
            VALUES(:grp,:artist,'Edition') RETURNING release_id"""),
            {**args, "grp": group},
        )
        medium = session.scalar(
            text(
                """INSERT INTO catalog.medium(release_id,position) VALUES(:rid,1)
                RETURNING medium_id"""
            ),
            {"rid": release},
        )
        session.execute(
            text("""INSERT INTO catalog.release_track
                (medium_id,recording_id,artist_credit_id,sequence_no,title)
            VALUES(:medium,:recording,:artist,1,:title)"""),
            {**args, "medium": medium},
        )
        provider = session.scalar(
            text("""INSERT INTO identity.source_provider
                (provider_key,display_name,adapter_id,adapter_version)
            VALUES('yandex','Test','fixture','1') RETURNING provider_id""")
        )
        session.execute(
            text("""INSERT INTO identity.external_reference
                (provider_id,external_entity_type,external_id,release_id)
             VALUES(:provider,'RELEASE','55',:rid)"""),
            {"provider": provider, "rid": release},
        )
    op, preview = start()
    assert preview["identified"] == preview["playable"] == 1
    again = service.confirm(owner.user_id, op, preview["revision"])
    assert again["import_job_id"] == saved["import_job_id"] and again["playable"] == 1
    source["contentWarning"] = "explicit"
    _, preview = start()
    assert preview["identified"] == preview["playable"] == 0
    with sessions.begin() as session:
        sc = session.scalar(
            text("""INSERT INTO identity.source_provider
            (provider_key,display_name,adapter_id,adapter_version)
            VALUES('soundcloud','SoundCloud','fixture','1') RETURNING provider_id""")
        )
        session.execute(
            text("""INSERT INTO identity.external_reference
            (provider_id,external_entity_type,external_id,release_id)
            VALUES(:provider,'RELEASE','soundcloud:playlists:7',:rid)"""),
            {"provider": sc, "rid": release},
        )
        session.execute(
            text("""INSERT INTO identity.external_reference
            (provider_id,external_entity_type,external_id,recording_id)
            VALUES(:provider,'RECORDING','soundcloud:tracks:7',:rid)"""),
            {"provider": sc, "rid": track.recording_id},
        )
    monkeypatch.setattr(collection_access, "access_token", lambda *_: "fixture")

    def read_set(provider: str, path: str, token: str | None) -> dict[str, Any]:
        return {
            "urn": "soundcloud:playlists:7",
            "title": "Transient title",
            "is_album": True,
            "track_count": 1,
            "tracks": [{"urn": "soundcloud:tracks:7", "title": track.title}],
        }

    service = ExternalCollections(sessions, CollectionReader(read_set))
    collection = uuid4()
    result = service.start(
        owner.user_id,
        collection,
        "https://soundcloud.com/fixture/sets/example",
        favorites=False,
        new_copy=False,
    )
    assert result["action"] == "ALBUM" and result["selection_key"]
    result = service.confirm(owner.user_id, collection, result["revision"], result["selection_key"])
    assert result["action"] == "ALBUM" and result["name"] == "Edition" and result["playable"] == 1
    engine.dispose()
