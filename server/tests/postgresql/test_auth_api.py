"""Real-database HTTP evidence for P03 device-session routes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from autplay.adapters.postgresql.readiness import ReadinessResult
from autplay.application.auth import AuthService, BootstrapOwnerCommand
from autplay.domain.auth import DeviceDescription, DevicePlatform
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_auth_service
from autplay.runtime.settings import ApiSettings, RuntimeProfile
from fastapi import FastAPI
from psycopg import Connection
from pydantic import SecretStr
from sqlalchemy import create_engine
from starlette.testclient import TestClient

AUTH_SECRET = "p03-http-test-signing-secret-at-least-32-bytes"


@dataclass(frozen=True, slots=True)
class ReadyProbe:
    def check(self) -> ReadinessResult:
        return ReadinessResult(ready=True, component="postgresql")


@pytest.fixture
def auth_http_runtime(database_url: str) -> Iterator[tuple[FastAPI, AuthService]]:
    settings = ApiSettings(
        profile=RuntimeProfile.TEST,
        database_url=SecretStr(database_url),
        auth_signing_secret=SecretStr(AUTH_SECRET),
        public_access_source_hmac_secret=SecretStr(
            "public-access-source-hmac-secret-at-least-32-bytes"
        ),
        auth_issuer="autplay-p03-test",
        auth_audience="autplay-p03-client",
        access_token_ttl_seconds=600,
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    service = build_auth_service(settings, engine)
    app = create_app(settings, readiness_probe=ReadyProbe(), auth_service=service)
    try:
        yield app, service
    finally:
        engine.dispose()


def test_refresh_logout_and_auth_responses_are_real_bounded_and_no_store(
    auth_http_runtime: tuple[FastAPI, AuthService],
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, service = auth_http_runtime
    first = service.bootstrap_owner(
        BootstrapOwnerCommand(
            display_name="HTTP Owner",
            device=DeviceDescription(
                name="HTTP Device",
                platform=DevicePlatform.ANDROID,
                app_version="p03-test",
            ),
        )
    )

    with TestClient(app) as client:
        missing_auth = client.post("/api/v1/auth/logout")
        rotated = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": first.refresh_token},
        )
        old_access = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {first.access_token}"},
        )
        replacement_access = rotated.json()["access_token"]
        duplicate_authorization = client.post(
            "/api/v1/auth/logout",
            headers=[
                ("Authorization", f"Bearer {replacement_access}"),
                ("Authorization", "Bearer duplicate-must-fail"),
            ],
        )
        logout = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {replacement_access}"},
        )
        logged_out = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {replacement_access}"},
        )

    assert missing_auth.status_code == 401
    assert missing_auth.headers["www-authenticate"] == "Bearer"
    assert rotated.status_code == 200
    assert rotated.headers["cache-control"] == "no-store"
    assert rotated.headers["pragma"] == "no-cache"
    assert rotated.json()["refresh_token"] != first.refresh_token
    assert rotated.json()["session_id"] != str(first.session_id)
    assert old_access.status_code == 401
    assert duplicate_authorization.status_code == 401
    assert logout.status_code == 204
    assert logged_out.status_code == 401

    rendered_logs = caplog.text
    assert first.access_token not in rendered_logs
    assert first.refresh_token not in rendered_logs
    assert rotated.json()["access_token"] not in rendered_logs
    assert rotated.json()["refresh_token"] not in rendered_logs


def test_refresh_replay_revokes_replacement_and_device_authorization_hides_ownership(
    auth_http_runtime: tuple[FastAPI, AuthService],
    database_connection: Connection[Any],
) -> None:
    app, service = auth_http_runtime
    first = service.bootstrap_owner(
        BootstrapOwnerCommand(
            display_name="Authorization Owner",
            device=DeviceDescription(
                name="Owner Device",
                platform=DevicePlatform.OTHER,
                app_version="p03-test",
            ),
        )
    )
    other_user = database_connection.execute(
        "INSERT INTO account.user_account (display_name, role) "
        "VALUES ('Other User', 'USER') RETURNING user_id"
    ).fetchone()
    assert other_user is not None
    other_device_id = uuid.uuid7()
    database_connection.execute(
        "INSERT INTO account.device "
        "(device_id, user_id, device_name, platform, app_version) "
        "VALUES (%s, %s, 'Other Device', 'OTHER', 'p03-test')",
        (other_device_id, other_user[0]),
    )
    database_connection.commit()

    with TestClient(app) as client:
        rotated = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": first.refresh_token},
        )
        assert rotated.status_code == 200
        replacement_access = rotated.json()["access_token"]

        cross_user = client.post(
            f"/api/v1/devices/{other_device_id}/revoke",
            headers={"Authorization": f"Bearer {replacement_access}"},
        )
        missing = client.post(
            f"/api/v1/devices/{uuid.uuid7()}/revoke",
            headers={"Authorization": f"Bearer {replacement_access}"},
        )
        replay = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": first.refresh_token},
        )
        revoked_access = client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {replacement_access}"},
        )
        malformed = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "not-a-token"},
        )

    assert cross_user.status_code == missing.status_code == 404
    assert cross_user.json()["error"]["code"] == missing.json()["error"]["code"] == "not_found"
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_token_replay"
    assert replay.headers["cache-control"] == "no-store"
    assert revoked_access.status_code == 401
    assert malformed.status_code == 401
    assert malformed.json()["error"]["code"] == "invalid_refresh_token"
    assert database_connection.execute(
        "SELECT revoked_at FROM account.device WHERE device_id = %s",
        (other_device_id,),
    ).fetchone() == (None,)


def test_admin_module_bootstraps_through_real_runtime_composition(
    database_url: str,
    database_connection: Connection[Any],
) -> None:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTPLAY_")
    }
    environment.update(
        {
            "AUTPLAY_DATABASE_URL": database_url,
            "AUTPLAY_AUTH_SIGNING_SECRET": AUTH_SECRET,
            "AUTPLAY_PUBLIC_ACCESS_SOURCE_HMAC_SECRET": (
                "public-access-source-hmac-secret-at-least-32-bytes"
            ),
            "AUTPLAY_PROFILE": "test",
        }
    )
    command = [
        sys.executable,
        "-m",
        "autplay.entrypoints.admin",
        "bootstrap-owner",
        "--display-name",
        "Composed Owner",
        "--device-name",
        "Composed Device",
        "--platform",
        "OTHER",
        "--app-version",
        "p03-test",
    ]
    first = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    second = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )

    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["token_type"] == "Bearer"
    assert first.stderr == ""
    assert second.returncode == 4
    assert json.loads(second.stderr)["error"]["code"] == "owner_already_bootstrapped"
    assert result["access_token"] not in second.stderr
    assert result["refresh_token"] not in second.stderr
    assert AUTH_SECRET not in first.stdout + first.stderr + second.stdout + second.stderr
    assert database_connection.execute(
        "SELECT count(*) FROM account.user_account WHERE role = 'OWNER'"
    ).fetchone() == (1,)
