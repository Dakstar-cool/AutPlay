"""Retain exact Linux M11 owner and cgroup identity before provider spawn."""

from pathlib import Path

from alembic import op

revision = "0108_m11_native_owner"
down_revision = "0107_m11_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
    LOCK TABLE discovery.m11_native_owner IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM discovery.m11_native_owner) THEN
        RAISE EXCEPTION 'm11_native_owner_retained' USING ERRCODE='55000';
      END IF;
    END $$;
    DROP TABLE discovery.m11_native_owner;
    DROP FUNCTION app_private.guard_m11_native_owner();
    """)
