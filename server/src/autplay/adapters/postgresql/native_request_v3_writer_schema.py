"""Dormant, private Source3 atomic writer and bounded original raw admission.

The caller owns the transaction, authenticated origin arm and the already
admitted G work/compiler lease. No argument is a quota receipt or a grant.
All constants are inert until an explicit installer executes them. The SQL
draft has not been executed against PostgreSQL or qualified for runtime use.
"""

from __future__ import annotations

from typing import Final

NATIVE_ORIGINAL_DOCUMENT_V3_MAX_BYTES: Final = 2_097_152
NATIVE_REQUEST_V3_WRITER_COMPILER_LIMIT: Final = 8_388_608
NATIVE_REQUEST_V3_ITEM_ROWS_HEADER_BYTES: Final = 43
NATIVE_REQUEST_V3_WRITER_METADATA_BYTES: Final = 262_144
NATIVE_REQUEST_V3_ITEM_OUTPUT_BYTES: Final = 196_640
NATIVE_REQUEST_V3_ITEM_MEASURER_BYTES: Final = 524_288
NATIVE_REQUEST_V3_ITEM_PROJECTION_BYTES: Final = 1_048_576
NATIVE_REQUEST_V3_COMPILER_SCOPE_BYTE_WORK: Final = 16_384
NATIVE_REQUEST_V3_COMPILER_SCOPE_NODE_VISITS: Final = 1_024
# Fifteen writer phase/parent calls plus the two fused wrappers' four calls.
# The three generic scans carry their six calls in their own exported bound;
# each returned leaf carries two calls in its leaf bound. Nested graph/current
# and deferred assertions have separate component costs and are never free.
NATIVE_REQUEST_V3_WRITER_BASE_SCOPE_CALLS: Final = 19
NATIVE_REQUEST_V3_ITEM_ROWS_MAGIC: Final = b"APIR3\0"
NATIVE_REQUEST_V3_ITEM_ROWS_ROLES: Final = {"ORIGINAL": b"O", "SERVED": b"S"}
NATIVE_REQUEST_V3_WRITER_DISPATCH_TRIGGER_TABLES: Final = (
    "recommendation_request",
    "recommendation_item",
    "recommendation_serving_decision",
    "recommendation_served_item",
    "recommendation_request_replay_payload",
)
NATIVE_REQUEST_V3_WRITER_SIGNATURE: Final = (
    "commit_native_atomic_capture_v3(uuid,uuid,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea)"
)
NATIVE_REQUEST_V3_WRITER_REQUIRED_FUNCTIONS: Final = (
    "app_private.native_original_document_preflight_v3(bytea)",
    "app_private.native_original_baseline_preflight_v4(bytea)",
    "app_private.native_source3_preflight_v3(bytea,bigint,bigint)",
    "app_private.native_original_profile_preflight_v3(bytea,bigint,bigint)",
    "app_private.native_v3_wire_value(bytea,integer,text,integer)",
    "app_private.native_v3_root_preflight(bytea)",
    "app_private.native_v3_role_hash(text,jsonb)",
    "app_private.native_v3_role_schema()",
    "app_private.native_v3_role_lp_schema(text)",
    "app_private.native_v3_lp_size(jsonb,jsonb,text,integer)",
    "app_private.native_served_json_lp(jsonb,jsonb,text,integer)",
    "app_private.native_served_json_hash(jsonb,text)",
    "app_private.native_served_baseline_digest(uuid,jsonb)",
    "app_private.native_served_item_digest(uuid,jsonb)",
    "app_private.native_v3_item_rows_byte_length(uuid,uuid,text)",
    "app_private.native_v3_assert_current_baseline_pipeline(bytea)",
    "app_private.native_profile_binary64_digest_v3(jsonb,jsonb,bytea,bytea,bigint,bigint)",
    "app_private.native_v3_source_index_prefix(integer)",
    "app_private.native_v3_source_index_entry(uuid,uuid,uuid,bytea,bytea,uuid,uuid,"
    "bigint,bigint,text,uuid,uuid,uuid,bigint)",
    "app_private.native_v3_source_cursor(uuid,uuid,bigint,bytea,bigint,bigint,"
    "bigint,text,bigint,bigint)",
    "app_private.native_v3_decision_digest(uuid)",
    "app_private.native_v3_connection_seal(uuid)",
    "app_private.assert_native_atomic_graph_v3(uuid)",
    "app_private.assert_native_atomic_current_v3(uuid,uuid,bigint)",
    "app_private.check_native_served_identity(uuid,uuid,uuid,uuid,bigint)",
    "app_private.native_v3_resolve_served_format(uuid,uuid)",
    "app_private.native_v3_bound_original(uuid,uuid)",
    "app_private.protect_native_served_authority_dispatch_v3()",
    "app_private.native_v3_compiler_enter(bigint)",
    "app_private.native_v3_compiler_grow(integer,bigint)",
    "app_private.native_v3_compiler_exit(integer)",
)

