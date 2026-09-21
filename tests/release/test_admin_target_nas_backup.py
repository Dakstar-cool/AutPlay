from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_tool() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root))
    try:
        return importlib.import_module("scripts.admin_target_nas_backup")
    finally:
        sys.path.remove(str(repository_root))


tool = _load_tool()


def test_default_mode_is_read_only() -> None:
    options = tool._parser().parse_args(["--destination-root", "/mnt/autplay-backups"])

    assert options.execute is False
    assert options.leave_stopped is False


def test_backup_id_is_strict() -> None:
    assert tool.BACKUP_ID.fullmatch("admin-target-20260920T180629Z")
    assert tool.BACKUP_ID.fullmatch("../../production") is None


def test_acquisition_shards_are_not_stopped() -> None:
    assert not any("replenish" in name for name in tool.STATEFUL_CONTAINERS)
    assert "autplay-acquisition-vault-bridge" in tool.STATEFUL_CONTAINERS


def test_restart_orders_cover_each_stateful_container_once() -> None:
    restart_order = tool.CORE_RESTART_ORDER + tool.AUXILIARY_RESTART_ORDER

    assert len(restart_order) == len(set(restart_order))
    assert set(restart_order) == set(tool.STATEFUL_CONTAINERS)


def test_hash_manifest_is_stable_and_excludes_completion_files(tmp_path: Path) -> None:
    (tmp_path / "b.bin").write_bytes(b"b")
    (tmp_path / "a.bin").write_bytes(b"a")
    (tmp_path / "COMPLETED.json").write_text("ignored", encoding="utf-8")

    digest = tool._write_hash_manifest(tmp_path)

    expected = (
        f"{hashlib.sha256(b'a').hexdigest()}  a.bin\n{hashlib.sha256(b'b').hexdigest()}  b.bin\n"
    ).encode("ascii")
    assert (tmp_path / "SHA256SUMS").read_bytes() == expected
    assert digest == hashlib.sha256(expected).hexdigest()
    assert (tmp_path / "BACKUP_SHA256").read_text(encoding="ascii") == digest + "\n"


def test_leave_stopped_requires_execute(tmp_path: Path) -> None:
    with pytest.raises(tool.BackupError, match="leave_stopped_requires_execute"):
        tool.main(
            [
                "--destination-root",
                str(tmp_path),
                "--leave-stopped",
            ]
        )
