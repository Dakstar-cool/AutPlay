from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

from autplay.adapters.postgresql.confirmed_edition import musicbrainz_reference
from autplay.adapters.postgresql.models import (
    PlaylistRow,
    UserTrackRefExternalReferenceRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.shared_catalog import playlist_catalog_handles
from autplay.application.playlist_shares import PlaylistShares
from autplay.application.sync import SyncService
from autplay.entrypoints.playlist_share_http import create_playlist_share_router
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .test_sync_runtime import _bind, _event, _principal


def test_frozen_links_survive_deletion_and_preserve_private_recipient_access(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    source, ref, mbid = uuid4(), uuid4(), uuid4()
    with sessions.begin() as session:
        sender = _principal(session, "Sender")
        receiver = _principal(session, "Recipient")
        session.add(
            PlaylistRow(playlist_id=source, owner_user_id=sender.user_id, name="Private original")
        )
        session.add(
            UserTrackRefRow(user_track_ref_id=ref, user_id=sender.user_id, raw_title="Song")
        )
        external = musicbrainz_reference(session, "RECORDING", str(mbid))
        session.flush()
        session.add(
            UserTrackRefExternalReferenceRow(
                user_track_ref_id=ref,
                external_reference_id=external.external_reference_id,
                relation_role="IMPORT_EVIDENCE",
            )
        )

    def authenticate(request: Request) -> None:
        actor = {"sender": sender, "recipient": receiver}.get(
            request.headers.get("Authorization", "")
        )
        if actor is None:
            raise HTTPException(401)
        request.state.principal = actor

    app = FastAPI()
    app.include_router(
        create_playlist_share_router(PlaylistShares(sessions), authenticate), prefix="/api/v1"
    )
    root = "/api/v1/playlist-shares"
    body: dict[str, Any] = {
        "playlist_id": str(source),
        "name": "<Mix>",
        "description": "Live",
        "tracks": [
            {"ref": str(ref), "title": "<Song>", "artist": "A"},
            {"ref": str(uuid4()), "title": "Phone only", "artist": "B"},
        ],
        "entries": [0, 1, 0],
    }
    with TestClient(app) as client:

        def publish(actor: str = "sender", **changes: object) -> str:
            response = client.post(root, headers={"Authorization": actor}, json={**body, **changes})
            assert response.status_code == 200, response.text
            token = response.json()["token"]
            assert isinstance(token, str)
            return token

        assert client.post(root, json=body).status_code == 401
        with sessions() as session:
            assert session.scalar(text("SELECT count(*) FROM playlist.shared_snapshot")) == 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, retry = pool.map(lambda _: publish(), range(2))
        assert first == retry
        edited = publish(entries=[1, 0])
        assert first != edited
        different_recording = {**body["tracks"][1], "ref": str(uuid4())}
        replaced = publish(tracks=[body["tracks"][0], different_recording])
        assert replaced != first  # same metadata, distinct phone-only recording
        original_copy = client.get(f"{root}/{first}/file").json()
        assert original_copy["tracks"][0]["recording_mbid"] == str(mbid)
        assert original_copy["tracks"][1]["recording_id"] is None
        assert str(ref) not in str(original_copy) and str(sender.user_id) not in str(original_copy)
        assert set(original_copy["tracks"][0]) == {
            "recording_id",
            "recording_mbid",
            "title",
            "artist",
            "duration_ms",
        }
        other = publish("recipient")
        assert client.get(f"{root}/{other}/file").json()["tracks"][0]["recording_mbid"] is None
        with sessions.begin() as session:
            session.execute(
                text("DELETE FROM playlist.playlist WHERE playlist_id=:source"), {"source": source}
            )
        assert client.get(f"{root}/{first}/file").json() == original_copy
        assert original_copy["entries"] == [0, 1, 0]
        assert client.get(f"{root}/{edited}/file").json()["entries"] == [1, 0]
        page = client.get(f"{root}/{first}")
        assert page.status_code == 200 and page.text.count("&lt;Song&gt;") == 2
        assert "<Mix>" not in page.text and f"{first}/file" in page.text
        assert (
            client.delete(f"{root}/{first}", headers={"Authorization": "sender"}).status_code == 405
        )
        assert client.get(f"{root}/{uuid4()}").status_code == 404
        assert (
            client.post(
                root, headers={"Authorization": "sender"}, json={**body, "entries": [0, 7]}
            ).status_code
            == 422
        )
        assert client.get(f"{root}/{publish(entries=[], tracks=[])}/file").json()["entries"] == []

    sync = SyncService(engine, cursor_secret=b"test-only-sync-secret-with-32-bytes")
    bound = _bind(sync, receiver, uuid4())
    copy_refs = [uuid4(), uuid4()]
    events = [
        _event(
            receiver,
            identity,
            index + 1,
            "USER_TRACK_REF_CREATED",
            "USER_TRACK_REF",
            {
                "title": original_copy["tracks"][index]["title"],
                "shared_playlist_id": first,
                "shared_track_index": index,
            },
        )
        for index, identity in enumerate(copy_refs)
    ]
    result = sync.push(receiver, {**bound, "events": events}, uuid4())
    assert [ack["outcome"] for ack in result["acks"]] == ["APPLIED", "APPLIED"]
    with sessions() as session:
        for identity in copy_refs:
            saved = session.get_one(UserTrackRefRow, identity)
            assert saved.user_id == receiver.user_id and saved.recording_id is None
            assert (
                playlist_catalog_handles(session, receiver.user_id)[identity]["resolution_status"]
                == "CATALOG_ONLY"
            )
        assert (
            session.scalar(
                text(
                    "SELECT external_reference_id FROM library.user_track_ref_external_reference "
                    "WHERE user_track_ref_id=:id"
                ),
                {"id": copy_refs[0]},
            )
            == external.external_reference_id
        )
        assert (
            session.scalar(
                text("SELECT count(*) FROM library.library_entry WHERE user_id=:id"),
                {"id": receiver.user_id},
            )
            == 0
        )
    engine.dispose()
