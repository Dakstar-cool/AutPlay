"""Add S1C durable same-server social runtime.

Revision ID: 0022_s1c_social_runtime
Revises: 0021_s1b_device_admission
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022_s1c_social_runtime"
down_revision: str | None = "0021_s1b_device_admission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA IF NOT EXISTS social;
        CREATE TABLE social.friend_request (
          request_id uuid PRIMARY KEY,
          requester_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          target_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          state text NOT NULL CONSTRAINT ck_social_friend_request_state CHECK(state IN ('PENDING','ACCEPTED','DECLINED','CANCELLED','BLOCKED','EXPIRED')),
          expires_at timestamptz NOT NULL, terminal_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_social_friend_request_pair CHECK(requester_user_id <> target_user_id)
        );
        CREATE UNIQUE INDEX uq_social_pending_friend_request ON social.friend_request(requester_user_id,target_user_id) WHERE state='PENDING';
        CREATE INDEX ix_social_friend_request_target ON social.friend_request(target_user_id,expires_at);

        CREATE TABLE social.friendship (
          lower_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          higher_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(lower_user_id,higher_user_id),
          CONSTRAINT ck_social_friendship_order CHECK(lower_user_id < higher_user_id)
        );
        CREATE TABLE social.user_block (
          blocker_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          blocked_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          blocked_at timestamptz NOT NULL DEFAULT now(), unblocked_at timestamptz,
          PRIMARY KEY(blocker_user_id,blocked_user_id),
          CONSTRAINT ck_social_user_block_pair CHECK(blocker_user_id <> blocked_user_id)
        );
        CREATE UNIQUE INDEX ix_social_user_block_active ON social.user_block(blocker_user_id,blocked_user_id) WHERE unblocked_at IS NULL;

        CREATE TABLE social.presence_settings (
          user_id uuid PRIMARY KEY REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          friend_presence_visibility_enabled boolean NOT NULL DEFAULT false,
          room_activity_sharing_enabled boolean NOT NULL DEFAULT false,
          invite_availability_enabled boolean NOT NULL DEFAULT false,
          revision bigint NOT NULL DEFAULT 1 CONSTRAINT ck_social_presence_settings_revision CHECK(revision >= 1),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE social.presence_heartbeat (
          user_id uuid NOT NULL, device_id uuid NOT NULL, session_id uuid NOT NULL REFERENCES account.user_session(session_id) ON DELETE RESTRICT,
          operation_id uuid NOT NULL, request_sha256 bytea NOT NULL CONSTRAINT ck_social_presence_hash CHECK(octet_length(request_sha256)=32),
          last_heartbeat_at timestamptz NOT NULL, fresh_until timestamptz NOT NULL,
          PRIMARY KEY(user_id,device_id),
          CONSTRAINT fk_social_presence_heartbeat_device_owner FOREIGN KEY(user_id,device_id) REFERENCES account.device(user_id,device_id) ON DELETE RESTRICT,
          CONSTRAINT ck_social_presence_expiry CHECK(fresh_until > last_heartbeat_at)
        );
        CREATE INDEX ix_social_presence_fresh ON social.presence_heartbeat(user_id,fresh_until);

        CREATE TABLE social.friend_room_invitation (
          invitation_id uuid PRIMARY KEY, create_operation_id uuid NOT NULL CONSTRAINT uq_social_room_invitation_create_operation UNIQUE,
          room_id uuid NOT NULL REFERENCES wave.room(room_id) ON DELETE RESTRICT,
          room_epoch bigint NOT NULL CONSTRAINT ck_social_room_invitation_epoch CHECK(room_epoch >= 1),
          host_user_id uuid NOT NULL, host_device_id uuid NOT NULL,
          target_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          state text NOT NULL CONSTRAINT ck_social_room_invitation_state CHECK(state IN ('PENDING','ACCEPTED','CANCELLED','EXPIRED','BLOCKED','FULL','ROOM_CHANGED')),
          expires_at timestamptz NOT NULL, terminal_at timestamptz, terminal_reason text,
          accepted_device_id uuid REFERENCES account.device(device_id) ON DELETE RESTRICT,
          accepting_session_id uuid REFERENCES account.user_session(session_id) ON DELETE RESTRICT,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT fk_social_room_invitation_host_device FOREIGN KEY(host_user_id,host_device_id) REFERENCES account.device(user_id,device_id) ON DELETE RESTRICT
        );
        CREATE INDEX ix_social_room_invitation_target ON social.friend_room_invitation(target_user_id,expires_at);
        CREATE UNIQUE INDEX uq_social_pending_room_target ON social.friend_room_invitation(room_id,target_user_id) WHERE state='PENDING';

        CREATE TABLE social.operation_receipt (
          operation_id uuid PRIMARY KEY, actor_user_id uuid NOT NULL, actor_device_id uuid NOT NULL,
          action text NOT NULL, request_sha256 bytea NOT NULL CONSTRAINT ck_social_operation_hash CHECK(octet_length(request_sha256)=32),
          result_code text NOT NULL CONSTRAINT ck_social_result_code CHECK(length(result_code) BETWEEN 1 AND 64),
          result_target_id uuid, result_room_id uuid, result_json text NOT NULL CONSTRAINT ck_social_result_json_size CHECK(octet_length(result_json)<=2048),
          expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT fk_social_operation_receipt_actor_device FOREIGN KEY(actor_user_id,actor_device_id) REFERENCES account.device(user_id,device_id) ON DELETE RESTRICT
        );
        CREATE INDEX ix_social_operation_receipt_expiry ON social.operation_receipt(expires_at);
        CREATE TABLE social.rate_window (
          rate_key_sha256 bytea PRIMARY KEY CONSTRAINT ck_social_rate_key CHECK(octet_length(rate_key_sha256)=32),
          scope text NOT NULL, window_started_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
          attempt_count integer NOT NULL CONSTRAINT ck_social_rate_attempts CHECK(attempt_count>=1),
          CONSTRAINT ck_social_rate_expiry CHECK(expires_at>window_started_at)
        );
        CREATE INDEX ix_social_rate_window_expiry ON social.rate_window(expires_at);

        CREATE FUNCTION social.retire_account_state() RETURNS trigger
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
               SET state='ROOM_CHANGED',terminal_at=statement_timestamp(),terminal_reason='ACCOUNT_UNAVAILABLE'
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
        CREATE TRIGGER trg_social_account_retire
          BEFORE UPDATE OR DELETE ON account.user_account
          FOR EACH ROW EXECUTE FUNCTION social.retire_account_state();

        REVOKE ALL ON SCHEMA social FROM PUBLIC;
        REVOKE ALL ON ALL TABLES IN SCHEMA social FROM PUBLIC;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM social.friend_request)
             OR EXISTS (SELECT 1 FROM social.friendship)
             OR EXISTS (SELECT 1 FROM social.user_block)
             OR EXISTS (SELECT 1 FROM social.presence_settings)
             OR EXISTS (SELECT 1 FROM social.presence_heartbeat)
             OR EXISTS (SELECT 1 FROM social.friend_room_invitation)
             OR EXISTS (SELECT 1 FROM social.operation_receipt)
             OR EXISTS (SELECT 1 FROM social.rate_window) THEN
            RAISE EXCEPTION 'refusing S1C downgrade with social evidence';
          END IF;
        END $$;
        DROP TRIGGER trg_social_account_retire ON account.user_account;
        DROP FUNCTION social.retire_account_state();
        DROP TABLE social.rate_window;
        DROP TABLE social.operation_receipt;
        DROP TABLE social.friend_room_invitation;
        DROP TABLE social.presence_heartbeat;
        DROP TABLE social.presence_settings;
        DROP TABLE social.user_block;
        DROP TABLE social.friendship;
        DROP TABLE social.friend_request;
        DROP SCHEMA social;
        """
    )
