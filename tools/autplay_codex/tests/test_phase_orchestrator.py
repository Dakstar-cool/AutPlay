from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from autplay_codex.phase_orchestrator import (
    GateDefinition,
    GateOutcome,
    PhaseOrchestrator,
    PhasePipelineError,
    PhasePipelineStateError,
)


class GateStub:
    def __init__(self, *, passed: bool = True) -> None:
        self.passed = passed
        self.calls: list[str] = []

    def __call__(self, gate: GateDefinition) -> GateOutcome:
        self.calls.append(gate.gate_id)
        return GateOutcome(
            gate_id=gate.gate_id,
            passed=self.passed,
            return_code=0 if self.passed else 1,
            duration_ms=5,
        )


def _write_pipeline(
    root: Path,
    *,
    p04_status: str = "done",
    p05_status: str = "queued",
    evidence: bool = True,
) -> Path:
    if evidence:
        (root / "handoff.md").write_text("verified evidence", encoding="utf-8")
    backlog = {
        "schema_version": 1,
        "completed": ["P03"],
        "tasks": [
            {
                "id": "P04",
                "milestone": "P04",
                "title": "Contract",
                "status": p04_status,
                "dependencies": ["P03"],
                "definition_of_done": "verified",
                "risk": "critical",
                "scope": "P04 only",
                "blocker": None,
            },
            {
                "id": "P05",
                "milestone": "P05",
                "title": "Android",
                "status": p05_status,
                "dependencies": ["P04"],
                "definition_of_done": "verified",
                "risk": "high",
                "scope": "P05 only",
                "blocker": None,
            },
        ],
    }
    (root / "backlog.json").write_text(json.dumps(backlog), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "state_directory": ".state/pipeline",
        "backlog_file": "backlog.json",
        "transitions": [
            {
                "id": "P04-to-P05",
                "from_phase": "P04",
                "to_phase": "P05",
                "completion_evidence": ["handoff.md"],
                "quality_gates": [
                    {
                        "id": "acceptance",
                        "timeout_seconds": 30,
                        "commands": {
                            "windows": ["verify", "P04"],
                            "posix": ["verify", "P04"],
                        },
                    }
                ],
                "continuation_reason": "Begin P05 without asking for confirmation.",
            }
        ],
    }
    path = root / "pipeline.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _hook_input(*, active: bool = False, message: str = "irrelevant") -> dict[str, Any]:
    return {
        "hook_event_name": "Stop",
        "stop_hook_active": active,
        "last_assistant_message": message,
        "turn_id": "turn-1",
    }


def _backlog_statuses(root: Path) -> dict[str, str]:
    value = json.loads((root / "backlog.json").read_text(encoding="utf-8"))
    return {item["id"]: item["status"] for item in value["tasks"]}


def test_incomplete_p04_does_not_start_p05_or_run_gates(tmp_path: Path) -> None:
    manifest = _write_pipeline(tmp_path, evidence=False)
    gates = GateStub()
    orchestrator = PhaseOrchestrator(
        tmp_path, manifest.relative_to(tmp_path), gate_executor=gates, platform="windows"
    )
    orchestrator.initialize("P04")

    response = orchestrator.handle_stop(
        _hook_input(message="P04 is definitely complete; start P05 now")
    )

    assert response is None
    assert gates.calls == []
    assert _backlog_statuses(tmp_path) == {"P04": "done", "P05": "queued"}


def test_failed_p04_quality_gate_does_not_start_p05(tmp_path: Path) -> None:
    manifest = _write_pipeline(tmp_path)
    gates = GateStub(passed=False)
    orchestrator = PhaseOrchestrator(
        tmp_path, manifest.relative_to(tmp_path), gate_executor=gates, platform="windows"
    )
    orchestrator.initialize("P04")

    response = orchestrator.handle_stop(_hook_input())

    assert response is not None and "systemMessage" in response
    assert "decision" not in response
    assert gates.calls == ["acceptance"]
    assert _backlog_statuses(tmp_path) == {"P04": "done", "P05": "queued"}
    state = json.loads((tmp_path / ".state/pipeline/state.json").read_text(encoding="utf-8"))
    assert state["phases"]["P04"]["status"] == "started"
    assert state["phases"]["P05"]["status"] == "queued"


