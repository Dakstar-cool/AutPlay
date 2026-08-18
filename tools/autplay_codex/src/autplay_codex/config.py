"""Validated project configuration for the AutPlay Codex harness."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import TaskClass

_REASONING_VALUES = {"minimal", "low", "medium", "high", "xhigh"}
_LOG_LEVELS = {"debug", "info", "warning", "error", "off"}


class ConfigurationError(RuntimeError):
    """Raised when project harness configuration is unsafe or malformed."""


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    """Model/reasoning pair for each routing class and independent review."""

    routes: dict[TaskClass, tuple[str, str]]
    review_model: str
    review_reasoning: str

    def for_class(self, task_class: TaskClass) -> tuple[str, str]:
        return self.routes[task_class]


@dataclass(frozen=True, slots=True)
class CheckPolicy:
    """Shell-free command vectors selected for the current platform."""

    targeted_windows: tuple[tuple[str, ...], ...]
    targeted_posix: tuple[tuple[str, ...], ...]
    final_windows: tuple[tuple[str, ...], ...]
    final_posix: tuple[tuple[str, ...], ...]

    def targeted(self) -> tuple[tuple[str, ...], ...]:
        return self.targeted_windows if os.name == "nt" else self.targeted_posix

    def final(self) -> tuple[tuple[str, ...], ...]:
        return self.final_windows if os.name == "nt" else self.final_posix


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """All validated harness configuration resolved against one repository."""

    repo_root: Path
    config_path: Path
    state_dir: Path
    backlog_file: Path
    task_result_schema: Path
    review_result_schema: Path
    max_review_iterations: int
    max_fix_iterations: int
    max_subagents: int
    command_timeout_seconds: int
    log_level: str
    protected_branches: frozenset[str]
    models: ModelPolicy
    checks: CheckPolicy


def load_config(repo_root: Path, config_path: Path | None = None) -> HarnessConfig:
    """Load and validate the checked-in harness configuration."""

    root = repo_root.resolve()
    path = (config_path or root / "autplay-codex.toml").resolve()
    _require_within(root, path, "configuration file")
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot load {path.name}: {exc}") from exc

    harness = _table(document, "harness")
    models = _table(document, "models")
    checks = _table(document, "checks")
    state_dir = _resolve_relative(root, _string(harness, "state_dir"), "state_dir")
    if state_dir == root:
        raise ConfigurationError("state_dir cannot be the repository root")

    policy = ModelPolicy(
        routes={
            TaskClass.CLEAR_REPEATABLE: _model_pair(models, "clear_repeatable"),
            TaskClass.NORMAL_ENGINEERING: _model_pair(models, "normal_engineering"),
            TaskClass.COMPLEX_ENGINEERING: _model_pair(models, "complex_engineering"),
            TaskClass.MILESTONE: _model_pair(models, "milestone"),
        },
        review_model=_string(models, "review_model"),
        review_reasoning=_reasoning(models, "review_reasoning"),
    )
    protected = harness.get("protected_branches")
    if (
        not isinstance(protected, list)
        or not protected
        or not all(isinstance(item, str) and item.strip() for item in protected)
    ):
        raise ConfigurationError("protected_branches must be a non-empty array of strings")

    return HarnessConfig(
        repo_root=root,
        config_path=path,
        state_dir=state_dir,
        backlog_file=_resolve_relative(
            root, _string(harness, "backlog_file"), "backlog_file", require_file=True
        ),
        task_result_schema=_resolve_relative(
            root,
            _string(harness, "task_result_schema"),
            "task_result_schema",
            require_file=True,
        ),
        review_result_schema=_resolve_relative(
            root,
            _string(harness, "review_result_schema"),
            "review_result_schema",
            require_file=True,
        ),
        max_review_iterations=_bounded_int(harness, "max_review_iterations", 1, 5),
        max_fix_iterations=_bounded_int(harness, "max_fix_iterations", 0, 5),
        max_subagents=_bounded_int(harness, "max_subagents", 0, 8),
        command_timeout_seconds=_bounded_int(harness, "command_timeout_seconds", 1, 7200),
        log_level=_choice(harness, "log_level", _LOG_LEVELS),
        protected_branches=frozenset(item.casefold() for item in protected),
        models=policy,
        checks=CheckPolicy(
            targeted_windows=_commands(checks, "targeted_windows"),
            targeted_posix=_commands(checks, "targeted_posix"),
            final_windows=_commands(checks, "final_windows"),
            final_posix=_commands(checks, "final_posix"),
        ),
    )


def _table(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"{key} must be a TOML table")
    return value


def _string(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty string")
    return value


def _reasoning(document: dict[str, Any], key: str) -> str:
    value = _string(document, key)
    if value not in _REASONING_VALUES:
        choices = ", ".join(sorted(_REASONING_VALUES))
        raise ConfigurationError(f"{key} must be one of: {choices}")
    return value


def _choice(document: dict[str, Any], key: str, choices: set[str]) -> str:
    value = _string(document, key).casefold()
    if value not in choices:
        allowed = ", ".join(sorted(choices))
        raise ConfigurationError(f"{key} must be one of: {allowed}")
    return value


def _model_pair(document: dict[str, Any], prefix: str) -> tuple[str, str]:
    return _string(document, f"{prefix}_model"), _reasoning(document, f"{prefix}_reasoning")


def _bounded_int(document: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ConfigurationError(f"{key} must be an integer from {minimum} through {maximum}")
    return value


def _commands(document: dict[str, Any], key: str) -> tuple[tuple[str, ...], ...]:
    value = document.get(key)
    if not isinstance(value, list):
        raise ConfigurationError(f"{key} must be an array of command arrays")
    result: list[tuple[str, ...]] = []
    for index, command in enumerate(value):
        if (
            not isinstance(command, list)
            or not command
            or len(command) > 64
            or not all(isinstance(item, str) and item for item in command)
        ):
            raise ConfigurationError(f"{key}[{index}] must be a bounded non-empty string array")
        if any(len(item) > 4096 for item in command):
            raise ConfigurationError(f"{key}[{index}] contains an oversized argument")
        result.append(tuple(command))
    return tuple(result)


def _resolve_relative(
    root: Path,
    value: str,
    label: str,
    *,
    require_file: bool = False,
) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        raise ConfigurationError(f"{label} must be repository-relative")
    resolved = (root / candidate).resolve()
    _require_within(root, resolved, label)
    if require_file and not resolved.is_file():
        raise ConfigurationError(f"{label} does not exist: {value}")
    return resolved


def _require_within(root: Path, candidate: Path, label: str) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(f"{label} escapes the repository root") from exc
