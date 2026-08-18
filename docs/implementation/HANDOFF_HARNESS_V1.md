# AutPlay Codex Development Harness v1 Handoff

## Outcome

AutPlay Codex Development Harness v1 is `PASS` as a repository-tooling milestone. The bootstrap ZIP was inspected and merged into the existing repository instead of being copied over it. The result provides a frozen local Python CLI, model/reasoning routing, durable task and goal state, dependency-aware phase selection, Git/request safety, bounded implementation/review/fix orchestration, structured result contracts, repository skill/config/custom agents, and canonical verification integration.

At harness milestone exit, the product baseline remained P03 `PASS` and P04 was `NOT_STARTED` and blocked by P00-D006. No product API, PostgreSQL migration, Android implementation, design decision, or future-phase behavior was added by the harness milestone.

## Post-handoff P00-D006 update

On 2026-08-15 the user explicitly approved P00-D006 Variant A and, after independent review exposed the merge collision case, P00-D006-R1. The complete accepted mapping is recorded in `P00-D006_AGGREGATE_ID_MAPPING.md`. The historical harness implementation and check evidence below is unchanged. P04 is now eligible but remains `NOT_STARTED`; this update creates no P04 contract or sync implementation.

Subsequent state: P04 completed independently on 2026-08-16; see `HANDOFF_P04.md`. The harness milestone itself still had no product-phase effect.

All harness-specific checks and both complete server/database regression paths passed. The current host cannot run the Android portion of the canonical gate because the exact required Microsoft OpenJDK `17.0.20+8-LTS` is not installed; this is recorded as `NOT RUN`, not as a pass or a product failure.

## Delivered scope

- A separate root uv project with CPython `3.14.7`, exact dependencies, a committed root `uv.lock`, and the six-command `autplay-codex` entry point.
- Repository configuration under `.codex/`, three bounded read-only custom agents, and the validated repo-level `autplay-development` skill.
- Luna/low, Terra/medium, Sol/high, and milestone Sol/xhigh routing with explicit overrides and ambiguity promotion to Sol. `ultra` is intentionally not a serialized reasoning value.
- Durable task state, milestone objective/completion/checkpoint records, atomic replacement, file locking, operation leases, revision compare-and-swap, corrupt-state refusal, terminal archival, and safe resume checks.
- Clean-worktree and protected-branch refusal, bounded request/argument validation, English/Russian destructive-operation gates, path containment, and no shell execution.
- Streamed bounded command capture and isolated process-tree termination on timeout.
- Separate implementation and deny-all/read-only review SDK threads, structured task/review JSON Schema validation, durable findings, bounded fix/re-review iterations, and canonical final checks.
- Allowlisted and redacted local events under ignored `.autplay-codex/`; secrets, DSNs, private paths, prompts, and personal payloads are not normal log fields.
- A machine-readable companion backlog that preserves `PLAN.md` and handoffs as authoritative and cannot skip declared phase gates or accepted dependency order.
- Root bootstrap/check integration that verifies the harness before the existing server/database/optional Android sequence while keeping `server/uv.lock` and the CPU-only server dependency graph independent.
- Updated README, plan, progress, traceability, risk, version, and CI records.

## Explicitly not delivered

- No P04 Sync Protocol v1, OpenAPI feature contract, event contract, golden vector, or sync implementation.
- No matcher, Vault, media, recommendation, import, Wave, Android product, or other later-phase behavior.
- No PostgreSQL migration, typed persistence mapping, product endpoint, or server runtime dependency change.
- No production deployment, push, PR, external write, secret use, paid resource, or hosted CI workflow.
- No live product implementation task was launched through the remote SDK. The SDK adapter and its sandbox/review/error contracts are exercised with deterministic tests; an actual future turn additionally depends on the operator's Codex service availability and a clean committed worktree.
- No new local Git commit was created because the initiating request did not explicitly authorize one.

## Changed modules/files

- Repository integration: `.gitignore`, `AGENTS.md`, `.codex/config.toml`, `.codex/agents/*.toml`, `.agents/skills/autplay-development/`, and `autplay-codex.toml`.
- Root Python project: `pyproject.toml`, `uv.lock`, and `tools/autplay_codex/`.
- Structured contracts: `schemas/autplay-codex-task-result.schema.json` and `schemas/autplay-codex-review-result.schema.json`.
- Harness tests: `tools/autplay_codex/tests/`.
- Machine-readable planning companion: `docs/implementation/AUTPLAY_CODEX_BACKLOG.json`.
- Canonical entry points: `scripts/bootstrap.ps1`, `scripts/bootstrap.sh`, `scripts/check.ps1`, and `scripts/check.sh`.
- Documentation: `README.md`, `docs/implementation/AUTPLAY_CODEX_HARNESS_V1.md`, `CI_PLAN.md`, `PLAN.md`, `PROGRESS.md`, `RISK_REGISTER.md`, `TRACEABILITY.md`, `VERSIONS.md`, and this handoff.

