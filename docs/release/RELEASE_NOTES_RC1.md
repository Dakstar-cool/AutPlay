# AutPlay RC1 local candidate release notes

## Status

This is a reproducible local release candidate, not a production release. Its CPU/local-first
build, migrations, restore drill, security/object-authorization audit, dependency inventory,
large-search benchmark, API 26 connected suite and physical Samsung SM-A556E qualification pass.
No image or APK was pushed, deployed, published or production-signed.

## Included

- Android offline library, search, playlists, playback/download ownership, sync status, import
  review, deterministic recommendations and trusted-local Wave recovery through Room v10.
- Optional CPU-only FastAPI/PostgreSQL server with owner/device sessions, immutable filesystem
  Vault, Range streaming, sync/import/recommendation jobs and Wave rooms through Alembic `0015`.
- Reproducible local CPU image and dev-signed RC APK, CycloneDX SBOMs, resolved Python/Android
  license metadata, OSV audit reports, pinned artifact inventory, backup/restore and observability/
  failure runbooks, plus explicit privacy/export/delete operating guidance.

## Deferred and excluded

- P12 A-030 real CUDA OOM, RTX throughput/job-time/VRAM and reviewed-model quality evidence is
  `DEFERRED_WITH_APPROVAL` by ADR-027. No GPU model is packaged, active or required; deterministic
  P11 CPU recommendations remain authoritative.
- Public Internet/TLS/reverse-proxy topology, cross-instance Wave fanout, production backup target
  and retention, external providers, production signing, store policy, publication and deployment
  are outside this local candidate.
- LGPL/MPL notice/source/relinking review and NVIDIA GPU dependency redistribution review remain
  mandatory before publication; P14 records these obligations and performs no publication.

## Physical Android qualification

- The dev-signed v2/v3 RC APK installs on a physical Samsung SM-A556E (`arm64-v8a`, Android SDK 36).
- Background transition, absence of a battery-optimization bypass request, force-stop process death
  and restart pass without uninstalling the package or clearing user data.
- Device serial evidence is stored only as SHA-256; the raw serial is not persisted.

## Evidence entry points

- `docs/release/RC1_CHECKLIST.md`
- `docs/release/TEST_EVIDENCE.md`
- `docs/release/SECURITY_REVIEW.md`
- `docs/release/PERFORMANCE_REPORT.md`
- `docs/operations/PRIVACY_DELETE_EXPORT.md`
- `docs/implementation/evidence/P14_RELEASE_INVENTORY.json`
- `docs/implementation/evidence/P14_LICENSE_INVENTORY.json`
- `docs/implementation/evidence/P14_ANDROID_PERFORMANCE.json`
- `docs/implementation/evidence/P14_BACKUP_RESTORE_2026-08-17.json`
