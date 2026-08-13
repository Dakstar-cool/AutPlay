# P00-D003 Proposed Change Set: Immutable Identity Decision History

**Status:** PROPOSED / NOT APPROVED

**Prepared:** 2026-08-13

**Base commit:** `48f8198738c6d50988e903cff7a8b4911c4d4615`

**Proposal revision:** see [`P00-D003_PROPOSAL.sha256`](P00-D003_PROPOSAL.sha256)

**Gates:** P02 remains `BLOCKED`; F-016 and P00-D004 remain unchanged

## 1. Purpose and stop boundary

This review packet proposes the smallest coordinated resolution of P00-D003. It aligns the narrower Track Identity contract with the ER model, PostgreSQL design, reference DDL and P02 inventory before any executable persistence is implemented.

This packet is not approval and changes no normative design source. It creates no migration, SQLAlchemy mapping, runtime table, API, matcher, benchmark or `HANDOFF_P02.md`. After the proposal checks pass, work stops for explicit user approval of the exact proposal revision.

## 2. Source conflict

The source-of-truth order makes the narrower Track Identity specification authoritative for identity semantics:

- [`AutPlay_Track_Identity_v1.md`](../design/AutPlay_Track_Identity_v1.md) lines 475-485 defines five resolver states.
- The same file lines 544-574 requires the full stored automatic/manual decision record and provider-independent explanation.
- Lines 761-769 require immutable matcher/threshold history, per-evidence extractor versions and a new row for every re-score.
- Lines 445-458 require an active matcher/threshold set, tier, score, margin and no hard conflict before auto-match.
- [`DECISION_REGISTER.md`](../build-pack/DECISION_REGISTER.md) F-014-F-016 makes versioned evidence, unresolved-first behavior and the benchmark gate frozen.
- [`AutPlay System Architecture v1.md`](<../design/AutPlay System Architecture v1.md>) lines 271-282 assigns match decisions to Identity Catalog; Library Migration owns import workflow rows.

The current physical model conflicts with that contract:

| Current object | Exact gap |
| --- | --- |
| `importing.import_entry` | Import-only query; state CHECK omits `INTEGRITY_CONFLICT` and `DEFERRED_EVIDENCE`; current projection is treated as history |
| `importing.match_candidate` | Candidate and decision are conflated; only `NONE/ACCEPTED/REJECTED`; no general query identity, normalization/extractor versions, top-2/margin, origins, actor type or supersession |
| Candidate uniqueness | `(import_entry_id, recording_id, matcher_version)` prevents distinct calibrator/threshold/evidence runs |
| Import cascade | Deleting an import entry deletes candidate rows and therefore cannot preserve identity history independently |
| Version strings | Matcher/calibrator/threshold values have no immutable registry or activation proof |
| PostgreSQL acceptance | Claims lossless identity-version retention although the required fields/states do not exist |

References: [`AutPlay_PostgreSQL_Schema_v1.sql`](../design/AutPlay_PostgreSQL_Schema_v1.sql) lines 1094-1158 and [`AutPlay_PostgreSQL_Schema_v1.md`](../design/AutPlay_PostgreSQL_Schema_v1.md) lines 487-500.

## 3. Proposed ownership and object inventory

Delete one draft table and its explicit index:

- `importing.match_candidate`;
- `ix_match_candidate_entry_rank`.

Add six `identity` tables:

1. `identity.matcher_release`;
2. `identity.calibrator_release`;
3. `identity.threshold_set`;
4. `identity.match_policy_activation`;
5. `identity.match_decision`;
6. `identity.match_candidate_evidence`.

Add six explicit indexes:

1. `ix_threshold_set_scope`;
2. `ix_match_policy_activation_threshold_time`;
3. `ix_match_decision_query_time`;
4. `ix_match_decision_candidate_time`;
5. `ix_match_decision_matcher_time`;
6. `ix_match_candidate_evidence_recording`.

Add three helper functions and eight triggers:

