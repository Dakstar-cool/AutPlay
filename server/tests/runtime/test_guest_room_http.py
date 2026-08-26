"""S1D HTTP evidence for body-only redemption and separate guest authority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from autplay.domain.auth import AccountRole, GuestPrincipal, Principal
from autplay.domain.wave import WaveRoom
from autplay.entrypoints.guest_room_http import (
    GUEST_HEADER,
    IssueGuestDocumentBody,
    RedeemGuestDocumentBody,
    create_guest_room_router,
)
from autplay.entrypoints.wave_http import WaveBroadcaster
from autplay.runtime.http import install_error_handlers
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

HOST = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
ROOM_ID = uuid4()
INVITATION_ID = uuid4()
GUEST_SESSION_ID = uuid4()
DOCUMENT = "A" * 43
SESSION = "B" * 43
NOW = datetime.now(UTC)


class Service:
    revoked = False

    def issue(
        self, principal: Principal, room_id: object, operation_id: object, *args: object
    ) -> dict[str, object]:
        assert principal == HOST and room_id == ROOM_ID and DOCUMENT in args
        return {
            "operation_id": str(operation_id),
            "invitation_id": str(INVITATION_ID),
            "room_id": str(ROOM_ID),
            "state": "PENDING",
        }

    def revoke(
        self, principal: Principal, invitation_id: object, operation_id: object, now: object
    ) -> dict[str, object]:
        assert principal == HOST and invitation_id == INVITATION_ID and now is not None
        self.revoked = True
        return {
            "operation_id": str(operation_id),
            "invitation_id": str(INVITATION_ID),
            "room_id": str(ROOM_ID),
            "state": "REVOKED",
        }

    def redeem(self, **values: object) -> dict[str, object]:
        assert values["document_bearer"] == DOCUMENT
        assert values["session_bearer"] == SESSION
        assert isinstance(values["source_rate_key"], bytes)
        return {
            "operation_id": str(values["operation_id"]),
            "guest_session_id": str(GUEST_SESSION_ID),
            "invitation_id": str(INVITATION_ID),
            "room_id": str(ROOM_ID),
        }

    def authenticate(
        self, bearer: str, room_id: object, action: str, now: object
    ) -> GuestPrincipal:
        assert bearer == SESSION and room_id == ROOM_ID and now is not None
        return GuestPrincipal(
            GUEST_SESSION_ID,
            INVITATION_ID,
            ROOM_ID,
            1,
            "GUEST",
            frozenset({action}),
            NOW + timedelta(minutes=15),
        )

    def snapshot(self, bearer: str, room_id: object, now: object) -> WaveRoom:
        self.authenticate(bearer, room_id, "ROOM_SNAPSHOT", now)
        return WaveRoom(
            ROOM_ID,
            "",
            HOST.user_id,
            NOW,
            NOW + timedelta(minutes=15),
            {GUEST_SESSION_ID},
            self_role="GUEST",
        )

    def presence(self, bearer: str, room_id: object, now: object) -> None:
        self.authenticate(bearer, room_id, "ROOM_PRESENCE", now)

    def preflight(self, bearer: str, room_id: object, *args: object) -> None:
        self.authenticate(bearer, room_id, "ROOM_PREFLIGHT", args[-1])

    def timing(self, bearer: str, room_id: object, *args: object, **kwargs: object) -> None:
        del kwargs
        self.authenticate(bearer, room_id, "ROOM_TIMING", args[-1])

    def leave(
        self, bearer: str, room_id: object, operation_id: object, now: object
    ) -> dict[str, object]:
        self.authenticate(bearer, room_id, "ROOM_LEAVE", now)
        return {"operation_id": str(operation_id), "room_id": str(room_id), "state": "LEFT"}

    def catch_up(
        self, bearer: str, room_id: object, after: int, now: object
    ) -> list[dict[str, object]]:
        self.authenticate(bearer, room_id, "ROOM_EVENTS", now)
        return [] if after else [{"sequence": 1, "kind": "PAUSE", "payload": {}}]


def _client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    def authenticated(request: Request) -> None:
        request.state.principal = HOST

    app.include_router(
        create_guest_room_router(
            Service(),  # type: ignore[arg-type]
            authenticated=authenticated,
            source_secret=b"s1d-source-secret-is-at-least-32-bytes",
            broadcaster=WaveBroadcaster(),
        )
    )
    return TestClient(app)


def test_secret_models_hide_repr_and_routes_are_body_only_no_store() -> None:
    issue = IssueGuestDocumentBody(operation_id=uuid4(), room_id=ROOM_ID, document_bearer=DOCUMENT)
    redeem = RedeemGuestDocumentBody(
        operation_id=uuid4(),
        invitation_id=INVITATION_ID,
        room_id=ROOM_ID,
        document_bearer=DOCUMENT,
        session_bearer=SESSION,
        display_name="Guest",
    )
    assert DOCUMENT not in repr(issue)
    assert DOCUMENT not in repr(redeem)
    assert SESSION not in repr(redeem)

    client = _client()
    issue_response = client.post("/social/guest-documents", json=issue.model_dump(mode="json"))
    assert issue_response.status_code == 201
    assert issue_response.headers["cache-control"].endswith("no-store, max-age=0")
    assert DOCUMENT not in issue_response.text

    redeem_response = client.post("/wave/guest/redeem", json=redeem.model_dump(mode="json"))
    assert redeem_response.status_code == 200
    assert redeem_response.headers["referrer-policy"] == "no-referrer"
    assert DOCUMENT not in redeem_response.text and SESSION not in redeem_response.text
    assert DOCUMENT not in str(redeem_response.request.url)
    assert SESSION not in str(redeem_response.request.url)

    clock_response = client.post(
        "/wave/guest/clock",
        headers={GUEST_HEADER: SESSION},
        json={"room_id": str(ROOM_ID)},
    )
    assert clock_response.status_code == 200
    assert set(clock_response.json()) == {
        "server_receive_epoch_ms",
        "server_send_epoch_ms",
    }
    assert clock_response.headers["cache-control"].endswith("no-store, max-age=0")


def test_guest_header_is_required_and_out_of_scope_wave_actions_do_not_exist() -> None:
    client = _client()
    path = f"/wave/guest/rooms/{ROOM_ID}/snapshot"
    assert client.get(path).status_code == 401
    response = client.get(path, headers={GUEST_HEADER: SESSION})
    assert response.status_code == 200
    assert response.json()["role"] == "GUEST"

    for suffix in ("commands", "source", "start", "close", "host-transfer"):
        denied = client.post(
            f"/wave/guest/rooms/{ROOM_ID}/{suffix}",
            headers={GUEST_HEADER: SESSION},
            json={},
        )
        assert denied.status_code in {404, 405}
