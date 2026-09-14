# AutPlay RC1 performance report

## Current development snapshot supplement - 2026-09-10

Recovered API 26 connected evidence measures local FTS over 10,000 records at p50 10.4098 ms,
p95 11.5697 ms and p99 14.565 ms. The 1,000-entry playlist query measures p50 8.584 ms,
p95 11.0831 ms and p99 12.6886 ms. Both p95 values meet the 150 ms threshold. These are 30-sample
Room query measurements, not a new physical-device, Face rendering or whole-release qualification.
See the [dated audit reconciliation](ANDROID_AUDIT_RECONCILIATION_2026-09-10.md) and
[machine-readable measurements](evidence/android-audit-2026-09-10/ANDROID_PERFORMANCE.json).
The original RC measurements below remain historical evidence.

## Environment and method

- Evidence window: server benchmark 2026-08-17; final Android/release audit 2026-08-18.
- Host: Windows 11 `10.0.26200`, AMD64 Family 25 Model 80, Docker Engine 29.6.1.
- Database: PostgreSQL 18.4 plus pgvector 0.8.6 in a disposable Linux container.
- Android: API 26 x86_64 emulator `codex_p13_api26`, 1080×1920 at 420 dpi.
- Server fixture: 100,000 deterministic active Recordings with trigram-indexed normalized titles.
- Search sample: 120 deterministic indexed substring queries, limit 50. The first measured query
  is retained conservatively; no result was discarded as warm-up.

## Measured results

| Path | Dataset / samples | p50 | p95 | p99 | Target | Result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| PostgreSQL catalog search | 100,000 / 120 | 5.525 ms | 6.403 ms | 6.665 ms | p95 ≤300 ms | PASS |
| Android local FTS top 50 | 10,000 rows / 30 measured iterations | 9.397 ms | 12.555 ms | 13.254 ms | p95 ≤150 ms | PASS |
| Android playlist query | 1,000 entries / 30 measured iterations | 8.760 ms | 11.876 ms | 11.879 ms | p95 ≤150 ms | PASS |
| Wave command lag | deterministic three-session API 26 fixture | n/a | 220 ms | n/a | p95 ≤250 ms | PASS |
| Wave start skew | deterministic three-session API 26 fixture | n/a | 0 ms | n/a | p95 ≤150 ms | PASS |
| Wave drift after 10 s | deterministic three-session API 26 fixture | n/a | 91 ms | n/a | p95 ≤100 ms | PASS |

The table retains the measured release values. Android API 26 instrumentation uses one warm-up,
30 measured iterations, `SystemClock.elapsedRealtimeNanos` and nearest-rank percentiles.

## Boundaries

The measured large-fixture gates directly prove server metadata search and Android Room/FTS/
playlist bounds. API, sync, ingest, Range start, queue and recommendation retain
their bounded integration/property suites, but this run does not claim one production soak number
for every path. Physical Samsung A55, five concurrent LAN streams, 1,000,000 playlist entries and
real RTX throughput remain unmeasured; release documentation keeps those limits visible.
