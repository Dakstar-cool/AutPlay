# AutPlay Codex Phase Pipeline v1 Handoff

## Outcome

The harness milestone is `PASS`. AutPlay now has a trusted project-local Codex `Stop` hook that can
continue from P04 to P05 without another user confirmation only after declared P04 artifacts and
mandatory real gates pass. The hook does not use the last assistant message as completion evidence.
P05 product implementation was not started by this milestone.

## Delivered scope

- `.codex/hooks.json` with one synchronous `Stop` command and official `decision: "block"` output.
- A declarative schema-versioned P04 -> P05 edge with evidence paths, platform argument-vector
  commands, bounded timeouts, and the exact continuation instruction.
- A small typed orchestrator inside the existing root uv package, plus `autplay-phase-stop`.
- Ignored runtime state under `.autplay-codex/phase-pipeline/`, separate from prompts and backlog.
- Explicit one-time initialization that refuses overwrite or a non-green P04/non-queued P05 plan.
- Fail-closed state/manifest/path/lock/gate handling, `stop_hook_active`, an atomic consumed-edge
  claim before stdout, and no automatic recovery after state loss.
- Generic reuse when a later manifest edge is added while its source is the durable active phase.
- Unit simulations for incomplete P04, failed gate, one successful transition, repeated Stop,
  active Stop hook, missing/corrupt/lost state, current package layout, a valid later declarative
  edge, and rejection of an edge outside the authoritative backlog graph.

## Explicitly not delivered

- No P05 Room schema/entity/DAO, Android repository, WorkManager, DataStore, Keystore, UI, sync
  transport, compatibility spike, or other P05 product behavior.
- No P06 transition is enabled in the checked-in manifest.
- No product API, PostgreSQL migration, server runtime behavior, deployment, push, PR, or commit.
- No automatic replay after a process crash between the state claim and Codex consuming stdout.

## Changed files and modules

- Hook/config: `.codex/hooks.json`, `.codex/config.toml`.
- Manifest: `docs/implementation/AUTPLAY_CODEX_PHASE_PIPELINE.json`.
- Runtime package: `phase_orchestrator.py`, root `pyproject.toml` entry point.
- Tests: `test_phase_orchestrator.py` and retained harness regression suite.
- Documentation: root `README.md`, harness design, PLAN, PROGRESS, TRACEABILITY, RISK_REGISTER,
  VERSIONS, the P04 post-handoff transition note, and this handoff.

The worktree already contained uncommitted harness/P04 and concurrent recommendation-contract work.
Those changes were preserved. During canonical stabilization only Ruff formatting and one strict
mypy narrowing were applied to the concurrently added contract test; its semantic fixtures were
completed by their owning work before the final stable gates.

## Decisions and contracts

1. The current user request is standing authorization for P04 -> P05. No additional phase
   confirmation is required, but frozen decisions, security/data boundaries, external writes, and
   documented user-decision blockers still stop execution.
2. `last_assistant_message` is intentionally ignored. Readiness requires declared files and actual
   zero-exit mandatory gates.
3. Runtime state is not stored in prompts or the tracked backlog. Missing/corrupt state fails closed
   and is not rebuilt from prose or plan status.
4. The transition claim writes P04 `completed`, P05 `started`, and the consumed transition in one
   atomic state replacement before emitting stdout.
5. Filesystem state and hook stdout cannot be one transaction. Normal execution returns exactly
   one continuation; retry/crash semantics are at-most-once. A crash after claim may lose the
   continuation but cannot duplicate P05.
6. The separate phase lock avoids reentrancy with the existing harness operation lock. Gate commands
   remain bounded argument vectors executed without shell interpolation.

## Commands and results

