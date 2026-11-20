# ruff: noqa: E501
"""Protected trusted-format dispatch assembled by native_request_v3_install.

The caller must first install the real graph and supply reviewed predecessor
body pins. This module imports no models and executes no SQL on import. It is
not connected to a live Alembic revision or public runtime composition.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Connection


@dataclass(frozen=True, slots=True)
class _Route:
    name: str
    types: str
    arguments: str
    result: str
    body: str
    frozen_alias: str | None = None

    @property
    def signature(self) -> str:
        return f"app_private.{self.name}({self.types})"

    @property
    def frozen_name(self) -> str:
        return self.frozen_alias or self.name + "_v2_frozen"


# These are fixed installed implementations, never caller-selected callbacks.
# The resolver may identify an existing ordinary contract1 request as 1 without
# assigning it a native format. Missing/contradictory native bindings must RAISE.
REQUIRED_FUNCTIONS = (
    "app_private.assert_native_atomic_graph_v3(uuid)",
    "app_private.assert_native_served_truth_v3(uuid)",
    "app_private.assert_native_atomic_current_v3(uuid,uuid,bigint)",
    "app_private.native_v3_decision_digest(uuid)",
    "app_private.native_v3_resolve_retired_served_format(uuid,uuid)",
    "app_private.record_native_served_retirement_v3(uuid,uuid)",
    "app_private.native_v3_retirement_cleanup_allowed(uuid,uuid,text)",
    "app_private.purge_native_served_retirement_v3(uuid,uuid)",
    "app_private.purge_native_served_retirements_owner_v3(uuid)",
    "app_private.ensure_native_served_expiry_v3(uuid,uuid)",
    "app_private.expire_native_served_authority_v3(uuid,uuid)",
    "app_private.purge_native_served_authority_v3(uuid,uuid)",
    "app_private.purge_native_served_expiry_receipts_v3(uuid,uuid)",
    "app_private.purge_native_served_owner_v3(uuid)",
    "app_private.native_v3_assert_owner_has_no_served3(uuid)",
    "app_private.count_native_item_insert_v3()",
    "app_private.check_native_shared_truth_v3()",
    "app_private.check_native_source_lifecycle_v3()",
    "app_private.guard_native_source_removal_v3()",
    "app_private.invalidate_native_export_source_v3()",
    "app_private.assert_native_atomic_capture(uuid,boolean)",
    "app_private.privacy_owner_allowed(uuid)",
    "app_private.privacy_entity_allowed(text,uuid)",
    "app_private.native_pack_payload_v3(ml.offline_pack_binding_receipt)",
    "app_private.assert_native_offline_pack_v3(uuid)",
    "app_private.create_native_offline_pack_v3(uuid,uuid,uuid,uuid,bigint,uuid,uuid)",
    "app_private.read_native_offline_pack_v3(uuid,uuid,uuid,uuid,bigint,uuid,boolean)",
)


def _pair(name: str, result: str, v3: str) -> _Route:
    return _Route(
        name,
        "uuid,uuid",
        "p_owner uuid,p_request uuid",
        result,
        f"""DECLARE format smallint;
BEGIN
 format:=app_private.native_v3_resolve_served_format(p_owner,p_request);
 IF format IN (1,2) THEN
  RETURN app_private.{name}_v2_frozen(p_owner,p_request);
 ELSIF format=3 THEN
  RETURN app_private.{v3}(p_owner,p_request);
 END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END;""",
    )


ROUTES = (
    _Route(
        "native_served_decision_digest",
        "uuid",
        "request_id uuid",
        "bytea",
        """DECLARE format smallint;
BEGIN
 IF app_private.native_v3_legacy_verifier_route_v3(request_id) THEN
  RETURN app_private.native_served_decision_digest_v2_frozen(request_id); END IF;
 format:=app_private.native_v3_resolve_served_format(NULL,request_id);
 IF format IN (1,2) THEN
  RETURN app_private.native_served_decision_digest_v2_frozen(request_id);
 ELSIF format=3 THEN RETURN app_private.native_v3_decision_digest(request_id); END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END;""",
    ),
    _Route(
        "assert_native_served_authority",
        "uuid",
        "request_id uuid",
        "void",
        """DECLARE format smallint;
BEGIN
 IF app_private.native_v3_legacy_verifier_route_v3(request_id) THEN
  PERFORM app_private.classify_native_legacy_served_v2(
   (SELECT r.user_id FROM ml.recommendation_request r
    WHERE r.recommendation_request_id=request_id),request_id); RETURN; END IF;
 format:=app_private.native_v3_resolve_served_format(NULL,request_id);
 IF format IN (1,2) THEN
  PERFORM app_private.assert_native_served_authority_v2_frozen(request_id); RETURN;
 ELSIF format=3 THEN
  -- A causal parent assertion checks retained served truth only. Re-entering
  -- live source facts here could traverse an arbitrarily long ancestor chain.
  PERFORM app_private.assert_native_served_truth_v3(request_id); RETURN;
 END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END;""",
    ),
    _pair("ensure_native_served_expiry", "boolean", "ensure_native_served_expiry_v3"),
    _pair("expire_native_served_authority", "jsonb", "expire_native_served_authority_v3"),
    _Route(
        "read_native_served_authority",
        "uuid,uuid",
        "p_owner uuid,p_request uuid",
        "jsonb",
        """DECLARE format smallint;
BEGIN
 format:=app_private.native_v3_resolve_served_format(p_owner,p_request);
 IF format IN (1,2) THEN
  RETURN app_private.read_native_served_authority_v2_frozen(p_owner,p_request);
 ELSIF format=3 THEN
  RAISE EXCEPTION 'NATIVE_V3_BOUNDED_READER_REQUIRED' USING ERRCODE='55000'; END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END;""",
    ),
    _pair("purge_native_served_authority", "jsonb", "purge_native_served_authority_v3"),
    _Route(
        "purge_native_served_expiry_receipts",
        "uuid,uuid",
        "p_owner uuid,p_request uuid",
        "jsonb",
        """DECLARE format smallint; result jsonb; removed bigint; prior_removed bigint;
BEGIN
 format:=app_private.native_v3_resolve_served_format(p_owner,p_request);
 IF format IN (1,2) THEN
  result:=app_private.purge_native_served_expiry_receipts_v2_frozen(p_owner,p_request);
  IF result->>'status'='EXPIRED_RECEIPTS_REMOVED' THEN
   prior_removed:=(result->>'removed')::bigint;
   IF prior_removed IS NULL OR prior_removed<0 THEN
    RAISE EXCEPTION 'NATIVE_V3_RECEIPT_PURGE_COUNT' USING ERRCODE='23514'; END IF;
   removed:=app_private.purge_native_served_retirement_v3(p_owner,p_request);
   IF removed IS NULL OR removed<0 THEN
    RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_PURGE_COUNT' USING ERRCODE='23514'; END IF;
   result:=jsonb_set(result,'{removed}',to_jsonb(prior_removed+removed));
  END IF;
  RETURN result;
 ELSIF format=3 THEN
  RETURN app_private.purge_native_served_expiry_receipts_v3(p_owner,p_request);
 END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END;""",
    ),
    _Route(
        "purge_native_served_owner",
        "uuid",
        "target uuid",
        "bigint",
        """DECLARE removed bigint; old_removed bigint;
BEGIN
 -- The V3 sibling authenticates the same privacy purge context before mutation.
 -- It must remove all exact format3 graph/receipt members, not return PENDING.
 removed:=app_private.purge_native_served_owner_v3(target);
 IF removed IS NULL OR removed<0 THEN
  RAISE EXCEPTION 'NATIVE_V3_OWNER_PURGE_INCOMPLETE' USING ERRCODE='55000'; END IF;
 PERFORM app_private.native_v3_assert_owner_has_no_served3(target);
 old_removed:=app_private.purge_native_served_owner_v2_frozen(target);
 IF old_removed IS NULL OR old_removed<0 THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_OWNER_PURGE_COUNT' USING ERRCODE='23514'; END IF;
 -- Original common parents are removed later by account.purge_account.
 -- Its pinned final phase cleans typed retirement after those parents vanish.
 RETURN removed+old_removed;
END;""",
    ),
    _Route(
        "commit_native_served_authority",
        "uuid,uuid,uuid,uuid,bigint,jsonb",
        "p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,p_identity bigint,doc jsonb",
        "jsonb",
        """DECLARE result jsonb;
BEGIN
 PERFORM app_private.native_v3_assert_legacy_served_writer(p_owner,(doc->>'request_id')::uuid);
 result:=app_private.commit_native_served_authority_v2_frozen(
  p_owner,p_device,p_epoch,p_server,p_identity,doc);
 PERFORM app_private.classify_native_legacy_served_v2(p_owner,(doc->>'request_id')::uuid);
 RETURN result;
END;""",
    ),
    _Route(
        "commit_native_atomic_served",
        "uuid,uuid,uuid,uuid,bigint,jsonb,uuid",
        "p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,p_identity bigint,"
        "doc jsonb,p_original uuid",
        "jsonb",
        """DECLARE result jsonb;
BEGIN
 PERFORM app_private.native_v3_assert_legacy_served_writer(p_owner,(doc->>'request_id')::uuid);
 PERFORM app_private.native_v3_bind_legacy_atomic_original(p_owner,p_original);
 result:=app_private.commit_native_atomic_served_v2_frozen(
  p_owner,p_device,p_epoch,p_server,p_identity,doc,p_original);
 PERFORM app_private.classify_native_legacy_served_v2(p_owner,(doc->>'request_id')::uuid);
 RETURN result;
END;""",
    ),
    _Route(
        "commit_native_atomic_capture",
        "uuid,uuid,uuid,uuid,uuid,bigint,jsonb",
        "original uuid,p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,"
        "p_identity bigint,doc jsonb",
        "jsonb",
        """DECLARE result jsonb;
BEGIN
 PERFORM app_private.native_v3_assert_legacy_served_writer(p_owner,(doc->>'request_id')::uuid);
 PERFORM app_private.native_v3_bind_legacy_atomic_original(p_owner,original);
 result:=app_private.commit_native_atomic_capture_v2_frozen(
  original,p_owner,p_device,p_epoch,p_server,p_identity,doc);
 PERFORM app_private.classify_native_legacy_original_v2(
  p_owner,original,(doc->>'request_id')::uuid);
 RETURN result;
END;""",
    ),
    _Route(
        "native_pack_payload_v2",
        "ml.offline_pack_binding_receipt",
        "b ml.offline_pack_binding_receipt",
        "bytea",
        """DECLARE format smallint;
BEGIN
 format:=app_private.native_v3_resolve_served_format(b.owner_user_id,b.recommendation_request_id);
 IF format IN (1,2) THEN RETURN app_private.native_pack_payload_v2_v2_frozen(b);
 ELSIF format=3 THEN RETURN app_private.native_pack_payload_v3(b); END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END;""",
    ),
    _Route(
        "assert_native_offline_pack_v2",
        "uuid",
        "pid uuid",
        "void",
        """DECLARE format smallint;
BEGIN
 IF pid IS NULL THEN
  PERFORM app_private.assert_native_offline_pack_v2_v2_frozen(pid); RETURN; END IF;
 format:=app_private.native_v3_resolve_pack_format(NULL,pid);
 IF format IN (1,2) THEN
  PERFORM app_private.assert_native_offline_pack_v2_v2_frozen(pid);
  IF format=2 AND EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=pid)
  THEN PERFORM app_private.classify_native_pack_format_v3(
   (SELECT b.owner_user_id FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=pid),pid); END IF;
  RETURN;
 ELSIF format=3 THEN
  PERFORM app_private.assert_native_offline_pack_v3(pid);
  IF EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=pid) THEN
   PERFORM app_private.classify_native_pack_format_v3(
    (SELECT b.owner_user_id FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=pid),pid); END IF;
  RETURN; END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END;""",
    ),
    _Route(
        "create_native_offline_pack_v2",
        "uuid,uuid,uuid,uuid,bigint,uuid,uuid",
        "p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,p_identity bigint,"
        "p_request uuid,p_pack uuid",
        "jsonb",
        """DECLARE format smallint;
BEGIN
 format:=app_private.native_v3_resolve_served_format(p_owner,p_request);
 IF format IN (1,2) THEN
  RETURN app_private.create_native_offline_pack_v2_v2_frozen(
   p_owner,p_device,p_epoch,p_server,p_identity,p_request,p_pack);
 ELSIF format=3 THEN
  RETURN app_private.create_native_offline_pack_v3(
   p_owner,p_device,p_epoch,p_server,p_identity,p_request,p_pack); END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END;""",
    ),
    _Route(
        "read_native_offline_pack_v2",
        "uuid,uuid,uuid,uuid,bigint,uuid,boolean",
        "p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,p_identity bigint,"
        "p_pack uuid,p_payload boolean",
        "jsonb",
        """DECLARE format smallint;
