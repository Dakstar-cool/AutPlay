"""Dormant Source3 expiry, paired history purge and owner cleanup.

The six public-to-the-private-dispatch signatures are fixed by the cc92 handoff.
No migration, role grant or runtime activation is performed here. Every caller
must start REPEATABLE READ before reading any row, retain the actual F operation
lock prefix and prepay all direct, nested and deferred graph checks on the same
external work owner. Actual compiler frames and both APIR3 roles are shared.

Only paired HISTORY_PURGE is implemented. Original-only removal has no legal
scope in the pinned context ABI. The current full-xid/PID deletion witness is
never discarded before deferred checks. Final outer privacy witness cleanup,
the G dirty-epoch/work binding and actual PostgreSQL qualification remain gates.
"""

from typing import Final

NATIVE_SERVED_LIFECYCLE_V3_FUNCTIONS: Final = (
    "ensure_native_served_expiry_v3(uuid,uuid)",
    "expire_native_served_authority_v3(uuid,uuid)",
    "purge_native_served_authority_v3(uuid,uuid)",
    "purge_native_served_expiry_receipts_v3(uuid,uuid)",
    "purge_native_served_owner_v3(uuid)",
    "native_v3_assert_owner_has_no_served3(uuid)",
)

_PRIVATE_FUNCTIONS: Final = (
    "native_v3_served_lifecycle_binding(uuid,uuid)",
    "native_v3_assert_lifecycle_graph(uuid,uuid)",
    "guard_native_served_retention_v3()",
    "purge_native_served_retirement_v3(uuid,uuid)",
    "purge_native_served_retirements_owner_v3(uuid)",
    "native_v3_purge_owner_request(uuid,uuid)",
)

_TRANSACTION: Final = """
 IF current_setting('transaction_isolation')<>'repeatable read'
  AND NOT app_private.native_v3_privacy_scope(p_owner) THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_TRANSACTION' USING ERRCODE='23514'; END IF;
"""
_PAIR_LOCKS: Final = """
 IF p_owner IS NULL OR p_request IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_INPUT' USING ERRCODE='23514'; END IF;
 PERFORM app_private.lock_native_served_server();
 PERFORM 1 FROM account.user_account WHERE user_id=p_owner FOR UPDATE;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(p_owner)),
  'hex'),1,16))::bit(64)::bigint);
 PERFORM app_private.lock_recommendation_request_v2(p_request);
"""
_PRIVACY: Final = """
 IF p_owner IS NULL OR p_request IS NULL OR NOT EXISTS(
  SELECT 1 FROM app_private.privacy_purge_context c
  WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=p_owner)
  OR NOT app_private.privacy_owner_allowed(p_owner) THEN
  RAISE EXCEPTION 'native_served_owner_purge_not_authorized' USING ERRCODE='55000'; END IF;
 PERFORM app_private.lock_recommendation_request_v2(p_request);
"""
_OLD_CONTEXT: Final = """
 IF EXISTS(SELECT 1 FROM app_private.native_served_authority_context c
  WHERE c.transaction_id=txid_current() AND c.recommendation_request_id=p_request
   AND c.operation<>'PURGE') THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_CONTEXT_CONFLICT' USING ERRCODE='23514'; END IF;
 INSERT INTO app_private.native_served_authority_context
 VALUES(txid_current(),p_request,'PURGE') ON CONFLICT DO NOTHING;
"""
_CLEAR_OLD_CONTEXT: Final = """
 DELETE FROM app_private.native_served_authority_context c
 WHERE c.transaction_id=txid_current() AND c.recommendation_request_id=p_request;
"""

