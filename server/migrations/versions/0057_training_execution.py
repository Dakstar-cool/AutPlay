"""Measured retained training process authority and exact exit accounting."""

from alembic import op

revision = "0057_training_execution"
down_revision = "0056_training_checkpoint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.training_execution (
 execution_id uuid PRIMARY KEY,run_id uuid NOT NULL REFERENCES ml.training_run(run_id),
 input_root text NOT NULL,output_root text NOT NULL,
 input_root_device text NOT NULL,input_root_inode text NOT NULL,
 input_scope_device text NOT NULL,input_scope_inode text NOT NULL,
 output_scope_device text NOT NULL,output_scope_inode text NOT NULL,
 input_inventory_sha256 bytea NOT NULL,root_inventory_sha256 bytea NOT NULL,
 input_bytes bigint NOT NULL,
 maximum_output_bytes bigint NOT NULL,state text NOT NULL,created_at timestamptz NOT NULL,
 started_at timestamptz,heartbeat_at timestamptz,io_deadline_at timestamptz,
 cleanup_started_at timestamptz,closed_at timestamptz,
 input_cleaned_at timestamptz,input_cleanup_sha256 bytea,
 checkpoint_device text,checkpoint_inode text,retain_checkpoint boolean,
 checkpoint_manifest_sha256 bytea,checkpoint_weights_sha256 bytea,
 checkpoint_optimizer_steps bigint,checkpoint_device_type text,
 child_pid bigint,child_identity_sha256 bytea,closure_kind text,
 closure_evidence_sha256 bytea,exit_code bigint,
 CONSTRAINT training_execution_target_check CHECK(octet_length(input_inventory_sha256)=32
  AND octet_length(root_inventory_sha256)=32
  AND input_bytes BETWEEN 1 AND 9223372036854775807
  AND maximum_output_bytes BETWEEN 1 AND 9223372036854775807
  AND length(input_root) BETWEEN 1 AND 4096 AND length(output_root) BETWEEN 1 AND 4096
  AND input_root_device~'^[0-9]{1,32}$' AND input_root_inode~'^[0-9]{1,32}$'
  AND input_scope_device~'^[0-9]{1,32}$' AND input_scope_inode~'^[0-9]{1,32}$'
  AND output_scope_device~'^[0-9]{1,32}$' AND output_scope_inode~'^[0-9]{1,32}$'
  AND input_root~('[\\\\/]'||execution_id::text||'$') AND input_root<>output_root
  AND state IN ('PREPARED','RUNNING','STOPPING','CLOSED')),
 CONSTRAINT training_execution_child_check CHECK(
  (child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL
   AND heartbeat_at IS NULL AND io_deadline_at IS NULL) OR
  (child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL
   AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL
   AND started_at>=created_at AND heartbeat_at IS NOT NULL AND heartbeat_at>=started_at
   AND io_deadline_at IS NOT NULL AND io_deadline_at>heartbeat_at
   AND io_deadline_at<=heartbeat_at+interval '5 seconds')),
 CONSTRAINT training_execution_times_check CHECK(
  (state='PREPARED' AND started_at IS NULL AND cleanup_started_at IS NULL
   AND closed_at IS NULL) OR
  (state='RUNNING' AND started_at IS NOT NULL AND cleanup_started_at IS NULL
   AND closed_at IS NULL) OR
  (state='STOPPING' AND cleanup_started_at IS NOT NULL AND closed_at IS NULL) OR
  (state='CLOSED' AND closed_at IS NOT NULL AND closed_at>=created_at
   AND cleanup_started_at IS NOT NULL AND closed_at>=cleanup_started_at
   AND (heartbeat_at IS NULL OR closed_at>=heartbeat_at))),
 CONSTRAINT training_execution_cleanup_check CHECK(
  (state<>'CLOSED' AND input_cleaned_at IS NULL AND input_cleanup_sha256 IS NULL) OR
  (state='CLOSED' AND input_cleaned_at IS NOT NULL AND input_cleanup_sha256 IS NOT NULL
   AND octet_length(input_cleanup_sha256)=32 AND closed_at>=input_cleaned_at)),
 CONSTRAINT training_execution_checkpoint_identity_check CHECK(
  (checkpoint_device IS NULL AND checkpoint_inode IS NULL) OR
  (checkpoint_device IS NOT NULL AND checkpoint_inode IS NOT NULL
   AND checkpoint_device~'^[0-9]{1,32}$' AND checkpoint_inode~'^[0-9]{1,32}$')),
 CONSTRAINT training_execution_checkpoint_result_check CHECK(
  (state NOT IN ('STOPPING','CLOSED') AND retain_checkpoint IS NULL
   AND checkpoint_manifest_sha256 IS NULL AND checkpoint_weights_sha256 IS NULL
   AND checkpoint_optimizer_steps IS NULL AND checkpoint_device_type IS NULL) OR
  (state IN ('STOPPING','CLOSED') AND (state<>'CLOSED' OR retain_checkpoint IS NOT NULL) AND
   ((checkpoint_manifest_sha256 IS NULL AND checkpoint_weights_sha256 IS NULL
     AND checkpoint_optimizer_steps IS NULL AND checkpoint_device_type IS NULL
     AND retain_checkpoint=false) OR
    (checkpoint_device IS NOT NULL AND checkpoint_manifest_sha256 IS NOT NULL
     AND octet_length(checkpoint_manifest_sha256)=32
     AND checkpoint_weights_sha256 IS NOT NULL
     AND octet_length(checkpoint_weights_sha256)=32
     AND checkpoint_optimizer_steps>=1 AND checkpoint_device_type IN ('cpu','cuda'))))),
 CONSTRAINT training_execution_closure_check CHECK(
  (state NOT IN ('STOPPING','CLOSED') AND closure_kind IS NULL
   AND closure_evidence_sha256 IS NULL AND exit_code IS NULL) OR
  (state IN ('STOPPING','CLOSED') AND closure_kind IS NOT NULL
   AND closure_evidence_sha256 IS NOT NULL
   AND octet_length(closure_evidence_sha256)=32 AND
   ((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL) OR
    (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL) OR
    (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL)))));
