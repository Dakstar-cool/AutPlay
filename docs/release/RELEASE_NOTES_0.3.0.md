# AutPlay v0.3.0 development release

## Status

`v0.3.0` is a manually published development pre-release built from the repository's current
post-RC line. It is suitable for local evaluation and a trusted home LAN. It is not app-store or
public-Internet production distribution: the APKs use the retained development signer, the bundled
server topology uses cleartext HTTP on a concrete RFC1918 address, and production signing,
domain/TLS, backup and rollout decisions remain open.

## Highlights since v0.2.0

- Completed Android product surfaces for Home, local/Vault search, Library, artist/release/track
  detail, playback, downloads, queue editing, imports, profile statistics, sync status and server
  features.
- Added secure personal-server profile discovery, exact server identity confirmation, device-key
  enrollment and Web-approved device admission.
- Added loopback-only administrative Web for owner-scoped operations, trusted-device review,
  import review and server feature management.
- Added manual Jamendo discovery/import plus default-off, explicitly confirmed automation controls;
  provider policy and authorization are rechecked at every external-I/O boundary.
- Added same-server social foundations, presence, invitations and Android-only guest Wave access
  under bounded capabilities.
- Qualified the isolated GPU runtime boundary on the private RTX 3060 host. No model is approved,
  activated or packaged; CPU behavior remains authoritative.
- Recorded the Resonance Lens AutPlay Face direction as a non-normative design exploration. It is
  not implemented product behavior in this release.

## Installable assets

- `autplay-0.3.0-dev-signed.apk`: minified hardened Android APK, directly installable, development
  signed. Local playback works without a server; pairing requires an HTTPS server topology.
- `autplay-0.3.0-trusted-lan.apk`: debuggable development APK with separate application
  id `app.autplay.lan`; it permits the bundled RFC1918 HTTP server topology without exposing the
  hardened app's local data. Never use it on an untrusted network or public Internet.
- `autplay-server-v0.3.0.docker.tar.gz`: verified CPU-only `linux/amd64` Docker image archive.
- `autplay-server-v0.3.0-installer.zip`: image archive, exact Compose overlays, Windows/Linux
  installer/control scripts and the detailed install/pairing guide.
- `SHA256SUMS`, release manifest, signing certificate, dependency report and CycloneDX SBOMs.

Read `INSTALL_AND_PAIR.md` before installing or exposing any port.
The installer bundles the AutPlay image; a first run still downloads the digest-pinned
PostgreSQL/pgvector image unless it is already present in Docker's local cache.

## Verified release boundary

The release path requires the canonical root, contract, Android lint/unit/release/R8 and server
checks; APK signature verification; image identity/reload/config/media checks; and a disposable
Compose health run. Today’s pre-publication verification additionally covers the trusted-LAN build,
installer syntax/contract tests and the current post-RC regression set. Exact results and any
environmental limitation are recorded in the release manifest and GitHub release notes.

## Known limits

- Development signing only; no Play/App Store policy or production key custody is claimed.
- The installer is CPU `linux/amd64`, single-operator and trusted-LAN only.
- Administrative Web stays literal loopback. There is no password login or public registration.
- The packaged Compose database/Vault topology is a development topology, not an approved
  production storage or backup system. Do not use `down --volumes` for data you need.
- No public domain/TLS/reverse proxy, production secrets delivery, off-host backup target,
  retention policy or registry publication is selected.
- No GPU model weights are bundled or active. Face/Resonance Lens remains design exploration.
