from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
from autplay_codex.checks import CheckRunner
from autplay_codex.codex_client import CodexExecutionError, CodexRunResult
from autplay_codex.config import HarnessConfig
from autplay_codex.event_log import EventLogger
from autplay_codex.git_safety import GitInspector, GitSnapshot
from autplay_codex.models import (
    CheckResult,
    CheckStatus,
    RoutingDecision,
    TaskClass,
    TaskState,
    TaskStatus,
)
from autplay_codex.state import StateConflictError, StateStore, transition
from autplay_codex.workflow import HarnessWorkflow


class FakeGit(GitInspector):
    def __init__(self, root: Path, dirty_paths: tuple[str, ...] = ()) -> None:
        self.current = GitSnapshot(root, "codex/test", "abc123", dirty_paths)

    def snapshot(self) -> GitSnapshot:
        return self.current

    def changed_files(self) -> tuple[str, ...]:
        return self.current.dirty_paths


class FakeChecks(CheckRunner):
    def __init__(self, batches: list[list[CheckResult]]) -> None:
        self.batches = batches
        self.commands: list[tuple[tuple[str, ...], ...]] = []

    def run(self, commands: Iterable[tuple[str, ...]]) -> list[CheckResult]:
        self.commands.append(tuple(commands))
        if not self.batches:
            raise AssertionError("unexpected check invocation")
        return self.batches.pop(0)


class FakeRunner:
    def __init__(
        self,
        *,
        task_documents: list[dict[str, Any]],
        review_documents: list[dict[str, Any]],
        fail_after_start: bool = False,
    ) -> None:
        self.task_documents = task_documents
        self.review_documents = review_documents
        self.fail_after_start = fail_after_start
        self.task_calls = 0
        self.resume_calls = 0
        self.review_calls = 0

    def run_task(
        self,
        prompt: str,
        decision: RoutingDecision,
        output_schema: dict[str, Any],
        on_thread_started: Callable[[str], None],
    ) -> CodexRunResult:
        del prompt, decision, output_schema
        self.task_calls += 1
        on_thread_started("thread-task")
        if self.fail_after_start:
            raise CodexExecutionError("simulated interruption")
        return _run_result("thread-task", self.task_documents.pop(0))

    def resume_task(
        self,
        thread_id: str,
        prompt: str,
        decision: RoutingDecision,
        output_schema: dict[str, Any],
    ) -> CodexRunResult:
        del prompt, decision, output_schema
        self.resume_calls += 1
        return _run_result(thread_id, self.task_documents.pop(0))

    def run_review(
        self,
        prompt: str,
        *,
        model: str,
        reasoning: str,
        output_schema: dict[str, Any],
        on_thread_started: Callable[[str], None],
    ) -> CodexRunResult:
        del prompt, model, reasoning, output_schema
        self.review_calls += 1
        thread_id = f"thread-review-{self.review_calls}"
        on_thread_started(thread_id)
        return _run_result(thread_id, self.review_documents.pop(0))


def _run_result(thread_id: str, document: dict[str, Any]) -> CodexRunResult:
    return CodexRunResult(
        thread_id=thread_id,
        status="completed",
        final_response=json.dumps(document),
        document=document,
    )


def _task_document(status: str = "done", blocker: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "summary": "result",
        "changed_files": [],
        "checks": [],
        "risks": [],
        "blocker": blocker,
    }


def _review_document(*findings: dict[str, Any]) -> dict[str, Any]:
    return {"summary": "review", "findings": list(findings)}


def _finding(severity: str = "major") -> dict[str, Any]:
    return {
        "severity": severity,
        "title": "Broken invariant",
        "evidence": "A concrete failure",
        "affected_files": ["module.py"],
        "failure_scenario": "The invariant fails on retry",
    }


def _check(status: CheckStatus, name: str = "check") -> CheckResult:
    return CheckResult(
        name=name,
        command=(name,),
        status=status,
        return_code=0 if status is CheckStatus.PASSED else 1,
        duration_ms=1,
        details="ok" if status is CheckStatus.PASSED else "failed",
    )


def _workflow(
    config: HarnessConfig,
    runner: FakeRunner,
    checks: FakeChecks,
    *,
    git: FakeGit | None = None,
) -> HarnessWorkflow:
    return HarnessWorkflow(
        config,
        git=git or FakeGit(config.repo_root),
        state_store=StateStore(config.state_dir),
        runner=runner,
        check_runner=checks,
        logger=EventLogger(config.state_dir, config.repo_root),
    )


