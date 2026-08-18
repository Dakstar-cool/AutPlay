# AutPlay privacy, export and deletion runbook

## RC1 privacy boundary

AutPlay is local-first. The Android library, playlists, playback state and Offline Journal remain
usable without a personal server. Connecting a personal server sends only the authenticated
owner's versioned sync events and explicitly authorized uploads/imports. RC1 has no telemetry,
advertising SDK, public sharing, cloud-account provider or automatic external-source scraping.

Vault knowledge is not authorization: every download, stream, object lookup, export and sync
projection remains owner-scoped. Normal logs and support evidence must not contain bearer tokens,
private URLs, raw filesystem paths, credentials, media bytes or personal payloads. Operators must
use the documented redacted metrics and stable error codes instead.

Android backup is deliberately disabled (`android:allowBackup="false"` and
`android:fullBackupContent="false"`). Server credentials are held by Android Keystore-backed
storage; a revoked URI permission or invalidated device credential fails closed and is repairable
by re-authorizing the source or reconnecting the profile. No destructive Room fallback is used.

## Owner export procedure

RC1 does not expose a one-click production export endpoint. Until that feature is delivered, an
owner export is an operator-assisted, quiesced export from an isolated copy or verified backup:

1. Authenticate the requesting owner and record a redacted audit reference.
2. Restore or snapshot into an isolated environment with no production routing.
3. Export only rows whose owner identifier matches the request: library references, playlists and
   order, ratings/history, settings, source metadata, sync events and explicitly owned Vault
   objects. Do not export another owner's shared deduplicated bytes or administrative/audit data.
4. Use a versioned manifest containing logical identifiers, schema version, byte sizes and SHA-256;
   omit tokens, credentials, private URLs and raw server paths.
5. Encrypt the archive for the requester, transfer it through an explicitly approved channel, and
   expire the temporary archive under the operator's retention policy.
6. Validate the manifest and a sample restore before declaring the export complete.

If a complete owner-scoped export cannot be produced without an unreviewed query or unsafe byte
authorization, stop and keep the request open; do not substitute a whole-database dump.

## Owner deletion procedure

RC1 has no unattended destructive delete command. A deletion request therefore requires an
operator-controlled future procedure and explicit confirmation of scope. Do not execute these
steps against real data as part of P14 evidence.

1. Verify requester identity, owner identifier, requested scope and applicable retention/legal
   holds. Take a recoverable, access-controlled backup before destructive work.
2. Revoke sessions and stop new owner writes. Mark owner-visible resources logically deleted and
   allow the documented grace period before physical purge.
3. Purge owner-scoped projections, journal/sync state and personal metadata transactionally. Never
   delete a content-addressed Vault object while any authorized reference from another owner or a
   required backup generation remains.
4. Garbage-collect only unreferenced Vault bytes after checksum and reference reconciliation.
   Preserve immutable audit evidence only to the minimum required policy, without personal
   payloads or secrets.
5. Reconcile PostgreSQL and Vault, verify that owner authentication/bootstrap no longer exposes
   deleted resources, and record counts and stable identifiers rather than raw payloads.
6. Let backups expire under the declared retention schedule; do not mutate historical verified
   generations in place.

## Disaster recovery and incident handling

Use [BACKUP_RESTORE.md](BACKUP_RESTORE.md) for generation, isolated restore, checksum,
reconciliation, RPO/RTO and rollback. During an incident, preserve evidence without secrets,
disable affected credentials, keep production untouched until an isolated restore passes, and
notify affected owners according to the operator's jurisdiction-specific policy. RC1 deliberately
does not choose a legal jurisdiction, retention duration, notification channel or external backup
provider.

## Evidence and release limits

The local P14 secret scan, authorization-negative tests, backup/restore drill, Android backup
policy tests and release inventory are indexed in `docs/release/TEST_EVIDENCE.md`. This runbook is
operational guidance, not proof that a real owner export or deletion was executed, and it does not
authorize deployment, publication or destructive handling of user data.
