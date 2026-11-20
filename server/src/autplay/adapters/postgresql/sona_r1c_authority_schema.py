# ruff: noqa: E501
"""Staged C0105 constraints and set-based revocation. Import installs nothing."""

from __future__ import annotations

from sqlalchemy import Connection, text

from .models.sona_r1c_authority import R1C_AUTHORITY_TABLES

R1C_PRIVATE_TABLES = ("app_private.sona_r1c_context", "app_private.sona_r1c_restore_context")
R1C_FUNCTIONS = (
    "r1c_jcs(jsonb)",
    "r1c_head_document(ml.sona_r1c_training_baseline_member)",
    "r1c_baseline_document(uuid)",
    "r1c_assert_baseline(uuid)",
    "r1c_assert_negative(uuid,bigint,text,uuid)",
    "r1c_check_graph()",
    "r1c_guard()",
    "r1c_context_empty()",
    "r1c_check_native_input()",
    "r1c_lock_fanout(uuid,bigint,text,bytea)",
    "r1c_lock_owner_personal(uuid)",
    "r1c_lock_legacy_owner_purge(uuid)",
    "r1c_release_restore(uuid,bigint,text)",
    "r1c_apply_negative(uuid,bigint,text,uuid,bytea,uuid,bigint,text,bytea,bytea,bytea,bigint,bytea)",
    "r1c_purge_owner(uuid,uuid)",
)

