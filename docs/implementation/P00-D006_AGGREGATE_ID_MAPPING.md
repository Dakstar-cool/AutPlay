# P00-D006 - Local/server aggregate ID mapping

**Status:** ACCEPTED

**Approved core:** 2026-08-15 by explicit user confirmation, `Утверждаю вариант A`

**Approved redirect detail:** 2026-08-15 by explicit user confirmation, `Утверждаю P00-D006-R1`

**Owning contract phase:** P04

## Context

Frozen decision F-017 requires an Android local aggregate ID to remain unchanged after server ACK or merge. The Room design therefore stores a stable `local_id` plus a nullable unique `server_id`, and each Offline Journal row captures both `aggregate_local_id` and nullable `aggregate_server_id`. PostgreSQL sync tables expose one `aggregate_id`, which must have one unambiguous meaning across inbox processing, the canonical change stream, cursors, conflicts and tombstones.

This decision resolves that mapping before P04. It does not create the Sync Protocol v1 artifacts or implement either sync engine.

## Accepted mapping

### Identity roles

- `aggregate_local_id` is the immutable Android row/Journal identity. It is required in every device push event, is echoed in the per-event ACK for local correlation, and is never an authorization credential or a server lookup substitute when a server ID is known.
- `aggregate_server_id` is the canonical server aggregate identity. It is nullable only while a client-created aggregate has not yet been bound by an ACK.
- Android never replaces `local_id`. ACK, pull or bootstrap may bind `server_id`; merge may update redirect projection state but must not create two rows with the same unique `server_id`.

### Push and canonicalization

1. A device push event carries both `aggregate_local_id` and `aggregate_server_id`, with an explicit JSON `null` when the server ID is not yet known.
2. When `aggregate_server_id` is present, it is the requested canonical aggregate. The server validates user/device ownership, aggregate type, authorization, row version and redirect state before applying the event.
3. When `aggregate_server_id` is null, only an aggregate type declared client-creatable by Sync Protocol v1 may use the event. The server adopts `aggregate_local_id` as the proposed canonical server UUID. This permits an ordered create and later pre-ACK events for the same local aggregate to resolve to one canonical ID without a separate mapping table.
4. A missing server ID for a server-authoritative aggregate type, an unknown non-create target, an unavailable UUID, or an unauthorized target is rejected with a stable non-disclosing error. Hash knowledge or UUID selection never grants access.
5. The immutable event request hash covers both ID fields, including explicit null. A pending Journal event is never rewritten after a later event binds the aggregate; retries therefore retain the same event ID, payload and request hash.

### PostgreSQL meaning

- `sync.device_event_inbox.aggregate_id`, `sync.sync_event.aggregate_id`, and `sync.tombstone.aggregate_id` always store the effective canonical server aggregate ID.
- For an accepted unbound client create, the effective canonical ID equals the proposed `aggregate_local_id`; otherwise it equals the validated/resolved `aggregate_server_id`.
- A PostgreSQL `aggregate_id` never alternates between a device-local correlation ID and a canonical server ID. The server does not need a `(device_id, aggregate_type, local_id) -> server_id` mapping table for v1.

### ACK, pull, bootstrap and merge

- Each per-event ACK returns at least `event_id`, `aggregate_type`, the echoed `aggregate_local_id`, the resolved canonical `aggregate_server_id`, outcome, and applicable server row version. When an input server ID was redirected by merge, the ACK also reports the redirect/canonicalization result defined by P04.
- Android applies the ACK and Journal state transition atomically. It binds `server_id` without rekeying the local row or local foreign keys.
- Pull and bootstrap identify aggregates by canonical `aggregate_server_id`. Android first resolves an existing row through its unique `server_id`.
- If that lookup misses, Android must next test an unbound row whose `local_id` equals the incoming server ID. It may bind that row only when aggregate type, active server profile/user and durable client-create Journal lineage prove that this device proposed the adopted ID. The binding, snapshot apply and cursor/bootstrap progress commit atomically while pending events remain intact for duplicate ACK recovery.
- If the equal local ID exists without that proof, Android records a visible ID-collision conflict and neither binds it nor silently creates a duplicate projection. Only when neither a server binding nor a valid adopted-ID candidate exists may bootstrap create a new stable `local_id` and bind the received server ID.
- Server change-stream events and server tombstones use only canonical server IDs. Android maps them to local rows by `server_id` and retains local tombstone/conflict correlation through `local_id`.

### Approved merge redirect detail P00-D006-R1