BEGIN
 format:=app_private.native_v3_resolve_pack_format(p_owner,p_pack);
 IF format IN (1,2) THEN
  RETURN app_private.read_native_offline_pack_v2_v2_frozen(
   p_owner,p_device,p_epoch,p_server,p_identity,p_pack,p_payload);
 ELSIF format=3 THEN
  RETURN app_private.read_native_offline_pack_v3(
   p_owner,p_device,p_epoch,p_server,p_identity,p_pack,p_payload); END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END;""",
    ),
)

SHARED_TABLES = (
    "recommendation_request",
    "recommendation_item",
    "recommendation_serving_decision",
    "recommendation_served_item",
    "recommendation_request_replay_payload",
)
SOURCE_TABLES = (
    "native_original_capture_v3",
    "native_capture_source_event_v3",
    "native_capture_lineage_cursor_v3",
    "native_capture_replay_v3",
    "native_atomic_capture_connection_v3",
)

GUARD_ROUTE = _Route("protect_native_served_authority", "", "", "trigger", "")
FORMAT_GUARD_ROUTE = _Route(
    "guard_native_request_write_v3",
    "",
    "",
    "trigger",
    "",
    "guard_native_request_write_v3_frozen",
)
FROZEN_ROUTES = (*ROUTES, GUARD_ROUTE, FORMAT_GUARD_ROUTE)
GUARD_DISPATCH_SIGNATURE = "app_private.protect_native_served_authority_dispatch_v3()"
ROOT_ORIGINAL_UNIQUE_INDEX = "ml.native_root_v3_original_unique"
NEW_PRIVATE_FUNCTIONS = (
    GUARD_DISPATCH_SIGNATURE,
    "app_private.guard_native_shared_truth_v3()",
    "app_private.guard_native_format_write_dispatch_v3()",
    "app_private.native_v3_resolve_served_format(uuid,uuid)",
    "app_private.native_v3_bound_original(uuid,uuid)",
    "app_private.native_v3_assert_no_source3_roles(uuid,uuid)",
    "app_private.guard_native_legacy_classification_v3()",
    "app_private.check_native_legacy_classification_empty_v3()",
    "app_private.native_v3_legacy_verifier_route_v3(uuid)",
    "app_private.classify_native_legacy_served_v2(uuid,uuid)",
    "app_private.classify_native_legacy_original_v2(uuid,uuid,uuid)",
    "app_private.native_v3_assert_legacy_served_writer(uuid,uuid)",
    "app_private.native_v3_bind_legacy_atomic_original(uuid,uuid)",
    "app_private.native_v3_bind_legacy_served_insert()",
    "app_private.guard_native_legacy_original_binding_v3()",
    "app_private.guard_native_legacy_retirement_witness_v3()",
    "app_private.native_v3_assert_legacy_format_retirement(uuid,uuid,text)",
    "app_private.retire_native_request_format_v3()",
    "app_private.native_v3_legacy_retirement_provenance(uuid,uuid)",
    "app_private.native_v3_resolve_pack_format(uuid,uuid)",
    "app_private.classify_native_pack_format_v3(uuid,uuid)",
    "app_private.guard_native_pack_format_binding_v3()",
    "app_private.check_native_pack_classification_empty_v3()",
    "app_private.witness_native_ordinary_pack_delete_v3()",
    "app_private.native_v3_assert_dispatch_backfill()",
    "app_private.native_v3_backfill_legacy_formats_v3()",
)

# This resolver never assigns a format or treats absence as2. Private fixed
# classifiers below establish legacy provenance only after the frozen complete
# validator succeeds; resource qualification of those old validators stays held.
RESOLVER_SQL = r"""
CREATE FUNCTION app_private.native_v3_resolve_served_format(p_owner uuid,p_request uuid)
RETURNS smallint LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_owner uuid; v_version integer; v_format integer; v_format_owner uuid; v_origin_owner uuid;
 v_retired_owner uuid; v_retired_format smallint;
 v_original_format integer; v_original_owner uuid; v_root_owner uuid; v_original uuid;
 v_context app_private.native_request_write_context_v3%ROWTYPE;
 v_witness app_private.native_request_deletion_witness_v3%ROWTYPE;
BEGIN
 IF p_request IS NULL THEN
  RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 SELECT r.user_id,r.serving_contract_version INTO v_owner,v_version
  FROM ml.recommendation_request r WHERE r.recommendation_request_id=p_request;
 SELECT f.owner_user_id,f.request_hash_format_version INTO v_format_owner,v_format
  FROM ml.native_served_request_hash_format f WHERE f.served_request_id=p_request;
 SELECT f.owner_user_id,f.original_capture_format_version
  INTO v_original_owner,v_original_format FROM ml.native_original_capture_format f
  WHERE f.original_request_id=p_request;
 SELECT r.owner_user_id,r.original_request_id INTO v_root_owner,v_original
  FROM ml.recommendation_request_root_v3 r WHERE r.served_request_id=p_request;
 SELECT r.owner_user_id INTO v_origin_owner FROM ml.recommendation_request_root_v3 r
  WHERE r.original_request_id=p_request;
 SELECT c.* INTO v_context FROM app_private.native_request_write_context_v3 c
  WHERE c.transaction_id=pg_current_xact_id_if_assigned() AND c.backend_pid=pg_backend_pid()
   AND c.served_request_id=p_request;
 -- The protected same-fullxid/PID witness survives the origin/context checker
 -- removing its context, including early constraints and final deferred checks.
 SELECT w.* INTO v_witness FROM app_private.native_request_deletion_witness_v3 w
  WHERE w.transaction_id=pg_current_xact_id_if_assigned() AND w.backend_pid=pg_backend_pid()
   AND w.served_request_id=p_request AND w.operation='HISTORY_PURGE'
   AND w.original_capture_format_version=3 AND w.request_hash_format_version=3;
 IF (v_format IS NOT NULL AND v_format NOT IN (2,3))
  OR (v_original_format IS NOT NULL AND v_original_format NOT IN (2,3))
  OR (v_format IS NOT NULL AND v_original_format IS NOT NULL)
  OR (v_format_owner IS NOT NULL AND v_owner IS NOT NULL AND v_format_owner<>v_owner)
  OR (v_original_owner IS NOT NULL AND v_owner IS NOT NULL AND v_original_owner<>v_owner)
  OR (v_origin_owner IS NOT NULL AND v_owner IS NOT NULL AND v_origin_owner<>v_owner)
  OR (v_root_owner IS NOT NULL AND v_owner IS NOT NULL AND v_root_owner<>v_owner)
  OR (v_root_owner IS NOT NULL AND v_format_owner IS NOT NULL AND v_root_owner<>v_format_owner)
  OR (v_witness.transaction_id IS NOT NULL AND (
   (v_owner IS NOT NULL AND v_witness.owner_user_id<>v_owner)
   OR (v_format_owner IS NOT NULL AND v_witness.owner_user_id<>v_format_owner)
   OR (v_root_owner IS NOT NULL AND (v_witness.owner_user_id<>v_root_owner
    OR v_witness.original_request_id<>v_original))
   OR (v_context.transaction_id IS NOT NULL AND (
    v_witness.owner_user_id<>v_context.owner_user_id
    OR v_witness.original_request_id<>v_context.original_request_id))))
  OR (v_context.transaction_id IS NOT NULL AND (
   (v_owner IS NOT NULL AND v_context.owner_user_id<>v_owner)
   OR (v_format_owner IS NOT NULL AND v_context.owner_user_id<>v_format_owner)
   OR (v_root_owner IS NOT NULL AND (v_context.owner_user_id<>v_root_owner
    OR v_context.original_request_id<>v_original)))) THEN
  RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
 -- An actual ordinary original remains ordinary. A caller-supplied version or
 -- an absent native binding cannot classify a served row as legacy2.
 IF v_version=1 AND v_format IS NULL AND v_root_owner IS NULL
  AND v_context.transaction_id IS NULL THEN
  IF p_owner IS NOT NULL AND p_owner IS DISTINCT FROM v_owner THEN
   RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
  RETURN 1;
 END IF;
 IF v_original_format IS NOT NULL OR v_origin_owner IS NOT NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
 IF v_format=2 AND v_version=2 AND v_root_owner IS NULL
  AND v_context.transaction_id IS NULL THEN
  IF p_owner IS NOT NULL AND p_owner IS DISTINCT FROM v_owner THEN
   RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
  RETURN 2;
 END IF;
 IF v_format=3 AND v_version=2 AND v_root_owner IS NOT NULL THEN
  IF p_owner IS NOT NULL AND p_owner IS DISTINCT FROM v_owner THEN
   RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
  RETURN 3;
 END IF;
 -- Before immutable root insertion, only the exact current private CREATE
 -- reservation can establish3. During physical deletion only its protected
 -- format3 witness survives missing live bindings/parents.
 IF v_context.transaction_id IS NOT NULL AND v_format IS DISTINCT FROM 2
  AND (v_version IS NULL OR v_version=2) AND (
   v_context.operation='CREATE' OR
   (v_context.operation='HISTORY_PURGE' AND v_witness.transaction_id IS NOT NULL)) THEN
  IF p_owner IS NOT NULL AND p_owner IS DISTINCT FROM v_context.owner_user_id THEN
   RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
  RETURN 3;
 END IF;
 IF v_version IS NULL AND v_format IS NULL AND v_root_owner IS NULL
  AND v_context.transaction_id IS NULL THEN
  IF v_witness.transaction_id IS NOT NULL THEN
   IF p_owner IS NOT NULL AND p_owner IS DISTINCT FROM v_witness.owner_user_id THEN
    RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
   RETURN 3;
  END IF;
  SELECT w.owner_user_id INTO v_retired_owner
   FROM app_private.native_legacy_retirement_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id_if_assigned() AND w.backend_pid=pg_backend_pid()
    AND w.request_id=p_request AND w.served_request_id=p_request
    AND w.request_role='SERVED' AND w.format_version=2;
  IF v_retired_owner IS NOT NULL THEN
   PERFORM app_private.native_v3_assert_no_source3_roles(v_retired_owner,p_request);
   IF p_owner IS NOT NULL AND p_owner IS DISTINCT FROM v_retired_owner THEN
    RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
   RETURN 2;
  END IF;
  -- Independent durable typed retirement is historical routing only. Expired
  -- unremoved evidence still selects its exact cleanup sibling, never reuse.
  v_retired_format:=app_private.native_v3_resolve_retired_served_format(p_owner,p_request);
  IF v_retired_format IN (2,3) THEN RETURN v_retired_format; END IF;
 END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END $$;

CREATE FUNCTION app_private.native_v3_bound_original(p_owner uuid,p_served uuid)
RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_original uuid; v_current uuid;
BEGIN
 IF app_private.native_v3_resolve_served_format(p_owner,p_served)<>3 THEN
  RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 SELECT r.original_request_id INTO v_original FROM ml.recommendation_request_root_v3 r
  WHERE r.served_request_id=p_served AND (p_owner IS NULL OR r.owner_user_id=p_owner);
 SELECT c.original_request_id INTO v_current FROM app_private.native_request_write_context_v3 c
  WHERE c.transaction_id=pg_current_xact_id_if_assigned() AND c.backend_pid=pg_backend_pid()
   AND c.served_request_id=p_served AND (p_owner IS NULL OR c.owner_user_id=p_owner)
   AND c.operation IN ('CREATE','HISTORY_PURGE');
 IF v_original IS NOT NULL AND v_current IS NOT NULL AND v_original<>v_current THEN
  RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
 IF v_original IS NULL THEN v_original:=v_current; END IF;
 IF v_original IS NULL OR v_original=p_served THEN
  RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 RETURN v_original;
END $$;
REVOKE ALL ON FUNCTION app_private.native_v3_resolve_served_format(uuid,uuid),
 app_private.native_v3_bound_original(uuid,uuid) FROM PUBLIC;
"""

LEGACY_CLASSIFICATION_SQL = r"""
CREATE TABLE app_private.native_legacy_format_classification_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL CHECK(backend_pid>0),
 owner_user_id uuid NOT NULL,request_id uuid NOT NULL,served_request_id uuid NOT NULL,
 request_role text NOT NULL CHECK(request_role IN ('ORIGINAL','SERVED')),
 phase text NOT NULL CHECK(phase IN ('VERIFYING','VERIFIED')),
 created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 PRIMARY KEY(transaction_id,backend_pid,request_role,request_id),
 CHECK((request_role='SERVED' AND request_id=served_request_id) OR
  (request_role='ORIGINAL' AND request_id<>served_request_id)),
 CHECK(expires_at=created_at+interval '180 days')
);
REVOKE ALL ON TABLE app_private.native_legacy_format_classification_v3 FROM PUBLIC;

CREATE FUNCTION app_private.native_v3_assert_no_source3_roles(p_owner uuid,p_request uuid)
RETURNS void LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_owner IS NULL OR p_request IS NULL
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_format f
   WHERE f.original_request_id=p_request AND
    (f.owner_user_id<>p_owner OR f.original_capture_format_version<>2))
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   WHERE f.served_request_id=p_request AND
    (f.owner_user_id<>p_owner OR f.request_hash_format_version<>2))
  OR EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
   WHERE r.original_request_id=p_request OR r.served_request_id=p_request)
  OR EXISTS(SELECT 1 FROM app_private.native_request_write_context_v3 c
   WHERE c.transaction_id=pg_current_xact_id_if_assigned() AND c.backend_pid=pg_backend_pid()
    AND (c.original_request_id=p_request OR c.served_request_id=p_request))
  OR EXISTS(SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id_if_assigned() AND w.backend_pid=pg_backend_pid()
    AND (w.original_request_id=p_request OR w.served_request_id=p_request)) THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_CROSS_FORMAT' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.guard_native_legacy_classification_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_SCHEMA<>'app_private' OR TG_TABLE_NAME<>'native_legacy_format_classification_v3'
  OR TG_OP NOT IN ('INSERT','UPDATE','DELETE') THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_CLASSIFICATION_TRIGGER' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN
  IF OLD.transaction_id IS DISTINCT FROM pg_current_xact_id()
   OR OLD.backend_pid IS DISTINCT FROM pg_backend_pid() OR OLD.phase<>'VERIFIED' THEN
   RAISE EXCEPTION 'NATIVE_V3_LEGACY_CLASSIFICATION_CONTEXT' USING ERRCODE='23514'; END IF;
  RETURN OLD;
 END IF;
 IF NEW.transaction_id IS DISTINCT FROM pg_current_xact_id()
  OR NEW.backend_pid IS DISTINCT FROM pg_backend_pid() THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_CLASSIFICATION_CONTEXT' USING ERRCODE='23514'; END IF;
 PERFORM app_private.native_v3_assert_no_source3_roles(NEW.owner_user_id,NEW.request_id);
 PERFORM app_private.native_v3_assert_no_source3_roles(NEW.owner_user_id,NEW.served_request_id);
 IF TG_OP='INSERT' THEN
  IF NEW.phase<>'VERIFYING' THEN
   RAISE EXCEPTION 'NATIVE_V3_LEGACY_CLASSIFICATION_PHASE' USING ERRCODE='23514'; END IF;
 ELSE
  IF OLD.phase<>'VERIFYING' OR NEW.phase<>'VERIFIED'
   OR OLD.transaction_id IS DISTINCT FROM NEW.transaction_id
   OR OLD.backend_pid IS DISTINCT FROM NEW.backend_pid
   OR OLD.owner_user_id IS DISTINCT FROM NEW.owner_user_id
   OR OLD.request_id IS DISTINCT FROM NEW.request_id
   OR OLD.served_request_id IS DISTINCT FROM NEW.served_request_id
   OR OLD.request_role IS DISTINCT FROM NEW.request_role
   OR OLD.created_at IS DISTINCT FROM NEW.created_at OR OLD.expires_at IS DISTINCT FROM NEW.expires_at
   THEN RAISE EXCEPTION 'NATIVE_V3_LEGACY_CLASSIFICATION_PHASE' USING ERRCODE='23514'; END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM ml.recommendation_request r
  WHERE r.recommendation_request_id=NEW.request_id AND r.user_id=NEW.owner_user_id
   AND r.created_at=NEW.created_at AND ((NEW.request_role='SERVED' AND r.serving_contract_version=2)
    OR (NEW.request_role='ORIGINAL' AND r.serving_contract_version=1))) THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_CLASSIFICATION_PARENT' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION app_private.check_native_legacy_classification_empty_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM app_private.native_legacy_format_classification_v3 c
  WHERE c.transaction_id=NEW.transaction_id AND c.backend_pid=NEW.backend_pid) THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_CLASSIFICATION_LEAK' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER native_legacy_classification_guard BEFORE INSERT OR UPDATE OR DELETE
 ON app_private.native_legacy_format_classification_v3 FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_legacy_classification_v3();
