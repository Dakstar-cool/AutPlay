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

## Accepted PA3 operator policy

The operator accepted the following personal-server policy on 2026-09-01:

- the independent target is the external USB disk recorded by the non-secret recovery receipts;
- attach the target and create a hash-verified encrypted generation at least once every 24 hours,
  then disconnect it after successful verification;
- retain the seven latest successful daily generations and four quarterly restore-tested
  generations;
- never prune the last two verified generations, and never let a failed generation replace a
  verified one;
- perform an isolated restore drill at least quarterly and continue to target RPO <= 24 hours and
  RTO <= 4 hours.

The current accepted records are `E:\AutPlay-Recovery\retention-policy.json`, the named generation
receipt, and `E:\AutPlay-Recovery\recovery-identity\recovery-identity-receipt.json`. Drive letters
are observational only: before every backup, the operator must verify that the mounted target is
the intended external USB device. The passphrase remains outside the archive and receipts.

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
temporary Vault root and a 100,000-record catalog. It verifies production Vault publication,
restore, reconciliation and corruption quarantine, then removes scoped containers, networks and
volumes. Its detailed JSON output is kept as ignored local operator release evidence and is not
committed.

## Authorized R1B source preflight

An isolated restore at Alembic head `0026_s1d_guest_room_access` can be checked for possible R1B
quality-source reconstruction without exporting owner or recording identifiers. Do this only after
the owner has explicitly authorized use of the restored data. The authorization flag records that
operator decision; it does not grant authority by itself.

Point the validated worker configuration (`AUTPLAY_DATABASE_URL`, or its approved secret-file
equivalent) at the isolated local restore, keep production routing disabled, and run:

```powershell
uv run --project server --frozen python -m autplay.entrypoints.sona_source_preflight `
  --generation-summary <absolute-generation-summary.json> `
  --encrypted-archive <absolute-generation.tar.age> `
  --output <new-absolute-preflight.json> `
  --authorized-owner-data-use
```

The command verifies the declared encrypted archive size and SHA-256, then executes one
aggregate-only query inside a read-only, repeatable-read transaction. The immutable output contains
counts, schema/archive identities, split feasibility, and blocker codes only. It always records
`quality_eligible=false`. For exact schema `0026`, it also records
`RECONSTRUCTED_FROM_0026_SYNC_TRUTH_V1`,
`original_persisted_temporal_snapshots_available=false`, and
`server_profile_binding_available=false`. Consequently the current preflight is deliberately
blocked with `ORIGINAL_TEMPORAL_SNAPSHOTS_UNAVAILABLE` and
`SERVER_PROFILE_BINDING_UNAVAILABLE`, even when every aggregate count is sufficient. No operator
flag silently converts those provenance gaps into approval. A blocked result is still written for
review and exits non-zero. The aggregate sufficiency checks additionally require exactly one active
provenance-approved embedding model and no more than the immutable 4,096-example materializer
bound. The subsequent deterministic planner excludes both seven-day embargo gaps and also rejects
actual label timestamps that touch or cross the next split boundary. Existing output files are
never overwritten.

Only after aggregate sufficiency has been reviewed and a separate approved record resolves both
known provenance blockers may extraction proceed. The implementation must remain in the same
exact-`0026`, read-only, repeatable-read trust boundary. Its first pass recomputes the RFC 8785
hashes of each retained P11 request and baseline snapshot, selects only the first mature
causally-attributed outcome, and pseudonymizes owner UUIDs in memory before a split plan can be
persisted. That plan is explicitly tagged `P11_CANONICAL_REQUEST_SHA256_V1`; it is planning
evidence, not final Sona dataset membership. After the tokenizer and Sona request document exist,
the materialization pass must derive `SONA_INFERENCE_REQUEST_SHA256_V1` identities and reconcile
the same split membership before source/dataset approval. Never copy raw owner UUIDs into an
intermediate plan or relabel a P11 request-set digest as a Sona request-set digest.

The reconciled Sona plan is then the sole request-membership input to the source-manifest builder.
It atomically publishes `source-manifest.json`, `catalog-manifest.json`, and
`source-rekey-plan.json` into a new directory. The first two retain the exact frozen manifest
schemas consumed by the signed quality verifier; the third is separate reviewer evidence binding
the P11 parent plan, both request-set digests, and a canonical digest of every one-to-one re-key
pair. These artifacts contain aggregate counts and digests only. Raw owner UUIDs, owner lineage
tokens, recording IDs, embeddings, and individual request hashes must not be serialized there.

Schema `0026` predates the persisted R1A normalized-event and temporal-snapshot tables. The
`server_profile_id` carried by normalized evidence is Android-local by design and is not a
PostgreSQL column, so the backup cannot recover its original value either. A
historical-case materializer may therefore reconstruct normalized temporal evidence from the exact
0026 sync/inbox, listening, preference, feedback and P11 snapshot truth, but it cannot represent
that reconstruction as an original persisted R1A temporal snapshot. The reconstruction identity is
content-addressed and explicitly tagged `RECONSTRUCTED_FROM_0026_SYNC_TRUTH_V1` with
`persisted_original_temporal_snapshot=false`; it remains reviewer evidence outside the frozen
source/dataset approval schemas. Do not sign a quality source or dataset approval from that path
until an explicit review accepts this reconstructed provenance boundary, or a source containing the
original persisted R1A temporal snapshots is supplied.

The same materializer computes a candidate-complete, recording-ID-ordered
`SONA_P11_TEACHER_CALIBRATION_INPUT_V1` artifact from the exact frozen P11 candidate pool. It fails
closed if that pool does not equal the mandatory-filtered Sona candidate set. This artifact binds
raw finite P11 heuristic scores and both request identities, but explicitly records
`calibration_fit_bound=false`, `quality_eligible=false`, and
`TEACHER_CALIBRATION_PARAMETERS_NOT_BOUND`. The current frozen teacher manifest names a temperature
calibration policy but does not bind fitted temperatures or calibration-dataset ancestry. Do not
fabricate teacher probabilities or treat the raw-score artifact as a calibrated teacher; a reviewed
schema/approval decision is required before those parameters can enter quality evidence.

## Failure and rollback

- Dump/manifest/hash failure: mark generation failed; do not prune an older verified generation.
- Restore mismatch: keep production untouched, quarantine the restore, preserve logs without paths
  or secrets, and investigate the exact object/database difference.
- Migration failure: stop rollout; use the compatible prior app only when its schema contract is
  valid, otherwise restore the last verified pre-migration generation. Never use destructive
  Alembic or Room fallback.
- Lost Vault replica: restore another verified replica or reacquire through an authorized source;
  never substitute a similar Recording automatically.
