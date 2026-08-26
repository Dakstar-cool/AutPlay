"""Add S2 private-by-default profile statistics sharing policy.

Revision ID: 0023_s2_profile_stats
Revises: 0022_s1c_social_runtime
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023_s2_profile_stats"
down_revision: str | None = "0022_s1c_social_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE social.profile_statistics_settings (
          user_id uuid PRIMARY KEY REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          friends_can_view_statistics boolean NOT NULL DEFAULT false,
          revision bigint NOT NULL DEFAULT 0
            CONSTRAINT ck_social_profile_statistics_settings_revision CHECK(revision >= 0),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        REVOKE ALL ON social.profile_statistics_settings FROM PUBLIC;

        CREATE OR REPLACE FUNCTION social.retire_account_state() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE affected_user_id uuid;
        BEGIN
          affected_user_id := COALESCE(OLD.user_id, NEW.user_id);
          IF TG_OP = 'DELETE' THEN
            DELETE FROM social.operation_receipt WHERE actor_user_id=affected_user_id;
            DELETE FROM social.friend_room_invitation
             WHERE host_user_id=affected_user_id OR target_user_id=affected_user_id;
            DELETE FROM social.presence_heartbeat WHERE user_id=affected_user_id;
            DELETE FROM social.presence_settings WHERE user_id=affected_user_id;
            DELETE FROM social.profile_statistics_settings WHERE user_id=affected_user_id;
            DELETE FROM social.user_block
             WHERE blocker_user_id=affected_user_id OR blocked_user_id=affected_user_id;
            DELETE FROM social.friendship
             WHERE lower_user_id=affected_user_id OR higher_user_id=affected_user_id;
            DELETE FROM social.friend_request
             WHERE requester_user_id=affected_user_id OR target_user_id=affected_user_id;
            RETURN OLD;
          END IF;
          IF NEW.status <> 'ACTIVE' OR NEW.deleted_at IS NOT NULL THEN
            UPDATE social.friend_request
               SET state='CANCELLED',terminal_at=statement_timestamp()
             WHERE state='PENDING'
               AND (requester_user_id=affected_user_id OR target_user_id=affected_user_id);
            UPDATE social.friend_room_invitation
               SET state='ROOM_CHANGED',terminal_at=statement_timestamp(),
                   terminal_reason='ACCOUNT_UNAVAILABLE'
             WHERE state='PENDING'
               AND (host_user_id=affected_user_id OR target_user_id=affected_user_id);
            DELETE FROM social.presence_heartbeat WHERE user_id=affected_user_id;
            DELETE FROM social.presence_settings WHERE user_id=affected_user_id;
            DELETE FROM social.profile_statistics_settings WHERE user_id=affected_user_id;
            DELETE FROM social.user_block
             WHERE blocker_user_id=affected_user_id OR blocked_user_id=affected_user_id;
            DELETE FROM social.friendship
             WHERE lower_user_id=affected_user_id OR higher_user_id=affected_user_id;
          END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM social.profile_statistics_settings) THEN
            RAISE EXCEPTION 'refusing S2 downgrade with profile statistics policy';
          END IF;
        END $$;

        CREATE OR REPLACE FUNCTION social.retire_account_state() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE affected_user_id uuid;
        BEGIN
          affected_user_id := COALESCE(OLD.user_id, NEW.user_id);
          IF TG_OP = 'DELETE' THEN
            DELETE FROM social.operation_receipt WHERE actor_user_id=affected_user_id;
            DELETE FROM social.friend_room_invitation
             WHERE host_user_id=affected_user_id OR target_user_id=affected_user_id;
            DELETE FROM social.presence_heartbeat WHERE user_id=affected_user_id;
            DELETE FROM social.presence_settings WHERE user_id=affected_user_id;
            DELETE FROM social.user_block
             WHERE blocker_user_id=affected_user_id OR blocked_user_id=affected_user_id;
            DELETE FROM social.friendship
             WHERE lower_user_id=affected_user_id OR higher_user_id=affected_user_id;
            DELETE FROM social.friend_request
             WHERE requester_user_id=affected_user_id OR target_user_id=affected_user_id;
            RETURN OLD;
          END IF;
          IF NEW.status <> 'ACTIVE' OR NEW.deleted_at IS NOT NULL THEN
            UPDATE social.friend_request
               SET state='CANCELLED',terminal_at=statement_timestamp()
             WHERE state='PENDING'
               AND (requester_user_id=affected_user_id OR target_user_id=affected_user_id);
            UPDATE social.friend_room_invitation
               SET state='ROOM_CHANGED',terminal_at=statement_timestamp(),
                   terminal_reason='ACCOUNT_UNAVAILABLE'
             WHERE state='PENDING'
               AND (host_user_id=affected_user_id OR target_user_id=affected_user_id);
            DELETE FROM social.presence_heartbeat WHERE user_id=affected_user_id;
            DELETE FROM social.presence_settings WHERE user_id=affected_user_id;
            DELETE FROM social.user_block
             WHERE blocker_user_id=affected_user_id OR blocked_user_id=affected_user_id;
            DELETE FROM social.friendship
             WHERE lower_user_id=affected_user_id OR higher_user_id=affected_user_id;
          END IF;
          RETURN NEW;
        END $$;

        DROP TABLE social.profile_statistics_settings;
        """
    )
