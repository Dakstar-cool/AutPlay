# AutPlay Post-MVP Library Discovery and Contextual Recommendations v1

**Status:** USER-PROVIDED PRODUCT REQUIREMENTS; A1A CONTRACT ACCEPTED; A1 RUNTIME AND R1 NOT STARTED

**Recorded:** 2026-08-21

**Execution boundary:** post-MVP expansion only; this document does not reopen P00-P14, create P15,
extend Server M6 or select an external provider. A1A was separately activated and reached PASS on
2026-08-21; A1B/A1C and all R1 runtime work still require their owning prompts and
prerequisites.

## 1. Purpose

This supplement records two compatible post-MVP product extensions:

1. controlled discovery and acquisition of new releases by artists already represented in an
   owner's AutPlay library;
2. a short-term, multi-horizon preference layer above the existing long-term recommendation
   profile.

The features are related but independently deployable. Discovered music enters the same canonical
Catalog, Vault, Library, analysis and recommendation paths as any other authorized acquisition.
Temporal adaptation changes personalized ranking; it does not create a separate catalog or replace
the durable taste profile.

This specification is narrower than the historical Release Watcher description in `ТЗ AutPlay.md`
for these two features. Existing security, identity, acquisition, Vault, recommendation and M6 Web
contracts remain authoritative.

The later
[`AutPlay_Recommendation_System_Current_and_Future_v1.md`](AutPlay_Recommendation_System_Current_and_Future_v1.md)
compiles the verified current recommender with this temporal layer and the user's profile-maturity
cold-start proposal. The dedicated R1A/R1B/R1C prompts own future execution; this document remains
the original A1/R1 product supplement.

## 2. Delivery decision: hybrid extension

The initial recommender prototype already exists as the closed P11 deterministic CPU pipeline.
Therefore this work must not be placed before that prototype or used to reopen it.

Use a hybrid sequence instead:

- preserve the existing model-independent recommendation API, interaction projection, immutable
  pipeline manifests and replay boundary;
- specify the temporal feature/snapshot seam before choosing a final sequential or SONA-like model;
- implement multi-horizon representation and ranking as a new post-MVP pipeline version, first in
  offline evaluation and shadow mode;
- activate serving only after deterministic replay, quality and fatigue safeguards pass;
- implement release discovery after the current MVP/post-RC line is closed and Server M6 is PASS,
  as a separate accepted Web/application contract rather than an unplanned M6 expansion.

This keeps the current baseline useful as a control and lets a future trainable model consume the
same temporal representation without changing public recommendation contracts.

## 3. Shared invariants

- Android local library and playback never require a synchronous server request.
- An external metadata result is evidence, not a canonical `Artist`, `Release` or `Recording`.
- Stable canonical `artist_id` and versioned provider evidence are used; artist names are never
  identity keys.
- Discovery, authorized audio acquisition, ingest, identity resolution, owner-library
  materialization and audience exposure are distinct transitions.
- No uncertain identity match is auto-merged. `REVIEW_REQUIRED` is safer than a false merge.
- A discovered or newly imported track is not an impression and receives no global audience
  exposure merely because it is new.
- Only an authorized, active, available canonical `Recording` may enter a recommendation candidate
  pool. Existing mandatory ACL, identity, dislike, exclusion and availability filters remain
  fail-closed.
- CPU recommendation and successful core ingest cannot depend on GPU/CUDA or completion of derived
  analysis.
- External adapters must use authorized/user-provided access, documented interfaces and bounded
  timeouts, pagination, concurrency, retry/backoff, rate-limit handling and checkpoints. AutPlay
  never bypasses DRM or treats knowledge of a hash/source URL as authorization.
- Product decisions about the first provider, credentials, paid resources and jurisdiction-specific
  acquisition rights remain deferred until an owning contract is explicitly accepted.

## 4. Post-MVP A1: controlled library expansion

### 4.1. Administrative capability

The optional administrative Web UI may, through application commands and queries:

- start a bounded discovery run for eligible artists already present in an authorized owner's
  library;
