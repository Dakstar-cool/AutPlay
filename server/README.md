# AutPlay CPU server foundation

This directory contains the P01 package boundaries, P02 PostgreSQL persistence foundation, and P03 CPU-only runtime foundation. It includes typed SQLAlchemy 2 row mappings, Alembic revisions `0001` through `0010`, a FastAPI application factory, validated runtime settings, structured redacted logs/metrics, owner/device session services, and a PostgreSQL lease worker.

The physical contract remains `docs/design/AutPlay_PostgreSQL_Schema_v1.sql`, and P03 adds no migration: the single head is still `0010_indexes_privileges`. Runtime repositories reuse the existing `account.user_session` and `jobs.*` tables. Refresh bearer values are opaque and only their SHA-256 digests are persisted.

Installed console entrypoints are:

- `autplay-api` for the API process; `--check-config` validates settings without listening;
- `autplay-worker-cpu` for the CPU worker; `--check-readiness` verifies PostgreSQL and the exact migration head, while P03 registers no feature handlers by default;
- `autplay-admin bootstrap-owner` for one-time, local owner/device/session bootstrap.

The API exposes `/health/live`, `/health/ready`, `/metrics`, and the P03 device-session routes `/api/v1/auth/refresh`, `/api/v1/auth/logout`, `/api/v1/auth/logout-all`, and `/api/v1/devices/{device_id}/revoke`. Password verification uses an explicit Argon2id primitive, but password login is disabled because schema v1 has no approved credential-persistence contract. There is no public self-registration/bootstrap HTTP route, password-login route, email/OAuth provider, sync endpoint, matcher, Vault ingest, non-auth product feature endpoint, or GPU dependency in this phase.

Use the canonical repository commands in the root [`README.md`](../README.md).
