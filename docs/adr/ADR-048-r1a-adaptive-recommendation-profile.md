# ADR-048: Versioned adaptive recommendation profile and temporal evidence

Status: Accepted by the user on 2026-09-03  
Date: 2026-09-03  
Owners: AutPlay recommendation, data and privacy boundaries

## Context

P11 serves a deterministic CPU baseline with immutable recommendation evidence and replay. Its
aggregate input snapshot is intentionally insufficient for episode/12-hour/24-hour/3-day/7-day
adaptation. Current interaction events contain most completion, origin, feedback, exclusion and
attribution facts, but the recommendation projection does not preserve a normalized three-time
history. Some desired signals, especially voluntary repeat and selection without a finalized
listen, cannot be inferred reliably.

AutPlay needs faster evidence-aware adaptation for immature profiles without turning temporary
context into durable taste, multiplying events across overlapping windows, weakening mandatory
filters or retaining personal data solely for replay.

## Decision

If the user accepts R1A, future R1 implementations will:

1. add an owner/profile/device-scoped normalized temporal evidence boundary with distinct source
   event and evidence identities, occurrence, receipt, effective times and server sequence; client
   occurrence time never authorizes inclusion, but classified effective time orders accepted events;
2. represent durable long-term affinity separately from recent episode/12h/24h/3d/7d context;
3. keep signed score, confidence and evidence-based maturity separate, with bounded plasticity that
   decreases monotonically as maturity rises;
4. conserve each event's evidence mass across active overlapping horizons and conserve each causal
   action chain across recommendation selection, attributed listen and derived outcome;
5. preserve P11 as the mandatory CPU fallback and evaluate owner, availability, identity, Dislike
   and taste-exclusion filters before any adaptive adjustment;
6. freeze every weight, threshold, cap, six-term maturity calculation, outcome classifier and time
   rule in an immutable hashed feature-policy version;
7. require the original snapshot and policy for algorithmic replay, never substitute current state,
   and honor deletion even when replay becomes unavailable;
8. allow only a bounded, expiring, owner/profile/device/pack-bound offline local delta that preserves
   source rank and one impression at actual presentation; unsynced local events carry no server
   sequence and cannot enter a server snapshot until later applied;
9. keep signals without reliable capture deferred until an explicit additive capture contract
   exists.
10. make accepted owner deletion override snapshot retention only through a restricted privacy
    function, delete children in an FK-compatible fixed order, reapply keyed deletion tombstones
    before routing a restored backup, and never claim remote wiping of an offline/dead device;
11. require bounded owner-export pages plus a contiguous, unique, hash-linked terminal manifest.

R1A itself implements none of these runtime or persistence changes.

## Consequences

Positive consequences:

- cold-start profiles can adapt more quickly without registration-age heuristics;
- mature profiles remain stable while recent context remains available;
- score, confidence, momentum, fatigue and maturity have auditable distinct meanings;
- delayed sync, clock skew, replay, export, unbinding and deletion are deterministic;
- evaluation and rollback compare the candidate directly with the P11 CPU baseline.

Costs and constraints:

- R1B needs additive normalized temporal storage and deterministic backfill/projection work;
- the projection must join current inbox, sync-sequence, interaction, listening and preference
  evidence; no current table or P11 snapshot is replay-complete;
- a single interaction may be observable in several windows but must be fused only once;
- retained snapshots and source lineage are bounded and expire, so algorithmic replay can become
  unavailable;
- privacy deletion intentionally makes both replay modes unavailable for deleted personal evidence;
- voluntary-repeat and selection-only behavior remain unavailable until capture is improved.

## Alternatives rejected

- **Registration-age cold start:** age is not evidence maturity.
- **One mutable preference vector:** it conflates durable taste and temporary direction and cannot
  explain replay.
- **Raw sum across windows:** it multiplies one event merely because windows overlap.
- **Score magnitude as confidence:** it makes uncertainty indistinguishable from strong preference.
- **Infer voluntary repeat from adjacent listens:** queue and autoplay produce the same observation.
- **Treat Search/Playlist queue origin as voluntary selection:** later queue entries carry the same
  origin without a user-initiated selection fact.
- **Retain deleted evidence for replay:** replay does not override the privacy boundary.
- **Require GPU/vector storage:** the accepted serving fallback remains CPU-only P11.

## Acceptance

The user accepted this ADR and the R1A contract on 2026-09-03 after the R1A tests and independent
review passed. Acceptance authorizes only the contract; R1B and R1C still require separate
activation.
