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

## Scope

1. Versioned recommendation request/context and model/rule registry.
2. CPU candidate generators using explicit preferences, history, artist/release/metadata affinity, freshness and controlled exploration.
3. Mandatory availability, object authorization, disliked/excluded and identity-status filters.
4. Deterministic scoring and diversity/repeat control.
5. Explanation/reason codes and provenance for each item.
6. Home feed: recent releases from relevant artists plus bounded recommendation sections.
7. Offline recommendation pack generation, hash/version/expiry and Android consumption.
8. Offline lightweight rerank based on fresh Like/Dislike/Skip without pretending server ML.
9. Evaluation dataset/split and report with relevance, coverage, diversity, novelty, repeat rate and latency.
10. Shadow/feature flag mechanism for later GPU/model comparison.

## Constraints

- No recommendation may bypass ACL/availability.
- Do not label heuristic score as calibrated probability.
- Avoid feedback loop where recommended listens are indistinguishable from organic listens.
- Dislike/explicit exclusion has deterministic precedence.
- No HNSW before benchmark need.
- GPU failure or absence has zero effect on baseline availability.

## Required tests

- cold-start and empty-history user;
- all tracks unavailable/disliked/excluded;
- deterministic output for fixed seed/snapshot;
- diversity and max-repeat constraints;
- organic vs recommendation-origin event attribution;
- stale/tampered/unknown-version offline pack;
- cross-user data isolation;
- exact query latency on representative fixture;
- evaluation report reproducibility.

## Acceptance

CPU-only server returns explainable, filtered recommendations and a usable home/offline feed with recorded baseline metrics suitable for comparing P12 models.

Create `HANDOFF_P11.md`, update A-026..A-028 and stop.
