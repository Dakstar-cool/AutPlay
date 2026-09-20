"""Run physical Android deletion request and explicit lost-reply cancellation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import uvicorn
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.account_recovery import AccountRecoveryCredentialRow
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.auth import BootstrapOwnerCommand
from autplay.domain.account_recovery import code_verifier, new_code
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
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

AUTH_SECRET = "account-deletion-disposable-signing-secret-v1"
LEDGER_KEY = b"account-deletion-e2e-ledger-key-v1"
TEST_SELECTOR = (
    "app.autplay.accountdeletion.AccountDeletionE2eTest#"
    "requestDetachesThenCancellationCreatesOneNewBinding"
)


class _AccountDeletionE2eApp:
    """One-shot setup plus exact suspension and lost-cancellation observations."""

    def __init__(
        self,
        app: Any,
        engine: Any,
        account_id: uuid.UUID,
        device_id: uuid.UUID,
        handoff: dict[str, str | int],
    ) -> None:
        self._app = app
        self._engine = engine
        self._account_id = account_id
        self._device_id = device_id
        self._handoff: bytes | None = json.dumps(handoff, separators=(",", ":")).encode()
        self._source_key_registered = False
        self._deletion_request_calls = 0
        self._suspension: dict[str, object] | None = None
        self._cancel_reply_dropped = False
        self._cancel_outcome_calls = 0
        self._lock = threading.Lock()

    @property
    def evidence(self) -> dict[str, object]:
        with self._lock:
            return {
                "source_key_registrations": int(self._source_key_registered),
                "deletion_request_calls": self._deletion_request_calls,
                "suspension": self._suspension,
                "cancel_replies_dropped": int(self._cancel_reply_dropped),
                "cancel_outcome_calls": self._cancel_outcome_calls,
            }

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] == ("/account-deletion-e2e/one-shot-handoff"):
            await self._serve_handoff(scope, send)
            return
        if scope["type"] == "http" and scope["path"] == (
            "/account-deletion-e2e/register-source-key"
        ):
            await self._register_source_key(scope, receive, send)
            return

        deletion_request = (
            scope["type"] == "http"
            and scope["path"] == "/api/v1/account/deletion"
            and scope["method"] == "POST"
        )
        cancel_commit = (
            scope["type"] == "http"
            and scope["path"] == "/api/v1/deletion/cancel/commit"
            and scope["method"] == "POST"
        )
        if scope["type"] == "http" and scope["path"] == "/api/v1/deletion/cancel/outcome":
            with self._lock:
                self._cancel_outcome_calls += 1
        if not deletion_request and not cancel_commit:
            await self._app(scope, receive, send)
            return

        with self._lock:
            if deletion_request:
                self._deletion_request_calls += 1
            drop = cancel_commit and not self._cancel_reply_dropped
        messages: list[dict[str, Any]] = []

        async def capture(message: dict[str, Any]) -> None:
            messages.append(message)

        await self._app(scope, receive, capture)
        start = next(
            (message for message in messages if message["type"] == "http.response.start"),
            None,
        )
        success = start is not None and 200 <= int(start["status"]) < 300
        if deletion_request and success:
            self._capture_suspension()
        if not (drop and success):
            for message in messages:
                await send(message)
            return
        with self._lock:
            self._cancel_reply_dropped = True
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
                "body": b'{"error":{"code":"account_deletion_unavailable"}}',
            }
        )

    async def _serve_handoff(self, scope: Any, send: Any) -> None:
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

    async def _register_source_key(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["method"] != "POST":
            await send({"type": "http.response.start", "status": 405, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if len(body) > 4096 or not message.get("more_body", False):
                break
        status = 422
        try:
            value = json.loads(bytes(body))
            if set(value) != {"public_key_spki_b64", "thumbprint_sha256"}:
                raise ValueError
            spki = base64.b64decode(value["public_key_spki_b64"], validate=True)
            thumbprint = hashlib.sha256(spki).hexdigest()
            if len(spki) > 1024 or thumbprint != value["thumbprint_sha256"]:
                raise ValueError
            with self._lock:
                if self._source_key_registered:
                    status = 409
                else:
                    with Session(self._engine) as session, session.begin():
                        device = session.get(DeviceRow, self._device_id, with_for_update=True)
                        if device is None or device.user_id != self._account_id:
                            raise ValueError
                        device.public_key = spki
                        device.public_key_thumbprint_sha256 = bytes.fromhex(thumbprint)
                    self._source_key_registered = True
                    status = 204
        except ValueError, TypeError, KeyError, json.JSONDecodeError:
            status = 422
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"cache-control", b"no-store"), (b"pragma", b"no-cache")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    def _capture_suspension(self) -> None:
        with Session(self._engine) as session:
            account = session.get(UserAccountRow, self._account_id)
            active_devices = session.scalar(
                select(func.count())
                .select_from(DeviceRow)
                .where(DeviceRow.user_id == self._account_id, DeviceRow.revoked_at.is_(None))
            )
            active_sessions = session.scalar(
                select(func.count())
                .select_from(UserSessionRow)
                .where(
                    UserSessionRow.user_id == self._account_id,
                    UserSessionRow.revoked_at.is_(None),
                )
            )
            state = session.execute(
                text(
                    "SELECT state, revision FROM account.account_deletion_request WHERE user_id=:u"
                ),
                {"u": self._account_id},
            ).fetchone()
        snapshot = {
            "account_status": None if account is None else account.status,
            "authority_generation": None if account is None else account.authority_generation,
            "active_devices": int(active_devices or 0),
            "active_sessions": int(active_sessions or 0),
            "request_state": None if state is None else state[0],
            "request_revision": None if state is None else state[1],
        }
        with self._lock:
            self._suspension = snapshot


def _promote_and_seed(
    engine: Any,
    owner: Any,
    *,
    server_id: uuid.UUID,
    identity_epoch: int,
    identity_thumbprint: bytes,
    recovery_code: str,
) -> uuid.UUID:
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        row = session.get(UserSessionRow, owner.session_id)
        if row is None:
            raise RuntimeError("bootstrap session disappeared")
        row.family_id = row.session_id
        row.generation = 0
        row.session_mode = "V2"
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
            UserAccountRow(
                user_id=uuid.uuid4(),
                display_name="Remaining deletion E2E owner",
                role="OWNER",
            )
        )
    return uuid.UUID(str(owner.session_id))


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
            "accountDeletionE2eBaseUrl",
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
        raise RuntimeError("account deletion Android instrumentation failed:\n" + safe)


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


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def run(*, java_home: Path, android_home: Path, serial: str, output: Path) -> dict[str, object]:
    project = _project_name("account-deletion-android-e2e")
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    adb: Path | None = None
    port: int | None = None
    started = datetime.now(UTC)
    output.unlink(missing_ok=True)
    output.with_suffix(".sha256.json").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="autplay-account-deletion-e2e-") as temporary:
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
            ledger_path = temporary_root / "deletion.sqlite3"
            ledger = FilesystemDeletionLedger(ledger_path, LEDGER_KEY, "physical-e2e-v1")
            ledger.initialize(coverage_started_at=datetime.now(UTC) - timedelta(days=2))
            settings = ApiSettings(
                database_url=SecretStr(sqlalchemy_url),
                auth_signing_secret=SecretStr(AUTH_SECRET),
                public_access_source_hmac_secret=SecretStr(
                    "account-deletion-source-hmac-secret-at-least-32-bytes"
                ),
                host="127.0.0.1",
                port=port,
                vault_root=temporary_root / "vault",
                vault_low_disk_bytes=0,
                profile_identity_private_key_pem=SecretStr(pem),
                profile_label_hint="Account deletion disposable",
                profile_api_origin=origin,
                profile_stream_origin=origin,
                account_recovery_enabled=True,
                account_deletion_enabled=True,
                privacy_ledger_path=ledger_path,
                privacy_ledger_key=SecretStr(LEDGER_KEY.decode()),
                privacy_ledger_key_id="physical-e2e-v1",
            )
            engine = create_runtime_engine(settings)
            try:
                auth = build_auth_service(settings, engine)
                owner = auth.bootstrap_owner(
                    BootstrapOwnerCommand(
                        display_name="Deletion E2E owner",
                        device=DeviceDescription(
                            name="A55 deletion source",
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
                family_id = _promote_and_seed(
                    engine,
                    owner,
                    server_id=server_id,
                    identity_epoch=identity_epoch,
                    identity_thumbprint=identity_thumbprint,
                    recovery_code=recovery_code,
                )
                binding_commit_id = uuid.uuid4()
                wrapper = _AccountDeletionE2eApp(
                    create_app(settings),
                    engine,
                    owner.user_id,
                    owner.device_id,
                    {
                        "account_id": str(owner.user_id),
                        "device_id": str(owner.device_id),
                        "session_id": str(owner.session_id),
                        "session_family_id": str(family_id),
                        "server_instance_id": str(server_id),
                        "identity_epoch": identity_epoch,
                        "identity_thumbprint_sha256": identity_thumbprint.hex(),
                        "binding_commit_id": str(binding_commit_id),
                        "access_token": owner.access_token,
                        "refresh_token": owner.refresh_token,
                        "recovery_code": recovery_code,
                    },
                )
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
                    name="account-deletion-e2e-api",
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

                evidence = wrapper.evidence
                expected_suspension = {
                    "account_status": "DELETION_PENDING",
                    "authority_generation": 2,
                    "active_devices": 0,
                    "active_sessions": 0,
                    "request_state": "PENDING",
                    "request_revision": 1,
                }
                if evidence != {
                    "source_key_registrations": 1,
                    "deletion_request_calls": 1,
                    "suspension": expected_suspension,
                    "cancel_replies_dropped": 1,
                    "cancel_outcome_calls": 1,
                }:
                    raise RuntimeError("deletion/cancellation transport evidence mismatch")
                production_after = _production_fingerprint(adb, serial)
                if production_before != production_after:
                    raise RuntimeError("production package metadata changed during QA proof")

                with psycopg.connect(dsn) as connection:
                    account = connection.execute(
                        "SELECT status, authority_generation FROM account.user_account "
                        "WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    request = connection.execute(
                        "SELECT state, revision, cancel_operation_id IS NOT NULL, "
                        "cancel_before-requested_at FROM account.account_deletion_request "
                        "WHERE user_id=%s",
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
                    credential = connection.execute(
                        "SELECT generation FROM account.account_recovery_credential "
                        "WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    operation = connection.execute(
                        "SELECT count(*), min(kind), max(result_generation) "
                        "FROM account.account_recovery_operation WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    audits = connection.execute(
                        "SELECT array_agg(action ORDER BY action) FROM audit.audit_event "
                        "WHERE actor_user_id=%s AND action IN "
                        "('account_deletion.requested','account_recovery.delete_cancel')",
                        (owner.user_id,),
                    ).fetchone()
                    active_owners = connection.execute(
                        "SELECT count(*) FROM account.user_account "
                        "WHERE role='OWNER' AND status='ACTIVE'"
                    ).fetchone()
                if account != ("ACTIVE", 3):
                    raise RuntimeError("cancelled account authority mismatch")
                if request is None or request[:3] != ("CANCELLED", 2, True):
                    raise RuntimeError("deletion request did not become cancelled revision 2")
                if request[3] != timedelta(days=30):
                    raise RuntimeError("deletion cancellation window changed")
                if devices != (1, 1) or sessions != (1, 1):
                    raise RuntimeError("deletion cancellation binding counts mismatch")
                if credential != (2,) or operation != (1, "DELETE_CANCEL", 2):
                    raise RuntimeError("deletion cancellation recovery rotation mismatch")
                expected_audits = [
                    "account_deletion.requested",
                    "account_recovery.delete_cancel",
                ]
                if audits is None or list(audits[0]) != expected_audits:
                    raise RuntimeError("deletion cancellation audit mismatch")
                if active_owners != (2,):
                    raise RuntimeError("remaining owner protection fixture changed")
                request_history = ledger.request_read()
                if len(request_history) != 1:
                    raise RuntimeError("independent deletion request evidence mismatch")
                history = request_history[0]
                if history.cancel_operation_id is None or history.cancelled_at is None:
                    raise RuntimeError("independent deletion cancellation evidence missing")

                finished = datetime.now(UTC)
                report: dict[str, object] = {
                    "schema_version": 1,
                    "status": "PASS",
                    "started_at": started.isoformat(),
                    "finished_at": finished.isoformat(),
                    "duration_seconds": round((finished - started).total_seconds(), 3),
                    "device_serial_sha256": _serial_sha256(serial),
                    "transport": (
                        "Android production deletion/cancellation runtimes, OkHttp and Keystore -> "
                        "FastAPI -> PostgreSQL 18.4/pgvector + independent SQLite ledger"
                    ),
                    "one_shot_handoff": (
                        "loopback-only and consumed once; no account ID, recovery code, access "
                        "token, refresh token, or binding identifier in Gradle arguments or report"
                    ),
                    "transport_evidence": evidence,
                    "final_account": {
                        "status": account[0],
                        "authority_generation": account[1],
                    },
                    "deletion_request": {
                        "state": request[0],
                        "revision": request[1],
                        "cancel_operation_present": request[2],
                        "cancel_window_days": request[3].days,
                    },
                    "binding_counts": {
                        "active_devices": devices[0],
                        "revoked_devices": devices[1],
                        "active_sessions": sessions[0],
                        "revoked_sessions": sessions[1],
                    },
                    "recovery": {
                        "generation": credential[0],
                        "operation_count": operation[0],
                        "operation_kind": operation[1],
                    },
                    "independent_ledger": {
                        "request_count": len(request_history),
                        "cancellation_recorded": True,
                    },
                    "active_owner_count": active_owners[0],
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
        / "ACCOUNT_DELETION_ANDROID_E2E.json",
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
