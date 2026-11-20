"""Dormant additive 0103 schema, installed only by an explicit disposable overlay.

Repository admission, authority locks, measured-policy verification and successful
outer COMMIT are still mandatory. These guards cannot mint execution authority.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from .models.native_training_execution import NATIVE_TRAINING_TABLES, NativeTrainingBase

PARENT_REVISION = "0102_native_dataset_export"
PARENT_SOURCE_SHA256 = "8bb0c0935a7f512fd339106a7069f3857f4cdd637ee635489f47c25c9e612d59"

FUNCTION_SQL = r"""
CREATE FUNCTION app_private.native_training_flat_jcs(value jsonb)
RETURNS bytea LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT convert_to('{' || coalesce(string_agg(to_json(key)::text || ':' || value::text,
  ',' ORDER BY key COLLATE "C"),'') || '}','UTF8') FROM jsonb_each(value)
$$;

CREATE FUNCTION app_private.native_training_receipt_document(value jsonb, kind text)
RETURNS bytea LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE result jsonb; item record;
BEGIN
 result := value - ARRAY['receipt_bytes','receipt_sha256','created_at','exit_evidence_sha256'];
 FOR item IN SELECT e.key,e.value FROM jsonb_each(result) AS e
 WHERE e.key LIKE '%\_sha256' ESCAPE '\'
 LOOP
  result := jsonb_set(result,ARRAY[item.key],to_jsonb(substr(item.value #>> '{}',3)));
 END LOOP;
 result := result || jsonb_build_object('kind',kind,'version',1);
 RETURN app_private.native_training_flat_jcs(result);
END $$;

CREATE FUNCTION app_private.guard_native_training_row()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE immutable_old jsonb; immutable_new jsonb; mutable text[];
 target uuid; op ml.native_training_operation%ROWTYPE; allowed boolean:=false;
BEGIN
 IF TG_OP='DELETE' THEN
  IF TG_TABLE_NAME='native_training_consumed_owner' THEN
   SELECT i.operation_id INTO target FROM ml.native_training_consumed_input i WHERE
 i.run_id=OLD.run_id;
   SELECT * INTO op FROM ml.native_training_operation WHERE operation_id=target;
   IF (app_private.privacy_owner_allowed(OLD.user_id) AND op.cancelled_at IS NOT NULL) OR
    (op.state='CLOSED' AND op.retention_until<=clock_timestamp()) THEN
    RETURN OLD;
   END IF;
   RAISE EXCEPTION 'native_training_owner_purge_not_authorized' USING ERRCODE='55000';
  ELSIF TG_TABLE_NAME='native_validation_policy' THEN
   RETURN OLD; -- Exact policy FKs refuse deleting any retained reference.
  ELSIF TG_TABLE_NAME='native_training_replay_interlock' THEN
   IF OLD.window_id IS NULL THEN RETURN OLD; END IF;
   RAISE EXCEPTION 'native_training_interlock_retained' USING ERRCODE='55000';
  END IF;
  target := (to_jsonb(OLD)->>'operation_id')::uuid;
  IF TG_TABLE_NAME='native_training_operation' THEN op:=OLD;
  ELSE SELECT * INTO op FROM ml.native_training_operation WHERE operation_id=target;
  END IF;
  IF op.operation_id IS NULL OR op.state<>'CLOSED' OR op.retention_until>clock_timestamp() THEN
   RAISE EXCEPTION 'native_training_retention_required' USING ERRCODE='55000';
  END IF;
  RETURN OLD;
 END IF;

 IF TG_OP='UPDATE' THEN
  mutable := CASE TG_TABLE_NAME
   WHEN 'native_training_operation' THEN ARRAY['state','work_units_used','deadline_at',
 'training_resource_vector','cancelled_at','restore_revoked_at','terminal_latched_at','closed_at']
   WHEN 'native_validation_stage' THEN ARRAY['state','started_at','heartbeat_at','deadline_at',
 'stopped_at','closed_at','child_pid','process_identity_sha256','exit_kind','exit_code',
 'exit_evidence_sha256','cleanup_evidence_sha256','stop_reason']
   WHEN 'native_training_escrow_reservation' THEN ARRAY['state','root_identity_sha256',
 'scope_identity_sha256','guardian_identity_sha256','control_identity_sha256','held_at',
 'releasing_at','closed_at','release_decision_id','release_ack_sha256','cleanup_evidence_sha256']
   WHEN 'native_training_source_window' THEN ARRAY['state','entered_at','terminal_at',
 'terminal_receipt_sha256']
   WHEN 'native_training_replay_interlock' THEN ARRAY['operation_id','stage_id','window_id',
 'identity_epoch','token_sha256','changed_at']
   ELSE ARRAY[]::text[] END;
  immutable_old:=to_jsonb(OLD)-mutable; immutable_new:=to_jsonb(NEW)-mutable;
  IF immutable_old IS DISTINCT FROM immutable_new THEN
   RAISE EXCEPTION 'native_training_binding_immutable' USING ERRCODE='55000';
  END IF;
  IF TG_TABLE_NAME='native_training_operation' THEN
   allowed := NEW.state=OLD.state OR
    (OLD.state='PREP' AND NEW.state IN ('TRAIN','CLEANUP')) OR
    (OLD.state='TRAIN' AND NEW.state IN ('FINAL','CLEANUP')) OR
    (OLD.state='FINAL' AND NEW.state='CLEANUP') OR
    (OLD.state='CLEANUP' AND NEW.state='CLOSED');
   IF NOT allowed OR NEW.work_units_used<OLD.work_units_used OR NEW.deadline_at>OLD.deadline_at OR
    (OLD.cancelled_at IS NOT NULL AND NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at) OR
    (OLD.restore_revoked_at IS NOT NULL AND NEW.restore_revoked_at IS DISTINCT FROM
 OLD.restore_revoked_at) OR
    (OLD.terminal_latched_at IS NOT NULL AND NEW.terminal_latched_at IS DISTINCT FROM
 OLD.terminal_latched_at) OR
    (OLD.training_resource_vector IS NOT NULL AND NEW.training_resource_vector IS DISTINCT FROM
 OLD.training_resource_vector) OR
    (OLD.closed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
    RAISE EXCEPTION 'native_training_operation_transition' USING ERRCODE='55000'; END IF;
  ELSIF TG_TABLE_NAME='native_validation_stage' THEN
   allowed := NEW.state=OLD.state OR (OLD.state='PREPARED' AND NEW.state IN ('RUNNING',
 'STOPPING')) OR
    (OLD.state='RUNNING' AND NEW.state='STOPPING') OR (OLD.state='STOPPING' AND NEW.state='CLOSED');
   IF NOT allowed OR NEW.deadline_at>OLD.deadline_at OR
    (OLD.state='CLOSED' AND NEW IS DISTINCT FROM OLD) OR
    (OLD.state='STOPPING' AND (NEW.heartbeat_at,NEW.deadline_at) IS DISTINCT FROM
 (OLD.heartbeat_at,OLD.deadline_at)) OR
    (OLD.state='STOPPING' AND (NEW.child_pid,NEW.process_identity_sha256,NEW.started_at)
     IS DISTINCT FROM (OLD.child_pid,OLD.process_identity_sha256,OLD.started_at)) OR
    (OLD.stopped_at IS NOT NULL AND NEW.stopped_at IS DISTINCT FROM OLD.stopped_at) OR
    (OLD.cleanup_evidence_sha256 IS NOT NULL AND NEW.cleanup_evidence_sha256 IS DISTINCT FROM
     OLD.cleanup_evidence_sha256) OR
    (OLD.child_pid IS NOT NULL AND (NEW.child_pid,NEW.process_identity_sha256,NEW.started_at)
     IS DISTINCT FROM (OLD.child_pid,OLD.process_identity_sha256,OLD.started_at)) OR
    (OLD.exit_evidence_sha256 IS NOT NULL AND (NEW.exit_kind,NEW.exit_code,NEW.exit_evidence_sha256)
     IS DISTINCT FROM (OLD.exit_kind,OLD.exit_code,OLD.exit_evidence_sha256)) OR
    (OLD.heartbeat_at IS NOT NULL AND NEW.heartbeat_at<OLD.heartbeat_at) THEN
    RAISE EXCEPTION 'native_training_stage_transition' USING ERRCODE='55000'; END IF;
   IF (NEW.heartbeat_at,NEW.deadline_at) IS DISTINCT FROM (OLD.heartbeat_at,OLD.deadline_at)
    AND OLD.deadline_at<=clock_timestamp() THEN
    RAISE EXCEPTION 'native_training_stage_expired' USING ERRCODE='55000'; END IF;
  ELSIF TG_TABLE_NAME='native_training_source_window' THEN
   allowed := NEW.state=OLD.state OR (OLD.state='REGISTERED' AND NEW.state IN ('ACTIVE',
 'REVOKED')) OR
    (OLD.state='ACTIVE' AND NEW.state IN ('CLOSED','REVOKED'));
   IF NOT allowed OR (OLD.state IN ('CLOSED','REVOKED') AND NEW IS DISTINCT FROM OLD) OR
    (OLD.entered_at IS NOT NULL AND NEW.entered_at IS DISTINCT FROM OLD.entered_at) THEN
    RAISE EXCEPTION 'native_training_window_transition' USING ERRCODE='55000'; END IF;
  ELSIF TG_TABLE_NAME='native_training_escrow_reservation' THEN
   allowed := NEW.state=OLD.state OR (OLD.state='RESERVED' AND NEW.state IN ('HELD','RELEASING')) OR
    (OLD.state='HELD' AND NEW.state='RELEASING') OR (OLD.state='RELEASING' AND NEW.state='CLOSED');
   IF NOT allowed OR (OLD.state='CLOSED' AND NEW IS DISTINCT FROM OLD) OR
    (OLD.root_identity_sha256 IS NOT NULL AND NEW.root_identity_sha256 IS DISTINCT FROM
 OLD.root_identity_sha256) OR
    (OLD.scope_identity_sha256 IS NOT NULL AND NEW.scope_identity_sha256 IS DISTINCT FROM
 OLD.scope_identity_sha256) OR
    (OLD.guardian_identity_sha256 IS NOT NULL AND NEW.guardian_identity_sha256 IS DISTINCT FROM
 OLD.guardian_identity_sha256) OR
    (OLD.control_identity_sha256 IS NOT NULL AND NEW.control_identity_sha256 IS DISTINCT FROM
 OLD.control_identity_sha256) OR
    (OLD.release_decision_id IS NOT NULL AND NEW.release_decision_id IS DISTINCT FROM
 OLD.release_decision_id) THEN
    RAISE EXCEPTION 'native_training_escrow_transition' USING ERRCODE='55000'; END IF;
  ELSIF TG_TABLE_NAME='native_training_replay_interlock' THEN
   IF OLD.window_id IS NOT NULL AND NEW.window_id IS NOT NULL AND
    (NEW.operation_id,NEW.stage_id,NEW.window_id,NEW.identity_epoch,NEW.token_sha256)
     IS DISTINCT FROM (OLD.operation_id,OLD.stage_id,OLD.window_id,OLD.identity_epoch,
 OLD.token_sha256) THEN
    RAISE EXCEPTION 'native_training_interlock_replace' USING ERRCODE='55000'; END IF;
   IF OLD.window_id IS NOT NULL AND NEW.window_id IS NULL AND EXISTS(
    SELECT 1 FROM ml.native_training_source_window WHERE window_id=OLD.window_id AND terminal_at
 IS NULL) THEN
    RAISE EXCEPTION 'native_training_window_not_terminal' USING ERRCODE='55000'; END IF;
  ELSIF NEW IS DISTINCT FROM OLD THEN
   RAISE EXCEPTION 'native_training_fact_immutable' USING ERRCODE='55000';
  END IF;
 ELSE
  IF TG_TABLE_NAME IN ('native_training_consumed_input','native_training_consumed_owner') THEN
   IF NOT EXISTS(SELECT 1 FROM ml.training_run WHERE run_id=NEW.run_id
    AND registration_xid=pg_current_xact_id()::text) THEN
    RAISE EXCEPTION 'native_training_transfer_registration_required' USING ERRCODE='55000';
   END IF;
  END IF;
  IF (TG_TABLE_NAME='native_training_operation' AND to_jsonb(NEW)->>'state'<>'PREP') OR
   (TG_TABLE_NAME='native_validation_stage' AND to_jsonb(NEW)->>'state'<>'PREPARED') OR
   (TG_TABLE_NAME='native_training_escrow_reservation' AND to_jsonb(NEW)->>'state'<>'RESERVED') OR
   (TG_TABLE_NAME='native_training_source_window' AND to_jsonb(NEW)->>'state'<>'REGISTERED') THEN
   RAISE EXCEPTION 'native_training_initial_state' USING ERRCODE='55000'; END IF;
  IF TG_TABLE_NAME='native_training_source_window' THEN
   IF NEW.registration_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
    RAISE EXCEPTION 'native_training_window_registration_transaction' USING ERRCODE='55000';
   END IF;
  END IF;
 END IF;
 IF TG_TABLE_NAME='native_training_operation' AND
  (to_jsonb(NEW)->>'terminal_latched_at') IS NOT NULL AND
  (to_jsonb(NEW)->>'state') NOT IN ('CLEANUP','CLOSED') THEN
  RAISE EXCEPTION 'native_training_terminal_dispatch_forbidden' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='native_training_escrow_reservation' THEN
  IF NEW.state IN ('RELEASING','CLOSED') AND NOT EXISTS(
   SELECT 1 FROM ml.native_training_recovery_decision d
   JOIN ml.native_training_operation o ON o.operation_id=d.operation_id
   WHERE d.decision_id=NEW.release_decision_id AND d.operation_id=NEW.operation_id
    AND d.escrow_generation=NEW.escrow_generation AND o.state IN ('CLEANUP','CLOSED')
    AND o.terminal_latched_at IS NOT NULL) THEN
   RAISE EXCEPTION 'native_training_operation_release_not_fenced' USING ERRCODE='55000'; END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION app_private.validate_native_training_consumed()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE i ml.native_training_consumed_input%ROWTYPE; f ml.native_training_consumed_final%ROWTYPE;
 r ml.training_run%ROWTYPE; op ml.native_training_operation%ROWTYPE;
 stage ml.native_validation_stage%ROWTYPE; member_bytes bytea; member_body text;
BEGIN
 SELECT * INTO i FROM ml.native_training_consumed_input WHERE run_id=NEW.run_id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO op FROM ml.native_training_operation WHERE operation_id=i.operation_id;
 SELECT * INTO r FROM ml.training_run WHERE run_id=i.run_id;
 IF (i.server_instance_id,i.identity_epoch,i.execution_id,i.selection_sha256) IS DISTINCT FROM
  (op.server_instance_id,op.identity_epoch,op.execution_id,op.selection_sha256) OR
  (r.server_instance_id,r.identity_epoch,r.source_sha256,r.dataset_sha256,r.request_sha256,
 r.lineage_key_id)
   IS DISTINCT FROM (i.server_instance_id,i.identity_epoch,i.source_manifest_sha256,
    i.authorized_dataset_sha256,i.registration_sha256,i.training_lineage_key_id) OR
  i.receipt_bytes<>app_private.native_training_receipt_document(to_jsonb(i),
 'SONA_NATIVE_CONSUMED_INPUT_RECEIPT') THEN
  RAISE EXCEPTION 'native_training_consumed_input_mismatch' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='native_training_consumed_final' THEN
  SELECT * INTO f FROM ml.native_training_consumed_final WHERE run_id=NEW.run_id;
  SELECT * INTO stage FROM ml.native_validation_stage WHERE stage_id=f.final_stage_id;
  IF (f.operation_id,f.execution_id,f.server_instance_id,f.identity_epoch,
 f.consumed_input_receipt_sha256)
   IS DISTINCT FROM (i.operation_id,i.execution_id,i.server_instance_id,i.identity_epoch,
 i.receipt_sha256) OR
   stage.operation_id<>i.operation_id OR stage.phase<>'FINAL' OR stage.exit_code IS DISTINCT FROM
 0 OR
   stage.exit_evidence_sha256 IS DISTINCT FROM f.exit_evidence_sha256 OR
   f.receipt_bytes<>app_private.native_training_receipt_document(to_jsonb(f),
 'SONA_NATIVE_CONSUMED_FINAL_RECEIPT') OR
   NOT EXISTS(SELECT 1 FROM ml.native_training_final_attempt_receipt a WHERE
 a.stage_id=f.final_stage_id
    AND a.operation_id=f.operation_id AND a.receipt_sha256=f.final_seal_receipt_sha256
    AND a.candidate_seal_sha256=f.candidate_seal_sha256 AND
 a.consumed_input_receipt_sha256=i.receipt_sha256) THEN
   RAISE EXCEPTION 'native_training_consumed_final_mismatch' USING ERRCODE='55000'; END IF;
 END IF;
 IF (SELECT count(*) FROM ml.native_training_consumed_owner WHERE
 run_id=i.run_id)<>i.source_owner_count OR
  (SELECT count(*) FROM ml.training_participant WHERE run_id=i.run_id)<>i.registered_member_count OR
  EXISTS(SELECT 1 FROM ml.training_participant p FULL JOIN ml.native_training_consumed_owner o
   ON p.run_id=o.run_id AND p.user_id=o.user_id WHERE coalesce(p.run_id,o.run_id)=i.run_id
   AND (p.user_id IS NULL OR o.user_id IS NULL OR p.owner_tag<>o.training_owner_tag
    OR p.consent_revision<>o.training_consent_revision)) THEN
  RAISE EXCEPTION 'native_training_consumed_members_incomplete' USING ERRCODE='55000'; END IF;
 SELECT string_agg('{"consent_revision":'||consent_revision::text||',"owner_tag":"'||
 encode(owner_tag,'hex')||
  '","user_id":"'||user_id::text||'"}',',' ORDER BY user_id) INTO member_body
  FROM ml.training_participant WHERE run_id=i.run_id;
 member_bytes:=convert_to('{"identity_epoch":'||i.identity_epoch::text||
  ',"kind":"SONA_NATIVE_REGISTERED_MEMBERS","lineage_key_id":'||
 to_json(i.training_lineage_key_id)::text||
  ',"members":['||member_body||'],"run_id":"'||i.run_id::text||
  '","server_instance_id":"'||i.server_instance_id::text||'","version":1}','UTF8');
 IF sha256(member_bytes)<>i.registered_member_binding_sha256 THEN
  RAISE EXCEPTION 'native_training_consumed_member_root' USING ERRCODE='55000'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION app_private.validate_native_training_ledger()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE stage ml.native_validation_stage%ROWTYPE; op ml.native_training_operation%ROWTYPE;
 policy ml.native_validation_policy%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='native_validation_stage' THEN
  SELECT * INTO op FROM ml.native_training_operation WHERE operation_id=NEW.operation_id;
  SELECT * INTO policy FROM ml.native_validation_policy WHERE policy_sha256=NEW.policy_sha256;
  IF NEW.policy_sha256<>op.policy_sha256 OR NEW.policy_revision<>policy.revision OR
   NEW.attempt>(CASE WHEN NEW.phase='PREP' THEN op.max_prep_attempts ELSE op.max_final_attempts END)
 OR
   NEW.deadline_at>op.deadline_at OR NEW.deadline_at>NEW.created_at+
    ((NEW.limits->>'maximum_wall_ms')::bigint * interval '1 millisecond') OR
   NEW.limits<>(CASE WHEN NEW.phase='PREP' THEN policy.prep_limits
    ELSE policy.final_limits END) THEN
   RAISE EXCEPTION 'native_training_stage_policy_mismatch' USING ERRCODE='55000'; END IF;
  IF TG_OP='INSERT' AND (op.cancelled_at IS NOT NULL OR op.restore_revoked_at IS NOT NULL OR
 op.terminal_latched_at IS NOT NULL OR
   op.state<>NEW.phase OR op.deadline_at<=clock_timestamp()) THEN
   RAISE EXCEPTION 'native_training_stage_operation_unavailable' USING ERRCODE='55000'; END IF;
  IF TG_OP='INSERT' AND NEW.phase='FINAL' AND NOT EXISTS(
   SELECT 1 FROM ml.training_execution e WHERE e.execution_id=NEW.training_execution_id
    AND e.execution_id=op.execution_id AND e.run_id=op.run_id AND e.state='STOPPING'
    AND e.closed_at IS NULL AND e.retain_checkpoint IS NULL) THEN
   RAISE EXCEPTION 'native_training_final_execution_unavailable' USING ERRCODE='55000'; END IF;
  IF NEW.state='CLOSED' AND EXISTS(SELECT 1 FROM ml.native_training_source_window w
   WHERE w.stage_id=NEW.stage_id AND w.terminal_at IS NULL) THEN
   RAISE EXCEPTION 'native_training_stage_window_retained' USING ERRCODE='55000'; END IF;
 ELSIF TG_TABLE_NAME='native_training_prep_acceptance' THEN
  SELECT * INTO stage FROM ml.native_validation_stage WHERE stage_id=NEW.stage_id;
  IF stage.operation_id<>NEW.operation_id OR stage.phase<>'PREP' OR stage.exit_code IS DISTINCT
 FROM 0 OR
   stage.exit_evidence_sha256 IS DISTINCT FROM NEW.exit_evidence_sha256 THEN
   RAISE EXCEPTION 'native_training_prep_exit_unconfirmed' USING ERRCODE='55000'; END IF;
 ELSIF TG_TABLE_NAME='native_training_operation' AND NEW.state='CLOSED' THEN
  IF EXISTS(SELECT 1 FROM ml.native_validation_stage WHERE operation_id=NEW.operation_id AND
 closed_at IS NULL) OR
   EXISTS(SELECT 1 FROM ml.native_training_escrow_reservation WHERE operation_id=NEW.operation_id
 AND closed_at IS NULL) OR
   EXISTS(SELECT 1 FROM ml.training_execution WHERE execution_id=NEW.execution_id AND closed_at IS
 NULL) THEN
   RAISE EXCEPTION 'native_training_operation_cleanup_retained' USING ERRCODE='55000'; END IF;
 END IF;
 RETURN NEW;
END $$;
"""


def install(connection: Connection) -> None:
    """Install only the new private metadata and its own guards in the caller transaction."""
    NativeTrainingBase.metadata.create_all(
        connection, tables=list(NATIVE_TRAINING_TABLES), checkfirst=False
    )
    # Function bodies contain literal JSON colons and PL/pgSQL percent tokens.
    # Bypass both TextClause bind parsing and DBAPI parameter interpolation.
    connection.exec_driver_sql(FUNCTION_SQL, execution_options={"no_parameters": True})
    for table in NATIVE_TRAINING_TABLES:
        name = table.name
        connection.execute(
            text(
                f"CREATE TRIGGER guard_{name} BEFORE INSERT OR UPDATE OR DELETE ON ml.{name} "
                "FOR EACH ROW EXECUTE FUNCTION app_private.guard_native_training_row()"
            )
        )
        connection.execute(text(f"REVOKE ALL ON ml.{name} FROM PUBLIC"))
    for name in (
        "native_validation_stage",
        "native_training_prep_acceptance",
        "native_training_operation",
    ):
        connection.execute(
            text(
                f"CREATE TRIGGER validate_{name} BEFORE INSERT OR UPDATE ON ml.{name} "
                "FOR EACH ROW EXECUTE FUNCTION app_private.validate_native_training_ledger()"
            )
        )
    for name in (
        "native_training_consumed_input",
        "native_training_consumed_owner",
        "native_training_consumed_final",
    ):
        connection.execute(
            text(
                f"CREATE CONSTRAINT TRIGGER validate_{name} AFTER INSERT ON ml.{name} "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION app_private.validate_native_training_consumed()"
            )
        )
    connection.execute(
        text(
            "REVOKE ALL ON FUNCTION app_private.native_training_flat_jcs(jsonb), "
            "app_private.native_training_receipt_document(jsonb,text), "
            "app_private.guard_native_training_row(), "
            "app_private.validate_native_training_consumed(), "
            "app_private.validate_native_training_ledger() FROM PUBLIC"
        )
    )


def remove(connection: Connection) -> None:
    """Refuse a destructive downgrade while any staged native record remains."""
    # Hold every relation against concurrent inserts through the caller's commit.
    # Checking first and acquiring DROP's locks later could discard newly added rows.
    relations = ", ".join(f"ml.{table.name}" for table in NATIVE_TRAINING_TABLES)
    connection.execute(text(f"LOCK TABLE {relations} IN ACCESS EXCLUSIVE MODE"))
    for table in NATIVE_TRAINING_TABLES:
        if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM ml.{table.name})")):
            raise RuntimeError("native_training_downgrade_records_retained")
    NativeTrainingBase.metadata.drop_all(
        connection, tables=list(NATIVE_TRAINING_TABLES), checkfirst=False
    )
    for signature in (
        "app_private.validate_native_training_ledger()",
        "app_private.validate_native_training_consumed()",
        "app_private.guard_native_training_row()",
        "app_private.native_training_receipt_document(jsonb,text)",
        "app_private.native_training_flat_jcs(jsonb)",
    ):
        connection.execute(text(f"DROP FUNCTION {signature}"))
