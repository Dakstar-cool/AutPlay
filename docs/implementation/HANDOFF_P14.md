# AutPlay P14 handoff

## Outcome

`PASS` on 2026-08-18 for the declared local CPU/local-first RC boundary. Every applicable P14 row
has executable PASS evidence, including A-038 on a physical Samsung SM-A556E. P12 A-030 remains
`DEFERRED_WITH_APPROVAL` under ADR-027; no GPU model is active or packaged. This handoff does not
authorize publication, deployment or production signing.

ADR-027 accepts P00-D005 under the initiating user's explicit authorization and defers only P12
A-030 real CUDA OOM/RTX/model metrics. P13 remains PASS. No GPU model is active or packaged.

## Delivered scope

- Two-project PostgreSQL/Vault backup and isolated restore drill with a custom dump, canonical
  manifest, production filesystem adapter, healthy APPLY reconciliation and corrupt-object
  quarantine across bytes plus object/replica database state.
- Named 100,000-row PostgreSQL search benchmark with p50/p95/p99 and documented target.
- API 26 Room/FTS benchmarks with named 10,000/1,000-row fixtures, 30 measured samples and
  machine-readable p50/p95/p99 evidence.
- Security/privacy and object-authorization review, negative suite, production-source secret scan,
  frozen-lock OSV audits, CycloneDX 1.5 SBOMs and resolved Python/Android license inventory.
- Android host lint/unit/release/R8 build, development-only v2/v3 signing, API 26 install/restart
  and 82-test connected suite; local CPU image and immutable artifact hashes.
- Backup/restore, privacy/export/delete and observability/failure runbooks, performance/security/
  test reports, checklist, release notes, acceptance/traceability/progress/risk/version updates.
- Recommendation impression visibility fix using Compose's visibility modifier; the focused test
  passes after the unmerged-semantics correction and the complete connected suite passes 82/82;
  failed impression writes can retry and have a host regression test.
- One joined file-backed Android Room → production `OkHttpSyncTransport` → FastAPI HTTP/auth →
  PostgreSQL 18.4 → second Android Room scenario, including post-commit ACK-loss replay and direct
  server-count verification, closes A-039.

## Not delivered or claimed

- Production signing, publication, pushed image, deployment, public domain/TLS/reverse proxy,
  production backup target/retention, live provider or cross-instance Wave fanout.
- P12 A-030 real CUDA OOM, RTX throughput/job-time/VRAM and reviewed-model quality metrics. These
  are `DEFERRED_WITH_APPROVAL`; no model is activated.

## Principal changed files

- `scripts/p14_drill.py`, `scripts/p14_android_server_e2e.py`, `scripts/p14_release_audit.py`,
  `scripts/build-p14-rc.ps1`
- `tools/autplay_codex/tests/test_p14_release_audit.py`
- `apps/android/src/main/kotlin/app/autplay/MainActivity.kt`, its host retry test and Home connected test
- `server/src/autplay/adapters/filesystem/vault.py` and its exact-byte regression test
- `apps/android/src/androidTest/kotlin/app/autplay/application/sync/SyncCoordinatorAcceptanceTest.kt`
- `server/tests/postgresql/test_p14_release_e2e.py`
- `docs/adr/ADR-027-p14-conditional-phase-reachability.md`
- `docs/operations/BACKUP_RESTORE.md`, `PRIVACY_DELETE_EXPORT.md`, `OBSERVABILITY.md`
- `docs/release/RC1_CHECKLIST.md`, `RELEASE_NOTES_RC1.md`, `PERFORMANCE_REPORT.md`,
  `SECURITY_REVIEW.md`, `TEST_EVIDENCE.md`, SBOMs, dependency report and dev-signed artifact
- `docs/implementation/evidence/P14_*.json`
- `docs/build-pack/MVP_ACCEPTANCE_MATRIX.md`
- `docs/implementation/PLAN.md`, `PROGRESS.md`, `RISK_REGISTER.md`, `TRACEABILITY.md`,
  `VERSIONS.md`, and root `README.md`

The worktree already contained the uncommitted P04-P13 implementation. It was preserved; this
handoff lists the P14-owned delta rather than presenting the shared dirty tree as newly clean.

## Decisions, migrations and contracts

- ADR-027 accepts P00-D005, with only A-030's unavailable real-accelerator/model evidence deferred.
  It cannot defer A-038 or any security/data-loss gate; A-038 now has physical-device PASS evidence.
- PostgreSQL stays at Alembic `0015_wave_runtime`; Android stays at Room v10. P14 adds no schema
  migration, external endpoint or protocol contract.
- The RC uses a development debug key only. Its hash is evidence, not a production signing choice.
- P14 tooling may uninstall `app.autplay` only after `ro.kernel.qemu=1` proves a disposable
  emulator and only for `INSTALL_FAILED_UPDATE_INCOMPATIBLE`; physical devices are never cleared.

## Exact commands and results

`uv run --project server --frozen python scripts/p14_drill.py`

