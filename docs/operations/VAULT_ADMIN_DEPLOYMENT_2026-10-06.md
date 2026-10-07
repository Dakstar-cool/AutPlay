# Vault Admin deployment - 2026-10-06

Deployed at `2026-10-06T12:08:03Z` with explicit user authorization.

Vault now explains upload, object and replica errors using bounded, redacted groups.
Authentication expiry, database outages, failed refreshes and timeouts have explicit
states and manual recovery. Diagnostics do not change quarantined data.

The dashboard Vault card and Vault page display filesystem usage, occupied/total
bytes and available space. Measurement uses the mounted Vault filesystem, includes
other files on that disk, has at most one in-flight read and expires after 30 seconds.
Disk usage observed after deployment: 74.3% occupied.

Admin timestamps use the browser timezone, including worker heartbeat and newly
refreshed fragments. The UTC fallback remains explicit when JavaScript is disabled.

## Identity

- Source base: `a17a4569ab462f2c650c757d1da433cdd58c0b03`.
- Base image: `sha256:3b89bdd445611e516559492d0d61f5ff80396328bbb26a733a7ff48e25de168c`.
- Deployed image: `sha256:1afca9623d7be0d8a3636c21414929018f6e1e47e7d60f793f993ff794b7be1e`.
- Runtime archive SHA-256: `e0aced879a2a8f34105d42552781120aa6f623154a6d3b9aab7a10a7f68f0a73`.
- Runtime files: 24.
- Existing production composition was preserved; only the disk sampler factory
  wiring was rebased onto it. Other working-tree changes were excluded.

## Verification

- 80 HTTP, presentation, sampler and browser checks passed.
- 8 disposable real PostgreSQL checks passed for account isolation, grouped errors,
  bounded diagnostics and dashboard totals; those SQL files were unchanged afterward.
- Ruff passed; mypy passed for 12 source files.
- Browser viewports 1440, 768, 390 and 320 pixels; Moscow and New York local time
  verified after health fragment refresh.
- Candidate and live image rendered RU/EN pages against production metadata in
  read-only transactions and read the mounted Vault filesystem as the runtime user.
- Live HTTP assets and external HTTPS assets matched their SHA-256 digests;
  certificate validation remained enabled. Phone API discovery stayed reachable.
- Application config, mounts, ports, network aliases and restart policy preserved.
- 13 other running containers retained their IDs.
- Docker autostart enabled and active; `unless-stopped` preserved.
- No migrations or persistent data repair performed. Rollback image/container kept.

An initial promotion automatically restored the original container when a check
looked for a legacy user service absent from this installation. Actual Docker
autostart was inspected and verified before the successful promotion.
