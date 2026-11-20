"""Inert flat-scalar resource preflight before the unchanged normalized hash.

The caller owns the admitted JSONB argument on its existing compiler frame and
prepays this additional work, including refusal, on the same work ledger. This
void helper supplies no normalized acceptance, canonical bytes, grant or quota.
It prevents Numeric text expansion before the old validator's first p::text.
"""

from typing import Final

NATIVE_NORMALIZED_SCALAR_V3_MAX_BYTES: Final = 8192
NATIVE_NORMALIZED_SCALAR_V3_MAX_MEMBERS: Final = 16
NATIVE_NORMALIZED_SCALAR_V3_MAX_SCALE: Final = 324
NATIVE_NORMALIZED_SCALAR_V3_SAFE_INTEGER: Final = 9_007_199_254_740_991
NATIVE_NORMALIZED_SCALAR_V3_COMPILER_BYTES: Final = 1_048_576
NATIVE_NORMALIZED_SCALAR_V3_MAX_CALLS_PER_FACT: Final = 6


def native_normalized_scalar_work_v3(physical_bytes: int) -> tuple[int, int]:
    """Additional logical work per actual current/parent scalar gate invocation.

    The64P+64KiB tuple/materialization bound fits the unconditional1MiB frame.
    The work bound additionally covers scalar copies/comparisons and the17-row
    refusal path. It excludes the old normalized hash and every causal graph.
    """
    if type(physical_bytes) is not int or not 1 <= physical_bytes <= 8192:
        raise ValueError("NATIVE_V3_NORMALIZED_SCALAR_WORK_INPUT")
    return 128 * physical_bytes + 65536, 32 * (physical_bytes + 1) + 1024


NATIVE_NORMALIZED_SCALAR_V3_INSTALL_SQL: Final = r"""
DO $$ BEGIN
 IF to_regprocedure('app_private.native_v3_compiler_enter(bigint)') IS NULL
  OR to_regprocedure('app_private.native_v3_compiler_exit(integer)') IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_NORMALIZED_SCALAR_COMPILER_REQUIRED' USING ERRCODE='55000'; END IF;
END $$;

CREATE FUNCTION app_private.native_v3_assert_flat_scalar_resource(p_value jsonb) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE compiler_depth integer; member record; member_count integer:=0;
 scalar_type text; number numeric;
BEGIN
 -- Argument hydration belongs to the actual caller. This separate fixed frame
 -- is entered even for refusal, before any FunctionScan tuple store/iteration.
 compiler_depth:=app_private.native_v3_compiler_enter(1048576);
 IF p_value IS NULL OR pg_column_compression(p_value) IS NOT NULL
  OR pg_column_size(p_value)>8192 OR jsonb_typeof(p_value) IS DISTINCT FROM 'object' THEN
  RAISE EXCEPTION 'NATIVE_V3_NORMALIZED_SCALAR_SHAPE' USING ERRCODE='23514'; END IF;
 FOR member IN SELECT e.key,e.value FROM jsonb_each(p_value) AS e LIMIT 17 LOOP
  member_count:=member_count+1;
  IF member_count>16 THEN
   RAISE EXCEPTION 'NATIVE_V3_NORMALIZED_SCALAR_MEMBERS' USING ERRCODE='23514'; END IF;
  scalar_type:=jsonb_typeof(member.value);
  IF scalar_type NOT IN ('null','boolean','number','string') THEN
   RAISE EXCEPTION 'NATIVE_V3_NORMALIZED_SCALAR_CHILD' USING ERRCODE='23514'; END IF;
  IF scalar_type='number' THEN
   -- jsonb_numeric copies the binary Numeric; no Numeric output function runs.
   number:=member.value::numeric;
   IF abs(number)>9007199254740991 OR scale(number)>324 THEN
    RAISE EXCEPTION 'NATIVE_V3_NORMALIZED_SCALAR_NUMBER' USING ERRCODE='23514'; END IF;
   number:=NULL;
  END IF;
  member:=NULL;
 END LOOP;
 member:=NULL; number:=NULL; scalar_type:=NULL;
 PERFORM app_private.native_v3_compiler_exit(compiler_depth);
END $$;
REVOKE ALL ON FUNCTION app_private.native_v3_assert_flat_scalar_resource(jsonb) FROM PUBLIC;
"""

NATIVE_NORMALIZED_SCALAR_V3_DROP_SQL: Final = r"""
DROP FUNCTION app_private.native_v3_assert_flat_scalar_resource(jsonb);
"""
