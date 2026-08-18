# ADR-024: P11 CPU Recommendation Replay and Offline Boundary

- Status: Accepted
- Date: 2026-08-17
- Decision owner: standing in-scope technical-decision authorization

## Context

P11 must add an explainable recommendation baseline without making embeddings, a sequential model,
CUDA, or the optional P12 worker a serving dependency. The existing PostgreSQL recommendation
tables preserve historical request/item identity but do not retain an immutable pipeline manifest
or the user/catalog input snapshot required for algorithmic replay. Android already stores opaque
offline-pack bytes, but it does not verify the P11 payload or preserve one actual-presentation event
identity across recomposition and restart.

The P04/P09 interaction path is already canonical. Recommendation delivery is not an impression,
and P11 must not introduce a second feedback endpoint. P11 also found that P09 presentation
deduplication was applied too broadly and that causal feedback did not yet prove equality with the
impression request, rank, and Recording.

## Decision

1. The initial active pipeline is a replaceable deterministic CPU baseline behind explicit domain
   ports for user representation, candidate generation, pool composition, mandatory filtering,
   ranking, reranking, immutable version resolution, trace/snapshot persistence, embedding reads,
   and offline evaluation. No sequential generator is registered and embeddings are optional reads.
2. Mandatory ACL, availability, active-identity, dislike, and explicit-exclusion filters run before
   scoring and fail closed. Candidate generators cannot bypass them. Multiple sources deduplicate by
   canonical Recording while every bounded contribution remains in the immutable item trace.
3. Alembic `0013_recommendation_runtime` adds immutable pipeline manifests and retained immutable
   input snapshots, then extends legacy request/item/offline-pack rows additively with replay hashes,
   snapshot references, provenance, reason codes, and pipeline identity. Existing pre-P11 rows remain
   readable through nullable compatibility columns.
4. Exact response replay returns persisted request/items. Algorithmic replay uses only the retained
   original pipeline, seed, and input snapshot; missing or expired inputs return
   `REPLAY_INPUT_UNAVAILABLE` and never substitute current state. Each capture also performs a
   bounded owner-scoped purge of expired snapshots. Purge nulls only the nullable snapshot FK while
   retaining the request hash, provenance, and persisted items required for exact response replay.
5. Serving, Home assembly, offline-pack generation, and immutable-dataset evaluation reuse the same
   pipeline runner. Heuristic scores are ranking values, never calibrated probabilities. Optional
   shadow execution is isolated from the served result and cannot affect CPU baseline availability.
6. Offline pack v1 is bounded RFC 8785 canonical `RAW_JSON`; SHA-256 covers the exact UTF-8 bytes.
   Items retain pack ID, immutable recommendation request ID and source rank. Android may change only
   display position through a deterministic local Like/Dislike/Skip policy.
7. Room advances additively from v8 to v9. Legacy pack bytes retain a nullable owner and fail closed
   until replaced. New packs require exact profile/user/device binding, known version/encoding,
   SHA-256, payload bounds, and an accepted expiry policy before use.
8. Android stores a profile/user-scoped presentation mapping keyed by presentation, request, and
   source rank. The mapping and existing P04 Offline Journal impression event commit in one Room
   transaction; recomposition and restart return the stable event ID. Feedback remains on the P04/P09
   sync path.
9. P09 semantic presentation deduplication applies only to impressions. Direct feedback validates
   that its causal impression has the same owner, device, request, source rank, and Recording.

## Consequences

- P11 works for cold-start users with no embeddings, GPU, or sequential component and creates a
  comparison baseline for P12 rather than selecting the final model.
- Request/item provenance and retained snapshots increase PostgreSQL storage during their declared
  retention window; bounded owner-scoped purge limits personal snapshot retention and controls
  algorithmic replay availability without deleting the exact persisted response.
- Android Home remains local-first and can use a verified fresh pack, or an explicitly labeled
  bounded stale-local fallback, without treating delivery as exposure.
- No HNSW, separate vector store, broker, provider choice, feedback REST path, or P12 behavior is
  introduced.

## Rejected alternatives

- A monolithic recommender or public DTOs containing model/tensor/CUDA types.
- Replaying with current user history or catalog after retained inputs expire.
- Treating API response or pack download as an impression.
- Allowing local reranking to rewrite the server request rank.
- Making embeddings or the GPU worker mandatory for recommendation availability.
