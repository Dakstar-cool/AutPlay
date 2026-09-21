"""Run OWNER/ADMIN/USER Android QR self-pairing against disposable PostgreSQL.

The physical recipient uses the production Android runtime, OkHttp, Android Keystore,
FastAPI, and PostgreSQL.  Source approval is coordinated through the production server
application service.  Each QR is delivered once over an adb-reversed loopback route;
no pairing secret, account identifier, or bearer is placed in Gradle arguments.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict, cast

import psycopg
import rfc8785
import uvicorn
from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.account_recovery import AccountRecoveryCredentialRow
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.auth import BootstrapOwnerCommand
from autplay.domain.auth import AccountRole, DeviceDescription, DevicePlatform, Principal
from autplay.domain.profile_pairing import canonical_sha256, public_spki
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import (
    build_auth_service,
    build_profile_pairing_service,
    build_self_device_pairing_service,
)
from autplay.runtime.settings import ApiSettings
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from p14_drill import REPOSITORY_ROOT as REPOSITORY_ROOT
from p14_drill import _project_name, _start, _stop, _upgrade
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

AUTH_SECRET = "self-pairing-roles-disposable-signing-secret-v1"
TEST_SELECTOR = (
    "app.autplay.selfpairing.SelfPairingRoleE2eTest#"
    "pairsActiveRoleThroughProductionRuntimeAndAndroidKeystore"
)
LOST_EXCHANGE_STAGE_SELECTOR = (
    "app.autplay.selfpairing.SelfPairingLostExchangeE2eTest#"
    "committedExchangeLostReplyLeavesEncryptedPendingJournal"
)
LOST_EXCHANGE_RESUME_SELECTOR = (
    "app.autplay.selfpairing.SelfPairingLostExchangeE2eTest#"
    "freshProcessReplaysExactExchangeAndMaterializesBinding"
)
QA_APPLICATION_ID = "app.autplay.qa"
QA_TEST_APPLICATION_ID = "app.autplay.qa.test"
PRODUCTION_APPLICATION_ID = "app.autplay"
ROLES = (AccountRole.OWNER, AccountRole.ADMIN, AccountRole.USER)


@dataclass(frozen=True, slots=True)
class RoleActor:
    role: AccountRole
    principal: Principal
    recovery_verifier: bytes


class IdentityDocument(TypedDict):
    expected_server_instance_id: str
    expected_identity_epoch: int
    expected_identity_thumbprint_sha256: str
    expected_api_origin: str
    expected_stream_origin: str


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
    raise RuntimeError("self-pairing E2E API did not become ready")


def _serial_sha256(serial: str) -> str:
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_apk_package(aapt: Path, apk: Path, expected: str) -> None:
    result = subprocess.run(
        [str(aapt), "dump", "badging", str(apk)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    if f"package: name='{expected}' " not in first_line:
        raise RuntimeError("self-pairing side-by-side APK identity mismatch")


def _production_fingerprint(adb: Path, serial: str) -> dict[str, object]:
    result = subprocess.run(
        [str(adb), "-s", serial, "shell", "dumpsys", "package", PRODUCTION_APPLICATION_ID],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if "Unable to find package" in result.stdout:
        return {"installed": False}

    def value(pattern: str) -> str:
        match = re.search(pattern, result.stdout)
        if match is None:
            raise RuntimeError("production package fingerprint is incomplete")
        return match.group(1).strip()

    return {
        "installed": True,
        "version_code": value(r"versionCode=(\d+)"),
        "version_name": value(r"versionName=([^\r\n]+)"),
        "last_update_time": value(r"lastUpdateTime=([^\r\n]+)"),
    }


class _OneShotRoleHandoffApp:
    """Adds one loopback-only handoff without adding a production API route."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._handoff: bytes | None = None
        self._lock = threading.Lock()
        self._drop_next_exchange_reply = False
        self._exchange_attempts = 0
        self._committed_exchange_replies_dropped = 0

    @property
    def exchange_attempts(self) -> int:
        with self._lock:
            return self._exchange_attempts

    @property
    def committed_exchange_replies_dropped(self) -> int:
        with self._lock:
            return self._committed_exchange_replies_dropped

    def drop_next_successful_exchange_reply(self) -> None:
        with self._lock:
            if self._drop_next_exchange_reply:
                raise RuntimeError("exchange reply drop is already armed")
            self._drop_next_exchange_reply = True

    def set_handoff(self, role: AccountRole, qr: bytes) -> None:
        document = {
            "role": role.value,
            "qr_payload_b64": base64.b64encode(qr).decode("ascii"),
        }
        with self._lock:
            if self._handoff is not None:
                raise RuntimeError("previous self-pairing handoff was not consumed")
            self._handoff = json.dumps(document, separators=(",", ":")).encode("utf-8")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] == "/self-pairing-e2e/one-shot-handoff":
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
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and "/pairing/self-service/" in scope["path"]
            and scope["path"].endswith("/exchange")
        ):
            with self._lock:
                self._exchange_attempts += 1
                drop = self._drop_next_exchange_reply
            if drop:
                messages: list[dict[str, Any]] = []

                async def collect(message: dict[str, Any]) -> None:
                    messages.append(message)

                await self._app(scope, receive, collect)
                status = next(
                    (
                        int(message["status"])
                        for message in messages
                        if message["type"] == "http.response.start"
                    ),
                    500,
                )
                if 200 <= status < 300:
                    with self._lock:
                        self._drop_next_exchange_reply = False
                        self._committed_exchange_replies_dropped += 1
                    body = b'{"error":{"code":"capability_missing"}}'
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 503,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode("ascii")),
                                (b"cache-control", b"no-store"),
                                (b"pragma", b"no-cache"),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return
                for message in messages:
                    await send(message)
                return
        await self._app(scope, receive, send)


