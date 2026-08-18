# AutPlay Codex Development Harness v1

**Status:** integrated repository tooling; product baseline remains P03

## Integration map

The bootstrap archive was treated as an inspect-and-merge engineering input, not as a source tree to overwrite. Existing AutPlay governance, P00-P14 prompts, design precedence, canonical scripts, server package, tests, and product phase state were retained.

| Bootstrap concern | Repository decision |
| --- | --- |
| `AGENTS.md` | Merge only harness workflow rules into the existing, stricter AutPlay governance file |
| `.codex/config.toml` | Add project-scoped defaults and bounded agent concurrency |
| Explorer/reviewer/architect | Add read-only custom agents under `.codex/agents/` with explicit roles and model/reasoning pins |
| Repository skill | Add `.agents/skills/autplay-development/` and validated `agents/openai.yaml` metadata |
| Python CLI | Add a root uv project and `tools/autplay_codex`; do not add SDK dependencies to `server/` |
| Backlog | Add a machine-readable companion sourced from `PLAN.md`; keep `PLAN.md` and phase handoffs authoritative |
| Structured output | Add separate task and reviewer Draft 2020-12 schemas |
| Existing checks | Extend the canonical bootstrap/check scripts so harness and server locks remain independently verified |
| Product scope | Do not start P04 or change API/contracts; expose the current P00-D006 decision state when selecting P04 |

The archive's generic file templates were adapted to the real repository. In particular, one combined Python project would have polluted the CPU-only server dependency graph, generic backlog examples would have bypassed phase governance, and blind replacement of `AGENTS.md` would have removed stronger architecture and data-safety rules. None of those package defaults were copied literally.

## Runtime boundary

The root `pyproject.toml` and `uv.lock` own only local orchestration and its quality tooling. The production-shaped server continues to use `server/pyproject.toml` and `server/uv.lock`; the Docker allowlist does not include the root harness. Canonical dependency audits continue to evaluate the server graph only.

The CLI uses the official `openai-codex` Python SDK. An implementation turn receives workspace-write sandboxing and auto-review approval handling. An independent review starts a separate read-only, deny-all thread. SDK exceptions, incomplete turns, missing output, and invalid structured JSON become bounded `CodexExecutionError` failures rather than raw tracebacks or placeholder success.

## Commands

Run from anywhere inside this Git repository; a custom configuration path is resolved from the repository root.

```text
uv run --frozen autplay-codex status
uv run --frozen autplay-codex task "<bounded task>" [--dry-run]
uv run --frozen autplay-codex next [--dry-run]
uv run --frozen autplay-codex milestone <Pxx> [--dry-run]
uv run --frozen autplay-codex review
uv run --frozen autplay-codex resume
```

`task`, `next`, and `milestone` accept explicit `--model`, `--reasoning`, and persisted-goal overrides. Supported reasoning values match checked-in Codex configuration: `minimal`, `low`, `medium`, `high`, and `xhigh`. `ultra` is not serialized as a reasoning effort.

## Routing and lifecycle

| Task class | Default route | Persisted goal |
| --- | --- | --- |
| Clear, repeatable/mechanical | Luna / low | No |
| Ordinary engineering | Terra / medium | No |
| Risky, ambiguous, or cross-module | Sol / high | No |
| Explicit milestone | Sol / xhigh | Yes |

Unknown or ambiguous work is promoted to complex engineering rather than silently routed to the cheapest model. Explicit overrides take precedence.

Persisted task states are `queued`, `planning`, `implementing`, `testing`, `reviewing`, `fixing`, `blocked`, `done`, and `failed`. A milestone additionally persists its objective, explicit completion criteria, active/complete/blocked/failed goal status, and state-transition checkpoints; completion therefore depends on the full test/review/final-check loop rather than an advisory boolean. The workflow is bounded:

