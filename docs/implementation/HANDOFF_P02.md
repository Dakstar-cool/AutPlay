# AutPlay P02 Handoff

## Outcome

P02 is `PASS`. PostgreSQL schema v1 is executable through one linear Alembic head, the complete physical model has typed SQLAlchemy 2 persistence mappings, and the declared migration/invariant contract passes against the digest-pinned PostgreSQL 18.4 + pgvector 0.8.6 service.

The migrated catalog and the independently loaded reference DDL agree on exactly 57 module tables, 53 explicit non-constraint indexes, 13 `app_private` functions, and 40 non-internal triggers. Legacy `importing.match_candidate` and ANN indexes are absent, initial policy activation history is empty, and Alembic/metadata comparison reports no unexplained drift.

## Scope delivered

- Alembic configuration and a reviewed linear `0001`-`0010` revision chain with reversible empty-development-database downgrades and no destructive fallback.
- A repository EOL contract that keeps both normative and vendored SQL at LF, so the frozen byte hash is stable in clean checkouts and Git archives.
- Exact schemas, extensions, tables, constraints, indexes, functions, triggers, sequence privileges, and PUBLIC revokes from PostgreSQL schema v1.
- 57 typed SQLAlchemy row mappings covering 616 columns and 53 explicit indexes; all persistence classes remain inside the PostgreSQL adapter and define no ORM relationship/domain behavior.
- A guarded disposable test database harness using a random loopback-only port, unique database names, bounded timeouts, real commits, and exact cleanup.
- Core physical invariants, migration lifecycle, exact catalog/drift, restricted-role, job-claim concurrency, and audio servability tests.
- Full ADR-015 identity persistence evidence: all five resolver states, all six typed query forms, every decision/evidence field, sealed 0/1/2/100-candidate sets, append-only registries/history, lineage/review/projection negatives, policy lifecycle and concurrency, F-016/T4 gates, RFC 8785/privacy/size boundaries, and idempotent command behavior.
- Canonical identity document helpers and bounded persistence commands. They recompute query/candidate RFC 8785 hashes, byte sizes, rank aggregate, and 4 MiB total before persistence and on replay.
- A conservative `gate_metadata` schema v1 that accepts only `{}`. The normative contract defines no portable non-empty keys; any later metadata must use an explicitly versioned allowlist rather than admitting provider payloads.
- Exact Python pins for `pgvector==0.5.0` and `rfc8785==0.1.4`; frozen lock and CPU dependency audit remain clean.
- A-003 and A-004 evidence/status plus P02 plan, progress, traceability, risk, version, CI and repository documentation.

## Scope not delivered

- No P03 API/worker runtime, configuration surface, HTTP endpoint, auth/session flow, or production job worker.
- No matcher, candidate-generation product behavior, benchmark activation, probabilistic auto-match, global Recording merge/split, or P00-D004 resolution.
- No sync/OpenAPI contract, Room/Android persistence, Vault byte handling, playback, recommendations, GPU worker, or Wave behavior.
- No production deployment roles, secrets, public port, backup/restore procedure, hosted CI workflow, push, or PR.
- Android lint/unit/APK tasks were not rerun in P02: no Android/Gradle file changed and the exact P01 Microsoft JDK 17 was not provisioned in the current environment. Historical P01 Android evidence remains in `HANDOFF_P01.md`.

## Changed files and modules

- Root/check surface: `.gitattributes`, `AGENTS.md`, `README.md`, `scripts/check.ps1`, `scripts/check.sh`.
- Dependency/config surface: `server/pyproject.toml`, `server/uv.lock`, `server/alembic.ini`.
- Database lifecycle: `server/migrations/README.md`, `env.py`, `migration_support.py`, `reference_v1.sql`, `script.py.mako`, and revisions `0001_extensions_schemas.py` through `0010_indexes_privileges.py`.
- Persistence adapter: `server/src/autplay/adapters/postgresql/` base, metadata, identity commands, and schema modules `account`, `catalog`, `audit`, `identity`, `sync`, `jobs`, `library`, `playlist`, `vault`, `importing`, and `ml`.
- Application boundary: `server/src/autplay/application/identity_evidence.py`.
- Test lifecycle: `deploy/compose/compose.test.yaml`, unit identity-evidence tests, and `server/tests/postgresql/` fixtures plus migration, inventory, metadata, core, identity, policy, projection, concurrency, job and restricted-role suites.
- Phase documentation: root/server/Compose READMEs, `CI_PLAN.md`, `VERSIONS.md`, `PLAN.md`, `PROGRESS.md`, `TRACEABILITY.md`, `RISK_REGISTER.md`, `MVP_ACCEPTANCE_MATRIX.md`, and this handoff.

