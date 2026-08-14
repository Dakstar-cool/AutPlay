"""Create account and catalog relations.

Revision ID: 0002_account_catalog
Revises: 0001_extensions_schemas
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_tables, execute_reference

revision: str = "0002_account_catalog"
down_revision: str | None = "0001_extensions_schemas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "user_account",
    "device",
    "user_session",
    "artist",
    "artist_credit",
    "artist_credit_name",
    "work",
    "recording",
    "release_group",
    "release",
    "medium",
    "release_track",
)
QUALIFIED_TABLES = (
    "account.user_account",
    "account.device",
    "account.user_session",
    "catalog.artist",
    "catalog.artist_credit",
    "catalog.artist_credit_name",
    "catalog.work",
    "catalog.recording",
    "catalog.release_group",
    "catalog.release",
    "catalog.medium",
    "catalog.release_track",
)


def upgrade() -> None:
    execute_reference("table", TABLES)


def downgrade() -> None:
    drop_tables(tuple(reversed(QUALIFIED_TABLES)))