_FOUNDATION: Final = r"""
CREATE TYPE app_private.native_served_lifecycle_binding_v3 AS (
 owner_user_id uuid,original_request_id uuid,served_request_id uuid,
 device_id uuid,journal_epoch uuid,server_instance_id uuid,identity_epoch bigint,
 created_at timestamptz,expires_at timestamptz,request_sha256 bytea,
 decision_sha256 bytea,served_items_sha256 bytea
);

CREATE FUNCTION app_private.native_v3_served_lifecycle_binding(p_owner uuid,p_request uuid)
RETURNS app_private.native_served_lifecycle_binding_v3
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app_private.native_served_lifecycle_binding_v3; v_owner uuid;
BEGIN
 IF (current_setting('transaction_isolation')<>'repeatable read'
  AND NOT app_private.native_v3_privacy_scope(p_owner))
  OR p_owner IS NULL OR p_request IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_TRANSACTION' USING ERRCODE='23514'; END IF;
 SELECT r.owner_user_id INTO v_owner FROM ml.recommendation_request_root_v3 r
 WHERE r.served_request_id=p_request;
 IF NOT FOUND THEN
  IF EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   WHERE f.served_request_id=p_request AND f.request_hash_format_version=3)
   OR EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection_v3 c
    WHERE c.served_request_id=p_request)
   OR EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r WHERE r.original_request_id=p_request)
   OR EXISTS(SELECT 1 FROM ml.native_original_capture_format f WHERE f.original_request_id=p_request) THEN
   RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_INCOMPLETE_BINDING' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM app_private.native_served_retirement_v3 t
   WHERE t.served_request_id=p_request AND (t.owner_user_id<>p_owner
    OR t.request_hash_format_version<>3)) THEN
   RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CROSS_BOUND' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM app_private.native_served_retirement_v3 t
    WHERE t.owner_user_id=p_owner AND t.served_request_id=p_request
     AND t.request_hash_format_version=3) AND (
   EXISTS(SELECT 1 FROM app_private.native_served_authority_purge_receipt r
    WHERE r.recommendation_request_id=p_request)
   OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_expiry_tombstone t
    WHERE t.recommendation_request_id=p_request)) THEN
   RAISE EXCEPTION 'NATIVE_V3_RETIRED_TYPED_PROVENANCE_REQUIRED' USING ERRCODE='23514'; END IF;
  RETURN NULL;
 END IF;
 IF v_owner<>p_owner THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CROSS_BOUND' USING ERRCODE='23514'; END IF;
 -- Fixed metadata only. No root/request/replay payload is hydrated as a probe.
 SELECT r.owner_user_id,r.original_request_id,r.served_request_id,r.device_id,r.journal_epoch,
  r.server_instance_id,r.identity_epoch,r.created_at,r.expires_at,q.request_sha256,
  d.decision_sha256,d.served_items_sha256 INTO b
 FROM ml.recommendation_request_root_v3 r
 JOIN ml.native_original_capture_format o ON o.original_request_id=r.original_request_id
  AND o.owner_user_id=r.owner_user_id AND o.original_capture_format_version=3
 JOIN ml.native_served_request_hash_format f ON f.served_request_id=r.served_request_id
  AND f.owner_user_id=r.owner_user_id AND f.request_hash_format_version=3
 JOIN ml.recommendation_request q ON q.recommendation_request_id=r.served_request_id
  AND q.user_id=r.owner_user_id AND q.serving_contract_version=2 AND q.created_at=r.created_at
 JOIN ml.recommendation_request original ON original.recommendation_request_id=r.original_request_id
  AND original.user_id=r.owner_user_id AND original.serving_contract_version=1
  AND original.created_at=r.created_at AND original.request_sha256=r.original_request_sha256
 JOIN ml.recommendation_serving_decision d ON d.recommendation_request_id=r.served_request_id
  AND d.served_truth_expires_at=r.expires_at
 WHERE r.served_request_id=p_request AND r.owner_user_id=p_owner
  AND r.request_hash_format_version=3 AND r.original_capture_format_version=3
  AND r.expires_at=r.created_at+interval '15552000 seconds'
  AND o.created_at=r.created_at AND f.created_at=r.created_at
  AND o.expires_at=r.expires_at AND f.expires_at=r.expires_at;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM ml.recommendation_request_replay_payload p
  WHERE p.recommendation_request_id=p_request) THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_INCOMPLETE_BINDING' USING ERRCODE='23514'; END IF;
 RETURN b;
END $$;

CREATE FUNCTION app_private.native_v3_assert_lifecycle_graph(p_owner uuid,p_request uuid)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app_private.native_served_lifecycle_binding_v3;
BEGIN
 b:=app_private.native_v3_served_lifecycle_binding(p_owner,p_request);
 IF b.served_request_id IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_LIFECYCLE_GRAPH_REQUIRED' USING ERRCODE='23514'; END IF;
 PERFORM app_private.native_v3_item_rows_byte_length(p_owner,b.original_request_id,'ORIGINAL');
 PERFORM app_private.native_v3_item_rows_byte_length(p_owner,p_request,'SERVED');
 PERFORM app_private.assert_native_atomic_graph_v3(b.original_request_id);
END $$;

CREATE FUNCTION app_private.guard_native_served_retention_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rid uuid; v_owner uuid; v_format integer;
 b app_private.native_served_lifecycle_binding_v3;
 t app_private.native_served_retirement_v3%ROWTYPE;
BEGIN
 IF TG_TABLE_SCHEMA NOT IN ('ml','app_private') OR TG_TABLE_NAME NOT IN
  ('recommendation_attribution_expiry_tombstone','native_served_authority_purge_receipt') THEN
  RAISE EXCEPTION 'NATIVE_V3_RETENTION_TRIGGER' USING ERRCODE='23514'; END IF;
 rid:=CASE WHEN TG_OP='DELETE' THEN OLD.recommendation_request_id ELSE NEW.recommendation_request_id END;
 v_owner:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_user_id ELSE NEW.owner_user_id END;
 SELECT f.request_hash_format_version INTO v_format FROM ml.native_served_request_hash_format f
 WHERE f.served_request_id=rid;
 IF v_format IS NULL THEN SELECT r.request_hash_format_version INTO v_format
  FROM app_private.native_served_retirement_v3 r WHERE r.served_request_id=rid; END IF;
 IF v_format IS DISTINCT FROM 3 THEN RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END; END IF;
 IF (current_setting('transaction_isolation')<>'repeatable read'
  AND NOT app_private.native_v3_privacy_scope(v_owner)) OR TG_OP='UPDATE' THEN
  RAISE EXCEPTION 'NATIVE_V3_RETENTION_IMMUTABLE' USING ERRCODE='55000'; END IF;
 IF TG_OP='INSERT' AND TG_TABLE_NAME='recommendation_attribution_expiry_tombstone' THEN
  b:=app_private.native_v3_served_lifecycle_binding(v_owner,rid);
  IF b.served_request_id IS NULL OR clock_timestamp()<b.expires_at
   OR clock_timestamp()>=b.created_at+interval '400 days'
   OR NEW.serving_contract_version IS DISTINCT FROM 2 OR NEW.reason IS DISTINCT FROM 'EXPIRED'
   OR NEW.request_sha256 IS DISTINCT FROM b.request_sha256
   OR NEW.decision_sha256 IS DISTINCT FROM b.decision_sha256
   OR NEW.served_items_sha256 IS DISTINCT FROM b.served_items_sha256
   OR NEW.expired_at IS DISTINCT FROM b.expires_at
   OR NEW.tombstone_expires_at IS DISTINCT FROM b.created_at+interval '400 days' THEN
   RAISE EXCEPTION 'NATIVE_V3_RETENTION_CONTENT' USING ERRCODE='23514'; END IF;
  PERFORM app_private.native_v3_assert_lifecycle_graph(v_owner,rid);
  IF clock_timestamp()<b.expires_at THEN
   RAISE EXCEPTION 'native_served_retention_active' USING ERRCODE='55000'; END IF;
  RETURN NEW;
 ELSIF TG_OP='INSERT' AND TG_TABLE_NAME='native_served_authority_purge_receipt' THEN
  SELECT r.owner_user_id,r.served_request_id,r.original_request_id,r.request_hash_format_version,
   r.original_capture_format_version,r.served_request_sha256,r.decision_sha256,r.served_items_sha256,
   r.original_request_sha256,r.capture_sha256,r.request_root_sha256,r.created_at,r.truth_expires_at,
   r.retain_until,r.purged_at,r.transaction_id,r.backend_pid,r.reason INTO t
  FROM app_private.native_served_retirement_v3 r WHERE r.served_request_id=rid;
  IF t.owner_user_id IS DISTINCT FROM v_owner OR t.request_hash_format_version<>3
   OR t.transaction_id IS DISTINCT FROM pg_current_xact_id() OR t.backend_pid<>pg_backend_pid()
   OR NEW.request_sha256 IS DISTINCT FROM t.served_request_sha256
   OR NEW.decision_sha256 IS DISTINCT FROM t.decision_sha256
   OR NEW.served_items_sha256 IS DISTINCT FROM t.served_items_sha256
   OR NEW.reason IS DISTINCT FROM t.reason OR NEW.purged_at IS DISTINCT FROM t.purged_at
   OR NEW.expires_at IS DISTINCT FROM t.retain_until OR NOT EXISTS(
    SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
    WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
     AND w.owner_user_id=v_owner AND w.served_request_id=rid
     AND w.original_request_id=t.original_request_id AND w.operation='HISTORY_PURGE'
     AND w.original_capture_format_version=3 AND w.request_hash_format_version=3) THEN
   RAISE EXCEPTION 'NATIVE_V3_RETENTION_CONTENT' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 ELSIF TG_OP='DELETE' AND app_private.native_v3_retirement_cleanup_authorized(v_owner,rid)
  AND EXISTS(SELECT 1 FROM app_private.native_retirement_cleanup_v3 c
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
    AND c.owner_user_id=v_owner AND c.served_request_id=rid AND c.request_hash_format_version=3) THEN
  RETURN OLD;
 END IF;
 RAISE EXCEPTION 'NATIVE_V3_RETENTION_CONTEXT_REQUIRED' USING ERRCODE='55000';
END $$;
"""

