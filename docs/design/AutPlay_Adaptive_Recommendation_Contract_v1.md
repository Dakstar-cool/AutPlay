# AutPlay Adaptive Recommendation Contract v1

**Status: ACCEPTED; RUNTIME NOT IMPLEMENTED**  
**Contract ID:** `adaptive-recommendations-r1-v1`  
**Owning milestone:** Post-MVP R1A  
**Machine record:** `contracts/recommendations/v1/contract-policy.json`

## 1. Scope and authority

This document freezes the accepted contract for multi-horizon adaptive recommendations. R1A is a
contract-only milestone: it changes no served recommendation, public route, PostgreSQL or Room
schema, worker, Android behavior, dependency, deployment or GPU requirement. P11 remains the sole
serving baseline until a separately activated R1B proves a shadow implementation and a separately
activated R1C authorizes controlled serving.

The machine-readable policy, strict schemas and fixtures under `contracts/recommendations/v1` and
`tests/fixtures/recommendations/v1` are normative where prose and examples differ. Candidate
feature-policy numbers are evaluation inputs, not timeless product constants. A policy can become
`SHADOW` or `ACTIVE` only through the later gates described in section 15.

## 2. Existing executable boundary

The current repository already provides:

- deterministic P11 CPU candidates, mandatory filters, ranking, reranking, immutable request/item
  evidence, exact replay, algorithmic replay and offline packs;
- P04/P09 `LISTENING_EVENT_RECORDED` evidence with played duration, track duration, completion
  ratio, origin, context, explicit feedback, exclusion and recommendation attribution;
- `RECOMMENDATION_FEEDBACK_RECORDED` selection/dismissal with a causal impression link;
- generic `USER_TRACK_PREFERENCE_SET` history plus the mutable preference projection;
- an Android local reranker that preserves server source rank and creates one impression at actual
  presentation.

The current P11 aggregate snapshot cannot reconstruct event-time horizons. The server inbox has
device occurrence and receipt facts, while the current canonical interaction projection does not
preserve all three required times as normalized recommendation evidence. R1B therefore requires an
additive temporal projection; R1A does not create it.

Two tempting signals are not reliable today. A repeated finalized listen cannot distinguish a
voluntary replay from queue/autoplay repetition, and a Search/Library/Playlist selection without a
finalized listen has no trustworthy common event. Both remain `DEFERRED_EXPLICIT_CAPTURE_REQUIRED`.

## 3. Two independent profile layers

The adaptive profile has independent durable and recent layers:

1. **Long-term affinity** represents stable evidence-backed taste. It changes gradually and is not
   erased by one skip.
2. **Recent context** represents current listening direction, persistence, momentum and temporary
   fatigue. It is not an emotion, diagnosis or sensitive psychological inference. It decays to a
   neutral adjustment when evidence is missing, stale or unreliable.

Every supported dimension stores or derives a signed `score` in `[-1, 1]` and a separate
`confidence` in `[0, 1]`. Score is not a probability. Confidence never hides inside an extreme
score. Low confidence shrinks the adaptive adjustment toward P11 rather than inventing certainty.

R1B candidates are limited to canonical artist ID, canonical release ID and bounded metadata
tokens. Display text is never identity. A versioned embedding neighborhood is deferred and cannot
be required for CPU availability.

## 4. Signal contract

| Signal | Verified source | Origin/effect | R1 status |
| --- | --- | --- | --- |
| Explicit Like | `USER_TRACK_PREFERENCE_SET` history | explicit positive affinity | captured; additive temporal projection needed |
| Explicit Dislike | `USER_TRACK_PREFERENCE_SET` history | mandatory filter | captured; additive temporal projection needed |
| Exclude from taste | preference/listen exclusion | zero training, maturity, momentum and fatigue mass | captured |
| Finalized organic listen | finalized listen with `ORGANIC` origin | quality-weighted consumption evidence, not a surface-selection claim | captured |
| Finalized recommendation listen | finalized attributed listen plus request/impression/source rank | bounded recommendation-lane consumption evidence | captured and grouped with its impression/outcome |
| Recommendation selected | feedback plus causal impression | bounded self-exposure positive | captured; additive temporal projection needed |
| Recommendation dismissed | feedback plus causal impression | bounded temporary negative pressure | captured; additive temporal projection needed |
| Finalized completion | played/duration/completion under policy | quality-weighted affinity | captured and derived once |
| Finalized short listen/skip | one finalized logical listen under policy thresholds | bounded temporary fatigue | captured and derived once |
| Voluntary repeat | no reliable discriminator | none | deferred; explicit capture required |
| Voluntary Search/Library/Playlist selection | no common `user_initiated`/surface/queue-transition fact | none | deferred; explicit capture required |