def _request(
    kind: str,
    identity: Mapping[str, object],
    ceremony_id: uuid.UUID,
    fields: dict[str, object],
) -> dict[str, object]:
    document: dict[str, object] = {
        "contract_version": "v1",
        "schema_version": 1,
        "ceremony_id": str(ceremony_id),
        "requested_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **identity,
        **fields,
    }
    document["request_sha256"] = canonical_sha256(document).hex()
    if kind not in {"start", "decision"}:
        raise ValueError("source coordinator may create only start and decision documents")
    return document


def _prepare_actors(
    engine: Any,
    owner: Any,
    identity: IdentityDocument,
) -> tuple[RoleActor, ...]:
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime.now(UTC)
    actors: list[RoleActor] = []
    with sessions.begin() as session:
        owner_session = session.get(UserSessionRow, owner.session_id)
        if owner_session is None:
            raise RuntimeError("bootstrap owner session is missing")
        owner_session.session_mode = "V2"
        owner_session.family_id = owner.session_id
        owner_session.generation = 0
        entries: list[tuple[AccountRole, uuid.UUID, uuid.UUID, uuid.UUID]] = [
            (AccountRole.OWNER, owner.user_id, owner.device_id, owner.session_id)
        ]
        source_keys: dict[uuid.UUID, bytes] = {}
        for role in (AccountRole.ADMIN, AccountRole.USER):
            user_id, device_id, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            session.add(
                UserAccountRow(
                    user_id=user_id,
                    display_name=f"Self-pairing {role.value}",
                    role=role.value,
                )
            )
            source_keys[device_id] = public_spki(ec.generate_private_key(ec.SECP256R1()))
            entries.append((role, user_id, device_id, session_id))
        # The mappings intentionally expose no ORM relationships, so establish the
        # cross-table insertion order explicitly at each foreign-key boundary.
        session.flush()

        for role, user_id, device_id, _ in entries[1:]:
            source_spki = source_keys[device_id]
            session.add(
                DeviceRow(
                    device_id=device_id,
                    user_id=user_id,
                    device_name=f"{role.value} source phone",
                    platform="ANDROID",
                    app_version="physical-e2e",
                    public_key=source_spki,
                    public_key_thumbprint_sha256=hashlib.sha256(source_spki).digest(),
                )
            )
        session.flush()

        for _, user_id, device_id, session_id in entries[1:]:
            session.add(
                UserSessionRow(
                    session_id=session_id,
                    user_id=user_id,
                    device_id=device_id,
                    refresh_token_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                    issued_at=now,
                    expires_at=now + timedelta(days=90),
                    family_id=session_id,
                    generation=0,
                    session_mode="V2",
                )
            )
        session.flush()

        for role, user_id, device_id, session_id in entries:
            verifier = secrets.token_bytes(32)
            session.add(
                AccountRecoveryCredentialRow(
                    user_id=user_id,
                    server_instance_id=uuid.UUID(str(identity["expected_server_instance_id"])),
                    identity_epoch=identity["expected_identity_epoch"],
                    identity_thumbprint_sha256=bytes.fromhex(
                        str(identity["expected_identity_thumbprint_sha256"])
                    ),
                    generation=1,
                    verifier_sha256=verifier,
                    created_at=now,
                    updated_at=now,
                )
            )
            actors.append(
                RoleActor(
                    role,
                    Principal(user_id, device_id, session_id, role),
                    verifier,
                )
            )
    return tuple(actors)


