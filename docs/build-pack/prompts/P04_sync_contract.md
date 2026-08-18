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
- `docs/design/AutPlay_Recommendation_Subsystem_v1.md` interaction and phase-ownership contract

## Deliverables

1. `docs/design/AutPlay_Sync_Protocol_v1.md`.
2. Versioned JSON Schemas under `contracts/events/v1/`.
3. OpenAPI contract for device registration/binding, push, pull/bootstrap and sync status.
4. Golden valid/invalid vectors under `tests/fixtures/sync/v1/`.
5. Contract validation tests runnable without Android device.
6. Compatibility/change policy and next-version procedure.
7. Specialized v1 schemas and golden vectors for canonical logical listening,
   recommendation-impression and direct recommendation-feedback events.

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
- two-stage event validation: generic forward-compatible envelope first, then the specialized schema
  for a known event type;
- actual presentation, not candidate generation or API delivery, as the definition of an impression;
- recommendation request/rank/recording attribution, optional causal impression linkage and
  non-disclosing same-owner validation;
- append-only interaction identity and exactly-once projection semantics without duplicate generic
  events for preference, playlist or playback actions;
- explicit privacy bounds excluding tokens, private URLs, raw paths, raw search queries and raw
  model features from interaction payloads.

## Golden vectors

Include duplicate event same payload, same ID different payload, reordered batch, sequence gap, partial rejection, offline delete, edit-vs-delete, expired cursor, bootstrap with pending local edits, unknown enum/event and oversized payload.

Also include organic and recommended listening, online/offline/local-reranked impressions, direct
selection/dismissal, same-presentation idempotency, causal pre-ACK impression linkage, cross-user
attribution rejection, rank/recording mismatch, explicit-null hash coverage and interaction payload
boundary vectors.

Include top-level sensitive extension rejection, `event_id`/aggregate-ID mismatch, canonical
listening origin/context mapping, recommended-listen attribution requirement, and a different-event-ID
same-presentation semantic duplicate that creates no second impression.

## Constraints

- Do not implement full sync engine.
- Do not use wall-clock timestamps as idempotency/order key.
- Do not make cursor guessable database offset contract.
- No silent last-write-wins for destructive conflicts.
- Do not expose private URLs/tokens in payload or conflict snapshot.
- Do not count generated/delivered recommendations as impressions.
- Do not add a second transport envelope or a parallel feedback endpoint.
- Do not implement recommendation serving, persistence projections or either sync engine in P04.

## Acceptance

Schemas and OpenAPI validate; all golden vectors have expected machine-readable outcomes; protocol maps every field to existing PostgreSQL/Room entity or accepted migration proposal; no unresolved semantic contradiction remains.

Create `HANDOFF_P04.md` and stop.