A progress update, seek or duplicate sync delivery is never another skip. Recommendation-origin
evidence always stays in the recommendation lane and uses its lower policy weight; it never raises
organic share. Explicit Dislike and exclusion are evaluated before scoring and cannot be offset by
recent interest, exploration or model output.

The Android producer currently writes `explicit_feedback=NONE` on finalized listens, so that field
is not a current Like/Dislike source. Search and Playlist origins may label an entire queue, and
`ORGANIC` does not distinguish a Library button action. They remain useful source/consumption lanes
but never prove that every later track was voluntarily selected.

## 5. Event time, cutoff and watermark

Each normalized evidence document records its own `evidence_id`, the original `source_event_id`,
owner/profile/device binding, `occurred_at_ms`, `received_at_ms`, `effective_at_ms` and the applied
`server_sequence`. Client `occurred_at` never authorizes inclusion or advances the watermark.
After an event is accepted and classified, its deterministic `effective_at_ms` does order event-time
feature projection. Snapshots use a server-database cutoff and an owner-scoped applied interaction
watermark. An event is eligible only when received and applied by the cutoff and at or below the
watermark. Stable feature-projection order is:

```text
(effective_at_ms, received_at_ms, server_sequence, evidence_id)
```

The feature policy owns the episode inactivity gap, future-clock tolerance and maximum recent
backfill. Event-time handling is deterministic:

- a plausible occurrence time is used directly;
- future skew beyond tolerance is clamped to receipt time and receives lower confidence;
- excessive past skew remains durable evidence when otherwise valid but is excluded from recent
  windows;
- missing occurrence time uses receipt time with lower confidence;
- delayed sync may affect a later request, but never mutates an existing snapshot, request or
  impression.

R1 source evidence is selected by owner events and watermark, independently of the P11 5,000-track
catalog-candidate cutoff. The replay-complete projection must join inbox occurrence/receipt/hash,
sync server sequence, interaction feedback/impression, listening metrics and preference history by
owner and event ID; no single current table is sufficient.

An episode is the owner event sequence after the versioned inactivity gap. It is not an account,
device or authentication session.

## 6. Horizons and event-mass conservation

The observable views are episode, 12 hours, 24 hours, 3 days, 7 days and long term. Recent windows
overlap for observation, but they do not duplicate evidence. For an event with base evidence mass
`m`, configured weight `w_h` and active view set `A`:

```text
coefficient(event, h) = w_h / sum(w_a for a in A), when h is active
fused_mass(event)     = m * sum(coefficient(event, h) for h in A) <= m
```

The implementation may maintain per-window summaries, but it must fuse them through these
normalized coefficients. Summing raw counts from every matching window is forbidden. The policy
weights sum to one and are covered by deterministic full- and partial-window fixtures.

Transport duplicates collapse by `(owner_user_id, source_event_id, source_request_sha256)` and a
hash conflict for the same owner/source event rejects the input. Normalized evidence identity is
`(owner_user_id, evidence_id)`, with `(signal_key, derivation_key)` as the derivation discriminator.
Consequently one finalized listen may produce a base-listen document and exactly one outcome
document without an identity collision. Events in one recommendation action chain group by
owner/request/impression/source-rank/recording. Signals in that group use signed weights divided by
`max(1, sum(abs(active weights)))`, so selection, its attributed listen and a derived outcome remain
observable without becoming independent intentions.
Completion and skip from one listening event are mutually exclusive. Mandatory Dislike/exclusion
filters remain outside this score fusion.

