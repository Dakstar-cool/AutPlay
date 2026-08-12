# HANDOFF P00 - Repository Intake and Engineering Contract

## Outcome

P00 is complete and green. A previously empty/unborn Git worktree now contains the original AutPlay design/build pack at stable repository paths plus a root engineering contract, phase plan, progress/traceability/risk/version records, this handoff and a non-destructive `.gitignore`. No P01 work or product feature code was created.

## Delivered scope

- Classified the workspace as a new repository: `.git` existed, `HEAD` and commits did not, and no user files were present.
- Safely extracted the 40 archive files directly into the repository root with path-traversal and overwrite guards.
- Read all P00-required design/build-pack inputs and audited source precedence, package membership, references and contradictions.
- Created root `AGENTS.md` with phase, safety, architecture, testing, Git, repeated-error and hidden-instruction rules.
- Created `PLAN.md`, `PROGRESS.md`, `TRACEABILITY.md`, `RISK_REGISTER.md`, `VERSIONS.md` and this handoff.
- Recorded nine proposed ADR/change items without modifying any frozen decision or source document.
- Verified archive integrity, Markdown links/fences, P00-P14 prompt numbering, reference DDL object counts, compact inventory and Git state.
- Prepared one local P00 commit; no push or external write is authorized or performed.

## Explicitly not delivered

- P01 monorepo paths, README, project manifests, lockfiles, wrappers, CI, Compose services or pinned toolchains.
- Server, Android, API, UI, domain, persistence, Vault, sync, playback, import, recommendation, GPU or Wave code.
- Alembic/Room migrations, executable contracts or external provider choices.
- Deployment, publication, signing, secrets, real data operations or cloud resources.
- Physical ADR files; only the proposed discrepancy/decision list is recorded for owning phases.

## Changed modules/files

Source files normalized from the archive, unchanged byte-for-byte:

- `START_HERE.md` and `FIRST_MESSAGE_TO_CODEX.md`;
- 9 files under `docs/design/`;
- 8 top-level build-pack documents, 15 phase prompts and 6 templates under `docs/build-pack/`.

P00-created files:

- `.gitignore`;
- `AGENTS.md`;
- `docs/implementation/PLAN.md`;
- `docs/implementation/PROGRESS.md`;
- `docs/implementation/TRACEABILITY.md`;
- `docs/implementation/RISK_REGISTER.md`;
- `docs/implementation/VERSIONS.md`;
- `docs/implementation/HANDOFF_P00.md`.

## Decisions and ADRs

No frozen architecture decision was changed and no ADR was accepted in P00.

Operational interpretations recorded for safe continuation:

1. The explicit P00-P14 build-pack prompt plus the latest handoff controls execution order. The legacy `AutPlay_Codex_Goal_Schema_Foundation_v1.md` remains a persistence requirement/history artifact, not a runnable first phase.
2. P00 “initial structure skeleton” means repository governance/document structure; P01 owns the code/monorepo skeleton.
3. PostgreSQL 18.x is a fixed major baseline; exact patch/image digest and all other reproducibility pins remain unresolved until validated in P01.
4. Missing Sync/OpenAPI/ADR artifacts named as later deliverables are not broken P00 links.

Proposed items P00-D001-P00-D009 are detailed in `PLAN.md`. Two need special attention:

- P00-D003: Track Identity requires immutable/general decision history, states and version evidence not fully represented by current reference DDL. Resolve through an approved change set before P02 implements migrations.
- P00-D004: frozen F-016 blanket auto-match wording is ambiguous against pre-benchmark deterministic T4 known-SHA reuse. User-approved clarification is required before P06/P10 relies on that path.

No decision above blocks P01.

## Migrations and contracts

None created or changed. The reference SQL remains a design artifact only. Sync Protocol v1, OpenAPI/event contracts, Alembic revisions and Room schema implementation belong to P04, P01/P02 and P05 as assigned in `PLAN.md`.

## Commands executed

| Command / validator | Result | Evidence |
| --- | --- | --- |
| `Get-FileHash -Algorithm SHA256 C:\Users\ptica\Downloads\AutPlay_Codex_Build_Pack_v1.zip` | PASS | ZIP SHA-256 `11EDC3EE63483A585DE851E92381E0700E8A8FC433F25C9D7C3BB2A880BAAE18` |
| PowerShell safe ZIP extraction using `System.IO.Compression.ZipFile` with canonical-path and no-overwrite checks | PASS | 40 files extracted; no unsafe or overwritten path |
| PowerShell SHA-256 comparison of every archive entry against its repository file | PASS | 40 files; 0 missing, 0 mismatched, 0 unsafe |
| PowerShell hidden-text scan plus `rg` hiding-CSS scan | PASS | 0 zero-width/bidi controls; 0 hiding CSS patterns; no hidden prompt injection found |
| PowerShell Markdown inline-link resolver | PASS | Final state: 46 Markdown files; 82 links; 26 repository-relative; 0 broken |
| PowerShell CommonMark fence scanner | PASS | Final state: 46 Markdown files; 422 markers / 211 blocks; 0 unclosed/mismatched |
| PowerShell phase filename/H1 enumeration for `docs/build-pack/prompts/P*.md` | PASS | Exactly P00-P14; 0 missing, duplicate, out-of-range or filename/H1 mismatch |
| PowerShell SQL object inventory over `AutPlay_PostgreSQL_Schema_v1.sql` | PASS | 52 tables, 48 indexes, 10 functions, 32 triggers, 12 schemas; matches design claim |
| `Get-ChildItem` compact repository inventory | PASS | Final state: 48 files = 40 source + 8 P00 outputs; 46 Markdown, 1 reference SQL and `.gitignore` |
| `git diff --cached --check -- .gitignore AGENTS.md docs/implementation` | PASS | No whitespace errors in P00-authored files; imported Markdown hard-break spaces remain byte-preserved and are validated separately |
| `git hash-object --no-filters` versus staged `git rev-parse :path` for every source file | PASS | 40 source files; 0 worktree/index byte mismatches |
| `git status --short --branch`, `git rev-parse --verify HEAD`, `git branch --show-current` | PASS | Intake state: unborn `master`, no prior commit/user files; final phase commit/check reported under Git state |
| Local tool inventory (`Get-Command` plus version commands) | PASS for audit | Git/Python/uv/Docker present; Java runtime 8 only; no `javac`/Gradle/Android CLI on PATH; observations are not pins |

