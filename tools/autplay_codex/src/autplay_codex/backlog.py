"""Machine-readable companion to the authoritative implementation plan."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state import atomic_write_json


class BacklogError(RuntimeError):
    """Raised when the checked-in task plan is malformed or has no eligible task."""


@dataclass(frozen=True, slots=True)
class BacklogTask:
    """Minimal task record required by `next` and `milestone`."""

    task_id: str
    milestone: str
    title: str
    status: str
    dependencies: tuple[str, ...]
    definition_of_done: str
    risk: str
    scope: str
    blocker: str | None

    @property
    def prompt(self) -> str:
        blocker = f" Current documented blocker: {self.blocker}" if self.blocker else ""
        return (
            f"Complete AutPlay {self.task_id}: {self.title}. Scope: {self.scope} "
            f"Definition of Done: {self.definition_of_done}{blocker}"
        )


class BacklogStore:
    """Load, select, and atomically update plan task statuses."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._document: dict[str, Any] | None = None
        self._tasks: tuple[BacklogTask, ...] = ()
        self._completed: frozenset[str] = frozenset()

    def load(self) -> tuple[BacklogTask, ...]:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BacklogError(f"cannot load backlog: {exc}") from exc
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise BacklogError("backlog must be a schema_version 1 object")
        completed_value = document.get("completed")
        tasks_value = document.get("tasks")
        if not isinstance(completed_value, list) or not all(
            isinstance(item, str) for item in completed_value
        ):
            raise BacklogError("backlog completed must be an array of strings")
        if not isinstance(tasks_value, list) or not all(
            isinstance(item, dict) for item in tasks_value
        ):
            raise BacklogError("backlog tasks must be an array of objects")
        tasks = tuple(_parse_task(item) for item in tasks_value)
        identifiers = [task.task_id for task in tasks]
        if len(set(identifiers)) != len(identifiers):
            raise BacklogError("backlog task ids must be unique")
        known = set(completed_value) | set(identifiers)
        for task in tasks:
            unknown = set(task.dependencies) - known
            if unknown:
                raise BacklogError(
                    f"task {task.task_id} has unknown dependencies: {sorted(unknown)}"
                )
        self._document = document
        self._tasks = tasks
        self._completed = frozenset(completed_value)
        return tasks

    def next_task(self) -> BacklogTask | None:
        tasks = self._ensure_loaded()
        return next((task for task in tasks if self.is_runnable(task)), None)

    def is_runnable(self, task: BacklogTask) -> bool:
        tasks = self._ensure_loaded()
        completed = set(self._completed)
        completed.update(item.task_id for item in tasks if item.status == "done")
        return task.status == "queued" and set(task.dependencies) <= completed

    def task_for_milestone(self, milestone: str) -> BacklogTask | None:
        return next((task for task in self._ensure_loaded() if task.milestone == milestone), None)

    def blockers(self) -> tuple[BacklogTask, ...]:
        return tuple(task for task in self._ensure_loaded() if task.status == "blocked")

    def mark(self, task_id: str, status: str, blocker: str | None = None) -> None:
        if status not in {"queued", "blocked", "done", "failed"}:
            raise BacklogError(f"unsupported backlog status: {status}")
        self._ensure_loaded()
        assert self._document is not None
        task_values = self._document["tasks"]
        if not isinstance(task_values, list):
            raise BacklogError("backlog tasks changed type during update")
        found = False
        for item in task_values:
            if isinstance(item, dict) and item.get("id") == task_id:
                item["status"] = status
                item["blocker"] = blocker
                found = True
                break
        if not found:
            raise BacklogError(f"unknown backlog task: {task_id}")
        atomic_write_json(self.path, self._document)
        self.load()

    def _ensure_loaded(self) -> tuple[BacklogTask, ...]:
        if self._document is None:
            return self.load()
        return self._tasks


def _parse_task(value: dict[str, Any]) -> BacklogTask:
    dependencies = value.get("dependencies")
    blocker = value.get("blocker")
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        raise BacklogError("task dependencies must be an array of strings")
    if blocker is not None and not isinstance(blocker, str):
        raise BacklogError("task blocker must be a string or null")
    status = _required_string(value, "status")
    if status not in {"queued", "blocked", "done", "failed"}:
        raise BacklogError(f"unsupported task status: {status}")
    return BacklogTask(
        task_id=_required_string(value, "id"),
        milestone=_required_string(value, "milestone"),
        title=_required_string(value, "title"),
        status=status,
        dependencies=tuple(dependencies),
        definition_of_done=_required_string(value, "definition_of_done"),
        risk=_required_string(value, "risk"),
        scope=_required_string(value, "scope"),
        blocker=blocker,
    )


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise BacklogError(f"task {key} must be a non-empty string")
    return item
