"""Verified, one-shot phase continuation for the project-local Codex Stop hook."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from .backlog import BacklogError, BacklogStore, BacklogTask
from .checks import CheckRunner
from .models import CheckStatus
from .state import StateConflictError, atomic_write_json

_MAX_HOOK_INPUT_BYTES = 1_048_576
_PHASE_STATUSES = frozenset({"queued", "started", "completed"})


class PhasePipelineError(RuntimeError):
    """Raised when pipeline configuration or durable state is unsafe to use."""


class PhasePipelineStateError(PhasePipelineError):
    """Raised when durable pipeline state is corrupt or inconsistent."""


@dataclass(frozen=True, slots=True)
class GateDefinition:
    """One mandatory, platform-specific quality gate."""

    gate_id: str
    command: tuple[str, ...]
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """Bounded machine-readable evidence from one quality gate execution."""

    gate_id: str
    passed: bool
    return_code: int | None
    duration_ms: int


@dataclass(frozen=True, slots=True)
class TransitionDefinition:
    """Declarative prerequisites and continuation for one phase edge."""

    transition_id: str
    from_phase: str
    to_phase: str
    completion_evidence: tuple[Path, ...]
    quality_gates: tuple[GateDefinition, ...]
    continuation_reason: str


@dataclass(frozen=True, slots=True)
class PipelineManifest:
    """Validated repository-local phase graph configuration."""

    path: Path
    state_directory: Path
    backlog_file: Path
    transitions: tuple[TransitionDefinition, ...]


GateExecutor = Callable[[GateDefinition], GateOutcome]


class PhaseOrchestrator:
    """Evaluate a Stop event and emit at most one verified continuation."""

    def __init__(
        self,
        repo_root: Path,
        manifest_path: Path,
        *,
        gate_executor: GateExecutor | None = None,
        platform: str | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.manifest_path = _resolve_repo_path(
            self.repo_root, manifest_path, "manifest", require_file=True
        )
        self.platform = platform or ("windows" if os.name == "nt" else "posix")
        if self.platform not in {"windows", "posix"}:
            raise PhasePipelineError(f"unsupported pipeline platform: {self.platform}")
        self.manifest = load_manifest(self.repo_root, self.manifest_path, self.platform)
        self.gate_executor = gate_executor or self._execute_gate
        self.state_path = self.manifest.state_directory / "state.json"
        self.lock_path = self.manifest.state_directory / "operation.lock"

    def handle_stop(self, hook_input: Mapping[str, Any]) -> dict[str, Any] | None:
        """Return a Codex hook response, or no response when continuation is ineligible."""

        if hook_input.get("hook_event_name") != "Stop":
            return None
        stop_hook_active = hook_input.get("stop_hook_active", False)
        if not isinstance(stop_hook_active, bool):
            raise PhasePipelineError("stop_hook_active must be a boolean")
        if stop_hook_active:
            return None

        with _exclusive_lock(self.lock_path):
            tasks = BacklogStore(self.manifest.backlog_file).load()
            self._validate_backlog_edges(tasks)
            state = self._load_state()
            transition = self._eligible_transition(state)
            if transition is None:
                return None
            if not self._completion_evidence_exists(transition):
                return None

            outcome_values: list[GateOutcome] = []
            for gate in transition.quality_gates:
                outcome = self.gate_executor(gate)
                outcome_values.append(outcome)
                if not outcome.passed:
                    break
            outcomes = tuple(outcome_values)
            self._record_gate_outcomes(state, transition, outcomes)
            if not outcomes or not all(outcome.passed for outcome in outcomes):
                self._save_state(state)
                return {
                    "systemMessage": (
                        f"AutPlay phase pipeline kept {transition.to_phase} queued because "
                        f"the mandatory {transition.from_phase} quality gate failed."
                    )
                }

            self._claim_transition(state, transition)
            self._save_state(state)
            return {"decision": "block", "reason": transition.continuation_reason}

    def initialize(self, from_phase: str) -> dict[str, Any]:
        """Create the one-time local state required before a transition may run."""

        with _exclusive_lock(self.lock_path):
            if self.state_path.exists():
                raise PhasePipelineStateError("phase pipeline state already exists")
            transition = next(
                (item for item in self.manifest.transitions if item.from_phase == from_phase),
                None,
            )
            if transition is None:
                raise PhasePipelineError(f"manifest has no transition from {from_phase}")
            tasks = BacklogStore(self.manifest.backlog_file).load()
            self._validate_backlog_edges(tasks)
            task_by_id = {task.task_id: task for task in tasks}
            source = task_by_id.get(transition.from_phase)
            target = task_by_id.get(transition.to_phase)
            if source is None or target is None:
                raise PhasePipelineError("manifest transition references an unknown backlog phase")
            if source.status != "done" or target.status != "queued":
                raise PhasePipelineError(
                    f"cannot initialize {transition.transition_id}: expected done/queued backlog"
                )
            now = _utc_now()
            state = {
                "schema_version": 1,
                "revision": 0,
                "active_phase": transition.from_phase,
                "phases": {
                    transition.from_phase: {"status": "started"},
                    transition.to_phase: {"status": "queued"},
                },
                "continuations_emitted": [],
                "gate_runs": {},
                "created_at": now,
                "updated_at": now,
            }
            self._save_state(state)
            return state

    def validate_current_structure(self) -> dict[str, Any]:
        """Validate the manifest, backlog references, and evidence paths without running gates."""

        tasks = BacklogStore(self.manifest.backlog_file).load()
        self._validate_backlog_edges(tasks)
        missing_evidence: list[str] = []
        for transition in self.manifest.transitions:
            for path in transition.completion_evidence:
                if not path.is_file():
                    missing_evidence.append(path.relative_to(self.repo_root).as_posix())
        if missing_evidence:
            raise PhasePipelineError(
                f"manifest completion evidence is missing: {sorted(set(missing_evidence))}"
            )
        return {
            "status": "ok",
            "platform": self.platform,
            "transitions": [item.transition_id for item in self.manifest.transitions],
        }

    def _validate_backlog_edges(self, tasks: Sequence[BacklogTask]) -> None:
        task_by_id = {task.task_id: task for task in tasks}
        for transition in self.manifest.transitions:
            source = task_by_id.get(transition.from_phase)
            target = task_by_id.get(transition.to_phase)
            if source is None or target is None:
                raise PhasePipelineError(
                    f"transition {transition.transition_id} references an unknown backlog phase"
                )
            if transition.from_phase not in target.dependencies:
                raise PhasePipelineError(
                    f"transition {transition.transition_id} is not a declared backlog dependency"
                )

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            raise PhasePipelineStateError("phase pipeline state is missing")
        try:
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PhasePipelineStateError(f"cannot load phase pipeline state: {exc}") from exc
        return _validate_state(document, self.manifest)

    def _eligible_transition(self, state: dict[str, Any]) -> TransitionDefinition | None:
        emitted = _string_set(state["continuations_emitted"], "continuations_emitted")
        phase_values = _phase_values(state)
        for transition in self.manifest.transitions:
            if transition.transition_id in emitted:
                continue
            source_state = phase_values.get(transition.from_phase, {}).get("status")
            target_state = phase_values.get(transition.to_phase, {}).get("status")
            if (
                source_state == "started"
                and target_state is None
                and state.get("active_phase") == transition.from_phase
            ):
                phase_values[transition.to_phase] = {"status": "queued"}
                target_state = "queued"
            if source_state != "started" or target_state != "queued":
                continue
            return transition
        return None

    @staticmethod
    def _record_gate_outcomes(
        state: dict[str, Any],
        transition: TransitionDefinition,
        outcomes: Sequence[GateOutcome],
    ) -> None:
        gate_runs = state["gate_runs"]
        if not isinstance(gate_runs, dict):
            raise PhasePipelineStateError("gate_runs must be an object")
        now = _utc_now()
        gate_runs[transition.transition_id] = {
            "checked_at": now,
            "gates": [
                {
                    "id": outcome.gate_id,
                    "status": "passed" if outcome.passed else "failed",
                    "return_code": outcome.return_code,
                    "duration_ms": outcome.duration_ms,
                }
                for outcome in outcomes
            ],
        }
        state["updated_at"] = now

    @staticmethod
    def _claim_transition(state: dict[str, Any], transition: TransitionDefinition) -> None:
        phases = _phase_values(state)
        phases[transition.from_phase] = {"status": "completed"}
        phases[transition.to_phase] = {"status": "started"}
        emitted = state["continuations_emitted"]
        if not isinstance(emitted, list):
            raise PhasePipelineStateError("continuations_emitted must be an array")
        emitted.append(transition.transition_id)
        state["active_phase"] = transition.to_phase
        state["revision"] = _state_revision(state) + 1
        state["updated_at"] = _utc_now()

    def _completion_evidence_exists(self, transition: TransitionDefinition) -> bool:
        return all(path.is_file() for path in transition.completion_evidence)

    def _save_state(self, state: dict[str, Any]) -> None:
        _validate_state(state, self.manifest)
        atomic_write_json(self.state_path, state)

    def _execute_gate(self, gate: GateDefinition) -> GateOutcome:
        result = CheckRunner(self.repo_root, gate.timeout_seconds).run([gate.command])[0]
        return GateOutcome(
            gate_id=gate.gate_id,
            passed=result.status is CheckStatus.PASSED,
            return_code=result.return_code,
            duration_ms=result.duration_ms,
        )


def load_manifest(repo_root: Path, path: Path, platform: str) -> PipelineManifest:
    """Load and strictly validate the declarative phase-transition manifest."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PhasePipelineError(f"cannot load phase pipeline manifest: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise PhasePipelineError("phase pipeline manifest must be a schema_version 1 object")
    state_directory = _resolve_repo_path(
        repo_root,
        Path(_required_string(document, "state_directory")),
        "state_directory",
    )
    backlog_file = _resolve_repo_path(
        repo_root,
        Path(_required_string(document, "backlog_file")),
        "backlog_file",
        require_file=True,
    )
    values = document.get("transitions")
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(item, dict) for item in values)
    ):
        raise PhasePipelineError("manifest transitions must be a non-empty object array")
    transitions = tuple(_parse_transition(repo_root, value, platform) for value in values)
    identifiers = [item.transition_id for item in transitions]
    if len(set(identifiers)) != len(identifiers):
        raise PhasePipelineError("manifest transition ids must be unique")
    edges = [(item.from_phase, item.to_phase) for item in transitions]
    if len(set(edges)) != len(edges):
        raise PhasePipelineError("manifest phase edges must be unique")
    return PipelineManifest(
        path=path,
        state_directory=state_directory,
        backlog_file=backlog_file,
        transitions=transitions,
    )