## 7. Maturity and bounded plasticity

Profile maturity is evidence quality, never registration age. The candidate policy computes a
bounded `[0, 1]` weighted combination of:

- effective signal mass after signal-quality and origin weights;
- track and artist coverage;
- observation span and recency;
- consistency versus contradiction;
- organic share versus recommendation self-exposure.

The candidate policy fixes `WEIGHTED_SIX_TERM_SATURATION_V1`: effective mass, track coverage,
artist coverage and observation span saturate at their policy limits; track and artist coverage are
averaged; recency is `0.5^(age_ms / recency_half_life_ms)`; consistency and organic share are
bounded `[0,1]`; the six policy-weighted terms sum to maturity. Candidate plasticity is monotonic:

```text
plasticity = max_plasticity - maturity * (max_plasticity - min_plasticity)
```

Thus one action matters more for a low-evidence profile but remains capped. As evidence grows,
individual updates shrink; recent-context adaptation does not disappear. Contradictory weighted
mass reduces confidence before it drives score magnitude. A mature but recently changing profile
can still have momentum, and an old account with ten useful events can remain immature.

The evaluation candidate also freezes executable formula IDs: momentum is clamped episode minus D7;
fatigue is prior fatigue times a versioned daily decay plus skip mass times skip gain minus completion
mass times recovery, clamped to `[0, 1]`; contradiction confidence is base confidence times one minus
the bounded opposing-mass ratio. These are candidate-policy calculations, not active product values.
The outcome classifier is `EXCLUSION_THEN_SKIP_ELSE_COMPLETION_ELSE_NEUTRAL_V1`: exclusion wins;
skip applies when completion ratio is at or below the skip ratio or played time is strictly below
the played-time threshold; otherwise completion applies at or above its threshold; the remaining
gap is neutral. When `completion_ratio` is `NULL`, both ratio predicates are false, while the
played-time skip predicate still applies. The policy invariant
`skip_completion_threshold < completion_threshold` prevents ratio overlap.

## 8. Dimension state and score composition

For each dimension the versioned snapshot contains long-term score/confidence/evidence mass; recent
score/confidence/evidence mass for every horizon; persistence; signed momentum; fatigue; the final
recent adjustment; and a bounded list of source event IDs.

After all mandatory filters, the conceptual served composition is:

```text
P11 baseline long-term score
  + confidence-gated recent-interest boost
  + bounded momentum adjustment
  - bounded temporary-fatigue penalty
  + bounded exploration adjustment
```

Each component and their total absolute contribution are capped by the immutable feature policy.
Fatigue is temporary negative pressure, not Dislike. It can rise faster after sustained finalized
negative evidence and fall as evidence expires or positive completion arrives. It never deletes
durable affinity. Momentum is direction across normalized horizons, not raw event volume.

## 9. Snapshot and replay

P11 `RecommendationInputSnapshot` v1 is never reinterpreted as temporal evidence. R1B would add a
distinct `RECOMMENDATION_TEMPORAL_SNAPSHOT_V2`, while old P11 snapshots remain readable.

An R1 temporal snapshot is owner-scoped, immutable and bounded. It contains the server cutoff,
interaction watermark, catalog and availability references, P11 baseline input reference, feature
policy key/version/hash, bounded normalized source evidence, adaptive profile, canonical source and
snapshot hashes, separate event-time-policy and derived-feature hashes, and retention deadline.
Nested owner, cutoff, watermark and feature-policy identity must equal the envelope. Limits are
10,000 source events, 512 dimensions, 64 source references per dimension and 4 MiB serialized
snapshot size. Every server snapshot source event has a non-null applied server sequence at or below
the watermark; locally unsynced evidence can never enter a server snapshot.

Existing P11 v1 snapshots and persisted response replay remain readable. Exact replay uses the
original persisted request/items only. Algorithmic replay requires the original temporal snapshot,
policy hash, seed, catalog and P11 inputs. If a required retained input is gone, the result is
`REPLAY_INPUT_UNAVAILABLE`; current state is never substituted. A later sync never rewrites an old
snapshot or impression.

