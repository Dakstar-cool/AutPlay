# P04 - Sync Protocol v1 and Contract Fixtures

Выполни только phase P04. Следуй common protocol и прочитай `HANDOFF_P03.md`.

## Цель

Зафиксировать language-neutral Sync Protocol v1 до реализации Android/server sync engines. Результат должен позволить двум сторонам независимо проверить одинаковые golden vectors.

## Inputs

- Product specification sync/offline/conflict sections
- System Architecture sync flow and compatibility rules
- ER sync entities
- PostgreSQL sync tables
- Android Room Offline Journal/sync entities

## Deliverables

1. `docs/design/AutPlay_Sync_Protocol_v1.md`.
2. Versioned JSON Schemas under `contracts/events/v1/`.
3. OpenAPI contract for device registration/binding, push, pull/bootstrap and sync status.
4. Golden valid/invalid vectors under `tests/fixtures/sync/v1/`.
5. Contract validation tests runnable without Android device.
6. Compatibility/change policy and next-version procedure.

## Protocol must define

- device identity and user/profile binding;
- client-generated event ID and monotonic device sequence;
- deterministic request hash and duplicate behavior;
- bounded push batches and per-event ACK status;
- APPLIED/DUPLICATE/CONFLICT/REJECTED distinctions;
- opaque server cursor and ordered server sequence;
- pull pagination without partial cursor advance;
- tombstone semantics and retention;
- bootstrap snapshot, invalid cursor and reset;
- pending local events during bootstrap;
- optimistic row version/base version;
- conflict taxonomy and user-visible resolution state;
- payload schema version, unknown additive fields and unsupported version error;
- retryability, limits, stable error codes and redaction;
- clock skew: client time is metadata, never sole ordering authority.

## Golden vectors

Include duplicate event same payload, same ID different payload, reordered batch, sequence gap, partial rejection, offline delete, edit-vs-delete, expired cursor, bootstrap with pending local edits, unknown enum/event and oversized payload.

## Constraints

- Do not implement full sync engine.
- Do not use wall-clock timestamps as idempotency/order key.
- Do not make cursor guessable database offset contract.
- No silent last-write-wins for destructive conflicts.
- Do not expose private URLs/tokens in payload or conflict snapshot.

## Acceptance

Schemas and OpenAPI validate; all golden vectors have expected machine-readable outcomes; protocol maps every field to existing PostgreSQL/Room entity or accepted migration proposal; no unresolved semantic contradiction remains.

Create `HANDOFF_P04.md` and stop.
