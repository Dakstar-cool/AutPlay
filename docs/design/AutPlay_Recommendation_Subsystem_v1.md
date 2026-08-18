# AutPlay Recommendation Subsystem v1

**Status:** ARCHITECTURE BASELINE FOR P07-P12

**Serving baseline:** CPU-only, local-home-server compatible

**Optional accelerator:** NVIDIA RTX 3060 12 GB in the isolated P12 worker only

## 1. Scope

This specification defines the stable recommendation-domain boundaries, interaction data contract,
versioning and evaluation seams required before the first recommender is implemented. It preserves a
path from the P11 deterministic/content baseline to hybrid ranking, sequential candidate generation
and a later lightweight SONA-inspired generate-and-rank experiment.

It does not select or implement a final model, SONA-Lite, semantic IDs, a transformer, distributed
training, continuous online training, a separate vector database or a mandatory GPU serving path.
The public recommendation API MUST NOT expose tensors, model-framework types or a concrete model.

## 2. Invariants

- Candidate generation, mandatory filtering, scoring and reranking are separate stages.
- ACL, availability, identity-status, dislike and explicit-exclusion filters fail closed and cannot
  be bypassed by a candidate generator or ranker.
- A generated item is not an impression. An impression is recorded only after actual presentation.
- Organic and recommendation-origin interactions remain distinguishable.
- The P11 baseline works with no sequential model, no GPU and no persisted audio embedding.
- PostgreSQL remains the server metadata/event/job source of truth; pgvector exact search is the
  initial vector baseline. No separate vector store or HNSW is introduced without measured need.
- Embeddings are immutable derived evidence attached to canonical `Recording`, model version and
  source `AudioVariant`; model changes create parallel rows rather than overwriting existing rows.
- Scores are internal ranking values, not calibrated probabilities unless a later calibrated model
  explicitly proves that interpretation.
- Unknown persisted and additive wire values are preserved safely.

## 3. Domain model and ports

The pure recommendation domain owns these model-independent values:

- `RecommendationQuery`: user, surface, context, limit, exploration intent, seed and request schema
  version.
- `RecommendationSnapshotRef`: catalog/availability snapshot, user-interaction watermark and policy
  snapshot hash.
- `ComponentVersionRef`: component key, kind, version, config hash and optional artifact reference.
- `CandidateContribution`: source key/version, source rank, raw source score and bounded provenance.
- `Candidate`: canonical `recording_id` plus one or more contributions.
- `ScoredCandidate` and `RankedRecommendation`.
- `PipelineDefinition`: ordered candidate sources and budgets, filters, ranker/reranker,
  representation provider, versions and canonical config hashes.
- `PreparedUserRepresentation`: request-scoped opaque prepared state plus version metadata. It may be
  shared by a future generator and ranker without leaking tensors through the API.

The application boundary uses explicit ports:

```text
CandidateGenerator.generate(context, limit) -> CandidateBatch
CandidatePoolComposer.compose(batches, policy) -> CandidatePool
RecommendationFilter.apply(context, pool) -> FilterResult
Ranker.score(context, candidates) -> ScoredCandidates
Reranker.rerank(context, scored, limit) -> RankedRecommendations
UserRepresentationProvider.prepare(context, snapshot) -> PreparedUserRepresentation
RecommendationVersionRegistry.resolve(pipeline_ref) -> PipelineDefinition
RecommendationTraceRepository.save(request, items)
UserInteractionEventStore.append/read_snapshot
TrackEmbeddingReader.get/search_exact
TrackEmbeddingWriter.put
TrackEmbedder.embed
OfflineRecommendationEvaluator.evaluate
```

Read and write embedding capabilities are separate. P11 API/CPU serving receives only
`TrackEmbeddingReader`; the isolated P12 worker owns `TrackEmbedder` and `TrackEmbeddingWriter`.

## 4. Pipeline orchestration

`RecommendationService` resolves one immutable pipeline, loads one bounded history/catalog
snapshot, prepares the user representation once, runs configured candidate generators, composes and
deduplicates by canonical `Recording` while retaining every contribution, applies mandatory filters,
scores, reranks, persists the request and final items atomically, and returns a model-independent
response.

Optional candidate-source failure may degrade only according to the resolved pipeline manifest and
must be recorded. Mandatory filter failure fails closed. Ranker fallback is allowed only to an
explicitly versioned deterministic baseline, and the request records the component actually used.

The initial backend uses explicit preferences, logical listening history, artist/release/metadata
affinity, freshness, forgotten favorites and controlled exploration. Compatible persisted
embeddings may be another source; they are not required for service availability.

Future components plug into the same ports:

- `SequentialCandidateGenerator` implements `CandidateGenerator`.
- `SonaLiteUserRepresentationProvider` prepares one shared representation.
- `SonaLiteCandidateGenerator` and `SonaLiteRanker` consume that representation.
- A new immutable pipeline manifest activates them without changing the public API.

## 5. Canonical interaction and attribution

P04 defines the versioned wire schemas for logical listening, recommendation impression and direct
recommendation feedback events. P07-P09 implement local capture, sync and the append-only server
projection. Domain events for preference and playlist changes may carry optional recommendation
attribution; they MUST NOT create duplicate generic like/dislike/playlist feedback rows.

The canonical interaction projection is append-only and stores stable event identity, schema
version, user/device sequence, occurred/received time, interaction type, track/recording references,
origin/context, recommendation request/rank, `presentation_id`, displayed position, optional causal
impression, bounded playback metrics, explicit feedback, taste exclusion and bounded additive
properties.

