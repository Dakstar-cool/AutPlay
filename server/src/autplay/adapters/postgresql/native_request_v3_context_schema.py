"""Dormant source3 origin witnesses and private request-bound write guards.

These constants install nothing on import. The privileged writer must prepay
its whole graph work before using them. Removal contexts have no callable
armer: future privileged lifecycle writers must establish their distinct SQL
authorization facts before inserting the protected context internally.
The primary full graph assertion is a mandatory installation prerequisite.
"""

from __future__ import annotations

NATIVE_REQUEST_V3_CONTEXT_REQUIRED_FUNCTIONS = (
    "app_private.check_native_served_identity(uuid,uuid,uuid,uuid,bigint)",
    "app_private.assert_native_atomic_graph_v3(uuid)",
)
NATIVE_REQUEST_V3_CONTEXT_GUARDED_TABLES = (
    "native_original_capture_format",
    "native_served_request_hash_format",
    "native_original_capture_v3",
    "native_capture_source_event_v3",
    "native_capture_lineage_cursor_v3",
    "native_capture_replay_v3",
    "recommendation_request_root_v3",
    "native_atomic_capture_connection_v3",
)
NATIVE_REQUEST_V3_CONTEXT_WITNESSED_TABLES = (
    "recommendation_input_snapshot",
    "recommendation_request",
    "recommendation_temporal_snapshot",
)
NATIVE_REQUEST_V3_CONTEXT_REQUIRED_TABLES = tuple(
    "ml." + name
    for name in (
        *NATIVE_REQUEST_V3_CONTEXT_GUARDED_TABLES,
        *NATIVE_REQUEST_V3_CONTEXT_WITNESSED_TABLES,
    )
)
NATIVE_REQUEST_V3_CONTEXT_PRIVATE_TABLES = (
    "native_original_context_v3",
    "native_original_insert_witness_v3",
    "native_request_write_context_v3",
    "native_request_deletion_witness_v3",
)
NATIVE_REQUEST_V3_CONTEXT_FUNCTIONS = (
    "arm_native_original_context_v3(uuid,uuid,uuid,uuid,bigint)",
    "witness_native_original_insert_v3()",
    "protect_native_request_context_v3()",
    "guard_native_request_write_v3()",
    "check_native_original_context_v3()",
    "check_native_request_removal_context_v3()",
)


def _required_sql() -> str:
    statements = []
    for function in NATIVE_REQUEST_V3_CONTEXT_REQUIRED_FUNCTIONS:
        statements.append(
            f"IF to_regprocedure('{function}') IS NULL THEN "
            "RAISE EXCEPTION 'NATIVE_V3_CONTEXT_ASSERTION_MISSING' USING ERRCODE='55000'; END IF;"
        )
    for table in NATIVE_REQUEST_V3_CONTEXT_REQUIRED_TABLES:
        statements.append(
            f"IF to_regclass('{table}') IS NULL THEN "
            "RAISE EXCEPTION 'NATIVE_V3_CONTEXT_TABLE_MISSING' USING ERRCODE='55000'; END IF;"
        )
    return "DO $$ BEGIN\n" + "\n".join(statements) + "\nEND $$;\n"


