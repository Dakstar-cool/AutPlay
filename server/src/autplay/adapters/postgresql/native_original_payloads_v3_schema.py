"""Dormant original Source3 bytes, SQL rows and independent R1 carrier closure.

The original creator and G's actual fact-derived R1 re-projection remain
mandatory private consumers. This SQL closure compares independent retained
bytes and actual SQL associations; it does not replace that floating-point
projector, an actual filesystem grant, or a current-generation fence.
Raw scanners and compiler/work admission precede every complete JSON cast.
"""

from __future__ import annotations

from autplay.domain.adaptive_recommendations import DEFAULT_ADAPTIVE_FEATURE_POLICY

NATIVE_ORIGINAL_PAYLOADS_V3_REQUIRED_FUNCTIONS = (
    "app_private.native_v3_compiler_enter(bigint)",
    "app_private.native_v3_compiler_grow(integer,bigint)",
    "app_private.native_v3_compiler_exit(integer)",
    "app_private.native_source3_preflight_v3(bytea,bigint,bigint)",
    "app_private.native_source3_scan_v3(bytea,integer,text,integer)",
    "app_private.native_original_document_preflight_v3(bytea)",
    "app_private.native_original_baseline_preflight_v4(bytea)",
    "app_private.native_original_profile_preflight_v3(bytea,bigint,bigint)",
    "app_private.native_v3_assert_source_facts(uuid,jsonb)",
    "app_private.native_v3_source_membership_sha256(bytea,integer)",
    "app_private.native_v3_source_cursor(uuid,uuid,bigint,bytea,bigint,bigint,bigint,"
    "text,bigint,bigint)",
    "app_private.native_v3_source_index_entry(uuid,uuid,uuid,bytea,bytea,uuid,uuid,"
    "bigint,bigint,text,uuid,uuid,uuid,bigint)",
    "app_private.native_profile_binary64_digest_v3(jsonb,jsonb,bytea,bytea,bigint,bigint)",
    "app_private.native_v3_assert_current_baseline_pipeline(bytea)",
    "app_private.native_v3_item_rows_byte_length(uuid,uuid,text)",
)


def _required() -> str:
    checks = "\n".join(
        f"IF to_regprocedure('{name}') IS NULL THEN "
        "RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_REAL_HELPER_REQUIRED' USING ERRCODE='55000'; END IF;"
        for name in NATIVE_ORIGINAL_PAYLOADS_V3_REQUIRED_FUNCTIONS
    )
    return "DO $$ BEGIN\n" + checks + "\nEND $$;\n"


