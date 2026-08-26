# ADR-038: S1B device-admission protocol completion

- Status: Accepted for S1B implementation on 2026-08-25
- Date: 2026-08-25
- Scope: Narrow completion of the accepted S1A admission and exact-key trust contract
- Decision owner: standing technical-decision authorization in `DECISION_REGISTER.md`

## Context

ADR-035 and the accepted S1A prose define the security and product behavior for targetless Android
admission, Web approval and exact-key trust. The initial strict schemas do not define the final
Android exchange or trusted-key re-enrollment messages, and contain four presentation-level
ambiguities: an approved status has no bounded account label, an exact duplicate submission cannot
repeat one-time plaintext secrets, the OpenAPI cookie name differs from M6, and an opaque review
binding would become an extra browser-held secret. S1B cannot implement those gaps by weakening M5
or M6.

## Decision

1. S1B adds an approved-admission exchange operation. Its signed request binds the exchange UUID,
   admission request UUID and canonical request hash, current poll-bearer SHA-256, M5 server instance,
   identity epoch and thumbprint, exact API and stream origins, approved account UUID, the exact
   existing P-256 key/SPKI/thumbprint, a client-generated successor refresh-token hash, a fresh
   nonce and a client binding-commit UUID. The proof is ES256-P1363 over the SHA-256 digest of the
   RFC 8785 request with only the proof field omitted, domain-separated as
   `autplay:s1b:admission-exchange:v1\n`. The raw poll bearer remains only in the canonical
   `X-AutPlay-Admission-Poll` header and is never duplicated in JSON.
2. A successful exchange calls the ordinary M5 device/session issuer and returns the existing M5
   enrollment-exchange response shape. It creates exact-key trust only for a `TRUST_DEVICE`
   decision and only in the same transaction as successful issuance. Exact replay returns the same
   device/session after mutable authority is revalidated; any changed request, account or key fails
   closed. No synthetic M5 invitation or browser enrollment bearer is introduced.
3. Trusted-key re-enrollment is a two-step operation. The server first returns a one-time random
   hash-only challenge bound to the trusted server identity and exact key. Android then submits an
   M5-compatible signed issuance request for the same account and key. The exchange proof uses
   domain `autplay:s1b:trusted-reenrollment:v1\n`; the transaction requires a fresh challenge,
   active account, active exact-key trust, no exact-key block and the ordinary M5 identity/origin
   checks. A new key cannot use this path.
4. Submit, poll and recovery proofs use RFC 8785 plus SHA-256 with only their proof field omitted and
   the domains `autplay:s1b:admission-request:v1\n`,
   `autplay:s1b:admission-poll:v1\n` and
   `autplay:s1b:admission-recovery:v1\n`, respectively. The server verifies the exact key and all
   trusted-server binding fields before exposing state.
5. Approved polling returns both the account UUID and a sanitized display label of 1-120 Unicode
   scalar values. The label is confirmation context, never an identifier or authorization input.
6. An exact duplicate submit creates no second row but cannot replay the one-time locator and poll
   bearer because PostgreSQL stores only their hashes. It returns the stable non-disclosing
   `admission_recovery_required` outcome; Android then performs the already accepted bounded atomic
   recovery rotation. `SAME_RECEIPT` means convergence on the same durable request, not repetition
   of plaintext creation secrets.
7. The review-locator resolution remains a no-store POST, but its durable binding stays server-side
   and is scoped to the authenticated M6 Web session and actor account. HTML/form state contains
   only ordinary request and operation UUIDs plus the existing M6 CSRF token. A decision reloads
   that server-side binding; no second secret-like hidden form value is created.
8. Production uses the accepted M6 `__Host-autplay_admin` cookie; the existing explicitly bounded
   loopback development cookie remains the only local exception. S1B OpenAPI must not define a
   parallel Web session cookie.

## Consequences

The strict S1B schemas and fixtures can now cover exchange, challenge/re-enrollment, the approved
account label and recovery-required duplicate behavior. Server, Web and Android share one canonical
proof convention while M5 remains the only device/session issuer. PostgreSQL must retain only
hashes of poll, locator and re-enrollment challenge secrets and use bounded receipts for exact
replay.

## Rejected alternatives

- Persist or replay plaintext creation secrets: violates the accepted hash-only boundary.
- Put the opaque review binding in HTML: creates a second browser-held secret outside M6 CSRF.
- Mint a session directly from Web approval: bypasses exact Android key proof and M5 replay rules.
- Re-enroll by nickname, model, installation or IP: permits trust inheritance by a new key.
- Reuse the invitation secret or manufacture an invitation: couples two independent ceremonies and
  expands bearer exposure.
