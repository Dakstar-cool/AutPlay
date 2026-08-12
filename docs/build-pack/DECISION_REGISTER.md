# AutPlay Decision Register

## 1. Frozen decisions

Эти решения нельзя менять без отдельного ADR и подтверждения пользователя.

| ID | Решение |
| --- | --- |
| F-001 | Product name: AutPlay |
| F-002 | Android-first, local-first; standalone mode является полноценным |
| F-003 | Personal server optional; local playback/library не зависят от server availability |
| F-004 | VPN является отдельным соседним сервисом и не входит в AutPlay |
| F-005 | Production server Linux x86_64; CPU core cross-platform where practical |
| F-006 | RTX 3060 12 GB доступна только isolated optional ML worker |
| F-007 | Modular monolith before microservices |
| F-008 | PostgreSQL является metadata, sync and job source of truth |
| F-009 | Initial job queue: PostgreSQL `FOR UPDATE SKIP LOCKED`, lease and heartbeat |
| F-010 | No mandatory Redis/RabbitMQ/NATS/Kafka |
| F-011 | No separate vector database in v1; pgvector exact baseline first |
| F-012 | Vault bytes immutable and addressed by verified SHA-256; DB stores metadata only |
| F-013 | `VaultObject`, `AudioVariant`, `Recording`, `ReleaseTrack`, `UserTrackRef` are distinct |
| F-014 | Fingerprint is versioned evidence, not identity primary key |
| F-015 | False Recording merge is worse than unresolved match |
| F-016 | Auto-match disabled until labeled benchmark/calibration gate passes |
| F-017 | Android local aggregate ID never changes after server ACK/merge |
| F-018 | Domain change + Offline Journal event commit in one Room transaction |
| F-019 | Media3 owns playback and download execution/progress |
| F-020 | WorkManager owns durable deferred sync/metadata jobs |
| F-021 | No destructive migration fallback |
| F-022 | Direct/local play preferred; Vault stream fallback; transcode only when needed |
| F-023 | External providers are adapters, never trusted source of truth |
| F-024 | Only authorized/user-provided imports; no DRM bypass or secret scraping |

## 2. Accepted technical baselines

| Area | Baseline | Change rule |
| --- | --- | --- |
| Server language | Python + `uv` | Major runtime change requires ADR |
| API | FastAPI | Framework replacement requires ADR |
| ORM/migrations | SQLAlchemy 2 typed + Alembic | Preserve physical schema contracts |
| Database | PostgreSQL 18.x + pgvector 0.8 compatible range | Patch updates via validation |
| Android | Kotlin + Jetpack Compose | Native Android remains first client |
| Local DB | Room 3.0.1 baseline + BundledSQLiteDriver | New patch after compatibility gate |
| Playback | AndroidX Media3 | DownloadIndex remains progress truth |
| Deferred work | WorkManager | Do not duplicate Media3 downloads |
| Vault v1 | Local/NAS filesystem adapter | S3/WebDAV later behind port |
| Media tools | Pinned FFmpeg + Chromaprint/fpcalc | Invoke without shell interpolation |

## 3. Decisions owned by P00/P01

Codex должен исследовать compatibility и записать ADR, не создавая бесконечный choice discussion.

| Decision | Preferred direction | Required evidence |
| --- | --- | --- |
| Exact Python/JDK/Kotlin/AGP versions | Current compatible stable set | Clean build on supported environment |
| Python PostgreSQL driver | Psycopg 3 preferred | Async/sync migration and integration test |
| Android DI | Hilt preferred; manual DI acceptable for tiny bootstrap | KSP/toolchain compatibility and testability |
| HTTP client | Retrofit + OkHttp preferred | Streaming/auth/error behavior |
| JSON | Kotlinx serialization preferred | Unknown/additive field compatibility |
| Server configuration | Pydantic settings or small typed equivalent | Env/file precedence and secret redaction tests |
| Structured logging | Minimal maintained solution | JSON output and redaction tests |
| Test PostgreSQL | Disposable container/task | CI reproducibility without real data |
| CI platform | GitHub Actions if repository is GitHub-bound | Local canonical commands remain source of truth |

## 4. Product choices intentionally deferred

Codex не должен угадывать:

- public domain and TLS/reverse-proxy provider;
- exact backup destination and retention budget;
- which external music services receive first-party adapters;
- whether public multi-user registration is allowed;
- final recommendation model;
- Wave operation over public Internet versus trusted LAN/VPN;
- app-store publishing account and signing keys;
- legal policy for source acquisition in target jurisdiction.

Implement ports/config seams only when current phase requires them.

## 5. Conflict handling

При конфликте с repository state:

1. cite exact files/lines/constraints;
2. classify as implementation defect, stale document or product conflict;
3. propose smallest compatible resolution;
4. do not change frozen decision silently;
5. record accepted resolution in ADR and traceability.
