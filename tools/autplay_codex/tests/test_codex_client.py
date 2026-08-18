from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import openai_codex
import pytest
from autplay_codex.codex_client import CodexExecutionError, OpenAICodexRunner
from openai_codex import ApprovalMode, Sandbox


class _FakeThread:
    id = "review-thread"

    def run(self, prompt: str, **kwargs: Any) -> SimpleNamespace:
        del prompt, kwargs
        return SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            final_response=json.dumps({"summary": "ok", "findings": []}),
        )


class _FakeCodex:
    captured: ClassVar[dict[str, Any]] = {}

    def __init__(self, config: Any) -> None:
        self.config = config

    def __enter__(self) -> _FakeCodex:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def thread_start(self, **kwargs: Any) -> _FakeThread:
        _FakeCodex.captured = kwargs
        return _FakeThread()


class _FailingCodex(_FakeCodex):
    def thread_start(self, **kwargs: Any) -> _FakeThread:
        del kwargs
        raise OSError("transport unavailable")


def test_independent_review_sdk_thread_is_forced_read_only(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(openai_codex, "Codex", _FakeCodex)
    started: list[str] = []

    result = OpenAICodexRunner(tmp_path).run_review(
        "review",
        model="gpt-5.6-terra",
        reasoning="high",
        output_schema={"type": "object"},
        on_thread_started=started.append,
    )

    assert result.thread_id == "review-thread"
    assert started == ["review-thread"]
    assert _FakeCodex.captured["sandbox"] is Sandbox.read_only
    assert _FakeCodex.captured["approval_mode"] is ApprovalMode.deny_all


def test_sdk_transport_failure_is_wrapped(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(openai_codex, "Codex", _FailingCodex)

    with pytest.raises(CodexExecutionError, match="review turn failed"):
        OpenAICodexRunner(tmp_path).run_review(
            "review",
            model="gpt-5.6-terra",
            reasoning="high",
            output_schema={"type": "object"},
            on_thread_started=lambda _thread_id: None,
        )
