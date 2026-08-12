# P09 - End-to-End Sync Engine

Выполни только phase P09. Следуй common protocol и прочитай `HANDOFF_P08.md`.

## Цель

Реализовать P04 Sync Protocol v1 на server и Android: offline Journal push, ACK, pull, cursor, tombstones, bootstrap/reset and conflict projection without data loss.

## Inputs

- `docs/design/AutPlay_Sync_Protocol_v1.md`
- event JSON Schemas/OpenAPI/golden vectors
- PostgreSQL sync tables and Room sync entities
- P07 domain commands and P05 transaction/journal code

## Scope

1. Device registration/profile binding and secure session integration.
2. Server push endpoint/application pipeline with inbox dedup and transactionally emitted sync events.
3. Per-event ACK outcomes and deterministic same-ID/different-payload rejection.
4. Pull pagination with opaque cursor and bounded batches.
5. Android WorkManager sync coordinator and network transport.
6. Journal leases, retry/backoff, ACK apply and compaction safety window.
7. Server projection apply to clean rows; conflict records for dirty/local changes.
8. Tombstone propagation, retain/ACK/compact rules.
9. Bootstrap snapshot and invalid cursor reset with pending local event preservation.
10. Sync Status UI: pending, last success, conflict, dead letter and retry.
11. Metrics/log correlation without payload leakage.

## Constraints

- One cursor advance transaction per fully applied batch.
- Server time/sequence authoritative for server event order.
- Never silently discard pending local event on bootstrap.
- No blind timestamp last-write-wins for playlist/library destructive operations.
- Retry uses same event ID/hash.
- No large payload in WorkManager input.

## Required tests

Run the same P04 golden vectors against server and Android implementations, plus:

- process death before/after local commit, send and ACK apply;
- duplicate/reordered push and pull;
- network timeout after server commit;
- partial batch rejection;
- edit-vs-delete and concurrent playlist reorder;
- tombstone retention/compaction;
- invalid/expired cursor and bootstrap with local pending changes;
- unknown event/version compatibility;
- two users/devices object isolation;
- large backlog batching and cursor lag metrics.

## Acceptance

An offline Android change converges to server and second device exactly once; conflicts/tombstones remain recoverable and no tested failure path loses user intent.

Create `HANDOFF_P09.md`, update A-018..A-022 and stop.