## Test evidence

- All 40 source files still match the ZIP byte-for-byte after P00 documentation work.
- All eight artifacts listed by `AutPlay_Design_Package_v1.md` exist and their explicit relative links resolve.
- No actual broken Markdown link or missing phase prompt exists.
- Future paths such as Sync Protocol v1 and phase handoffs appear as planned deliverables/code spans, not unresolved current links.
- Design audit findings are routed to P00-D001-P00-D009 and R-002/R-013-R-016 rather than silently editing source documents.

## Acceptance criteria

| Criterion | Status | Evidence |
| --- | --- | --- |
| All source documents are available at stable repository paths | PASS | 40/40 archive entries present with identical SHA-256 |
| Root instructions do not contradict design precedence | PASS | `AGENTS.md` reproduces protocol precedence and narrower phase-scope rule |
| PLAN maps every phase to outputs and acceptance | PASS | P01-P14 table names prompt, dependency, deliverables, exit evidence and A-IDs |
| Risk register covers required minimum areas | PASS | R-001-R-017 include data loss, false merge, sync, Vault, Android URI, provider/legal, GPU, backup, secrets/security and performance |
| Version file separates pinned baseline and unresolved versions | PASS | Design baselines, observed-not-pinned tools, P01 resolutions and files of record are separate sections |
| Existing user changes are preserved | PASS | Initial workspace had only unborn `.git`; extraction refused overwrite; sources remain byte-identical |
| Handoff contains exact next command/prompt | PASS | “Preconditions for next phase” below |
| Relative links are valid | PASS | 0 broken repository-relative links |
| Markdown code fences are balanced | PASS | 0 unclosed/mismatched blocks |
| Phase prompts are complete and unique | PASS | P00-P14 exactly once, filenames match H1 |
| Compact inventory is recorded | PASS | 40 source files plus 8 P00-created governance files |
| Git status/commit is recorded | PASS | Branch and symbolic phase commit recorded below; immutable SHA reported after commit |
| No feature code or P01 work was started | PASS | Final inventory contains Markdown, reference SQL and `.gitignore` only |

## Known risks and debt

- P00-D003/P00-D004 are normative identity/data-model issues and must not be silently resolved during P02/P06/P10.
- P00-D005 formalizes the P12/P13 deferral boundary before P14.
- P00-D006 must map stable local/server aggregate identities in P04.
- Exact server/Android toolchains remain intentionally unresolved until P01 clean smoke tests.
- The current workstation does not yet provide a validated Android JDK/compiler/Gradle/SDK CLI path.
- Full open risks remain in `RISK_REGISTER.md`; P00 documentation is not mitigation evidence for future product risks.

## Preconditions for next phase

1. Confirm this P00 phase commit is `HEAD` and the worktree is clean.
2. Execute only `docs/build-pack/prompts/P01_monorepo_foundation.md`.
3. Follow `docs/build-pack/PROMPT_PROTOCOL.md` and read `AGENTS.md`, this handoff, `PLAN.md`, `VERSIONS.md`, `RISK_REGISTER.md`, `DECISION_REGISTER.md` and all P01 Inputs before edits.
4. Resolve/pin versions only with official-source compatibility review and clean build evidence.
5. Do not begin P02; create and validate `HANDOFF_P01.md`, then stop.

Exact next prompt:

```text
Выполни только AutPlay phase P01 по `docs/build-pack/prompts/P01_monorepo_foundation.md`. Следуй `docs/build-pack/PROMPT_PROTOCOL.md`; сначала полностью прочитай `AGENTS.md`, `docs/implementation/HANDOFF_P00.md`, `docs/implementation/PLAN.md`, `docs/implementation/VERSIONS.md`, `docs/implementation/RISK_REGISTER.md`, `docs/build-pack/DECISION_REGISTER.md` и все Inputs P01. Не начинай P02. Подтверди acceptance P01 проверками, создай `docs/implementation/HANDOFF_P01.md` и остановись.
```

## Git state

- Branch: `master`
- Commit: P00 phase commit at `HEAD`; retrieve with `git rev-parse HEAD`. A commit cannot embed its own final immutable SHA, so the exact hash is reported in the phase-completion response.
- Worktree: expected clean after the single local phase commit; verified immediately after commit and reported in the phase-completion response.
- Push: not performed.

## Blocking user decisions

None for P01. P00-D003 requires an approved data-model change before P02, and P00-D004 requires user-approved frozen-decision clarification before P06/P10.