def _start_ceremony(
    service: Any,
    actor: RoleActor,
    identity: IdentityDocument,
) -> tuple[uuid.UUID, bytes]:
    ceremony_id = uuid.uuid4()
    rendezvous = secrets.token_urlsafe(32)
    start = _request(
        "start",
        identity,
        ceremony_id,
        {
            "operation_id": str(uuid.uuid4()),
            "rendezvous_secret_sha256": hashlib.sha256(rendezvous.encode("ascii")).hexdigest(),
        },
    )
    status = service.start(actor.principal, start)
    qr = rfc8785.dumps(
        {
            "format": "autplay-self-device-pairing",
            "version": 1,
            "ceremony_id": str(ceremony_id),
            "expires_at": status["expires_at"],
            "rendezvous_secret": rendezvous,
            "expected_server_instance_id": identity["expected_server_instance_id"],
            "expected_identity_epoch": identity["expected_identity_epoch"],
            "expected_identity_thumbprint_sha256": identity["expected_identity_thumbprint_sha256"],
            "expected_api_origin": identity["expected_api_origin"],
            "expected_stream_origin": identity["expected_stream_origin"],
        }
    )
    return ceremony_id, qr


def _approve_when_claimed(
    service: Any,
    actor: RoleActor,
    identity: IdentityDocument,
    ceremony_id: uuid.UUID,
    errors: list[BaseException],
) -> None:
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status = service.status(actor.principal, ceremony_id)
            if status["state"] == "CLAIMED":
                decision = _request(
                    "decision",
                    identity,
                    ceremony_id,
                    {
                        "operation_id": str(uuid.uuid4()),
                        "action": "APPROVE",
                        "expected_revision": status["revision"],
                        "claim_id": status["claim_id"],
                        "claim_request_sha256": status["claim_request_sha256"],
                        "comparison_code": status["comparison_code"],
                    },
                )
                service.decide(actor.principal, decision)
                return
            if status["state"] in {"APPROVED", "EXCHANGED"}:
                return
            time.sleep(0.1)
        raise RuntimeError("source approval timed out before recipient claim")
    except BaseException as error:
        errors.append(error)