- list discovered releases and tracks with pagination;
- filter by canonical artist, provider, release date, discovery time, identity confidence, status,
  availability and other evidence actually supplied by the adapter;
- select individual tracks for acquisition/import;
- enable an explicit automatic-import policy globally and override it per canonical artist;
- disable discovery or import for selected artists;
- retry an eligible discovery or acquisition run and view safe failure summaries;
- view the transition from discovery through acquisition, ingest, analysis and readiness.

The Web presentation layer must not call providers, write PostgreSQL/Vault/job rows directly or
construct identity decisions. It invokes bounded application commands under the accepted M6
session, CSRF, idempotency, RBAC, audit and redaction rules.

### 4.2. Policy scope and defaults

Artist policy is keyed by `(policy_owner_user_id, canonical_artist_id)`, not by display name. In v1,
`policy_owner_user_id` is derived from the authenticated Web actor; list/filter/command inputs
cannot supply or override it. OWNER/ADMIN is not a cross-account or cross-user superuser role.
Future delegated administration requires a separate persisted-grant contract and is not implied by
RBAC. Importing for one owner never adds a `UserTrackRef` or library entry for every other user.

Owner isolation must be proven negatively at the application-command and PostgreSQL boundaries for
candidate list/filter, policy changes, discovery run, selection, retry and materialization.

Discovery and import are independent policy axes:

- `discovery_mode`: `DISABLED`, `MANUAL_ONLY` or `SCHEDULED`;
- `import_mode`: `REVIEW_REQUIRED` or `AUTO_IMPORT`.

The safe default is `MANUAL_ONLY + REVIEW_REQUIRED`. `SCHEDULED` uses provider-specific bounded
cadence, cursor/checkpoint, page and retry limits and exposes `last_checked_at` plus
`next_eligible_at`; manual rerun cannot bypass those limits. Enabling `AUTO_IMPORT` requires a named
consequence confirmation, is audited and can be reversed for future discoveries. Disabling either
axis does not delete already acquired music.

Each automatic job captures the policy revision that authorized it. Moving from `AUTO_IMPORT` to
`REVIEW_REQUIRED` cancels queued, not-yet-started auto-origin work and prevents new auto-origin work;
it does not cancel a separately confirmed manual selection. A worker revalidates the active actor,
owner, artist policy revision, candidate identity decision and source authorization before
acquisition, before Vault commit and before owner-library materialization. A stale/revoked attempt
fails with a stable policy reason under existing lease fencing and operation idempotency rather than
continuing under old authority. In-flight network discovery may finish and retain bounded evidence,
but cannot auto-select or auto-import after revocation. Manual work remains independently
cancellable only through an accepted job command and always revalidates source authorization.

### 4.3. Discovery and track state

Provider results live in a separate discovery projection until canonical identity is resolved.
Suggested release/track states are:

```text
DISCOVERED
ALREADY_IN_OWNER_LIBRARY
DUPLICATE_EVIDENCE
REVIEW_REQUIRED
SELECTED
ACQUISITION_QUEUED
ACQUIRING
INGESTING
IDENTITY_REVIEW_REQUIRED
READY
IGNORED
FAILED_RETRYABLE
FAILED_TERMINAL
```

Release status is an aggregate view of track states, not a substitute for track-level truth. Every
transition records the owner, stable operation/job identity, adapter and evidence version, safe
reason code and timestamps. Retry reuses durable operation identity and cannot duplicate a
successful materialization. `READY` means canonical playable media and the owner-library projection
are committed and standard analysis work is durably enqueued.

Analysis has a separate status such as `QUEUED`, `RUNNING`, `PARTIAL`, `COMPLETE`,
`FAILED_RETRYABLE` or `FAILED_TERMINAL`. Optional analysis failure does not roll back `READY`, core
playback or baseline recommendation eligibility.

### 4.4. Discovery pipeline

