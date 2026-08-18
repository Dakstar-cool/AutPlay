# AutPlay P03 Handoff

## Outcome

P03 is `PASS`. The phase delivers a production-shaped, CPU-only FastAPI/API and PostgreSQL-worker foundation with explicit validated configuration, bounded HTTP handling, operational health/readiness, redacted structured logging, low-cardinality API metrics, local owner/device sessions, and fenced PostgreSQL job execution. It adds no PostgreSQL migration, no non-auth product endpoint, and no P04 contract or sync implementation.

Both canonical server-only gates passed from the committed frozen lock against disposable PostgreSQL 18.4/pgvector 0.8.6: 298 tests passed in PowerShell and Git Bash. The Linux runtime image built from a digest-pinned base then passed migration, API and worker readiness, loopback binding, non-root/read-only execution, CPU-only import, graceful worker shutdown, and exact resource cleanup evidence. MVP acceptance A-002 is `PASS`.

## Post-handoff prerequisite update

On 2026-08-15 the user explicitly approved P00-D006 Variant A and, after independent review exposed the merge collision case, P00-D006-R1. The complete accepted mapping is recorded in `P00-D006_AGGREGATE_ID_MAPPING.md`. The historical P03 implementation and verification evidence below is unchanged. P04 is now eligible but remains `NOT_STARTED`; no P04 contract or sync implementation was added by this update.

Subsequent state: P04 completed on 2026-08-16; its current evidence is in `HANDOFF_P04.md`. The sentence above records the post-P03/P00-D006 checkpoint, not current phase status.

## Delivered scope

- Separate typed API and CPU-worker settings with explicit precedence: safe defaults, explicitly selected base TOML, named profile TOML, explicit secret files, environment, then caller overrides. Implicit dotenv and ambient secret-directory loading are disabled.
- Bounded and sanitized startup validation, separate API/worker secret surfaces, strict readiness contracts, and a fail-closed password-login switch.
- FastAPI application factory with stable machine-readable errors, canonical/generated request IDs, a 1 MiB and 1,024-frame outer ASGI request-body bound, duplicate-authorization rejection, liveness independent of PostgreSQL, and readiness dependent on PostgreSQL plus the exact Alembic head.
- Structured JSON logging with allowlisted fields and recursive redaction. Prometheus HTTP counters use bounded method/route/status labels, HTTP histograms use method/route labels, and the readiness gauge uses a component label; arbitrary HTTP methods normalize to `OTHER`.
- Local first-owner bootstrap through `autplay-admin bootstrap-owner`, serialized by a PostgreSQL advisory lock and committing owner, device, initial session, and sanitized audit event atomically.
- Explicit Argon2id hash/verify primitive with bounded input and reviewed parameters. Password login remains disabled because schema v1 has no approved credential-persistence contract.
- Session-bound access authentication and real device-session API operations: refresh rotation, current/all-session logout, and owner-scoped device revocation. Access validation reloads mutable account, device, and session state.
- Opaque 32-byte refresh credentials with SHA-256-only persistence, new session generation per rotation, original absolute expiry, known-generation replay detection, and device-wide revocation on replay.
- Domain/application/port/adapter job layers with bounded JSON documents, atomic `FOR UPDATE SKIP LOCKED` claim plus attempt creation, DB-authoritative lease time, `attempt_no` fencing, heartbeat/checkpoint, cancellation-safe checkpoints, expired-lease recovery, deterministic bounded retry/backoff, owner-scoped cancellation, and safe terminal transitions.
- CPU worker with exact-head PostgreSQL startup/readiness preflight, one-job-at-a-time execution, registered-type filtering, cooperative signal shutdown, sanitized failure reporting, and no feature handlers by default.
- Multi-stage CPU image built from a digest-pinned base and a disposable Compose runtime profile for migration, API, and worker. Runtime processes use UID 999, a read-only root filesystem, dropped capabilities, bounded resources, and no GPU dependency; API publication is loopback-only.
- A strict root `.dockerignore` allowlist that limits the Docker build context to the server package, lock, source, and migration inputs.
- Updated ADR-016, acceptance matrix, plan, progress, traceability, risk, version, CI, README, and canonical check evidence.