CREATE UNIQUE INDEX uq_training_execution_run_open ON ml.training_execution(run_id)
 WHERE closed_at IS NULL;

ALTER TABLE account.internal_io_policy DROP CONSTRAINT internal_io_workload_version_check;
ALTER TABLE account.internal_io_policy ADD CONSTRAINT internal_io_workload_version_check
 CHECK(workload_version IN (1,2,3));

CREATE FUNCTION app_private.lock_training_execution_identity()
 RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE run ml.training_run%ROWTYPE;
BEGIN
 IF TG_OP='INSERT' OR NEW.state='RUNNING' THEN
  SELECT * INTO run FROM ml.training_run WHERE run_id=NEW.run_id;
  PERFORM 1 FROM account.server_instance WHERE server_instance_id=run.server_instance_id
   AND identity_epoch=run.identity_epoch FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'training_identity_changed' USING ERRCODE='55000'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_training_execution_identity BEFORE INSERT OR UPDATE ON ml.training_execution
 FOR EACH ROW EXECUTE FUNCTION app_private.lock_training_execution_identity();

CREATE FUNCTION app_private.authorize_training_execution_transition(
 target_execution uuid,expected_state text,target_state text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE actual_state text;
BEGIN
 IF (expected_state NOT IN ('PREPARED','RUNNING') OR target_state<>'STOPPING')
   AND (expected_state<>'STOPPING' OR target_state NOT IN ('STOPPING','CLOSED')) THEN
  RAISE EXCEPTION 'training_execution_transition_invalid' USING ERRCODE='55000';
 END IF;
 SELECT state INTO actual_state FROM ml.training_execution
  WHERE execution_id=target_execution FOR UPDATE;
 IF actual_state IS NULL OR actual_state<>expected_state THEN
  RAISE EXCEPTION 'training_execution_stale' USING ERRCODE='55000';
 END IF;
 CREATE TEMP TABLE IF NOT EXISTS training_execution_transition_guard(
  backend_pid integer NOT NULL,transaction_id text NOT NULL,execution_id uuid NOT NULL,
  expected_state text NOT NULL,target_state text NOT NULL,
  PRIMARY KEY(backend_pid,transaction_id,execution_id)) ON COMMIT DELETE ROWS;
 DELETE FROM pg_temp.training_execution_transition_guard
  WHERE backend_pid=pg_backend_pid() AND transaction_id=pg_current_xact_id()::text
   AND execution_id=target_execution;
 INSERT INTO pg_temp.training_execution_transition_guard VALUES(
  pg_backend_pid(),pg_current_xact_id()::text,target_execution,expected_state,target_state);
END $$;

CREATE FUNCTION app_private.protect_training_execution() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE participant record; participant_total bigint; run ml.training_run%ROWTYPE;
 transition_count integer:=0;
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'Training execution ownership cannot be deleted' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'PREPARED' THEN
   RAISE EXCEPTION 'training_execution_stale' USING ERRCODE='55000';
  END IF;
 ELSE
  IF ROW(NEW.execution_id,NEW.run_id,NEW.input_root,NEW.output_root,
    NEW.input_root_device,NEW.input_root_inode,NEW.input_scope_device,NEW.input_scope_inode,
    NEW.output_scope_device,NEW.output_scope_inode,NEW.input_inventory_sha256,
    NEW.root_inventory_sha256,NEW.input_bytes,
    NEW.maximum_output_bytes,NEW.created_at) IS DISTINCT FROM
   ROW(OLD.execution_id,OLD.run_id,OLD.input_root,OLD.output_root,
    OLD.input_root_device,OLD.input_root_inode,OLD.input_scope_device,OLD.input_scope_inode,
    OLD.output_scope_device,OLD.output_scope_inode,OLD.input_inventory_sha256,
    OLD.root_inventory_sha256,OLD.input_bytes,
    OLD.maximum_output_bytes,OLD.created_at)
   OR (OLD.state='CLOSED' AND NEW IS DISTINCT FROM OLD)
   OR (OLD.state='RUNNING' AND NEW.state NOT IN ('RUNNING','STOPPING'))
   OR (OLD.state='STOPPING' AND NEW.state NOT IN ('STOPPING','CLOSED'))
   OR (OLD.state='PREPARED' AND NEW.state NOT IN ('PREPARED','RUNNING','STOPPING'))
   OR (OLD.state='STOPPING' AND ROW(NEW.cleanup_started_at,NEW.closure_kind,
     NEW.closure_evidence_sha256,NEW.exit_code,NEW.checkpoint_device,
     NEW.checkpoint_inode,NEW.checkpoint_manifest_sha256,NEW.checkpoint_weights_sha256,
     NEW.checkpoint_optimizer_steps,NEW.checkpoint_device_type) IS DISTINCT FROM
    ROW(OLD.cleanup_started_at,OLD.closure_kind,OLD.closure_evidence_sha256,OLD.exit_code,
     OLD.checkpoint_device,OLD.checkpoint_inode,OLD.checkpoint_manifest_sha256,
     OLD.checkpoint_weights_sha256,OLD.checkpoint_optimizer_steps,
     OLD.checkpoint_device_type))
   OR (OLD.state='STOPPING' AND OLD.retain_checkpoint IS NOT NULL
     AND NEW.retain_checkpoint IS DISTINCT FROM OLD.retain_checkpoint)
   OR (OLD.checkpoint_device IS NOT NULL AND
    ROW(NEW.checkpoint_device,NEW.checkpoint_inode) IS DISTINCT FROM
    ROW(OLD.checkpoint_device,OLD.checkpoint_inode))
   OR (OLD.child_pid IS NOT NULL AND
    ROW(NEW.child_pid,NEW.child_identity_sha256,NEW.started_at) IS DISTINCT FROM
    ROW(OLD.child_pid,OLD.child_identity_sha256,OLD.started_at))
   OR (OLD.heartbeat_at IS NOT NULL AND NEW.heartbeat_at<OLD.heartbeat_at) THEN
   RAISE EXCEPTION 'Training execution ownership is immutable' USING ERRCODE='23514';
  END IF;
 END IF;
 IF TG_OP='UPDATE' AND ((NEW.state<>OLD.state AND NEW.state IN ('STOPPING','CLOSED'))
   OR (OLD.state='STOPPING' AND OLD.retain_checkpoint IS NULL
     AND NEW.retain_checkpoint IS NOT NULL)) THEN
  BEGIN
   DELETE FROM pg_temp.training_execution_transition_guard
    WHERE backend_pid=pg_backend_pid() AND transaction_id=pg_current_xact_id()::text
     AND execution_id=NEW.execution_id AND expected_state=OLD.state
     AND target_state=NEW.state;
   GET DIAGNOSTICS transition_count=ROW_COUNT;
  EXCEPTION WHEN undefined_table THEN transition_count:=0;
  END;
  IF transition_count<>1 THEN
   RAISE EXCEPTION 'training_execution_transition_required' USING ERRCODE='55000';
  END IF;
 END IF;
 IF TG_OP='INSERT' OR NEW.state='RUNNING' THEN
  IF current_setting('transaction_isolation')<>'read committed' THEN
   RAISE EXCEPTION 'training_work_isolation_required' USING ERRCODE='55000';
  END IF;
  participant_total:=0;
  FOR participant IN SELECT * FROM ml.training_participant
    WHERE run_id=NEW.run_id ORDER BY user_id LOOP
   participant_total:=participant_total+1;
   PERFORM 1 FROM account.user_account a JOIN account.training_consent c USING(user_id)
    WHERE a.user_id=participant.user_id AND a.status='ACTIVE' AND a.deleted_at IS NULL
      AND c.decision='GRANTED' AND c.revision=participant.consent_revision
      AND c.policy_version=1 FOR UPDATE OF a,c;
   IF NOT FOUND THEN
    RAISE EXCEPTION 'training_execution_stale' USING ERRCODE='55000';
   END IF;
  END LOOP;
  SELECT * INTO run FROM ml.training_run WHERE run_id=NEW.run_id FOR UPDATE;
  IF run.phase NOT IN ('READY','RUNNING') OR run.dataset_sha256 IS NULL
    OR participant_total<>run.participant_count THEN
   RAISE EXCEPTION 'training_execution_stale' USING ERRCODE='55000';
  END IF;
 END IF;
 IF NEW.state='RUNNING' AND (NEW.heartbeat_at>clock_timestamp()
    OR NEW.io_deadline_at>clock_timestamp()+interval '5 seconds'
    OR NEW.io_deadline_at<=clock_timestamp()
    OR (OLD.state='RUNNING' AND OLD.io_deadline_at<=clock_timestamp())) THEN
  RAISE EXCEPTION 'training_execution_stale' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER training_execution_guard BEFORE INSERT OR UPDATE OR DELETE
 ON ml.training_execution FOR EACH ROW EXECUTE FUNCTION app_private.protect_training_execution();

CREATE OR REPLACE FUNCTION app_private.admit_internal_io() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE capacity integer; occupied bigint;
BEGIN
 PERFORM pg_advisory_xact_lock(4707761689340236801::bigint);
 SELECT p.active_limit INTO capacity FROM account.internal_io_policy p
 JOIN account.server_instance s ON s.server_instance_id=p.server_instance_id
  AND s.identity_epoch=p.identity_epoch
 JOIN account.resource_quota_policy q ON q.singleton_id=1
  AND q.playback_ceiling<=p.playback_ceiling AND q.transfer_ceiling<=p.transfer_ceiling
 WHERE p.singleton_id=1
  AND (TG_TABLE_NAME<>'metadata_execution' OR p.workload_version>=2)
  AND (TG_TABLE_NAME<>'training_execution' OR (p.workload_version>=3 AND EXISTS(
   SELECT 1 FROM ml.training_run r WHERE r.run_id=(to_jsonb(NEW)->>'run_id')::uuid
    AND r.server_instance_id=p.server_instance_id AND r.identity_epoch=p.identity_epoch)));
 IF capacity IS NULL THEN
  RAISE EXCEPTION 'internal_io_budget_unconfigured' USING ERRCODE='55000';
 END IF;
 SELECT (SELECT count(*) FROM vault.ingest_execution WHERE closed_at IS NULL)
  +(SELECT count(*) FROM vault.ingest_cleanup_execution WHERE closed_at IS NULL)
  +(SELECT count(*) FROM vault.provider_maintenance WHERE closed_at IS NULL)
  +(SELECT count(*) FROM library.metadata_execution WHERE closed_at IS NULL)
  +(SELECT count(*) FROM ml.training_execution WHERE closed_at IS NULL) INTO occupied;
 IF occupied>=capacity THEN RAISE EXCEPTION 'internal_io_busy' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER m_training_io_admission BEFORE INSERT ON ml.training_execution
 FOR EACH ROW EXECUTE FUNCTION app_private.admit_internal_io();

CREATE OR REPLACE FUNCTION ml.guard_training_cleanup_claim()
 RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' AND NEW.phase='PENDING' AND EXISTS(SELECT 1 FROM ml.training_run
   WHERE run_id=NEW.run_id AND phase IN ('INVALIDATED','PUBLISHED')) THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND OLD.phase='PENDING' AND NEW.phase='COMPLETE'
   AND NEW.run_id=OLD.run_id AND NEW.requested_at=OLD.requested_at
   AND NEW.completed_at IS NOT NULL AND NEW.completed_at>=NEW.requested_at
   AND EXISTS(SELECT 1 FROM ml.training_execution WHERE run_id=NEW.run_id)
   AND NOT EXISTS(SELECT 1 FROM ml.training_execution WHERE run_id=NEW.run_id
     AND (state<>'CLOSED' OR input_cleanup_sha256 IS NULL)) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'training_cleanup_evidence_required' USING ERRCODE='55000';