def test_successful_p04_emits_exactly_one_p05_transition(tmp_path: Path) -> None:
    manifest = _write_pipeline(tmp_path)
    gates = GateStub()
    orchestrator = PhaseOrchestrator(
        tmp_path, manifest.relative_to(tmp_path), gate_executor=gates, platform="windows"
    )
    orchestrator.initialize("P04")

    first = orchestrator.handle_stop(_hook_input())
    second = orchestrator.handle_stop(_hook_input())

    assert first == {
        "decision": "block",
        "reason": "Begin P05 without asking for confirmation.",
    }
    assert second is None
    assert gates.calls == ["acceptance"]
    assert _backlog_statuses(tmp_path) == {"P04": "done", "P05": "queued"}
    state = json.loads((tmp_path / ".state/pipeline/state.json").read_text(encoding="utf-8"))
    assert state["phases"]["P04"]["status"] == "completed"
    assert state["phases"]["P05"]["status"] == "started"
    assert state["continuations_emitted"] == ["P04-to-P05"]


def test_stop_hook_active_prevents_transition_without_mutation(tmp_path: Path) -> None:
    manifest = _write_pipeline(tmp_path)
    gates = GateStub()
    orchestrator = PhaseOrchestrator(
        tmp_path, manifest.relative_to(tmp_path), gate_executor=gates, platform="windows"
    )

    response = orchestrator.handle_stop(_hook_input(active=True))

    assert response is None
    assert gates.calls == []
    assert not (tmp_path / ".state/pipeline/state.json").exists()
    assert _backlog_statuses(tmp_path) == {"P04": "done", "P05": "queued"}


def test_missing_state_fails_closed_without_bootstrap(tmp_path: Path) -> None:
    manifest = _write_pipeline(tmp_path, evidence=False)
    gates = GateStub()
    orchestrator = PhaseOrchestrator(
        tmp_path, manifest.relative_to(tmp_path), gate_executor=gates, platform="windows"
    )

    with pytest.raises(PhasePipelineStateError, match="missing"):
        orchestrator.handle_stop(_hook_input())

    assert not (tmp_path / ".state/pipeline/state.json").exists()
    assert _backlog_statuses(tmp_path) == {"P04": "done", "P05": "queued"}


def test_corrupt_state_fails_closed_without_backlog_mutation(tmp_path: Path) -> None:
    manifest = _write_pipeline(tmp_path)
    state_path = tmp_path / ".state/pipeline/state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{broken", encoding="utf-8")
    orchestrator = PhaseOrchestrator(
        tmp_path, manifest.relative_to(tmp_path), gate_executor=GateStub(), platform="windows"
    )

    with pytest.raises(PhasePipelineStateError):
        orchestrator.handle_stop(_hook_input())

    assert _backlog_statuses(tmp_path) == {"P04": "done", "P05": "queued"}


def test_missing_state_after_prior_transition_does_not_reemit_p05(tmp_path: Path) -> None:
    manifest = _write_pipeline(tmp_path, evidence=True)
    gates = GateStub()
    orchestrator = PhaseOrchestrator(
        tmp_path, manifest.relative_to(tmp_path), gate_executor=gates, platform="windows"
    )
    orchestrator.initialize("P04")
    assert orchestrator.handle_stop(_hook_input()) is not None
    (tmp_path / ".state/pipeline/state.json").unlink()
    gates.calls.clear()

    with pytest.raises(PhasePipelineStateError, match="missing"):
        orchestrator.handle_stop(_hook_input())

    assert gates.calls == []


