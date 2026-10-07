# ADR-055: Same-server public ID registration and exact authenticated lookup

- Status: Accepted, user-authorized extension on 2026-10-06
- Date: 2026-10-06
- Scope: Additive Android onboarding identity and same-server social locator

## Context

The user requested friend search by ID with confirmation and explicitly required a name at first
launch. The accepted scope permits a chosen public ID to remain pending offline and requires
server confirmation of uniqueness when binding to a personal server. This is a narrow,
user-authorized extension of ADR-036/039's previous shared-card-only discovery decision. It does
not imply a global identity, global federation, account directory or public account registration.

## Decision

1. A client chooses a display name and an ASCII public ID during local onboarding. Local playback
   remains independent of server availability. Pending local IDs confer no server identity or
   uniqueness; only the server registration response confirms a same-server ID. The display name
   remains distinct from the public ID and follows existing account/profile pairing contracts.
2. `GET /api/v1/social/public-id` returns only the active authenticated caller's registration:
   `{"public_id":null,"status":"unregistered"}` or
   `{"public_id":"alice_1","status":"confirmed"}`. Existing accounts receive no automatic ID.
3. `PUT /api/v1/social/public-id` accepts exactly `operation_id` (UUID) and `public_id` (3-24 ASCII
   letters, digits or underscores). Uppercase is normalized to lowercase. Whitespace, Unicode
   lookalikes and `@` are rejected; `@` is presentation only. PostgreSQL enforces one canonical ID
   per account and one account per canonical ID under the C collation. Confirmation is one-time;
   another operation with the same ID is safe, and another ID returns
   `public_id_already_registered`. Renaming/releasing IDs requires a future explicit contract.
4. Registration, its receipt and rate accounting commit atomically. UUID operation IDs bind the
   exact account, device, action and normalized input. Exact replay revalidates current account,
   device and session authority and returns the recorded outcome. Changed input or cross-owner/
   cross-device replay returns `operation_conflict`. Concurrent claims serialize by canonical
   ID, with PostgreSQL uniqueness as the final authority. Every command sharing the social receipt
   namespace serializes its operation UUID before account/session locks, including cross-action
   and cross-owner races. Success and denied claim receipts use
   the existing 30-day social receipt retention. A disabled/deletion-pending account retains its
   reservation until ordinary account hard deletion; the new FK then cascades only this locator.
5. `GET /api/v1/social/accounts/by-public-id/{public_id}` accepts one exact, case-insensitive ID
   and returns the existing signed ContactCard with the frozen S1C signature domain and maximum
   30-day TTL. It returns no separate profile, settings or presence data. No prefix, autocomplete,
   list, wildcard or unauthenticated lookup is provided. Invalid, missing, blocked in either
   direction, disabled and deletion-pending targets all return `404 public_id_not_found` with the
   same public envelope. Account-pair locks recheck authority and block state before card issuance.
6. Limits are 60 own-registration reads, 10 new registration operations and 30 exact lookups per
   authenticated account per 15 minutes. Negative lookups and denied registration attempts consume
   the budget; exact receipt replay does not. Errors contain no target names, UUIDs or owner hints.
   All three endpoints apply private no-store headers to success, auth and validation errors.
7. Search is only a locator. The user must confirm sending a request; the existing friendship
   command still requires the signed card. Acceptance/rejection and crossed requests remain
   explicit. Friendship grants no account/session, library, Vault, Room or device authority.

The new endpoints use the existing social router and PostgreSQL transaction boundary. All other
ADR-036/039 privacy, presence, invitation and server-scope decisions remain frozen.

## Migration and verification

`0063_social_public_id` follows `0062_cpu_worker_health`. It creates only
`social.public_id_registration`, its canonical CHECK, unique constraint and account FK, and revokes
PUBLIC access. Forward upgrade does not backfill, rename or rewrite existing accounts. Deploy the
forward migration before the matching API runtime; readiness requires the new exact head. This
change is prepared locally; no production migration or deployment is authorized by this ADR.

Downgrade refuses while registrations or REGISTER_PUBLIC_ID receipts exist. There is no destructive
fallback; operators must use an independently approved retention/export/rollback plan for real
data. Disposable PostgreSQL tests prove forward migration preserves existing accounts, uniqueness,
receipt durability/replay, races, authentication, block/disable concealment, signed-card commands,
privacy bounds, catalog parity and empty-database migration lifecycle.

## Consequences

A registered ID intentionally reveals its signed account card to an authenticated same-server
caller who knows the complete ID. This is the specific discovery expansion the user authorized.
Rate limits reduce probing but cannot make a guessable ID secret. Registration is independent of
display-name edits, and collisions require the caller to choose another ID explicitly. Offline
pending IDs must never be presented as globally unique or used as command authorization.
