# P13 Platform-Neutral CI Plan

**Status:** P13 CPU/server/database, Android host and API 26 deterministic Wave evidence verified locally; the independent P12 real RTX runner remains required after reviewed hardware/artifact access exists

**Canonical commands:** root [`README.md`](../../README.md)

No Git remote or CI host is configured. The plan therefore keeps provider-neutral jobs and required evidence without adding provider-specific YAML, mutable marketplace actions, or real credentials.

## Required jobs

| Job | Host / prerequisites | Canonical command | Required evidence |
| --- | --- | --- | --- |
| Server Windows | Windows x64; uv `0.12.3`; Docker Engine/Compose | `scripts/check.ps1 -ServerOnly` through the README invocation | Separate frozen root/server sync and locks; harness plus P04 contract Ruff/format/strict-mypy/unit/schema/OpenAPI/vector gates; server CPU graph and complete pytest suite against PostgreSQL 18.4/pgvector 0.8.6; exact cleanup |
| Server Linux | Linux x86_64; uv `0.12.3`; Docker Engine/Compose | `bash scripts/check.sh --server-only` | Same clean harness/P04 contract plus server/config/API/auth/job and real-database evidence from an empty workspace cache |
| Server macOS | Supported macOS; uv `0.12.3`; Docker Engine/Compose | `bash scripts/check.sh --server-only` | Same harness, server, and real-database evidence; no Android requirement |
| CPU runtime smoke | Linux x86_64; Docker Engine/Compose; ephemeral development signing-secret file | Runtime-profile commands plus `scripts/test-p06-media-runtime.ps1` in the root README | Image builds from digest-pinned sources; migration `0011` completes; API/worker/isolated stream start without CUDA; real FFmpeg/ffprobe/fpcalc valid/hostile fixtures pass; stream mount is read-only; scoped cleanup |
| Android host | Linux x86_64; Microsoft JDK `17.0.20+8-LTS`; Android platform `36.1`, Build Tools `36.1.0`; Docker | `bash scripts/check.sh` | Wrapper checksum/resolution, Room/KSP schema generation, lint, host unit tests, debug APK, minified release/R8 APK, plus the complete current server/database suite and cleanup |
| Android API 26 | Hardware virtualization; API 26 x86_64 image; same JDK/SDK pins | `./gradlew "-Dorg.gradle.java.home=$JAVA_HOME" --no-daemon --console=plain :apps:android:connectedDebugAndroidTest` | 44 connected tests covering P05/P07 persistence and P08 local/Vault ExoPlayer, controller admission, Room v1→v2, logical-session/process recovery, DownloadService restart/reconciliation and storage policy |
| GPU static | Any host; uv `0.12.3`; no NVIDIA hardware required for unit tests | frozen Ruff/format/mypy/pytest under `gpu/` | Separate lock/import boundary, device-selection fixtures, artifact hash, deterministic preprocessing and pooling tests; CPU jobs do not sync/build/pull the GPU project |
| GPU RTX | Linux x86_64; NVIDIA Container Toolkit; capability-compatible selected NVIDIA GPU (current server: RTX 3060 12 GB / GA106 / sm_86); reviewed private artifact/dataset | `scripts/test-p12-gpu.ps1` followed by the approved shadow benchmark command | Selected UUID/PCI/inventory, driver/runtime/artifact hashes, same P11 dataset identity, tracks/hour, p95 job time, peak VRAM, quality delta and CPU health while the worker is stopped/restarted |

WSL is treated as Linux only when its own Linux JDK and Android SDK are provisioned. A Windows SDK path mounted into WSL is not accepted as portable CI evidence.

The GPU RTX job is always opt-in and self-hosted. CPU jobs must never install the GPU lock, pull its
image, request an accelerator or fail because the runner has none. A model stays `BENCHMARK` unless
the RTX job produces an immutable `APPROVED` report.

## P03 evidence requirements

The server jobs must fail if any of these regress:

- settings precedence, bounded validation, missing-secret behavior, or secret-safe representations;
- API liveness independence from PostgreSQL, readiness failure semantics, Alembic-head check, stable error/request IDs, redacted JSON logging, or low-cardinality metrics;
- local-only owner bootstrap, Argon2id parameters, refresh rotation/replay/revoke, active-principal reload, or owner-scoped authorization;
- atomic disjoint job claim, `attempt_no` fencing, expired-lease recovery, heartbeat/checkpoint, bounded deterministic retry, owner cancellation, or safe terminal transitions;
- API/CPU-worker import or startup if a torch, TensorFlow, JAX, CuPy, CUDA, NVIDIA, or other accelerator runtime enters the core graph;
- appearance of a placeholder feature endpoint or default feature job handler.
- invalid harness schemas, unsafe Git/request handling, non-atomic resume state, non-read-only reviewer configuration, unbounded fix/review flow, or routing that treats ambiguity as a cheap mechanical task.