CREATE CONSTRAINT TRIGGER native_legacy_classification_empty AFTER INSERT OR UPDATE
 ON app_private.native_legacy_format_classification_v3 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app_private.check_native_legacy_classification_empty_v3();

CREATE FUNCTION app_private.native_v3_legacy_verifier_route_v3(p_request uuid)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_owner uuid;
BEGIN
 SELECT r.user_id INTO v_owner FROM ml.recommendation_request r
  WHERE r.recommendation_request_id=p_request AND r.serving_contract_version=2;
 IF v_owner IS NULL OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
  WHERE f.served_request_id=p_request) THEN RETURN false; END IF;
 PERFORM app_private.native_v3_assert_no_source3_roles(v_owner,p_request);
 -- This selects the legacy VERIFIER/digest only. It cannot select read/expiry,
 -- serving or format insertion; completion requires the classifier below.
 RETURN EXISTS(SELECT 1 FROM app_private.native_served_authority_context c
  WHERE c.transaction_id=txid_current() AND c.recommendation_request_id=p_request
   AND c.operation='CREATE') OR EXISTS(
  SELECT 1 FROM app_private.native_legacy_format_classification_v3 c
  WHERE c.transaction_id=pg_current_xact_id_if_assigned() AND c.backend_pid=pg_backend_pid()
   AND c.owner_user_id=v_owner AND c.request_id=p_request AND c.request_role='SERVED'
   AND c.served_request_id=p_request AND c.phase IN ('VERIFYING','VERIFIED'));
END $$;

CREATE FUNCTION app_private.classify_native_legacy_served_v2(p_owner uuid,p_served uuid)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_created timestamptz; v_expires timestamptz;
BEGIN
 PERFORM app_private.lock_recommendation_request_v2(p_served);
 PERFORM app_private.native_v3_assert_no_source3_roles(p_owner,p_served);
 IF EXISTS(SELECT 1 FROM ml.native_original_capture_format f
  WHERE f.original_request_id=p_served) THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_CROSS_FORMAT' USING ERRCODE='23514'; END IF;
 -- A previously classified tag is idempotent after replay expiry. Requiring a
 -- fresh replay here would change the frozen writer's REPLAY_EXPIRED result.
 IF EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
  WHERE f.served_request_id=p_served) THEN
  IF NOT EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   JOIN ml.recommendation_request r ON r.recommendation_request_id=f.served_request_id
    AND r.user_id=f.owner_user_id AND r.serving_contract_version=2
   WHERE f.served_request_id=p_served AND f.owner_user_id=p_owner
    AND f.request_hash_format_version=2 AND f.created_transaction IS NULL
    AND f.created_at=r.created_at AND f.expires_at=r.created_at+interval '180 days') THEN
   RAISE EXCEPTION 'NATIVE_V3_LEGACY_CROSS_FORMAT' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 SELECT r.created_at,p.replay_expires_at INTO v_created,v_expires
  FROM ml.recommendation_request r JOIN ml.recommendation_request_replay_payload p
   ON p.recommendation_request_id=r.recommendation_request_id AND p.owner_user_id=r.user_id
  WHERE r.recommendation_request_id=p_served AND r.user_id=p_owner
   AND r.serving_contract_version=2;
 IF NOT FOUND OR v_expires IS DISTINCT FROM v_created+interval '180 days' THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_FULL_GRAPH_REQUIRED' USING ERRCODE='23514'; END IF;
 INSERT INTO app_private.native_legacy_format_classification_v3 VALUES(
  pg_current_xact_id(),pg_backend_pid(),p_owner,p_served,p_served,'SERVED','VERIFYING',
  v_created,v_expires);
 PERFORM app_private.assert_native_served_authority_v2_frozen(p_served);
 UPDATE app_private.native_legacy_format_classification_v3 SET phase='VERIFIED'
  WHERE transaction_id=pg_current_xact_id() AND backend_pid=pg_backend_pid()
   AND owner_user_id=p_owner AND request_id=p_served AND request_role='SERVED';
 INSERT INTO ml.native_served_request_hash_format VALUES(
  p_served,p_owner,2,NULL,v_created,v_expires);
 DELETE FROM app_private.native_legacy_format_classification_v3
  WHERE transaction_id=pg_current_xact_id() AND backend_pid=pg_backend_pid()
   AND owner_user_id=p_owner AND request_id=p_served AND request_role='SERVED';
END $$;

CREATE FUNCTION app_private.classify_native_legacy_original_v2(
 p_owner uuid,p_original uuid,p_served uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_created timestamptz; v_expires timestamptz;
 v_request_sha bytea; v_capture_sha bytea; v_decision_sha bytea;
BEGIN
 PERFORM app_private.native_v3_assert_no_source3_roles(p_owner,p_original);
 PERFORM app_private.native_v3_assert_no_source3_roles(p_owner,p_served);
 PERFORM app_private.classify_native_legacy_served_v2(p_owner,p_served);
 SELECT r.created_at,b.expires_at,r.request_sha256,c.capture_sha256,c.decision_sha256
  INTO v_created,v_expires,v_request_sha,v_capture_sha,v_decision_sha
  FROM ml.native_atomic_capture_connection c JOIN ml.recommendation_request r
   ON r.recommendation_request_id=c.original_request_id AND r.user_id=c.owner_user_id
  JOIN ml.sona_capture_bundle b ON b.recommendation_request_id=c.original_request_id
   AND b.user_id=c.owner_user_id
  WHERE c.original_request_id=p_original AND c.served_request_id=p_served
   AND c.owner_user_id=p_owner AND r.serving_contract_version=1 AND p_original<>p_served;
 IF NOT FOUND OR v_expires IS DISTINCT FROM v_created+interval '180 days' OR EXISTS(
  SELECT 1 FROM ml.native_served_request_hash_format f WHERE f.served_request_id=p_original) THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_ATOMIC_GRAPH_REQUIRED' USING ERRCODE='23514'; END IF;
 -- Fixed historical old validator, never caller-selected initial/current mode.
 PERFORM app_private.assert_native_atomic_capture(p_original,false);
 IF EXISTS(SELECT 1 FROM ml.native_original_capture_format f
  WHERE f.original_request_id=p_original) THEN
  IF NOT EXISTS(SELECT 1 FROM ml.native_original_capture_format f
   WHERE f.original_request_id=p_original AND f.owner_user_id=p_owner
    AND f.original_capture_format_version=2 AND f.created_transaction IS NULL
    AND f.created_at=v_created AND f.expires_at=v_expires) THEN
   RAISE EXCEPTION 'NATIVE_V3_LEGACY_CROSS_FORMAT' USING ERRCODE='23514'; END IF;
  -- An existing tag alone cannot reconstruct withdrawn original provenance.
 END IF;
 IF EXISTS(SELECT 1 FROM app_private.native_legacy_original_binding_v3 b
  WHERE b.original_request_id=p_original) THEN
  IF NOT EXISTS(SELECT 1 FROM app_private.native_legacy_original_binding_v3 b
   WHERE b.original_request_id=p_original AND b.owner_user_id=p_owner
    AND b.served_request_id=p_served AND b.original_request_sha256=v_request_sha
    AND b.capture_sha256=v_capture_sha AND b.decision_sha256=v_decision_sha
    AND b.created_at=v_created AND b.expires_at=v_expires) THEN
   RAISE EXCEPTION 'NATIVE_V3_LEGACY_ORIGINAL_PROVENANCE' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 INSERT INTO app_private.native_legacy_format_classification_v3 VALUES(
  pg_current_xact_id(),pg_backend_pid(),p_owner,p_original,p_served,'ORIGINAL','VERIFYING',
  v_created,v_expires);
 UPDATE app_private.native_legacy_format_classification_v3 SET phase='VERIFIED'
  WHERE transaction_id=pg_current_xact_id() AND backend_pid=pg_backend_pid()
   AND owner_user_id=p_owner AND request_id=p_original AND request_role='ORIGINAL';
 INSERT INTO app_private.native_legacy_original_binding_v3 VALUES(
  p_original,p_owner,p_served,2,v_request_sha,v_capture_sha,v_decision_sha,v_created,v_expires);
 IF NOT EXISTS(SELECT 1 FROM ml.native_original_capture_format f
  WHERE f.original_request_id=p_original) THEN
  INSERT INTO ml.native_original_capture_format VALUES(
   p_original,p_owner,2,NULL,v_created,v_expires);
 END IF;
 DELETE FROM app_private.native_legacy_format_classification_v3
  WHERE transaction_id=pg_current_xact_id() AND backend_pid=pg_backend_pid()
   AND owner_user_id=p_owner AND request_id=p_original AND request_role='ORIGINAL';
END $$;

CREATE FUNCTION app_private.native_v3_assert_legacy_served_writer(p_owner uuid,p_served uuid)
RETURNS void LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM app_private.native_v3_assert_no_source3_roles(p_owner,p_served);
 IF NOT EXISTS(SELECT 1 FROM ml.recommendation_request r
   WHERE r.recommendation_request_id=p_served)
  AND app_private.native_v3_resolve_retired_served_format(NULL,p_served) IS NOT NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIRED_REQUEST_REUSE' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM ml.native_original_capture_format f WHERE f.original_request_id=p_served)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request r WHERE r.recommendation_request_id=p_served
   AND (r.user_id<>p_owner OR r.serving_contract_version<>2)) THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_CROSS_FORMAT' USING ERRCODE='23514'; END IF;
 -- Missing is admissible only as input to the explicit old authenticated writer,
 -- not classified2. That frozen writer still performs its exact refusal checks.
END $$;

CREATE FUNCTION app_private.native_v3_bind_legacy_atomic_original(p_owner uuid,p_original uuid)
RETURNS void LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM app_private.native_v3_assert_no_source3_roles(p_owner,p_original);
 IF NOT EXISTS(SELECT 1 FROM ml.recommendation_request r JOIN ml.sona_capture_bundle b
  ON b.recommendation_request_id=r.recommendation_request_id AND b.user_id=r.user_id
  WHERE r.recommendation_request_id=p_original AND r.user_id=p_owner
   AND r.serving_contract_version=1) THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_ATOMIC_GRAPH_REQUIRED' USING ERRCODE='23514'; END IF;
 -- Preflight only. Format2 assignment occurs after actual old connection closure.
END $$;

CREATE FUNCTION app_private.native_v3_bind_legacy_served_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_SCHEMA<>'ml' OR TG_TABLE_NAME<>'recommendation_request' OR TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_BIND_TRIGGER' USING ERRCODE='23514'; END IF;
 IF NEW.serving_contract_version=1 THEN RETURN NEW; END IF;
 IF EXISTS(SELECT 1 FROM app_private.native_request_write_context_v3 c
  WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
   AND c.owner_user_id=NEW.user_id AND c.served_request_id=NEW.recommendation_request_id
   AND c.operation='CREATE') THEN RETURN NEW; END IF;
 PERFORM app_private.native_v3_assert_no_source3_roles(NEW.user_id,NEW.recommendation_request_id);
 IF NOT EXISTS(SELECT 1 FROM app_private.native_served_authority_context c
  WHERE c.transaction_id=txid_current() AND c.recommendation_request_id=NEW.recommendation_request_id
   AND c.operation='CREATE') THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_CREATE_CONTEXT_REQUIRED' USING ERRCODE='23514'; END IF;
 -- No early format2 INSERT: complete old rows must pass the fixed classifier.
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION app_private.native_v3_assert_no_source3_roles(uuid,uuid),
 app_private.guard_native_legacy_classification_v3(),
 app_private.check_native_legacy_classification_empty_v3(),
 app_private.native_v3_legacy_verifier_route_v3(uuid),
 app_private.classify_native_legacy_served_v2(uuid,uuid),
 app_private.classify_native_legacy_original_v2(uuid,uuid,uuid),
 app_private.native_v3_assert_legacy_served_writer(uuid,uuid),
 app_private.native_v3_bind_legacy_atomic_original(uuid,uuid),
 app_private.native_v3_bind_legacy_served_insert() FROM PUBLIC;
"""

LEGACY_RETIREMENT_SQL = r"""
-- Typed provenance extension: populated only by completed actual old atomic
-- closure; survives capture withdrawal. No FK/cascade to a live payload.
CREATE TABLE app_private.native_legacy_original_binding_v3 (
 original_request_id uuid PRIMARY KEY,owner_user_id uuid NOT NULL,
 served_request_id uuid NOT NULL UNIQUE,original_capture_format_version integer NOT NULL CHECK(
  original_capture_format_version=2),original_request_sha256 bytea NOT NULL CHECK(
  octet_length(original_request_sha256)=32),capture_sha256 bytea NOT NULL CHECK(
  octet_length(capture_sha256)=32),decision_sha256 bytea NOT NULL CHECK(
  octet_length(decision_sha256)=32),created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 CHECK(original_request_id<>served_request_id),CHECK(expires_at=created_at+interval '180 days')
);
-- Exact current-xid/PID evidence survives old context deletion and early
-- SET CONSTRAINTS. Retention/cleanup beyond that is explicitly still HOLD.
CREATE TABLE app_private.native_legacy_retirement_witness_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL CHECK(backend_pid>0),
 owner_user_id uuid NOT NULL,request_id uuid NOT NULL,served_request_id uuid NOT NULL,
 request_role text NOT NULL CHECK(request_role IN ('ORIGINAL','SERVED')),
 format_version integer NOT NULL CHECK(format_version=2),request_sha256 bytea NOT NULL CHECK(
  octet_length(request_sha256)=32),capture_sha256 bytea,decision_sha256 bytea NOT NULL CHECK(
  octet_length(decision_sha256)=32),served_items_sha256 bytea,
 reason text NOT NULL CHECK(reason IN ('EXPIRED','ACCOUNT_DELETION')),
 created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 receipt_expires_at timestamptz NOT NULL,
 PRIMARY KEY(transaction_id,backend_pid,request_role,request_id),
 CHECK(expires_at=created_at+interval '180 days'),
 CHECK(receipt_expires_at=created_at+interval '400 days'),
 CHECK((request_role='ORIGINAL' AND request_id<>served_request_id
   AND capture_sha256 IS NOT NULL AND octet_length(capture_sha256)=32) OR
  (request_role='SERVED' AND request_id=served_request_id AND capture_sha256 IS NULL
   AND served_items_sha256 IS NOT NULL AND octet_length(served_items_sha256)=32))
);
REVOKE ALL ON app_private.native_legacy_original_binding_v3,
 app_private.native_legacy_retirement_witness_v3 FROM PUBLIC;