```text
owner library artists
  -> canonical artist/provider mapping evidence
  -> bounded provider release discovery
  -> discovery candidate projection
  -> owner-library and global technical deduplication
  -> identity decision or explicit review
  -> manual selection or allowed AUTO_IMPORT
  -> authorized acquisition
  -> validation/hash/fingerprint/Vault commit
  -> Recording/ReleaseTrack/UserTrackRef/library materialization
  -> READY + standard analysis jobs
  -> baseline eligibility while optional enrichment continues
```

Metadata discovery and obtaining playable audio are different capabilities. A provider may support
release discovery without granting an authorized acquisition path. In that case the UI may show a
candidate and a stable unavailable reason, but must not claim that the track was added.

### 4.5. Deduplication and identity

Deduplication occurs at several explicit levels:

- exact replay of the same provider page/cursor and adapter evidence;
- provider-native release/track IDs under a versioned adapter namespace;
- owner-library membership by canonical `recording_id`;
- exact valid-byte reuse by SHA-256 under existing authorization rules;
- versioned external-ID, ISRC, fingerprint and metadata identity evidence;
- release/medium/position association through `ReleaseTrack`, never by collapsing it into
  `Recording`.

Provider IDs or matching metadata alone cannot authorize bytes or force an auto-merge. If a
canonical recording already exists but is absent from the owner's library, the system may offer an
owner-scoped add/reuse action only when availability and authorization are independently proven.

### 4.6. Analysis and recommendations

Once materialized, a new track is an ordinary canonical library item. It enters the existing
analysis queues and becomes eligible for baseline recommendations even if optional embeddings or
tags are still pending. Derived features may improve later pipeline versions but cannot gate core
ingest or playback.

New releases are not broadcast to all users and are not inserted into every Home feed. They may be
generated as freshness/artist-affinity candidates only for an authorized user, then pass the same
personalized ranking, diversity and mandatory filters as older catalog items. Acquisition origin
must not be a permanent positive ranking bias.

### 4.7. A1 acceptance gates

A1 manual discovery/import and later automation are not PASS until executable evidence proves:

- actor-derived owner isolation for every query, command, job and materialization path;
- idempotent provider cursor/page replay and bounded repeated discovery without duplicate candidate
  or canonical materialization;
- lease fencing and idempotency under concurrent manual/automatic selection, retry, timeout, crash
  and lost response;
- a visible non-added outcome when metadata exists but no authorized playable source exists;
- explicit identity review and zero automatic uncertain merge;
- policy-disable races cannot continue stale auto-origin acquisition or owner materialization;
- canonical `READY` materialization and durable standard-analysis enqueue have a specified atomic or
  recoverable boundary, while optional analysis failure cannot roll back core readiness;
- status recovery after restart derives from durable state and never reports success after failure;
- discovery and acquisition create no impression and no cross-user/global recommendation exposure;
- Web RBAC/CSRF/idempotency, audit/redaction, bounded pagination/filtering and provider error
  summaries remain within the accepted M6 security boundary;
- CPU-only API/worker/Android behavior remains available with the discovery adapter disabled or
  unavailable.

## 5. Post-MVP R1: multi-horizon contextual adaptation

### 5.1. Product interpretation

The recommender maintains two distinct layers:

- a durable long-term taste profile;
- an ephemeral contextual profile describing recent listening direction, momentum and temporary
  fatigue.

The product may describe the second layer as current musical mood, but implementation must not
claim to infer a psychological or sensitive emotional state. It is a bounded music-interaction
context derived from the user's own recent actions.

### 5.2. Initial horizons

The first evaluated representation uses overlapping horizons:

- current listening episode;
- 12 hours;
- 24 hours;
- 3 days;
- 7 days;
- long-term profile.

An episode is a sequence separated by a versioned inactivity-gap policy; it is not an account,
browser or authentication session. Exact gaps, decay half-lives, thresholds and weights belong to
an immutable pipeline/feature-policy version and must be selected through evaluation rather than
hard-coded as timeless product constants.

Overlapping windows are feature views over one event stream. Their raw counts are not simply added,
which would count one interaction five times.