def _parse_transition(
    repo_root: Path, value: dict[str, Any], platform: str
) -> TransitionDefinition:
    transition_id = _required_string(value, "id")
    from_phase = _required_string(value, "from_phase")
    to_phase = _required_string(value, "to_phase")
    if from_phase == to_phase:
        raise PhasePipelineError(f"transition {transition_id} must change phase")
    evidence_values = value.get("completion_evidence")
    if not isinstance(evidence_values, list) or not evidence_values:
        raise PhasePipelineError(f"transition {transition_id} needs completion evidence")
    evidence: list[Path] = []
    for index, item in enumerate(evidence_values):
        if not isinstance(item, str) or not item:
            raise PhasePipelineError(
                f"transition {transition_id} completion_evidence[{index}] is invalid"
            )
        evidence.append(_resolve_repo_path(repo_root, Path(item), "completion evidence"))

    gate_values = value.get("quality_gates")
    if not isinstance(gate_values, list) or not gate_values:
        raise PhasePipelineError(f"transition {transition_id} needs mandatory quality gates")
    gates: list[GateDefinition] = []
    for gate_value in gate_values:
        if not isinstance(gate_value, dict):
            raise PhasePipelineError(f"transition {transition_id} has an invalid quality gate")
        commands = gate_value.get("commands")
        if not isinstance(commands, dict):
            raise PhasePipelineError("quality gate commands must be an object")
        command_value = commands.get(platform)
        if command_value is None:
            continue
        command = _command(command_value, transition_id)
        timeout = gate_value.get("timeout_seconds")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
            raise PhasePipelineError("quality gate timeout_seconds must be from 1 through 3600")
        gates.append(
            GateDefinition(
                gate_id=_required_string(gate_value, "id"),
                command=command,
                timeout_seconds=timeout,
            )
        )
    if not gates:
        raise PhasePipelineError(f"transition {transition_id} has no quality gates for {platform}")
    gate_ids = [gate.gate_id for gate in gates]
    if len(set(gate_ids)) != len(gate_ids):
        raise PhasePipelineError(f"transition {transition_id} quality gate ids must be unique")

    reason = _required_string(value, "continuation_reason")
    if len(reason) > 8_000:
        raise PhasePipelineError("continuation_reason exceeds 8000 characters")
    return TransitionDefinition(
        transition_id=transition_id,
        from_phase=from_phase,
        to_phase=to_phase,
        completion_evidence=tuple(evidence),
        quality_gates=tuple(gates),
        continuation_reason=reason,
    )


