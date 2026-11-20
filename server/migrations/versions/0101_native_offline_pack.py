"""Private v2 offline-pack completeness and retained binding; no rollout."""

from alembic import op
from autplay.adapters.postgresql.native_offline_pack_schema import install, remove

revision = "0101_native_offline_pack"
down_revision = "0100_native_export_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    install(op.get_bind())


def downgrade() -> None:
    remove(op.get_bind())
