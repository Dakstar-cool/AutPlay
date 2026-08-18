# ADR-018: Standalone Outbox and Journal Lineage

- Status: Accepted
- Date: 2026-08-16
- Decision owner: explicitly approved by the user on 2026-08-16

## Context

P05 must prove that a fresh Android installation can accept a local mutation with no server
configuration. Frozen decisions F-002 and F-003 make standalone local-first operation a complete
mode, while F-018 requires a domain mutation and its Offline Journal record to commit in one Room
transaction.

P04 then defined a wire-ready client event whose immutable hash binds `user_id`, `device_id`,
`server_profile_id` and the remaining event members. A fresh standalone installation has no
authenticated values for those members. Writing nullable placeholders and later rewriting the
event would change its identity and hash. Requiring a profile before mutation would instead violate
the standalone acceptance criterion.

There is a second persistence mismatch. The original Room v1 proposal uses one global
`device_sequence_counter`, but P04 sequences are monotonic inside one authenticated device Journal
lineage. `server_profile_id` is only a local wrong-profile guard; it is not a server lineage key.
The current server enforces uniqueness on `(device_id, device_sequence)`, and a device reset creates
a new device identity. A local profile recreation must therefore neither restart nor fork the
counter for the same authenticated remote binding.

## Decision

1. Treat the Android Offline Journal as two durable stages:
   - `local_mutation_outbox` contains local-only intent created before an authenticated server
     profile is selected. It is not a P04 client event and has no wire sequence or request hash.
   - `offline_journal_event` contains only complete, immutable, wire-ready P04 client events. Its
     owner/profile/device binding and hash inputs remain non-null and are never rewritten.
2. A standalone domain mutation and its `local_mutation_outbox` row commit in one Room write
   transaction. A bound mutation and its `offline_journal_event` row continue to commit in one Room
   write transaction. This is the narrow F-018 amendment required to preserve its atomic-intent
   purpose in both modes.
3. First profile binding or profile switching does not silently assign standalone intent. The user
   explicitly chooses whether to keep it standalone or materialize it into the selected profile.
4. P05 owns a pure local `materializeOutboxToJournal` repository transaction. Given an immutable,
   already revalidated binding, it validates the source intent, allocates a lineage sequence,
   inserts a new P04 event with a new `event_id`, idempotency key and RFC 8785 request hash, and links
   the source row to that event atomically. It never turns the source row into an event or rewrites
   an existing event. P09 owns user-consent UX, authenticated binding revalidation and transport,
   and invokes this P05 operation only after those checks succeed.
5. `local_mutation_outbox` stores a stable local change ID, versioned event/aggregate discriminator,
   immutable aggregate local ID, canonical payload, occurrence time, materialization state and an
   optional unique materialized-event link. P05 validates new intent against a versioned
   event-type/aggregate-type allowlist and applies the P04 recursive safe-object rule at insertion
   and again at materialization. Canonical payload bytes are limited to 262,144. Token,
   authorization, password/credential, private/base URL, filesystem/raw-path, raw-audio and other
   forbidden properties are rejected at any nesting depth. Unknown rows from a newer version are
   preserved but remain non-materializable until supported; current writers fail closed on unknown
   or unsafe intent.
6. Replace the singleton allocator with a durable `journal_lineage` plus counter. One stable local
   lineage maps to the authenticated remote tuple `(user_id, device_id, journal_epoch)`; one or more
   local profile records resolving to that same tuple share its counter and pending-event set.
   `server_profile_id` remains an immutable event member and wrong-profile guard, not a counter key.
   Each Journal event stores the stable lineage reference, and sequence uniqueness is enforced
   within that lineage.
7. Under the current P04/server contract, sequence 1 for a new lineage requires a new device
   identity. Recreating or switching a local profile for the same authenticated binding continues
   the existing counter and cannot reset it. Supporting a new epoch with the same device identity
   requires the explicit P09 server cursor/dedupe/unique-constraint migration already proposed by
   the Sync Protocol; P05 does not pretend a Room-only change can authorize it.
8. Pending events from an older lineage remain immutable and visible. Reset/profile-change flows
   cannot compact, rebind or silently discard them; P09 must make the user resolve incompatible
   pending state before activating a genuinely new lineage.
9. Because P05 is still defining pre-release schema v1, acceptance regenerates the exported v1
   schema and exact-schema evidence. It does not add a destructive migration fallback.

## Consequences

- Fresh standalone commands remain atomic and durable without inventing an authenticated identity.
- P04 event identity, idempotency and hash invariants remain unchanged.
- A standalone local collection cannot leak into the wrong personal-server profile through an
  automatic binding decision.
- Room v1 gains one local-only table, a stable authenticated-lineage record, lineage
  columns/indexes and a lineage-scoped sequence counter.
- P07 can build local-first library behavior on one explicit mutation-record abstraction. P05 owns
  pure local materialization; P09 owns explicit consent, authenticated binding revalidation,
  transport and reset behavior.
- P05 must add fresh-install mutation/restart, atomic materialization rollback/retry, duplicate local
  profile binding, same-binding profile switch, nested-forbidden-key, oversize-payload and
  per-lineage sequence tests before it can pass.

## Rejected alternatives

- Nullable owner/profile/device values in `offline_journal_event` followed by one-time mutation:
  this changes the immutable P04 hash identity and makes retries ambiguous.
- Fake local UUIDs in authenticated binding fields: the server must reject them, and later repair
  would still rewrite event identity.
- Requiring a configured profile before any local mutation: this contradicts standalone
  local-first acceptance.
- A counter keyed by local `server_profile_id`: recreating a profile could emit a duplicate
  `(device_id, device_sequence)` rejected by the current server contract.
- One global device sequence across unrelated authenticated bindings: it cannot isolate local
  pending sets or prove safe reset behavior.
- Silent automatic import into the first configured profile: this creates an ownership/privacy
  decision without user authorization.

## Implementation ownership

P05 owns the Room implementation and pure local materialization evidence. P09 owns consent,
authenticated binding revalidation, transport and reset behavior. P06 remains outside this ADR and
must not start before P05 passes its complete phase gate.
