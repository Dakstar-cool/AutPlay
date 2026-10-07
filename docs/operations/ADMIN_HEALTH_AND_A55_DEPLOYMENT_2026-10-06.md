# Admin health, trusted devices and A55 network policy - 2026-10-06

The user-authorized server update was deployed to the active personal-server installation
at 2026-10-06 10:12:05 UTC. The previously used server is retired by the user's
instruction. Existing backup mounts, runtime configuration and unrelated
containers were preserved.

## Result

- Trusted devices use compact rows/cards with only Delete and Block/Unblock.
  Delete revokes current access and removes future trust; blocking applies to
  future connections. Connection dates and ascending/descending name/date
  sorting are available, including on narrow screens.
- Audit is the single navigation destination; old diagnostics links redirect.
- CPU worker health reports real cgroup CPU usage, memory, job counts and a
  heartbeat. Stale observations expire rather than remain healthy indefinitely.
- Vault status lists its concrete warning reasons. At the recorded live probe,
  59 quarantined uploads caused the warning. There were no missing, corrupt or
  quarantined replicas and no quarantined committed objects. These counts are
  observations, not permanently fixed values.
- A55 initially had an empty library because its queued sync required an
  unmetered network while the phone was on mobile data. It now has 16,380 library
  entries. Metadata sync and streaming can use any connected network.
- The former metered-sync switch is now **Download tracks over mobile data** in
  Settings / Downloads. It controls durable offline audio transfers only.
  Media3 applies preference changes to active/pending downloads. Legacy local
  preferences and version-1 portable settings remain readable; new exports use
  schema version 2 and the download preference key. The switch is disabled on
  A55; this does not prevent metadata sync or streaming.

## Deployment identity and recovery

The allowlisted server payload contains 23 runtime files, inherited from the
exact previously running images. It is not a deployment of the entire dirty
workspace. Payload SHA-256:
`39bccc2baadb27a2c3db9c218adc15556a5119f1282afe1dbaa906f90572a685`.

| Runtime group | Image ID |
| --- | --- |
| API, Stream, operator, music worker | `sha256:e455157ad3d2a61204e8274d199a3bf7e62283543901dc8929afcfd409ebb735` |
| Mobile API | `sha256:6cceef1d287a56976c713df0f1ce35a7b618a16a6fbc7bf8053bd5ed4446c907` |
| Admin Web | `sha256:3b89bdd445611e516559492d0d61f5ff80396328bbb26a733a7ff48e25de168c` |
| CPU worker | `sha256:fa9fb2d807b0892c2c2f846584fd03d14d6d1d2564c51a174eb11957b4ba9056` |

Migration `0062_cpu_worker_health` adds worker observations after
`0061_admission_device_name`. Before migration, a 1,066,804,205-byte custom-format
PostgreSQL dump was created and its restore catalog verified. Dump SHA-256:
`2c22b7457a998d65041bb83cb87c12db559d0472f00289ae47936618442cc8f6`.
The database dump and secret-bearing container snapshots remain host-local with
restricted permissions.

All seven updated services passed their applicable health/readiness probes;
installed file hashes and preserved runtime configuration matched. Seven
unrelated containers retained their identities and running state. The
acquisition agent was quiesced during migration and resumed online. Original
containers remain stopped with suffix `-before-admin-health-20261006` for
recovery. Recovery images retain revision 0062 readiness and migration support;
recovery does not rewind production data or remove the new table.

Host material: `host-deployment-material/admin-health-20261006`.
Local manifests, receipts and the delivered APK:
`local-checkpoints/admin-health-deployment-20261006`.

## Android identity and verification

The delivered A55 APK is `autplay-vault-sync-debug.apk`, package `app.autplay`,
debug version 1.0.5 / code 18, installed at 10:22:46 UTC. SHA-256:
`3200e188cb21c7418b5cd7df6bc5efc97c9fe0ef4117a79fb22f3ddee4b45521`.
The installed APK bytes matched this hash. Updating preserved the database inode,
first-install timestamp and all 56 Room table counts; no application data was
cleared. The scoped network-policy source snapshots and task-only patch cover
19 files, including two new offline-download policy files.

- 12 selected Android unit tests passed, covering preference migration,
  portable settings compatibility, download network requirements and worker
  behavior. The committed Gradle wrapper, standard heap and one worker were used.
- `assembleDebug` and `compileDebugAndroidTestKotlin` passed. Instrumentation was
  compiled, not executed against the real user's application data.
- Metadata sync completed over mobile data with the offline-download switch
  disabled. The library contains 16,380 records; this is a metadata count and
  does not mean those audio files were downloaded locally.
- The user confirmed playback works and requested stopping phone verification.
  Temporary playback diagnostic source edits were restored and never installed.
  A later unused diagnostic APK in the Gradle build directory is not the
  delivered artifact; use the frozen checkpoint APK above.

## Server validation evidence

63 final focused worker, HTTP, rendering and browser checks and 29 final real
PostgreSQL checks passed. The database checks cover migration/schema parity,
account scoping, stale observations, writer fencing and Vault reason counts.
The sampler was also exercised against a real CPU-limited Linux container with
a busy child process. All disposable test containers, networks and volumes were
cleaned up. Ruff and mypy passed for the affected Python sources.

Live probes verified authenticated-page login redirects, RU/EN templates, five
Admin static asset hashes, database readiness and the revoked-device projection.
Private HTTPS certificate validation, login, existing static assets and signed
mobile discovery passed. The new dashboard script passed the live loopback HTTP
hash check. The live CPU heartbeat was fresh with non-null CPU/memory metrics;
the recorded Vault warning was attributable to quarantined uploads.

Earlier UI and worker-health source manifests, task patches and synthetic
screenshots are retained in the adjacent `admin-trusted-layout-20261006` and
`admin-worker-health-20261006` checkpoints. Their earlier validation notes remain
pre-deployment evidence; this document and the deployment/install receipts record
the subsequent production update.
