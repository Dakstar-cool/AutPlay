"""Dormant, independently typed Source2/3 retirement and bounded cleanup.

Importing this module runs no SQL. Install its definitions before B's private
retirement hook is bound. B's exact legacy witness/pack tables must exist before
the functions are used. They are deliberately not declared as PL/pgSQL rowtypes,
so definition order does not require disabling check_function_bodies.

The privileged caller starts REPEATABLE READ before its first row access, holds
the actual server/owner/publication/request lock prefix, and prepays every real
graph check on its existing external work owner. Compiler frames below share
the actual transaction/backend stack; they are not a grant or a work allowance.
Current-xid witnesses remain through deferred checks. Final privacy cleanup of
those witnesses requires a separately reviewed outer post-constraint hook.
"""

from typing import Final

_COLUMNS: Final = (
    "owner_user_id,served_request_id,original_request_id,request_hash_format_version,"
    "original_capture_format_version,served_request_sha256,decision_sha256,served_items_sha256,"
    "original_request_sha256,capture_sha256,request_root_sha256,created_at,truth_expires_at,"
    "retain_until,purged_at,transaction_id,backend_pid,reason"
)

NATIVE_SERVED_RETIREMENT_V3_FUNCTIONS: Final = (
    "native_v3_retirement_facts(uuid,uuid)",
    "record_native_served_retirement_v3(uuid,uuid)",
    "_c3_record_native_served_retirement_v3(uuid,uuid)",
    "native_v3_resolve_retired_served_format(uuid,uuid)",
    "native_v3_retirement_graph_absent(uuid,uuid)",
    "native_v3_retirement_cleanup_authorized(uuid,uuid)",
    "native_v3_retirement_cleanup_allowed(uuid,uuid,text)",
    "guard_native_served_retirement_v3()",
    "guard_native_retirement_cleanup_v3()",
    "guard_native_retirement_pack_cleanup_v3()",
    "guard_native_retirement_owner_cleanup_v3()",
)