### 5.3. Signals

Positive contextual evidence may include:

- explicit Like;
- organic selection from Search, Library or Playlist;
- high completion and repeated voluntary listening;
- selection of a presented recommendation;
- consistent affinity across several recent interactions.

Negative contextual evidence may include:

- explicit Dislike, which remains a durable mandatory exclusion according to existing policy;
- dismissal of a recommendation;
- a run of finalized short listens/skips for similar music;
- falling completion and repeated abandonment after prior positive interest.

A skip is derived only from a finalized logical listening event under a versioned threshold policy;
seek/progress telemetry must not create event floods or be treated as an independent skip. Organic
and recommended interactions remain distinguishable so the system does not reinforce only its own
previous output.

### 5.4. Context model

For each supported feature dimension, such as canonical artist, release, bounded metadata/tags or a
versioned embedding neighborhood, the representation estimates:

- long-term affinity;
- recent affinity at each horizon;
- persistence across adjacent horizons;
- rising or falling momentum;
- temporary fatigue/negative pressure;
- evidence count and confidence.

Sparse or contradictory evidence reduces contextual influence. One skip must not erase a durable
taste; a sustained recent skip pattern may lower the temporary layer quickly. Positive interest
decays smoothly, while fatigue may use a faster attack and faster recovery, both bounded by the
immutable feature policy.

Conceptually:

```text
served score
  = baseline long-term score
  + bounded recent-interest boost
  + bounded momentum adjustment
  - bounded temporary-fatigue penalty
```

The temporal layer cannot bypass ACL/availability/identity/dislike/exclusion filters or diversity,
artist-repeat and release-repeat safeguards. With insufficient or stale evidence it becomes neutral
and the P11 baseline remains available.

### 5.5. Profile maturity, score and confidence

Cold-start plasticity is part of the same R1 representation rather than a second recommender. For
each supported direction, `score` describes estimated affinity and `confidence` describes the
strength/consistency of its evidence. Confidence cannot be simulated by making a score extreme.

Global profile maturity is derived from bounded effective interaction mass, signal quality,
coverage/diversity, observation span, recency and contradiction. Registration age alone is not a
valid maturity proxy. Low maturity/confidence increases the influence of reliable fresh explicit or
organic evidence within versioned caps; as maturity grows, a single action changes the durable
profile less while the short-term contextual layer remains responsive.

Organic and recommendation-origin evidence use separate weights to limit self-reinforcing output.
One Like, repeat or skip cannot produce an unbounded update. With insufficient evidence, confidence
shrinks the adjustment toward the deterministic P11 baseline and controlled exploration.

### 5.6. Architecture and replay

R1 is implemented behind existing `UserRepresentationProvider`, `Ranker`/`Reranker` and immutable
pipeline registry seams. The public recommendation request/response and attribution contracts need
not expose model tensors or temporal feature vectors.

The current P11 snapshot stores aggregate counts and one `last_played_at`; that is insufficient for
session/12h/24h/3d/7d reasoning. R1 therefore requires an additive, versioned snapshot/feature
document that records:

- a deterministic evaluation anchor/cutoff time;
- an owner-scoped interaction watermark and exact bounded source evidence/reference;
- event-time handling for delayed offline sync and bounded device-clock skew;
- the temporal feature-policy version and hashes;
- sufficient retained inputs for deterministic algorithmic replay.

Existing v1 snapshots and exact response replay remain readable. Current mutable state must never
be substituted when replaying a retained request. Temporal evidence has owner-scoped bounded
retention. Exact response replay continues to return the persisted request/items; if the retained
temporal evidence required for algorithmic replay is absent or expired, the existing stable
`REPLAY_INPUT_UNAVAILABLE` result is returned. The implementation never reconstructs that request
from current events. Purge, delayed-sync and clock-skew replay fixtures must prove this boundary.

Server horizons use the synchronized canonical event projection. The current listening episode may
also use a deterministic device-local delta to rerank a verified offline pack, preserving the
server's immutable source rank and mandatory local policy. Later sync reconciles new interactions;
it does not rewrite already recorded impressions.

