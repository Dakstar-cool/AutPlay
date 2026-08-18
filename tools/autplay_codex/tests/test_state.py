from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from autplay_codex.models import ReviewFinding, Severity, TaskClass, TaskState, TaskStatus
from autplay_codex.state import (
    StateConflictError,
    StateCorruptError,
    StateStore,
    transition,
)


def _state(task_id: str = "task-1") -> TaskState:
    return TaskState(
        task_id=task_id,
        description="test",
        task_class=TaskClass.NORMAL_ENGINEERING,
        selected_model="terra",
        selected_reasoning="medium",
        persisted_goal=False,
        repo_root="C:/repo",
        branch="codex/test",
        base_head="abc",
    )


def test_atomic_state_round_trip_preserves_unknown_fields(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    state = _state()
    state.extensions["future_field"] = {"value": 7}

    store.begin(state)
    loaded = store.load()

    assert loaded is not None
    assert loaded.task_id == state.task_id
    assert loaded.extensions == {"future_field": {"value": 7}}
    assert not list(store.state_dir.glob("*.tmp"))


def test_corrupted_state_has_clear_error(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.state_dir.mkdir()
    store.state_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(StateCorruptError, match=r"state\.json"):
        store.load()


def test_unfinished_state_is_never_overwritten(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.begin(_state("task-1"))

    with pytest.raises(StateConflictError, match="resume"):
        store.begin(_state("task-2"))


def test_completed_state_is_archived_before_next_task(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    first = _state("task-1")
    transition(first, TaskStatus.PLANNING)
    transition(first, TaskStatus.IMPLEMENTING)
    transition(first, TaskStatus.TESTING)
    transition(first, TaskStatus.DONE)
    store.begin(first)

    store.begin(_state("task-2"))

    archive = json.loads((store.history_dir / "task-1.json").read_text(encoding="utf-8"))
    assert archive["current_state"] == "done"
    loaded = store.load()
    assert loaded is not None
    assert loaded.task_id == "task-2"


def test_invalid_transition_is_rejected() -> None:
    state = _state()

    with pytest.raises(StateConflictError, match="queued -> done"):
        transition(state, TaskStatus.DONE)


def test_failed_atomic_replace_preserves_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path / "state")
    original = _state("task-original")
    store.begin(original)
    replacement = deepcopy(original)

    def fail_replace(source: os.PathLike[str], target: os.PathLike[str]) -> None:
        del source, target
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        store.save(replacement)

    document = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert document["task_id"] == "task-original"
    assert not list(store.state_dir.glob("*.tmp"))


def test_state_revision_rejects_stale_writer(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.begin(_state())
    first = store.load()
    stale = store.load()
    assert first is not None
    assert stale is not None

    store.save(first)

    with pytest.raises(StateConflictError, match="revision changed"):
        store.save(stale)


def test_exclusive_operation_rejects_overlapping_process_work(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")

    with (
        store.exclusive_operation(),
        pytest.raises(StateConflictError, match="another harness operation"),
        store.exclusive_operation(),
    ):
        raise AssertionError("overlapping operation lock was acquired")


def test_new_task_cannot_archive_unresolved_major_review(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    reviewed = _state("reviewed")
    transition(reviewed, TaskStatus.PLANNING)
    transition(reviewed, TaskStatus.IMPLEMENTING)
    transition(reviewed, TaskStatus.TESTING)
    transition(reviewed, TaskStatus.DONE)
    reviewed.review_findings.append(
        ReviewFinding(
            severity=Severity.MAJOR,
            title="Major",
            evidence="evidence",
            affected_files=("file.py",),
        )
    )
    store.begin(reviewed)

    with pytest.raises(StateConflictError, match="unresolved"):
        store.begin(_state("next"))
