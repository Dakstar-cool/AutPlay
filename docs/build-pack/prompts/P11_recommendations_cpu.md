# P11 - CPU Recommendation Baseline and Home Feed

Выполни только phase P11. Следуй common protocol и прочитай `HANDOFF_P10.md`.

## Цель

Создать воспроизводимую recommendation baseline, работающую без GPU: candidate generation, filters, scoring/reranking, explanations, home feed and offline pack.

## Inputs

- Product taste/home/Wave recommendation requirements
- System Architecture recommendation stages
- PostgreSQL ML tables and Room recommendation pack
- Listening/history/preferences from P07-P09
- Track Identity status and availability rules
- pgvector reference notes, но vector index пока не обязателен
- `docs/design/AutPlay_Recommendation_Subsystem_v1.md`
- P04 user-interaction/impression contracts and P09 canonical interaction projection

## Scope

1. Model-independent `RecommendationService` and explicit domain ports for candidate generation,
   pool composition, mandatory filters, ranking, reranking, user representation, version registry,
   trace persistence, embedding reads and offline evaluation.
2. Immutable pipeline/request/context/component/config version registry with seed, interaction
   watermark, catalog/availability snapshot and stable replay behavior.
3. Composable CPU candidate generators using explicit preferences, history,
   artist/release/metadata affinity, freshness and controlled exploration. The first backend is one
   interchangeable implementation; `SequentialCandidateGenerator` remains an unregistered future
   implementation.
4. Mandatory availability, object authorization, disliked/excluded and identity-status filters.
5. Deterministic scoring and diversity/repeat control.
6. Explanation/reason codes and complete multi-source provenance for each item.
7. Home feed: recent releases from relevant artists plus bounded recommendation sections.
8. Offline recommendation pack generation, hash/version/expiry and Android consumption. Pack items
   retain original request ID/rank plus pack ID; local rerank changes display position, not source rank.
9. Offline lightweight rerank based on fresh Like/Dislike/Skip without pretending server ML.
10. Actual-impression and subsequent-feedback ingestion through the P04/P09 event path. API delivery
    is not an impression and there is no parallel feedback REST path.
    Persist an Android-local owner/profile-scoped presentation-to-impression mapping before
    journaling so recomposition and restart reuse one stable event ID.
11. Offline evaluation interface using the same pipeline runner and an immutable dataset/split,
    reporting Recall/Precision/NDCG/MRR/HitRate, coverage, diversity, novelty, repeat and latency with
    all dataset/model/config/snapshot versions.
12. Shadow/feature flag mechanism for later GPU/model comparison.

## Constraints

- No recommendation may bypass ACL/availability.
- Do not label heuristic score as calibrated probability.
- Avoid feedback loop where recommended listens are indistinguishable from organic listens.
- Dislike/explicit exclusion has deterministic precedence.
- No HNSW before benchmark need.
- GPU failure or absence has zero effect on baseline availability.
- Recommendation API/domain types contain no model-framework, CUDA, tensor or semantic-ID type.
- Baseline must work with no embedding, no sequential component and no GPU.
- Exact response replay and algorithmic replay are distinct; unavailable retained inputs return
  `REPLAY_INPUT_UNAVAILABLE` rather than silently using current state.

## Required tests

- cold-start and empty-history user;
- all tracks unavailable/disliked/excluded;
- deterministic output for fixed seed/snapshot;
- diversity and max-repeat constraints;
- organic vs recommendation-origin event attribution;
- swapping candidate generators without changing OpenAPI/DTOs;
- multi-source deduplication by Recording while preserving every contribution;
- request/item atomic persistence and request -> impression -> feedback joins;
- same presentation across recomposition/restart reuses one impression ID; a different UUID cannot
  create a second server projection;
- fixed pipeline/config/seed/history/catalog snapshots reproduce request hash and ranked output;
- cold start with no embedding and no registered sequential generator;
- stale/tampered/unknown-version offline pack;
- cross-user data isolation;
- exact query latency on representative fixture;
- evaluation report reproducibility.

## Acceptance

CPU-only server returns explainable, filtered recommendations through a model-independent API and a
usable home/offline feed. The initial backend is replaceable without API changes, requests are
replayable under the declared snapshot contract, interaction attribution is complete, and recorded
baseline metrics are suitable for comparing P12 or later sequential models.

Create `HANDOFF_P11.md`, update A-026..A-028 and stop.