# This separate prerequisite installs before the real original assertion, graph
# and context. Combining it with the writer would introduce a dependency cycle.
NATIVE_ORIGINAL_DOCUMENT_V3_INSTALL_SQL: Final = r"""
DO $$ BEGIN
 IF to_regprocedure('app_private.native_source3_string_v3(bytea,integer)') IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_STRING_HELPER_REQUIRED' USING ERRCODE='55000'; END IF;
 IF to_regprocedure('app_private.native_v3_compiler_enter(bigint)') IS NULL
  OR to_regprocedure('app_private.native_v3_compiler_exit(integer)') IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_COMPILER_REQUIRED' USING ERRCODE='55000'; END IF;
END $$;
CREATE FUNCTION app_private.native_original_document_scan_v3(
 p_raw bytea,p_offset integer,p_depth integer) RETURNS bigint[]
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE v_size integer:=octet_length(p_raw); v_token integer; v_count integer:=0;
 v_start integer; v_text text; v_key text; v_last bytea; v_key_bytes bytea;
 v_nodes bigint:=1; v_json bigint:=32; v_child bigint[]; v_exp text; v_number float8;
BEGIN
 IF p_raw IS NULL OR p_offset IS NULL OR p_depth IS NULL OR p_depth NOT BETWEEN 0 AND 12
  OR p_offset<0 OR p_offset>=v_size THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_RAW' USING ERRCODE='23514'; END IF;
 v_token:=get_byte(p_raw,p_offset);
 IF v_token=34 THEN
  SELECT s.next_position,s.decoded_value INTO p_offset,v_text
   FROM app_private.native_source3_string_v3(p_raw,p_offset) s;
  RETURN ARRAY[p_offset::bigint,v_nodes,16+octet_length(convert_to(v_text,'UTF8'))::bigint];
 ELSIF v_token IN (123,91) THEN
  p_offset:=p_offset+1;
  IF p_offset<v_size AND get_byte(p_raw,p_offset)=(CASE v_token WHEN 123 THEN 125 ELSE 93 END)
   THEN RETURN ARRAY[(p_offset+1)::bigint,v_nodes,v_json]; END IF;
  LOOP
   v_count:=v_count+1;
   IF v_count>(CASE v_token WHEN 123 THEN 4096 ELSE 5000 END) THEN
    RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_CONTAINER_BOUND' USING ERRCODE='23514'; END IF;
   IF v_token=123 THEN
    SELECT s.next_position,s.decoded_value INTO p_offset,v_key
     FROM app_private.native_source3_string_v3(p_raw,p_offset) s;
    v_key_bytes:=convert_to(v_key,'UTF8');
    IF v_key !~ '^[ -~]*$' OR (v_last IS NOT NULL AND v_key_bytes<=v_last)
     OR p_offset>=v_size OR get_byte(p_raw,p_offset)<>58 THEN
     RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_KEYS' USING ERRCODE='23514'; END IF;
    v_last:=v_key_bytes; p_offset:=p_offset+1;
    v_nodes:=v_nodes+1; v_json:=v_json+32+octet_length(v_key_bytes);
   ELSE v_json:=v_json+16; END IF;
   v_child:=app_private.native_original_document_scan_v3(p_raw,p_offset,p_depth+1);
   p_offset:=v_child[1]::integer; v_nodes:=v_nodes+v_child[2]; v_json:=v_json+v_child[3];
   IF v_json>8388608 OR p_offset>=v_size THEN
    RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_LOGICAL_BOUND' USING ERRCODE='23514'; END IF;
   v_start:=get_byte(p_raw,p_offset); p_offset:=p_offset+1;
   IF v_start=(CASE v_token WHEN 123 THEN 125 ELSE 93 END) THEN EXIT; END IF;
   IF v_start<>44 THEN
    RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_RAW' USING ERRCODE='23514'; END IF;
  END LOOP;
  RETURN ARRAY[p_offset::bigint,v_nodes,v_json];
 ELSIF substring(p_raw FROM p_offset+1 FOR 4) IN (convert_to('null','UTF8'),
  convert_to('true','UTF8')) THEN RETURN ARRAY[(p_offset+4)::bigint,v_nodes,16::bigint];
 ELSIF substring(p_raw FROM p_offset+1 FOR 5)=convert_to('false','UTF8') THEN
  RETURN ARRAY[(p_offset+5)::bigint,v_nodes,16::bigint];
 END IF;
 v_start:=p_offset;
 WHILE p_offset<v_size AND get_byte(p_raw,p_offset) NOT IN (44,93,125) LOOP
  IF p_offset-v_start>=32 THEN
   RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_NUMBER_BOUND' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1;
 END LOOP;
 v_text:=convert_from(substring(p_raw FROM v_start+1 FOR p_offset-v_start),'UTF8');
 IF v_text !~ '^-?(0|[1-9][0-9]*)(\.[0-9]+)?(e[+-]?[0-9]+)?$' OR v_text='-0' THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_NUMBER' USING ERRCODE='23514'; END IF;
 v_exp:=substring(v_text FROM 'e([+-]?[0-9]+)$');
 IF v_exp IS NOT NULL THEN
  IF length(v_exp)>4 THEN
   RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_NUMBER_BOUND' USING ERRCODE='23514'; END IF;
  IF v_exp::integer NOT BETWEEN -324 AND 308 THEN
   RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_NUMBER_BOUND' USING ERRCODE='23514'; END IF;
 END IF;
 IF position('.' IN v_text)=0 AND position('e' IN v_text)=0
  AND abs(v_text::numeric)>9007199254740991 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_SAFE53' USING ERRCODE='23514'; END IF;
 BEGIN
  v_number:=v_text::float8;
 EXCEPTION WHEN data_exception THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_NUMBER' USING ERRCODE='23514'; END;
 IF v_number IN ('Infinity'::float8,'-Infinity'::float8,'NaN'::float8) THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_NUMBER' USING ERRCODE='23514'; END IF;
 RETURN ARRAY[p_offset::bigint,v_nodes,64::bigint];
END $$;

CREATE FUNCTION app_private.native_original_baseline_preflight_v4(p_raw bytea)
RETURNS bigint[] LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE v_stat bigint[]; v_size bigint:=octet_length(p_raw); v_frame integer;
 v_result bigint[];
BEGIN
 IF p_raw IS NULL OR v_size NOT BETWEEN 1 AND 4194304 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_BYTES' USING ERRCODE='23514'; END IF;
 v_frame:=app_private.native_v3_compiler_enter(v_size+1048576);
 -- Materialize the admitted bytea once before recursive indexed reads.
 v_stat:=app_private.native_original_document_scan_v3(p_raw || ''::bytea,0,0);
 IF v_stat[1]<>v_size THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_TRAILING' USING ERRCODE='23514'; END IF;
 v_result:=ARRAY[v_size,v_stat[2],v_stat[3]];
 v_stat:=NULL; p_raw:=NULL;
 -- Three scalar counters transfer into the caller's fixed metadata slot.
 PERFORM app_private.native_v3_compiler_exit(v_frame);
 RETURN v_result;
END $$;
CREATE FUNCTION app_private.native_original_document_preflight_v3(p_raw bytea)
RETURNS bigint[] LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF p_raw IS NULL OR octet_length(p_raw) NOT BETWEEN 1 AND 2097152 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_BYTES' USING ERRCODE='23514'; END IF;
 RETURN app_private.native_original_baseline_preflight_v4(p_raw);
END $$;
REVOKE ALL ON FUNCTION app_private.native_original_document_scan_v3(bytea,integer,integer),
 app_private.native_original_baseline_preflight_v4(bytea),
 app_private.native_original_document_preflight_v3(bytea) FROM PUBLIC;
"""
NATIVE_ORIGINAL_DOCUMENT_V3_DROP_SQL: Final = r"""
DROP FUNCTION app_private.native_original_document_preflight_v3(bytea);
DROP FUNCTION app_private.native_original_baseline_preflight_v4(bytea);
DROP FUNCTION app_private.native_original_document_scan_v3(bytea,integer,integer);
"""


def native_v3_item_leaf_work_bound_v3(lp_bytes: int, lp_nodes: int) -> tuple[int, int]:
    """Prepay bounded leaf validation/codec and its two compiler mutations.

    The caller obtains bounds during its separately charged discovery phase.
    Each encoder container has at most ``lp_nodes`` prefixes of at most L
    bytes. The factor includes two role/hash/encoder traversals and copies;
    Callers separately admit/prepay actual SQL row payload reads, copies and
    numeric normalization from bounded row descriptors before invocation,
    including failures before a valid LP leaf exists. They also add metadata
    queries and the enclosing writer/assertion work.
    This conservative bound may refuse an expensive valid leaf on the same
    existing owner; it neither resets its allowance nor proves provenance.
    """
    if (
        type(lp_bytes) is not int
        or type(lp_nodes) is not int
        or not 1 <= lp_bytes <= 65_536
        or not 1 <= lp_nodes <= 65_536
    ):
        raise ValueError("NATIVE_V3_ITEM_WORK_BOUND")
    return (
        64 * lp_bytes * (lp_nodes + 1) + 262_144 + 2 * NATIVE_REQUEST_V3_COMPILER_SCOPE_BYTE_WORK,
        64 * (lp_nodes + 1) + 4_096 + 2 * NATIVE_REQUEST_V3_COMPILER_SCOPE_NODE_VISITS,
    )


def native_v3_original_document_work_bound_v3(raw_bytes: int) -> tuple[int, int]:
    """Prepay one bounded raw scan, including its two actual scope mutations."""
    if type(raw_bytes) is not int or not 1 <= raw_bytes <= NATIVE_ORIGINAL_DOCUMENT_V3_MAX_BYTES:
        raise ValueError("NATIVE_V3_ORIGINAL_WORK_BOUND")
    return (
        144 * raw_bytes + 16_777_216 + 2 * NATIVE_REQUEST_V3_COMPILER_SCOPE_BYTE_WORK,
        16 * (raw_bytes + 1) + 2 * NATIVE_REQUEST_V3_COMPILER_SCOPE_NODE_VISITS,
    )


