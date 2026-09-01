# AutPlay Public Invite-only Account Threat Model v1

| Field | Value |
| --- | --- |
| Status | ACCEPTED / CONTRACT_ONLY |
| Date | 2026-08-31 |
| Contract | `AutPlay_Public_Invite_Only_Account_Contract_v1.md` |
| Implementation | PA2_RUNTIME_IMPLEMENTED; PA3_EDGE_CANDIDATE_BLOCKED |

## Assets and trust boundaries

Protected assets are the account-invitation bearer, device private key, refresh/access session
material, persistent server identity, account/Vault isolation, account capacity and personal display
names. Boundaries are Android process/Keystore, public TLS edge, mobile API, PostgreSQL and the local
OWNER administration surface. Discovery/origin, QR metadata, IP address, display name and friendship
are never authority by themselves.

## Threats and required controls

| Threat | Required control | Residual |
| --- | --- | --- |
| Bearer stolen before use | 256-bit secret, 10-minute default TTL, no URL/clipboard/log, explicit trust screen, first-use transaction | A thief who wins first valid PoP redemption can create the account; OWNER must disable it |
| Concurrent redemption | Invitation/account-cap/receipt locks and one transaction | One durable winner; losers receive non-disclosing failure |
| Changed lost-response replay | Registration ID, canonical request hash, bearer hash and exact device key bound in receipt; a fresh valid ECDSA proof over the same hash is accepted | Exact replay only while current account/device/session authority remains active |
| Active profile attempts account creation | Reject every valid Authorization or recognized AutPlay session credential on redemption | No implicit account switching or authenticated-account influence on bootstrap |
| Changed OWNER operation replay | Receipt binds actor, operation UUID, action, target and server-computed RFC 8785/SHA-256 command hash | Changed create/cancel/disable reuse returns `operation_conflict` and performs no mutation |
| Fake server or origin substitution | Platform PKI, signed M5 identity, exact invitation origins/epoch/thumbprint and downgrade rejection | Compromise of both delivery channel and trusted edge remains an operator incident |
| Role escalation | Role is server constant `USER`; request schema has no role field | OWNER remains the only provisioning actor |
| Cross-account OWNER overreach | Immutable provisioning link and an exact allowlist of list/disable fields/actions | OWNER can disable invited access but cannot inspect user data |
| Enumeration | Uniform unavailable response, bounded page visible only to OWNER | OWNER legitimately sees accounts it provisioned |
| Resource exhaustion | 20-account cap, five active invites, issuer/invite/source/server windows, 16 KiB body | Distributed sources can consume the global budget and temporarily delay valid redemption |
| Proxy-header spoofing | Trust forwarded source only from exact edge address; otherwise global budget | Source token is abuse evidence, never identity |
| Secret leakage through UI/OS | Secure window, app-private content URI, no browser/URL/notification/analytics/export | The chosen human messenger may retain the encrypted attachment according to its policy |
| Account disable race | Lock account and reload status before every token/replay operation; existing S1C retirement | In-flight responses may return unknown outcome but cannot restore authority |
| Backup loss/ransomware | Encrypted off-host named generations and isolated restore before WAN | RPO/RTO depend on the later accepted operator target |
| Public Admin/DB exposure | Edge allowlist exposes only API/stream on 443; Admin stays loopback | Host compromise remains outside application-layer containment |
| Malicious future schema value | Strict v1 input schemas; unknown persisted state retained but non-authorizing | Future clients require explicit version negotiation |

## Abuse and privacy classification

- **Secret:** invitation bearer, access/refresh material, device private key, HMAC keys.
- **Sensitive personal:** display name, account UUID, device description, source address before
  immediate HMAC reduction, private origins.
- **Public non-authority:** product/API compatibility and server label from signed discovery.
- **Safe audit:** generated operation/invitation IDs, actor UUID, terminal reason code and timestamp.

No CAPTCHA, email verification, telephone number, hardware fingerprint, public directory, broker or
third-party identity provider is introduced. These would add privacy/legal/dependency surfaces and
are unnecessary for owner-issued invitations.

## Rollout stop conditions

WAN enablement stops if TLS renewal, exact origin advertisement, forwarded-header rejection,
off-host restore, owner isolation, rate-limit behavior, Android mobile-network redemption or Range
streaming is unproved. Wave stays disabled. A certificate alone is not production evidence.
