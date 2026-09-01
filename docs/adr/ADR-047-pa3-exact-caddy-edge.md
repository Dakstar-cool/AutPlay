# ADR-047: PA3 exact Caddy edge and proxy-source evidence

- Status: Accepted for the local PA3 candidate under standing technical-decision authorization;
  live qualification blocked
- Date: 2026-09-01
- Scope: Public Access PA3 only

## Context

PA2 deliberately ignores peer and forwarded addresses and uses the server-global redemption rate
window until PA3 establishes an exact trusted edge. The accepted PA1 contract requires public PKI,
only TCP 443, spoof-resistant forwarded-source parsing and no public Admin, database, health,
metrics or raw service listener. Existing trusted-LAN Compose publishes raw HTTP ports and is not a
production topology.

## Decision

1. Use the Docker Official Caddy `2.11.4-alpine` Linux/amd64 image pinned to
   `sha256:98eb57d882ccd5213d1688764db10c1ca2c58a1ca3a6717a3411ad798f7a423a`.
   Caddy is Apache-2.0 and stays a replaceable edge adapter.
2. Caddy is the sole public service and publishes IPv4 TCP 443 only. HTTP redirects, the ACME
   HTTP-01 challenge and HTTP/3 are disabled. TLS-ALPN-01 uses TCP 443. Certificate state is a
   persistent named volume and is never recreated during a config rollback.
3. `api.autplay.win` routes only `/api/v1/*` except `/api/v1/wave*` to the admin-disabled mobile API.
   `stream.autplay.win` routes only `/api/v1/stream/*` to the stream process. Admin, health, metrics,
   Wave and unmatched paths return a non-disclosing `404` at the edge.
4. Uvicorn keeps `proxy_headers=False`. Caddy overwrites standard forwarding fields and the private
   `X-AutPlay-Client-IP` field. PA2 accepts that field only when the socket peer equals one exact,
   configured Caddy address and the field has one canonical parseable IP value. Invalid, duplicate,
   chained or untrusted evidence becomes no source evidence and therefore cannot create a source
   rate bucket; the existing server-global fallback remains authoritative.
5. Tailscale SSH is management-only. It is not joined to Compose networks, exposed to Android or
   treated as product authentication.

## Consequences

The edge can be tested without weakening PA2 or enabling Uvicorn's broad proxy mode. The fixed
Compose address requires a deployment preflight for subnet conflicts. Host compromise remains
outside application-layer containment, while a compromised or misconfigured edge cannot turn an
arbitrary forwarded chain into identity or authorization.

The overlay is not production-ready evidence by itself. PA3 stays blocked until off-host restore,
stable Android signer custody, real TLS/renewal/rollback, an external port scan and mobile-network
API/Range evidence pass.

The local candidate passed the 2026-09-01 canonical root and server gates: 141 contract/release
tests and 712 server tests passed, with one expected Windows symlink-policy skip. No public port,
certificate, DNS/firewall mutation, server deployment or production credential was used.

## Rejected alternatives

- Cloudflare proxy or broad trusted CIDRs: changes/expands the authority chain.
- Uvicorn `proxy_headers=True`: broad implicit parsing is larger than the PA1 requirement.
- public HTTP port 80: conflicts with the TCP-443-only gate.
- self-signed TLS or a user CA: fails the platform-PKI Android requirement.
- keeping the Android signing key on the server: one host compromise would gain runtime and update
  authority.
