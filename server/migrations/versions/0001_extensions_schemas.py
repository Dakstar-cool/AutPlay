"""Create required extensions and module schemas.

Revision ID: 0001_extensions_schemas
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_extensions, drop_schemas, execute_reference

revision: str = "0001_extensions_schemas"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("pg_trgm", "vector")
SCHEMAS = (
    "account",
    "catalog",
    "identity",
    "library",
    "playlist",
    "vault",
    "importing",
    "sync",
    "jobs",
    "ml",
    "audit",
    "app_private",
)


def upgrade() -> None:
    execute_reference("extension", EXTENSIONS)
    execute_reference("schema", SCHEMAS)
    execute_reference("revoke", ("app_private_schema_public",))


def downgrade() -> None:
    drop_schemas(tuple(reversed(SCHEMAS)))
    drop_extensions(tuple(reversed(EXTENSIONS)))