END $$;
CREATE FUNCTION app_private.advance_training_cleanup_claim()
 RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 UPDATE ml.training_cleanup_claim SET phase='COMPLETE',completed_at=clock_timestamp()
  WHERE run_id=NEW.run_id AND phase='PENDING'
   AND EXISTS(SELECT 1 FROM ml.training_execution WHERE run_id=NEW.run_id)
   AND NOT EXISTS(SELECT 1 FROM ml.training_execution WHERE run_id=NEW.run_id
     AND (state<>'CLOSED' OR input_cleanup_sha256 IS NULL));
 RETURN NEW;
END $$;
CREATE TRIGGER advance_training_cleanup_claim AFTER INSERT ON ml.training_cleanup_claim
 FOR EACH ROW EXECUTE FUNCTION app_private.advance_training_cleanup_claim();
REVOKE ALL ON TABLE ml.training_execution FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.lock_training_execution_identity() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_training_execution(),
 app_private.authorize_training_execution_transition(uuid,text,text),
 app_private.advance_training_cleanup_claim() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
LOCK TABLE account.internal_io_policy,ml.training_execution IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.training_execution)
  OR EXISTS(SELECT 1 FROM account.internal_io_policy WHERE workload_version=3
    AND active_limit IS NOT NULL) THEN
  RAISE EXCEPTION 'Refusing to discard training execution or measured workload history';
 END IF;