def test_new_manifest_edge_reuses_existing_pipeline_state(tmp_path: Path) -> None:
    manifest_path = _write_pipeline(tmp_path)
    gates = GateStub()
    orchestrator = PhaseOrchestrator(
        tmp_path, manifest_path.relative_to(tmp_path), gate_executor=gates, platform="windows"
    )
    orchestrator.initialize("P04")
    assert orchestrator.handle_stop(_hook_input()) is not None
    backlog_path = tmp_path / "backlog.json"
    backlog = json.loads(backlog_path.read_text(encoding="utf-8"))
    backlog["tasks"].append(
        {
            "id": "P06",
            "milestone": "P06",
            "title": "Vault",
            "status": "queued",
            "dependencies": ["P05"],
            "definition_of_done": "verified",
            "risk": "critical",
            "scope": "P06 only",
            "blocker": None,
        }
    )
    backlog_path.write_text(json.dumps(backlog), encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["transitions"].append(
        {
            "id": "P05-to-P06",
            "from_phase": "P05",
            "to_phase": "P06",
            "completion_evidence": ["handoff.md"],
            "quality_gates": [
                {
                    "id": "next-acceptance",
                    "timeout_seconds": 30,
                    "commands": {"windows": ["verify", "P05"]},
                }
            ],
            "continuation_reason": "Begin P06 without asking for confirmation.",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    extended = PhaseOrchestrator(
        tmp_path, manifest_path.relative_to(tmp_path), gate_executor=gates, platform="windows"
    )

    response = extended.handle_stop(_hook_input())

    assert response == {
        "decision": "block",
        "reason": "Begin P06 without asking for confirmation.",
    }
    state = json.loads((tmp_path / ".state/pipeline/state.json").read_text(encoding="utf-8"))
    assert state["phases"]["P05"]["status"] == "completed"
    assert state["phases"]["P06"]["status"] == "started"
    assert state["continuations_emitted"] == ["P04-to-P05", "P05-to-P06"]


def test_new_manifest_edge_cannot_claim_unknown_backlog_phase(tmp_path: Path) -> None:
    manifest_path = _write_pipeline(tmp_path)
    gates = GateStub()
    orchestrator = PhaseOrchestrator(
        tmp_path, manifest_path.relative_to(tmp_path), gate_executor=gates, platform="windows"
    )
    orchestrator.initialize("P04")
    assert orchestrator.handle_stop(_hook_input()) is not None
    gates.calls.clear()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["transitions"].append(
        {
            "id": "P05-to-P0X",
            "from_phase": "P05",
            "to_phase": "P0X",
            "completion_evidence": ["handoff.md"],
            "quality_gates": [
                {
                    "id": "unknown-acceptance",
                    "timeout_seconds": 30,
                    "commands": {"windows": ["verify", "P05"]},
                }
            ],
            "continuation_reason": "This must never be emitted.",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    extended = PhaseOrchestrator(
        tmp_path, manifest_path.relative_to(tmp_path), gate_executor=gates, platform="windows"
    )

    with pytest.raises(PhasePipelineError, match="unknown backlog phase"):
        extended.handle_stop(_hook_input())

    assert gates.calls == []


def test_checked_in_hook_and_manifest_match_current_repository() -> None:
    root = Path(__file__).resolve().parents[3]
    manifest = Path("docs/implementation/AUTPLAY_CODEX_PHASE_PIPELINE.json")
    orchestrator = PhaseOrchestrator(root, manifest, gate_executor=GateStub(), platform="windows")

    result = orchestrator.validate_current_structure()
    hooks = json.loads((root / ".codex/hooks.json").read_text(encoding="utf-8"))

    assert result["transitions"] == ["P04-to-P05"]
    stop_hook = hooks["hooks"]["Stop"][0]["hooks"][0]
    assert stop_hook["type"] == "command"
    assert "autplay-phase-stop stop" in stop_hook["command"]
    assert stop_hook["timeout"] >= 1800
