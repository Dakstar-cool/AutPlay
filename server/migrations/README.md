# PostgreSQL migrations

This directory contains the linear P02 clean-install chain `0001` through `0010`.
Each revision reads only the immutable vendored `reference_v1.sql` asset, whose
SHA-256 is checked before any reference statement runs. Revisions never import a
later revision or the mutable design copy of the DDL.

Run Alembic from the repository root with a disposable database URL:

```text
AUTPLAY_DATABASE_URL=postgresql+psycopg://... uv run --project server --frozen alembic -c server/alembic.ini upgrade head
AUTPLAY_DATABASE_URL=postgresql+psycopg://... uv run --project server --frozen alembic -c server/alembic.ini downgrade base
```

New revisions must be generated only after reviewing autogenerate output against
the physical reference contract. Alembic does not fully compare PostgreSQL
functions, triggers, extensions, CHECK constraints, privileges, or all expression
indexes, so the exact catalog inventory tests remain mandatory. Downgrades never
use `CASCADE`; a data-bearing or destructive production rollback requires its own
expand/backfill/verify plan and explicit operator approval.