`library.listening_event` remains the logical playback-session aggregate and
`library.user_track_preference` remains the mutable preference truth. A future
`library.user_interaction_event` projection is analytical/training history, not a second mutable
source of domain truth.

Recommendation attribution identifies the immutable request, generated item rank/recording,
surface and actual presentation. A causal impression link is optional only when no impression was
recorded; implementations never infer causality from recording identity or timestamp proximity.
Ownership and request/rank/recording consistency are validated without disclosing cross-user state.

P11 persists an Android-local presentation-to-impression mapping keyed by server profile/user,
presentation, recommendation request and source rank. Recomposition and process restart reuse its
stable event ID. P09 enforces the same owner-scoped semantic uniqueness on the canonical projection;
a different event ID for an already recorded presentation tuple is terminal
`IMPRESSION_ALREADY_RECORDED` and never creates a second impression.

Logical listening events use the existing PostgreSQL canonical vocabularies without remapping:
origins `ORGANIC`, `RECOMMENDED`, `PLAYLIST`, `SEARCH`, `WAVE`; contexts `GENERAL`, `WORKOUT`,
`CYCLING`, `WORK`, `SLEEP`, `PARTY`. A `RECOMMENDED` listen requires recommendation attribution.
Unknown future strings remain structurally parseable but are semantically rejected before the v1
projection until a compatible storage/version change exists.

## 6. Persistence and versioning

Existing `ml.embedding_model`, `ml.recording_embedding`, `ml.recommendation_request`,
`ml.recommendation_item`, `ml.taste_cluster` and `ml.offline_recommendation_pack` remain the
foundation. P11 adds only additive migrations required by its real implementation:

- immutable `ml.recommendation_pipeline_version` with pipeline key/version, implementation
  revision, request schema version, bounded canonical manifest, SHA-256 and lifecycle status;
- pipeline version, request canonicalization version, input snapshot hash, interaction watermark
  and catalog/availability snapshot reference on `ml.recommendation_request`;
- bounded multi-source candidate provenance on final `ml.recommendation_item` rows;
- append-only canonical interaction projection when P07-P09 have not already materialized it.

Existing string version fields remain during compatibility migration. A model/component reference
contains version and canonical config hash. No secret URL, raw user path, personal payload or model
feature vector belongs in a pipeline manifest or interaction event.

P12 reuses the existing embedding tables. A generic trainable-model artifact table is added only
when the first actual non-embedding trainable component requires it. Training runs, semantic IDs,
user encoder caches and SONA tensors are not persisted speculatively.

## 7. Reproducibility and replay

Every served request records the request schema/canonicalization version, resolved pipeline and
component versions/config hashes, seed, interaction watermark, catalog/availability snapshot and
the final ranked items. Versions alone are insufficient after user history or catalog mutation.

Two guarantees are distinct:

1. Exact response replay returns the persisted request/items within the declared retention window.
2. Algorithmic replay reruns the pipeline only when the manifest, seed and referenced input
   snapshots remain available. Otherwise it returns stable `REPLAY_INPUT_UNAVAILABLE`; it never
   silently substitutes current state.

Complete candidate pools are retained for reproducible evaluation runs and bounded sampled/shadow
traces, not necessarily for every production request.

## 8. Offline evaluation

`OfflineRecommendationEvaluator` runs the same pipeline runner against an immutable fixture or
dataset snapshot. Reports identify dataset/split/snapshot, event schema, pipeline/component/config
versions, seed, code revision and environment.

Candidate metrics include Recall@100/500/1000 and coverage. Ranking metrics include Precision@K,
Recall@K, NDCG@K, MRR, HitRate@K and, when applicable, pairwise accuracy. Product safeguards include
diversity, novelty, repeat rate, artist/genre concentration, skip/completion/like/dislike attribution
and latency. A metric report is reproducible from its manifest and must not treat recommended
listens as organic observations.

## 9. Phase ownership

| Phase | Ownership |
| --- | --- |
| P04 | Language-neutral interaction/impression/feedback schemas, attribution semantics, hash/idempotency vectors and persistence proposals only |
| P07 | Preference/playlist/history capture with domain mutation plus Offline Journal atomicity; no duplicate analytical event |
| P08 | One logical playback event, stable attribution through queue/restart and no progress/seek event flood |
| P09 | Specialized-schema dispatch, idempotent sync and atomic canonical interaction projection with ownership plus presentation-uniqueness checks |
| P11 | Domain interfaces, composable CPU candidate sources, filters/rankers, immutable pipeline registry, model-independent API, durable presentation/impression mapping, replay and offline evaluation |
| P12 | Isolated GPU embedding extraction, approved artifacts, parallel embedding versions, exact retrieval and RTX 3060/OOM evidence |
| Later explicit phase | Sequential training/inference, SONA-Lite, semantic IDs and shared-encoder model implementation |

## 10. Required compatibility evidence

- Replacing candidate generators leaves the public request/response and attribution contracts
  unchanged.
- Multiple sources deduplicate by `recording_id` while preserving contributions.
- Fixed pipeline manifest, seed, clock and input snapshots reproduce request hash and ranked output.
- Request/items commit atomically and impressions/feedback join idempotently.
- Cold start, empty history, absent embeddings and absent sequential components still run the CPU
  baseline.
- GPU/model absence never blocks interaction capture or P11 availability.
- Interaction schemas contain no CUDA/tensor/semantic-ID/model-feature field.
