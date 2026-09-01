"""Run the disposable M5B server -> Android enrollment -> sync -> revoke proof.

The test-only handoff route is implemented by this process-local ASGI wrapper.  It is
one-shot, loopback-only (via adb reverse), and is never part of the production API.
No invitation, bearer, refresh token, or private endpoint is passed to Gradle.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import uvicorn
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.auth import BootstrapOwnerCommand
from autplay.domain.auth import DeviceDescription, DevicePlatform
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_auth_service, build_profile_pairing_service
from autplay.runtime.settings import ApiSettings
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from p14_drill import REPOSITORY_ROOT, _project_name, _start, _stop, _upgrade
from pydantic import SecretStr

AUTH_SECRET = "m5b-disposable-android-e2e-signing-secret-v1"
TEST_SELECTOR = "app.autplay.profilepairing.M5bProfilePairingE2eTest"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_ready(port: int) -> None:
    import urllib.request

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health/ready", timeout=2
            ) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("M5B API did not become ready")


class _OneShotInvitationApp:
    """ASGI wrapper which consumes the invitation before forwarding every other request."""

    def __init__(self, app: Any, invitation: dict[str, object]) -> None:
        self._app = app
        self._invitation: bytes | None = json.dumps(invitation, separators=(",", ":")).encode(
            "utf-8"
        )
        self._lock = threading.Lock()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] == "/m5b-e2e/one-shot-invitation":
            if scope["method"] != "GET":
                await send({"type": "http.response.start", "status": 405, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
            with self._lock:
                body, self._invitation = self._invitation, None
            if body is None:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 410,
                        "headers": [(b"cache-control", b"no-store"), (b"pragma", b"no-cache")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"cache-control", b"no-store"),
                        (b"pragma", b"no-cache"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self._app(scope, receive, send)


def _run_gradle(java_home: Path, android_home: Path, serial: str, port: int) -> None:
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
    try:
        subprocess.run(
            [
                str(REPOSITORY_ROOT / "gradlew.bat"),
                f"-Dorg.gradle.java.home={java_home}",
                "--no-daemon",
                "--console=plain",
                ":apps:android:assembleDebug",
                ":apps:android:assembleDebugAndroidTest",
            ],
            cwd=REPOSITORY_ROOT,
            env={**os.environ, "JAVA_HOME": str(java_home), "ANDROID_HOME": str(android_home)},
            check=True,
        )
        apk_root = REPOSITORY_ROOT / "apps" / "android" / "build" / "outputs" / "apk"
        target_apk = apk_root / "debug" / "android-debug.apk"
        test_apk = apk_root / "androidTest" / "debug" / "android-debug-androidTest.apk"
        for apk in (target_apk, test_apk):
            subprocess.run(
                [str(adb), "-s", serial, "install", "-r", "-t", str(apk)],
                cwd=REPOSITORY_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
        instrumentation = subprocess.run(
            [
                str(adb),
                "-s",
                serial,
                "shell",
                "am",
                "instrument",
                "-w",
                "-e",
                "class",
                TEST_SELECTOR,
                "-e",
                "m5bE2eBaseUrl",
                f"http://127.0.0.1:{port}",
                "app.autplay.test/androidx.test.runner.AndroidJUnitRunner",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if instrumentation.returncode != 0 or "OK (1 test)" not in instrumentation.stdout:
            safe_output = instrumentation.stdout.replace(
                f"http://127.0.0.1:{port}", "<loopback>"
            )[-4_000:]
            raise RuntimeError(
                "M5B Android instrumentation failed:\n" + safe_output
            )
    finally:
        subprocess.run(
            [str(adb), "-s", serial, "reverse", "--remove", f"tcp:{port}"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )


def run(*, java_home: Path, android_home: Path, serial: str, output: Path) -> dict[str, object]:
    project, server, thread, started = _project_name("m5b-e2e"), None, None, datetime.now(UTC)
    output.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="autplay-m5b-e2e-") as temporary:
        try:
            dsn, sqlalchemy_url = _start(project)
            _upgrade(sqlalchemy_url)
            port = _free_port()
            pem = (
                ec.generate_private_key(ec.SECP256R1())
                .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
                .decode("ascii")
            )
            origin = f"http://127.0.0.1:{port}"
            settings = ApiSettings(
                database_url=SecretStr(sqlalchemy_url),
                auth_signing_secret=SecretStr(AUTH_SECRET),
                public_access_source_hmac_secret=SecretStr(
                    "public-access-source-hmac-secret-at-least-32-bytes"
                ),
                host="127.0.0.1",
                port=port,
                vault_root=Path(temporary) / "vault",
                vault_low_disk_bytes=0,
                profile_identity_private_key_pem=SecretStr(pem),
                profile_label_hint="M5B disposable",
                profile_api_origin=origin,
                profile_stream_origin=origin,
            )
            engine = create_runtime_engine(settings)
            try:
                auth = build_auth_service(settings, engine)
                owner = auth.bootstrap_owner(
                    BootstrapOwnerCommand(
                        display_name="M5B Android E2E",
                        device=DeviceDescription(
                            name="M5B owner", platform=DevicePlatform.ANDROID, app_version="e2e"
                        ),
                    )
                )
                pairing = build_profile_pairing_service(settings, engine)
                if pairing is None:
                    raise RuntimeError("M5B pairing service unavailable")
                invitation = pairing.issue_recovery_invitation(owner.user_id, uuid.uuid4(), 600)
            finally:
                engine.dispose()
            server = uvicorn.Server(
                uvicorn.Config(
                    _OneShotInvitationApp(create_app(settings), invitation),
                    host="127.0.0.1",
                    port=port,
                    access_log=False,
                    log_level="warning",
                )
            )
            thread = threading.Thread(target=server.run, name="m5b-api", daemon=True)
            thread.start()
            _wait_ready(port)
            _run_gradle(java_home, android_home, serial, port)
            with psycopg.connect(dsn) as connection:
                row = connection.execute(
                    "SELECT count(*) FROM account.device WHERE user_id = %s", (owner.user_id,)
                ).fetchone()
                sessions = connection.execute(
                    "SELECT count(*) FROM account.user_session "
                    "WHERE user_id = %s AND revoked_at IS NOT NULL",
                    (owner.user_id,),
                ).fetchone()
                audits = connection.execute(
                    "SELECT count(*) FROM audit.audit_event WHERE action IN ("
                    "'profile.invitation_issued', 'profile.enrollment_exchanged', "
                    "'profile.device_revoked')",
                    (),
                ).fetchone()
            if row != (2,) or sessions != (1,) or audits != (3,):
                raise RuntimeError("M5B durable enrollment/revocation evidence mismatch")
            finished = datetime.now(UTC)
            report: dict[str, object] = {
                "schema_version": 1,
                "status": "PASS",
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_seconds": round((finished - started).total_seconds(), 3),
                "device_serial": serial,
                "transport": "Android OkHttp pairing/sync -> FastAPI -> PostgreSQL 18.4/pgvector",
                "one_shot_handoff": (
                    "loopback-only, consumed once, no invitation or bearer in Gradle arguments"
                ),
                "durable_counts": {
                    "devices": row[0],
                    "revoked_sessions": sessions[0],
                    "security_audit_events": audits[0],
                },
                "credentials_persisted": False,
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return report
        finally:
            if server is not None:
                server.should_exit = True
            if thread is not None:
                thread.join(timeout=15)
            _stop(project)


def main() -> int:
    parser = argparse.ArgumentParser()
    # Environment defaults keep Windows paths with spaces out of a background
    # process command line; explicit paths remain available for CI.
    parser.add_argument(
        "--java-home",
        type=Path,
        default=Path(os.environ["JAVA_HOME"].rstrip("\\/")),
    )
    parser.add_argument("--android-home", type=Path, default=Path(os.environ["ANDROID_HOME"]))
    parser.add_argument("--device-serial", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT
        / "docs"
        / "implementation"
        / "evidence"
        / "M5B_PROFILE_PAIRING_E2E.json",
    )
    args = parser.parse_args()
    report = run(
        java_home=args.java_home.resolve(),
        android_home=args.android_home.resolve(),
        serial=args.device_serial,
        output=args.output.resolve(),
    )
    print(json.dumps({"status": report["status"], "evidence": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
