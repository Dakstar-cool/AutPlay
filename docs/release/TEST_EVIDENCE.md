# AutPlay RC1 test evidence

This document is the historical RC1 result summary. Raw phase JSON is kept as local operator
evidence rather than distributed in Git. Re-run the canonical checks and the release/restore
scripts for evidence against the current checkout.

## Executed release evidence

| Gate | Result | Evidence |
| --- | --- | --- |
| Canonical repository gate | PASS | contracts 53/53; server 425 passed/1 Windows symlink skip; Android lint/unit/release/R8; real PostgreSQL 18.4/pgvector 0.8.6; scoped cleanup |
| Release tooling Ruff/format/mypy | PASS | root Ruff/format + root/server strict mypy commands |
| Isolated PostgreSQL/Vault restore | PASS | two isolated PostgreSQL projects, manifest/hash verification and corruption quarantine |
| Large catalog benchmark | PASS | same report; 100,000 rows, p50/p95/p99 5.525/6.403/6.665 ms |
| Android Room/FTS benchmark | PASS | API 26, 30 samples; FTS p50/p95/p99 9.397/12.555/13.254 ms and 1,000-entry playlist 8.760/11.876/11.879 ms |
| Joined offline-to-online E2E | PASS | one execution crosses file-backed Room journal/reopen, production OkHttp serialization/auth, FastAPI HTTP, PostgreSQL 18.4, post-commit ACK loss/immutable retry and second Android DB projection; server counts are inbox=1, sync event=1, user projection=1, device cursors=2 |
| Python SBOM/vulnerability audit | PASS | three generated CycloneDX documents and zero reported audit findings |
| Dependency license inventory | PASS with publication review boundary | 299 resolved environment entries, zero unresolved; LGPL/MPL and proprietary GPU obligations declared |
| Production secret scan | PASS | zero findings |
| Targeted auth/redaction/Vault/API/filesystem | PASS | 66 passed, 1 Windows symlink-privilege skip |
| Android host lint/unit/R8/release | PASS | canonical release build command |
| Android API 26 connected suite | PASS after visibility and P14 E2E additions | 82 passed, 0 skipped/failures |
| Dev-signed APK install/restart | PASS on API 26 emulator and physical Samsung SM-A556E | v2/v3 signature, install/background/battery-policy/process-death/restart verified without data clearing |
| CPU server image | PASS local build | local content image verified; no push |

## Mandatory scenario mapping

| Scenario | Executable coverage | State |
| --- | --- | --- |
| Standalone scan → library → playlist → playback → restart | P05/P07/P08 API 26 migration/library/playback/process tests | COMPOSITE PASS |
| Server registration → offline edit → sync → second device | P14 joined Android/OkHttp/FastAPI/PostgreSQL/second-Room run plus server persistence-count verification | PASS (single joined P14 flow) |
| Upload → crash/retry → dedup → Range → offline download | P06 real-PG/filesystem/runtime plus P08 Media3/download reconciliation tests | COMPOSITE PASS |
| Import fixture → ambiguous review → resolve → playlist order | P10 parser/checkpoint/real-PG/API 26 review tests | COMPOSITE PASS |
| Server unavailable during playback/edit → recovery | P07/P08 local-first plus P09 WorkManager/recovery tests | COMPOSITE PASS |
| Backup → isolated restore → bootstrap/integrity | P14 two-container production-adapter reconciliation drill plus P03 new-client bootstrap and canonical integrity tests | COMPOSITE PASS |
| GPU stopped/OOM while core remains | P12 forced handler OOM/process/CPU independence; real CUDA OOM deferred | DEFERRED_WITH_APPROVAL |

The offline-to-online acceptance row has one joined disposable execution through production Room,
`OkHttpSyncTransport`, FastAPI authentication/sync routes and PostgreSQL, followed by the second
Room projection and direct server-count verification. `adb reverse` supplies only the local test
wire; it does not claim a public/WAN deployment or physical second handset. The remaining mandatory
scenarios use their owning phase's integrated device/runtime evidence.
