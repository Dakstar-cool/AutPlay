# ADR-023: P10 Import, Identity and Review Runtime Boundary

- Status: Accepted
- Date: 2026-08-17
- Decision owner: standing in-scope technical-decision authorization

## Context

P02 already made the import-job, import-entry, immutable identity decision/evidence, source
provenance, fingerprint and catalog change-set schemas executable. P06 separately established the
ADR-019 exact-byte Vault reuse path. P10 must now add resumable user-owned imports, explainable
candidate evaluation, offline review and reversible catalog-change seams without creating a second
identity ledger, weakening frozen F-016 or silently selecting a service-specific acquisition
provider.

The product prompt uses `RESOLVED` as a user workflow state, while the physical schema distinguishes
resolver states, import workflow projections and UserTrackRef resolution. These vocabularies must
remain separate so a parser result, shadow evaluation or user review cannot masquerade as an
approved probabilistic auto-match.

## Decision

1. Keep the PostgreSQL Alembic head at `0012_sync_runtime`. P10 reuses
   `importing.import_job`/`import_entry`, `identity.match_decision`/
   `match_candidate_evidence`, the immutable release/policy registries, generic leased jobs,
   fingerprint provenance and `audit.catalog_change_set`/`catalog_change_item`. Versioned bounded
   checkpoint, report and raw-row documents fit their existing JSON contracts, so a duplicate P10
   schema is not introduced.
2. Parse user-owned CSV, JSON and HTML through one bounded versioned generic-export adapter. An
   invalid outer envelope or unsupported schema rejects the job; an invalid row is retained with a
   stable row key and reason code and does not discard other rows. The exact input SHA-256,
   adapter/parser versions and sanitized raw provenance make replays deterministic.
3. Define Source Adapter capabilities, limits, authentication and provenance as ports. P10 ships
   local MediaStore/SAF and generic user-export implementations plus a transport-independent public
   metadata lookup seam. It does not choose or enable a live service-specific provider, scrape a
   private page, store credentials in source references or implement acquisition without a later
   provider/policy decision.
4. Candidate generation is bounded to 100 canonical Recordings and retains every generator origin.
   Normalization preserves raw display metadata and explicit version markers. Scoring stores
   feature presence/version, hard conflicts, fingerprint algorithm/version, top-one/top-two scores,
   margin and a provider-independent explanation.
5. All P10 probabilistic evaluations are `SHADOW`. Before a complete labeled production gate,
   candidate-bearing results are review-only, low-scoring results are `NO_MATCH`, integrity and
   unavailable-evidence results retain their exact states, and no policy activation event is
   inserted. ADR-019 T4 remains technical Vault reuse plus shadow identity evidence; it never becomes
   `AUTO_MATCH` or a hidden owner projection.
6. Product `RESOLVED` maps to a validated owner projection. In P10 this is normally
   `MANUAL_MATCH` plus UserTrackRef `RESOLVED`, produced by an append-only applied
   `REVIEW_ACTION`. `REJECTED` remains parser-invalid, `NO_MATCH` remains an identity result, and
   `MANUAL_UNRESOLVED` records an explicit user choice. An ImportEntry decision is never reused as a
   UserTrackRef decision because typed query lineages are distinct.
7. Android advances additively from Room v7 to v8 with profile-scoped import job/entry and immutable
   local decision/candidate projections. A review transaction appends the local review fact, updates
   the import/UserTrackRef projection and records durable standalone-outbox intent atomically. It
   does not overload a frozen P04 event with new identity semantics. End-to-end review delivery may
   be added only through an explicit additive contract and specialized server command.
8. The matcher may only propose a `PLANNED` catalog change set. Global merge, split, reassign and
   undo remain explicit authorized commands. Apply locks affected Recordings in stable order, writes
   redirects/items/audit atomically and preserves user references. Undo consumes stored snapshots in
   a new reviewed change set and refuses divergent later dependencies instead of guessing an inverse.
9. The P10 benchmark runner always emits deterministic dataset/version hashes, confusion counts,
   precision/recall, hard-negative and error slices. Fixture-scale evidence cannot activate
   auto-match; the complete Track Identity sample-size and production gate remain prerequisites.

## Consequences

- PostgreSQL inventory and migration history remain unchanged while P10 gains executable runtime
  behavior over the already verified identity schema.
- Every imported row and user review remains auditable and resumable without creating a poor global
  Recording or silently merging live/remix/edit/remaster variants.
- Android can review and preserve intent offline; unresolved delivery state stays visible until a
  compatible server contract handles it.
- A later live metadata provider requires an explicit provider, policy, rate-limit and legal review;
  the generic P10 port and fixtures do not imply such approval.
- P11 recommendation work and P12 GPU/model work remain out of scope.

## Rejected alternatives

- Add a P10 deterministic-resolution table: duplicates ADR-015 history and increases migration risk.
- Encode exact bytes as `AUTO_MATCH`: violates ADR-019 and frozen F-016.
- Activate shadow thresholds from the small P10 fixture set: does not meet the production gate.
- Reuse an ImportEntry decision as the UserTrackRef current pointer: violates typed query ownership.
- Overload `USER_TRACK_REF_PATCHED` with review evidence: changes frozen sync semantics and cannot
  satisfy the immutable predecessor/candidate contract.
- Let a matcher directly apply a global merge: conflates owner resolution with catalog mutation.
- Add a broker, identity microservice or external vector database: unnecessary for the modular
  monolith and current measured scale.
