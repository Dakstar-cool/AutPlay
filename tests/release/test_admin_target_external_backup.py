from __future__ import annotations

import hashlib
import importlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import ModuleType

import pytest


def _load_tool() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root))
    try:
        return importlib.import_module("scripts.admin_target_external_backup")
    finally:
        sys.path.remove(str(repository_root))


tool = _load_tool()


def test_default_mode_is_read_only() -> None:
    options = tool._parser().parse_args(["--destination-root", r"E:\AutPlayBackups"])

    assert options.execute is False
    assert options.leave_stopped is False
    assert options.vault_compression == "none"
    assert options.ssh_target == tool.DEFAULT_SSH_TARGET


def test_remote_command_is_single_fail_closed_shell_argument() -> None:
    command = tool._remote_shell("docker image save 'image:name' | gzip -1")

    assert command.startswith("bash -o pipefail -c ")
    assert "docker image save" in command
    assert "gzip -1" in command


def test_backup_id_is_strict() -> None:
    assert tool.BACKUP_ID.fullmatch("admin-target-20260920T180629Z")
    assert tool.BACKUP_ID.fullmatch(r"..\production") is None


def test_hash_manifest_is_stable_and_read_back(tmp_path: Path) -> None:
    (tmp_path / "b.bin").write_bytes(b"b")
    (tmp_path / "a.bin").write_bytes(b"a")
    (tmp_path / "COMPLETED.json").write_text("ignored", encoding="utf-8")

    budget = tool.TransferBudget(1024**3, 90, 0)
    digest = tool._write_hash_manifest(tmp_path, budget)

    expected = (
        f"{hashlib.sha256(b'a').hexdigest()}  a.bin\n{hashlib.sha256(b'b').hexdigest()}  b.bin\n"
    ).encode("ascii")
    assert (tmp_path / "SHA256SUMS").read_bytes() == expected
    assert digest == hashlib.sha256(expected).hexdigest()


def test_baseline_file_is_strict_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    expected = tool._default_baseline()
    path.write_text(json.dumps(asdict(expected)), encoding="utf-8")

    assert tool._load_baseline(path) == expected

    value = asdict(expected)
    value["live_image_sha256"] = "not-a-digest"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(tool.ExternalBackupError, match="backup_baseline_invalid"):
        tool._load_baseline(path)


def test_transfer_budget_warns_once_and_enforces_hard_cap() -> None:
    updates: list[tuple[int, bool]] = []
    budget = tool.TransferBudget(
        1024**3,
        90,
        0,
        lambda written, warned: updates.append((written, warned)),
    )

    budget.accept(900 * 1024**2)
    budget.accept(100 * 1024**2)

    assert budget.warned is True
    assert any(warned for _, warned in updates)
    with pytest.raises(tool.ExternalBackupError, match="backup_size_limit_exceeded"):
        budget.accept(25 * 1024**2)


def test_execute_requires_explicit_size_cap(tmp_path: Path) -> None:
    with pytest.raises(tool.ExternalBackupError, match="max_backup_bytes_required"):
        tool.main(["--destination-root", str(tmp_path), "--execute"])


def test_leave_stopped_requires_execute(tmp_path: Path) -> None:
    with pytest.raises(tool.ExternalBackupError, match="leave_stopped_requires_execute"):
        tool.main(
            [
                "--destination-root",
                str(tmp_path),
                "--leave-stopped",
            ]
        )


def test_run_requested_requires_remote_control(tmp_path: Path) -> None:
    with pytest.raises(tool.ExternalBackupError, match="run_requested_requires_remote_control"):
        tool.main(["--destination-root", str(tmp_path), "--run-requested"])


def test_execute_modes_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(tool.ExternalBackupError, match="execute_modes_mutually_exclusive"):
        tool.main(
            [
                "--destination-root",
                str(tmp_path),
                "--execute",
                "--run-requested",
            ]
        )


def test_remote_control_helper_image_requires_root(tmp_path: Path) -> None:
    with pytest.raises(tool.ExternalBackupError, match="remote_control_helper_image_requires_root"):
        tool.main(
            [
                "--destination-root",
                str(tmp_path),
                "--remote-control-helper-image",
                "autplay-admin-acceptance:504cf8310f3f",
            ]
        )


def test_run_requested_noops_when_admin_status_is_not_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(tool.RemoteControlReporter, "request_is_pending", lambda self: False)

    assert (
        tool.main(
            [
                "--destination-root",
                str(tmp_path),
                "--run-requested",
                "--remote-control-root",
                "/srv/autplay/operator/backup-control",
                "--target-id",
                "windows-usb-e",
            ]
        )
        == 0
    )

    assert '"status": "NOOP"' in capsys.readouterr().out


def test_remote_control_helper_reads_owner_only_spool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def remote_output(ssh_target: str, command: str) -> str:
        assert ssh_target == "operator@example"
        observed.append(command)
        return '{"state":"REQUESTED","target_id":"windows-usb-e"}'

    monkeypatch.setattr(tool, "_remote_output", remote_output)
    reporter = tool.RemoteControlReporter(
        "operator@example",
        "/srv/autplay/operator/backup-control",
        "windows-usb-e",
        "admin-target-20260920T180629Z",
        "autplay-admin-acceptance:504cf8310f3f",
    )

    assert reporter.request_is_pending() is True
    assert len(observed) == 1
    assert observed[0].startswith("docker run --rm --network none --user 999:999 --volume ")
    assert "--entrypoint cat autplay-admin-acceptance:504cf8310f3f" in observed[0]