Password login must remain a negative startup/configuration case until an approved credential-persistence contract and migration exist. CI must never work around this gate by storing a credential in an unrelated column.

## P04 evidence requirements

The server jobs must also fail if any of these regress:

- any public sync schema stops validating as Draft 2020-12 or a valid/invalid example changes classification;
- the OpenAPI 3.1 source drops authenticated device bind, push, pull, bootstrap, or status operations, or omits required binding fields;
- RFC 8785/SHA-256 golden hashes, push/event byte limits, sequence ordering, per-event ACK status, opaque cursor, atomic page/cutover, tombstone retention, or pending-intent rules drift;
- required prompt or P00-D006/R1 vector IDs lose a machine-readable expected outcome;
- an additive member stops being parseable, an unsupported server event advances a cursor, or fixtures/contracts introduce sensitive keys;
- P04 adds a runtime sync route, engine, Android Room implementation, or PostgreSQL migration.

## P05 evidence requirements

The Android jobs must also fail if any of these regress:

- the exported Room v1 schema changes from its approved 26-table entity set or exact normalized hash without an accepted migration ADR;
- destructive migration fallback appears, bundled SQLite is removed, or WAL/foreign-key/FTS5 evidence fails on API 26;
- a standalone domain/search mutation stops sharing one transaction with its outbox row, a bound mutation stops sharing one transaction with lineage allocation and Journal append, or explicit materialization loses rollback/idempotent-retry guarantees;
- event/cursor copied binding fields stop matching their lineage through composite foreign keys;
- playlist active order, queue active-slot, journal idempotency/sequence, cursor epoch/opaque cursor, server-event dedupe or aggregate redirect persistence constraints drift;
- FTS query text reaches SQL syntax unquoted/unbounded, unknown persisted strings are rejected, or a missing content URI deletes user intent;
- credentials are persisted outside Keystore-encrypted storage, secrets/private URLs/absolute paths enter Room, or WorkManager input contains payloads instead of stable IDs;
- Android startup requires server configuration, the offline UI command does not survive recreation, or release minification needs broad keep-rule exceptions.

## P06 evidence requirements

The server/runtime jobs must also fail if any of these regress:

- the substrate no longer migrates through current head `0015_wave_runtime`, the exact current physical inventory is not `75/67/19/49`, or upload/chunk/recommendation/enrichment/Wave constraints drift;
- resumable offsets, receipt idempotency, TTL, low-disk reserve, authorization masking or size/chunk/tool limits stop failing closed;
- staging and final objects leave one atomic filesystem domain, publication overwrites a CAS object, or a stream opens bytes whose descriptor size/mtime no longer matches the commit-time DB proof;
- same-SHA concurrency, any prepare/publish/finalize crash window, missing staging, corrupt/orphan final bytes, terminal jobs or expired sessions fail to converge through bounded reconciliation;
- the pinned Linux image stops full-decoding/fingerprinting the valid fixture or accepting a corrupt/truncated/metadata-shaped hostile fixture without quarantine;
- Range 200/206/416, HEAD/ETag/If-Range, disconnect cleanup or owner-to-active-library authorization semantics drift;
- exact-byte reuse creates/merges/reassigns a Recording or owner projection, accepts multiple/non-valid variants, inactive/redirected recordings, unavailable replicas or integrity conflicts.

## P07 evidence requirements

The Android/server jobs must also fail if any of these regress:

- offline library remove/restore, preference, playlist, listening or import stops committing its
  aggregate with exactly one Journal/outbox intent in one Room transaction;
- a missing or permission-revoked `content://` URI deletes user intent, or imported metadata stops
  entering the bounded FTS projection;
- duplicate playlist entries collapse, fractional ordering/rebalance loses strict order, or an
  UPSERT event loses its parent playlist/placement intent;
- a P07 payload is not strict RFC 8785, accepts duplicate/sensitive names, loses safe additive
  attribution, or a recommended listening event lacks complete P04 attribution;
