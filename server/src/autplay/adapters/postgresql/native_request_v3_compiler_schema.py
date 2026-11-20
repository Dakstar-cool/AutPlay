"""Explicit SQL installation for one transaction/backend's fixed compiler stack.

This is internal logical allocation accounting, never grant/input/work authority.
The caller enters before retaining raw SQL payloads, grows before further copies,
and exits only after dropping those actual objects. Nested functions share the
same fixed8MiB sum; no caller can choose a larger limit or reset another frame.
Uncaught SQL failure unwinds the statement's rows and function allocations.
A caller catching a local EXCEPTION must discard surviving graph variables
before continuing: database rollback alone does not clear PL/pgSQL variables.
The independent external work ledger is not refunded. No SQL runs on import.
"""

from typing import Final

NATIVE_REQUEST_V3_COMPILER_MAX_BYTES: Final = 8_388_608
NATIVE_REQUEST_V3_COMPILER_MAX_DEPTH: Final = 64

_INVENTORY: Final = r"""
 SELECT count(*) INTO v_count FROM (
  SELECT 1 FROM app_private.native_v3_compiler_frame AS f
  WHERE f.transaction_id=v_tx AND f.backend_pid=v_pid LIMIT 65
 ) AS bounded;
 IF v_count>64 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_STACK' USING ERRCODE='23514'; END IF;
 -- At most64 CHECK-bounded terms reach this aggregate. Their maximum sum is
 -- 536870912, so the explicit bigint conversion cannot overflow.
 SELECT coalesce(min(f.depth),0),coalesce(max(f.depth),0),
  coalesce(sum(f.byte_count),0)::bigint INTO v_min,v_top,v_total
 FROM app_private.native_v3_compiler_frame AS f
 WHERE f.transaction_id=v_tx AND f.backend_pid=v_pid;
 IF (v_count>0 AND (v_min<>1 OR v_top<>v_count)) OR v_total>8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_STACK' USING ERRCODE='23514'; END IF;
"""

NATIVE_REQUEST_V3_COMPILER_INSTALL_SQL: Final = (
    r"""
CREATE TABLE app_private.native_v3_compiler_frame (
 transaction_id xid8 NOT NULL,
 backend_pid integer NOT NULL CHECK(backend_pid>0),
 depth integer NOT NULL CHECK(depth BETWEEN 1 AND 64),
 byte_count bigint NOT NULL CHECK(byte_count BETWEEN 0 AND 8388608),
 PRIMARY KEY(transaction_id,backend_pid,depth)
);
REVOKE ALL ON TABLE app_private.native_v3_compiler_frame FROM PUBLIC;

CREATE FUNCTION app_private.native_v3_compiler_enter(p_bytes bigint) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_tx xid8:=pg_current_xact_id(); v_pid integer:=pg_backend_pid();
 v_count integer; v_min integer; v_top integer; v_total bigint;
BEGIN
 IF p_bytes IS NULL OR p_bytes NOT BETWEEN 0 AND 8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BYTES' USING ERRCODE='23514'; END IF;
"""
    + _INVENTORY
    + r"""
 IF v_count=64 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_DEPTH' USING ERRCODE='23514'; END IF;
 IF p_bytes>8388608-v_total THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
 INSERT INTO app_private.native_v3_compiler_frame(transaction_id,backend_pid,depth,byte_count)
 VALUES(v_tx,v_pid,v_count+1,p_bytes);
 RETURN v_count+1;
END $$;

CREATE FUNCTION app_private.native_v3_compiler_grow(p_depth integer,p_bytes bigint) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_tx xid8:=pg_current_xact_id(); v_pid integer:=pg_backend_pid();
 v_count integer; v_min integer; v_top integer; v_total bigint; v_existing bigint;
BEGIN
 IF p_depth IS NULL OR p_depth NOT BETWEEN 1 AND 64
  OR p_bytes IS NULL OR p_bytes NOT BETWEEN 0 AND 8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BYTES' USING ERRCODE='23514'; END IF;
"""
    + _INVENTORY
    + r"""
 IF v_count=0 OR p_depth<>v_top THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_LIFO' USING ERRCODE='23514'; END IF;
 SELECT f.byte_count INTO v_existing FROM app_private.native_v3_compiler_frame AS f
 WHERE f.transaction_id=v_tx AND f.backend_pid=v_pid AND f.depth=p_depth;
 IF v_existing IS NULL OR p_bytes<v_existing THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_SHRINK' USING ERRCODE='23514'; END IF;
 -- Compare the additional bytes before any mutation; subtraction is bounded.
 IF p_bytes-v_existing>8388608-v_total THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
 UPDATE app_private.native_v3_compiler_frame AS f SET byte_count=p_bytes
 WHERE f.transaction_id=v_tx AND f.backend_pid=v_pid AND f.depth=p_depth;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_STACK' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.native_v3_compiler_exit(p_depth integer) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_tx xid8:=pg_current_xact_id(); v_pid integer:=pg_backend_pid();
 v_count integer; v_min integer; v_top integer; v_total bigint;
BEGIN
 IF p_depth IS NULL OR p_depth NOT BETWEEN 1 AND 64 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_LIFO' USING ERRCODE='23514'; END IF;
"""
    + _INVENTORY
    + r"""
 IF v_count=0 OR p_depth<>v_top THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_LIFO' USING ERRCODE='23514'; END IF;
 DELETE FROM app_private.native_v3_compiler_frame AS f
 WHERE f.transaction_id=v_tx AND f.backend_pid=v_pid AND f.depth=p_depth;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_STACK' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.check_native_v3_compiler_empty() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.transaction_id IS DISTINCT FROM pg_current_xact_id()
  OR NEW.backend_pid IS DISTINCT FROM pg_backend_pid()
  OR EXISTS(SELECT 1 FROM app_private.native_v3_compiler_frame AS f
   WHERE f.transaction_id=pg_current_xact_id() AND f.backend_pid=pg_backend_pid()) THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_LEAK' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER native_v3_compiler_empty
 AFTER INSERT OR UPDATE ON app_private.native_v3_compiler_frame
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.check_native_v3_compiler_empty();

REVOKE ALL ON FUNCTION app_private.native_v3_compiler_enter(bigint),
 app_private.native_v3_compiler_grow(integer,bigint),
 app_private.native_v3_compiler_exit(integer),
 app_private.check_native_v3_compiler_empty() FROM PUBLIC;
"""
)

NATIVE_REQUEST_V3_COMPILER_DROP_SQL: Final = r"""
DROP TRIGGER native_v3_compiler_empty ON app_private.native_v3_compiler_frame;
DROP FUNCTION app_private.check_native_v3_compiler_empty();
DROP FUNCTION app_private.native_v3_compiler_exit(integer);
DROP FUNCTION app_private.native_v3_compiler_grow(integer,bigint);
DROP FUNCTION app_private.native_v3_compiler_enter(bigint);
DROP TABLE app_private.native_v3_compiler_frame;
"""
