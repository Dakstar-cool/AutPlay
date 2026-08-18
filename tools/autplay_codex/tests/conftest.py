from __future__ import annotations

import json
from pathlib import Path

import pytest
from autplay_codex.config import CheckPolicy, HarnessConfig, ModelPolicy
from autplay_codex.models import TaskClass


@pytest.fixture
def harness_config(tmp_path: Path) -> HarnessConfig:
    task_schema = tmp_path / "task.schema.json"
    review_schema = tmp_path / "review.schema.json"
    backlog = tmp_path / "backlog.json"
    task_schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    review_schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    backlog.write_text(
        json.dumps({"schema_version": 1, "completed": [], "tasks": []}),
        encoding="utf-8",
    )
    return HarnessConfig(
        repo_root=tmp_path,
        config_path=tmp_path / "autplay-codex.toml",
        state_dir=tmp_path / ".autplay-codex",
        backlog_file=backlog,
        task_result_schema=task_schema,
        review_result_schema=review_schema,
        max_review_iterations=2,
        max_fix_iterations=2,
        max_subagents=3,
        command_timeout_seconds=30,
        log_level="info",
        protected_branches=frozenset({"main", "master"}),
        models=ModelPolicy(
            routes={
                TaskClass.CLEAR_REPEATABLE: ("gpt-5.6-luna", "low"),
                TaskClass.NORMAL_ENGINEERING: ("gpt-5.6-terra", "medium"),
                TaskClass.COMPLEX_ENGINEERING: ("gpt-5.6-sol", "high"),
                TaskClass.MILESTONE: ("gpt-5.6-sol", "xhigh"),
            },
            review_model="gpt-5.6-terra",
            review_reasoning="high",
        ),
        checks=CheckPolicy(
            targeted_windows=(("targeted",),),
            targeted_posix=(("targeted",),),
            final_windows=(("final",),),
            final_posix=(("final",),),
        ),
    )