_ENSURE: Final = (
    r"""
CREATE FUNCTION app_private.ensure_native_served_expiry_v3(p_owner uuid,p_request uuid)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app_private.native_served_lifecycle_binding_v3;
BEGIN
"""
    + _TRANSACTION
    + _PAIR_LOCKS
    + r"""
 b:=app_private.native_v3_served_lifecycle_binding(p_owner,p_request);
 IF b.served_request_id IS NULL OR clock_timestamp()<b.expires_at THEN RETURN false; END IF;
 PERFORM app_private.native_v3_assert_lifecycle_graph(p_owner,p_request);
 IF clock_timestamp()<b.expires_at THEN RETURN false; END IF;
"""
    + _OLD_CONTEXT
    + r"""
 IF clock_timestamp()<b.created_at+interval '400 days' THEN
  INSERT INTO ml.recommendation_attribution_expiry_tombstone
  VALUES(p_owner,p_request,2,b.request_sha256,b.decision_sha256,b.served_items_sha256,
   b.expires_at,b.created_at+interval '400 days','EXPIRED') ON CONFLICT DO NOTHING;
  IF NOT EXISTS(SELECT 1 FROM ml.recommendation_attribution_expiry_tombstone t
   WHERE t.owner_user_id=p_owner AND t.recommendation_request_id=p_request
    AND t.serving_contract_version=2 AND t.request_sha256=b.request_sha256
    AND t.decision_sha256=b.decision_sha256 AND t.served_items_sha256=b.served_items_sha256
    AND t.expired_at=b.expires_at AND t.tombstone_expires_at=b.created_at+interval '400 days'
    AND t.reason='EXPIRED') THEN
   RAISE EXCEPTION 'NATIVE_V3_EXPIRY_RETRY_CONTENT' USING ERRCODE='23514'; END IF;
 END IF;
"""
    + _CLEAR_OLD_CONTEXT
    + r"""
 RETURN true;
END $$;

CREATE FUNCTION app_private.expire_native_served_authority_v3(p_owner uuid,p_request uuid)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app_private.native_served_lifecycle_binding_v3;
BEGIN
"""
    + _TRANSACTION
    + _PAIR_LOCKS
    + r"""
 b:=app_private.native_v3_served_lifecycle_binding(p_owner,p_request);
 IF b.served_request_id IS NOT NULL THEN
  IF NOT app_private.ensure_native_served_expiry_v3(p_owner,p_request) THEN
   RETURN jsonb_build_object('outcome','NOT_EXPIRED'); END IF;
  IF clock_timestamp()<b.created_at+interval '400 days' THEN
   RETURN jsonb_build_object('outcome','ATTRIBUTION_EXPIRED'); END IF;
 ELSIF EXISTS(SELECT 1 FROM app_private.native_served_retirement_v3 r
  WHERE r.owner_user_id=p_owner AND r.served_request_id=p_request
   AND r.request_hash_format_version=3 AND r.retain_until>clock_timestamp()) THEN
  RETURN jsonb_build_object('outcome','ATTRIBUTION_EXPIRED');
 END IF;
 RETURN jsonb_build_object('outcome','ATTRIBUTION_NOT_FOUND');
END $$;
"""
)