NATIVE_SERVED_RETIREMENT_V3_INSTALL_SQL: Final = r"""
CREATE TABLE app_private.native_served_retirement_v3 (
 owner_user_id uuid NOT NULL,served_request_id uuid PRIMARY KEY,original_request_id uuid,
 request_hash_format_version integer NOT NULL CHECK(request_hash_format_version IN (2,3)),
 original_capture_format_version integer,
 served_request_sha256 bytea NOT NULL CHECK(octet_length(served_request_sha256)=32),
 decision_sha256 bytea NOT NULL CHECK(octet_length(decision_sha256)=32),
 served_items_sha256 bytea NOT NULL CHECK(octet_length(served_items_sha256)=32),
 original_request_sha256 bytea,capture_sha256 bytea,request_root_sha256 bytea,
 created_at timestamptz NOT NULL,truth_expires_at timestamptz NOT NULL,
 retain_until timestamptz NOT NULL,purged_at timestamptz NOT NULL,
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL CHECK(backend_pid>0),
 reason text NOT NULL CHECK(reason IN ('EXPIRED','ACCOUNT_DELETION')),
 UNIQUE(owner_user_id,served_request_id),
 CHECK(original_request_id IS NULL OR original_request_id<>served_request_id),
 CHECK(retain_until=created_at+interval '400 days'),
 CHECK((request_hash_format_version=2 AND truth_expires_at=created_at+interval '180 days'
   AND request_root_sha256 IS NULL AND ((original_request_id IS NULL
    AND original_capture_format_version IS NULL AND original_request_sha256 IS NULL
    AND capture_sha256 IS NULL) OR (original_request_id IS NOT NULL
    AND original_capture_format_version IS NOT NULL AND original_capture_format_version=2
    AND original_request_sha256 IS NOT NULL AND octet_length(original_request_sha256)=32
    AND capture_sha256 IS NOT NULL AND octet_length(capture_sha256)=32))) OR
  (request_hash_format_version=3 AND truth_expires_at=created_at+interval '15552000 seconds'
   AND original_request_id IS NOT NULL AND original_capture_format_version IS NOT NULL
   AND original_capture_format_version=3
   AND original_request_sha256 IS NOT NULL AND octet_length(original_request_sha256)=32
   AND capture_sha256 IS NOT NULL AND octet_length(capture_sha256)=32
   AND request_root_sha256 IS NOT NULL AND octet_length(request_root_sha256)=32)),
 CHECK(reason<>'EXPIRED' OR purged_at>=truth_expires_at)
);
CREATE INDEX native_served_retirement_v3_owner
 ON app_private.native_served_retirement_v3(owner_user_id,served_request_id);
CREATE UNIQUE INDEX native_served_retirement_v3_original
 ON app_private.native_served_retirement_v3(original_request_id)
 WHERE original_request_id IS NOT NULL;
-- A fixed content snapshot, not a caller-supplied admission/reason flag.
CREATE TABLE app_private.native_retirement_cleanup_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL CHECK(backend_pid>0),
 owner_user_id uuid NOT NULL,served_request_id uuid NOT NULL,
 request_hash_format_version integer NOT NULL CHECK(request_hash_format_version IN (2,3)),
 served_request_sha256 bytea NOT NULL CHECK(octet_length(served_request_sha256)=32),
 decision_sha256 bytea NOT NULL CHECK(octet_length(decision_sha256)=32),
 served_items_sha256 bytea NOT NULL CHECK(octet_length(served_items_sha256)=32),
 retain_until timestamptz NOT NULL,
 PRIMARY KEY(transaction_id,backend_pid,owner_user_id,served_request_id)
);
CREATE TABLE app_private.native_served_retirement_pack_cleanup_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL CHECK(backend_pid>0),
 owner_user_id uuid NOT NULL,pack_id uuid NOT NULL,served_request_id uuid NOT NULL,
 request_hash_format_version integer NOT NULL CHECK(request_hash_format_version IN (2,3)),
 binding_sha256 bytea NOT NULL CHECK(octet_length(binding_sha256)=32),
 PRIMARY KEY(transaction_id,backend_pid,pack_id)
);
CREATE TABLE app_private.native_retirement_owner_cleanup_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL CHECK(backend_pid>0),
 owner_user_id uuid NOT NULL,privacy_request_id uuid NOT NULL,
 PRIMARY KEY(transaction_id,backend_pid,owner_user_id)
);
REVOKE ALL ON app_private.native_served_retirement_v3,
 app_private.native_retirement_cleanup_v3,
 app_private.native_served_retirement_pack_cleanup_v3,
 app_private.native_retirement_owner_cleanup_v3 FROM PUBLIC;

CREATE FUNCTION app_private.native_v3_retirement_facts(p_owner uuid,p_request uuid)
RETURNS app_private.native_served_retirement_v3
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result app_private.native_served_retirement_v3%ROWTYPE;
 v_format integer; v_privacy boolean; r record; w record; b record; n integer;
BEGIN
 IF (current_setting('transaction_isolation')<>'repeatable read'
  AND NOT app_private.native_v3_privacy_scope(p_owner))
  OR p_owner IS NULL OR p_request IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_TRANSACTION' USING ERRCODE='23514'; END IF;
 SELECT f.request_hash_format_version INTO v_format
 FROM ml.native_served_request_hash_format f
 WHERE f.owner_user_id=p_owner AND f.served_request_id=p_request;
 IF v_format NOT IN (2,3) OR v_format IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_FORMAT_REQUIRED' USING ERRCODE='23514'; END IF;
 v_privacy:=EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
  WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=p_owner)
  AND app_private.privacy_owner_allowed(p_owner);
 result.owner_user_id:=p_owner; result.served_request_id:=p_request;
 result.request_hash_format_version:=v_format;
 result.transaction_id:=pg_current_xact_id(); result.backend_pid:=pg_backend_pid();
 IF v_format=2 THEN
  -- B calls this inside BEFORE parent DELETE after its independently guarded
  -- current typed witness exists. The old child rows have already been removed.
  SELECT q.created_at,q.request_sha256,p.decision_sha256,p.served_items_sha256,
   p.purged_at,p.expires_at,p.reason INTO r
  FROM ml.recommendation_request q
  JOIN ml.native_served_request_hash_format f ON f.served_request_id=q.recommendation_request_id
   AND f.owner_user_id=q.user_id AND f.request_hash_format_version=2
   AND f.created_transaction IS NULL AND f.created_at=q.created_at
   AND f.expires_at=q.created_at+interval '180 days'
  JOIN app_private.native_served_authority_purge_receipt p
   ON p.recommendation_request_id=q.recommendation_request_id AND p.owner_user_id=q.user_id
   AND p.request_sha256=q.request_sha256 AND p.expires_at=q.created_at+interval '400 days'
  WHERE q.user_id=p_owner AND q.recommendation_request_id=p_request
   AND q.serving_contract_version=2;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM app_private.native_served_authority_context c
   WHERE c.transaction_id=txid_current() AND c.recommendation_request_id=p_request
    AND c.operation='PURGE') THEN
   RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CONTENT_RECEIPT' USING ERRCODE='23514'; END IF;
  SELECT x.transaction_id,x.backend_pid INTO w
  FROM app_private.native_legacy_retirement_witness_v3 x
  WHERE x.transaction_id=pg_current_xact_id() AND x.backend_pid=pg_backend_pid()
   AND x.owner_user_id=p_owner AND x.request_id=p_request AND x.served_request_id=p_request
   AND x.request_role='SERVED' AND x.format_version=2 AND x.request_sha256=r.request_sha256
   AND x.decision_sha256=r.decision_sha256 AND x.served_items_sha256=r.served_items_sha256
   AND x.created_at=r.created_at AND x.expires_at=r.created_at+interval '180 days'
   AND x.receipt_expires_at=r.expires_at AND x.reason=r.reason;
  IF NOT FOUND OR (r.reason='ACCOUNT_DELETION' AND NOT v_privacy)
   OR (r.reason='EXPIRED' AND (clock_timestamp()<r.created_at+interval '180 days'
    OR r.purged_at<r.created_at+interval '180 days')) THEN
   RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_TYPED_WITNESS' USING ERRCODE='23514'; END IF;
  -- A real atomic original is optional for old standalone served2. Never
  -- manufacture an original ID/hash from the opaque served request digest.
  SELECT count(*) INTO n FROM (SELECT x.original_request_id FROM
   app_private.native_legacy_original_binding_v3 x WHERE x.owner_user_id=p_owner
    AND x.served_request_id=p_request
   UNION SELECT x.request_id FROM app_private.native_legacy_retirement_witness_v3 x
    WHERE x.owner_user_id=p_owner AND x.served_request_id=p_request
     AND x.request_role='ORIGINAL' AND x.format_version=2) originals;
  IF n>1 THEN RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CROSS_BOUND' USING ERRCODE='23514'; END IF;
  IF n=1 THEN
   SELECT x.original_request_id,x.original_request_sha256,x.capture_sha256 INTO b
   FROM app_private.native_legacy_original_binding_v3 x WHERE x.owner_user_id=p_owner
    AND x.served_request_id=p_request AND x.decision_sha256=r.decision_sha256
    AND x.created_at=r.created_at AND x.expires_at=r.created_at+interval '180 days';
   IF NOT FOUND THEN
    SELECT x.request_id AS original_request_id,x.request_sha256 AS original_request_sha256,
     x.capture_sha256 INTO b FROM app_private.native_legacy_retirement_witness_v3 x
    WHERE x.owner_user_id=p_owner AND x.served_request_id=p_request
     AND x.request_role='ORIGINAL' AND x.format_version=2
     AND x.decision_sha256=r.decision_sha256 AND x.created_at=r.created_at
     AND x.expires_at=r.created_at+interval '180 days'
    ORDER BY x.transaction_id,x.backend_pid LIMIT 1;
   END IF;
   IF NOT FOUND THEN RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_ORIGIN' USING ERRCODE='23514'; END IF;
   result.original_request_id:=b.original_request_id; result.original_capture_format_version:=2;
   result.original_request_sha256:=b.original_request_sha256; result.capture_sha256:=b.capture_sha256;
  END IF;
  result.created_at:=r.created_at; result.truth_expires_at:=r.created_at+interval '180 days';
  result.served_request_sha256:=r.request_sha256; result.decision_sha256:=r.decision_sha256;
  result.served_items_sha256:=r.served_items_sha256; result.purged_at:=r.purged_at;
  result.reason:=r.reason;
 ELSE
  SELECT q.owner_user_id,q.original_request_id,q.original_request_sha256,q.capture_sha256,
   q.request_root_sha256,q.created_at,q.expires_at,v.request_sha256,d.decision_sha256,
   d.served_items_sha256 INTO r
  FROM ml.recommendation_request_root_v3 q
  JOIN ml.native_original_capture_format o ON o.original_request_id=q.original_request_id
   AND o.owner_user_id=q.owner_user_id AND o.original_capture_format_version=3
  JOIN ml.native_served_request_hash_format f ON f.served_request_id=q.served_request_id
   AND f.owner_user_id=q.owner_user_id AND f.request_hash_format_version=3
  JOIN ml.recommendation_request v ON v.recommendation_request_id=q.served_request_id
   AND v.user_id=q.owner_user_id AND v.serving_contract_version=2
  JOIN ml.recommendation_request original ON original.recommendation_request_id=q.original_request_id
   AND original.user_id=q.owner_user_id AND original.serving_contract_version=1
   AND original.request_sha256=q.original_request_sha256 AND original.created_at=q.created_at
  JOIN ml.recommendation_serving_decision d ON d.recommendation_request_id=q.served_request_id
   AND d.served_truth_expires_at=q.expires_at
  WHERE q.owner_user_id=p_owner AND q.served_request_id=p_request
   AND q.request_hash_format_version=3 AND q.original_capture_format_version=3
   AND q.expires_at=q.created_at+interval '15552000 seconds'
   AND o.created_at=q.created_at AND f.created_at=q.created_at
   AND o.expires_at=q.expires_at AND f.expires_at=q.expires_at;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM app_private.native_request_write_context_v3 c
   JOIN app_private.native_request_deletion_witness_v3 x ON x.transaction_id=c.transaction_id
    AND x.backend_pid=c.backend_pid AND x.owner_user_id=c.owner_user_id
    AND x.original_request_id=c.original_request_id AND x.served_request_id=c.served_request_id
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
    AND c.owner_user_id=p_owner AND c.served_request_id=p_request
    AND c.original_request_id=r.original_request_id AND c.operation='HISTORY_PURGE'
    AND x.operation='HISTORY_PURGE' AND x.original_capture_format_version=3
    AND x.request_hash_format_version=3) THEN
   RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_HISTORY_CONTEXT' USING ERRCODE='23514'; END IF;
  IF NOT v_privacy AND clock_timestamp()<r.expires_at THEN
   RAISE EXCEPTION 'native_served_retention_active' USING ERRCODE='55000'; END IF;
  -- Every complete graph adds both real APIR3 roles on the same physical stack.
  PERFORM app_private.native_v3_item_rows_byte_length(p_owner,r.original_request_id,'ORIGINAL');
  PERFORM app_private.native_v3_item_rows_byte_length(p_owner,p_request,'SERVED');
  PERFORM app_private.assert_native_atomic_graph_v3(r.original_request_id);
  IF NOT v_privacy AND clock_timestamp()<r.expires_at THEN
   RAISE EXCEPTION 'native_served_retention_active' USING ERRCODE='55000'; END IF;
  result.original_request_id:=r.original_request_id; result.original_capture_format_version:=3;
  result.original_request_sha256:=r.original_request_sha256; result.capture_sha256:=r.capture_sha256;
  result.request_root_sha256:=r.request_root_sha256; result.created_at:=r.created_at;
  result.truth_expires_at:=r.expires_at; result.served_request_sha256:=r.request_sha256;
  result.decision_sha256:=r.decision_sha256; result.served_items_sha256:=r.served_items_sha256;
  result.purged_at:=clock_timestamp();
  result.reason:=CASE WHEN v_privacy THEN 'ACCOUNT_DELETION' ELSE 'EXPIRED' END;
 END IF;
 result.retain_until:=result.created_at+interval '400 days';
 RETURN result;
END $$;

CREATE FUNCTION app_private._c3_record_native_served_retirement_v3(p_owner uuid,p_request uuid)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE facts app_private.native_served_retirement_v3%ROWTYPE;
BEGIN
 IF current_setting('transaction_isolation')<>'repeatable read'
  AND NOT app_private.native_v3_privacy_scope(p_owner) THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_TRANSACTION' USING ERRCODE='23514'; END IF;
 facts:=app_private.native_v3_retirement_facts(p_owner,p_request);
 INSERT INTO app_private.native_served_retirement_v3 SELECT facts.*;
END $$;

CREATE FUNCTION app_private.record_native_served_retirement_v3(p_owner uuid,p_request uuid)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE frame integer;
BEGIN
 IF current_setting('transaction_isolation')<>'repeatable read'
  AND NOT app_private.native_v3_privacy_scope(p_owner) THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_TRANSACTION' USING ERRCODE='23514'; END IF;
 -- Fixed scalar facts/typed rows from all nested capture/guard frames fit8KiB.
 -- Variable graph payloads retain their additional real nested compiler frames.
 frame:=app_private.native_v3_compiler_enter(8192);
 PERFORM app_private._c3_record_native_served_retirement_v3(p_owner,p_request);
 PERFORM app_private.native_v3_compiler_exit(frame);
END $$;

CREATE FUNCTION app_private.native_v3_retirement_graph_absent(p_owner uuid,p_request uuid)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE original uuid;
BEGIN
 SELECT t.original_request_id INTO original FROM app_private.native_served_retirement_v3 t
 WHERE t.owner_user_id=p_owner AND t.served_request_id=p_request;
 IF NOT FOUND THEN RETURN false; END IF;
 RETURN NOT EXISTS(SELECT 1 FROM ml.recommendation_request q
   WHERE q.recommendation_request_id IN (p_request,original))
  AND NOT EXISTS(SELECT 1 FROM ml.recommendation_item q
   WHERE q.recommendation_request_id IN (p_request,original))
  AND NOT EXISTS(SELECT 1 FROM ml.recommendation_serving_decision q
   WHERE q.recommendation_request_id IN (p_request,original))
  AND NOT EXISTS(SELECT 1 FROM ml.recommendation_served_item q
   WHERE q.recommendation_request_id IN (p_request,original))
  AND NOT EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   WHERE f.served_request_id IN (p_request,original))
  AND NOT EXISTS(SELECT 1 FROM ml.native_original_capture_format f
   WHERE f.original_request_id IN (p_request,original))
  AND NOT EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
   WHERE r.served_request_id IN (p_request,original) OR r.original_request_id IN (p_request,original))
  AND NOT EXISTS(SELECT 1 FROM ml.native_original_capture_v3 b WHERE b.original_request_id=original)
  AND NOT EXISTS(SELECT 1 FROM ml.native_capture_source_event_v3 e WHERE e.original_request_id=original)
  AND NOT EXISTS(SELECT 1 FROM ml.native_capture_lineage_cursor_v3 c WHERE c.original_request_id=original)
  AND NOT EXISTS(SELECT 1 FROM ml.native_capture_replay_v3 r WHERE r.original_request_id=original)
  AND NOT EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection_v3 c
   WHERE c.original_request_id=original OR c.served_request_id=p_request)
  AND NOT EXISTS(SELECT 1 FROM ml.recommendation_request_replay_payload p
   WHERE p.recommendation_request_id=p_request)
  AND NOT EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b
   WHERE b.recommendation_request_id=p_request)
  AND NOT EXISTS(SELECT 1 FROM ml.recommendation_attribution_association a
   WHERE a.recommendation_request_id=p_request)
  AND NOT EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection c
   WHERE c.original_request_id=original OR c.served_request_id=p_request)
  AND NOT EXISTS(SELECT 1 FROM ml.sona_capture_bundle b WHERE b.recommendation_request_id=original)
  AND NOT EXISTS(SELECT 1 FROM ml.sona_capture_replay_payload p
   WHERE p.recommendation_request_id=original);
END $$;

CREATE FUNCTION app_private.native_v3_resolve_retired_served_format(p_owner uuid,p_request uuid)
RETURNS smallint LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_owner uuid; v_format integer;
BEGIN
 IF p_request IS NULL THEN
  RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM app_private.native_served_retirement_v3 t
  WHERE t.original_request_id=p_request) THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CROSS_BOUND' USING ERRCODE='23514'; END IF;
 SELECT t.owner_user_id,t.request_hash_format_version INTO v_owner,v_format
 FROM app_private.native_served_retirement_v3 t WHERE t.served_request_id=p_request;
 IF NOT FOUND THEN
  IF EXISTS(SELECT 1 FROM app_private.native_served_authority_purge_receipt r
    WHERE r.recommendation_request_id=p_request)
   OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_expiry_tombstone r
    WHERE r.recommendation_request_id=p_request) THEN
   RAISE EXCEPTION 'NATIVE_V3_RETIRED_TYPED_PROVENANCE_REQUIRED' USING ERRCODE='23514'; END IF;
  RETURN NULL;
 END IF;
 IF (p_owner IS NOT NULL AND p_owner<>v_owner)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request q WHERE q.recommendation_request_id=p_request)
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f WHERE f.served_request_id=p_request)
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_format f WHERE f.original_request_id=p_request)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
   WHERE r.served_request_id=p_request OR r.original_request_id=p_request) THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CROSS_BOUND' USING ERRCODE='23514'; END IF;
 -- Expired, unremoved typed evidence remains routable for the cleanup operation.
 -- This metadata says nothing about current consent, live sources or admission.
 RETURN v_format::smallint;
END $$;

CREATE FUNCTION app_private.native_v3_retirement_cleanup_authorized(p_owner uuid,p_request uuid)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_until timestamptz; v_tx xid8; v_privacy boolean;
BEGIN
 IF current_setting('transaction_isolation')<>'repeatable read'
  AND NOT app_private.native_v3_privacy_scope(p_owner) THEN RETURN false; END IF;
 SELECT t.retain_until,t.transaction_id INTO v_until,v_tx
 FROM app_private.native_served_retirement_v3 t WHERE t.owner_user_id=p_owner
  AND t.served_request_id=p_request;
 IF NOT FOUND OR NOT app_private.native_v3_retirement_graph_absent(p_owner,p_request) THEN
  RETURN false; END IF;
 v_privacy:=EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
  WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=p_owner)
  AND app_private.privacy_owner_allowed(p_owner);
 IF v_privacy THEN RETURN true; END IF;
 RETURN v_until<=clock_timestamp() AND v_tx<>pg_current_xact_id()
  AND NOT EXISTS(SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
    AND w.owner_user_id=p_owner AND w.served_request_id=p_request)
  AND NOT EXISTS(SELECT 1 FROM app_private.native_legacy_retirement_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
    AND w.owner_user_id=p_owner AND w.served_request_id=p_request);
END $$;

CREATE FUNCTION app_private.guard_native_retirement_cleanup_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP<>'INSERT' OR NEW.transaction_id IS DISTINCT FROM pg_current_xact_id()
  OR NEW.backend_pid IS DISTINCT FROM pg_backend_pid()
  OR NOT app_private.native_v3_retirement_cleanup_authorized(NEW.owner_user_id,NEW.served_request_id)
  OR NOT EXISTS(SELECT 1 FROM app_private.native_served_retirement_v3 t
   WHERE t.owner_user_id=NEW.owner_user_id AND t.served_request_id=NEW.served_request_id
    AND t.request_hash_format_version=NEW.request_hash_format_version
    AND t.served_request_sha256=NEW.served_request_sha256 AND t.decision_sha256=NEW.decision_sha256
    AND t.served_items_sha256=NEW.served_items_sha256 AND t.retain_until=NEW.retain_until) THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CLEANUP_CONTEXT' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION app_private.native_v3_retirement_cleanup_allowed(
 p_owner uuid,p_row uuid,p_kind text) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE served uuid; v_sha bytea; v_until timestamptz; v_format integer;
BEGIN
 -- The second UUID is the actual OLD row key. The guard supplies its fixed
 -- TG_TABLE_NAME; neither the string nor a true return value creates a scope.
 IF EXISTS(SELECT 1 FROM app_private.native_retirement_owner_cleanup_v3 c
  JOIN app_private.privacy_purge_context p ON p.transaction_id=c.transaction_id
   AND p.owner_id=c.owner_user_id AND p.request_id=c.privacy_request_id
  WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
   AND c.owner_user_id=p_owner) AND app_private.privacy_owner_allowed(p_owner) THEN
  -- Account erasure owns prior, protected typed metadata even when its
  -- ordinary400d retirement anchor has already been legitimately removed.
  IF p_kind='native_legacy_original_binding_v3' THEN
   RETURN EXISTS(SELECT 1 FROM app_private.native_legacy_original_binding_v3 b
    WHERE b.owner_user_id=p_owner AND b.original_request_id=p_row
     AND NOT EXISTS(SELECT 1 FROM ml.recommendation_request q
      WHERE q.recommendation_request_id IN (b.original_request_id,b.served_request_id))
     AND NOT EXISTS(SELECT 1 FROM ml.native_original_capture_format f
      WHERE f.original_request_id=b.original_request_id)
     AND NOT EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
      WHERE f.served_request_id=b.served_request_id));
  ELSIF p_kind='native_legacy_retirement_witness_v3' THEN
   IF EXISTS(SELECT 1 FROM app_private.native_legacy_retirement_witness_v3 w
    WHERE w.owner_user_id=p_owner AND w.request_id=p_row
     AND w.transaction_id=pg_current_xact_id())
     AND NOT app_private.native_v3_owner_final_phase(p_owner) THEN RETURN false; END IF;
   RETURN EXISTS(SELECT 1 FROM app_private.native_legacy_retirement_witness_v3 w
    WHERE w.owner_user_id=p_owner AND w.request_id=p_row
     AND NOT EXISTS(SELECT 1 FROM ml.recommendation_request q
      WHERE q.recommendation_request_id IN (w.request_id,w.served_request_id)));
  ELSIF p_kind='native_pack_format_binding_v3' THEN
   RETURN EXISTS(SELECT 1 FROM app_private.native_pack_format_binding_v3 b
    JOIN ml.native_offline_pack_purge_receipt c ON c.pack_id=b.pack_id
     AND c.owner_user_id=b.owner_user_id AND c.binding_sha256=b.binding_sha256
     AND c.stage='CONTENT' AND c.retain_until=b.attribution_expires_at+interval '220 days'
    JOIN ml.native_offline_pack_purge_receipt r ON r.pack_id=b.pack_id
     AND r.owner_user_id=b.owner_user_id AND r.binding_sha256=b.binding_sha256
     AND r.stage='BINDING' AND r.retain_until=b.attribution_expires_at+interval '220 days'
    WHERE b.owner_user_id=p_owner AND b.pack_id=p_row
     AND NOT EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt x WHERE x.pack_id=p_row)
     AND NOT EXISTS(SELECT 1 FROM ml.offline_recommendation_pack x WHERE x.offline_pack_id=p_row)
     AND NOT EXISTS(SELECT 1 FROM ml.recommendation_attribution_association x WHERE x.offline_pack_id=p_row));
  ELSIF p_kind='native_ordinary_pack_delete_witness_v3' THEN
   RETURN EXISTS(SELECT 1 FROM app_private.native_ordinary_pack_delete_witness_v3 w
     WHERE w.owner_user_id=p_owner AND w.pack_id=p_row
      AND (w.transaction_id<>pg_current_xact_id()
       OR app_private.native_v3_owner_final_phase(p_owner)))
    AND (app_private.native_v3_owner_final_phase(p_owner) OR NOT EXISTS(
     SELECT 1 FROM app_private.native_ordinary_pack_delete_witness_v3 w
     WHERE w.owner_user_id=p_owner AND w.pack_id=p_row AND w.transaction_id=pg_current_xact_id()))
    AND NOT EXISTS(SELECT 1 FROM ml.offline_recommendation_pack p WHERE p.offline_pack_id=p_row)
    AND NOT EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=p_row)
    AND NOT EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt r WHERE r.pack_id=p_row)
    AND NOT EXISTS(SELECT 1 FROM ml.recommendation_attribution_association a WHERE a.offline_pack_id=p_row);
  ELSE RETURN false;
  END IF;
 END IF;
 IF p_kind='native_legacy_original_binding_v3' THEN
  SELECT b.served_request_id INTO served FROM app_private.native_legacy_original_binding_v3 b
  JOIN app_private.native_served_retirement_v3 t ON t.owner_user_id=b.owner_user_id
   AND t.served_request_id=b.served_request_id AND t.original_request_id=b.original_request_id
   AND t.request_hash_format_version=2 AND t.original_capture_format_version=2
   AND t.original_request_sha256=b.original_request_sha256 AND t.capture_sha256=b.capture_sha256
   AND t.decision_sha256=b.decision_sha256 AND t.created_at=b.created_at
   AND t.truth_expires_at=b.expires_at
  WHERE b.owner_user_id=p_owner AND b.original_request_id=p_row;
 ELSIF p_kind='native_legacy_retirement_witness_v3' THEN
  -- No current-xid witness is disposable before all deferred checks finish.
  IF EXISTS(SELECT 1 FROM app_private.native_legacy_retirement_witness_v3 w
   WHERE w.owner_user_id=p_owner AND w.request_id=p_row
    AND w.transaction_id=pg_current_xact_id()) THEN RETURN false; END IF;
  SELECT DISTINCT w.served_request_id INTO served
  FROM app_private.native_legacy_retirement_witness_v3 w
  JOIN app_private.native_served_retirement_v3 t ON t.owner_user_id=w.owner_user_id
   AND t.served_request_id=w.served_request_id AND t.request_hash_format_version=2
   AND t.decision_sha256=w.decision_sha256 AND t.created_at=w.created_at
   AND t.truth_expires_at=w.expires_at AND t.retain_until=w.receipt_expires_at
   AND ((w.request_role='SERVED' AND w.request_id=t.served_request_id
    AND w.request_sha256=t.served_request_sha256 AND w.served_items_sha256=t.served_items_sha256)
    OR (w.request_role='ORIGINAL' AND w.request_id=t.original_request_id
     AND w.request_sha256=t.original_request_sha256 AND w.capture_sha256=t.capture_sha256))
  WHERE w.owner_user_id=p_owner AND w.request_id=p_row;
 ELSIF p_kind='native_pack_format_binding_v3' THEN
  SELECT b.served_request_id,b.binding_sha256,b.attribution_expires_at,b.request_hash_format_version
   INTO served,v_sha,v_until,v_format FROM app_private.native_pack_format_binding_v3 b
  WHERE b.owner_user_id=p_owner AND b.pack_id=p_row;
  IF served IS NULL OR EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=p_row)
   OR EXISTS(SELECT 1 FROM ml.offline_recommendation_pack p WHERE p.offline_pack_id=p_row)
   OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association a WHERE a.offline_pack_id=p_row)
   OR NOT EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt r
    WHERE r.owner_user_id=p_owner AND r.pack_id=p_row AND r.binding_sha256=v_sha
     AND r.stage='CONTENT' AND r.retain_until=v_until+interval '220 days')
   OR NOT EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt r
    WHERE r.owner_user_id=p_owner AND r.pack_id=p_row AND r.binding_sha256=v_sha
     AND r.stage='BINDING' AND r.retain_until=v_until+interval '220 days')
   OR NOT EXISTS(SELECT 1 FROM app_private.native_served_retirement_v3 t
    WHERE t.owner_user_id=p_owner AND t.served_request_id=served
     AND t.request_hash_format_version=v_format AND t.truth_expires_at=v_until) THEN RETURN false; END IF;
 ELSIF p_kind='native_ordinary_pack_delete_witness_v3' THEN
  -- This old fixed witness has no timestamp or native served binding. Its
  -- age cannot be inferred. Only authenticated owner deletion can remove a
  -- prior-xid ordinary witness, through an actual owner cleanup scope.
  RETURN EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
    WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=p_owner)
   AND app_private.privacy_owner_allowed(p_owner)
   AND EXISTS(SELECT 1 FROM app_private.native_retirement_cleanup_v3 c
    WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
     AND c.owner_user_id=p_owner)
   AND EXISTS(SELECT 1 FROM app_private.native_ordinary_pack_delete_witness_v3 w
    WHERE w.owner_user_id=p_owner AND w.pack_id=p_row AND w.transaction_id<>pg_current_xact_id())
   AND NOT EXISTS(SELECT 1 FROM app_private.native_ordinary_pack_delete_witness_v3 w
    WHERE w.owner_user_id=p_owner AND w.pack_id=p_row AND w.transaction_id=pg_current_xact_id())
   AND NOT EXISTS(SELECT 1 FROM ml.offline_recommendation_pack p WHERE p.offline_pack_id=p_row)
   AND NOT EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=p_row)
   AND NOT EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt r WHERE r.pack_id=p_row)
   AND NOT EXISTS(SELECT 1 FROM ml.recommendation_attribution_association a WHERE a.offline_pack_id=p_row);
 ELSE RETURN false;
 END IF;
 RETURN served IS NOT NULL AND app_private.native_v3_retirement_cleanup_authorized(p_owner,served)
  AND EXISTS(SELECT 1 FROM app_private.native_retirement_cleanup_v3 c
   JOIN app_private.native_served_retirement_v3 t ON t.owner_user_id=c.owner_user_id
    AND t.served_request_id=c.served_request_id AND t.request_hash_format_version=c.request_hash_format_version
    AND t.served_request_sha256=c.served_request_sha256 AND t.decision_sha256=c.decision_sha256
    AND t.served_items_sha256=c.served_items_sha256 AND t.retain_until=c.retain_until
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
    AND c.owner_user_id=p_owner AND c.served_request_id=served);
END $$;

CREATE FUNCTION app_private.guard_native_served_retirement_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE expected app_private.native_served_retirement_v3%ROWTYPE; started timestamptz;
BEGIN
 IF TG_OP='INSERT' THEN
  expected:=app_private.native_v3_retirement_facts(NEW.owner_user_id,NEW.served_request_id);
  IF EXISTS(SELECT 1 FROM app_private.native_served_retirement_v3 t
   WHERE t.original_request_id=NEW.served_request_id OR t.served_request_id=NEW.original_request_id) THEN
   RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CROSS_BOUND' USING ERRCODE='23514'; END IF;
  IF NEW.request_hash_format_version=3 THEN
   -- Only Source3 samples a clock in this capture. Source2 must equal the
   -- independently protected old content receipt's exact purged_at.
   SELECT c.started_at INTO started FROM app_private.native_request_write_context_v3 c
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
    AND c.owner_user_id=NEW.owner_user_id AND c.served_request_id=NEW.served_request_id
    AND c.original_request_id=NEW.original_request_id AND c.operation='HISTORY_PURGE';
   IF started IS NULL OR NEW.purged_at<started OR NEW.purged_at<NEW.created_at THEN
    RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CLOCK' USING ERRCODE='23514'; END IF;
   expected.purged_at:=NEW.purged_at;
  END IF;
  IF NEW IS DISTINCT FROM expected OR NEW.purged_at>clock_timestamp() THEN
   RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CONTENT' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 ELSIF TG_OP='DELETE' AND app_private.native_v3_retirement_cleanup_authorized(
   OLD.owner_user_id,OLD.served_request_id) AND EXISTS(
  SELECT 1 FROM app_private.native_retirement_cleanup_v3 c
  WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
   AND c.owner_user_id=OLD.owner_user_id AND c.served_request_id=OLD.served_request_id
   AND c.request_hash_format_version=OLD.request_hash_format_version
   AND c.served_request_sha256=OLD.served_request_sha256 AND c.decision_sha256=OLD.decision_sha256
   AND c.served_items_sha256=OLD.served_items_sha256 AND c.retain_until=OLD.retain_until) THEN
  RETURN OLD;
 END IF;
 RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_IMMUTABLE' USING ERRCODE='55000';
END $$;

CREATE FUNCTION app_private.guard_native_retirement_pack_cleanup_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='INSERT' AND NEW.transaction_id=pg_current_xact_id()
  AND NEW.backend_pid=pg_backend_pid()
  AND app_private.native_v3_retirement_cleanup_allowed(
   NEW.owner_user_id,NEW.pack_id,'native_pack_format_binding_v3')
  AND EXISTS(SELECT 1 FROM app_private.native_pack_format_binding_v3 b
   WHERE b.owner_user_id=NEW.owner_user_id AND b.pack_id=NEW.pack_id
    AND b.served_request_id=NEW.served_request_id
    AND b.request_hash_format_version=NEW.request_hash_format_version
    AND b.binding_sha256=NEW.binding_sha256) THEN
  RETURN NEW;
 ELSIF TG_OP='DELETE' AND (OLD.transaction_id<>pg_current_xact_id()
  OR app_private.native_v3_owner_final_phase(OLD.owner_user_id))
  AND EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=OLD.owner_user_id)
  AND app_private.privacy_owner_allowed(OLD.owner_user_id)
  AND NOT EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=OLD.pack_id)
  AND NOT EXISTS(SELECT 1 FROM ml.offline_recommendation_pack p WHERE p.offline_pack_id=OLD.pack_id)
  AND NOT EXISTS(SELECT 1 FROM ml.recommendation_attribution_association a WHERE a.offline_pack_id=OLD.pack_id)
  AND NOT EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt r WHERE r.pack_id=OLD.pack_id)
  AND NOT EXISTS(SELECT 1 FROM app_private.native_pack_format_binding_v3 b WHERE b.pack_id=OLD.pack_id) THEN
  RETURN OLD;
 END IF;
 RAISE EXCEPTION 'NATIVE_V3_PACK_CLEANUP_WITNESS_IMMUTABLE' USING ERRCODE='55000';
END $$;

CREATE FUNCTION app_private.guard_native_retirement_owner_cleanup_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP<>'INSERT' OR (current_setting('transaction_isolation')<>'repeatable read'
  AND NOT app_private.native_v3_privacy_scope(NEW.owner_user_id))
  OR NEW.transaction_id IS DISTINCT FROM pg_current_xact_id()
  OR NEW.backend_pid IS DISTINCT FROM pg_backend_pid()
  OR NOT EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
   WHERE c.transaction_id=NEW.transaction_id AND c.owner_id=NEW.owner_user_id
    AND c.request_id=NEW.privacy_request_id)
  OR NOT app_private.privacy_owner_allowed(NEW.owner_user_id)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request q
   WHERE q.user_id=NEW.owner_user_id AND q.serving_contract_version=2)
  OR EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.owner_user_id=NEW.owner_user_id)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r WHERE r.owner_user_id=NEW.owner_user_id) THEN
  RAISE EXCEPTION 'NATIVE_V3_OWNER_CLEANUP_CONTEXT' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE TRIGGER native_served_retirement_v3_guard BEFORE INSERT OR UPDATE OR DELETE
 ON app_private.native_served_retirement_v3 FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_served_retirement_v3();
CREATE TRIGGER native_retirement_cleanup_v3_guard BEFORE INSERT OR UPDATE
 ON app_private.native_retirement_cleanup_v3 FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_retirement_cleanup_v3();
CREATE TRIGGER native_retirement_pack_cleanup_v3_guard BEFORE INSERT OR UPDATE OR DELETE
 ON app_private.native_served_retirement_pack_cleanup_v3 FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_retirement_pack_cleanup_v3();
CREATE TRIGGER native_retirement_owner_cleanup_v3_guard BEFORE INSERT OR UPDATE
 ON app_private.native_retirement_owner_cleanup_v3 FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_retirement_owner_cleanup_v3();
""" + (
    "REVOKE ALL ON FUNCTION "
    + ",".join("app_private." + signature for signature in NATIVE_SERVED_RETIREMENT_V3_FUNCTIONS)
    + " FROM PUBLIC;\n"
)

# No automatic destructive downgrade: retained personal evidence must first be
# reconciled through the reviewed lifecycle and actual final deferred checks.
