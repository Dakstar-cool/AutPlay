# AutPlay RC1 observability and failure runbook

## Current executable surface

API and stream expose `/health/live` and Prometheus `/metrics`; API also exposes dependency-aware
`/health/ready`. GPU readiness never participates in core readiness. Current runtime metrics are:

- `autplay_http_requests_total{method,route,status}`;
- `autplay_http_request_duration_seconds{method,route}`;
- `autplay_readiness{component}`;
- `autplay_wave_timing_milliseconds{measurement}`;
- `autplay_wave_failures_total{kind}`.

Labels are bounded. Room/user/device/Recording identifiers, tokens, URLs, paths and payloads are
forbidden as metric labels. Structured logs use sanitized request IDs and the existing recursive
redaction boundary.

## Authenticated Web Admin health cards

The CPU process publishes its latest observation every five seconds in `jobs.worker_health`
(migration `0062_cpu_worker_health`). One row per personal server bounds retention. A new reporting
process fences older writers and their shutdown reports. No worker IDs, paths or job payloads reach
the browser. Identity must resolve to exactly one server; ambiguous identity leaves telemetry absent.

CPU utilization is the cgroup-v2 `cpu.stat` usage delta divided by elapsed time and the smaller of
`cpu.max` quota and effective CPU set. It covers the container and child processes, including media
tools. Memory uses `memory.current` / `memory.max` (container accounting, including cache). Missing
controllers or unlimited memory limits remain unknown; unsupported hosts never fabricate zero.
The independent reporter runs while a job is executing and while the worker is idle. Telemetry uses
an independent short-transaction pool; collection/publishing errors never change job/lease outcomes.

The dashboard refreshes health cards every ten seconds. A stopped process or heartbeat older than
thirty seconds is shown as no contact, with expired load values hidden. A browser unable to refresh
for thirty seconds also hides its previous readings. Queue/active-job counts are scoped to the
current account and CPU-supported job types. Idle describes process activity, not low CPU usage.

Vault warnings show counts of missing/corrupt/quarantined replicas, quarantined objects and the
current account's quarantined uploads. Both dashboard and Vault detail use the same warning rule.
These are persisted integrity observations, not a live disk capacity or mount probe.

## Minimum dashboard panels

| Panel | Query/measure | Alert |
| --- | --- | --- |
| API availability | request rate and non-4xx 5xx ratio | 5xx >1% for 5 min |
| API latency | p50/p95/p99 from HTTP histogram | p95 above endpoint SLO for 10 min |
| Dependency readiness | `min(autplay_readiness)` by component | required component 0 for 2 min |
| Wave command lag | p95 `command_lag` | >250 ms in trusted-local profile |
| Wave start skew/drift | p95 `start_skew`/`drift` | >150 ms / >100 ms |
| Wave failures | rate by bounded `buffer`/`rejoin` kind | sustained increase for 5 min |
| Backup | last verified generation and restore-drill age from operator record | age >24 h / drill >90 d |

Queue age/depth, Vault capacity/corruption, stream-start latency, sync cursor lag and GPU telemetry
remain required production panels when their collectors are wired. Their absence is visible in the
RC checklist and is not represented as a passing production monitoring deployment.

## Failure response

| Failure | Core behavior | Operator response |
| --- | --- | --- |
| PostgreSQL unavailable | Android local library/playback continues; server fails closed | restore DB, verify readiness and sync cursors before reconnect |
| Vault unavailable | metadata/local playback continue; server stream/ingest unavailable | keep objects logically present, restore mount/replica, reconcile |
| GPU/model unavailable | API/playback/CPU recommendations continue | keep ML pending; never activate unreviewed fallback weights |
| Provider/network unavailable | local/Vault content continues | bounded retry/circuit state; no secret scraping |
| Low storage | existing safe reads continue | pause ingest/download, reclaim only declared cache, alert |
| Worker restart | committed work persists | wait for lease expiry, verify fenced retry/checkpoint |
| Corrupt blob | other variants/catalog remain | quarantine, restore verified replica, no identity merge |