def _purge_request_sql(*, privacy: bool) -> str:
    """Generate two fixed entrypoints; no SQL caller can choose a reason/mode."""
    name = "native_v3_purge_owner_request" if privacy else "purge_native_served_authority_v3"
    reason = "ACCOUNT_DELETION" if privacy else "SERVING_TRUTH_EXPIRED"
    authorization = _PRIVACY if privacy else _PAIR_LOCKS
    expiry = (
        ""
        if privacy
        else """
 IF NOT app_private.ensure_native_served_expiry_v3(p_owner,p_request) THEN
  RAISE EXCEPTION 'native_served_retention_active' USING ERRCODE='55000'; END IF;
"""
    )
    pending = (
        ""
        if privacy
        else """
  IF remaining>0 THEN RETURN jsonb_build_object('status','PENDING'); END IF;
"""
    )
    causal_pending = (
        ""
        if privacy
        else """
  IF remaining>0 THEN RETURN jsonb_build_object('status','PENDING',
   'remaining_associations',remaining); END IF;
"""
    )
    entity = (
        """
 INSERT INTO app_private.privacy_purge_entity VALUES(
  pg_current_xact_id(),'ml.recommendation_request',b.original_request_id) ON CONFLICT DO NOTHING;
 INSERT INTO app_private.privacy_purge_entity VALUES(
  pg_current_xact_id(),'ml.recommendation_request',p_request) ON CONFLICT DO NOTHING;
"""
        if privacy
        else ""
    )
    deletion = ""
    for table, key, value in (
        ("native_capture_source_event_v3", "original_request_id", "b.original_request_id"),
        ("native_capture_lineage_cursor_v3", "original_request_id", "b.original_request_id"),
        ("native_capture_replay_v3", "original_request_id", "b.original_request_id"),
        ("native_atomic_capture_connection_v3", "served_request_id", "p_request"),
        ("native_original_capture_v3", "original_request_id", "b.original_request_id"),
        ("recommendation_request_root_v3", "served_request_id", "p_request"),
        ("recommendation_served_item", "recommendation_request_id", "p_request"),
        ("recommendation_serving_decision", "recommendation_request_id", "p_request"),
        ("recommendation_item", "recommendation_request_id", "p_request"),
        ("recommendation_item", "recommendation_request_id", "b.original_request_id"),
        # B's actual BEFORE parent trigger retires the exact format rows while
        # the protected HISTORY context and full-xid/PID witness still exist.
        ("recommendation_request", "recommendation_request_id", "p_request"),
        ("recommendation_request", "recommendation_request_id", "b.original_request_id"),
    ):
        deletion += (
            f" DELETE FROM ml.{table} WHERE {key}={value};\n"
            " GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;\n"
        )
    cleanup = (
        " total:=total+app_private.purge_native_served_retirement_v3(p_owner,p_request);\n"
        if privacy
        else ""
    )
    return f"""
CREATE FUNCTION app_private.{name}(p_owner uuid,p_request uuid)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b app_private.native_served_lifecycle_binding_v3;
 remaining bigint; previous bigint; total bigint:=0; affected bigint;
BEGIN
{_TRANSACTION}{authorization}
 b:=app_private.native_v3_served_lifecycle_binding(p_owner,p_request);
 IF b.served_request_id IS NULL THEN
  RETURN jsonb_build_object('status',CASE WHEN EXISTS(
   SELECT 1 FROM app_private.native_served_retirement_v3 r
   WHERE r.owner_user_id=p_owner AND r.served_request_id=p_request
    AND r.request_hash_format_version=3) THEN 'PURGED' ELSE 'ATTRIBUTION_NOT_FOUND' END,'removed',0);
 END IF;
{expiry}
 -- Actual0101 order is mandatory: pack bindings before causal children.
 SELECT count(*) INTO remaining FROM ml.offline_pack_binding_receipt
 WHERE recommendation_request_id=p_request;
 WHILE remaining>0 LOOP
  previous:=remaining;
  PERFORM app_private.purge_native_offline_request_v2(p_owner,p_request,'{reason}',256);
  SELECT count(*) INTO remaining FROM ml.offline_pack_binding_receipt
  WHERE recommendation_request_id=p_request;
  IF remaining>=previous THEN RAISE EXCEPTION 'NATIVE_V3_PACK_PURGE_NO_PROGRESS'
   USING ERRCODE='55000'; END IF;
  total:=total+previous-remaining;
{pending}
 END LOOP;
 SELECT count(*) INTO remaining FROM ml.recommendation_attribution_association
 WHERE recommendation_request_id=p_request;
 WHILE remaining>0 LOOP
  previous:=remaining;
  PERFORM app_private.purge_native_request_attributions_v2(p_owner,p_request,'{reason}',256);
  SELECT count(*) INTO remaining FROM ml.recommendation_attribution_association
  WHERE recommendation_request_id=p_request;
  IF remaining>=previous THEN RAISE EXCEPTION 'NATIVE_V3_CAUSAL_PURGE_NO_PROGRESS'
   USING ERRCODE='55000'; END IF;
  total:=total+previous-remaining;
{causal_pending}
 END LOOP;
 PERFORM app_private.native_v3_assert_lifecycle_graph(p_owner,p_request);
{entity}
 INSERT INTO app_private.native_request_write_context_v3 VALUES(
  pg_current_xact_id(),pg_backend_pid(),p_owner,b.original_request_id,p_request,'HISTORY_PURGE',
  b.device_id,b.journal_epoch,b.server_instance_id,b.identity_epoch,clock_timestamp());
 PERFORM app_private.record_native_served_retirement_v3(p_owner,p_request);
{_OLD_CONTEXT}
 INSERT INTO app_private.native_served_authority_purge_receipt
 SELECT r.owner_user_id,r.served_request_id,r.served_request_sha256,r.decision_sha256,
  r.served_items_sha256,r.reason,r.purged_at,r.retain_until
 FROM app_private.native_served_retirement_v3 r WHERE r.owner_user_id=p_owner
  AND r.served_request_id=p_request AND r.request_hash_format_version=3;
{deletion}
 IF NOT app_private.native_v3_retirement_graph_absent(p_owner,p_request) THEN
  RAISE EXCEPTION 'NATIVE_V3_HISTORY_PURGE_INCOMPLETE' USING ERRCODE='23514'; END IF;
{cleanup}{_CLEAR_OLD_CONTEXT}
 -- The NEW context is removed by its real deferred completeness trigger.
 -- Keep the typed deletion witness through every deferred check and COMMIT.
 RETURN jsonb_build_object('status','PURGED','removed',total);
END $$;
"""