Normative design inputs, build-pack phase prompts/protocol/decision register, prior handoffs, product source, Android source, server source, migrations, reference DDL, and both existing version locks were inspected and retained rather than overwritten.

## Decisions and contracts

1. Harness dependencies live in a separate root uv project. They are excluded from the server Docker build context and cannot enter the CPU-only product graph.
2. `docs/implementation/PLAN.md` and phase handoffs remain authoritative. The JSON backlog is only a machine-readable selection/status companion.
3. Automated write turns always refuse dirty worktrees. A hash cannot prove preservation after uncommitted bytes have already been lost, so no unsafe dirty override is exposed.
4. Task selection, eligibility revalidation, terminal backlog synchronization, and crash-window reconciliation occur under the same per-repository operation lease.
5. Standalone critical/major review findings durably block the task/backlog. `resume` uses the bounded fix/check/re-review/final-check pipeline; a new task cannot silently replace unresolved actionable findings.
6. Review runs in a separate read-only, deny-all thread. Commands use argument vectors, bounded output, timeouts, and complete process-tree termination.
7. Milestones persist an objective, explicit done conditions, status, and checkpoints. This harness-native record is distinct from the surrounding Codex app goal used while integrating the bootstrap.
8. Supported reasoning values stop at `xhigh`; `ultra` is not written to Codex config as a reasoning effort.
9. Two new development-only Draft 2020-12 JSON Schema contracts define task and review structured results. No product wire or persistence contract changed.

During implementation, Ruff SIM117 recurred twice. In accordance with the repeated-error protocol, official Ruff, mypy, and Python context-management documentation was reviewed; the compatible fix was one combined `with` statement plus a precise platform-specific type suppression. The final lint/type suite is green.

The timeout process-tree finding also recurred during final review. Official Python process-group documentation and Microsoft `taskkill`, Toolhelp, Job Object, assignment, and termination documentation were compared. The selected implementation uses an unconditional POSIX group force phase and, on Windows, a launch gate assigned to a `KILL_ON_JOB_CLOSE` Job Object before the original command starts; `TerminateJobObject` then forcibly ends signal-ignoring descendants even after the wrapper exits. The adversarial regression test makes the child ignore the graceful signal and verifies that it cannot perform a delayed write.

## Commands and results

| Command | Result | Exact evidence |
| --- | --- | --- |
| `uv lock` and `uv sync --frozen --python 3.14.7` | PASS | Root lock resolves 26 package stanzas; harness installs independently of `server/` |
| `uv run --frozen ruff format --check tools/autplay_codex`, `ruff check`, strict `mypy`, and `pytest tools/autplay_codex/tests` | PASS | Final format/lint/type checks green; `69 passed in 8.76s` |
| `uv run --frozen autplay-codex --help` and CLI smoke | PASS | Exactly `status`, `task`, `next`, `milestone`, `review`, and `resume`; no dirty-write override |
| `status --json`, mechanical `task --dry-run`, and milestone dry run | PASS | Status exposes task/state/model/reasoning/routing/thread/Git/check/review fields; mechanical route Luna/low; milestone route Sol/xhigh with a durable goal record |
| `uv run --frozen autplay-codex next --json` | PASS (blocked by design) | Returns P04 with P00-D006 as the explicit blocker, exits `3`, and does not select P05+ |
| Repository skill quick validator | PASS | `Skill is valid!` for `.agents/skills/autplay-development` |
| `codex doctor --summary --no-color --ascii` | PASS with host note | Project config loaded; authentication/sandbox/network checks green; `TERM=dumb` reported as a non-failing terminal note; exit `0` |
| Repository Markdown link/fence, JSON/TOML parse, and `bash -n` checks | PASS | 68 repository Markdown files, zero odd fences, zero broken local links; all harness JSON/TOML parses; both shell scripts parse |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly` | PASS | Final tree: harness `69 passed in 9.18s`; complete server suite `298 passed in 113.72s`; PostgreSQL `18.4`/pgvector `0.8.6`; scoped container/network/volume removed |
| `& 'C:\Program Files\Git\bin\bash.exe' scripts/check.sh --server-only` | PASS | Final tree: same frozen harness/server/database gates; harness `69 passed in 9.20s`; server `298 passed in 113.85s`; scoped container/network/volume removed |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1` | `NOT RUN` past prerequisite | Canonical bootstrap stopped before Android tasks: `JAVA_HOME must point to pinned JDK 17`; required Microsoft OpenJDK `17.0.20+8-LTS` is absent. Installed candidates are Android Studio JBR `21.0.10` and Oracle Java 8; Android SDK platform/build-tools pins are present. No version bypass or unapproved install was used. |

