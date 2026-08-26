# ADR-035: S1A Web device admission and exact-key trust

- Status: Accepted on 2026-08-25; implementation deferred to S1B
- Date: 2026-08-25
- Scope: Post-MVP S1A contract only; implementation deferred to S1B

## Context

M5 enrollment is secure but requires a separately delivered one-time invitation. AutPlay needs a
lower-friction flow in which Android requests access and the already authenticated M6 user approves
the exact device without exposing an M5 bearer in HTML or turning OWNER/ADMIN into cross-account
authority.

## Decision

1. Android completes M5 server identity trust before submitting a bounded targetless request for
   one exact non-exportable P-256 key. The server returns separate hash-only 128-bit Web review and
   Android polling values; the review locator grants no enrollment/session authority.
2. A domain-separated RFC 8785/SHA-256 12-decimal comparison string binds the server instance,
   request UUID, canonical request hash and accepted M5 lowercase-hex DER-SPKI thumbprint. It does
   not bind an account because the request is still targetless. Counter-based 40-bit rejection
   sampling avoids modulo bias. A hash-only 128-bit poll bearer exposes only coarse request state.
3. M6 has no global request list. OWNER/ADMIN submits the exact review locator in a no-store POST;
   the server replaces it with an opaque binding scoped to that M6 session. A decision requires the
   binding and may approve only the actor's own active account using the existing browser authority,
   CSRF, operation-id, idempotency and audit boundaries. Android confirms that account before
   exchange. The raw locator is never placed in a URL.
4. Approve-once, trust, reject and exact-key block are distinct decisions. Trust materializes only
   inside successful M5-compatible enrollment.
5. Trust continuity always requires proof by the same key. New key/reinstall never inherits trust
   from nickname, model, IP or physical similarity.
6. Remove trust, block key and revoke device/session remain separate consequences. A combined action
   requires explicit selection of each consequence.
7. A lost one-time creation response is recovered only by bounded exact-request/exact-key proof.
   The server atomically invalidates the old locator/poll hashes and returns a newly generated pair
   once; it never persists plaintext recovery material or creates a second pending request.

## Consequences

S1B needs additive PostgreSQL request/decision/trust/block/receipt state, M6 Connection Requests and
Trusted Devices surfaces and Android request/poll/exchange states. Existing M5 invitation and
session routes remain valid; no parallel authentication service or browser enrollment secret is
introduced.

## Rejected alternatives

- Web-rendered M5 bearer: violates the accepted M6 HTML/screenshot boundary.
- Nickname, IP or model as trust: forgeable and not key continuity.
- Role-based cross-account approval: M6 roles are not delegated device administration grants.
- Approval creating a session directly: bypasses Android key proof and M5 exchange invariants.
