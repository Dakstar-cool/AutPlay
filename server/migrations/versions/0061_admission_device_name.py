"""Preserve the administrator's name separately from the signed device request."""

import sqlalchemy as sa
from alembic import op

revision = "0061_admission_device_name"
down_revision = "0060_local_bridge_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_admission", sa.Column("approved_device_name", sa.Text()), schema="account"
    )
    op.create_check_constraint(
        "device_admission_approved_name_check",
        "device_admission",
        "approved_device_name IS NULL OR length(approved_device_name) BETWEEN 1 AND 120",
        schema="account",
    )


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM account.device_admission WHERE approved_device_name IS NOT NULL) THEN
  RAISE EXCEPTION 'refusing device-name downgrade with administrator names' USING ERRCODE='55000';
 END IF;
END $$;
""")
    op.drop_constraint("device_admission_approved_name_check", "device_admission", schema="account")
    op.drop_column("device_admission", "approved_device_name", schema="account")