R1C_AUTHORITY_SQL = r"""
CREATE TABLE app_private.sona_r1c_context(
 transaction_id text NOT NULL, owner_user_id uuid NOT NULL, request_id uuid NOT NULL,
 run_id uuid, mode text NOT NULL CHECK(mode='OWNER_PURGE'),
 UNIQUE NULLS NOT DISTINCT(transaction_id,owner_user_id,run_id)
);
REVOKE ALL ON app_private.sona_r1c_context FROM PUBLIC;
CREATE TABLE app_private.sona_r1c_restore_context(
 transaction_id text PRIMARY KEY,server_instance_id uuid NOT NULL,
 identity_epoch bigint NOT NULL,serving_ledger_key_id text NOT NULL
);
REVOKE ALL ON app_private.sona_r1c_restore_context FROM PUBLIC;

CREATE FUNCTION app_private.r1c_jcs(value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE result text; kind text:=jsonb_typeof(value);
BEGIN
 IF kind='object' THEN
  SELECT '{'||coalesce(string_agg(to_jsonb(key)::text||':'||app_private.r1c_jcs(v),','
   ORDER BY key COLLATE "C"),'')||'}' INTO result FROM jsonb_each(value) e(key,v);
 ELSIF kind='array' THEN
  SELECT '['||coalesce(string_agg(app_private.r1c_jcs(v),',' ORDER BY n),'')||']'
   INTO result FROM jsonb_array_elements(value) WITH ORDINALITY e(v,n);
 ELSIF kind='string' THEN
  IF octet_length(value#>>'{}')<>length(value#>>'{}') THEN
   RAISE EXCEPTION 'R1C_ASCII_REQUIRED' USING ERRCODE='23514'; END IF;
  result:=value::text;
 ELSIF kind='number' THEN
  IF value::text !~ '^(0|[1-9][0-9]{0,15})$' OR (value::text)::numeric>9007199254740991 THEN
   RAISE EXCEPTION 'R1C_INTEGER_REQUIRED' USING ERRCODE='23514'; END IF;
  result:=value::text;
 ELSIF kind IN ('null','boolean') THEN result:=value::text;
 ELSE RAISE EXCEPTION 'R1C_JSON_REQUIRED' USING ERRCODE='23514'; END IF;
 RETURN result;
END $$;

CREATE FUNCTION app_private.r1c_head_document(m ml.sona_r1c_training_baseline_member)
RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT CASE WHEN m.head_operation_id IS NULL THEN 'null'::jsonb ELSE jsonb_build_object(
 'operation_id',m.head_operation_id::text,'revision',m.head_revision,'decision',m.head_decision,
 'actor_tag',encode(m.head_actor_tag,'hex'),'request_sha256',encode(m.head_request_sha256,'hex'),
 'previous_policy_sha256',encode(m.head_previous_policy_sha256,'hex'),
 'changed_at_us',m.head_changed_at_us) END
$$;

CREATE FUNCTION app_private.r1c_baseline_document(rid uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('schema_version',1,'run_id',b.run_id::text,
 'server_instance_id',b.server_instance_id::text,'identity_epoch',b.identity_epoch,
 'f_input_receipt_sha256',encode(b.f_input_receipt_sha256,'hex'),
 'registration_sha256',encode(b.registration_sha256,'hex'),
 'training_input_binding_sha256',encode(b.training_input_binding_sha256,'hex'),
 'full_owner_binding_sha256',encode(b.full_owner_binding_sha256,'hex'),
 'source_owner_set_sha256',encode(b.source_owner_set_sha256,'hex'),
 'source_lineage_key_id',b.source_lineage_key_id,'run_lineage_key_id',b.run_lineage_key_id,
 'serving_ledger_key_id',b.serving_ledger_key_id,'member_count',b.member_count,
 'members',coalesce((SELECT jsonb_agg(jsonb_build_object(
  'training_owner_tag',encode(m.training_owner_tag,'hex'),
  'training_consent_revision',m.training_consent_revision,
  'source_owner_tag',encode(m.source_owner_tag,'hex'),
  'serving_owner_tag',encode(m.serving_owner_tag,'hex'),
  'registered_dependency_generation',m.registered_dependency_generation,
  'head',app_private.r1c_head_document(m),
  'last_applied_negative',CASE WHEN n.operation_id IS NULL THEN 'null'::jsonb ELSE
   jsonb_build_object('operation_id',n.operation_id::text,'revision',n.revision,
    'request_sha256',encode(n.request_sha256,'hex'),'head_sha256',encode(n.head_sha256,'hex'),
    'covered_dependency_generation',n.covered_dependency_generation) END)
  ORDER BY m.serving_owner_tag) FROM ml.sona_r1c_training_baseline_member m
  LEFT JOIN ml.sona_r1c_negative_applied n ON n.server_instance_id=m.server_instance_id
   AND n.identity_epoch=m.identity_epoch AND n.serving_ledger_key_id=m.serving_ledger_key_id
   AND n.operation_id=m.last_applied_negative_operation_id WHERE m.run_id=b.run_id),'[]'::jsonb))
 FROM ml.sona_r1c_training_baseline b WHERE b.run_id=rid
$$;

CREATE FUNCTION app_private.r1c_assert_baseline(rid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE b ml.sona_r1c_training_baseline; f ml.native_training_consumed_input;
 r ml.training_run; digest bytea;
BEGIN
 SELECT * INTO b FROM ml.sona_r1c_training_baseline WHERE run_id=rid;
 IF NOT FOUND THEN
  IF EXISTS(SELECT 1 FROM ml.sona_r1c_training_baseline_member WHERE run_id=rid)
   OR EXISTS(SELECT 1 FROM ml.sona_r1c_run_authority WHERE run_id=rid) THEN
   RAISE EXCEPTION 'R1C_ORPHAN_BASELINE' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 SELECT * INTO STRICT r FROM ml.training_run WHERE run_id=rid;
 SELECT * INTO STRICT f FROM ml.native_training_consumed_input WHERE run_id=rid;
 IF b.server_instance_id<>r.server_instance_id OR b.identity_epoch<>r.identity_epoch
  OR b.f_operation_id<>f.operation_id OR b.f_execution_id<>f.execution_id
  OR b.f_input_receipt_sha256<>f.receipt_sha256 OR b.f_input_schema_version<>1
  OR b.registration_sha256<>r.request_sha256 OR b.registration_sha256<>f.registration_sha256
  OR b.training_input_binding_sha256<>f.training_input_binding_sha256
  OR b.full_owner_binding_sha256<>f.full_owner_binding_sha256
  OR b.source_owner_set_sha256<>f.source_owner_set_sha256
  OR b.source_lineage_key_id<>f.source_owner_key_id
  OR b.run_lineage_key_id<>f.training_lineage_key_id
  OR b.source_owner_count<>f.source_owner_count OR b.member_count<>f.registered_member_count
  OR b.member_count<>r.participant_count
  OR b.admission_xid<>r.registration_xid
  OR EXISTS(SELECT 1 FROM ml.sona_r1c_terminal_deny WHERE run_id=rid)
  OR (SELECT count(*) FROM ml.sona_r1c_training_baseline_member WHERE run_id=rid)<>b.member_count
  OR EXISTS(
   SELECT 1 FROM ml.native_training_consumed_owner o FULL JOIN
    ml.sona_r1c_training_baseline_member m ON m.run_id=o.run_id AND m.owner_user_id=o.user_id
   WHERE coalesce(o.run_id,m.run_id)=rid AND (o.run_id IS NULL OR m.run_id IS NULL
    OR o.source_owner_tag<>m.source_owner_tag OR o.training_owner_tag<>m.training_owner_tag
    OR o.training_consent_revision<>m.training_consent_revision))
  OR NOT EXISTS(SELECT 1 FROM ml.sona_r1c_run_authority a
   WHERE a.run_id=rid AND a.baseline_sha256=b.baseline_sha256)
  OR EXISTS(SELECT 1 FROM ml.sona_r1c_cohort c JOIN ml.sona_r1c_run_authority a USING(run_id)
   WHERE c.run_id=rid AND a.revoked AND NOT c.killed) THEN
  RAISE EXCEPTION 'R1C_BASELINE_BINDING' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM ml.sona_r1c_training_baseline_member m
  LEFT JOIN ml.sona_r1c_negative_applied n ON n.server_instance_id=m.server_instance_id
   AND n.identity_epoch=m.identity_epoch AND n.serving_ledger_key_id=m.serving_ledger_key_id
   AND n.operation_id=m.last_applied_negative_operation_id
  JOIN ml.sona_r1c_participant_dependency d ON d.server_instance_id=m.server_instance_id
   AND d.identity_epoch=m.identity_epoch AND d.serving_ledger_key_id=m.serving_ledger_key_id
   AND d.serving_owner_tag=m.serving_owner_tag
  WHERE m.run_id=rid AND (d.owner_user_id<>m.owner_user_id
   OR d.dependency_generation<m.registered_dependency_generation
   OR (n.operation_id IS NOT NULL AND (n.owner_user_id<>m.owner_user_id
    OR n.serving_owner_tag<>m.serving_owner_tag
    OR n.covered_dependency_generation>=m.registered_dependency_generation))
   OR m.head_sha256<>sha256(convert_to('autplay.sona.r1c-serving-head-baseline.v1','UTF8')||decode('00','hex')||
    convert_to(app_private.r1c_jcs(jsonb_build_object('schema_version',1,
     'serving_ledger_key_id',m.serving_ledger_key_id,'serving_owner_tag',encode(m.serving_owner_tag,'hex'),
     'purpose','SONA_R1C_DESCENDANT_MODEL_SERVING_V1','head',app_private.r1c_head_document(m))),'UTF8')))) THEN
  RAISE EXCEPTION 'R1C_MEMBER_BINDING' USING ERRCODE='23514'; END IF;
 digest:=sha256(convert_to('autplay.sona.r1c-training-serving-baseline.v1','UTF8')||decode('00','hex')||
  convert_to(app_private.r1c_jcs(app_private.r1c_baseline_document(rid)),'UTF8'));
 IF digest<>b.baseline_sha256 THEN
  RAISE EXCEPTION 'R1C_BASELINE_HASH' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.r1c_assert_negative(sid uuid,epoch bigint,kid text,op uuid)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE n ml.sona_r1c_negative_applied; actual_count bigint; actual_hash bytea;
BEGIN
 SELECT * INTO n FROM ml.sona_r1c_negative_applied WHERE server_instance_id=sid
  AND identity_epoch=epoch AND serving_ledger_key_id=kid AND operation_id=op;
 IF NOT FOUND THEN RETURN; END IF;
 IF n.head_sha256<>sha256(convert_to('autplay.sona.r1c-serving-head-baseline.v1','UTF8')||decode('00','hex')||
  convert_to(app_private.r1c_jcs(jsonb_build_object('schema_version',1,
   'serving_ledger_key_id',kid,'serving_owner_tag',encode(n.serving_owner_tag,'hex'),
   'purpose','SONA_R1C_DESCENDANT_MODEL_SERVING_V1','head',jsonb_build_object(
    'operation_id',n.operation_id::text,'revision',n.revision,'decision',n.decision,
    'actor_tag',encode(n.actor_tag,'hex'),'request_sha256',encode(n.request_sha256,'hex'),
    'previous_policy_sha256',encode(n.previous_policy_sha256,'hex'),'changed_at_us',n.changed_at_us))),'UTF8')) THEN
  RAISE EXCEPTION 'R1C_NEGATIVE_HEAD_HASH' USING ERRCODE='23514'; END IF;
 SELECT count(*),sha256(convert_to(app_private.r1c_jcs(coalesce(jsonb_agg(run_id::text ORDER BY run_id),'[]'::jsonb)),'UTF8'))
  INTO actual_count,actual_hash FROM (
   SELECT run_id,registered_dependency_generation FROM ml.sona_r1c_run_revocation
    WHERE server_instance_id=sid AND identity_epoch=epoch AND serving_ledger_key_id=kid AND negative_operation_id=op
   UNION ALL
   SELECT run_id,registered_dependency_generation FROM ml.sona_r1c_terminal_negative_coverage
    WHERE server_instance_id=sid AND identity_epoch=epoch AND serving_ledger_key_id=kid AND negative_operation_id=op
  ) covered;
 IF actual_count<>n.affected_run_count OR actual_hash<>n.affected_run_set_sha256
  OR EXISTS(SELECT 1 FROM ml.sona_r1c_training_baseline_member m
   WHERE m.server_instance_id=sid AND m.identity_epoch=epoch AND m.serving_ledger_key_id=kid
    AND m.serving_owner_tag=n.serving_owner_tag
    AND m.registered_dependency_generation<=n.covered_dependency_generation
    AND NOT EXISTS(SELECT 1 FROM ml.sona_r1c_run_revocation v WHERE v.run_id=m.run_id
     AND v.server_instance_id=sid AND v.identity_epoch=epoch AND v.serving_ledger_key_id=kid
     AND v.negative_operation_id=op AND v.registered_dependency_generation=m.registered_dependency_generation))
  OR EXISTS(SELECT 1 FROM ml.sona_r1c_run_revocation v JOIN ml.sona_r1c_run_authority a USING(run_id)
   WHERE v.server_instance_id=sid AND v.identity_epoch=epoch AND v.serving_ledger_key_id=kid
    AND v.negative_operation_id=op AND (NOT a.revoked
     OR v.registered_dependency_generation>n.covered_dependency_generation
     OR NOT EXISTS(SELECT 1 FROM ml.sona_r1c_training_baseline_member m
      WHERE m.run_id=v.run_id AND m.server_instance_id=sid AND m.identity_epoch=epoch
       AND m.serving_ledger_key_id=kid AND m.serving_owner_tag=n.serving_owner_tag
       AND m.owner_user_id=n.owner_user_id
       AND m.registered_dependency_generation=v.registered_dependency_generation)))
  OR EXISTS(SELECT 1 FROM ml.sona_r1c_terminal_negative_coverage c
   JOIN ml.sona_r1c_terminal_deny t USING(run_id)
   WHERE c.server_instance_id=sid AND c.identity_epoch=epoch AND c.serving_ledger_key_id=kid
    AND c.negative_operation_id=op AND (c.terminal_sha256<>t.transition_sha256
     OR c.registered_dependency_generation>n.covered_dependency_generation)) THEN
  RAISE EXCEPTION 'R1C_NEGATIVE_WHOLE_SET' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.r1c_check_graph() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE value jsonb:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 rid uuid; n record;
BEGIN
 rid:=(value->>'run_id')::uuid;
 IF TG_TABLE_NAME='sona_r1c_terminal_deny' THEN
  IF (value->>'transition_sha256')::bytea<>sha256(convert_to('autplay.sona.r1c-terminal-deny.v1','UTF8')||
   decode('00','hex')||convert_to(app_private.r1c_jcs((value-'transition_sha256')||jsonb_build_object('schema_version',1)),'UTF8')) THEN
   RAISE EXCEPTION 'R1C_TERMINAL_DENY_HASH' USING ERRCODE='23514'; END IF;
 END IF;
 IF rid IS NOT NULL THEN PERFORM app_private.r1c_assert_baseline(rid); END IF;
 IF TG_TABLE_NAME IN ('sona_r1c_negative_applied','sona_r1c_run_revocation','sona_r1c_terminal_negative_coverage') THEN
  PERFORM app_private.r1c_assert_negative((value->>'server_instance_id')::uuid,
   (value->>'identity_epoch')::bigint,value->>'serving_ledger_key_id',
   coalesce(value->>'negative_operation_id',value->>'operation_id')::uuid);
 END IF;
 IF TG_TABLE_NAME='sona_r1c_run_authority' THEN
  IF EXISTS(SELECT 1 FROM ml.sona_r1c_run_authority a WHERE a.run_id=rid
   AND a.revocation_generation<>(SELECT count(*) FROM ml.sona_r1c_run_revocation v WHERE v.run_id=rid)) THEN
   RAISE EXCEPTION 'R1C_REVOCATION_COUNT' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_TABLE_NAME='sona_r1c_approval_artifact' AND EXISTS(SELECT 1 FROM ml.sona_r1c_approval_artifact
  WHERE approval_sha256=(value->>'approval_sha256')::bytea GROUP BY approval_sha256 HAVING count(*)>64) THEN
  RAISE EXCEPTION 'R1C_ARTIFACT_BOUND' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION app_private.r1c_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE old_value jsonb; new_value jsonb; rid uuid; personal_owner uuid; allowed boolean:=false;
BEGIN
 new_value:=to_jsonb(NEW);
 IF TG_OP='INSERT' THEN
  IF TG_TABLE_NAME='sona_r1c_training_baseline' AND (new_value->>'admission_xid'<>pg_current_xact_id()::text
   OR NOT EXISTS(SELECT 1 FROM ml.training_run r WHERE r.run_id=(new_value->>'run_id')::uuid AND r.registration_xid=pg_current_xact_id()::text)) THEN
   RAISE EXCEPTION 'R1C_NO_BACKFILL' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='sona_r1c_negative_applied' AND NOT EXISTS(
   SELECT 1 FROM ml.sona_r1c_participant_dependency d
   WHERE d.server_instance_id=(new_value->>'server_instance_id')::uuid
    AND d.identity_epoch=(new_value->>'identity_epoch')::bigint
    AND d.serving_ledger_key_id=new_value->>'serving_ledger_key_id'
    AND d.serving_owner_tag=(new_value->>'serving_owner_tag')::bytea
    AND d.owner_user_id=(new_value->>'owner_user_id')::uuid
    AND d.dependency_generation=(new_value->>'covered_dependency_generation')::bigint) THEN
   RAISE EXCEPTION 'R1C_NEGATIVE_CURRENT_BOUNDARY' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='sona_r1c_terminal_negative_coverage' AND NOT EXISTS(
   SELECT 1 FROM app_private.sona_r1c_context c JOIN ml.sona_r1c_terminal_deny t USING(run_id)
   JOIN ml.sona_r1c_run_revocation v USING(run_id)
   WHERE c.transaction_id=pg_current_xact_id()::text AND c.run_id=(new_value->>'run_id')::uuid
    AND t.transition_sha256=(new_value->>'terminal_sha256')::bytea
    AND v.server_instance_id=(new_value->>'server_instance_id')::uuid
    AND v.identity_epoch=(new_value->>'identity_epoch')::bigint
    AND v.serving_ledger_key_id=new_value->>'serving_ledger_key_id'
    AND v.negative_operation_id=(new_value->>'negative_operation_id')::uuid
    AND v.registered_dependency_generation=(new_value->>'registered_dependency_generation')::bigint) THEN
   RAISE EXCEPTION 'R1C_TERMINAL_COVERAGE_CONTEXT' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='sona_r1c_terminal_deny' AND NOT EXISTS(
   SELECT 1 FROM app_private.sona_r1c_context c JOIN ml.sona_r1c_run_authority a USING(run_id)
   WHERE c.transaction_id=pg_current_xact_id()::text AND c.run_id=(new_value->>'run_id')::uuid
    AND a.revoked AND a.revocation_generation=(new_value->>'terminal_generation')::bigint) THEN
   RAISE EXCEPTION 'R1C_TERMINAL_DENY_CONTEXT' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 old_value:=to_jsonb(OLD); rid:=(old_value->>'run_id')::uuid;
 personal_owner:=(old_value->>'owner_user_id')::uuid;
 IF TG_OP='DELETE' THEN
  IF TG_TABLE_NAME='sona_r1c_approval_artifact' THEN
   SELECT run_id INTO rid FROM ml.sona_r1c_approval
    WHERE approval_sha256=(old_value->>'approval_sha256')::bytea;
  ELSIF TG_TABLE_NAME='sona_r1c_recipient' THEN
   SELECT run_id INTO rid FROM ml.sona_r1c_cohort WHERE cohort_id=(old_value->>'cohort_id')::uuid;
  ELSIF TG_TABLE_NAME='sona_r1c_terminal_negative_coverage' THEN
   SELECT owner_user_id INTO personal_owner FROM ml.sona_r1c_negative_applied
    WHERE server_instance_id=(old_value->>'server_instance_id')::uuid
     AND identity_epoch=(old_value->>'identity_epoch')::bigint
     AND serving_ledger_key_id=old_value->>'serving_ledger_key_id'
     AND operation_id=(old_value->>'negative_operation_id')::uuid;
  END IF;
  SELECT EXISTS(SELECT 1 FROM app_private.sona_r1c_context c
   WHERE c.transaction_id=pg_current_xact_id()::text AND c.mode='OWNER_PURGE'
    AND ((rid IS NOT NULL AND c.run_id=rid AND EXISTS(SELECT 1 FROM ml.sona_r1c_terminal_deny t WHERE t.run_id=rid))
     OR personal_owner=c.owner_user_id)) INTO allowed;
  IF allowed AND TG_TABLE_NAME NOT IN ('sona_r1c_terminal_deny','sona_r1c_restore_quarantine') THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'R1C_IMMUTABLE_DELETE' USING ERRCODE='23514';
 END IF;
 IF TG_TABLE_NAME='sona_r1c_participant_dependency' AND
  new_value-'dependency_generation'=old_value-'dependency_generation'
  AND (new_value->>'dependency_generation')::bigint=(old_value->>'dependency_generation')::bigint+1 THEN RETURN NEW;
 ELSIF TG_TABLE_NAME='sona_r1c_run_authority' AND
  new_value-ARRAY['authority_generation','revocation_generation','revoked']=old_value-ARRAY['authority_generation','revocation_generation','revoked']
  AND (new_value->>'authority_generation')::bigint=(old_value->>'authority_generation')::bigint+1
  AND (new_value->>'revocation_generation')::bigint BETWEEN (old_value->>'revocation_generation')::bigint AND (old_value->>'revocation_generation')::bigint+1
  AND (NOT (old_value->>'revoked')::boolean OR (new_value->>'revoked')::boolean) THEN RETURN NEW;
 ELSIF TG_TABLE_NAME='sona_r1c_cohort' AND
  new_value-ARRAY['generation','killed']=old_value-ARRAY['generation','killed']
  AND (new_value->>'generation')::bigint=(old_value->>'generation')::bigint+1
  AND (new_value->>'killed')::boolean THEN RETURN NEW;
 ELSIF TG_TABLE_NAME='sona_r1c_restore_quarantine' AND new_value->>'singleton'=old_value->>'singleton'
  AND (new_value->>'generation')::bigint=(old_value->>'generation')::bigint+1
  AND new_value->>'released_at' IS NULL THEN RETURN NEW;
 ELSIF TG_TABLE_NAME='sona_r1c_restore_quarantine' AND
  new_value-ARRAY['released_at']=old_value-ARRAY['released_at']
  AND old_value->>'released_at' IS NULL AND new_value->>'released_at' IS NOT NULL
  AND EXISTS(SELECT 1 FROM app_private.sona_r1c_restore_context c
   JOIN account.server_instance s ON s.server_instance_id=c.server_instance_id
    AND s.identity_epoch=c.identity_epoch WHERE c.transaction_id=pg_current_xact_id()::text) THEN RETURN NEW;
 END IF;
 RAISE EXCEPTION 'R1C_IMMUTABLE_UPDATE' USING ERRCODE='23514';
END $$;

CREATE FUNCTION app_private.r1c_context_empty() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM app_private.sona_r1c_context WHERE transaction_id=pg_current_xact_id()::text)
  OR EXISTS(SELECT 1 FROM app_private.sona_r1c_restore_context WHERE transaction_id=pg_current_xact_id()::text) THEN
  RAISE EXCEPTION 'R1C_PURGE_CONTEXT_RETAINED' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER r1c_context_deferred AFTER INSERT OR UPDATE ON app_private.sona_r1c_context
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app_private.r1c_context_empty();
CREATE CONSTRAINT TRIGGER r1c_restore_context_deferred AFTER INSERT OR UPDATE ON app_private.sona_r1c_restore_context
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app_private.r1c_context_empty();

CREATE FUNCTION app_private.r1c_check_native_input() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ml.sona_r1c_training_baseline b
  WHERE b.run_id=NEW.run_id AND b.f_input_receipt_sha256=NEW.receipt_sha256
   AND b.admission_xid=pg_current_xact_id()::text) THEN
  RAISE EXCEPTION 'R1C_INSTALLED_INPUT_HOOK_REQUIRED' USING ERRCODE='23514'; END IF;
 PERFORM app_private.r1c_assert_baseline(NEW.run_id);
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER r1c_native_input_deferred AFTER INSERT ON ml.native_training_consumed_input
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app_private.r1c_check_native_input();

CREATE FUNCTION app_private.r1c_lock_fanout(sid uuid,epoch bigint,kid text,tag bytea)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM a.run_id FROM ml.sona_r1c_run_authority a
  JOIN ml.sona_r1c_training_baseline_member m USING(run_id)
  WHERE m.server_instance_id=sid AND m.identity_epoch=epoch AND m.serving_ledger_key_id=kid
   AND m.serving_owner_tag=tag ORDER BY a.run_id FOR UPDATE OF a;
 PERFORM b.run_id FROM ml.sona_r1c_training_baseline b
  JOIN ml.sona_r1c_training_baseline_member m USING(run_id)
  WHERE m.server_instance_id=sid AND m.identity_epoch=epoch AND m.serving_ledger_key_id=kid
   AND m.serving_owner_tag=tag ORDER BY b.run_id FOR UPDATE OF b;
 PERFORM c.cohort_id FROM ml.sona_r1c_cohort c WHERE EXISTS(
  SELECT 1 FROM ml.sona_r1c_training_baseline_member m WHERE m.run_id=c.run_id
   AND m.server_instance_id=sid AND m.identity_epoch=epoch AND m.serving_ledger_key_id=kid
   AND m.serving_owner_tag=tag) ORDER BY c.run_id,c.cohort_id FOR UPDATE OF c;
 PERFORM a.approval_sha256 FROM ml.sona_r1c_approval a WHERE EXISTS(
  SELECT 1 FROM ml.sona_r1c_training_baseline_member m WHERE m.run_id=a.run_id
   AND m.server_instance_id=sid AND m.identity_epoch=epoch AND m.serving_ledger_key_id=kid
   AND m.serving_owner_tag=tag) ORDER BY a.run_id,a.approval_sha256 FOR UPDATE OF a;
END $$;

CREATE FUNCTION app_private.r1c_lock_owner_personal(owner_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM d.serving_owner_tag FROM ml.sona_r1c_participant_dependency d
  WHERE d.owner_user_id=owner_id ORDER BY d.server_instance_id,d.identity_epoch,
   d.serving_ledger_key_id,d.serving_owner_tag FOR UPDATE;
 PERFORM n.operation_id FROM ml.sona_r1c_negative_applied n
  WHERE n.owner_user_id=owner_id ORDER BY n.server_instance_id,n.identity_epoch,
   n.serving_ledger_key_id,n.operation_id FOR UPDATE;
 PERFORM c.cohort_id FROM ml.sona_r1c_cohort c WHERE EXISTS(
  SELECT 1 FROM ml.sona_r1c_recipient p WHERE p.cohort_id=c.cohort_id AND p.owner_user_id=owner_id)
  ORDER BY c.run_id,c.cohort_id FOR UPDATE OF c;
 PERFORM p.cohort_id FROM ml.sona_r1c_recipient p WHERE p.owner_user_id=owner_id
  ORDER BY p.cohort_id FOR UPDATE;
END $$;

CREATE FUNCTION app_private.r1c_lock_legacy_owner_purge(p_owner uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE rid uuid; work record;
BEGIN
 -- The caller already holds ALL identity rows UPDATE, common admission, this
 -- account UPDATE and its publication mutex. This is lock acquisition only:
 -- no purge authorization, context, mutation, validators or feature activation.
 PERFORM d.deletion_request_id FROM account.account_deletion_request d
  WHERE d.user_id=p_owner ORDER BY d.deletion_request_id FOR UPDATE;
 PERFORM account.verify_account_purge_ready(p_owner);
 -- Cover the full owner graph, including retained receipts and child locators.
 -- The legacy purge may drain capped mutation batches; locks are never capped.
 FOR rid IN
  SELECT recommendation_request_id FROM ml.recommendation_request
   WHERE user_id=p_owner AND serving_contract_version=2
  UNION SELECT recommendation_request_id FROM ml.recommendation_attribution_expiry_tombstone
   WHERE owner_user_id=p_owner
  UNION SELECT recommendation_request_id FROM app_private.native_served_authority_purge_receipt
   WHERE owner_user_id=p_owner
  UNION SELECT recommendation_request_id FROM ml.recommendation_attribution_association
   WHERE owner_user_id=p_owner
  UNION SELECT recommendation_request_id FROM ml.offline_pack_binding_receipt
   WHERE owner_user_id=p_owner
  ORDER BY 1
 LOOP
  PERFORM app_private.lock_recommendation_request_v2(rid);
 END LOOP;
 -- Offline packs are removed before ordinary attribution. Lock every binding,
 -- payload and association, including all packs beyond the first 256-row batch.
 PERFORM b.pack_id FROM ml.offline_pack_binding_receipt b
  WHERE b.owner_user_id=p_owner ORDER BY b.pack_id FOR UPDATE;
 PERFORM p.offline_pack_id FROM ml.offline_recommendation_pack p WHERE EXISTS(
  SELECT 1 FROM ml.offline_pack_binding_receipt b
   WHERE b.pack_id=p.offline_pack_id AND b.owner_user_id=p_owner)
  ORDER BY p.offline_pack_id FOR UPDATE OF p;
 PERFORM a.association_id FROM ml.recommendation_attribution_association a
  WHERE a.owner_user_id=p_owner OR EXISTS(
   SELECT 1 FROM ml.offline_pack_binding_receipt b
    WHERE b.owner_user_id=p_owner AND b.pack_id=a.offline_pack_id)
   OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association child
    WHERE child.owner_user_id=p_owner AND child.impression_association_id=a.association_id)
  ORDER BY a.association_id FOR UPDATE OF a;
 -- Feedback/listen validation locks the original impression's sync, inbox and
 -- interaction rows as well as the child event. Include that exact parent closure.
 PERFORM s.event_id FROM sync.sync_event s WHERE EXISTS(
  SELECT 1 FROM ml.recommendation_attribution_association a
   WHERE (a.owner_user_id=p_owner OR EXISTS(
    SELECT 1 FROM ml.recommendation_attribution_association child
     WHERE child.owner_user_id=p_owner AND child.impression_association_id=a.association_id))
   AND s.event_id IN (a.interaction_id,a.listening_event_id))
  ORDER BY s.event_id FOR UPDATE OF s;
 PERFORM i.event_id FROM sync.device_event_inbox i WHERE EXISTS(
  SELECT 1 FROM ml.recommendation_attribution_association a
   WHERE (a.owner_user_id=p_owner OR EXISTS(
    SELECT 1 FROM ml.recommendation_attribution_association child
     WHERE child.owner_user_id=p_owner AND child.impression_association_id=a.association_id))
   AND i.event_id IN (a.interaction_id,a.listening_event_id))
  ORDER BY i.event_id FOR UPDATE OF i;
 PERFORM t.interaction_id FROM library.user_interaction_event t WHERE EXISTS(
  SELECT 1 FROM ml.recommendation_attribution_association a
   WHERE a.interaction_id=t.interaction_id AND (a.owner_user_id=p_owner OR EXISTS(
    SELECT 1 FROM ml.recommendation_attribution_association child
     WHERE child.owner_user_id=p_owner AND child.impression_association_id=a.association_id)))
  ORDER BY t.interaction_id FOR UPDATE OF t;
 PERFORM t.listening_event_id FROM library.listening_event t WHERE EXISTS(
  SELECT 1 FROM ml.recommendation_attribution_association a
   WHERE a.owner_user_id=p_owner AND a.listening_event_id=t.listening_event_id)
  ORDER BY t.listening_event_id FOR UPDATE OF t;
 -- Existing historical owner-purge hooks still traverse these stored rows.
 -- Reuse their exact mutex without importing or enabling any Face runtime.
 FOR work IN SELECT w.face_work_id,w.source_sha256,w.lineage_sha256
  FROM ml.face_analysis_work w WHERE EXISTS(
   SELECT 1 FROM ml.face_analysis_sponsor s
    WHERE s.face_work_id=w.face_work_id AND s.owner_user_id=p_owner)
  ORDER BY w.source_sha256,w.lineage_sha256,w.face_work_id
 LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended('autplay.face.work.v1:'||
   encode(work.source_sha256,'hex')||':'||encode(work.lineage_sha256,'hex'),0));
  PERFORM 1 FROM ml.face_analysis_work WHERE face_work_id=work.face_work_id FOR UPDATE;
 END LOOP;
END $$;

CREATE FUNCTION app_private.r1c_release_restore(sid uuid,epoch bigint,kid text)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE n record;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM app_private.sona_r1c_restore_context c
  WHERE c.transaction_id=pg_current_xact_id()::text AND c.server_instance_id=sid
   AND c.identity_epoch=epoch AND c.serving_ledger_key_id=kid) THEN
  RAISE EXCEPTION 'R1C_RESTORE_CONTEXT_REQUIRED' USING ERRCODE='23514'; END IF;
 FOR n IN SELECT server_instance_id,identity_epoch,serving_ledger_key_id,operation_id
  FROM ml.sona_r1c_negative_applied ORDER BY server_instance_id,identity_epoch,serving_ledger_key_id,operation_id LOOP
  PERFORM app_private.r1c_assert_negative(n.server_instance_id,n.identity_epoch,n.serving_ledger_key_id,n.operation_id);
 END LOOP;
 UPDATE ml.sona_r1c_restore_quarantine SET released_at=clock_timestamp() WHERE singleton=1 AND released_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'R1C_RESTORE_STATE_REQUIRED' USING ERRCODE='23514'; END IF;
 DELETE FROM app_private.sona_r1c_restore_context WHERE transaction_id=pg_current_xact_id()::text;
END $$;

CREATE FUNCTION app_private.r1c_apply_negative(sid uuid,epoch bigint,kid text,owner_id uuid,
 tag bytea,op uuid,rev bigint,decision text,actor bytea,request bytea,previous bytea,at_us bigint,head_hash bytea)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE prior ml.sona_r1c_negative_applied; covered_g bigint; set_count bigint; set_hash bytea;
BEGIN
 IF decision NOT IN ('DENIED','WITHDRAWN') THEN RAISE EXCEPTION 'R1C_NEGATIVE_REQUIRED' USING ERRCODE='23514'; END IF;
 SELECT * INTO prior FROM ml.sona_r1c_negative_applied WHERE server_instance_id=sid
  AND identity_epoch=epoch AND serving_ledger_key_id=kid AND operation_id=op;
 IF FOUND THEN
  IF ROW(prior.owner_user_id,prior.serving_owner_tag,prior.revision,prior.decision,prior.actor_tag,
   prior.request_sha256,prior.previous_policy_sha256,prior.changed_at_us,prior.head_sha256)
   IS DISTINCT FROM ROW(owner_id,tag,rev,decision,actor,request,previous,at_us,head_hash) THEN
   RAISE EXCEPTION 'R1C_NEGATIVE_CONFLICT' USING ERRCODE='23514'; END IF;
  PERFORM app_private.r1c_assert_negative(sid,epoch,kid,op); RETURN;
 END IF;
 SELECT dependency_generation INTO STRICT covered_g FROM ml.sona_r1c_participant_dependency
  WHERE server_instance_id=sid AND identity_epoch=epoch AND serving_ledger_key_id=kid
   AND serving_owner_tag=tag AND owner_user_id=owner_id;
 SELECT count(*),sha256(convert_to(app_private.r1c_jcs(coalesce(jsonb_agg(run_id::text ORDER BY run_id),'[]'::jsonb)),'UTF8'))
  INTO set_count,set_hash FROM ml.sona_r1c_training_baseline_member
  WHERE server_instance_id=sid AND identity_epoch=epoch AND serving_ledger_key_id=kid
   AND serving_owner_tag=tag AND registered_dependency_generation<=covered_g;
 INSERT INTO ml.sona_r1c_negative_applied VALUES(sid,epoch,kid,op,owner_id,tag,rev,decision,actor,
  request,previous,at_us,head_hash,covered_g,set_count,set_hash,clock_timestamp());
 INSERT INTO ml.sona_r1c_run_revocation(run_id,negative_operation_id,registered_dependency_generation,
  server_instance_id,identity_epoch,serving_ledger_key_id)
  SELECT run_id,op,registered_dependency_generation,sid,epoch,kid FROM ml.sona_r1c_training_baseline_member
  WHERE server_instance_id=sid AND identity_epoch=epoch AND serving_ledger_key_id=kid
   AND serving_owner_tag=tag AND registered_dependency_generation<=covered_g;
 UPDATE ml.sona_r1c_run_authority a SET revoked=true,revocation_generation=revocation_generation+1,
  authority_generation=authority_generation+1 WHERE EXISTS(SELECT 1 FROM ml.sona_r1c_run_revocation v
   WHERE v.run_id=a.run_id AND v.server_instance_id=sid AND v.identity_epoch=epoch
    AND v.serving_ledger_key_id=kid AND v.negative_operation_id=op);
 UPDATE ml.sona_r1c_cohort c SET killed=true,generation=generation+1 WHERE NOT killed AND EXISTS(
  SELECT 1 FROM ml.sona_r1c_run_revocation v WHERE v.run_id=c.run_id AND v.server_instance_id=sid
   AND v.identity_epoch=epoch AND v.serving_ledger_key_id=kid AND v.negative_operation_id=op);
 PERFORM app_private.r1c_assert_negative(sid,epoch,kid,op);
END $$;

CREATE FUNCTION app_private.r1c_purge_owner(p_owner_id uuid,p_request_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE rid uuid; b ml.sona_r1c_training_baseline; generation bigint; day date; body jsonb;
BEGIN
 IF NOT (EXISTS(SELECT 1 FROM app_private.privacy_restore_authorization p
   WHERE p.transaction_id=pg_current_xact_id() AND p.owner_id=p_owner_id AND p.request_id=p_request_id)
  OR EXISTS(SELECT 1 FROM account.account_deletion_request d WHERE d.user_id=p_owner_id
   AND d.deletion_request_id=p_request_id AND d.state='PURGING' AND d.cancel_before<=clock_timestamp())) THEN
  RAISE EXCEPTION 'R1C_PURGE_NOT_ADMITTED' USING ERRCODE='23514'; END IF;
 -- Descendants are locked by the real repository BEFORE its independent fence.
 FOR rid IN SELECT run_id FROM ml.sona_r1c_training_baseline_member
  WHERE owner_user_id=p_owner_id ORDER BY run_id LOOP
  SELECT * INTO STRICT b FROM ml.sona_r1c_training_baseline WHERE run_id=rid;
  SELECT revocation_generation INTO STRICT generation FROM ml.sona_r1c_run_authority
   WHERE run_id=rid AND revoked;
  day:=(clock_timestamp() AT TIME ZONE 'UTC')::date;
  body:=jsonb_build_object('schema_version',1,'run_id',rid::text,
   'server_instance_id',b.server_instance_id::text,'identity_epoch',b.identity_epoch,
   'terminal_generation',generation,'reason_code','OWNER_PURGE','completed_day',day::text);
  INSERT INTO app_private.sona_r1c_context VALUES(pg_current_xact_id()::text,p_owner_id,p_request_id,rid,'OWNER_PURGE');
  INSERT INTO ml.sona_r1c_terminal_deny VALUES(rid,b.server_instance_id,b.identity_epoch,
   generation,'OWNER_PURGE',day,sha256(convert_to('autplay.sona.r1c-terminal-deny.v1','UTF8')||
    decode('00','hex')||convert_to(app_private.r1c_jcs(body),'UTF8')));
 END LOOP;
 INSERT INTO ml.sona_r1c_terminal_negative_coverage(run_id,negative_operation_id,
  registered_dependency_generation,terminal_sha256,server_instance_id,identity_epoch,serving_ledger_key_id)
  SELECT v.run_id,v.negative_operation_id,v.registered_dependency_generation,t.transition_sha256,
   v.server_instance_id,v.identity_epoch,v.serving_ledger_key_id FROM ml.sona_r1c_run_revocation v
  JOIN ml.sona_r1c_negative_applied n ON n.server_instance_id=v.server_instance_id
   AND n.identity_epoch=v.identity_epoch AND n.serving_ledger_key_id=v.serving_ledger_key_id
   AND n.operation_id=v.negative_operation_id JOIN ml.sona_r1c_terminal_deny t USING(run_id)
  WHERE n.owner_user_id<>p_owner_id AND EXISTS(SELECT 1 FROM app_private.sona_r1c_context c
   WHERE c.transaction_id=pg_current_xact_id()::text AND c.owner_user_id=p_owner_id AND c.run_id=v.run_id);
 -- A recipient-only purge has no affected trained run; use a private scoped locator.
 INSERT INTO app_private.sona_r1c_context VALUES(pg_current_xact_id()::text,p_owner_id,p_request_id,NULL,'OWNER_PURGE') ON CONFLICT DO NOTHING;
 DELETE FROM ml.sona_r1c_recipient p WHERE p.owner_user_id=p_owner_id OR EXISTS(
  SELECT 1 FROM ml.sona_r1c_cohort c JOIN app_private.sona_r1c_context x USING(run_id)
   WHERE c.cohort_id=p.cohort_id AND x.transaction_id=pg_current_xact_id()::text AND x.owner_user_id=p_owner_id);
 DELETE FROM ml.sona_r1c_cohort c WHERE EXISTS(SELECT 1 FROM app_private.sona_r1c_context x
  WHERE x.transaction_id=pg_current_xact_id()::text AND x.owner_user_id=p_owner_id AND x.run_id=c.run_id);
 DELETE FROM ml.sona_r1c_approval_artifact p WHERE EXISTS(SELECT 1 FROM ml.sona_r1c_approval a
  JOIN app_private.sona_r1c_context x USING(run_id) WHERE a.approval_sha256=p.approval_sha256
   AND x.transaction_id=pg_current_xact_id()::text AND x.owner_user_id=p_owner_id);
 DELETE FROM ml.sona_r1c_approval a WHERE EXISTS(SELECT 1 FROM app_private.sona_r1c_context x
  WHERE x.transaction_id=pg_current_xact_id()::text AND x.owner_user_id=p_owner_id AND x.run_id=a.run_id);
 DELETE FROM ml.sona_r1c_run_revocation v WHERE EXISTS(SELECT 1 FROM app_private.sona_r1c_context x
  WHERE x.transaction_id=pg_current_xact_id()::text AND x.owner_user_id=p_owner_id AND x.run_id=v.run_id);
 DELETE FROM ml.sona_r1c_run_authority a WHERE EXISTS(SELECT 1 FROM app_private.sona_r1c_context x
  WHERE x.transaction_id=pg_current_xact_id()::text AND x.owner_user_id=p_owner_id AND x.run_id=a.run_id);
 DELETE FROM ml.sona_r1c_training_baseline_member m WHERE EXISTS(SELECT 1 FROM app_private.sona_r1c_context x
  WHERE x.transaction_id=pg_current_xact_id()::text AND x.owner_user_id=p_owner_id AND x.run_id=m.run_id);
 DELETE FROM ml.sona_r1c_training_baseline baseline_row WHERE EXISTS(SELECT 1 FROM app_private.sona_r1c_context x
  WHERE x.transaction_id=pg_current_xact_id()::text AND x.owner_user_id=p_owner_id AND x.run_id=baseline_row.run_id);
 DELETE FROM ml.sona_r1c_terminal_negative_coverage c USING ml.sona_r1c_negative_applied n
  WHERE n.owner_user_id=p_owner_id AND n.server_instance_id=c.server_instance_id
   AND n.identity_epoch=c.identity_epoch AND n.serving_ledger_key_id=c.serving_ledger_key_id
   AND n.operation_id=c.negative_operation_id;
 DELETE FROM ml.sona_r1c_negative_applied WHERE owner_user_id=p_owner_id;
 DELETE FROM ml.sona_r1c_participant_dependency WHERE owner_user_id=p_owner_id;
 DELETE FROM app_private.sona_r1c_context WHERE transaction_id=pg_current_xact_id()::text AND owner_user_id=p_owner_id;
END $$;
"""


