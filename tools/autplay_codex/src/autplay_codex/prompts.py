"""Bounded prompt construction for SDK-driven task and review turns."""

from __future__ import annotations

from .models import CheckResult, ReviewFinding, RoutingDecision, TaskState


def task_prompt(description: str, decision: RoutingDecision, max_subagents: int) -> str:
    """Build an implementation prompt that preserves repository phase boundaries."""

    goal_clause = (
        "Maintain a persisted milestone goal and explicit checkpoints in the harness state."
        if decision.persisted_goal
        else "Keep this as a bounded task; do not widen it into a milestone."
    )
    return f"""Work on the following AutPlay task:

{description}

Routing selected {decision.task_class.value}, model {decision.model},
reasoning {decision.reasoning}.
{goal_clause}

Before changing code:
- read the applicable AGENTS.md chain;
- inspect the relevant implementation and tests;
- read the explicit current product phase prompt, latest handoff, and relevant design inputs;
- identify the smallest safe scope;
- do not start a future P00-P14 phase implicitly.

Constraints:
- preserve behavior outside the task scope and preserve pre-existing user changes;
- keep product-domain code independent from the Codex harness;
- do not merge a protected branch, force-push, deploy, or perform destructive operations;
- do not use real credentials, paid resources, or external writes;
- use existing abstractions when adequate.
- use no more than {max_subagents} subagents, only for independent bounded work.

Implement the requested change and run the cheapest relevant targeted checks.
Return the required structured task result. The harness will independently run
its configured checks and review afterward.
"""


def resume_prompt(state: TaskState, max_subagents: int) -> str:
    """Continue an interrupted implementation without replaying destructive work."""

    return f"""Resume AutPlay task {state.task_id} from checkpoint {state.current_state}.

Re-read the applicable AGENTS.md and inspect the current Git diff before acting.
Do not repeat an already completed or destructive action. Preserve pre-existing
dirty paths recorded by the harness. Continue only the unfinished in-scope work,
use no more than {max_subagents} subagents for independent bounded work, run the
smallest relevant checks, and return the required structured task result.
"""


def fix_checks_prompt(results: list[CheckResult]) -> str:
    """Ask the implementation thread to fix concrete failed harness checks."""

    failures = "\n\n".join(
        f"Check {result.name} failed (return code {result.return_code}):\n{result.details}"
        for result in results
        if result.status.value == "failed"
    )
    return f"""The harness-owned targeted checks failed:

{failures}

Fix only the real in-scope causes. Do not weaken, skip, or delete required checks.
Inspect the diff before editing and return the required structured task result.
"""


def review_prompt(state: TaskState) -> str:
    """Build an independent read-only review prompt."""

    return f"""Review AutPlay task {state.task_id} against its request,
repository instructions, applicable architecture, and Definition of Done.

Task request:
{state.description}

Base commit: {state.base_head}
Current branch: {state.branch}

Do not modify files. Review the current branch and working tree. Prioritize
correctness, regressions, data loss, concurrency, security, async lifecycle bugs,
canonical-data violations, missing error handling, missing tests, and
phase/architecture boundary violations.

Return only structured findings. For each real finding provide severity, evidence,
affected files, and a concrete failure scenario when possible. Do not report
style-only issues unless they hide a defect.
"""


def fix_findings_prompt(findings: list[ReviewFinding]) -> str:
    """Ask the implementation thread to resolve critical and major findings only."""

    actionable = [
        finding for finding in findings if finding.severity.value in {"critical", "major"}
    ]
    details = "\n\n".join(
        (
            f"{finding.severity.value.upper()}: {finding.title}\n"
            f"Evidence: {finding.evidence}\n"
            f"Files: {', '.join(finding.affected_files) or '<not specified>'}\n"
            f"Scenario: {finding.failure_scenario or '<not specified>'}"
        )
        for finding in actionable
    )
    return f"""Independent read-only review found these critical or major defects:

{details}

Verify each finding, fix the real in-scope defects, and add or adjust tests that
prove the behavior. Do not perform unrelated cleanup or weaken required checks.
Return the required structured task result.
"""