def test_full_implementation_test_review_final_cycle_reaches_done(
    harness_config: HarnessConfig,
) -> None:
    runner = FakeRunner(
        task_documents=[_task_document()],
        review_documents=[_review_document()],
    )
    checks = FakeChecks(
        [[_check(CheckStatus.PASSED, "targeted")], [_check(CheckStatus.PASSED, "final")]]
    )

    state = _workflow(harness_config, runner, checks).run_new_task("Add a CLI feature")

    assert isinstance(state, TaskState)
    assert state.current_state == "done"
    assert state.thread_id == "thread-task"
    assert state.review_thread_ids == ["thread-review-1"]
    assert state.tests_executed == ["targeted", "final"]
    assert runner.task_calls == 1
    assert runner.review_calls == 1


def test_milestone_persists_goal_definition_and_checkpoints(
    harness_config: HarnessConfig,
) -> None:
    runner = FakeRunner(
        task_documents=[_task_document()],
        review_documents=[_review_document()],
    )
    checks = FakeChecks(
        [[_check(CheckStatus.PASSED, "targeted")], [_check(CheckStatus.PASSED, "final")]]
    )

    state = _workflow(harness_config, runner, checks).run_new_task(
        "Complete milestone P04",
        milestone_id="P04",
        backlog_task_id="P04",
    )

    assert isinstance(state, TaskState)
    assert state.backlog_task_id == "P04"
    assert state.persisted_goal
    assert state.goal_status == "complete"
    assert state.goal_objective == "Complete milestone P04"
    assert "canonical final checks pass" in state.goal_done_when
    assert state.goal_checkpoints[0] == "queued"
    assert state.goal_checkpoints[-1] == "done"


def test_failed_targeted_check_triggers_bounded_fix_then_review(
    harness_config: HarnessConfig,
) -> None:
    runner = FakeRunner(
        task_documents=[_task_document(), _task_document()],
        review_documents=[_review_document()],
    )
    checks = FakeChecks(
        [
            [_check(CheckStatus.FAILED, "targeted")],
            [_check(CheckStatus.PASSED, "targeted")],
            [_check(CheckStatus.PASSED, "final")],
        ]
    )

    state = _workflow(harness_config, runner, checks).run_new_task("Add a CLI feature")

    assert isinstance(state, TaskState)
    assert state.current_state == "done"
    assert runner.resume_calls == 1
    assert state.attempts == 2


def test_major_review_finding_triggers_fix_and_second_review(
    harness_config: HarnessConfig,
) -> None:
    runner = FakeRunner(
        task_documents=[_task_document(), _task_document()],
        review_documents=[_review_document(_finding()), _review_document()],
    )
    checks = FakeChecks(
        [
            [_check(CheckStatus.PASSED, "targeted-1")],
            [_check(CheckStatus.PASSED, "targeted-2")],
            [_check(CheckStatus.PASSED, "final")],
        ]
    )

    state = _workflow(harness_config, runner, checks).run_new_task("Add a CLI feature")

    assert isinstance(state, TaskState)
    assert state.current_state == "done"
    assert runner.resume_calls == 1
    assert runner.review_calls == 2
    assert len(state.review_findings) == 1
    assert state.review_findings[0].resolved


def test_review_loop_is_bounded_when_major_finding_persists(
    harness_config: HarnessConfig,
) -> None:
    runner = FakeRunner(
        task_documents=[_task_document(), _task_document()],
        review_documents=[_review_document(_finding()), _review_document(_finding())],
    )
    checks = FakeChecks(
        [
            [_check(CheckStatus.PASSED, "targeted-1")],
            [_check(CheckStatus.PASSED, "targeted-2")],
        ]
    )

    state = _workflow(harness_config, runner, checks).run_new_task("Add a CLI feature")

    assert isinstance(state, TaskState)
    assert state.current_state == "failed"
    assert "bounded review loop" in (state.blocker_reason or "")
    assert runner.review_calls == harness_config.max_review_iterations


def test_blocked_agent_result_is_persisted_without_running_checks(
    harness_config: HarnessConfig,
) -> None:
    runner = FakeRunner(
        task_documents=[_task_document("blocked", "external decision required")],
        review_documents=[],
    )
    checks = FakeChecks([])

    state = _workflow(harness_config, runner, checks).run_new_task("Add a CLI feature")

    assert isinstance(state, TaskState)
    assert state.current_state == "blocked"
    assert state.blocker_reason == "external decision required"
    assert not checks.commands


def test_blocker_payload_is_redacted_before_state_persistence(
    harness_config: HarnessConfig,
) -> None:
    runner = FakeRunner(
        task_documents=[
            _task_document(
                "blocked",
                "postgresql://owner:topsecret@db.internal/autplay cannot connect",
            )
        ],
        review_documents=[],
    )

    state = _workflow(harness_config, runner, FakeChecks([])).run_new_task("Add a CLI feature")

    assert isinstance(state, TaskState)
    assert state.current_state == "blocked"
    assert "topsecret" not in (state.blocker_reason or "")
    assert "postgresql://<redacted>@db.internal/autplay" in (state.blocker_reason or "")


