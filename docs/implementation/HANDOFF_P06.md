# AutPlay P06 Handoff

## Outcome

P06 is `PASS`. It delivers the CPU-only, crash-safe filesystem Vault path from an authorized,
bounded resumable upload through full decode validation, versioned Chromaprint evidence,
immutable SHA-256 CAS publication, deterministic exact-byte reuse, recovery reconciliation and
owner-authorized direct HTTP Range streaming. MVP acceptance A-011, A-012 and A-013 are `PASS`.

P00-D004 Variant A was explicitly accepted by the user and is implemented through ADR-019. The
technical reuse path is separate from probabilistic matching, requires one committed object, one
valid non-deleted variant, one active non-redirected target Recording and one available verified
local replica, and fails closed on every ambiguity or integrity conflict. It never creates, merges,
reassigns or resolves a Recording or owner projection.

## Delivered scope

- Alembic `0011_vault_runtime` with durable owner/device upload sessions and per-chunk receipts,
  typed SQLAlchemy rows and exact reference-DDL parity. The executable inventory is 59 tables,
  57 explicit indexes, 13 functions and 41 triggers; the two reference SQL files are byte-identical.
- Owner-authorized create/replay, HEAD/status, append, seal/enqueue, cancel and TTL expiry with
  stable errors, request/idempotency hashing, strict offset/hash/size/chunk-count limits and a
  low-disk reserve checked both before enqueue and before worker claim.
- Generated storage keys only, private same-filesystem staging, symlink/special-file/path escape
  rejection, durable append/truncate, no-overwrite hard-link CAS publication, inode and directory
  fsync on both new and recovery paths, immutable POSIX final mode and recoverable quarantine.
- Separate full-decode FFmpeg validation, bounded ffprobe metadata extraction and versioned
  Chromaprint/fpcalc evidence. Subprocesses use exact argument vectors, a minimal environment,
  no shell, bounded combined output, timeouts and process-group termination.
- Transaction-separated ingest checkpoints around DB prepare, file publish, DB finalize and
  staging cleanup. Same-SHA publication uses a PostgreSQL advisory fence and converges to one
  object; classified retry/terminal failures cannot strand a non-terminal upload.
- Bounded APPLY/DRY_RUN reconciliation for orphan/corrupt/missing final objects, terminal/expired/
  missing staging, DB/file crash windows and stranded jobs. Anomaly-first scheduling plus a
  deterministic `verified_at`/ID round-robin guarantees progress beyond one limit page.
- Separate direct-stream process and owner authorization through active LibraryEntry -> resolved
  UserTrackRef -> active Recording -> valid AudioVariant -> committed object -> available replica.
  Hash knowledge never authorizes. GET/HEAD, 200/206/416, single ranges, suffix/open ranges,
  If-Range/ETag, content length/type, disconnect cleanup and cross-owner masking are implemented.
- O(1) per-request integrity proof checks the opened descriptor's exact size, commit-time mtime
  watermark and immutable POSIX mode. Full SHA-256 verification occurs at commit and during the
  rotating reconciliation scan, avoiding a full multi-gigabyte synchronous read before each Range.
- Separate migration, API, CPU-worker and stream Compose processes. API/worker share the writable
  Vault atomicity domain; stream receives the volume read-only. All processes are non-root with a
  read-only root filesystem, dropped capabilities, bounded resources and no GPU dependency.
- Redacted aggregate audit events for committed, reused, quarantined, expired and reconciliation
  transitions. Payload bytes, hashes, local paths, source URLs, tokens and credentials are absent.

## Explicitly not delivered

- No external downloader, provider adapter, DRM handling, secret scraping or legal-policy choice.
- No transcoding, waveform, loudness, Media3 playback/download implementation or Android Vault UI.
- No probabilistic matcher, automatic Recording merge, owner/import projection mutation or P10
  identity-ledger representation.
- No sync engine, recommendation endpoint, GPU worker, separate object store, Redis or message bus.
- No destructive garbage collection, last-copy deletion, backup/restore implementation or real-data
  migration.
- No production domain/TLS/role/secret-manager/NAS/backup topology, deployment, push or PR.

## Main changed paths

- Schema: `server/migrations/versions/0011_vault_runtime.py`, both reference SQL sources,
  PostgreSQL Vault models/metadata/readiness and migration lifecycle/inventory tests.
- Core: `server/src/autplay/domain/vault.py`, `ports/vault.py`,
  `adapters/filesystem/vault.py` and `adapters/media/tools.py`.
- Application/persistence: `application/vault_uploads.py`, `vault_ingest.py`,
  `vault_reconciliation.py`, `vault_streaming.py`, `adapters/postgresql/vault_runtime.py` and
  `vault_uow.py`.
