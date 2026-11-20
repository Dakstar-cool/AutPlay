"""Draft private SQL identity of an admitted source3 bit-profile.

The caller retains one exact same-source dimensions projection under its
separately prepaid 8-MiB compiler phase. This helper receives that datum; it
never extracts the full array per row. It grants no provenance/current consent.
The writer compares its result with an independently generated original typed
profile identity and separately checks the unchanged numeric R1 semantics.

No SQL installs on import. Work reservations must be fully charged before the
call, including failed attempts. Logical workspace is distinct from backend
RSS, allocator capacity, or physical page IO. PostgreSQL qualification remains
required for the exact ordered aggregate/ProjectSet lifetime used below.
"""

from __future__ import annotations

import json

from autplay.domain.adaptive_recommendations import DEFAULT_ADAPTIVE_FEATURE_POLICY, DimensionKind
from autplay.domain.native_profile_binary64_v3 import (
    NATIVE_PROFILE_BINARY64_V3_DIMENSION_FIELDS,
    NATIVE_PROFILE_BINARY64_V3_DOMAIN,
    NATIVE_PROFILE_BINARY64_V3_MATURITY_FIELDS,
)

NATIVE_PROFILE_BINARY64_SQL_HEADER_BYTES = (
    len(NATIVE_PROFILE_BINARY64_V3_DOMAIN.encode("ascii"))
    + 5
    + (5 + 16)
    + 3 * (5 + 8)
    + 3 * (5 + 32)
    + len(NATIVE_PROFILE_BINARY64_V3_MATURITY_FIELDS) * (5 + 8)
)
NATIVE_PROFILE_BINARY64_SQL_DIMENSION_FIXED_BYTES = 2 * 5 + len(
    NATIVE_PROFILE_BINARY64_V3_DIMENSION_FIELDS
) * (5 + 8)
NATIVE_PROFILE_BINARY64_SQL_MAX_SEGMENT_BYTES = (
    NATIVE_PROFILE_BINARY64_SQL_DIMENSION_FIXED_BYTES
    + max(len(value.value.encode()) for value in DimensionKind)
    + 4 * 200
)
NATIVE_PROFILE_BINARY64_SQL_FIXED_WORKSPACE = 16_384
NATIVE_PROFILE_BINARY64_SQL_WORKSPACE_LIMIT = 524_288
NATIVE_PROFILE_BINARY64_SQL_STAT_PREIMAGE_BYTES = 1
NATIVE_PROFILE_BINARY64_SQL_STAT_MAX_SEGMENT = 2
NATIVE_PROFILE_BINARY64_SQL_STAT_ROWS = 3
NATIVE_PROFILE_BINARY64_SQL_STAT_SORT_BYTES = 4
NATIVE_PROFILE_BINARY64_SQL_STAT_WORKSPACE = 5
NATIVE_PROFILE_BINARY64_SQL_STAT_BYTE_WORK = 6
NATIVE_PROFILE_BINARY64_SQL_STAT_NODE_VISITS = 7


def native_profile_binary64_sql_work_v3(
    *, dimension_count: int, profile_jsonb_bytes: int, dimensions_jsonb_bytes: int
) -> tuple[int, int]:
    """Full precharge before measure+hash, using already admitted actual sizes."""
    if (
        type(dimension_count) is not int
        or not 0 <= dimension_count <= 512
        or type(profile_jsonb_bytes) is not int
        or not 1 <= profile_jsonb_bytes <= 8_388_608
        or type(dimensions_jsonb_bytes) is not int
        or not 1 <= dimensions_jsonb_bytes <= 8_388_608
    ):
        raise ValueError("PROFILE_BINARY64_SQL_WORK_INPUT")
    upper = (
        NATIVE_PROFILE_BINARY64_SQL_HEADER_BYTES
        + NATIVE_PROFILE_BINARY64_SQL_MAX_SEGMENT_BYTES * dimension_count
    )
    return (
        64 * (profile_jsonb_bytes + dimensions_jsonb_bytes) + 128 * upper + 65_536,
        4096 + 2048 * (dimension_count + 1),
    )


def _literal(value: object) -> str:
    return "'" + json.dumps(value, separators=(",", ":")).replace("'", "''") + "'"


