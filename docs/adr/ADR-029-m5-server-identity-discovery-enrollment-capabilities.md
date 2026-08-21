# ADR-029: M5 server identity, discovery, enrollment and capabilities

- Status: Accepted by the user on 2026-08-20
- Date: 2026-08-20
- Scope: Product M5A contract only; implementation deferred to M5B

## Context

AutPlay needs to connect an Android device to an optional personal server without inventing password
login or treating an origin, friendly label or QR as authentication. Private deployments also need a
stable application identity across certificate renewal and origin changes.

## Decision

1. A server has a persistent UUID, P-256 application identity key, SHA-256 public-key thumbprint and
   monotonic identity epoch. Signed payloads use RFC 8785 canonical JSON and ECDSA P-256/SHA-256
   fixed-size IEEE P1363 signatures.
2. Release clients send enrollment/session secrets only over HTTPS. Platform PKI or an explicitly
   confirmed SPKI pin supplies transport trust. HTTP is limited to opt-in debug/QA loopback/RFC1918.
3. Discovery and enrollment use different QR document types. Discovery contains no secret and grants
   no authority. Enrollment carries a short-lived one-time bearer invitation but never session
   credentials.
4. OWNER/ADMIN may invite an additional device to their own existing active account; local CLI may
   issue recovery for an explicitly selected existing account. Account creation, role choice and
   elevation are impossible in v1.
5. Invitations default to 10 minutes, are capped at 30 minutes, 5 active per account and 10 issues
   per actor/hour. Only the secret SHA-256 persists; terminal transitions are atomic and audited.
6. Authenticated capabilities are signed and bound to instance, epoch, account and device; public
   discovery claims never authorize. A monotonic high-water detects downgrade.
7. Origin changes require explicit confirmation. V1 defines no application-key rotation protocol:
   any key/epoch change fails closed and requires a new trust/enrollment ceremony. HTTPS downgrade
   is forbidden.

## Consequences

The model is usable with public or private TLS without selecting a provider. An unused enrollment QR
remains a bearer secret vulnerable to shoulder surfing during its bounded TTL; this residual risk is
explicit. Restore/key loss requires administrator removal of the old trust binding and a new trust
ceremony rather than silent identity replacement or claimed continuity.

## Rejected alternatives

- Origin, label or discovery QR as identity: permits substitution.
- Blind TOFU: persists an active MITM as trusted.
- Cleartext release LAN: exposes invitation/session bearer material.
- One QR type for both discovery and enrollment: makes secret handling ambiguous.
- Public registration/password recovery in M5: outside the accepted product/security boundary.
- Redis, broker or separate auth service: no measured need in the modular monolith.