The Android source tree was not changed. Its last accepted P01 evidence remains the pinned JDK 17 lint/unit/assemble gate; current-host re-execution requires provisioning that exact external prerequisite.

## Acceptance evidence

| Bootstrap criterion | Status | Evidence |
| --- | --- | --- |
| A. Frozen sync and six-command CLI | PASS | Root lock, canonical bootstrap, import/help smoke |
| B. Status exposes routing/task/thread/Git/check/review state | PASS | CLI output and unit tests |
| C. Six routing cases, overrides, ambiguity promotion, and milestone persistence | PASS | Routing/config/workflow tests and dry-run smoke |
| D. Atomic, corrupt, resume, interruption, terminal, and backlog-sync behavior | PASS | State/workflow/backlog tests including lease and revision races |
| E. Dirty/protected/destructive Git safety | PASS | Dirty worktree always refused; HEAD/path/parser and English/Russian policy tests |
| F. Read-only reviewer, visible findings, bounded fix loop, and final checks | PASS | SDK/workflow/check-runner tests, including descendant-process timeout and redaction |
| G. Existing tests, product APIs, and documentation remain valid | PASS with Android host gap | No product/API/migration/Android diff; both 298-test server/database gates pass; documentation/schema/config checks pass. Android re-run is `NOT RUN` solely because the pinned external JDK is absent. |

The bounded independent read-only review/fix verification ended with no unresolved critical or major findings. Earlier passes reported eight initial major gaps, six follow-up gaps, and one residual timeout-tree gap; every concrete finding was addressed with targeted regression coverage: resume/backlog consistency, operation locking/CAS, dirty/HEAD safety, durable standalone-review blocking, destructive-command coverage, redaction, bounded streaming, durable goals, lease-scoped terminal synchronization/reconciliation, complete process-tree timeout including a signal-ignoring descendant, and checkout/restore/rmdir detection.

## Known risks and debt

- A real SDK-driven implementation turn depends on operator authentication/service availability and intentionally requires a clean non-protected branch. The local adapter boundary is tested, but no external task was run as acceptance evidence.
- This host lacks the exact pinned Microsoft OpenJDK `17.0.20+8-LTS`, so the unchanged Android lint/unit/assemble gate was not re-executed. Installing or selecting a different JDK requires an explicit environment action, not a harness workaround.
- The frozen SDK/bundled CLI is `0.144.4`, while the separately installed host Codex CLI is `0.147.0`. This is intentional lock isolation and is documented in `VERSIONS.md`; upgrades require independent validation.
- `.autplay-codex/` is local ignored execution state. Its atomicity/locking behavior is tested, but cloud-synced workspace filesystem semantics remain risk R-017.
- P00-D006 was unresolved at harness exit and was fully accepted post-handoff through Variant A plus reviewed P00-D006-R1. The harness reflects the approved decision without inventing or altering it.

## Exact next prerequisite and prompt

P00-D006 is resolved through explicit approvals of Variant A and P00-D006-R1, preserving F-017. The next eligible phase prompt is `docs/build-pack/prompts/P04_sync_contract.md`:

```text
Выполни только AutPlay phase P04 по `docs/build-pack/prompts/P04_sync_contract.md`. Следуй `docs/build-pack/PROMPT_PROTOCOL.md`, проверь `HANDOFF_P03.md`, сначала разреши P00-D006 aggregate-ID mapping и не начинай P05. Подтверди acceptance P04 language-neutral schema/OpenAPI/golden-vector checks, создай `docs/implementation/HANDOFF_P04.md` и остановись.
```

After backlog synchronization, `uv run --frozen autplay-codex next --json` reports P04 as eligible and performs no product write when Git safety refuses the intentionally dirty worktree.

## Git state

- Branch: `codex/autplay-harness-v1`.
- Base HEAD: `0023fa9ad9d12633ad988230662fbd69bb74eb20`.
- Worktree: contains only the intentional inspect-and-merge harness changes listed above; final status is recorded in the delivery response.
- Commit: not created.
- Push/PR/deployment/external write: not performed.

## Blocking user decisions

None remain for the harness tooling milestone or P04 eligibility. Exact JDK 17 provisioning remains the external prerequisite for rerunning Android validation on this host, but it does not block P04 contract work.
