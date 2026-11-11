"""Future isolated M1 ASGI tests, SOURCE ONLY / UNVERIFIED.

This fixture mounts the slice explicitly with an isolated fake public settings
reference. It does NOT establish production api.py/settings/M5 integration.
Real JWT, schema and read-only PG qualification is in postgresql/test_media_source.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from autplay.application.media_source import MediaSourceService
from autplay.domain.auth import AccessTokenClaims, AccountRole, InvalidAccessTokenError, Principal
from autplay.domain.media_source import MediaSourceError, parse_snapshot
from autplay.entrypoints.media_source_http import (
    MediaSourceIngressMiddleware,
    create_media_source_router,
)
from autplay.ports.media_source import (
    ActorFacts,
    ConfiguredM5Identity,
    DatabaseClockSample,
    ElapsedMark,
    MediaSourcePolicy,
    RelationshipCapture,
    SourceFacts,
)
from autplay.runtime.http import (
    MAX_REQUEST_BODY_FRAMES,
    RequestRuntimeMiddleware,
    install_error_handlers,
)
from autplay.runtime.metrics import RuntimeMetrics
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message
from typed_assertions import present

T = datetime(2026, 10, 16, tzinfo=UTC)
PATH = "/api/v1/media-source/user-tracks/00000000-0000-0000-0000-000000000006/snapshot"
BEARER = {"Authorization": "Bearer synthetic-current"}


class RuntimeFixture:
    def __init__(self, *, mount_ingress: bool = True) -> None:
        self.ns = 0
        self.auth_calls = 0
        self.read_calls = 0
        self.delay_ns = 0
        self.db_fault = False
        self.clock_fault = False
        self.flip_after_clock = False
        self.principal = Principal(UUID(int=1), UUID(int=2), UUID(int=3), AccountRole.USER)
        self.claims = AccessTokenClaims(
            UUID(int=1),
            UUID(int=2),
            UUID(int=3),
            UUID(int=4),
            T - timedelta(seconds=1),
            T - timedelta(seconds=1),
            T + timedelta(seconds=60),
        )
        identity = ConfiguredM5Identity(
            UUID(int=5),
            1,
            b"fixture-public-identity",
            bytes(32),
            "https://api.invalid",
            "https://stream.invalid",
        )
        self.policy = MediaSourcePolicy(True, identity, m5_capability_revision=3)
        actor = ActorFacts(
            UUID(int=1),
            UUID(int=2),
            UUID(int=3),
            UUID(int=1),
            UUID(int=1),
            UUID(int=2),
            "USER",
            "ACTIVE",
            None,
            T - timedelta(days=1),
            None,
            T - timedelta(seconds=2),
            T + timedelta(seconds=60),
            None,
            False,
        )
        source = SourceFacts(
            UUID(int=6),
            7,
            UUID(int=8),
            UUID(int=9),
            11,
            bytes(32),
            2048,
            "audio/flac",
            "flac",
            "flac",
        )
        self.capture = RelationshipCapture(actor, identity, 3, source)
        self.app = FastAPI()
        install_error_handlers(self.app)
        self.app.add_middleware(RequestRuntimeMiddleware, metrics=RuntimeMetrics())
        if mount_ingress:
            self.app.add_middleware(MediaSourceIngressMiddleware, clock=self)
        service = MediaSourceService(self, policy=lambda: self.policy, clock=self)
        self.app.include_router(
            create_media_source_router(
                service, authenticate=self.authenticate, clock=self, isolated_fixture=True
            ),
            prefix="/api/v1",
        )

    def mark(self) -> ElapsedMark:
        return ElapsedMark(self.ns, self.ns)

    def authenticate(self, token: str) -> tuple[Principal, AccessTokenClaims]:
        self.auth_calls += 1
        if token != "synthetic-current":
            raise InvalidAccessTokenError()
        return self.principal, self.claims

    def read_relationship(
        self, principal: Principal, user_track_ref_id: UUID, *, started: ElapsedMark
    ) -> RelationshipCapture:
        self.read_calls += 1
        self.ns += self.delay_ns
        if self.db_fault:
            raise MediaSourceError()
        return self.capture

    def final_database_clock(self, *, started: ElapsedMark) -> DatabaseClockSample:
        if self.clock_fault:
            raise MediaSourceError()
        mark = self.mark()
        sample = DatabaseClockSample(T + timedelta(microseconds=self.ns // 1000), mark, mark)
        if self.flip_after_clock:
            self.policy = replace(self.policy, media_source_snapshot_v1_enabled=False)
        return sample


def assert_error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status
    assert len(response.content) <= 4096
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["vary"] == "Authorization"
    assert "content-encoding" not in response.headers and "location" not in response.headers
    outer = response.json()
    assert set(outer) == {"error"}
    assert set(outer["error"]) == {"code", "message", "retryable", "request_id"}
    assert outer["error"]["code"] == code
    assert outer["error"]["retryable"] is (status == 503)
    assert str(UUID(outer["error"]["request_id"])) == outer["error"]["request_id"]


def test_exact_closed_success_ignores_conditionals_without_cache_or_media_grant() -> None:
    fixture = RuntimeFixture()
    with TestClient(fixture.app, follow_redirects=False) as client:
        response = client.get(PATH, headers={**BEARER, "If-None-Match": "*", "Range": "bytes=0-1"})
    assert response.status_code == 200
    value = parse_snapshot(response.content)
    assert value.capability_revision == 3 and value.user_track_ref_row_version == 7
    assert (
        len(response.content) <= 4096 and response.headers["cache-control"] == "private, no-store"
    )
    assert fixture.auth_calls == fixture.read_calls == 1
    assert "storage_key" not in response.text and "stream.invalid" not in response.text


@pytest.mark.parametrize(
    "path,body,headers",
    [
        (PATH + "?user_id=other", b"", BEARER),
        (PATH, b"{}", BEARER),
        (PATH, b"\x00", BEARER),
        (PATH.replace("000000000006", "00000000000A"), b"", BEARER),
        (PATH.replace("000000000006", "%30%30%30%30%30%30%30%30%30%30%30%36"), b"", BEARER),
        (PATH, b"", {**BEARER, "Content-Encoding": "identity"}),
        (PATH, b"", {**BEARER, "X-Oversized": "x" * 16384}),
    ],
)
def test_invalid_raw_request_precedes_authentication(
    path: str, body: bytes, headers: dict[str, str]
) -> None:
    fixture = RuntimeFixture()
    with TestClient(fixture.app, follow_redirects=False) as client:
        response = client.request("GET", path, content=body, headers=headers)
    assert_error(response, 422, "request_validation_failed")
    assert fixture.auth_calls == fixture.read_calls == 0


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Authorization", "Bearer")],
        [
            ("Authorization", "Bearer synthetic-current"),
            ("Authorization", "Bearer synthetic-current"),
        ],
        [("Authorization", "Bearer  synthetic-current")],
        [("Authorization", "Bearer " + "x" * 8193)],
        [("Authorization", "Bearer wrong")],
    ],
)
def test_exact_bearer_and_current_authentication_precede_source(
    headers: list[tuple[str, str]],
) -> None:
    fixture = RuntimeFixture()
    with TestClient(fixture.app) as client:
        response = client.get(PATH, headers=headers)
    assert_error(response, 401, "authentication_required")
    assert response.headers["www-authenticate"] == "Bearer"
    assert fixture.read_calls == 0


@pytest.mark.parametrize(
    "fault", ["gate", "db", "clock", "config", "oversize", "changed-gate", "no-mount"]
)
def test_safe_unavailable_has_bounded_retry_without_echo(fault: str) -> None:
    fixture = RuntimeFixture(mount_ingress=fault != "no-mount")
    if fault == "gate":
        fixture.policy = replace(fixture.policy, media_source_snapshot_v1_enabled=False)
    elif fault == "db":
        fixture.db_fault = True
    elif fault == "clock":
        fixture.clock_fault = True
    elif fault == "config":
        fixture.capture = replace(
            present(fixture.capture),
            identity=replace(present(fixture.capture.identity), identity_epoch=2),
        )
    elif fault == "oversize":
        fixture.capture = replace(
            present(fixture.capture),
            source=replace(present(fixture.capture.source), codec="x" * 101),
        )
    elif fault == "changed-gate":
        fixture.flip_after_clock = True
    with TestClient(fixture.app) as client:
        response = client.get(PATH, headers=BEARER)
    assert_error(response, 503, "media_source_unavailable")
    assert response.headers["retry-after"] == "5"
    assert "synthetic-current" not in response.text and "api.invalid" not in response.text


@pytest.mark.parametrize("expires", ["jwt", "session"])
def test_http_final_time_refuses_200_after_five_second_authority_read(expires: str) -> None:
    fixture = RuntimeFixture()
    fixture.delay_ns = 5_000_000_000
    if expires == "jwt":
        fixture.claims = replace(fixture.claims, expires_at=T + timedelta(seconds=2))
    else:
        fixture.capture = replace(
            present(fixture.capture),
            actor=replace(
                present(fixture.capture.actor), session_expires_at=T + timedelta(seconds=2)
            ),
        )
    with TestClient(fixture.app) as client:
        response = client.get(PATH, headers=BEARER)
    assert_error(
        response, 401, "authentication_required" if expires == "jwt" else "session_revoked"
    )


def test_missing_target_and_routes_never_redirect_or_echo_path() -> None:
    fixture = RuntimeFixture()
    fixture.capture = replace(fixture.capture, source=None)
    with TestClient(fixture.app, follow_redirects=False) as client:
        for path in (PATH, PATH + "/", "/api/v1/media-source/unknown"):
            response = client.get(path, headers=BEARER)
            assert_error(response, 404, "not_found")
            assert "000000000006" not in response.text


def test_raw_asgi_nonempty_frame_duplicate_length_and_flood_refuse_before_buffering() -> None:
    async def exercise(
        headers: list[tuple[bytes, bytes]], body: bytes, flood: bool = False
    ) -> tuple[list[Message], RuntimeFixture]:
        fixture = RuntimeFixture()
        sent: list[Message] = []
        received = 0

        async def receive() -> Message:
            nonlocal received
            received += 1
            return {"type": "http.request", "body": body, "more_body": flood}

        async def send(message: Message) -> None:
            sent.append(message)

        await fixture.app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "method": "GET",
                "path": PATH,
                "raw_path": PATH.encode(),
                "query_string": b"",
                "headers": headers,
                "scheme": "https",
                "server": ("test.invalid", 443),
                "client": ("127.0.0.1", 1),
                "root_path": "",
            },
            receive,
            send,
        )
        if flood:
            assert received <= MAX_REQUEST_BODY_FRAMES
        return sent, fixture

    for headers, body, flood in (
        ([], b"x", False),
        ([(b"content-length", b"0"), (b"content-length", b"0")], b"", False),
        ([(b"transfer-encoding", b"chunked")], b"", False),
        ([], b"", True),
    ):
        sent, fixture = asyncio.run(exercise(headers, body, flood))
        assert sent[0]["status"] == 422
        assert fixture.auth_calls == fixture.read_calls == 0
