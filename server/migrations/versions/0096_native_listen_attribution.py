"""Dormant original-byte causal listening; no producer or public role grant."""

from alembic import op

from autplay.adapters.postgresql.native_listening_attribution_schema import (
    install_native_listening,
    remove_native_listening,
)

revision = "0096_native_listen_attribution"
down_revision = "0095_native_capture_closure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    install_native_listening(op.get_bind())


def downgrade() -> None:
    remove_native_listening(op.get_bind())
