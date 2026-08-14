# ADR-016: P03 runtime and authentication boundary

**Status:** Accepted

**Date:** 2026-08-15

**Owners:** AutPlay server runtime and security maintainers

## Context

P03 must establish a production-shaped, CPU-only FastAPI and PostgreSQL-worker foundation with safe personal-server owner/device sessions. PostgreSQL schema v1 already contains `account.user_account`, `account.device`, `account.user_session`, audit, and `jobs.*` tables, but it contains no password-credential table or versioned credential-persistence contract.

Inventing a password column, placing a password verifier in profile/session metadata, or adding a public login flow would silently change the P02 physical contract and security boundary. Conversely, postponing all authentication primitives would leave later API work without a tested owner/session seam. The phase therefore needs an explicit boundary that uses the existing schema without pretending password login is complete.

## Decision drivers

- Keep owner setup local and intentional; do not create public self-registration.
- Persist no bearer token, password, private URL, or raw secret in plaintext.
- Make access authorization depend on current account, device, and session state rather than JWT claims alone.
- Bound access/refresh lifetimes and preserve an absolute refresh expiry across rotation.
- Detect reuse of a known revoked refresh generation and fail closed.
- Preserve the P02 schema and one Alembic head unless an approved migration is genuinely required.
- Keep API, CPU worker, and administrative composition separate and free of GPU dependencies.

## Options considered

| Option | Benefits | Problems | Result |
| --- | --- | --- | --- |
| Add password credentials to schema v1 during P03 | Complete password login immediately | Unapproved physical/security contract; migration outside the requested boundary; credential recovery/policy still undefined | Rejected |
| Store password verifier or refresh bearer in an existing profile/session metadata field | Avoids a migration | Misuses unrelated columns, weakens type/invariant review, and risks credential disclosure | Rejected |
| Disable password login, bootstrap the first owner locally, and use opaque rotating sessions in existing tables | Small, explicit, testable boundary with no schema drift | Password login remains unavailable until a later approved contract | Accepted |
| Use an external identity provider | Delegates credential handling | Provider choice, Internet dependency, account setup, and OAuth/email flows are explicitly out of P03 scope | Rejected |

## Decision

1. The server remains one modular monolith with separate console entrypoints for the FastAPI process, CPU worker, and local administrative CLI. API/worker readiness never depends on GPU or external Internet.
2. Password login is disabled. Setting `password_login_enabled=true` fails startup with the stable sanitized code `password_login_persistence_contract_missing`. An explicit Argon2id hashing/verification primitive is present and tested so future credential work cannot choose unsafe defaults, but no P03 path accepts or persists a password.
3. The first owner is created only through `autplay-admin bootstrap-owner` in a locally invoked administrative context. A transaction-scoped PostgreSQL advisory lock serializes the empty-account check, then owner, device, initial session, and audit event commit atomically. If any account already exists, bootstrap fails closed.
4. Bootstrap deliberately emits the newly issued token pair exactly once to the caller-selected standard-output stream. Token-bearing values have redacted representations and are never sent through the logging subsystem. The operator is responsible for transferring and protecting that one-time output.
5. Access tokens are short-lived HS256 JWTs with a fixed algorithm, issuer, audience, user/device/session identities, issue/expiry times, and token ID. Their maximum lifetime is 15 minutes. No role claim is trusted or emitted: authentication reloads the role, account, device, and session from PostgreSQL and rejects disabled/deleted accounts, revoked devices, and expired/revoked sessions.
6. Refresh tokens are 32-byte cryptographically random opaque values. Only their 32-byte SHA-256 digests are stored in the existing `account.user_session.refresh_token_hash` column. Each rotation revokes the old session row, creates a new session/digest generation, and retains the original absolute expiry, bounded at 90 days.
7. Known revoked refresh generations remain stored for replay detection. Reuse commits revocation of every active session for that device before returning a stable replay error. Logout, logout-all, and device revocation are explicit application transactions; owner-scoped lookups return one indistinguishable not-found outcome across ownership boundaries.
8. P03 exposes only real device-session operations under `/api/v1`: refresh, current/all-session logout, and owner-scoped device revoke. It exposes no public registration/bootstrap HTTP route, password-login endpoint, email/OAuth provider, sync endpoint, or non-auth product resource endpoint.
9. The PostgreSQL job worker reuses the existing `jobs.job` and `jobs.job_attempt` tables. Claim/lease fencing, retries, cancellation, and terminal transitions are application/runtime behavior, not a P03 schema change.

## Physical schema and migration effect

P03 creates no Alembic revision and changes no reference DDL or normative design file. The one migration head remains `0010_indexes_privileges`. Authentication uses existing account/device/session/audit columns, and the job worker uses existing `jobs.*` columns.

The absence of password credential persistence is intentional evidence, not placeholder success. A future password-login proposal must define at least credential ownership, verifier/version fields, recovery/rotation, audit/lockout behavior, safe migration, and deletion/retention semantics before an Alembic revision is approved.

## Consequences

### Positive

- P03 can prove owner/device session rotation, replay response, revocation, and cross-user failure semantics against the real database without weakening schema v1.
- No bearer refresh token or password is stored in plaintext.
- A compromised but otherwise valid access JWT stops working when mutable account/device/session state is revoked.
- Local owner bootstrap has a clear one-time operational boundary and no public attack surface.
- API and worker processes stay CPU-only and independently composable.

### Negative

- Users cannot sign in with a password in P03.
- HS256 requires every validating API instance to possess the same signing secret; this is acceptable only inside the current personal-server trust boundary.
- Refresh generations consume append-like session rows until their later retention policy is defined.
- One-time bootstrap output is sensitive operator material and cannot be recovered from the database.

## Compatibility and reversal triggers

- Enabling password login requires an approved credential-persistence/security contract and named Alembic migration; it must not reuse unrelated profile or session metadata.
- Public multi-user registration, email, OAuth, recovery, or an external identity provider requires its owning product decision and threat-model tests.
- A move to independently operated token verifiers or a wider public trust boundary triggers review of asymmetric signing/key rotation; it is not an implicit P03 change.
- Any change to refresh hashing, generation lineage, or retention requires compatibility/replay evidence and, if physical storage changes, a reversible migration.
- Production domain/TLS, role topology, secret delivery, and backup/restore remain later-phase deployment decisions.

## Validation evidence

- Unit and real-PostgreSQL auth tests cover Argon2id parameters, token claim confusion/tampering/expiry, opaque refresh representation, serialized one-time bootstrap, rotation with absolute expiry, replay revocation, logout/device revocation, and cross-user failure.
- Settings tests cover the disabled password-login boundary and sanitized configuration errors.
- Job-control tests cover fenced lease behavior against the unchanged P02 schema.
- `HANDOFF_P03.md` records both 298-test canonical gates and the non-root CPU runtime image/API/worker smoke with exact cleanup evidence.

## Reversal trigger

Revisit this ADR only when an owning phase supplies an approved credential schema, public identity policy, multi-verifier signing requirement, or evidence that the personal-server session model cannot meet its security/recovery requirements. Do not weaken the local-only/no-plaintext boundary as a temporary workaround.