## Explicitly not delivered

- No P04 Sync Protocol v1, OpenAPI feature operations, event schemas, golden vectors, or sync engine.
- No public registration or bootstrap endpoint, password-login endpoint, password credential persistence, account recovery, email, OAuth, or external identity provider.
- No non-auth product resource endpoint under `/api/v1`; no placeholder success response and no default feature-job handler.
- No matcher, import provider, Vault ingest/streaming, playback/download, recommendation, Wave, Android persistence/network/DI, or GPU-worker behavior.
- No Redis, RabbitMQ, NATS, Kafka, external vector database, or mandatory GPU runtime.
- No production domain/TLS topology, production database-role grants, external secret manager, backup/restore runbook, public bind, hosted CI workflow, deployment, push, or PR.
- No Alembic revision, reference-DDL change, typed schema-mapping change, or normative design-document rewrite.

## Changed modules/files

- Dependencies and console entrypoints: `server/pyproject.toml`, `server/uv.lock`.
- Runtime configuration and HTTP/observability: `server/src/autplay/runtime/`.
- PostgreSQL runtime adapters: `server/src/autplay/adapters/postgresql/auth_runtime.py`, `jobs_runtime.py`, `jobs_uow.py`, `readiness.py`, and `runtime_database.py`.
- Authentication/security: `server/src/autplay/domain/auth.py`, `server/src/autplay/ports/auth.py`, `server/src/autplay/application/auth.py`, `server/src/autplay/application/authorization.py`, and `server/src/autplay/adapters/security/`.
- Shared boundaries: `server/src/autplay/ports/clock.py`, `ids.py`, and `transactions.py`; `server/src/autplay/adapters/system.py`.
- Jobs: `server/src/autplay/domain/jobs.py`, `server/src/autplay/ports/jobs.py`, and `server/src/autplay/application/job_worker.py`.
- Entrypoints/composition: `server/src/autplay/entrypoints/api.py`, `auth_http.py`, `composition.py`, `admin.py`, and `worker_cpu.py`.
- Runtime packaging: root `.dockerignore`, `server/Dockerfile`, `deploy/compose/compose.runtime.yaml`, and the test-overlay/check-script updates.
- Tests: `server/tests/runtime/`, `server/tests/test_auth_security.py`, `server/tests/test_jobs.py`, `server/tests/postgresql/test_auth_api.py`, `test_auth_runtime.py`, and `test_jobs_runtime.py`.
- Documentation: root `AGENTS.md`; root/server/Compose READMEs; `docs/adr/ADR-016-p03-runtime-auth-boundary.md`; implementation plan/progress/traceability/risk/version/CI records; canonical MVP acceptance matrix; and this handoff.

Normative design files, build-pack prompts/protocol/decision register, P00-D004/P00-D006, frozen decisions, P02 migrations/reference DDL, schema mappings, and prior handoffs were not changed.

## Decisions and ADRs

1. ADR-016 accepts the narrow personal-server runtime/authentication boundary. Password login remains disabled until an approved credential schema and migration exist; the Argon2id primitive is verified preparation, not a working-login claim.
2. First-owner setup is a local administrative operation. A transaction-scoped PostgreSQL advisory lock serializes the empty-account check and one transaction creates owner, device, first session, and audit event.
3. Access JWTs use fixed HS256 inside the current personal-server trust boundary and are always revalidated against account/device/session state. Refresh values are opaque 32-byte credentials; only SHA-256 digests are persisted.
4. Refresh rotation creates a new session generation with the original absolute expiry. Reuse of a known revoked generation commits device-wide active-session revocation before returning the replay error.
5. P03 does not invent credential columns, public registration policy, production role topology, asymmetric key distribution, or external identity providers.
6. Job execution remains at-least-once after crash. `attempt_no` is the active-lease epoch fence, PostgreSQL owns lease time, and future handlers must make external side effects idempotent or checkpointed.
7. The CPU worker claims and recovers only registered `(job_type, schema_version)` keys and registers none by default, so it cannot consume future feature/GPU work or report placeholder success.
8. P03 Prometheus instrumentation covers API and readiness behavior only. Job queue-depth/age/attempt/duration exporters wait for real handler/operations ownership rather than exposing unused metric families.