- PASS in 36.925 s: PostgreSQL 18.4/pgvector 0.8.6, Alembic `0015`, 100,000 recordings, one Vault
  object/replica restored into an independent project through `FilesystemVaultStorage`; healthy
  reconciliation inspected 1/repaired 0, corrupt reconciliation inspected 1/repaired 1/quarantined
  1 and set both object/replica `QUARANTINED`; p50/p95/p99 `5.525/6.403/6.665 ms` against p95
  `300 ms`; all scoped containers, volumes and networks removed.

Targeted security command covering auth/logging/Vault/stream/library/import/recommendation/Wave,
filesystem and sync suites:

- PASS: 66 passed, 1 skipped because Windows lacks symlink-creation privilege.

`./gradlew.bat -Dorg.gradle.java.home=<JDK17> --no-daemon --console=plain :apps:android:connectedDebugAndroidTest`

- PASS: 82 passed, 0 skipped or failed on API 26 after the focused Home and P14 sync scenarios.

`SyncCoordinatorAcceptanceTest.offlineRoomJournalSurvivesProcessDeathAndProjectsExactlyOnceToSecondDevice`
on API 26:

- PASS in the standard suite with the stateful transport seam, and PASS in the joined P14 run with
  the production HTTP adapter. The file-backed Room transaction/journal survive close/reopen; the
  real server commits before the wrapper loses the first ACK; the reopened coordinator reuses the
  immutable event ID/hash and materializes the event in a second Android database.

`uv run --project server --frozen python scripts/p14_android_server_e2e.py --java-home <JDK17> --android-home <ANDROID_SDK> --device-serial emulator-5560`

- PASS in 54.755 s. One execution crossed Android Room, `OkHttpSyncTransport`, bearer-protected
  FastAPI routes, PostgreSQL 18.4 and second-device Room bootstrap over a scoped `adb reverse` wire.
  Direct PostgreSQL verification found one device inbox row, one sync event, one user-track
  projection and two device cursors. Disposable credentials were not persisted; Compose and adb
  forwarding cleanup passed. Evidence: `P14_ANDROID_SERVER_E2E_2026-08-17.json`.

`server/tests/postgresql/test_p14_release_e2e.py` in the canonical real-PostgreSQL gate:

- PASS: one joined offline batch preserves IDs/order through server push, second-device delivery,
  process death before cursor ACK, replay, cursor ACK and new-client bootstrap projection.

`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-p14-rc.ps1 -JavaHome <JDK17> -AndroidHome <ANDROID_SDK> -DeviceSerial <physical-serial>`

- PASS: Android lint/unit/release/R8; CPU image
  `sha256:8d259f377f6911adfe5716a0b414747f3e1521efe5080650a6cf4e2ec665c840`.
- Unsigned APK SHA-256 `ae5665db91180f43bae676b14316d593625eac42a51a87c0e4ddc20f3141036c`.
- Development-signed APK SHA-256
  `f76b96eb4f3b8e5eda9e609a86aeb8b7ddfffe8c01de24f9236f37074962acfc`.
- v2/v3 signature, explicit-serial install, background transition, no battery-optimization bypass,
  observed force-stop process death and restart PASS on physical Samsung SM-A556E (`arm64-v8a`,
  SDK 36); no uninstall, data clearing, push, deploy or production signing. The raw serial is not
  persisted; evidence records only its SHA-256.

`uv run --frozen python scripts/p14_release_audit.py --java-home <JDK17> --android-home <ANDROID_SDK> --device-serial <physical-serial>`

- PASS: 19 artifacts, including `P14_ANDROID_PERFORMANCE.json`; root/server/GPU SBOMs with
  36/46/55 components; license metadata resolved
  for 37/47/56 Python environment entries and 159 Android runtime coordinates with zero unresolved;
  all OSV audits report zero vulnerabilities/adverse statuses; secret scan zero findings; physical
  A55 true. The audit installs the same dev-signed RC artifact as the build command and fails closed
  if it is missing. LGPL/MPL notices/linking and NVIDIA redistribution review remain required before
  any publication, which P14 does not perform.

`LibraryVerticalSliceRepositoryTest.largeSearchAndPlaylistQueriesMeetApi26Baseline`

- PASS in the final 82/82 suite: API 26 x86_64, one warm-up and 30 measured nearest-rank samples.
  Local FTS top-50 p50/p95/p99 `9.397/12.555/13.254 ms`; 1,000-entry playlist query
  `8.760/11.876/11.879 ms`; both p95 values pass the `150 ms` target. Evidence:
  `P14_ANDROID_PERFORMANCE.json`.

`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`

- PASS: root harness 85/85, sync/Wave contracts 53/53, Android lint/unit/debug/release/R8, server
  425 passed plus one documented Windows symlink skip against real PostgreSQL 18.4/pgvector 0.8.6;
  Docker container/network/volume cleanup PASS.

## Acceptance evidence

