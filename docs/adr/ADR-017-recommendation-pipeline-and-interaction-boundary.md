# ADR-017: Recommendation Pipeline and Interaction Boundary

- Status: Accepted
- Date: 2026-08-16
- Decision owner: user-approved P04 scope amendment

## Context

AutPlay plans a simple CPU recommendation baseline in P11 and optional GPU embeddings in P12, but
the initial architecture must not lock the API to one content model or miss the interaction and
impression data required by later hybrid, sequential or SONA-Lite experiments. Capturing those facts
only in P11 would lose P07/P08 first-use behavior. Treating generated recommendation items as
impressions would also corrupt feedback attribution.

The repository already contains versioned embedding, recommendation request/item, logical listening
and offline-pack persistence foundations. It does not yet have a canonical actual-impression wire
contract or explicit recommendation-domain ports.

## Decision

1. P04 owns versioned canonical listening/impression/direct-feedback schemas, attribution semantics,
   golden vectors and persistence proposals only. It implements no engine or projection.
2. P07/P08 capture owning preference/playlist/playback events locally with one Offline Journal event
   in the same Room transaction. They do not emit duplicate generic analytical events.
3. P09 validates known events in two stages and atomically projects the canonical append-only
   interaction history with sync inbox/domain/ACK state.
4. P11 exposes a model-independent `RecommendationService` composed from separate candidate,
   filtering, ranking, reranking, representation, trace, version and evaluation ports. Its CPU
   baseline works without embeddings, a sequential model or GPU.
5. P12 owns isolated embedding generation/writes and model artifacts. The CPU API reads only
   compatible persisted embeddings and remains available when GPU/model work fails.
6. A future `SequentialCandidateGenerator` and SONA-Lite generator/ranker implement the same ports
   and activate through immutable pipeline manifests without changing the public API or interaction
   schema.
7. Reproducibility records pipeline/component/config versions, seed and immutable input snapshot
   references. Persisted-response replay and algorithmic replay are distinct guarantees.

The normative architecture details are in `docs/design/AutPlay_Recommendation_Subsystem_v1.md`.

## Consequences

- Interaction data needed for later training is captured from the first P07/P08 use.
- Recommendation delivery is not misclassified as exposure; actual impression and subsequent
  feedback remain joinable and idempotent.
- Durable client presentation mapping plus server semantic uniqueness prevents duplicate impressions
  even when a buggy/restarted client attempts a new event ID.
- The public API and interaction data are independent of CLAP/MERT/transformer/SONA choices.
- P11 needs additive pipeline/replay persistence and P09 needs an additive canonical interaction
  projection; exact physical migrations remain owned by those implementation phases.
- P12 may improve candidate quality but cannot become a serving/readiness dependency.

## Rejected alternatives

- One monolithic `Recommender` interface coupling retrieval and ranking.
- Deferring event/impression design to P11 and losing early interaction history.
- Treating `recommendation_item` generation as an impression.
- A second transport or parallel feedback REST path outside Offline Journal/sync.
- One mandatory averaged user vector instead of the existing multi-cluster taste model.
- FAISS/Qdrant or HNSW before a measured pgvector exact-search need.
- Implementing sequential recommendation or SONA-Lite before a reproducible baseline and adequate
  interaction data exist.
