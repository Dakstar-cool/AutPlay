"""UNEXECUTED production M1 mount/ownership/commit controls.

All imports, keys, JWTs, DB and ASGI operations below are FUTURE statements.
Only the existing guarded disposable database_url and synthetic fixture are used.
These scenarios do not qualify production, real media or macOS behavior.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from autplay.domain.media_source import (
    MediaSourceError,
    MediaSourceSnapshotRequestV1,
    parse_snapshot,
)
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_media_source_authenticator
from autplay.entrypoints.media_source_http import MediaSourceIngressMiddleware
from autplay.runtime.http import RequestRuntimeMiddleware
from autplay.runtime.settings import ApiSettings
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, text

from .test_media_source import Harness, _database_image
from .test_media_source import media as media


def settings_for(
    media: Harness, database_url: str, tmp_path: Path, *, enabled: bool = True
) -> ApiSettings:
    pem = media.identity_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")
    return ApiSettings(
        database_url=SecretStr(database_url),
        vault_root=tmp_path / "vault",
        auth_signing_secret=SecretStr("synthetic-M1-access-secret-at-least-32-bytes"),
        public_access_source_hmac_secret=SecretStr("M1-synthetic-source-secret-at-least-32-bytes"),
        auth_issuer="M1",
        auth_audience="M1",
        media_source_snapshot_v1_enabled=enabled,
        profile_identity_private_key_pem=SecretStr(pem),
        profile_label_hint="Synthetic M1",
        profile_api_origin="https://api.invalid",
        profile_stream_origin="https://stream.invalid",
    )


def assert_closed(response: Any, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["vary"] == "Authorization"
    assert response.headers["content-type"] == "application/json"
    assert "content-encoding" not in response.headers and "location" not in response.headers
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"code", "message", "retryable", "request_id"}
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"] is (status == 503)
    assert len(response.content) <= 4096


def test_actual_mount_public_capture_same_pool_no_writes_and_shutdown(
    media: Harness,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings_for(media, database_url, tmp_path))
    path = f"/api/v1/media-source/user-tracks/{media.source}/snapshot"
    headers = {"Authorization": "Bearer " + media.token()}
    classes: list[object] = [item.cls for item in app.user_middleware]
    assert classes[0] is MediaSourceIngressMiddleware
    assert classes.index(MediaSourceIngressMiddleware) < classes.index(RequestRuntimeMiddleware)
    with TestClient(app, follow_redirects=False) as client:
        assert_closed(client.get(path, headers=headers), 503, "media_source_unavailable")
        # Existing M5 producer actually commits before the public reference exists.
        assert client.get("/api/v1/pairing/discovery").status_code == 200
        pairing = app.state.profile_pairing_service
        policy = app.state.media_source_policy()
        assert policy.identity is not None and policy.m5_capability_revision == 3
        assert client.get("/api/v1/pairing/discovery").status_code == 200
        assert app.state.media_source_policy() is policy
        source_db = app.state.media_source_database
        assert source_db is not app.state.sync_binding_database
        assert source_db.engine.url == pairing._sessions.kw["bind"].url
        assert source_db.engine.sync_engine is not pairing._sessions.kw["bind"]

        def connection_defaults(sessions: Any, budget: Any) -> Any:
            with sessions() as session:
                return tuple(
                    session.execute(
                        text(
                            "SELECT current_setting('transaction_read_only'), "
                            "current_setting('application_name')"
                        )
                    ).one()
                )

        assert asyncio.run(source_db.run(connection_defaults)) == ("on", "autplay-media-source")
        shared: list[str] = []
        statements: list[str] = []

        def shared_read(*args: Any) -> None:
            shared.append(args[2])

        def owned_read(*args: Any) -> None:
            statements.append(args[2])

        def forbidden(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("snapshot attempted mutable/locking identity access")

        before = _database_image(media)
        event.listen(pairing._sessions.kw["bind"], "before_cursor_execute", shared_read)
        event.listen(source_db.engine.sync_engine, "before_cursor_execute", owned_read)
        monkeypatch.setattr(pairing, "_instance", forbidden)
        monkeypatch.setattr(pairing, "current_device_contract_identity", forbidden)
        try:
            response = client.get(path, headers={**headers, "If-None-Match": "*"})
        finally:
            event.remove(pairing._sessions.kw["bind"], "before_cursor_execute", shared_read)
            event.remove(source_db.engine.sync_engine, "before_cursor_execute", owned_read)
        assert response.status_code == 200
        value = parse_snapshot(response.content)
        assert value.recording_id == str(media.recording) and value.capability_revision == 3
        assert (
            value.user_track_ref_row_version
            == parse_snapshot(media.reply()).user_track_ref_row_version
        )
        assert shared == []
        assert sum("FROM (SELECT 1) AS anchor" in query for query in statements) == 1
        assert sum(query == "SELECT clock_timestamp()" for query in statements) == 1
        assert not any(
            "FOR UPDATE" in query
            or "pg_advisory" in query
            or query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            for query in statements
        )
        assert _database_image(media) == before
    assert app.state.media_source_policy().media_source_snapshot_v1_enabled is False
    assert asyncio.run(source_db.await_closed()).value == "CLOSED"


def test_actual_mount_ingress_gate_and_closed_runtime_are_unavailable(
    media: Harness,
    database_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(settings_for(media, database_url, tmp_path, enabled=False))
    path = f"/api/v1/media-source/user-tracks/{media.source}/snapshot"
    headers = {"Authorization": "Bearer " + media.token()}
    with TestClient(app, follow_redirects=False) as client:
        for suffix in ("/", "/unknown"):
            assert_closed(client.get(path + suffix, headers=headers), 404, "not_found")
        assert_closed(
            client.request("GET", path + "?x=1", content=b"x"), 422, "request_validation_failed"
        )
        assert_closed(client.post(path, headers=headers), 422, "request_validation_failed")
        assert_closed(client.get(path), 401, "authentication_required")
        assert_closed(client.get(path, headers=headers), 503, "media_source_unavailable")
        assert client.get("/api/v1/pairing/discovery").status_code == 200
        app.state.profile_pairing_service.configure_media_source_policy(enabled=True)
        assert client.get(path, headers=headers).status_code == 200
        asyncio.run(app.state.media_source_database.shutdown())
        assert_closed(client.get(path, headers=headers), 503, "media_source_unavailable")


def test_m5_only_real_outer_commit_publishes_and_configuration_change_invalidates_prepared(
    media: Harness,
    database_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(settings_for(media, database_url, tmp_path))
    with TestClient(app):
        pairing = app.state.profile_pairing_service
        cold = pairing.current_media_source_policy()
        with pairing._sessions() as session, session.begin():
            with session.begin_nested():
                pairing._instance(session, datetime.now(UTC))
            assert pairing.current_media_source_policy() is cold
            session.rollback()
        assert pairing.current_media_source_policy() is cold
        pairing.discovery()
        committed = pairing.current_media_source_policy()
        assert committed is not cold and committed.m5_capability_revision == 3

        token = media.token()
        started = app.state.media_source_clock.mark()

        def prepare(sessions: Any, budget: Any) -> Any:
            authenticate = build_media_source_authenticator(
                app.state.auth_service,
                sessions,
                identity=pairing,
                deletion_evidence=app.state.deletion_evidence,
            )
            principal, claims = authenticate(token)
            return app.state.media_source_service.prepare(
                principal, claims, MediaSourceSnapshotRequestV1(str(media.source)), started=started
            )

        prepared = asyncio.run(app.state.media_source_database.run(prepare))
        assert parse_snapshot(prepared.admit()).capability_revision == 3
        pairing.discovery()
        assert pairing.current_media_source_policy() is committed
        pairing._label = "Synthetic changed M5 label"
        pairing.discovery()
        changed = pairing.current_media_source_policy()
        assert changed is not committed and changed.m5_capability_revision == 4
        with pytest.raises(MediaSourceError, match="media_source_unavailable"):
            prepared.admit()


def test_mounted_actual_auth_then_slow_relationship_refuses_expired_jwt(
    media: Harness,
    database_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(settings_for(media, database_url, tmp_path))
    path = f"/api/v1/media-source/user-tracks/{media.source}/snapshot"
    with TestClient(app) as client:
        assert client.get("/api/v1/pairing/discovery").status_code == 200
        token = media.token(expires_in=2)
        delayed = False

        def slow_relationship(
            connection: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            nonlocal delayed
            if not delayed and "FROM (SELECT 1) AS anchor" in statement:
                delayed = True
                delay = connection.connection.dbapi_connection.cursor()
                try:
                    delay.execute("SELECT pg_sleep(5)")
                    delay.fetchall()
                finally:
                    delay.close()

        engine = app.state.media_source_database.engine.sync_engine
        event.listen(engine, "before_cursor_execute", slow_relationship)
        try:
            response = client.get(path, headers={"Authorization": "Bearer " + token})
        finally:
            event.remove(engine, "before_cursor_execute", slow_relationship)
        assert delayed
        assert_closed(response, 401, "authentication_required")
