# Disposable PostgreSQL and P03 CPU runtime

The base Compose file runs exactly one PostgreSQL 18 service with pgvector and publishes no host port. Its named volume is scoped by the Compose project and is intentionally disposable. `compose.test.yaml` is used only by the canonical check scripts: it assigns a random loopback-only host port so real migration, auth, and job tests can connect, then the scripts remove the exact project container, network, and volume and verify that none remain.

`compose.runtime.yaml` adds the `runtime` profile: one-shot Alembic migration followed by separate API and CPU-worker processes built from the same non-root CPU image whose base is digest-pinned. Both process healthchecks require PostgreSQL and the exact Alembic head but never GPU or Internet access. The API host port is loopback-only. The worker uses the PostgreSQL queue and has no P03 feature handlers by default. The root allowlist `.dockerignore` limits the build context to the server lock, package source, and migration inputs so workspace secrets and caches are never uploaded to the builder.

Before parsing the runtime overlay, set `AUTPLAY_RUNTIME_AUTH_SECRET_FILE` to a local file outside the repository containing at least 32 random characters. Compose mounts it read-only; do not place a real credential in YAML, source control, shell history, or logs.

```text
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime up --build --wait
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime down --volumes
```

`autplay_dev_only` is a fixed disposable development credential, not a deployable secret. These files are not production manifests and must never be pointed at real/user data. Production database roles, TLS/domain topology, secret delivery, backup/restore, and public networking require their owning later phase and explicit deployment approval.