## 10. Offline local delta

An authorized device may apply one deterministic, bounded local delta to an already verified
offline pack using at most 256 newly finalized local events. The delta is bound to owner, profile,
device, pack, request and feature-policy hash, and expires within seven days.

The UUID-valued server profile binding prevents pre-unbinding evidence from being relabelled for a
later profile. The device may change display position but must retain the server `source_rank`, mandatory filters,
parent pack integrity and one stable impression at actual presentation. It cannot claim a server
model result, export tensors or rewrite the earlier request/impression after sync. Unrecognized or
expired local evidence produces no adaptive adjustment.

The delta carries parent-pack and canonical parent-item hashes. Expiry must be after creation, no
later than both seven days and parent-pack expiry. Every adjustment matches one unique parent
recording/source-rank pair and carries the RFC 8785/SHA-256 stable impression key over
owner/device/request/recording/source-rank. Display position, impression event ID and impression key
are each unique within the delta. Every local event's owner, server profile and device must equal
the delta binding, so data sealed before unbinding cannot enter a later profile. Evaluation at or
after either delta or parent expiry fails closed. Tampered parents, foreign bindings, duplicates and
rank changes fail closed. A newly finalized offline event has `server_sequence=null` and
`LOCAL_UNSYNCED_TIME`; after sync it may enter a later server snapshot with its applied sequence but
never rewrites the earlier delta or impression.

## 11. Retention and privacy lifecycle

| Artifact | Normal retention | Accepted owner privacy deletion | Device unbinding |
| --- | --- | --- | --- |
| Raw interaction | existing owner privacy boundary | atomic purge of owner inbox/sync, interaction, listening and preference rows | remote unchanged |
| Normalized temporal event | 30 days | cascade purge | remote unchanged |
| Active adaptive profile | until replacement/delete; superseded row immediately purged after any retained snapshot embeds its copy | cascade purge | derived local copy purged |
| Temporal snapshot | 30 days | cascade purge | local copy purged |
| Request/item replay trace | 30 days | cascade purge | local copy purged |
| Server offline pack | 8 days | cascade purge | local copy purged |
| Device local delta | at most 7 days | purge at next authorized delete sync or local reset | immediate purge |

Deletion is owner-authorized through the existing privacy boundary; ordinary expiry is not a
substitute. A pre-existing external legal hold blocks the operation before any partial delete; R1
creates no hold. Once accepted, new owner writes stop and one transaction deletes in this order:
R1/P11 offline packs; Library interaction/listening/preference rows; request items/traces; requests;
R1 temporal snapshots; P11 input snapshots; adaptive profiles; normalized events; sync tombstones;
then sync events and inbox rows. This ordering satisfies the current `listening_event →
recommendation_request` and `sync.tombstone → sync_event` `ON DELETE RESTRICT` dependencies. The
proposed restricted `SECURITY DEFINER` privacy function requires the accepted
delete request and bypasses only P11's active snapshot-retention trigger; no ordinary worker can use
that override and no FK cascade is assumed.

After accepted deletion, both exact and algorithmic replay return `RECOMMENDATION_NOT_FOUND`.
Personal evidence is not retained merely to preserve replay. Immutable pre-delete backup generations
expire normally and are never routed directly: before a restore can serve traffic, an independently
retained `HMAC-SHA256("privacy-delete-v1", user_id)` tombstone is reapplied in isolation and zero
owner rows are verified. The keyed tombstone stores completion/protection times but no raw owner ID
or payload and expires only after every pre-delete backup expires or is verified destroyed. A later
non-personal receipt retains only request ID, aggregate counts and completion time.

Unbinding means local credential/active-binding and derived recommendation-context erasure, not
remote deletion. It immediately removes recommendation packs, deltas and in-memory context but,
under the accepted pairing contract, retains library, outbox, media and prior profile data sealed
and inaccessible to another binding. A later profile cannot inherit context, and even the same owner
must obtain a fresh server snapshot after reconnecting.