_ITEM_ROWS_SQL: Final = r"""
CREATE FUNCTION app_private.native_v3_item_integer_v3(p_value jsonb) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE v_number numeric;
BEGIN
 IF jsonb_typeof(p_value) IS DISTINCT FROM 'number' THEN
  RAISE EXCEPTION 'NATIVE_V3_ITEM_INTEGER' USING ERRCODE='23514'; END IF;
 -- Direct JSONB->numeric avoids expanding compact huge exponents as text.
 v_number:=p_value::numeric;
 IF NOT (v_number BETWEEN -9223372036854775808 AND 9223372036854775807)
  OR v_number<>trunc(v_number) THEN
  RAISE EXCEPTION 'NATIVE_V3_ITEM_INTEGER' USING ERRCODE='23514'; END IF;
 RETURN v_number::bigint;
END $$;

CREATE FUNCTION app_private.native_v3_item_e8_v3(p_score numeric) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE v_scaled numeric;
BEGIN
 IF p_score IS NULL OR NOT (p_score BETWEEN -92233720368.54775808 AND 92233720368.54775807) THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_ITEM_E8_GRID' USING ERRCODE='23514'; END IF;
 v_scaled:=p_score*100000000;
 IF v_scaled<>trunc(v_scaled) THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_ITEM_E8_GRID' USING ERRCODE='23514'; END IF;
 RETURN v_scaled::bigint;
END $$;

CREATE FUNCTION app_private.native_v3_item_rows_byte_length(
 p_owner uuid,p_request uuid,p_role text) RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE v_row record; v_leaf jsonb; v_contributions jsonb; v_part jsonb;
 v_provenance jsonb; v_fields jsonb; v_field text; v_code text; v_source text;
 v_count integer; v_rank integer; v_contribution_count integer; v_idx integer;
 v_units bigint; v_kind text; v_frame integer; v_row_frame integer;
 v_length bigint:=43; v_encoded bytea; v_lp_role text; v_schema jsonb;
BEGIN
 -- Closed private measurer, not a row reader or grant. A fixed RR snapshot
 -- binds each metadata cap and subsequent payload SELECT. The actual writer
 -- already uses RR; standalone private calls must obey the same requirement.
 IF p_role='RC_SERVED' AND current_setting('transaction_isolation')='read committed' THEN
  -- RC impression ingress revalidates immutable served rows. Lock the FK
  -- parent against new children and every bounded child against mutation
  -- before measuring metadata or fetching the separately stored payloads.
  PERFORM q.recommendation_request_id FROM ml.recommendation_request q
   WHERE q.recommendation_request_id=p_request AND q.user_id=p_owner
    AND q.serving_contract_version=2 FOR UPDATE NOWAIT;
  IF NOT FOUND THEN RAISE EXCEPTION 'NATIVE_V3_SERVED_PARENT_REQUIRED'; END IF;
  PERFORM a.source_rank FROM ml.recommendation_served_item a
   WHERE a.recommendation_request_id=p_request ORDER BY a.source_rank LIMIT 101 FOR SHARE NOWAIT;
  p_role:='SERVED';
 ELSIF current_setting('transaction_isolation')<>'repeatable read'
  AND NOT (current_setting('transaction_isolation')='read committed'
   AND EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
    WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=p_owner)
   AND app_private.privacy_owner_allowed(p_owner)) THEN
  RAISE EXCEPTION 'NATIVE_V3_ITEM_MEASURER_TRANSACTION' USING ERRCODE='23514'; END IF;
 v_frame:=app_private.native_v3_compiler_enter(524288);
 IF p_owner IS NULL OR p_request IS NULL OR p_role IS NULL OR p_role NOT IN ('ORIGINAL','SERVED')
  OR NOT EXISTS(SELECT 1 FROM ml.recommendation_request q WHERE q.user_id=p_owner
   AND q.recommendation_request_id=p_request
   AND q.serving_contract_version=CASE p_role WHEN 'ORIGINAL' THEN 1 ELSE 2 END) THEN
  RAISE EXCEPTION 'NATIVE_V3_ITEM_ROW_ROLE' USING ERRCODE='23514'; END IF;
 IF p_role='ORIGINAL' THEN
  SELECT count(*) INTO v_count FROM (SELECT 1 FROM ml.recommendation_item a
   WHERE a.recommendation_request_id=p_request LIMIT 101) bounded;
 ELSE
  SELECT count(*) INTO v_count FROM (SELECT 1 FROM ml.recommendation_served_item a
   WHERE a.recommendation_request_id=p_request LIMIT 101) bounded;
 END IF;
 IF v_count>100 THEN RAISE EXCEPTION 'NATIVE_V3_ITEM_ROW_COUNT' USING ERRCODE='23514'; END IF;
 v_lp_role:=CASE p_role WHEN 'ORIGINAL' THEN 'p11-item' ELSE 'served-item' END;
 v_schema:=app_private.native_v3_role_lp_schema(v_lp_role);
 -- APIR3 NUL + role O/S + ownerUUID16 + requestUUID16 + u32N =43 bytes,
 -- including N=0. There is no separately callable JSONB projection. This real
 -- function owns its512KiB leaf/encoder slot before any per-row allocation,
 -- and drops every row, leaf and encoded result before closing either frame.
 FOR v_rank IN 1..v_count LOOP
  v_row_frame:=app_private.native_v3_compiler_enter(1048576);
  v_contributions:='[]'::jsonb;
  IF p_role='ORIGINAL' THEN
   IF NOT EXISTS(SELECT 1 FROM ml.recommendation_item a WHERE a.recommendation_request_id=p_request
    AND a.rank=v_rank AND pg_column_compression(a.contributions) IS NULL
    AND pg_column_compression(a.availability_snapshot) IS NULL
    AND pg_column_compression(a.item_provenance) IS NULL
    AND pg_column_compression(a.candidate_sources) IS NULL
    AND pg_column_compression(a.reason_codes) IS NULL
    AND pg_column_compression(a.score) IS NULL
    AND pg_column_size(a.contributions)<=65536 AND pg_column_size(a.availability_snapshot)<=8192
    AND pg_column_size(a.item_provenance)<=16384 AND pg_column_size(a.candidate_sources)<=65536
    AND pg_column_size(a.reason_codes)<=65536 AND pg_column_size(a.score)<=65536
    AND octet_length(a.explanation_code)<=4096) THEN
    RAISE EXCEPTION 'NATIVE_V3_ITEM_ROW_BOUND' USING ERRCODE='23514'; END IF;
   SELECT a.rank,a.recording_id,a.score,a.candidate_sources,a.explanation_code,
    a.availability_snapshot,a.contributions,a.reason_codes,a.item_provenance INTO v_row
    FROM ml.recommendation_item a WHERE a.recommendation_request_id=p_request AND a.rank=v_rank;
   v_units:=app_private.native_v3_item_e8_v3(v_row.score);
  ELSE
   IF NOT EXISTS(SELECT 1 FROM ml.recommendation_served_item a
    WHERE a.recommendation_request_id=p_request AND a.source_rank=v_rank
     AND pg_column_compression(a.contributions) IS NULL AND pg_column_compression(a.reasons) IS NULL
     AND pg_column_size(a.contributions)<=65536 AND pg_column_size(a.reasons)<=65536
     AND octet_length(a.score_kind)<=32 AND octet_length(a.attribution_source)<=32
     AND octet_length(a.origin)<=32) THEN
    RAISE EXCEPTION 'NATIVE_V3_ITEM_ROW_BOUND' USING ERRCODE='23514'; END IF;
   SELECT a.source_rank,a.recording_id,a.score_units_e8,a.score_kind,a.attribution_source,
    a.reasons,a.contributions,a.original_p11_rank,a.origin INTO v_row
    FROM ml.recommendation_served_item a WHERE a.recommendation_request_id=p_request
     AND a.source_rank=v_rank;
   v_units:=v_row.score_units_e8;
  END IF;
  IF jsonb_typeof(v_row.contributions) IS DISTINCT FROM 'array'
   OR jsonb_array_length(v_row.contributions)>5 THEN
   RAISE EXCEPTION 'NATIVE_V3_ITEM_CONTRIBUTIONS' USING ERRCODE='23514'; END IF;
  v_contribution_count:=jsonb_array_length(v_row.contributions);
  FOR v_idx IN 0..v_contribution_count-1 LOOP
   v_part:=v_row.contributions->v_idx; v_source:=v_part->>'source_key';
   v_kind:=CASE p_role WHEN 'ORIGINAL' THEN 'raw_score' ELSE 'raw_score_units_e8' END;
   IF jsonb_typeof(v_part) IS DISTINCT FROM 'object'
    OR (SELECT count(*) FROM jsonb_object_keys(v_part))<>5
    OR NOT (v_part ?& ARRAY['source_key','source_version','source_rank','provenance',v_kind]) THEN
    RAISE EXCEPTION 'NATIVE_V3_ITEM_CONTRIBUTIONS' USING ERRCODE='23514'; END IF;
   v_fields:=app_private.native_v3_role_schema()->v_source; v_provenance:=v_part->'provenance';
   IF v_source NOT IN ('preferences','history','artist_release_metadata','freshness','exploration')
    OR v_fields IS NULL OR jsonb_typeof(v_provenance) IS DISTINCT FROM 'object'
    OR (SELECT count(*) FROM jsonb_object_keys(v_provenance))<>
     (SELECT count(*) FROM jsonb_object_keys(v_fields))
    OR EXISTS(SELECT 1 FROM jsonb_object_keys(v_fields) f(key) WHERE NOT v_provenance ? f.key) THEN
    RAISE EXCEPTION 'NATIVE_V3_ITEM_PROVENANCE' USING ERRCODE='23514'; END IF;
   IF p_role='ORIGINAL' AND jsonb_typeof(v_part->'raw_score') IS DISTINCT FROM 'number' THEN
    RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_ITEM_E8_GRID' USING ERRCODE='23514'; END IF;
   FOR v_field,v_code IN SELECT f.key,f.value FROM jsonb_each_text(v_fields) f LOOP
    IF left(v_code,1) IN ('I','Z','P','L','E','R','T','N')
     AND v_provenance->v_field IS DISTINCT FROM 'null'::jsonb THEN
     v_provenance:=jsonb_set(v_provenance,ARRAY[v_field],to_jsonb(
      app_private.native_v3_item_integer_v3(v_provenance->v_field)));
    END IF;
   END LOOP;
   v_contributions:=v_contributions||jsonb_build_array(jsonb_build_object(
    'source_key',v_part->'source_key','source_version',v_part->'source_version',
    'source_rank',app_private.native_v3_item_integer_v3(v_part->'source_rank'),
    'raw_score_units_e8',CASE p_role WHEN 'ORIGINAL' THEN
     app_private.native_v3_item_e8_v3((v_part->'raw_score')::numeric)
     ELSE app_private.native_v3_item_integer_v3(v_part->'raw_score_units_e8') END,
    'provenance',v_provenance));
  END LOOP;
  IF p_role='ORIGINAL' THEN
   v_leaf:=jsonb_build_object('rank',v_row.rank,'recording_id',v_row.recording_id,
    'score_units_e8',v_units,'candidate_sources',v_row.candidate_sources,
    'explanation_code',v_row.explanation_code,'availability_snapshot',v_row.availability_snapshot,
    'contributions',v_contributions,'reason_codes',v_row.reason_codes,'item_provenance',v_row.item_provenance);
  ELSE
   v_leaf:=jsonb_build_object('source_rank',v_row.source_rank,'recording_id',v_row.recording_id,
    'score_units_e8',v_units,'score_kind',v_row.score_kind,'attribution_source',v_row.attribution_source,
    'reasons',v_row.reasons,'contributions',v_contributions,'original_p11_rank',v_row.original_p11_rank,
    'origin',v_row.origin);
  END IF;
  -- The LP bound precedes closed-role FunctionScans and every old encoder call.
  PERFORM app_private.native_v3_role_hash(
   CASE p_role WHEN 'ORIGINAL' THEN 'p11-item' ELSE 'served-item' END,v_leaf);
  IF pg_column_size(v_leaf)>196640 THEN
   RAISE EXCEPTION 'NATIVE_V3_ITEM_OUTPUT_BOUND' USING ERRCODE='23514'; END IF;
  PERFORM app_private.native_v3_lp_size(v_leaf,v_schema);
  v_encoded:=app_private.native_served_json_lp(v_leaf,v_schema);
  v_length:=v_length+4+octet_length(v_encoded);
  IF v_length>2097152 THEN
   RAISE EXCEPTION 'NATIVE_V3_ITEM_ROWS_BYTES' USING ERRCODE='23514'; END IF;
  v_row:=NULL; v_contributions:=NULL; v_part:=NULL; v_provenance:=NULL; v_fields:=NULL;
  v_source:=NULL; v_code:=NULL; v_field:=NULL; v_kind:=NULL;
  v_encoded:=NULL; v_leaf:=NULL;
  PERFORM app_private.native_v3_compiler_exit(v_row_frame);
 END LOOP;
 v_schema:=NULL; v_lp_role:=NULL; v_leaf:=NULL; v_encoded:=NULL;
 PERFORM app_private.native_v3_compiler_exit(v_frame);
 RETURN v_length;
END $$;
REVOKE ALL ON FUNCTION app_private.native_v3_item_integer_v3(jsonb),
 app_private.native_v3_item_e8_v3(numeric),
 app_private.native_v3_item_rows_byte_length(uuid,uuid,text) FROM PUBLIC;
"""


