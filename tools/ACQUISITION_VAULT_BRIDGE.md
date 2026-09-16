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

Repeat `--root` for additional explicitly selected roots. Each completed item must have
`tracks/<key>/receipt.json` and the receipt's `<provider>/<filename>` beneath that directory.
Receipts must contain the full SHA-256, exact byte length, positive duration and declared metadata.
The first run explicitly provisions one deterministic owner device; revoking that device stops
publication. Use a process supervisor with restart-on-failure and preserve the checkpoint volume.

The process:

1. Checks receipt containment, file stability and full content hash.
2. Imports exact metadata and creates a distinct recording through the existing explicit review
   command. Similar titles/artists never silently merge recordings.
3. Uploads with stable replay keys; the CPU workers perform normal media validation and ingestion.
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
tampering, malformed receipt isolation, and fresh-arrival priority during backfill. Use the normal
disposable PostgreSQL fixture for `server/tests/postgresql/test_acquisition_bridge.py`.