- `app_private.reject_identity_history_mutation()` plus three `BEFORE UPDATE OR DELETE` triggers on the pure release/threshold registries;
- `app_private.validate_match_policy_activation()` plus one trigger that validates INSERT and rejects UPDATE/DELETE on the activation log;
- `app_private.validate_match_decision()` plus two deferred constraint triggers on decision/evidence INSERT and mutation, sealing the aggregate snapshot;
- the same decision validator plus two deferred projection triggers on `importing.import_entry` and `library.user_track_ref`.

Expected clean-reference inventory after approval:

| Object | Before | Delta | After |
| --- | ---: | ---: | ---: |
| Tables | 52 | -1 + 6 | 57 |
| Explicit indexes | 48 | -1 + 6 | 53 |
| Helper/constraint functions | 10 | +3 | 13 |
| Triggers | 32 | +8 | 40 |

PK/UNIQUE constraints create PostgreSQL-owned backing indexes and are not counted as explicit `CREATE INDEX` objects, matching the current counting convention.

## 4. Proposed physical contract

### 4.1. `identity.matcher_release`

Immutable matcher manifest:

| Column | Contract |
| --- | --- |
| `matcher_version` | text PK |
| `candidate_generation_version` | non-empty text |
| `normalization_version` | non-empty text |
| `feature_extractor_versions` | JSONB object, canonical schema v1, maximum 128 KiB; exact feature-to-extractor manifest |
| `manifest_sha256` | 32-byte hash of canonical release manifest |
| `created_at` | server timestamp |

All version identifiers are 1..200 UTF-8 characters. The release row never changes. A changed generator, normalizer, extractor manifest or matcher produces a new `matcher_version`.

### 4.2. `identity.calibrator_release`

Immutable calibrator artifact:

| Column | Contract |
| --- | --- |
| `calibrator_version` | text PK |
| `matcher_version` | FK to matcher release, `ON DELETE RESTRICT` |
| `evidence_mode` | `METADATA_ONLY` or `AUDIO_AVAILABLE` |
| `artifact_sha256` | 32-byte artifact hash |
| `input_schema_version` | non-empty text |
| `created_at` | server timestamp |

A no-calibrator deterministic/shadow evaluation leaves the decision calibrator FK null; it does not invent a placeholder release.

### 4.3. `identity.threshold_set`

Immutable threshold/gate definition:

| Column | Contract |
| --- | --- |
| `threshold_set_version` | text PK |
| `matcher_version` | FK to matcher release |
| `calibrator_version` | nullable FK only for non-calibrated shadow evidence; non-null for an activatable set |
| `evidence_mode` | `METADATA_ONLY`, `AUDIO_AVAILABLE` or `DETERMINISTIC_BYTES` |
| `minimum_evidence_tier` | `T0` through `T4` |
| `auto_threshold` | numeric 0..1 |
| `review_threshold` | numeric 0..1 |
| `margin_threshold` | numeric 0..1 |
| `benchmark_report_sha256` | nullable for shadow-only set; required by activation trigger; points to an external immutable report artifact, not benchmark payload in the registry |
| `gate_metadata` | allowlisted JSONB object, schema versioned, maximum 128 KiB; no secrets/raw paths/private URLs |
| `created_at` | server timestamp |

Constraints require `auto_threshold >= review_threshold`. Initial shadow values remain benchmark hypotheses, not an active production set.

### 4.4. `identity.match_policy_activation`

Append-only policy lifecycle/rollback event:

| Column | Contract |
| --- | --- |
| `activation_id` | UUID PK |
| `evidence_mode`, `evidence_tier` | explicit activation scope |
| `sequence_no` | positive, unique within scope |
| `action` | `ACTIVATE`, `DEACTIVATE` or `ROLLBACK` |
| `threshold_set_version` | FK to immutable threshold set |
| `supersedes_activation_id` | nullable backward self-FK |
| `actor_type` | `ADMIN` for activation lifecycle |
| `actor_user_id` | required FK to account user |
| `reason`, `created_at` | required audited reason and server time |

