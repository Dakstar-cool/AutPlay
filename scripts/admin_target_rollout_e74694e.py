"""One-time fail-closed rollout of the reviewed e74694e Admin target image."""

from __future__ import annotations

import copy
import http.client
import json
import os
import socket
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path("/srv/autplay/production/admin-target-e74694e107a7")
IMAGE = "autplay-admin-acceptance:e74694e107a7"
IMAGE_SHA256 = "f01ecfb52e6f2d40a947aa6e08bbc80288e07741f851bdb250c4db617fc81170"
OLD_IMAGE = "autplay-metadata:20260916-r2"
OLD_IMAGE_SHA256 = "c6518996ff1b0cdfaa4df24dcabe8a574b3019fefe2c31ebae8bcc08e09adb58"
OLD_MIGRATION = "0032_track_metadata"
NEW_MIGRATION = "0060_local_bridge_authority"
POSTGRES = "autplay-production-postgres-1"
NETWORK = "autplay-production_default"
SUFFIX = "-before-admin-e74694e"
STAGED_SUFFIX = "-staged-admin-e74694e"
SECRETS = PurePosixPath("/srv/autplay/secrets/production")
CONTROL_ROOT = PurePosixPath("/srv/autplay/operator/backup-control")
PRIVACY_VOLUME = "autplay-production_privacy-ledger-data"
TRAINING_VOLUME = "autplay-production_training-consent-ledger-data"

SERVER_CONTAINERS = (
    "autplay-production-api-1",
    "autplay-production-mobile-api-1",
    "autplay-production-stream-1",
    "autplay-production-worker-cpu-1",
    "autplay-production-operator",
    "autplay-production-tailnet-admin",
    "autplay-music-worker",
    "autplay-metadata-worker",
)
START_ORDER = (
    "autplay-production-api-1",
    "autplay-production-mobile-api-1",
    "autplay-production-stream-1",
    "autplay-production-operator",
    "autplay-production-tailnet-admin",
    "autplay-production-worker-cpu-1",
    "autplay-music-worker",
    "autplay-metadata-worker",
)
AUXILIARY_RESTART = (
    "autplay-acquisition-vault-bridge",
    "autplay-production-tailnet-mobile-edge",
)
HEALTH_REQUIRED = (
    "autplay-production-api-1",
    "autplay-production-mobile-api-1",
    "autplay-production-stream-1",
    "autplay-production-tailnet-admin",
)


def run(*arguments: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        check=True,
        text=True,
        input=input_text,
        capture_output=True,
    )


def docker(*arguments: str) -> str:
    return run("docker", *arguments).stdout.strip()


class DockerConnection(http.client.HTTPConnection):
    def __init__(self) -> None:
        super().__init__("localhost", timeout=60)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect("/var/run/docker.sock")


