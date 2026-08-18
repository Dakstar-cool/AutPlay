"""Bounded implementation, validation, review, fix, and resume orchestration."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from .checks import CheckRunner, checks_passed
from .codex_client import CodexRunner, CodexRunResult, load_json_schema
from .config import HarnessConfig
from .event_log import EventLogger
from .git_safety import (
    GitInspector,
    GitSnapshot,
    assert_dirty_baseline_preserved,
    assert_request_is_non_destructive,
    assert_resume_compatible,
    assert_safe_to_start,
)
from .models import (
    CheckResult,
    ReviewFinding,
    RoutingDecision,
    Severity,
    TaskState,
    TaskStatus,
    has_unresolved_actionable_findings,
)
from .prompts import (
    fix_checks_prompt,
    fix_findings_prompt,
    resume_prompt,
    review_prompt,
    task_prompt,
)
from .routing import route_task
from .state import StateConflictError, StateStore, is_terminal, transition


class WorkflowError(RuntimeError):
    """Raised when a workflow cannot safely continue."""


@dataclass(frozen=True, slots=True)
class DryRunResult:
    """Routing result returned without state or external execution."""

    decision: RoutingDecision


class HarnessWorkflow:
    """Coordinate one active task with crash-safe checkpoints."""

    def __init__(
        self,
        config: HarnessConfig,
        *,
        git: GitInspector,
        state_store: StateStore,
        runner: CodexRunner,
        check_runner: CheckRunner,
        logger: EventLogger,
    ) -> None:
        self.config = config
        self.git = git
        self.state_store = state_store
        self.runner = runner
        self.check_runner = check_runner
        self.logger = logger
        self.task_schema = load_json_schema(config.task_result_schema)
        self.review_schema = load_json_schema(config.review_result_schema)

    def run_new_task(
        self,
        description: str,
        *,
        milestone_id: str | None = None,
        backlog_task_id: str | None = None,
        model_override: str | None = None,
        reasoning_override: str | None = None,
        persisted_goal_override: bool | None = None,
        dry_run: bool = False,
        preflight: Callable[[], None] | None = None,
        terminal_callback: Callable[[TaskState], None] | None = None,
    ) -> TaskState | DryRunResult:
        """Route and execute a new task, or report routing without side effects."""

        decision = route_task(
            description,
            self.config.models,
            milestone=milestone_id is not None,
            model_override=model_override,
            reasoning_override=reasoning_override,
            persisted_goal_override=persisted_goal_override,
        )
        if dry_run:
            return DryRunResult(decision)

        with self.state_store.exclusive_operation():
            if preflight is not None:
                preflight()
            state = self._run_new_task_locked(
                description,
                decision,
                milestone_id=milestone_id,
                backlog_task_id=backlog_task_id,
            )
            if terminal_callback is not None:
                terminal_callback(state)
            return state

    def _run_new_task_locked(
        self,
        description: str,
        decision: RoutingDecision,
        *,
        milestone_id: str | None,
        backlog_task_id: str | None,
    ) -> TaskState:
        assert_request_is_non_destructive(description)
        snapshot = self.git.snapshot()
        assert_safe_to_start(
            snapshot,
            expected_root=self.config.repo_root,
            protected_branches=self.config.protected_branches,
        )
        state = _new_state(
            description,
            decision,
            snapshot,
            milestone_id,
            backlog_task_id,
        )
        self.state_store.begin(state)
        self._emit_state(state, reason="task-created")

        transition(state, TaskStatus.PLANNING)
        self._save(state)
        transition(state, TaskStatus.IMPLEMENTING)
        self._save(state)
        result = self.runner.run_task(
            task_prompt(description, decision, self.config.max_subagents),
            decision,
            self.task_schema,
            lambda thread_id: self._record_task_thread(state, thread_id),
        )
        if not self._apply_agent_result(state, result):
            return state
        return self._complete_pipeline(state, decision, targeted_ready=False)

    def resume(self, terminal_callback: Callable[[TaskState], None] | None = None) -> TaskState:
        """Continue from the last durable checkpoint without rerunning a done task."""

        with self.state_store.exclusive_operation():
            state = self._resume_locked()
            if terminal_callback is not None:
                terminal_callback(state)
            return state

    def _resume_locked(self) -> TaskState:
        state = self.state_store.load()
        if state is None:
            raise WorkflowError("no persisted task exists")
        if is_terminal(state):
            raise StateConflictError(
                f"task {state.task_id} is already {state.current_state}; resume is not allowed"
            )
        snapshot = self.git.snapshot()
        assert_resume_compatible(
            state.repo_root,
            state.branch,
            state.last_head or state.base_head,
            snapshot,
        )
        assert_dirty_baseline_preserved(state.baseline_dirty_fingerprints, snapshot)
        decision = _decision_from_state(state)

        if state.current_state == TaskStatus.TESTING.value:
            return self._complete_pipeline(state, decision, targeted_ready=False)
        if state.current_state == TaskStatus.REVIEWING.value:
            return self._complete_pipeline(state, decision, targeted_ready=True)
        if state.thread_id is None:
            raise WorkflowError("persisted task has no Codex thread id to resume")

        if state.current_state == TaskStatus.QUEUED.value:
            transition(state, TaskStatus.PLANNING)
            self._save(state)
        pending_review_indices = [
            index
            for index, finding in enumerate(state.review_findings)
            if not finding.resolved and finding.severity in {Severity.CRITICAL, Severity.MAJOR}
        ]
        repairing_review = bool(pending_review_indices)
        target = (
            TaskStatus.FIXING
            if state.current_state == TaskStatus.FIXING.value or repairing_review
            else TaskStatus.IMPLEMENTING
        )
        if state.current_state != target.value:
            transition(state, target)
            self._save(state)
        state.blocker_reason = None
        prompt = (
            fix_findings_prompt([state.review_findings[index] for index in pending_review_indices])
            if repairing_review
            else resume_prompt(state, self.config.max_subagents)
        )
        result = self.runner.resume_task(
            state.thread_id,
            prompt,
            decision,
            self.task_schema,
        )
        if not self._apply_agent_result(state, result):
            return state
        return self._complete_pipeline(
            state,
            decision,
            targeted_ready=False,
            pending_resolution_indices=pending_review_indices,
        )

    def review_only(
        self, terminal_callback: Callable[[TaskState], None] | None = None
    ) -> TaskState:
        """Run one independent read-only review without entering a fix loop."""

        with self.state_store.exclusive_operation():
            state = self._review_only_locked()
            if terminal_callback is not None:
                terminal_callback(state)
            return state

    def _review_only_locked(self) -> TaskState:
        state = self.state_store.load()
        if state is None:
            raise WorkflowError("no persisted task exists to review")
        snapshot = self.git.snapshot()
        assert_resume_compatible(
            state.repo_root,
            state.branch,
            state.last_head or state.base_head,
            snapshot,
        )
        assert_dirty_baseline_preserved(state.baseline_dirty_fingerprints, snapshot)
        findings = self._run_review(state)
        state.review_findings.extend(findings)
        if any(finding.severity in {Severity.CRITICAL, Severity.MAJOR} for finding in findings):
            state.blocker_reason = "independent review found unresolved critical/major defects"
            if state.current_state != TaskStatus.BLOCKED.value:
                transition(state, TaskStatus.BLOCKED)
        state.changed_files = list(snapshot.dirty_paths)
        self._save(state)
        return state

    def _complete_pipeline(
        self,
        state: TaskState,
        decision: RoutingDecision,
        *,
        targeted_ready: bool,
        pending_resolution_indices: list[int] | None = None,
    ) -> TaskState:
        fix_attempts = 0
        review_iterations = 0
        pending_resolution_indices = list(pending_resolution_indices or [])
        while review_iterations < self.config.max_review_iterations:
            if not targeted_ready:
                green, fix_attempts = self._ensure_checks_green(
                    state,
                    decision,
                    self.config.checks.targeted(),
                    fix_attempts,
                )
                if not green:
                    return state
                if pending_resolution_indices:
                    for index in pending_resolution_indices:
                        state.review_findings[index] = replace(
                            state.review_findings[index], resolved=True
                        )
                    pending_resolution_indices = []
                    self._save(state)
            if state.current_state != TaskStatus.REVIEWING.value:
                transition(state, TaskStatus.REVIEWING)
                self._save(state)

            findings = self._run_review(state)
            first_new_finding = len(state.review_findings)
            state.review_findings.extend(findings)
            self._save(state)
            review_iterations += 1
            actionable = [
                finding
                for finding in findings
                if finding.severity in {Severity.CRITICAL, Severity.MAJOR}
            ]
            if actionable:
                if (
                    review_iterations >= self.config.max_review_iterations
                    or fix_attempts >= self.config.max_fix_iterations
                ):
                    state.blocker_reason = (
                        "bounded review loop exhausted with critical/major findings"
                    )
                    transition(state, TaskStatus.FAILED)
                    self._save(state)
                    return state
                transition(state, TaskStatus.FIXING)
                self._save(state)
                fix_attempts += 1
                if not self._resume_for_fix(state, decision, fix_findings_prompt(actionable)):
                    return state
                pending_resolution_indices = [
                    first_new_finding + index
                    for index, finding in enumerate(findings)
                    if finding.severity in {Severity.CRITICAL, Severity.MAJOR}
                ]
                targeted_ready = False
                continue

            transition(state, TaskStatus.TESTING)
            self._save(state)
            final_results = self.check_runner.run(self.config.checks.final())
            self._record_checks(state, final_results)
            if checks_passed(final_results):
                transition(state, TaskStatus.DONE)
                state.blocker_reason = None
                self._save(state)
                return state
            if (
                review_iterations >= self.config.max_review_iterations
                or fix_attempts >= self.config.max_fix_iterations
            ):
                state.blocker_reason = "bounded fix loop exhausted after final checks failed"
                transition(state, TaskStatus.FAILED)
                self._save(state)
                return state
            transition(state, TaskStatus.FIXING)
            self._save(state)
            fix_attempts += 1
            if not self._resume_for_fix(state, decision, fix_checks_prompt(final_results)):
                return state
            targeted_ready = False

        state.blocker_reason = "bounded review loop exhausted"
        if state.current_state != TaskStatus.FAILED.value:
            transition(state, TaskStatus.FAILED)
        self._save(state)
        return state

    def _ensure_checks_green(
        self,
        state: TaskState,
        decision: RoutingDecision,
        commands: tuple[tuple[str, ...], ...],
        fix_attempts: int,
    ) -> tuple[bool, int]:
        if state.current_state != TaskStatus.TESTING.value:
            transition(state, TaskStatus.TESTING)
            self._save(state)
        while True:
            results = self.check_runner.run(commands)
            self._record_checks(state, results)
            if checks_passed(results):
                return True, fix_attempts
            if fix_attempts >= self.config.max_fix_iterations:
                state.blocker_reason = "bounded fix loop exhausted while targeted checks are red"
                transition(state, TaskStatus.FAILED)
                self._save(state)
                return False, fix_attempts
            transition(state, TaskStatus.FIXING)
            self._save(state)
            fix_attempts += 1
            if not self._resume_for_fix(state, decision, fix_checks_prompt(results)):
                return False, fix_attempts

    def _resume_for_fix(self, state: TaskState, decision: RoutingDecision, prompt: str) -> bool:
        if state.thread_id is None:
            raise WorkflowError("cannot fix without a persisted implementation thread")
        result = self.runner.resume_task(
            state.thread_id,
            prompt,
            decision,
            self.task_schema,
        )
        return self._apply_agent_result(state, result)

    def _apply_agent_result(self, state: TaskState, result: CodexRunResult) -> bool:
        state.attempts += 1
        state.thread_id = result.thread_id
        state.final_response = self.logger.redactor.text(result.final_response)
        state.changed_files = list(self.git.changed_files())
        status = result.document.get("status")
        if status == "blocked":
            blocker = result.document.get("blocker")
            state.blocker_reason = (
                self.logger.redactor.text(blocker)
                if isinstance(blocker, str)
                else "Codex reported blocked"
            )
            transition(state, TaskStatus.BLOCKED)
            self._save(state)
            return False
        if status == "failed":
            state.blocker_reason = "Codex implementation turn reported failure"
            transition(state, TaskStatus.FAILED)
            self._save(state)
            return False
        if status != "done":
            raise WorkflowError("structured task result has an unsupported status")
        transition(state, TaskStatus.TESTING)
        self._save(state)
        return True

    def _run_review(self, state: TaskState) -> list[ReviewFinding]:
        result = self.runner.run_review(
            review_prompt(state),
            model=self.config.models.review_model,
            reasoning=self.config.models.review_reasoning,
            output_schema=self.review_schema,
            on_thread_started=lambda thread_id: self._record_review_thread(state, thread_id),
        )
        raw_findings = result.document.get("findings")
        if not isinstance(raw_findings, list) or not all(
            isinstance(item, dict) for item in raw_findings
        ):
            raise WorkflowError("structured review result has invalid findings")
        findings = [self._redact_finding(ReviewFinding.from_dict(item)) for item in raw_findings]
        self.logger.emit(
            "review-completed",
            task_id=state.task_id,
            thread_id=result.thread_id,
            finding_count=len(findings),
            status="completed",
        )
        return findings

    def _record_task_thread(self, state: TaskState, thread_id: str) -> None:
        state.thread_id = thread_id
        self._save(state)

    def _record_review_thread(self, state: TaskState, thread_id: str) -> None:
        state.review_thread_ids.append(thread_id)
        self._save(state)

    def _record_checks(self, state: TaskState, results: list[CheckResult]) -> None:
        state.test_results.extend(results)
        state.tests_executed.extend(result.name for result in results)
        for result in results:
            self.logger.emit(
                "check-completed",
                task_id=state.task_id,
                check=result.name,
                status=result.status.value,
            )
        self._save(state)

    def _save(self, state: TaskState) -> None:
        snapshot = self.git.snapshot()
        assert_dirty_baseline_preserved(state.baseline_dirty_fingerprints, snapshot)
        state.changed_files = list(snapshot.dirty_paths)
        state.last_head = snapshot.head
        _checkpoint_goal(state)
        self.state_store.save(state)
        self._emit_state(state, reason="checkpoint")

    def _redact_finding(self, finding: ReviewFinding) -> ReviewFinding:
        return replace(
            finding,
            title=self.logger.redactor.text(finding.title),
            evidence=self.logger.redactor.text(finding.evidence),
            affected_files=tuple(
                self.logger.redactor.text(path) for path in finding.affected_files
            ),
            failure_scenario=(
                self.logger.redactor.text(finding.failure_scenario)
                if finding.failure_scenario is not None
                else None
            ),
        )

    def _emit_state(self, state: TaskState, *, reason: str) -> None:
        self.logger.emit(
            "state-changed",
            task_id=state.task_id,
            milestone_id=state.milestone_id,
            state=state.current_state,
            task_class=state.task_class.value,
            model=state.selected_model,
            reasoning=state.selected_reasoning,
            routing_reason="; ".join(state.routing_reasons),
            thread_id=state.thread_id,
            attempt=state.attempts,
            reason=reason,
        )


def _new_state(
    description: str,
    decision: RoutingDecision,
    snapshot: GitSnapshot,
    milestone_id: str | None,
    backlog_task_id: str | None,
) -> TaskState:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return TaskState(
        task_id=f"task-{timestamp}-{secrets.token_hex(4)}",
        backlog_task_id=backlog_task_id,
        milestone_id=milestone_id,
        description=description,
        task_class=decision.task_class,
        selected_model=decision.model,
        selected_reasoning=decision.reasoning,
        persisted_goal=decision.persisted_goal,
        routing_reasons=list(decision.reasons),
        repo_root=str(snapshot.root),
        branch=snapshot.branch,
        base_head=snapshot.head,
        last_head=snapshot.head,
        baseline_dirty=list(snapshot.dirty_paths),
        baseline_dirty_fingerprints=dict(snapshot.dirty_fingerprints),
        goal_status="active" if decision.persisted_goal else None,
        goal_objective=description if decision.persisted_goal else None,
        goal_done_when=(
            [
                "implementation returns a structured done result",
                "targeted checks pass",
                "independent review has no unresolved critical or major findings",
                "canonical final checks pass",
            ]
            if decision.persisted_goal
            else []
        ),
        goal_checkpoints=[TaskStatus.QUEUED.value] if decision.persisted_goal else [],
    )


def _checkpoint_goal(state: TaskState) -> None:
    if not state.persisted_goal:
        return
    if has_unresolved_actionable_findings(state):
        state.goal_status = "blocked"
        if not state.goal_checkpoints or state.goal_checkpoints[-1] != TaskStatus.BLOCKED.value:
            state.goal_checkpoints.append(TaskStatus.BLOCKED.value)
        return
    goal_status = {
        TaskStatus.DONE.value: "complete",
        TaskStatus.BLOCKED.value: "blocked",
        TaskStatus.FAILED.value: "failed",
    }.get(state.current_state, "active")
    state.goal_status = goal_status
    if not state.goal_checkpoints or state.goal_checkpoints[-1] != state.current_state:
        state.goal_checkpoints.append(state.current_state)


def _decision_from_state(state: TaskState) -> RoutingDecision:
    return RoutingDecision(
        task_class=state.task_class,
        model=state.selected_model,
        reasoning=state.selected_reasoning,
        persisted_goal=state.persisted_goal,
        reasons=("restored from persisted task state",),
    )
