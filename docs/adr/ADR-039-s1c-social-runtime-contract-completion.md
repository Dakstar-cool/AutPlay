# ADR-039: S1C social runtime contract completion

- Status: Accepted on 2026-08-25
- Scope: Post-MVP S1C same-server friendship, aggregate presence and P13 friend Room invitations

## Context

ADR-036 accepted the S1C authority boundary, but its initial OpenAPI surface did not provide a
bounded way to obtain a signed contact card, recover the caller's social state, cancel an
invitation, or return durable command receipts. Android recovery therefore could not implement
the accepted Friends screen without inferring state from mutations or exposing a directory.

## Decision

S1C completes the additive social contract as follows.

1. `GET /social/contact-card` returns only the authenticated account's signed, same-server contact
   card. The card carries a bounded display-name hint, has server-derived issue/expiry values and
   expires no later than 30 days. Its signature domain is frozen as
   `autplay:s1c:social-contact-card:v1\n`.
2. `GET /social/snapshot` is caller-owned recovery state, not a directory. It returns at most 100
   entries each for friends, incoming requests, outgoing requests, blocks, sent invitations and
   received invitations, plus the caller's presence settings. It contains only aggregate presence;
   it never returns device/session counts, Room codes, exact activity, library or media data.
3. Friend commands return a durable idempotent receipt. Presence settings return their persisted
   settings view. Room invitation creation, cancellation and acceptance return non-secret
   invitation or device-bound membership receipts. Heartbeat stays bodyless (`204`).
4. The public Room invitation view is FRIEND-only in S1C. Guest invitation fields and redemption
   remain deferred to S1D and are absent from implemented S1C contracts.
5. Mutable authority is reloaded inside the command transaction. All endpoints are non-disclosing
   for foreign, blocked, unavailable or terminal objects.

The numeric limits are fixed: contact-card and snapshot reads 60/account/15 minutes; friendship
commands 30/account and 10/pair/15 minutes; presence settings 10/account/15 minutes; heartbeat at
most once/30 seconds; presence reads 120/account/15 minutes; Room invitations 20/host/15 minutes
and at most 8 pending invitations per Room. Accepted TTLs remain: contact cards maximum 30 days,
friend requests maximum 14 days, presence freshness 90 seconds, invitation default/maximum 10
minutes bounded by Room expiry, and terminal evidence maximum 30 days.

## Consequences

The Android client can recover bounded private social state and share/import a signed contact card
without a user directory. The runtime must verify the contact-card signature and server binding,
enforce all stated limits, and never use the snapshot as Room or media authority. S1D must add a
new contract rather than reactivate guest fields in S1C.
