# AutPlay Administrative Web Security Contract v1

| Field | Value |
| --- | --- |
| Status | ACCEPTED |
| Version | 1.0 |
| Date | 2026-08-21 |
| Scope | M6 administrative browser authentication, session and request security only |

## 1. Scope and authority

This contract defines the security boundary that M6-B through M6-D may implement. It does not
authorize public deployment, registration, password login, a browser music client or reuse of an
Android enrollment/session secret. Product security/privacy rules, the accepted M5 contract and
ADRs, physical persistence contracts and the modular-monolith boundary remain authoritative.

The Web adapter is optional. Disabling it cannot affect API, worker, stream, Android standalone
library/playback or Android M5 authentication. The local CLI remains the mandatory bootstrap,
browser-session recovery and emergency revocation path.

## 2. Identities and authorization

- A browser principal is `(server_instance_id, user_id, web_session_id, current_role)`. It is not a
  device principal and never fabricates `device_id` or an Android `session_id`.
- Only an ACTIVE `OWNER` or `ADMIN` may create or use an administrative browser session. `USER`,
  disabled/deleted accounts and stale/unknown roles fail with a non-disclosing forbidden or
  authentication response.
- Every protected request reloads the Web session and mutable account role/status from PostgreSQL.
  A cookie, receipt, hidden control or UI visibility is never authority by itself.
- Application commands and queries receive a typed Web actor. Handlers/templates do not construct
  Android `Principal` values and do not write PostgreSQL, Vault bytes or job state directly.
- All resource queries and commands retain account/object authorization. OWNER/ADMIN is not a
  cross-account superuser role.

## 3. Browser bootstrap and recovery invitation

M6 uses a new browser-only invitation. It is cryptographically and semantically separate from M5
device enrollment and cannot be exchanged at an Android endpoint.

1. A local operator runs the future interactive command
   `autplay-admin web-session-invite --user-id <existing-owner-or-admin>` using the server's normal
   local administrative configuration. The command refuses inactive/non-OWNER/non-ADMIN targets.
2. The command creates 32 random bytes and prints the base64url value once to an attached TTY. It
   refuses redirected stdout, JSON/file output and non-interactive secret emission. Terminal output
   is recovery material: it must not be logged, recorded or included in diagnostics.
3. PostgreSQL stores only SHA-256 of the bearer plus a generated invitation ID, exact server/user,
   issuer kind, creation/expiry/consumption facts and sanitized audit fields. The bearer is never
   recoverable from the database.
4. Default TTL is 5 minutes and maximum TTL is 10 minutes. At most 3 active invitations exist per
   target and at most 10 may be issued per local operator/server hour. Issuance and first consume
   serialize per target; expiration, cancellation and first success are terminal.
5. The user opens the fixed `GET /admin/login` route and enters the value into a masked,
   non-persisted `autocomplete="off"` field. The browser submits it only in the body of an exact
   same-origin `POST /admin/login`; the server never echoes it.
6. The login form has a separate five-minute pre-authentication challenge with two independent
   32-byte values: one cookie bearer and one hidden form nonce. PostgreSQL stores only their hashes,
   a generated login operation ID and expiry. The POST requires the exact configured Origin, cookie,
   nonce, operation ID and browser invitation. The pre-auth cookie is deleted on terminal handling
   and is never promoted into an authenticated session.
7. Login is the only unauthenticated authority-changing transition. First valid execution atomically
   consumes the invitation, creates exactly one Web session and stores a terminal receipt bound to
   the invitation hash, pre-auth challenge and login operation ID. Because the server does not retain
   a recoverable cookie bearer, a lost success response is deliberately not replayable: exact retry
   returns `browser_login_outcome_unknown`, creates no second session and requires a new CLI
   invitation. CLI session listing/revocation recovers any orphan session.

There is no password, email, OAuth, registration, Android-token copy or public bootstrap endpoint.
CLI recovery may list bounded session metadata and revoke one/all browser sessions without exposing
cookie hashes.

