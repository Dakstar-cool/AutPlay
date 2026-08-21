# ADR-031: M6 administrative Web session security

- Status: Accepted
- Date: 2026-08-21
- Scope: Server M6 browser security boundary; accepted before M6-B implementation

## Context

M5 device enrollment deliberately does not authorize a normal browser. M6 needs a local/private
administrative session without introducing passwords, public registration, JavaScript token
storage, a separate auth service or a public deployment decision. It must also preserve the frozen
M5 device-PoP lifecycle and the local CLI recovery boundary.

The M6 prompt both requests device-invitation administration and forbids raw enrollment secrets in
HTML/screenshots. A browser cannot receive a usable enrollment bearer without violating the stronger
security rule, so the first Web surface must not issue/render that secret.

## Proposed decision

1. Use a distinct CLI-only, five-minute-default, one-time 256-bit browser invitation for an exact
   existing ACTIVE OWNER/ADMIN. Only its SHA-256 persists; the value is accepted only from a masked
   exact-origin POST body and is never an Android credential.
2. Create a separate PostgreSQL-backed opaque Web session. Only token hashes persist. The browser
   receives one HttpOnly SameSite=Strict host-only cookie; release/private use requires Secure HTTPS.
   Explicit cleartext development is literal-loopback only.
3. Use 30-minute idle, 12-hour absolute and 15-minute fail-closed token rotation. A safe GET may
   rotate only the authentication authority row before its read query; HEAD never rotates, and a
   due state-changing POST runs no command until that rotation completes. Reload mutable
   session/account/role authority on every protected request. CLI can revoke one/all sessions.
4. Require exact Origin, a server-stored synchronizer-token hash and an application operation ID on
   every authenticated POST mutation. Login is the sole unauthenticated exception and instead
   requires independent pre-auth cookie/form nonces, exact Origin, a bound login operation ID and a
   one-time invitation. Lost login response creates no replay bearer or second session and requires a
   new CLI invitation. GET/HEAD never execute application mutations and redirects are fixed
   same-origin routes.
5. Require bundled assets, strict CSP, frame denial, output encoding, no-store administrative
   responses, bounded PostgreSQL rate limits and sanitized audit facts.
6. A Web principal is separate from an Android device principal. Presentation calls typed
   application ports and never fabricates device/session IDs or writes persistence directly.
7. Web may list/cancel M5 invitation metadata but does not issue/render enrollment bearers. Android
   and local CLI retain issuance until a separate non-browser delivery contract is accepted.
8. Self-logout and logout-all atomically persist a terminal receipt bound to server, user, initiating
   Web session, token generation and hash, operation, action, target, reason and request hash before
   revocation. A now-revoked initiating cookie may only read that exact receipt and clear itself; it
   never regains a principal or command/query authority. Receipts expire after the original absolute
   session expiry plus five minutes and bounded cleanup removes them within a further 24 hours.

## Consequences

M6-B needs additive browser-invitation/session/throttle/terminal-receipt persistence, an optional
CPU-only FastAPI adapter and typed Web authorization/application seams. There is no password
database, refresh token, JWT in the browser, local/session storage auth, broker, Redis, microservice
or Android schema change. Lost login/rotation responses require a new CLI invitation; this is
deliberately less available than persisting a recoverable bearer or granting an old token a replay
grace. A lost self-logout response is recoverable only as the exact terminal receipt and cannot
restore authority.

## Rejected alternatives

- Copy an Android enrollment/access/refresh value into the browser: crosses the accepted credential
  boundary and leaks device authority.
- Password/email/OAuth login: requires credential/recovery/provider decisions outside M6.
- JWT or refresh token in JavaScript/localStorage/sessionStorage: expands XSS impact and violates the
  no-browser-secret-storage rule.
- Client certificate/device-PoP browser enrollment: operationally complex and not owned by the
  current personal-server contract.
- Magic link/query/fragment bearer: leaks through URL/history/referrer/screenshot/browser tooling.
- Web-rendered enrollment QR/text: conflicts with the prompt's stronger no-enrollment-secret-in-HTML
  and screenshot rule.
- Previous-token authorization grace after rotation: improves multi-tab availability by extending a
  stolen predecessor's authority; M6 chooses fail-closed re-login.
- Redis/session microservice: no measured need; PostgreSQL and the modular monolith already own
  authoritative session/job state.