Variant A requires one further local persistence rule when both redirect source and canonical target already exist on one device: `L1 -> S1`, `L2 -> S2`, followed by server redirect `S1 -> S2`. Assigning `S2` to `L1` violates the Room unique server-ID constraint; rewriting all `L1` references to `L2` would violate F-017.

The recommended resolution is a durable local aggregate redirect record, owned physically by P05 and proposed contractually by P04:

- retain both immutable local IDs and their distinct original server bindings;
- store an owner/profile-scoped mapping from alias `(aggregate_type, L1, S1)` to canonical `(L2, S2)`;
- resolve reads and new mutations through the canonical local row while preserving existing local foreign keys and immutable pending Journal events;
- apply the redirect record, projection state, tombstone/conflict correlation and cursor in one Room transaction;
- let retries of pre-merge events retain `L1`/`S1`, with the server resolving S1 and the ACK reporting S2;
- reject redirect cycles, cross-type, cross-profile and unauthorized targets.

This is not a PostgreSQL local/server-ID mapping table and does not change Variant A server canonicalization. P00-D006-R1 explicitly accepts this general Room persistence seam beyond the currently listed per-projection redirect field. P04 owns its contract/migration proposal and P05 owns its initial physical Room implementation.

## P04 requirements derived from this decision

P04 must encode the mapping in its protocol, JSON Schemas, OpenAPI and machine-readable vectors. At minimum, vectors must cover:

- client-created aggregate with a null server ID and an adopted canonical ID;
- ordered pre-ACK events for the same local aggregate;
- committed create with a lost ACK followed by bootstrap, proving atomic reuse of the unbound local row;
- mutation of a bootstrap/server-bound aggregate;
- duplicate retry with an unchanged request hash;
- pull/bootstrap on another device with a different local ID;
- merge redirect with only the alias present locally;
- merge redirect with both alias and canonical rows present locally, preserving both local IDs and server-ID uniqueness;
- tombstone before and after server binding;
- unavailable-ID and cross-owner/non-disclosing rejection;
- rejection of an ambiguous single-ID envelope.

P04 must explicitly classify its aggregate types as client-creatable or server-authoritative. It must not treat client time, ID equality, or possession of any UUID as authorization.

## Persistence impact

- PostgreSQL migration: none for this decision. Existing sync `aggregate_id` columns retain UUID type and acquire the canonical-only contract meaning above.
- Room migration: none at this decision gate. The existing design already provides local/server ID columns in projections, Journal events and tombstones. Approved P00-D006-R1 requires P04 to record the accepted general redirect-store proposal and P05 to implement it in the initial physical Room schema.
- Product API or sync engine: none. Those remain owned by P04 and P09 respectively.

## Rejected alternatives

1. **Server always generates a different ID.** This requires a durable, owner-scoped device/local-to-server mapping table and a PostgreSQL migration proposal before P04. It adds failure and cleanup states without a v1 requirement that offsets the cost.
2. **One envelope `aggregate_id` with context-dependent meaning.** Rejected because inbox, canonical change events, pull/bootstrap and tombstones could not distinguish a local correlation ID from the server identity.
3. **Replace the Android primary key after ACK.** Rejected by frozen F-017 and because it would require unsafe local foreign-key rewrites.

## Acceptance and next gate

Variant A and P00-D006-R1 are explicitly approved, so P00-D006 is fully resolved. P04 remains `NOT_STARTED` until explicitly requested. P04 is eligible when the implementation plan, progress/traceability records and machine-readable backlog reference this complete decision and `uv run --frozen autplay-codex next --json` returns P04 as the next task with no P00-D006 blocker.

## Verification evidence

- Independent review found and the decision now closes two major edge cases: bootstrap after a committed create with a lost ACK, and merge redirect when both alias and canonical rows already exist locally. Final re-review reported no remaining critical or major finding.
- `uv run --frozen autplay-codex next --json` returned `status: eligible`, `next_task.id: P04`, `decision_blocker: null`, and `execution_started: false`. Exit `1` is the expected dirty-worktree Git-safety refusal and proves the verification did not start P04.
- Final targeted validation passed nine backlog/CLI tests, strict mypy over all 29 harness source/test files, JSON parsing and `git diff --check`.
- The canonical PowerShell server-only gate passed 70 harness tests and 298 server/PostgreSQL tests against PostgreSQL 18.4/pgvector 0.8.6, then removed the scoped container, network and volume.
- No P04/P05 contract, Android product implementation, PostgreSQL migration, server product source, commit, push or external write was created.
