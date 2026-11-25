# Completed downloads to the personal Vault

`acquisition_vault_bridge.py` is a privileged **local operator** process. It publishes completed
acquisition receipts through the existing import, identity-review, resumable-upload and fenced
Vault ingestion services. It opens no network listener and creates no bearer credentials.

Run it in the matching CPU server runtime with the normal API settings/secret-file configuration,
read-only mounts for acquisition roots, the existing writable Vault volume, and a private writable
checkpoint directory. The service account needs read access to receipt/audio files and write access
to its Vault and checkpoint directories. Do not expose download directories through a web server.

```text
python acquisition_vault_bridge.py --root /music --state /checkpoints --owner OWNER_UUID --provision-device
```

Android playlist imports enqueue catalog requests after the recipient syncs the playlist.
The continuous bridge processes these requests in one background lane when Internet music
and the shared server catalog are enabled. The bridge uses the configured Internet music
provider, including its runtime and token provider, and stages pending audio under
`--state/playlist-audio`. Imported entries and likes belong to the recipient. Up to five
additional catalog tracks per artist use the existing verified operator publication path.

Repeat `--root` for additional explicitly selected roots. Each completed item must have
`tracks/<key>/receipt.json` and the receipt's `<provider>/<filename>` beneath that directory.
Receipts must contain the full SHA-256, exact byte length, positive duration and declared metadata.
The first run explicitly provisions one deterministic owner device; revoking that device stops
publication. Use a process supervisor with restart-on-failure and preserve the checkpoint volume.
The provisioned device is represented by the narrow `LOCAL_BRIDGE` authority introduced in
`0060_local_bridge_authority`. It creates no `account.user_session`, cannot authorize playback or
download, and is accepted only for an owned `UPLOAD_INTENT`. Every chunk consumes the measured
transfer budget, runs in the retained Vault child, commits only after current account/device/grant
revalidation, and releases its permit after exact child-exit acknowledgement.

The process:

1. Checks receipt containment, file stability and full content hash.
2. Imports exact metadata and creates a distinct recording through the existing explicit review
   command. Similar titles/artists never silently merge recordings.
3. Uploads with stable replay keys through the ordinary quota, retained-process and commit-guard
   path; the CPU workers then perform normal media validation and ingestion.
4. Selects the upload's verified audio variant only when no canonical choice exists.
5. Confirms owner-scoped playback resolution, then publishes `VAULT` availability and catalog sync
   events in the same library transaction.

PostgreSQL owns jobs, upload offsets, identity and library state. Checkpoints cache their IDs and
are atomically persisted; re-execution after a process failure replays existing operations.
One advisory file lock prevents two publishers sharing a checkpoint directory. Eight bounded I/O
threads process independent identities, and new completions take priority over archive backfill.
Completed identities are checkpointed immediately, without waiting for unrelated uploads. Pending
worker results are checked again after one second; new receipts are scanned every two seconds.
Workers may run independently using the existing PostgreSQL lease/fencing implementation.

`status.json` reports receipt/state counts, invalid receipts, scan timing and in-flight work. `NEEDS_REVIEW`
keeps a stable error code without publishing unverified audio. In particular, equal file hashes with
different declared recording identities must not be forcibly merged or have their checkpoints
deleted to suppress an identity conflict. Check owner-scoped existing playback and review the
identity evidence first. Interrupted uploads resume at the server's offset.

The Android app binds its existing device journal before syncing and refreshes through WorkManager
every five seconds while in the foreground. Background operation remains governed by Android's
WorkManager constraints. Actual time from file completion to playback also includes server media
verification; the app does not advertise a file as ready before that verification succeeds.

Validation covers real PostgreSQL/filesystem publication, replay after each command, immutable-byte
tampering, malformed receipt isolation, fresh-arrival priority during backfill, zero fabricated
user sessions, `LOCAL_BRIDGE`-only admissions, exact permit/execution cleanup and device revocation.
Use the normal disposable PostgreSQL fixture for
`server/tests/postgresql/test_acquisition_bridge.py`.


## Music Preservation Watcher

The bridge runs a preservation scan for its owner at startup and once seven days have
elapsed since the last completed scan. The acquisition page can request an earlier scan.
The page displays findings, source failures, original-edition checks and job outcomes.
PostgreSQL retains the observations for each run. Install migration
`0079_music_preservation` with the updated API and bridge.

The bundled registry, MusicBrainz withdrawn-release catalogue, Guardian music feed and
US iTunes edition lookups work without credentials. Additional curated sources live under
`<bridge --state>/preservation/feeds/*.json`. Each file contains an array of observations
or an object with a `url` pointing to an HTTPS JSON array. Use a stable `event` across
sources and revisions. Records carry `kind` (track, album, catalog or lead), `artist`,
`title`, `version`, `scope`, `date` (ISO date or empty), `reason`, `url`, `confidence`
(review or confirmed) and `status` (reported, historical, active, restored or retracted).
A source can include `original_sha256` to check an identified edition in Vault.

The download worker uses the bridge's existing internet-music and catalog settings.
Supply existing acquisition rights in the bridge-owned
`<bridge --state>/preservation/originals.json`. A licensing system can maintain this
file by atomic rename. Its keys are event IDs. Each entry identifies the original with
the same artist, title and version as the case, SHA-256 of the downloadable file,
duration in milliseconds and the existing provider's candidate ID. Set `allow_vault`
for an acquisition and storage grant and supply its `rights_url` and timezone-aware
`expires_at`. News reports and community feeds cannot issue grants. The worker
rechecks the grant on use and verifies the file digest and source tags before ingest.
Queued jobs have priority 1. An album or catalogue can list its original recordings in
a `tracks` array. Each item supplies its own stable `event`, `artist`, `title`, `version`
and optional `original_sha256`. The watcher checks and acquires those tracks individually
using their own grants. A retracted collection or a removed track stops pending acquisitions.
A collection without an identified track list and unconfirmed reports remain informational.