NATIVE_ORIGINAL_PAYLOADS_V3_INSTALL_SQL = (
    _required()
    + r"""
CREATE FUNCTION app_private.native_v3_original_profile_number(
 p_bits jsonb,p_numeric jsonb,p_rule text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE bits bytea; actual double precision;
BEGIN
 IF jsonb_typeof(p_bits) IS DISTINCT FROM 'string'
  OR jsonb_typeof(p_numeric) IS DISTINCT FROM 'number' THEN RETURN false; END IF;
 bits:=app_private.native_source3_float_bits_v3(p_bits#>>'{}',p_rule);
 actual:=(p_numeric#>>'{}')::double precision;
 -- Unchanged RFC8785 writes either signed zero as numeric0. The independently
 -- produced raw32 carrier below preserves its original sign; numeric0 alone
 -- must neither manufacture that sign nor reject an otherwise matching value.
 RETURN float8send(actual)=bits OR (actual=0 AND
  bits IN (decode('0000000000000000','hex'),decode('8000000000000000','hex')));
END $$;

CREATE FUNCTION app_private.native_v3_source_raw_member(
 p_raw bytea,p_key text,p_path text) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE marker bytea; start_at integer; span bigint[]; size integer;
BEGIN
 -- Only two installed object/array members may be sliced. The complete raw
 -- source must already have passed its closed sorted-key scanner. This is not
 -- an arbitrary path/name lookup or a stand-alone admission API.
 IF p_key='temporal_snapshot' AND p_path='$.temporal_snapshot' THEN NULL;
 ELSIF p_key='candidate_recording_ids' AND p_path='$.candidate_recording_ids' THEN NULL;
 ELSE RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_MEMBER' USING ERRCODE='23514'; END IF;
 marker:=convert_to('"'||p_key||'":','UTF8');
 start_at:=position(marker IN p_raw);
 IF start_at<1 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_MEMBER' USING ERRCODE='23514'; END IF;
 start_at:=start_at-1+octet_length(marker);
 span:=app_private.native_source3_scan_v3(p_raw,start_at,p_path,1);
 size:=span[1]::integer-start_at;
 -- A newly materialized hashing preimage stays under the shared512KiB cap.
 -- Refusal is a compiler/resource bound, not a changed source schema limit.
 IF size<1 OR size>524288 THEN
  RAISE EXCEPTION 'NATIVE_V3_SQL_PREIMAGE_BOUND' USING ERRCODE='23514'; END IF;
 RETURN substring(p_raw FROM start_at+1 FOR size);
END $$;

-- Comparing one canonical track at a time avoids materializing a second whole
-- snapshot. The owning phase has already admitted the physical JSONB size.
CREATE FUNCTION app_private.native_v4_check_baseline(raw bytea, snapshot jsonb,
 owner_id uuid, watermark bigint, total integer) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE tracks jsonb; leaf jsonb; fragment bytea; pos integer:=1; i integer;
 eligible uuid[]:=ARRAY[]::uuid[]; result jsonb;
BEGIN
 IF octet_length(raw) NOT BETWEEN 2097153 AND 4194304 OR total NOT BETWEEN 0 AND 5000 THEN
  RAISE EXCEPTION 'NATIVE_V4_BASELINE_BYTES' USING ERRCODE='23514'; END IF;
 IF snapshot->'schema_version' NOT IN ('1'::jsonb,'2'::jsonb) THEN
  RAISE EXCEPTION 'NATIVE_V4_BASELINE_HEADER' USING ERRCODE='23514'; END IF;
 fragment:=convert_to('{"interaction_watermark":'||watermark||',"max_tracks":'||
  CASE WHEN snapshot->'schema_version'='2'::jsonb THEN '1024' ELSE '5000' END||
  ',"schema_version":'||(snapshot->>'schema_version')||',"selection_policy":"most_recent_added_then_recording_id_v'||
  (snapshot->>'schema_version')||'","tracks":[','UTF8');
 IF substring(raw FROM pos FOR octet_length(fragment)) IS DISTINCT FROM fragment THEN
  RAISE EXCEPTION 'NATIVE_V4_BASELINE_HEADER' USING ERRCODE='23514'; END IF;
 pos:=pos+octet_length(fragment);
 tracks:=snapshot->'tracks';
 IF jsonb_array_length(tracks) IS DISTINCT FROM total THEN
  RAISE EXCEPTION 'NATIVE_V4_BASELINE_COUNT' USING ERRCODE='23514'; END IF;
 FOR i IN 0..total-1 LOOP
  leaf:=tracks->i;
  IF jsonb_typeof(leaf) IS DISTINCT FROM 'object' OR pg_column_size(leaf)>65536 THEN
   RAISE EXCEPTION 'NATIVE_V4_BASELINE_LEAF' USING ERRCODE='23514'; END IF;
  -- All closed track fields are scalar except the ordered metadata-token list.
  SELECT convert_to(CASE WHEN i=0 THEN '{' ELSE ',{' END ||
   string_agg(to_jsonb(k)::text||':'||CASE WHEN k='metadata_tokens' THEN
     (SELECT '['||coalesce(string_agg(tok::text,',' ORDER BY n),'')||']'
      FROM jsonb_array_elements(v) WITH ORDINALITY AS tokens(tok,n))
    ELSE v::text END,',' ORDER BY k COLLATE "C")||'}','UTF8')
   INTO STRICT fragment FROM jsonb_each(leaf) AS members(k,v);
  IF fragment IS NULL OR octet_length(fragment)>65537
   OR substring(raw FROM pos FOR octet_length(fragment)) IS DISTINCT FROM fragment THEN
   RAISE EXCEPTION 'NATIVE_V4_BASELINE_TRACK' USING ERRCODE='23514'; END IF;
  pos:=pos+octet_length(fragment);
  IF app_private.native_p11_source_candidate((snapshot->>'schema_version')::integer,leaf,'recommendations') THEN
   eligible:=array_append(eligible,(leaf->>'recording_id')::uuid);
  END IF;
 END LOOP;
 fragment:=convert_to('],"user_id":"'||owner_id||'"}','UTF8');
 IF pos+octet_length(fragment)-1 IS DISTINCT FROM octet_length(raw)
  OR substring(raw FROM pos FOR octet_length(fragment)) IS DISTINCT FROM fragment THEN
  RAISE EXCEPTION 'NATIVE_V4_BASELINE_TRAILING' USING ERRCODE='23514'; END IF;
 SELECT coalesce(jsonb_agg(id ORDER BY id),'[]'::jsonb) INTO result FROM unnest(eligible) AS u(id);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION app_private.native_v4_check_baseline(bytea,jsonb,uuid,bigint,integer)
 FROM PUBLIC;

CREATE FUNCTION app_private.assert_native_original_payloads_v3(p_original uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE h record; r record; o record; s record; temporal_row record; p record;
 raw bytea; source_raw bytea; part bytea; source jsonb; temporal jsonb; numeric_profile jsonb;
 original_doc jsonb; snapshot_doc jsonb; profile_bits jsonb; dimensions jsonb;
 numeric_dimensions jsonb;
 stat bigint[]; source_stat bigint[]; profile_stat bigint[]; original_bytes bigint;
 retained_compiler bigint; old_json_bytes bigint; peak bigint; d integer; idx integer;
 field text; rule text; path text[]; row_bits jsonb; row_numeric jsonb; previous_dim text[];
 ranking_items jsonb; expected_contributions jsonb; served_item record;
 calculated bytea; profile_old_sha bytea; source_membership bytea;
 source_count integer; candidate_count integer; t integer; n integer; v_rank integer;
 ids jsonb; track jsonb; item record; eligible jsonb; profile_byte_count bigint;
 cursor_row record; index_entry record; index_size bigint:=10; index_count integer:=0;
 compiler_depth integer; subgraph_depth integer; phase_depth integer;
 phase_bytes bigint; membership_bytes bigint; membership_depth integer;
BEGIN
 IF p_original IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_INPUT' USING ERRCODE='23514'; END IF;
 -- Fixed metadata only: no BYTEA, common request JSON or snapshot row hydration.
 SELECT b.owner_user_id,b.baseline_snapshot_id,b.temporal_snapshot_id,b.source_codec,
  b.source_codec_sha256,b.original_capture_format_version,b.bundle_sha256,b.request_sha256,
  b.pipeline_manifest_sha256,b.baseline_snapshot_sha256,b.temporal_snapshot_sha256,
  b.candidate_membership_sha256,b.original_ranking_sha256,b.feature_policy_sha256,
  b.eligibility_policy_sha256,b.consent_receipt_sha256,b.consent_generation,b.cutoff_at_ms,
  b.interaction_watermark,b.native_interaction_watermark,b.source_snapshot_track_count,
  b.candidate_universe_count,b.p11_item_count,b.source_event_count,b.source_evidence_count,
  b.profile_dimension_count,b.ineligibility_reason,b.source_index_byte_length,
  b.created_transaction,b.created_at,b.expires_at,octet_length(b.bundle_document) AS raw_bytes,
  pg_column_size(bs.snapshot_document) AS baseline_json_bytes,
  octet_length(bs.availability_snapshot) AS baseline_availability_bytes,
  pg_column_compression(bs.snapshot_document) AS baseline_compression,
  pg_column_size(tm.snapshot_document) AS temporal_json_bytes,
  pg_column_compression(tm.snapshot_document) AS temporal_compression
 INTO h FROM ml.native_original_capture_v3 b
 JOIN ml.recommendation_input_snapshot bs ON bs.user_id=b.owner_user_id
  AND bs.recommendation_input_snapshot_id=b.baseline_snapshot_id
 JOIN ml.recommendation_temporal_snapshot tm ON tm.user_id=b.owner_user_id
  AND tm.recommendation_temporal_snapshot_id=b.temporal_snapshot_id
 WHERE b.original_request_id=p_original;
 IF h.owner_user_id IS NULL OR h.original_capture_format_version<>3
  OR h.source_codec IS DISTINCT FROM 'NATIVE_SOURCE3_F64BITS_JSON_SAFE53_V1'
  OR h.source_codec_sha256 IS DISTINCT FROM decode(
   'eda9439b265c5e92934f8f62a333782579a14aed116385271f158819045581a2','hex')
  OR h.raw_bytes NOT BETWEEN 1 AND 2097152
  OR h.feature_policy_sha256 IS DISTINCT FROM decode('__FEATURE_POLICY__','hex')
  OR h.eligibility_policy_sha256 IS DISTINCT FROM app_private.native_p11_source_policy(h.pipeline_manifest_sha256)
  OR h.baseline_availability_bytes IS DISTINCT FROM 64
  OR h.baseline_compression IS NOT NULL OR h.temporal_compression IS NOT NULL
  OR h.baseline_json_bytes NOT BETWEEN 1 AND 8388608
  OR h.temporal_json_bytes NOT BETWEEN 1 AND 8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_METADATA' USING ERRCODE='23514'; END IF;
 SELECT rv.owner_user_id,rv.original_request_id,rv.served_request_id,rv.baseline_snapshot_id,
  rv.temporal_snapshot_id,rv.original_request_sha256,rv.original_ranking_sha256,
  rv.baseline_snapshot_sha256,rv.temporal_snapshot_sha256,rv.candidate_membership_sha256,
  rv.original_policy_snapshot_sha256,rv.original_availability_snapshot_sha256,rv.cutoff_at_ms,
  rv.source_snapshot_track_count,rv.candidate_universe_count,rv.p11_item_count,rv.created_transaction,
  rv.effective_limit,rv.exploration_units_e8,rv.server_instance_id,rv.identity_epoch,
  rv.created_at,rv.expires_at INTO r FROM ml.recommendation_request_root_v3 rv
  WHERE rv.original_request_id=p_original;
 SELECT pv.owner_user_id,pv.baseline_snapshot_id,pv.temporal_snapshot_id,pv.request_sha256,
  pv.original_ranking_sha256,pv.baseline_snapshot_sha256,pv.temporal_snapshot_sha256,
  pv.original_profile_document_sha256,pv.original_profile_sha256,pv.original_profile_binary64_sha256,
  pv.pipeline_document_sha256,pv.original_profile_byte_length,pv.original_ranking_byte_length,
  pv.baseline_byte_length,pv.original_item_count,pv.original_items_byte_length,
  pv.created_transaction,pv.created_at,pv.expires_at,
  octet_length(pv.original_profile_document) AS profile_raw_bytes,
  octet_length(pv.baseline_document) AS baseline_raw_bytes,
  octet_length(pv.request_document) AS request_raw_bytes,
  octet_length(pv.ranking_document) AS ranking_raw_bytes,
  octet_length(pv.pipeline_document) AS pipeline_raw_bytes
  INTO p FROM ml.native_capture_replay_v3 pv WHERE pv.original_request_id=p_original;
 -- Validate the potentially large baseline before loading the independent
 -- Source3 document. Only the sorted UUID membership crosses the phase boundary.
 t:=h.source_snapshot_track_count;
 IF t IS NULL OR t NOT BETWEEN 0 AND 5000 THEN
  RAISE EXCEPTION 'NATIVE_V3_CAPTURE_COLUMNS' USING ERRCODE='23514'; END IF;
 membership_bytes:=65536+40*t;
 membership_depth:=app_private.native_v3_compiler_enter(membership_bytes);
 retained_compiler:=membership_bytes;
 -- Actual original baseline and ordinary request associations are independent
 -- of the V3 served role-set hash domains.
 IF retained_compiler+2*h.baseline_json_bytes+65536>8388608
  OR retained_compiler+2*h.temporal_json_bytes+65536>8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
 IF p.baseline_raw_bytes IS DISTINCT FROM p.baseline_byte_length
  OR p.baseline_raw_bytes IS NULL OR p.baseline_raw_bytes NOT BETWEEN 1 AND 4194304 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_BASELINE_RAW' USING ERRCODE='23514'; END IF;
 IF p.baseline_raw_bytes>2097152 THEN
  -- Extracting tracks briefly owns the parent and child. The UUID sort and
  -- per-leaf encoding run after extraction, with one retained tracks array.
  phase_bytes:=greatest(2*h.baseline_json_bytes+65536,
   h.baseline_json_bytes+524288,p.baseline_raw_bytes+65536);
 ELSE
  phase_bytes:=2*h.baseline_json_bytes+p.baseline_raw_bytes+65536;
 END IF;
 phase_depth:=app_private.native_v3_compiler_enter(phase_bytes);
 SELECT bs.user_id,bs.interaction_watermark,bs.catalog_snapshot,bs.availability_snapshot,
  bs.policy_snapshot_sha256,bs.input_snapshot_sha256,bs.retained_until,bs.created_at,
  bs.snapshot_document INTO s FROM ml.recommendation_input_snapshot bs
  WHERE bs.recommendation_input_snapshot_id=h.baseline_snapshot_id;
 IF s.user_id IS DISTINCT FROM h.owner_user_id
  OR s.input_snapshot_sha256<>h.baseline_snapshot_sha256
  OR s.interaction_watermark<>h.interaction_watermark OR s.retained_until<h.expires_at
  OR s.created_at IS DISTINCT FROM h.created_at
  OR s.policy_snapshot_sha256 IS DISTINCT FROM r.original_policy_snapshot_sha256
  OR s.availability_snapshot IS DISTINCT FROM encode(r.original_availability_snapshot_sha256,'hex')
  OR s.snapshot_document-'tracks' IS DISTINCT FROM app_private.native_p11_snapshot_header(
   h.pipeline_manifest_sha256,h.owner_user_id,h.interaction_watermark)
  OR (app_private.native_p11_source_version(h.pipeline_manifest_sha256)=2 AND t>1024)
  OR jsonb_array_length(s.snapshot_document->'tracks')<>t THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_BASELINE' USING ERRCODE='23514'; END IF;
 SELECT pv.baseline_document INTO raw FROM ml.native_capture_replay_v3 pv
  WHERE pv.original_request_id=p_original;
 IF p.baseline_raw_bytes>2097152 THEN
  IF pg_column_compression(raw) IS NOT NULL OR sha256(raw)<>h.baseline_snapshot_sha256 THEN
   RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_BASELINE_RAW' USING ERRCODE='23514'; END IF;
  eligible:=app_private.native_v4_check_baseline(
   raw,s.snapshot_document,h.owner_user_id,h.interaction_watermark,t);
 ELSE
 stat:=app_private.native_original_document_preflight_v3(raw);
 IF retained_compiler+h.baseline_json_bytes+2*stat[1]+stat[3]>8388608
  OR octet_length(raw)<>p.baseline_byte_length OR sha256(raw)<>h.baseline_snapshot_sha256 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_BASELINE_RAW' USING ERRCODE='23514'; END IF;
 phase_bytes:=greatest(phase_bytes,2*h.baseline_json_bytes+2*stat[1]+stat[3]+1048576);
 PERFORM app_private.native_v3_compiler_grow(phase_depth,phase_bytes);
 original_doc:=convert_from(raw,'UTF8')::jsonb; raw:=NULL;
 IF original_doc IS DISTINCT FROM s.snapshot_document THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_BASELINE_RAW' USING ERRCODE='23514'; END IF;
 original_doc:=NULL;
 SELECT coalesce(jsonb_agg(rows.track->'recording_id' ORDER BY (rows.track->>'recording_id')::uuid),
  '[]'::jsonb) INTO eligible
 FROM (SELECT s.snapshot_document->'tracks'->generate_series(0,t-1) AS track OFFSET 0) rows
 WHERE app_private.native_p11_source_candidate(
   app_private.native_p11_source_version(h.pipeline_manifest_sha256), rows.track, 'recommendations');
 END IF;
 s.snapshot_document:=NULL; raw:=NULL; original_doc:=NULL;
 IF pg_column_size(eligible)>membership_bytes THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 compiler_depth:=app_private.native_v3_compiler_enter(h.raw_bytes+65536);
 SELECT b.bundle_document || ''::bytea INTO source_raw FROM ml.native_original_capture_v3 b
  WHERE b.original_request_id=p_original;
 source_stat:=app_private.native_source3_preflight_v3(source_raw,
  144*h.raw_bytes+16777216,16*(h.raw_bytes+1));
 -- The base owns the raw source and full JSONB until final source-fact return.
 -- A short child owns convert_from's transient UTF8, without discounting base.
 retained_compiler:=membership_bytes+h.raw_bytes+4*source_stat[4]+65536;
 IF retained_compiler+h.raw_bytes>8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
 PERFORM app_private.native_v3_compiler_grow(compiler_depth,h.raw_bytes+source_stat[4]+65536);
 phase_depth:=app_private.native_v3_compiler_enter(h.raw_bytes);
 source:=convert_from(source_raw,'UTF8')::jsonb;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 -- Persistent owned sub-datums have a separate lifetime from the base source.
 -- Only the temporal subdocument and its two descendants survive this projection.
 -- The candidate array can dominate the source and is absent from all three.
 phase_depth:=app_private.native_v3_compiler_enter(source_stat[4]+65536);
 phase_bytes:=3*(pg_column_size(source->'temporal_snapshot')::bigint+16)+65536;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 IF phase_bytes IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_METADATA' USING ERRCODE='23514'; END IF;
 subgraph_depth:=app_private.native_v3_compiler_enter(phase_bytes);
 retained_compiler:=membership_bytes+h.raw_bytes+source_stat[4]+65536+phase_bytes;
 IF eligible IS DISTINCT FROM source->'candidate_recording_ids' THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_FILTER' USING ERRCODE='23514'; END IF;
 eligible:=NULL;
 temporal:=source->'temporal_snapshot'; profile_bits:=temporal->'adaptive_profile';
 dimensions:=profile_bits->'dimensions';
 source_count:=jsonb_array_length(temporal->'facts');
 candidate_count:=jsonb_array_length(source->'candidate_recording_ids');
 d:=jsonb_array_length(dimensions); t:=h.source_snapshot_track_count; n:=h.p11_item_count;
 IF h.bundle_sha256 IS DISTINCT FROM sha256(source_raw)
  OR source->>'recommendation_request_id' IS DISTINCT FROM p_original::text
  OR source->>'owner_user_id' IS DISTINCT FROM h.owner_user_id::text
  OR source->>'request_sha256' IS DISTINCT FROM encode(h.request_sha256,'hex')
  OR source->>'pipeline_manifest_sha256' IS DISTINCT FROM encode(h.pipeline_manifest_sha256,'hex')
  OR source->>'baseline_snapshot_id' IS DISTINCT FROM h.baseline_snapshot_id::text
  OR source->>'baseline_snapshot_sha256' IS DISTINCT FROM encode(h.baseline_snapshot_sha256,'hex')
  OR source->>'temporal_snapshot_id' IS DISTINCT FROM h.temporal_snapshot_id::text
  OR source->>'temporal_snapshot_sha256' IS DISTINCT FROM encode(h.temporal_snapshot_sha256,'hex')
  OR source->>'feature_policy_sha256' IS DISTINCT FROM encode(h.feature_policy_sha256,'hex')
  OR source->'cutoff_at_ms' IS DISTINCT FROM to_jsonb(h.cutoff_at_ms)
  OR source->'interaction_watermark' IS DISTINCT FROM to_jsonb(h.interaction_watermark)
  OR source->'native_interaction_watermark' IS DISTINCT FROM
   to_jsonb(h.native_interaction_watermark)
  OR source->'source_snapshot_track_count' IS DISTINCT FROM to_jsonb(t)
  OR source->'candidate_universe_count' IS DISTINCT FROM to_jsonb(candidate_count)
  OR source->>'ineligibility_reason' IS DISTINCT FROM h.ineligibility_reason
  OR source->>'consent_receipt_sha256' IS DISTINCT FROM encode(h.consent_receipt_sha256,'hex')
  OR source->'consent_generation' IS DISTINCT FROM to_jsonb(h.consent_generation)
  OR source_count<>h.source_event_count OR candidate_count<>h.candidate_universe_count
  OR jsonb_array_length(temporal->'source_event_ids')<>source_count
  OR jsonb_array_length(temporal->'source_evidence')<>h.source_evidence_count
  OR d<>h.profile_dimension_count OR h.expires_at<>h.created_at+interval '15552000 seconds'
  OR h.cutoff_at_ms<>floor(extract(epoch FROM h.created_at)*1000) THEN
  RAISE EXCEPTION 'NATIVE_V3_CAPTURE_COLUMNS' USING ERRCODE='23514'; END IF;
 phase_depth:=app_private.native_v3_compiler_enter(524288+65536);
 part:=app_private.native_v3_source_raw_member(source_raw,'temporal_snapshot','$.temporal_snapshot');
 IF retained_compiler+octet_length(part)>8388608
  OR sha256(part) IS DISTINCT FROM h.temporal_snapshot_sha256 THEN
  RAISE EXCEPTION 'NATIVE_V3_TEMPORAL_RAW_HASH' USING ERRCODE='23514'; END IF;
 part:=NULL;
 part:=app_private.native_v3_source_raw_member(source_raw,
  'candidate_recording_ids','$.candidate_recording_ids');
 IF sha256(part) IS DISTINCT FROM h.candidate_membership_sha256 THEN
  RAISE EXCEPTION 'NATIVE_V3_CANDIDATE_RAW_HASH' USING ERRCODE='23514'; END IF;
 part:=NULL;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 IF r.owner_user_id IS DISTINCT FROM h.owner_user_id
  OR p.owner_user_id IS DISTINCT FROM h.owner_user_id
  OR r.original_request_sha256 IS DISTINCT FROM h.request_sha256
  OR r.original_ranking_sha256 IS DISTINCT FROM h.original_ranking_sha256
  OR p.request_sha256 IS DISTINCT FROM h.request_sha256
  OR p.original_ranking_sha256 IS DISTINCT FROM h.original_ranking_sha256
  OR p.baseline_snapshot_sha256 IS DISTINCT FROM h.baseline_snapshot_sha256
  OR r.baseline_snapshot_sha256 IS DISTINCT FROM h.baseline_snapshot_sha256
  OR p.temporal_snapshot_sha256 IS DISTINCT FROM h.temporal_snapshot_sha256
  OR r.temporal_snapshot_sha256 IS DISTINCT FROM h.temporal_snapshot_sha256
  OR r.candidate_membership_sha256 IS DISTINCT FROM h.candidate_membership_sha256
  OR p.pipeline_document_sha256 IS DISTINCT FROM h.pipeline_manifest_sha256
  OR p.baseline_snapshot_id IS DISTINCT FROM h.baseline_snapshot_id
  OR r.baseline_snapshot_id IS DISTINCT FROM h.baseline_snapshot_id
  OR p.temporal_snapshot_id IS DISTINCT FROM h.temporal_snapshot_id
  OR r.temporal_snapshot_id IS DISTINCT FROM h.temporal_snapshot_id
  OR p.created_transaction IS DISTINCT FROM h.created_transaction
  OR r.created_transaction IS DISTINCT FROM h.created_transaction
  OR p.created_at IS DISTINCT FROM h.created_at OR r.created_at IS DISTINCT FROM h.created_at
  OR p.expires_at IS DISTINCT FROM h.expires_at OR r.expires_at IS DISTINCT FROM h.expires_at
  OR p.original_item_count IS DISTINCT FROM n OR r.p11_item_count IS DISTINCT FROM n
  OR r.source_snapshot_track_count IS DISTINCT FROM t
  OR r.candidate_universe_count IS DISTINCT FROM candidate_count
  OR r.cutoff_at_ms IS DISTINCT FROM h.cutoff_at_ms THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_GRAPH_COLUMNS' USING ERRCODE='23514'; END IF;
 IF temporal->>'owner_user_id' IS DISTINCT FROM h.owner_user_id::text
  OR temporal->>'baseline_snapshot_id' IS DISTINCT FROM h.baseline_snapshot_id::text
  OR temporal->>'baseline_snapshot_sha256' IS DISTINCT FROM encode(h.baseline_snapshot_sha256,'hex')
  OR temporal->>'temporal_snapshot_id' IS DISTINCT FROM h.temporal_snapshot_id::text
  OR temporal->'cutoff_at_ms' IS DISTINCT FROM to_jsonb(h.cutoff_at_ms)
  OR temporal->'native_interaction_watermark' IS DISTINCT FROM
   to_jsonb(h.native_interaction_watermark)
  OR temporal->>'feature_policy_sha256' IS DISTINCT FROM encode(h.feature_policy_sha256,'hex')
  -- Compare the creator's exact UTC isoformat, without parsing untrusted
  -- timestamp text or depending on the session TimeZone/DateStyle.
  OR temporal->>'expires_at' IS DISTINCT FROM
   to_char(h.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')||
   (CASE WHEN mod(extract(microseconds FROM h.expires_at)::bigint,1000000)=0 THEN ''
    ELSE '.'||to_char(h.expires_at AT TIME ZONE 'UTC','US') END)||'+00:00'
  OR temporal->'server_identity' IS DISTINCT FROM jsonb_build_object(
   'server_instance_id',r.server_instance_id,'identity_epoch',r.identity_epoch)
  OR temporal->'supported_source_types' IS DISTINCT FROM
   '["LISTENING_EVENT_RECORDED","RECOMMENDATION_FEEDBACK_RECORDED",'
   '"RECOMMENDATION_IMPRESSION_RECORDED"]'::jsonb
  OR temporal->'artifact_authority'->>'pipeline_manifest_sha256' IS DISTINCT FROM
   encode(h.pipeline_manifest_sha256,'hex')
  OR profile_bits->>'owner_user_id' IS DISTINCT FROM h.owner_user_id::text
  OR profile_bits->'cutoff_at_ms' IS DISTINCT FROM to_jsonb(h.cutoff_at_ms)
  OR profile_bits->'interaction_watermark' IS DISTINCT FROM to_jsonb(h.native_interaction_watermark)
  OR profile_bits->'feature_policy' IS DISTINCT FROM jsonb_build_object(
   'key','__FEATURE_POLICY_KEY__','version','__FEATURE_POLICY_VERSION__',
   'content_sha256',encode(h.feature_policy_sha256,'hex')) THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_TEMPORAL_COLUMNS' USING ERRCODE='23514'; END IF;
 -- Numeric original profile: raw scan+self-SHA splice precede whole JSONB.
 IF p.profile_raw_bytes IS NULL OR p.profile_raw_bytes NOT BETWEEN 1 AND 2097152 THEN
  RAISE EXCEPTION 'NATIVE_V3_PROFILE_RAW_HASH' USING ERRCODE='23514'; END IF;
 phase_depth:=app_private.native_v3_compiler_enter(p.profile_raw_bytes+65536);
 SELECT original_profile_document || ''::bytea INTO raw FROM ml.native_capture_replay_v3
  WHERE original_request_id=p_original;
 profile_byte_count:=octet_length(raw);
 profile_stat:=app_private.native_original_profile_preflight_v3(raw,
  144*profile_byte_count+16777216,16*(profile_byte_count+1));
 IF retained_compiler+2*profile_stat[4]+524288>8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
 -- Includes numeric JSONB+dimensions, UTF8/raw and both self-SHA splice inputs.
 PERFORM app_private.native_v3_compiler_grow(phase_depth,2*profile_stat[4]+524288);
 IF profile_byte_count<>p.original_profile_byte_length
  OR sha256(raw) IS DISTINCT FROM p.original_profile_document_sha256 THEN
  RAISE EXCEPTION 'NATIVE_V3_PROFILE_RAW_HASH' USING ERRCODE='23514'; END IF;
 IF profile_byte_count-(profile_stat[9]-profile_stat[8])>524288 THEN
  RAISE EXCEPTION 'NATIVE_V3_SQL_PREIMAGE_BOUND' USING ERRCODE='23514'; END IF;
 part:=substring(raw FROM 1 FOR profile_stat[8]::integer)||
  substring(raw FROM profile_stat[9]::integer+1);
 profile_old_sha:=sha256(part); part:=NULL;
 IF profile_old_sha IS DISTINCT FROM p.original_profile_sha256
  OR substring(raw FROM profile_stat[10]::integer+1 FOR 64) IS DISTINCT FROM
   convert_to(encode(profile_old_sha,'hex'),'UTF8') THEN
  RAISE EXCEPTION 'NATIVE_V3_PROFILE_SELF_HASH' USING ERRCODE='23514'; END IF;
 numeric_profile:=convert_from(raw,'UTF8')::jsonb; raw:=NULL;
 numeric_dimensions:=numeric_profile->'dimensions';
 IF (numeric_profile-ARRAY['maturity','dimensions']) IS DISTINCT FROM
  (profile_bits-ARRAY['maturity','dimensions']) THEN
  RAISE EXCEPTION 'NATIVE_V3_PROFILE_NUMERIC_BINDING' USING ERRCODE='23514'; END IF;
 FOR field,rule IN SELECT * FROM (VALUES ('score','unit'),
  ('effective_signal_mass','nonnegative'),('recency','unit'),('consistency','unit'),
  ('organic_share','unit'),('plasticity_multiplier','plasticity')) fields(field,rule) LOOP
  IF NOT app_private.native_v3_original_profile_number(profile_bits->'maturity'->field,
   numeric_profile->'maturity'->field,rule) THEN
   RAISE EXCEPTION 'NATIVE_V3_PROFILE_NUMERIC_BINDING' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF ((numeric_profile->'maturity')-ARRAY['score','effective_signal_mass','recency','consistency',
  'organic_share','plasticity_multiplier']) IS DISTINCT FROM
  ((profile_bits->'maturity')-ARRAY['score','effective_signal_mass','recency','consistency',
  'organic_share','plasticity_multiplier']) OR jsonb_array_length(numeric_profile->'dimensions')<>d
  THEN RAISE EXCEPTION 'NATIVE_V3_PROFILE_NUMERIC_BINDING' USING ERRCODE='23514'; END IF;
 FOR idx IN 0..d-1 LOOP
  row_bits:=dimensions->idx; row_numeric:=numeric_dimensions->idx;
  IF (row_numeric-ARRAY['long_term','recent','persistence','momentum','fatigue',
   'recent_adjustment'])
   IS DISTINCT FROM
   (row_bits-ARRAY['long_term','recent','persistence','momentum','fatigue',
    'recent_adjustment']) THEN
   RAISE EXCEPTION 'NATIVE_V3_PROFILE_NUMERIC_BINDING' USING ERRCODE='23514'; END IF;
  IF previous_dim IS NOT NULL AND
   ARRAY[row_bits->>'kind',row_bits->>'key'] COLLATE "C"<=previous_dim COLLATE "C" THEN
   RAISE EXCEPTION 'NATIVE_V3_PROFILE_DIMENSION_ORDER' USING ERRCODE='23514'; END IF;
  previous_dim:=ARRAY[row_bits->>'kind',row_bits->>'key'];
  FOR path,rule IN SELECT * FROM (VALUES
   (ARRAY['long_term','score'],'signed'),(ARRAY['long_term','confidence'],'unit'),
   (ARRAY['long_term','evidence_mass'],'nonnegative'),(ARRAY['persistence'],'unit'),
   (ARRAY['momentum'],'signed'),(ARRAY['fatigue'],'unit'),(ARRAY['recent_adjustment'],'signed'),
   (ARRAY['recent','EPISODE','score'],'signed'),(ARRAY['recent','EPISODE','confidence'],'unit'),
   (ARRAY['recent','EPISODE','evidence_mass'],'nonnegative'),
   (ARRAY['recent','H12','score'],'signed'),(ARRAY['recent','H12','confidence'],'unit'),
   (ARRAY['recent','H12','evidence_mass'],'nonnegative'),
   (ARRAY['recent','H24','score'],'signed'),(ARRAY['recent','H24','confidence'],'unit'),
   (ARRAY['recent','H24','evidence_mass'],'nonnegative'),
   (ARRAY['recent','D3','score'],'signed'),(ARRAY['recent','D3','confidence'],'unit'),
   (ARRAY['recent','D3','evidence_mass'],'nonnegative'),
   (ARRAY['recent','D7','score'],'signed'),(ARRAY['recent','D7','confidence'],'unit'),
   (ARRAY['recent','D7','evidence_mass'],'nonnegative')) fields(path,rule) LOOP
   IF NOT app_private.native_v3_original_profile_number(row_bits#>path,row_numeric#>path,rule) THEN
    RAISE EXCEPTION 'NATIVE_V3_PROFILE_NUMERIC_BINDING' USING ERRCODE='23514'; END IF;
  END LOOP;
 END LOOP;
 numeric_profile:=NULL; numeric_dimensions:=NULL; row_bits:=NULL; row_numeric:=NULL;
 raw:=NULL; part:=NULL;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 source_membership:=decode(temporal->>'source_membership_sha256','hex');
 phase_depth:=app_private.native_v3_compiler_enter(524288+65536);
 IF source_membership IS DISTINCT FROM
  app_private.native_v3_source_membership_sha256(source_raw,source_count) THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_RAW_HASH' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM ml.native_original_capture_format f
  WHERE f.original_request_id=p_original AND f.owner_user_id=h.owner_user_id
   AND f.original_capture_format_version=3 AND f.created_transaction=h.created_transaction
   AND f.created_at=h.created_at AND f.expires_at=h.expires_at) THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_FORMAT' USING ERRCODE='23514'; END IF;
 SELECT k.owner_user_id,k.state,k.source_event_count,k.source_membership_sha256,k.cutoff_at_ms,
  k.native_interaction_watermark,k.source_generation,k.source_cursor_byte_length,
  k.created_transaction,k.created_at,k.expires_at INTO cursor_row
  FROM ml.native_capture_lineage_cursor_v3 k WHERE k.original_request_id=p_original;
 IF cursor_row.owner_user_id IS DISTINCT FROM h.owner_user_id
  OR cursor_row.state IS DISTINCT FROM 'ACTIVE'
  OR cursor_row.source_event_count IS DISTINCT FROM source_count
  OR cursor_row.source_membership_sha256 IS DISTINCT FROM source_membership
  OR cursor_row.cutoff_at_ms IS DISTINCT FROM h.cutoff_at_ms
  OR cursor_row.native_interaction_watermark IS DISTINCT FROM h.native_interaction_watermark
  OR cursor_row.created_transaction IS DISTINCT FROM h.created_transaction
  OR cursor_row.created_at IS DISTINCT FROM h.created_at
  OR cursor_row.expires_at IS DISTINCT FROM h.expires_at THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_CURSOR' USING ERRCODE='23514'; END IF;
 part:=app_private.native_v3_source_cursor(p_original,h.owner_user_id,source_count::bigint,
  source_membership,h.cutoff_at_ms,h.native_interaction_watermark,cursor_row.source_generation,
  h.created_transaction::text,(extract(epoch FROM h.created_at)*1000000)::bigint,
  (extract(epoch FROM h.expires_at)*1000000)::bigint);
 IF cursor_row.source_cursor_byte_length IS DISTINCT FROM octet_length(part) THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_CURSOR_BYTES' USING ERRCODE='23514'; END IF;
 part:=NULL;
 FOR index_entry IN SELECT e.source_event_id,e.owner_user_id,e.source_request_sha256,
  e.normalized_payload_sha256,e.device_id,e.journal_epoch,e.device_sequence,e.server_sequence,
  e.event_type,e.association_id,e.causal_request_id,e.impression_event_id,e.retained_until
  FROM ml.native_capture_source_event_v3 e WHERE e.original_request_id=p_original
  ORDER BY e.source_event_id LIMIT 10001 LOOP
  index_count:=index_count+1;
  IF index_count>source_count OR index_entry.owner_user_id IS DISTINCT FROM h.owner_user_id THEN
   RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_SOURCE_INDEX' USING ERRCODE='23514'; END IF;
  part:=app_private.native_v3_source_index_entry(p_original,h.owner_user_id,
   index_entry.source_event_id,index_entry.source_request_sha256,index_entry.normalized_payload_sha256,
   index_entry.device_id,index_entry.journal_epoch,index_entry.device_sequence,
   index_entry.server_sequence,index_entry.event_type,index_entry.association_id,
   index_entry.causal_request_id,index_entry.impression_event_id,
   (extract(epoch FROM index_entry.retained_until)*1000000)::bigint);
  index_size:=index_size+octet_length(part); part:=NULL;
 END LOOP;
 IF index_count<>source_count OR index_size<>h.source_index_byte_length THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_SOURCE_INDEX_BYTES' USING ERRCODE='23514'; END IF;
 part:=NULL; index_entry:=NULL; cursor_row:=NULL;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 phase_depth:=app_private.native_v3_compiler_enter(524288);
 calculated:=app_private.native_profile_binary64_digest_v3(profile_bits,dimensions,
  source_membership,profile_old_sha,
  64*(pg_column_size(profile_bits)::bigint+pg_column_size(dimensions)::bigint)
   +128*(293+1118::bigint*d)+65536,4096+2048::bigint*(d+1));
 IF calculated IS DISTINCT FROM p.original_profile_binary64_sha256 THEN
  RAISE EXCEPTION 'NATIVE_V3_PROFILE_BINARY64_BINDING' USING ERRCODE='23514'; END IF;
 calculated:=NULL;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 IF NOT EXISTS(SELECT 1 FROM ml.recommendation_request q
  WHERE q.recommendation_request_id=p_original AND octet_length(q.availability_snapshot_ref)=64
   AND octet_length(q.pipeline_key)<=4096 AND octet_length(q.pipeline_version)<=4096
   AND CASE WHEN pg_column_compression(q.request_features) IS NULL
    AND pg_column_size(q.request_features)<=256 THEN q.request_features=jsonb_build_object(
     'limit',r.effective_limit,'exploration',r.exploration_units_e8::numeric/100000000)
    ELSE false END) THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_SCALAR_BOUND' USING ERRCODE='23514'; END IF;
 SELECT q.user_id,q.serving_contract_version,q.request_sha256,q.request_schema_version,
  q.request_canonicalization_version,q.recommendation_input_snapshot_id,
  q.recommendation_temporal_snapshot_id,q.input_snapshot_sha256,q.temporal_snapshot_sha256,
  q.adaptive_feature_policy_sha256,q.surface,q.context,q.seed,q.shadow,q.created_at,
  q.interaction_watermark,q.catalog_snapshot,q.availability_snapshot_ref,q.policy_snapshot_sha256,
  q.pipeline_key,q.pipeline_version,q.pipeline_manifest_sha256
  INTO o FROM ml.recommendation_request q WHERE q.recommendation_request_id=p_original;
 IF o.user_id IS DISTINCT FROM h.owner_user_id OR o.serving_contract_version<>1
  OR o.request_schema_version<>1 OR o.request_canonicalization_version<>1
  OR o.request_sha256 IS DISTINCT FROM h.request_sha256
  OR o.recommendation_input_snapshot_id IS DISTINCT FROM h.baseline_snapshot_id
  OR o.recommendation_temporal_snapshot_id IS DISTINCT FROM h.temporal_snapshot_id
  OR o.input_snapshot_sha256 IS DISTINCT FROM h.baseline_snapshot_sha256
  OR o.temporal_snapshot_sha256 IS DISTINCT FROM h.temporal_snapshot_sha256
  OR o.adaptive_feature_policy_sha256 IS DISTINCT FROM h.feature_policy_sha256
  OR o.interaction_watermark IS DISTINCT FROM h.interaction_watermark
  OR o.catalog_snapshot IS DISTINCT FROM s.catalog_snapshot
  OR o.policy_snapshot_sha256 IS DISTINCT FROM r.original_policy_snapshot_sha256
  OR o.availability_snapshot_ref IS DISTINCT FROM
   encode(r.original_availability_snapshot_sha256,'hex')
  OR o.pipeline_key IS DISTINCT FROM 'cpu-baseline'
  OR o.pipeline_version IS DISTINCT FROM app_private.native_p11_source_version(h.pipeline_manifest_sha256)::text
  OR o.pipeline_manifest_sha256 IS DISTINCT FROM h.pipeline_manifest_sha256
  OR o.surface IS DISTINCT FROM 'recommendations' OR o.shadow OR o.created_at<>h.created_at THEN
  RAISE EXCEPTION 'NATIVE_V3_ORDINARY_ORIGINAL' USING ERRCODE='23514'; END IF;
 IF p.request_raw_bytes IS NULL OR p.request_raw_bytes NOT BETWEEN 1 AND 2097152 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_REQUEST_RAW' USING ERRCODE='23514'; END IF;
 phase_bytes:=p.request_raw_bytes+65536;
 phase_depth:=app_private.native_v3_compiler_enter(phase_bytes);
 SELECT pv.request_document INTO raw FROM ml.native_capture_replay_v3 pv
  WHERE pv.original_request_id=p_original;
 stat:=app_private.native_original_document_preflight_v3(raw);
 IF retained_compiler+2*stat[1]+stat[3]+65536>8388608 OR sha256(raw)<>h.request_sha256 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_REQUEST_RAW' USING ERRCODE='23514'; END IF;
 phase_bytes:=greatest(phase_bytes,2*stat[1]+stat[3]+65536);
 PERFORM app_private.native_v3_compiler_grow(phase_depth,phase_bytes);
 original_doc:=convert_from(raw,'UTF8')::jsonb; raw:=NULL;
 IF original_doc IS DISTINCT FROM jsonb_build_object('schema_version',1,
  'canonicalization_version',1,'user_id',h.owner_user_id,'surface',o.surface,
  'context',o.context,'limit',r.effective_limit,'exploration',r.exploration_units_e8::numeric/100000000,
  'seed',o.seed,'shadow',false,'pipeline',jsonb_build_object('key',o.pipeline_key,
   'version',o.pipeline_version,'manifest_sha256',encode(o.pipeline_manifest_sha256,'hex')),
  'snapshot',jsonb_build_object('snapshot_id',h.baseline_snapshot_id,
   'input_snapshot_sha256',encode(h.baseline_snapshot_sha256,'hex'),
   'interaction_watermark',h.interaction_watermark,'catalog_snapshot',o.catalog_snapshot,
   'availability_snapshot',o.availability_snapshot_ref,
   'policy_snapshot_sha256',encode(r.original_policy_snapshot_sha256,'hex')))
  OR NOT EXISTS(SELECT 1 FROM ml.recommendation_request served
   WHERE served.recommendation_request_id=r.served_request_id AND served.user_id=h.owner_user_id
    AND served.context=o.context AND served.seed=o.seed AND served.surface=o.surface
    AND served.interaction_watermark=o.interaction_watermark
    AND served.catalog_snapshot=o.catalog_snapshot AND served.pipeline_key=o.pipeline_key
    AND served.pipeline_version=o.pipeline_version
    AND served.pipeline_manifest_sha256=o.pipeline_manifest_sha256) THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_QUERY_BINDING' USING ERRCODE='23514'; END IF;
 -- The independent request bytes must equal the immutable original common
 -- JSON, whose direct size is admitted before it is hydrated for comparison.
 SELECT pg_column_size(q.request_document),pg_column_compression(q.request_document)
  INTO old_json_bytes,field FROM ml.recommendation_request q
  WHERE q.recommendation_request_id=p_original;
 IF old_json_bytes IS NULL OR field IS NOT NULL
  OR retained_compiler+2*old_json_bytes+2*stat[3]>8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
 phase_bytes:=greatest(phase_bytes,2*old_json_bytes+2*stat[3]+2*stat[1]+65536);
 PERFORM app_private.native_v3_compiler_grow(phase_depth,phase_bytes);
 IF original_doc IS DISTINCT FROM (SELECT q.request_document FROM ml.recommendation_request q
  WHERE q.recommendation_request_id=p_original) THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_REQUEST_RAW' USING ERRCODE='23514'; END IF;
 original_doc:=NULL;
 raw:=NULL;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 -- Independently retained ORIGINAL ranking, not a reconstruction from the
 -- received root. Every public field binds actual ordinary original rows.
 IF p.ranking_raw_bytes IS NULL OR p.ranking_raw_bytes NOT BETWEEN 1 AND 2097152 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_RANKING_RAW' USING ERRCODE='23514'; END IF;
 phase_depth:=app_private.native_v3_compiler_enter(p.ranking_raw_bytes+65536);
 SELECT pv.ranking_document INTO raw FROM ml.native_capture_replay_v3 pv
  WHERE pv.original_request_id=p_original;
 stat:=app_private.native_original_document_preflight_v3(raw);
 IF retained_compiler+2*stat[1]+2*stat[3]+1048576>8388608
  OR octet_length(raw)<>p.original_ranking_byte_length
  OR sha256(raw) IS DISTINCT FROM h.original_ranking_sha256 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_RANKING_RAW' USING ERRCODE='23514'; END IF;
 -- Two bounded item projections and numeric contribution conversion overlap.
 PERFORM app_private.native_v3_compiler_grow(phase_depth,2*stat[1]+2*stat[3]+2097152);
 original_doc:=convert_from(raw,'UTF8')::jsonb; raw:=NULL;
 IF original_doc-'items' IS DISTINCT FROM jsonb_build_object('schema_version',1,
  'kind','RECOMMENDATION_RANKING_V1') OR jsonb_typeof(original_doc->'items')<>'array'
  OR jsonb_array_length(original_doc->'items')<>n
  OR (SELECT count(*) FROM (SELECT 1 FROM ml.recommendation_item a
   WHERE a.recommendation_request_id=p_original LIMIT 101) bounded)<>n THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_RANKING_COUNT' USING ERRCODE='23514'; END IF;
 ranking_items:=original_doc->'items'; original_doc:=NULL;
 FOR v_rank IN 1..n LOOP
  IF NOT EXISTS(SELECT 1 FROM ml.recommendation_item a
   WHERE a.recommendation_request_id=p_original AND a.rank=v_rank
    AND pg_column_compression(a.contributions) IS NULL
    AND pg_column_compression(a.availability_snapshot) IS NULL
    AND pg_column_compression(a.item_provenance) IS NULL
    AND pg_column_compression(a.candidate_sources) IS NULL
    AND pg_column_compression(a.reason_codes) IS NULL
    AND pg_column_size(a.score)<=128 AND pg_column_size(a.contributions)<=65536
    AND pg_column_size(a.availability_snapshot)<=8192 AND pg_column_size(a.item_provenance)<=16384
    AND pg_column_size(a.candidate_sources)<=65536 AND pg_column_size(a.reason_codes)<=65536
    AND octet_length(a.explanation_code)<=4096) THEN
   RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_ITEM_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
  SELECT a.recording_id,a.score,a.contributions,a.candidate_sources,a.explanation_code,
   a.reason_codes,a.item_provenance,a.availability_snapshot,
   jsonb_build_object('recording_id',a.recording_id,'source_rank',a.rank,'score',a.score,
    'section',a.item_provenance->'section','reason_code',a.explanation_code,
    'reason_codes',a.reason_codes,'contributions',a.contributions,
    'artist_key',a.item_provenance->'artist_key','release_key',a.item_provenance->'release_key')
   AS value INTO item FROM ml.recommendation_item a
   WHERE a.recommendation_request_id=p_original AND a.rank=v_rank;
  -- The served graph already admitted and validated its bounded integer-unit
  -- item components. Convert their exact E8 values to the old numeric public
  -- contribution shape; this performs no score rounding or ranker execution.
  SELECT a.recording_id,a.score,a.contributions,a.candidate_sources,a.explanation_code,
   a.reason_codes,a.item_provenance INTO served_item FROM ml.recommendation_item a
   WHERE a.recommendation_request_id=r.served_request_id AND a.rank=v_rank;
  SELECT coalesce(jsonb_agg((value-'raw_score_units_e8')||jsonb_build_object('raw_score',
   (value->>'raw_score_units_e8')::numeric/100000000) ORDER BY ordinal),'[]'::jsonb)
   INTO expected_contributions
   FROM jsonb_array_elements(served_item.contributions) WITH ORDINALITY rows(value,ordinal);
  IF item.value IS DISTINCT FROM ranking_items->(v_rank-1)
   OR item.recording_id IS DISTINCT FROM served_item.recording_id
   OR item.score IS DISTINCT FROM served_item.score
   OR item.candidate_sources IS DISTINCT FROM served_item.candidate_sources
   OR item.explanation_code IS DISTINCT FROM served_item.explanation_code
   OR item.reason_codes IS DISTINCT FROM served_item.reason_codes
   OR item.item_provenance IS DISTINCT FROM served_item.item_provenance
   OR item.contributions IS DISTINCT FROM expected_contributions
   OR item.availability_snapshot IS DISTINCT FROM jsonb_build_object('snapshot_sha256',
    encode(r.original_availability_snapshot_sha256,'hex')) THEN
   RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_RANKING_BINDING' USING ERRCODE='23514'; END IF;
  expected_contributions:=NULL; item.value:=NULL; item.contributions:=NULL;
  item.candidate_sources:=NULL; item.explanation_code:=NULL; item.reason_codes:=NULL;
  item.item_provenance:=NULL; item.availability_snapshot:=NULL;
  served_item.contributions:=NULL; served_item.candidate_sources:=NULL;
  served_item.reason_codes:=NULL; served_item.item_provenance:=NULL;
  served_item.explanation_code:=NULL;
 END LOOP;
 ranking_items:=NULL;
 raw:=NULL; original_doc:=NULL; expected_contributions:=NULL;
 item:=NULL; served_item:=NULL;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 -- Actual APIR3 ORIGINAL rows are independently framed; a stored scalar,
 -- including an old zero fixture, is never its own resource-length proof.
 -- The fused measurer requires the same repeatable-read snapshot and owns
 -- its512KiB output slot plus per-row child on this existing compiler stack.
 -- Callers prepay this extra complete measurement, including refusal paths.
 IF p.original_items_byte_length IS DISTINCT FROM
  app_private.native_v3_item_rows_byte_length(h.owner_user_id,p_original,'ORIGINAL') THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_ITEMS_BYTES' USING ERRCODE='23514'; END IF;
 IF p.pipeline_raw_bytes IS NULL OR p.pipeline_raw_bytes NOT BETWEEN 1 AND 65536 THEN
  RAISE EXCEPTION 'NATIVE_V3_ORIGINAL_PIPELINE_RAW' USING ERRCODE='23514'; END IF;
 phase_depth:=app_private.native_v3_compiler_enter(p.pipeline_raw_bytes+65536);
 SELECT pv.pipeline_document INTO raw FROM ml.native_capture_replay_v3 pv
  WHERE pv.original_request_id=p_original;
 PERFORM app_private.native_v3_assert_current_baseline_pipeline(raw); raw:=NULL;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 -- Fixed old temporal row projects this exact source3 temporal object, never
 -- an independently reconstructed RFC document with different float meaning.
 phase_depth:=app_private.native_v3_compiler_enter(2*h.temporal_json_bytes+65536);
 SELECT tm.snapshot_sha256,tm.baseline_input_snapshot_sha256,tm.cutoff_at_ms,
  tm.interaction_watermark,tm.feature_policy_sha256,tm.source_event_count,tm.dimension_count,
  tm.recommendation_input_snapshot_id,tm.created_at,tm.retained_until,tm.snapshot_document
  INTO temporal_row FROM ml.recommendation_temporal_snapshot tm
  WHERE tm.recommendation_temporal_snapshot_id=h.temporal_snapshot_id
   AND tm.user_id=h.owner_user_id;
 IF temporal_row.snapshot_sha256 IS DISTINCT FROM h.temporal_snapshot_sha256
  OR temporal_row.baseline_input_snapshot_sha256 IS DISTINCT FROM h.baseline_snapshot_sha256
  OR temporal_row.cutoff_at_ms IS DISTINCT FROM h.cutoff_at_ms
  OR temporal_row.interaction_watermark IS DISTINCT FROM h.native_interaction_watermark
  OR temporal_row.feature_policy_sha256 IS DISTINCT FROM h.feature_policy_sha256
  OR temporal_row.source_event_count IS DISTINCT FROM source_count
  OR temporal_row.dimension_count IS DISTINCT FROM d
  OR temporal_row.recommendation_input_snapshot_id IS DISTINCT FROM h.baseline_snapshot_id
  OR temporal_row.created_at IS DISTINCT FROM h.created_at
  OR temporal_row.retained_until<h.expires_at
  OR temporal_row.snapshot_document IS DISTINCT FROM temporal
  THEN RAISE EXCEPTION 'NATIVE_V3_ORDINARY_TEMPORAL' USING ERRCODE='23514'; END IF;
 temporal_row.snapshot_document:=NULL;
 PERFORM app_private.native_v3_compiler_exit(phase_depth);
 -- Bounded accepted-source equality and descriptor membership are a real
 -- installed helper, not a raw hash, boolean marker, or historical receipt.
 -- Release all obsolete caller subgraphs before a nested facts/causal/root
 -- compiler. Its actual shared compiler frame must include the retained
 -- source argument and every additional nested allocation, not a new8MiB.
 raw:=NULL; part:=NULL; source_raw:=NULL; temporal:=NULL;
 profile_bits:=NULL; dimensions:=NULL; numeric_profile:=NULL; numeric_dimensions:=NULL;
 original_doc:=NULL; ranking_items:=NULL; snapshot_doc:=NULL;
 row_bits:=NULL; row_numeric:=NULL; eligible:=NULL; ids:=NULL; track:=NULL;
 PERFORM app_private.native_v3_compiler_exit(subgraph_depth);
 PERFORM app_private.native_v3_assert_source_facts(p_original,source);
 source:=NULL; calculated:=NULL; profile_old_sha:=NULL; source_membership:=NULL;
 h:=NULL; r:=NULL; o:=NULL; s:=NULL; p:=NULL; temporal_row:=NULL;
 cursor_row:=NULL; index_entry:=NULL; item:=NULL; served_item:=NULL;
 PERFORM app_private.native_v3_compiler_exit(compiler_depth);
 PERFORM app_private.native_v3_compiler_exit(membership_depth);
END $$;

REVOKE ALL ON FUNCTION app_private.native_v3_original_profile_number(jsonb,jsonb,text),
 app_private.native_v3_source_raw_member(bytea,text,text),
 app_private.assert_native_original_payloads_v3(uuid) FROM PUBLIC;
"""
)

NATIVE_ORIGINAL_PAYLOADS_V3_INSTALL_SQL = (
    NATIVE_ORIGINAL_PAYLOADS_V3_INSTALL_SQL.replace(
        "__FEATURE_POLICY__", DEFAULT_ADAPTIVE_FEATURE_POLICY.content_sha256
    )
    .replace("__FEATURE_POLICY_KEY__", DEFAULT_ADAPTIVE_FEATURE_POLICY.key.replace("'", "''"))
    .replace(
        "__FEATURE_POLICY_VERSION__", DEFAULT_ADAPTIVE_FEATURE_POLICY.version.replace("'", "''")
    )
)

NATIVE_ORIGINAL_PAYLOADS_V3_DROP_SQL = r"""
DROP FUNCTION app_private.assert_native_original_payloads_v3(uuid);
DROP FUNCTION app_private.native_v4_check_baseline(bytea,jsonb,uuid,bigint,integer);
DROP FUNCTION app_private.native_v3_source_raw_member(bytea,text,text);
DROP FUNCTION app_private.native_v3_original_profile_number(jsonb,jsonb,text);
"""