CREATE FUNCTION app_private.native_v3_legacy_retirement_provenance(p_owner uuid,p_request uuid)
RETURNS app_private.native_legacy_retirement_witness_v3
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result app_private.native_legacy_retirement_witness_v3%ROWTYPE;
 v_created timestamptz; v_request_sha bytea; v_version integer;
 binding app_private.native_legacy_original_binding_v3%ROWTYPE;
 receipt app_private.native_served_authority_purge_receipt%ROWTYPE;
 v_privacy boolean;
BEGIN
 PERFORM app_private.native_v3_assert_no_source3_roles(p_owner,p_request);
 SELECT r.created_at,r.request_sha256,r.serving_contract_version
  INTO v_created,v_request_sha,v_version FROM ml.recommendation_request r
  WHERE r.recommendation_request_id=p_request AND r.user_id=p_owner;
 IF NOT FOUND OR octet_length(v_request_sha) IS DISTINCT FROM 32 THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_RETIREMENT_PARENT' USING ERRCODE='23514'; END IF;
 result.transaction_id:=pg_current_xact_id(); result.backend_pid:=pg_backend_pid();
 result.owner_user_id:=p_owner; result.request_id:=p_request; result.format_version:=2;
 result.request_sha256:=v_request_sha; result.created_at:=v_created;
 result.expires_at:=v_created+interval '180 days';
 result.receipt_expires_at:=v_created+interval '400 days';
 v_privacy:=EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
  WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=p_owner)
  AND app_private.privacy_owner_allowed(p_owner);
 IF v_version=2 AND EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
  WHERE f.served_request_id=p_request AND f.owner_user_id=p_owner
   AND f.request_hash_format_version=2 AND f.created_transaction IS NULL
   AND f.created_at=result.created_at AND f.expires_at=result.expires_at) THEN
  result.request_role:='SERVED'; result.served_request_id:=p_request;
  SELECT p.* INTO receipt FROM app_private.native_served_authority_purge_receipt p
   WHERE p.owner_user_id=p_owner AND p.recommendation_request_id=p_request;
  IF NOT FOUND OR receipt.request_sha256 IS DISTINCT FROM v_request_sha
   OR receipt.expires_at IS DISTINCT FROM result.receipt_expires_at OR NOT EXISTS(
    SELECT 1 FROM app_private.native_served_authority_context c WHERE c.transaction_id=txid_current()
     AND c.recommendation_request_id=p_request AND c.operation='PURGE')
   OR (receipt.reason='ACCOUNT_DELETION' AND NOT v_privacy)
   OR (receipt.reason='EXPIRED' AND (clock_timestamp()<result.expires_at
    OR receipt.purged_at<result.expires_at)) THEN
   RAISE EXCEPTION 'NATIVE_V3_LEGACY_RETIREMENT_RECEIPT' USING ERRCODE='23514'; END IF;
  result.reason:=receipt.reason; result.decision_sha256:=receipt.decision_sha256;
  result.served_items_sha256:=receipt.served_items_sha256;
 ELSIF v_version=1 AND EXISTS(SELECT 1 FROM ml.native_original_capture_format f
  WHERE f.original_request_id=p_request AND f.owner_user_id=p_owner
   AND f.original_capture_format_version=2 AND f.created_transaction IS NULL
   AND f.created_at=result.created_at AND f.expires_at=result.expires_at) THEN
  SELECT b.* INTO binding FROM app_private.native_legacy_original_binding_v3 b
   WHERE b.original_request_id=p_request AND b.owner_user_id=p_owner;
  IF NOT FOUND OR binding.original_request_sha256 IS DISTINCT FROM v_request_sha
   OR binding.created_at IS DISTINCT FROM v_created
   OR binding.expires_at IS DISTINCT FROM result.expires_at THEN
   RAISE EXCEPTION 'NATIVE_V3_LEGACY_ORIGINAL_PROVENANCE_REQUIRED' USING ERRCODE='23514'; END IF;
  result.request_role:='ORIGINAL'; result.served_request_id:=binding.served_request_id;
  result.capture_sha256:=binding.capture_sha256; result.decision_sha256:=binding.decision_sha256;
  SELECT p.* INTO receipt FROM app_private.native_served_authority_purge_receipt p
   WHERE p.owner_user_id=p_owner AND p.recommendation_request_id=binding.served_request_id
    AND p.decision_sha256=binding.decision_sha256;
  IF v_privacy THEN result.reason:='ACCOUNT_DELETION';
  ELSIF receipt.recommendation_request_id IS NOT NULL AND receipt.reason='EXPIRED'
   AND clock_timestamp()>=result.expires_at AND receipt.purged_at>=result.expires_at
   AND EXISTS(SELECT 1 FROM app_private.native_served_authority_context c
    WHERE c.transaction_id=txid_current() AND c.recommendation_request_id=binding.served_request_id
     AND c.operation='PURGE') THEN
   result.reason:='EXPIRED'; result.served_items_sha256:=receipt.served_items_sha256;
  ELSE RAISE EXCEPTION 'NATIVE_V3_LEGACY_RETIREMENT_CONTEXT' USING ERRCODE='23514'; END IF;
 ELSE RAISE EXCEPTION 'NATIVE_V3_LEGACY_RETIREMENT_FORMAT' USING ERRCODE='23514'; END IF;
 RETURN result;
END $$;

CREATE FUNCTION app_private.guard_native_legacy_retirement_witness_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE expected app_private.native_legacy_retirement_witness_v3%ROWTYPE;
BEGIN
 IF TG_TABLE_SCHEMA<>'app_private' OR TG_TABLE_NAME<>'native_legacy_retirement_witness_v3'
  OR TG_OP NOT IN ('INSERT','DELETE') THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_RETIREMENT_IMMUTABLE' USING ERRCODE='55000'; END IF;
 IF TG_OP='DELETE' THEN
  IF app_private.native_v3_retirement_cleanup_allowed(
   OLD.owner_user_id,OLD.request_id,TG_TABLE_NAME) IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'NATIVE_V3_LEGACY_RETIREMENT_CLEANUP_REQUIRED' USING ERRCODE='23514'; END IF;
  RETURN OLD;
 END IF;
 expected:=app_private.native_v3_legacy_retirement_provenance(NEW.owner_user_id,NEW.request_id);
 IF NEW IS DISTINCT FROM expected THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_RETIREMENT_PROVENANCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION app_private.guard_native_legacy_original_binding_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_SCHEMA<>'app_private' OR TG_TABLE_NAME<>'native_legacy_original_binding_v3'
  OR TG_OP NOT IN ('INSERT','DELETE') THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_ORIGINAL_BINDING_IMMUTABLE' USING ERRCODE='55000'; END IF;
 IF TG_OP='DELETE' THEN
  IF app_private.native_v3_retirement_cleanup_allowed(
   OLD.owner_user_id,OLD.original_request_id,TG_TABLE_NAME) IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'NATIVE_V3_LEGACY_ORIGINAL_CLEANUP_REQUIRED' USING ERRCODE='23514'; END IF;
  RETURN OLD;
 END IF;
 PERFORM app_private.native_v3_assert_no_source3_roles(NEW.owner_user_id,NEW.original_request_id);
 IF NOT EXISTS(SELECT 1 FROM app_private.native_legacy_format_classification_v3 c
  JOIN ml.native_atomic_capture_connection a ON a.original_request_id=c.request_id
   AND a.served_request_id=c.served_request_id AND a.owner_user_id=c.owner_user_id
  JOIN ml.recommendation_request r ON r.recommendation_request_id=a.original_request_id
   AND r.user_id=a.owner_user_id AND r.serving_contract_version=1
  JOIN ml.sona_capture_bundle b ON b.recommendation_request_id=a.original_request_id
   AND b.user_id=a.owner_user_id
  WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
   AND c.owner_user_id=NEW.owner_user_id AND c.request_id=NEW.original_request_id
   AND c.served_request_id=NEW.served_request_id AND c.request_role='ORIGINAL' AND c.phase='VERIFIED'
   AND NEW.original_capture_format_version=2 AND NEW.original_request_sha256=r.request_sha256
   AND NEW.capture_sha256=a.capture_sha256 AND NEW.capture_sha256=b.bundle_sha256
   AND NEW.decision_sha256=a.decision_sha256 AND NEW.created_at=r.created_at
   AND NEW.expires_at=b.expires_at AND NEW.created_at=c.created_at AND NEW.expires_at=c.expires_at) THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_ORIGINAL_PROVENANCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION app_private.native_v3_assert_legacy_format_retirement(
 p_owner uuid,p_request uuid,p_role text) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_role IS NULL OR p_role NOT IN ('ORIGINAL','SERVED') OR NOT EXISTS(
  SELECT 1 FROM app_private.native_legacy_retirement_witness_v3 w
  WHERE w.transaction_id=pg_current_xact_id_if_assigned() AND w.backend_pid=pg_backend_pid()
   AND w.owner_user_id=p_owner AND w.request_id=p_request AND w.request_role=p_role
   AND w.format_version=2) THEN
  RAISE EXCEPTION 'NATIVE_V3_LEGACY_RETIREMENT_WITNESS_REQUIRED' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.retire_native_request_format_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE proof app_private.native_legacy_retirement_witness_v3%ROWTYPE;
 v_original_format integer; v_served_format integer;
BEGIN
 IF TG_TABLE_SCHEMA<>'ml' OR TG_TABLE_NAME<>'recommendation_request' OR TG_OP<>'DELETE' THEN
  RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_TRIGGER' USING ERRCODE='23514'; END IF;
 SELECT f.original_capture_format_version INTO v_original_format
  FROM ml.native_original_capture_format f WHERE f.original_request_id=OLD.recommendation_request_id
   AND f.owner_user_id=OLD.user_id;
 SELECT f.request_hash_format_version INTO v_served_format
  FROM ml.native_served_request_hash_format f WHERE f.served_request_id=OLD.recommendation_request_id
   AND f.owner_user_id=OLD.user_id;
 IF EXISTS(SELECT 1 FROM ml.native_original_capture_format f
   WHERE f.original_request_id=OLD.recommendation_request_id AND f.owner_user_id<>OLD.user_id)
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   WHERE f.served_request_id=OLD.recommendation_request_id AND f.owner_user_id<>OLD.user_id) THEN
  RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
 IF v_original_format IS NOT NULL AND v_served_format IS NOT NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
 IF v_original_format IS NULL AND v_served_format IS NULL AND OLD.serving_contract_version=2
  AND NOT EXISTS(SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
    AND w.owner_user_id=OLD.user_id AND w.served_request_id=OLD.recommendation_request_id
    AND w.original_capture_format_version=3 AND w.request_hash_format_version=3)
  AND NOT EXISTS(SELECT 1 FROM app_private.native_legacy_retirement_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
    AND w.owner_user_id=OLD.user_id AND w.request_id=OLD.recommendation_request_id
    AND w.request_role='SERVED' AND w.format_version=2) THEN
  RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF v_original_format=2 OR v_served_format=2 THEN
  proof:=app_private.native_v3_legacy_retirement_provenance(OLD.user_id,OLD.recommendation_request_id);
  INSERT INTO app_private.native_legacy_retirement_witness_v3 SELECT proof.*;
  IF proof.request_role='ORIGINAL' THEN
   DELETE FROM ml.native_original_capture_format WHERE original_request_id=OLD.recommendation_request_id
    AND owner_user_id=OLD.user_id AND original_capture_format_version=2;
   -- Independent original2 creation provenance survives parent retirement until
   -- C's actual protected cleanup scope permits its later immutable DELETE.
  ELSE
   -- Children are already gone. Capture typed retirement from the actual
   -- guarded SERVED witness, old PURGE/privacy and content receipt before
   -- removing the last live format2 binding; no full graph runs at this point.
   PERFORM app_private.record_native_served_retirement_v3(OLD.user_id,OLD.recommendation_request_id);
   DELETE FROM ml.native_served_request_hash_format
   WHERE served_request_id=OLD.recommendation_request_id AND owner_user_id=OLD.user_id
    AND request_hash_format_version=2; END IF;
 ELSIF v_original_format=3 OR v_served_format=3 THEN
  IF NOT EXISTS(SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
   JOIN app_private.native_request_write_context_v3 c ON c.transaction_id=w.transaction_id
    AND c.backend_pid=w.backend_pid AND c.owner_user_id=w.owner_user_id
    AND c.original_request_id=w.original_request_id AND c.served_request_id=w.served_request_id
   WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
    AND w.owner_user_id=OLD.user_id AND w.operation='HISTORY_PURGE'
    AND c.operation='HISTORY_PURGE' AND w.original_capture_format_version=3
    AND w.request_hash_format_version=3 AND ((v_original_format=3
     AND w.original_request_id=OLD.recommendation_request_id) OR (v_served_format=3
     AND w.served_request_id=OLD.recommendation_request_id))) THEN
   RAISE EXCEPTION 'NATIVE_V3_RETIREMENT_CONTEXT' USING ERRCODE='23514'; END IF;
  IF v_original_format=3 THEN DELETE FROM ml.native_original_capture_format
   WHERE original_request_id=OLD.recommendation_request_id AND owner_user_id=OLD.user_id
    AND original_capture_format_version=3;
  ELSE DELETE FROM ml.native_served_request_hash_format WHERE served_request_id=OLD.recommendation_request_id
   AND owner_user_id=OLD.user_id AND request_hash_format_version=3; END IF;
 END IF;
 RETURN OLD;
END $$;
CREATE TRIGGER native_legacy_original_binding_guard BEFORE INSERT OR UPDATE OR DELETE
 ON app_private.native_legacy_original_binding_v3 FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_legacy_original_binding_v3();
CREATE TRIGGER native_legacy_retirement_witness_guard BEFORE INSERT OR UPDATE OR DELETE
 ON app_private.native_legacy_retirement_witness_v3 FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_legacy_retirement_witness_v3();
REVOKE ALL ON FUNCTION app_private.native_v3_legacy_retirement_provenance(uuid,uuid),
 app_private.guard_native_legacy_original_binding_v3(),
 app_private.guard_native_legacy_retirement_witness_v3(),
 app_private.native_v3_assert_legacy_format_retirement(uuid,uuid,text),
 app_private.retire_native_request_format_v3() FROM PUBLIC;