def test_standalone_major_review_blocks_done_task_until_resume_fixes_it(
    harness_config: HarnessConfig,
) -> None:
    persisted = _persisted_state(harness_config)
    transition(persisted, TaskStatus.PLANNING)
    transition(persisted, TaskStatus.IMPLEMENTING)
    transition(persisted, TaskStatus.TESTING)
    transition(persisted, TaskStatus.DONE)
    StateStore(harness_config.state_dir).begin(persisted)
    runner = FakeRunner(
        task_documents=[_task_document()],
        review_documents=[_review_document(_finding()), _review_document()],
    )
    checks = FakeChecks(
        [[_check(CheckStatus.PASSED, "targeted")], [_check(CheckStatus.PASSED, "final")]]
    )
    workflow = _workflow(harness_config, runner, checks)

    reviewed = workflow.review_only()
    resumed = workflow.resume()

    assert reviewed.current_state == "blocked"
    assert reviewed.blocker_reason == "independent review found unresolved critical/major defects"
    assert resumed.current_state == "done"
    assert resumed.review_findings[0].resolved
    assert runner.resume_calls == 1
    assert runner.review_calls == 2


def test_terminal_callback_runs_while_operation_lease_is_held(
    harness_config: HarnessConfig,
) -> None:
    runner = FakeRunner(
        task_documents=[_task_document()],
        review_documents=[_review_document()],
    )
    checks = FakeChecks(
        [[_check(CheckStatus.PASSED, "targeted")], [_check(CheckStatus.PASSED, "final")]]
    )
    workflow = _workflow(harness_config, runner, checks)
    callback_states: list[str] = []

    def callback(state: TaskState) -> None:
        callback_states.append(state.current_state)
        with (
            pytest.raises(StateConflictError, match="another harness operation"),
            StateStore(harness_config.state_dir).exclusive_operation(),
        ):
            raise AssertionError("terminal callback ran outside operation lease")

    result = workflow.run_new_task("Add a CLI feature", terminal_callback=callback)

    assert isinstance(result, TaskState)
    assert callback_states == ["done"]


def test_thread_id_is_durable_when_turn_is_interrupted(
    harness_config: HarnessConfig,
) -> None:
    runner = FakeRunner(task_documents=[], review_documents=[], fail_after_start=True)
    workflow = _workflow(harness_config, runner, FakeChecks([]))

    with pytest.raises(CodexExecutionError, match="interruption"):
        workflow.run_new_task("Add a CLI feature")

    state = StateStore(harness_config.state_dir).load()
    assert state is not None
    assert state.thread_id == "thread-task"
    assert state.current_state == "implementing"


def test_resume_from_testing_continues_at_checks_not_implementation(
    harness_config: HarnessConfig,
) -> None:
    state = _persisted_state(harness_config)
    transition(state, TaskStatus.PLANNING)
    transition(state, TaskStatus.IMPLEMENTING)
    transition(state, TaskStatus.TESTING)
    StateStore(harness_config.state_dir).begin(state)
    runner = FakeRunner(task_documents=[], review_documents=[_review_document()])
    checks = FakeChecks(
        [[_check(CheckStatus.PASSED, "targeted")], [_check(CheckStatus.PASSED, "final")]]
    )

    resumed = _workflow(harness_config, runner, checks).resume()

    assert resumed.current_state == "done"
    assert runner.resume_calls == 0
    assert runner.review_calls == 1


def test_completed_task_is_never_restarted_by_resume(harness_config: HarnessConfig) -> None:
    state = _persisted_state(harness_config)
    transition(state, TaskStatus.PLANNING)
    transition(state, TaskStatus.IMPLEMENTING)
    transition(state, TaskStatus.TESTING)
    transition(state, TaskStatus.DONE)
    StateStore(harness_config.state_dir).begin(state)
    workflow = _workflow(
        harness_config,
        FakeRunner(task_documents=[], review_documents=[]),
        FakeChecks([]),
    )

    with pytest.raises(StateConflictError, match="resume is not allowed"):
        workflow.resume()


def _persisted_state(config: HarnessConfig) -> TaskState:
    return TaskState(
        task_id="task-resume",
        description="Add a CLI feature",
        task_class=TaskClass.NORMAL_ENGINEERING,
        selected_model="gpt-5.6-terra",
        selected_reasoning="medium",
        persisted_goal=False,
        thread_id="thread-task",
        repo_root=str(config.repo_root),
        branch="codex/test",
        base_head="abc123",
    )