- owner-scoped server commands or read-only projections disclose another user, accept whitespace
  search, lose literal escaping, or entry/playlist/history opaque keyset cursors skip/duplicate rows;
- API 26 top-50 FTS on 10,000 rows or a 1,000-entry playlist load exceeds the declared `150 ms`
  p95 target in the executable 30-sample baseline.

## P08 evidence requirements

The Android jobs must also fail if any of these regress:

- a readable local URI is not preferred, a revoked/unreadable URI deletes library intent, or a fresh authorized Vault source cannot recover after one 401 refresh;
- queue-entry identity collapses duplicate tracks, repeat/shuffle/position or logical played time is derived from seeks, or a logical session can finalize more than once;
- a process restart, queue replacement or profile switch loses a listening event or attributes it to a mutable current owner instead of the owner captured at session start;
- an untrusted external controller can obtain the exported MediaSession or issue playback commands;
- Room stores byte progress/private URLs/tokens, Media3 ceases to own DownloadIndex execution/progress, or stream and download caches collapse into one eviction domain;
- interrupted range transfer, DownloadService restart/reconciliation, duplicate intent protection, stable failure classification, storage reserve or protected pinned/user-download admission stops failing deterministically;
- exported Room schema v2 changes from normalized SHA-256 `c69acd49acceadf9c1c92874ab2eca9069c6958f1bd4c313136ed8a5e80d3acf` without a named migration and updated migration/device evidence.

## P09 evidence requirements

The server/Android jobs must also fail if any of these regress:

- an exact event retry changes identity/hash or creates a second mutation/interaction, a same-device
  sequence race escapes serialization, or a terminal per-event outcome is not durable;
- a pull/bootstrap page advances its cursor after malformed, unknown, reordered or incomplete
  projection, or a reset discards/rewrites pending Journal intent;
- an owner/device/epoch/snapshot cursor token is accepted in another scope, a fixed bootstrap page
  observes intervening writes, or authoritative bootstrap can resurrect a deleted aggregate;
- dirty local state is overwritten, tombstones compact before retention plus every active device
  checkpoint, or a profile can read/apply/search another profile's projection;
- ACK preflight mutates Room before validating event/type/local/server/version fields, unsupported
  data is marked applied, or WorkManager carries payload/token/URL data;
- Room schema v7 changes from normalized SHA-256
  `ff44bce40b9934784d9022e7eee8ada7ac86fee34624dd8e7be2ac91d93a0b9d` without a named
  non-destructive migration and API 26 preservation evidence.

## P10 evidence requirements

The server/Android jobs must also fail if any of these regress:

- a bounded CSV/JSON/HTML import cannot replay, resume from its durable checkpoint, retain an
  invalid row, or reproduce its sorted audit report from the same input identity;
- any T0-T4 evaluation advances ImportEntry, UserTrackRef or catalog projection before explicit
  review, or a fixture benchmark can activate F-016 automatic matching;
- identifier/fingerprint candidates lose algorithm/version/provenance, hard conflicts are hidden,
  or benchmark output omits its dataset ID/version/canonical SHA-256;
- manual accept/create reuses an ImportEntry decision as a UserTrackRef pointer, loses duplicate
  playlist intent, or fails to coalesce one active owner mapping with distinct immutable lineages;
- a catalog merge/split/reassign/undo bypasses explicit authorization, stable ordered locks,
  inverse audit provenance or atomic rollback;
- Room schema v8 changes from normalized SHA-256
  `7639eb1f005957e057a76812ec4a1a7a2699ed5c451443b4883dda309d73f82c` without a named
  non-destructive migration and API 26 v7-v8 preservation evidence.

## P11 evidence requirements

The server/Android jobs must also fail if any of these regress:

- a fixed pipeline/seed/input snapshot changes ranks, explanation/provenance or evaluator report,
  exact replay loses persisted output, or algorithmic replay substitutes current state after purge;
- any candidate bypasses owner/ACL/availability/active-identity/dislike/exclusion filters, a Home
  fresh-release section ignores artist affinity, or recommended-only history becomes organic taste;
- pipeline manifests or retained snapshots mutate, snapshot/watermark visibility diverges, the
  deterministic 5,000-item cutoff drifts, or expired personal snapshots cannot purge boundedly;
- offline-pack bytes are accepted with an unknown numeric version/encoding, wrong owner/profile,
  non-canonical body, hash mismatch or expiry, or Android rewrites immutable server source rank;
