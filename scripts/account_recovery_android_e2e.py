"""Run physical Android one-use recovery with a deliberately lost commit reply."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import uvicorn
from autplay.adapters.postgresql.models import DeviceRow, UserSessionRow
from autplay.adapters.postgresql.models.account_recovery import AccountRecoveryCredentialRow
from autplay.adapters.postgresql.models.profile_pairing import TrustedDeviceKeyRow
from autplay.adapters.postgresql.models.web_admin import WebSessionRow
from autplay.adapters.postgresql.models.web_passkeys import WebPasskeyRow
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.auth import BootstrapOwnerCommand
from autplay.domain.account_recovery import code_verifier, new_code
from autplay.domain.auth import DeviceDescription, DevicePlatform
from autplay.domain.profile_pairing import public_spki
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_auth_service, build_profile_pairing_service
from autplay.runtime.settings import ApiSettings
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
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
from sqlalchemy.orm import Session, sessionmaker

AUTH_SECRET = "account-recovery-disposable-signing-secret-v1"
TEST_SELECTOR = (
    "app.autplay.accountrecovery.AccountRecoveryE2eTest#"
    "manualRecoveryRotatesCodeAndCommitsThroughLostReply"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class _RecoveryE2eApp:
    """Serves the old code once and discards the first committed recovery reply."""

    def __init__(self, app: Any, account_id: uuid.UUID, recovery_code: str) -> None:
        self._app = app
        self._handoff: bytes | None = json.dumps(
            {"account_id": str(account_id), "recovery_code": recovery_code},
            separators=(",", ":"),
        ).encode("utf-8")
        self._commit_reply_dropped = False
        self._outcome_calls = 0
        self._lock = threading.Lock()

    @property
    def evidence(self) -> dict[str, int]:
        with self._lock:
            return {
                "committed_replies_dropped": int(self._commit_reply_dropped),
                "outcome_calls": self._outcome_calls,
            }

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] == "/account-recovery-e2e/one-shot-handoff":
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

        if scope["type"] == "http" and scope["path"] == "/api/v1/recovery/outcome":
            with self._lock:
                self._outcome_calls += 1

        should_capture = False
        if scope["type"] == "http" and scope["path"] == "/api/v1/recovery/commit":
            with self._lock:
                should_capture = not self._commit_reply_dropped
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
            self._commit_reply_dropped = True
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
                "body": b'{"error_code":"account_recovery_unavailable"}',
            }
        )


def _seed_recovery_authority(
    engine: Any,
    owner: Any,
    *,
    server_id: uuid.UUID,
    identity_epoch: int,
    identity_thumbprint: bytes,
    recovery_code: str,
) -> None:
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime.now(UTC)
    old_device_id, old_session_id = uuid.uuid4(), uuid.uuid4()
    trusted_spki = public_spki(ec.generate_private_key(ec.SECP256R1()))
    trusted_thumbprint = hashlib.sha256(trusted_spki).digest()
    passkey_id = uuid.uuid4()
    web_session_id = uuid.uuid4()
    with sessions.begin() as session:
        session.add(
            DeviceRow(
                device_id=old_device_id,
                user_id=owner.user_id,
                device_name="Prior recovery phone",
                platform="ANDROID",
                app_version="physical-e2e",
                public_key=trusted_spki,
                public_key_thumbprint_sha256=trusted_thumbprint,
            )
        )
        session.flush()
        session.add(
            UserSessionRow(
                session_id=old_session_id,
                user_id=owner.user_id,
                device_id=old_device_id,
                refresh_token_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                issued_at=now,
                expires_at=now + timedelta(days=90),
                family_id=old_session_id,
                generation=0,
                session_mode="V2",
            )
        )
        session.add(
            AccountRecoveryCredentialRow(
                user_id=owner.user_id,
                server_instance_id=server_id,
                identity_epoch=identity_epoch,
                identity_thumbprint_sha256=identity_thumbprint,
                generation=1,
                verifier_sha256=code_verifier(server_id, owner.user_id, recovery_code),
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TrustedDeviceKeyRow(
                user_id=owner.user_id,
                device_key_thumbprint_sha256=trusted_thumbprint,
                device_public_key_spki=trusted_spki,
                approved_request_id=uuid.uuid4(),
                key_reference=uuid.uuid4(),
                revision=1,
                created_at=now,
            )
        )
        session.add(
            WebPasskeyRow(
                passkey_id=passkey_id,
                server_instance_id=server_id,
                user_id=owner.user_id,
                user_handle=secrets.token_bytes(32),
                credential_id=secrets.token_bytes(32),
                public_key=trusted_spki,
                sign_count=0,
                backup_eligible=False,
                backed_up=False,
                label="Recovery E2E passkey",
                created_at=now,
            )
        )
        session.flush()
        session.add(
            WebSessionRow(
                web_session_id=web_session_id,
                family_id=web_session_id,
                server_instance_id=server_id,
                user_id=owner.user_id,
                token_generation=0,
                token_sha256=secrets.token_bytes(32),
                csrf_sha256=secrets.token_bytes(32),
                issued_at=now,
                token_issued_at=now,
                last_activity_at=now,
                idle_expires_at=now + timedelta(minutes=5),
                absolute_expires_at=now + timedelta(hours=1),
                passkey_id=passkey_id,
            )
        )


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
            "accountRecoveryE2eBaseUrl",
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
        raise RuntimeError("account recovery Android instrumentation failed:\n" + safe)


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
    project = _project_name("account-recovery-android-e2e")
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    adb: Path | None = None
    port: int | None = None
    started = datetime.now(UTC)
    output.unlink(missing_ok=True)
    output.with_suffix(".sha256.json").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="autplay-account-recovery-e2e-") as temporary:
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
            settings = ApiSettings(
                database_url=SecretStr(sqlalchemy_url),
                auth_signing_secret=SecretStr(AUTH_SECRET),
                public_access_source_hmac_secret=SecretStr(
                    "account-recovery-source-hmac-secret-at-least-32-bytes"
                ),
                host="127.0.0.1",
                port=port,
                vault_root=Path(temporary) / "vault",
                vault_low_disk_bytes=0,
                profile_identity_private_key_pem=SecretStr(pem),
                profile_label_hint="Account recovery disposable",
                profile_api_origin=origin,
                profile_stream_origin=origin,
                account_recovery_enabled=True,
            )
            engine = create_runtime_engine(settings)
            try:
                auth = build_auth_service(settings, engine)
                owner = auth.bootstrap_owner(
                    BootstrapOwnerCommand(
                        display_name="Recovery E2E owner",
                        device=DeviceDescription(
                            name="Original recovery phone",
                            platform=DevicePlatform.ANDROID,
                            app_version="physical-e2e",
                        ),
                    )
                )
                profile = build_profile_pairing_service(settings, engine)
                if profile is None:
                    raise RuntimeError("profile identity service unavailable")
                discovery = profile.discovery()["payload"]
                server_id = uuid.UUID(str(discovery["server_instance_id"]))
                identity_epoch = int(str(discovery["identity_epoch"]))
                identity_thumbprint = bytes.fromhex(str(discovery["identity_thumbprint_sha256"]))
                recovery_code = new_code()
                _seed_recovery_authority(
                    engine,
                    owner,
                    server_id=server_id,
                    identity_epoch=identity_epoch,
                    identity_thumbprint=identity_thumbprint,
                    recovery_code=recovery_code,
                )

                wrapper = _RecoveryE2eApp(create_app(settings), owner.user_id, recovery_code)
                recovery_code = ""
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
                    name="account-recovery-e2e-api",
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
                if loss != {"committed_replies_dropped": 1, "outcome_calls": 1}:
                    raise RuntimeError("lost-reply recovery evidence mismatch")
                production_after = _production_fingerprint(adb, serial)
                if production_before != production_after:
                    raise RuntimeError("production package metadata changed during QA proof")

                with psycopg.connect(dsn) as connection:
                    authority_generation = connection.execute(
                        "SELECT authority_generation FROM account.user_account WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    devices = connection.execute(
                        "SELECT count(*) FILTER (WHERE revoked_at IS NULL), "
                        "count(*) FILTER (WHERE revoked_at IS NOT NULL) "
                        "FROM account.device WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    sessions = connection.execute(
                        "SELECT count(*) FILTER (WHERE revoked_at IS NULL), "
                        "count(*) FILTER (WHERE revoked_at IS NOT NULL) "
                        "FROM account.user_session WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    web_sessions = connection.execute(
                        "SELECT count(*) FILTER (WHERE revoked_at IS NOT NULL) "
                        "FROM account.web_session WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    passkeys = connection.execute(
                        "SELECT count(*) FILTER (WHERE revoked_at IS NOT NULL) "
                        "FROM account.web_passkey WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    trust = connection.execute(
                        "SELECT count(*) FILTER (WHERE removed_at IS NOT NULL) "
                        "FROM account.trusted_device_key WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    credential = connection.execute(
                        "SELECT generation, verifier_sha256 "
                        "FROM account.account_recovery_credential WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    operations = connection.execute(
                        "SELECT count(*), min(kind), max(result_generation) "
                        "FROM account.account_recovery_operation WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    audit = connection.execute(
                        "SELECT count(*) FROM audit.audit_event "
                        "WHERE actor_user_id=%s AND action='account_recovery.recover'",
                        (owner.user_id,),
                    ).fetchone()
                    recovered = connection.execute(
                        "SELECT d.device_name, s.session_mode "
                        "FROM account.device d JOIN account.user_session s "
                        "ON s.user_id=d.user_id AND s.device_id=d.device_id "
                        "WHERE d.user_id=%s AND d.revoked_at IS NULL AND s.revoked_at IS NULL",
                        (owner.user_id,),
                    ).fetchone()

                if authority_generation != (2,):
                    raise RuntimeError("account authority generation did not rotate")
                if devices != (1, 2) or sessions != (1, 2):
                    raise RuntimeError("prior Android authority was not fully revoked")
                if web_sessions != (1,) or passkeys != (1,) or trust != (1,):
                    raise RuntimeError("browser/passkey/trust authority was not fully revoked")
                if credential is None or credential[0] != 2:
                    raise RuntimeError("recovery credential generation did not rotate")
                if operations != (1, "RECOVER", 2) or audit != (1,):
                    raise RuntimeError("recovery receipt/audit evidence mismatch")
                if recovered != ("A55 recovered phone", "V2"):
                    raise RuntimeError("new recovery binding evidence mismatch")

                finished = datetime.now(UTC)
                report: dict[str, object] = {
                    "schema_version": 1,
                    "status": "PASS",
                    "started_at": started.isoformat(),
                    "finished_at": finished.isoformat(),
                    "duration_seconds": round((finished - started).total_seconds(), 3),
                    "device_serial_sha256": _serial_sha256(serial),
                    "transport": (
                        "Android production recovery runtime/OkHttp/Keystore -> "
                        "FastAPI -> PostgreSQL 18.4/pgvector"
                    ),
                    "one_shot_handoff": (
                        "loopback-only and consumed once; no recovery code, account ID, "
                        "refresh secret, or bearer in Gradle arguments or report"
                    ),
                    "lost_reply": loss,
                    "authority_generation": authority_generation[0],
                    "durable_counts": {
                        "active_devices": devices[0],
                        "revoked_devices": devices[1],
                        "active_application_sessions": sessions[0],
                        "revoked_application_sessions": sessions[1],
                        "revoked_browser_sessions": web_sessions[0],
                        "revoked_passkeys": passkeys[0],
                        "removed_trusted_keys": trust[0],
                        "recovery_operations": operations[0],
                        "recovery_audit_events": audit[0],
                    },
                    "recovery_generation": credential[0],
                    "new_binding": {"active_devices": 1, "active_sessions": 1, "mode": "V2"},
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
        / "ACCOUNT_RECOVERY_ANDROID_E2E.json",
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