Server deletion cannot guarantee remote wipe of an offline, dead or never-reconnecting device. A
device copy is purged only after an authoritative delete receipt is actually delivered or through an
explicit local reset, uninstall, OS erasure or physical destruction. Server completion never claims
that local purge occurred.

The owner export may include policy/pipeline version, stable dimension kind/key, score, confidence,
maturity components, momentum, fatigue, cutoff, watermark, time classification and bounded event
IDs/hashes. It excludes secrets, private URLs, raw paths, raw search queries, model tensors and all
other-owner data. `owner-export.schema.json` is the strict page representation and
`owner-export-manifest.schema.json` is the terminal manifest. A page contains at most 100 profiles,
100 snapshot-lineage records, ten event-lineage entries per snapshot, 1,100 counted items and 1 MiB
of canonical JSON; `item_count` equals those nested counts. The completed manifest binds the owner
and immutable export ID to a unique ordered hash for every contiguous page. Reordered, missing,
duplicate, hash-mismatched or oversized pages keep the privacy request open and forbid completion.

## 12. Proposed additive implementation impact (not implemented)

R1B may propose PostgreSQL rows equivalent to normalized temporal events, adaptive profiles and
immutable temporal snapshots, plus nullable adaptive snapshot/feature-policy references on the
existing recommendation request. It also requires the restricted privacy-delete function and the
owner/event projection across current sync, interaction, listening and preference tables. It may
propose one owner/profile/device/pack-bound Room local delta with expiry and purge. Exact names
remain a migration design choice subject to R1B review.

No public recommendation DTO change is required. A bounded owner export projection is the only
optional additive API surface. R1B must reuse P04/P09 attribution and P11 request/impression paths;
it must not create a second feedback route.

## 13. Determinism and compatibility

RFC 8785 canonical JSON plus SHA-256 freezes the complete contract policy, feature policy, normalized evidence, profile, source
list, event-time policy, derived features, snapshot, parent items, impression key, offline delta,
export page and export-manifest vectors.
`source_request_sha256` remains the original accepted sync request hash;
`normalized_evidence_sha256` is a distinct self-hash of the R1 evidence document. Unknown
dimension values are preserved for forward compatibility but not scored. Schema version, policy
version and content hash travel together. A changed policy is a new immutable version; changing
weights in place is forbidden.

R1 remains owner-isolated, CPU-capable and compatible with P11. Mandatory owner authorization,
availability, active identity, Dislike and exclusion filters run before adaptive scoring. Diversity,
artist concentration, repeat control, causal attribution and stable impression rules remain P11/P04
authorities and cannot be weakened by this contract.

## 14. Shadow evaluation and rollback

R1B is shadow-only. It must create no served item and no impression. A fixed dataset and the
language-neutral scenarios must prove:

- 100% expected directional outcomes and zero mandatory-filter, owner-isolation or replay mismatch;
- NDCG@10 regression no worse than 0.01 against P11;
- diversity regression, artist-concentration increase and repeat-rate increase each no worse than
  0.02;
- p95 CPU latency no more than 300 ms and no more than 1.25 times the paired P11 baseline;
- deterministic hashes, cutoffs, watermarks, event order, delayed sync, skew, retention, export,
  unbinding and deletion behavior.

Any safety/privacy violation, nonzero shadow impression, algorithmic replay mismatch or unavailable
P11 CPU fallback fails R1B. R1C requires a separate explicit user decision, version allowlist,
bounded cohort, observable rollback switch and unchanged P11 fallback. Rollback disables the
adaptive policy; it does not rewrite historical requests or delete required privacy evidence.

## 15. Acceptance and stop boundary

R1A reached acceptance on 2026-09-03 after executable contract/release tests, an independent
recommendation/data/privacy review with zero unresolved Critical/Major findings, and explicit user
acceptance of this contract and ADR-048.

After R1A acceptance, R1B becomes eligible but does not start automatically. R1C remains separately
gated even after a successful R1B.