def install(connection: Connection) -> None:
    """Explicit private-overlay install, only after real F0103/B0104 predecessors."""
    required = connection.scalar(
        text(
            "SELECT to_regclass('ml.native_training_consumed_input') IS NOT NULL AND to_regclass('ml.native_training_consumed_owner') IS NOT NULL"
        )
    )
    if required is not True:
        raise ValueError("r1c_native_predecessor_required")
    for table in R1C_AUTHORITY_TABLES:
        table.create(connection, checkfirst=False)
    connection.execute(text(R1C_AUTHORITY_SQL))
    for table in R1C_AUTHORITY_TABLES:
        name = table.name
        connection.execute(
            text(
                f"CREATE TRIGGER r1c_guard BEFORE INSERT OR UPDATE OR DELETE ON ml.{name} FOR EACH ROW EXECUTE FUNCTION app_private.r1c_guard()"
            )
        )
        connection.execute(
            text(
                f"CREATE CONSTRAINT TRIGGER r1c_graph_deferred AFTER INSERT OR UPDATE OR DELETE ON ml.{name} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app_private.r1c_check_graph()"
            )
        )
    for signature in R1C_FUNCTIONS:
        connection.execute(text(f"REVOKE ALL ON FUNCTION app_private.{signature} FROM PUBLIC"))
    connection.execute(text("INSERT INTO ml.sona_r1c_restore_quarantine VALUES(1,1,NULL)"))


