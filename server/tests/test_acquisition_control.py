from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from autplay.application.acquisition_control import AcquisitionControlService
from autplay.domain.auth import AccountRole
from autplay.domain.discovery import (
    BulkArtistResolution,
    DiscoveryCandidate,
    ProviderArtist,
    ProviderArtistTracks,
)
from autplay.domain.web_admin import AuthenticatedWebSession, WebActor, WebAdminError
from autplay.entrypoints.acquisition_control_http import create_acquisition_control_router
from autplay.entrypoints.admin_web_http import WebAdminHttp
from autplay.entrypoints.discovery_admin_http import ManualDiscoveryHttp
from autplay.runtime.web_security import encode_request_integrity_token
from autplay.web.renderer import AdminTemplateRenderer
from fastapi import FastAPI
from starlette.testclient import TestClient


def _actor(role: AccountRole = AccountRole.OWNER) -> WebActor:
    return WebActor(uuid4(), uuid4(), uuid4(), role, 0)


def test_submit_is_bounded_owner_only_and_never_carries_operator_paths(tmp_path: Path) -> None:
    control = AcquisitionControlService(tmp_path / "control")
    owner = _actor()
    operation_id = uuid4()
    control.submit_track(
        owner,
        operation_id,
        artist="Artist",
        title="Song",
        workers=3,
        yt_dlp_concurrency=2,
        soundcloud_concurrency=1,
    )
    request = json.loads((tmp_path / "control" / "requests" / f"{operation_id}.json").read_text())
    assert request == {
        "schema_version": 1,
        "action": "submit",
        "queue_id": str(operation_id),
        "workers": 3,
        "yt_dlp_concurrency": 2,
        "soundcloud_concurrency": 1,
    }
    configuration_id = uuid4()
    control.configure(
        owner,
        configuration_id,
        operation_id,
        workers=4,
        yt_dlp_concurrency=2,
        soundcloud_concurrency=2,
    )
    configuration = json.loads(
        (tmp_path / "control" / "requests" / f"{configuration_id}.json").read_text()
    )
    assert configuration["action"] == "configure" and configuration["workers"] == 4
    assert (tmp_path / "control" / "playlists" / f"{operation_id}.txt").read_text() == (
        "Artist\tSong\n"
    )
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "control" / "requests").stat().st_mode) == 0o2770
        assert (
            stat.S_IMODE(
                (tmp_path / "control" / "requests" / f"{operation_id}.json").stat().st_mode
            )
            == 0o660
        )
    assert control.queues(owner)[0].state == "queued"
    assert control.agent_online(owner) is False
    (tmp_path / "control" / "agent.json").write_text(
        json.dumps({"schema_version": 1, "last_seen_unix": time.time(), "sources": ["hitmo"]})
    )
    assert control.agent_online(owner) is True
    assert control.agent_sources(owner) == ("hitmo",)
    with pytest.raises(WebAdminError, match="forbidden"):
        control.queues(_actor(AccountRole.ADMIN))
    with pytest.raises(WebAdminError, match="acquisition_input_invalid"):
        control.submit_track(
            owner,
            uuid4(),
            artist="Artist\nInjected",
            title="Song",
            workers=2,
            yt_dlp_concurrency=1,
            soundcloud_concurrency=1,
        )
    with pytest.raises(WebAdminError, match="acquisition_input_invalid"):
        control.submit_track(
            owner,
            uuid4(),
            artist="Artist",
            title="Song\u2028Injected",
            workers=2,
            yt_dlp_concurrency=1,
            soundcloud_concurrency=1,
        )


def test_artist_group_renders_bounded_status_without_playlist_contents(tmp_path: Path) -> None:
    control = AcquisitionControlService(tmp_path / "control")
    owner = _actor()
    operation_id = uuid4()
    control.submit_artist_group(
        owner,
        operation_id,
        artist="Artist",
        titles="First\nSecond",
        workers=2,
        yt_dlp_concurrency=1,
        soundcloud_concurrency=1,
    )
    status_path = tmp_path / "control" / "status" / f"{operation_id}.json"
    status = json.loads(status_path.read_text())
    status["requested"] = 2
    status["pending"] = 2
    status["providers"] = {
        "yt_dlp": {
            "requests": 3,
            "downloaded": 1,
            "misses": 1,
            "failures": 1,
            "deferred": 0,
            "circuit_open": True,
        }
    }
    status_path.write_text(json.dumps(status))
    queues = control.queues(owner)
    html = AdminTemplateRenderer().render(
        "acquisition_control.html",
        locale="ru",
        context={
            "page_title": "Acquisition",
            "authenticated": True,
            "navigation": (),
            "flash": None,
            "development_mode": False,
            "language_url": "/admin/acquisition?lang=en",
            "csrf_token": "token",
            "upload_operation_id": str(uuid4()),
            "track_operation_id": str(uuid4()),
            "group_operation_id": str(uuid4()),
            "queues": queues,
            "agent_online": False,
            "agent_sources": (),
            "action_operation_ids": {
                str(operation_id): {
                    action: str(uuid4())
                    for action in ("run", "pause", "resume", "retry", "retry_not_found", "verify")
                }
            },
            "configure_operation_ids": {str(operation_id): str(uuid4())},
            "search_operation_id": str(uuid4()),
            "artist_search_operation_id": str(uuid4()),
            "selection_operation_id": str(uuid4()),
            "search_results": (),
            "artist_results": (),
            "search_mode": None,
            "notice": None,
            "live": False,
            "discovery_enabled": False,
        },
    )
    assert "Управление загрузкой" in html
    assert "First" not in html and "Second" not in html
    assert f"/admin/acquisition/{operation_id}/pause" in html
    assert "yt_dlp" in html and "Охлаждение" in html


