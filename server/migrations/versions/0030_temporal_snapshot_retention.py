"""Make retained temporal evidence compatible with baseline cleanup.

Revision ID: 0030_temporal_snapshot_retention
Revises: 0029_sona_shadow_binding
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030_temporal_snapshot_retention"
down_revision: str | None = "0029_sona_shadow_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow only FK nulling after an input snapshot has expired."""

    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_private.protect_recommendation_temporal_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' THEN
            IF OLD.recommendation_input_snapshot_id IS NOT NULL
              AND NEW.recommendation_input_snapshot_id IS NULL
              AND to_jsonb(NEW) - 'recommendation_input_snapshot_id'
                IS NOT DISTINCT FROM
                  to_jsonb(OLD) - 'recommendation_input_snapshot_id'
              AND NOT EXISTS(
                SELECT 1
                FROM ml.recommendation_input_snapshot snapshot
                WHERE snapshot.user_id=OLD.user_id
                  AND snapshot.recommendation_input_snapshot_id=
                    OLD.recommendation_input_snapshot_id
                  AND snapshot.retained_until>now()
              ) THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'recommendation temporal snapshots are immutable';
          END IF;
          IF OLD.retained_until>now() THEN
            RAISE EXCEPTION 'recommendation temporal snapshot retention is active';
          END IF;
          RETURN OLD;
        END; $$;

        REVOKE ALL ON FUNCTION app_private.protect_recommendation_temporal_snapshot()
          FROM PUBLIC;
        """
    )


def downgrade() -> None:
    """Restore the original strict update policy without deleting evidence."""

    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_private.protect_recommendation_temporal_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' THEN
            RAISE EXCEPTION 'recommendation temporal snapshots are immutable';
          END IF;
          IF OLD.retained_until>now() THEN
            RAISE EXCEPTION 'recommendation temporal snapshot retention is active';
          END IF;
          RETURN OLD;
        END; $$;

        REVOKE ALL ON FUNCTION app_private.protect_recommendation_temporal_snapshot()
          FROM PUBLIC;
        """
    )
