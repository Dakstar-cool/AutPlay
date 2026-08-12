# P14 - Security, Recovery, Performance and Release Candidate

Выполни только phase P14. Следуй common protocol and read all phase handoffs plus current acceptance matrix.

## Цель

Довести реализованный scope до evidence-backed release candidate without deployment/publication. Закрыть security, migration, backup/restore, observability, performance, privacy and end-to-end gates.

## Inputs

- Full design package and accepted ADRs
- `MVP_ACCEPTANCE_MATRIX.md`
- `RISK_REGISTER.md`, `TRACEABILITY.md`, `VERSIONS.md`
- P00-P13 handoffs
- production/deployment/security/backup sections of specification and architecture

## Scope

1. Run full static/unit/persistence/contract/integration/instrumentation/e2e suites.
2. Threat-model review: auth, object authorization, upload, source adapter, Vault, shell tools, GPU model supply chain, logs and admin operations.
3. Dependency/license/vulnerability inventory and SBOM where toolchain supports it.
4. Secret scanning and release log/export redaction checks.
5. PostgreSQL backup plus isolated restore drill and integrity validation.
6. Vault backup/replica/checksum/reconciliation drill; DB and bytes consistency.
7. Android backup/restore policy and revoked URI/device credential behavior.
8. Upgrade tests from every supported Alembic/Room version fixture.
9. Large-fixture performance: API, DB, search, sync, ingest, Range start, Room, FTS, queue and recommendation.
10. Failure matrix: DB/Vault/GPU/provider/network/storage/process restart.
11. Observability dashboards/runbooks for critical metrics and alerts.
12. Release builds, container digests, version manifest and reproducibility evidence.
13. Samsung A55 and minimum-SDK smoke, battery/background behavior and process death.
14. Final operator/user documentation, privacy/delete/export and disaster recovery runbook.

## Constraints

- Do not publish, sign with real production key, push images or deploy externally.
- Do not mark unexecuted test as PASS.
- P12/P13 may be `DEFERRED_WITH_APPROVAL`, never silently treated as implemented.
- No late architectural rewrite hidden inside hardening.
- Critical data-loss/security failure blocks RC.

## Mandatory end-to-end scenarios

1. Standalone Android local scan -> library -> playlist -> playback -> restart.
2. Connect personal server -> device registration -> offline edit -> sync -> second-device projection.
3. Upload -> crash/retry -> Vault dedup -> Range playback -> offline download.
4. Import export fixture -> ambiguous review -> resolved item -> playlist order preserved.
5. Server unavailable during playback/edit -> later recovery without loss.
6. Backup -> isolated restore -> new client bootstrap -> integrity verification.
7. GPU stopped/OOM while core server and playback remain available.

## Acceptance

- Every applicable A-001..A-040 row has evidence path and PASS or explicit approved deferral.
- No open critical/high data-loss or object-authorization defect.
- Builds/migrations/restore are reproducible from clean checkout.
- Performance report names hardware, dataset, method and p50/p95/p99.
- Release notes state included/deferred features honestly.

Create:

- `docs/implementation/HANDOFF_P14.md`;
- `docs/release/RC1_CHECKLIST.md`;
- `docs/release/TEST_EVIDENCE.md`;
- `docs/release/SECURITY_REVIEW.md`;
- `docs/release/PERFORMANCE_REPORT.md`;
- `docs/operations/BACKUP_RESTORE.md`;
- final updated matrices.

Stop after local RC evidence. Wait for explicit user authorization before push, deployment, signing or publication.
