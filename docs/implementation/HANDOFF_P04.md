# AutPlay P04 Handoff

## Outcome

P04 is `PASS`. The phase freezes a language-neutral Sync Protocol v1 before either Android or
server sync engine exists. It delivers fourteen Draft 2020-12 JSON Schemas, a validated OpenAPI 3.1
contract for authenticated device binding/push/pull/bootstrap/status, machine-readable valid and
invalid golden vectors, an exact RFC 8785/SHA-256 hash oracle, specialized canonical
listening/actual-impression/direct-feedback contracts, compatibility/change policy, and a 51-test
device-independent validator suite.

P00-D006 Variant A and P00-D006-R1 are fully encoded. Android local IDs remain immutable; push
carries required local plus nullable server IDs; PostgreSQL sync aggregate IDs are canonical-only;
lost-ACK bootstrap reuses a proven unbound row; alias/canonical coexistence uses the accepted P05
durable redirect seam without violating unique server IDs or rewriting local foreign keys.

Both canonical server-only gates pass. Each path validated 78 harness tests, 51 P04 contract tests,
and the unchanged 298-test server/PostgreSQL suite against PostgreSQL 18.4/pgvector 0.8.6, then
removed its disposable container, network, and volume. P05 remains `NOT_STARTED`.

## Delivered scope

- `AutPlay_Sync_Protocol_v1.md` with device/user/profile binding, reset lineage, client event and
  deterministic hash, strict sequence rules, per-event ACK taxonomy, limits, conflict policy,
  cursor/pull transaction boundary, tombstone retention, bootstrap/cutover, status/errors,
  compatibility and next-version procedure.
- Nine versioned Draft 2020-12 schemas for device binding, client events, push request/response,
  pull response, bootstrap request/response, status, and error envelopes.
- Five specialized Draft 2020-12 schemas for canonical logical listening, actual recommendation
  impressions, direct recommendation feedback, bounded attribution, and two-stage known-event
  dispatch without adding a second transport or feedback endpoint.
- Static OpenAPI `3.1.0` source for `/api/v1/devices/bind`, `/api/v1/sync/push`,
  `/api/v1/sync/pull`, `/api/v1/sync/bootstrap`, and `/api/v1/sync/status`, all behind the P03
  bearer-session boundary.
- Language-neutral semantic vectors for every prompt case: same/different duplicate, reorder,
  gap, partial rejection, offline delete, edit-vs-delete, expired/forged/replayed cursor,
  bootstrap with pending edits, unknown event/enum, unsupported pulled version, oversized payload,
  reset lineage, and client-clock metadata.
- P00-D006/R1 vectors for adopted IDs, ordered pre-ACK follow-up, lost-ACK bootstrap reuse,
  server-bound and other-device projections, server-authoritative null rejection, alias-only and
  alias+canonical redirects, cycle rejection, tombstones before/after binding, unavailable and
  cross-owner IDs, and ambiguous one-ID envelopes.
- Interaction valid/invalid/hash vectors for organic and recommended listening, online/offline/local
  reranked impressions, actual-presentation idempotency, direct selection/dismissal, pre-ACK causal
  linkage, ownership/rank/recording rejection, explicit-null hashing, and privacy boundaries.
- Exact schema/OpenAPI/vector validation integrated into both canonical root check scripts.
- Complete wire-field storage ledger. Existing PostgreSQL/Room fields are named; transient fields
  are identified; P05 initial-schema additions and P09 durable bootstrap/reset proposals are
  explicit.

## Explicitly not delivered

- No Android or server sync engine, runtime sync endpoint, transport client, repository handler,
  background worker, or product-resource API.
- No P05 Room entity/DAO/schema, WorkManager, DataStore, Keystore, Compose feature, or Android
  compatibility spike.
