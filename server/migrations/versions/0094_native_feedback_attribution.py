"""Dormant private feedback associations; provisional successor to installed 0093."""

from alembic import op

from autplay.adapters.postgresql.native_feedback_attribution_schema import (
    install_native_feedback,
    remove_native_feedback,
)

revision = "0094_native_feedback_attribution"
down_revision = "0093_native_causal_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    install_native_feedback(op.get_bind())


def downgrade() -> None:
    remove_native_feedback(op.get_bind())