def _requirements() -> str:
    checks = [
        f"IF to_regprocedure('{name}') IS NULL THEN "
        "RAISE EXCEPTION 'NATIVE_V3_WRITER_REAL_HELPER_REQUIRED' USING ERRCODE='55000'; END IF;"
        for name in NATIVE_REQUEST_V3_WRITER_REQUIRED_FUNCTIONS
    ]
    for name in (
        "native_original_context_v3",
        "native_original_insert_witness_v3",
        "native_request_write_context_v3",
    ):
        checks.append(
            f"IF to_regclass('app_private.{name}') IS NULL THEN "
            "RAISE EXCEPTION 'NATIVE_V3_WRITER_CONTEXT_REQUIRED' USING ERRCODE='55000'; END IF;"
        )
    for table in NATIVE_REQUEST_V3_WRITER_DISPATCH_TRIGGER_TABLES:
        checks.append(
            "IF NOT EXISTS(SELECT 1 FROM pg_trigger t "
            f"WHERE t.tgrelid=to_regclass('ml.{table}') "
            "AND t.tgname='z_native_served_immutable' AND t.tgtype=31 "
            "AND t.tgenabled='O' AND NOT t.tgisinternal AND t.tgfoid="
            "to_regprocedure('app_private.protect_native_served_authority_dispatch_v3()')) "
            "THEN RAISE EXCEPTION 'NATIVE_V3_WRITER_DISPATCH_REQUIRED' "
            "USING ERRCODE='55000'; END IF;"
        )
    return "DO $$ BEGIN\n" + "\n".join(checks) + "\nEND $$;\n"


NATIVE_REQUEST_V3_ITEM_ROWS_INSTALL_SQL: Final = _ITEM_ROWS_SQL
NATIVE_REQUEST_V3_ITEM_ROWS_DROP_SQL: Final = r"""
DROP FUNCTION app_private.native_v3_item_rows_byte_length(uuid,uuid,text);
DROP FUNCTION app_private.native_v3_item_e8_v3(numeric);
DROP FUNCTION app_private.native_v3_item_integer_v3(jsonb);
"""
NATIVE_REQUEST_V3_SOURCE_DEADLINE_INSTALL_SQL: Final = r"""
CREATE FUNCTION app_private.native_v3_source_deadline_from_ms_v3(p_ms bigint) RETURNS timestamptz
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF p_ms IS NULL OR p_ms NOT BETWEEN 0 AND 9007199254740991 THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_DEADLINE' USING ERRCODE='23514'; END IF;
 -- Each seconds/millisecond multiplication is exact. Timestamp without timezone
 -- avoids session-DST shifts when adding the integer day component.
 RETURN (timestamp 'epoch'+make_interval(days=>(p_ms/86400000)::integer)
  +((p_ms%86400000)/1000)*interval '1 second'
  +(p_ms%1000)*interval '1 millisecond') AT TIME ZONE 'UTC';
END $$;
REVOKE ALL ON FUNCTION app_private.native_v3_source_deadline_from_ms_v3(bigint) FROM PUBLIC;
"""

