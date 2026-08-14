# AutPlay server persistence foundation

This directory contains the P01 package boundaries plus the P02 PostgreSQL persistence foundation: typed SQLAlchemy 2 row mappings, Alembic revisions `0001` through `0010`, canonical identity-evidence validation, and bounded atomic persistence commands required by P02 acceptance.

The physical contract remains `docs/design/AutPlay_PostgreSQL_Schema_v1.sql`. Migrations are reviewed, self-contained, and must support clean upgrade, downgrade to base on an empty development database, and upgrade again. Production roles, API endpoints, matcher behavior, workers, and product/domain behavior are intentionally absent.

Use the canonical repository commands in the root [`README.md`](../README.md).
