"""P06 direct-stream HTTP semantics with descriptor-free fake storage."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from autplay.domain.auth import (
    AccountRole,
    InvalidAccessTokenError,
    OwnedObjectNotFoundError,
    Principal,
)
from autplay.domain.vault import ByteRange, OpaqueStorageKey, Sha256Digest
from autplay.entrypoints.stream import create_stream_app
from autplay.entrypoints.stream_http import AuthorizedStream, _stream
from autplay.runtime.settings import StreamSettings
from pydantic import SecretStr
from starlette.requests import Request
from starlette.testclient import TestClient
from starlette.types import Scope

DATABASE_URL = "postgresql+psycopg://runtime:runtime@127.0.0.1:1/autplay"
AUTH_SECRET = "runtime-test-signing-secret-at-least-32-bytes"
BYTES = b"abcdefghij"
OWNER = Principal(uuid4(), uuid4(), uuid4(), AccountRole.OWNER)


class Auth:
    def authenticate_access(self, token: str) -> Principal:
        if token != "good":
            raise InvalidAccessTokenError()
        return OWNER


@dataclass
class Lookup:
    denied: bool = False

    def resolve(self, principal: Principal, audio_variant_id: UUID) -> AuthorizedStream:
        del audio_variant_id
        if self.denied or principal != OWNER:
            raise OwnedObjectNotFoundError()
        return AuthorizedStream(
            OpaqueStorageKey("a" * 64),
            Sha256Digest(b"a" * 32),
            len(BYTES),
            "audio/mpeg",
            datetime.now(UTC),
        )


class Reader:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield self.payload

    def close(self) -> None:
        self.closed = True


class Storage:
    def __init__(self) -> None:
        self.reader: Reader | None = None

    def open_range(self, key: OpaqueStorageKey, byte_range: ByteRange, **_: object) -> Reader:
        assert key.value == "a" * 64
        self.reader = Reader(BYTES[byte_range.start : byte_range.end + 1])
        return self.reader


def _client(lookup: Lookup, storage: Storage) -> TestClient:
    settings = StreamSettings(
        database_url=SecretStr(DATABASE_URL),
        auth_signing_secret=SecretStr(AUTH_SECRET),
    )
    app = create_stream_app(settings, lookup=lookup, auth_service=Auth(), storage=storage)  # type: ignore[arg-type]
    return TestClient(app)


def test_stream_full_partial_head_and_range_policy() -> None:
    storage = Storage()
    identifier = uuid4()
    etag = '"sha256-' + "61" * 32 + '"'
    with _client(Lookup(), storage) as client:
        full = client.get(
            f"/api/v1/stream/audio-variants/{identifier}", headers={"Authorization": "Bearer good"}
        )
        partial = client.get(
            f"/api/v1/stream/audio-variants/{identifier}",
            headers={"Authorization": "Bearer good", "Range": "bytes=2-5"},
        )
        open_ended = client.get(
            f"/api/v1/stream/audio-variants/{identifier}",
            headers={"Authorization": "Bearer good", "Range": "bytes=8-"},
        )
        suffix = client.get(
            f"/api/v1/stream/audio-variants/{identifier}",
            headers={"Authorization": "Bearer good", "Range": "bytes=-3"},
        )
        head = client.head(
            f"/api/v1/stream/audio-variants/{identifier}",
            headers={"Authorization": "Bearer good", "Range": "bytes=2-5"},
        )
        invalid = client.get(
            f"/api/v1/stream/audio-variants/{identifier}",
            headers={"Authorization": "Bearer good", "Range": "bytes=0-1,3-4"},
        )
    assert full.status_code == 200 and full.content == BYTES
    assert full.headers["etag"] == etag and full.headers["accept-ranges"] == "bytes"
    assert partial.status_code == 206 and partial.content == b"cdef"
    assert partial.headers["content-range"] == "bytes 2-5/10"
    assert open_ended.status_code == 206 and open_ended.content == b"ij"
    assert suffix.status_code == 206 and suffix.content == b"hij"
    assert head.status_code == 206 and head.content == b"" and head.headers["content-length"] == "4"
    assert invalid.status_code == 416 and invalid.headers["content-range"] == "bytes */10"
    assert storage.reader is not None and storage.reader.closed


def test_stream_if_range_and_owner_auth_are_fail_closed() -> None:
    identifier = uuid4()
    etag = '"sha256-' + "61" * 32 + '"'
    with _client(Lookup(), Storage()) as client:
        weak = client.get(
            f"/api/v1/stream/audio-variants/{identifier}",
            headers={"Authorization": "Bearer good", "Range": "bytes=0-1", "If-Range": "W/" + etag},
        )
        mismatch = client.get(
            f"/api/v1/stream/audio-variants/{identifier}",
            headers={
                "Authorization": "Bearer good",
                "Range": "bytes=0-1",
                "If-Range": "Wed, 21 Oct 2015 07:28:00 GMT",
            },
        )
        unauthenticated = client.get(f"/api/v1/stream/audio-variants/{identifier}")
    assert weak.status_code == mismatch.status_code == 200
    assert unauthenticated.status_code == 401
    with _client(Lookup(denied=True), Storage()) as client:
        missing = client.get(
            f"/api/v1/stream/audio-variants/{identifier}",
            headers={"Authorization": "Bearer good", "Range": "broken"},
        )
    assert missing.status_code == 404


def test_stream_disconnect_stops_iteration_and_closes_reader() -> None:
    reader = Reader(BYTES)

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": None,
            "server": None,
        },
    )

    async def consume() -> list[bytes]:
        return [payload async for payload in _stream(reader, Request(scope, receive))]

    assert asyncio.run(consume()) == []
    assert reader.closed
