"""Private immutable P11 served authority; no production serving cutover."""

from alembic import op

from autplay.adapters.postgresql.native_served_authority_schema import (
    NATIVE_SERVED_AUTHORITY_DROP_SQL,
    NATIVE_SERVED_AUTHORITY_INSTALL_SQL,
)

revision = "0092_native_served_authority"
down_revision = "0091_ingest_linux_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(NATIVE_SERVED_AUTHORITY_INSTALL_SQL)


def downgrade() -> None:
    op.execute(NATIVE_SERVED_AUTHORITY_DROP_SQL)
