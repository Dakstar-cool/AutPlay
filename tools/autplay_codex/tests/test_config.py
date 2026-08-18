from __future__ import annotations

from pathlib import Path

import pytest
from autplay_codex.config import ConfigurationError, load_config
from autplay_codex.models import TaskClass


def _write_project(tmp_path: Path, *, state_dir: str = ".state", reasoning: str = "high") -> None:
    (tmp_path / "backlog.json").write_text("{}", encoding="utf-8")
    (tmp_path / "task.json").write_text("{}", encoding="utf-8")
    (tmp_path / "review.json").write_text("{}", encoding="utf-8")
    (tmp_path / "autplay-codex.toml").write_text(
        f"""
[harness]
state_dir = "{state_dir}"
backlog_file = "backlog.json"
task_result_schema = "task.json"
review_result_schema = "review.json"
max_review_iterations = 2
max_fix_iterations = 2
max_subagents = 3
command_timeout_seconds = 60
log_level = "info"
protected_branches = ["main", "master"]

[models]
clear_repeatable_model = "luna"
clear_repeatable_reasoning = "low"
normal_engineering_model = "terra"
normal_engineering_reasoning = "medium"
complex_engineering_model = "sol"
complex_engineering_reasoning = "{reasoning}"
milestone_model = "sol"
milestone_reasoning = "xhigh"
review_model = "terra"
review_reasoning = "high"

[checks]
targeted_windows = [["uv", "run", "pytest"]]
targeted_posix = [["uv", "run", "pytest"]]
final_windows = [["powershell", "check.ps1"]]
final_posix = [["bash", "check.sh"]]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_load_config_resolves_paths_and_models(tmp_path: Path) -> None:
    _write_project(tmp_path)

    config = load_config(tmp_path)

    assert config.state_dir == tmp_path / ".state"
    assert config.models.for_class(TaskClass.COMPLEX_ENGINEERING) == ("sol", "high")
    assert config.checks.targeted()


def test_state_directory_cannot_escape_repository(tmp_path: Path) -> None:
    _write_project(tmp_path, state_dir="../outside")

    with pytest.raises(ConfigurationError, match="escapes"):
        load_config(tmp_path)


def test_reasoning_values_follow_codex_config_reference(tmp_path: Path) -> None:
    _write_project(tmp_path, reasoning="ultra")

    with pytest.raises(ConfigurationError, match="xhigh"):
        load_config(tmp_path)


def test_event_log_level_is_validated(tmp_path: Path) -> None:
    _write_project(tmp_path)
    path = tmp_path / "autplay-codex.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('log_level = "info"', 'log_level = "verbose"'),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="log_level"):
        load_config(tmp_path)