NATIVE_REQUEST_V3_CONTEXT_INSTALL_SQL = (
    _required_sql()
    + r"""
CREATE TABLE app_private.native_original_context_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL CHECK(backend_pid>0),
 owner_user_id uuid NOT NULL,device_id uuid NOT NULL,journal_epoch uuid NOT NULL,
 server_instance_id uuid NOT NULL,identity_epoch bigint NOT NULL CHECK(identity_epoch>=1),
 started_at timestamptz NOT NULL,
 PRIMARY KEY(transaction_id,backend_pid),
 UNIQUE(transaction_id,backend_pid,owner_user_id)
);
CREATE TABLE app_private.native_original_insert_witness_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL,owner_user_id uuid NOT NULL,
 table_name text NOT NULL CHECK(table_name IN ('recommendation_input_snapshot',
  'recommendation_request','recommendation_temporal_snapshot')),
 row_id uuid NOT NULL,baseline_snapshot_id uuid,temporal_snapshot_id uuid,
 serving_contract_version integer,created_at timestamptz NOT NULL,
 PRIMARY KEY(transaction_id,backend_pid,table_name,row_id),
 FOREIGN KEY(transaction_id,backend_pid,owner_user_id)
  REFERENCES app_private.native_original_context_v3(transaction_id,backend_pid,owner_user_id)
);
CREATE TABLE app_private.native_request_write_context_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL CHECK(backend_pid>0),
 owner_user_id uuid NOT NULL,original_request_id uuid NOT NULL,served_request_id uuid NOT NULL,
 operation text NOT NULL CHECK(operation IN ('CREATE','LIVE_REMOVE','HISTORY_PURGE')),
 device_id uuid NOT NULL,journal_epoch uuid NOT NULL,server_instance_id uuid NOT NULL,
 identity_epoch bigint NOT NULL CHECK(identity_epoch>=1),started_at timestamptz NOT NULL,
 PRIMARY KEY(transaction_id,backend_pid,owner_user_id,original_request_id,served_request_id),
 UNIQUE(transaction_id,backend_pid,original_request_id),
 UNIQUE(transaction_id,backend_pid,served_request_id),
 CHECK(original_request_id<>served_request_id)
);
CREATE UNIQUE INDEX native_request_write_context_v3_one_create
 ON app_private.native_request_write_context_v3(transaction_id,backend_pid)
 WHERE operation='CREATE';
CREATE TABLE app_private.native_request_deletion_witness_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL,owner_user_id uuid NOT NULL,
 original_request_id uuid NOT NULL,served_request_id uuid NOT NULL,
 operation text NOT NULL CHECK(operation='HISTORY_PURGE'),
 original_capture_format_version integer NOT NULL CHECK(original_capture_format_version=3),
 request_hash_format_version integer NOT NULL CHECK(request_hash_format_version=3),
 PRIMARY KEY(transaction_id,backend_pid,owner_user_id,original_request_id,served_request_id),
 CHECK(original_request_id<>served_request_id)
);

CREATE FUNCTION app_private.arm_native_original_context_v3(
 p_owner uuid,p_device uuid,p_journal uuid,p_server uuid,p_identity bigint)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'repeatable read'
  OR p_owner IS NULL OR p_device IS NULL OR p_journal IS NULL OR p_server IS NULL
  OR p_identity IS NULL OR p_identity<1 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGIN_TRANSACTION' USING ERRCODE='23514'; END IF;
 PERFORM app_private.check_native_served_identity(p_owner,p_device,p_journal,p_server,p_identity);
 INSERT INTO app_private.native_original_context_v3(
  transaction_id,backend_pid,owner_user_id,device_id,journal_epoch,
  server_instance_id,identity_epoch,started_at)
 VALUES(pg_current_xact_id(),pg_backend_pid(),p_owner,p_device,p_journal,
  p_server,p_identity,clock_timestamp());
END $$;

CREATE FUNCTION app_private.witness_native_original_insert_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_started timestamptz; v_row uuid; v_baseline uuid; v_temporal uuid;
 v_contract integer; v_created timestamptz;
BEGIN
 IF TG_OP<>'INSERT' OR TG_TABLE_SCHEMA<>'ml' THEN
  RAISE EXCEPTION 'NATIVE_V3_WITNESS_TRIGGER' USING ERRCODE='23514'; END IF;
 SELECT c.started_at INTO v_started FROM app_private.native_original_context_v3 c
  WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
   AND c.owner_user_id=NEW.user_id;
 IF v_started IS NULL THEN RETURN NEW; END IF;
 -- Fixed scalar projection only; never serialize the complete personal NEW row.
 IF TG_TABLE_NAME='recommendation_input_snapshot' THEN
  v_row:=NEW.recommendation_input_snapshot_id; v_baseline:=v_row;
 ELSIF TG_TABLE_NAME='recommendation_request' THEN
  v_row:=NEW.recommendation_request_id;
  v_baseline:=NEW.recommendation_input_snapshot_id;
  v_temporal:=NEW.recommendation_temporal_snapshot_id;
  v_contract:=NEW.serving_contract_version;
 ELSIF TG_TABLE_NAME='recommendation_temporal_snapshot' THEN
  v_row:=NEW.recommendation_temporal_snapshot_id; v_temporal:=v_row;
  v_baseline:=NEW.recommendation_input_snapshot_id;
 ELSE RAISE EXCEPTION 'NATIVE_V3_WITNESS_TABLE' USING ERRCODE='23514'; END IF;
 v_created:=NEW.created_at;
 IF current_setting('transaction_isolation')<>'repeatable read' OR v_row IS NULL
  OR v_created IS NULL OR v_created<v_started-interval '30 seconds'
  OR v_created>clock_timestamp() THEN
  RAISE EXCEPTION 'NATIVE_V3_WITNESS_TIME' USING ERRCODE='23514'; END IF;
 INSERT INTO app_private.native_original_insert_witness_v3(
  transaction_id,backend_pid,owner_user_id,table_name,row_id,baseline_snapshot_id,
  temporal_snapshot_id,serving_contract_version,created_at)
 VALUES(pg_current_xact_id(),pg_backend_pid(),NEW.user_id,TG_TABLE_NAME,v_row,
  v_baseline,v_temporal,v_contract,v_created);
 RETURN NEW;
END $$;

CREATE FUNCTION app_private.protect_native_request_context_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_started timestamptz;
BEGIN
 IF TG_OP<>'INSERT' OR NEW.transaction_id IS DISTINCT FROM pg_current_xact_id()
  OR NEW.backend_pid IS DISTINCT FROM pg_backend_pid()
  OR NEW.owner_user_id IS NULL OR NEW.original_request_id IS NULL
  OR NEW.served_request_id IS NULL OR NEW.original_request_id=NEW.served_request_id THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITE_CONTEXT' USING ERRCODE='23514'; END IF;
 IF NEW.operation='CREATE' THEN
  SELECT c.started_at INTO v_started FROM app_private.native_original_context_v3 c
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
    AND c.owner_user_id=NEW.owner_user_id AND c.device_id=NEW.device_id
    AND c.journal_epoch=NEW.journal_epoch AND c.server_instance_id=NEW.server_instance_id
    AND c.identity_epoch=NEW.identity_epoch;
  IF v_started IS NULL OR current_setting('transaction_isolation')<>'repeatable read'
   OR NOT EXISTS(SELECT 1 FROM app_private.native_original_insert_witness_v3 w
    WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
     AND w.owner_user_id=NEW.owner_user_id AND w.table_name='recommendation_request'
     AND w.row_id=NEW.original_request_id AND w.serving_contract_version=1
     AND w.baseline_snapshot_id IS NOT NULL AND w.temporal_snapshot_id IS NOT NULL)
   OR EXISTS(SELECT 1 FROM ml.recommendation_request r
    WHERE r.recommendation_request_id=NEW.served_request_id)
   OR EXISTS(SELECT 1 FROM ml.native_original_capture_format f
    WHERE f.original_request_id IN (NEW.original_request_id,NEW.served_request_id))
   OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
    WHERE f.served_request_id IN (NEW.original_request_id,NEW.served_request_id)) THEN
   RAISE EXCEPTION 'NATIVE_V3_CREATE_WITNESS_REQUIRED' USING ERRCODE='23514'; END IF;
  NEW.started_at:=v_started;
 ELSIF NEW.operation IN ('LIVE_REMOVE','HISTORY_PURGE') THEN
  -- No function here arms removal. Future privileged writers must first prove
  -- distinct expiry/privacy facts; these comparisons bind their private scope.
  IF NOT EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
   JOIN ml.native_original_capture_format o ON o.original_request_id=r.original_request_id
    AND o.owner_user_id=r.owner_user_id AND o.original_capture_format_version=3
   JOIN ml.native_served_request_hash_format f ON f.served_request_id=r.served_request_id
    AND f.owner_user_id=r.owner_user_id AND f.request_hash_format_version=3
   WHERE r.owner_user_id=NEW.owner_user_id AND r.original_request_id=NEW.original_request_id
    AND r.served_request_id=NEW.served_request_id AND r.device_id=NEW.device_id
    AND r.journal_epoch=NEW.journal_epoch AND r.server_instance_id=NEW.server_instance_id
    AND r.identity_epoch=NEW.identity_epoch) THEN
   RAISE EXCEPTION 'NATIVE_V3_REMOVE_BINDING' USING ERRCODE='23514'; END IF;
  NEW.started_at:=clock_timestamp();
  IF NEW.operation='HISTORY_PURGE' THEN
   INSERT INTO app_private.native_request_deletion_witness_v3 VALUES(
    pg_current_xact_id(),pg_backend_pid(),NEW.owner_user_id,NEW.original_request_id,
    NEW.served_request_id,'HISTORY_PURGE',3,3);
  END IF;
 ELSE RAISE EXCEPTION 'NATIVE_V3_WRITE_MODE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION app_private.guard_native_request_write_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_owner uuid; v_original uuid; v_served uuid; v_tx xid8; v_created timestamptz;
 v_expires timestamptz; v_context app_private.native_request_write_context_v3%ROWTYPE;
BEGIN
 IF TG_TABLE_SCHEMA<>'ml' OR TG_OP NOT IN ('INSERT','UPDATE','DELETE') THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITE_TRIGGER' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' THEN
  -- A future generation-invalidation sibling needs its own explicit guard.
  RAISE EXCEPTION 'NATIVE_V3_ROW_IMMUTABLE' USING ERRCODE='55000'; END IF;
 IF TG_OP='INSERT' THEN
  v_owner:=NEW.owner_user_id;
  IF TG_TABLE_NAME='native_served_request_hash_format' THEN
   v_served:=NEW.served_request_id;
   IF NEW.request_hash_format_version IS DISTINCT FROM 3 THEN
    RAISE EXCEPTION 'NATIVE_V3_WRITE_FORMAT' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME IN
   ('recommendation_request_root_v3','native_atomic_capture_connection_v3') THEN
   v_original:=NEW.original_request_id; v_served:=NEW.served_request_id;
   IF NEW.original_capture_format_version IS DISTINCT FROM 3
    OR NEW.request_hash_format_version IS DISTINCT FROM 3 THEN
    RAISE EXCEPTION 'NATIVE_V3_WRITE_FORMAT' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME IN ('native_original_capture_format','native_original_capture_v3',
   'native_capture_source_event_v3','native_capture_lineage_cursor_v3',
   'native_capture_replay_v3') THEN
   v_original:=NEW.original_request_id;
   IF TG_TABLE_NAME IN ('native_original_capture_format','native_original_capture_v3') THEN
    IF NEW.original_capture_format_version IS DISTINCT FROM 3 THEN
     RAISE EXCEPTION 'NATIVE_V3_WRITE_FORMAT' USING ERRCODE='23514'; END IF;
   END IF;
  ELSE RAISE EXCEPTION 'NATIVE_V3_WRITE_TABLE' USING ERRCODE='23514'; END IF;
 ELSE
  v_owner:=OLD.owner_user_id;
  IF TG_TABLE_NAME='native_served_request_hash_format' THEN v_served:=OLD.served_request_id;
  ELSIF TG_TABLE_NAME IN
   ('recommendation_request_root_v3','native_atomic_capture_connection_v3') THEN
   v_original:=OLD.original_request_id; v_served:=OLD.served_request_id;
  ELSIF TG_TABLE_NAME IN ('native_original_capture_format','native_original_capture_v3',
   'native_capture_source_event_v3','native_capture_lineage_cursor_v3',
   'native_capture_replay_v3') THEN
   v_original:=OLD.original_request_id;
  ELSE RAISE EXCEPTION 'NATIVE_V3_WRITE_TABLE' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT c.* INTO v_context FROM app_private.native_request_write_context_v3 c
  WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
   AND c.owner_user_id=v_owner AND (v_original IS NULL OR c.original_request_id=v_original)
   AND (v_served IS NULL OR c.served_request_id=v_served);
 IF v_context.transaction_id IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITE_CONTEXT_REQUIRED' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN
  IF v_context.operation='LIVE_REMOVE' AND TG_TABLE_NAME IN ('native_original_capture_v3',
   'native_capture_source_event_v3','native_capture_lineage_cursor_v3',
   'native_capture_replay_v3','native_atomic_capture_connection_v3') THEN RETURN OLD; END IF;
  IF v_context.operation='HISTORY_PURGE' AND EXISTS(
   SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
    AND w.owner_user_id=v_owner AND w.original_request_id=v_context.original_request_id
    AND w.served_request_id=v_context.served_request_id AND w.operation='HISTORY_PURGE'
    AND w.original_capture_format_version=3 AND w.request_hash_format_version=3) THEN
   RETURN OLD; END IF;
  RAISE EXCEPTION 'NATIVE_V3_DELETE_CONTEXT_REQUIRED' USING ERRCODE='55000';
 END IF;
 IF v_context.operation<>'CREATE'
  OR current_setting('transaction_isolation')<>'repeatable read' THEN
  RAISE EXCEPTION 'NATIVE_V3_CREATE_CONTEXT_REQUIRED' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='native_capture_source_event_v3' THEN
  IF NOT EXISTS(SELECT 1 FROM ml.native_original_capture_v3 b
   WHERE b.original_request_id=v_original AND b.owner_user_id=v_owner
    AND b.created_transaction=pg_current_xact_id()) THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_PARENT_TRANSACTION' USING ERRCODE='23514'; END IF;
 ELSE
  v_tx:=NEW.created_transaction; v_created:=NEW.created_at; v_expires:=NEW.expires_at;
  IF v_tx IS DISTINCT FROM pg_current_xact_id()
   OR v_created IS DISTINCT FROM (SELECT w.created_at
    FROM app_private.native_original_insert_witness_v3 w
    WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
     AND w.owner_user_id=v_owner AND w.table_name='recommendation_request'
     AND w.row_id=v_context.original_request_id)
   OR v_expires IS DISTINCT FROM v_created+interval '15552000 seconds' THEN
   RAISE EXCEPTION 'NATIVE_V3_WRITE_TRANSACTION_TIME' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_TABLE_NAME='recommendation_request_root_v3' THEN
  IF NEW.device_id IS DISTINCT FROM v_context.device_id
   OR NEW.journal_epoch IS DISTINCT FROM v_context.journal_epoch
   OR NEW.server_instance_id IS DISTINCT FROM v_context.server_instance_id
   OR NEW.identity_epoch IS DISTINCT FROM v_context.identity_epoch THEN
   RAISE EXCEPTION 'NATIVE_V3_WRITE_IDENTITY' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION app_private.check_native_original_context_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_context app_private.native_request_write_context_v3%ROWTYPE;
 v_baseline uuid; v_temporal uuid; v_created timestamptz; v_source_count bigint;
BEGIN
 IF NEW.transaction_id IS DISTINCT FROM pg_current_xact_id()
  OR NEW.backend_pid IS DISTINCT FROM pg_backend_pid()
  OR current_setting('transaction_isolation')<>'repeatable read'
  OR NOT EXISTS(SELECT 1 FROM app_private.native_original_context_v3 o
   WHERE o.transaction_id=NEW.transaction_id AND o.backend_pid=NEW.backend_pid
    AND o.owner_user_id=NEW.owner_user_id AND o.device_id=NEW.device_id
    AND o.journal_epoch=NEW.journal_epoch AND o.server_instance_id=NEW.server_instance_id
    AND o.identity_epoch=NEW.identity_epoch AND o.started_at=NEW.started_at) THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGIN_CONTEXT_LOST' USING ERRCODE='23514'; END IF;
 SELECT c.* INTO v_context FROM app_private.native_request_write_context_v3 c
  WHERE c.transaction_id=NEW.transaction_id AND c.backend_pid=NEW.backend_pid
   AND c.owner_user_id=NEW.owner_user_id AND c.operation='CREATE';
 IF v_context.original_request_id IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_ARMED_GRAPH_INCOMPLETE' USING ERRCODE='23514'; END IF;
 SELECT w.baseline_snapshot_id,w.temporal_snapshot_id,w.created_at
  INTO v_baseline,v_temporal,v_created FROM app_private.native_original_insert_witness_v3 w
  WHERE w.transaction_id=NEW.transaction_id AND w.backend_pid=NEW.backend_pid
   AND w.owner_user_id=NEW.owner_user_id AND w.table_name='recommendation_request'
   AND w.row_id=v_context.original_request_id AND w.serving_contract_version=1;
 IF v_baseline IS NULL OR v_temporal IS NULL OR v_created IS NULL
  OR (SELECT count(*) FROM (SELECT 1 FROM app_private.native_original_insert_witness_v3 w
   WHERE w.transaction_id=NEW.transaction_id AND w.backend_pid=NEW.backend_pid
   LIMIT 5) witness_rows)<>4
  OR EXISTS(SELECT 1 FROM (VALUES
   ('recommendation_input_snapshot',v_baseline),('recommendation_temporal_snapshot',v_temporal),
   ('recommendation_request',v_context.original_request_id),
   ('recommendation_request',v_context.served_request_id)) required(table_name,row_id)
   WHERE NOT EXISTS(SELECT 1 FROM app_private.native_original_insert_witness_v3 w
    WHERE w.transaction_id=NEW.transaction_id AND w.backend_pid=NEW.backend_pid
     AND w.owner_user_id=NEW.owner_user_id AND w.table_name=required.table_name
     AND w.row_id=required.row_id)) THEN
  RAISE EXCEPTION 'NATIVE_V3_INSERT_WITNESS_REQUIRED' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection_v3 c
  JOIN ml.native_original_capture_v3 b ON b.original_request_id=c.original_request_id
   AND b.owner_user_id=c.owner_user_id
  JOIN ml.native_original_capture_format o ON o.original_request_id=b.original_request_id
   AND o.owner_user_id=b.owner_user_id AND o.original_capture_format_version=3
  JOIN ml.native_served_request_hash_format f ON f.served_request_id=c.served_request_id
   AND f.owner_user_id=c.owner_user_id AND f.request_hash_format_version=3
  JOIN ml.native_capture_lineage_cursor_v3 k ON k.original_request_id=b.original_request_id
   AND k.owner_user_id=b.owner_user_id AND k.state='ACTIVE'
  JOIN ml.native_capture_replay_v3 p ON p.original_request_id=b.original_request_id
   AND p.owner_user_id=b.owner_user_id
  JOIN ml.recommendation_request_root_v3 r ON r.original_request_id=c.original_request_id
   AND r.served_request_id=c.served_request_id AND r.owner_user_id=c.owner_user_id
  JOIN ml.recommendation_request original
   ON original.recommendation_request_id=c.original_request_id
   AND original.user_id=c.owner_user_id AND original.serving_contract_version=1
  JOIN ml.recommendation_request served ON served.recommendation_request_id=c.served_request_id
   AND served.user_id=c.owner_user_id AND served.serving_contract_version=2
  JOIN ml.recommendation_input_snapshot baseline
   ON baseline.recommendation_input_snapshot_id=v_baseline AND baseline.user_id=c.owner_user_id
  JOIN ml.recommendation_temporal_snapshot temporal
   ON temporal.recommendation_temporal_snapshot_id=v_temporal AND temporal.user_id=c.owner_user_id
  WHERE c.owner_user_id=NEW.owner_user_id AND c.original_request_id=v_context.original_request_id
   AND c.served_request_id=v_context.served_request_id
   AND c.original_capture_format_version=3 AND c.request_hash_format_version=3
   AND b.baseline_snapshot_id=v_baseline AND b.temporal_snapshot_id=v_temporal
   AND p.baseline_snapshot_id=v_baseline AND p.temporal_snapshot_id=v_temporal
   AND r.baseline_snapshot_id=v_baseline AND r.temporal_snapshot_id=v_temporal
   AND original.recommendation_input_snapshot_id=v_baseline
   AND original.recommendation_temporal_snapshot_id=v_temporal
   AND served.recommendation_input_snapshot_id=v_baseline
   AND temporal.recommendation_input_snapshot_id=v_baseline
   AND r.device_id=NEW.device_id AND r.journal_epoch=NEW.journal_epoch
   AND r.server_instance_id=NEW.server_instance_id AND r.identity_epoch=NEW.identity_epoch
   AND c.created_transaction=NEW.transaction_id AND b.created_transaction=NEW.transaction_id
   AND o.created_transaction=NEW.transaction_id AND f.created_transaction=NEW.transaction_id
   AND k.created_transaction=NEW.transaction_id AND p.created_transaction=NEW.transaction_id
   AND r.created_transaction=NEW.transaction_id
   AND c.created_at=v_created AND b.created_at=v_created AND o.created_at=v_created
   AND f.created_at=v_created AND k.created_at=v_created AND p.created_at=v_created
   AND r.created_at=v_created AND original.created_at=v_created AND served.created_at=v_created
   AND baseline.created_at=v_created AND temporal.created_at=v_created
   AND c.expires_at=v_created+interval '15552000 seconds' AND b.expires_at=c.expires_at
   AND o.expires_at=c.expires_at AND f.expires_at=c.expires_at AND k.expires_at=c.expires_at
   AND p.expires_at=c.expires_at AND r.expires_at=c.expires_at
   AND k.source_event_count=b.source_event_count
   AND k.cutoff_at_ms=b.cutoff_at_ms
   AND k.native_interaction_watermark=b.native_interaction_watermark
   AND r.cutoff_at_ms=b.cutoff_at_ms AND b.cutoff_at_ms=floor(extract(epoch FROM v_created)*1000)
   AND k.source_generation>=1 AND b.source_event_count BETWEEN 0 AND 10000) THEN
  RAISE EXCEPTION 'NATIVE_V3_ARMED_GRAPH_INCOMPLETE' USING ERRCODE='23514'; END IF;
 SELECT count(*) INTO v_source_count FROM (SELECT 1 FROM ml.native_capture_source_event_v3 e
  WHERE e.original_request_id=v_context.original_request_id LIMIT 10001) source_rows;
 IF v_source_count IS DISTINCT FROM (SELECT b.source_event_count
  FROM ml.native_original_capture_v3 b WHERE b.original_request_id=v_context.original_request_id
   AND b.owner_user_id=NEW.owner_user_id) THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_CURSOR_CLOSURE' USING ERRCODE='23514'; END IF;
 -- This is the primary assertion, never an optional placeholder or a receipt grant.
 PERFORM app_private.assert_native_atomic_graph_v3(v_context.original_request_id);
 DELETE FROM app_private.native_original_insert_witness_v3 w
  WHERE w.transaction_id=NEW.transaction_id AND w.backend_pid=NEW.backend_pid;
 DELETE FROM app_private.native_request_write_context_v3 c
  WHERE c.transaction_id=NEW.transaction_id AND c.backend_pid=NEW.backend_pid
   AND c.operation='CREATE';
 DELETE FROM app_private.native_original_context_v3 o
  WHERE o.transaction_id=NEW.transaction_id AND o.backend_pid=NEW.backend_pid;
 RETURN NULL;
END $$;

CREATE FUNCTION app_private.check_native_request_removal_context_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.transaction_id IS DISTINCT FROM pg_current_xact_id()
  OR NEW.backend_pid IS DISTINCT FROM pg_backend_pid()
  OR NOT EXISTS(SELECT 1 FROM app_private.native_request_write_context_v3 c
   WHERE c.transaction_id=NEW.transaction_id AND c.backend_pid=NEW.backend_pid
    AND c.owner_user_id=NEW.owner_user_id AND c.original_request_id=NEW.original_request_id
    AND c.served_request_id=NEW.served_request_id AND c.operation=NEW.operation)
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_v3 b
   WHERE b.original_request_id=NEW.original_request_id)
  OR EXISTS(SELECT 1 FROM ml.native_capture_source_event_v3 e
   WHERE e.original_request_id=NEW.original_request_id)
  OR EXISTS(SELECT 1 FROM ml.native_capture_lineage_cursor_v3 k
   WHERE k.original_request_id=NEW.original_request_id)
  OR EXISTS(SELECT 1 FROM ml.native_capture_replay_v3 p
   WHERE p.original_request_id=NEW.original_request_id)
  OR EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection_v3 c
   WHERE c.original_request_id=NEW.original_request_id
    OR c.served_request_id=NEW.served_request_id) THEN
  RAISE EXCEPTION 'NATIVE_V3_REMOVE_INCOMPLETE' USING ERRCODE='23514'; END IF;
 IF NEW.operation='LIVE_REMOVE' THEN
  IF NOT EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
   JOIN ml.native_original_capture_format o ON o.original_request_id=r.original_request_id
    AND o.owner_user_id=r.owner_user_id AND o.original_capture_format_version=3
   JOIN ml.native_served_request_hash_format f ON f.served_request_id=r.served_request_id
    AND f.owner_user_id=r.owner_user_id AND f.request_hash_format_version=3
   JOIN ml.recommendation_request original
    ON original.recommendation_request_id=r.original_request_id
    AND original.user_id=r.owner_user_id
   JOIN ml.recommendation_request served ON served.recommendation_request_id=r.served_request_id
    AND served.user_id=r.owner_user_id
   WHERE r.owner_user_id=NEW.owner_user_id AND r.original_request_id=NEW.original_request_id
    AND r.served_request_id=NEW.served_request_id) THEN
   RAISE EXCEPTION 'NATIVE_V3_LIVE_HISTORY_LOST' USING ERRCODE='23514'; END IF;
 ELSIF NEW.operation='HISTORY_PURGE' THEN
  IF NOT EXISTS(SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
   WHERE w.transaction_id=NEW.transaction_id AND w.backend_pid=NEW.backend_pid
    AND w.owner_user_id=NEW.owner_user_id AND w.original_request_id=NEW.original_request_id
    AND w.served_request_id=NEW.served_request_id AND w.operation='HISTORY_PURGE'
    AND w.original_capture_format_version=3 AND w.request_hash_format_version=3)
   OR EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
    WHERE r.original_request_id=NEW.original_request_id
     OR r.served_request_id=NEW.served_request_id)
   OR EXISTS(SELECT 1 FROM ml.native_original_capture_format o
    WHERE o.original_request_id IN (NEW.original_request_id,NEW.served_request_id))
   OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
    WHERE f.served_request_id IN (NEW.original_request_id,NEW.served_request_id))
   OR EXISTS(SELECT 1 FROM ml.recommendation_request request
    WHERE request.recommendation_request_id IN (NEW.original_request_id,NEW.served_request_id)) THEN
   RAISE EXCEPTION 'NATIVE_V3_HISTORY_PURGE_INCOMPLETE' USING ERRCODE='23514'; END IF;
 ELSE RAISE EXCEPTION 'NATIVE_V3_REMOVE_MODE' USING ERRCODE='23514'; END IF;
 -- Retain the protected full-xid format witness through COMMIT even when
 -- constraints were forced early. It is never a current grant in a later xid.
 -- Future durable expiry/purge receipt and private retention work own cleanup.
 DELETE FROM app_private.native_request_write_context_v3 c
  WHERE c.transaction_id=NEW.transaction_id AND c.backend_pid=NEW.backend_pid
   AND c.owner_user_id=NEW.owner_user_id AND c.original_request_id=NEW.original_request_id
   AND c.served_request_id=NEW.served_request_id;
 RETURN NULL;
END $$;

CREATE TRIGGER z_native_v3_write_context BEFORE INSERT OR UPDATE
 ON app_private.native_request_write_context_v3
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_native_request_context_v3();
CREATE CONSTRAINT TRIGGER z_native_v3_origin_complete AFTER INSERT
 ON app_private.native_original_context_v3 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app_private.check_native_original_context_v3();
CREATE CONSTRAINT TRIGGER z_native_v3_removal_complete AFTER INSERT
 ON app_private.native_request_write_context_v3 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW WHEN (NEW.operation<>'CREATE')
 EXECUTE FUNCTION app_private.check_native_request_removal_context_v3();
"""
)

