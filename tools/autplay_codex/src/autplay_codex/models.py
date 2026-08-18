"""Typed values persisted and exchanged by the development harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class TaskClass(StrEnum):
    """Deterministic routing classes."""

    CLEAR_REPEATABLE = "CLEAR_REPEATABLE"
    NORMAL_ENGINEERING = "NORMAL_ENGINEERING"
    COMPLEX_ENGINEERING = "COMPLEX_ENGINEERING"
    MILESTONE = "MILESTONE"


class TaskStatus(StrEnum):
    """Persisted workflow states."""

    QUEUED = "queued"
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    REVIEWING = "reviewing"
    FIXING = "fixing"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"


class CheckStatus(StrEnum):
    """Result of a harness-owned validation command."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Severity(StrEnum):
    """Independent review finding severity."""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFORMATIONAL = "informational"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Model and reasoning decision with explainable routing evidence."""

    task_class: TaskClass
    model: str
    reasoning: str
    persisted_goal: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Bounded, redacted command evidence."""

    name: str
    command: tuple[str, ...]
    status: CheckStatus
    return_code: int | None
    duration_ms: int
    details: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "status": self.status.value,
            "return_code": self.return_code,
            "duration_ms": self.duration_ms,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CheckResult:
        return cls(
            name=_required_str(value, "name"),
            command=tuple(_string_list(value.get("command", []), "command")),
            status=CheckStatus(_required_str(value, "status")),
            return_code=_optional_int(value.get("return_code"), "return_code"),
            duration_ms=_required_int(value, "duration_ms"),
            details=_required_str(value, "details"),
        )


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """One structured independent-review finding."""

    severity: Severity
    title: str
    evidence: str
    affected_files: tuple[str, ...]
    failure_scenario: str | None = None
    resolved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "title": self.title,
            "evidence": self.evidence,
            "affected_files": list(self.affected_files),
            "failure_scenario": self.failure_scenario,
            "resolved": self.resolved,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReviewFinding:
        scenario = value.get("failure_scenario")
        if scenario is not None and not isinstance(scenario, str):
            raise ValueError("failure_scenario must be a string or null")
        resolved = value.get("resolved", False)
        if not isinstance(resolved, bool):
            raise ValueError("resolved must be a boolean")
        return cls(
            severity=Severity(_required_str(value, "severity")),
            title=_required_str(value, "title"),
            evidence=_required_str(value, "evidence"),
            affected_files=tuple(_string_list(value.get("affected_files", []), "affected_files")),
            failure_scenario=scenario,
            resolved=resolved,
        )


