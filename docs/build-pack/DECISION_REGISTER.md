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
| F-018 | A bound domain change + immutable Offline Journal event, or a standalone domain change + local mutation outbox, commits in one Room transaction; explicit materialization creates a new immutable event atomically per accepted ADR-018 |
| F-019 | Media3 owns playback and download execution/progress |
| F-020 | WorkManager owns durable deferred sync/metadata jobs |
| F-021 | No destructive migration fallback |
| F-022 | Direct/local play preferred; Vault stream fallback; transcode only when needed |
| F-023 | External providers are adapters, never trusted source of truth |
| F-024 | Only authorized/user-provided imports; no DRM bypass or secret scraping |

### P00-D004 companion clarification

ADR-019 was explicitly accepted by the user on 2026-08-16 as P00-D004 Variant A.
Server-verified exact SHA-256 plus byte size may deterministically re-reference one existing
`COMMITTED` VaultObject, one non-deleted `VALID` AudioVariant and one active canonical Recording
when the complete integrity and authorization-independent eligibility predicate passes. This is
byte-level CAS idempotency, not probabilistic `AUTO_MATCH`, merge or catalog mutation. Any
ambiguity, corruption, quarantine, unavailable replica or conflicting Recording fails closed.
P06 does not mutate owner/import projections. P10 records T4 as shadow evidence and permits only an
explicit reviewed owner projection through separate typed ImportEntry/UserTrackRef lineages. F-016
remains unchanged for every probabilistic path.

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
| Android P07 JSON/toolchain | AGP 9.1.0 + RFC 8785 Java canonicalizer 1.1 | ADR-020; exact-pin compatibility and full-gate change only |
| Android P08 playback/downloads | Media3 1.10.1 + Room v2 captured-session ownership + separate caches | ADR-021; Media3 owns execution/progress, exact-pin/full-gate changes only |
| P09 sync runtime | Per-event PostgreSQL commits, opaque owner/device/epoch cursors, durable materialized bootstrap, Room v7 profile ownership, bounded WorkManager drain, OkHttp 5.4.0 | ADR-022; immutable retry identity, no blind LWW, no destructive migration or payload-bearing work input |
| P10 import/identity review | Existing PostgreSQL import/identity/audit schema, all T0-T4 evaluations shadow-only, explicit typed manual projection, Room v8 offline review and provider-neutral adapters | ADR-023; F-016 stays disabled, no live-provider selection, no destructive migration or frozen-sync overload |
| P11 CPU recommendation/replay | Replaceable deterministic CPU baseline, immutable retained input/pipeline evidence, model-independent API, exact RAW_JSON offline packs and Room v9 presentation mapping | ADR-024; mandatory filters fail closed, expired personal snapshots purge boundedly, feedback stays on P04/P09, embeddings/GPU/final model remain P12-owned |
| P12 isolated GPU enrichment | Separate `gpu/` uv/image, deterministic NVIDIA auto/UUID/PCI/index selection, immutable approved-model/benchmark provenance, exact owner-safe pgvector and rollback-gated activation | ADR-025; CPU path never imports/installs GPU code, names are not selectors, HNSW is absent without measured need, no model becomes ACTIVE without a real approved RTX report |
| P13 Hybrid Wave | CPU modular-monolith `wave` schema, REST/PostgreSQL durable truth, disposable header-auth WebSocket hints, device-bound membership/preflight, Room v10 projection and Media3 monotonic execution | ADR-026; room code is only an invite-scoped locator, every device opens its own authorized source, no P2P/broker/GPU dependency, trusted-local single-API evidence only |

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

## 6. Standing technical-decision authorization

On 2026-08-16 the user authorized Codex to decide future analogous in-scope architecture,
persistence and frozen-contract conflicts autonomously. Codex must still record each resolution in
an ADR/change entry, follow the source-of-truth precedence, choose the smallest coherent resolution,
obtain independent review for critical/high-risk changes and preserve phase boundaries.

This standing authorization does not authorize destructive or irreversible operations on real/user
data, use of secrets/accounts/paid resources, external writes/publication/deployment, or selection
of an external provider/legal policy. Those actions still require explicit case-specific approval.