The validation trigger requires a non-null calibrator, a 32-byte benchmark report hash for `ACTIVATE`/`ROLLBACK`, matching matcher/calibrator/mode/tier scope and an active `OWNER`/`ADMIN` account. The first event has sequence 1; every later event must supersede the latest same-scope event with `sequence_no = predecessor + 1`. Named UNIQUE constraints on `(evidence_mode, evidence_tier, sequence_no)` and non-null `supersedes_activation_id` prevent concurrent branches. Rollback may target only a threshold set previously activated in that scope. Current policy is the latest event; history is never rewritten.

Activation/deactivation and an applied SYSTEM auto decision take the same transaction-scoped advisory lock derived from `(evidence_mode, evidence_tier)`. This prevents a policy from being deactivated between validation and commit.

The initial reference DDL inserts zero activation events.

### 4.5. `identity.match_decision`

Immutable automatic/manual decision row:

| Column group | Contract |
| --- | --- |
| Identity | `decision_id` UUID PK; `query_type`; typed query key; conditionally required owner/device; allowlisted query snapshot; snapshot canonicalization/schema version and SHA-256 |
| Kind/mode | `decision_kind = EVALUATION or REVIEW_ACTION`; `execution_mode = SHADOW or APPLIED`; optional manual `review_action` |
| Result | nullable candidate/action `candidate_recording_id`; one of the five resolver `decision_state` values; exact `candidate_count` and aggregate candidate-evidence SHA-256 |
| Version snapshot | explicit `evidence_mode`; candidate-generation, normalization and feature-extractor versions plus matcher/calibrator FKs; threshold-set FK is required for `AUTO_MATCH` and otherwise nullable |
| Scores | nullable `raw_score`, `confidence`, `top2_confidence`, `margin`; nullable `evidence_tier` |
| Explanation | top-one summary `feature_scores`, `hard_conflicts`, `candidate_origins`, byte-equal to rank-1 evidence; optional shadow counterfactual classification; unknown keys round-trip unchanged |
| Actor | `SYSTEM`, `USER` or `ADMIN`; user FK required for USER/ADMIN and null for SYSTEM |
| Idempotency | scope 1..100 chars and key 1..200 chars plus 32-byte request hash; unique scope/key |
| Lineage | nullable backward `supersedes_decision_id`, required reason when present, `decided_at` |

Allowed `query_type` values for v1 are `IMPORT_ENTRY`, `USER_TRACK_REF`, `LOCAL_AUDIO`, `EXTERNAL_REFERENCE`, `VAULT_OBJECT` and `AUDIO_VARIANT`. Typed nullable query FKs are used for all server-owned objects; a CHECK requires exactly the FK matching `query_type`. `IMPORT_ENTRY`, `USER_TRACK_REF` and `LOCAL_AUDIO` require `owner_user_id`; `LOCAL_AUDIO` is an opaque client-local UUID and also requires `device_id`. Shared `EXTERNAL_REFERENCE`, `VAULT_OBJECT` and `AUDIO_VARIANT` SYSTEM queries may have a null initiating user; user-initiated access to them is authorized by the application before command creation. A DB validator enforces object ownership for owner-scoped queries. There is no unverified polymorphic UUID reference.

The query snapshot is a durable sanitized explanation input. Its allowlisted schema admits only normalized identity attributes and opaque evidence IDs, rejects unknown sensitive fields before persistence, and has maximum size 128 KiB. Canonicalization is RFC 8785 JSON Canonicalization Scheme, named by `snapshot_canonicalization_version`; `query_snapshot_sha256` hashes those exact bytes. Privacy/content allowlisting and canonical-hash computation are application invariants proven by P02 negative tests; the database enforces JSON type, version presence and `octet_length(convert_to(value::text, 'UTF8')) <= 131072`.

The decision row directly retains every normative stored-decision field. Its version snapshot must equal the referenced immutable release manifests; the deferred validator checks this at commit.

### 4.6. `identity.match_candidate_evidence`

