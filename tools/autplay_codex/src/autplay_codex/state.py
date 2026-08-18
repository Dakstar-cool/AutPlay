"""Atomic, versioned local state persistence for harness workflows."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from .models import TaskState, TaskStatus, has_unresolved_actionable_findings, utc_now


class StateError(RuntimeError):
    """Base state persistence error."""


class StateCorruptError(StateError):
    """Raised when the persisted state cannot be decoded safely."""


class StateConflictError(StateError):
    """Raised when a new workflow would overwrite unfinished work."""


_TERMINAL = {TaskStatus.DONE.value, TaskStatus.FAILED.value}
_TRANSITIONS: dict[str, frozenset[str]] = {
    TaskStatus.QUEUED.value: frozenset(
        {TaskStatus.PLANNING.value, TaskStatus.BLOCKED.value, TaskStatus.FAILED.value}
    ),
    TaskStatus.PLANNING.value: frozenset(
        {TaskStatus.IMPLEMENTING.value, TaskStatus.BLOCKED.value, TaskStatus.FAILED.value}
    ),
    TaskStatus.IMPLEMENTING.value: frozenset(
        {TaskStatus.TESTING.value, TaskStatus.BLOCKED.value, TaskStatus.FAILED.value}
    ),
    TaskStatus.TESTING.value: frozenset(
        {
            TaskStatus.REVIEWING.value,
            TaskStatus.FIXING.value,
            TaskStatus.DONE.value,
            TaskStatus.BLOCKED.value,
            TaskStatus.FAILED.value,
        }
    ),
    TaskStatus.REVIEWING.value: frozenset(
        {
            TaskStatus.FIXING.value,
            TaskStatus.TESTING.value,
            TaskStatus.DONE.value,
            TaskStatus.BLOCKED.value,
            TaskStatus.FAILED.value,
        }
    ),
    TaskStatus.FIXING.value: frozenset(
        {TaskStatus.TESTING.value, TaskStatus.BLOCKED.value, TaskStatus.FAILED.value}
    ),
    TaskStatus.BLOCKED.value: frozenset(
        {TaskStatus.PLANNING.value, TaskStatus.IMPLEMENTING.value, TaskStatus.FIXING.value}
    ),
    TaskStatus.FAILED.value: frozenset({TaskStatus.FIXING.value, TaskStatus.BLOCKED.value}),
    TaskStatus.DONE.value: frozenset({TaskStatus.BLOCKED.value}),
}


class StateStore:
    """Persist one active state and archive completed predecessors."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_path = state_dir / "state.json"
        self.history_dir = state_dir / "history"
        self.lock_path = state_dir / "state.lock"
        self.operation_lock_path = state_dir / "operation.lock"

    @contextmanager
    def exclusive_operation(self) -> Iterator[None]:
        """Prevent overlapping implementation, resume, or review processes."""

        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.operation_lock_path.open("a+b") as handle:
            try:
                _lock_file(handle, blocking=False)
            except OSError as exc:
                raise StateConflictError(
                    "another harness operation is active for this repository"
                ) from exc
            try:
                yield
            finally:
                _unlock_file(handle)

    def load(self) -> TaskState | None:
        with self._lock():
            return self._load_unlocked()

    def begin(self, state: TaskState) -> None:
        with self._lock():
            current = self._load_unlocked()
            if current is not None and has_unresolved_actionable_findings(current):
                raise StateConflictError(
                    f"task {current.task_id} has unresolved critical/major review findings"
                )
            if current is not None and current.current_state not in _TERMINAL:
                raise StateConflictError(
                    f"task {current.task_id} is still {current.current_state}; resume it first"
                )
            if current is not None:
                if not re.fullmatch(r"[A-Za-z0-9._-]+", current.task_id):
                    raise StateCorruptError("existing task_id is not safe for history archival")
                self.history_dir.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(self.history_dir / f"{current.task_id}.json", current.to_dict())
            state.revision = 0
            _atomic_write_json(self.state_path, state.to_dict())

    def save(self, state: TaskState) -> None:
        state.updated_at = utc_now()
        with self._lock():
            current = self._load_unlocked()
            if current is None:
                raise StateConflictError("cannot save state before begin")
            if current.task_id != state.task_id:
                raise StateConflictError("persisted task changed before save")
            if current.revision != state.revision:
                raise StateConflictError(
                    "persisted state revision changed; reload before continuing"
                )
            previous_revision = state.revision
            state.revision += 1
            try:
                _atomic_write_json(self.state_path, state.to_dict())
            except BaseException:
                state.revision = previous_revision
                raise

    def _load_unlocked(self) -> TaskState | None:
        if not self.state_path.exists():
            return None
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError("state root must be an object")
            return TaskState.from_dict(document)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
            raise StateCorruptError(f"cannot load state.json: {exc}") from exc

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            _lock_file(handle)
            try:
                yield
            finally:
                _unlock_file(handle)


def transition(state: TaskState, target: TaskStatus) -> None:
    """Apply one validated state-machine transition."""

    allowed = _TRANSITIONS.get(state.current_state)
    if allowed is None:
        raise StateCorruptError(f"unknown persisted state: {state.current_state}")
    if target.value not in allowed:
        raise StateConflictError(
            f"invalid state transition: {state.current_state} -> {target.value}"
        )
    state.current_state = target.value
    state.updated_at = utc_now()


def is_terminal(state: TaskState) -> bool:
    """Return whether resume must not rerun this task."""

    return state.current_state in _TERMINAL


def atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    """Write a JSON document durably without exposing a partial target file."""

    _atomic_write_json(path, document)


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _lock_file(handle: BinaryIO, *, blocking: bool = True) -> None:
    handle.seek(0)
    if handle.read(1) == b"":
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(handle.fileno(), mode, 1)
    else:
        import fcntl

        flags = fcntl.LOCK_EX  # type: ignore[attr-defined]
        if not blocking:
            flags |= fcntl.LOCK_NB  # type: ignore[attr-defined]
        fcntl.flock(handle.fileno(), flags)  # type: ignore[attr-defined]


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
