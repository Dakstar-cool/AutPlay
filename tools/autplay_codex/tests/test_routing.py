from __future__ import annotations

from autplay_codex.config import HarnessConfig
from autplay_codex.models import TaskClass
from autplay_codex.routing import RoutingError, route_task


def test_narrow_mechanical_task_routes_to_luna(harness_config: HarnessConfig) -> None:
    decision = route_task("Fix one README typo", harness_config.models)

    assert decision.task_class is TaskClass.CLEAR_REPEATABLE
    assert decision.model == "gpt-5.6-luna"
    assert decision.reasoning == "low"
    assert not decision.persisted_goal


def test_ordinary_feature_routes_to_terra(harness_config: HarnessConfig) -> None:
    decision = route_task("Add a bounded CLI flag with unit tests", harness_config.models)

    assert decision.task_class is TaskClass.NORMAL_ENGINEERING
    assert decision.model == "gpt-5.6-terra"
    assert decision.reasoning == "medium"


def test_cross_module_risky_task_routes_to_sol(harness_config: HarnessConfig) -> None:
    decision = route_task(
        "Implement a cross-module database migration with concurrency safety",
        harness_config.models,
    )

    assert decision.task_class is TaskClass.COMPLEX_ENGINEERING
    assert decision.model == "gpt-5.6-sol"
    assert decision.reasoning == "high"


def test_milestone_routes_to_sol_xhigh_and_persisted_goal(
    harness_config: HarnessConfig,
) -> None:
    decision = route_task("Complete milestone M3", harness_config.models, milestone=True)

    assert decision.task_class is TaskClass.MILESTONE
    assert decision.model == "gpt-5.6-sol"
    assert decision.reasoning == "xhigh"
    assert decision.persisted_goal


def test_manual_overrides_take_priority(harness_config: HarnessConfig) -> None:
    decision = route_task(
        "Fix one typo",
        harness_config.models,
        model_override="gpt-5.6-sol",
        reasoning_override="xhigh",
        persisted_goal_override=True,
    )

    assert decision.task_class is TaskClass.CLEAR_REPEATABLE
    assert decision.model == "gpt-5.6-sol"
    assert decision.reasoning == "xhigh"
    assert decision.persisted_goal
    assert "explicit model override" in decision.reasons


def test_ambiguous_task_never_routes_to_cheap_mode(harness_config: HarnessConfig) -> None:
    decision = route_task("Improve it", harness_config.models)

    assert decision.task_class is TaskClass.COMPLEX_ENGINEERING
    assert decision.model == "gpt-5.6-sol"
    assert "ambiguity=high" in decision.reasons
    assert "exploration=needed" in decision.reasons


def test_ultra_is_rejected_as_reasoning_override(harness_config: HarnessConfig) -> None:
    try:
        route_task("Fix one typo", harness_config.models, reasoning_override="ultra")
    except RoutingError as exc:
        assert "xhigh" in str(exc)
    else:
        raise AssertionError("ultra reasoning override was accepted")
