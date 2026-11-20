"""Dormant Source3 shared/source lifecycle and export trigger definitions.

Importing this module installs nothing. B's phased dispatcher owns all trigger
attachments and predecessor catalog/body/index qualification. C alone privately
arms removal contexts after actual expiry/privacy evidence. Original-only purge
is unsupported: HISTORY_PURGE removes both parents and every typed pair row.

Do not deploy until actual G same-owner work admission and protected dirty-epoch
accounting cover every deferred invocation, including independent ORIGINAL and
SERVED APIR3 work. These definitions deliberately provide no passing substitute,
new allowance, memo, budget reset, or runtime armer. Current deletion witnesses
must survive every deferred check. The explicit B assembler supplies owner-purge
final cleanup after draining those checks in the protected privacy context.
Real PostgreSQL compilation and legacy lifecycle cutover are exercised; complete
Source3 closure, concurrency, work accounting and fanout remain unqualified.
"""

from __future__ import annotations

NATIVE_REQUEST_V3_LIFECYCLE_REQUIRED_FUNCTIONS = (
    "app_private.assert_native_atomic_graph_v3(uuid)",
    "app_private.assert_native_served_truth_v3(uuid)",
    "app_private.guard_native_request_write_v3()",
    "app_private.protect_native_request_context_v3()",
    "app_private.check_native_request_removal_context_v3()",
    "app_private.guard_native_export()",
    "app_private.invalidate_native_export_dependents()",
)
NATIVE_REQUEST_V3_LIFECYCLE_SHARED_TABLES = (
    "recommendation_request",
    "recommendation_item",
    "recommendation_serving_decision",
    "recommendation_served_item",
    "recommendation_request_replay_payload",
)
NATIVE_REQUEST_V3_LIFECYCLE_SOURCE_TABLES = (
    "native_original_capture_v3",
    "native_capture_source_event_v3",
    "native_capture_lineage_cursor_v3",
    "native_capture_replay_v3",
    "native_atomic_capture_connection_v3",
)
NATIVE_REQUEST_V3_LIFECYCLE_FUNCTIONS = (
    "native_v3_lifecycle_pair(uuid)",
    "native_v3_check_retained_pair(uuid,uuid,uuid)",
    "native_v3_check_removed_pair(uuid,uuid,uuid)",
    "native_v3_check_source_pair(uuid,uuid,uuid)",
    "count_native_item_insert_v3()",
    "check_native_shared_truth_v3()",
    "check_native_source_lifecycle_v3()",
    "guard_native_source_removal_v3()",
    "invalidate_native_export_source_v3()",
)


def _prerequisites() -> str:
    checks = "\n".join(
        f"IF to_regprocedure('{function}') IS NULL THEN "
        "RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_REAL_HELPER_REQUIRED' "
        "USING ERRCODE='55000'; END IF;"
        for function in NATIVE_REQUEST_V3_LIFECYCLE_REQUIRED_FUNCTIONS
    )
    return "DO $$ BEGIN\n" + checks + "\nEND $$;\n"


