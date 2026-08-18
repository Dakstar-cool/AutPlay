from __future__ import annotations

from pathlib import Path

import pytest
from autplay_codex.git_safety import (
    GitInspector,
    GitSafetyError,
    GitSnapshot,
    assert_dirty_baseline_preserved,
    assert_request_is_non_destructive,
    assert_resume_compatible,
    assert_safe_to_start,
)


def _snapshot(tmp_path: Path, *, branch: str = "codex/test", dirty: bool = False) -> GitSnapshot:
    return GitSnapshot(
        root=tmp_path,
        branch=branch,
        head="abc",
        dirty_paths=("user.txt",) if dirty else (),
        dirty_fingerprints=(("user.txt", "hash-1"),) if dirty else (),
    )


def test_protected_branch_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(GitSafetyError, match="protected branch"):
        assert_safe_to_start(
            _snapshot(tmp_path, branch="master"),
            expected_root=tmp_path,
            protected_branches=frozenset({"master"}),
        )


def test_dirty_tree_requires_explicit_preservation_gate(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, dirty=True)
    with pytest.raises(GitSafetyError, match="dirty"):
        assert_safe_to_start(
            snapshot,
            expected_root=tmp_path,
            protected_branches=frozenset({"master"}),
        )


@pytest.mark.parametrize(
    "description",
    [
        "force push this branch",
        "merge into main",
        "deploy to production",
        "drop database",
        "delete the media vault",
        "git push --force origin HEAD",
        "git reset --hard HEAD~1",
        "git checkout -- README.md",
        "git restore README.md",
        "rmdir /s /q build",
        "удали базу данных",
        "удали хранилище Vault",
    ],
)
def test_destructive_or_external_operations_are_rejected(description: str) -> None:
    with pytest.raises(GitSafetyError, match="cannot authorize"):
        assert_request_is_non_destructive(description)


def test_resume_requires_same_branch_and_repository(tmp_path: Path) -> None:
    with pytest.raises(GitSafetyError, match="branch"):
        assert_resume_compatible(str(tmp_path), "codex/other", "abc", _snapshot(tmp_path))


def test_resume_rejects_changed_head_and_dirty_content(tmp_path: Path) -> None:
    with pytest.raises(GitSafetyError, match="HEAD changed"):
        assert_resume_compatible(str(tmp_path), "codex/test", "old", _snapshot(tmp_path))

    with pytest.raises(GitSafetyError, match="dirty content"):
        assert_dirty_baseline_preserved(
            {"user.txt": "hash-original"},
            _snapshot(tmp_path, dirty=True),
        )


def test_git_snapshot_fingerprints_untracked_file_bytes(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-b", "codex/test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True)
    dirty = tmp_path / "nested" / "user.txt"
    dirty.parent.mkdir()
    dirty.write_text("first", encoding="utf-8")
    first = GitInspector(tmp_path).snapshot()
    dirty.write_text("second", encoding="utf-8")
    second = GitInspector(tmp_path).snapshot()

    assert first.dirty_paths == ("nested/user.txt",)
    assert first.dirty_fingerprints != second.dirty_fingerprints
