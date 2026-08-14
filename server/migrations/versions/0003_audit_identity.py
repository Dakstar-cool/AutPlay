"""Create audit and identity-base relations and empty policy history.

Revision ID: 0003_audit_identity
Revises: 0002_account_catalog
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_tables, execute_reference

revision: str = "0003_audit_identity"
down_revision: str | None = "0002_account_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "catalog_change_set",
    "catalog_change_item",
    "audit_event",
    "source_provider",
    "recording_identifier",
    "external_reference",
    "source_observation",
    "matcher_release",
    "calibrator_release",
    "threshold_set",
    "match_policy_activation",
    "recording_redirect",
)
QUALIFIED_TABLES = (
    "audit.catalog_change_set",
    "audit.catalog_change_item",
    "audit.audit_event",
    "identity.source_provider",
    "identity.recording_identifier",
    "identity.external_reference",
    "identity.source_observation",
    "identity.matcher_release",
    "identity.calibrator_release",
    "identity.threshold_set",
    "identity.match_policy_activation",
    "identity.recording_redirect",
)


def upgrade() -> None:
    execute_reference("table", TABLES)


def downgrade() -> None:
    drop_tables(tuple(reversed(QUALIFIED_TABLES)))
