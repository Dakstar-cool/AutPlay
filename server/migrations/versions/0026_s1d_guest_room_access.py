# ruff: noqa: E501
"""Add S1D hash-only guest Room capability state.

Revision ID: 0026_s1d_guest_room_access
Revises: 0025_a1c_automation_runtime
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026_s1d_guest_room_access"
down_revision: str | None = "0025_a1c_automation_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE account.user_session
          ADD CONSTRAINT uq_user_session_actor UNIQUE(user_id,device_id,session_id);

        CREATE TABLE social.guest_invitation (
          invitation_id uuid PRIMARY KEY,
          room_id uuid NOT NULL REFERENCES wave.room(room_id) ON DELETE RESTRICT,
          room_epoch bigint NOT NULL,
          host_user_id uuid NOT NULL,
          host_device_id uuid NOT NULL,
          host_session_id uuid NOT NULL,
          document_secret_sha256 bytea NOT NULL UNIQUE,
          role text NOT NULL DEFAULT 'GUEST',
          allowed_actions text[] NOT NULL DEFAULT ARRAY['ROOM_SNAPSHOT','ROOM_EVENTS','ROOM_PRESENCE','ROOM_PREFLIGHT','ROOM_TIMING','ROOM_LEAVE']::text[],
          state text NOT NULL DEFAULT 'PENDING',
          max_uses smallint NOT NULL DEFAULT 1,
          consumed_uses smallint NOT NULL DEFAULT 0,
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          terminal_at timestamptz,
          terminal_reason text,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_social_guest_invitation_room UNIQUE(invitation_id,room_id),
          CONSTRAINT fk_social_guest_invitation_host_device FOREIGN KEY(host_user_id,host_device_id) REFERENCES account.device(user_id,device_id) ON DELETE RESTRICT,
          CONSTRAINT fk_social_guest_invitation_host_session FOREIGN KEY(host_user_id,host_device_id,host_session_id) REFERENCES account.user_session(user_id,device_id,session_id) ON DELETE RESTRICT,
          CONSTRAINT ck_social_guest_invitation_hash CHECK(octet_length(document_secret_sha256)=32),
          CONSTRAINT ck_social_guest_invitation_epoch CHECK(room_epoch>=1),
          CONSTRAINT ck_social_guest_invitation_role CHECK(role='GUEST'),
          CONSTRAINT ck_social_guest_invitation_actions CHECK(allowed_actions=ARRAY['ROOM_SNAPSHOT','ROOM_EVENTS','ROOM_PRESENCE','ROOM_PREFLIGHT','ROOM_TIMING','ROOM_LEAVE']::text[]),
          CONSTRAINT ck_social_guest_invitation_state CHECK(state IN ('PENDING','DEPLETED','REVOKED','EXPIRED','ROOM_CLOSED')),
          CONSTRAINT ck_social_guest_invitation_uses CHECK(max_uses BETWEEN 1 AND 8 AND consumed_uses BETWEEN 0 AND max_uses),
          CONSTRAINT ck_social_guest_invitation_expiry CHECK(expires_at>created_at),
          CONSTRAINT ck_social_guest_invitation_terminal CHECK((state='PENDING' AND terminal_at IS NULL) OR (state<>'PENDING' AND terminal_at IS NOT NULL))
        );
        CREATE INDEX ix_social_guest_invitation_room_state ON social.guest_invitation(room_id,state,expires_at);
        CREATE INDEX ix_social_guest_invitation_expiry ON social.guest_invitation(expires_at);

        CREATE TABLE social.guest_session (
          guest_session_id uuid PRIMARY KEY,
          invitation_id uuid NOT NULL,
          room_id uuid NOT NULL,
          room_epoch bigint NOT NULL,
          access_secret_sha256 bytea NOT NULL UNIQUE,
          display_name text NOT NULL,
          role text NOT NULL DEFAULT 'GUEST',
          allowed_actions text[] NOT NULL DEFAULT ARRAY['ROOM_SNAPSHOT','ROOM_EVENTS','ROOM_PRESENCE','ROOM_PREFLIGHT','ROOM_TIMING','ROOM_LEAVE']::text[],
          state text NOT NULL DEFAULT 'ACTIVE',
          expires_at timestamptz NOT NULL,
          last_present_at timestamptz,
          left_at timestamptz,
          revoked_at timestamptz,
          terminal_at timestamptz,
          terminal_reason text,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_social_guest_session_room UNIQUE(guest_session_id,room_id),
          CONSTRAINT fk_social_guest_session_invitation FOREIGN KEY(invitation_id,room_id) REFERENCES social.guest_invitation(invitation_id,room_id) ON DELETE RESTRICT,
          CONSTRAINT ck_social_guest_session_hash CHECK(octet_length(access_secret_sha256)=32),
          CONSTRAINT ck_social_guest_session_epoch CHECK(room_epoch>=1),
          CONSTRAINT ck_social_guest_session_name CHECK(length(display_name) BETWEEN 1 AND 40 AND display_name !~ '[[:cntrl:]]'),
          CONSTRAINT ck_social_guest_session_role CHECK(role='GUEST'),
          CONSTRAINT ck_social_guest_session_actions CHECK(allowed_actions=ARRAY['ROOM_SNAPSHOT','ROOM_EVENTS','ROOM_PRESENCE','ROOM_PREFLIGHT','ROOM_TIMING','ROOM_LEAVE']::text[]),
          CONSTRAINT ck_social_guest_session_state CHECK(state IN ('ACTIVE','LEFT','REVOKED','EXPIRED','ROOM_CLOSED')),
          CONSTRAINT ck_social_guest_session_expiry CHECK(expires_at>created_at),
          CONSTRAINT ck_social_guest_session_presence CHECK(last_present_at IS NULL OR last_present_at>=created_at),
          CONSTRAINT ck_social_guest_session_terminal CHECK((state='ACTIVE' AND terminal_at IS NULL) OR (state<>'ACTIVE' AND terminal_at IS NOT NULL))
        );
        CREATE INDEX ix_social_guest_session_room_state ON social.guest_session(room_id,state,expires_at);
        CREATE INDEX ix_social_guest_session_expiry ON social.guest_session(expires_at);

        CREATE TABLE social.guest_operation_receipt (
          operation_id uuid PRIMARY KEY,
          actor_kind text NOT NULL,
          actor_user_id uuid,
          actor_device_id uuid,
          actor_secret_sha256 bytea,
          actor_guest_session_id uuid,
          action text NOT NULL,
          request_sha256 bytea NOT NULL,
          result_code text NOT NULL,
          result_invitation_id uuid,
          result_guest_session_id uuid,
          result_room_id uuid,
          result_json text NOT NULL,
          expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT fk_social_guest_operation_host FOREIGN KEY(actor_user_id,actor_device_id) REFERENCES account.device(user_id,device_id) ON DELETE RESTRICT,
          CONSTRAINT fk_social_guest_operation_actor_session FOREIGN KEY(actor_guest_session_id) REFERENCES social.guest_session(guest_session_id) ON DELETE RESTRICT,
          CONSTRAINT fk_social_guest_operation_invitation FOREIGN KEY(result_invitation_id) REFERENCES social.guest_invitation(invitation_id) ON DELETE RESTRICT,
          CONSTRAINT fk_social_guest_operation_result_session FOREIGN KEY(result_guest_session_id) REFERENCES social.guest_session(guest_session_id) ON DELETE RESTRICT,
          CONSTRAINT fk_social_guest_operation_room FOREIGN KEY(result_room_id) REFERENCES wave.room(room_id) ON DELETE RESTRICT,
          CONSTRAINT ck_social_guest_operation_actor_kind CHECK(actor_kind IN ('HOST','DOCUMENT','GUEST')),
          CONSTRAINT ck_social_guest_operation_actor CHECK((actor_kind='HOST' AND actor_user_id IS NOT NULL AND actor_device_id IS NOT NULL AND actor_secret_sha256 IS NULL AND actor_guest_session_id IS NULL) OR (actor_kind='DOCUMENT' AND actor_user_id IS NULL AND actor_device_id IS NULL AND octet_length(actor_secret_sha256)=32 AND actor_guest_session_id IS NULL) OR (actor_kind='GUEST' AND actor_user_id IS NULL AND actor_device_id IS NULL AND actor_secret_sha256 IS NULL AND actor_guest_session_id IS NOT NULL)),
          CONSTRAINT ck_social_guest_operation_action CHECK(action IN ('ISSUE','REDEEM','REVOKE','LEAVE')),
          CONSTRAINT ck_social_guest_operation_hash CHECK(octet_length(request_sha256)=32),
          CONSTRAINT ck_social_guest_operation_result CHECK(length(result_code) BETWEEN 1 AND 64 AND octet_length(result_json)<=2048),
          CONSTRAINT ck_social_guest_operation_expiry CHECK(expires_at>created_at)
        );
        CREATE INDEX ix_social_guest_operation_expiry ON social.guest_operation_receipt(expires_at);

        CREATE TABLE social.guest_preflight (
          room_id uuid NOT NULL,
          guest_session_id uuid NOT NULL,
          queue_entry_id uuid NOT NULL,
          recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
          queue_version bigint NOT NULL,
          availability text NOT NULL,
          final_ready boolean NOT NULL DEFAULT false,
          source_checked_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          PRIMARY KEY(room_id,guest_session_id,queue_entry_id),
          CONSTRAINT fk_social_guest_preflight_session FOREIGN KEY(guest_session_id,room_id) REFERENCES social.guest_session(guest_session_id,room_id) ON DELETE RESTRICT,
          CONSTRAINT fk_social_guest_preflight_queue FOREIGN KEY(queue_entry_id,room_id) REFERENCES wave.queue_entry(queue_entry_id,room_id) ON DELETE RESTRICT,
          CONSTRAINT ck_social_guest_preflight_version CHECK(queue_version>=1),
          CONSTRAINT ck_social_guest_preflight_availability CHECK(availability IN ('LOCAL','DOWNLOADED','VAULT_STREAMABLE','UNAVAILABLE')),
          CONSTRAINT ck_social_guest_preflight_expiry CHECK(expires_at>source_checked_at)
        );
        CREATE INDEX ix_social_guest_preflight_expiry ON social.guest_preflight(expires_at);

        CREATE TABLE social.guest_timing_report (
          room_id uuid NOT NULL,
          guest_session_id uuid NOT NULL,
          command_sequence bigint NOT NULL,
          rtt_ms integer NOT NULL,
          offset_ms integer NOT NULL,
          uncertainty_ms integer NOT NULL,
          start_skew_ms integer,
          drift_ms integer,
          reported_at timestamptz NOT NULL,
          PRIMARY KEY(room_id,guest_session_id,command_sequence),
          CONSTRAINT fk_social_guest_timing_session FOREIGN KEY(guest_session_id,room_id) REFERENCES social.guest_session(guest_session_id,room_id) ON DELETE RESTRICT,
          CONSTRAINT ck_social_guest_timing_bounds CHECK(rtt_ms BETWEEN 0 AND 1000 AND uncertainty_ms BETWEEN 0 AND 100 AND abs(offset_ms)<=86400000)
        );
        CREATE INDEX ix_social_guest_timing_reported ON social.guest_timing_report(reported_at);

        CREATE TABLE social.guest_rate_window (
          rate_key_sha256 bytea PRIMARY KEY,
          scope text NOT NULL,
          window_started_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          attempt_count integer NOT NULL,
          CONSTRAINT ck_social_guest_rate_hash CHECK(octet_length(rate_key_sha256)=32),
          CONSTRAINT ck_social_guest_rate_attempts CHECK(attempt_count>=1),
          CONSTRAINT ck_social_guest_rate_expiry CHECK(expires_at>window_started_at)
        );
        CREATE INDEX ix_social_guest_rate_expiry ON social.guest_rate_window(expires_at);

        CREATE FUNCTION social.retire_guest_invitation_sessions() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.state IN ('REVOKED','EXPIRED','ROOM_CLOSED') AND OLD.state IS DISTINCT FROM NEW.state THEN
            UPDATE social.guest_session
               SET state=NEW.state, revoked_at=CASE WHEN NEW.state='REVOKED' THEN statement_timestamp() ELSE revoked_at END,
                   terminal_at=statement_timestamp(), terminal_reason=COALESCE(NEW.terminal_reason,NEW.state)
             WHERE invitation_id=NEW.invitation_id AND state='ACTIVE';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_social_guest_invitation_retire
          AFTER UPDATE OF state ON social.guest_invitation
          FOR EACH ROW EXECUTE FUNCTION social.retire_guest_invitation_sessions();

        CREATE FUNCTION social.retire_guest_room_state() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.state IN ('CLOSED','EXPIRED') OR NEW.expires_at<=statement_timestamp() THEN
            UPDATE social.guest_invitation
               SET state=CASE WHEN NEW.state='CLOSED' THEN 'ROOM_CLOSED' ELSE 'EXPIRED' END,
                   terminal_at=statement_timestamp(), terminal_reason=CASE WHEN NEW.state='CLOSED' THEN 'ROOM_CLOSED' ELSE 'ROOM_EXPIRED' END
              WHERE room_id=NEW.room_id AND state IN ('PENDING','DEPLETED');
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_social_guest_room_retire
          AFTER UPDATE OF state,expires_at ON wave.room
          FOR EACH ROW EXECUTE FUNCTION social.retire_guest_room_state();

        CREATE FUNCTION social.retire_guest_device_state() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.revoked_at IS NOT NULL AND OLD.revoked_at IS DISTINCT FROM NEW.revoked_at THEN
            UPDATE social.guest_invitation
               SET state='REVOKED',revoked_at=statement_timestamp(),terminal_at=statement_timestamp(),terminal_reason='HOST_DEVICE_REVOKED'
             WHERE host_user_id=NEW.user_id AND host_device_id=NEW.device_id AND state IN ('PENDING','DEPLETED');
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_social_guest_device_retire
          AFTER UPDATE OF revoked_at ON account.device
          FOR EACH ROW EXECUTE FUNCTION social.retire_guest_device_state();

        CREATE FUNCTION social.retire_guest_user_session_state() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.revoked_at IS NOT NULL AND OLD.revoked_at IS DISTINCT FROM NEW.revoked_at THEN
            UPDATE social.guest_invitation
               SET state='REVOKED',revoked_at=statement_timestamp(),terminal_at=statement_timestamp(),terminal_reason='HOST_SESSION_REVOKED'
             WHERE host_user_id=NEW.user_id AND host_device_id=NEW.device_id AND host_session_id=NEW.session_id AND state IN ('PENDING','DEPLETED');
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_social_guest_user_session_retire
          AFTER UPDATE OF revoked_at ON account.user_session
          FOR EACH ROW EXECUTE FUNCTION social.retire_guest_user_session_state();

        CREATE FUNCTION social.retire_guest_account_state() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE affected_user_id uuid;
        BEGIN
          affected_user_id := COALESCE(OLD.user_id,NEW.user_id);
          IF TG_OP='DELETE' THEN
            DELETE FROM social.guest_preflight WHERE guest_session_id IN (SELECT guest_session_id FROM social.guest_session s JOIN social.guest_invitation i USING(invitation_id) WHERE i.host_user_id=affected_user_id);
            DELETE FROM social.guest_timing_report WHERE guest_session_id IN (SELECT guest_session_id FROM social.guest_session s JOIN social.guest_invitation i USING(invitation_id) WHERE i.host_user_id=affected_user_id);
            DELETE FROM social.guest_operation_receipt WHERE result_invitation_id IN (SELECT invitation_id FROM social.guest_invitation WHERE host_user_id=affected_user_id) OR result_guest_session_id IN (SELECT guest_session_id FROM social.guest_session s JOIN social.guest_invitation i USING(invitation_id) WHERE i.host_user_id=affected_user_id) OR actor_guest_session_id IN (SELECT guest_session_id FROM social.guest_session s JOIN social.guest_invitation i USING(invitation_id) WHERE i.host_user_id=affected_user_id) OR actor_user_id=affected_user_id;
            DELETE FROM social.guest_session WHERE invitation_id IN (SELECT invitation_id FROM social.guest_invitation WHERE host_user_id=affected_user_id);
            DELETE FROM social.guest_invitation WHERE host_user_id=affected_user_id;
            RETURN OLD;
          END IF;
          IF NEW.status<>'ACTIVE' OR NEW.deleted_at IS NOT NULL THEN
            UPDATE social.guest_invitation SET state='REVOKED',revoked_at=statement_timestamp(),terminal_at=statement_timestamp(),terminal_reason='HOST_ACCOUNT_UNAVAILABLE' WHERE host_user_id=affected_user_id AND state IN ('PENDING','DEPLETED');
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_social_guest_account_retire
          BEFORE UPDATE OR DELETE ON account.user_account
          FOR EACH ROW EXECUTE FUNCTION social.retire_guest_account_state();

        REVOKE ALL ON ALL TABLES IN SCHEMA social FROM PUBLIC;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM social.guest_invitation)
             OR EXISTS(SELECT 1 FROM social.guest_session)
             OR EXISTS(SELECT 1 FROM social.guest_operation_receipt)
             OR EXISTS(SELECT 1 FROM social.guest_preflight)
             OR EXISTS(SELECT 1 FROM social.guest_timing_report)
             OR EXISTS(SELECT 1 FROM social.guest_rate_window) THEN
            RAISE EXCEPTION 'refusing S1D downgrade with guest evidence';
          END IF;
        END $$;
        DROP TRIGGER trg_social_guest_account_retire ON account.user_account;
        DROP FUNCTION social.retire_guest_account_state();
        DROP TRIGGER trg_social_guest_user_session_retire ON account.user_session;
        DROP FUNCTION social.retire_guest_user_session_state();
        DROP TRIGGER trg_social_guest_device_retire ON account.device;
        DROP FUNCTION social.retire_guest_device_state();
        DROP TRIGGER trg_social_guest_room_retire ON wave.room;
        DROP FUNCTION social.retire_guest_room_state();
        DROP TRIGGER trg_social_guest_invitation_retire ON social.guest_invitation;
        DROP FUNCTION social.retire_guest_invitation_sessions();
        DROP TABLE social.guest_rate_window;
        DROP TABLE social.guest_timing_report;
        DROP TABLE social.guest_preflight;
        DROP TABLE social.guest_operation_receipt;
        DROP TABLE social.guest_session;
        DROP TABLE social.guest_invitation;
        ALTER TABLE account.user_session DROP CONSTRAINT uq_user_session_actor;
        """
    )