## 4. Server-side Web session

- A successful invitation exchange atomically consumes the invitation and creates a new random
  32-byte opaque session bearer. Only its SHA-256 persists. No access JWT or refresh token is sent
  to the browser and no session bearer appears in HTML or JavaScript-readable state.
- The session is bound to the exact server instance and user. It records generated session/family
  IDs, token generation, creation, last activity, idle/absolute expiry, rotation/revocation and safe
  audit evidence. At most 8 active Web sessions are allowed per user; exceeding the cap fails rather
  than silently revoking another browser.
- Idle expiry is 30 minutes; absolute expiry is 12 hours from initial creation and never extends.
  Last activity is written at most once per five minutes but authorization evaluates the database
  timestamps on every request.
- The bearer rotates atomically after 15 minutes of token age, after an allowed role transition and
  after explicit re-authentication. Rotation replaces the hash and CSRF secret; the predecessor
  never authorizes another request. Its SHA-256 may remain for at most five minutes as non-authority
  replay evidence before bounded cleanup. A lost rotation response requires login again and cannot
  create two live successors.
- Rotation is an authentication-maintenance transition, not an application/domain command. A safe
  authenticated GET may rotate before running its read-only query and return the new cookie. HEAD
  never rotates. A state-changing POST whose token is due for rotation executes no application
  command and returns `browser_session_rotation_required`; a subsequent safe GET performs rotation
  before the form can be resubmitted. Role/status is reloaded before this decision; a now-forbidden
  role revokes/fails rather than rotating.
- Unknown, expired, revoked or predecessor bearers produce `authentication_required`, clear the
  cookie and have no mutation side effect. The sole exception is the exact terminal-receipt read
  below; it grants no session or application authority. Session receipts/evidence never otherwise
  grant authority.
- Logout-current revokes the exact Web session before clearing the cookie. Logout-all-browser
  revokes every Web session for that user including current. These are distinct from M5
  logout-all-device-sessions and are named separately in UI/audit. Account disable/delete invalidates
  all Web sessions on the next request. CLI revocation is always available.
- Logout-current, logout-all-browser and revocation of the initiating browser session atomically
  store their terminal response before revoking that session. The receipt binds the exact
  `server_instance_id`, `user_id`, initiating `web_session_id`, token generation, SHA-256 of the
  presented opaque cookie, operation ID, action, target, reason code and canonical request hash.
  Standard active-session authentication is always attempted first. Only after it rejects the
  now-revoked initiating cookie may a dedicated path hash that presented cookie and read an exact
  matching terminal receipt. It may return only the original fixed terminal response and cookie
  deletion; it cannot reload a principal, update activity, rotate a token, read administrative data
  or execute any command. Unknown receipts and any changed operation, action, target, reason or
  request remain `authentication_required` with zero mutation. This receipt is retained through the
  original session absolute expiry plus five minutes, then removed by bounded PostgreSQL cleanup no
  later than 24 hours afterward. A receipt is terminal evidence, never reusable authority.

## 5. Cookie profiles

Release/private deployment requires one explicitly configured canonical HTTPS origin and this
cookie on every authenticated response:

`__Host-autplay_admin=<opaque>; Secure; HttpOnly; SameSite=Strict; Path=/`

No `Domain` attribute is allowed. Cookie expiry cannot exceed the server-side absolute expiry. The
server refuses wildcard origins, userinfo, fragments, non-normalized hosts and ambiguous forwarded
host/scheme values. Proxy headers are ignored unless an exact trusted-proxy list is configured.

The release pre-auth cookie is
`__Host-autplay_login=<opaque>; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=300`, without a
`Domain` attribute. Its independent hidden nonce is not a cookie. Both values become unusable after
five minutes and are cleared on terminal login handling.