def _build_and_install(
    java_home: Path,
    android_home: Path,
    serial: str,
) -> tuple[Path, Path, Path, dict[str, str]]:
    adb = android_home / "platform-tools" / "adb.exe"
    aapt = android_home / "build-tools" / "36.1.0" / "aapt.exe"
    if not adb.is_file() or not aapt.is_file():
        raise RuntimeError("Android platform/build tools are unavailable")
    subprocess.run(
        [
            str(REPOSITORY_ROOT / "gradlew.bat"),
            f"-Dorg.gradle.java.home={java_home}",
            "--no-daemon",
            "--console=plain",
            "-Pautplay.qaSideBySide=true",
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
    for apk, expected_package in (
        (target_apk, QA_APPLICATION_ID),
        (test_apk, QA_TEST_APPLICATION_ID),
    ):
        _assert_apk_package(aapt, apk, expected_package)
        subprocess.run(
            [str(adb), "-s", serial, "install", "-r", "-t", str(apk)],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    return (
        adb,
        target_apk,
        test_apk,
        {"qa_apk": _file_sha256(target_apk), "qa_test_apk": _file_sha256(test_apk)},
    )


def _run_role(adb: Path, serial: str, port: int) -> None:
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
            "selfPairingE2eBaseUrl",
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
        raise RuntimeError("self-pairing Android instrumentation failed:\n" + safe)


def _run_lost_exchange_role(adb: Path, serial: str, port: int) -> dict[str, object]:
    cleared = subprocess.run(
        [str(adb), "-s", serial, "shell", "pm", "clear", QA_APPLICATION_ID],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if "Success" not in cleared.stdout:
        raise RuntimeError("QA application data was not cleared")

    def stage(selector: str) -> None:
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
                selector,
                "-e",
                "selfPairingE2eBaseUrl",
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
            raise RuntimeError("self-pairing lost-exchange instrumentation failed:\n" + safe)

    stage(LOST_EXCHANGE_STAGE_SELECTOR)
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
    stage(LOST_EXCHANGE_RESUME_SELECTOR)
    return {
        "force_stop_issued": True,
        "process_exit_confirmed": True,
        "fresh_instrumentation_resume": True,
    }


def _write_hash_manifest(output: Path) -> str:
    digest = _file_sha256(output)
    manifest = output.with_suffix(".sha256.json")
    manifest.write_text(
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
    project = _project_name("self-pairing-roles-e2e")
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    adb: Path | None = None
    port: int | None = None
    started = datetime.now(UTC)
    output.unlink(missing_ok=True)
    output.with_suffix(".sha256.json").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="autplay-self-pairing-roles-e2e-") as temporary:
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
                    "self-pairing-source-hmac-secret-at-least-32-bytes"
                ),
                host="127.0.0.1",
                port=port,
                vault_root=Path(temporary) / "vault",
                vault_low_disk_bytes=0,
                profile_identity_private_key_pem=SecretStr(pem),
                profile_label_hint="Self-pairing roles disposable",
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
                        display_name="Self-pairing OWNER",
                        device=DeviceDescription(
                            name="OWNER source phone",
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
                actors = _prepare_actors(engine, owner, identity)
                pairing = build_self_device_pairing_service(settings, engine)
                if pairing is None:
                    raise RuntimeError("self-pairing service unavailable")

                wrapper = _OneShotRoleHandoffApp(create_app(settings))
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
                    name="self-pairing-roles-api",
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

                completed_roles: list[str] = []
                lost_exchange_process_death: dict[str, object] | None = None
                for actor in actors:
                    ceremony_id, qr = _start_ceremony(pairing, actor, identity)
                    wrapper.set_handoff(actor.role, qr)
                    qr = b""
                    approval_errors: list[BaseException] = []
                    approval = threading.Thread(
                        target=_approve_when_claimed,
                        args=(pairing, actor, identity, ceremony_id, approval_errors),
                        name=f"self-pairing-{actor.role.value.lower()}-approval",
                        daemon=True,
                    )
                    approval.start()
                    if actor.role is AccountRole.OWNER:
                        wrapper.drop_next_successful_exchange_reply()
                        lost_exchange_process_death = _run_lost_exchange_role(
                            adb,
                            serial,
                            port,
                        )
                    else:
                        _run_role(adb, serial, port)
                    approval.join(timeout=15)
                    if approval.is_alive():
                        raise RuntimeError("source approval did not finish")
                    if approval_errors:
                        raise RuntimeError("source approval failed") from approval_errors[0]
                    status = pairing.status(actor.principal, ceremony_id)
                    if status["state"] != "EXCHANGED":
                        raise RuntimeError("self-pairing ceremony was not durably exchanged")
                    completed_roles.append(actor.role.value)

                if lost_exchange_process_death is None:
                    raise RuntimeError("OWNER lost-exchange process-death proof did not run")
                if wrapper.exchange_attempts != 4:
                    raise RuntimeError("self-pairing exchange attempt evidence mismatch")
                if wrapper.committed_exchange_replies_dropped != 1:
                    raise RuntimeError("committed exchange reply-drop evidence mismatch")

                production_after = _production_fingerprint(adb, serial)
                if production_before != production_after:
                    raise RuntimeError("production package metadata changed during QA proof")

                with psycopg.connect(dsn) as connection:
                    role_rows = connection.execute(
                        "SELECT u.role, p.state, count(*) "
                        "FROM account.self_device_pairing p "
                        "JOIN account.user_account u ON u.user_id=p.user_id "
                        "GROUP BY u.role, p.state ORDER BY u.role"
                    ).fetchall()
                    device_counts = connection.execute(
                        "SELECT u.role, count(*) "
                        "FROM account.device d JOIN account.user_account u ON u.user_id=d.user_id "
                        "WHERE d.revoked_at IS NULL GROUP BY u.role ORDER BY u.role"
                    ).fetchall()
                    recovery_rows = connection.execute(
                        "SELECT user_id, generation, verifier_sha256 "
                        "FROM account.account_recovery_credential"
                    ).fetchall()
                    recovery_operations = connection.execute(
                        "SELECT count(*) FROM account.account_recovery_operation"
                    ).fetchone()
                    audit_count = connection.execute(
                        "SELECT count(*) FROM audit.audit_event "
                        "WHERE action IN ('self_pairing.started','self_pairing.claimed',"
                        "'self_pairing.approve','self_pairing.exchanged')"
                    ).fetchone()

                expected_verifiers = {
                    actor.principal.user_id: actor.recovery_verifier for actor in actors
                }
                recovery_unchanged = len(recovery_rows) == 3 and all(
                    generation == 1 and expected_verifiers.get(user_id) == bytes(verifier)
                    for user_id, generation, verifier in recovery_rows
                )
                if sorted(role_rows) != [
                    ("ADMIN", "EXCHANGED", 1),
                    ("OWNER", "EXCHANGED", 1),
                    ("USER", "EXCHANGED", 1),
                ]:
                    raise RuntimeError("role-specific durable ceremony evidence mismatch")
                if sorted(device_counts) != [("ADMIN", 2), ("OWNER", 2), ("USER", 2)]:
                    raise RuntimeError("role-specific active device evidence mismatch")
                if not recovery_unchanged or recovery_operations != (0,):
                    raise RuntimeError("self-pairing changed recovery authority")
                if audit_count != (12,):
                    raise RuntimeError("self-pairing audit evidence mismatch")

                finished = datetime.now(UTC)
                report: dict[str, object] = {
                    "schema_version": 1,
                    "status": "PASS",
                    "started_at": started.isoformat(),
                    "finished_at": finished.isoformat(),
                    "duration_seconds": round((finished - started).total_seconds(), 3),
                    "device_serial_sha256": _serial_sha256(serial),
                    "roles": completed_roles,
                    "transport": (
                        "Android production self-pairing runtime/OkHttp/Keystore -> "
                        "FastAPI -> PostgreSQL 18.4/pgvector"
                    ),
                    "one_shot_handoff": (
                        "loopback-only and consumed once per role; no QR secret, account ID, "
                        "or bearer in Gradle arguments"
                    ),
                    "android_binding_commits_verified": len(completed_roles),
                    "lost_exchange_process_death": {
                        **lost_exchange_process_death,
                        "role": AccountRole.OWNER.value,
                        "committed_success_replies_dropped": (
                            wrapper.committed_exchange_replies_dropped
                        ),
                        "owner_exchange_attempts": 2,
                        "all_role_exchange_attempts": wrapper.exchange_attempts,
                        "encrypted_pending_journal_replayed": True,
                        "replayed_owner_binding_materialized": True,
                    },
                    "durable_counts": {
                        "exchanged_ceremonies": sum(row[2] for row in role_rows),
                        "active_devices": sum(row[1] for row in device_counts),
                        "self_pairing_audit_events": audit_count[0],
                    },
                    "recovery_authority": {
                        "credential_count": len(recovery_rows),
                        "generation": 1,
                        "verifier_unchanged": recovery_unchanged,
                        "operations": recovery_operations[0],
                    },
                    "production_package": {**production_after, "unchanged": True},
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
        / "SELF_PAIRING_ROLES_ANDROID_E2E.json",
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
