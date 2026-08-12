# AutPlay Phase Index

Выполнять строго по порядку. Повторный запуск phase сначала читает ее последний handoff и не повторяет уже подтвержденную работу.

| Phase | Название | Главный результат | Exit gate |
| --- | --- | --- | --- |
| P00 | Repository intake | `AGENTS.md`, plan, progress, decisions, risks, normalized docs | Repository state понятен, no feature code |
| P01 | Monorepo foundation | Server/Android/contracts/deploy skeleton, pinned toolchains, CI smoke | Clean bootstrap на поддерживаемой машине |
| P02 | PostgreSQL persistence | Alembic head + typed mappings + invariant tests | Upgrade/downgrade/upgrade and schema tests green |
| P03 | Server runtime | Config, health, logging, auth/device session, job worker skeleton | API/worker start CPU-only, auth/job tests green |
| P04 | Sync contract | Versioned push/ACK/pull/bootstrap/conflict specification and fixtures | Server/client contract tests share golden vectors |
| P05 | Android foundation | Compose shell, Room v1, repositories, Offline Journal transaction skeleton | Fresh/open/restart/migration/FTS tests green |
| P06 | Vault and streaming | Resumable staging, hash/validate/commit, Range streaming | Crash-safe ingest and byte-range tests green |
| P07 | Library vertical slice | Library, playlist, history, local search across domain/server/client | Offline create/edit/restart flows green |
| P08 | Playback and downloads | Media3 service, queue restore, LOCAL/Vault selection, downloads | Local and server playback survive process/network cases |
| P09 | End-to-end sync | Journal push, ACK, pull, cursor, tombstone, conflict UI/state | Duplicate/reorder/offline/bootstrap scenarios green |
| P10 | Import and identity | Export parsers, provider ports, matching, review queue, provenance | Golden imports never silently false-merge |
| P11 | Recommendation baseline | CPU candidates/ranker, home feed, offline pack, evaluation report | Quality and repeat/diversity baselines recorded |
| P12 | GPU enrichment | Isolated RTX 3060 worker, model registry, versioned embeddings | CPU readiness independent, OOM/retry and benchmark green |
| P13 | Wave | Room lifecycle, preflight/prefetch, clock sync and degraded behavior | Multi-device timing/failure test evidence |
| P14 | Hardening and release | Security, backup/restore, load, observability, packaging, RC checklist | `MVP_ACCEPTANCE_MATRIX` closed with evidence |

## Dependency graph

```text
P00 -> P01 -> P02 -> P03 -> P04 -> P05
P05 -> P06 -> P07 -> P08 -> P09 -> P10 -> P11
P11 -> P12
P09 + P08 -> P13
P12 + P13 -> P14
```

P12 и P13 могут быть deferred для первого connected MVP. P14 должен явно отметить deferred capabilities и не выдавать их за готовые.

## Prompt locations

```text
docs/build-pack/prompts/P00_repository_intake.md
docs/build-pack/prompts/P01_monorepo_foundation.md
docs/build-pack/prompts/P02_postgresql_persistence.md
docs/build-pack/prompts/P03_server_runtime.md
docs/build-pack/prompts/P04_sync_contract.md
docs/build-pack/prompts/P05_android_foundation.md
docs/build-pack/prompts/P06_vault_streaming.md
docs/build-pack/prompts/P07_library_vertical_slice.md
docs/build-pack/prompts/P08_playback_downloads.md
docs/build-pack/prompts/P09_sync_end_to_end.md
docs/build-pack/prompts/P10_import_identity.md
docs/build-pack/prompts/P11_recommendations_cpu.md
docs/build-pack/prompts/P12_gpu_enrichment.md
docs/build-pack/prompts/P13_wave.md
docs/build-pack/prompts/P14_hardening_release.md
```