| Command | Result | Evidence |
| --- | --- | --- |
| Harness Ruff format/check and strict mypy | PASS | 32 root tooling/contract source files in each canonical path; orchestrator tree clean |
| `uv run --frozen pytest -c pyproject.toml tools/autplay_codex/tests` | PASS | `80 passed`; ten phase-orchestrator scenarios |
| `uv run --frozen autplay-phase-stop validate` | PASS | Current non-ASCII repository resolves hook, manifest, backlog, and all P04 evidence paths |
| Stop adapter with missing state | PASS, fail-closed | Valid `systemMessage`; no `decision`, gates, state reconstruction, or P05 transition |
| `uv run --frozen pytest -c pyproject.toml tests/contract/test_sync_contract_v1.py` | PASS | `51 passed`; actual P04 schemas/OpenAPI/vectors/hash/privacy checks |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly` | PASS | 80 harness + 51 contract + server `298 passed in 154.62s`; disposable resources removed |
| `& 'C:\Program Files\Git\bin\bash.exe' scripts/check.sh --server-only` | PASS | Same suites; server `298 passed in 139.66s`; disposable resources removed |
| `git diff --check`, JSON/TOML parse, `uv lock --check` | PASS | No whitespace/parse/lock drift error |

The canonical PowerShell path initially encountered moving concurrent P04 contract additions:
formatting, strict typing, and golden-hash/privacy fixture snapshots changed while the gate was
running. No orchestrator bypass was introduced. After the formatter condition repeated, the
repository protocol was followed: official Ruff formatter, configuration, editor, and E501
documentation were compared. The selected compatible action was the pinned repository formatter on
the affected test followed by a stable `--check` snapshot; both full canonical paths then passed.

## Acceptance evidence

| Requirement | Status | Evidence |
| --- | --- | --- |
| Incomplete P04 does not start P05 | PASS | Missing-evidence test ignores a false-positive final message and does not run gates |
| Failed P04 gate does not start P05 | PASS | Failed result persists; P04 stays started and P05 queued |
| Successful P04 emits one P05 transition | PASS | State records completed/started/consumed before one `decision: "block"` result |
| Repeated Stop cannot repeat P05 | PASS | Consumed-edge retry and post-state-loss tests return no continuation |
| `stop_hook_active` prevents recursion | PASS | Early return with no state or gate access |
| Missing/corrupt state is safe | PASS | Strict fail-closed tests preserve missing/corrupt state and do not emit P05 |
| Current AutPlay package remains compatible | PASS | Frozen entry point, manifest validation, both canonical server-only gates |
| Later edges reuse the mechanism | PASS | Test adds P05 -> P06 declaratively and reuses the same durable state machine |
| Later edges cannot bypass the phase graph | PASS | Unknown target and missing dependency edges fail before gates or state mutation |
| P05 was not implemented by setup | PASS | Changed-path/source audit; no Android product implementation path added |

## Known risks and debt

- Codex requires one-time trust review for a new or changed non-managed project hook. This is hook
  security review, not a phase-transition confirmation.
- Deleting ignored pipeline state after a claim intentionally disables automatic continuation.
  Recovery must be explicit so P05 cannot be launched twice.
- A crash after the state claim and before Codex consumes stdout can require an explicit operator
  resume. The design chooses at-most-once over unsafe replay.
- Later phase edges need their own declared acceptance files, commands, continuation reason, and
  standing user authorization; adding an edge cannot weaken frozen/security/data stop conditions.

## Exact next prerequisite

The local pipeline state must be initialized once for P04 after this handoff and final checks. The
trusted Stop hook then re-runs the manifest P04 gates. On success it alone records P04 completed and
P05 started and emits the pre-authorized P05 continuation. Do not manually execute P05 as part of
this harness milestone.

## Git state

- Branch: current working branch; final status is reported by the delivery response.
- Commit: not created; push/PR/deployment: not performed.
- Worktree: intentionally dirty with preserved prior/concurrent work and this harness milestone.

## Blocking user decisions

None for the P04 -> P05 pipeline. A one-time Codex hook trust review may be required by the official
hook security mechanism before the newly added command can run.