def test_web_track_form_requires_origin_and_csrf(tmp_path: Path) -> None:
    control = AcquisitionControlService(tmp_path / "control")
    actor = _actor()

    class Web:
        def authenticate_safe_get(self, _bearer: bytes) -> AuthenticatedWebSession:
            return AuthenticatedWebSession(actor, b"x" * 32)

        def authenticate(self, _bearer: bytes, *, mutation: bool) -> AuthenticatedWebSession:
            assert mutation
            return AuthenticatedWebSession(actor, b"x" * 32)

        def validate_csrf(self, current: WebActor, csrf: bytes, _operation_id: object) -> None:
            assert current == actor and csrf == b"x" * 32

    candidate = DiscoveryCandidate(
        "1",
        "2",
        "Song",
        "Artist",
        None,
        180,
        "https://creativecommons.org/licenses/by/4.0/",
        "https://www.jamendo.com/track/1",
        False,
        None,
    )
    provider_artist = ProviderArtist("2", "Artist", "https://www.jamendo.com/artist/2")

    class Discovery:
        def search(
            self, _owner_id: object, _query: str, *, limit: int = 20
        ) -> tuple[DiscoveryCandidate, ...]:
            assert limit == 20
            return (candidate,)

        def resolve_artists(
            self, _owner_id: object, _artists: object
        ) -> tuple[BulkArtistResolution, ...]:
            return (BulkArtistResolution("Artist", 1, "EXACT_MATCH", provider_artist),)

        def preview_artist_tracks(
            self,
            _owner_id: object,
            _artists: object,
            *,
            max_tracks_per_artist: int = 25,
            max_tracks_total: int = 25,
        ) -> tuple[ProviderArtistTracks, ...]:
            return (ProviderArtistTracks("2", 1, (candidate,)),)

    app = FastAPI()
    app.include_router(
        create_acquisition_control_router(
            web=cast(WebAdminHttp, Web()),
            acquisition=control,
            renderer=AdminTemplateRenderer(),
            origin="http://127.0.0.1:8787",
            discovery_enabled=True,
            discovery=cast(ManualDiscoveryHttp, Discovery()),
        )
    )
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    assert client.get("/admin/acquisition").status_code == 200
    fields = {
        "csrf_token": encode_request_integrity_token(b"x" * 32),
        "operation_id": str(uuid4()),
        "artist": "Artist",
        "title": "Song",
        "workers": "2",
        "yt_dlp_concurrency": "1",
        "soundcloud_concurrency": "1",
    }
    denied = client.post("/admin/acquisition/track", data=fields, follow_redirects=False)
    assert denied.status_code == 400
    accepted = client.post(
        "/admin/acquisition/track",
        data=fields,
        headers={"Origin": "http://127.0.0.1:8787"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    assert len(control.queues(actor)) == 1
    upload_fields = {key: value for key, value in fields.items() if key not in {"artist", "title"}}
    upload_fields["operation_id"] = str(uuid4())
    uploaded = client.post(
        "/admin/acquisition/upload",
        data=upload_fields,
        files={"playlist": ("tracks.txt", b"Artist\tAnother\n", "text/plain")},
        headers={"Origin": "http://127.0.0.1:8787"},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    assert len(control.queues(actor)) == 2
    searched = client.post(
        "/admin/acquisition/search",
        data={
            "csrf_token": fields["csrf_token"],
            "operation_id": str(uuid4()),
            "query": "Song",
        },
        headers={"Origin": "http://127.0.0.1:8787"},
    )
    assert searched.status_code == 200 and "Artist" in searched.text
    artist_results = client.post(
        "/admin/acquisition/artist-search",
        data={
            "csrf_token": fields["csrf_token"],
            "operation_id": str(uuid4()),
            "artist": "Artist",
        },
        headers={"Origin": "http://127.0.0.1:8787"},
    )
    assert artist_results.status_code == 200
    assert 'name="selection"' in artist_results.text
    selected = client.post(
        "/admin/acquisition/selection",
        data={
            "csrf_token": fields["csrf_token"],
            "operation_id": str(uuid4()),
            "selection": "Artist\tSong",
            "workers": "2",
            "yt_dlp_concurrency": "1",
            "soundcloud_concurrency": "1",
        },
        headers={"Origin": "http://127.0.0.1:8787"},
        follow_redirects=False,
    )
    assert selected.status_code == 303 and len(control.queues(actor)) == 3
