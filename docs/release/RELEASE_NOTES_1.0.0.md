# AutPlay v1.0.0 production release candidate

Release candidate date: 2026-09-22

`v1.0.0` (`versionCode 13`) is the immutable source identity for AutPlay's first production
candidate. It retains the Android-first, local-first application and optional CPU-only personal
server from the qualified development line, and adds the fail-closed production signing,
supply-chain audit and cross-artifact finalization path.

This document does not claim that the tag is published or deployed. A distributable candidate
exists only when `production-release-manifest.json` and `SHA256SUMS` bind the exact production-
signed APK, signer certificate, Room schema, CPU Docker archive/config digest, Alembic head and a
fresh release audit from this tag. That manifest deliberately records `production_deployed=false`.

## Candidate contents

- `app.autplay`, version `1.0.0`, `versionCode 13`, built as one minified production-signed APK.
- The latest tracked Room schema, bound by version, identity hash, repository path and SHA-256.
- A CPU-only `linux/amd64` server Docker archive whose archive hash and configuration digest are
  recorded independently.
- The single linear Alembic head and exact head migration hash.
- Fresh CycloneDX SBOMs, vulnerability results, Python/Android license inventory, source secret
  scan and artifact provenance for the immutable release inputs.

## Android transition

Existing A55 and M52 development-signed installations are disposable test installations. The
accepted first-release transition is a controlled uninstall of `app.autplay`, followed by a clean
install of the exact production APK and fresh qualification. Uninstalling deletes the application's
Room database, DataStore, encrypted preferences, Android Keystore entries, persisted grants,
offline downloads and private caches. This transition must not be used for an installation whose
local-only state matters.

The exact production APK must still pass the physical Samsung A55 checks for package/version and
signer identity, local-first playback, process death/restart, media re-indexing, pairing/recovery,
sync and accessibility surfaces. Historical emulator, M52 or development-signed APK evidence does
not transfer to this artifact.

## Product boundary

- Local playback, search, playlists, queue control and local mutations remain available without a
  synchronous server dependency.
- The optional personal server remains CPU-only; GPU, Sona-Lite and semantic Face models are not
  required for the deterministic production core.
- The neutral PCM-reactive Face remains the safe fallback. Full semantic Face qualification is a
  separate unfinished phase.
- Real worker resource budgets, Admin/account target Gates A-D, public-edge certificate/external
  scan/renewal/rollback checks and real-mobile API/Range evidence remain deployment gates.

## Distribution and activation boundary

The local build and finalization workflow does not push a Git tag, create a GitHub Release, upload
an image, publish an APK, change DNS/firewall/TLS, migrate a persistent target or activate a live
production deployment. Direct APK distribution is the selected first-release channel, but actual
publication and live activation require separate operator approval after exact-artifact and target
evidence pass.

Read the [production readiness plan](../operations/PRODUCTION_READINESS_PLAN.md),
[Android signing custody runbook](../operations/ANDROID_SIGNING_CUSTODY.md),
[CI/release boundary](../operations/CI_RELEASE.md) and
[deployment boundary](../operations/DEPLOYMENT.md) before handling the candidate.
