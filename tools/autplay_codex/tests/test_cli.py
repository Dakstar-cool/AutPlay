from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from autplay_codex import cli, git_safety
from autplay_codex import state as state_module
from autplay_codex.backlog import BacklogError
from autplay_codex.git_safety import GitSafetyError, GitSnapshot
from autplay_codex.models import ReviewFinding, Severity, TaskClass, TaskState


def test_repository_relative_config_is_resolved_from_git_root(
    tmp_path: Path, monkeypatch: Any
) -> None:
    captured: dict[str, Path | None] = {}
    sentinel = object()

    def fake_load_config(repo_root: Path, config_path: Path | None = None) -> object:
        captured["root"] = repo_root
        captured["config"] = config_path
        return sentinel

    monkeypatch.setattr(cli, "_repository_root", lambda _start: tmp_path)
    monkeypatch.setattr(cli, "load_config", fake_load_config)
    monkeypatch.setattr(cli, "_status", lambda config, as_json: 0)

    assert cli.main(["--config", "config/harness.toml", "status"]) == 0
    assert captured == {
        "root": tmp_path,
        "config": tmp_path / "config" / "harness.toml",
    }


def test_status_is_observational_even_when_last_task_is_blocked(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    snapshot = GitSnapshot(
        root=tmp_path,
        branch="codex/test",
        head="a" * 40,
        dirty_paths=(),
    )
    task_state = SimpleNamespace(
        task_id="task-1",
        current_state="blocked",
        selected_model="gpt-5.6-sol",
        selected_reasoning="high",
        routing_reasons=["risk=high"],
        thread_id="thread-1",
        test_results=[],
        review_findings=[],
        blocker_reason="decision required",
    )
    monkeypatch.setattr(git_safety.GitInspector, "snapshot", lambda _self: snapshot)
    monkeypatch.setattr(state_module.StateStore, "load", lambda _self: task_state)
    config: Any = SimpleNamespace(repo_root=tmp_path, state_dir=tmp_path / ".state")

    assert cli._status(config, as_json=True) == 0
    assert '"state": "blocked"' in capsys.readouterr().out


def test_unresolved_major_review_is_visible_and_returns_failure(capsys: Any) -> None:
    state = TaskState(
        task_id="task-1",
        description="test",
        task_class=TaskClass.NORMAL_ENGINEERING,
        selected_model="terra",
        selected_reasoning="medium",
        persisted_goal=False,
        current_state="done",
        review_findings=[
            ReviewFinding(
                severity=Severity.MAJOR,
                title="Unsafe resume",
                evidence="stale state can overwrite a new checkpoint",
                affected_files=("state.py",),
                failure_scenario="two resume processes overlap",
            )
        ],
    )

    cli._print_state(state, as_json=False)

    output = capsys.readouterr().out
    assert "[major] Unsafe resume" in output
    assert "stale state" in output
    assert cli._state_exit(state) == 1


def test_resume_terminal_state_synchronizes_machine_backlog(tmp_path: Path) -> None:
    backlog_path = tmp_path / "backlog.json"
    backlog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "completed": [],
                "tasks": [
                    {
                        "id": "P04",
                        "milestone": "P04",
                        "title": "Contract",
                        "status": "queued",
                        "dependencies": [],
                        "definition_of_done": "done",
                        "risk": "critical",
                        "scope": "P04 only",
                        "blocker": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config: Any = SimpleNamespace(backlog_file=backlog_path)
    state = TaskState(
        task_id="task-1",
        backlog_task_id="P04",
        description="P04",
        task_class=TaskClass.MILESTONE,
        selected_model="sol",
        selected_reasoning="xhigh",
        persisted_goal=True,
        current_state="done",
    )

    cli._sync_backlog_state(config, state)

    document = json.loads(backlog_path.read_text(encoding="utf-8"))
    assert document["tasks"][0]["status"] == "done"


def test_cli_redacts_credentials_from_top_level_errors(monkeypatch: Any, capsys: Any) -> None:
    def fail_root(_start: Path) -> Path:
        raise GitSafetyError("postgresql://owner:topsecret@db.internal/autplay")

    monkeypatch.setattr(cli, "_repository_root", fail_root)

    assert cli.main(["status"]) == 1
    error = capsys.readouterr().err
    assert "topsecret" not in error
    assert "postgresql://<redacted>@db.internal/autplay" in error


def test_backlog_task_is_revalidated_inside_operation_lease(tmp_path: Path) -> None:
    backlog_path = tmp_path / "backlog.json"
    backlog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "completed": [],
                "tasks": [
                    {
                        "id": "P04",
                        "milestone": "P04",
                        "title": "Contract",
                        "status": "done",
                        "dependencies": [],
                        "definition_of_done": "done",
                        "risk": "critical",
                        "scope": "P04 only",
                        "blocker": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config: Any = SimpleNamespace(backlog_file=backlog_path)

    with pytest.raises(BacklogError, match="no longer runnable"):
        cli._require_backlog_task_runnable(config, "P04")


def test_next_json_preserves_eligible_task_when_git_safety_refuses_execution(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    backlog_path = tmp_path / "backlog.json"
    backlog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "completed": ["P03"],
                "tasks": [
                    {
                        "id": "P04",
                        "milestone": "P04",
                        "title": "Sync contract",
                        "status": "queued",
                        "dependencies": ["P03"],
                        "definition_of_done": "done",
                        "risk": "critical",
                        "scope": "P04 only",
                        "blocker": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config: Any = SimpleNamespace(backlog_file=backlog_path)
    args = argparse.Namespace(json=True)
    monkeypatch.setattr(cli, "_reconcile_persisted_backlog", lambda _config: None)

    def refuse_execution(_config: Any, _task: Any, _args: Any) -> int:
        raise GitSafetyError("working tree is dirty")

    monkeypatch.setattr(cli, "_run_backlog_task", refuse_execution)

    assert cli._next(config, args) == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "decision_blocker": None,
        "execution_started": False,
        "execution_status": "refused_by_git_safety",
        "next_task": {
            "id": "P04",
            "milestone": "P04",
            "title": "Sync contract",
        },
        "status": "eligible",
    }
