# PA3 production-edge qualification runbook

## Status and authority

This runbook describes the accepted PA3 candidate for `api.autplay.win` and
`stream.autplay.win`. It is not authorization to expose the server. The edge must remain stopped
and TCP 443 must remain closed until all of these are true:

- an encrypted off-host named backup generation has been restored in isolation;
- the long-lived Android signer and update transition are accepted and proven;
- the exact deployment action, firewall/NAT change and certificate issuance are approved;
- a prior compatible image/configuration and the rollback operator are identified.

SSH administration stays on Tailscale. Public SSH, TCP 80, UDP 443, PostgreSQL, Admin Web, health,
metrics, workers and raw API/stream ports are outside the public boundary. WAN Wave remains
disabled.

## Candidate topology

```text
Internet TCP 443
        |
        v
Caddy 172.30.77.2 (TLS, HTTP/1.1 + HTTP/2)
        |-- api.autplay.win/api/v1/* --------> mobile-api:8787
        `-- stream.autplay.win/api/v1/stream/* -> stream:8788
```

Caddy overwrites the dedicated client-IP header. The application accepts one canonical IP only
when the direct socket peer is exactly `172.30.77.2`; malformed, duplicate, list-valued or
wrong-peer input falls back to the server-wide rate budget. No raw client IP is stored or included
in routine logs.

## Host preparation without WAN mutation

Use a fixed Compose project name, for example `autplay-production`. Keep the extracted immutable
release bundle separate from mutable state. A suitable private state layout is:

```text
/srv/autplay/
  releases/<release-id>/
  secrets/production/
  evidence/pa3/
```

The existing Jamendo client ID is not an SSH key, database credential or Android signing key. Do
not reuse it for another purpose. Keep the production secret directory owner-only and keep every
secret out of the repository, Compose YAML, terminal transcripts and process arguments. Native
Linux Compose bind-mounts file secrets; the files must remain readable by the corresponding
container process while their parent directory is inaccessible to other host users.

The production overlay requires separate files for:

- PostgreSQL password and the SQLAlchemy URL containing that same password;
- access-token signing HMAC and the distinct public-access source HMAC;
- the distinct loopback Admin source and CSRF HMAC values;
- the persistent P-256 server identity private key.

It also requires an operator ACME contact in `AUTPLAY_ACME_EMAIL`. The Android APK keystore is not
a server secret and must never be copied to this host.

Before starting any service, export only file paths and non-secret configuration in the operator
session, then render the complete model:

```bash
export AUTPLAY_SERVER_IMAGE='<approved immutable local image tag>'
export AUTPLAY_RUNTIME_POSTGRES_PASSWORD_FILE='/srv/autplay/secrets/production/postgres-password'
export AUTPLAY_RUNTIME_DATABASE_URL_FILE='/srv/autplay/secrets/production/database-url'
export AUTPLAY_RUNTIME_AUTH_SECRET_FILE='/srv/autplay/secrets/production/auth-signing-hmac'
export AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE='/srv/autplay/secrets/production/public-source-hmac'
export AUTPLAY_RUNTIME_ADMIN_SOURCE_SECRET_FILE='/srv/autplay/secrets/production/admin-source-hmac'
export AUTPLAY_RUNTIME_ADMIN_CSRF_SECRET_FILE='/srv/autplay/secrets/production/admin-csrf-hmac'
export AUTPLAY_RUNTIME_PROFILE_IDENTITY_KEY_FILE='/srv/autplay/secrets/production/server-identity.pem'
export AUTPLAY_ACME_EMAIL='<operator contact>'

docker compose -p autplay-production \
  -f compose.yaml \
  -f compose.runtime.yaml \
  -f compose.admin-local.yaml \
  -f compose.public-edge.yaml \
  -f compose.release.yaml \
  --profile runtime --profile public-edge config --quiet
```

Inspect the rendered model without printing environment values. It must have exactly one
non-loopback publication: IPv4 TCP 443 on `edge`. `api` may publish only literal loopback;
`mobile-api` and `stream` must publish no host port. PostgreSQL and workers publish none. Confirm
that every application process receives `AUTPLAY_DATABASE_URL_FILE` and no
`AUTPLAY_DATABASE_URL`, and PostgreSQL receives `POSTGRES_PASSWORD_FILE` and no
`POSTGRES_PASSWORD`.

Validate the Caddyfile with the pinned image before a start:

```bash
docker run --rm --network none --read-only \
  --tmpfs /config:size=8m,mode=0700 \
  --tmpfs /data:size=8m,mode=0700 \
  -e AUTPLAY_ACME_EMAIL=operator@example.invalid \
  -v "$PWD/Caddyfile.public-edge:/etc/caddy/Caddyfile:ro" \
  caddy:2.11.4-alpine@sha256:98eb57d882ccd5213d1688764db10c1ca2c58a1ca3a6717a3411ad798f7a423a \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

## Network and certificate activation gate

Only after the blocked prerequisites and explicit activation approval:

1. Verify the DNS-only IPv4 A records for both exact names resolve to the intended public address.
   Do not publish an AAAA record during the IPv4-only rollout.
2. Verify public TCP 22, 80, 8787, 8788, 5432 and UDP 443 are closed. Keep SSH reachable through
   Tailscale before changing the firewall or NAT.
3. Permit/forward only IPv4 TCP 443 to the server. Caddy uses TLS-ALPN-01; TCP 80 is not needed.
4. Record the pre-deployment named backup generation, release digest, Alembic head and previous
   configuration hash.
5. Start the reviewed model with `--no-build --wait`; never run `down --volumes`:

```bash
docker compose -p autplay-production \
  -f compose.yaml \
  -f compose.runtime.yaml \
  -f compose.admin-local.yaml \
  -f compose.public-edge.yaml \
  -f compose.release.yaml \
  --profile runtime --profile public-edge up -d --no-build --wait
```

The first live start may issue public certificates. Preserve the project-scoped `caddy-data`
volume across restarts and rollbacks. Do not copy its private material into routine evidence.

## Qualification evidence

Collect evidence from a network that is not the server LAN or Tailscale:

- platform-trusted certificate chains and exact SANs for both names;
- successful signed discovery from `https://api.autplay.win`;
- authenticated stream `Range`, `If-Range`, resume and expiry behavior on a synthetic object;
- 404 for Admin, health, metrics, Wave and non-stream paths on the stream origin;
- an external TCP/UDP scan proving that only TCP 443 is reachable;
- a real Android mobile-network invitation redemption with exact identity/origin confirmation;
- re-issuance or renewal evidence without replacing `caddy-data`;
- the backup restore and signer/update records required by the PA3 prompt.

Never place invitation secrets, access/refresh tokens, raw IP addresses, private paths, certificate
private keys or personal media in evidence.

## Configuration failure and rollback

Keep the last known-good Compose files, image and their hashes before each change. Validate a new
Caddyfile in an isolated one-shot container before replacing the live bind-mounted file. To prove
the failed-configuration path, validate a deliberately invalid candidate, record the redacted
non-zero result, and confirm the running HTTPS endpoints and `caddy-data` volume are unchanged.

If a recreated edge fails after a reviewed change:

1. restore the last known-good Caddy/Compose files;
2. recreate only the affected processes with the prior compatible image;
3. retain PostgreSQL, Vault and `caddy-data` volumes;
4. do not downgrade Alembic and do not delete or restore production data unless the accepted
   compatibility/restore procedure requires it;
5. close public TCP 443 if trusted TLS or route isolation cannot be restored promptly.

Record the failure code, configuration/image hashes, certificate continuity and post-rollback
health without secrets. A locally valid configuration or an automatic certificate log is not by
itself PA3 `PASS` evidence.
