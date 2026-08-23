# AutPlay Recommendation System: Current State and Future Features v1

**Status:** CURRENT IMPLEMENTATION SUMMARY AND RECORDED FUTURE ROADMAP

**Recorded:** 2026-08-21

**Execution boundary:** this document describes verified behavior and planned extensions. It does
not activate R1, choose a final model, change a frozen contract or claim that a future feature is
implemented.

## 1. Short product summary

AutPlay currently uses a deterministic, explainable CPU recommendation baseline. It combines
explicit preferences, organic listening history, artist/release/metadata affinity, freshness,
forgotten content and controlled exploration. Mandatory authorization, availability, identity,
Dislike and taste-exclusion checks run before scoring. A diversity reranker limits repeated artists
and releases.

The current profile is deliberately stable and simple. It supports an empty-history user through
freshness and exploration, but it does not yet estimate profile maturity, per-direction confidence,
multi-horizon recent interest, momentum or temporary fatigue. Post-MVP R1 adds those capabilities as
a bounded dynamic layer above the existing baseline.

## 2. Current verified architecture

```text
local-first interaction capture
  -> P04/P09 append-only canonical interaction projection
  -> one owner-scoped immutable recommendation input snapshot
  -> user representation prepared once
  -> candidate generators
  -> Recording-level composition and deduplication with provenance
  -> mandatory fail-closed filters
  -> deterministic scoring
  -> diversity/repeat reranking
  -> immutable request and item trace
  -> Home / verified offline pack
  -> impression only on actual presentation
```

The public request/response and attribution contracts are model-independent. Candidate generation,
composition, filtering, ranking, reranking, representation, pipeline registry, snapshot/trace
storage and evaluation are separate ports. A future component can replace one stage through a new
immutable pipeline manifest without exposing embeddings, tensors or CUDA concepts in the API.

PostgreSQL owns synchronized interaction, recommendation request/item and snapshot truth. Android
keeps a profile/user/device-bound verified offline pack and local presentation mapping. Local
playback and readable local media do not synchronously depend on the server.

## 3. Signals used today

| Signal/source | Current interpretation |
| --- | --- |
| Explicit Like | Strong candidate and artist/metadata affinity evidence |
| Explicit Dislike | Mandatory exclusion before scoring |
| Exclude from taste | Mandatory taste exclusion; the event is not allowed to train affinity |
| Organic listening history | Play counts, artist affinity and last-played evidence; recommended-only history is not treated as organic affinity |
| Artist/release/metadata | Related candidates from canonical artist keys, release association and bounded metadata-token overlap |
| Freshness | Recent releases for known affinity artists; catalog-wide freshness fallback when no affinity exists |
| Forgotten content | A bounded boost to previously played content that has not been heard recently |
| Controlled exploration | Seeded deterministic coverage of less-known/unplayed material, scaled by request exploration |
| Repeat evidence | Small score penalty plus artist/release repeat caps |
| Request context | A versioned context value exists; the baseline uses it only conservatively and does not infer mood |

The initial fixed heuristic weights are internal ranking values, not calibrated probabilities. The
baseline representation contains liked artists, organically listened artists and tokens from liked
tracks. The server snapshot stores per-track aggregate play counts, organic/recommended counts and
one `last_played_at`; it is bounded to a deterministic set of 5,000 tracks.

## 4. Serving, replay and offline behavior

- Candidate sources deduplicate by canonical `Recording` while preserving every bounded source
  contribution and reason code.
- Mandatory owner/ACL, availability, active identity, Dislike and explicit-exclusion filters fail
  closed and cannot be bypassed by a generator or ranker.
- Fixed pipeline/component/config versions, seed and input snapshot identity are recorded for each
  served request.
- Exact replay returns persisted request/items. Algorithmic replay uses only the retained original
  inputs; after their bounded retention expires it returns `REPLAY_INPUT_UNAVAILABLE` instead of
  using current history.
- Home and offline-pack generation reuse the same pipeline. Delivery is not an impression.
- Android validates exact canonical pack bytes, expiry and profile/user/device ownership. Its
  current local reranker can promote a fresh Like, suppress a recent skip/repeat and enforce local
  availability/diversity without rewriting the immutable server `source_rank`.
- One stable impression event is committed only when an item is actually presented. Feedback stays
  on the existing P04/P09 sync path.
- CPU serving works without embeddings, GPU/CUDA, a sequential model or a separate vector store.

## 5. Current cold-start behavior and missing capabilities

Cold start is safe but basic. With no learned affinity, the baseline uses authorized fresh catalog
items and deterministic exploration. It does not fail merely because the user has no prior
`UserTrackRef`, embeddings or GPU features.

The following capabilities are not implemented today:

- profile maturity derived from amount, quality, diversity and consistency of evidence;
- increased but bounded early-profile plasticity;
- separate per-direction `score` and `confidence`;
- current episode, 12-hour, 24-hour, 3-day and 7-day representations;
- rising/falling interest and confidence-gated momentum;
- temporary fatigue distinct from durable Dislike or long-term affinity;
- deterministic replay of horizon-specific event evidence;
- continuous profile learning or a trainable sequential/SONA-Lite serving model.

