"""Live Wave transport tests with no durable database dependency."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from autplay.application.auth import AuthService
from autplay.application.vault_uploads import VaultNotFoundError
from autplay.domain.auth import AccountRole, OwnedObjectNotFoundError, Principal
from autplay.domain.vault import VaultError
from autplay.domain.wave import WaveRoom
from autplay.entrypoints.auth_http import bearer_authentication
from autplay.entrypoints.wave_http import WaveBroadcaster, create_wave_router
from fastapi import FastAPI
from starlette.requests import HTTPConnection
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

OWNER = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
ROOM = WaveRoom(
    uuid4(),
    "0123456789",
    OWNER.user_id,
    datetime.now(UTC),
    datetime.now(UTC) + timedelta(hours=1),
    {OWNER.user_id},
)


class Auth:
    revoked = False

    def authenticate_access(self, token: str) -> Principal:
        if token != "good" or self.revoked:
            from autplay.domain.auth import InvalidAccessTokenError

            raise InvalidAccessTokenError()
        return OWNER


class Service:
    room = ROOM

    def snapshot(self, room_id: object, principal: Principal, now: object) -> WaveRoom:
        assert room_id == ROOM.room_id and principal == OWNER
        return self.room

    def catch_up(
        self, room_id: object, principal: Principal, after: int, now: object
    ) -> list[dict[str, object]]:
        return (
            [{"sequence": 1, "kind": "PLAY"}]
            if after == 0
            else ([{"sequence": n, "kind": "QUEUE"} for n in range(100)] if after == 9 else [])
        )


def _client(
    source_lookup: object | None = None, *, production_bearer: bool = False
) -> tuple[TestClient, WaveBroadcaster, Auth]:
    app = FastAPI()

    def principal_state(connection: HTTPConnection) -> None:
        connection.state.principal = OWNER

    live = WaveBroadcaster()
    auth = Auth()
    app.include_router(
        create_wave_router(
            Service(),
            authenticated=bearer_authentication(cast(AuthService, auth))
            if production_bearer
            else principal_state,
            auth_service=auth,
            broadcaster=live,
            source_lookup=source_lookup,
        )
    )
    return TestClient(app), live, auth


def test_ws_accepts_header_auth_with_production_http_dependency() -> None:
    client, _, _ = _client(production_bearer=True)
    with client.websocket_connect(
        f"/wave/ws/{ROOM.room_id}", headers={"Authorization": "Bearer good"}
    ) as socket:
        socket.send_json({"type": "hello", "after_sequence": 1})
        assert socket.receive_json()["type"] == "hello"


@pytest.mark.parametrize("query", ["", "?access_token=good"])
def test_ws_rejects_missing_header_with_production_http_dependency(query: str) -> None:
    client, _, _ = _client(production_bearer=True)
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect(f"/wave/ws/{ROOM.room_id}{query}"),
    ):
        pass
    assert closed.value.code == 4401


def test_ws_rejects_duplicate_authorization_headers() -> None:
    client, _, _ = _client(production_bearer=True)
    headers = {"Authorization": "Bearer good", "authorization": "Bearer good"}
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect(f"/wave/ws/{ROOM.room_id}", headers=headers),
    ):
        pass
    assert closed.value.code == 4401


def test_ws_header_auth_hello_catchup_and_gap() -> None:
    client, _, _ = _client()
    with client.websocket_connect(
        f"/wave/ws/{ROOM.room_id}", headers={"Authorization": "Bearer good"}
    ) as socket:
        socket.send_json({"type": "hello", "after_sequence": 0})
        assert socket.receive_json()["type"] == "event"
        assert socket.receive_json()["type"] == "hello"
    with client.websocket_connect(
        f"/wave/ws/{ROOM.room_id}", headers={"Authorization": "Bearer good"}
    ) as socket:
        socket.send_json({"type": "hello", "after_sequence": 9})
        assert socket.receive_json()["type"] == "snapshot_required"


def test_ws_rejects_malformed_hello() -> None:
    client, _, _ = _client()
    with client.websocket_connect(
        f"/wave/ws/{ROOM.room_id}", headers={"Authorization": "Bearer good"}
    ) as socket:
        socket.send_json({"type": "wrong"})
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 4400


def test_ws_live_invalidation_and_revoke_on_ping() -> None:
    client, live, auth = _client()
    with client.websocket_connect(
        f"/wave/ws/{ROOM.room_id}", headers={"Authorization": "Bearer good"}
    ) as socket:
        socket.send_json({"type": "hello", "after_sequence": 1, "room_epoch": "1"})
        assert socket.receive_json()["type"] == "hello"
        live.publish(ROOM.room_id, {"type": "invalidate"})
        assert socket.receive_json()["type"] == "invalidate"
        auth.revoked = True
        socket.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 4401


@pytest.mark.parametrize("denial", [VaultError(), VaultNotFoundError(), OwnedObjectNotFoundError()])
def test_source_lookup_is_owner_filtered_and_masked(denial: Exception) -> None:
    class Lookup:
        fail = False

        def resolve(self, principal: Principal, variant_id: object) -> object:
            assert principal == OWNER and variant_id is not None
            if self.fail:
                raise denial
            return object()

    lookup = Lookup()
    client, _, _ = _client(lookup)
    variant = uuid4()
    assert client.post(
        f"/wave/rooms/{ROOM.room_id}/source", json={"audio_variant_id": str(variant)}
    ).json() == {"vault_streamable": True}
    lookup.fail = True
    assert client.post(
        f"/wave/rooms/{ROOM.room_id}/source", json={"audio_variant_id": str(variant)}
    ).json() == {"vault_streamable": False}


def test_invalidation_from_http_worker_wakes_socket_without_a_ping() -> None:
    async def scenario() -> None:
        live = WaveBroadcaster()
        queue = live.subscribe(ROOM.room_id)
        worker = threading.Thread(target=live.publish, args=(ROOM.room_id, {"type": "invalidate"}))
        worker.start()
        try:
            event = await asyncio.wait_for(queue.get(), timeout=1)
            assert event == {"protocol_version": 1, "type": "invalidate"}
        finally:
            worker.join(timeout=1)
            live.unsubscribe(ROOM.room_id, queue)

    asyncio.run(scenario())


def test_snapshot_contains_durable_playback_intent_for_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent = {
        "sequence": 3,
        "kind": "PLAY",
        "payload": {"queue_entry_id": str(uuid4()), "effective_at": "2026-10-13T12:00:00Z"},
    }
    monkeypatch.setattr(Service, "room", replace(ROOM, command_sequence=3, playback_command=intent))
    client, _, _ = _client()
    response = client.get(f"/wave/rooms/{ROOM.room_id}/snapshot")
    assert response.status_code == 200
    assert response.json()["playback_command"] == intent