Immutable per-candidate evidence snapshot:

| Column | Contract |
| --- | --- |
| `match_candidate_evidence_id` | UUID PK |
| `decision_id` | FK to match decision, `ON DELETE RESTRICT` |
| `recording_id` | FK to Recording, `ON DELETE RESTRICT` |
| `rank` | positive integer |
| `raw_score`, `confidence` | nullable numeric 0..1 for deferred evidence; otherwise required |
| `evidence_tier` | `T0` through `T4` |
| `feature_scores` | JSONB array schema v1, maximum 128 KiB, preserving `feature`, `value`, `present`, `extractor_version`, `evidence_refs` |
| `hard_conflicts` | JSONB array schema v1, maximum 128 KiB |
| `candidate_origins` | JSONB array schema v1, maximum 128 KiB, preserving every generator and rank |
| `extractor_versions` | JSONB object schema v1, maximum 128 KiB |
| `evidence_sha256` | 32-byte hash of the RFC 8785 canonical candidate evidence document |
| `created_at` | server timestamp |

Rank is bounded to 1..100. UNIQUE constraints cover `(decision_id, rank)` and `(decision_id, recording_id)`. The decision stores `candidate_count` (0..100) plus SHA-256 over the byte stream `int4send(rank) || evidence_sha256`, concatenated in ascending rank order; zero candidates hash the empty byte stream. Each `evidence_sha256` covers a schema-versioned RFC 8785 document containing recording ID, scores, tier, features, conflicts, origins and extractor versions. The deferred validator requires ranks to be contiguous, row count/hash to match, the selected Recording and complete rank-1 summary to agree for an evaluation, and rank 2 to agree bidirectionally with `top2_confidence`/`margin`. The evidence trigger repeats aggregate validation on INSERT, so a post-commit addition no longer matches the sealed count/hash and is rejected. Thus a decision plus its candidate set is one sealed snapshot, not merely a collection of immutable rows.

Every versioned JSON structure has an explicit schema-version column or enclosing schema version. Every listed JSON column is individually bounded by `octet_length(convert_to(value::text, 'UTF8')) <= 131072`; `match_candidate_evidence` additionally caps the sum of its four JSON columns at 131072 bytes, and one decision caps total canonical candidate-evidence bytes at 4 MiB. Feature and origin arrays are limited to 256 elements and hard-conflict arrays to 64. JSONB shape/cardinality and relational IDs are database constraints where practical; the sanitizer/content allowlist and RFC 8785 hash computation are application invariants with negative integration fixtures.

### 4.7. Owner projections

`importing.import_entry` gains nullable `current_match_decision_id` referencing `identity.match_decision` with `ON DELETE RESTRICT`. `match_status` remains a workflow/current projection, gains `MANUAL_MATCH`, `MANUAL_UNRESOLVED`, `INTEGRITY_CONFLICT` and `DEFERRED_EVIDENCE`, and does not become the historical source of truth.

A database trigger verifies that the referenced decision has `query_type = IMPORT_ENTRY`, the matching typed FK, the same owner, `APPLIED` mode, and a state/action consistent with `match_status` and `selected_recording_id`. An import query envelope is retained while identity history refers to it; configured raw-payload cleanup nulls/erases the bounded raw payload instead of cascading deletion into `identity`. Direct DML cannot create a cross-entry, cross-user, shadow or target-divergent projection.

`library.user_track_ref` also gains nullable `current_match_decision_id`. Its deferred projection trigger requires matching `USER_TRACK_REF` identity/owner, `APPLIED` mode and agreement among review/auto outcome, `resolution_status`, `recording_id`, confidence and decision target. Other query types have history but no current-state projection in this change set; any later owner projection must adopt the same validated-pointer rule.

## 5. State and nullability rules

Resolver state remains exactly:

```text
AUTO_MATCH
REVIEW_REQUIRED
NO_MATCH
INTEGRITY_CONFLICT
DEFERRED_EVIDENCE
```

