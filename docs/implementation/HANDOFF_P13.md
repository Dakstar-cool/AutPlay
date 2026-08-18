# P13 Handoff — Hybrid Wave Group Playback

**Outcome:** `PASS` (A-032 and A-033 PASS inside the declared trusted-local, single-API-process boundary)

**Date:** 2026-08-17

## Summary

P13 delivers authenticated host-controlled Wave rooms without changing AutPlay's local-first
library boundary. PostgreSQL is the durable room, queue, membership and command truth; REST owns
mutations and snapshots; WebSocket is a disposable hint/catch-up channel. Every Android device
retains its own P08 source authorization and Media3 execution. Room v10 stores only a bounded
profile/user/device recovery projection. P09 Offline Journal, P2P audio, GPU enrichment, a broker
and room-scoped Vault grants are not used.

P12 remains independently `BLOCKED`: no RTX/model metric has been inferred from Wave evidence and
no model was activated.

## Delivered scope

- Accepted [ADR-026](../adr/ADR-026-p13-wave-room-clock-and-recovery.md) and frozen
  [Wave Protocol v1](../design/AutPlay_Wave_Protocol_v1.md), JSON envelope and OpenAPI contracts.
- Alembic `0015_wave_runtime` adds seven `wave` tables for digest-only room codes, invite-scoped
  device membership, canonical Recording queue entries, ordered commands, expiring per-device
  preflight and timing reports. Downgrade refuses while any Wave row exists.
- Create/join/snapshot/leave/close/host-transfer/expiry and host-loss election are durable. Leave,
  close, explicit/automatic transfer, orphan pause and expiry append one ordered command in the
  same room-row transaction. Host leave requires transfer while another member remains.
- At most seven invited users and eight active devices; a current bearer, matching invite and
  ten-character code are required to join. The code is stored only as SHA-256 and never authorizes
  media. Every room/source operation rechecks the caller's user/device membership.
- Queue writes are host-only, bounded to 100 and versioned. Exact idempotent retries return the
  prior sequence; changed-body reuse or stale expected sequence/version fails closed.
- Strict `/start` captures present devices and emits `PLAY` only when every device has fresh
  queue-entry/version-bound final readiness and an eligible clock. Generic `PLAY` is rejected.
  An unavailable source, stale report or unstable clock durably emits `START_ABORTED` and pauses.
- Vault capability lookup delegates to the existing P06 owner-filtered resolver and returns only a
  boolean capability; no token, URL, path, grant or other user's authorization crosses Wave.
- WebSocket authenticates from headers only, validates epoch/cursor, reauthenticates within ten
  seconds and uses PostgreSQL catch-up or `snapshot_required`; a bounded in-process broadcaster
  may drop hints without losing truth.
- Low-cardinality Wave timing/failure metrics accept only command-lag/start-skew/drift and bounded
  buffer/rejoin labels; room/user/device/Recording IDs and payloads are excluded.
- Android Room v10 adds `wave_room`, `wave_preflight` and `wave_queue_projection` with a named
  v9→v10 migration. Snapshot replacement and contiguous sequence application are transactional;
  credentials, room codes, URLs, paths, clock samples and byte progress are not persisted.
- The Android coordinator exposes create/join/leave/close/transfer, preflight/timing and host
  commands; performs snapshot-first bounded reconnect; rejects duplicate/gap/epoch mismatch;
  maps canonical Recording to nullable local UserTrackRef; and leaves the ordinary library intact.
- Accepted Wave play materializes a one-item P08 `WAVE` queue, prepares it through the existing
  Media3 service and schedules against `SystemClock.elapsedRealtime()`. The current source remains
  pinned. On snapshot, the Android adapter probes current+next three through the P08 LOCAL →
  completed download → normally authorized Vault resolver; current final-ready waits up to eight
  seconds for the actual Media3 `STATE_READY`, and Vault additionally requires 3,000 ms buffered.
  Proactive future entries delegate to the existing DownloadIntentRepository/DownloadService;
  prefetch planning is capped at the next three entries and the existing Media3 manager remains
  capped at two concurrent downloads.
- Clock estimation uses seven initial samples, 20 retained and the five lowest RTTs. Start
  eligibility, 8-second lead cap and hysteretic drift speed/seek/degraded limits match ADR-026.
- A minimal Compose Wave surface exposes host/member, preflight, rejoin and degraded state while
  explicitly preserving local library/queue usability.

## Contracts and migrations

- Alembic head: `0015_wave_runtime`; physical inventory `75/67/19/49`; typed mapping inventory
  75 tables, 841 columns and 66 mapped explicit indexes.
- Room head: v10; Room identity hash `eff029c0b73e3189b9ab8e31b0261541`; exported schema file
  SHA-256 `9f42becf68b2bd5a92a1bf788dbc3cda361894db3690d1fa9a77f6cd34aa7c90`.
- Event schema: `contracts/events/v1/wave-envelope.schema.json`.
- OpenAPI slice: `contracts/openapi/v1/autplay-wave.openapi.json`.

## Acceptance evidence