@dataclass(slots=True)
class TaskState:
    """Crash-safe state for one active or most recently completed task."""

    task_id: str
    description: str
    task_class: TaskClass
    selected_model: str
    selected_reasoning: str
    persisted_goal: bool
    routing_reasons: list[str] = field(default_factory=list)
    backlog_task_id: str | None = None
    current_state: str = TaskStatus.QUEUED.value
    milestone_id: str | None = None
    thread_id: str | None = None
    review_thread_ids: list[str] = field(default_factory=list)
    attempts: int = 0
    tests_executed: list[str] = field(default_factory=list)
    test_results: list[CheckResult] = field(default_factory=list)
    review_findings: list[ReviewFinding] = field(default_factory=list)
    blocker_reason: str | None = None
    changed_files: list[str] = field(default_factory=list)
    repo_root: str = ""
    branch: str = ""
    base_head: str = ""
    last_head: str = ""
    baseline_dirty: list[str] = field(default_factory=list)
    baseline_dirty_fingerprints: dict[str, str] = field(default_factory=dict)
    goal_status: str | None = None
    goal_objective: str | None = None
    goal_done_when: list[str] = field(default_factory=list)
    goal_checkpoints: list[str] = field(default_factory=list)
    final_response: str | None = None
    created_at: str = field(default_factory=lambda: utc_now())
    updated_at: str = field(default_factory=lambda: utc_now())
    schema_version: int = 1
    revision: int = 0
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "backlog_task_id": self.backlog_task_id,
            "milestone_id": self.milestone_id,
            "description": self.description,
            "task_class": self.task_class.value,
            "selected_model": self.selected_model,
            "selected_reasoning": self.selected_reasoning,
            "persisted_goal": self.persisted_goal,
            "routing_reasons": list(self.routing_reasons),
            "thread_id": self.thread_id,
            "review_thread_ids": list(self.review_thread_ids),
            "attempts": self.attempts,
            "current_state": self.current_state,
            "tests_executed": list(self.tests_executed),
            "test_results": [result.to_dict() for result in self.test_results],
            "review_findings": [finding.to_dict() for finding in self.review_findings],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "blocker_reason": self.blocker_reason,
            "changed_files": list(self.changed_files),
            "repo_root": self.repo_root,
            "branch": self.branch,
            "base_head": self.base_head,
            "last_head": self.last_head,
            "baseline_dirty": list(self.baseline_dirty),
            "baseline_dirty_fingerprints": dict(self.baseline_dirty_fingerprints),
            "goal_status": self.goal_status,
            "goal_objective": self.goal_objective,
            "goal_done_when": list(self.goal_done_when),
            "goal_checkpoints": list(self.goal_checkpoints),
            "final_response": self.final_response,
            "revision": self.revision,
        }
        for key, value in self.extensions.items():
            if key not in document:
                document[key] = value
        return document

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskState:
        known = {
            "schema_version",
            "task_id",
            "backlog_task_id",
            "milestone_id",
            "description",
            "task_class",
            "selected_model",
            "selected_reasoning",
            "persisted_goal",
            "routing_reasons",
            "thread_id",
            "review_thread_ids",
            "attempts",
            "current_state",
            "tests_executed",
            "test_results",
            "review_findings",
            "created_at",
            "updated_at",
            "blocker_reason",
            "changed_files",
            "repo_root",
            "branch",
            "base_head",
            "last_head",
            "baseline_dirty",
            "baseline_dirty_fingerprints",
            "goal_status",
            "goal_objective",
            "goal_done_when",
            "goal_checkpoints",
            "final_response",
            "revision",
        }
        backlog_task_id = _optional_str(value.get("backlog_task_id"), "backlog_task_id")
        milestone_id = _optional_str(value.get("milestone_id"), "milestone_id")
        thread_id = _optional_str(value.get("thread_id"), "thread_id")
        blocker_reason = _optional_str(value.get("blocker_reason"), "blocker_reason")
        final_response = _optional_str(value.get("final_response"), "final_response")
        goal_status = _optional_str(value.get("goal_status"), "goal_status")
        goal_objective = _optional_str(value.get("goal_objective"), "goal_objective")
        base_head = _required_str(value, "base_head")
        persisted_goal = value.get("persisted_goal")
        if not isinstance(persisted_goal, bool):
            raise ValueError("persisted_goal must be a boolean")
        test_values = value.get("test_results", [])
        review_values = value.get("review_findings", [])
        if not isinstance(test_values, list) or not all(
            isinstance(item, dict) for item in test_values
        ):
            raise ValueError("test_results must be an array of objects")
        if not isinstance(review_values, list) or not all(
            isinstance(item, dict) for item in review_values
        ):
            raise ValueError("review_findings must be an array of objects")
        return cls(
            schema_version=_required_int(value, "schema_version"),
            revision=_int_default(value.get("revision"), "revision", 0),
            task_id=_required_str(value, "task_id"),
            backlog_task_id=backlog_task_id,
            milestone_id=milestone_id,
            description=_required_str(value, "description"),
            task_class=TaskClass(_required_str(value, "task_class")),
            selected_model=_required_str(value, "selected_model"),
            selected_reasoning=_required_str(value, "selected_reasoning"),
            persisted_goal=persisted_goal,
            routing_reasons=_string_list(value.get("routing_reasons", []), "routing_reasons"),
            thread_id=thread_id,
            review_thread_ids=_string_list(value.get("review_thread_ids", []), "review_thread_ids"),
            attempts=_required_int(value, "attempts"),
            current_state=_required_str(value, "current_state"),
            tests_executed=_string_list(value.get("tests_executed", []), "tests_executed"),
            test_results=[CheckResult.from_dict(item) for item in test_values],
            review_findings=[ReviewFinding.from_dict(item) for item in review_values],
            created_at=_required_str(value, "created_at"),
            updated_at=_required_str(value, "updated_at"),
            blocker_reason=blocker_reason,
            changed_files=_string_list(value.get("changed_files", []), "changed_files"),
            repo_root=_required_str(value, "repo_root"),
            branch=_required_str(value, "branch"),
            base_head=base_head,
            last_head=_string_default(value.get("last_head"), "last_head", base_head),
            baseline_dirty=_string_list(value.get("baseline_dirty", []), "baseline_dirty"),
            baseline_dirty_fingerprints=_string_dict(
                value.get("baseline_dirty_fingerprints", {}),
                "baseline_dirty_fingerprints",
            ),
            goal_status=goal_status,
            goal_objective=goal_objective,
            goal_done_when=_string_list(value.get("goal_done_when", []), "goal_done_when"),
            goal_checkpoints=_string_list(value.get("goal_checkpoints", []), "goal_checkpoints"),
            final_response=final_response,
            extensions={key: item for key, item in value.items() if key not in known},
        )


def utc_now() -> str:
    """Return a stable UTC timestamp for state documents."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def has_unresolved_actionable_findings(state: TaskState) -> bool:
    """Return whether an independent critical/major finding still blocks work."""

    return any(
        not finding.resolved and finding.severity in {Severity.CRITICAL, Severity.MAJOR}
        for finding in state.review_findings
    )


def _required_str(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string")
    return item


def _optional_str(value: Any, key: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _required_int(value: dict[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise ValueError(f"{key} must be an integer")
    return item


def _int_default(value: Any, key: str, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _string_default(value: Any, key: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_int(value: Any, key: str) -> int | None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"{key} must be an integer or null")
    return value


def _string_list(value: Any, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be an array of strings")
    return list(value)


def _string_dict(value: Any, key: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(item_key, str) and isinstance(item, str) for item_key, item in value.items()
    ):
        raise ValueError(f"{key} must be an object with string values")
    return dict(value)
