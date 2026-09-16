# Phone sync and completed-download publication, 2026-09-16

The private production server is connected to the Samsung M52 installation. Completed downloads
now enter the owner's personal Vault automatically and reach the foreground Android library without
manual import or a manual sync command. Archive backfill continues independently on the server.

## Implemented and deployed

- Bind the existing device journal before sending events. Recover only the active lineage's
  previously rejected `JOURNAL_RESET_REQUIRED` events; retain payloads, IDs and hashes.
- Resolve old profile-projected Android track IDs only against that owner's server refs. This does
  not match titles or expose another owner's records.
- Persist `ORGANIC` for new listening events originating from the `HOME`/`LIBRARY` presentation queues;
  preserve unknown values and the presentation queue's original source.
- Refresh through WorkManager every five seconds while the app is in the foreground, using `KEEP`
  to avoid overlapping workers.
- Run the local [acquisition bridge](../../tools/ACQUISITION_VAULT_BRIDGE.md) with durable checkpoints,
  eight bounded I/O tasks, ordinary import/review/upload/ingest services, and newest-file priority.
  Ready tasks publish independently; media verification and immutable-byte checks remain enabled.
- Scope catalog publication for standalone imports, retaining full release closure when required.
  Publication uses the same owner-before-library lock order as ordinary sync.

The private API and stream gateway remain in place. No public listener was enabled. Server container
configurations and a database dump were retained before deployment. The downloader was not replaced
or reset. Two bounded temporary CPU workers assist archive backfill; a host service retires only those
two workers when the captured archive cohort reaches a terminal state. Normal ingestion continues.

## Existing data and verification

- Room schema 16 was retained on the phone. The matching `m52-ui` worktree supplied the installed APK;
  its pre-existing UI and schema changes were preserved. No application data was cleared.
- Original 28 current-profile journal events were recovered. Later readback showed 31 acknowledged
  events and no active-profile errors. The old inactive profile's unrelated failure was retained.
- An audited, exact-ID repair applied 21 historical listening projections while retaining their
  original inbox payloads, hashes and sequence numbers. Repeating it made no further changes.
- All 24 pre-existing canonical tracks passed normal owner playback resolution. Their stale `PENDING`
  statuses were repaired to `VAULT` with versioned sync events. Four entries without canonical audio
  were left pending.
- The legacy archive inventory reconciled 1,395 MP3 paths into 1,394 receipt identities. All associated
  audio was fully SHA-256 checked, metadata mapped without unmatched files, and all generated receipts
  passed ffprobe validation.
- Two exact-byte duplicate pairs had punctuation/case-only title differences. Explicit operator
  review accepted only the immutable candidate for the already verified recording. Both source import
  entries and their original metadata remain; ordinary review reuses the owner's resolved track ref.
  Both second uploads ended `REUSED`, and both pairs resolve to their original verified variants.
- At 08:10 UTC, all **1,161** then-advertised `VAULT` library entries passed the normal owner resolver
  and real reads of the first and last 1,024 bytes using the production immutable-file checks:
  **zero failures**. Backfill was still running; this is not a claim that the entire archive was ready.
- Actual phone playback and seeking succeeded. A track completed during the run appeared without
  manual import/sync and played for over 40 seconds with no Media3 error. Its initial measured
  completion-to-publication delay was 45.7 seconds, before the independent scheduler improvement.
- After the final scheduler restart, six fresh downloads took **15.5-25.1 seconds**, median
  **18.9 seconds**, to become published. The next new item was still processing at age 5.8 seconds.
- At 08:15 UTC, the bridge had 2,832 valid receipt identities: 1,294 published, 12 in processing,
  and 1,526 waiting. There were no invalid receipts or bridge review failures. These counts continue
  changing; receipt identities include two reviewed aliases of existing audio.

The downloader is supervised by its enabled user systemd service (`Restart=on-failure`, user
lingering enabled), so its container-level `restart=no` does not disable boot recovery. The bridge
uses Docker `unless-stopped`. Restart/replay was observed successfully, rather than inferred solely
from configuration. The disposable local PostgreSQL test project and volume were removed; production
data, backups, download files and the server's normal worker remain intact.

## Checks and build

- Real PostgreSQL: 15 sync tests; 6 artist-contract tests; 14 bridge/import-identity tests before the
  scheduler change; final bridge regression suite **5 passed** after scheduler and lock-order fixes.
- Actual M52 source: **293 JVM tests passed**, zero failures/errors/skips; debug and instrumentation
  APKs built with one Gradle worker and the standard heap.
- Real phone: 3 sync acceptance regressions and 1 queue-origin/listening persistence regression passed.
- Ruff and changed-file formatting passed; read-only review findings were fixed and affected tests rerun.

Installed application: `app.autplay`, version code `9`, version `0.3.5-m52-sync`.
APK SHA-256: `2dbd6eae87d3f2a910a81e1f68880054f89492f4ddaea9e04141cde5004bd0de`.
Private evidence and recovery scripts are kept in the ignored task scratch directory and on the
operator host; credentials, owner IDs and private origins are excluded from this report.

Git: local changes in the main checkout plus narrowly ported Android changes in the existing M52
worktree. No commit, push or release publication was requested or performed.
