# ADR-037: S1A Android guest capability and media boundary

- Status: Accepted on 2026-08-25; implementation deferred to S1D
- Date: 2026-08-25
- Scope: Post-MVP S1A guest contract only; implementation deferred to S1D

## Context

Guest Room access must not create an account or leak a bearer through URL/history/referrer paths.
P13 also deliberately grants no audio or Vault authority through a Room.

## Decision

1. Guest v1 uses an Android non-HTTP autplay-guest-v1 QR/app document with a random 256-bit bearer.
   PostgreSQL stores only its hash, one Room, bounded actions, expiry, use count and terminal facts.
2. Android exchanges the bearer by an explicit no-store POST from ephemeral app state and clears it
   before normal navigation. It is forbidden in URL path/query, clipboard, logs, analytics, crash,
   screenshot, recent-task, export and diagnostics state.
3. The capability principal can access only the exact Room operations accepted by S1D. Every call
   rechecks capability, Room state, expiry and allowlist and denies all account/library/Vault/admin
   API families.
4. Guests play only media independently available and authorized on their device under ordinary
   P08/P13 source resolution. The guest capability, host and Room grant no bytes.
5. Browser redemption/playback, media relay and room-scoped streaming/Vault grants remain deferred.

## Consequences

Guest users may join a room but report unavailable for some or all queue entries. This is an honest
limitation and preserves the accepted media-rights boundary. S1D requires separate activation and
negative authorization/leakage evidence.

## Rejected alternatives

- Bearer in HTTP link/query: leaks through history, referrers and infrastructure logs.
- Browser bootstrap in v1: expands CSP/cookie/redemption scope without a current product need.
- Host-to-guest or Room Vault grant: changes P13 byte authorization and requires a separate rights
  and security decision.