## Migrations and contracts

- New Alembic revisions: none.
- The one Alembic head remains `0010_indexes_privileges`.
- Reference DDL, 57-table typed metadata, and P02 row mappings remain the physical persistence contract.
- Authentication reuses `account.user_account`, `account.device`, `account.user_session`, and audit tables. No bearer token or password is persisted in plaintext.
- Jobs reuse `jobs.job` and `jobs.job_attempt`; repository/application predicates enforce lease, fence, retry, cancellation, and terminal behavior against the unchanged schema.
- Operational HTTP surface: `GET /health/live`, `GET /health/ready`, and `GET /metrics`.
- Device-session HTTP surface: `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`, `POST /api/v1/auth/logout-all`, and `POST /api/v1/devices/{device_id}/revoke`.
- OpenAPI/docs exposure is disabled. There is no password-login, registration/bootstrap, sync, or non-auth product resource route.
- P04-owned files under `contracts/openapi` and `contracts/events` remain untouched placeholders.

## Commands executed

| Command | Result | Exact evidence |
| --- | --- | --- |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly` | PASS | Frozen lock with 47 package stanzas; Ruff PASS; 101 files formatted; strict mypy PASS for 87 source files; PostgreSQL `18.4`/pgvector `0.8.6`; migration/autogenerate and CPU dependency gates PASS; `298 passed in 120.24s`; scoped container/network/volume cleanup PASS |
| `& 'C:\Program Files\Git\bin\bash.exe' scripts/check.sh --server-only` | PASS | Same frozen/import/lint/format/type/CPU/migration/database suite; `298 passed in 126.11s`; scoped cleanup PASS |
| `docker build --check --progress plain -f server/Dockerfile .` | PASS | Dockerfile build checks completed with no warnings |
| Runtime Compose `up --build --detach --wait` using base plus runtime overlay, project `autplay-p03-runtime-final-a74c1f`, loopback port `4336`, and a synthetic secret file outside the repository | PASS | Build context `96.21 kB`; migration exit 0; API and worker healthy; `/health/live` returned `live` for component `api`; `/health/ready` returned `ready` for component `api`; both runtime processes UID 999/read-only; worker environment contained no auth signing secret; in-container dependency/import audit returned `CPU_ONLY` |
| Runtime worker `SIGTERM`, restart/readiness, and Compose `down --volumes --remove-orphans` | PASS | Worker exited 0, restarted healthy, API remained bound to `127.0.0.1:4336`; final image `autplay-server:p03-local`, `linux/amd64`, ID `sha256:5b1476db5d1648688b66d222f7a736d3300e1e377a7f453681cf1472de465f6f`; scoped container/network/volume residue `0/0/0`; temporary secret removed |

The canonical 298-test suite includes 30 runtime tests, 22 auth/security/API tests, 21 job tests (eight unit and thirteen real-PostgreSQL), and the retained 225-test P02 baseline. No skipped or disabled critical test is used as evidence.

## Acceptance criteria

| Criterion | Status | Evidence |
| --- | --- | --- |
| Typed config precedence, required-secret validation, bounds, and redaction | PASS | Runtime settings suite plus both canonical gates |
| Liveness without DB; exact-head readiness and correct DB/schema failure classification | PASS | Runtime/readiness tests, real migrated runtime profile, and healthchecks |
| Stable errors/request IDs, bounded bodies/frames, redacted logs, and low-cardinality API metrics | PASS | Runtime ASGI/API/logging/metrics tests including adversarial frame/method/header cases |
| Safe local owner bootstrap and Argon2id boundary | PASS | Unit and concurrent real-PostgreSQL auth tests; no public bootstrap/login route |
| Refresh rotation, absolute expiry, replay/revoke, active reload, and cross-user failure | PASS | Unit, HTTP, and real-PostgreSQL auth suites including logout/rotation race |
| Atomic disjoint job claim and fenced lease/heartbeat/checkpoint/recovery | PASS | Eight unit plus thirteen real-PostgreSQL job tests |
| Bounded retry/terminal transitions and owner cancellation | PASS | Same job suites, including cancellation/checkpoint and stale-epoch negatives |
| API and CPU worker clean locked start without CUDA | PASS | Process/import tests, dependency audit, non-root/read-only Compose smoke, and graceful worker stop |
| A-002 CPU-only server starts without CUDA | PASS | Canonical MVP matrix points to this handoff and runtime evidence |
| No non-auth feature endpoint/handler and no P04 implementation | PASS | Forbidden-route negatives, empty-registry test, and final route/changed-path audit |
| Migration compatibility | PASS | No migration/mapping/reference-DDL diff; head remains `0010_indexes_privileges`; upgrade/autogenerate gates pass |

## Known risks and debt

- Password login and credential recovery are intentionally unavailable. Enabling either requires an approved credential-persistence/security contract and migration. Loss of the one-time bootstrap credentials currently has no recovery path; this is an explicit operator lockout risk, not hidden fallback behavior.
- HS256 uses one shared personal-server signing secret. A wider or independently operated verifier boundary triggers asymmetric-key and rotation review.
- Revoked refresh generations are retained for replay detection; a later bounded retention policy must preserve the required replay window and privacy/deletion semantics.
- Lease fencing prevents stale workers from mutating job state but cannot make external side effects exactly once. Every future handler must define idempotency/checkpoint behavior and add crash/failure-injection evidence.
- The CPU worker deliberately has no feature handlers. Job queue-depth/age and handler outcome/duration metrics are deferred until real handlers and an operational exporter exist.
- Production database roles/grants, TLS/domain topology, secret delivery, backups/restores, resource sizing, and hosted cross-platform CI remain later operational work.
- P00-D006 was unresolved at P03 exit and was fully accepted post-handoff through Variant A plus reviewed P00-D006-R1; it no longer blocks P04. P00-D004 remains unresolved for P06/P10 and was not touched.

## Preconditions for next phase

P03 is green, A-002 is `PASS`, P00-D006 Variant A and P00-D006-R1 are accepted, and P04 has not started. P04 must encode `P00-D006_AGGREGATE_ID_MAPPING.md` rather than infer semantics from the current server inbox column.

The complete next prompt was read from `docs/build-pack/prompts/P04_sync_contract.md`. Exact next phase prompt:

```text
Выполни только AutPlay phase P04 по `docs/build-pack/prompts/P04_sync_contract.md`. Следуй `docs/build-pack/PROMPT_PROTOCOL.md`, проверь `HANDOFF_P03.md`, сначала разреши P00-D006 aggregate-ID mapping и не начинай P05. Подтверди acceptance P04 language-neutral schema/OpenAPI/golden-vector checks, создай `docs/implementation/HANDOFF_P04.md` и остановись.
```

P04 owns Sync Protocol v1, versioned JSON Schemas, OpenAPI operations, valid/invalid golden vectors, language-neutral contract tests, and compatibility policy only. It must not implement either sync engine.

## Git state

- Branch: `master`.
- Phase commit: this P03 commit; the final response records its hash after creation.
- Worktree: clean after the local phase commit.
- Push/PR/deployment: not performed.

## Blocking user decisions

None remain for P03 or P04 eligibility. P04 still requires an explicit execution request.
