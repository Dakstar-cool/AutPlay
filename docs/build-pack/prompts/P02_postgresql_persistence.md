# P02 - PostgreSQL Persistence Foundation

Выполни только phase P02. Следуй common protocol и прочитай `HANDOFF_P01.md`.

## Цель

Реализовать executable PostgreSQL schema v1 через Alembic и SQLAlchemy 2 typed persistence mappings, сохранив reference DDL invariants.

## Inputs

- `docs/design/AutPlay_PostgreSQL_Schema_v1.sql`
- `docs/design/AutPlay_PostgreSQL_Schema_v1.md`
- `docs/design/AutPlay ER Model v1.md`
- `docs/design/AutPlay_Track_Identity_v1.md`
- `docs/design/AutPlay System Architecture v1.md`
- `docs/adr/ADR-015-immutable-identity-decision-history.md`

## Scope

1. Configure Alembic with deterministic naming and explicit initial revision.
2. Implement schema equivalent to reference DDL: schemas, extensions, exactly 57 tables, 53 explicit indexes, 13 helper/constraint functions, 40 non-internal triggers and grants/roles appropriate for tests. PK/UNIQUE backing indexes do not count as explicit indexes.
3. Add typed SQLAlchemy persistence mappings without domain behavior.
4. Keep application/domain imports independent from SQLAlchemy.
5. Add disposable PostgreSQL integration fixture.
6. Add exact-name object inventory comparison between migrated DB and reference expectations, including identity registries/history and absence of legacy `importing.match_candidate`.
7. Implement every invariant test listed in the PostgreSQL schema decision document, including immutable identity history, sealed candidate evidence, policy lifecycle/concurrency and owner projection validation.
8. Document migration creation/review/rollback workflow.

## Mandatory tests

- clean upgrade to head;
- downgrade to base on empty development DB;
- upgrade again;
- SHA-256 length and Vault uniqueness;
- active UserTrackRef/Library uniqueness with tombstones;
- duplicate playlist Track allowed, duplicate active position rejected;
- cross-user/device ownership rejected;
- event sequence/idempotency uniqueness;
- canonical AudioVariant belongs to same Recording;
- Recording redirect and job dependency cycles rejected;
- embedding dimension/model/Recording integrity;
- named constraints and exact 57/53/13/40 object counts;
- all five identity resolver states and every normative decision/evidence/version/actor/lineage field round-trip without loss;
- import, local-audio and external-reference query fixtures; multiple origins/ranks and unknown feature round-trip;
- sealed candidate sets with 0/1/2/100 rows; reject candidate 101, rank gaps/duplicates, count/hash mismatch and late/post-commit insertion;
- reject invalid state/actor/user, score/margin, typed query, release-version and rank-1/rank-2 consistency combinations;
- reject `UPDATE`/`DELETE` for all six identity history/registry tables; re-score/review appends rows and preserves old explanation;
- reject self/cycle/cross-query/earlier-time/branch decision supersession, including concurrent successor race;
- validate ACCEPT/REJECT immediate-predecessor evidence, KEEP_UNRESOLVED and atomic CREATE_RECORDING projection without global merge;
- reject wrong-type/owner, shadow and target-divergent import/UserTrackRef current-decision pointers; import payload cleanup preserves identity history;
- require active benchmark-backed exact-scope policy for applied SYSTEM auto-match; reject insufficient tier/score/margin, hard conflict, inactive/mismatched policy and nullable calibrator;
- require active OWNER/ADMIN, benchmark hash and exact matcher/calibrator/mode/tier for activation; reject sequence gap/branch and rollback to never-active set;
- serialize deactivate-vs-auto and concurrent activation races; activation/rollback remains append-only;
- reject pre-benchmark applied T4 and keep initial activation history empty;
- validate RFC 8785 hashes, JSON shape/cardinality/bounds and sensitive-field fixtures; provider-independent explanation survives provider unavailability;
- application-command same-hash identity replay returns the stored row after the named unique collision; different-hash replay is rejected, while raw direct duplicate INSERT proves the constraint with a unique violation.

## Constraints

- PostgreSQL text+named CHECK, not mutable database enums.
- No HNSW/IVFFlat in initial migration.
- No Vault bytes in DB.
- Do not weaken triggers/FK to simplify ORM.
- Keep the six identity registries/history tables append-only and candidate snapshots sealed; use deferred constraints for aggregate/projection validation.
- Initial schema inserts zero identity policy activation events. Do not implement matcher behavior, benchmark activation or product auto-match in P02.
- Preserve frozen F-016 unchanged and do not resolve or reinterpret P00-D004.
- No HTTP API, sync engine or product feature logic.
- Autogenerate output must be reviewed; reference DDL remains physical contract.

## Acceptance

All migration/invariant tests pass against real pinned PostgreSQL 18 + pgvector. Alembic head, SQLAlchemy metadata and reference inventory have no unexplained drift.

Create `HANDOFF_P02.md`, update A-003/A-004 evidence and stop.
