"""Real signed Mac account/sync acceptance against an isolated, pinned PostgreSQL server.

Private test state stays under RUNNER_TEMP. Only aggregate evidence enters artifacts.
The isolated server uses the checked source registries without replacing qualification policy.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import macos_community as community

ROOT = Path(__file__).resolve().parents[1]
TEMP = community.TEMP / "account-acceptance"
PG = TEMP / "postgres"
DATA = TEMP / "pgdata"
DB_NAME = "autplay_mac_acceptance_synthetic"
PG_SOURCE = "https://ftp.postgresql.org/pub/source/v18.4/postgresql-18.4.tar.bz2"
PG_SHA = "81a81ec695fb0c7901407defaa1d2f7973617154cf27ba74e3a7ab8e64436094"
VECTOR_COMMIT = "8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c"
VECTOR_SOURCE = "https://codeload.github.com/pgvector/pgvector/tar.gz/" + VECTOR_COMMIT
VECTOR_SHA = "d076a3098010905fd60256649327809651f6288327db6413f0938305f62ea299"


def run(*args: str | Path, cwd: Path = ROOT, timeout: int = 180, env: dict | None = None) -> None:
    # Never print commands, DSNs or unfiltered API errors containing synthetic credentials.
    with (TEMP / "commands.private.log").open("ab") as log:
        result = subprocess.run(
            [str(x) for x in args], cwd=cwd, env=env, stdout=log, stderr=log, timeout=timeout
        )
    if result.returncode:
        raise RuntimeError(f"Acceptance command {Path(args[0]).name} failed ({result.returncode})")


def private_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def download(url: str, digest: str, name: str) -> Path:
    archive = TEMP / name
    with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as target:
        shutil.copyfileobj(response, target)
    with archive.open("rb") as checked:
        if hashlib.file_digest(checked, "sha256").hexdigest() != digest:
            raise RuntimeError("Pinned source archive mismatch")
    destination = TEMP / (name + "-source")
    destination.mkdir()
    with tarfile.open(archive) as source:
        source.extractall(destination, filter="data")
    roots = list(destination.iterdir())
    assert len(roots) == 1 and roots[0].is_dir()
    return roots[0]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_database() -> None:
    source = download(PG_SOURCE, PG_SHA, "postgresql.tar.bz2")
    run(
        source / "configure",
        "--prefix=" + str(PG),
        "--without-icu",
        "--without-readline",
        "--without-zlib",
        cwd=source,
    )
    run("make", "-j3", cwd=source, timeout=900)
    run("make", "install", cwd=source)
    run("make", "-C", "contrib/pg_trgm", "install", cwd=source)
    vector = download(VECTOR_SOURCE, VECTOR_SHA, "pgvector.tar.gz")
    run("make", "-j3", "PG_CONFIG=" + str(PG / "bin/pg_config"), "OPTFLAGS=", cwd=vector)
    run("make", "install", "PG_CONFIG=" + str(PG / "bin/pg_config"), cwd=vector)


def serve() -> None:
    """Child process uses unchanged production composition and actual authentication."""
    from autplay.domain import device_capability_state, device_compatibility, sync_binding_v2

    # Verify both producers use the released policy; this launcher cannot qualify a path.
    families = frozenset({"enrollment", "session", "portable"})
    platforms = frozenset({"ANDROID", "MACOS"})
    if (
        families != device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES
        or families != device_capability_state.IMPLEMENTED_AND_QUALIFIED_FAMILIES
        or platforms != sync_binding_v2.IMPLEMENTED_AND_QUALIFIED_PLATFORMS
        or platforms != device_capability_state.IMPLEMENTED_AND_QUALIFIED_PLATFORMS
    ):
        raise RuntimeError("Account acceptance requires the exact qualified source policy")
    private_json(
        TEMP / "source-qualification.json",
        {"d1_families": sorted(families), "b1_platforms": sorted(platforms)},
    )
    import uvicorn
    from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
    from autplay.entrypoints.api import create_app
    from autplay.runtime.settings import ApiSettings
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    from pydantic import SecretStr

    settings = json.loads((TEMP / "server.private.json").read_text())
    identity = ec.generate_private_key(ec.SECP256R1())
    tls = ec.generate_private_key(ec.SECP256R1())

    def pem(key):
        return key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    tls_key = TEMP / "tls.private.pem"
    tls_key.write_bytes(pem(tls))
    tls_key.chmod(0o600)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AutPlay isolated acceptance")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(tls.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=2))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=3))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(tls, hashes.SHA256())
    )
    certificate = TEMP / "tls.pem"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    origin = f"https://127.0.0.1:{settings['api_port']}"
    ledger_secret = secrets.token_urlsafe(48)
    # Establish independent evidence before the first acceptor on this new empty test server.
    FilesystemDeletionLedger(
        TEMP / "privacy.sqlite3", ledger_secret.encode(), "synthetic-ci-1"
    ).initialize()
    app = create_app(
        ApiSettings(
            database_url=SecretStr(settings["dsn"]),
            auth_signing_secret=SecretStr(secrets.token_urlsafe(48)),
            public_access_source_hmac_secret=SecretStr(secrets.token_urlsafe(48)),
            profile_identity_private_key_pem=SecretStr(pem(identity).decode()),
            profile_api_origin=origin,
            profile_stream_origin=origin,
            profile_label_hint="Isolated Mac acceptance",
            portable_accounts_enabled=True,
            device_compatibility_v2_enabled=True,
            sync_binding_v2_enabled=True,
            privacy_ledger_path=TEMP / "privacy.sqlite3",
            privacy_ledger_key=SecretStr(ledger_secret),
            privacy_ledger_key_id="synthetic-ci-1",
            vault_root=TEMP / "vault",
        )
    )

    @app.middleware("http")
    async def safe_delivery_evidence(request, call_next):
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        with (TEMP / "http-evidence.jsonl").open("a") as evidence:
            evidence.write(
                json.dumps(
                    {
                        "route_template": route,
                        "status": response.status_code,
                        "content_type": response.headers.get("content-type"),
                        "content_length": response.headers.get("content-length"),
                    }
                )
                + "\n"
            )
        return response

    discovery = app.state.profile_pairing_service.discovery()["payload"]
    private_json(
        TEMP / "client.private.json",
        {
            "origin": origin,
            "tls_spki_b64": base64.b64encode(
                tls.public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                )
            ).decode(),
            "instance": discovery["server_instance_id"],
            "identity_epoch": discovery["identity_epoch"],
            "identity_spki_b64": discovery["identity_public_key_spki_b64"],
            "public_id": "mac_ci_" + secrets.token_hex(6),
            "password": secrets.token_urlsafe(36),
        },
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=settings["api_port"],
        ssl_keyfile=str(tls_key),
        ssl_certfile=str(certificate),
        access_log=False,
        log_level="warning",
    )


def exercise_clients(config: Path, password: str) -> list[str]:
    """Four real process lifecycles with two isolated protected Keychains."""
    assert sys.platform == "darwin" and os.environ.get("GITHUB_ACTIONS") == "true"
    canonical = Path.home() / "Library/Application Support/AutPlay"
    old_data = TEMP / "preexisting-runner-data"
    saved_default = (
        community.run("security", "default-keychain", "-d", "user").stdout.strip().strip('"')
    )
    keys: list[Path] = []
    data_switched = False
    try:
        app = TEMP / "probe/AutPlay.app"
        shutil.copytree(community.APP, app)
        shutil.copyfile(
            ROOT / "apps/desktop/build/libs/account-acceptance.jar",
            app / "Contents/app/account-acceptance.jar",
        )
        cfg = app / "Contents/app/AutPlay.cfg"
        text, count = re.subn(
            r"^app.mainclass=.*$",
            "app.mainclass=app.autplay.desktop.CommunityAccountProbe",
            cfg.read_text(),
            flags=re.M,
        )
        assert count == 1
        text, count = re.subn(
            r"^app.classpath=",
            "app.classpath=$APPDIR/account-acceptance.jar:",
            text,
            count=1,
            flags=re.M,
        )
        assert count == 1
        cfg.write_text(text)
        community.sign(app)
        if canonical.exists():
            canonical.rename(old_data)
        data_switched = True
        for _device in ("a", "b"):
            key = (
                Path.home()
                / "Library/Keychains"
                / ("autplay-account-ci-" + secrets.token_hex(8) + ".keychain-db")
            )
            community.run("security", "create-keychain", "-p", password, key)
            keys.append(key)
            community.run("security", "set-keychain-settings", "-lut", "3600", key)
            community.run("security", "unlock-keychain", "-p", password, key)
        passed = []
        for phase, device in (("register", "a"), ("restart", "a"), ("login", "b"), ("pull", "a")):
            if phase == "login":
                canonical.rename(TEMP / "data-a")
            if phase == "pull":
                canonical.rename(TEMP / "data-b")
                (TEMP / "data-a").rename(canonical)
            key = keys[0 if device == "a" else 1]
            community.run("security", "default-keychain", "-d", "user", "-s", key)
            result = community.run(app / "Contents/MacOS/AutPlay", phase, config, timeout=360)
            if f"AUTPLAY_ACCOUNT_PROBE_PASSED {phase}" not in result.stdout:
                raise RuntimeError("Missing production account acceptance marker")
            passed.append(phase)
            print("Production Mac account acceptance passed: " + phase, flush=True)
        return passed
    finally:
        cleanup_errors = []
        try:
            community.run("security", "default-keychain", "-d", "user", "-s", saved_default)
        except Exception:
            cleanup_errors.append("default_keychain")
        for key in keys:
            try:
                community.run("security", "delete-keychain", key)
            except Exception:
                cleanup_errors.append("synthetic_keychain")
        if data_switched:
            try:
                if canonical.exists():
                    canonical.rename(TEMP / "data-final")
                if old_data.exists():
                    old_data.rename(canonical)
            except Exception:
                cleanup_errors.append("original_data")
        if cleanup_errors:
            raise RuntimeError("Account acceptance cleanup failed: " + ",".join(cleanup_errors))


def validate_live_target(raw: bytes, now: datetime) -> dict:
    """Validate operator-supplied, independently pinned deployment evidence.

    This input is prepared after deployment and the final restored backup. It is
    never derived from unauthenticated discovery and cannot contain account secrets.
    Validation does not itself prove that deployment or restoration occurred.
    """

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate field")
            value[key] = item
        return value

    try:
        if len(raw) > 16384:
            raise ValueError("size")
        target = json.loads(raw, object_pairs_hook=unique)
        required = {
            "kind",
            "origin",
            "tls_spki_b64",
            "instance",
            "identity_epoch",
            "identity_spki_b64",
            "server_source",
            "server_image",
            "schema_head",
            "deployment_receipt_sha256",
            "backup_restore_receipt_sha256",
            "production_deployed",
            "backup_restore_verified",
            "observed_at_utc",
        }
        if type(target) is not dict or set(target) != required:
            raise ValueError("fields")
        non_text = {"identity_epoch", "production_deployed", "backup_restore_verified"}
        if any(type(target[key]) is not str for key in required - non_text):
            raise ValueError("field type")
        if target["kind"] != "AUTPLAY_LIVE_9443_ACCEPTANCE":
            raise ValueError("kind")
        if (
            target["production_deployed"] is not True
            or target["backup_restore_verified"] is not True
        ):
            raise ValueError("deployment boundary")
        origin = urlsplit(target["origin"])
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.port != 9443
            or origin.username is not None
            or origin.password is not None
            or origin.path
            or origin.query
            or origin.fragment
            or not target["origin"].isascii()
            or any(ord(c) <= 32 for c in target["origin"])
            or "?" in target["origin"]
            or "#" in target["origin"]
        ):
            raise ValueError("origin")
        if str(UUID(target["instance"])) != target["instance"]:
            raise ValueError("identity")
        if (
            type(target["identity_epoch"]) is not int
            or not 1 <= target["identity_epoch"] <= 9007199254740991
        ):
            raise ValueError("epoch")
        if not re.fullmatch("[0-9a-f]{40}", target["server_source"]):
            raise ValueError("source")
        if not re.fullmatch("sha256:[0-9a-f]{64}", target["server_image"]):
            raise ValueError("image")
        if target["schema_head"] != "0074_device_deletion_v2":
            raise ValueError("schema")
        for key in ("deployment_receipt_sha256", "backup_restore_receipt_sha256"):
            if not re.fullmatch("[0-9a-f]{64}", target[key]):
                raise ValueError("receipt")
        for key in ("tls_spki_b64", "identity_spki_b64"):
            value = base64.b64decode(target[key], validate=True)
            if not 32 <= len(value) <= 2048 or base64.b64encode(value).decode() != target[key]:
                raise ValueError("public key")
        observed = datetime.fromisoformat(target["observed_at_utc"])
        if observed.tzinfo is None or not -120 <= (now - observed).total_seconds() <= 3600:
            raise ValueError("stale target observation")
        return target
    except ValueError, TypeError, KeyError, OverflowError:
        raise ValueError("Invalid live acceptance target evidence") from None


def live_acceptance() -> None:
    """Prepared live check; never starts, migrates, patches or configures the server."""
    assert sys.platform == "darwin" and os.environ.get("GITHUB_ACTIONS") == "true"
    assert not TEMP.exists(), "Live acceptance requires a fresh disposable hosted runner"
    target_path = Path(os.environ["AUTPLAY_LIVE_ACCEPTANCE_TARGET"])
    info = target_path.lstat()
    if (
        not target_path.is_file()
        or target_path.is_symlink()
        or info.st_mode & 0o077
        or info.st_uid != os.getuid()
    ):
        raise RuntimeError("Live target evidence must be an owner-only regular file")
    if info.st_size > 16384:
        raise RuntimeError("Live target evidence is too large")
    raw = target_path.read_bytes()
    target = validate_live_target(raw, datetime.now(UTC))
    if target["server_source"] != os.environ["GITHUB_SHA"]:
        raise RuntimeError("Live acceptance requires the exact deployed candidate source")
    TEMP.mkdir(mode=0o700)
    client = {
        key: target[key]
        for key in ("origin", "tls_spki_b64", "instance", "identity_epoch", "identity_spki_b64")
    }
    # No owner ID/password input exists. Each attempt creates its own bounded synthetic account.
    client.update(public_id="mac_live_" + secrets.token_hex(6), password=secrets.token_urlsafe(36))
    config = TEMP / "client.private.json"
    private_json(config, client)
    passed = exercise_clients(config, secrets.token_urlsafe(32))
    report = {
        "status": "passed",
        "scope": "LIVE_9443_SYNTHETIC_ACCOUNT",
        "source_commit": os.environ["GITHUB_SHA"],
        "checks": passed,
        "real_keychain": True,
        "real_https": True,
        "production_graph": True,
        "production_9443_verified": True,
        "synthetic_candidate_qualification": False,
        "target_evidence_sha256": hashlib.sha256(raw).hexdigest(),
        "target_identity_verified_by_client": True,
        "operator_deployment_evidence": {
            key: target[key]
            for key in (
                "server_source",
                "server_image",
                "schema_head",
                "deployment_receipt_sha256",
                "backup_restore_receipt_sha256",
                "observed_at_utc",
            )
        },
        "journal": "unchanged v1 protocol",
        "devices": 2,
    }
    (community.OUT / "live-account-acceptance.json").write_text(json.dumps(report, indent=2) + "\n")


def acceptance() -> None:
    assert sys.platform == "darwin" and os.environ.get("GITHUB_ACTIONS") == "true"
    assert not TEMP.exists(), "Acceptance requires a fresh disposable hosted runner"
    TEMP.mkdir(mode=0o700)
    build_database()
    password = secrets.token_urlsafe(32)
    pwfile = TEMP / "pg-password.private"
    pwfile.write_text(password)
    pwfile.chmod(0o600)
    run(
        PG / "bin/initdb",
        "-D",
        DATA,
        "-U",
        "autplay",
        "--auth=scram-sha-256",
        "--pwfile=" + str(pwfile),
        "--locale=C",
        "--encoding=UTF8",
    )
    dbport, apiport = free_port(), free_port()
    run(
        PG / "bin/pg_ctl",
        "-D",
        DATA,
        "-l",
        TEMP / "postgres.private.log",
        "-o",
        f"-h 127.0.0.1 -p {dbport} -k {TEMP}",
        "-w",
        "start",
    )
    api = None
    try:
        env = dict(
            os.environ,
            PGHOST="127.0.0.1",
            PGPORT=str(dbport),
            PGUSER="autplay",
            PGPASSWORD=password,
        )
        run(PG / "bin/createdb", DB_NAME, env=env)
        run("uv", "sync", "--project", "server", "--frozen", timeout=600)
        dsn = f"postgresql+psycopg://autplay:{password}@127.0.0.1:{dbport}/{DB_NAME}"
        python = ROOT / "server/.venv/bin/python"
        run(
            python,
            "-m",
            "alembic",
            "-c",
            "server/alembic.ini",
            "upgrade",
            "head",
            env=dict(os.environ, AUTPLAY_DATABASE_URL=dsn),
            timeout=240,
        )
        private_json(TEMP / "server.private.json", {"dsn": dsn, "api_port": apiport})
        api_log = (TEMP / "api.private.log").open("wb")
        api = subprocess.Popen(
            [str(python), str(Path(__file__).resolve()), "serve"], stdout=api_log, stderr=api_log
        )
        for _ in range(60):
            if api.poll() is not None:
                raise RuntimeError("Isolated API exited during startup")
            if (TEMP / "client.private.json").exists():
                context = ssl.create_default_context(cafile=str(TEMP / "tls.pem"))
                try:
                    with urllib.request.urlopen(
                        f"https://127.0.0.1:{apiport}/health/ready", context=context, timeout=2
                    ) as response:
                        if response.status == 200:
                            break
                except OSError, urllib.error.URLError:
                    pass
            time.sleep(1)
        else:
            raise RuntimeError("Isolated API readiness deadline")
        passed = exercise_clients(TEMP / "client.private.json", password)
        (community.OUT / "account-acceptance.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "source_commit": os.environ["GITHUB_SHA"],
                    "postgresql": "18.4",
                    "pgvector": "0.8.6",
                    "scope": "ISOLATED_SYNTHETIC_ACCOUNT",
                    "synthetic_candidate_qualification": False,
                    "source_qualification": json.loads(
                        (TEMP / "source-qualification.json").read_text()
                    ),
                    "production_9443_verified": False,
                    "production_graph": True,
                    "checks": passed,
                    "real_keychain": True,
                    "real_https": True,
                    "journal": "unchanged v1 protocol",
                    "devices": 2,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        if api is not None:
            api.terminate()
            try:
                api.wait(timeout=30)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait(timeout=10)
        run(PG / "bin/pg_ctl", "-D", DATA, "-m", "fast", "-w", "stop")
        if (TEMP / "http-evidence.jsonl").exists():
            # Only fixed route templates, status and response sizes; no bodies or credentials.
            shutil.copyfile(
                TEMP / "http-evidence.jsonl", community.OUT / "account-http-evidence.jsonl"
            )


if __name__ == "__main__":
    {"serve": serve, "run": acceptance, "live": live_acceptance}[sys.argv[1]]()
