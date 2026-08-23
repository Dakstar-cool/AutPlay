# AutPlay Discovery and Acquisition Contract v1

| Field | Value |
| --- | --- |
| Status | ACCEPTED CONTRACT; RUNTIME NOT IMPLEMENTED |
| Version | 1.0 |
| Date | 2026-08-21 |
| Scope | Post-MVP A1A provider-neutral release discovery and authorized acquisition boundary |
| Contract ID | `release-discovery-v1` |

## 1. Authority and scope

This contract is the narrower specification for Post-MVP A1 controlled library expansion. It
defines the application and administrative Web boundary before a provider is selected. Product
security/privacy rules, physical Catalog/Identity/Vault/Library/Jobs schemas, the M6 Web security
contract and accepted identity/recommendation ADRs remain authoritative.

A1A adds contract artifacts only. It does not add a route, migration, worker, provider credential,
Catalog row, library entry or served recommendation. A1B may implement manual discovery and import
only after the prerequisite in section 15 is met. A1C separately owns scheduled discovery and
automatic import activation.

## 2. Permanent invariants

- Android local library and playback never require a synchronous server request.
- External metadata is versioned evidence, not a canonical Artist, Release or Recording.
- Discovery, identity, authorized acquisition, ingest, owner materialization, analysis and audience
  exposure are distinct transitions.
- Metadata discovery never implies permission to obtain playable audio.
- An external ID, ISRC, fingerprint, source location or hash never grants authorization.
- Uncertain identity is review-required; F-016 automatic probabilistic matching stays disabled.
- `VaultObject`, `AudioVariant`, `Recording`, `ReleaseTrack` and `UserTrackRef` remain distinct.
- New music creates no impression and receives no global audience exposure or permanent novelty
  boost merely because it was discovered or imported.
- CPU ingest, playback and baseline recommendation eligibility never depend on GPU or optional
  derived analysis.
- Credentials, private source URLs, raw paths and provider payloads are absent from routine Web,
  job, audit, log, diagnostic and export values.

## 3. Ownership and module boundaries

### 3.1. Owner derivation

Every Web query and command derives `owner_user_id` from the authenticated `WebActor.user_id`.
Request bodies, path parameters, filters and cursors never accept an owner override. `OWNER` and
`ADMIN` roles are administrative roles inside one account boundary, not cross-account authority.

An eligible artist is a canonical `catalog.artist.artist_id` reachable from the active owner's
library through live `LibraryEntry -> UserTrackRef -> Recording -> ArtistCreditName` relations.
Display name and `artist_credit_id` are not policy keys. A foreign or no-longer-reachable artist is
reported through the same non-disclosing `discovery_target_not_found` outcome.

Every query, command and worker step rechecks row owner against actor/job owner. Materialization may
create or coalesce a library projection only for that owner.

### 3.2. Bounded context ownership

A future additive `discovery` context owns:

- per-owner/per-canonical-artist policy and immutable policy revisions;
- discovery run/checkpoint projection;
- provider release and track candidate projection;
- candidate selection and acquisition-attempt projection.

It does not repurpose `importing.import_job` or `importing.import_entry`. Existing contexts retain:

| Context | Existing authority retained |
| --- | --- |
| Identity | provider registry, external reference, source observation, match evidence/decision |
| Catalog | Artist, Recording, Release, Medium and ReleaseTrack canonical truth |
| Vault/Ingest | acquired bytes, validation, hash, AudioVariant and acquisition record |
| Library/Sync | owner UserTrackRef, LibraryEntry and synchronized materialization fact |
| Jobs | lease, retry, checkpoint, fencing and delivery only |
| Recommendations | owner-authorized candidate snapshot, filters, rank and impression semantics |
| Web | presentation and typed application command/query adaptation only |

The Web layer never calls a provider or writes persistence, Vault, job or identity state directly.

## 4. Policy contract

Policy is keyed by `(owner_user_id, canonical_artist_id)` and has an immutable positive revision.
The safe default is:

```text
discovery_mode = MANUAL_ONLY
import_mode = REVIEW_REQUIRED
```

Supported contract values are:

- `discovery_mode`: `DISABLED`, `MANUAL_ONLY`, `SCHEDULED`;
- `import_mode`: `REVIEW_REQUIRED`, `AUTO_IMPORT`.

A1B runtime supports only `MANUAL_ONLY + REVIEW_REQUIRED`. It must reject `SCHEDULED` and
`AUTO_IMPORT` with `automation_not_active` until A1C is separately accepted. Preserving these values
in v1 avoids an incompatible contract rewrite when A1C begins; it does not activate them.

Every automatic-origin job captures its authorizing policy revision. Policy is revalidated before
enqueue, provider I/O, Vault publication and owner materialization. A stale revision cannot cross
the next irreversible boundary. Disabling policy never deletes previously acquired music.

