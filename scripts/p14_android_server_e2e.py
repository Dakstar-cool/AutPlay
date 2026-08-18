"""Run the joined Android HTTP/FastAPI/PostgreSQL P14 sync scenario locally."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import uvicorn
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.auth import BootstrapOwnerCommand
from autplay.domain.auth import AccountRole, DeviceDescription, DevicePlatform, Principal
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_auth_service
from autplay.runtime.settings import ApiSettings
from p14_drill import REPOSITORY_ROOT, _project_name, _start, _stop, _upgrade
from pydantic import SecretStr
from sqlalchemy import text

DEFAULT_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "implementation"
    / "evidence"
    / "P14_ANDROID_SERVER_E2E_2026-08-17.json"
)
TEST_SELECTOR = (
    "app.autplay.application.sync.SyncCoordinatorAcceptanceTest#"
    "offlineRoomJournalSurvivesProcessDeathAndProjectsExactlyOnceToSecondDevice"
)
AUTH_SECRET = "p14-disposable-android-server-e2e-signing-secret-v1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_ready(port: int) -> None:
    deadline = time.monotonic() + 30
    url = f"http://127.0.0.1:{port}/health/ready"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("P14 API did not become ready")


def _insert_second_session(
    sqlalchemy_url: str,
    *,
    user_id: uuid.UUID,
    issued_at: datetime,
) -> tuple[uuid.UUID, uuid.UUID]:
    device_id = uuid.uuid7()
    session_id = uuid.uuid7()
    engine = create_runtime_engine(
        ApiSettings(
            database_url=SecretStr(sqlalchemy_url),
            auth_signing_secret=SecretStr(AUTH_SECRET),
            vault_root=REPOSITORY_ROOT / "var" / "p14-e2e-unused",
            vault_low_disk_bytes=0,
        )
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO account.device "
                    "(device_id, user_id, device_name, platform, app_version) "
                    "VALUES (:device_id, :user_id, 'P14 second Android', 'ANDROID', 'rc1')"
                ),
                {"device_id": device_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO account.user_session "
                    "(session_id, user_id, device_id, refresh_token_hash, issued_at, expires_at) "
                    "VALUES (:session_id, :user_id, :device_id, :refresh_hash, "
                    ":issued_at, :expires_at)"
                ),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "device_id": device_id,
                    "refresh_hash": hashlib.sha256(os.urandom(32)).digest(),
                    "issued_at": issued_at,
                    "expires_at": issued_at + timedelta(days=30),
                },
            )
    finally:
        engine.dispose()
    return device_id, session_id


def _run_gradle(
    *,
    java_home: Path,
    android_home: Path,
    serial: str,
    port: int,
    user_id: uuid.UUID,
    first_device_id: uuid.UUID,
    second_device_id: uuid.UUID,
    first_token: str,
    second_token: str,
) -> None:
    adb = android_home / "platform-tools" / "adb.exe"
    if not adb.is_file():
        raise RuntimeError("adb.exe is unavailable")
    subprocess.run(
        [str(adb), "-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    environment = {**os.environ, "JAVA_HOME": str(java_home), "ANDROID_HOME": str(android_home)}
    try:
        subprocess.run(
            [
                str(REPOSITORY_ROOT / "gradlew.bat"),
                f"-Dorg.gradle.java.home={java_home}",
                "--no-daemon",
                "--console=plain",
                ":apps:android:connectedDebugAndroidTest",
                f"-Pandroid.testInstrumentationRunnerArguments.class={TEST_SELECTOR}",
                f"-Pandroid.testInstrumentationRunnerArguments.p14BaseUrl=http://127.0.0.1:{port}/api/v1",
                f"-Pandroid.testInstrumentationRunnerArguments.p14UserId={user_id}",
                f"-Pandroid.testInstrumentationRunnerArguments.p14FirstDeviceId={first_device_id}",
                f"-Pandroid.testInstrumentationRunnerArguments.p14SecondDeviceId={second_device_id}",
                f"-Pandroid.testInstrumentationRunnerArguments.p14FirstToken={first_token}",
                f"-Pandroid.testInstrumentationRunnerArguments.p14SecondToken={second_token}",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=True,
        )
    finally:
        subprocess.run(
            [str(adb), "-s", serial, "reverse", "--remove", f"tcp:{port}"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )


def run(
    *,
    output: Path,
    java_home: Path,
    android_home: Path,
    serial: str,
) -> dict[str, object]:
    output.unlink(missing_ok=True)
    project = _project_name("android-e2e")
    project_started = False
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    started_at = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="autplay-p14-android-server-") as temporary:
        try:
            project_started = True
            dsn, sqlalchemy_url = _start(project)
            _upgrade(sqlalchemy_url)
            port = _free_port()
            settings = ApiSettings(
                database_url=SecretStr(sqlalchemy_url),
                auth_signing_secret=SecretStr(AUTH_SECRET),
                host="127.0.0.1",
                port=port,
                vault_root=Path(temporary) / "vault",
                vault_low_disk_bytes=0,
            )
            engine = create_runtime_engine(settings)
            try:
                auth = build_auth_service(settings, engine)
                first = auth.bootstrap_owner(
                    BootstrapOwnerCommand(
                        display_name="P14 Android E2E",
                        device=DeviceDescription(
                            name="P14 first Android",
                            platform=DevicePlatform.ANDROID,
                            app_version="rc1",
                        ),
                    )
                )
                issued_at = datetime.now(UTC)
                second_device_id, second_session_id = _insert_second_session(
                    sqlalchemy_url,
                    user_id=first.user_id,
                    issued_at=issued_at,
                )
                codec = Hs256AccessTokenCodec(
                    AUTH_SECRET,
                    issuer=settings.auth_issuer,
                    audience=settings.auth_audience,
                    max_ttl=timedelta(seconds=settings.access_token_ttl_seconds),
                )
                second_token = codec.issue(
                    Principal(
                        first.user_id,
                        second_device_id,
                        second_session_id,
                        AccountRole.OWNER,
                    ),
                    token_id=uuid.uuid7(),
                    issued_at=issued_at,
                    expires_at=issued_at + timedelta(minutes=10),
                )
            finally:
                engine.dispose()

            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(settings),
                    host="127.0.0.1",
                    port=port,
                    access_log=False,
                    log_level="warning",
                )
            )
            server_thread = threading.Thread(target=server.run, name="p14-api", daemon=True)
            server_thread.start()
            _wait_ready(port)
            _run_gradle(
                java_home=java_home,
                android_home=android_home,
                serial=serial,
                port=port,
                user_id=first.user_id,
                first_device_id=first.device_id,
                second_device_id=second_device_id,
                first_token=first.access_token,
                second_token=second_token,
            )

            with psycopg.connect(dsn) as connection:
                row = connection.execute(
                    """
                    SELECT
                        (SELECT count(*) FROM sync.device_event_inbox),
                        (SELECT count(*) FROM sync.sync_event),
                        (SELECT count(*) FROM library.user_track_ref WHERE user_id = %s),
                        (SELECT count(*) FROM sync.device_sync_cursor WHERE user_id = %s)
                    """,
                    (first.user_id, first.user_id),
                ).fetchone()
            if row != (1, 1, 1, 2):
                raise RuntimeError(f"joined Android/server persistence mismatch: {row!r}")
            finished_at = datetime.now(UTC)
            report: dict[str, object] = {
                "schema_version": 1,
                "status": "PASS",
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
                "device_serial": serial,
                "transport": "Android OkHttpSyncTransport -> FastAPI HTTP -> PostgreSQL 18.4",
                "android_database": "two independent file-backed Room databases",
                "process_recovery": [
                    "close/reopen after offline transaction",
                    "server commit followed by simulated ACK loss",
                    "same immutable event ID/hash retried as duplicate APPLIED",
                ],
                "server_counts": {
                    "device_event_inbox": row[0],
                    "sync_event": row[1],
                    "user_track_ref": row[2],
                    "device_sync_cursor": row[3],
                },
                "credentials_persisted": False,
                "compose_cleanup": "verified by scoped finalizer",
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return report
        finally:
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=15)
            if project_started:
                try:
                    _stop(project)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--android-home", type=Path, required=True)
    parser.add_argument("--device-serial", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    report = run(
        output=arguments.output.resolve(),
        java_home=arguments.java_home.resolve(),
        android_home=arguments.android_home.resolve(),
        serial=arguments.device_serial,
    )
    print(json.dumps({"status": report["status"], "evidence": str(arguments.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
