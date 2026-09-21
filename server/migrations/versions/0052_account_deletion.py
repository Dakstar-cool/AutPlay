"""Thirty-day deletion intent, separate cancellation and last-owner protection."""

from alembic import op

revision = "0052_account_deletion"
down_revision = "0051_account_recovery"
branch_labels = None
depends_on = None

SOCIAL_PREVIOUS = """
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

SOCIAL_PENDING = """
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
            IF NEW.status='DELETION_PENDING' AND NEW.deleted_at IS NULL THEN
              RETURN NEW;
            END IF;
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


def upgrade() -> None:
    op.execute(SOCIAL_PENDING)
    op.execute("""
ALTER TABLE account.user_account DROP CONSTRAINT ck_user_account_status;
ALTER TABLE account.user_account ADD CONSTRAINT ck_user_account_status
 CHECK(status IN ('ACTIVE','DISABLED','DELETION_PENDING'));
ALTER TABLE account.account_recovery_operation DROP CONSTRAINT recovery_operation_shape_check;
ALTER TABLE account.account_recovery_operation ADD CONSTRAINT recovery_operation_shape_check CHECK (
(kind='CONFIGURE' AND actor_device_id IS NOT NULL AND actor_family_id IS NOT NULL AND
spent_verifier_sha256 IS NULL AND result_device_id IS NULL AND result_session_id IS NULL AND
binding_commit_id IS NULL) OR (kind IN ('RECOVER','DELETE_CANCEL') AND actor_device_id IS NULL AND
actor_family_id IS NULL AND spent_verifier_sha256 IS NOT NULL AND result_device_id IS NOT NULL AND
result_session_id IS NOT NULL AND binding_commit_id IS NOT NULL AND previous_generation>=1));

CREATE TABLE account.account_deletion_request (
	deletion_request_id UUID NOT NULL,
	user_id UUID NOT NULL,
	server_instance_id UUID NOT NULL,
	identity_epoch BIGINT NOT NULL,
	identity_thumbprint_sha256 BYTEA NOT NULL,
	request_sha256 BYTEA NOT NULL,
	actor_device_id UUID NOT NULL,
	actor_public_key_spki BYTEA NOT NULL,
	code_generation BIGINT NOT NULL,
	code_verifier_sha256 BYTEA NOT NULL,
	authority_generation BIGINT NOT NULL,
	requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
	cancel_before TIMESTAMP WITH TIME ZONE NOT NULL,
	state TEXT NOT NULL,
	revision BIGINT NOT NULL,
	cancelled_at TIMESTAMP WITH TIME ZONE,
	cancel_operation_id UUID,
	purge_started_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT account_deletion_request_pkey PRIMARY KEY (deletion_request_id),
	CONSTRAINT deletion_request_actor_fkey
FOREIGN KEY (user_id, actor_device_id)
REFERENCES account.device (user_id, device_id),
	CONSTRAINT deletion_request_versions_check
CHECK (identity_epoch>=1
AND code_generation>=1
AND authority_generation>=2),
	CONSTRAINT deletion_request_hashes_check
CHECK (octet_length(identity_thumbprint_sha256)=32
AND octet_length(request_sha256)=32
AND octet_length(code_verifier_sha256)=32
AND octet_length(actor_public_key_spki) BETWEEN 32
AND 256),
	CONSTRAINT deletion_request_deadline_check
CHECK (cancel_before=requested_at+interval '720 hours'),
	CONSTRAINT deletion_request_state_check
CHECK ((state='PENDING'
AND revision=1
AND cancelled_at IS NULL
AND cancel_operation_id IS NULL
AND purge_started_at IS NULL)
OR (state='CANCELLED'
AND revision=2
AND cancelled_at IS NOT NULL
AND cancelled_at>=requested_at
AND cancelled_at<cancel_before
AND cancel_operation_id IS NOT NULL
AND purge_started_at IS NULL)
OR (state='PURGING'
AND revision=2
AND cancelled_at IS NULL
AND cancel_operation_id IS NULL
AND purge_started_at IS NOT NULL
AND purge_started_at>=cancel_before)),
	CONSTRAINT account_deletion_request_user_id_fkey
FOREIGN KEY (user_id)
REFERENCES account.user_account (user_id),
	CONSTRAINT account_deletion_request_server_instance_id_fkey
FOREIGN KEY (server_instance_id)
REFERENCES account.server_instance (server_instance_id),
	CONSTRAINT account_deletion_request_actor_device_id_fkey
FOREIGN KEY (actor_device_id)
REFERENCES account.device (device_id),
	CONSTRAINT account_deletion_request_cancel_operation_id_key UNIQUE (cancel_operation_id)
);
CREATE INDEX ix_deletion_request_deadline ON account.account_deletion_request (cancel_before)
WHERE state='PENDING';
CREATE UNIQUE INDEX ix_deletion_request_pending_user ON account.account_deletion_request (user_id)
WHERE state IN ('PENDING','PURGING');
""")
    op.execute("""
CREATE FUNCTION account.lock_owner_lifecycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(4707761689340236801);
 PERFORM user_id FROM account.user_account
 WHERE role='OWNER' AND status='ACTIVE' AND deleted_at IS NULL ORDER BY user_id FOR UPDATE;
 RETURN NULL;
END $$;
CREATE TRIGGER lock_owner_lifecycle BEFORE DELETE OR UPDATE OF status,role,deleted_at
 ON account.user_account FOR EACH STATEMENT EXECUTE FUNCTION account.lock_owner_lifecycle();
CREATE FUNCTION account.guard_last_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.role='OWNER' AND OLD.status='ACTIVE' AND OLD.deleted_at IS NULL
   AND (TG_OP='DELETE' OR NEW.role<>'OWNER' OR NEW.status<>'ACTIVE' OR NEW.deleted_at IS NOT NULL)
   AND NOT EXISTS(SELECT 1 FROM account.user_account WHERE user_id<>OLD.user_id
      AND role='OWNER' AND status='ACTIVE' AND deleted_at IS NULL) THEN
   RAISE EXCEPTION 'last_owner_required' USING ERRCODE='23514';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_last_owner BEFORE DELETE OR UPDATE OF status,role,deleted_at
 ON account.user_account FOR EACH ROW EXECUTE FUNCTION account.guard_last_owner();
CREATE FUNCTION account.guard_deletion_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'deletion intent cannot be discarded'; END IF;
 IF TG_OP='INSERT' THEN
   IF NEW.state<>'PENDING' THEN RAISE EXCEPTION 'deletion must start pending'; END IF;
 ELSE
   IF OLD.state<>'PENDING' OR NEW.state NOT IN ('CANCELLED','PURGING')
     OR NEW.revision<>OLD.revision+1
     OR
(to_jsonb(NEW)-ARRAY['state','revision','cancelled_at','cancel_operation_id','purge_started_at'])
       IS DISTINCT FROM
(to_jsonb(OLD)-ARRAY['state','revision','cancelled_at','cancel_operation_id','purge_started_at'])
THEN
     RAISE EXCEPTION 'deletion transition is not monotonic';
   END IF;
 END IF;
 IF NEW.state='CANCELLED' AND NOT EXISTS(
   SELECT 1 FROM account.account_recovery_operation WHERE operation_id=NEW.cancel_operation_id
   AND user_id=NEW.user_id AND kind='DELETE_CANCEL') THEN
   RAISE EXCEPTION 'deletion cancellation proof is missing';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_deletion_request BEFORE INSERT OR UPDATE OR DELETE
 ON account.account_deletion_request FOR EACH ROW EXECUTE FUNCTION account.guard_deletion_request();
CREATE FUNCTION account.check_deletion_account() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE id uuid; current_status text; pending boolean;
BEGIN
 id:=COALESCE(NEW.user_id,OLD.user_id);
 SELECT status INTO current_status FROM account.user_account WHERE user_id=id;
 SELECT EXISTS(SELECT 1 FROM account.account_deletion_request
   WHERE user_id=id AND state IN ('PENDING','PURGING')) INTO pending;
 IF (current_status='DELETION_PENDING' AND NOT pending)
   OR (pending AND current_status NOT IN ('DELETION_PENDING','DISABLED')) THEN
   RAISE EXCEPTION 'deletion lifecycle and account disagree';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER check_deletion_account AFTER INSERT OR UPDATE
 ON account.account_deletion_request DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION account.check_deletion_account();
CREATE CONSTRAINT TRIGGER check_account_deletion AFTER INSERT OR UPDATE
 ON account.user_account DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION account.check_deletion_account();
REVOKE ALL ON account.account_deletion_request FROM PUBLIC;
REVOKE ALL ON FUNCTION account.lock_owner_lifecycle(),account.guard_last_owner(),
 account.guard_deletion_request(),account.check_deletion_account() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
LOCK TABLE account.user_account,account.account_deletion_request,
 account.account_recovery_operation IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM account.account_deletion_request)
 OR EXISTS(SELECT 1 FROM account.user_account WHERE status='DELETION_PENDING')
 OR EXISTS(SELECT 1 FROM account.account_recovery_operation WHERE kind='DELETE_CANCEL') THEN
 RAISE EXCEPTION 'Refusing to discard account deletion evidence';
 END IF;
END $$;
DROP TRIGGER check_account_deletion ON account.user_account;
DROP TABLE account.account_deletion_request;
DROP TRIGGER guard_last_owner ON account.user_account;
DROP TRIGGER lock_owner_lifecycle ON account.user_account;
DROP FUNCTION account.check_deletion_account();
DROP FUNCTION account.guard_deletion_request();
DROP FUNCTION account.guard_last_owner();
DROP FUNCTION account.lock_owner_lifecycle();
ALTER TABLE account.user_account DROP CONSTRAINT ck_user_account_status;
ALTER TABLE account.user_account ADD CONSTRAINT ck_user_account_status CHECK(status IN
('ACTIVE','DISABLED'));
ALTER TABLE account.account_recovery_operation DROP CONSTRAINT recovery_operation_shape_check;
ALTER TABLE account.account_recovery_operation ADD CONSTRAINT recovery_operation_shape_check CHECK (
 (kind='CONFIGURE' AND actor_device_id IS NOT NULL AND actor_family_id IS NOT NULL
 AND spent_verifier_sha256 IS NULL AND result_device_id IS NULL AND result_session_id IS NULL
 AND binding_commit_id IS NULL) OR (kind='RECOVER' AND actor_device_id IS NULL
 AND actor_family_id IS NULL AND spent_verifier_sha256 IS NOT NULL AND result_device_id IS NOT NULL
 AND result_session_id IS NOT NULL AND binding_commit_id IS NOT NULL AND previous_generation>=1));
""")
    op.execute(SOCIAL_PREVIOUS)