## 5. Provider capability and source authorization

### 5.1. Capability separation

An adapter manifest declares independent capabilities:

- `RELEASE_DISCOVERY`: bounded release/track metadata discovery;
- `PLAYABLE_ACQUISITION`: technical ability to deliver bytes through an accepted acquisition path.

`PLAYABLE_ACQUISITION` is not authorization. It is usable only with a current owner-scoped source
authorization record containing the exact provider/adapter/version, market scope, rights capability,
authorization revision, policy reference and expiry/revocation state. The source locator and
credentials stay behind the adapter/secret boundary and are represented only by opaque references.

`provider_id` is the immutable physical identity used by durable uniqueness and authorization.
`provider_key` is a bounded presentation alias only: renaming it cannot change provider identity,
coalesce candidates or authorize access. Adapter version remains a separate versioned evidence key.

### 5.2. Rights truth table

| Discovery capability | Acquisition capability | Current owner authorization | Candidate outcome |
| --- | --- | --- | --- |
| absent | any | any | adapter unavailable; no run |
| present | absent | any | metadata visible; `UNAVAILABLE/no_authorized_playable_source` |
| present | present | absent/expired/revoked | metadata visible; `UNAVAILABLE/source_authorization_unavailable` |
| present | present | valid | identity/dedupe evaluation; selectable only after all other gates pass |
| present | existing Vault reuse | valid independent authorization | ADR-019 reuse may be offered with owner acquisition lineage |

Authorization is rechecked before network access, before Vault commit and before owner
materialization. Provider enablement, exact adapter version and market scope must still match.

## 6. Durable state machines

The earlier product supplement listed a useful Web-facing status vocabulary. A1A refines it into
orthogonal durable state so a job result, identity decision, acquisition result and analysis result
cannot masquerade as one another. Web status is a derived projection and never the source of truth.

### 6.1. Discovery run

```text
QUEUED -> RUNNING | CANCELLED
RUNNING -> COMPLETED | PARTIAL | RETRY_WAIT | FAILED_TERMINAL | CANCELLED
RETRY_WAIT -> QUEUED | CANCELLED
```

Terminal runs are never reopened. A manual rerun creates a new run with a new operation identity and
may reuse the last eligible provider checkpoint without bypassing cadence or rate limits.

### 6.2. Candidate disposition

```text
DISCOVERED -> ALREADY_IN_LIBRARY | IDENTITY_REVIEW_REQUIRED | SELECTABLE | UNAVAILABLE
IDENTITY_REVIEW_REQUIRED -> SELECTABLE | ALREADY_IN_LIBRARY | UNAVAILABLE | IGNORED
SELECTABLE -> SELECTED | ALREADY_IN_LIBRARY | UNAVAILABLE | IGNORED
SELECTED -> SELECTABLE | ALREADY_IN_LIBRARY | UNAVAILABLE | IGNORED
UNAVAILABLE -> SELECTABLE | ALREADY_IN_LIBRARY | IDENTITY_REVIEW_REQUIRED | IGNORED
IGNORED -> SELECTABLE | IDENTITY_REVIEW_REQUIRED | UNAVAILABLE
ALREADY_IN_LIBRARY -> SELECTABLE
```

Every edge into `SELECTABLE` or `SELECTED` is guarded by owner match, current canonical-artist
eligibility, resolved identity, current playable authorization and active policy. Reconsideration additionally requires the edge-specific durable
cause: new identity evidence from `IDENTITY_REVIEW_REQUIRED`, explicit owner reconsideration from
`SELECTED` or `IGNORED`, new source authorization/evidence from `UNAVAILABLE`, or removal of the
owner's active library membership from `ALREADY_IN_LIBRARY`. `SELECTABLE -> SELECTED` additionally
requires an explicit owner action and current candidate revision. Exact replay never manufactures
any guard.

Canonical-artist eligibility is rechecked again before owner materialization. If the artist is no
longer reachable from the owner's active library, the operation returns the same non-disclosing
`discovery_target_not_found` result and creates no acquisition or owner-library write.

`DUPLICATE_EVIDENCE` is an observation disposition, not a candidate state. A replayed provider page
or observation is coalesced into the existing candidate and records a bounded duplicate count/reason.

### 6.3. Acquisition and materialization

```text
QUEUED -> ACQUIRING | CANCELLED | FAILED_TERMINAL
ACQUIRING -> INGESTING | RETRY_WAIT | FAILED_TERMINAL | CANCELLED
INGESTING -> MATERIALIZING | RETRY_WAIT | FAILED_TERMINAL
MATERIALIZING -> READY | RETRY_WAIT | FAILED_TERMINAL
RETRY_WAIT -> QUEUED | CANCELLED
```