Normative design files, `DECISION_REGISTER.md`, frozen F-016, P00-D004, the approved P00-D003 packet/manifest, and `HANDOFF_P01.md` were not changed.

## Decisions and boundaries

1. ADR-015 and approved P00-D003 revision `c108c109d8eb1ab71631ea79e831a20e6cc6811bff5264cb6d1bb38f7433ac71` control the physical identity history implementation.
2. Frozen F-016 is unchanged. The database starts with zero activation rows; an applied SYSTEM auto-match requires the exact current benchmark-backed policy. Pre-P00-D004 deterministic-byte T4 evaluation remains `SHADOW + REVIEW_REQUIRED`.
3. The reference SQL remains the physical contract. Migrations contain a byte-for-byte frozen asset and are grouped into reviewable dependency layers; they do not import mutable current models.
4. `public.alembic_version` is Alembic-owned and excluded from the 57 module-table count. No production role topology was invented; migrations reproduce portable PUBLIC revokes, while a disposable restricted role proves constraints cannot be bypassed.
5. SQLAlchemy rows use scalar typed columns without `relationship()` cascades or domain methods. Cross-schema/circular FKs remain explicit and three late cyclic constraints use `use_alter=True`.
6. The base Compose service publishes no port. Only the test override creates a random `127.0.0.1` port, and canonical scripts own the full project lifecycle. Their bounded readiness gate waits for the pinned image's completed init process before issuing SQL, avoiding the temporary init-server shutdown window.

No new ADR was required. A future non-empty gate-metadata schema, production privilege topology, or P00-D004 semantic resolution requires its owning approved decision.

## Migrations and contracts

| Revision | Contents |
| --- | --- |
| `0001` | `pg_trgm`/`vector`, 12 schemas, `app_private` PUBLIC revoke |
| `0002` | account and catalog tables |
| `0003` | audit and identity base/registry tables; zero activation seed rows |
| `0004` | sync and jobs tables |
| `0005` | library and playlist tables |
| `0006` | Vault tables |
| `0007` | importing plus immutable identity decision/evidence history and late cyclic FKs |
| `0008` | ML history and listening events |
| `0009` | 13 functions and 40 non-internal triggers |
| `0010` | 53 explicit indexes and portable privilege revokes |

- Reference SQL SHA-256: `596ec53be759a9c6851b3280d2a8335c8bbd5d1424bf152b43f5d13407fe02f9`.
- Typed mapping fingerprint: `b73c63293c623e17b65fafe823b414da06b0e7034fdb581083f70f5604d350e1`.
- One Alembic head: `0010_indexes_privileges`.
- Metadata inventory: 57 mapped rows, 616 columns, 53 explicit indexes, 267 CHECK, 116 FK, 57 PK, and 34 UNIQUE constraints.

## Commands executed and results

From the repository root:

```text
uv lock --project server --check
uv run --project server --frozen ruff check --config server/pyproject.toml server
uv run --project server --frozen ruff format --check --config server/pyproject.toml server
uv run --project server --frozen mypy --config-file server/pyproject.toml server/src server/tests
```