def create(name: str, spec: dict[str, Any]) -> None:
    connection = DockerConnection()
    connection.request(
        "POST",
        "/v1.55/containers/create?name=" + name,
        json.dumps(spec),
        {"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    body = response.read()
    connection.close()
    if response.status != 201:
        raise RuntimeError(f"container_create_{response.status}:{body[:300]!r}")


def save(name: str, value: object) -> None:
    path = ROOT / name
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def env_map(spec: dict[str, Any]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in spec.get("Env") or [])


def set_environment(spec: dict[str, Any], updates: dict[str, str]) -> None:
    values = env_map(spec)
    values.update(updates)
    spec["Env"] = [f"{key}={value}" for key, value in sorted(values.items())]


def add_bind(spec: dict[str, Any], source: str, target: str, mode: str) -> None:
    binds = list(spec["HostConfig"].get("Binds") or [])
    spec["HostConfig"]["Binds"] = binds
    binds[:] = [entry for entry in binds if entry.split(":", 2)[1] != target]
    binds.append(f"{source}:{target}:{mode}")


def network_config(old: dict[str, Any]) -> dict[str, Any]:
    return {
        "EndpointsConfig": {
            name: {
                "Aliases": [
                    alias
                    for alias in (network.get("Aliases") or [])
                    if alias not in (old["Id"], old["Id"][:12])
                ]
            }
            for name, network in old["NetworkSettings"]["Networks"].items()
        }
    }


def common_runtime(spec: dict[str, Any], *, ledger_mode: str = "rw") -> None:
    set_environment(
        spec,
        {
            "AUTPLAY_PRIVACY_LEDGER_PATH": "/var/lib/autplay/privacy-ledger/deletion.sqlite3",
            "AUTPLAY_PRIVACY_LEDGER_KEY_FILE": "/run/secrets/autplay-privacy-ledger-key",
            "AUTPLAY_PRIVACY_LEDGER_KEY_ID": "deletion-ledger-v1",
            "AUTPLAY_TRAINING_CONSENT_LEDGER_PATH": (
                "/var/lib/autplay/training-consent-ledger/consent.sqlite3"
            ),
            "AUTPLAY_TRAINING_CONSENT_LEDGER_KEY_FILE": (
                "/run/secrets/autplay-training-consent-ledger-key"
            ),
            "AUTPLAY_TRAINING_CONSENT_LEDGER_KEY_ID": "training-consent-ledger-v1",
        },
    )
    add_bind(
        spec,
        str(SECRETS / "deletion-ledger-key"),
        "/run/secrets/autplay-privacy-ledger-key",
        "ro",
    )
    add_bind(
        spec,
        str(SECRETS / "training-consent-ledger-key"),
        "/run/secrets/autplay-training-consent-ledger-key",
        "ro",
    )
    add_bind(spec, PRIVACY_VOLUME, "/var/lib/autplay/privacy-ledger", ledger_mode)
    add_bind(
        spec,
        TRAINING_VOLUME,
        "/var/lib/autplay/training-consent-ledger",
        ledger_mode,
    )


def replacement(old: dict[str, Any]) -> dict[str, Any]:
    name = old["Name"].lstrip("/")
    spec = copy.deepcopy(old["Config"])
    spec["Image"] = IMAGE
    spec.setdefault("Labels", {})["com.autplay.admin-target"] = "e74694e107a7"
    spec["HostConfig"] = copy.deepcopy(old["HostConfig"])
    spec["NetworkingConfig"] = network_config(old)
    common_runtime(spec, ledger_mode="ro" if name == "autplay-production-stream-1" else "rw")
    if name == "autplay-production-tailnet-admin":
        set_environment(
            spec,
            {
                "AUTPLAY_PROFILE": "production",
                "AUTPLAY_ADMIN_PASSKEYS_ENABLED": "true",
                "AUTPLAY_ADMIN_BACKUP_TARGETS_JSON": (
                    '[{"id":"windows-usb-e","kind":"external-agent",'
                    '"label":"Windows USB backup drive (E:)"}]'
                ),
                "AUTPLAY_ADMIN_BACKUP_CONTROL_ROOT": "/var/lib/autplay/backup-control",
            },
        )
        add_bind(spec, str(CONTROL_ROOT), "/var/lib/autplay/backup-control", "rw")
    if name == "autplay-production-mobile-api-1":
        set_environment(
            spec,
            {
                "AUTPLAY_SELF_DEVICE_PAIRING_ENABLED": "true",
                "AUTPLAY_ACCOUNT_RECOVERY_ENABLED": "true",
                "AUTPLAY_ACCOUNT_DELETION_ENABLED": "true",
                "AUTPLAY_SHARED_TRAINING_CONSENT_ENABLED": "true",
            },
        )
    if name == "autplay-production-worker-cpu-1":
        set_environment(spec, {"AUTPLAY_ACCOUNT_PURGE_ENABLED": "true"})
    return spec


def helper_spec(
    old: dict[str, Any],
    command: list[str],
    *,
    include_private_state: bool = True,
) -> dict[str, Any]:
    spec = replacement(old) if include_private_state else copy.deepcopy(old["Config"])
    if not include_private_state:
        spec["Image"] = IMAGE
        spec["HostConfig"] = copy.deepcopy(old["HostConfig"])
        spec["NetworkingConfig"] = network_config(old)
    spec["Cmd"] = command
    spec["Healthcheck"] = {"Test": ["NONE"]}
    spec["ExposedPorts"] = {}
    spec["HostConfig"]["PortBindings"] = {}
    spec["HostConfig"]["RestartPolicy"] = {"Name": "no", "MaximumRetryCount": 0}
    spec["NetworkingConfig"] = {"EndpointsConfig": {NETWORK: {}}}
    set_environment(spec, {"AUTPLAY_PROFILE": "production"})
    return spec


def wait_healthy(name: str, attempts: int = 90) -> None:
    for _ in range(attempts):
        status = docker(
            "inspect",
            name,
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
        )
        if status == "healthy":
            return
        time.sleep(2)
    raise RuntimeError(f"container_health_timeout:{name}")


def prepare_private_storage() -> None:
    key_program = """
import os
import secrets
from pathlib import Path
for name in ('deletion-ledger-key', 'training-consent-ledger-key'):
    path = Path('/secrets') / name
    if path.exists():
        raise SystemExit('ledger_key_already_exists:' + name)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        os.write(descriptor, secrets.token_urlsafe(48).encode('ascii'))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(path, 999, 999)
"""
    run(
        "docker",
        "run",
        "--rm",
        "--log-driver",
        "none",
        "--network",
        "none",
        "--user",
        "0:0",
        "--volume",
        f"{SECRETS}:/secrets:rw",
        "--entrypoint",
        "python",
        IMAGE,
        "-",
        input_text=key_program,
    )
    run(
        "docker",
        "run",
        "--rm",
        "--log-driver",
        "none",
        "--network",
        "none",
        "--user",
        "0:0",
        "--volume",
        "/srv/autplay/operator:/operator:rw",
        "--entrypoint",
        "sh",
        IMAGE,
        "-c",
        "install -d -o 999 -g 999 -m 0700 /operator/backup-control",
    )
    for volume, purpose in (
        (PRIVACY_VOLUME, "persistent-production-deletion-ledger"),
        (TRAINING_VOLUME, "persistent-production-training-consent-ledger"),
    ):
        docker("volume", "create", "--label", f"app.autplay.purpose={purpose}", volume)
        run(
            "docker",
            "run",
            "--rm",
            "--log-driver",
            "none",
            "--network",
            "none",
            "--user",
            "0:0",
            "--volume",
            f"{volume}:/target:rw",
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            "chown 999:999 /target && chmod 0700 /target",
        )


def initialize_private_ledgers(operator: dict[str, Any]) -> None:
    for name, command in (
        (
            "autplay-admin-e74694e-privacy-ledger-init",
            ["python", "-m", "autplay.entrypoints.privacy_admin", "initialize-ledger"],
        ),
        (
            "autplay-admin-e74694e-training-ledger-init",
            [
                "python",
                "-m",
                "autplay.entrypoints.training_consent_restore",
                "initialize-ledger",
            ],
        ),
    ):
        create(name, helper_spec(operator, command))
        docker("start", name)
        if docker("wait", name) != "0":
            raise RuntimeError(f"ledger_initialization_failed:{name}")


def main() -> None:
    os.umask(0o077)
    for key in (SECRETS / "deletion-ledger-key", SECRETS / "training-consent-ledger-key"):
        if Path(key).exists():
            raise RuntimeError(f"ledger_key_already_exists:{key.name}")
    for volume in (PRIVACY_VOLUME, TRAINING_VOLUME):
        if docker("volume", "ls", "-q", "--filter", f"name=^{volume}$"):
            raise RuntimeError(f"ledger_volume_already_exists:{volume}")
    if Path(CONTROL_ROOT).exists():
        raise RuntimeError("backup_control_root_already_exists")
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=False)
    actual_image = docker("image", "inspect", IMAGE, "--format", "{{.Id}}").removeprefix(
        "sha256:"
    )
    if actual_image != IMAGE_SHA256:
        raise RuntimeError("candidate_image_digest_changed")
    old_actual = docker(
        "image", "inspect", OLD_IMAGE, "--format", "{{.Id}}"
    ).removeprefix("sha256:")
    if old_actual != OLD_IMAGE_SHA256:
        raise RuntimeError("rollback_image_digest_changed")
    running = set(docker("ps", "--format", "{{.Names}}").splitlines())
    if running != {"autplay-music-proxy", "open-webui"}:
        raise RuntimeError("unexpected_running_container_set")
    originals = {
        name: json.loads(docker("inspect", name))[0] for name in SERVER_CONTAINERS
    }
    if any(item["State"]["Running"] for item in originals.values()):
        raise RuntimeError("server_container_not_quiesced")
    if any(item["Config"]["Image"] != OLD_IMAGE for item in originals.values()):
        raise RuntimeError("server_image_baseline_changed")
    save("originals.json", originals)

    operator = originals["autplay-production-operator"]
    prepare_private_storage()
    replacements = {name: replacement(item) for name, item in originals.items()}
    save("replacements.json", replacements)
    for name in SERVER_CONTAINERS:
        staged = name + STAGED_SUFFIX
        if docker("ps", "-a", "--filter", f"name=^{staged}$", "--format", "{{.Names}}"):
            raise RuntimeError(f"staged_container_already_exists:{staged}")
        create(staged, replacements[name])

    docker("start", POSTGRES)
    wait_healthy(POSTGRES)
    current = docker(
        "exec",
        POSTGRES,
        "psql",
        "-U",
        "autplay",
        "-d",
        "autplay",
        "-Atqc",
        "SELECT version_num FROM alembic_version",
    )
    if current != OLD_MIGRATION:
        raise RuntimeError("production_migration_baseline_changed")

    migration = helper_spec(
        operator,
        ["alembic", "-c", "/opt/autplay/alembic.ini", "upgrade", "head"],
        include_private_state=False,
    )
    create("autplay-admin-e74694e-migrate", migration)
    docker("start", "autplay-admin-e74694e-migrate")
    if docker("wait", "autplay-admin-e74694e-migrate") != "0":
        raise RuntimeError("production_migration_failed")
    current = docker(
        "exec",
        POSTGRES,
        "psql",
        "-U",
        "autplay",
        "-d",
        "autplay",
        "-Atqc",
        "SELECT version_num FROM alembic_version",
    )
    if current != NEW_MIGRATION:
        raise RuntimeError("production_migration_head_invalid")

    initialize_private_ledgers(operator)
    for name in SERVER_CONTAINERS:
        before = name + SUFFIX
        if docker("ps", "-a", "--filter", f"name=^{before}$", "--format", "{{.Names}}"):
            raise RuntimeError(f"rollback_container_already_exists:{before}")
        docker("rename", name, before)
        docker("rename", name + STAGED_SUFFIX, name)
    for name in START_ORDER:
        docker("start", name)
    for name in AUXILIARY_RESTART:
        docker("start", name)
    for name in HEALTH_REQUIRED:
        wait_healthy(name)
    save(
        "phase.json",
        {
            "phase": "started",
            "image_sha256": actual_image,
            "migration_head": current,
        },
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "migration_head": current,
                "image_sha256": actual_image,
                "health_required": list(HEALTH_REQUIRED),
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