| Rows | State | Evidence |
| --- | --- | --- |
| A-001-A-004 | PASS | prior green phase handoffs and final canonical gate |
| A-005-A-007 | PASS | corrected stale matrix rows from executable P05 API 26 evidence |
| A-008-A-029 | PASS | P07-P12 handoffs plus final canonical/connected regressions |
| A-030 | DEFERRED_WITH_APPROVAL | ADR-027 and P12 hardware probe; no model active |
| A-031-A-033 | PASS | P12/P13 handoffs and final regressions |
| A-034 | PASS | production-adapter healthy/corrupt reconciliation in `P14_BACKUP_RESTORE_2026-08-17.json` |
| A-035 | PASS | security review, secret scan, targeted/canonical gates |
| A-036 | PASS | owner-negative targeted and real-PostgreSQL suites |
| A-037 | PASS | performance report and drill JSON |
| A-038 | PASS | physical Samsung SM-A556E install/background/battery-policy/process-death/restart; no uninstall or data clearing |
| A-039 | PASS | one joined file-backed Android Room/OkHttp/FastAPI/real-PG/crash-replay/second-Room run with direct exactly-once server counts |
| A-040 | PASS | release inventory, SBOMs, locks/digests and artifact hashes |

No critical/high data-loss or object-authorization defect was found in the tested RC surface.

## Repeated-error protocol record

- Two secret-scan false positives triggered research against official Gitleaks/OWASP guidance. The
  fix is an exact-path allowlist for the disposable loopback credential, not a broad exclusion.
- Repeated `INSTALL_FAILED_UPDATE_INCOMPATIBLE` triggered Android signing/adb/build-variant research.
  Recovery now requires an explicit build switch or `ro.kernel.qemu=1`, and deletes only disposable
  emulator app data. Physical devices are protected.
- The Home impression test failed twice because the custom global-layout callback did not reliably
  re-fire after scroll. Compose testing/visibility guidance was reviewed; production now uses
  `onVisibilityChanged(minFractionVisible = 0.01f)`. A later full-suite run exposed a separate
  post-`Activity.recreate()` semantics race; the test now waits for the unmerged node before
  scrolling. Focused and final 82/82 regressions pass.
- Exercising the production Vault adapter exposed Windows text-mode newline translation in staging
  descriptors. `O_BINARY` is now mandatory for creation/read/write, and a newline-bearing physical
  CAS byte test plus the full production-adapter restore/reconciliation drill pass.
- Two JBR 21 Windows test runs failed to load compiled classes from the repository's Cyrillic path.
  Official Gradle/Android/Kotlin guidance and the generated worker argfile were inspected; an ASCII
  drive alias proved the classpath diagnosis. The normative Microsoft OpenJDK 17.0.20 run then
  rebuilt and passed all 18 JVM test classes, so no dependency or build-script workaround was added.
- The physical closeout first stopped because Docker Desktop's Linux engine was not running. Official
  Docker start/restart, engine-selection, WSL/virtualization and diagnostics guidance was compared;
  `docker desktop start` restored the existing Linux engine without reset, purge or image deletion.
- The first physical release-audit pass attempted to replace the dev-signed RC with a separately
  signed debug APK. Signature inspection proved the mismatch before any uninstall. The audit now
  requires and reinstalls the exact build-evidence-bound dev-signed RC artifact, re-verifies v2/v3,
  returns nonzero without a physical A55, records its path/hash, and passes five focused harness
  regressions plus the physical audit.

## Independent review

- Initial read-only review found two Major issues: a non-A55 audit exited zero, and the tested APK
  was not cryptographically bound back to `P14_RELEASE_BUILD.json`. Both are fixed and regression
  tested.
- Independent re-review reports zero unresolved Critical or Major findings and confirms coherent
  APK hashes, physical evidence, fail-closed status and no uninstall/`pm clear` in the Python
  physical-device failure path.
- Residual Minor: the PowerShell build script's physical failure branch is protected by explicit
  opt-in plus `ro.kernel.qemu=1` and was code-reviewed, but lacks a dedicated automated branch test.

## Risks and debt

- Public/WAN topology, production backup target, hosted historical secret scanning, production
  concurrency/soak and hosted cross-platform CI remain deployment/future-policy work.
- A-039 is joined across the production HTTP adapter and real PostgreSQL, but uses scoped local
  `adb reverse`, an emulator and two independent Room databases rather than public/WAN transport or
  a second physical handset. This remains a topology limitation, not an A-038 blocker.
- The PowerShell RC builder's emulator-only uninstall recovery guard lacks a dedicated automated
  physical-failure branch test; the equivalent Python audit path is covered and both implementations
  require explicit opt-in plus emulator proof.

## Exact continuation prerequisite

There is no P15 prompt. The user authorized one consolidated P04-P14 checkpoint on the primary Git
branch and a private GitHub push. Product publication, deployment, production signing, production
backup/provider selection and GPU model activation remain outside this handoff and require their
own decisions.

## Git state

- Target branch: primary local branch `master`.
- P04-P14 and the repository harness are consolidated in this checkpoint commit under the user's
  explicit authorization; prior phase handoffs preserve their historical dirty-worktree state.
- The user authorized a private GitHub push. No product publication, deployment, production signing
  or destructive real-data action ran.
