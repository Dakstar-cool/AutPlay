# Contained metadata worker: 2026-09-18

This closes the metadata-worker integration slice of the Admin/account resource
goal. The full seven-criterion goal remains incomplete. Production migration,
delegation, real provider credentials and measured budget activation were not run.
The next priority is the user-facing TXT/manual account recovery scenario.

## Implemented behavior

Migration `0050_metadata_execution` adds `library.metadata_execution` and
`library.metadata_provider_gate`, plus internal-policy workload version. The current
typed inventory is 157 tables, 1789 columns and 150 explicit indexes; fingerprint:
`6f5cb00c013b7eee1941466d175ac40cd8892ed399ca3935bb5b1a52a6c47524`.

Unclosed metadata receipts join WORK, finalized cleanup and maintenance in one
durable internal capacity count. Both Python and SQL require a reviewed v2 report
including METADATA_ENRICHMENT before new metadata admission. Old v1 evidence is
valid only for its original eight paths. Python and SQL reject v2-to-v1 evidence
downgrade; lowering capacity preserves already admitted charges and valid renewal.

Each receipt pins the job/attempt, account authority generation, metadata generation,
active library ref and optional canonical recording/variant/object/key/hash/size.
The admission/owner/job/ref lock order precedes execution validation. Account revoke,
library removal and REFRESH/SELECT generation replacement stop old authority.
Ordinary EDIT preserves generation and the existing locked-field merge semantics.
Final metadata/history/sync flush is followed by a SQL receipt check without renewal;
expiry at that last boundary rolls back publication. Expiry never resurrects a grant.

The actual metadata entrypoint uses the retained child coordinator, independent
control pool, signal observer and bounded shutdown, including `--once`. It reads
immutable CAS directly after verification and never makes a full temporary audio
copy. ffprobe, fpcalc, HTTP, and pipe-only artwork decoding stay in that tree.
Child responses and stdin/stdout payloads are bounded. Media failures retain the
nonretryable `metadata_media_unreadable` classification. The adjacent metadata
service's three owner lookups now pass the UUID expected by MusicLibraryService.

Provider requests occupy a durable singleton through their completion or acknowledged
tree exit. Each completed request starts a further 1.1-second cooldown. Short control
transactions do not span HTTP. A lost closure reply retains the local callback and
capacity; an expired in-flight request cannot be released by timeout alone.

Application DB credentials never enter the child. Only an explicitly configured
metadata proxy is added to the minimal child environment. The optional AcoustID
key is transported in the private pipe/POST body, never process arguments or URLs.
WorkerSettings now carries these settings; the key keeps the file-only secret-loader
policy. Existing unregistered workers must still be drained before rollout.

## Verified evidence

- Linux: **162 distinct passing cases**, no skips, against PostgreSQL 18.4 /
  pgvector 0.8.6 and the pinned `autplay-ingest:proof-20260918` image. The cgroup
  runner used UID/GID 10001 and CapEff=0 after setup-only delegation. The image
  supplies ffmpeg/ffprobe 8.1.2 and fpcalc 1.6.1.
- Eleven new PostgreSQL cases passed before the first actual-media fixture failure.
  Fixture corrections supplied a real identity decision and canonical publication
  through MusicLibraryService. No constraints were disabled or assertions weakened.
  Those eleven passing cases were not rerun. The remaining affected batch passed
  **149 cases in 49.98 seconds**.
- The final **three cases passed in 20.45 seconds**: the actual worker test additionally
  exercised fpcalc and a private AcoustID POST with a dummy key; live Alembic drift
  and effective PUBLIC ACL checks passed. The worker case overlaps the 149-case batch.
- Windows: **two cases passed in 13.58 seconds**, exercising actual Job Objects,
  durable provider pacing, lost exit acknowledgement and stop during a request.
- Ruff/format passed on 35 affected Python files; strict mypy passed on 27 source
  files for both Windows and Linux targets. `git diff --check` passed.
- Bounded independent read-only review is closed. Findings fixed: first-start
  autoflush, Principal/UUID call sites, workload-coverage downgrade and media error
  classification. No implementation was delegated.

The actual worker test uses real contained media executables and a deterministic
network-free provider transport. It is not live MusicBrainz/AcoustID interoperability,
physical target-device acceptance or a deployment measurement report.

## Reproduction scope

Run the following affected files with locked dependencies. Keep PostgreSQL paths
contiguous (the previously documented pytest 9.1 collection issue still applies):

```text
tests/postgresql/test_metadata_execution.py
tests/postgresql/test_track_metadata.py
tests/postgresql/test_internal_io.py
tests/postgresql/test_metadata.py
tests/postgresql/test_migrations.py::test_clean_upgrade_downgrade_and_upgrade_again
tests/postgresql/test_migrations.py::test_every_revision_has_one_linear_predecessor
tests/postgresql/test_migration_close_gates.py::test_live_alembic_metadata_check_has_no_upgrade_operations
tests/postgresql/test_migration_close_gates.py::test_public_has_no_reference_object_access
tests/runtime/test_ingest_io.py
tests/runtime/test_settings.py
tests/test_metadata_child.py
tests/test_media_tools.py
tests/test_public_track_metadata.py
tests/test_track_metadata.py
```

Use the existing disposable PostgreSQL container and the Linux delegation recipe
from the ingest/containment records. No test session remains running at this point.
Git remains dirty/uncommitted; adjacent changes were preserved. No commit or push.

Repeated-error handling: long SQL literals remained over E501 after formatting.
The official [Ruff formatter](https://docs.astral.sh/ruff/formatter/) and
[E501 documentation](https://docs.astral.sh/ruff/rules/line-too-long/) describe the
best-effort formatter. Considered adjacent literal splitting, multiline SQL,
changing configured width and local suppression. Applied explicit line splitting;
kept the project's lint limit and checks. Earlier path lookup failures were resolved
by `rg --files` discovery rather than guessed names.