NATIVE_REQUEST_V3_LIFECYCLE_INSTALL_SQL = (
    _prerequisites()
    + r"""
-- Private definitions only. B owns trigger attachment and exact predecessor pins.
-- No SQL execution or deployment qualification. External G admission/dirty epoch
-- remains an installation blocker: every invocation must prepay complete actual
-- graph work on the existing physical owner, including separate ORIGINAL/SERVED
-- APIR3 measurements and nested causal work. These functions never memoize,
-- create a budget, reset work, or replace the shared compiler's 8 MiB stack.

CREATE FUNCTION app_private.native_v3_lifecycle_pair(p_request uuid)
RETURNS TABLE(pair_owner uuid,pair_original uuid,pair_served uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE candidate record; matches integer:=0;
BEGIN
 IF p_request IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_ID' USING ERRCODE='23514'; END IF;
 -- Only fixed scalar protected bindings establish a role. Each arm is bounded;
 -- two distinct triples are sufficient to refuse cross-owner/cross-role reuse.
 FOR candidate IN
  SELECT binding.owner_user_id,binding.original_request_id,binding.served_request_id
  FROM (
   (SELECT r.owner_user_id,r.original_request_id,r.served_request_id
    FROM ml.recommendation_request_root_v3 r
    WHERE r.original_request_id=p_request OR r.served_request_id=p_request LIMIT 2)
   UNION
   (SELECT c.owner_user_id,c.original_request_id,c.served_request_id
    FROM ml.native_atomic_capture_connection_v3 c
    WHERE c.original_request_id=p_request OR c.served_request_id=p_request LIMIT 2)
   UNION
   (SELECT c.owner_user_id,c.original_request_id,c.served_request_id
    FROM app_private.native_request_write_context_v3 c
    WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
     AND (c.original_request_id=p_request OR c.served_request_id=p_request) LIMIT 2)
   UNION
   (SELECT w.owner_user_id,w.original_request_id,w.served_request_id
    FROM app_private.native_request_deletion_witness_v3 w
    WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
     AND w.operation='HISTORY_PURGE' AND w.original_capture_format_version=3
     AND w.request_hash_format_version=3
     AND (w.original_request_id=p_request OR w.served_request_id=p_request) LIMIT 2)
  ) binding LIMIT 2
 LOOP
  matches:=matches+1;
  pair_owner:=candidate.owner_user_id;
  pair_original:=candidate.original_request_id;
  pair_served:=candidate.served_request_id;
 END LOOP;
 IF matches>1 OR (matches=1 AND (pair_owner IS NULL OR pair_original IS NULL
  OR pair_served IS NULL OR pair_original=pair_served)) THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_ROLE_CONFLICT' USING ERRCODE='23514'; END IF;
 IF matches=1 THEN RETURN NEXT; RETURN; END IF;
 -- A broken typed Source3 row cannot become an ordinary request by losing its
 -- pair binding. Common contract1 alone, however, is never a native declaration.
 IF EXISTS(SELECT 1 FROM ml.native_original_capture_format f
   WHERE f.original_request_id=p_request AND f.original_capture_format_version=3)
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   WHERE f.served_request_id=p_request AND f.request_hash_format_version=3)
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_v3 b WHERE b.original_request_id=p_request)
  OR EXISTS(SELECT 1 FROM ml.native_capture_source_event_v3 e WHERE e.original_request_id=p_request)
  OR EXISTS(SELECT 1 FROM ml.native_capture_lineage_cursor_v3 k
   WHERE k.original_request_id=p_request)
  OR EXISTS(SELECT 1 FROM ml.native_capture_replay_v3 p WHERE p.original_request_id=p_request) THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_PAIR_MISSING' USING ERRCODE='23514'; END IF;
 RETURN;
END $$;

CREATE FUNCTION app_private.native_v3_check_retained_pair(
 p_owner uuid,p_original uuid,p_served uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_owner IS NULL OR p_original IS NULL OR p_served IS NULL OR p_original=p_served
  OR NOT EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
   JOIN ml.native_original_capture_format o ON o.original_request_id=r.original_request_id
    AND o.owner_user_id=r.owner_user_id AND o.original_capture_format_version=3
   JOIN ml.native_served_request_hash_format f ON f.served_request_id=r.served_request_id
    AND f.owner_user_id=r.owner_user_id AND f.request_hash_format_version=3
   JOIN ml.recommendation_request original
    ON original.recommendation_request_id=r.original_request_id
    AND original.user_id=r.owner_user_id AND original.serving_contract_version=1
   JOIN ml.recommendation_request served ON served.recommendation_request_id=r.served_request_id
    AND served.user_id=r.owner_user_id AND served.serving_contract_version=2
   WHERE r.owner_user_id=p_owner AND r.original_request_id=p_original
    AND r.served_request_id=p_served)
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_format o
   WHERE o.original_request_id=p_served)
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   WHERE f.served_request_id=p_original)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request_replay_payload p
   WHERE p.recommendation_request_id IN (p_original,p_served)) THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_RETAINED_PAIR' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.native_v3_check_removed_pair(
 p_owner uuid,p_original uuid,p_served uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 -- The context's deferred checker can run first and delete its context. Its
 -- independently protected typed deletion witness must survive through COMMIT.
 -- Absence alone, a prior transaction, or a caller-supplied reason grants nothing.
 IF p_owner IS NULL OR p_original IS NULL OR p_served IS NULL OR p_original=p_served
  OR NOT EXISTS(SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
    AND w.owner_user_id=p_owner AND w.original_request_id=p_original
    AND w.served_request_id=p_served AND w.operation='HISTORY_PURGE'
    AND w.original_capture_format_version=3 AND w.request_hash_format_version=3)
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_format f
   WHERE f.original_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   WHERE f.served_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_v3 b
   WHERE b.original_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.native_capture_source_event_v3 e
   WHERE e.original_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.native_capture_lineage_cursor_v3 k
   WHERE k.original_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.native_capture_replay_v3 p
   WHERE p.original_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
   WHERE r.original_request_id IN (p_original,p_served)
    OR r.served_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection_v3 c
   WHERE c.original_request_id IN (p_original,p_served)
    OR c.served_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.recommendation_request q
   WHERE q.recommendation_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.recommendation_item i
   WHERE i.recommendation_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.recommendation_serving_decision d
   WHERE d.recommendation_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.recommendation_served_item i
   WHERE i.recommendation_request_id IN (p_original,p_served))
  OR EXISTS(SELECT 1 FROM ml.recommendation_request_replay_payload p
   WHERE p.recommendation_request_id IN (p_original,p_served)) THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_PURGE_INCOMPLETE' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.native_v3_check_source_pair(
 p_owner uuid,p_original uuid,p_served uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
  WHERE r.original_request_id=p_original AND r.served_request_id=p_served) THEN
  PERFORM app_private.native_v3_check_removed_pair(p_owner,p_original,p_served);
  RETURN;
 END IF;
 PERFORM app_private.native_v3_check_retained_pair(p_owner,p_original,p_served);
 IF EXISTS(SELECT 1 FROM ml.native_original_capture_v3 b
  WHERE b.original_request_id=p_original) THEN
  -- Actual eight-table graph; original payloads and served rows are independently
  -- measured by the pinned implementation. There is no once-per-xid shortcut.
  PERFORM app_private.assert_native_atomic_graph_v3(p_original);
 ELSE
  IF EXISTS(SELECT 1 FROM ml.native_capture_source_event_v3 e
    WHERE e.original_request_id=p_original)
   OR EXISTS(SELECT 1 FROM ml.native_capture_lineage_cursor_v3 k
    WHERE k.original_request_id=p_original)
   OR EXISTS(SELECT 1 FROM ml.native_capture_replay_v3 p
    WHERE p.original_request_id=p_original)
   OR EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection_v3 c
    WHERE c.original_request_id=p_original OR c.served_request_id=p_served) THEN
   RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_PARTIAL_WITHDRAWAL' USING ERRCODE='23514'; END IF;
  -- BEFORE guards already required C's privately armed removal context. It may
  -- have been cleared by its own deferred check. Retained truth grants no live use.
  PERFORM app_private.assert_native_served_truth_v3(p_served);
 END IF;
END $$;

-- Count only actual inserts in the private creation transaction. Each
-- deferred shared-row event consumes one ticket. The last event for its
-- table/request must perform the complete current graph check, including when
-- the named constraint is forced before commit. Updates and deletions always
-- take the original checking path.
CREATE TABLE app_private.native_v3_pending_item_check (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL,relation_id oid NOT NULL,
 request_id uuid NOT NULL,remaining integer NOT NULL CHECK(remaining>=1),
 PRIMARY KEY(transaction_id,backend_pid,relation_id,request_id)
);
REVOKE ALL ON TABLE app_private.native_v3_pending_item_check FROM PUBLIC;
CREATE FUNCTION app_private.count_native_item_insert_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP<>'INSERT' OR TG_WHEN<>'AFTER' OR TG_LEVEL<>'ROW' OR TG_NARGS<>0
  OR TG_TABLE_SCHEMA<>'ml' OR TG_TABLE_NAME NOT IN ('recommendation_item','recommendation_served_item')
 THEN RAISE EXCEPTION 'NATIVE_V3_ITEM_TICKET_TRIGGER' USING ERRCODE='55000'; END IF;
 IF current_setting('transaction_isolation')='repeatable read' AND EXISTS(
  SELECT 1 FROM app_private.native_original_context_v3 ctx
  JOIN ml.recommendation_request req ON req.user_id=ctx.owner_user_id
  WHERE ctx.transaction_id=pg_current_xact_id() AND ctx.backend_pid=pg_backend_pid()
   AND req.recommendation_request_id=NEW.recommendation_request_id) THEN
  INSERT INTO app_private.native_v3_pending_item_check AS pending
  VALUES(pg_current_xact_id(),pg_backend_pid(),TG_RELID,NEW.recommendation_request_id,1)
  ON CONFLICT(transaction_id,backend_pid,relation_id,request_id)
   DO UPDATE SET remaining=pending.remaining+1;
 END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION app_private.count_native_item_insert_v3() FROM PUBLIC;

CREATE FUNCTION app_private.check_native_shared_truth_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request_ids uuid[]; request_id uuid; pair record;
BEGIN
 IF TG_WHEN<>'AFTER' OR TG_LEVEL<>'ROW' OR TG_NARGS<>0 OR TG_TABLE_SCHEMA<>'ml'
  OR TG_OP NOT IN ('INSERT','UPDATE','DELETE')
  OR TG_TABLE_NAME NOT IN ('recommendation_request','recommendation_item',
   'recommendation_serving_decision','recommendation_served_item',
   'recommendation_request_replay_payload') THEN
  RAISE EXCEPTION 'NATIVE_V3_SHARED_TRIGGER' USING ERRCODE='55000'; END IF;
 IF TG_OP='INSERT' AND TG_TABLE_NAME IN ('recommendation_item','recommendation_served_item') THEN
  UPDATE app_private.native_v3_pending_item_check AS pending SET remaining=pending.remaining-1
  WHERE pending.transaction_id=pg_current_xact_id() AND pending.backend_pid=pg_backend_pid()
   AND pending.relation_id=TG_RELID AND pending.request_id=NEW.recommendation_request_id
   AND pending.remaining>1;
  IF FOUND THEN RETURN NULL; END IF;
  DELETE FROM app_private.native_v3_pending_item_check AS pending
  WHERE pending.transaction_id=pg_current_xact_id() AND pending.backend_pid=pg_backend_pid()
   AND pending.relation_id=TG_RELID AND pending.request_id=NEW.recommendation_request_id;
 END IF;
 IF TG_OP='INSERT' THEN request_ids:=ARRAY[NEW.recommendation_request_id];
 ELSIF TG_OP='DELETE' THEN request_ids:=ARRAY[OLD.recommendation_request_id];
 ELSE request_ids:=ARRAY[OLD.recommendation_request_id,NEW.recommendation_request_id]; END IF;
 FOREACH request_id IN ARRAY request_ids LOOP
  SELECT p.pair_owner,p.pair_original,p.pair_served INTO pair
   FROM app_private.native_v3_lifecycle_pair(request_id) p;
  IF NOT FOUND THEN CONTINUE; END IF;
  IF NOT EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
   WHERE r.original_request_id=pair.pair_original AND r.served_request_id=pair.pair_served) THEN
   PERFORM app_private.native_v3_check_removed_pair(
    pair.pair_owner,pair.pair_original,pair.pair_served);
  ELSIF request_id=pair.pair_original THEN
   PERFORM app_private.native_v3_check_source_pair(
    pair.pair_owner,pair.pair_original,pair.pair_served);
  ELSE
   PERFORM app_private.native_v3_check_retained_pair(
    pair.pair_owner,pair.pair_original,pair.pair_served);
   -- SERVED/historical/causal entry does not recurse through live source facts.
   PERFORM app_private.assert_native_served_truth_v3(pair.pair_served);
  END IF;
 END LOOP;
 RETURN NULL;
END $$;

CREATE FUNCTION app_private.check_native_source_lifecycle_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE original_id uuid; owner_id uuid; pair record;
BEGIN
 IF TG_WHEN<>'AFTER' OR TG_LEVEL<>'ROW' OR TG_NARGS<>0 OR TG_TABLE_SCHEMA<>'ml'
  OR TG_OP NOT IN ('INSERT','UPDATE','DELETE')
  OR TG_TABLE_NAME NOT IN ('native_original_capture_v3','native_capture_source_event_v3',
   'native_capture_lineage_cursor_v3','native_capture_replay_v3',
   'native_atomic_capture_connection_v3') THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_TRIGGER' USING ERRCODE='55000'; END IF;
 IF TG_OP='UPDATE' THEN
  -- Frozen cursor is ACTIVE-only. Source generation is not consent generation.
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_IMMUTABLE' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN original_id:=OLD.original_request_id; owner_id:=OLD.owner_user_id;
 ELSE original_id:=NEW.original_request_id; owner_id:=NEW.owner_user_id; END IF;
 SELECT p.pair_owner,p.pair_original,p.pair_served INTO pair
  FROM app_private.native_v3_lifecycle_pair(original_id) p;
 IF NOT FOUND OR pair.pair_owner IS DISTINCT FROM owner_id
  OR pair.pair_original IS DISTINCT FROM original_id THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_PAIR' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='native_atomic_capture_connection_v3' THEN
  IF (TG_OP='DELETE' AND OLD.served_request_id IS DISTINCT FROM pair.pair_served)
   OR (TG_OP='INSERT' AND NEW.served_request_id IS DISTINCT FROM pair.pair_served) THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_SERVED_PAIR' USING ERRCODE='23514'; END IF;
 END IF;
 PERFORM app_private.native_v3_check_source_pair(
  pair.pair_owner,pair.pair_original,pair.pair_served);
 RETURN NULL;
END $$;

REVOKE ALL ON FUNCTION app_private.native_v3_lifecycle_pair(uuid),
 app_private.native_v3_check_retained_pair(uuid,uuid,uuid),
 app_private.native_v3_check_removed_pair(uuid,uuid,uuid),
 app_private.native_v3_check_source_pair(uuid,uuid,uuid),
 app_private.check_native_shared_truth_v3(),
 app_private.check_native_source_lifecycle_v3() FROM PUBLIC;

-- REVIEW ONLY: function definition, no trigger attachment or migration activation.
-- Frozen ABI cc92; context ab8, model e224, graph c016.
-- DEPLOYMENT HOLD: C's actual privileged lifecycle writer must establish real
-- retention/expiry/privacy negatives BEFORE internally inserting this context.
-- The existing context protector binds identity, but does NOT prove negatives.
-- This function neither arms a context nor invents a public removal grant.
-- C must delete all five live tables BEFORE root/formats/common parents. Requiring
-- retained typed identity on every BEFORE invocation deliberately refuses an
-- original-only/no-root purge. A HISTORY witness alone cannot replace that root.
-- Context must still exist at deletion time; forced deferred context cleanup
-- cannot be repaired by consulting an old witness or by reconstructing context.
-- Complete removal is separately checked by the deferred lifecycle assertion;
-- this bounded scalar guard does not reparse documents or assert a partial graph.
CREATE FUNCTION app_private.guard_native_source_removal_v3()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $native_source_removal_v3$
DECLARE
 v_owner uuid;
 v_original uuid;
 v_served uuid;
 v_operation text;
 v_device uuid;
 v_journal uuid;
 v_server uuid;
 v_identity bigint;
BEGIN
 IF TG_OP <> 'DELETE' OR TG_WHEN <> 'BEFORE' OR TG_LEVEL <> 'ROW'
  OR TG_NARGS <> 0 OR TG_TABLE_SCHEMA <> 'ml'
  OR TG_TABLE_NAME NOT IN (
   'native_original_capture_v3', 'native_capture_source_event_v3',
   'native_capture_lineage_cursor_v3', 'native_capture_replay_v3',
   'native_atomic_capture_connection_v3') THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_REMOVAL_TRIGGER' USING ERRCODE='23514';
 END IF;
 IF current_setting('transaction_isolation') IS DISTINCT FROM 'repeatable read'
  AND NOT app_private.native_v3_privacy_scope(OLD.owner_user_id) THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_REMOVAL_ISOLATION' USING ERRCODE='55000';
 END IF;
 v_owner := OLD.owner_user_id;
 v_original := OLD.original_request_id;
 IF v_owner IS NULL OR v_original IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_REMOVAL_IDENTITY' USING ERRCODE='23514';
 END IF;

 -- The protected UNIQUE(full xid, PID, original) makes this an exact lookup.
 -- No JSON hydration, whole personal row, caller argument, or generic GUC mode.
 SELECT c.served_request_id,c.operation,c.device_id,c.journal_epoch,
        c.server_instance_id,c.identity_epoch
 INTO v_served,v_operation,v_device,v_journal,v_server,v_identity
 FROM app_private.native_request_write_context_v3 c
 WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
  AND c.owner_user_id=v_owner AND c.original_request_id=v_original;
 IF NOT FOUND OR v_served IS NULL OR v_served=v_original
  OR v_operation IS NULL OR v_operation NOT IN ('LIVE_REMOVE','HISTORY_PURGE')
  OR v_device IS NULL OR v_journal IS NULL OR v_server IS NULL
  OR v_identity IS NULL OR v_identity<1 THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_REMOVAL_CONTEXT' USING ERRCODE='55000';
 END IF;

 -- Every row must still belong to the exact original/served typed identity.
 -- Do not depend on a connection/bundle that an earlier source DELETE removed.
 IF NOT EXISTS (
  SELECT 1 FROM ml.recommendation_request_root_v3 r
  JOIN ml.native_original_capture_format o
   ON o.original_request_id=r.original_request_id AND o.owner_user_id=r.owner_user_id
    AND o.original_capture_format_version=3
  JOIN ml.native_served_request_hash_format f
   ON f.served_request_id=r.served_request_id AND f.owner_user_id=r.owner_user_id
    AND f.request_hash_format_version=3
  WHERE r.owner_user_id=v_owner AND r.original_request_id=v_original
   AND r.served_request_id=v_served
   AND r.original_capture_format_version=3 AND r.request_hash_format_version=3
   AND r.device_id=v_device AND r.journal_epoch=v_journal
   AND r.server_instance_id=v_server AND r.identity_epoch=v_identity
 ) THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_REMOVAL_TYPED_BINDING' USING ERRCODE='23514';
 END IF;
 IF TG_TABLE_NAME='native_original_capture_v3' THEN
  IF OLD.original_capture_format_version IS DISTINCT FROM 3 THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_REMOVAL_ROW_FORMAT' USING ERRCODE='23514';
  END IF;
 ELSIF TG_TABLE_NAME='native_atomic_capture_connection_v3' THEN
  IF OLD.served_request_id IS DISTINCT FROM v_served
   OR OLD.original_capture_format_version IS DISTINCT FROM 3
   OR OLD.request_hash_format_version IS DISTINCT FROM 3 THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_REMOVAL_ROW_BINDING' USING ERRCODE='23514';
  END IF;
 END IF;

 IF v_operation='HISTORY_PURGE' AND NOT EXISTS (
  SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
  WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
   AND w.owner_user_id=v_owner AND w.original_request_id=v_original
   AND w.served_request_id=v_served AND w.operation='HISTORY_PURGE'
   AND w.original_capture_format_version=3 AND w.request_hash_format_version=3
 ) THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_REMOVAL_WITNESS' USING ERRCODE='55000';
 END IF;
 RETURN OLD;
END;
$native_source_removal_v3$;
REVOKE ALL ON FUNCTION app_private.guard_native_source_removal_v3() FROM PUBLIC;

-- Private definition fragment; not installed, activated or PostgreSQL-qualified.
-- Handoff: VERTICAL_PREDECESSOR_ABI_HANDOFF.json
-- SHA256 cc92fb26c94c9085fdb63e8632f827e6e032dd14a6f367ecc932187c475146f6.
-- Source precedence predecessor: 0100_native_export_lifecycle.py
-- SHA256 c2011a6fb755023c512e3c240f0717bc8e7e8487d47361516138584e2b97e4c7.
--
-- The phased dispatcher installer owns BOTH trigger attachments, exactly:
-- native_export_source3_negative BEFORE DELETE FOR EACH ROW on
-- ml.native_original_capture_v3 and ml.native_capture_source_event_v3,
-- EXECUTE FUNCTION app_private.invalidate_native_export_source_v3().
-- Require tgtype=11, tgenabled='O', NOT tgisinternal, tgnargs=0 and exact tgfoid.
-- The protected source-removal guards must remain attached before this trigger;
-- this function invalidates exports and never authorizes the underlying deletion.
--
-- Required retained predecessors, independently checked/pinned by the installer:
-- * ix_native_export_owner_lookup on ml.native_export_owner(user_id,export_id,generation),
--   valid/ready/nonpartial, plus native_export PK(export_id,generation).
-- * native_export_guard BEFORE I/U/D, exact installed guard_native_export body.
-- * native_export_dependents AFTER UPDATE ROW on ml.native_export, tgtype=17,
--   enabled/noninternal/noargs, exact installed invalidate_native_export_dependents tgfoid/body.
-- * ix_native_export_dependency_parent(packet_id,packet_generation), valid/ready/nonpartial.
-- The actual 0102_native_dataset_export amendment extends PACKET->AGGREGATE
-- propagation to AGGREGATE->DATASET. Preserve and pin that installed body too;
-- the 0100 file hash alone does NOT identify the installed amended definition.
-- Existing owner/source2 functions, membership guards, dependency triggers, export
-- generation, expiry, inventory and cleanup obligations are not replaced here.
-- Pin pg_get_functiondef/catalog/index identities under the installer table locks.
-- No full export metadata read, custom dependency walk, publication, budget reset,
-- owner-membership deletion or new write to an already strongly invalidated row.

CREATE FUNCTION app_private.invalidate_native_export_source_v3()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $native_export_source_v3$
BEGIN
 IF TG_OP <> 'DELETE' OR TG_WHEN <> 'BEFORE' OR TG_LEVEL <> 'ROW'
  OR TG_TABLE_SCHEMA <> 'ml' OR TG_NARGS <> 0
  OR TG_TABLE_NAME NOT IN ('native_original_capture_v3','native_capture_source_event_v3') THEN
  RAISE EXCEPTION 'native_export_source_v3_trigger_invalid' USING ERRCODE='55000';
 END IF;
 IF OLD.owner_user_id IS NULL THEN
  RAISE EXCEPTION 'native_export_source_v3_owner_invalid' USING ERRCODE='55000';
 END IF;
 UPDATE ml.native_export e SET state='INVALIDATED',reason='SOURCE_REMOVED'
  FROM ml.native_export_owner o
  WHERE (e.export_id,e.generation)=(o.export_id,o.generation)
   AND o.user_id=OLD.owner_user_id
   AND (e.state IN ('ALLOCATED','READY') OR
    (e.state='INVALIDATED' AND e.reason IN
     ('OWNER_DELETION_PENDING','OWNER_ACCESS_CLOSED','OWNER_REMOVED','DEPENDENCY_INVALIDATED')));
 RETURN OLD;
END;
$native_export_source_v3$;

REVOKE ALL ON FUNCTION app_private.invalidate_native_export_source_v3() FROM PUBLIC;

"""
)

# B must detach its triggers first. No CASCADE, predecessor replacement or
# claim of a complete downgrade is supplied by this definition-only module.
NATIVE_REQUEST_V3_LIFECYCLE_DROP_SQL = "\n".join(
    f"DROP FUNCTION app_private.{function};"
    for function in reversed(NATIVE_REQUEST_V3_LIFECYCLE_FUNCTIONS)
) + "\nDROP TABLE app_private.native_v3_pending_item_check;"