"""

PACK_FORMAT_SQL = r"""
CREATE TABLE app_private.native_pack_format_binding_v3 (
 pack_id uuid PRIMARY KEY,owner_user_id uuid NOT NULL,served_request_id uuid NOT NULL,
 request_hash_format_version integer NOT NULL CHECK(request_hash_format_version IN (2,3)),
 binding_sha256 bytea NOT NULL CHECK(octet_length(binding_sha256)=32),
 created_at timestamptz NOT NULL,content_expires_at timestamptz NOT NULL,
 attribution_expires_at timestamptz NOT NULL,
 CHECK(content_expires_at>created_at AND content_expires_at<=created_at+interval '7 days'
  AND content_expires_at<=attribution_expires_at)
);
CREATE TABLE app_private.native_pack_format_classification_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL CHECK(backend_pid>0),
 pack_id uuid NOT NULL,owner_user_id uuid NOT NULL,served_request_id uuid NOT NULL,
 request_hash_format_version integer NOT NULL CHECK(request_hash_format_version IN (2,3)),
 binding_sha256 bytea NOT NULL CHECK(octet_length(binding_sha256)=32),
 created_at timestamptz NOT NULL,content_expires_at timestamptz NOT NULL,
 attribution_expires_at timestamptz NOT NULL,
 PRIMARY KEY(transaction_id,backend_pid,pack_id)
);
CREATE TABLE app_private.native_ordinary_pack_delete_witness_v3 (
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL CHECK(backend_pid>0),
 pack_id uuid NOT NULL,owner_user_id uuid NOT NULL,
 attribution_contract_version integer NOT NULL CHECK(attribution_contract_version=1),
 PRIMARY KEY(transaction_id,backend_pid,pack_id)
);
REVOKE ALL ON app_private.native_pack_format_binding_v3,
 app_private.native_pack_format_classification_v3,
 app_private.native_ordinary_pack_delete_witness_v3 FROM PUBLIC;

CREATE FUNCTION app_private.native_v3_resolve_pack_format(p_owner uuid,p_pack uuid)
RETURNS smallint LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE current_binding ml.offline_pack_binding_receipt%ROWTYPE;
 typed app_private.native_pack_format_binding_v3%ROWTYPE; v_format smallint; v_owner uuid;
 cleanup record;
BEGIN
 IF p_pack IS NULL THEN
  RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 -- Both records contain only fixed-width scalars plus checked small enum text.
 SELECT b.* INTO current_binding FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=p_pack;
 SELECT t.* INTO typed FROM app_private.native_pack_format_binding_v3 t WHERE t.pack_id=p_pack;
 IF current_binding.pack_id IS NOT NULL THEN
  v_format:=app_private.native_v3_resolve_served_format(
   current_binding.owner_user_id,current_binding.recommendation_request_id);
  IF v_format NOT IN (2,3) OR current_binding.serving_contract_version<>2
   OR (p_owner IS NOT NULL AND p_owner<>current_binding.owner_user_id)
   OR (typed.pack_id IS NOT NULL AND (typed.owner_user_id<>current_binding.owner_user_id
    OR typed.served_request_id<>current_binding.recommendation_request_id
    OR typed.request_hash_format_version<>v_format OR typed.binding_sha256<>current_binding.binding_sha256
    OR typed.created_at<>current_binding.created_at
    OR typed.content_expires_at<>current_binding.content_expires_at
    OR typed.attribution_expires_at<>current_binding.attribution_expires_at)) THEN
   RAISE EXCEPTION 'NATIVE_V3_PACK_CROSS_FORMAT' USING ERRCODE='23514'; END IF;
  RETURN v_format;
 END IF;
 -- Only C's actual protected current-fullxid/PID typed witness survives the
 -- final privacy cleanup. It selects a type only after all actual content and
 -- both CONTENT/BINDING receipts have disappeared; no lifetime is invented.
 SELECT w.owner_user_id,w.served_request_id,w.request_hash_format_version,w.binding_sha256
  INTO cleanup FROM app_private.native_served_retirement_pack_cleanup_v3 w
  WHERE w.transaction_id=pg_current_xact_id_if_assigned() AND w.backend_pid=pg_backend_pid()
   AND w.pack_id=p_pack;
 IF FOUND THEN
  IF cleanup.request_hash_format_version IS NULL OR cleanup.request_hash_format_version NOT IN (2,3)
   OR (p_owner IS NOT NULL AND p_owner<>cleanup.owner_user_id)
   OR typed.pack_id IS NOT NULL
   OR EXISTS(SELECT 1 FROM ml.offline_recommendation_pack p WHERE p.offline_pack_id=p_pack)
   OR EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=p_pack)
   OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association a WHERE a.offline_pack_id=p_pack)
   OR EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt r WHERE r.pack_id=p_pack) THEN
   RAISE EXCEPTION 'NATIVE_V3_PACK_CLEANUP_INCOMPLETE' USING ERRCODE='23514'; END IF;
  RETURN cleanup.request_hash_format_version::smallint;
 END IF;
 IF typed.pack_id IS NOT NULL THEN
  IF (p_owner IS NOT NULL AND p_owner<>typed.owner_user_id) OR NOT EXISTS(
   SELECT 1 FROM ml.native_offline_pack_purge_receipt r WHERE r.pack_id=p_pack
    AND r.owner_user_id=typed.owner_user_id AND r.binding_sha256=typed.binding_sha256
    AND r.stage='BINDING') THEN
   RAISE EXCEPTION 'NATIVE_V3_PACK_RETIRED_PROVENANCE_REQUIRED' USING ERRCODE='23514'; END IF;
  RETURN typed.request_hash_format_version::smallint;
 END IF;
 IF EXISTS(SELECT 1 FROM ml.recommendation_attribution_association a
  WHERE a.offline_pack_id=p_pack) OR EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt r
  WHERE r.pack_id=p_pack) THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_RETIRED_PROVENANCE_REQUIRED' USING ERRCODE='23514'; END IF;
 SELECT p.user_id INTO v_owner FROM ml.offline_recommendation_pack p
  WHERE p.offline_pack_id=p_pack AND p.attribution_contract_version=1;
 IF v_owner IS NULL THEN SELECT w.owner_user_id INTO v_owner
  FROM app_private.native_ordinary_pack_delete_witness_v3 w
  WHERE w.transaction_id=pg_current_xact_id_if_assigned() AND w.backend_pid=pg_backend_pid()
   AND w.pack_id=p_pack AND w.attribution_contract_version=1; END IF;
 IF v_owner IS NOT NULL AND (p_owner IS NULL OR p_owner=v_owner) THEN RETURN 1; END IF;
 RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514';
END $$;

CREATE FUNCTION app_private.classify_native_pack_format_v3(p_owner uuid,p_pack uuid)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ml.offline_pack_binding_receipt%ROWTYPE; v_format smallint;
BEGIN
 SELECT x.* INTO b FROM ml.offline_pack_binding_receipt x
  WHERE x.pack_id=p_pack AND x.owner_user_id=p_owner;
 IF b.pack_id IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_CURRENT_PROVENANCE_REQUIRED' USING ERRCODE='23514'; END IF;
 v_format:=app_private.native_v3_resolve_served_format(p_owner,b.recommendation_request_id);
 IF v_format=2 THEN PERFORM app_private.assert_native_offline_pack_v2_v2_frozen(p_pack);
 ELSIF v_format=3 THEN PERFORM app_private.assert_native_offline_pack_v3(p_pack);
 ELSE RAISE EXCEPTION 'NATIVE_V3_PACK_CROSS_FORMAT' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM app_private.native_pack_format_binding_v3 t WHERE t.pack_id=p_pack) THEN
  PERFORM app_private.native_v3_resolve_pack_format(p_owner,p_pack); RETURN; END IF;
 -- This private context is created only AFTER the actual complete verifier.
 INSERT INTO app_private.native_pack_format_classification_v3 VALUES(
  pg_current_xact_id(),pg_backend_pid(),p_pack,p_owner,b.recommendation_request_id,v_format,
  b.binding_sha256,b.created_at,b.content_expires_at,b.attribution_expires_at);
 INSERT INTO app_private.native_pack_format_binding_v3 VALUES(
  p_pack,p_owner,b.recommendation_request_id,v_format,b.binding_sha256,
  b.created_at,b.content_expires_at,b.attribution_expires_at);
 DELETE FROM app_private.native_pack_format_classification_v3 c
  WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid() AND c.pack_id=p_pack;
END $$;

CREATE FUNCTION app_private.guard_native_pack_format_binding_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_SCHEMA<>'app_private' OR TG_TABLE_NAME NOT IN ('native_pack_format_binding_v3',
  'native_pack_format_classification_v3','native_ordinary_pack_delete_witness_v3') THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_FORMAT_TRIGGER' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='native_pack_format_classification_v3' AND TG_OP='DELETE' THEN
  IF OLD.transaction_id IS DISTINCT FROM pg_current_xact_id()
   OR OLD.backend_pid IS DISTINCT FROM pg_backend_pid() THEN
   RAISE EXCEPTION 'NATIVE_V3_PACK_FORMAT_CONTEXT' USING ERRCODE='23514'; END IF;
  RETURN OLD;
 END IF;
 IF TG_OP='DELETE' AND TG_TABLE_NAME IN (
  'native_pack_format_binding_v3','native_ordinary_pack_delete_witness_v3') THEN
  IF app_private.native_v3_retirement_cleanup_allowed(
   OLD.owner_user_id,OLD.pack_id,TG_TABLE_NAME) IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'NATIVE_V3_PACK_RETIREMENT_CLEANUP_REQUIRED' USING ERRCODE='23514'; END IF;
  RETURN OLD;
 END IF;
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_FORMAT_IMMUTABLE' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='native_ordinary_pack_delete_witness_v3' THEN
  IF NEW.transaction_id IS DISTINCT FROM pg_current_xact_id()
   OR NEW.backend_pid IS DISTINCT FROM pg_backend_pid() OR NOT EXISTS(
    SELECT 1 FROM ml.offline_recommendation_pack p WHERE p.offline_pack_id=NEW.pack_id
     AND p.user_id=NEW.owner_user_id AND p.attribution_contract_version=1)
   OR EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE b.pack_id=NEW.pack_id)
   OR EXISTS(SELECT 1 FROM app_private.native_pack_format_binding_v3 t WHERE t.pack_id=NEW.pack_id)
   OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association a WHERE a.offline_pack_id=NEW.pack_id)
   OR EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt r WHERE r.pack_id=NEW.pack_id) THEN
   RAISE EXCEPTION 'NATIVE_V3_PACK_ORDINARY_WITNESS' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='native_pack_format_classification_v3' THEN
  IF NEW.transaction_id IS DISTINCT FROM pg_current_xact_id()
   OR NEW.backend_pid IS DISTINCT FROM pg_backend_pid() THEN
   RAISE EXCEPTION 'NATIVE_V3_PACK_FORMAT_CONTEXT' USING ERRCODE='23514'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM app_private.native_pack_format_classification_v3 c
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
    AND c.pack_id=NEW.pack_id AND c.owner_user_id=NEW.owner_user_id
    AND c.served_request_id=NEW.served_request_id
    AND c.request_hash_format_version=NEW.request_hash_format_version
    AND c.binding_sha256=NEW.binding_sha256 AND c.created_at=NEW.created_at
    AND c.content_expires_at=NEW.content_expires_at
    AND c.attribution_expires_at=NEW.attribution_expires_at) THEN
   RAISE EXCEPTION 'NATIVE_V3_PACK_FORMAT_VERIFIED_CONTEXT' USING ERRCODE='23514'; END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b
  WHERE b.pack_id=NEW.pack_id AND b.owner_user_id=NEW.owner_user_id
   AND b.recommendation_request_id=NEW.served_request_id AND b.binding_sha256=NEW.binding_sha256
   AND b.created_at=NEW.created_at AND b.content_expires_at=NEW.content_expires_at
   AND b.attribution_expires_at=NEW.attribution_expires_at AND NEW.request_hash_format_version=
    app_private.native_v3_resolve_served_format(NEW.owner_user_id,NEW.served_request_id)) THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_CURRENT_PROVENANCE_REQUIRED' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION app_private.check_native_pack_classification_empty_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM app_private.native_pack_format_classification_v3 c
  WHERE c.transaction_id=NEW.transaction_id AND c.backend_pid=NEW.backend_pid) THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_CLASSIFICATION_LEAK' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION app_private.witness_native_ordinary_pack_delete_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_SCHEMA<>'ml' OR TG_TABLE_NAME<>'offline_recommendation_pack' OR TG_OP<>'DELETE' THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_ORDINARY_TRIGGER' USING ERRCODE='23514'; END IF;
 IF OLD.attribution_contract_version=1 THEN
  INSERT INTO app_private.native_ordinary_pack_delete_witness_v3 VALUES(
   pg_current_xact_id(),pg_backend_pid(),OLD.offline_pack_id,OLD.user_id,1);
 END IF;
 RETURN OLD;
END $$;
CREATE TRIGGER native_pack_format_binding_guard BEFORE INSERT OR UPDATE OR DELETE
 ON app_private.native_pack_format_binding_v3 FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_pack_format_binding_v3();
CREATE TRIGGER native_pack_format_classification_guard BEFORE INSERT OR UPDATE OR DELETE
 ON app_private.native_pack_format_classification_v3 FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_pack_format_binding_v3();
CREATE TRIGGER native_ordinary_pack_delete_witness_guard BEFORE INSERT OR UPDATE OR DELETE
 ON app_private.native_ordinary_pack_delete_witness_v3 FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_native_pack_format_binding_v3();
CREATE CONSTRAINT TRIGGER native_pack_classification_empty AFTER INSERT
 ON app_private.native_pack_format_classification_v3 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.check_native_pack_classification_empty_v3();
CREATE TRIGGER a_native_pack_ordinary_route_v3 BEFORE DELETE ON ml.offline_recommendation_pack
 FOR EACH ROW EXECUTE FUNCTION app_private.witness_native_ordinary_pack_delete_v3();
REVOKE ALL ON FUNCTION app_private.native_v3_resolve_pack_format(uuid,uuid),
 app_private.classify_native_pack_format_v3(uuid,uuid),
 app_private.guard_native_pack_format_binding_v3(),
 app_private.check_native_pack_classification_empty_v3(),
 app_private.witness_native_ordinary_pack_delete_v3() FROM PUBLIC;