1. validate Git/request safety and atomically persist the task;
2. start or immediately checkpoint the SDK thread id;
3. run targeted checks;
4. run a separate read-only structured review;
5. apply bounded fix turns and rerun targeted checks/review;
6. run the canonical final check sequence;
7. persist the terminal outcome while retaining review history.

Completed tasks are archived before a new task and are never rerun by `resume`. A resumed backlog task stores its backlog id and synchronizes terminal state even when the previous process ended between state completion and backlog update. Selection is revalidated and status is written while the same operation lease is held. Resume validates repository, branch, and last checkpoint HEAD. Long implementation/review operations hold a non-blocking per-repository lease; each state write also requires the expected revision, uses a file lock, same-directory temporary file, flush/fsync, and atomic replacement. Unknown persisted fields survive read/write so newer compatible state is not silently destroyed.

## Safety and observability

- Automated writes are refused on `main` and `master`.
- Dirty worktrees are always refused before an automated write turn. Existing work must be committed or stashed, so the harness never relies on a non-recoverable hash to claim that user bytes are preserved. A JSON `next` refusal retains bounded eligible-task metadata with `execution_started: false`, while non-JSON behavior and clean-worktree execution semantics remain unchanged.
- Force-push, protected-branch merge, production deploy, destructive database/data/Vault, and `rm -rf` requests require a human-guided workflow outside the harness.
- Commands are argument vectors and never use a shell; time, argument size, retries, review, and fix iteration counts are bounded. Stdout/stderr are drained concurrently into fixed-size head/tail buffers, so truncation happens during execution rather than after unbounded capture. Each command starts in an isolated process group; timeout first signals and then forcibly terminates the complete tree.
- `.autplay-codex/` is ignored local runtime state. JSONL events are allowlisted and redact secret-like values and private absolute paths; prompts and personal payloads are not logged.
- Reviewer findings retain redacted severity/evidence/files/scenario/history. Critical and major findings block completion until a later green check marks the prior finding resolved and a clean review is obtained. Standalone review prints unresolved details, moves a completed task and its backlog entry to `blocked`, and exits non-zero; `resume` repairs those findings through the bounded fix/re-review pipeline.

## Product-plan boundary

`docs/implementation/PLAN.md` and the latest `HANDOFF_Pxx.md` remain authoritative. The JSON backlog mirrors phase dependencies only for selection and status. A separately initialized, ignored phase-pipeline state may claim only a checked-in manifest edge after its declared artifacts and real mandatory gates pass. The current user authorization pre-approves P04 -> P05 without another confirmation; it does not permit P06, reinterpret a frozen decision, bypass a documented blocker, or mark a phase complete from model prose.

## Verified Stop-hook transition

`.codex/hooks.json` registers one synchronous `Stop` command. `phase_orchestrator.py` ignores `last_assistant_message`, exits without state access when `stop_hook_active` is true, uses a separate non-blocking lock, runs commands through the bounded shell-free `CheckRunner`, and persists only gate IDs/status/return codes/durations. Runtime state is separate from prompts and the tracked backlog. Missing or corrupt state fails closed and is never reconstructed automatically, preventing replay after state loss. The claim is committed before stdout, so normal execution returns one continuation and crash behavior is at-most-once; a crash after claim but before Codex consumes stdout can lose, but never duplicate, the continuation.

## Verification ownership

Harness unit tests cover routing and overrides, configuration/path containment, schema validity, atomic/failure state writes, operation locking and revision CAS, corrupt/resume/terminal state handling, Git HEAD/dirty fingerprint safety, command/Russian destructive gates, DSN/top-level/review redaction, streaming command bounds, backlog dependency/resume synchronization, durable milestone goals, SDK read-only review and exception mapping, bounded review/fix/final-check behavior, CLI status/config/finding behavior, and the Stop-hook incomplete/failed/success/repeat/corrupt/missing/unknown-edge matrix. The root canonical scripts run these checks before the existing full server/database/Android sequence.