Result: PASS; 35 locked packages, 60 formatted source files, strict mypy green for 46 source files, and no prohibited GPU/ML dependency.

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly
```

Result: PASS; PostgreSQL `18.4 (Debian 18.4-1.pgdg12+1)`, pgvector `0.8.6`, `225 passed in 109.74s`, zero scoped resources after cleanup.

```text
"C:\Program Files\Git\bin\bash.exe" scripts/check.sh --server-only
```

Result: PASS; the same pinned runtime, `225 passed in 110.80s`, zero scoped resources after cleanup.

A clean Git staged-index export was then built outside the working tree and checked with both canonical shell paths. The archived normative and migration SQL both retained SHA-256 `596ec53be759a9c6851b3280d2a8335c8bbd5d1424bf152b43f5d13407fe02f9`; PowerShell reported `225 passed in 112.13s` and Git Bash reported `225 passed in 106.97s`, each with zero scoped resources after cleanup. The only subsequent repository edit was this evidence-only handoff update.

Additional targeted real-database evidence after boundary changes:

- identity persistence commands: `10 passed in 12.11s`;
- history/projection plus full-field round-trip: `63 passed in 40.11s`;
- policy/F-016 matrix: `29 passed in 19.94s`;
- every targeted project was removed with zero remaining containers, volumes, or networks.

## Acceptance evidence

| Criterion | Status | Evidence |
| --- | --- | --- |
| A-003 clean upgrade → base → upgrade | PASS | Full first-head catalog snapshot, exact empty base, second snapshot byte-structurally equal to first; all ten adjacent downgrade/upgrade pairs |
| A-004 reference DDL invariants | PASS | Migrated/reference catalog equality; exact 57/53/13/40; exact constraints; zero metadata/autogenerate drift |
| Named schema and no legacy objects | PASS | One head; all 12 schemas/extensions where expected; `importing.match_candidate` absent; activation rows zero; no HNSW/IVFFlat |
| Typed mappings and domain independence | PASS | 57 mappers/616 columns; mapper fingerprint; no relationships, Python defaults, or domain/application dependency on SQLAlchemy |
| Core persistence invariants | PASS | SHA/Vault, tombstones/uniqueness, playlist positions, ownership, sync/idempotency, canonical variant, cycles, embeddings and external-reference target tests |
| Identity lossless/sealed history | PASS | All five states/six query types/all row fields; multiple origins/unknown features; 0/1/2/100 rows; 101/gap/duplicate/hash/count/size/late mutation negatives |
| Review/lineage/projection | PASS | Append-only six-table matrix; immediate-predecessor evidence; four review actions; self/cycle/cross/time/branch; wrong type/owner/shadow/target for both owner projections |
| Policy/F-016/concurrency | PASS | Active exact-scope benchmark gate; actor/hash/version/tier/score/margin/conflict negatives; lifecycle/rollback; successor, activation and both deactivate-vs-auto races |
| Canonical/privacy/idempotency | PASS | RFC 8785 vectors and N-1/N/N+1 sizes; nested sensitive fields; provider-independent explanation; command hash/size recomputation; same/different-hash replay and raw named UQ |
| CPU-only/disposable execution | PASS | Structural 35-package graph audit; no CUDA/GPU/ML package; random loopback port; PowerShell and Bash cleanup verification |

## Known risks and debt

- P00-D004 remains unresolved. P02 deliberately stores deterministic T4 evidence only as a shadow review-required outcome and does not reinterpret F-016.
- Production role grants and runtime credentials are not part of the portable reference contract. P03/deployment work must define least-privilege roles before production use; P02 proves PUBLIC revokes and one restricted test role only.
- `gate_metadata` schema v1 is intentionally empty because no portable key allowlist is normative. A useful non-empty schema requires explicit versioned privacy review.
- Hosted Linux/macOS/Windows CI is still planned rather than executed; current evidence is Windows PowerShell plus Git Bash against Linux containers.
- PostgreSQL clean-baseline migration risk is mitigated, but production backup/restore and old-data upgrade fixtures remain P14 responsibilities. Room migration risk remains P05.
- No Android task was rerun in P02; rely on the unchanged P01 Android evidence until P05 provisions its full compatibility/device matrix.

## Preconditions for next phase

P03 may begin only after this P02 phase commit is `HEAD`, the worktree is clean, this handoff remains green, and the user explicitly requests P03. P03 must not add matcher, sync, Vault, Android or later-phase product work.

Exact next phase prompt:

```text
Выполни только AutPlay phase P03 по `docs/build-pack/prompts/P03_server_runtime.md`. Следуй `docs/build-pack/PROMPT_PROTOCOL.md`, проверь `HANDOFF_P02.md`, не начинай P04. Подтверди acceptance P03 проверками, создай `docs/implementation/HANDOFF_P03.md` и остановись.
```

## Git state

- Branch: `master`.
- Parent P01/P00-D003 application commit: `dfbb58e2756ee2c6f7b694fe7fee1e14ca5e60cf`.
- Commit: P02 phase commit at `HEAD`; retrieve with `git rev-parse HEAD`. Its self-hash cannot be embedded in its own contents and is reported in the completion response.
- Worktree: expected clean after the single local P02 commit; verified and reported immediately after commit.
- Push/PR: not performed.

## Blocking user decisions

None for closing P02. P00-D004 remains a mandatory approved decision before any P06/P10 deterministic reuse or matcher behavior that would change the current shadow-only semantics.