Retry preserves acquisition operation identity. A stale lease cannot publish a transition. `READY`
is terminal for that acquisition lineage.

`READY` requires all of the following to be committed or connected by the accepted recoverable
Vault publication protocol:

1. one active canonical Recording and non-ambiguous identity decision;
2. one `VALID` AudioVariant backed by a `COMMITTED` VaultObject with an available replica;
3. owner-specific acquisition provenance and source authorization revision;
4. a coalesced resolved UserTrackRef and active LibraryEntry for the owner;
5. the existing owner sync/outbox fact for that materialization;
6. a durable standard-analysis job enqueue;
7. the candidate acquisition projection marked `READY` in the owner transaction.

`jobs.job.COMPLETED`, successful download, hash verification or Vault publication alone is not
`READY`.

### 6.4. Analysis

Analysis state is absent before a standard-analysis job exists, then follows:

```text
QUEUED -> RUNNING
RUNNING -> COMPLETE | PARTIAL | FAILED_RETRYABLE | FAILED_TERMINAL
FAILED_RETRYABLE -> QUEUED
```

Analysis may become partial or fail after core readiness. It cannot roll back `READY`, playable
media or baseline recommendation eligibility. Optional enrichment is not part of the readiness
transaction.

## 7. Evidence, identity and deduplication

Discovery release/track values remain provider evidence until Identity Catalog resolves them. A
candidate is unique by `(owner_user_id, provider_id, market_scope,
external_track_reference_id)`. Provider page evidence is unique by adapter/version, canonical query
hash and cursor/page identity.

Deduplication is explicit at these layers:

1. exact provider page/cursor replay and source-observation replay;
2. provider-native external release/track ID in a versioned namespace;
3. owner library membership by canonical `recording_id`;
4. exact valid-byte reuse under ADR-019 and independent authorization;
5. versioned external ID, ISRC, fingerprint and metadata identity evidence;
6. release/medium/position association through ReleaseTrack.

Provider IDs, metadata or artist names never create or merge a Recording. An existing global
Recording absent from the owner's library may be offered only when identity, playable availability
and owner authorization are each independently proven. ImportEntry and UserTrackRef identity
decision lineages remain distinct.

## 8. Commands, queries and Web contract

All Web pages remain server-rendered, no-store and protected by the accepted M6 cookie, exact-Origin,
CSRF, operation-id, RBAC, CSP, escaping, audit and redaction rules.

Application queries are bounded and support only contract fields:

- candidates/releases: canonical artist ID, immutable provider ID, release date interval, discovered-at
  interval, candidate disposition, acquisition state, analysis state, source availability and
  opaque cursor;
- artist policy: canonical artist ID and effective modes/revision;
- run status: run ID, safe provider code, state, bounded counts/checkpoint summary and next eligible
  time.

The maximum page size is 200, provider page size 100, artists per manual run 100, releases per
provider page 100 and tracks per release 500. Provider requests use no more than 60 seconds each;
the selected A1B adapter may impose smaller limits.

Mutations are `start_discovery`, `retry_discovery`, `select_candidate`, `ignore_candidate` and
`set_artist_policy`. A1B may implement the first four plus the safe manual policy. Every mutation
binds `(owner_user_id, operation_id, server_computed_canonical_request_sha256)`. After strict schema
validation rejects unknown or wrong-action fields, the application computes RFC 8785/SHA-256 over
`contract_version`, `schema_version`, `operation_id`, `action` and the exact action-specific fields.
The owner remains a separate tuple member. Cookies, session/CSRF values, locale, presentation
fields and any client-claimed digest are excluded; the browser cannot supply the digest. Same hash
returns the stored result; same operation with another hash returns `operation_conflict`.

The contract exposes explicit redacted owner-scoped projections for candidates, releases, runs and
artist policies. This internal SSR application contract is not a new public REST/OpenAPI surface.
Only bounded display title, artist credit, release title and release date may be rendered, as
escaped text on the owner-scoped no-store A1 page. They are never trusted HTML or routine
log/audit/export fields, and arbitrary provider extensions or raw provider payloads cannot cross
the Web response boundary.

## 9. Jobs, retries and crash consistency

Discovery and acquisition jobs carry `user_id` plus opaque domain row IDs only. They never carry
credentials, source URLs, media metadata or unrestricted provider payloads. Existing PostgreSQL
lease ownership, attempt number, heartbeat, checkpoint, retry classification and cancellation
fields are reused.

Concurrent manual and future automatic selection converges on one active acquisition operation per
candidate/source-authorization revision. A retry keeps the same operation lineage. Existing unique
Vault hash, active `(user_id, recording_id)` and owner materialization coalescing are final
idempotency guards, not substitutes for application idempotency.