"""

BACKFILL_SQL = r"""
CREATE FUNCTION app_private.native_v3_assert_dispatch_backfill() RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 -- This is the one-time pre-activation cutover census, never a runtime current
 -- grant check. Unexpected preexisting Source3 data refuses first installation.
 IF EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3)
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_v3)
  OR EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection_v3)
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f WHERE f.request_hash_format_version<>2)
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_format f WHERE f.original_capture_format_version<>2)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request r WHERE r.serving_contract_version=2
   AND NOT EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
    WHERE f.served_request_id=r.recommendation_request_id AND f.owner_user_id=r.user_id
     AND f.request_hash_format_version=2 AND f.created_transaction IS NULL
     AND f.created_at=r.created_at AND f.expires_at=r.created_at+interval '180 days'))
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f WHERE NOT EXISTS(
   SELECT 1 FROM ml.recommendation_request r WHERE r.recommendation_request_id=f.served_request_id
    AND r.user_id=f.owner_user_id AND r.serving_contract_version=2 AND f.created_transaction IS NULL
    AND r.created_at=f.created_at AND f.expires_at=r.created_at+interval '180 days'))
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_format f WHERE NOT EXISTS(
   SELECT 1 FROM app_private.native_legacy_original_binding_v3 b
    JOIN ml.native_atomic_capture_connection c ON c.original_request_id=b.original_request_id
     AND c.served_request_id=b.served_request_id AND c.owner_user_id=b.owner_user_id
    JOIN ml.recommendation_request r ON r.recommendation_request_id=b.original_request_id
     AND r.user_id=b.owner_user_id AND r.serving_contract_version=1
   WHERE b.original_request_id=f.original_request_id AND b.owner_user_id=f.owner_user_id
    AND b.original_request_sha256=r.request_sha256 AND b.capture_sha256=c.capture_sha256
    AND b.decision_sha256=c.decision_sha256 AND b.created_at=f.created_at AND b.expires_at=f.expires_at
    AND f.created_transaction IS NULL))
  OR EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection c WHERE NOT EXISTS(
   SELECT 1 FROM ml.native_original_capture_format f
    JOIN app_private.native_legacy_original_binding_v3 b ON b.original_request_id=f.original_request_id
     AND b.owner_user_id=f.owner_user_id AND b.served_request_id=c.served_request_id
   WHERE f.original_request_id=c.original_request_id AND f.owner_user_id=c.owner_user_id
    AND f.original_capture_format_version=2 AND b.capture_sha256=c.capture_sha256
    AND b.decision_sha256=c.decision_sha256))
  OR EXISTS(SELECT 1 FROM ml.offline_pack_binding_receipt b WHERE NOT EXISTS(
   SELECT 1 FROM app_private.native_pack_format_binding_v3 t WHERE t.pack_id=b.pack_id
    AND t.owner_user_id=b.owner_user_id AND t.served_request_id=b.recommendation_request_id
    AND t.request_hash_format_version=2 AND t.binding_sha256=b.binding_sha256
    AND t.created_at=b.created_at AND t.content_expires_at=b.content_expires_at
    AND t.attribution_expires_at=b.attribution_expires_at))
  OR EXISTS(SELECT 1 FROM app_private.native_legacy_format_classification_v3 c
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid())
  OR EXISTS(SELECT 1 FROM app_private.native_pack_format_classification_v3 c
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()) THEN
  RAISE EXCEPTION 'NATIVE_V3_DISPATCH_BACKFILL_INCOMPLETE' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.native_v3_backfill_legacy_formats_v3() RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row record; after_id uuid;
BEGIN
 IF EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3)
  OR EXISTS(SELECT 1 FROM ml.native_original_capture_v3) THEN
  RAISE EXCEPTION 'NATIVE_V3_DISPATCH_PREACTIVATION_REQUIRED' USING ERRCODE='23514'; END IF;
 -- Fixed keyset cursor, one scalar row at a time. Each classifier runs the
 -- actual complete frozen verifier; no caller supplies a range/format/marker.
 LOOP
  SELECT r.user_id,r.recommendation_request_id INTO row FROM ml.recommendation_request r
   WHERE r.serving_contract_version=2 AND (after_id IS NULL OR r.recommendation_request_id>after_id)
   ORDER BY r.recommendation_request_id LIMIT 1;
  EXIT WHEN NOT FOUND;
  -- Runtime classifiers preserve the old REPLAY_EXPIRED idempotent result for
  -- a prior immutable2 tag. The one-time cutover still revalidates its actual
  -- complete old graph instead of treating that existing tag as proof.
  IF EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   WHERE f.served_request_id=row.recommendation_request_id) THEN
   PERFORM app_private.assert_native_served_authority_v2_frozen(row.recommendation_request_id);
  END IF;
  PERFORM app_private.classify_native_legacy_served_v2(row.user_id,row.recommendation_request_id);
  after_id:=row.recommendation_request_id;
 END LOOP;
 after_id:=NULL;
 LOOP
  SELECT c.owner_user_id,c.original_request_id,c.served_request_id INTO row
   FROM ml.native_atomic_capture_connection c
   WHERE after_id IS NULL OR c.original_request_id>after_id ORDER BY c.original_request_id LIMIT 1;
  EXIT WHEN NOT FOUND;
  PERFORM app_private.classify_native_legacy_original_v2(
   row.owner_user_id,row.original_request_id,row.served_request_id);
  after_id:=row.original_request_id;
 END LOOP;
 after_id:=NULL;
 LOOP
  SELECT b.owner_user_id,b.pack_id INTO row FROM ml.offline_pack_binding_receipt b
   WHERE after_id IS NULL OR b.pack_id>after_id ORDER BY b.pack_id LIMIT 1;
  EXIT WHEN NOT FOUND;
  PERFORM app_private.classify_native_pack_format_v3(row.owner_user_id,row.pack_id);
  after_id:=row.pack_id;
 END LOOP;
 PERFORM app_private.native_v3_assert_dispatch_backfill();
END $$;
REVOKE ALL ON FUNCTION app_private.native_v3_assert_dispatch_backfill(),
 app_private.native_v3_backfill_legacy_formats_v3() FROM PUBLIC;
"""

# These fixed scalar sources are the authority for the request's role. An
# unbound row's serving_contract_version is never a native3 selector. Root
# original lookup is backed by the required unique original UUID index, including
# after independent source withdrawal/original-row purge. No whole row/JSON copy.
_GUARD_DECLARE = """DECLARE v_request uuid; v_owner uuid; v_original uuid; v_served uuid;
 v_request_role text; v_binding record; v_parent_owner uuid; v_version integer;
 v_context app_private.native_request_write_context_v3%ROWTYPE;
 v_baseline uuid; v_temporal uuid; v_created timestamptz;
"""
_GUARD_LOOKUP = """
 IF TG_TABLE_SCHEMA<>'ml' OR TG_TABLE_NAME NOT IN ('recommendation_request',
  'recommendation_item','recommendation_serving_decision','recommendation_served_item',
  'recommendation_request_replay_payload') OR TG_OP NOT IN ('INSERT','UPDATE','DELETE') THEN
  RAISE EXCEPTION 'NATIVE_V3_SHARED_TRIGGER' USING ERRCODE='23514'; END IF;
 v_request:=CASE WHEN TG_OP='DELETE' THEN OLD.recommendation_request_id
  ELSE NEW.recommendation_request_id END;
 IF v_request IS NULL THEN
  RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 -- This runs before either ordinary1's old early return or Source3 CREATE
 -- role selection, covering both new original and served request UUIDs.
 IF TG_TABLE_NAME='recommendation_request' AND TG_OP='INSERT' THEN
  IF app_private.native_v3_resolve_retired_served_format(NULL,NEW.recommendation_request_id)
   IS NOT NULL THEN
   RAISE EXCEPTION 'NATIVE_V3_RETIRED_REQUEST_REUSE' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_OP='UPDATE' AND (
  EXISTS(SELECT 1 FROM ml.native_original_capture_format f WHERE
   f.original_request_id IN (OLD.recommendation_request_id,NEW.recommendation_request_id)
    AND f.original_capture_format_version=3)
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f WHERE
   f.served_request_id IN (OLD.recommendation_request_id,NEW.recommendation_request_id)
    AND f.request_hash_format_version=3)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r WHERE
   r.original_request_id IN (OLD.recommendation_request_id,NEW.recommendation_request_id)
    OR r.served_request_id IN (OLD.recommendation_request_id,NEW.recommendation_request_id))
  OR EXISTS(SELECT 1 FROM app_private.native_request_write_context_v3 c WHERE
   c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid() AND
   (c.original_request_id IN (OLD.recommendation_request_id,NEW.recommendation_request_id)
    OR c.served_request_id IN (OLD.recommendation_request_id,NEW.recommendation_request_id)))
  OR EXISTS(SELECT 1 FROM app_private.native_request_deletion_witness_v3 w WHERE
   w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid() AND
   (w.original_request_id IN (OLD.recommendation_request_id,NEW.recommendation_request_id)
    OR w.served_request_id IN (OLD.recommendation_request_id,NEW.recommendation_request_id)))
 ) THEN RAISE EXCEPTION 'NATIVE_V3_SHARED_IMMUTABLE' USING ERRCODE='55000'; END IF;
 IF EXISTS(SELECT 1 FROM ml.native_original_capture_format f
   WHERE f.original_request_id=v_request AND f.original_capture_format_version NOT IN (2,3))
  OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
   WHERE f.served_request_id=v_request AND f.request_hash_format_version NOT IN (2,3))
  OR (EXISTS(SELECT 1 FROM ml.native_original_capture_format f
   WHERE f.original_request_id=v_request) AND EXISTS(
   SELECT 1 FROM ml.native_served_request_hash_format f WHERE f.served_request_id=v_request))
 THEN RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 FOR v_binding IN
  SELECT c.owner_user_id,c.original_request_id,c.served_request_id,
   CASE WHEN c.original_request_id=v_request THEN 'ORIGINAL' ELSE 'SERVED' END AS request_role
   FROM app_private.native_request_write_context_v3 c
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
    AND (c.original_request_id=v_request OR c.served_request_id=v_request)
  UNION ALL
  SELECT r.owner_user_id,r.original_request_id,r.served_request_id,
   CASE WHEN r.original_request_id=v_request THEN 'ORIGINAL' ELSE 'SERVED' END
   FROM ml.recommendation_request_root_v3 r
   WHERE r.original_request_id=v_request OR r.served_request_id=v_request
  UNION ALL
  SELECT f.owner_user_id,f.original_request_id,NULL::uuid,'ORIGINAL'
   FROM ml.native_original_capture_format f WHERE f.original_request_id=v_request
    AND f.original_capture_format_version=3
  UNION ALL
  SELECT f.owner_user_id,NULL::uuid,f.served_request_id,'SERVED'
   FROM ml.native_served_request_hash_format f WHERE f.served_request_id=v_request
    AND f.request_hash_format_version=3
  UNION ALL
  SELECT w.owner_user_id,w.original_request_id,w.served_request_id,
   CASE WHEN w.original_request_id=v_request THEN 'ORIGINAL' ELSE 'SERVED' END
   FROM app_private.native_request_deletion_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
    AND w.operation='HISTORY_PURGE' AND w.original_capture_format_version=3
    AND w.request_hash_format_version=3
    AND (w.original_request_id=v_request OR w.served_request_id=v_request)
 LOOP
  IF v_binding.owner_user_id IS NULL OR (v_owner IS NOT NULL
    AND v_owner IS DISTINCT FROM v_binding.owner_user_id)
   OR (v_request_role IS NOT NULL AND v_request_role IS DISTINCT FROM v_binding.request_role)
   OR (v_original IS NOT NULL AND v_binding.original_request_id IS NOT NULL
    AND v_original IS DISTINCT FROM v_binding.original_request_id)
   OR (v_served IS NOT NULL AND v_binding.served_request_id IS NOT NULL
    AND v_served IS DISTINCT FROM v_binding.served_request_id) THEN
   RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
  v_owner:=v_binding.owner_user_id; v_request_role:=v_binding.request_role;
  IF v_binding.original_request_id IS NOT NULL THEN
   v_original:=v_binding.original_request_id; END IF;
  IF v_binding.served_request_id IS NOT NULL THEN
   v_served:=v_binding.served_request_id; END IF;
 END LOOP;
