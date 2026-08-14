"""Create import workflow and immutable identity decision history.

Revision ID: 0007_importing_identity_history
Revises: 0006_vault
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_constraints, drop_tables, execute_reference

revision: str = "0007_importing_identity_history"
down_revision: str | None = "0006_vault"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "import_job",
    "import_entry",
    "match_decision",
    "match_candidate_evidence",
)
QUALIFIED_TABLES = (
    "importing.import_job",
    "importing.import_entry",
    "identity.match_decision",
    "identity.match_candidate_evidence",
)
LATE_CONSTRAINTS = (
    "fk_match_decision_reviewed_evidence",
    "fk_user_track_ref_current_match_decision",
    "fk_import_entry_current_match_decision",
)
DROP_CONSTRAINTS = (
    ("importing.import_entry", "fk_import_entry_current_match_decision"),
    ("library.user_track_ref", "fk_user_track_ref_current_match_decision"),
    ("identity.match_decision", "fk_match_decision_reviewed_evidence"),
)


def upgrade() -> None:
    execute_reference("table", TABLES)
    execute_reference("alter_constraint", LATE_CONSTRAINTS)


def downgrade() -> None:
    drop_constraints(DROP_CONSTRAINTS)
    drop_tables(tuple(reversed(QUALIFIED_TABLES)))
