from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_backup_control_overlay_mounts_only_private_spool(tmp_path: Path) -> None:
    secrets = {
        "AUTPLAY_RUNTIME_AUTH_SECRET_FILE": tmp_path / "auth.txt",
        "AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE": tmp_path / "source.txt",
        "AUTPLAY_RUNTIME_ADMIN_SOURCE_SECRET_FILE": tmp_path / "admin-source.txt",
        "AUTPLAY_RUNTIME_ADMIN_CSRF_SECRET_FILE": tmp_path / "admin-csrf.txt",
        "AUTPLAY_RUNTIME_PROFILE_IDENTITY_KEY_FILE": tmp_path / "identity.pem",
    }
    for index, path in enumerate(secrets.values()):
        path.write_text(chr(ord("a") + index) * 48, encoding="utf-8")
    control = tmp_path / "backup-control"
    control.mkdir()
    targets = json.dumps(
        [
            {
                "id": "workstation-usb-e",
                "label": "External USB E",
                "kind": "external-agent",
            }
        ],
        separators=(",", ":"),
    )
    environment = os.environ.copy()
    environment.update({name: str(path) for name, path in secrets.items()})
    environment["AUTPLAY_RUNTIME_BACKUP_CONTROL_ROOT"] = str(control)
    environment["AUTPLAY_ADMIN_BACKUP_TARGETS_JSON"] = targets
    command = ["docker", "compose", "-p", "autplay-backup-control-test"]
    for compose_path in (
        "deploy/compose/compose.yaml",
        "deploy/compose/compose.runtime.yaml",
        "deploy/compose/compose.admin-local.yaml",
        "deploy/compose/compose.backup-control.yaml",
    ):
        command.extend(("-f", compose_path))
    command.extend(("--profile", "runtime", "config", "--format", "json"))

    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    api = json.loads(completed.stdout)["services"]["api"]
    mounts = {mount["target"]: mount for mount in api["volumes"]}

    assert api["environment"]["AUTPLAY_ADMIN_BACKUP_TARGETS_JSON"] == targets
    assert api["environment"]["AUTPLAY_ADMIN_BACKUP_CONTROL_ROOT"] == (
        "/var/lib/autplay/backup-control"
    )
    assert mounts["/var/lib/autplay/backup-control"]["type"] == "bind"
    assert Path(mounts["/var/lib/autplay/backup-control"]["source"]) == control
    assert "/var/run/docker.sock" not in mounts
    assert all("E:\\" not in str(value) for value in api["environment"].values())