An explicit development profile may use `autplay_admin_dev` without `Secure` only on literal
loopback HTTP (`127.0.0.1` or `[::1]`). It uses `HttpOnly; SameSite=Strict; Path=/admin`, displays a
persistent development warning and refuses RFC1918/LAN/public binds. Its pre-auth counterpart is
`autplay_login_dev` with `HttpOnly; SameSite=Strict; Path=/admin/login; Max-Age=300`, no `Domain` and
no `Secure`. HTTPS remains required for any non-loopback browser. This contract selects no domain,
certificate, reverse proxy or VPN provider.

## 6. CSRF, method and replay rules

- Every authenticated application state-changing request is POST and requires all of: active
  session, exact configured Origin, a 32-byte synchronizer token whose SHA-256 matches the current
  session generation, and a bounded application operation ID. GET/HEAD never execute an application
  mutation; the safe-GET authentication-rotation exception is limited to the session authority row.
  Method override is forbidden.
- The unauthenticated `POST /admin/login` bootstrap exception requires exact Origin, the independent
  pre-auth cookie and hidden nonce, its bound login operation ID and the browser invitation. It has
  the terminal lost-response behavior in section 3 and cannot call another application command.
- CSRF tokens may appear only as encoded hidden form fields in no-store same-origin HTML. They are
  request-integrity nonces, not authentication bearers. This is the only permitted secret-like HTML
  value; they are never put in URLs, JavaScript storage, logs or exports and rotate with the session.
- Missing/multiple/malformed Origin, token, cookie or operation ID fails before application command
  execution. Comparison is constant-time. Cross-origin CORS is disabled for `/admin`.
- Consequential commands use their existing durable idempotency semantics or a new application-owned
  receipt before being exposed. A timeout never becomes presentation-level success. Back/cancel
  submits no mutation.
- The revoked-cookie terminal-receipt read in section 4 is the only authenticated-POST retry that
  does not require an active session. It requires the same Origin, synchronizer token, operation ID
  and canonical request fields as the committed request, plus an exact receipt match. It can only
  reproduce that request's terminal response and clear the cookie; it cannot authorize a new
  mutation or query.
- Responses use Post/Redirect/Get only to fixed route identifiers. User-supplied absolute redirects,
  protocol-relative values, backslashes, encoded scheme tricks and arbitrary `return_to` are
  rejected; the default is `/admin`.

## 7. Browser and content security

Protected/authentication HTML and JSON use `Cache-Control: no-store` and `Pragma: no-cache`.
Fingerprint-named public static assets may use immutable caching but contain no user or configuration
data. Authentication and administrative responses also send:

```text
Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
```

All runtime assets are bundled; no CDN, remote font, inline handler, `unsafe-inline`, `unsafe-eval`
or third-party analytics is permitted. Server-rendered templates auto-escape by default. Safe
diagnostic codes are rendered as text; exception strings, markup from stored values and personal
payloads are never trusted HTML. Any future rich text requires a separately reviewed sanitizer.

## 8. Rate limits and stable failures

- Login exchange permits at most 10 failed attempts per opaque source key per 15 minutes and 30 per
  invitation hash. A global bounded emergency ceiling protects the single personal-server process.
  Source keys are HMAC-derived with a separate secret and expire; raw IP addresses are not routine
  audit/log fields.
- PostgreSQL owns rate/terminal state; no Redis, broker or in-memory-only authority is introduced.
  Bounds are checked before expensive hashing/template work. Responses have bounded `Retry-After`.
- Invitation-not-found, expired, consumed, wrong user/server and invalid bearer share
  `browser_invitation_unavailable`. Other stable codes are `authentication_required`, `forbidden`,
  `csrf_invalid`, `origin_invalid`, `browser_session_expired`, `browser_session_revoked`,
  `browser_session_rotated`, `browser_login_outcome_unknown`,
  `browser_session_rotation_required`, `rate_limited`, `operation_conflict` and
  `server_unavailable`.
- Normal errors contain a localized safe message, stable ASCII code and non-linkable correlation
  code. They contain no stack, cookie/token/invitation, raw identifier, private origin/path, query,
  filename, media metadata or unrestricted payload.

