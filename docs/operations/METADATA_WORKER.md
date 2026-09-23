# Production metadata worker

The opt-in `metadata` profile in `deploy/compose/compose.metadata-worker.yaml` runs one
`music.metadata.enrich` consumer. It has no published ports. Its Vault mount is read-only;
the deletion and training-consent ledgers remain writable because both restore guards
open SQLite write transactions at startup. The worker may use its own reviewed image
tag, so activating it does not replace images of the running API, stream or other
workers. Preserve the image ID and source-snapshot identity in deployment evidence.

## Before activation

1. Preserve the PostgreSQL and Vault backup generation and record the release image
   digest, Alembic head and rendered Compose configuration hash. Drain legacy
   unregistered byte workers before budget activation. Stop any earlier metadata
   containers and confirm they have no retained children.
2. Verify that `account.resource_quota_policy` has reviewed, non-null global limits,
   ceilings and budget evidence. Verify `account.internal_io_policy` has a reviewed
   active limit, ceiling, matching server identity and `workload_version = 3` from the
   same target environment. The operator must have explicitly applied both reports
   through the documented local commands. Do not substitute fixture values or change
   policy rows directly.
3. Load the reviewed `AUTPLAY_METADATA_WORKER_IMAGE` containing the metadata bootstrap
   selection and readiness probe. Layer the new Compose file **after**
   `compose.release.yaml`. Supply the same file-backed database and ledger secrets
   and ledger key IDs used by the running production stack. Include every site-specific Compose
   override before the new metadata overlay so the rendered project matches the
   active deployment. The metadata service is started only after the core runtime
   is healthy; its own readiness probe checks PostgreSQL and the schema. Targeted
   activation does not start or rerun the existing one-shot migration, Vault
   initializer or other runtime services. Do not copy secret values into Compose
   or logs.

From `deploy/compose`, validate the rendered model with the existing production inputs:

```bash
docker compose -p autplay-production \
  -f compose.yaml -f compose.runtime.yaml -f compose.admin-local.yaml \
  -f compose.public-edge.yaml -f compose.release.yaml \
  -f compose.metadata-worker.yaml \
  --profile runtime --profile metadata config --quiet
```

Inspect the rendered metadata service for `cgroup: private`, root only during bootstrap,
`cap_drop: ALL`, the five explicit bootstrap capabilities, `no-new-privileges`,
`apparmor=unconfined`, a read-only root filesystem, bounded memory/CPU/PIDs, the
read-only Vault mount, and no `ports`. The private cgroup2 mount requires this one
AppArmor exception on the validated Ubuntu Docker host. The bootstrap delegates a
subtree to `autplay`, then drops to that UID/GID with zero effective capabilities.

## Start and observe

```bash
docker compose -p autplay-production \
  -f compose.yaml -f compose.runtime.yaml -f compose.admin-local.yaml \
  -f compose.public-edge.yaml -f compose.release.yaml \
  -f compose.metadata-worker.yaml \
  --profile runtime --profile metadata up -d --no-build --wait metadata-worker
```

Check the service health, PID 1 `Uid`, `Gid`, `CapEff=0`, and the private cgroup2 mount.
The readiness probe checks migration compatibility and the applied metadata-inclusive
internal budget without claiming work. An `internal_io_budget_unconfigured` or
`metadata_budget_unconfigured` result is a stop condition, not a retry instruction.

Query only aggregate states before and after each bounded observation interval:

```sql
SELECT state, count(*) FROM library.track_metadata GROUP BY state ORDER BY state;
SELECT state, count(*) FROM jobs.job
 WHERE job_type = 'music.metadata.enrich' GROUP BY state ORDER BY state;
SELECT count(*) FROM library.metadata_execution WHERE closed_at IS NULL;
```

The worker sweeps missing metadata in batches of at most 100 and handles one job at a
time. Compare job completions with `READY`, `REVIEW`, `NOT_FOUND`, `RETRY` and `FAILED`
movement and inspect only sanitized error-code aggregates. Previously failed jobs
need their own diagnosis and authorized requeue path; starting this worker does not
silently requeue them. Stop the service with `docker compose ... stop metadata-worker`
if health fails or retained executions do not close, and preserve the SQL/child evidence
before another attempt.
