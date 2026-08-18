from __future__ import annotations

import json
from pathlib import Path

from autplay_codex.backlog import BacklogStore


def _write_backlog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "completed": ["P03"],
                "tasks": [
                    {
                        "id": "P04",
                        "milestone": "P04",
                        "title": "Blocked",
                        "status": "blocked",
                        "dependencies": ["P03"],
                        "definition_of_done": "done",
                        "risk": "critical",
                        "scope": "P04 only",
                        "blocker": "decision required",
                    },
                    {
                        "id": "P05",
                        "milestone": "P05",
                        "title": "Waiting",
                        "status": "queued",
                        "dependencies": ["P04"],
                        "definition_of_done": "done",
                        "risk": "high",
                        "scope": "P05 only",
                        "blocker": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_next_skips_blocked_and_dependency_ineligible_tasks(tmp_path: Path) -> None:
    path = tmp_path / "backlog.json"
    _write_backlog(path)
    store = BacklogStore(path)

    assert store.next_task() is None
    assert [task.task_id for task in store.blockers()] == ["P04"]


def test_mark_done_unlocks_dependent_task_atomically(tmp_path: Path) -> None:
    path = tmp_path / "backlog.json"
    _write_backlog(path)
    store = BacklogStore(path)

    store.mark("P04", "done")

    next_task = store.next_task()
    assert next_task is not None
    assert next_task.task_id == "P05"
    assert not list(tmp_path.glob("*.tmp"))
