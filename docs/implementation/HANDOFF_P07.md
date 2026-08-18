# HANDOFF P07 — Library, Playlist, History and Search Vertical Slice

## Outcome

P07 is PASS. Android now provides the first user-visible local-first library/search/playlist/history
slice without a server dependency. Local library, preference, playlist, listening and URI-import
mutations commit with exactly one standalone outbox intent or one bound Offline Journal event in
the same Room transaction. Server-side internal commands and read-only authenticated projections
are owner-scoped and P04-compatible; no REST mutation bypass or sync engine was added.

A-008, A-009 and A-010 are PASS. P08 is eligible but has not started.

## Delivered scope

### Android

- Offline library add, logical remove and restore with retained `UserTrackRef`/audio intent.
- Canonical `NEUTRAL`/`LIKED`/`DISLIKED` preference and logical listening-history capture with
  exclusion rules.
- Manual playlists with stable entry IDs, duplicate Track refs, fixed-width base-62 fractional
  positions, insert/reorder/remove/delete and bounded rebalance.
- SAF `OpenDocument` UI, persistable-read permission attempt, metadata/readability inspection and
  visible repairable `MISSING`/`PERMISSION_REVOKED` intent.
- External-content FTS5 projection for add/import, bounded safe Cyrillic/Latin/transliteration/
  punctuation queries and deterministic BM25 plus row-ID ordering.
- Observable Compose library/search/playlist/history/import flows; UI calls application
  repositories only.
- One generic Room transaction boundary for library, preference, playlist, playlist-entry,
  listening and import mutations, plus explicit standalone-to-Journal materialization seam.
- P04 event names, aggregate types, parent playlist/placement intent and specialized full
  `LISTENING_EVENT_RECORDED` payloads. Recommended listens require complete attribution.
- Strict recursive privacy-name validation and RFC 8785 canonicalization for additive nested P07
  payloads; unknown safe attribution members are retained without acquiring local semantics.

### Server

- Pure command values and owner-scoped PostgreSQL repository/application seams for unresolved
  refs, library entries, preferences, playlists/entries and logical listening.
- Library restore, playlist metadata/delete, duplicate entry add, move/remove, optimistic row
  versions and non-disclosing cross-owner failures.
- Authenticated read-only `/api/v1/library/entries`, `/search`, `/playlists` and `/history` routes;
  there are deliberately no library REST writes.
- Bounded literal-escaped search and deterministic opaque keyset pagination using timestamp plus
  UUID for entries, playlists and history. Malformed cursors and stripped-empty search use stable
  422 errors.
- Recommended listening validates a complete attribution, verifies the recommendation request is
  owned by the principal and persists its FK; P09 still owns canonical interaction projection.

## Not delivered by design

- No Media3 playback/download implementation, queue restoration or Vault source resolver (P08).
- No push/ACK/pull transport, sync dispatcher, tombstone lifecycle, bootstrap/reset engine or
  canonical interaction projection (P09).
- No import-file adapter, probabilistic identity matcher, Recording merge or identity-ledger
  owner projection (P10).
- No recommendation serving/model pipeline, external acquisition adapter, GPU worker, message bus,
  production topology, deployment, backup or destructive migration.

## Main changed paths

- Android vertical slice:
  - `apps/android/src/main/kotlin/app/autplay/application/library/LibraryVerticalSliceRepository.kt`
  - `apps/android/src/main/kotlin/app/autplay/application/importing/ContentUriInspector.kt`
  - `apps/android/src/main/kotlin/app/autplay/application/search/LocalTrackSearchRepository.kt`
  - `apps/android/src/main/kotlin/app/autplay/application/sync/P07PayloadCodec.kt`
  - `apps/android/src/main/kotlin/app/autplay/domain/library/LibraryRules.kt`
  - `apps/android/src/main/kotlin/app/autplay/data/local/dao/Daos.kt`
  - `apps/android/src/main/kotlin/app/autplay/MainActivity.kt`
- Android evidence:
  - `apps/android/src/test/java/app/autplay/**`
  - `apps/android/src/androidTest/kotlin/app/autplay/application/library/LibraryVerticalSliceRepositoryTest.kt`
  - `apps/android/src/androidTest/kotlin/app/autplay/data/local/AutPlayDatabaseTest.kt`
  - `apps/android/src/androidTest/kotlin/app/autplay/OfflineLibraryScreenTest.kt`
  - `apps/android/src/androidTest/AndroidManifest.xml`
  - `apps/android/src/androidTest/kotlin/app/autplay/testing/RevokedContentProvider.kt`
- Server:
  - `server/src/autplay/domain/library.py`
  - `server/src/autplay/application/library.py`
  - `server/src/autplay/adapters/postgresql/library_runtime.py`
  - `server/src/autplay/entrypoints/library_http.py`
  - `server/src/autplay/entrypoints/api.py`
  - `server/src/autplay/entrypoints/composition.py`
  - `server/tests/test_library_domain.py`
  - `server/tests/runtime/test_library_api.py`
  - `server/tests/postgresql/test_library_runtime.py`
