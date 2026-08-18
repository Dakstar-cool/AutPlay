# AutPlay backup and restore runbook

## Supported RC1 policy

RC1 proves a local, quiesced full-generation drill. The production backup backend, retention
budget and filesystem/NAS topology remain operator choices and are not silently selected here.

- Critical: PostgreSQL catalog, accounts, sync, jobs, audit and configuration schema references.
- Primary bytes: immutable Vault objects plus a manifest of SHA-256, byte size, backend and key.
- Derived: embeddings, search indexes, thumbnails and transcode cache may be omitted only when the
  corresponding rebuild procedure is tested and the required model artifact still exists.
- Secrets are never written to the manifest. Secret files/configuration require a separate
  access-controlled backup policy.
- Targets: database/profile RPO at most 24 hours, RTO at most 4 hours, isolated restore drill at
  least quarterly.

## Backup generation

1. Enter a documented quiesced window: stop API/worker writes and leave PostgreSQL/Vault readable.
2. Record application version, Alembic head, PostgreSQL/pgvector versions, generation UUID,
   timestamp and database WAL position.
3. Enumerate every expected committed Vault object into a canonical manifest. Fail if a listed
   object is absent, the path escapes the configured root, or size/SHA-256 differs.
4. Run `pg_dump --format=custom --no-owner` against the same generation.
5. Copy the manifest, database dump and configured original-blob replica to the backup target.
6. Hash the dump, manifest and every copied blob. A completed archive without restore is not a
   successful backup.
7. Resume writes only after the generation is complete or explicitly marked failed.

## Isolated restore

1. Provision a clean PostgreSQL 18.4/pgvector 0.8.6 instance with no production routing.
2. Verify manifest schema, application compatibility and artifact hashes before restore.
3. Restore with `pg_restore --exit-on-error --no-owner`.
4. Restore/copy Vault objects only under the configured root; reject links and path traversal.
5. Verify Alembic head, row counts and every manifest byte size/SHA-256 against database metadata.
6. Run Vault reconciliation. Missing/corrupt replicas become unavailable/quarantined; logical
   Tracks and user library intent are not deleted.
7. Rebuild declared derived indexes, run auth/new-client bootstrap plus stream/sync smoke, and only
   then consider switching production routing.

## Reproducible local drill

```powershell
uv run --project server --frozen python scripts/p14_drill.py
```

The command uses two independently named, loopback-only disposable Compose projects, a generated
temporary Vault root and a 100,000-record catalog. Bytes are published by the production
`FilesystemVaultStorage`; the restored root is checked by `VaultReconciliationService` in APPLY
mode. A healthy object remains available, then injected corruption is moved to recoverable
quarantine while both object and replica database states become `QUARANTINED`. The command writes
`docs/implementation/evidence/P14_BACKUP_RESTORE_2026-08-17.json`, removes scoped containers,
networks and volumes, and never accepts a production DSN.

## Failure and rollback

- Dump/manifest/hash failure: mark generation failed; do not prune an older verified generation.
- Restore mismatch: keep production untouched, quarantine the restore, preserve logs without paths
  or secrets, and investigate the exact object/database difference.
- Migration failure: stop rollout; use the compatible prior app only when its schema contract is
  valid, otherwise restore the last verified pre-migration generation. Never use destructive
  Alembic or Room fallback.
- Lost Vault replica: restore another verified replica or reacquire through an authorized source;
  never substitute a similar Recording automatically.
