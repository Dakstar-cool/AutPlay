# ADR-045: PA1 invite-only account provisioning

- Status: Accepted by the user on 2026-09-01
- Date: 2026-08-31
- Scope: Public Access PA1 contract only; implementation deferred to PA2

## Context

AutPlay v0.3.0 can enroll another device only into an existing account. The user wants friends to
connect without a VPN client and have separate accounts. Open registration, password recovery and
public Admin Web would introduce larger authentication, abuse, privacy and operations surfaces.
Existing M5 and M6 contracts also forbid inferring cross-account power from `OWNER`/`ADMIN` roles.

## Decision

1. Add a distinct account-invitation protocol. It never overloads M5 existing-account enrollment.
2. Only the unique active bootstrap OWNER may issue/list/cancel invitations and list/disable exact
   accounts created by those invitations. This is an explicit narrow provisioning/lifecycle grant,
   not general cross-account data or device/session authority. `ADMIN` and `USER` receive none.
3. Every invited account is role `USER`. The issuer fixes its initial display name. Open signup,
   caller-selected roles, email, password, OAuth and account switching remain absent.
4. A 256-bit bearer is returned once, stored only as SHA-256 and delivered only as an in-app QR or
   `.autplayinvite` document. URLs, browser HTML, clipboard, logs, analytics and exports are banned.
5. Redemption requires platform-trusted HTTPS, exact M5 server identity/origin confirmation and a
   new Android P-256 proof. One transaction consumes the invitation and creates account, first
   device, M5-compatible session, provisioning evidence and exact replay receipt.
   A valid Authorization or recognized AutPlay session credential is rejected rather than ignored.
   Replay matches the canonical request hash, bearer hash and device key; a fresh valid ECDSA proof
   over that same hash may have different signature bytes.
6. Fixed first-rollout limits are 20 active accounts, five active invitations, 10 issues/OWNER/hour
   and bounded per-invitation/source/server redemption windows. Raw source IP is immediately reduced
   to a dedicated HMAC token and is never identity.
7. Account disable is the only PA1 cross-account lifecycle mutation. Re-enable, hard delete,
   password recovery and individual friend device/session administration remain deferred.
8. Public-edge deployment is PA3 work. Admin Web stays loopback; WAN Wave stays disabled; encrypted
   off-host backup/restore, trusted TLS, signing custody and WAN evidence gate rollout.
9. Every OWNER mutation receipt binds actor, operation UUID, action, target and the server-computed
   RFC 8785/SHA-256 command hash. Changed reuse fails with `operation_conflict`.
10. Proposed ADR-046 corrects the recovery sentence without weakening S1: PA2 creates no trust row;
    reenrollment/new-key/additional-device recovery for invited `USER` accounts remains deferred.

## Consequences

- Friends can become separate same-server users without a password database or public signup.
- Existing M5/S1 device/session/social flows can be reused after the first atomic registration.
- The OWNER gains an explicit but tightly enumerated cross-account provisioning relation. New
  persistence must prove that it cannot authorize library/Vault/session access.
- A stolen unused invitation remains a bounded first-winner risk. Short TTL, explicit confirmation
  and immediate OWNER disable are the recovery boundary.
- PA1 changes no runtime. PA2 requires separate activation after this ADR and contract are accepted.

## Rejected alternatives

- Public self-registration, email/password or OAuth: disproportionate recovery/abuse/legal surface.
- Reusing M5 enrollment invitation: it is normatively bound to an existing account.
- Letting ADMIN issue invites: role labels remain non-global and least privilege is preferred.
- Bearer in HTTPS link/query/fragment or Admin Web: history, referrer, proxy, preview and screenshot
  leakage risks conflict with existing secret rules.
- Self-signed public TLS, Tailscale/Funnel or public M6 Admin: incompatible with the chosen ordinary
  no-VPN friend experience or accepted release boundaries.
- Redis, broker or separate auth service: no measured need; PostgreSQL transactions own authority.
