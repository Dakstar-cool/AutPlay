# ADR-043: S1D guest Room capability runtime

- Status: Accepted for S1D implementation on 2026-08-26
- Date: 2026-08-26
- Scope: Post-MVP S1D only

## Context

ADR-037 selected Android-only `autplay-guest-v1` documents and independent device media. S1D must
turn that boundary into a replay-safe runtime without storing a raw invitation or session bearer,
without treating a guest as an account/device session, and without widening P13 Vault authority.

## Decision

1. The authenticated host Android app generates a cryptographically random 256-bit document bearer
   and submits it only in the body of a no-store invitation POST. The server stores its SHA-256 and
   returns a typed `autplay-guest-v1` app document. Exact create replay uses the same operation UUID
   and bearer; a changed request conflicts. The document is never an HTTP URL.
2. Redemption submits the document bearer and a separate Android-generated random 256-bit session
   bearer in one no-store POST. PostgreSQL stores only both hashes. The room row is locked while
   expiry, epoch, capacity and use count are checked. An exact operation replay with the same hashes
   converges; another operation consumes another allowed use or fails closed.
3. The resulting principal is not an M5 `Principal`. It is one guest-principal UUID bound to one
   room, role `GUEST`, expiry, invitation and the fixed server-owned allowlist
   `ROOM_SNAPSHOT`, `ROOM_EVENTS`, `ROOM_PRESENCE`, `ROOM_PREFLIGHT`, `ROOM_TIMING`, `ROOM_LEAVE`.
   Callers cannot supply or expand this allowlist.
4. Guest authentication uses a dedicated header and router. Every REST call and every WebSocket
   heartbeat/event delivery reloads the guest principal, invitation and room state. Expiry,
   revocation, leave, room close or room epoch change terminates access. Guest credentials are not
   accepted by account, profile, library, search, sync, recommendation, Vault or administrative
   routes.
5. Guest presence, preflight and timing use guest-owned tables because P13 account membership rows
   require real account/device foreign keys. The P13 strict-start gate counts fresh ordinary and
   guest participants together. Guests cannot enqueue, control playback, transfer/close the room or
   resolve a Vault source through guest authority.
6. Active ordinary devices and active guest principals share the existing eight-participant room
   capacity. Invitations default to one use and 15 minutes, may allow at most eight uses, and never
   outlive the six-hour room. Terminal invitation/principal evidence is retained for at most 30
   days; bounded cleanup removes expired transient rows first.
7. Android keeps both raw bearers only in process memory, clears the app-document/Intent copy before
   normal navigation, never persists a bearer in Room/DataStore/navigation/saved state, and loses
   guest authority on process death. Room v13 stores only a sanitized recovery/display projection.
   Queue playback still uses ordinary P08/P13 local/download/authorized source resolution.

## Consequences

Guests can participate in snapshot/live/preflight/timing recovery without an account, but they may
report every queue entry unavailable. Lost process-local guest authority requires a fresh document
or invitation use. This is intentional and safer than durable bearer storage.

## Rejected alternatives

- Reusing the document bearer as the long-lived session credential: prevents prompt Intent-secret
  destruction and couples invitation use to live access.
- Server-retained plaintext for replay: violates the hash-only boundary.
- M5 fake users/devices or nullable foreign keys in ordinary membership: confuses account and guest
  authority.
- Guest access through the ordinary Wave router: risks API-family and Vault escalation.
- Browser bootstrap, host relay or Room-scoped streaming grant: outside ADR-037 and S1D.