## 9. Audit and secret classification

- Authentication secret, never present in HTML/URL/JavaScript storage/log/export/diagnostics:
  browser invitation bearer, pre-auth cookie, session cookie, token/hash material, Android
  invitation/access/refresh values and private keys. User-entered values exist only as masked input
  control state and POST bytes and are never echoed in the HTML response.
- Request-integrity secret: the pre-auth form nonce and authenticated CSRF value may appear only in
  encoded hidden fields of no-store same-origin forms. They never authorize without their matching
  cookie/server row and are excluded from URLs, JavaScript storage, logs, exports and diagnostics.
- Sensitive non-secret, restricted and redacted from routine output: private origin, network/source
  address, server label, SPKI, user/device/session/invitation IDs and correlation values when
  linkable.
- Personal payload excluded by default: paths, filenames, library/search/import content and media
  metadata.
- Allowed restricted audit facts: action code, outcome/reason, timestamp, actor/target opaque IDs,
  token generation, expiry class and bounded counts. Failed login telemetry is aggregate or keyed by
  expiring HMAC; it never records submitted values.
- Audit/session state changes commit atomically where they describe authority changes. UI hiding and
  audit rows never act as an authorization or idempotency store.

## 10. Administrative surface boundary

The Web adapter may expose only application-backed, bounded surfaces. Unsupported commands remain
read-only or absent. In particular:

- dashboard, health, devices/sessions, Vault/jobs/import/review, backup evidence and diagnostics are
  bounded/paginated and redact private values;
- destructive restore, arbitrary file browsing, shell/SQL, unrestricted export and direct
  table/Vault/job writes are absent;
- existing M5 enrollment invitation metadata may be listed and an existing invitation cancelled,
  but the Web UI does not create or render an enrollment bearer. The M6 prompt's stronger rule that
  raw enrollment secrets never appear in HTML/screenshots controls; Android and the local CLI remain
  the issuance paths until a separately accepted non-browser delivery mechanism exists;
- state-changing controls name target/consequence, suppress duplicate submission and depend on
  server authorization/idempotency, not disabled CSS.

## 11. Persistence and implementation impact

After acceptance, M6-B may add only additive PostgreSQL tables for browser invitations, Web sessions,
rotation/revocation evidence, terminal lifecycle receipts and bounded login throttling.
Cookie/pre-auth/session/CSRF bearer values persist only as SHA-256 or keyed hashes. No existing M5
device session row is repurposed. M6 adds an optional FastAPI Web entrypoint/adapter and typed Web
actor/application ports inside the CPU modular monolith; it adds no service, broker, GPU dependency
or Android schema/API dependency.

## 12. Acceptance decisions

Explicit user acceptance of M6-A means accepting all of the following:

1. Browser access is OWNER/ADMIN-only and bootstraps/recoveries through a distinct five-minute
   CLI-issued one-time bearer; there is no password, email/OAuth or Android-secret reuse.
2. Browser authentication uses a PostgreSQL-backed opaque HttpOnly cookie with 30-minute idle,
   12-hour absolute and 15-minute token rotation; a lost login/rotation response fails closed to a
   new CLI invitation and never creates a second session on exact retry.
3. Production/private access requires an exact HTTPS origin. Cleartext Web auth is loopback-only
   explicit development mode; provider/domain/TLS topology stays deferred.
4. Every mutation uses exact-Origin plus synchronizer-token CSRF protection and application-owned
   idempotency; CSP/clickjacking/output-encoding/no-store rules are mandatory.
5. Because enrollment bearers are forbidden in HTML/screenshots, M6 Web lists/cancels M5 invitation
   metadata but does not issue or display the secret; Android/local CLI remain issuance paths.

These five decisions were explicitly accepted on 2026-08-21 after independent review reported zero
Critical, Major or Minor findings. They are the binding boundary for M6-B through M6-D.