- A-032: real PostgreSQL invite/device ACL, exact retry, strict LOCAL/Vault/unavailable final-ready
  gate, durable close/transfer/expiry and deterministic host-loss election in
  `server/tests/postgresql/test_wave_runtime.py`.
- A-033: deterministic API 26 three-session LOCAL/DOWNLOAD/VAULT fixture, unavailable/high-RTT
  fail-closed case and measured report in
  [P13_WAVE_TIMING_2026-08-17.json](evidence/P13_WAVE_TIMING_2026-08-17.json): p95 command lag
  220 ms (target 250), start skew 0 ms (target 150), p95 absolute drift after ten seconds 91 ms
  (target 100), with p95 RTT 60 ms and uncertainty 30 ms.
- API 26 Room v9→v10 preservation and exact three additive Wave tables.
- JVM duplicate/reorder/gap, reconnect, seven-sample clock, scheduled lead, drift hysteresis,
  prefetch caps and Authorization-header/no-token-URL tests.
- Runtime WebSocket hello/catch-up/gap, live invalidation, token revoke and masked owner-source tests.
- The P13 timing artifact is deterministic injected emulator evidence, not a physical multi-handset
  or public-Internet/WAN measurement. It is sufficient only for the declared P13 boundary.

## Exact commands and results

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly
```

- PASS: 80 harness tests, 53 contract tests, strict Ruff/format/mypy and CPU dependency audit.
- PASS against disposable PostgreSQL 18.4 / pgvector 0.8.6: 424 passed, 1 Windows symlink test
  skipped because the host lacks symlink privilege; scoped container/network/volume removed.

```powershell
$env:JAVA_HOME='C:\Users\ptica\AppData\Local\Temp\autplay-jdk-17.0.20-8\extracted\jdk-17.0.20+8'
.\gradlew.bat "-Dorg.gradle.java.home=$env:JAVA_HOME" --no-daemon --console=plain `
  :apps:android:compileDebugKotlin :apps:android:testDebugUnitTest
.\gradlew.bat "-Dorg.gradle.java.home=$env:JAVA_HOME" --no-daemon --console=plain `
  :apps:android:lintDebug :apps:android:assembleDebug :apps:android:assembleRelease
```

- PASS: Android lint, 54 JVM tests, debug APK and minified release/R8 APK.

```powershell
.\gradlew.bat :apps:android:connectedDebugAndroidTest `
  -Pandroid.testInstrumentationRunnerArguments.class=app.autplay.domain.wave.P13WaveRuntimeTest,app.autplay.data.local.P13RoomMigrationTest
```

- PASS on `codex_p13_api26` / Android 8.0 API 26: 3 tests, 0 failures.

## Independent review and corrections

The read-only P13 review identified plaintext/authorization ambiguity, generic `PLAY` bypass,
wall-clock scheduling, incomplete snapshots/reconnect, non-durable lifecycle changes and missing
Android execution/lifecycle seams. The final tree closes these findings with invite rows, strict
`/start`, AndroidX Media3 monotonic scheduling, full epoch/role/version snapshots, bounded recovery,
durable lifecycle commands, concrete REST lifecycle methods and Media3 executor wiring. Focused
tests and both canonical gates cover the corrected paths.

## Deliberate exclusions and residual boundary

- Public Internet domain/TLS/reverse proxy, NAT traversal and cross-instance WebSocket fanout are
  not selected. P13 is verified only on a trusted local network with one API process.
- No P2P audio, party voting/collaborative queue, room-scoped Vault grant, broker, Redis or Wave
  microservice was added.
- Physical multi-handset/WAN timing and Samsung A55 release qualification remain P14 evidence; the
  deterministic API 26 report is not presented as that measurement.
- P12 A-030 remains `IN_PROGRESS` until reviewed RTX/model OOM, throughput, p95, VRAM and quality
  evidence exists. P13 does not make P14 reachable unless P12 passes or receives an explicit
  approved deferral.

## Documentation updated

`README.md`, `DECISION_REGISTER.md`, `MVP_ACCEPTANCE_MATRIX.md`, `PLAN.md`, `PROGRESS.md`,
`TRACEABILITY.md`, `RISK_REGISTER.md`, `VERSIONS.md`, `CI_PLAN.md`, the PostgreSQL/Room schema
documents, ADR-026 and this handoff reflect the verified P13 state.

## Git state

- Branch: `codex/autplay-harness-v1`.
- Starting/recorded HEAD: `0023fa9ad9d12633ad988230662fbd69bb74eb20`.
- The shared worktree contains the user's accumulated uncommitted/untracked P04-P13 work. It was
  preserved; no reset, cleanup, commit, push, PR, deployment or external write was performed.

## Exact next prerequisite

Stop after P13. Before P14, either complete P12 A-030 with the reviewed artifact and real RTX
evidence, or obtain and record an explicit P12 deferral under P00-D005. Then read
`docs/build-pack/prompts/P14_hardening_release.md` and the latest handoffs; do not infer that
P13's trusted-local evidence selects production TLS/network topology.