## 6. Recorded future recommendation features

### 6.1. Post-MVP R1 adaptive taste

R1 combines the two requested improvements in one compatible feature layer:

1. recent-context adaptation across the current listening episode, 12 hours, 24 hours, 3 days and
   7 days;
2. maturity-aware cold start with separate score/confidence and a bounded plasticity multiplier.

For every supported taste dimension, R1 will evaluate long-term affinity/confidence, recent
affinity/confidence, persistence, momentum, fatigue and effective evidence mass. Profile maturity is
based on useful evidence, not registration age. Low maturity increases the influence of reliable
fresh signals within hard caps; growing maturity reduces the effect of one action without disabling
short-term adaptation.

Conceptually:

```text
served score
  = P11 baseline
  + confidence-gated recent-interest boost
  + bounded momentum adjustment
  - bounded temporary-fatigue penalty
  + bounded exploration adjustment
```

One finalized skip is not a Dislike. A sustained recent skip pattern may raise fatigue quickly,
while durable affinity remains. Overlapping horizons are views of one event stream and cannot count
the same event five independent times. Exact policies and weights are immutable/versioned and must
pass offline plus shadow evaluation before serving.

Delivery is split into:

- `R1A`: contract, feature/snapshot/replay semantics, fixtures and ADR;
- `R1B`: CPU implementation and offline/shadow evidence while P11 still serves;
- `R1C`: explicit controlled activation of a new immutable pipeline with rollback.

### 6.2. Optional content enrichment

P12 provides an isolated experimental path for versioned recording embeddings/tags and exact
pgvector retrieval, but no GPU model is active in the local RC. Compatible derived features may
later improve candidate retrieval or taste dimensions; they cannot gate CPU serving, local playback
or core ingest.

### 6.3. Later model components

Sequential candidates, SONA-Lite representation/ranking and other trainable components remain
future experiments behind the same ports and immutable pipeline registry. No final model is
selected. Continuous online training after every action is not part of the accepted roadmap.

### 6.4. Catalog and social extensions

Post-MVP A1 may add controlled discovery of newly released music by library artists. A `READY`
track becomes an ordinary owner-authorized candidate and receives no global or permanent ranking
boost merely because it was discovered.

After the S1 social contract and privacy controls exist, opt-in social or co-listening signals may
be evaluated separately. Friendship itself must never expose another user's history or become an
automatic affinity signal.

## 7. Additional signals and methods worth evaluating

These are candidates for future contracts, not committed implementation:

| Candidate | Potential value | Required guardrail |
| --- | --- | --- |
| Optional onboarding seeds | Faster cold start from user-selected artists/tags | Skippable, editable and never required for local use |
| Time of day/day of week | Recurring listening patterns | Local timezone policy, no sensitive inference, decay and user control |
| Explicit taste context | User-selected work/sleep/workout/party direction | Explicit choice outranks inferred context; easy reset/exclusion |
| Search-to-play conversion | Stronger intent than a query or impression alone | Store only bounded canonical attribution; avoid raw-query retention |
| Queue/playlist transitions | Learn compatible sequencing and session direction | Do not treat passive/autoplay transitions as organic choice |
| Novelty appetite | Personalize exploration versus familiarity | Hard diversity/concentration caps and a visible reset/control |
| Versioned audio traits | Tempo, energy, key, timbre, tags and embedding neighborhoods | Derived evidence versioning, CPU fallback and no emotion claim |
| Exposure/position debiasing | Reduce self-reinforcing recommendation feedback | Preserve impression/selection causality; shadow evaluation first |
| Contextual bandit | Adapt exploration using uncertainty | Bounded action space, offline/shadow safety and deterministic policy version |
| Opt-in co-listening/social signal | Shared discovery and group-session relevance | Aggregated/minimized data, consent, block enforcement and no friend-history leak |
| Per-session taste exclusion | Prevent sleep/children/party sessions from changing taste | Explicit user control and immutable event attribution |

The highest-value near-term additions are onboarding seeds, explicit taste context, search-to-play
conversion, novelty appetite and exposure debiasing because they can reuse current interaction and
pipeline seams without making GPU or social infrastructure mandatory.

## 8. Permanent safeguards

- Recent behavior supplements but never replaces durable taste.
- Dislike, authorization, availability, active identity and explicit exclusion remain mandatory
  filters.
- Organic and recommendation-origin evidence remain distinguishable.
- Recommendation delivery is not an impression; actual presentation is.
- Scores are not probabilities unless a later calibrated model proves that interpretation.
- Unknown persisted/API values remain preservable under versioned compatibility rules.
- Personal evidence is owner-scoped, bounded in retention/export and absent from routine logs.
- A future model must beat or complement the reproducible P11 control without weakening diversity,
  attribution, replay, local-first behavior or CPU availability.