These values are not interchangeable with candidate disposition, UserTrackRef resolution state, import workflow state or Recording lifecycle state.

Mandatory constraints/validator rules:

- scores are null or in `[0,1]`;
- when both exist, rank-1 `confidence >= top2_confidence` and margin is non-negative;
- `top2_confidence` requires `confidence` and `margin`;
- `margin = confidence - top2_confidence` at stored numeric precision;
- a missing top-two candidate requires both `top2_confidence` and `margin` null;
- `AUTO_MATCH` is valid only for `APPLIED + EVALUATION`, `actor_type = SYSTEM`, a selected rank-1 candidate, matching referenced versions, sufficient tier/score/margin, an empty hard-conflict set, a non-null calibrator and the latest benchmark-approved active exact-scope policy;
- a shadow/unapproved evaluation keeps `decision_state = REVIEW_REQUIRED`; it may store an explicitly labeled counterfactual classification in explanation metadata but cannot store resolver state `AUTO_MATCH`;
- `SHADOW` never authorizes catalog/user/import mutation;
- `REVIEW_REQUIRED` evaluation requires at least one candidate; evidence acquisition that produced no scored candidate uses `DEFERRED_EVIDENCE`;
- `NO_MATCH` has no selected candidate but may retain ranked below-review candidates;
- `INTEGRITY_CONFLICT` requires a non-empty hard-conflict/integrity reason set and cannot mutate a resolution projection;
- `DEFERRED_EVIDENCE` may retain candidates with null scores and cannot mutate a resolution projection;
- a pre-P00-D004 T4 observation without calibrator/top-two/margin is stored as `SHADOW + REVIEW_REQUIRED` with nullable threshold set, never as `AUTO_MATCH`;
- a review action is always `APPLIED`, requires a non-`AUTO_MATCH` predecessor and a USER/ADMIN actor with owner/admin authorization, copies that predecessor resolver state, and never converts the state to `AUTO_MATCH`;
- `ACCEPT` is allowed from `REVIEW_REQUIRED`, requires `reviewed_candidate_evidence_id` from the immediate predecessor and atomically projects its Recording as `MANUAL_MATCH`/resolved target;
- `REJECT` is allowed from `REVIEW_REQUIRED`, requires `reviewed_candidate_evidence_id` from the immediate predecessor, and leaves the owner projection reviewable rather than rejecting every candidate/query;
- `KEEP_UNRESOLVED` is allowed from any non-auto resolver state, has no selected target and projects `MANUAL_UNRESOLVED`/unresolved state;
- `CREATE_RECORDING` is allowed from `REVIEW_REQUIRED`, `NO_MATCH` or `DEFERRED_EVIDENCE`, has no reviewed-candidate FK, requires the newly created Recording as action target and atomically projects `MANUAL_MATCH`/resolved target;
- `INTEGRITY_CONFLICT` permits only `KEEP_UNRESOLVED` until a new evaluation proves the conflict cleared;
- a review action must supersede an evaluation or prior review in the same lineage; global `MERGE` is not an action in this table.

Here `decision_state` records the predecessor resolver condition being reviewed, while `review_action` records the authorized human outcome. `reviewed_candidate_evidence_id` is a composite FK/validator-backed reference to evidence owned by the immediate predecessor; the review row retains the predecessor aggregate hash and candidate target for deterministic explanation. This deliberately prevents selection of an arbitrary Recording or labeling a manual accept as auto-match. Projection effects, the review row, audit and any new UserTrackRef/Recording are committed in one application transaction; deferred projection triggers reject a partial or divergent commit.

## 6. Append-only supersession

Strict immutability is incompatible with updating an old row's `superseded_by_decision_id`. The coordinated source edit therefore replaces the physical forward pointer with `supersedes_decision_id` on the new row. `superseded_by` remains a derived inverse query.

The validator rejects:

- self-supersession;
- a predecessor with different typed query identity; `LOCAL_AUDIO` equality includes owner and device;
- an equal/earlier `decided_at`;
- more than one direct successor;
- a lineage cycle;
- review action without an evaluation/review predecessor.

