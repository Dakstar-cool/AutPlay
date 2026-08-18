"""Command-line interface for the AutPlay Codex development harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .backlog import BacklogError, BacklogStore, BacklogTask
from .checks import CheckRunner
from .codex_client import CodexExecutionError, OpenAICodexRunner
from .config import ConfigurationError, HarnessConfig, load_config
from .event_log import EventLogger
from .git_safety import GitInspector, GitSafetyError
from .models import TaskState, has_unresolved_actionable_findings
from .redaction import Redactor
from .routing import RoutingError
from .state import StateError, StateStore
from .workflow import DryRunResult, HarnessWorkflow, WorkflowError

_BLOCKED_EXIT = 3
_FAILED_EXIT = 1


def main(arguments: list[str] | None = None) -> int:
    """Run the command-line harness and return a stable process exit code."""

    parser = _build_parser()
    args = parser.parse_args(arguments)
    redactor = Redactor(Path.cwd())
    try:
        repo_root = _repository_root(Path.cwd())
        redactor = Redactor(repo_root)
        config_path = repo_root / args.config if args.config else None
        config = load_config(repo_root, config_path)
        if args.command == "status":
            return _status(config, as_json=args.json)
        if args.command == "task":
            return _task(config, args)
        if args.command == "next":
            return _next(config, args)
        if args.command == "milestone":
            return _milestone(config, args)
        if args.command == "review":
            state = _workflow(config).review_only(
                terminal_callback=lambda result: _sync_backlog_state(config, result)
            )
            _print_state(state, as_json=args.json)
            return _state_exit(state)
        if args.command == "resume":
            state = _resume_command(config)
            _print_state(state, as_json=args.json)
            return _state_exit(state)
        parser.error(f"unsupported command: {args.command}")
    except (
        BacklogError,
        CodexExecutionError,
        ConfigurationError,
        GitSafetyError,
        RoutingError,
        StateError,
        WorkflowError,
    ) as exc:
        print(f"autplay-codex: {redactor.text(str(exc))}", file=sys.stderr)
        return _FAILED_EXIT
    return _FAILED_EXIT


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autplay-codex",
        description="Safe local Codex orchestration for AutPlay development.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        help="Repository-relative harness TOML path (default: autplay-codex.toml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show persisted task, Git, and check state.")
    _json_flag(status)

    task = subparsers.add_parser("task", help="Route and execute one bounded task.")
    task.add_argument("description")
    _execution_flags(task)

    next_parser = subparsers.add_parser(
        "next", help="Select and execute the next dependency-eligible plan task."
    )
    _execution_flags(next_parser)

    milestone = subparsers.add_parser(
        "milestone", help="Execute one plan milestone with persisted-goal routing."
    )
    milestone.add_argument("milestone_id")
    _execution_flags(milestone)

    review = subparsers.add_parser("review", help="Run one independent read-only review.")
    _json_flag(review)
    resume = subparsers.add_parser("resume", help="Resume the last unfinished checkpoint.")
    _json_flag(resume)
    return parser


def _execution_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="Explicit model override.")
    parser.add_argument(
        "--reasoning",
        choices=("minimal", "low", "medium", "high", "xhigh"),
        help="Explicit reasoning override.",
    )
    goal = parser.add_mutually_exclusive_group()
    goal.add_argument("--persisted-goal", dest="persisted_goal", action="store_true", default=None)
    goal.add_argument("--no-persisted-goal", dest="persisted_goal", action="store_false")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show routing without creating state or starting Codex.",
    )
    _json_flag(parser)


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def _workflow(config: HarnessConfig) -> HarnessWorkflow:
    git = GitInspector(config.repo_root)
    state_store = StateStore(config.state_dir)
    return HarnessWorkflow(
        config,
        git=git,
        state_store=state_store,
        runner=OpenAICodexRunner(config.repo_root),
        check_runner=CheckRunner(config.repo_root, config.command_timeout_seconds),
        logger=EventLogger(config.state_dir, config.repo_root, log_level=config.log_level),
    )


def _task(config: HarnessConfig, args: argparse.Namespace) -> int:
    result = _workflow(config).run_new_task(
        args.description,
        model_override=args.model,
        reasoning_override=args.reasoning,
        persisted_goal_override=args.persisted_goal,
        dry_run=args.dry_run,
    )
    return _print_execution_result(result, as_json=args.json)


def _next(config: HarnessConfig, args: argparse.Namespace) -> int:
    _reconcile_persisted_backlog(config)
    backlog = BacklogStore(config.backlog_file)
    task = backlog.next_task()
    if task is None:
        return _print_no_next(backlog, as_json=args.json)
    try:
        return _run_backlog_task(config, task, args)
    except GitSafetyError:
        if not args.json:
            raise
        _print_json(
            {
                "status": "eligible",
                "next_task": {
                    "id": task.task_id,
                    "milestone": task.milestone,
                    "title": task.title,
                },
                "decision_blocker": task.blocker,
                "execution_started": False,
                "execution_status": "refused_by_git_safety",
            }
        )
        return _FAILED_EXIT


def _milestone(config: HarnessConfig, args: argparse.Namespace) -> int:
    _reconcile_persisted_backlog(config)
    backlog = BacklogStore(config.backlog_file)
    task = backlog.task_for_milestone(args.milestone_id)
    if task is None:
        raise BacklogError(f"unknown milestone: {args.milestone_id}")
    if not backlog.is_runnable(task):
        reason = task.blocker or "dependencies are incomplete or milestone is not queued"
        raise BacklogError(f"milestone {args.milestone_id} is not runnable: {reason}")
    return _run_backlog_task(config, task, args)


def _run_backlog_task(
    config: HarnessConfig,
    task: BacklogTask,
    args: argparse.Namespace,
) -> int:
    result = _workflow(config).run_new_task(
        task.prompt,
        milestone_id=task.milestone,
        backlog_task_id=task.task_id,
        model_override=args.model,
        reasoning_override=args.reasoning,
        persisted_goal_override=args.persisted_goal,
        dry_run=args.dry_run,
        preflight=lambda: _require_backlog_task_runnable(config, task.task_id),
        terminal_callback=lambda state: _sync_backlog_state(config, state),
    )
    return _print_execution_result(result, as_json=args.json)


def _status(config: HarnessConfig, *, as_json: bool) -> int:
    snapshot = GitInspector(config.repo_root).snapshot()
    state = StateStore(config.state_dir).load()
    document: dict[str, Any] = {
        "active_task": state.task_id if state else None,
        "state": state.current_state if state else None,
        "selected_model": state.selected_model if state else None,
        "reasoning": state.selected_reasoning if state else None,
        "routing_reasons": list(state.routing_reasons) if state else [],
        "thread_id": state.thread_id if state else None,
        "git": {
            "branch": snapshot.branch,
            "head": snapshot.head,
            "dirty": bool(snapshot.dirty_paths),
            "dirty_paths": list(snapshot.dirty_paths),
        },
        "last_checks": [result.to_dict() for result in state.test_results[-5:]] if state else [],
        "review_findings": (
            [finding.to_dict() for finding in state.review_findings] if state else []
        ),
        "blocker": state.blocker_reason if state else None,
    }
    if as_json:
        _print_json(document)
    else:
        print(f"active task: {document['active_task'] or 'none'}")
        print(f"state: {document['state'] or 'none'}")
        print(f"model: {document['selected_model'] or 'none'}")
        print(f"reasoning: {document['reasoning'] or 'none'}")
        if state and state.routing_reasons:
            print(f"routing: {'; '.join(state.routing_reasons)}")
        print(f"thread id: {document['thread_id'] or 'none'}")
        print(
            f"git: {snapshot.branch} at {snapshot.head[:12]} "
            f"({'dirty' if snapshot.dirty_paths else 'clean'})"
        )
        if state and state.test_results:
            latest = state.test_results[-1]
            print(f"last check: {latest.name} {latest.status.value}")
        if state and state.blocker_reason:
            print(f"blocker: {state.blocker_reason}")
        if state:
            _print_findings(state)
    return 0


def _print_execution_result(result: TaskState | DryRunResult, *, as_json: bool) -> int:
    if isinstance(result, DryRunResult):
        document = {
            "task_class": result.decision.task_class.value,
            "model": result.decision.model,
            "reasoning": result.decision.reasoning,
            "persisted_goal": result.decision.persisted_goal,
            "reasons": list(result.decision.reasons),
            "dry_run": True,
        }
        if as_json:
            _print_json(document)
        else:
            print(f"class: {document['task_class']}")
            print(f"model: {document['model']}")
            print(f"reasoning: {document['reasoning']}")
            print(f"persisted goal: {str(document['persisted_goal']).lower()}")
            print(f"reason: {'; '.join(result.decision.reasons)}")
        return 0
    _print_state(result, as_json=as_json)
    return _state_exit(result)


def _print_state(state: TaskState, *, as_json: bool) -> None:
    if as_json:
        _print_json(state.to_dict())
        return
    print(f"task: {state.task_id}")
    print(f"state: {state.current_state}")
    print(f"route: {state.task_class.value} -> {state.selected_model}/{state.selected_reasoning}")
    print(f"thread id: {state.thread_id or 'none'}")
    print(f"checks: {len(state.test_results)}")
    unresolved = sum(not finding.resolved for finding in state.review_findings)
    print(f"review findings: {len(state.review_findings)} ({unresolved} unresolved)")
    _print_findings(state)
    if state.blocker_reason:
        print(f"blocker: {state.blocker_reason}")


def _print_no_next(backlog: BacklogStore, *, as_json: bool) -> int:
    blockers = backlog.blockers()
    document = {
        "status": "blocked" if blockers else "empty",
        "next_task": None,
        "blockers": [
            {"id": task.task_id, "title": task.title, "reason": task.blocker} for task in blockers
        ],
    }
    if as_json:
        _print_json(document)
    else:
        print("no dependency-eligible queued task")
        for task in blockers:
            print(f"blocker {task.task_id}: {task.blocker or 'unspecified'}")
    return _BLOCKED_EXIT if blockers else 0


def _state_exit(state: TaskState | None) -> int:
    if state is not None and has_unresolved_actionable_findings(state):
        return _FAILED_EXIT
    if state is None or state.current_state == "done":
        return 0
    if state.current_state == "blocked":
        return _BLOCKED_EXIT
    if state.current_state == "failed":
        return _FAILED_EXIT
    return 0


def _print_findings(state: TaskState) -> None:
    for finding in state.review_findings:
        if finding.resolved:
            continue
        print(f"finding [{finding.severity.value}] {finding.title}")
        print(f"  evidence: {finding.evidence}")
        print(f"  files: {', '.join(finding.affected_files) or 'none'}")
        if finding.failure_scenario:
            print(f"  scenario: {finding.failure_scenario}")


def _sync_backlog_state(config: HarnessConfig, state: TaskState) -> None:
    if state.backlog_task_id is None:
        return
    backlog = BacklogStore(config.backlog_file)
    if has_unresolved_actionable_findings(state):
        backlog.mark(state.backlog_task_id, "blocked", state.blocker_reason)
    elif state.current_state == "done":
        backlog.mark(state.backlog_task_id, "done")
    elif state.current_state == "blocked":
        backlog.mark(state.backlog_task_id, "blocked", state.blocker_reason)
    elif state.current_state == "failed":
        backlog.mark(state.backlog_task_id, "failed", state.blocker_reason)


def _resume_command(config: HarnessConfig) -> TaskState:
    store = StateStore(config.state_dir)
    persisted = store.load()
    if persisted is not None and persisted.current_state in {"done", "failed"}:
        with store.exclusive_operation():
            current = store.load()
            if current is None:
                raise StateError("persisted task disappeared before backlog reconciliation")
            _sync_backlog_state(config, current)
            return current
    return _workflow(config).resume(
        terminal_callback=lambda state: _sync_backlog_state(config, state)
    )


def _reconcile_persisted_backlog(config: HarnessConfig) -> None:
    store = StateStore(config.state_dir)
    with store.exclusive_operation():
        state = store.load()
        if state is not None:
            _sync_backlog_state(config, state)


def _require_backlog_task_runnable(config: HarnessConfig, task_id: str) -> None:
    backlog = BacklogStore(config.backlog_file)
    task = next((item for item in backlog.load() if item.task_id == task_id), None)
    if task is None:
        raise BacklogError(f"backlog task disappeared before start: {task_id}")
    if not backlog.is_runnable(task):
        raise BacklogError(f"backlog task is no longer runnable: {task_id}")


def _print_json(document: dict[str, Any]) -> None:
    print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))


def _repository_root(start: Path) -> Path:
    return GitInspector(start).snapshot().root