NATIVE_REQUEST_V3_WRITER_INSTALL_SQL: Final = (
    _requirements()
    + NATIVE_REQUEST_V3_SOURCE_DEADLINE_INSTALL_SQL
    + r"""
CREATE FUNCTION app_private.commit_native_atomic_capture_v3(
 p_original uuid,p_served uuid,p_capture bytea,p_root bytea,p_request bytea,
 p_baseline bytea,p_ranking bytea,p_profile bytea,p_pipeline bytea,p_profile_binary64_sha bytea)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_ctx app_private.native_original_context_v3%ROWTYPE; v_original record; v_witness record;
 v_baseline record; v_temporal_meta record; v_created timestamptz; v_expires timestamptz;
 v_original_availability text; v_referenced_snapshot uuid;
 v_created_us bigint; v_original_json_bytes bigint; v_original_compression text;
 v_expires_us bigint; v_raw_total bigint; v_peak bigint; v_scan bigint[]; v_cap_stat bigint[];
 v_profile_stat bigint[]; v_base_stat bigint[]; v_req_stat bigint[]; v_rank_stat bigint[];
 v_capture jsonb; v_temporal jsonb; v_profile jsonb; v_dimensions jsonb; v_facts jsonb;
 v_proof jsonb; v_header jsonb; v_body jsonb; v_metadata jsonb; v_items jsonb; v_ranking jsonb;
 v_leaf jsonb; v_served_leaf jsonb; v_fact jsonb; v_causal jsonb;
 v_baseline_id uuid; v_temporal_id uuid; v_owner uuid; v_count integer; v_idx integer;
 v_dimension_count integer; v_index_length bigint; v_cursor_length integer;
 v_original_count integer;
 v_event uuid; v_previous_event uuid; v_retained_ms bigint; v_retained_us bigint;
 v_entry bytea; v_part bytea; v_old_profile_sha bytea; v_calculated bytea;
 v_capture_sha bytea; v_root_sha bytea; v_root_proof_sha bytea; v_origin_sha bytea;
 v_decision_sha bytea; v_consent_generation bigint; v_cutoff bigint;
 v_source_membership bytea; v_source_count integer; v_evidence_count integer;
 v_original_items_bytes integer; v_served_items_bytes integer;
 v_exploration integer; v_raw_frame integer; v_phase_frame integer;
 v_sources text[]; v_reasons text[]; v_contributions_sha bytea; v_reasons_sha bytea;
BEGIN
 -- Metadata-only checks precede every complete JSON/UTF8 materialization.
 IF p_original IS NULL OR p_served IS NULL OR p_original=p_served
  OR p_capture IS NULL OR octet_length(p_capture) NOT BETWEEN 1 AND 2097152
  OR p_root IS NULL OR octet_length(p_root) NOT BETWEEN 1 AND 2097152
  OR p_request IS NULL OR octet_length(p_request) NOT BETWEEN 1 AND 2097152
  OR p_baseline IS NULL OR octet_length(p_baseline) NOT BETWEEN 1 AND 4194304
  OR p_ranking IS NULL OR octet_length(p_ranking) NOT BETWEEN 1 AND 2097152
  OR p_profile IS NULL OR octet_length(p_profile) NOT BETWEEN 1 AND 2097152
  OR p_pipeline IS NULL OR octet_length(p_pipeline) NOT BETWEEN 1 AND 65536
  OR p_profile_binary64_sha IS NULL OR octet_length(p_profile_binary64_sha)<>32 THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_INPUT' USING ERRCODE='23514'; END IF;
 v_raw_total:=octet_length(p_capture)::bigint+octet_length(p_root)+octet_length(p_request)
  +octet_length(p_baseline)+octet_length(p_ranking)+octet_length(p_profile)+octet_length(p_pipeline);
 -- Incoming arguments and the actual bounded metadata/output slot remain owned
 -- through all nested current/graph assertions, on the shared tx/backend stack.
 v_raw_frame:=app_private.native_v3_compiler_enter(v_raw_total+262144);
 IF current_setting('transaction_isolation')<>'repeatable read' THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_TRANSACTION' USING ERRCODE='23514'; END IF;
 SELECT c.* INTO v_ctx FROM app_private.native_original_context_v3 c
  WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid();
 IF v_ctx.owner_user_id IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_ORIGIN_REQUIRED' USING ERRCODE='23514'; END IF;
 v_owner:=v_ctx.owner_user_id;
 PERFORM app_private.check_native_served_identity(v_owner,v_ctx.device_id,v_ctx.journal_epoch,
  v_ctx.server_instance_id,v_ctx.identity_epoch);
 SELECT w.baseline_snapshot_id,w.temporal_snapshot_id,w.created_at INTO v_witness
 FROM app_private.native_original_insert_witness_v3 w
 WHERE w.transaction_id=pg_current_xact_id() AND w.backend_pid=pg_backend_pid()
  AND w.owner_user_id=v_owner AND w.table_name='recommendation_request'
  AND w.row_id=p_original AND w.serving_contract_version=1;
 IF v_witness.baseline_snapshot_id IS NULL OR v_witness.temporal_snapshot_id IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_ACTUAL_INSERT_REQUIRED' USING ERRCODE='23514'; END IF;
 v_baseline_id:=v_witness.baseline_snapshot_id; v_temporal_id:=v_witness.temporal_snapshot_id;
 v_created:=v_witness.created_at; v_expires:=v_created+interval '15552000 seconds';
 v_created_us:=(extract(epoch FROM v_created)*1000000)::bigint;
 v_expires_us:=(extract(epoch FROM v_expires)*1000000)::bigint;
 SELECT q.user_id,q.serving_contract_version,q.request_sha256,q.created_at,
  q.recommendation_input_snapshot_id,q.recommendation_temporal_snapshot_id INTO v_original
 FROM ml.recommendation_request q WHERE q.recommendation_request_id=p_original FOR SHARE;
 SELECT b.user_id,b.input_snapshot_sha256,b.policy_snapshot_sha256,
  octet_length(b.availability_snapshot) AS availability_bytes,
  b.created_at,pg_column_size(b.snapshot_document) AS logical_bytes,
  pg_column_compression(b.snapshot_document) AS compression INTO v_baseline
 FROM ml.recommendation_input_snapshot b
 WHERE b.recommendation_input_snapshot_id=v_baseline_id FOR SHARE;
 IF v_original.user_id IS DISTINCT FROM v_owner OR v_original.serving_contract_version<>1
  OR v_original.created_at IS DISTINCT FROM v_created
  OR v_original.recommendation_input_snapshot_id IS DISTINCT FROM v_baseline_id
  OR v_original.recommendation_temporal_snapshot_id IS DISTINCT FROM v_temporal_id
  OR v_baseline.user_id IS DISTINCT FROM v_owner OR v_baseline.created_at<>v_created
  OR v_baseline.availability_bytes IS DISTINCT FROM 64
  OR v_baseline.compression IS NOT NULL OR v_baseline.logical_bytes NOT BETWEEN 1 AND 8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_ORDINARY_BINDING' USING ERRCODE='23514'; END IF;
 SELECT b.availability_snapshot INTO v_original_availability
  FROM ml.recommendation_input_snapshot b
  WHERE b.recommendation_input_snapshot_id=v_baseline_id;
 SELECT tm.user_id,tm.created_at,pg_column_size(tm.snapshot_document) AS logical_bytes,
  pg_column_compression(tm.snapshot_document) AS compression INTO v_temporal_meta
 FROM ml.recommendation_temporal_snapshot tm
 WHERE tm.recommendation_temporal_snapshot_id=v_temporal_id FOR SHARE;
 SELECT pg_column_size(q.request_document),pg_column_compression(q.request_document)
  INTO v_original_json_bytes,v_original_compression FROM ml.recommendation_request q
  WHERE q.recommendation_request_id=p_original;
 IF v_temporal_meta.user_id IS DISTINCT FROM v_owner
  OR v_temporal_meta.created_at IS DISTINCT FROM v_created
  OR v_temporal_meta.compression IS NOT NULL
  OR v_temporal_meta.logical_bytes NOT BETWEEN 1 AND 8388608
  OR v_original_compression IS NOT NULL OR v_original_json_bytes NOT BETWEEN 1 AND 8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_ORDINARY_BOUND' USING ERRCODE='23514'; END IF;
 IF v_raw_total+1048576>8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
 -- Fixed manifest equality/locked actual registry, with no whole caller JSON cast.
 PERFORM app_private.native_v3_assert_current_baseline_pipeline(p_pipeline);
 v_phase_frame:=app_private.native_v3_compiler_enter(1048576);
 v_cap_stat:=app_private.native_source3_preflight_v3(p_capture,
  144*octet_length(p_capture)::bigint+16777216,16*(octet_length(p_capture)::bigint+1));
 v_scan:=app_private.native_v3_wire_value(p_root,0,'$',0);
 IF v_scan[1]<>octet_length(p_root) THEN
  RAISE EXCEPTION 'NATIVE_V3_RAW_TRAILING' USING ERRCODE='23514'; END IF;
 PERFORM app_private.native_v3_compiler_exit(v_phase_frame);
 v_req_stat:=app_private.native_original_document_preflight_v3(p_request);
 v_base_stat:=app_private.native_original_baseline_preflight_v4(p_baseline);
 v_rank_stat:=app_private.native_original_document_preflight_v3(p_ranking);
 v_profile_stat:=app_private.native_original_profile_preflight_v3(p_profile,
  144*octet_length(p_profile)::bigint+16777216,16*(octet_length(p_profile)::bigint+1));
 -- Include incoming raw arguments through nested final assertions. Root, source,
 -- independent profile and old-row projections occupy separate bounded phases.
 v_peak:=v_raw_total+greatest(3*octet_length(p_root)::bigint+32*v_scan[3]+1048576,
  3*octet_length(p_capture)::bigint+4*v_cap_stat[4]+v_profile_stat[4]+1048576,
  265536+CASE WHEN octet_length(p_baseline)>2097152 THEN
   greatest(2*v_baseline.logical_bytes+65536,v_baseline.logical_bytes+524288,
    octet_length(p_baseline)::bigint+65536)
   ELSE 2*v_baseline.logical_bytes+2*v_base_stat[1]+v_base_stat[3]+1048576 END,
  octet_length(p_capture)::bigint+v_cap_stat[4]+2*v_temporal_meta.logical_bytes+1048576,
  octet_length(p_capture)::bigint+v_cap_stat[4]+2*v_req_stat[1]+2*v_req_stat[3]+1048576,
  octet_length(p_capture)::bigint+v_cap_stat[4]+2*v_original_json_bytes+2*v_req_stat[3]+1048576,
  octet_length(p_capture)::bigint+v_cap_stat[4]+2*v_rank_stat[1]+2*v_rank_stat[3]+1048576);
 IF v_peak>8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
 v_phase_frame:=app_private.native_v3_compiler_enter(65536);
 PERFORM app_private.native_v3_compiler_grow(v_phase_frame,
  2*v_rank_stat[1]+v_rank_stat[3]+65536);
 v_ranking:=convert_from(p_ranking,'UTF8')::jsonb;
 IF jsonb_typeof(v_ranking) IS DISTINCT FROM 'object'
  OR v_ranking->>'kind' IS DISTINCT FROM 'RECOMMENDATION_RANKING_V1'
  OR v_ranking->'schema_version' IS DISTINCT FROM '1'::jsonb
  OR jsonb_typeof(v_ranking->'items') IS DISTINCT FROM 'array'
  OR (SELECT count(*) FROM jsonb_object_keys(v_ranking))<>3 THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_RANKING' USING ERRCODE='23514'; END IF;
 v_original_count:=jsonb_array_length(v_ranking->'items'); v_ranking:=NULL;
 PERFORM app_private.native_v3_compiler_exit(v_phase_frame);
 IF v_original_count NOT BETWEEN 0 AND 100 THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_RANKING' USING ERRCODE='23514'; END IF;
 IF (SELECT count(*) FROM (SELECT 1 FROM ml.recommendation_item a
   WHERE a.recommendation_request_id=p_original LIMIT 101) bounded)<>v_original_count THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_ORIGINAL_ITEM_COUNT' USING ERRCODE='23514'; END IF;
 v_original_items_bytes:=app_private.native_v3_item_rows_byte_length(v_owner,p_original,'ORIGINAL');
 IF octet_length(p_profile)-(v_profile_stat[9]-v_profile_stat[8])>524288 THEN
  RAISE EXCEPTION 'NATIVE_V3_SQL_PREIMAGE_BOUND' USING ERRCODE='23514'; END IF;
 v_phase_frame:=app_private.native_v3_compiler_enter(1114112);
 v_part:=substring(p_profile FROM 1 FOR v_profile_stat[8]::integer)||
  substring(p_profile FROM v_profile_stat[9]::integer+1);
 v_old_profile_sha:=sha256(v_part); v_part:=NULL;
 IF substring(p_profile FROM v_profile_stat[10]::integer+1 FOR 64) IS DISTINCT FROM
  convert_to(encode(v_old_profile_sha,'hex'),'UTF8') THEN
  RAISE EXCEPTION 'NATIVE_V3_PROFILE_SELF_HASH' USING ERRCODE='23514'; END IF;
 PERFORM app_private.native_v3_compiler_exit(v_phase_frame);
 v_phase_frame:=app_private.native_v3_compiler_enter(65536);
 PERFORM app_private.native_v3_compiler_grow(v_phase_frame,
  3*octet_length(p_capture)::bigint+4*v_cap_stat[4]+v_profile_stat[4]+1048576);
 v_capture:=convert_from(p_capture,'UTF8')::jsonb;
 v_temporal:=v_capture->'temporal_snapshot'; v_profile:=v_temporal->'adaptive_profile';
 v_dimensions:=v_profile->'dimensions'; v_dimension_count:=jsonb_array_length(v_dimensions);
 v_source_membership:=decode(v_temporal->>'source_membership_sha256','hex');
 v_calculated:=app_private.native_profile_binary64_digest_v3(v_profile,v_dimensions,
  v_source_membership,v_old_profile_sha,
  64*(pg_column_size(v_profile)::bigint+pg_column_size(v_dimensions)::bigint)
   +128*(293+1118::bigint*v_dimension_count)+65536,4096+2048::bigint*(v_dimension_count+1));
 IF v_calculated IS DISTINCT FROM p_profile_binary64_sha THEN
  RAISE EXCEPTION 'NATIVE_V3_PROFILE_BINARY64_BINDING' USING ERRCODE='23514'; END IF;
 v_profile:=NULL; v_dimensions:=NULL;
 v_capture_sha:=sha256(p_capture); v_consent_generation:=(v_capture->>'consent_generation')::bigint;
 v_cutoff:=(v_capture->>'cutoff_at_ms')::bigint;
 IF v_capture->>'recommendation_request_id' IS DISTINCT FROM p_original::text
  OR v_capture->>'owner_user_id' IS DISTINCT FROM v_owner::text
  OR (v_capture->>'baseline_snapshot_id')::uuid IS DISTINCT FROM v_baseline_id
  OR (v_capture->>'temporal_snapshot_id')::uuid IS DISTINCT FROM v_temporal_id
  OR decode(v_capture->>'request_sha256','hex') IS DISTINCT FROM sha256(p_request)
  OR v_original.request_sha256 IS DISTINCT FROM sha256(p_request)
  OR decode(v_capture->>'baseline_snapshot_sha256','hex') IS DISTINCT FROM sha256(p_baseline)
  OR v_baseline.input_snapshot_sha256 IS DISTINCT FROM sha256(p_baseline)
  OR decode(v_capture->>'p11_ranking_sha256','hex') IS DISTINCT FROM sha256(p_ranking)
  OR decode(v_capture->>'pipeline_manifest_sha256','hex') IS DISTINCT FROM sha256(p_pipeline)
  OR v_cutoff<>floor(extract(epoch FROM v_created)*1000)
  OR v_created>clock_timestamp() OR v_expires<=clock_timestamp() THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_CAPTURE_BINDING' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM account.training_consent tc WHERE tc.user_id=v_owner
  AND tc.decision='GRANTED' AND tc.policy_version=1 AND tc.revision=v_consent_generation
  FOR SHARE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_CURRENT_CONSENT' USING ERRCODE='23514'; END IF;
 -- This private CREATE row is derived from the protected actual INSERT witness,
 -- before the served parent exists; no caller-selected version or mode enters.
 INSERT INTO app_private.native_request_write_context_v3(transaction_id,backend_pid,
  owner_user_id,original_request_id,served_request_id,operation,device_id,journal_epoch,
  server_instance_id,identity_epoch,started_at)
 VALUES(pg_current_xact_id(),pg_backend_pid(),v_owner,p_original,p_served,'CREATE',
  v_ctx.device_id,v_ctx.journal_epoch,v_ctx.server_instance_id,v_ctx.identity_epoch,v_ctx.started_at);
 INSERT INTO ml.native_original_capture_format VALUES(
  p_original,v_owner,3,pg_current_xact_id(),v_created,v_expires);
 v_facts:=v_temporal->'facts'; v_source_count:=jsonb_array_length(v_facts);
 v_evidence_count:=jsonb_array_length(v_temporal->'source_evidence');
 v_index_length:=octet_length(app_private.native_v3_source_index_prefix(v_source_count));
 -- One bounded row at a time. No aggregate source descriptor/preimage is built.
 FOR v_idx IN 0..v_source_count-1 LOOP
  v_fact:=v_facts->v_idx; v_causal:=v_fact->'causality';
  v_event:=(v_fact->>'event_id')::uuid; v_retained_ms:=(v_fact->>'retained_until_ms')::bigint;
  IF v_previous_event IS NOT NULL AND v_event<=v_previous_event THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_ORDER' USING ERRCODE='23514'; END IF;
  v_previous_event:=v_event;
  IF v_retained_ms>9223372036854775 THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_DEADLINE' USING ERRCODE='23514'; END IF;
  v_retained_us:=v_retained_ms*1000;
  v_entry:=app_private.native_v3_source_index_entry(p_original,v_owner,v_event,
   decode(v_fact->>'source_request_sha256','hex'),decode(v_fact->>'normalized_payload_sha256','hex'),
   (v_fact->>'device')::uuid,(v_fact->>'journal_epoch')::uuid,
   (v_fact->>'device_sequence')::bigint,(v_fact->>'server_sequence')::bigint,
   v_fact->>'event_type',(v_causal->>'association_id')::uuid,
   (v_causal->>'request_id')::uuid,(v_causal->>'impression_event_id')::uuid,v_retained_us);
  IF octet_length(v_entry)>512 THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_ROW_BOUND' USING ERRCODE='23514'; END IF;
  v_index_length:=v_index_length+octet_length(v_entry); v_entry:=NULL;
 END LOOP;
 IF v_index_length>2097152 THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_INDEX_BOUND' USING ERRCODE='23514'; END IF;
 INSERT INTO ml.native_original_capture_v3(original_request_id,owner_user_id,
  original_capture_format_version,source_codec,source_codec_sha256,bundle_document,bundle_sha256,
  request_sha256,pipeline_manifest_sha256,baseline_snapshot_sha256,temporal_snapshot_sha256,
  candidate_membership_sha256,original_ranking_sha256,feature_policy_sha256,
  eligibility_policy_sha256,consent_receipt_sha256,baseline_snapshot_id,temporal_snapshot_id,
  consent_generation,cutoff_at_ms,interaction_watermark,native_interaction_watermark,
  source_snapshot_track_count,candidate_universe_count,p11_item_count,source_event_count,
  source_evidence_count,profile_dimension_count,ineligibility_reason,source_index_byte_length,
  created_transaction,created_at,expires_at)
 VALUES(p_original,v_owner,3,'NATIVE_SOURCE3_F64BITS_JSON_SAFE53_V1',decode(
  'eda9439b265c5e92934f8f62a333782579a14aed116385271f158819045581a2','hex'),p_capture,v_capture_sha,
  sha256(p_request),sha256(p_pipeline),sha256(p_baseline),
  decode(v_capture->>'temporal_snapshot_sha256','hex'),
  decode(v_capture->>'candidate_membership_sha256','hex'),sha256(p_ranking),
  decode(v_capture->>'feature_policy_sha256','hex'),app_private.native_p11_source_policy(sha256(p_pipeline)),
  decode(v_capture->>'consent_receipt_sha256','hex'),v_baseline_id,v_temporal_id,
  v_consent_generation,v_cutoff,(v_capture->>'interaction_watermark')::bigint,
  (v_capture->>'native_interaction_watermark')::bigint,
  (v_capture->>'source_snapshot_track_count')::integer,
  (v_capture->>'candidate_universe_count')::integer,
  v_original_count,v_source_count,v_evidence_count,v_dimension_count,v_capture->>'ineligibility_reason',
  v_index_length,pg_current_xact_id(),v_created,v_expires);
 FOR v_idx IN 0..v_source_count-1 LOOP
  v_fact:=v_facts->v_idx; v_causal:=v_fact->'causality';
  v_retained_ms:=(v_fact->>'retained_until_ms')::bigint;
  INSERT INTO ml.native_capture_source_event_v3(original_request_id,source_event_id,
   owner_user_id,source_request_sha256,normalized_payload_sha256,device_id,journal_epoch,
   device_sequence,server_sequence,event_type,association_id,causal_request_id,
   impression_event_id,retained_until)
  VALUES(p_original,(v_fact->>'event_id')::uuid,v_owner,
   decode(v_fact->>'source_request_sha256','hex'),decode(v_fact->>'normalized_payload_sha256','hex'),
   (v_fact->>'device')::uuid,(v_fact->>'journal_epoch')::uuid,
   (v_fact->>'device_sequence')::bigint,(v_fact->>'server_sequence')::bigint,v_fact->>'event_type',
   (v_causal->>'association_id')::uuid,(v_causal->>'request_id')::uuid,
   (v_causal->>'impression_event_id')::uuid,
   app_private.native_v3_source_deadline_from_ms_v3(v_retained_ms));
 END LOOP;
 v_fact:=NULL; v_causal:=NULL; v_facts:=NULL;
 v_cursor_length:=octet_length(app_private.native_v3_source_cursor(p_original,v_owner,
  v_source_count,v_source_membership,v_cutoff,(v_capture->>'native_interaction_watermark')::bigint,
  1,pg_current_xact_id()::text,v_created_us,v_expires_us));
 INSERT INTO ml.native_capture_lineage_cursor_v3 VALUES(p_original,v_owner,'ACTIVE',v_source_count,
  v_source_membership,v_cutoff,(v_capture->>'native_interaction_watermark')::bigint,
  1,v_cursor_length,pg_current_xact_id(),v_created,v_expires);
 v_temporal:=NULL; v_capture:=NULL;
 PERFORM app_private.native_v3_compiler_exit(v_phase_frame);
 -- Root hydration is a separate compiler phase from the source tree.
 v_phase_frame:=app_private.native_v3_compiler_enter(65536);
 PERFORM app_private.native_v3_compiler_grow(v_phase_frame,
  3*octet_length(p_root)::bigint+32*v_scan[3]+1048576);
 -- v_scan already admitted these immutable bytes before the hydration phase.
 v_proof:=convert_from(p_root,'UTF8')::jsonb;
 v_header:=v_proof->'root_header'; v_body:=v_proof->'body';
 IF v_proof->'proof_version'='4'::jsonb THEN v_referenced_snapshot:=v_baseline_id; END IF;
 v_metadata:=v_body->'metadata'; v_items:=v_body->'p11_items';
 v_exploration:=(v_body->'request_features'->>'exploration_units_e8')::integer;
 v_root_sha:=decode(v_proof->>'root_sha256','hex'); v_root_proof_sha:=sha256(p_root);
 v_origin_sha:=decode(v_header->>'original_capture_binding_sha256','hex');
 v_count:=jsonb_array_length(v_items);
 IF v_body->>'request_id' IS DISTINCT FROM p_served::text
  OR v_body->'identity' IS DISTINCT FROM jsonb_build_object('owner_user_id',v_owner,
   'device_id',v_ctx.device_id,'journal_epoch',v_ctx.journal_epoch,
   'server_instance_id',v_ctx.server_instance_id,'identity_epoch',v_ctx.identity_epoch)
  OR (v_metadata->>'cutoff_at_ms')::bigint IS DISTINCT FROM v_cutoff
  OR v_count<>v_original_count THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_ROOT_BINDING' USING ERRCODE='23514'; END IF;
 v_proof:=NULL; v_body:=NULL;
 INSERT INTO ml.recommendation_request(recommendation_request_id,user_id,context,surface,
  serving_contract_version,pipeline_key,pipeline_version,pipeline_manifest_sha256,
  request_schema_version,request_canonicalization_version,request_sha256,
  recommendation_input_snapshot_id,input_snapshot_sha256,interaction_watermark,catalog_snapshot,
  availability_snapshot_ref,policy_snapshot_sha256,recommendation_temporal_snapshot_id,
  temporal_snapshot_sha256,adaptive_feature_policy_sha256,shadow,model_bundle_version,
  candidate_policy_version,filter_policy_version,reranker_version,seed,request_features,
  request_document,created_at)
 SELECT p_served,v_owner,v_metadata->>'context',v_metadata->>'surface',2,
  v_metadata->>'pipeline_key',v_metadata->>'pipeline_version',sha256(p_pipeline),2,1,
  decode(v_header->>'query_document_sha256','hex'),v_baseline_id,
  decode(v_header->>'snapshot_tracks_sha256','hex'),(v_metadata->>'interaction_watermark')::bigint,
  (v_metadata->>'catalog_snapshot')::bigint,v_header->>'availability_tracks_sha256',
  decode(v_header->>'policy_tracks_sha256','hex'),v_temporal_id,b.temporal_snapshot_sha256,
  b.feature_policy_sha256,false,v_metadata->>'model_bundle_version',
  v_metadata->>'candidate_policy_version',v_metadata->>'filter_policy_version',
  v_metadata->>'reranker_version',(v_metadata->>'seed')::bigint,'{}'::jsonb,NULL,v_created
 FROM ml.native_original_capture_v3 b WHERE b.original_request_id=p_original;
 INSERT INTO ml.native_served_request_hash_format VALUES(
  p_served,v_owner,3,pg_current_xact_id(),v_created,v_expires);
 FOR v_idx IN 0..v_count-1 LOOP
  v_leaf:=v_items->v_idx;
  PERFORM app_private.native_v3_role_hash('p11-item',v_leaf);
  SELECT coalesce(array_agg(value ORDER BY ord),'{}'::text[]) INTO v_sources
   FROM jsonb_array_elements_text(v_leaf->'candidate_sources') WITH ORDINALITY a(value,ord);
  SELECT coalesce(array_agg(value ORDER BY ord),'{}'::text[]) INTO v_reasons
   FROM jsonb_array_elements_text(v_leaf->'reason_codes') WITH ORDINALITY a(value,ord);
  v_contributions_sha:=app_private.native_served_json_hash(
   v_leaf->'contributions','$.items[].contributions');
  v_reasons_sha:=app_private.native_served_json_hash(v_leaf->'reason_codes','$.items[].reason_codes');
  INSERT INTO ml.recommendation_item(recommendation_request_id,rank,recording_id,score,
   candidate_sources,explanation_code,availability_snapshot,contributions,reason_codes,
   item_provenance,score_units_e8,candidate_sources_sha256,availability_snapshot_sha256,
   contributions_sha256,reason_codes_sha256,item_provenance_sha256,baseline_item_sha256)
  VALUES(p_served,(v_leaf->>'rank')::integer,(v_leaf->>'recording_id')::uuid,
   (v_leaf->>'score_units_e8')::numeric/100000000,v_sources,v_leaf->>'explanation_code',
   v_leaf->'availability_snapshot',v_leaf->'contributions',v_reasons,v_leaf->'item_provenance',
   (v_leaf->>'score_units_e8')::bigint,app_private.native_served_json_hash(
    v_leaf->'candidate_sources','$.items[].candidate_sources'),app_private.native_served_json_hash(
    v_leaf->'availability_snapshot','$.items[].availability_snapshot'),v_contributions_sha,
   v_reasons_sha,app_private.native_served_json_hash(v_leaf->'item_provenance',
    '$.items[].item_provenance'),app_private.native_served_baseline_digest(p_served,v_leaf));
  v_served_leaf:=jsonb_build_object('source_rank',v_idx+1,'recording_id',v_leaf->'recording_id',
   'score_units_e8',v_leaf->'score_units_e8','score_kind','heuristic','attribution_source','P11',
   'reasons',v_leaf->'reason_codes','contributions',v_leaf->'contributions',
   'original_p11_rank',v_idx+1,'origin','P11');
  INSERT INTO ml.recommendation_served_item VALUES(p_served,v_idx+1,
   (v_leaf->>'recording_id')::uuid,(v_leaf->>'score_units_e8')::bigint,'heuristic','P11',
   v_leaf->'reason_codes',v_reasons_sha,v_leaf->'contributions',v_contributions_sha,
   v_idx+1,'P11',app_private.native_served_item_digest(p_served,v_served_leaf));
 END LOOP;
 v_items:=NULL; v_leaf:=NULL; v_served_leaf:=NULL; v_sources:=NULL; v_reasons:=NULL;
 -- Only header+metadata survive in the already owned writer metadata slot.
 IF pg_column_size(v_header)::bigint+pg_column_size(v_metadata)>212992 THEN
  RAISE EXCEPTION 'NATIVE_V3_WRITER_METADATA_BOUND' USING ERRCODE='23514'; END IF;
 PERFORM app_private.native_v3_compiler_exit(v_phase_frame);
 v_served_items_bytes:=app_private.native_v3_item_rows_byte_length(v_owner,p_served,'SERVED');
 INSERT INTO ml.native_capture_replay_v3(original_request_id,owner_user_id,request_document,
  baseline_document,ranking_document,original_profile_document,pipeline_document,request_sha256,
  original_ranking_sha256,baseline_snapshot_sha256,temporal_snapshot_sha256,
  original_profile_document_sha256,original_profile_sha256,original_profile_binary64_sha256,
  pipeline_document_sha256,original_profile_byte_length,original_ranking_byte_length,
  baseline_byte_length,original_items_byte_length,baseline_snapshot_id,temporal_snapshot_id,
  original_item_count,created_transaction,created_at,expires_at)
 SELECT p_original,v_owner,p_request,p_baseline,p_ranking,p_profile,p_pipeline,sha256(p_request),
  sha256(p_ranking),sha256(p_baseline),b.temporal_snapshot_sha256,sha256(p_profile),
  v_old_profile_sha,p_profile_binary64_sha,sha256(p_pipeline),octet_length(p_profile),
  octet_length(p_ranking),octet_length(p_baseline),v_original_items_bytes,v_baseline_id,
  v_temporal_id,v_original_count,pg_current_xact_id(),v_created,v_expires
 FROM ml.native_original_capture_v3 b WHERE b.original_request_id=p_original;
 INSERT INTO ml.recommendation_request_root_v3(served_request_id,owner_user_id,
  request_hash_format_version,root_document,source_codec_sha256,root_proof_sha256,
  request_root_sha256,input_origin_sha256,original_request_sha256,original_ranking_sha256,
  capture_sha256,eligibility_policy_sha256,baseline_snapshot_sha256,temporal_snapshot_sha256,
  candidate_membership_sha256,original_policy_snapshot_sha256,original_availability_snapshot_sha256,
  consent_receipt_sha256,original_request_id,original_capture_format_version,baseline_snapshot_id,
  temporal_snapshot_id,consent_generation,device_id,journal_epoch,server_instance_id,identity_epoch,
  effective_limit,endpoint_max,exploration_units_e8,pipeline_byte_length,cutoff_at_ms,
  source_snapshot_track_count,candidate_universe_count,p11_item_count,served_item_count,
  served_items_byte_length,created_transaction,created_at,expires_at,referenced_snapshot_id)
 SELECT p_served,v_owner,3,p_root,b.source_codec_sha256,v_root_proof_sha,v_root_sha,v_origin_sha,
  b.request_sha256,b.original_ranking_sha256,b.bundle_sha256,b.eligibility_policy_sha256,
  b.baseline_snapshot_sha256,b.temporal_snapshot_sha256,b.candidate_membership_sha256,
  v_baseline.policy_snapshot_sha256,decode(v_original_availability,'hex'),
  b.consent_receipt_sha256,p_original,3,v_baseline_id,v_temporal_id,v_consent_generation,
  v_ctx.device_id,v_ctx.journal_epoch,v_ctx.server_instance_id,v_ctx.identity_epoch,
  (v_metadata->>'effective_limit')::integer,(v_metadata->>'endpoint_max')::integer,
  v_exploration,octet_length(p_pipeline),v_cutoff,
  b.source_snapshot_track_count,b.candidate_universe_count,v_count,v_count,v_served_items_bytes,
  pg_current_xact_id(),v_created,v_expires,v_referenced_snapshot
 FROM ml.native_original_capture_v3 b WHERE b.original_request_id=p_original;
 v_decision_sha:=sha256(app_private.native_served_lp('autplay.recommendation-decision.v3',ARRAY[
  int8send(3::bigint),uuid_send(p_served),uuid_send(v_owner),uuid_send(v_ctx.device_id),
  uuid_send(v_ctx.journal_epoch),uuid_send(v_ctx.server_instance_id),int8send(v_ctx.identity_epoch),
  decode(v_header->>'query_document_sha256','hex'),v_root_sha,
  convert_to(v_metadata->>'surface','UTF8'),convert_to(v_metadata->>'pipeline_key','UTF8'),
  convert_to(v_metadata->>'pipeline_version','UTF8'),sha256(p_pipeline),int8send(v_cutoff),
  convert_to('COMMITTED_P11','UTF8'),convert_to('P11','UTF8'),NULL::bytea,
  int8send(v_count::bigint),decode(v_header->>'p11_items_sha256','hex'),
  int8send(v_count::bigint),decode(v_header->>'served_items_sha256','hex'),
  int8send(v_created_us),int8send(v_expires_us)]));
 INSERT INTO ml.recommendation_serving_decision VALUES(p_served,'COMMITTED_P11','P11',
  decode(v_header->>'p11_items_sha256','hex'),NULL,NULL,v_count,
  decode(v_header->>'p11_items_sha256','hex'),v_count,
  decode(v_header->>'served_items_sha256','hex'),v_decision_sha,v_expires);
 INSERT INTO ml.native_atomic_capture_connection_v3(original_request_id,owner_user_id,
  served_request_id,original_capture_format_version,request_hash_format_version,
  source_codec_sha256,input_origin_sha256,root_proof_sha256,request_root_sha256,decision_sha256,
  connection_seal_sha256,capture_sha256,eligibility_policy_sha256,consent_receipt_sha256,
  consent_generation,created_transaction,created_at,expires_at)
 SELECT p_original,v_owner,p_served,3,3,b.source_codec_sha256,v_origin_sha,v_root_proof_sha,
  v_root_sha,v_decision_sha,app_private.native_v3_connection_seal(p_served),b.bundle_sha256,
  b.eligibility_policy_sha256,b.consent_receipt_sha256,v_consent_generation,
  pg_current_xact_id(),v_created,v_expires
 FROM ml.native_original_capture_v3 b WHERE b.original_request_id=p_original;
 -- Drop owned JSON/leaf/array references before the independent complete graph
 -- traversal. Incoming raw arguments remain included in the admission above.
 v_header:=NULL; v_metadata:=NULL; v_items:=NULL; v_leaf:=NULL; v_served_leaf:=NULL;
 v_body:=NULL; v_sources:=NULL; v_reasons:=NULL; v_entry:=NULL; v_part:=NULL;
 -- This real current assertion invokes the required complete graph assertion;
 -- deferred origin completion invokes that graph again on the same work owner.
 PERFORM app_private.assert_native_atomic_current_v3(v_owner,p_original,1);
 p_capture:=NULL; p_root:=NULL; p_request:=NULL; p_baseline:=NULL;
 p_ranking:=NULL; p_profile:=NULL; p_pipeline:=NULL; p_profile_binary64_sha:=NULL;
 v_ctx:=NULL; v_original:=NULL; v_baseline:=NULL; v_temporal_meta:=NULL; v_witness:=NULL;
 v_original_availability:=NULL;
 PERFORM app_private.native_v3_compiler_exit(v_raw_frame);
END $$;
REVOKE ALL ON FUNCTION app_private.commit_native_atomic_capture_v3(
 uuid,uuid,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea) FROM PUBLIC;
"""
)

NATIVE_REQUEST_V3_WRITER_DROP_SQL: Final = (
    "DROP FUNCTION app_private." + NATIVE_REQUEST_V3_WRITER_SIGNATURE + ";\n"
    "DROP FUNCTION app_private.native_v3_source_deadline_from_ms_v3(bigint);\n"
)
