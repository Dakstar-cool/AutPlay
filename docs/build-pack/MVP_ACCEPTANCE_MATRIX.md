# AutPlay MVP Acceptance Matrix

Status values: `NOT_STARTED`, `IN_PROGRESS`, `PASS`, `DEFERRED_WITH_APPROVAL`, `FAIL`.

| ID | Requirement | Phase | Required evidence | Status |
| --- | --- | --- | --- | --- |
| A-001 | Clean repository bootstrap | P01 | [`HANDOFF_P01.md`](../implementation/HANDOFF_P01.md) canonical and clean-index export smoke | PASS |
| A-002 | CPU-only server starts without CUDA | P03 | Import/start integration test | NOT_STARTED |
| A-003 | PostgreSQL clean upgrade/downgrade/upgrade | P02 | Real PG18 migration logs/tests | NOT_STARTED |
| A-004 | DB invariants match reference DDL | P02 | Constraint/object inventory tests | NOT_STARTED |
| A-005 | Android DB fresh create/open/restart | P05 | Instrumentation test | NOT_STARTED |
| A-006 | No destructive Room fallback | P05 | Configuration/static check | NOT_STARTED |
| A-007 | Local mutation + Journal atomic | P05/P07 | Failure-injection transaction test | NOT_STARTED |
| A-008 | Offline library works without server | P07 | Airplane-mode scenario | NOT_STARTED |
| A-009 | Duplicate Track entries preserved in playlist | P07 | Persistence/order test | NOT_STARTED |
| A-010 | Local FTS rebuilds and handles Cyrillic/Latin input | P05/P07 | FTS fixtures and rebuild test | NOT_STARTED |
| A-011 | Vault commit immutable by SHA-256 | P06 | Duplicate/corruption/failure tests | NOT_STARTED |
| A-012 | Ingest resumes safely after process failure | P06 | Checkpoint/failure-injection test | NOT_STARTED |
| A-013 | HTTP Range streaming correct | P06 | 200/206/416 and authorization tests | NOT_STARTED |
| A-014 | Local playback preferred | P08 | Source-selection integration test | NOT_STARTED |
| A-015 | Vault fallback when local URI missing | P08 | Revoked/missing URI scenario | NOT_STARTED |
| A-016 | Media3 owns durable download progress | P08 | Reconciliation/process-death test | NOT_STARTED |
| A-017 | Queue restores after process death | P08 | Instrumentation test | NOT_STARTED |
| A-018 | Sync push idempotent | P09 | Duplicate event/hash vectors | NOT_STARTED |
| A-019 | Cursor never advances on partial apply | P09 | Batch failure test | NOT_STARTED |
| A-020 | Tombstones retained through ACK window | P09 | Delete/offline/resync test | NOT_STARTED |
| A-021 | Dirty local edit not overwritten by pull | P09 | Conflict vector | NOT_STARTED |
| A-022 | Bootstrap/reset preserves pending local intent | P09 | Invalid cursor/rebootstrap test | NOT_STARTED |
| A-023 | User export import is resumable and auditable | P10 | Golden fixture report | NOT_STARTED |
| A-024 | Ambiguous match never silently auto-merges | P10 | Hard-negative dataset | NOT_STARTED |
| A-025 | Fingerprint version/provenance persisted | P10 | Persistence and reprocessing test | NOT_STARTED |
| A-026 | Recommendation baseline reproducible | P11 | Fixed dataset metrics/report | NOT_STARTED |
| A-027 | Availability/ACL filters applied before serving | P11 | Negative authorization tests | NOT_STARTED |
| A-028 | Offline recommendation pack verified by hash/version | P11 | Tamper/expiry tests | NOT_STARTED |
| A-029 | GPU worker isolated | P12 | CPU/API no CUDA imports and Compose profiles | NOT_STARTED |
| A-030 | GPU OOM degrades without core outage | P12 | Forced OOM/retry terminal test | NOT_STARTED |
| A-031 | Model changes create parallel versioned embeddings | P12 | Migration/switch/rollback test | NOT_STARTED |
| A-032 | Wave preflight catches unavailable media | P13 | Multi-device fixture | NOT_STARTED |
| A-033 | Wave clock/degraded behavior bounded | P13 | Timing and disconnect evidence | NOT_STARTED |
| A-034 | Backup restore drill succeeds | P14 | Isolated restore and checksums | NOT_STARTED |
| A-035 | Secrets/private URLs absent from logs/export | P14 | Redaction/security tests | NOT_STARTED |
| A-036 | Object authorization prevents cross-user access | P14 | API negative suite | NOT_STARTED |
| A-037 | Large fixture meets documented p95 targets | P14 | Named hardware benchmark report | NOT_STARTED |
| A-038 | Android release build installs on Samsung A55 | P14 | Signed/dev release smoke evidence | NOT_STARTED |
| A-039 | Full end-to-end offline-to-online flow passes | P14 | Scenario log and artifacts | NOT_STARTED |
| A-040 | Release artifacts use pinned versions/digests | P14 | SBOM/version manifest | NOT_STARTED |

`PASS` требует path/link на evidence. Текстовое утверждение без test/report не является PASS.