for _table in NATIVE_REQUEST_V3_CONTEXT_WITNESSED_TABLES:
    NATIVE_REQUEST_V3_CONTEXT_INSTALL_SQL += (
        f"CREATE TRIGGER z_native_v3_origin_insert AFTER INSERT ON ml.{_table}\n"
        "FOR EACH ROW EXECUTE FUNCTION app_private.witness_native_original_insert_v3();\n"
    )
for _table in NATIVE_REQUEST_V3_CONTEXT_GUARDED_TABLES:
    NATIVE_REQUEST_V3_CONTEXT_INSTALL_SQL += (
        f"CREATE TRIGGER z_native_v3_typed_write BEFORE INSERT OR UPDATE OR DELETE ON ml.{_table}\n"
        "FOR EACH ROW EXECUTE FUNCTION app_private.guard_native_request_write_v3();\n"
    )
for _table in NATIVE_REQUEST_V3_CONTEXT_PRIVATE_TABLES:
    NATIVE_REQUEST_V3_CONTEXT_INSTALL_SQL += f"REVOKE ALL ON app_private.{_table} FROM PUBLIC;\n"
NATIVE_REQUEST_V3_CONTEXT_INSTALL_SQL += (
    "REVOKE ALL ON FUNCTION "
    + ",".join("app_private." + signature for signature in NATIVE_REQUEST_V3_CONTEXT_FUNCTIONS)
    + " FROM PUBLIC;\n"
)

