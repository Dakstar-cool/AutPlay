"""Private final owner-purge phase, after real deferred constraint validation."""

from __future__ import annotations

from hashlib import sha256

from sqlalchemy import Connection, text

PURGE_SIGNATURE = "account.purge_account(uuid,uuid)"

FINAL_SCOPE_SQL = r"""
CREATE FUNCTION app_private.native_v3_privacy_scope(p_owner uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 -- Only account.purge_account creates this protected exact-owner context,
 -- after taking its server, owner and publication locks. This exception is
 -- negative authority only; ordinary reads/creation retain their RR contract.
 SELECT current_setting('transaction_isolation')='read committed'
  AND EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=p_owner)
  AND app_private.privacy_owner_allowed(p_owner)
$$;
REVOKE ALL ON FUNCTION app_private.native_v3_privacy_scope(uuid) FROM PUBLIC;
CREATE TABLE app_private.native_owner_purge_final_v3 (
 transaction_id xid8 NOT NULL, backend_pid integer NOT NULL CHECK(backend_pid>0),
 owner_user_id uuid NOT NULL, privacy_request_id uuid NOT NULL,
 PRIMARY KEY(transaction_id,backend_pid,owner_user_id)
);
REVOKE ALL ON app_private.native_owner_purge_final_v3 FROM PUBLIC;
CREATE FUNCTION app_private.native_v3_owner_final_phase(p_owner uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app_private.native_owner_purge_final_v3 f
  JOIN app_private.privacy_purge_context c ON c.transaction_id=f.transaction_id
   AND c.owner_id=f.owner_user_id AND c.request_id=f.privacy_request_id
  WHERE f.transaction_id=pg_current_xact_id() AND f.backend_pid=pg_backend_pid()
   AND f.owner_user_id=p_owner)
  AND app_private.privacy_owner_allowed(p_owner)
  AND NOT EXISTS(SELECT 1 FROM account.user_account a WHERE a.user_id=p_owner)
  AND NOT EXISTS(SELECT 1 FROM ml.recommendation_request q WHERE q.user_id=p_owner)
  AND NOT EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r WHERE r.owner_user_id=p_owner)
$$;
REVOKE ALL ON FUNCTION app_private.native_v3_owner_final_phase(uuid) FROM PUBLIC;
"""

FINALIZE_SQL = r"""
CREATE FUNCTION app_private.finalize_native_owner_purge_v3(target uuid) RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE step text; request_id uuid; affected bigint; total bigint:=0;
BEGIN
 SELECT c.request_id INTO request_id FROM app_private.privacy_purge_context c
 WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=target;
 IF request_id IS NULL OR NOT app_private.privacy_owner_allowed(target)
  OR NOT app_private.native_v3_privacy_scope(target)
  OR EXISTS(SELECT 1 FROM account.user_account a WHERE a.user_id=target)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request q WHERE q.user_id=target)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r WHERE r.owner_user_id=target) THEN
  RAISE EXCEPTION 'NATIVE_V3_OWNER_FINAL_SCOPE_REQUIRED' USING ERRCODE='55000'; END IF;
 -- This function itself drains actual constraints. A caller cannot replace
 -- validation with a flag, an early SET CONSTRAINTS, or an old witness.
 SELECT string_agg(DISTINCT format('%I.%I',n.nspname,c.conname),',') INTO step
 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
 WHERE c.condeferrable AND c.conname<>'z_face_lifecycle_independent_commit'
 AND n.nspname IN ('account','audit','discovery','identity','importing','jobs','library',
  'ml','playlist','social','sync','vault','wave','app_private');
 IF step IS NOT NULL THEN EXECUTE 'SET CONSTRAINTS '||step||' IMMEDIATE'; END IF;
 INSERT INTO app_private.native_owner_purge_final_v3
 VALUES(pg_current_xact_id(),pg_backend_pid(),target,request_id);
 INSERT INTO app_private.native_retirement_owner_cleanup_v3
 VALUES(pg_current_xact_id(),pg_backend_pid(),target,request_id);
 DELETE FROM app_private.native_legacy_retirement_witness_v3 WHERE owner_user_id=target;
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 DELETE FROM app_private.native_ordinary_pack_delete_witness_v3 WHERE owner_user_id=target;
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 DELETE FROM app_private.native_served_retirement_pack_cleanup_v3 WHERE owner_user_id=target;
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 DELETE FROM app_private.native_request_deletion_witness_v3 WHERE owner_user_id=target;
 GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 DELETE FROM app_private.native_retirement_owner_cleanup_v3
 WHERE transaction_id=pg_current_xact_id() AND backend_pid=pg_backend_pid()
  AND owner_user_id=target;
 DELETE FROM app_private.native_owner_purge_final_v3
 WHERE transaction_id=pg_current_xact_id() AND backend_pid=pg_backend_pid()
  AND owner_user_id=target;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION app_private.finalize_native_owner_purge_v3(uuid) FROM PUBLIC;
"""


def install(connection: Connection, expected_sha256: str) -> None:
    """Extend only the pinned caller, preserving its independent commit fence."""
    definition = connection.scalar(
        text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": PURGE_SIGNATURE},
    )
    if type(definition) is not str or sha256(definition.encode()).hexdigest() != expected_sha256:
        raise RuntimeError("native_v3_privacy_prior_digest_mismatch")
    before_checks = (
        "-- Validate deferred match evidence while the protected exact-ID manifest still exists."
    )
    after_checks = "PERFORM account.verify_owner_absent(target,device_ids);"
    if definition.count(before_checks) != 1 or definition.count(after_checks) != 1:
        raise RuntimeError("native_v3_privacy_prior_boundary_mismatch")
    connection.exec_driver_sql(FINALIZE_SQL, execution_options={"no_parameters": True})
    definition = definition.replace(
        before_checks,
        "total:=total+app_private.purge_native_served_retirements_owner_v3(target);\n "
        + before_checks,
    ).replace(
        after_checks,
        "total:=total+app_private.finalize_native_owner_purge_v3(target);\n " + after_checks,
    )
    connection.exec_driver_sql(definition, execution_options={"no_parameters": True})
