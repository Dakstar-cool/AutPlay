"""Prove the five-device self-pairing quota on a physical Android recipient."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import psycopg
import uvicorn
from autplay.adapters.postgresql.models import DeviceRow, UserSessionRow
from autplay.adapters.postgresql.models.account_recovery import AccountRecoveryCredentialRow
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.auth import BootstrapOwnerCommand
from autplay.domain.auth import AccountRole, DeviceDescription, DevicePlatform, Principal
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import (
    build_auth_service,
    build_profile_pairing_service,
    build_self_device_pairing_service,
)
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
    IdentityDocument,
    RoleActor,
    _approve_when_claimed,
    _build_and_install,
    _file_sha256,
    _free_port,
    _OneShotRoleHandoffApp,
    _production_fingerprint,
    _serial_sha256,
    _start_ceremony,
    _wait_ready,
)
from sqlalchemy.orm import Session, sessionmaker

AUTH_SECRET = "self-pairing-device-quota-signing-secret-v1"
TEST_SELECTOR = (
    "app.autplay.selfpairing.SelfPairingDeviceQuotaE2eTest#"
    "sixthDeviceIsRejectedAndEncryptedRetryRemainsPending"
)
RESUME_TEST_SELECTOR = (
    "app.autplay.selfpairing.SelfPairingDeviceQuotaE2eTest#"
    "pendingQuotaRefusalSurvivesProcessRestartAndExactRetry"
)
UI_TEST_SELECTOR = (
    "app.autplay.ui.profilepairing.SelfPairingCardTest#quotaRefusalIsLocalizedPendingAndRetryable"
)
UI_SCREENSHOTS = (
    "self-pairing-quota-ru-dark.png",
    "self-pairing-quota-en-dark.png",
)


class _QuotaHandoffApp:
    """Add the one-shot handoff and count recipient exchange attempts."""

    def __init__(self, app: Any) -> None:
        self._handoff = _OneShotRoleHandoffApp(app)
        self._exchange_attempts = 0
        self._lock = threading.Lock()

    @property
    def exchange_attempts(self) -> int:
        with self._lock:
            return self._exchange_attempts

    def set_handoff(self, role: AccountRole, qr: bytes) -> None:
        self._handoff.set_handoff(role, qr)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"].endswith("/exchange")
        ):
            with self._lock:
                self._exchange_attempts += 1
        await self._handoff(scope, receive, send)


def _seed_full_device_quota(
    engine: Any,
    owner: Any,
    identity: IdentityDocument,
) -> RoleActor:
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime.now(UTC)
    verifier = secrets.token_bytes(32)
    with sessions.begin() as session:
        owner_session = session.get(UserSessionRow, owner.session_id)
        if owner_session is None:
            raise RuntimeError("bootstrap owner session is missing")
        owner_session.session_mode = "V2"
        owner_session.family_id = owner.session_id
        owner_session.generation = 0
        for index in range(2, 6):
            device_id, session_id = uuid.uuid4(), uuid.uuid4()
            session.add(
                DeviceRow(
                    device_id=device_id,
                    user_id=owner.user_id,
                    device_name=f"Quota device {index}",
                    platform="ANDROID",
                    app_version="physical-e2e",
                )
            )
            session.flush()
            session.add(
                UserSessionRow(
                    session_id=session_id,
                    user_id=owner.user_id,
                    device_id=device_id,
                    refresh_token_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                    issued_at=now,
                    expires_at=now + timedelta(days=90),
                    family_id=session_id,
                    generation=0,
                    session_mode="V2",
                )
            )
        session.add(
            AccountRecoveryCredentialRow(
                user_id=owner.user_id,
                server_instance_id=uuid.UUID(str(identity["expected_server_instance_id"])),
                identity_epoch=identity["expected_identity_epoch"],
                identity_thumbprint_sha256=bytes.fromhex(
                    identity["expected_identity_thumbprint_sha256"]
                ),
                generation=1,
                verifier_sha256=verifier,
                created_at=now,
                updated_at=now,
            )
        )
    return RoleActor(
        AccountRole.OWNER,
        Principal(owner.user_id, owner.device_id, owner.session_id, AccountRole.OWNER),
        verifier,
    )


def _run_android(adb: Path, serial: str, port: int) -> dict[str, object]:
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
            "selfPairingQuotaE2eBaseUrl",
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
        raise RuntimeError("self-pairing quota Android instrumentation failed:\n" + safe)

    subprocess.run(
        [str(adb), "-s", serial, "shell", "am", "force-stop", QA_APPLICATION_ID],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    process = subprocess.run(
        [str(adb), "-s", serial, "shell", "pidof", QA_APPLICATION_ID],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if process.stdout.strip():
        raise RuntimeError("QA application process remained alive after force-stop")

    resumed = subprocess.run(
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
            RESUME_TEST_SELECTOR,
            "-e",
            "selfPairingQuotaE2eBaseUrl",
            f"http://127.0.0.1:{port}",
            f"{QA_TEST_APPLICATION_ID}/androidx.test.runner.AndroidJUnitRunner",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if resumed.returncode != 0 or "OK (1 test)" not in resumed.stdout:
        safe = resumed.stdout.replace(f"http://127.0.0.1:{port}", "<loopback>")[-4_000:]
        raise RuntimeError("self-pairing quota resume instrumentation failed:\n" + safe)

    ui_instrumentation = subprocess.run(
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
            UI_TEST_SELECTOR,
            f"{QA_TEST_APPLICATION_ID}/androidx.test.runner.AndroidJUnitRunner",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if ui_instrumentation.returncode != 0 or "OK (1 test)" not in ui_instrumentation.stdout:
        raise RuntimeError(
            "self-pairing quota UI instrumentation failed:\n" + ui_instrumentation.stdout[-4_000:]
        )
    return {
        "force_stop_issued": True,
        "process_exit_confirmed": True,
        "fresh_instrumentation_resume": True,
    }


def _pull_ui_evidence(adb: Path, serial: str, output: Path) -> dict[str, str]:
    directory = output.parent / "self-pairing-device-quota-ui"
    directory.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name in UI_SCREENSHOTS:
        destination = directory / name
        destination.unlink(missing_ok=True)
        subprocess.run(
            [
                str(adb),
                "-s",
                serial,
                "pull",
                f"/sdcard/Android/data/{QA_APPLICATION_ID}/files/{name}",
                str(destination),
            ],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("self-pairing quota UI evidence is missing")
        relative = destination.relative_to(output.parent).as_posix()
        hashes[relative] = str(_file_sha256(destination))
    return hashes


def _write_hash_manifest(output: Path, supplemental: dict[str, str]) -> str:
    digest = str(_file_sha256(output))
    output.with_suffix(".sha256.json").write_text(
        json.dumps(
            {"schema_version": 1, "files": {output.name: digest, **supplemental}},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return digest


def run(*, java_home: Path, android_home: Path, serial: str, output: Path) -> dict[str, object]:
    project = _project_name("self-pairing-device-quota-e2e")
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    approval: threading.Thread | None = None
    approval_errors: list[BaseException] = []
    adb: Path | None = None
    port: int | None = None
    started = datetime.now(UTC)
    output.unlink(missing_ok=True)
    output.with_suffix(".sha256.json").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="autplay-self-pairing-quota-e2e-") as temporary:
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
                    "self-pairing-quota-source-secret-at-least-32-bytes"
                ),
                host="127.0.0.1",
                port=port,
                vault_root=Path(temporary) / "vault",
                vault_low_disk_bytes=0,
                profile_identity_private_key_pem=SecretStr(pem),
                profile_label_hint="Self-pairing quota disposable",
                profile_api_origin=origin,
                profile_stream_origin=origin,
                self_device_pairing_enabled=True,
                account_recovery_enabled=True,
            )
            engine = create_runtime_engine(settings)
            try:
                auth = build_auth_service(settings, engine)
                owner = auth.bootstrap_owner(
                    BootstrapOwnerCommand(
                        display_name="Self-pairing quota owner",
                        device=DeviceDescription(
                            name="Quota source phone",
                            platform=DevicePlatform.ANDROID,
                            app_version="physical-e2e",
                        ),
                    )
                )
                profile = build_profile_pairing_service(settings, engine)
                if profile is None:
                    raise RuntimeError("profile identity service unavailable")
                discovery = cast(dict[str, object], profile.discovery()["payload"])
                identity = IdentityDocument(
                    expected_server_instance_id=str(discovery["server_instance_id"]),
                    expected_identity_epoch=int(str(discovery["identity_epoch"])),
                    expected_identity_thumbprint_sha256=str(
                        discovery["identity_thumbprint_sha256"]
                    ),
                    expected_api_origin=str(discovery["api_origin"]),
                    expected_stream_origin=str(discovery["stream_origin"]),
                )
                actor = _seed_full_device_quota(engine, owner, identity)
                pairing = build_self_device_pairing_service(settings, engine)
                if pairing is None:
                    raise RuntimeError("self-pairing service unavailable")

                wrapper = _QuotaHandoffApp(create_app(settings))
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
                    name="self-pairing-quota-api",
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

                ceremony_id, qr = _start_ceremony(pairing, actor, identity)
                wrapper.set_handoff(AccountRole.OWNER, qr)
                qr = b""
                approval = threading.Thread(
                    target=_approve_when_claimed,
                    args=(pairing, actor, identity, ceremony_id, approval_errors),
                    name="self-pairing-quota-approval",
                    daemon=True,
                )
                approval.start()

                process_death = _run_android(adb, serial, port)
                ui_evidence = _pull_ui_evidence(adb, serial, output)
                approval.join(timeout=15)
                if approval.is_alive():
                    raise RuntimeError("source approval did not finish")
                if approval_errors:
                    raise RuntimeError("source approval failed") from approval_errors[0]
                status = pairing.status(actor.principal, ceremony_id)
                if status["state"] != "APPROVED":
                    raise RuntimeError("quota-refused ceremony did not remain approved")
                if wrapper.exchange_attempts != 2:
                    raise RuntimeError("recipient did not make exactly two exchange attempts")

                production_after = _production_fingerprint(adb, serial)
                if production_before != production_after:
                    raise RuntimeError("production package metadata changed during QA proof")
                with psycopg.connect(dsn) as connection:
                    policy = connection.execute(
                        "SELECT default_devices FROM account.resource_quota_policy"
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
                    ceremony = connection.execute(
                        "SELECT state, revision FROM account.self_device_pairing "
                        "WHERE ceremony_id=%s",
                        (ceremony_id,),
                    ).fetchone()
                    recovery = connection.execute(
                        "SELECT generation, verifier_sha256 FROM "
                        "account.account_recovery_credential WHERE user_id=%s",
                        (owner.user_id,),
                    ).fetchone()
                    recovery_operations = connection.execute(
                        "SELECT count(*) FROM account.account_recovery_operation"
                    ).fetchone()
                    audits = connection.execute(
                        "SELECT array_agg(action ORDER BY action) FROM audit.audit_event "
                        "WHERE action LIKE 'self_pairing.%'"
                    ).fetchone()
                if policy != (5,):
                    raise RuntimeError("default device quota is not five")
                if devices != (5, 0) or sessions != (5, 0):
                    raise RuntimeError("quota refusal changed existing device authority")
                if ceremony != ("APPROVED", 3):
                    raise RuntimeError("quota refusal changed ceremony state")
                if (
                    recovery is None
                    or recovery[0] != 1
                    or bytes(recovery[1]) != actor.recovery_verifier
                ):
                    raise RuntimeError("quota refusal changed recovery authority")
                if recovery_operations != (0,):
                    raise RuntimeError("quota refusal created a recovery operation")
                expected_audits = [
                    "self_pairing.approve",
                    "self_pairing.claimed",
                    "self_pairing.started",
                ]
                if audits is None or list(audits[0]) != expected_audits:
                    raise RuntimeError("quota refusal audit evidence mismatch")

                finished = datetime.now(UTC)
                report: dict[str, object] = {
                    "schema_version": 1,
                    "status": "PASS",
                    "started_at": started.isoformat(),
                    "finished_at": finished.isoformat(),
                    "duration_seconds": round((finished - started).total_seconds(), 3),
                    "device_serial_sha256": _serial_sha256(serial),
                    "transport": (
                        "Android production self-pairing runtime/OkHttp/Keystore -> "
                        "FastAPI -> PostgreSQL 18.4/pgvector"
                    ),
                    "refusal": {
                        "safe_code": "account_device_limit_reached",
                        "exact_exchange_attempts": wrapper.exchange_attempts,
                        "configured_device_limit": policy[0],
                    },
                    "durable_counts": {
                        "active_devices": devices[0],
                        "revoked_devices": devices[1],
                        "active_sessions": sessions[0],
                        "revoked_sessions": sessions[1],
                    },
                    "ceremony": {"state": ceremony[0], "revision": ceremony[1]},
                    "recovery_authority": {
                        "generation": recovery[0],
                        "verifier_unchanged": True,
                        "operations": recovery_operations[0],
                    },
                    "one_shot_handoff": (
                        "loopback-only and consumed once; no QR secret, account ID, "
                        "or bearer in Gradle arguments or report"
                    ),
                    "ui": {
                        "locales": ["en", "ru"],
                        "pending_retry_action": True,
                        "screenshots_sha256": ui_evidence,
                    },
                    "process_death": process_death,
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
                _write_hash_manifest(output, ui_evidence)
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
        / "SELF_PAIRING_DEVICE_QUOTA_ANDROID_E2E.json",
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