def _text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_POLICY = DEFAULT_ADAPTIVE_FEATURE_POLICY
NATIVE_PROFILE_BINARY64_V3_INSTALL_SQL = (
    r"""
CREATE FUNCTION app_private.native_profile_binary64_fields_v3() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _literal(
        {
            "maturity": NATIVE_PROFILE_BINARY64_V3_MATURITY_FIELDS,
            "dimensions": NATIVE_PROFILE_BINARY64_V3_DIMENSION_FIELDS,
        }
    )
    + r"""::jsonb $$;

CREATE FUNCTION app_private.native_profile_binary64_frame_v3(value bytea) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
 IF value IS NULL OR octet_length(value)>800 THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_FIELD' USING ERRCODE='23514'; END IF;
 RETURN decode('01','hex')||int4send(octet_length(value))||value;
END $$;

CREATE FUNCTION app_private.native_profile_binary64_i64_v3(value jsonb) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE number numeric;
BEGIN
 IF jsonb_typeof(value) IS DISTINCT FROM 'number' THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_INTEGER' USING ERRCODE='23514'; END IF;
 -- Numeric comparison/scale avoids expanding a compact hostile exponent to
 -- text. Raw spelling was already checked by the separate source3 scanner.
 number:=value::numeric;
 IF scale(number)<>0 OR number NOT BETWEEN 0 AND 9223372036854775807::numeric THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_INTEGER' USING ERRCODE='23514'; END IF;
 RETURN number::bigint;
END $$;

CREATE FUNCTION app_private.native_profile_binary64_header_v3(profile jsonb,
 source_membership bytea,old_profile_sha bytea,dimension_count integer) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE output bytea; owner_text text; policy_sha text; fields jsonb; ordinal integer;
 field_path text; field_rule text; encoded jsonb;
BEGIN
 IF jsonb_typeof(profile) IS DISTINCT FROM 'object'
  OR source_membership IS NULL OR octet_length(source_membership)<>32
  OR old_profile_sha IS NULL OR octet_length(old_profile_sha)<>32
  OR dimension_count IS NULL OR dimension_count NOT BETWEEN 0 AND 512 THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_INPUT' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(profile->'owner_user_id') IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_OWNER' USING ERRCODE='23514'; END IF;
 owner_text:=profile->>'owner_user_id';
 IF owner_text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  OR app_private.native_profile_binary64_i64_v3(profile->'schema_version')<>1 THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_OWNER' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(profile#>'{feature_policy,key}') IS DISTINCT FROM 'string'
  OR jsonb_typeof(profile#>'{feature_policy,version}') IS DISTINCT FROM 'string'
  OR jsonb_typeof(profile#>'{feature_policy,content_sha256}') IS DISTINCT FROM 'string'
  OR jsonb_typeof(profile->'profile_sha256') IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_POLICY' USING ERRCODE='23514'; END IF;
 IF profile#>>'{feature_policy,key}' IS DISTINCT FROM """
    + _text(_POLICY.key)
    + r"""
  OR profile#>>'{feature_policy,version}' IS DISTINCT FROM """
    + _text(_POLICY.version)
    + r""" THEN RAISE EXCEPTION 'PROFILE_BINARY64_POLICY' USING ERRCODE='23514'; END IF;
 policy_sha:=profile#>>'{feature_policy,content_sha256}';
 IF policy_sha IS DISTINCT FROM '"""
    + _POLICY.content_sha256
    + r"""' OR jsonb_typeof(profile->'profile_sha256') IS DISTINCT FROM 'string'
  OR (profile->>'profile_sha256') !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_POLICY' USING ERRCODE='23514'; END IF;
 IF decode(profile->>'profile_sha256','hex') IS DISTINCT FROM old_profile_sha THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_PROFILE_SHA' USING ERRCODE='23514'; END IF;
 output:=convert_to('"""
    + NATIVE_PROFILE_BINARY64_V3_DOMAIN
    + r"""','UTF8')||decode('00','hex')||int4send(13+24*dimension_count)
  ||app_private.native_profile_binary64_frame_v3(uuid_send(owner_text::uuid))
  ||app_private.native_profile_binary64_frame_v3(int8send(
    app_private.native_profile_binary64_i64_v3(profile->'cutoff_at_ms')))
  ||app_private.native_profile_binary64_frame_v3(int8send(
    app_private.native_profile_binary64_i64_v3(profile->'interaction_watermark')))
  ||app_private.native_profile_binary64_frame_v3(decode(policy_sha,'hex'))
  ||app_private.native_profile_binary64_frame_v3(source_membership)
  ||app_private.native_profile_binary64_frame_v3(old_profile_sha)
  ||app_private.native_profile_binary64_frame_v3(int8send(dimension_count::bigint));
 fields:=app_private.native_profile_binary64_fields_v3()->'maturity';
 FOR ordinal IN 0..5 LOOP
  field_path:=fields->ordinal->>0; field_rule:=fields->ordinal->>1;
  encoded:=profile#>ARRAY['maturity',field_path];
  IF jsonb_typeof(encoded) IS DISTINCT FROM 'string' THEN
   RAISE EXCEPTION 'PROFILE_BINARY64_FLOAT' USING ERRCODE='23514'; END IF;
  output:=output||app_private.native_profile_binary64_frame_v3(
   app_private.native_source3_float_bits_v3(encoded#>>'{}',field_rule));
 END LOOP;
 RETURN output;
END $$;

CREATE FUNCTION app_private.native_profile_binary64_segment_v3(dimensions jsonb,
 ordinal integer) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE row_value jsonb; output bytea; fields jsonb; field_index integer; encoded jsonb;
 field_path text; field_rule text;
BEGIN
 IF jsonb_typeof(dimensions) IS DISTINCT FROM 'array' OR ordinal IS NULL
  OR ordinal NOT BETWEEN 1 AND jsonb_array_length(dimensions)
  OR jsonb_array_length(dimensions)>512 THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_DIMENSION' USING ERRCODE='23514'; END IF;
 row_value:=dimensions->(ordinal-1);
 -- One admitted bounded row, never a fresh full dimensions-array projection.
 output:=app_private.native_profile_binary64_frame_v3(convert_to(row_value->>'kind','UTF8'))
  ||app_private.native_profile_binary64_frame_v3(convert_to(row_value->>'key','UTF8'));
 fields:=app_private.native_profile_binary64_fields_v3()->'dimensions';
 FOR field_index IN 0..21 LOOP
  field_path:=fields->field_index->>0; field_rule:=fields->field_index->>1;
  encoded:=row_value#>string_to_array(field_path,'.');
  IF jsonb_typeof(encoded) IS DISTINCT FROM 'string' THEN
   RAISE EXCEPTION 'PROFILE_BINARY64_FLOAT' USING ERRCODE='23514'; END IF;
  output:=output||app_private.native_profile_binary64_frame_v3(
   app_private.native_source3_float_bits_v3(encoded#>>'{}',field_rule));
 END LOOP;
 RETURN output;
END $$;

CREATE FUNCTION app_private.native_profile_binary64_preflight_v3(profile jsonb,
 dimensions jsonb,source_membership bytea,old_profile_sha bytea,
 remaining_byte_work bigint,remaining_node_visits bigint) RETURNS bigint[]
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE count integer; ordinal integer; preimage bigint; maximum bigint:=293; size bigint;
 row_value jsonb; kind_text text; key_text text;
 kind_bytes bytea; key_bytes bytea; previous_kind bytea:=NULL; previous_key bytea;
 header bytea; segment bytea; sorter bigint; workspace bigint; byte_work bigint; visits bigint;
BEGIN
 IF jsonb_typeof(dimensions) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_DIMENSION' USING ERRCODE='23514'; END IF;
 count:=jsonb_array_length(dimensions);
 IF count>512 THEN RAISE EXCEPTION 'PROFILE_BINARY64_COUNT' USING ERRCODE='23514'; END IF;
 -- These input datums were already admitted/projected by the caller's compiler
 -- phase. They are not a new bytea/JSONB hydration or a workspace credit here.
 byte_work:=64::bigint*(pg_column_size(profile)::bigint+pg_column_size(dimensions)::bigint)
  +128::bigint*(293+1118*count)+65536;
 visits:=4096+2048::bigint*(count+1);
 IF remaining_byte_work IS NULL OR remaining_node_visits IS NULL
  OR byte_work IS NULL OR remaining_byte_work<byte_work OR remaining_node_visits<visits THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_WORK' USING ERRCODE='23514'; END IF;
 header:=app_private.native_profile_binary64_header_v3(
  profile,source_membership,old_profile_sha,count);
 IF octet_length(header) IS DISTINCT FROM 293 THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_HEADER' USING ERRCODE='23514'; END IF;
 preimage:=293; header:=NULL;
 IF count>0 THEN
  FOR ordinal IN 1..count LOOP
   row_value:=dimensions->(ordinal-1);
   IF jsonb_typeof(row_value->'kind') IS DISTINCT FROM 'string'
    OR jsonb_typeof(row_value->'key') IS DISTINCT FROM 'string' THEN
    RAISE EXCEPTION 'PROFILE_BINARY64_STRING' USING ERRCODE='23514'; END IF;
   kind_text:=row_value->>'kind'; key_text:=row_value->>'key';
   IF kind_text NOT IN ('BOUNDED_METADATA_TOKEN','CANONICAL_ARTIST_ID','CANONICAL_RELEASE_ID')
    OR char_length(key_text) NOT BETWEEN 1 AND 200
    OR octet_length(convert_to(key_text,'UTF8'))>800 THEN
    RAISE EXCEPTION 'PROFILE_BINARY64_STRING' USING ERRCODE='23514'; END IF;
   kind_bytes:=convert_to(kind_text,'UTF8'); key_bytes:=convert_to(key_text,'UTF8');
   -- UTF8 byte order agrees with Unicode scalar order, independent of the
   -- database text encoding/collation. Preserve normalization and zero signs.
   IF previous_kind IS NOT NULL AND
    (kind_bytes<previous_kind OR (kind_bytes=previous_kind AND key_bytes<=previous_key)) THEN
    RAISE EXCEPTION 'PROFILE_BINARY64_ORDER' USING ERRCODE='23514'; END IF;
   previous_kind:=kind_bytes; previous_key:=key_bytes;
   row_value:=NULL;
   -- Only this one <=1118-byte segment is built during measurement.
   segment:=app_private.native_profile_binary64_segment_v3(dimensions,ordinal);
   size:=octet_length(segment); segment:=NULL; row_value:=NULL;
   IF size IS NULL OR size<>296+octet_length(kind_text)+octet_length(convert_to(key_text,'UTF8'))
    OR size>1118 THEN RAISE EXCEPTION 'PROFILE_BINARY64_SEGMENT' USING ERRCODE='23514'; END IF;
   preimage:=preimage+size; maximum:=greatest(maximum,size);
  END LOOP;
 END IF;
 sorter:=preimage+8::bigint*(count+1);
 -- Count old+new logical transition overlap, sorter, two current segment
 -- copies, and fixed roots/framing/row/scalar/hash scratch. Finalization must
 -- end tuplesort BEFORE its final full bytea copy and direct core SHA call.
 workspace:=greatest(sorter+2*maximum+16384,
  2*preimage+sorter+2*maximum+16384,2*preimage+2*maximum+16384);
 IF workspace>524288 THEN
  RAISE EXCEPTION 'PROFILE_BINARY64_WORKSPACE' USING ERRCODE='23514'; END IF;
 -- One-based slots: P,M,rows,Q,W,full-byte-precharge,full-visit-precharge.
 RETURN ARRAY[preimage,maximum,(count+1)::bigint,sorter,workspace,byte_work,visits];
END $$;

CREATE FUNCTION app_private.native_profile_binary64_digest_v3(profile jsonb,
 dimensions jsonb,source_membership bytea,old_profile_sha bytea,
 remaining_byte_work bigint,remaining_node_visits bigint) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog
 SET enable_presorted_aggregate=off AS $$
DECLARE stats bigint[]; count integer;
BEGIN
 stats:=app_private.native_profile_binary64_preflight_v3(profile,dimensions,
  source_membership,old_profile_sha,remaining_byte_work,remaining_node_visits);
 count:=stats[3]::integer-1;
 -- Target-list ProjectSet yields ordinals only. Ordered aggregate retains one
 -- framed segment+int8 ordinal per row; no FunctionScan/materialized CTE, full
 -- preimage assignment, full field vector, extra prefix or posthash copy.
 RETURN (SELECT sha256(string_agg(CASE WHEN ordinal=0 THEN
   app_private.native_profile_binary64_header_v3(profile,source_membership,old_profile_sha,count)
  ELSE app_private.native_profile_binary64_segment_v3(dimensions,ordinal::integer) END,
  ''::bytea ORDER BY ordinal))
  FROM (SELECT generate_series(0,count)::bigint AS ordinal OFFSET 0) positions);
END $$;

REVOKE ALL ON FUNCTION app_private.native_profile_binary64_fields_v3(),
 app_private.native_profile_binary64_frame_v3(bytea),
 app_private.native_profile_binary64_i64_v3(jsonb),
 app_private.native_profile_binary64_header_v3(jsonb,bytea,bytea,integer),
 app_private.native_profile_binary64_segment_v3(jsonb,integer),
 app_private.native_profile_binary64_preflight_v3(jsonb,jsonb,bytea,bytea,bigint,bigint),
 app_private.native_profile_binary64_digest_v3(jsonb,jsonb,bytea,bytea,bigint,bigint) FROM PUBLIC;
"""
)

NATIVE_PROFILE_BINARY64_V3_DROP_SQL = r"""
DROP FUNCTION app_private.native_profile_binary64_digest_v3(jsonb,jsonb,bytea,bytea,bigint,bigint);
DROP FUNCTION app_private.native_profile_binary64_preflight_v3(
 jsonb,jsonb,bytea,bytea,bigint,bigint);
DROP FUNCTION app_private.native_profile_binary64_segment_v3(jsonb,integer);
DROP FUNCTION app_private.native_profile_binary64_header_v3(jsonb,bytea,bytea,integer);
DROP FUNCTION app_private.native_profile_binary64_i64_v3(jsonb);
DROP FUNCTION app_private.native_profile_binary64_frame_v3(bytea);
DROP FUNCTION app_private.native_profile_binary64_fields_v3();
"""