- No Alembic revision, PostgreSQL DDL/model/reference-SQL change, or database data migration.
- No matcher, Vault/media, playback/download, recommendation serving/ranking/projection, Wave,
  import, GPU, deployment, hosted CI, production provider/TLS topology, external write, push, PR,
  or local commit.

## Changed files and modules

- Protocol: `docs/design/AutPlay_Sync_Protocol_v1.md`.
- Recommendation architecture: `docs/design/AutPlay_Recommendation_Subsystem_v1.md` and accepted
  `docs/adr/ADR-017-recommendation-pipeline-and-interaction-boundary.md`.
- Event/sync schemas: `contracts/events/v1/*.schema.json` and `contracts/events/README.md`.
- HTTP contract: `contracts/openapi/v1/autplay-sync.openapi.json` and its README.
- Golden evidence: `tests/fixtures/sync/v1/` and `tests/contract/test_sync_contract_v1.py`.
- Root development-only validation dependencies: `pyproject.toml`, `uv.lock`.
- Canonical integration: `scripts/check.ps1`, `scripts/check.sh`, root `README.md`.
- Verified-state records: `PLAN.md`, `PROGRESS.md`, `TRACEABILITY.md`, `RISK_REGISTER.md`,
  `VERSIONS.md`, `CI_PLAN.md`, `AUTPLAY_CODEX_BACKLOG.json`, and this handoff.

The worktree already contained the uncommitted Codex Development Harness v1 and P00-D006 changes
when P04 began. P04 preserved them and did not revert unrelated user work.

## Decisions and ADRs

1. P00-D006 Variant A/R1 is the aggregate-ID authority; P04 does not reinterpret it or add a
   PostgreSQL local/server mapping table.
2. A client event hash is lowercase hex SHA-256 over RFC 8785 canonical UTF-8 JSON of the complete
   immutable event with only `request_hash` omitted. Explicit null and additive fields are covered.
3. Push batches are 1-100 events and at most 8 MiB. Durable duplicate/integrity lookup precedes
   new-sequence admission. A batch may start below the next expected sequence only with a contiguous
   exact-duplicate replay prefix; its first new event must equal the next expected sequence.
   Reorder/gap is never silently sorted and cannot mutate or advance the new-event checkpoint.
4. Once sequence shape is valid, each event commits/classifies separately. A terminal semantic
   rejection advances the contiguous checkpoint, so one bad intent cannot deadlock later events.
5. Pull cursors are opaque lineage-bound tokens, never database-offset contracts. An Android page
   apply and cursor advance are one transaction; unsupported pulled versions are deferred as
   `UPGRADE_REQUIRED` and do not advance the cursor.
6. Destructive conflicts never use silent last-write-wins. Tombstones defeat older edits; explicit
   newer restore may be allowed for restorable user content; unresolved state is visible.
7. Existing device enrollment/authentication remains P03-owned. Device binding only confirms an
   already authenticated device/profile for sync.
8. User-approved ADR-017 fixes the P04 interaction/attribution boundary and future P07-P12 phase
   ownership without implementing recommendation serving, a projection, or either sync engine.

## Migrations and contracts

- Alembic revisions: none; head remains `0010_indexes_privileges`.
- PostgreSQL schema/model/reference DDL: unchanged.
- Static protocol version: `1`; OpenAPI document version: `1.0.0`.
- Schema dialect: JSON Schema Draft 2020-12.
- Limits: canonical payload 262,144 bytes; push request 8,388,608 bytes; push batch 100 events;
  pull/bootstrap page 500 items.
- Accepted P05 initial Room seams: Journal `idempotency_key`; cursor `journal_epoch` and opaque
  cursor; durable applied/deferred server-event dedupe; existing bootstrap/conflict state; and the
  P00-D006-R1 aggregate redirect table.
- P09-owned server proposals: durable device Journal epoch/lineage and a stable multi-request
  bootstrap snapshot/session unless P09 proves an equally durable mechanism.
- P07/P08-owned local proposal: persist owning domain/listening events with stable bounded
  recommendation attribution through restart and ACK.
