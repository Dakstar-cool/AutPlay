# II security remediation, 2026-10-07

The security and playback chats coordinated the server changes. Android/ADB work
belongs to the playback chat. This bundle retains each exact deployed image as
its base, including the already deployed stream threadpool fix. It does not carry
the current checkout's unrelated server changes or migrations.

## Changes and qualification

- Public account management accepts the `Principal` set by the real shared Bearer
  dependency, while rejecting absent or incorrectly typed principals. OWNER,
  USER, missing and invalid credentials are exercised at the HTTP boundary.
- Caddy requires the HTTP Host to match TLS SNI. The adapted configuration is
  checked with pinned Caddy 2.11.4 and the existing private-route ordering remains
  qualified. See [Caddy's option reference](https://caddyserver.com/docs/caddyfile/options#strict-sni-host).
- The narrow image overlays install hash-verified PyJWT 2.15.0, urllib3 2.8.0 and
  Mako 1.4.2 without updating other installed distributions. Each base retains
  its own manifests, with only affected package records/requirements amended.
- [The egress guard](../../deploy/operations/autplay-egress-guard.py) controls an
  independent `inet autplay_egress` nftables table. It does not replace Docker's
  rules. Container traffic terminating on the host is checked in INPUT;
  forwarded traffic is checked separately. The distinction follows
  [Docker's firewall documentation](https://docs.docker.com/engine/network/packet-filtering-firewalls/).

## Egress policy

The guard discovers existing `autplay-*` bridge networks. Traffic from those
bridges to host TCP 53/443 and UDP 53 is allowed; other new host connections are
rejected. Existing connections remain usable. Forwarded traffic to the discovered
AutPlay subnets and globally routed provider destinations is allowed; other
private, loopback, link-local and multicast destinations are rejected. Docker's
own forwarding/isolation rules continue to apply after this table.

An isolated network-namespace fixture exercised actual packets for SSH, Ollama,
RDP, raw API, DNS, HTTPS, private LAN, Internet and PostgreSQL. Administration
outside a protected bridge, atomic reload and policy removal were also verified.
No production routes were used for that fixture.

Install the reviewed guard at `/usr/local/lib/autplay/autplay-egress-guard.py`
with [its unit](../../deploy/operations/autplay-egress-guard.service) at
`/etc/systemd/system/autplay-egress-guard.service`. The unit applies after Docker
starts. After intentionally creating/replacing an AutPlay network, reload it:

```sh
systemctl reload autplay-egress-guard.service
```

Before introducing a legitimate private-network integration, review its exact
destination and port. The present policy supplies no broad LAN exception.
Topology changes need renewed packet and live dependency checks.

## Promotion and rollback

The target-local evidence directory is
`/srv/autplay/production/security-remediation-20261007`. It contains image
identities, exact prior container snapshots, the narrow Compose image override,
the previous Caddyfile, progress and acceptance results. Private snapshots stay
owner-only and must not be published.

Promotion requires no RUNNING jobs. Qualified overlays replace app containers
sequentially, preserving environment, HostConfig, semantic mounts, aliases and
static endpoint configuration. The stopped originals remain disconnected under
`<original-name>-rollback-security-20261007`; their images are retained. Caddy
keeps its certificate volumes and container identity. Schema must remain
`0062_cpu_worker_health`.

Acceptance covers installed package/module identities, OWNER access, missing
authentication, Host/SNI rejection, discovery, protected host ports, PostgreSQL,
authorized Range byte continuity, HEAD/416/If-Range and abort descriptor cleanup.
Synthetic USER isolation, refresh/replay and revocation are rerun afterward;
their generated accounts and data must be removed with verified exact-ID cleanup.

On failure the guarded promotion restores containers, Caddy and the previous
operator image model and removes the new nftables table. Manual rollback uses
the saved snapshots and reconnects the originals' recorded endpoints; no schema
downgrade, data deletion or volume removal is involved. To remove only the guard:

```sh
systemctl disable --now autplay-egress-guard.service
python3 /usr/local/lib/autplay/autplay-egress-guard.py remove
```

Full live audit acceptance still needs the separate external-WAN and Android security
gates. Keep this remediation evidence distinct from those unperformed checks.

## Target result

Promotion completed with `DEPLOYED_PASS`: eight app containers, six qualified
overlay images, no schema changes, all target services running. The live
synthetic API retest passed 40/40 and exact-ID cleanup left zero fixture rows.
OSV checked 57 distinct installed public package/version pairs with no findings.

External DNS/TLS to PyPI and Cover Art Archive passed from the mobile API and
both workers. Direct MusicBrainz DNS/TCP succeeded, but its TLS handshake timed
out from the host as well as the containers. Through the existing music SOCKS
proxy the workers received HTTP 200; the mobile API sample received HTTP 503.
The guard was not relaxed for this unrelated external-path limitation.

The coordinated playback chat subsequently completed physical A55 acceptance:
498 unit, 39 playback instrumentation and 29 UI instrumentation checks passed.
The final APK retained the existing signature and application data; Room
integrity passed. Twelve MediaSession observations following a real natural
transition were PLAYING without observed repeated buffering. PCM audio gaps
were not measured, and seeking into an unloaded tail took about 7.8 seconds.
This playback acceptance does not replace the full Android security audit.
