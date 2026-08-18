"""Deterministic, explainable model routing for AutPlay engineering tasks."""

from __future__ import annotations

import re

from .config import ModelPolicy
from .models import RoutingDecision, TaskClass

_MILESTONE = re.compile(r"\b(milestone|phase|этап|фаза|p(?:0[0-9]|1[0-4]))\b", re.IGNORECASE)
_COMPLEX = re.compile(
    r"\b(architecture|architectural|migration|schema|database|postgres|room|sync|"
    r"concurren\w*|race|transaction|security|authentication|authorization|vault|"
    r"cross[- ]module|multi[- ]module|protocol|rollback|data loss|destructive|"
    r"архитектур\w*|миграци\w*|схем\w*|синхронизаци\w*|конкурент\w*|"
    r"безопасност\w*|транзакци\w*)\b",
    re.IGNORECASE,
)
_CLEAR = re.compile(
    r"\b(typo|spelling|format|formatting|rename|comment|whitespace|"
    r"опечатк\w*|форматир\w*|переимен\w*|комментари\w*)\b",
    re.IGNORECASE,
)
_AMBIGUOUS = re.compile(
    r"^(fix|improve|change|update|refactor|почини|улучши|измени|обнови|"
    r"отрефактори)(\s+(it|this|that|это|тут|что-нибудь))?[.!?\s]*$",
    re.IGNORECASE,
)
_EXPLORATION = re.compile(
    r"\b(inspect|investigate|explore|research|trace|analy[sz]e|"
    r"исслед\w*|изуч\w*|проанализ\w*|разбер\w*)\b",
    re.IGNORECASE,
)
_ARCHITECTURE = re.compile(
    r"\b(architecture|architectural|tradeoffs?|boundary|design|adr|"
    r"архитектур\w*|границ\w*|проектирован\w*)\b",
    re.IGNORECASE,
)
_PARALLEL = re.compile(
    r"\b(parallel|independent|multiple components|multiple modules|"
    r"параллел\w*|независим\w*|несколько модул\w*)\b",
    re.IGNORECASE,
)
_EXPLICIT_PATH = re.compile(r"(?<!\w)(?:[A-Za-z0-9_.-]+[/\\]){1,}[A-Za-z0-9_.-]+")
_STEP_WORD = re.compile(
    r"\b(add|change|create|fix|implement|migrate|review|test|update|"
    r"добав\w*|измен\w*|созд\w*|исправ\w*|реализ\w*|проверь\w*|тестир\w*)\b",
    re.IGNORECASE,
)


class RoutingError(ValueError):
    """Raised when an explicit routing override is invalid."""


def route_task(
    description: str,
    policy: ModelPolicy,
    *,
    milestone: bool = False,
    model_override: str | None = None,
    reasoning_override: str | None = None,
    persisted_goal_override: bool | None = None,
) -> RoutingDecision:
    """Classify a task and apply explicit model/reasoning overrides last."""

    normalized = " ".join(description.split())
    if not normalized:
        raise RoutingError("task description cannot be empty")
    if len(normalized) > 12_000:
        raise RoutingError("task description exceeds 12000 characters")

    ambiguous = bool(_AMBIGUOUS.fullmatch(normalized) or len(normalized) < 8)
    high_risk = bool(_COMPLEX.search(normalized))
    architecture_judgment = bool(_ARCHITECTURE.search(normalized))
    exploration_needed = bool(_EXPLORATION.search(normalized) or ambiguous)
    parallelizable = bool(_PARALLEL.search(normalized))
    explicit_paths = len(_EXPLICIT_PATH.findall(normalized))
    estimated_steps = len(_STEP_WORD.findall(normalized))
    broad_scope = explicit_paths >= 3 or parallelizable
    is_milestone = bool(milestone or _MILESTONE.search(normalized))

    reasons: list[str] = [
        f"scope={'broad' if broad_scope else 'bounded'}; explicit_paths={explicit_paths}",
        f"ambiguity={'high' if ambiguous else 'normal'}",
        f"risk={'high' if high_risk else 'normal'}",
        f"estimated_steps={'multiple' if estimated_steps >= 3 else 'few'}",
        f"exploration={'needed' if exploration_needed else 'not_signaled'}",
        f"architecture_judgment={'needed' if architecture_judgment else 'not_signaled'}",
        f"parallelizable={'yes' if parallelizable else 'no'}",
    ]
    if is_milestone:
        task_class = TaskClass.MILESTONE
        reasons.append("milestone or product-phase workflow requested")
    elif high_risk or architecture_judgment or broad_scope:
        task_class = TaskClass.COMPLEX_ENGINEERING
        reasons.append("risk, architecture, persistence, or cross-module signal detected")
    elif _CLEAR.search(normalized) and len(normalized) <= 240:
        task_class = TaskClass.CLEAR_REPEATABLE
        reasons.append("bounded mechanical-change signal detected")
    elif ambiguous:
        task_class = TaskClass.COMPLEX_ENGINEERING
        reasons.append("underspecified task is routed conservatively")
    else:
        task_class = TaskClass.NORMAL_ENGINEERING
        reasons.append("ordinary bounded engineering task")

    model, reasoning = policy.for_class(task_class)
    persisted_goal = task_class is TaskClass.MILESTONE
    reasons.append(f"persisted_goal={'yes' if persisted_goal else 'no'}")
    if model_override is not None:
        if not model_override.strip():
            raise RoutingError("model override cannot be empty")
        model = model_override
        reasons.append("explicit model override")
    if reasoning_override is not None:
        allowed = {"minimal", "low", "medium", "high", "xhigh"}
        if reasoning_override not in allowed:
            raise RoutingError("reasoning override must be minimal, low, medium, high, or xhigh")
        reasoning = reasoning_override
        reasons.append("explicit reasoning override")
    if persisted_goal_override is not None:
        persisted_goal = persisted_goal_override
        reasons.append("explicit persisted-goal override")

    return RoutingDecision(
        task_class=task_class,
        model=model,
        reasoning=reasoning,
        persisted_goal=persisted_goal,
        reasons=tuple(reasons),
    )