### 5.7. Evaluation and activation

R1 must pass these gates before serving activation:

- deterministic replay for fixed events, cutoff, policy, pipeline, seed and catalog snapshot;
- time-split offline evaluation against the P11 baseline;
- explicit scenarios for rising interest, fading interest, temporary fatigue, recovery, sparse
  evidence, multi-device delayed sync and clock skew;
- no degradation of mandatory filtering, attribution, diversity, repeat control or CPU-only
  availability;
- shadow execution that cannot affect served results or create impressions;
- bounded latency/storage/retention evidence;
- an activation and rollback decision tied to an immutable pipeline version.

Useful comparison metrics include attributed skip/completion/selection/like/dislike rates,
NDCG/Recall at K, artist/genre concentration, novelty, repeat rate, contextual responsiveness and
recovery from fatigue. No single engagement metric may override safety and diversity guards.

## 6. Milestone boundaries

These are post-MVP expansion milestones, not P15 and not part of Server M6:

| Milestone | Prerequisites | Bounded outcome |
| --- | --- | --- |
| [Post-MVP R1A adaptive contract](../build-pack/prompts/POST_MVP_R1A_ADAPTIVE_RECOMMENDATIONS_CONTRACT.md) | Current MVP/post-RC line closed; explicit activation | Versioned temporal/maturity/confidence feature, snapshot and replay contract, offline fixtures and ADR; no serving change |
| [Post-MVP R1B shadow implementation](../build-pack/prompts/POST_MVP_R1B_ADAPTIVE_RECOMMENDATIONS_SHADOW.md) | R1A accepted | CPU representation/ranker, additive persistence if required, offline and shadow evidence |
| [Post-MVP R1C controlled activation](../build-pack/prompts/POST_MVP_R1C_ADAPTIVE_RECOMMENDATIONS_ACTIVATION.md) | R1B PASS; explicit activation decision | New immutable pipeline version, rollback and monitored serving evidence |
| [Post-MVP A1A discovery/acquisition contract](../build-pack/prompts/POST_MVP_A1A_DISCOVERY_ACQUISITION_CONTRACT.md) | PASS; P10/P11/Server M6 PASS and explicit activation | Accepted provider-neutral domain/Web contract, immutable provider identity, server-computed idempotency, guarded orthogonal state machines, nine executable schemas/fixtures, ADR-033 and final review zero Critical/Major/Minor; no runtime/provider selected |
| Post-MVP A1-B manual discovery/import | A1-A accepted; one authorized adapter explicitly selected | Discovery jobs, review/select flow, dedupe/identity/acquisition/ingest integration and Web qualification |
| Post-MVP A1-C opt-in automation | A1-B green; separate auto-import acceptance | Per-artist policy, guarded auto-import, retry/audit/rollback controls and recommendation integration evidence |

R1 and A1 may be implemented in either order after their prerequisites. Their integration gate proves
that an A1 `READY` track becomes an ordinary owner-authorized candidate under the active baseline or
R1 pipeline without global exposure.

## 7. Explicit non-goals

This supplement does not authorize:

- changing or expanding the in-progress Server M6 scope;
- choosing Spotify, Yandex Music, MusicBrainz, Discogs or any other provider;
- scraping private/undocumented interfaces, bypassing DRM or assuming download rights;
- public registration, public deployment, domain/TLS/proxy selection or paid resources;
- global automatic promotion of new releases;
- automatic uncertain identity merges;
- replacing the durable taste profile with recent behavior;
- continuous online model training, a mandatory GPU, a separate vector database or a message
  broker.

## 8. Documentation rule

When one of these milestones is explicitly activated, create its own prompt/contract, ADRs and
handoff. Update `PLAN.md`, `PROGRESS.md`, `TRACEABILITY.md`, `RISK_REGISTER.md` and `VERSIONS.md` only
for verified state. Do not append this work to the M6 handoff or alter the closed P14 claim.