END $$;
DROP TRIGGER m_training_io_admission ON ml.training_execution;
DROP TRIGGER training_execution_guard ON ml.training_execution;
DROP TRIGGER a_training_execution_identity ON ml.training_execution;
DROP TRIGGER advance_training_cleanup_claim ON ml.training_cleanup_claim;
DROP FUNCTION app_private.advance_training_cleanup_claim();
CREATE OR REPLACE FUNCTION ml.guard_training_cleanup_claim() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' AND NEW.phase='PENDING' AND EXISTS(SELECT 1 FROM ml.training_run
   WHERE run_id=NEW.run_id AND phase IN ('INVALIDATED','PUBLISHED')) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'training_cleanup_evidence_required' USING ERRCODE='55000';
END $$;
DROP FUNCTION app_private.protect_training_execution();
DROP FUNCTION app_private.authorize_training_execution_transition(uuid,text,text);
DROP FUNCTION app_private.lock_training_execution_identity();
DROP TABLE ml.training_execution;
ALTER TABLE account.internal_io_policy DROP CONSTRAINT internal_io_workload_version_check;
ALTER TABLE account.internal_io_policy ADD CONSTRAINT internal_io_workload_version_check
 CHECK(workload_version IN (1,2));
CREATE OR REPLACE FUNCTION app_private.admit_internal_io() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE capacity integer; occupied bigint;
BEGIN
 PERFORM pg_advisory_xact_lock(4707761689340236801::bigint);
 SELECT p.active_limit INTO capacity FROM account.internal_io_policy p
 JOIN account.server_instance s ON s.server_instance_id=p.server_instance_id
  AND s.identity_epoch=p.identity_epoch
 JOIN account.resource_quota_policy q ON q.singleton_id=1
  AND q.playback_ceiling<=p.playback_ceiling AND q.transfer_ceiling<=p.transfer_ceiling
 WHERE p.singleton_id=1 AND (TG_TABLE_NAME<>'metadata_execution' OR p.workload_version>=2);
 IF capacity IS NULL THEN
  RAISE EXCEPTION 'internal_io_budget_unconfigured' USING ERRCODE='55000';
 END IF;
 SELECT (SELECT count(*) FROM vault.ingest_execution WHERE closed_at IS NULL)
  +(SELECT count(*) FROM vault.ingest_cleanup_execution WHERE closed_at IS NULL)
  +(SELECT count(*) FROM vault.provider_maintenance WHERE closed_at IS NULL)
  +(SELECT count(*) FROM library.metadata_execution WHERE closed_at IS NULL) INTO occupied;
 IF occupied>=capacity THEN RAISE EXCEPTION 'internal_io_busy' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
""")