_CLEANUP: Final = (
    r"""
CREATE FUNCTION app_private.purge_native_served_retirement_v3(p_owner uuid,p_request uuid)
RETURNS bigint LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total bigint:=0; affected bigint; pack record;
BEGIN
"""
    + _TRANSACTION
    + r"""
 IF NOT app_private.native_v3_retirement_cleanup_authorized(p_owner,p_request) THEN RETURN 0; END IF;
 IF EXISTS(SELECT 1 FROM app_private.native_pack_format_binding_v3 b
   WHERE b.owner_user_id=p_owner AND b.served_request_id=p_request)
  AND NOT (EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=p_owner)
   AND app_private.privacy_owner_allowed(p_owner)) THEN
  -- A newly committed pack witness after400d would otherwise retain personal
  -- IDs without a reviewed ordinary remover. Refuse the whole statement until
  -- the actual final post-constraint phase exists; no expiry deadline is reset.
  RAISE EXCEPTION 'NATIVE_V3_PACK_FINAL_CLEANUP_PHASE_REQUIRED' USING ERRCODE='55000'; END IF;
 INSERT INTO app_private.native_retirement_cleanup_v3
 SELECT pg_current_xact_id(),pg_backend_pid(),r.owner_user_id,r.served_request_id,
  r.request_hash_format_version,r.served_request_sha256,r.decision_sha256,r.served_items_sha256,r.retain_until
 FROM app_private.native_served_retirement_v3 r
 WHERE r.owner_user_id=p_owner AND r.served_request_id=p_request;
 -- Each fixed witness is issued from the actual protected type plus both
 -- independently retained pack receipts. It survives their physical deletion.
 FOR pack IN SELECT b.pack_id,b.request_hash_format_version,b.binding_sha256
  FROM app_private.native_pack_format_binding_v3 b
  WHERE b.owner_user_id=p_owner AND b.served_request_id=p_request ORDER BY b.pack_id LOOP
  INSERT INTO app_private.native_served_retirement_pack_cleanup_v3
  VALUES(pg_current_xact_id(),pg_backend_pid(),p_owner,pack.pack_id,p_request,
   pack.request_hash_format_version,pack.binding_sha256);
  DELETE FROM app_private.native_pack_format_binding_v3 b
  WHERE b.pack_id=pack.pack_id AND b.owner_user_id=p_owner;
  GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
  INSERT INTO app_private.native_pack_context
  VALUES(pg_current_xact_id(),pack.pack_id,p_owner,'RECEIPT_PURGE',pack.binding_sha256);
  DELETE FROM ml.native_offline_pack_purge_receipt r
  WHERE r.owner_user_id=p_owner AND r.pack_id=pack.pack_id
   AND r.binding_sha256=pack.binding_sha256;
  GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
  DELETE FROM app_private.native_pack_context c
  WHERE c.transaction_id=pg_current_xact_id() AND c.pack_id=pack.pack_id;
 END LOOP;
 DELETE FROM app_private.native_legacy_original_binding_v3 b
 WHERE b.owner_user_id=p_owner AND b.served_request_id=p_request
  AND app_private.native_v3_retirement_cleanup_allowed(p_owner,b.original_request_id,
   'native_legacy_original_binding_v3');
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 DELETE FROM app_private.native_legacy_retirement_witness_v3 w
 WHERE w.owner_user_id=p_owner AND w.served_request_id=p_request
  AND w.transaction_id<>pg_current_xact_id()
  AND app_private.native_v3_retirement_cleanup_allowed(p_owner,w.request_id,
   'native_legacy_retirement_witness_v3');
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 DELETE FROM app_private.native_request_deletion_witness_v3 w
 USING app_private.native_served_retirement_v3 t
 WHERE t.owner_user_id=p_owner AND t.served_request_id=p_request
  AND t.request_hash_format_version=3 AND w.owner_user_id=t.owner_user_id
  AND w.original_request_id=t.original_request_id AND w.served_request_id=t.served_request_id
  AND w.original_capture_format_version=3 AND w.request_hash_format_version=3
  AND w.operation='HISTORY_PURGE' AND w.transaction_id<>pg_current_xact_id();
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
"""
    + _OLD_CONTEXT
    + r"""
 DELETE FROM ml.recommendation_attribution_expiry_tombstone
 WHERE owner_user_id=p_owner AND recommendation_request_id=p_request;
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 DELETE FROM app_private.native_served_authority_purge_receipt
 WHERE owner_user_id=p_owner AND recommendation_request_id=p_request;
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 -- No unexplained old metadata may silently outlive its typed parent record.
 IF EXISTS(SELECT 1 FROM app_private.native_legacy_original_binding_v3 b
   WHERE b.owner_user_id=p_owner AND b.served_request_id=p_request)
  OR EXISTS(SELECT 1 FROM app_private.native_legacy_retirement_witness_v3 w
   WHERE w.owner_user_id=p_owner AND w.served_request_id=p_request
    AND w.transaction_id<>pg_current_xact_id()) THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CLEANUP_INCOMPLETE' USING ERRCODE='23514'; END IF;
 DELETE FROM app_private.native_served_retirement_v3
 WHERE owner_user_id=p_owner AND served_request_id=p_request;
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 DELETE FROM app_private.native_retirement_cleanup_v3 c
 WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
  AND c.owner_user_id=p_owner AND c.served_request_id=p_request;
"""
    + _CLEAR_OLD_CONTEXT
    + r"""
 RETURN total;
END $$;

CREATE FUNCTION app_private.purge_native_served_expiry_receipts_v3(p_owner uuid,p_request uuid)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE removed bigint;
BEGIN
"""
    + _TRANSACTION
    + _PAIR_LOCKS
    + r"""
 PERFORM app_private.native_v3_served_lifecycle_binding(p_owner,p_request);
 IF EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
   WHERE r.served_request_id=p_request AND r.owner_user_id=p_owner)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request q
   WHERE q.recommendation_request_id=p_request AND q.user_id=p_owner)
  OR EXISTS(SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
    AND w.owner_user_id=p_owner AND w.served_request_id=p_request) THEN
  RETURN jsonb_build_object('status','GRAPH_PURGE_REQUIRED','removed',0); END IF;
 IF EXISTS(SELECT 1 FROM app_private.native_served_retirement_v3 r
  WHERE r.served_request_id=p_request AND (r.owner_user_id<>p_owner OR r.request_hash_format_version<>3)) THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CROSS_BOUND' USING ERRCODE='23514'; END IF;
 removed:=app_private.purge_native_served_retirement_v3(p_owner,p_request);
 RETURN jsonb_build_object('status','EXPIRED_RECEIPTS_REMOVED','removed',removed);
END $$;

CREATE FUNCTION app_private.purge_native_served_retirements_owner_v3(target uuid)
RETURNS bigint LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rid uuid; total bigint:=0; affected bigint; pack record;
BEGIN
"""
    + _TRANSACTION.replace("p_owner", "target")
    + r"""
 IF NOT EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
  WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=target)
  OR NOT app_private.privacy_owner_allowed(target) THEN
  RAISE EXCEPTION 'native_served_owner_purge_not_authorized' USING ERRCODE='55000'; END IF;
 INSERT INTO app_private.native_retirement_owner_cleanup_v3
 SELECT pg_current_xact_id(),pg_backend_pid(),target,c.request_id
 FROM app_private.privacy_purge_context c
 WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=target;
 -- Prior pack witnesses may no longer have a durable request anchor after an
 -- earlier ordinary cleanup. Their exact type/owner is still protected.
 DELETE FROM app_private.native_served_retirement_pack_cleanup_v3 w
 WHERE w.owner_user_id=target AND w.transaction_id<>pg_current_xact_id();
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 FOR rid IN SELECT r.served_request_id FROM app_private.native_served_retirement_v3 r
  WHERE r.owner_user_id=target ORDER BY r.served_request_id LOOP
  total:=total+app_private.purge_native_served_retirement_v3(target,rid);
 END LOOP;
 -- Also reconcile legitimate old typed metadata without a surviving C anchor.
 -- The new scope binds the actual privacy request after frozen old2 graph purge.
 FOR pack IN SELECT b.pack_id,b.served_request_id,b.request_hash_format_version,b.binding_sha256
  FROM app_private.native_pack_format_binding_v3 b WHERE b.owner_user_id=target ORDER BY b.pack_id LOOP
  INSERT INTO app_private.native_served_retirement_pack_cleanup_v3
  VALUES(pg_current_xact_id(),pg_backend_pid(),target,pack.pack_id,pack.served_request_id,
   pack.request_hash_format_version,pack.binding_sha256);
  DELETE FROM app_private.native_pack_format_binding_v3 b WHERE b.pack_id=pack.pack_id;
  GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
  DELETE FROM ml.native_offline_pack_purge_receipt r
  WHERE r.owner_user_id=target AND r.pack_id=pack.pack_id AND r.binding_sha256=pack.binding_sha256;
  GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 END LOOP;
 DELETE FROM app_private.native_legacy_original_binding_v3 b WHERE b.owner_user_id=target
  AND app_private.native_v3_retirement_cleanup_allowed(target,b.original_request_id,'native_legacy_original_binding_v3');
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 DELETE FROM app_private.native_legacy_retirement_witness_v3 w WHERE w.owner_user_id=target
  AND w.transaction_id<>pg_current_xact_id()
  AND app_private.native_v3_retirement_cleanup_allowed(target,w.request_id,'native_legacy_retirement_witness_v3');
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 DELETE FROM app_private.native_ordinary_pack_delete_witness_v3 w WHERE w.owner_user_id=target
  AND w.transaction_id<>pg_current_xact_id()
  AND app_private.native_v3_retirement_cleanup_allowed(target,w.pack_id,'native_ordinary_pack_delete_witness_v3');
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 IF EXISTS(SELECT 1 FROM app_private.native_served_retirement_v3 r WHERE r.owner_user_id=target) THEN
  RAISE EXCEPTION 'NATIVE_V3_OWNER_RETIREMENT_REMAINS' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM app_private.native_pack_format_binding_v3 b WHERE b.owner_user_id=target)
  OR EXISTS(SELECT 1 FROM app_private.native_legacy_original_binding_v3 b WHERE b.owner_user_id=target)
  OR EXISTS(SELECT 1 FROM app_private.native_legacy_retirement_witness_v3 w
   WHERE w.owner_user_id=target AND w.transaction_id<>pg_current_xact_id())
  OR EXISTS(SELECT 1 FROM app_private.native_ordinary_pack_delete_witness_v3 w
   WHERE w.owner_user_id=target AND w.transaction_id<>pg_current_xact_id()) THEN
  RAISE EXCEPTION 'NATIVE_V3_OWNER_TYPED_METADATA_REMAINS' USING ERRCODE='23514'; END IF;
 DELETE FROM app_private.native_retirement_owner_cleanup_v3 c
 WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid() AND c.owner_user_id=target;
 RETURN total;
END $$;
"""
)

