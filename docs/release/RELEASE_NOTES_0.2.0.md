# AutPlay 0.2.0 release notes

## Status

AutPlay 0.2.0 is the first GitHub release of the current local-first development line. It is
published from immutable tag `v0.2.0` in the private project repository. The release provides an
installable minified Android APK signed with the retained local development key, a matching unsigned
APK for downstream production signing, and a Linux x86_64 CPU server image as a compressed Docker
image archive.

This is not a production deployment or store-ready Android distribution. No production signing
key, public server endpoint, container registry, persistent database target, secret, or user data is
used by the release process.

## Assets

- `autplay-0.2.0-dev-signed.apk`: installable minified development release; signed by the retained
  local development key. The release manifest records the certificate SHA-256, and the certificate
  is published as `autplay-0.2.0-development-signing-cert.der`.
- `autplay-0.2.0-unsigned.apk`: minified/R8 release build; must be production-signed before normal
  end-user distribution.
- `autplay-server-v0.2.0.docker.tar.gz`: CPU-only Linux x86_64 Docker image archive. Load it with
  `docker load --input <archive>`; runtime configuration still follows `deploy/compose/README.md`.
- `release-manifest.json`, `SHA256SUMS`, and CycloneDX SBOMs: source binding, image identity,
  artifact hashes, and dependency inventory.

## Included since 0.1.0 RC1

- Adaptive Android shell, Media3 playback/download surfaces, Home, Search, Library, Wave, Profile,
  Settings, and typed track/release/playlist/artist detail surfaces.
- Web-approved exact-key Android admission and recovery without plaintext credential persistence.
- Accepted friends, privacy-bounded coarse presence, fast Wave invitations, and capability-limited
  guest access to exactly one room.
- Offline owner profile statistics plus default-private friend visibility controlled by explicit
  opt-in and rechecked friendship/block policy.
- Duplicate-preserving manual playlists and a durable ordinary queue with process-death recovery.
- Manual TXT/Jamendo discovery plus an independently enabled, default-off 24-hour automation path
  that requires a separate `AUTO_IMPORT` confirmation.
- Optional CPU-only server-rendered administration for devices, sessions, Vault, jobs, imports,
  backup/restore operations, and local profile pairing.

## Verification

- Canonical contract/release gate: 119 tests PASS.
- Android host gate: lint, 199 JVM tests, debug APK, and minified release/R8 PASS.
- Server gate: 662 tests PASS against disposable PostgreSQL 18.4 and pgvector 0.8.6; one expected
  Windows host-policy symlink test is skipped.
- CPU image build: pinned Python/uv, FFmpeg 8.1.2, ffprobe 8.1.2, and fpcalc 1.6.1 hashes and
  versions verified; archive reload, non-root configuration checks, and offline media smoke PASS.
- The release manifest and `SHA256SUMS` bind the downloadable assets to the tagged Git commit.

## Deliberately not claimed

- The Android APK is not production-signed and is not distributed through an app store.
- The Docker image archive is not pushed to a registry and is not deployed automatically.
- Friend-visible statistics are not Internet-public. Collaborative playlists and cross-device
  active-queue synchronization are not included.
- Public Internet/TLS topology, multi-instance Wave fanout, production backup target, secret
  delivery, and account registration/legal policy remain operator decisions.
- P12 real RTX/model evidence remains deferred with approval; no GPU model is active or required.