def remove(connection: Connection) -> None:
    """Adjacent overlay downgrade; installed personal authority must be empty."""
    for table in R1C_AUTHORITY_TABLES:
        if table.name != "sona_r1c_restore_quarantine" and connection.scalar(
            text(f"SELECT EXISTS(SELECT 1 FROM ml.{table.name})")
        ):
            raise ValueError("r1c_nonempty_downgrade_refused")
    connection.execute(
        text("DROP TRIGGER r1c_native_input_deferred ON ml.native_training_consumed_input")
    )
    connection.execute(text("DROP TRIGGER r1c_context_deferred ON app_private.sona_r1c_context"))
    connection.execute(
        text("DROP TRIGGER r1c_restore_context_deferred ON app_private.sona_r1c_restore_context")
    )
    for table in R1C_AUTHORITY_TABLES:
        connection.execute(text(f"DROP TRIGGER r1c_graph_deferred ON ml.{table.name}"))
        connection.execute(text(f"DROP TRIGGER r1c_guard ON ml.{table.name}"))
    for signature in reversed(R1C_FUNCTIONS):
        connection.execute(text(f"DROP FUNCTION app_private.{signature}"))
    connection.execute(text("DROP TABLE app_private.sona_r1c_context"))
    connection.execute(text("DROP TABLE app_private.sona_r1c_restore_context"))
    for table in reversed(R1C_AUTHORITY_TABLES):
        table.drop(connection, checkfirst=False)