def _validate_state(value: Any, manifest: PipelineManifest) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise PhasePipelineStateError("phase pipeline state must be a schema_version 1 object")
    _state_revision(value)
    active_phase = value.get("active_phase")
    if active_phase is not None and (not isinstance(active_phase, str) or not active_phase):
        raise PhasePipelineStateError("active_phase must be a string or null")
    phases = _phase_values(value)
    known_phases = {
        phase
        for transition in manifest.transitions
        for phase in (transition.from_phase, transition.to_phase)
    }
    if set(phases) - known_phases:
        raise PhasePipelineStateError("phase pipeline state contains an unknown phase")
    if active_phase is not None and active_phase not in known_phases:
        raise PhasePipelineStateError("active_phase is not declared in the manifest")
    emitted = _string_set(value.get("continuations_emitted"), "continuations_emitted")
    transition_ids = {item.transition_id for item in manifest.transitions}
    if emitted - transition_ids:
        raise PhasePipelineStateError("state contains an unknown emitted transition")
    if not isinstance(value.get("gate_runs"), dict):
        raise PhasePipelineStateError("gate_runs must be an object")
    for key in ("created_at", "updated_at"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise PhasePipelineStateError(f"{key} must be a non-empty string")
    return value


def _phase_values(state: Mapping[str, Any]) -> dict[str, Any]:
    phases = state.get("phases")
    if not isinstance(phases, dict):
        raise PhasePipelineStateError("phases must be an object")
    for phase, item in phases.items():
        if not isinstance(phase, str) or not phase or not isinstance(item, dict):
            raise PhasePipelineStateError("phase entries must be named objects")
        if item.get("status") not in _PHASE_STATUSES:
            raise PhasePipelineStateError(f"phase {phase} has an invalid status")
    return phases


def _state_revision(state: Mapping[str, Any]) -> int:
    revision = state.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise PhasePipelineStateError("revision must be a non-negative integer")
    return revision


def _string_set(value: Any, label: str) -> set[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PhasePipelineStateError(f"{label} must be a string array")
    if len(set(value)) != len(value):
        raise PhasePipelineStateError(f"{label} must not contain duplicates")
    return set(value)


def _command(value: Any, transition_id: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 64
        or not all(isinstance(item, str) and item and len(item) <= 4096 for item in value)
    ):
        raise PhasePipelineError(
            f"transition {transition_id} quality gate command must be a bounded string array"
        )
    return tuple(value)


def _required_string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise PhasePipelineError(f"{key} must be a non-empty string")
    return item


def _resolve_repo_path(
    repo_root: Path,
    value: Path,
    label: str,
    *,
    require_file: bool = False,
) -> Path:
    if value.is_absolute():
        raise PhasePipelineError(f"{label} must be repository-relative")
    resolved = (repo_root / value).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise PhasePipelineError(f"{label} escapes the repository root") from exc
    if require_file and not resolved.is_file():
        raise PhasePipelineError(f"{label} does not exist: {value.as_posix()}")
    return resolved


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            _lock_file(handle)
        except OSError as exc:
            raise StateConflictError("another phase pipeline operation is active") from exc
        try:
            yield
        finally:
            _unlock_file(handle)


def _lock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if handle.read(1) == b"":
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _repository_root(start: Path) -> Path:
    candidate = start.resolve()
    for directory in (candidate, *candidate.parents):
        if (directory / ".git").exists():
            return directory
    process = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if process.returncode != 0:
        raise PhasePipelineError("phase pipeline must run inside the AutPlay Git repository")
    root = Path(process.stdout.strip()).resolve()
    if not root.is_dir():
        raise PhasePipelineError("Git returned an invalid repository root")
    return root


def _read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(_MAX_HOOK_INPUT_BYTES + 1)
    if len(raw) > _MAX_HOOK_INPUT_BYTES:
        raise PhasePipelineError("Stop hook input exceeds 1 MiB")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhasePipelineError(f"invalid Stop hook input: {exc}") from exc
    if not isinstance(value, dict):
        raise PhasePipelineError("Stop hook input must be a JSON object")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autplay-phase-orchestrator")
    parser.add_argument("action", choices=("init", "stop", "validate"))
    parser.add_argument(
        "--manifest",
        default="docs/implementation/AUTPLAY_CODEX_PHASE_PIPELINE.json",
        help="Repository-relative declarative pipeline manifest.",
    )
    parser.add_argument("--from-phase", default="P04")
    return parser


def main(arguments: list[str] | None = None) -> int:
    """Run the Stop-hook adapter or a read-only structure validation."""

    args = _build_parser().parse_args(arguments)
    try:
        repo_root = _repository_root(Path.cwd())
        orchestrator = PhaseOrchestrator(repo_root, Path(args.manifest))
        if args.action == "init":
            state = orchestrator.initialize(args.from_phase)
            print(json.dumps(state, sort_keys=True))
            return 0
        if args.action == "validate":
            print(json.dumps(orchestrator.validate_current_structure(), sort_keys=True))
            return 0
        response = orchestrator.handle_stop(_read_hook_input())
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, sort_keys=True))
        return 0
    except (BacklogError, PhasePipelineError, StateConflictError) as exc:
        if args.action == "stop":
            print(
                json.dumps(
                    {
                        "systemMessage": (
                            f"AutPlay phase continuation was disabled safely: {type(exc).__name__}."
                        )
                    },
                    sort_keys=True,
                )
            )
            return 0
        print(f"autplay-phase-orchestrator: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
