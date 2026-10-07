"""Add opt-in same-server exact public ID lookup without backfilling accounts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063_social_public_id"
down_revision = "0062_cpu_worker_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_id_registration",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("public_id", sa.Text(collation="C"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["account.user_account.user_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("public_id", name="uq_social_public_id"),
        sa.CheckConstraint(
            "public_id COLLATE \"C\" ~ '^[a-z0-9_]{3,24}$'", name="ck_social_public_id_format"
        ),
        schema="social",
    )
    op.execute("REVOKE ALL ON social.public_id_registration FROM PUBLIC")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM social.public_id_registration)
 OR EXISTS (SELECT 1 FROM social.operation_receipt WHERE action='REGISTER_PUBLIC_ID') THEN
  RAISE EXCEPTION 'refusing public ID downgrade with durable registrations or receipts'
   USING ERRCODE='55000';
 END IF;
END $$;
""")
    op.drop_table("public_id_registration", schema="social")
