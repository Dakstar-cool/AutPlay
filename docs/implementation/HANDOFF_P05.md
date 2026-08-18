# P05 Android Local-First Foundation — Final Handoff

**Status:** PASS

**Date:** 2026-08-16

## Outcome

P05 is complete. The Android application launches without server configuration and persists a
sample standalone mutation through Activity/database recreation. Room v1 contains the accepted
ADR-018 local mutation outbox and authenticated Journal lineage model, while bound mutations and
explicit materialization create immutable P04 client events atomically.

P06 has not started. P05 adds no network sync transport, Vault implementation or Media3 playback
implementation.

## Delivered scope

- Room 3.0.1/KSP2 2.3.9 with SQLite 2.7.0 `BundledSQLiteDriver`, WAL, foreign keys, FTS5 and an exact
  committed 26-table schema v1 export.
- Typed IDs and unknown-safe persisted-string values; DAOs for projections, library, playlists,
  audio state, queue, history, Journal, sync state, recommendation packs, search and P04 dedupe/
  redirect seams.
- Fresh standalone transaction: domain/library/search state plus one `local_mutation_outbox` row,
  with no fabricated authenticated binding, wire sequence or request hash.
- Bound transaction: lineage resolution/allocation plus domain/library/search state and one complete
  immutable P04 `offline_journal_event`.
- Explicit pure-local `materializeOutboxToJournal`: stored-payload revalidation, lineage allocation,
  new event identity/hash, domain correlation and outbox link in one idempotent Room transaction.
- Durable `journal_lineage` shared by recreated local profiles for the same
  `(user_id, device_id, journal_epoch)`; the current server-compatible model rejects same-device
  epoch/user reuse and requires a new device identity for a new sequence-1 lineage.
- Composite SQLite FKs ensure event `(lineage,user,device)` and cursor `(lineage,device,epoch)`
  bindings cannot contradict the sequence owner even when a DAO is called directly.
- Strict versioned local-intent policy for `USER_TRACK_REF_CREATED` schema 1: duplicate/malformed
  JSON rejection, recursive safe-property validation, bounded depth, lone-surrogate rejection,
  exact canonical representation and the 262,144-byte UTF-8 limit at insertion/materialization.
- RFC 8785-compatible fixed P04 client-event hashing verified against the language-neutral golden
  vector without adding `journal_epoch` to the hashed wire event.
- External-content FTS5 with bounded quoted queries and SQLite-allocated row IDs; content URI
  validation rejects file/absolute paths before persistence.
- DataStore non-secret settings, Android Keystore AES-GCM credential adapter, stable-ID-only
  WorkManager scheduler seam and implementation-free playback ownership seam.
- Minimal Compose offline flow with the no-profile action enabled and configured/no-profile
  recreation evidence.

## Not delivered

- Sync HTTP transport, consent/authenticated binding UX, ACK/pull/bootstrap/reset and automatic
  outbox conversion remain P09-owned.
- Vault upload/ingest/streaming remains P06-owned.
- Media3 playback/download execution remains P08-owned.
- Samsung A55 physical execution and hosted cross-platform CI remain later evidence; API 26 is the
  verified minimum-SDK device gate.

## Decisions and persistent contracts

- The user accepted `docs/adr/ADR-018-standalone-outbox-and-journal-lineage.md` on 2026-08-16.
- F-018 now means one atomic mutation record in either mode: Journal for an authenticated binding,
  local outbox for standalone; materialization creates a new immutable event atomically.
- Room database version remains 1 because P05 is pre-release. No destructive fallback or migration
  bypass was added.
- Exported normalized schema SHA-256:
  `f063c8ec14ecf8c1fbd7d926f5e9322021e1187c2bf6c486c6b9a6aed88924d2`.
- Exact new Android dependency: `org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0`, used only
  for the bounded JsonElement-based local-intent policy.

## Main changed paths

- `apps/android/src/main/kotlin/app/autplay/application/` — local command, materialization, FTS and
  P04 hash/payload policies.
- `apps/android/src/main/kotlin/app/autplay/data/local/` — Room entities, DAOs and database.
- `apps/android/src/main/kotlin/app/autplay/data/settings/`, `data/security/`, `work/`, `playback/`
  and `domain/` — platform seams and typed identifiers.
- `apps/android/src/androidTest/`, `apps/android/src/test/`, `apps/android/schemas/` — API 26,
  host/golden and exact-schema evidence.
- `docs/adr/ADR-018-standalone-outbox-and-journal-lineage.md` and
  `docs/design/AutPlay_Android_Room_Schema_v1.md` — accepted decision and final physical contract.
- `README.md`, version catalog/build files and implementation registers — reproducible pins,
  commands, traceability and risk state.

## Exact verification evidence

- `:apps:android:testDebugUnitTest`: PASS, 14 tests, including exact schema hash, payload policy and
  P04 golden hash.
- `:apps:android:connectedDebugAndroidTest`: PASS on API 26 x86_64 AVD, 20 tests. Evidence includes
  fresh/restart/WAL/FK/FTS5; standalone and bound commit/rollback; materialization rollback/retry and
  committed-result idempotency; profile-lineage reuse/mismatch isolation; composite-FK mismatch
  rejection; playlist/queue/URI/unknown-state/lease/10,000-row/FTS and Compose recreation cases.
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`: PASS on the final
  tree. It includes 80 harness tests, 51 P04 contract tests, Android lint/unit/debug/minified
  release-R8 gates, CPU dependency audit and 298 real-PostgreSQL/server tests; scoped Docker
  container/network/volume cleanup passed.
- `C:\Program Files\Git\bin\bash.exe scripts/check.sh --server-only`: PASS on the final tree with
  80 harness, 51 contract and 298 real-PostgreSQL/server tests plus scoped cleanup.
- `git diff --check`: PASS.

The non-blocking SDK XML-v4 parser warning is retained as environment evidence. The repeated
`UseKtx` lint category was resolved after official Android source review by pinning Core KTX 1.18.0
and using `String.toUri`; no global lint suppression or baseline was added.

## Independent review

The initial implementation review found P04 binding/hash, FTS row-ID, URI and exact-schema issues;
they were fixed. ADR-018 then received a separate architecture/review cycle and was accepted after
lineage, ownership and recursive-payload corrections.

The final read-only review found two majors: copied event/cursor binding columns were not enforced
by SQLite, and phase evidence still described the earlier blocker. Composite parent keys/FKs and an
API 26 negative test resolved the integrity issue; this final handoff and the synchronized registers
resolve the evidence issue. Final re-review reports no remaining critical or major finding.

## Risks and debt

- R-001/R-003 are mitigated for the P05 local persistence boundary; P09 still owns network
  convergence, cursor application and reset behavior.
- Samsung A55-class physical, hosted Linux/macOS/Windows CI and production release evidence remain
  open under R-011/R-012/R-016 and P14.
- The shared repository is still pre-release; future persistent changes must increment the schema
  and provide non-destructive migrations.

## Exact next prerequisite

The next product phase is P06 using `docs/build-pack/prompts/P06_vault_streaming.md`, after this
handoff is verified. P00-D004 must be resolved before deterministic SHA/T4 identity-reuse semantics
are implemented. The checked-in phase pipeline currently has no P05 -> P06 edge, so the trusted
Stop hook does not start P06 automatically.

## Git state

The shared worktree remains intentionally dirty with the accumulated uncommitted P04/harness and
P05 changes. No reset, stash, commit, push, PR, deployment or external write was performed.
