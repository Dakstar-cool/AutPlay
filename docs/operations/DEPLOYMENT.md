# Production Deployment Boundary

## Current state

Android 1.0.8 is distributed as the exact production-signed `app.autplay` APK qualified on a
physical Samsung M52. The compatible CPU server image remains 1.0.7 with Alembic
`0066_portable_local_account`. A controlled existing target has verified rollout, owner-data
preservation, backup/isolated restore and 13 running processes. The
[1.0.8 release notes](../release/RELEASE_NOTES_1.0.8.md) bind that accepted scope to the artifacts.

These results do not configure or qualify a different operator's server. Each new target still
requires its own decisions, data-preserving migration, resource review and network/backup checks
below. The normal runtime and trusted-LAN Compose files remain development examples and must not
be exposed to the public Internet or pointed at personal/production data. See
`PRODUCTION_READINESS_PLAN.md` for remaining cross-project work and `PUBLIC_EDGE_PA3.md` for the
public-edge procedure. The historical 0.4.0 installer is not the current portable-account server.

No checked-in workflow currently deploys, pushes an OCI image, creates a GitHub Release, signs an
APK with a production key or runs a migration against a persistent target. This is an intentional
fail-closed boundary, not a missing success claim.

## Decisions to confirm for the exact live production deployment

The operator must explicitly choose and record all of the following:

1. Linux x86_64 target and access model (for example, an on-host self-hosted runner or a reviewed
   SSH transport with pinned host identity).
2. Container registry and immutable image naming/retention policy.
3. Public or trusted-LAN network boundary, domain and TLS/reverse-proxy topology. PA3 fixes the
   current candidate to DNS-only IPv4 `api.autplay.win`/`stream.autplay.win`, Caddy and TCP 443;
   changing that topology requires a new decision.
4. Production PostgreSQL roles, storage lifecycle and non-destructive migration authority.
5. Secret delivery mechanism for database and auth material; values must never enter GitHub logs,
   workflow artifacts, Compose YAML or repository files.
6. PostgreSQL/Vault backup destination, retention, encryption, restore drill schedule, RPO and RTO.
7. Android distribution channel, application ID/version policy and production signing custody.
8. Rollout health gate, rollback procedure and operator responsible for approval.

Some choices have historical PA3 evidence, but every item must be rebound to the exact release and
target configuration. Missing choices cannot be guessed by a provider-neutral repository change.

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