- Toolchain/decision/docs:
  - `gradle/libs.versions.toml`, `apps/android/build.gradle.kts`
  - `docs/adr/ADR-020-p07-agp-kotlin-jcs-compatibility.md`
  - README, Decision/MVP registers, PLAN/PROGRESS/TRACEABILITY/RISK/VERSIONS/CI and this handoff.

## Decisions, migrations and contracts

- ADR-020 is accepted. AGP is pinned to `9.1.0`, the published Kotlin `2.4.10` compatibility upper
  bound, after AGP `9.1.1` reproducibly compiled but failed to place built-in-Kotlin unit classes on
  the AndroidUnitTest runtime classpath.
- General P07 payloads use exact `io.github.erdtman:java-json-canonicalization:1.1`, the Java
  implementation referenced by RFC 8785, after AutPlay's bounded strict scanner. The jar SHA-256
  is recorded in `VERSIONS.md`.
- P04 schemas/event names and all 51 contract vectors remain unchanged and green. P07 emits only
  contract-declared event/aggregate combinations and does not emit duplicate generic feedback.
- No Room schema change, PostgreSQL schema change, Alembic migration or OpenAPI sync-contract path
  was added. Alembic head remains `0011_vault_runtime`; inventory remains `59/57/13/41`.

## Exact verification evidence

- Final canonical gate, with verified Microsoft OpenJDK `17.0.20+8-LTS` and the pinned SDK:
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1` — PASS.
  It passed root lock/import/Ruff/format/mypy, 80 harness tests, all 51 P04 contract tests, server
  lock/import/Ruff/format/strict mypy/CPU dependency audit, Android lint, 23 host tests, debug APK,
  minified release/R8 APK (`BUILD SUCCESSFUL`, 101 tasks), and 347 server/real-PostgreSQL tests
  (`346 passed, 1 skipped`). The one skip is the pre-existing Windows symlink-privilege case.
- The final disposable PostgreSQL 18.4/pgvector 0.8.6 project passed the P07 cross-owner, literal
  search, restore, duplicate/move/delete, all three cursor and recommendation-request ownership/FK
  assertions. Its container, network and volume were removed; command exit code was 0.
- Final API 26 gate:
  `.\gradlew.bat "-Dorg.gradle.java.home=$env:JAVA_HOME" --no-daemon --console=plain :apps:android:connectedDebugAndroidTest`
  — 30 tests PASS, `BUILD SUCCESSFUL` in 38 seconds.
- The API 26 30-sample baseline on 10,000 FTS rows and one 1,000-entry playlist recorded search
  p95/p99 `13.0833/13.6908 ms` and playlist p95/p99 `10.5265/12.8252 ms`; both p95 values are below
  the declared `150 ms` target.
- API 26 also proves offline remove/restore, shared rollback for every P07 aggregate, duplicate/
  reorder persistence, attributed-vs-organic owning events after reopen, import-to-FTS, and a real
  permission-denied content provider whose intent remains repairable.
- The disposable AVD, AVD files and emulator process were removed; `adb devices` was empty.
- Final `git diff --check`: PASS after handoff/register updates.

## Independent review

The read-only P07 reviewer initially found malformed specialized listening payloads, non-JCS JSON,
privacy-key gaps, Android/server enum divergence, missing import FTS/UI, broken keyset pagination,
unbounded blank search and missing playlist validation/evidence. These were fixed.

The bounded re-review then found recommendation-request persistence/ownership, playlist pagination
and playlist-entry parent/order payload gaps. The implementation and real-PostgreSQL/API 26 tests
were corrected. The final narrow review reports no remaining critical or major issue.

## Risks and debt

- R-005 is mitigated for P07 intent retention. P08 still owns actual playback fallback when local
  content is unavailable.
- R-010 is mitigated for P07 commands/queries, including recommendation-request ownership and
  non-disclosing failures. P14 still owns the complete cross-feature authorization audit.
- R-011 P07 minimum-API query targets pass on the named synthetic fixtures; Samsung A55-class and
  system-wide release SLO evidence remains P14-owned.
- R-020 owning-event attribution/canonical bytes are proven through restart. P09 still owns
  exactly-once canonical server interaction projection; no such projection is claimed here.
- The Android SDK XML-v4 parser warning remains non-blocking environment evidence. No lint or
  version assertion was bypassed.

## Exact next prerequisite

P08 is eligible but has not started. Its exact prompt is
`docs/build-pack/prompts/P08_playback_downloads.md`; first verify this handoff and preserve P04-P07
contract, persistence, authorization and canonical-gate evidence. Do not start P09 early.

## Git state

The shared worktree remains intentionally dirty with accumulated uncommitted P04/harness and
P05-P07 changes. Existing unrelated user edits were preserved. No reset, stash, commit, push, PR,
deployment, real-data write or external-system write was performed.