Named `UNIQUE (supersedes_decision_id)` for non-null predecessors makes the single successor rule concurrency-safe. Owner projections select only the unique lineage leaf. Idempotent replay with the same scope/key and request hash returns the stored decision; the same key with a different hash is rejected with a stable conflict error.

Re-score, manual review, conflict discovery and rollback each append a new decision. Old scores, feature values, origins, actor and release FKs remain byte-for-byte explainable.

## 7. Frozen F-016 and P00-D004

P00-D003 changes storage safety only.

- No activation event exists at initial schema creation.
- An inactive/shadow set cannot authorize an applied `AUTO_MATCH`.
- T4 can be stored as evidence or shadow output but cannot be applied pre-benchmark by this change set.
- This proposal does not decide whether deterministic T4 reuse is semantically outside frozen F-016.
- It does not rename or silently weaken F-016.

That semantic choice remains P00-D004 and requires separate user approval before P06/P10 behavior.

## 8. Coordinated files after explicit approval

Only an exact approval of this proposal revision authorizes edits to:

| File | Approved-purpose delta |
| --- | --- |
| `docs/adr/ADR-015-immutable-identity-decision-history.md` | Change status to Accepted and record approved proposal revision |
| `docs/design/AutPlay_Track_Identity_v1.md` | Clarify execution mode, review action, state nullability, registries and backward supersession; retain F-016 boundary |
| `docs/design/AutPlay ER Model v1.md` | Add general identity objects and make import rows projections |
| `docs/design/AutPlay_PostgreSQL_Schema_v1.md` | Update inventory, invariants, migration layout and tests |
| `docs/design/AutPlay_PostgreSQL_Schema_v1.sql` | Apply the reviewed six-table physical contract and constraints |
| `docs/build-pack/prompts/P02_postgresql_persistence.md` | Change exact inventory to 57/53 and add required history/immutability tests |
| `docs/implementation/PLAN.md` | Mark P00-D003 approved/applied only after all coordinated edits validate |
| `docs/implementation/TRACEABILITY.md` | Link accepted ADR and synchronized sources; keep product behavior unimplemented |
| `docs/implementation/RISK_REGISTER.md` | Keep R-014 open until P02 executable evidence; record design resolution |
| `docs/implementation/PROGRESS.md` | After all coordinated edits validate, move P02 from `BLOCKED` to `NOT_STARTED`; do not mark P02 started |

Not authorized: edits to F-016, P00-D004, completed `HANDOFF_P01.md`, A-003/A-004 status, application code, Alembic, SQLAlchemy, API, matcher behavior, benchmark data or later-phase artifacts.

After approved source application and validation, create one dedicated local change-set commit and stop again before P02.

## 9. Required validation after normative application

### P00-D003 structural checks

- exact 57-table / 53-explicit-index / 13-function / 40-trigger inventory;
- reference DDL executes in one transaction on pinned PostgreSQL 18.4 + pgvector 0.8.6;
- all named PK/FK/UQ/CK/triggers exist;
- Markdown links/fences and P00-P14 prompt numbering remain valid;
- no Alembic revision, mapping or `HANDOFF_P02.md` exists.

### P02 real persistence tests