"""
_GUARD_V3_CHECKS = """
 IF v_request_role IS NOT NULL THEN
  IF EXISTS(SELECT 1 FROM ml.native_original_capture_format f
    WHERE f.original_request_id=v_request AND f.original_capture_format_version<>3)
   OR EXISTS(SELECT 1 FROM ml.native_served_request_hash_format f
    WHERE f.served_request_id=v_request AND f.request_hash_format_version<>3) THEN
   RAISE EXCEPTION 'NATIVE_V3_CROSS_BOUND_REQUEST' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='recommendation_request' THEN
   v_parent_owner:=CASE WHEN TG_OP='DELETE' THEN OLD.user_id ELSE NEW.user_id END;
   v_version:=CASE WHEN TG_OP='DELETE' THEN OLD.serving_contract_version
    ELSE NEW.serving_contract_version END;
  ELSE SELECT r.user_id,r.serving_contract_version INTO v_parent_owner,v_version
   FROM ml.recommendation_request r WHERE r.recommendation_request_id=v_request;
  END IF;
  IF (v_parent_owner IS NOT NULL AND v_parent_owner IS DISTINCT FROM v_owner)
   OR (v_version IS NOT NULL AND ((v_request_role='ORIGINAL' AND v_version<>1)
    OR (v_request_role='SERVED' AND v_version<>2))) THEN
   RAISE EXCEPTION 'NATIVE_V3_SHARED_ROLE' USING ERRCODE='23514'; END IF;
  SELECT c.* INTO v_context FROM app_private.native_request_write_context_v3 c
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
    AND c.owner_user_id=v_owner AND c.original_request_id=v_original
    AND c.served_request_id=v_served;
  IF v_context.transaction_id IS NULL OR v_original IS NULL OR v_served IS NULL
   OR v_original=v_served THEN
   RAISE EXCEPTION 'NATIVE_V3_SHARED_CONTEXT_REQUIRED' USING ERRCODE='23514'; END IF;
  -- No old replay row belongs to Source3, even during history purge. Finding
  -- one is contradictory graph evidence, never a cleanup authorization.
  IF TG_TABLE_NAME='recommendation_request_replay_payload' OR TG_OP='UPDATE' THEN
   RAISE EXCEPTION 'NATIVE_V3_SHARED_COLUMN_SET' USING ERRCODE='55000'; END IF;
  IF TG_OP='DELETE' THEN
   -- LIVE_REMOVE deletes only the five live Source3 tables, never shared truth.
   IF v_context.operation<>'HISTORY_PURGE' OR NOT EXISTS(
    SELECT 1 FROM app_private.native_request_deletion_witness_v3 w
    WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
     AND w.owner_user_id=v_owner AND w.original_request_id=v_original
     AND w.served_request_id=v_served AND w.operation='HISTORY_PURGE'
     AND w.original_capture_format_version=3 AND w.request_hash_format_version=3) THEN
    RAISE EXCEPTION 'NATIVE_V3_SHARED_DELETE_CONTEXT' USING ERRCODE='55000'; END IF;
   IF v_request_role='ORIGINAL' AND TG_TABLE_NAME NOT IN
    ('recommendation_request','recommendation_item') THEN
    RAISE EXCEPTION 'NATIVE_V3_SHARED_COLUMN_SET' USING ERRCODE='55000'; END IF;
   RETURN OLD;
  END IF;
  IF TG_OP<>'INSERT' OR v_context.operation<>'CREATE' OR v_request_role<>'SERVED'
   OR current_setting('transaction_isolation')<>'repeatable read' THEN
   RAISE EXCEPTION 'NATIVE_V3_SHARED_CREATE_CONTEXT' USING ERRCODE='23514'; END IF;
  SELECT w.baseline_snapshot_id,w.temporal_snapshot_id,w.created_at
   INTO v_baseline,v_temporal,v_created
   FROM app_private.native_original_insert_witness_v3 w
   WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
    AND w.owner_user_id=v_owner AND w.table_name='recommendation_request'
    AND w.row_id=v_original AND w.serving_contract_version=1;
  IF v_baseline IS NULL OR v_temporal IS NULL OR v_created IS NULL THEN
   RAISE EXCEPTION 'NATIVE_V3_SHARED_ORIGIN_WITNESS' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='recommendation_request' THEN
   IF NEW.user_id IS DISTINCT FROM v_owner OR NEW.serving_contract_version IS DISTINCT FROM 2
    OR NEW.request_schema_version IS DISTINCT FROM 2
    OR NEW.request_canonicalization_version IS DISTINCT FROM 1
    OR NEW.created_at IS DISTINCT FROM v_created OR NEW.shadow IS DISTINCT FROM false
    OR NEW.surface IS NULL OR NEW.surface NOT IN ('recommendations','home','offline_pack')
    OR NEW.recommendation_input_snapshot_id IS DISTINCT FROM v_baseline
    OR NEW.recommendation_temporal_snapshot_id IS DISTINCT FROM v_temporal
    OR NEW.request_document IS NOT NULL OR NEW.sona_shadow_document IS NOT NULL
    OR NEW.sona_shadow_status IS NOT NULL OR NEW.sona_shadow_reason IS NOT NULL
    OR NEW.sona_shadow_pipeline_key IS NOT NULL OR NEW.sona_shadow_pipeline_version IS NOT NULL
    OR NEW.sona_shadow_pipeline_manifest_sha256 IS NOT NULL OR NEW.sona_tokenizer_sha256 IS NOT NULL
    OR NEW.sona_model_manifest_sha256 IS NOT NULL OR NEW.sona_request_sha256 IS NOT NULL
    OR NEW.sona_output_sha256 IS NOT NULL OR NEW.sona_shadow_evidence_sha256 IS NOT NULL
    OR NEW.request_features IS NULL OR pg_column_compression(NEW.request_features) IS NOT NULL
    OR pg_column_size(NEW.request_features)>64 THEN
    RAISE EXCEPTION 'NATIVE_V3_SHARED_COLUMN_SET' USING ERRCODE='23514'; END IF;
   IF NEW.request_features IS DISTINCT FROM '{}'::jsonb
    OR octet_length(NEW.request_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.input_snapshot_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.policy_snapshot_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.pipeline_manifest_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.temporal_snapshot_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.adaptive_feature_policy_sha256) IS DISTINCT FROM 32
    OR NEW.availability_snapshot_ref IS NULL
    OR octet_length(NEW.availability_snapshot_ref)<>64
    OR NEW.availability_snapshot_ref!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'NATIVE_V3_SHARED_COLUMN_SET' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='recommendation_item' THEN
   IF NEW.rank NOT BETWEEN 1 AND 100 OR NEW.score_units_e8 IS NULL
    OR NEW.score IS DISTINCT FROM NEW.score_units_e8::numeric/100000000
    OR octet_length(NEW.baseline_item_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.candidate_sources_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.availability_snapshot_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.contributions_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.reason_codes_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.item_provenance_sha256) IS DISTINCT FROM 32 THEN
    RAISE EXCEPTION 'NATIVE_V3_SHARED_COLUMN_SET' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='recommendation_serving_decision' THEN
   IF NEW.state IS DISTINCT FROM 'COMMITTED_P11' OR NEW.serving_provenance IS DISTINCT FROM 'P11'
    OR NEW.inference_evidence_sha256 IS NOT NULL OR NEW.fallback_reason IS NOT NULL
    OR NEW.p11_item_count NOT BETWEEN 0 AND 100
    OR NEW.served_item_count IS DISTINCT FROM NEW.p11_item_count
    OR NEW.p11_ranking_sha256 IS DISTINCT FROM NEW.p11_items_sha256
    OR octet_length(NEW.p11_items_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.served_items_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.decision_sha256) IS DISTINCT FROM 32
    OR NEW.served_truth_expires_at IS DISTINCT FROM v_created+interval '15552000 seconds' THEN
    RAISE EXCEPTION 'NATIVE_V3_SHARED_COLUMN_SET' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='recommendation_served_item' THEN
   IF NEW.source_rank NOT BETWEEN 1 AND 100
    OR NEW.original_p11_rank IS DISTINCT FROM NEW.source_rank
    OR NEW.score_kind IS DISTINCT FROM 'heuristic' OR NEW.attribution_source IS DISTINCT FROM 'P11'
    OR NEW.origin IS DISTINCT FROM 'P11' OR octet_length(NEW.reasons_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.contributions_sha256) IS DISTINCT FROM 32
    OR octet_length(NEW.served_item_sha256) IS DISTINCT FROM 32
    OR NOT EXISTS(SELECT 1 FROM ml.recommendation_item i
     WHERE i.recommendation_request_id=v_served AND i.rank=NEW.source_rank
      AND i.recording_id=NEW.recording_id AND i.score_units_e8=NEW.score_units_e8) THEN
    RAISE EXCEPTION 'NATIVE_V3_SHARED_COLUMN_SET' USING ERRCODE='23514'; END IF;
  ELSE RAISE EXCEPTION 'NATIVE_V3_SHARED_COLUMN_SET' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
"""


def _nested_body(body: str) -> str:
    # PostgreSQL permits the outer function block's final END without a
    # semicolon. Once embedded as a nested block, that delimiter is mandatory.
    # Keep every byte of the pinned body and append only this delimiter.
    if re.search(r"\bEND\s*;\s*\Z", body, re.IGNORECASE):
        return body
    if re.search(r"\bEND\s*\Z", body, re.IGNORECASE):
        return body + ";"
    raise RuntimeError("native_v3_dispatch_prior_block_end_mismatch")


def guard_sql(definition: str) -> tuple[str, str]:
    """Embed the pinned trigger body unchanged; trigger functions cannot be called."""
    body = re.search(r"\bAS\s+(\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$)([\s\S]*?)\1", definition)
    if body is None or "LANGUAGE plpgsql" not in definition or "$dispatch_guard$" in definition:
        raise RuntimeError("native_v3_dispatch_prior_trigger_body_mismatch")
    old_body = _nested_body(body[2])
    prologue = _GUARD_DECLARE + "BEGIN\n" + _GUARD_LOOKUP + _GUARD_V3_CHECKS
    declaration = (
        "RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog "
        "AS $dispatch_guard$\n"
    )
    dispatcher = (
        "CREATE FUNCTION "
        + GUARD_DISPATCH_SIGNATURE
        + "\n"
        + declaration
        + prologue
        + " <<native_v2_frozen_body>>\n"
        + old_body
        + "\nEND;\n$dispatch_guard$;"
    )
    shared_guard = (
        "CREATE FUNCTION app_private.guard_native_shared_truth_v3()\n"
        + declaration
        + prologue
        + " RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;\nEND;\n$dispatch_guard$;"
    )
    return dispatcher, shared_guard


def format_guard_sql(definition: str) -> str:
    """Only format2 tags gain a protected classifier branch; all3 stays exact."""
    body = re.search(r"\bAS\s+(\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$)([\s\S]*?)\1", definition)
    if body is None or "LANGUAGE plpgsql" not in definition or "$format_guard$" in definition:
        raise RuntimeError("native_v3_dispatch_prior_trigger_body_mismatch")
    return (
        r"""
CREATE FUNCTION app_private.guard_native_format_write_dispatch_v3() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $format_guard$
DECLARE v_owner uuid; v_request uuid; v_role text; v_version integer;
BEGIN
 IF TG_TABLE_SCHEMA<>'ml' OR TG_TABLE_NAME NOT IN
  ('native_original_capture_format','native_served_request_hash_format') THEN
  RAISE EXCEPTION 'NATIVE_V3_FORMAT_TRIGGER' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' THEN
  RAISE EXCEPTION 'NATIVE_V3_ROW_IMMUTABLE' USING ERRCODE='55000'; END IF;
 IF TG_OP NOT IN ('INSERT','DELETE') THEN
  RAISE EXCEPTION 'NATIVE_V3_FORMAT_TRIGGER' USING ERRCODE='23514'; END IF;
 v_owner:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_user_id ELSE NEW.owner_user_id END;
 IF TG_TABLE_NAME='native_original_capture_format' THEN
  v_request:=CASE WHEN TG_OP='DELETE' THEN OLD.original_request_id ELSE NEW.original_request_id END;
  v_version:=CASE WHEN TG_OP='DELETE' THEN OLD.original_capture_format_version
   ELSE NEW.original_capture_format_version END; v_role:='ORIGINAL';
 ELSE
  v_request:=CASE WHEN TG_OP='DELETE' THEN OLD.served_request_id ELSE NEW.served_request_id END;
  v_version:=CASE WHEN TG_OP='DELETE' THEN OLD.request_hash_format_version
   ELSE NEW.request_hash_format_version END; v_role:='SERVED';
 END IF;
 IF v_version=2 THEN
  PERFORM app_private.native_v3_assert_no_source3_roles(v_owner,v_request);
  IF TG_OP='DELETE' THEN
   -- Mandatory fixed lifecycle sibling proves actual old PURGE/privacy context
   -- and content-bound receipt. Its absence holds the entire cutover install.
   PERFORM app_private.native_v3_assert_legacy_format_retirement(v_owner,v_request,v_role);
   RETURN OLD;
  END IF;
  IF NEW.created_transaction IS NOT NULL OR NOT EXISTS(
   SELECT 1 FROM app_private.native_legacy_format_classification_v3 c
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
    AND c.owner_user_id=v_owner AND c.request_id=v_request AND c.request_role=v_role
    AND c.phase='VERIFIED' AND c.created_at=NEW.created_at AND c.expires_at=NEW.expires_at) THEN
   RAISE EXCEPTION 'NATIVE_V3_LEGACY_CLASSIFICATION_REQUIRED' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 ELSIF v_version IS DISTINCT FROM 3 THEN
  RAISE EXCEPTION 'NATIVE_REQUEST_FORMAT_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 <<native_v3_frozen_body>>
"""
        + _nested_body(body[2])
        + "\nEND;\n$format_guard$;"
    )


def format_trigger_sql() -> tuple[str, ...]:
    """Bind only the protected format guard before legacy classification."""
    return tuple(
        "CREATE OR REPLACE TRIGGER z_native_v3_typed_write BEFORE INSERT OR UPDATE OR DELETE "
        f"ON ml.{table} FOR EACH ROW EXECUTE FUNCTION "
        "app_private.guard_native_format_write_dispatch_v3();"
        for table in ("native_original_capture_format", "native_served_request_hash_format")
    )


def trigger_sql() -> tuple[str, ...]:
    statements = [
        # Immediate FK: the parent exists in AFTER INSERT. This fixed helper only
        # binds2 under the old private CREATE context, never by JSON decoding.
        "CREATE TRIGGER b_native_served_format_bind_v3 AFTER INSERT ON ml.recommendation_request "
        "FOR EACH ROW EXECUTE FUNCTION app_private.native_v3_bind_legacy_served_insert();"
    ]
    statements.append(
        # Immediate RESTRICT FKs cannot be bypassed or deferred. Retire binding
        # only on actual parent DELETE, after bounded child cleanup reached it.
        "CREATE TRIGGER b_native_request_format_retire_v3 BEFORE DELETE "
        "ON ml.recommendation_request "
        "FOR EACH ROW EXECUTE FUNCTION app_private.retire_native_request_format_v3();"
    )
    for table in ("recommendation_item", "recommendation_served_item"):
        statements.append(
            f"CREATE TRIGGER a_native_item_insert_count_v3 AFTER INSERT ON ml.{table} "
            "FOR EACH ROW EXECUTE FUNCTION app_private.count_native_item_insert_v3();"
        )
    for table in SHARED_TABLES:
        # Replace the old trigger binding, preserving its old installed body as
        # an executable inner legacy block. A separate BEFORE fence also protects
        # ordinary original rows after source withdrawal. No old context arms3.
        statements.extend(
            (
                "CREATE OR REPLACE TRIGGER z_native_served_immutable "
                f"BEFORE INSERT OR UPDATE OR DELETE ON ml.{table} FOR EACH ROW "
                "EXECUTE FUNCTION app_private.protect_native_served_authority_dispatch_v3();",
                "CREATE TRIGGER a_native_shared_truth_v3 BEFORE INSERT OR UPDATE OR DELETE "
                f"ON ml.{table} "
                "FOR EACH ROW EXECUTE FUNCTION app_private.guard_native_shared_truth_v3();",
                "CREATE CONSTRAINT TRIGGER z_native_shared_truth_v3 "
                "AFTER INSERT OR UPDATE OR DELETE "
                f"ON ml.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION app_private.check_native_shared_truth_v3();",
            )
        )
    for table in SOURCE_TABLES:
        statements.extend(
            (
                f"CREATE TRIGGER a_native_source_remove_v3 BEFORE DELETE ON ml.{table} "
                "FOR EACH ROW EXECUTE FUNCTION app_private.guard_native_source_removal_v3();",
                "CREATE CONSTRAINT TRIGGER z_native_source_lifecycle_v3 "
                "AFTER INSERT OR UPDATE OR DELETE "
                f"ON ml.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION app_private.check_native_source_lifecycle_v3();",
            )
        )
    for table in ("native_original_capture_v3", "native_capture_source_event_v3"):
        # The old export trigger reads OLD.user_id; source3 has owner_user_id.
        statements.append(
            f"CREATE TRIGGER native_export_source3_negative BEFORE DELETE ON ml.{table} "
            "FOR EACH ROW EXECUTE FUNCTION app_private.invalidate_native_export_source_v3();"
        )
    # Format/root reciprocal checks themselves are the eight-new-table context
    # module's responsibility; they are required before this stage can install.
    return tuple(statements)


def freeze_definition(route: _Route, definition: str) -> str:
    """Change only the installed declaration name; preserve the full old body."""
    prefix = re.compile(
        r"\ACREATE OR REPLACE FUNCTION app_private\." + re.escape(route.name) + r"\("
    )
    if prefix.search(definition) is None:
        raise RuntimeError("native_v3_dispatch_prior_header_mismatch")
    return prefix.sub(
        "CREATE OR REPLACE FUNCTION app_private." + route.frozen_name + "(", definition, 1
    )


def wrapper_sql(route: _Route) -> str:
    properties = (
        "STABLE SECURITY INVOKER"
        if route.name in {"native_served_decision_digest", "native_pack_payload_v2"}
        else "SECURITY DEFINER"
    )
    return (
        f"CREATE OR REPLACE FUNCTION app_private.{route.name}({route.arguments})\n"
        f"RETURNS {route.result} LANGUAGE plpgsql {properties} "
        "SET search_path=pg_catalog AS $dispatch$\n" + route.body + "\n$dispatch$;"
    )


@dataclass(frozen=True, slots=True)
class PreparedDispatch:
    clones: tuple[str, ...]
    private: tuple[str, ...]
    backfill: tuple[str, ...]
    cutover: tuple[str, ...]

    @property
    def statements(self) -> tuple[str, ...]:
        return self.clones + self.private + self.backfill + self.cutover


def prepare_phases(
    definitions: Mapping[str, str], expected_sha256: Mapping[str, str]
) -> PreparedDispatch:
    """Prepare exact fixed phases without executing or importing runtime models."""
    required = {route.signature for route in FROZEN_ROUTES}
    if set(definitions) != required or set(expected_sha256) != required:
        raise RuntimeError("native_v3_dispatch_prior_inventory_mismatch")
    cloned: list[str] = []
    for route in FROZEN_ROUTES:
        definition = definitions[route.signature]
        if (
            hashlib.sha256(definition.encode("utf-8")).hexdigest()
            != expected_sha256[route.signature]
        ):
            raise RuntimeError("native_v3_dispatch_prior_digest_mismatch")
        cloned.append(freeze_definition(route, definition))
    # Revoke each new cloned function before replacing any existing OID. Existing
    # wrapper ACLs survive CREATE OR REPLACE. install rejects non-owner direct
    # grants inherited from function default ACLs before replacing any wrapper.
    statements: list[str] = []
    for route, clone in zip(FROZEN_ROUTES, cloned, strict=True):
        statements.extend(
            (
                clone,
                f"REVOKE ALL ON FUNCTION app_private.{route.frozen_name}({route.types}) "
                "FROM PUBLIC;",
            )
        )
    private = [
        RESOLVER_SQL,
        LEGACY_CLASSIFICATION_SQL,
        LEGACY_RETIREMENT_SQL,
        PACK_FORMAT_SQL,
        BACKFILL_SQL,
        *guard_sql(definitions[GUARD_ROUTE.signature]),
        format_guard_sql(definitions[FORMAT_GUARD_ROUTE.signature]),
    ]
    private.extend(
        "REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC;"
        for signature in (
            GUARD_DISPATCH_SIGNATURE,
            "app_private.guard_native_shared_truth_v3()",
            "app_private.guard_native_format_write_dispatch_v3()",
        )
    )
    private.extend(format_trigger_sql())
    # All old generic functions remain installed during fixed classification.
    # Their complete verifiers can therefore validate genuine untagged legacy2
    # without passing through the strict post-cutover format resolver.
    return PreparedDispatch(
        tuple(statements),
        tuple(private),
        ("SELECT app_private.native_v3_backfill_legacy_formats_v3()",),
        tuple(wrapper_sql(route) for route in ROUTES) + trigger_sql(),
    )


def prepare(definitions: Mapping[str, str], expected_sha256: Mapping[str, str]) -> tuple[str, ...]:
    """Pure all-or-nothing preparation; root supplies exact installed pins."""
    return prepare_phases(definitions, expected_sha256).statements


def shared_trigger_inventory_sql(function: str) -> str:
    """Exact old/new binding proof; a resolver's presence alone is insufficient."""
    if function not in {"app_private.protect_native_served_authority()", GUARD_DISPATCH_SIGNATURE}:
        raise ValueError("native_v3_dispatch_fixed_guard_required")
    tables = ",".join("('" + name + "')" for name in SHARED_TABLES)
    return (
        "SELECT count(*)=5 AND bool_and(t.tgfoid=to_regprocedure('" + function + "') "
        "AND t.tgtype=31 AND t.tgenabled='O' AND NOT t.tgisinternal) "
        "FROM (VALUES " + tables + ") AS wanted(name) "
        "JOIN pg_namespace n ON n.nspname='ml' JOIN pg_class c "
        "ON c.relnamespace=n.oid AND c.relname=wanted.name "
        "JOIN pg_trigger t ON t.tgrelid=c.oid AND t.tgname='z_native_served_immutable'"
    )


