# AutPlay Build Strategy

## 1. Почему не один prompt

AutPlay включает несколько независимых систем:

- local-first Android persistence;
- playback/download service;
- optional personal server;
- canonical catalog and Track identity;
- immutable Vault;
- sync and conflict recovery;
- import adapters;
- recommendation pipeline;
- optional GPU worker;
- Wave group playback.

Один prompt вынуждает Codex одновременно принимать десятки архитектурных решений, усложняет проверку и провоцирует premature abstractions. Правильная единица работы - один bounded vertical/infrastructure slice с явным exit gate.

## 2. Принцип последовательности

Каждая phase:

1. читает нормативные входы;
2. проверяет prerequisites предыдущей phase;
3. реализует ограниченный scope;
4. добавляет tests до расширения feature surface;
5. запускает project checks;
6. обновляет traceability и risk register;
7. создает handoff;
8. останавливается.

## 3. Что фиксируется заранее

Frozen decisions перечислены в `DECISION_REGISTER.md`. Они не пересматриваются ради удобства implementation.

Toolchain patch versions, конкретная Android dependency injection library и test runner могут уточняться в phase P00/P01, но решение документируется до широкого использования.

## 4. Почему backend начинается раньше UI

Server schema, identity и sync contracts влияют на IDs, tombstones, idempotency и migration compatibility. Если начать с UI screens, later persistence changes приведут к массовой переделке client state.

При этом Android foundation появляется до большинства server features, чтобы local-first assumptions проверялись реальным client code, а не только server model.

## 5. Vertical slices

После foundations функции добавляются end-to-end:

```text
domain rule
  -> server persistence/API when needed
  -> Android local transaction
  -> sync event when needed
  -> UI state/action
  -> contract/integration test
```

Не создавать пустые layers на десятки будущих features. Interface вводится, когда существует минимум один real implementation или test double с конкретным use case.

## 6. Test pyramid

| Уровень | Назначение |
| --- | --- |
| Pure unit | Domain policies, ordering, matching features, retry decisions |
| Persistence | PostgreSQL constraints/Alembic и Room migrations/transactions |
| Contract | OpenAPI, event schemas, compatibility fixtures |
| Integration | PostgreSQL, filesystem Vault, FFmpeg/Chromaprint, Media3 where practical |
| End-to-end | Android/emulator + server + Vault happy/failure paths |
| Performance | Large fixtures, streaming start, search, sync batch, ingest throughput |

Critical acceptance не может быть закрыт mocked test only.

## 7. Deployment strategy

Initial production topology остается modular monolith:

- `autplay-api`;
- `autplay-stream` при необходимости отдельного process boundary;
- `autplay-worker-cpu`;
- `autplay-ml-gpu` optional;
- PostgreSQL + pgvector;
- local/NAS filesystem Vault;
- reverse proxy/TLS outside core application.

Redis, broker, MinIO/S3 и external vector database не добавляются без benchmark/ADR.

## 8. Release slices

### Foundation release

Builds, migrations, health, local database, demo screen, no production feature promise.

### Local MVP

User can scan/import local audio, search library, play locally, manage playlists and preserve state after process death without server.

### Connected MVP

User can connect a personal server, upload/ingest into Vault, sync changes, stream via HTTP Range and recover from offline/retry cases.

### Smart MVP

Imports from supported user exports, identity review queue, CPU recommendation baseline and explainable home feed.

### Optional capabilities

GPU embeddings/enrichment and Wave only after core correctness and performance gates.

## 9. Scope-control rule

Если feature не нужен acceptance current phase, он не реализуется. Допустимы только:

- interface seam, необходимый текущему test;
- explicit TODO linked to a future phase;
- migration-compatible placeholder field already required by approved schema.

Запрещены speculative services, generic plugin frameworks без adapter, premature event bus и duplicate persistence models.

## 10. Завершение проекта

`P14` не означает, что продукт навсегда закончен. Он означает подтвержденный release candidate v1 с audit evidence. Новые providers, web client, collaborative editing и новые ML models оформляются отдельными post-v1 phases.
