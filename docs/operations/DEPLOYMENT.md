# Production Deployment Boundary

## Current state

AutPlay has reproducible local runtime Compose files and GitHub Actions for CI plus restricted
release-candidate bundles. It does not yet have a safe production deployment topology. The existing
`deploy/compose` files are loopback-first development/runtime evidence and must not be exposed to
the public Internet or pointed at personal/production data.

No checked-in workflow currently deploys, pushes an OCI image, creates a GitHub Release, signs an
APK with a production key or runs a migration against a persistent target. This is an intentional
fail-closed boundary, not a missing success claim.

## Decisions required before a production manifest

The operator must explicitly choose and record all of the following:

1. Linux x86_64 target and access model (for example, an on-host self-hosted runner or a reviewed
   SSH transport with pinned host identity).
2. Container registry and immutable image naming/retention policy.
3. Public or trusted-LAN network boundary, domain and TLS/reverse-proxy topology.
4. Production PostgreSQL roles, storage lifecycle and non-destructive migration authority.
5. Secret delivery mechanism for database and auth material; values must never enter GitHub logs,
   workflow artifacts, Compose YAML or repository files.
6. PostgreSQL/Vault backup destination, retention, encryption, restore drill schedule, RPO and RTO.
7. Android distribution channel, application ID/version policy and production signing custody.
8. Rollout health gate, rollback procedure and operator responsible for approval.

These items are deliberately deferred by `DECISION_REGISTER.md` and cannot be guessed by a
provider-neutral repository change.

## Required production gate

After the decisions are accepted, the deployment implementation must:

- consume an immutable image digest produced from a green commit;
- use a protected GitHub `production` environment and prevent self-approval where the repository
  plan supports it;
- back up PostgreSQL and Vault as one named generation before any migration;
- validate the expected Alembic head before and after the change;
- deploy without destructive database or Vault fallback;
- wait for API, worker and stream readiness using redacted diagnostics;
- run an owner-isolation and Range-stream smoke against synthetic data;
- roll back process/image state without rolling schema backward or deleting user data;
- record commit, image digest, migration head, backup generation and health result.

The first live deployment is an external action and still requires an explicit target-specific
approval even after these manifests exist.
