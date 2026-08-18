# AutPlay RC1 checklist

## Release verdict

`LOCAL_RC_PASS`. The CPU/local-first candidate is buildable and its security, restore, dependency,
secret, API 26, large-search and physical Samsung A55 gates are green. This is evidence for the
declared local RC boundary, not authorization to publish or deploy. No push, deployment,
publication or production signing is authorized by this checklist.

## Included

- Android local-first library/search/playlists/import review/playback/download/sync status/Home.
- Optional CPU personal server with authenticated sessions, PostgreSQL metadata/jobs/sync/import,
  immutable filesystem Vault, Range streaming and deterministic CPU recommendations.
- Trusted-local, single-API-process Wave boundary from P13.
- Named Alembic/Room migrations through PostgreSQL `0015` and Room v10.

## Explicitly deferred/excluded

- Real GPU model activation and A-030 CUDA OOM/RTX throughput/VRAM/quality evidence:
  `DEFERRED_WITH_APPROVAL` under ADR-027. CPU serving remains authoritative.
- Public domain/TLS/reverse proxy, cross-instance WebSocket fanout, public registration,
  production backup target/retention, live external providers and app-store policy.
- Production signing key, publication, deployment and pushed images.

## Gate table

| Gate | State |
| --- | --- |
| Canonical static/unit/contract/real-PG suite | PASS: root 85, contracts 53, server 425 + 1 documented Windows symlink skip; Android lint/unit/release/R8 PASS |
| Full API 26 connected suite | PASS, 82/82 |
| Joined Android/FastAPI/PostgreSQL sync E2E | PASS, isolated disposable run |
| Restore/Vault consistency/corruption drill | PASS |
| Secret/redaction/object authorization review | PASS |
| SBOM, resolved license metadata and OSV vulnerability inventory | PASS; publication obligations declared |
| 100k server search p50/p95/p99 | PASS |
| API 26 Room/FTS p50/p95/p99 | PASS; both p95 values below 13 ms against 150 ms target |
| Privacy/export/delete and disaster-recovery runbooks | PASS; no real-data deletion or export executed |
| Dev-signed RC APK and CPU image build | PASS locally |
| Physical Samsung A55 install/background/process smoke | PASS: physical SM-A556E, arm64-v8a, SDK 36; install/background/battery-policy/process-death/restart; no data clearing |
| External publication/deployment | NOT AUTHORIZED |