- HTTP/runtime: `entrypoints/vault_http.py`, `stream_http.py`, `stream.py`, `composition.py`,
  `worker_cpu.py`, `admin.py`, settings, OpenAPI, Dockerfile and Compose runtime overlay.
- Evidence: P06 filesystem/media/service/HTTP/PostgreSQL tests,
  `entrypoints/media_smoke.py` and `scripts/test-p06-media-runtime.ps1`.
- Decisions/registers: ADR-019, Track Identity/ER clarification, Decision Register, README,
  PLAN/PROGRESS/TRACEABILITY/RISK/VERSIONS/CI and this handoff.

## Decisions, migrations and contracts

- P00-D004 Variant A / ADR-019 is accepted and implemented; F-016 remains unchanged.
- One new linear migration exists: `0010_indexes_privileges -> 0011_vault_runtime`. Clean base/head,
  adjacent downgrade/upgrade, exact reference equality and zero Alembic drift are executable gates.
- P06 adds upload/status/complete/cancel and direct-stream OpenAPI paths. The P04 sync path set and
  all 51 language-neutral contract tests remain unchanged and green.
- Production Linux x86_64 media pins are FFmpeg/ffprobe 8.1.2 and Chromaprint/fpcalc 1.6.1 with
  exact source image/archive and binary SHA-256 values recorded in `VERSIONS.md`.

## Exact verification evidence

- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly`:
  PASS on the final tree: 80 harness tests, 51 contract tests and 342 server/real-PostgreSQL tests;
  one Windows-only symlink test skips because the host lacks symlink privilege. Ruff, format,
  strict mypy, lock checks, CPU dependency audit, PostgreSQL 18.4/pgvector 0.8.6 and exact scoped
  container/network/volume cleanup all pass.
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-p06-media-runtime.ps1`:
  PASS in a built, read-only, network-disabled Linux container. Exact tools report 8.1.2/1.6.1;
  a generated 12-second FLAC full-decodes and yields 52 fingerprint bytes; truncated and hostile
  metadata-shaped non-audio fixtures are both rejected/quarantined; sealed CAS Range returns
  4096 bytes.
- P06 real-PostgreSQL evidence includes same-SHA concurrent publication, every prepare/publish/
  finalize cleanup crash window, strict different-Recording conflict, TTL/missing staging repair,
  orphan/corrupt final quarantine and `limit=1` second-page reconciliation progress.
- Disposable runtime-profile smoke: migration/API/worker/stream healthy, isolated stream mount
  read-only, `autplay-admin vault-reconcile --limit 10` returned zero pending anomalies, and every
  scoped container/network/PostgreSQL/Vault volume was removed.
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1`: PASS on the final
  tree with the pinned local JDK/SDK exported. It repeats all 80/51/342 server gates and adds Android
  lint, 14 host unit tests, debug APK and minified release/R8 APK (`BUILD SUCCESSFUL`, 101 tasks).
  The disposable PostgreSQL container/network/volume is removed exactly. The SDK XML-v4 parser
  warning remains non-blocking environment evidence; no lint suppression or version bypass exists.
- Final `git diff --check`: PASS. No generated credential or real user data was used.

## Independent review

The read-only P06 reviewer found lifecycle I/O recovery, TTL, low-disk, real-tool, stream
cancellation/integrity and strict Variant-A evidence gaps; all were fixed with executable tests.
A second cycle found per-request full-file hashing, durable read-only sealing/retry fsync and
reconciliation-page starvation; these were replaced by the O(1) stat proof, idempotent inode plus
directory fsync, anomaly-first round-robin reconciliation and Linux/real-PostgreSQL evidence.
Final read-only review reports no remaining critical or major issue.

## Risks and debt

- R-004 is mitigated for the P06 local filesystem backend. Production NAS qualification,
  backup/restore consistency, destructive GC policy and last-copy protection remain P14-owned.
- POSIX directory fsync is proven in the Linux image. CPython on Windows cannot open a directory
  for fsync, so Windows remains a development path with file fsync plus mandatory reconciliation,
  not the production Vault target.
- Reconciliation actions are bounded and make deterministic progress, but inventory/known-key
  discovery is O(number of objects). Scale/SLO measurement and operational scheduling remain P14.
- Hosted Linux/macOS/Windows CI, physical Samsung A55 evidence and production release/security
  artifacts remain open under R-011/R-012/R-016 and P14.

## Exact next prerequisite

P07 is now eligible but has not started. Its exact prompt is
`docs/build-pack/prompts/P07_library_vertical_slice.md`; first verify this handoff and preserve the
P06 migration/contract/evidence gates. No phase-pipeline edge starts P07 implicitly.

## Git state

The shared worktree remains intentionally dirty with accumulated uncommitted P04/harness, P05 and
P06 changes. Existing unrelated user edits were preserved. No reset, stash, commit, push, PR,
deployment, real-data write or external-system write was performed.