def format_trigger_inventory_sql(function: str) -> str:
    if function not in {
        FORMAT_GUARD_ROUTE.signature,
        "app_private.guard_native_format_write_dispatch_v3()",
    }:
        raise ValueError("native_v3_dispatch_fixed_guard_required")
    return (
        "SELECT count(*)=2 AND bool_and(t.tgfoid=to_regprocedure('" + function + "') "
        "AND t.tgtype=31 AND t.tgenabled='O' AND NOT t.tgisinternal) "
        "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='ml' "
        "AND c.relname IN ('native_original_capture_format','native_served_request_hash_format') "
        "AND t.tgname='z_native_v3_typed_write'"
    )


def _abort_after_ddl(connection: Connection) -> None:
    # A Python exception alone can be caught and followed by COMMIT. Poison the
    # migration SQL transaction after a failed post-DDL privilege/catalog proof.
    connection.exec_driver_sql(
        "DO $$ BEGIN RAISE EXCEPTION 'NATIVE_V3_DISPATCH_CHECK_FAILED' "
        "USING ERRCODE='55000'; END $$;"
    )


ROOT_ORIGINAL_INDEX_SQL = (
    "SELECT EXISTS(SELECT 1 FROM pg_index i JOIN pg_attribute a "
    "ON a.attrelid=i.indrelid AND a.attname='original_request_id' AND NOT a.attisdropped "
    "WHERE i.indexrelid=to_regclass('ml.native_root_v3_original_unique') "
    "AND i.indrelid=to_regclass('ml.recommendation_request_root_v3') "
    "AND i.indisunique AND i.indisvalid AND i.indisready AND i.indimmediate "
    "AND i.indnkeyatts=1 AND i.indnatts=1 AND i.indkey[0]=a.attnum "
    "AND i.indpred IS NULL AND i.indexprs IS NULL)"
)

PACK_CLEANUP_TABLE_SQL = """
SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='app_private' AND c.relname='native_served_retirement_pack_cleanup_v3'
  AND c.relkind='r' AND (SELECT count(*) FROM pg_attribute a
   WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped)=7
  AND NOT EXISTS(SELECT 1 FROM (VALUES
   ('transaction_id','xid8'),('backend_pid','integer'),('owner_user_id','uuid'),
   ('pack_id','uuid'),('served_request_id','uuid'),('request_hash_format_version','integer'),
   ('binding_sha256','bytea')) AS wanted(name,typ)
   LEFT JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname=wanted.name
    AND a.attnum>0 AND NOT a.attisdropped
   WHERE a.attnum IS NULL OR NOT a.attnotnull OR a.atttypid<>to_regtype(wanted.typ))
  AND EXISTS(SELECT 1 FROM pg_constraint p WHERE p.conrelid=c.oid AND p.contype='p'
   AND NOT p.condeferrable AND NOT p.condeferred
   AND p.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='transaction_id'),
    (SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='backend_pid'),
    (SELECT attnum FROM pg_attribute WHERE attrelid=c.oid AND attname='pack_id')]::smallint[])
  AND EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
   WHERE t.tgrelid=c.oid AND t.tgname='native_retirement_pack_cleanup_v3_guard'
    AND t.tgfoid=to_regprocedure('app_private.guard_native_retirement_pack_cleanup_v3()')
    AND t.tgtype=31 AND t.tgenabled='O' AND NOT t.tgisinternal
    AND p.prosecdef AND p.proowner=c.relowner
    AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
     WHERE a.grantee<>p.proowner AND a.privilege_type='EXECUTE'))
  AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
   WHERE a.grantee<>c.relowner))
"""

CUTOVER_LOCK_TABLES = (
    "account.device",
    "account.server_instance",
    "account.training_consent",
    "account.user_account",
    "app_private.native_atomic_capture_context",
    "app_private.native_atomic_capture_insert_witness",
    "app_private.native_pack_context",
    "app_private.native_served_authority_context",
    "app_private.native_served_authority_purge_receipt",
    "app_private.privacy_purge_context",
    "app_private.privacy_purge_entity",
    "ml.native_atomic_capture_connection",
    "ml.native_offline_pack_purge_receipt",
    "ml.offline_pack_binding_receipt",
    "ml.offline_recommendation_pack",
    "ml.recommendation_attribution_association",
    "ml.recommendation_attribution_expiry_tombstone",
    "ml.recommendation_input_snapshot",
    "ml.recommendation_pipeline_version",
    "ml.sona_capture_bundle",
    "ml.sona_capture_lineage_cursor",
    "ml.sona_capture_replay_payload",
    "ml.sona_capture_source_event",
    "ml.sona_event_erasure_receipt",
    "sync.device_erasure_floor",
    "sync.device_erasure_invalidated_epoch",
    "sync.device_event_inbox",
    "sync.device_sync_cursor",
    "sync.owner_erasure_floor",
    "sync.sync_event",
    *("ml." + table for table in SHARED_TABLES),
    *("ml." + table for table in SOURCE_TABLES),
    "ml.recommendation_request_root_v3",
    "ml.native_original_capture_format",
    "ml.native_served_request_hash_format",
    "app_private.native_original_context_v3",
    "app_private.native_original_insert_witness_v3",
    "app_private.native_request_write_context_v3",
    "app_private.native_request_deletion_witness_v3",
)


def install(connection: Connection, expected_sha256: Mapping[str, str]) -> None:
    """Cut over only after exact graph, predecessor body and catalog checks."""
    from sqlalchemy import text

    if getattr(connection.dialect, "name", None) != "postgresql" or not connection.in_transaction():
        raise RuntimeError("native_v3_dispatch_migration_transaction_required")
    for signature in REQUIRED_FUNCTIONS:
        if (
            connection.scalar(
                text("SELECT to_regprocedure(:signature) IS NOT NULL"), {"signature": signature}
            )
            is not True
        ):
            raise RuntimeError("native_v3_dispatch_full_graph_predecessor_missing")
    definitions: dict[str, str] = {}
    for route in FROZEN_ROUTES:
        if (
            connection.scalar(
                text("SELECT to_regprocedure(:signature) IS NOT NULL"),
                {"signature": f"app_private.{route.frozen_name}({route.types})"},
            )
            is not False
        ):
            raise RuntimeError("native_v3_dispatch_frozen_name_already_present")
        definition = connection.scalar(
            text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
            {"signature": route.signature},
        )
        if type(definition) is not str:
            raise RuntimeError("native_v3_dispatch_prior_definition_missing")
        definitions[route.signature] = definition
    phases = prepare_phases(definitions, expected_sha256)
    if connection.scalar(text(ROOT_ORIGINAL_INDEX_SQL)) is not True:
        raise RuntimeError("native_v3_dispatch_original_unique_index_required")
    if connection.scalar(text(PACK_CLEANUP_TABLE_SQL)) is not True:
        raise RuntimeError("native_v3_dispatch_pack_cleanup_table_required")
    if connection.scalar(text(shared_trigger_inventory_sql(GUARD_ROUTE.signature))) is not True:
        raise RuntimeError("native_v3_dispatch_prior_trigger_inventory_mismatch")
    if (
        connection.scalar(text(format_trigger_inventory_sql(FORMAT_GUARD_ROUTE.signature)))
        is not True
    ):
        raise RuntimeError("native_v3_dispatch_prior_format_trigger_inventory_mismatch")
    connection.exec_driver_sql(
        "LOCK TABLE " + ",".join(CUTOVER_LOCK_TABLES) + " IN ACCESS EXCLUSIVE MODE"
    )
    # All old generic routes are still installed. Freeze them before making the
    # private classifiers or their protected format2 guard executable.
    for statement in phases.clones:
        connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
    for route in FROZEN_ROUTES:
        private = connection.scalar(
            text(
                "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
                "aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE p.oid=to_regprocedure(:signature) AND a.grantee<>p.proowner "
                "AND a.privilege_type='EXECUTE')"
            ),
            {"signature": f"app_private.{route.frozen_name}({route.types})"},
        )
        if private is not True:
            _abort_after_ddl(connection)
            raise RuntimeError("native_v3_dispatch_frozen_branch_not_private")
    for statement in phases.private:
        connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
    for signature in NEW_PRIVATE_FUNCTIONS:
        private = connection.scalar(
            text(
                "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
                "aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE p.oid=to_regprocedure(:signature) AND a.grantee<>p.proowner "
                "AND a.privilege_type='EXECUTE')"
            ),
            {"signature": signature},
        )
        if private is not True:
            _abort_after_ddl(connection)
            raise RuntimeError("native_v3_dispatch_new_guard_not_private")
    context_private = connection.scalar(
        text(
            "SELECT NOT EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL "
            "aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a "
            "WHERE c.oid IN (to_regclass('app_private.native_legacy_format_classification_v3'),"
            "to_regclass('app_private.native_legacy_original_binding_v3'),"
            "to_regclass('app_private.native_legacy_retirement_witness_v3'),"
            "to_regclass('app_private.native_pack_format_binding_v3'),"
            "to_regclass('app_private.native_pack_format_classification_v3'),"
            "to_regclass('app_private.native_ordinary_pack_delete_witness_v3')) "
            "AND a.grantee<>c.relowner)"
        )
    )
    if context_private is not True:
        _abort_after_ddl(connection)
        raise RuntimeError("native_v3_dispatch_classification_context_not_private")
    if (
        connection.scalar(
            text(
                format_trigger_inventory_sql("app_private.guard_native_format_write_dispatch_v3()")
            )
        )
        is not True
    ):
        _abort_after_ddl(connection)
        raise RuntimeError("native_v3_dispatch_result_format_trigger_inventory_mismatch")
    # Genuine complete old rows are classified only after exact frozen verifiers,
    # private provenance tables and the restrictive format2 guard exist. No
    # generic route or shared/common guard has changed yet. Backfill failure is
    # a SQL exception, aborting all preparatory DDL with the transaction.
    for statement in phases.backfill:
        connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
    for statement in phases.cutover:
        connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
    if connection.scalar(text(shared_trigger_inventory_sql(GUARD_DISPATCH_SIGNATURE))) is not True:
        _abort_after_ddl(connection)
        raise RuntimeError("native_v3_dispatch_result_trigger_inventory_mismatch")
    if (
        connection.scalar(
            text(
                format_trigger_inventory_sql("app_private.guard_native_format_write_dispatch_v3()")
            )
        )
        is not True
    ):
        _abort_after_ddl(connection)
        raise RuntimeError("native_v3_dispatch_result_format_trigger_inventory_mismatch")


# No downgrade is supplied: restoring old format-blind functions while any V3
# evidence remains would bypass authority. Exact coordinated teardown is future
# coherent graph work, never DROP CASCADE or structural-foundation removal.