_OWNER: Final = (
    r"""
CREATE FUNCTION app_private.native_v3_assert_owner_has_no_served3(target uuid)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
"""
    + _TRANSACTION.replace("p_owner", "target")
    + r"""
 IF target IS NULL OR EXISTS(SELECT 1 FROM ml.native_original_capture_format f
   WHERE f.owner_user_id=target AND f.original_capture_format_version=3)
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   WHERE f.owner_user_id=target AND f.request_hash_format_version=3)
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_v3 b WHERE b.owner_user_id=target)
  OR EXISTS(SELECT 1 FROM ml.native_capture_source_event_v3 e WHERE e.owner_user_id=target)
  OR EXISTS(SELECT 1 FROM ml.native_capture_lineage_cursor_v3 c WHERE c.owner_user_id=target)
  OR EXISTS(SELECT 1 FROM ml.native_capture_replay_v3 r WHERE r.owner_user_id=target)
  OR EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection_v3 c WHERE c.owner_user_id=target)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r WHERE r.owner_user_id=target)
  OR EXISTS(SELECT 1 FROM app_private.native_served_retirement_v3 r
   WHERE r.owner_user_id=target AND r.request_hash_format_version=3)
  OR EXISTS(SELECT 1 FROM app_private.native_pack_format_binding_v3 b
   WHERE b.owner_user_id=target AND b.request_hash_format_version=3)
  OR EXISTS(SELECT 1 FROM app_private.native_served_retirement_pack_cleanup_v3 w
   WHERE w.owner_user_id=target AND w.request_hash_format_version=3 AND (
    w.transaction_id<>pg_current_xact_id() OR w.backend_pid<>pg_backend_pid()
    OR NOT EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
     WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=target)
    OR NOT app_private.privacy_owner_allowed(target)
    OR EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=w.pack_id)
    OR EXISTS(SELECT 1 FROM ml.offline_recommendation_pack p WHERE p.offline_pack_id=w.pack_id)
    OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association a WHERE a.offline_pack_id=w.pack_id)
    OR EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt r WHERE r.pack_id=w.pack_id)))
  OR EXISTS(SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
   WHERE w.owner_user_id=target AND (w.transaction_id<>pg_current_xact_id()
    OR w.backend_pid<>pg_backend_pid() OR NOT EXISTS(
     SELECT 1 FROM app_private.privacy_purge_context c
     WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=target)
    OR NOT app_private.privacy_owner_allowed(target)
    OR EXISTS(SELECT 1 FROM ml.recommendation_request q
     WHERE q.recommendation_request_id IN (w.original_request_id,w.served_request_id))
    OR EXISTS(SELECT 1 FROM ml.recommendation_item q
     WHERE q.recommendation_request_id IN (w.original_request_id,w.served_request_id))
    OR EXISTS(SELECT 1 FROM ml.recommendation_serving_decision q
     WHERE q.recommendation_request_id IN (w.original_request_id,w.served_request_id))
    OR EXISTS(SELECT 1 FROM ml.recommendation_served_item q
     WHERE q.recommendation_request_id IN (w.original_request_id,w.served_request_id))
    OR NOT EXISTS(SELECT 1 FROM app_private.privacy_purge_entity e
     WHERE e.transaction_id=pg_current_xact_id() AND e.entity='ml.recommendation_request'
      AND e.entity_id=w.served_request_id)
    OR NOT EXISTS(SELECT 1 FROM app_private.privacy_purge_entity e
     WHERE e.transaction_id=pg_current_xact_id() AND e.entity='ml.recommendation_request'
      AND e.entity_id=w.original_request_id))) THEN
  RAISE EXCEPTION 'NATIVE_V3_OWNER_HAS_SERVED3' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.purge_native_served_owner_v3(target uuid)
RETURNS bigint LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rid uuid; result jsonb; total bigint:=0;
BEGIN
"""
    + _TRANSACTION.replace("p_owner", "target")
    + r"""
 IF NOT EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
  WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=target)
  OR NOT app_private.privacy_owner_allowed(target) THEN
  RAISE EXCEPTION 'native_served_owner_purge_not_authorized' USING ERRCODE='55000'; END IF;
 DELETE FROM app_private.native_served_retirement_pack_cleanup_v3 w
 WHERE w.owner_user_id=target AND w.request_hash_format_version=3
  AND w.transaction_id<>pg_current_xact_id();
 -- The authenticated outer purge already holds the server/owner/publication
 -- prefix. Do not acquire identity locks late or invoke the broad old2 owner
 -- scan until this complete typed3 census has passed.
 FOR rid IN SELECT r.served_request_id FROM ml.recommendation_request_root_v3 r
   WHERE r.owner_user_id=target
  UNION SELECT f.served_request_id FROM ml.native_served_request_hash_format f
   WHERE f.owner_user_id=target AND f.request_hash_format_version=3
  UNION SELECT r.served_request_id FROM app_private.native_served_retirement_v3 r
   WHERE r.owner_user_id=target AND r.request_hash_format_version=3
  ORDER BY 1 LOOP
  result:=app_private.native_v3_purge_owner_request(target,rid);
  IF result->>'status' NOT IN ('PURGED','ATTRIBUTION_NOT_FOUND') THEN
   RAISE EXCEPTION 'NATIVE_V3_OWNER_PURGE_PENDING' USING ERRCODE='23514'; END IF;
  total:=total+coalesce((result->>'removed')::bigint,0);
  total:=total+app_private.purge_native_served_retirement_v3(target,rid);
 END LOOP;
 PERFORM app_private.native_v3_assert_owner_has_no_served3(target);
 RETURN total;
END $$;
"""
)

