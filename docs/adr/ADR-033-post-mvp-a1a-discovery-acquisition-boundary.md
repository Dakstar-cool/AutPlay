# ADR-033: A1A discovery, acquisition and readiness boundary

- Status: Accepted
- Date: 2026-08-21
- Scope: Post-MVP A1A contract; no runtime/provider activation
- Decision owner: initiating user authorization plus standing in-scope technical authorization

## Context

AutPlay already has provider-neutral metadata seams, immutable identity evidence, authorized Vault
ingest, owner library materialization, generic leased jobs, a deterministic recommendation pipeline
and an accepted administrative Web security boundary. It does not have a durable discovery context
or a selected provider with an accepted playable-acquisition rights policy.

The post-MVP product supplement described one convenient list of track statuses. Directly storing
that list as one state machine would mix provider delivery, identity disposition, acquisition/ingest,
owner materialization and optional analysis. It could make a completed job or successful download
look like a fully added track. The existing P10 import tables also have a different owner/workflow
and identity lineage and cannot safely be reused as a release watcher.

The existing P10 `PUBLIC_METADATA` capability does not distinguish finding release metadata from
permission to obtain playable bytes. The physical provider registry already anticipated separate
`RELEASE_WATCH`, `DOWNLOAD` and `IMPORT` concepts, but technical capability alone cannot establish
owner authorization or jurisdiction-specific rights.

## Decision

1. Create a separate bounded `discovery` context for future owner policy/revisions, runs, provider
   pages, release/track candidates, selections and acquisition projections. Do not repurpose
   `importing.import_job` or `importing.import_entry`.
2. Keep existing module ownership: Identity owns evidence/decisions; Catalog owns canonical music;
   Vault/Ingest owns bytes and variants; Library/Sync owns owner projections; Jobs owns delivery;
   Recommendations consumes only ready owner-authorized Catalog state; Web is an adapter only.
3. Derive owner only from `WebActor.user_id`. Canonical `artist_id`, proven reachable from the
   owner's active library, is the policy key. Names and artist-credit IDs are never identity keys.
4. Introduce separate provider-neutral capabilities `RELEASE_DISCOVERY` and
   `PLAYABLE_ACQUISITION`. The latter is technical capability, not authorization. Each acquisition
   additionally requires a current owner-scoped source-authorization revision with exact provider,
   adapter/version, market and named rights capability.
   Immutable `provider_id` is the durable authorization/uniqueness key; `provider_key` is only a
   bounded presentation alias and a rename never changes identity.
5. Represent durable truth with four orthogonal state machines: discovery run, candidate
   disposition, acquisition/materialization and analysis. Web status is derived from them.
   Duplicate evidence is coalesced evidence, not a second candidate state. Every transition into
   selectable/selected state and the final owner materialization rechecks that the canonical artist
   is still reachable from the owner's active library.
6. Define `READY` only after an active canonical Recording, playable valid Vault-backed variant,
   owner acquisition lineage, owner UserTrackRef/LibraryEntry, sync/outbox fact, standard-analysis
   enqueue and owner candidate projection reach the accepted atomic/recoverable boundary.
   `jobs.job.COMPLETED` is insufficient. Optional analysis failure never rolls back readiness.
7. Preserve F-016. Provider ID, ISRC, metadata and fingerprint evidence cannot auto-merge. ADR-019
   exact-byte reuse remains a technical CAS path and requires independent owner authorization plus
   owner-specific acquisition lineage.
8. Bind every Web mutation to owner, operation UUID and an application-computed RFC 8785/SHA-256
   canonical request hash. Strict schema validation precedes hashing; the browser cannot supply the
   digest. Provider page replay and candidate identity have separate durable uniqueness. Jobs reuse current owner, lease,
   checkpoint, retry and fencing rules and revalidate authority before each irreversible boundary.
9. Preserve `SCHEDULED` and `AUTO_IMPORT` as versioned contract values but require runtime rejection
   until A1C. A1B is manual/review-only.
10. Treat discovery/acquisition as provenance, not audience exposure. No step creates an impression
    or cross-user/global Home insertion. A ready track enters the ordinary owner-authorized
    recommendation snapshot and existing mandatory filters.
11. Expose candidates, releases, runs and artist policies only through explicit owner-scoped
    redacted projections. Bounded music display strings render as escaped text on no-store pages;
    arbitrary extensions, trusted HTML and raw provider payloads never cross the Web boundary.

## Product-design clarification

Section 4.3 of the post-MVP supplement called its flat statuses "suggested". Splitting them into
orthogonal durable state is a compatible clarification, not a change to the requested user-visible
capability. The Web projection still exposes found, selectable, selected, acquiring, ingesting,
ready, retryable/terminal failure, ignored, already-present and identity-review outcomes without
allowing one subsystem to claim another subsystem's success.

## Consequences

### Positive

- Metadata-only discovery cannot be mistaken for an authorized add-to-library path.
- Owner isolation and revocation can be enforced at each query, command and worker boundary.
- Retry/crash recovery cannot manufacture `READY` from a generic job outcome.
- Existing identity, Vault, library, recommendation and Web security contracts remain reusable.
- A discovery-only provider can be supported honestly while its tracks remain unavailable to add.

### Negative

- A1B needs additive discovery persistence rather than reusing the existing import tables.
- The UI must compose several durable states into one concise presentation.
- Selecting the first useful adapter remains blocked on provider, credentials and rights decisions.

## Rejected alternatives

- One flat status column: conflates evidence, identity, acquisition, materialization and analysis.
- Reuse P10 ImportEntry: wrong workflow/lineage and encourages hidden owner projection.
- Treat `PUBLIC_METADATA`, external IDs, URLs or hashes as acquisition authority: violates F-023,
  F-024 and object authorization.
- Create Catalog rows at discovery time: external evidence is not canonical truth.
- Let Web call a provider/Vault or write SQL/jobs: violates the accepted M6/application boundary.
- Global library materialization or broadcast: violates owner isolation and recommendation policy.
- Add a broker, microservice or object store: no measured need exists.

## Compatibility and implementation effect

A1A adds only documentation and executable contract artifacts. It makes no PostgreSQL/Room/API
runtime change. A1B must use an additive migration from the then-current Alembic head, preserve
unknown values and provide real-PostgreSQL concurrency/idempotency evidence. Android remains
unchanged and local-first.

## Approval and next boundary

The initiating user explicitly activated this implementation and authorized in-scope decisions on
2026-08-21. The A1A gate and final independent re-review passed with zero Critical/Major/Minor. The decision
does not select an external provider, accept credentials, use a paid resource or decide
jurisdiction-specific acquisition rights. A1B remains blocked until the exact prerequisite in the
versioned contract is accepted.