NATIVE_REQUEST_V3_CONTEXT_DROP_SQL = "\n".join(
    (
        *(
            f"DROP TRIGGER z_native_v3_typed_write ON ml.{table};"
            for table in reversed(NATIVE_REQUEST_V3_CONTEXT_GUARDED_TABLES)
        ),
        *(
            f"DROP TRIGGER z_native_v3_origin_insert ON ml.{table};"
            for table in reversed(NATIVE_REQUEST_V3_CONTEXT_WITNESSED_TABLES)
        ),
        "DROP TRIGGER z_native_v3_removal_complete ON app_private.native_request_write_context_v3;",
        "DROP TRIGGER z_native_v3_origin_complete ON app_private.native_original_context_v3;",
        "DROP TRIGGER z_native_v3_write_context ON app_private.native_request_write_context_v3;",
        *(
            f"DROP FUNCTION app_private.{signature};"
            for signature in reversed(NATIVE_REQUEST_V3_CONTEXT_FUNCTIONS)
        ),
        "DROP TABLE app_private.native_request_deletion_witness_v3;",
        "DROP TABLE app_private.native_request_write_context_v3;",
        "DROP TABLE app_private.native_original_insert_witness_v3;",
        "DROP TABLE app_private.native_original_context_v3;",
    )
)
