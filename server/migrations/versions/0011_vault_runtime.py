"""Create durable P06 Vault upload sessions and chunk receipts.

Revision ID: 0011_vault_runtime
Revises: 0010_indexes_privileges
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_indexes, drop_tables, drop_triggers, execute_reference

revision: str = "0011_vault_runtime"
down_revision: str | None = "0010_indexes_privileges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("upload_session", "upload_chunk")
INDEXES = (
    "ix_upload_session_owner_state_time",
    "ix_upload_session_state_expiry",
    "ix_upload_session_computed_sha256",
    "ix_upload_session_job",
)
TRIGGERS = ("tr_upload_session_row_version",)
QUALIFIED_TABLES = ("vault.upload_chunk", "vault.upload_session")
PUBLIC_REVOKES = (
    "all_tables_public",
    "all_sequences_public",
    "app_private_functions_public",
)


def upgrade() -> None:
    execute_reference("table", TABLES)
    execute_reference("index", INDEXES)
    execute_reference("trigger", TRIGGERS)
    execute_reference("revoke", PUBLIC_REVOKES)


def downgrade() -> None:
    drop_triggers(TRIGGERS)
    drop_indexes(tuple(reversed(INDEXES)))
    drop_tables(QUALIFIED_TABLES)
