# AutPlay MVP Acceptance Matrix

Status values: `NOT_STARTED`, `IN_PROGRESS`, `PASS`, `DEFERRED_WITH_APPROVAL`, `FAIL`.

| ID | Requirement | Phase | Required evidence | Status |
| --- | --- | --- | --- | --- |
| A-001 | Clean repository bootstrap | P01 | [`HANDOFF_P01.md`](../implementation/HANDOFF_P01.md) canonical and clean-index export smoke | PASS |
| A-002 | CPU-only server starts without CUDA | P03 | [`HANDOFF_P03.md`](../implementation/HANDOFF_P03.md) locked import/process tests, CPU dependency audit, and non-root runtime Compose smoke | PASS |
| A-003 | PostgreSQL clean upgrade/downgrade/upgrade | P02 | [`HANDOFF_P02.md`](../implementation/HANDOFF_P02.md) real PG18 lifecycle and equal head snapshots | PASS |
| A-004 | DB invariants match reference DDL | P02 | [`HANDOFF_P02.md`](../implementation/HANDOFF_P02.md) 57/53/13/40 inventory, zero drift and invariant suite | PASS |
| A-005 | Android DB fresh create/open/restart | P05 | [`HANDOFF_P05.md`](../implementation/HANDOFF_P05.md) API 26 fresh/open/restart, Activity recreation and standalone persistence evidence | PASS |
| A-006 | No destructive Room fallback | P05 | [`HANDOFF_P05.md`](../implementation/HANDOFF_P05.md) exact schema hashes, named migration/FK negatives, source audit and release/R8 gate | PASS |
| A-007 | Local mutation + Journal atomic | P05/P07 | [`HANDOFF_P05.md`](../implementation/HANDOFF_P05.md) API 26 commit/restart/rollback/retry/profile-lineage and composite-FK transaction evidence | PASS |
| A-008 | Offline library works without server | P07 | [`HANDOFF_P07.md`](../implementation/HANDOFF_P07.md) API 26 add/remove/restore/restart, preference/history/import and all-aggregate transaction evidence | PASS |
| A-009 | Duplicate Track entries preserved in playlist | P07 | [`HANDOFF_P07.md`](../implementation/HANDOFF_P07.md) duplicate persistence, random order/rebalance and 1,000-entry query evidence | PASS |
| A-010 | Local FTS rebuilds and handles Cyrillic/Latin input | P05/P07 | [`HANDOFF_P07.md`](../implementation/HANDOFF_P07.md) Cyrillic/Latin/transliteration/punctuation/rebuild/import and 10,000-row ranked-query evidence | PASS |
| A-011 | Vault commit immutable by SHA-256 | P06 | [`HANDOFF_P06.md`](../implementation/HANDOFF_P06.md) no-overwrite CAS, duplicate/concurrency/corruption, Linux seal/recovery and hostile-media evidence | PASS |
| A-012 | Ingest resumes safely after process failure | P06 | [`HANDOFF_P06.md`](../implementation/HANDOFF_P06.md) transaction-separated crash windows plus bounded TTL/missing/orphan/corrupt/paginated reconciliation | PASS |
| A-013 | HTTP Range streaming correct | P06 | [`HANDOFF_P06.md`](../implementation/HANDOFF_P06.md) 200/206/416, HEAD/ETag/If-Range, cancellation, integrity and owner-authorization evidence | PASS |
| A-014 | Local playback preferred | P08 | [`HANDOFF_P08.md`](../implementation/HANDOFF_P08.md) readable provider + resolver and real Media3 local preparation | PASS |
| A-015 | Vault fallback when local URI missing | P08 | [`HANDOFF_P08.md`](../implementation/HANDOFF_P08.md) revoked provider retains Track and selects fresh stable Vault reference | PASS |
| A-016 | Media3 owns durable download progress | P08 | [`HANDOFF_P08.md`](../implementation/HANDOFF_P08.md) DownloadService/DownloadIndex interruption, recreation, reconciliation and no-progress Room projection | PASS |
| A-017 | Queue restores after process death | P08 | [`HANDOFF_P08.md`](../implementation/HANDOFF_P08.md) Room v2 and two-stage API 26 adb force-stop/service restore evidence | PASS |
| A-018 | Sync push idempotent | P09 | [`HANDOFF_P09.md`](../implementation/HANDOFF_P09.md) duplicate/hash/lost-ACK and exactly-once interaction evidence | PASS |
| A-019 | Cursor never advances on partial apply | P09 | [`HANDOFF_P09.md`](../implementation/HANDOFF_P09.md) malformed/unknown/reordered page and atomic cursor evidence | PASS |
| A-020 | Tombstones retained through ACK window | P09 | [`HANDOFF_P09.md`](../implementation/HANDOFF_P09.md) delete/offline/bootstrap and real-PostgreSQL compaction evidence | PASS |
| A-021 | Dirty local edit not overwritten by pull | P09 | [`HANDOFF_P09.md`](../implementation/HANDOFF_P09.md) dirty-edit/delete conflict and profile-isolation evidence | PASS |
| A-022 | Bootstrap/reset preserves pending local intent | P09 | [`HANDOFF_P09.md`](../implementation/HANDOFF_P09.md) invalid-cursor, multi-page cutover and pending-Journal evidence | PASS |
| A-023 | User export import is resumable and auditable | P10 | [`HANDOFF_P10.md`](../implementation/HANDOFF_P10.md) golden parser/checkpoint/replay/report and Room restart evidence | PASS |
| A-024 | Ambiguous match never silently auto-merges | P10 | [`HANDOFF_P10.md`](../implementation/HANDOFF_P10.md) hard-negative, all-shadow and explicit-review evidence | PASS |
| A-025 | Fingerprint version/provenance persisted | P10 | [`HANDOFF_P10.md`](../implementation/HANDOFF_P10.md) versioned evidence/history and reprocessing tests | PASS |
| A-026 | Recommendation baseline reproducible | P11 | [`HANDOFF_P11.md`](../implementation/HANDOFF_P11.md) fixed seed/snapshot replay, immutable evaluator report and generator-swap evidence | PASS |
| A-027 | Availability/ACL filters applied before serving | P11 | [`HANDOFF_P11.md`](../implementation/HANDOFF_P11.md) fail-closed state matrix, owner isolation and cross-owner FK tests | PASS |
| A-028 | Offline recommendation pack verified by hash/version | P11 | [`HANDOFF_P11.md`](../implementation/HANDOFF_P11.md) exact-byte tamper/version/expiry/owner tests and Room v9 device evidence | PASS |
| A-029 | GPU worker isolated | P12 | [`HANDOFF_P12.md`](../implementation/HANDOFF_P12.md) separate lock/image, CPU dependency audit and runtime/GPU Compose service-set evidence | PASS |
| A-030 | GPU OOM degrades without core outage | P12 | [`ADR-027`](../adr/ADR-027-p14-conditional-phase-reachability.md) and [`HANDOFF_P12.md`](../implementation/HANDOFF_P12.md): bounded handler/restart/CPU independence pass; real CUDA OOM/RTX/model metrics explicitly deferred and no model activated | DEFERRED_WITH_APPROVAL |
| A-031 | Model changes create parallel versioned embeddings | P12 | [`HANDOFF_P12.md`](../implementation/HANDOFF_P12.md) Alembic `0014`, real-PG parallel rows/hash/dimension/fence/ACL/switch/rollback test | PASS |
| A-032 | Wave preflight catches unavailable media | P13 | [`HANDOFF_P13.md`](../implementation/HANDOFF_P13.md) real-PG invite/device ACL plus strict LOCAL/Vault/unavailable participant gate and deterministic three-session API 26 fixture | PASS |
| A-033 | Wave clock/degraded behavior bounded | P13 | [`P13_WAVE_TIMING_2026-08-17.json`](../implementation/evidence/P13_WAVE_TIMING_2026-08-17.json), reconnect/reorder/gap/revoke tests and monotonic Media3 schedule/drift policy | PASS |
| A-034 | Backup restore drill succeeds | P14 | [`P14_BACKUP_RESTORE_2026-08-17.json`](../implementation/evidence/P14_BACKUP_RESTORE_2026-08-17.json): two isolated PostgreSQL/Vault generations; production filesystem adapter; healthy and corrupt APPLY reconciliation | PASS |
| A-035 | Secrets/private URLs absent from logs/export | P14 | [`SECURITY_REVIEW.md`](../release/SECURITY_REVIEW.md), zero-finding production-source scan and 66-test targeted redaction/security suite | PASS |
| A-036 | Object authorization prevents cross-user access | P14 | [`SECURITY_REVIEW.md`](../release/SECURITY_REVIEW.md), targeted API/stream/Vault/library/import/recommendation/Wave owner-negative suite and canonical real-PG cases | PASS |
| A-037 | Large fixture meets documented p95 targets | P14 | [`PERFORMANCE_REPORT.md`](../release/PERFORMANCE_REPORT.md): named host, 100,000-row/120-query PostgreSQL p50/p95/p99 `5.525/6.403/6.665 ms`; API 26 10,000-row FTS `9.397/12.555/13.254 ms` and 1,000-entry Room playlist `8.760/11.876/11.879 ms`; all p95 targets pass | PASS |
| A-038 | Android release build installs on Samsung A55 | P14 | [`P14_RELEASE_BUILD.json`](../implementation/evidence/P14_RELEASE_BUILD.json) and `P14_ANDROID_DEVICE_SMOKE.json`: dev-signed v2/v3 RC APK installs on physical Samsung SM-A556E, backgrounds without a battery-optimization bypass, survives the expected force-stop process-death boundary and restarts; user data was not cleared | PASS |
| A-039 | Full end-to-end offline-to-online flow passes | P14 | [`P14_ANDROID_SERVER_E2E_2026-08-17.json`](../implementation/evidence/P14_ANDROID_SERVER_E2E_2026-08-17.json): one joined file-backed Room → production `OkHttpSyncTransport` → FastAPI HTTP/auth → PostgreSQL 18.4 → second Android Room run; post-commit ACK loss/retry leaves exactly one inbox, sync event and user projection | PASS |
| A-040 | Release artifacts use pinned versions/digests | P14 | [`P14_RELEASE_INVENTORY.json`](../implementation/evidence/P14_RELEASE_INVENTORY.json), three CycloneDX SBOMs, exact lock/Gradle/Compose/image/APK hashes and zero-vulnerability OSV reports | PASS |

`PASS` требует path/link на evidence. Текстовое утверждение без test/report не является PASS.