- P09-owned canonical interaction proposal: additive append-only projection with actual impressions,
  direct feedback, presentation/display fields and owner-scoped presentation uniqueness;
  `listening_event` and preference truth remain.
- P11-owned persistence proposal: immutable pipeline/component/config manifest plus seed,
  interaction watermark and catalog/availability snapshot references for deterministic replay, plus
  a durable local presentation-to-impression mapping.

## Commands executed

| Command | Result | Evidence |
| --- | --- | --- |
| `uv run --frozen pytest tests/contract -q` | PASS | `51 passed`; Draft 2020-12 schema checks, OpenAPI specialized parity, valid/invalid/schema examples, RFC 8785 sync/interaction hashes, required semantic/P00-D006 vectors, recursive sensitive-key and storage-ledger guards |
| Root frozen lock + Ruff/format/strict-mypy over harness and `tests/contract` | PASS | 32 typed source/test files; no lint, format, or mypy finding |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly` | PASS | 78 harness + 51 contract + 298 server/PostgreSQL tests (`138.86s`); PostgreSQL `18.4`/pgvector `0.8.6`; no migration drift; disposable project removed completely |
| `& 'C:\Program Files\Git\bin\bash.exe' scripts/check.sh --server-only` | PASS | Same 78 + 51 + 298 checks; server suite `142.74s`; disposable project removed completely |
| `git diff --check` and JSON parsing/path/scope audits | PASS | No whitespace error; all contracts/fixtures parse; no P05/server/Android/migration implementation path added |

The recommendation-contract amendment reran both complete canonical gates after the targeted
51-test contract gate. The repeated Ruff formatting failure triggered the repository protocol:
official Ruff formatter/linter/line-length/suppression options were reviewed, the scoped canonical
formatter was selected, and the final lint/format/type/test gates passed without policy weakening.

## Acceptance evidence

| Criterion | Status | Evidence |
| --- | --- | --- |
| Sync Protocol v1 covers every required semantic | PASS | Normative protocol sections 2-12 plus required sync/interaction case guards in the 51-test suite |
| Versioned JSON Schemas validate | PASS | Fourteen Draft 2020-12 schemas self-check; generic and specialized valid/invalid/hash examples execute |
| OpenAPI binding/push/pull/bootstrap/status validates | PASS | `openapi-spec-validator==0.9.0`; operation/binding/schema parity tests |
| Golden vectors are language-neutral and machine-readable | PASS | JSON fixtures with required IDs, inputs and non-empty expected outcomes |
| P00-D006 Variant A/R1 is complete | PASS | Full adopted/bound/bootstrap/tombstone/redirect/collision/authorization vector matrix |
| Every wire field has a storage or transient/proposal classification | PASS | Protocol section 3 field table and section 12 complete family ledger; test guard |
| Compatibility and next-version procedure exists | PASS | Protocol section 11; additive/unknown/version tests |
| Recommendation API/model evolution boundary is explicit | PASS | ADR-017 and `AutPlay_Recommendation_Subsystem_v1.md`; event schemas carry no model/ranker/config or candidate-generator fields |
| Requests can be reproduced from declared versions and snapshots | BASELINED | P11 contract requires immutable pipeline/component/config, seed, event watermark and catalog/availability snapshot; executable replay evidence remains P11-owned |
| Candidate generators can be replaced without event/API changes | BASELINED | P11 ports and swap-test requirement; P04 attribution is request/rank/recording based and generator/model independent; executable swap remains P11-owned |
| Baseline can run without a sequential model | BASELINED | P11/P12 prompts and ADR-017 forbid a sequential/GPU serving dependency; executable cold-start/import evidence remains P11-owned |
| No engine, P05 or PostgreSQL migration started | PASS | Changed-path/source/route/Alembic audits; existing 298-test migration gate |
| Both canonical gates pass and clean resources | PASS | PowerShell and Git Bash evidence above |

P04 freezes prerequisites for A-018-A-022 but does not make those product-behavior rows pass; their
engine evidence remains owned by P09.

## Independent review

The required read-only recommendation-contract review found four major and one minor issue. All
were fixed and the reviewer confirmed no remaining finding from that set:

- OpenAPI now conditionally validates the complete known interaction payloads, including actual
  presentation fields, while unknown event types retain the generic compatibility path.
- Top-level and recursively nested secret/path/model-feature property names are schema-invalid in
  JSON Schema and OpenAPI; dedicated negative vectors prove both paths.
- Logical listening fields and origin/context vocabularies map directly to the existing PostgreSQL
  contract, and `RECOMMENDED` requires attribution.
- Durable client presentation mapping plus P09 owner-scoped semantic uniqueness prevents a new UUID
  from creating a second impression for the same presentation tuple.
- `EVENT_AGGREGATE_ID_MISMATCH` and `IMPRESSION_ALREADY_RECORDED` have required semantic vectors.

The reviewer reran the 51-test contract gate and directly observed JSON Schema/OpenAPI rejection for
top-level `access_token` and missing impression presentation fields.

## Known risks and debt

- P05 must prove Room 3/KSP/SQLite/FTS/R8/device compatibility before materializing the accepted
  initial schema and must keep all pending Journal/redirect/cursor operations transactional.
- P09 must implement the stable bootstrap/reset persistence seam and prove convergence/process-death
  behavior against these vectors. Contract tests alone do not prove a sync engine.
- P07-P09 must preserve one owning domain/Journal event, stable attribution through restart, and an
  exactly-once append-only interaction projection; P11 still owns serving/ranking/replay behavior.
- P09 must map canonical listening origin/context values without translation and enforce
  presentation semantic uniqueness in addition to transport event-ID idempotency.
- The OpenAPI file is static by design; mounting routes before P09 would be out of phase.
- Hosted Windows/Linux/macOS evidence remains unavailable because no Git remote/CI provider is
  configured. Local PowerShell and Git Bash plus Linux-container PostgreSQL evidence is green.
- The repository remains intentionally dirty with prior harness/P00-D006 work and P04 changes;
  there is no isolated P04 commit because the user did not authorize one.

## Exact prerequisite and prompt for P05

P04 is green. Before P05, read this handoff and `docs/build-pack/prompts/P05_android_foundation.md`,
then run the Room 3/Kotlin/KSP/AGP compatibility gate before generating the user schema. Do not
silently fall back to Room 2 and do not implement sync transport.

Exact next phase request:

```text
Выполни только AutPlay phase P05 по `docs/build-pack/prompts/P05_android_foundation.md`. Следуй `docs/build-pack/PROMPT_PROTOCOL.md`, прочитай `docs/implementation/HANDOFF_P04.md`, сначала выполни Room 3/Kotlin/KSP/AGP compatibility gate, реализуй только Android local-first foundation и не начинай P06 или sync transport. Создай `docs/implementation/HANDOFF_P05.md` и остановись.
```

P05 was not started by P04. A later Codex phase-pipeline milestone records standing user
authorization for P04 -> P05: after re-running the declared P04 acceptance gates, the trusted Stop
hook may start P05 without an additional confirmation. The hook setup itself does not implement P05.

## Git state

- Branch: `codex/autplay-harness-v1`.
- HEAD before P04: `0023fa9ad9d12633ad988230662fbd69bb74eb20`.
- P04 commit: none; commit/push/PR were not authorized or performed.
- Worktree: intentionally dirty with pre-existing harness/P00-D006 changes plus uncommitted P04
  artifacts. No destructive reset/checkout/stash was used.
- Deployment/external write: not performed.

## Blocking user decisions

None remain for P04 completion or P05 eligibility. The verified P04 -> P05 Stop-hook continuation
is pre-authorized; no additional phase confirmation is required.
