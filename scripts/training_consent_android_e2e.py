"""Run physical Android consent grant/replay/withdrawal against disposable PostgreSQL."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import uvicorn
from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.adapters.postgresql.models import UserSessionRow
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.auth import BootstrapOwnerCommand
from autplay.domain.auth import DeviceDescription, DevicePlatform
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_auth_service, build_profile_pairing_service
from autplay.runtime.settings import ApiSettings
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from p14_drill import _project_name, _start, _stop, _upgrade
from pydantic import SecretStr
from self_pairing_roles_android_e2e import (
    PRODUCTION_APPLICATION_ID,
    QA_APPLICATION_ID,
    QA_TEST_APPLICATION_ID,
    REPOSITORY_ROOT,
    _build_and_install,
    _file_sha256,
    _production_fingerprint,
    _serial_sha256,
    _wait_ready,
)
from sqlalchemy.orm import Session

AUTH_SECRET = "training-consent-disposable-signing-secret-v1"
LEDGER_KEY = b"training-consent-e2e-ledger-key-v1"
TEST_SELECTOR = (
    "app.autplay.trainingconsent.TrainingConsentE2eTest#grantLostReplyReplayThenWithdraw"
)


class _TrainingConsentE2eApp:
    """Serves credentials once and drops the first committed consent reply."""

    def __init__(self, app: Any, handoff: dict[str, str | int]) -> None:
        self._app = app
        self._handoff: bytes | None = json.dumps(handoff, separators=(",", ":")).encode()
        self._successful_reply_dropped = False
        self._put_calls = 0
        self._lock = threading.Lock()

    @property
    def evidence(self) -> dict[str, int]:
        with self._lock:
            return {
                "successful_replies_dropped": int(self._successful_reply_dropped),
                "put_calls": self._put_calls,
            }

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] == ("/training-consent-e2e/one-shot-handoff"):
            if scope["method"] != "GET":
                await send({"type": "http.response.start", "status": 405, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
            with self._lock:
                body, self._handoff = self._handoff, None
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

        consent_put = (
            scope["type"] == "http"
            and scope["path"] == "/api/v1/privacy/shared-training"
            and scope["method"] == "PUT"
        )
        if consent_put:
            with self._lock:
                self._put_calls += 1
                should_capture = not self._successful_reply_dropped
        else:
            should_capture = False
        if not should_capture:
            await self._app(scope, receive, send)
            return

        messages: list[dict[str, Any]] = []

        async def capture(message: dict[str, Any]) -> None:
            messages.append(message)

        await self._app(scope, receive, capture)
        start = next(
            (message for message in messages if message["type"] == "http.response.start"),
            None,
        )
        if start is None or not 200 <= int(start["status"]) < 300:
            for message in messages:
                await send(message)
            return
        with self._lock:
            self._successful_reply_dropped = True
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                    (b"pragma", b"no-cache"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"error":{"code":"training_consent_unavailable"}}',
            }
        )


def _promote_session_to_v2(engine: Any, session_id: uuid.UUID) -> uuid.UUID:
    with Session(engine) as session, session.begin():
        row = session.get(UserSessionRow, session_id)
        if row is None:
            raise RuntimeError("bootstrap session disappeared")
        row.family_id = row.session_id
        row.generation = 0
        row.session_mode = "V2"
        return session_id


def _run_android(adb: Path, serial: str, port: int) -> None:
    cleared = subprocess.run(
        [str(adb), "-s", serial, "shell", "pm", "clear", QA_APPLICATION_ID],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if "Success" not in cleared.stdout:
        raise RuntimeError("QA application data was not cleared")
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
            "trainingConsentE2eBaseUrl",
            f"http://127.0.0.1:{port}",
            f"{QA_TEST_APPLICATION_ID}/androidx.test.runner.AndroidJUnitRunner",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if instrumentation.returncode != 0 or "OK (1 test)" not in instrumentation.stdout:
        safe = instrumentation.stdout.replace(f"http://127.0.0.1:{port}", "<loopback>")[-4_000:]
        raise RuntimeError("training consent Android instrumentation failed:\n" + safe)


def _write_hash_manifest(output: Path) -> str:
    digest = str(_file_sha256(output))
    output.with_suffix(".sha256.json").write_text(
        json.dumps(
            {"schema_version": 1, "files": {output.name: digest}},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return digest


def run(*, java_home: Path, android_home: Path, serial: str, output: Path) -> dict[str, object]:
    project = _project_name("training-consent-android-e2e")
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    adb: Path | None = None
    port: int | None = None
    started = datetime.now(UTC)
    output.unlink(missing_ok=True)
    output.with_suffix(".sha256.json").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="autplay-training-consent-e2e-") as temporary:
        temporary_root = Path(temporary)
        try:
            dsn, sqlalchemy_url = _start(project)
            _upgrade(sqlalchemy_url)
            port = _free_port()
            origin = f"http://127.0.0.1:{port}"
            private_key = ec.generate_private_key(ec.SECP256R1())
            pem = private_key.private_bytes(
                Encoding.PEM,
                PrivateFormat.PKCS8,
                NoEncryption(),
            ).decode("ascii")
            ledger_path = temporary_root / "training-consent.sqlite3"
            ledger = FilesystemTrainingConsentLedger(ledger_path, LEDGER_KEY, "physical-e2e-v1")
            ledger.initialize()
            settings = ApiSettings(
                database_url=SecretStr(sqlalchemy_url),
                auth_signing_secret=SecretStr(AUTH_SECRET),
                public_access_source_hmac_secret=SecretStr(
                    "training-consent-source-hmac-secret-at-least-32-bytes"
                ),
                host="127.0.0.1",
                port=port,
                vault_root=temporary_root / "vault",
                vault_low_disk_bytes=0,
                profile_identity_private_key_pem=SecretStr(pem),
                profile_label_hint="Training consent disposable",
                profile_api_origin=origin,
                profile_stream_origin=origin,
                shared_training_consent_enabled=True,
                training_consent_ledger_path=ledger_path,
                training_consent_ledger_key=SecretStr(LEDGER_KEY.decode()),
                training_consent_ledger_key_id="physical-e2e-v1",
            )
            engine = create_runtime_engine(settings)
            try:
                auth = build_auth_service(settings, engine)
                owner = auth.bootstrap_owner(
                    BootstrapOwnerCommand(
                        display_name="Training consent E2E owner",
                        device=DeviceDescription(
                            name="A55 consent phone",
                            platform=DevicePlatform.ANDROID,
                            app_version="physical-e2e",
                        ),
                    )
                )
                family_id = _promote_session_to_v2(engine, owner.session_id)
                profile = build_profile_pairing_service(settings, engine)
                if profile is None:
                    raise RuntimeError("profile identity service unavailable")
                discovery = profile.discovery()["payload"]
                binding_commit_id = uuid.uuid4()
                wrapper = _TrainingConsentE2eApp(
                    create_app(settings),
                    {
                        "account_id": str(owner.user_id),
                        "device_id": str(owner.device_id),
                        "session_id": str(owner.session_id),
                        "session_family_id": str(family_id),
                        "server_instance_id": str(discovery["server_instance_id"]),
                        "identity_epoch": int(str(discovery["identity_epoch"])),
                        "identity_thumbprint_sha256": str(discovery["identity_thumbprint_sha256"]),
                        "binding_commit_id": str(binding_commit_id),
                        "access_token": owner.access_token,
                        "refresh_token": owner.refresh_token,
                    },
                )
                server = uvicorn.Server(
                    uvicorn.Config(
                        wrapper,
                        host="127.0.0.1",
                        port=port,
                        access_log=False,
                        log_level="warning",
                    )
                )
                server_thread = threading.Thread(
                    target=server.run,
                    name="training-consent-e2e-api",
                    daemon=True,
                )
                server_thread.start()
                _wait_ready(port)

                adb_candidate = android_home / "platform-tools" / "adb.exe"
                production_before = _production_fingerprint(adb_candidate, serial)
                adb, _, _, apk_hashes = _build_and_install(java_home, android_home, serial)
                subprocess.run(
                    [str(adb), "-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}"],
                    cwd=REPOSITORY_ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                _run_android(adb, serial, port)

                loss = wrapper.evidence
                if loss != {"successful_replies_dropped": 1, "put_calls": 3}:
                    raise RuntimeError("consent lost-reply replay evidence mismatch")
                production_after = _production_fingerprint(adb, serial)
                if production_before != production_after:
                    raise RuntimeError("production package metadata changed during QA proof")

                with psycopg.connect(dsn) as connection:
                    policy = connection.execute(
                        "SELECT decision, revision, policy_version "
                        "FROM account.training_consent WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    operations = connection.execute(
                        "SELECT count(*), array_agg(applied_decision ORDER BY applied_revision), "
                        "array_agg(applied_revision ORDER BY applied_revision) "
                        "FROM account.training_consent_operation WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    authority = connection.execute(
                        "SELECT a.status, d.revoked_at IS NULL, s.revoked_at IS NULL, "
                        "s.session_mode, s.generation "
                        "FROM account.user_account a "
                        "JOIN account.device d ON d.user_id=a.user_id AND d.device_id=%s "
                        "JOIN account.user_session s ON s.user_id=a.user_id AND s.session_id=%s "
                        "WHERE a.user_id=%s",
                        (owner.device_id, owner.session_id, owner.user_id),
                    ).fetchone()
                if policy != ("WITHDRAWN", 2, 1):
                    raise RuntimeError("final training consent policy mismatch")
                if operations is None or operations[0] != 2:
                    raise RuntimeError("training consent operation count mismatch")
                if list(operations[1]) != ["GRANTED", "WITHDRAWN"] or list(operations[2]) != [1, 2]:
                    raise RuntimeError("training consent operation sequence mismatch")
                if authority != ("ACTIVE", True, True, "V2", 0):
                    raise RuntimeError("consent changed unrelated binding authority")
                history = ledger.read()
                latest = history.latest.get(ledger.owner_tag(owner.user_id))
                if latest is None or latest.decision != "WITHDRAWN" or latest.revision != 2:
                    raise RuntimeError("independent consent ledger mismatch")
                if len(history.operations) != 2:
                    raise RuntimeError("independent consent operation count mismatch")

                finished = datetime.now(UTC)
                report: dict[str, object] = {
                    "schema_version": 1,
                    "status": "PASS",
                    "started_at": started.isoformat(),
                    "finished_at": finished.isoformat(),
                    "duration_seconds": round((finished - started).total_seconds(), 3),
                    "device_serial_sha256": _serial_sha256(serial),
                    "transport": (
                        "Android production consent runtime/OkHttp/Keystore -> "
                        "FastAPI -> PostgreSQL 18.4/pgvector + independent SQLite ledger"
                    ),
                    "one_shot_handoff": (
                        "loopback-only and consumed once; no account ID, access token, "
                        "refresh token, or binding identifier in Gradle arguments or report"
                    ),
                    "lost_reply": loss,
                    "policy": {"decision": policy[0], "revision": policy[1]},
                    "operations": {
                        "count": operations[0],
                        "decisions": list(operations[1]),
                        "revisions": list(operations[2]),
                    },
                    "independent_ledger": {
                        "operation_count": len(history.operations),
                        "latest_decision": latest.decision,
                        "latest_revision": latest.revision,
                    },
                    "binding_authority": {
                        "account_status": authority[0],
                        "active_device": authority[1],
                        "active_session": authority[2],
                        "session_mode": authority[3],
                        "session_generation": authority[4],
                    },
                    "android_journal": "encrypted pending grant replayed and cleared",
                    "production_package": {
                        "application_id": PRODUCTION_APPLICATION_ID,
                        **production_after,
                        "unchanged": True,
                    },
                    "apk_sha256": apk_hashes,
                    "credentials_persisted": False,
                }
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                _write_hash_manifest(output)
                return report
            finally:
                engine.dispose()
        finally:
            if adb is not None and port is not None:
                subprocess.run(
                    [str(adb), "-s", serial, "shell", "pm", "clear", QA_APPLICATION_ID],
                    cwd=REPOSITORY_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [str(adb), "-s", serial, "reverse", "--remove", f"tcp:{port}"],
                    cwd=REPOSITORY_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=15)
            _stop(project)


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser()
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
        / "TRAINING_CONSENT_ANDROID_E2E.json",
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
