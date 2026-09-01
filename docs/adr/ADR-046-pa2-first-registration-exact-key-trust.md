# ADR-046: PA2 invited-account recovery boundary correction

- Status: Accepted by the user on 2026-09-01 after architecture correction
- Scope: PA2 first account registration and deferred recovery

## Context

PA1 originally stated that a newly invited account uses existing M5/S1 recovery after its first
registration. The frozen M5 implementation permits additional-device invitations only to
self-account OWNER or ADMIN. S1A/ADR-035/ADR-038 additionally define `TrustedDeviceKeyRow` as the
result of a separate exact-key `TRUST_DEVICE` Web decision and successful issuance. A generic
OWNER-issued account bearer predates and does not approve the key later supplied at redemption.

An initial PA2 proposal would have auto-seeded trust for that first key. The user accepted that
proposal on 2026-09-01, but the architecture review then found the conflict above before any runtime
implementation. The proposal is withdrawn rather than treating consent based on an incomplete
premise as authority to weaken S1.

The user then explicitly accepted the corrected no-trust/no-reenrollment boundary on 2026-09-01.

## Decision

1. The PA2 registration transaction creates the account, first device and ordinary generation-zero
   V2 session, but no `TrustedDeviceKeyRow`.
2. Existing signed session rotation/logout/device revocation remain available while that first
   binding and key survive.
3. Same-key S1B reenrollment, new-key recovery and additional-device enrollment for an invited
   `USER` are not available in PA2.
4. A future recovery milestone must define explicit self-service exact-key approval or another
   user-consented ceremony without granting bootstrap OWNER cross-account device authority.

## Consequences

The first Android installation remains usable through its normal V2 session lifecycle. Losing the
binding/session or reinstalling with any key has no invited-account recovery path in PA2. The OWNER
may disable the inaccessible account but cannot inspect or rebind its devices. This limitation must
be disclosed before invitation redemption.

## Rejected alternative

- Auto-trusting the redemption key: bypasses the accepted exact-key Web decision.
- Allowing every `USER` to issue self-account M5 enrollment invitations: changes frozen M5
  authority, expands secret issuance and requires a separate security milestone.
