"""Managed fixed-inventory DATASET descendants; no training activation."""

from alembic import op
from autplay.adapters.postgresql.native_dataset_export_schema import install, remove

revision = "0102_native_dataset_export"
down_revision = "0101_native_offline_pack"
branch_labels = None
depends_on = None


def upgrade() -> None:
    install(op.get_bind())


def downgrade() -> None:
    remove(op.get_bind())