If Vault publication succeeds and the database transaction does not, recovery re-enters through the
existing reconcile/reuse boundary. It never reports `READY` until section 6.3 is true. Lost response
returns the durable terminal result only for an exact operation/hash replay.

## 10. Stable errors and redaction

The contract defines these stable lowercase errors:

```text
automation_not_active
discovery_adapter_unavailable
discovery_target_not_found
discovery_not_eligible
source_authorization_unavailable
no_authorized_playable_source
identity_review_required
candidate_not_selectable
operation_conflict
policy_revision_stale
lease_fence_lost
provider_rate_limited
provider_timeout
provider_schema_invalid
acquisition_failed_terminal
```

Safe errors include only code, retryability, optional bounded retry-after seconds and a non-linkable
correlation code. Raw exception messages, identifiers that disclose another owner, provider
payloads, media metadata, source locations, credentials and paths are excluded.

## 11. Recommendation and audience boundary

Discovery, candidate display, selection, acquisition, pack delivery and analysis enqueue create no
impression. An acquisition origin is recorded for provenance but is not a permanent score boost.

After `READY`, the Recording is an ordinary owner-authorized, available Catalog candidate. It may
participate in artist-affinity/freshness generation for that owner and then passes the existing ACL,
availability, active identity, Dislike, explicit exclusion, diversity and repeat filters. It is not
inserted into every Home feed and is never materialized into another user's library merely because
the Catalog/Vault object is shared.

## 12. Proposed additive implementation impact

The following is proposed design under A1A review, not implemented in A1A:

- additive `discovery` PostgreSQL schema for policy/revision, run, provider page evidence, release,
  track candidate, selection and acquisition projection;
- pure domain values and ports for provider discovery, independently authorized acquisition,
  owner queries/commands and analysis enqueue;
- M6 application DTOs and SSR routes/templates for bounded discovery/review/filter/status;
- CPU worker handlers reusing generic Jobs, Vault/Ingest, Identity, Library/Sync and analysis seams;
- no Android schema or synchronous local-action dependency.

Any migration is additive from current Alembic head, preserves unknown values and has no destructive
fallback. A1B owns its exact physical design and real-PostgreSQL evidence.

## 13. Privacy, retention and deletion

Provider evidence and candidate projections are owner-scoped personal operational data. The A1B
physical contract must define bounded retention for raw observations, page checkpoints, failed
attempts and safe audit. Disabling policy does not delete acquired music. Privacy deletion removes
or irreversibly minimizes owner-specific discovery/acquisition projections under the existing
authorized privacy boundary without deleting a shared canonical Recording or VaultObject still in
use by others.

Unknown provider fields are preserved only inside bounded versioned evidence, never rendered or
trusted as commands. Credentials and private source locations are never part of this evidence.

## 14. Acceptance evidence

Language-neutral schemas, deterministic examples and scenario vectors must prove:

- all allowed and forbidden transitions;
- discovery-only and independently authorized acquisition truth;
- actor-derived owner isolation for every query/command/job/materialization path;
- page replay, exact operation replay and divergent operation conflict;
- no duplicate candidate/materialization under concurrency, timeout, crash or lost response;
- uncertain identity review and zero automatic merge;
- revocation/stale-policy/stale-lease blocking at every irreversible boundary;
- exact readiness and analysis-failure behavior;
- redaction and bounded list/filter/error values;
- no impression, global audience broadcast or cross-owner library projection;
- CPU API/worker and Android remain usable with discovery disabled or unavailable.

## 15. Exact A1B prerequisite

A1B is blocked until all of these are recorded and accepted together:

1. this A1A handoff is PASS;
2. one named adapter/provider and exact adapter version;
3. documented official interface and egress allowlist;
4. separate declared `RELEASE_DISCOVERY` and `PLAYABLE_ACQUISITION` capabilities;
5. jurisdiction/market/terms-specific rights basis for playable acquisition;
6. credential storage, rotation, revocation and redaction policy, if credentials are required;
7. bounded rate, pagination, timeout, retry/backoff and checkpoint policy;
8. deterministic sandbox/fixture strategy that uses no real credentials or paid resource;
9. accepted retention/privacy policy for provider evidence and acquisition provenance.

If an adapter offers discovery but no authorized playable acquisition, A1B may expose metadata-only
candidates but cannot claim the requested add-to-library path is delivered.

## 16. Accepted decision record

The initiating user explicitly activated implementation and authorized in-scope decisions on
2026-08-21. Milestone acceptance still requires the complete A1A gate and zero unresolved
Critical/Major review findings. That authorization does not override the standing stop boundary for
choosing an ambiguous external provider, credentials, paid resource or jurisdiction-specific
acquisition policy. Those remain the formal A1B blocker above.
