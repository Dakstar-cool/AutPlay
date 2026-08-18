# ADR-022: P09 Sync Runtime Transaction, Cursor and Reset Boundaries

- Status: Accepted
- Date: 2026-08-16
- Decision owner: standing in-scope technical-decision authorization

## Context

P04 froze an at-least-once sync protocol before either runtime existed. P09 must now make a lost
HTTP response, process death, duplicate delivery, cursor reset and multi-page bootstrap recoverable
without changing event identity or discarding local intent. The original PostgreSQL sync tables do
not retain the Android aggregate ID, idempotency key, Journal epoch, base row version or complete
terminal ACK, and they have no durable bootstrap snapshot or canonical interaction projection.

The Android P07 writer also created every event with `base_server_row_version = null`. A newly
created overwrite/delete can capture the row's previously observed server version, but a dependent
operation made before the create ACK has no truthful server version to record. Rewriting that
immutable event after ACK would change its hash; inventing a version would turn an unobserved state
into an unsafe last-write-wins operation.

## Decision

1. Add one additive P09 PostgreSQL migration. Extend the inbox/cursor with Journal-lineage and
   terminal-classification fields, add a durable owner/device/epoch-scoped materialized bootstrap
   session, and add the append-only canonical user-interaction projection with owner-scoped actual-
   presentation uniqueness.
2. Serialize push admission per authenticated device lineage. Commit each eligible new event in
   its own PostgreSQL transaction containing inbox reservation, authorization, domain fact/mutation,
   interaction projection, canonical sync event, terminal ACK/idempotency result and contiguous
   device checkpoint. An exact retry returns the stored result as `DUPLICATE`.
3. Encode pull and bootstrap cursors as authenticated opaque tokens bound to protocol, owner,
   device, Journal epoch, cursor generation and checkpoint. A multi-page bootstrap reads materialized
   snapshot rows fixed at one high-water mark; it does not depend on a process-local database
   snapshot.
4. Evolve Room non-destructively from v2 through v7: runtime status, independent bootstrap state,
   profile-scoped conflicts/tombstones, append-only recommendation interaction facts, and explicit
   profile ownership for every synced projection. Add a profile-scoped WorkManager coordinator.
   ACK apply and each supported pull/bootstrap page use one Room write transaction. Unsupported or
   malformed server data is durably deferred while the old cursor remains. Bootstrap never clears
   or rewrites pending Journal rows.
5. New overwrite/destructive Android events capture the aggregate's pre-mutation
   `server_row_version` when it is known. When an authorized dependent operation has no observed
   version because its create has not yet been acknowledged, the server records a visible
   `CONFLICT/POLICY_REVIEW`, advances the classified sequence, and preserves the immutable event and
   bounded conflict evidence. It does not mutate, reject-and-delete, synthesize a version or use a
   timestamp tie-breaker.
6. Tombstones compact only after their retention deadline and after every supported active device
   checkpoint has passed the deletion event. A device outside the supported offline window must
   bootstrap instead of holding tombstones forever.
7. Pin OkHttp `5.4.0` for the first Android sync transport. WorkManager input contains only stable
   profile/device identifiers; tokens, private URLs and event bodies remain in their existing
   protected/runtime or Room boundaries.
8. Return `CURSOR_INVALID` as HTTP 410 and `DEVICE_RESET_REQUIRED` as HTTP 409, both with a stable
   bootstrap directive. A WorkManager attempt drains at most ten pages and returns retry when more
   work remains, so large backlogs are bounded without claiming premature success.

## Consequences

- A server commit followed by a lost response is indistinguishable from an ordinary retry and
  cannot produce a second mutation or interaction projection.
- Partial push acceptance remains intentional and durable per event; pull cursor advancement
  remains all-or-nothing per page.
- Pre-ACK dependent destructive intent is recoverable and visible instead of silently overwritten
  or discarded. Explicit user resolution may emit a later immutable event with a then-observed
  server version.
- PostgreSQL and Room schema snapshots, migrations, real-database/device tests and exact dependency
  evidence must be updated in P09.
- Legacy standalone Android rows remain explicitly `legacy-unscoped`; authenticated materialization
  claims them atomically into the active profile, and all bound lookup/search/apply paths are
  profile-scoped.
- P10 import/identity and P11 recommendation generation remain out of scope.

## Rejected alternatives

- One transaction for an entire push batch: contradicts the frozen partial-acceptance contract.
- Numeric or unsigned database-offset cursors: expose storage layout and cannot bind reset lineage.
- Long-lived exported PostgreSQL snapshots: do not survive ordinary process/request boundaries.
- Rehash or rewrite a pending event after an ACK: destroys deterministic retry identity.
- Treat a missing pre-ACK base version as permission to overwrite: unsafe implicit last-write-wins.
- Store event payloads or credentials in WorkManager input: violates durable ownership and privacy
  boundaries.
- Add a broker, cache service or sync microservice: unnecessary for the modular-monolith baseline.
