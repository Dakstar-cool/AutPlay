"""Narrow adapter around the official Python Codex SDK."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import RoutingDecision


class CodexExecutionError(RuntimeError):
    """Raised when a Codex turn fails or violates its structured contract."""


@dataclass(frozen=True, slots=True)
class CodexRunResult:
    """Stable harness view of a completed SDK turn."""

    thread_id: str
    status: str
    final_response: str
    document: dict[str, Any]


class CodexRunner(Protocol):
    """Injectable boundary used by workflow tests and the real SDK adapter."""

    def run_task(
        self,
        prompt: str,
        decision: RoutingDecision,
        output_schema: dict[str, Any],
        on_thread_started: Callable[[str], None],
    ) -> CodexRunResult: ...

    def resume_task(
        self,
        thread_id: str,
        prompt: str,
        decision: RoutingDecision,
        output_schema: dict[str, Any],
    ) -> CodexRunResult: ...

    def run_review(
        self,
        prompt: str,
        *,
        model: str,
        reasoning: str,
        output_schema: dict[str, Any],
        on_thread_started: Callable[[str], None],
    ) -> CodexRunResult: ...


class OpenAICodexRunner:
    """Run local Codex app-server threads through `openai-codex`."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()

    def run_task(
        self,
        prompt: str,
        decision: RoutingDecision,
        output_schema: dict[str, Any],
        on_thread_started: Callable[[str], None],
    ) -> CodexRunResult:
        from openai_codex import ApprovalMode, Codex, CodexError, Sandbox
        from openai_codex.types import ReasoningEffort

        try:
            with Codex(self._config()) as client:
                thread = client.thread_start(
                    approval_mode=ApprovalMode.auto_review,
                    cwd=str(self.repo_root),
                    model=decision.model,
                    sandbox=Sandbox.workspace_write,
                )
                on_thread_started(thread.id)
                result = thread.run(
                    prompt,
                    effort=ReasoningEffort(decision.reasoning),
                    output_schema=output_schema,
                )
        except (CodexError, OSError, ValueError) as exc:
            raise CodexExecutionError(f"Codex implementation turn failed: {exc}") from exc
        return _convert_result(thread.id, result.status.value, result.final_response)

    def resume_task(
        self,
        thread_id: str,
        prompt: str,
        decision: RoutingDecision,
        output_schema: dict[str, Any],
    ) -> CodexRunResult:
        from openai_codex import ApprovalMode, Codex, CodexError, Sandbox
        from openai_codex.types import ReasoningEffort

        try:
            with Codex(self._config()) as client:
                thread = client.thread_resume(
                    thread_id,
                    approval_mode=ApprovalMode.auto_review,
                    cwd=str(self.repo_root),
                    model=decision.model,
                    sandbox=Sandbox.workspace_write,
                )
                result = thread.run(
                    prompt,
                    effort=ReasoningEffort(decision.reasoning),
                    output_schema=output_schema,
                )
        except (CodexError, OSError, ValueError) as exc:
            raise CodexExecutionError(f"Codex resume turn failed: {exc}") from exc
        return _convert_result(thread.id, result.status.value, result.final_response)

    def run_review(
        self,
        prompt: str,
        *,
        model: str,
        reasoning: str,
        output_schema: dict[str, Any],
        on_thread_started: Callable[[str], None],
    ) -> CodexRunResult:
        from openai_codex import ApprovalMode, Codex, CodexError, Sandbox
        from openai_codex.types import ReasoningEffort

        try:
            with Codex(self._config()) as client:
                thread = client.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(self.repo_root),
                    developer_instructions=(
                        "Act as the autplay_reviewer. Remain read-only and return "
                        "concrete findings."
                    ),
                    model=model,
                    sandbox=Sandbox.read_only,
                )
                on_thread_started(thread.id)
                result = thread.run(
                    prompt,
                    effort=ReasoningEffort(reasoning),
                    output_schema=output_schema,
                )
        except (CodexError, OSError, ValueError) as exc:
            raise CodexExecutionError(f"Codex review turn failed: {exc}") from exc
        return _convert_result(thread.id, result.status.value, result.final_response)

    def _config(self) -> Any:
        from openai_codex import CodexConfig

        return CodexConfig(
            cwd=str(self.repo_root),
            client_name="autplay_codex_harness",
            client_title="AutPlay Codex Harness",
            client_version="0.1.0",
        )


def load_json_schema(path: Path) -> dict[str, Any]:
    """Load a checked-in SDK output schema."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CodexExecutionError(f"cannot load output schema {path.name}: {exc}") from exc
    if not isinstance(document, dict):
        raise CodexExecutionError(f"output schema {path.name} must contain an object")
    return document


def _convert_result(thread_id: str, status: str, final_response: str | None) -> CodexRunResult:
    if status != "completed":
        raise CodexExecutionError(f"Codex turn ended with status {status}")
    if final_response is None:
        raise CodexExecutionError("Codex turn completed without a final response")
    try:
        document = json.loads(final_response)
    except json.JSONDecodeError as exc:
        raise CodexExecutionError("Codex final response is not valid structured JSON") from exc
    if not isinstance(document, dict):
        raise CodexExecutionError("Codex final response must be a JSON object")
    return CodexRunResult(
        thread_id=thread_id,
        status=status,
        final_response=final_response,
        document=document,
    )