- round-trip every decision field and all five resolver states;
- import, local-audio and external-reference query fixtures;
- multiple candidate origins/ranks and unknown feature round-trip;
- seal 0/1/2/100 candidates and reject candidate 101, gaps and post-commit/late insertion;
- validate every state/nullability and manual action/state/projection combination;
- invalid state, actor/user mismatch, score range and margin mismatch rejection;
- reject shadow resolver state `AUTO_MATCH`; preserve counterfactual shadow output under `REVIEW_REQUIRED` only;
- reject applied auto-match below threshold, with insufficient tier/margin or any hard conflict;
- require result Recording/atomic projection for `CREATE_RECORDING` and prove no global merge;
- reject ACCEPT/REJECT target not present in the immediate predecessor candidate evidence;
- reject orphan, wrong-type, cross-user/device query and cross-import/shadow/target-divergent projection pointers;
- unknown release FK and cross-version snapshot mismatch rejection;
- UPDATE/DELETE rejection for all six history/registry tables;
- re-score and review append new rows while old explanation is unchanged;
- self/cycle/cross-query/earlier-time/branch supersession rejection;
- two-transaction decision and activation branch races leave one successor only;
- import cleanup leaves identity history intact;
- inactive or mismatched policy cannot authorize applied auto-match;
- pre-benchmark applied T4 and nullable-calibrator activation are rejected;
- benchmark hash, active OWNER/ADMIN and exact matcher/calibrator/mode/tier are required for activation;
- threshold activation and rollback append events without rewriting releases;
- activation sequences cannot gap/branch, rollback cannot target a never-active set, and deactivate-vs-auto race is serialized;
- provider-independent explanation survives unavailable provider;
- RFC 8785 hash, JSON shape/cardinality, N-1/N/N+1 byte and sensitive-field fixture checks;
- selected/top-one evidence equality, rank ordering and top-two/margin bidirectional checks;
- same-hash idempotent replay returns the row and a different-hash replay is rejected;
- clean Alembic upgrade/downgrade/upgrade, object inventory, metadata drift and named constraints on real PostgreSQL.

### P10 behavior/benchmark tests, explicitly deferred

P10 still owns hard-negative identity fixtures, manual review behavior, labeled benchmark/calibration metrics and any activation decision. This storage change does not claim A-024/A-025 behavior PASS.

## 10. Proposal validation evidence

Executed against the exact proposal candidate before freezing its hash:

| Check | Result |
| --- | --- |
| Structural SQL feasibility probe on pinned PostgreSQL 18.4 / pgvector 0.8.6 | PASS: a structural prototype of all six proposed tables/registries, functions and triggers executed in a disposable transaction; two decisions, one candidate-evidence row and one activation inserted; immutable UPDATE, hard-conflict applied auto-match and invalid margin rejected; transaction rolled back. Final exact normative DDL must rerun every expanded §9 case after approval |
| Disposable Compose cleanup | PASS: `autplay-p00d003-probe` has 0 containers, 0 volumes and 0 networks |
| Proposal/repository links and fenced blocks | PASS before manifest creation: 60 Markdown files, 123 inline links, 43 relative links; only the two expected references to the not-yet-created manifest pending, 0 other broken; 434 fence markers / 217 blocks, 0 unclosed. Final post-manifest scan must report 0 broken |
| P00-P14 prompt sequence | PASS: 15 prompts, exact P00..P14, unique and filename/H1-aligned |
| Git scope | PASS: six proposal/tracker paths only, `git diff --check` PASS; no changed design/build-pack/code/deploy/contract path and no Alembic revision, mapping or `HANDOFF_P02.md` |
| P02/A-003/A-004/R-014 state | BLOCKED / NOT_STARTED / NOT_STARTED / OPEN |

The feasibility probe establishes syntax/constraint viability, not application of the normative DDL. The manifest is created only after these exact scan results; a final post-manifest scan must reduce the two expected pending references to zero without changing any hashed file.

## 11. Approval wording

Approval must cite the exact manifest revision printed in `P00-D003_PROPOSAL.sha256`:

```text
Утверждаю ADR-015 и P00-D003 change set revision <manifest-sha256>. Разрешаю синхронно изменить только перечисленные normative design/DDL/test documents. F-016 и P00-D004 не изменять. P02 не начинать; после применения change set и проверок создать один локальный commit и остановиться.
```

Any requested semantic change to auto-match/T4, any added object outside this manifest, or any request to begin P02 requires a separate explicit instruction.

The proposal packet and its manifest remain immutable historical review artifacts after hash freeze. If approved, the external approval text and the Accepted ADR-015 record this aggregate revision; the packet itself is not relabeled or rehashed.
