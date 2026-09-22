# AutPlay v0.4.0 development release

Release date: 2026-09-22

`v0.4.0` is a complete development distribution of AutPlay: installable Android APKs, a
CPU-only `linux/amd64` personal-server installer, dependency inventories, SBOMs, a release
manifest and SHA-256 checksums. It is published from one immutable source tag after the canonical
host gate, APK signature verification, Docker archive reload and a disposable Compose runtime
smoke all pass.

This is still a **development pre-release**. The APKs use the retained development signer, the
bundled server targets a trusted personal network, and no app-store publication, container-registry
push or live production migration is performed by the release process.

## Highlights since v0.3.0

### Android and the music path

- Local-first playback, search, playlists, queue control, offline metadata and artwork remain
  available without an account or server.
- System-picker imports keep permission to the selected source and index it without making an
  unnecessary second copy. Uploads to Vault and downloads for offline playback remain explicit.
- Missing sync bindings are initialized before background work, long bootstrap snapshots use a
  separate bounded timeout, and a completed Internet acquisition can request one full snapshot
  when the expected server track was absent locally.
- Media3 downloads resume after an application restart; upload-offset parsing is header-case-safe.
- Developer mode is now a server-authorized setting instead of a stale device-local privilege.

### Accounts, trust and privacy

- Admin Web has four task-oriented areas: overview, accounts/devices, music/transfers and server.
- WebAuthn/passkeys, browser-session revocation and exact static-asset integrity checks are present.
- ACTIVE OWNER, ADMIN and USER accounts can add another owned device through a confirmed QR
  ceremony. Expired admissions recover safely and active-device ownership is rendered correctly.
- One-use TXT recovery rotates recovery authority and revokes prior devices, application/browser
  sessions, trust and passkeys before installing one replacement binding.
- Account deletion suspends access immediately, provides a 30-day cancellation window, protects
  the last active OWNER and records independent restore evidence.
- Shared-training consent is off by default, versioned, withdrawable and checked again at each
  preparation, execution and publication boundary.

### Personal-server runtime

- Account defaults of five devices, two playback operations and two transfers are enforced together
  with global admission. Retained I/O capacity is released only after exact process-tree exit.
- Vault upload/ingest recovery, orphan cleanup, metadata execution and training cleanup use durable
  authority and restart-safe reconciliation paths.
- The YouTube provider path supports an isolated, digest-pinned PO-token service. Provider failures
  are classified as retryable or terminal and persisted for Android instead of being reported as
  generic success or an unbounded retry.
- Public-edge and trusted-LAN Compose definitions retain their separate network boundaries. Public
  Internet activation, TLS evidence and a production rollout are not performed by this release.

## Published assets

- `autplay-0.4.0-dev-signed.apk` — minified hardened `app.autplay`, signed with the retained
  development key, `versionCode 12`; plain HTTP is disabled.
- `autplay-0.4.0-trusted-lan.apk` — separately identified debuggable `app.autplay.lan` for explicit
  loopback/RFC1918 HTTP testing.
- `autplay-server-v0.4.0.docker.tar.gz` — verified CPU-only `linux/amd64` Docker image archive.
- `autplay-server-v0.4.0-installer.zip` — image archive, exact Compose overlays, Windows/Linux
  installers, release notes and installation guide.
- `autplay-0.4.0-development-signing-cert.der`, unsigned APK, dependency report, CycloneDX SBOMs,
  `release-manifest.json`, `SECURITY_REVIEW.md`, `INSTALL_AND_PAIR.md` and `SHA256SUMS`.

## Verification

The release packager runs `scripts/check.ps1`, which covers locked Python projects, Ruff/format,
strict mypy, contract/release tests, the server suite against disposable PostgreSQL 18.4 with
pgvector 0.8.6, Android lint/unit/debug/trusted-LAN/release-R8, GPU static tests and Sona-training
tests. It then verifies both APK manifests and signer continuity, reloads the generated Docker
archive, checks API/worker/stream/media configuration and starts the packaged server in a disposable
Compose topology before generating the manifest and checksums.

Current implementation evidence and the still-separate target acceptance gates are indexed in
[`ADMIN_UNIFIED_GOAL_2026_09_19.md`](ADMIN_UNIFIED_GOAL_2026_09_19.md). The release bundle's
`release-manifest.json` and `SHA256SUMS` are the authoritative evidence for the exact published
files.

## Known boundaries

- Android requires API 26 or newer. The two APK variants use separate application IDs and databases.
- The server installer is CPU-only `linux/amd64` and is intended for a single operator on a trusted
  personal network. ARM64 is not included.
- Stable production signing, store policy, public domain/TLS activation, registry distribution,
  production secret delivery and rollout/rollback remain separate operator decisions.
- Sona-Lite shadow/training code is present, but the adaptive model is not active for recommendations;
  the deterministic CPU recommender remains authoritative.
- Face/Resonance Lens production analysis is not active. The local playback-reactive visualization
  does not infer mood, timbre or pitch.
- External music availability depends on the provider and the user's rights. DRM bypass is not
  supported.

Read the [installation guide](../operations/INSTALL_AND_PAIR.md),
[deployment boundary](../operations/DEPLOYMENT.md),
[backup/restore guide](../operations/BACKUP_RESTORE.md) and
[Android signing custody](../operations/ANDROID_SIGNING_CUSTODY.md) before installation.