_DEFINITIONS: Final = (
    _FOUNDATION
    + _ENSURE
    + _CLEANUP
    + _purge_request_sql(privacy=False)
    + _purge_request_sql(privacy=True)
    + _OWNER
    + """
CREATE TRIGGER native_v3_expiry_receipt_guard BEFORE INSERT OR UPDATE OR DELETE
 ON ml.recommendation_attribution_expiry_tombstone FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_served_retention_v3();
CREATE TRIGGER native_v3_purge_receipt_guard BEFORE INSERT OR UPDATE OR DELETE
 ON app_private.native_served_authority_purge_receipt FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_served_retention_v3();
"""
)


def _bounded_entrypoints(sql: str) -> tuple[str, tuple[str, ...]]:
    """Own fixed scalar locals on the same compiler stack as nested graphs.

    There is no exception continuation or new work allowance. A body has
    returned and dropped all graph/fact locals before its outer frame closes.
    Its fixed scalar/JSON status return (under1KiB) belongs to the caller's
    already admitted output. These wrappers neither change transaction mode
    nor create the caller's F/G lock, grant, work or dirty-epoch authority.
    """
    wrappers = []
    private = []
    entries = (
        ("ensure_native_served_expiry_v3", "boolean", False),
        ("expire_native_served_authority_v3", "jsonb", False),
        ("purge_native_served_authority_v3", "jsonb", False),
        ("purge_native_served_expiry_receipts_v3", "jsonb", False),
        ("purge_native_served_owner_v3", "bigint", True),
        ("native_v3_assert_owner_has_no_served3", "void", True),
        ("purge_native_served_retirement_v3", "bigint", False),
        ("purge_native_served_retirements_owner_v3", "bigint", True),
    )
    for name, result, owner in entries:
        inner = "_c3_" + name
        marker = f"CREATE FUNCTION app_private.{name}("
        if sql.count(marker) != 1:
            raise ValueError("C lifecycle definition must be unique")
        sql = sql.replace(marker, f"CREATE FUNCTION app_private.{inner}(")
        arguments = "target" if owner else "p_owner,p_request"
        parameters = "target uuid" if owner else "p_owner uuid,p_request uuid"
        types = "uuid" if owner else "uuid,uuid"
        private.append(f"{inner}({types})")
        declaration = "" if result == "void" else f" value {result};"
        invocation = (
            f" PERFORM app_private.{inner}({arguments});"
            if result == "void"
            else f" value:=app_private.{inner}({arguments});"
        )
        returned = " RETURN;" if result == "void" else " RETURN value;"
        wrappers.append(f"""
CREATE FUNCTION app_private.{name}({parameters})
RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE frame integer;{declaration}
BEGIN
{_TRANSACTION.replace("p_owner", "target" if owner else "p_owner")}
 frame:=app_private.native_v3_compiler_enter(8192);
{invocation}
 PERFORM app_private.native_v3_compiler_exit(frame);
{returned}
END $$;
""")
    return sql + "\n".join(wrappers), tuple(private)


_BOUNDED_SQL, _BOUNDED_PRIVATE = _bounded_entrypoints(_DEFINITIONS)

NATIVE_SERVED_LIFECYCLE_V3_INSTALL_SQL: Final = (
    _BOUNDED_SQL
    + "REVOKE ALL ON TYPE app_private.native_served_lifecycle_binding_v3 FROM PUBLIC;\n"
    + "REVOKE ALL ON FUNCTION "
    + ",".join(
        "app_private." + signature
        for signature in (
            *NATIVE_SERVED_LIFECYCLE_V3_FUNCTIONS,
            *_PRIVATE_FUNCTIONS,
            *_BOUNDED_PRIVATE,
        )
    )
    + " FROM PUBLIC;\n"
)