- delivery is treated as impression, presentation retry creates a second P04 event, feedback loses
  causal request/rank/Recording equality, or concurrent devices turn semantic duplicate into 500;
- Room schema v9 changes from normalized SHA-256
  `f7764762cdc29efe25c285e53b0cce6c513dfba0e4a491dfc9ffd2bdcb915d62` without a named
  non-destructive migration and API 26 v8-v9 preservation evidence.

## P12 evidence requirements

The CPU/server jobs must fail if the server lock/import tree gains an ML/CUDA package or imports
`autplay_gpu`, if the runtime-only Compose service set contains `ml-gpu`, or if API/CPU readiness
depends on it. The separate GPU static job must fail if inventory selection is nondeterministic,
an explicit UUID/PCI/index can select an incompatible device, a job carries a URL/path, artifact
size/hash validation occurs after runtime load, FFmpeg output is unbounded, OOM reductions/retries
are unbounded, a restarted lease loses its checkpoint, a stale attempt publishes, model versions
overwrite one another, activation bypasses an APPROVED report, or exact retrieval crosses owner
authorization. HNSW remains forbidden until a named exact-search latency/recall report and rollback
plan justify it. The RTX job is the only accepted source of throughput, p95 and VRAM metrics.

## P13 evidence requirements

The server/Android jobs must fail if room codes are stored in plaintext or treated as media
authorization, an uninvited user/device joins, a Wave source bypasses the normal P06 owner query,
generic commands bypass the final-ready/clock start gate, lifecycle mutations lose their durable
sequence, WebSocket becomes durable truth, snapshot gaps advance Room state, credentials/URLs/paths
enter the v10 projections, playback is scheduled from wall time, prefetch exceeds three future
entries/two active downloads, or drift correction violates the ten-second seek and six-second
direction bounds. The API 26 deterministic three-session fixture records its injected timing and
must fail closed for unavailable media or p95 RTT above 1,000 ms. Physical handset/WAN results may
extend the report later but cannot rewrite the trusted-local evidence boundary.

## Execution policy

1. Start from a fresh checkout with no `.venv`, Gradle cache, Docker project resources, build outputs, generated local config, or secret file.
2. Provision uv, Microsoft JDK and Android SDK from approved official sources; verify exact versions before the scripts run.
3. Use the committed root, server and isolated GPU `uv.lock` files, version catalog, wrapper and image digest. Do not rewrite locks in CI.
4. Generate the runtime signing-secret file only inside the job's restricted temporary directory, mount it through the documented Compose secret, redact its path/value, and remove it during job cleanup.
5. Preserve machine-readable test/lint reports and the debug APK as short-lived CI artifacts; never preserve tokens, configuration secrets, database volumes, or owner/session payloads.
6. Let each script use a PID-scoped Compose project and random loopback database port. The script must refuse pre-existing scoped resources and verify cleanup, so independent jobs need no shared fixed project.
7. Keep the runtime smoke loopback-only and use disposable synthetic data. Never point a CI manifest at a personal or production database.
8. Run the current API 26 emulator suite as a required separate gate. Keep the Samsung A55-class physical target as named P14 evidence; do not turn an absent physical device into a fake pass.

## Cache policy

- Caches are optional accelerators, never evidence. A scheduled or pre-merge cold-cache run is required.
- Key uv caches by OS, architecture, Python/uv pin and both root/server `uv.lock` hashes.
- Key Gradle caches by OS, architecture, JDK, wrapper properties and version-catalog hashes.
- Never cache `.env`, credentials, signing material, PostgreSQL volumes, APK signing keys, tokens, or project-local user data.

## Merge gate

P12/P13 CPU-equivalent changes require all CPU/Android jobs green after CI exists; the GPU static job is
also required for `gpu/` changes. The real RTX job is required before P12 can become green or any
model becomes ACTIVE, but its absence cannot fail unrelated CPU changes. PostgreSQL inventory is
exactly `75/67/19/49` with one Alembic head at `0015_wave_runtime`; Room head is v10 with named
non-destructive migrations from every prior supported version. Toolchain/lock/digest changes also
require official-source review, a cold run, updated `VERSIONS.md`, and handoff evidence.
A-008-A-033 have executable P06-P13 evidence except A-030 is `DEFERRED_WITH_APPROVAL` by ADR-027; P12 real-accelerator/model metrics remain explicitly unavailable and no model is active.
