# ruff: noqa: E501
"""NEW source3 fact binding, installed only by an explicit coherent SQL owner.

The caller has already validated the closed raw26-key source, bound its raw SHA
to the actual capture, and prepaid compiler copies and ALL nested normalized
validator/causal graph work on the existing operation ledger. This helper owns
no allowance and grants no consent. It proves fact/index/accepted-row equality;
derived evidence and R1 math remain separate mandatory checks.

The admitted source itself stays in the caller's compiler frame. Local source
projections/baseline copies and direct normalized payload/ACK/target copies enter
additional shared compiler frames before allocation, and leave after dropping
their actual aliases. This does not claim an independently bounded legacy2
causal validator graph: that remains a separate mandatory caller prerequisite.

Current and actual parent normalized payload/target/ACK flat-scalar gates run
before the old normalized validator can serialize Numeric. Their additional
native_normalized_scalar_work_v3 cost must be prepaid for every actual call,
including refusal, on the caller's same existing work owner. This is not a
claim that common request/item/legacy replay expansion is closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

FACT_SOURCE_LIMIT_V3: Final = 10_000
FACT_TRACK_LIMIT_V3: Final = 5_000
FACT_COMPILER_LIMIT_V3: Final = 8_388_608
FACT_MEMBERSHIP_WORKSPACE_V3: Final = 524_288
FACT_SQL_REQUIRED_FUNCTIONS_V3: Final = (
    "app_private.assert_normalized_listen_acceptance(uuid)",
    "app_private.native_source3_float_matches_v3(jsonb,double precision,text,boolean)",
    "app_private.native_source3_scan_v3(bytea,integer,text,integer)",
    "app_private.native_source3_string_v3(bytea,integer)",
    "app_private.native_v3_compiler_enter(bigint)",
    "app_private.native_v3_compiler_grow(integer,bigint)",
    "app_private.native_v3_compiler_exit(integer)",
    "app_private.native_v3_assert_flat_scalar_resource(jsonb)",
)


@dataclass(frozen=True, slots=True)
class NativeOriginalFactsLocalWorkV3:
    """Local only; nested installed validators/causal graphs are ADDITIONAL."""

    byte_work: int
    node_visits: int
    normalized_validator_calls: int
    compiler_owned_bytes: int


def native_original_facts_local_work_v3(
    *, source_count: int, track_count: int, source_jsonb_bytes: int, baseline_jsonb_bytes: int
) -> NativeOriginalFactsLocalWorkV3:
    for value, maximum in (
        (source_count, FACT_SOURCE_LIMIT_V3),
        (track_count, FACT_TRACK_LIMIT_V3),
        (source_jsonb_bytes, FACT_COMPILER_LIMIT_V3),
        (baseline_jsonb_bytes, FACT_COMPILER_LIMIT_V3),
    ):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError("NATIVE_V3_FACT_WORK_INPUT")
    if not source_jsonb_bytes or not baseline_jsonb_bytes:
        raise ValueError("NATIVE_V3_FACT_WORK_INPUT")
    baseline_workspace = baseline_jsonb_bytes * (2 if source_count else 1)
    owned = 2 * source_jsonb_bytes + baseline_workspace + 128 * (source_count + 1) + 65_536
    if owned > FACT_COMPILER_LIMIT_V3:
        raise ValueError("NATIVE_V3_FACT_COMPILER_BOUND")
    # Scalar recording-ID path probes do not materialize each full track row.
    # Two bounded discovery/index passes, source+baseline projections and each
    # exact accepted-row/causal projection are included. Physical query plans,
    # page IO and nested normalized validators are not claimed by this bound.
    return NativeOriginalFactsLocalWorkV3(
        8 * (source_jsonb_bytes + baseline_jsonb_bytes)
        + 256 * source_count * track_count
        + 32_768 * (source_count + 1)
        + 65_536,
        8 * (source_jsonb_bytes + baseline_jsonb_bytes)
        + 64 * source_count * track_count
        + 2_048 * (source_count + 1)
        + 4_096,
        source_count,
        owned,
    )


def native_original_facts_validator_payload_workspace_v3(
    payload_bytes: int, ack_bytes: int, target_bytes: int
) -> int:
    """Direct row-payload child only; causal common graph is ADDITIONAL."""
    for value, minimum, maximum in (
        (payload_bytes, 1, 8192),
        (ack_bytes, 1, 4092),
        (target_bytes, 0, 8188),
    ):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError("NATIVE_V3_FACT_PAYLOAD_COMPILER_INPUT")
    return 8 * (payload_bytes + ack_bytes + target_bytes + 12 + 8192 + 64) + 65_536


def native_source_membership_work_v3(raw_bytes: int, source_count: int) -> tuple[int, int, int]:
    if (
        type(raw_bytes) is not int
        or not 1 <= raw_bytes <= 2_097_152
        or type(source_count) is not int
        or not 0 <= source_count <= FACT_SOURCE_LIMIT_V3
    ):
        raise ValueError("NATIVE_V3_MEMBERSHIP_WORK_INPUT")
    size = 2 if source_count == 0 else 39 * source_count + 1
    return (
        144 * raw_bytes + 16_777_216 + 3 * size + 256 * source_count + 16_384,
        16 * (raw_bytes + 1) + 4096 + 16 * (source_count + 1),
        size + 16_384,
    )


_REQUIRED_SQL: Final = (
    "DO $$ BEGIN\n"
    + "\n".join(
        "IF to_regprocedure('" + signature + "') IS NULL THEN "
        "RAISE EXCEPTION 'NATIVE_V3_FACT_PREDECESSOR_MISSING' USING ERRCODE='55000'; END IF;"
        for signature in FACT_SQL_REQUIRED_FUNCTIONS_V3
    )
    + "\nEND $$;\n"
)

NATIVE_ORIGINAL_FACTS_V3_INSTALL_SQL: Final = (
    _REQUIRED_SQL
    + r"""
CREATE FUNCTION app_private.native_v3_source_membership_sha256(raw bytea,p_count integer)
RETURNS bytea LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE start integer; size integer; span bytea; ordinal integer; offset_in_span integer;
 text_id text; current_id uuid; previous_id uuid; cursor_pos integer:=1;
 stage integer:=1; visited integer:=0; path text:='$'; wanted text:='temporal_snapshot';
 member record; member_path text; value_start integer; measured bigint[];
BEGIN
 -- Caller MUST first run the frozen closed-source raw preflight. This bounded
 -- span helper is a hash, not raw admission or independent membership authority.
 IF raw IS NULL OR octet_length(raw) NOT BETWEEN 1 AND 2097152
  OR p_count IS NULL OR p_count NOT BETWEEN 0 AND 10000 THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_INPUT' USING ERRCODE='23514'; END IF;
 IF get_byte(raw,0)<>123 THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_KEY' USING ERRCODE='23514'; END IF;
 -- Walk ONLY installed root/temporal member paths. Profile dimensions also
 -- contain source_event_ids; a global substring search would bind the wrong
 -- member or reject legitimate D>0 sources. No suffix BYTEA copy is made.
 LOOP
  visited:=visited+1;
  IF visited>45 OR cursor_pos>=octet_length(raw) OR get_byte(raw,cursor_pos)=125 THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_KEY' USING ERRCODE='23514'; END IF;
  SELECT next_position,decoded_value INTO member
   FROM app_private.native_source3_string_v3(raw,cursor_pos);
  IF octet_length(member.decoded_value)>64 OR get_byte(raw,member.next_position)<>58 THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_KEY' USING ERRCODE='23514'; END IF;
  value_start:=member.next_position+1; member_path:=path||'.'||member.decoded_value;
  IF member.decoded_value=wanted THEN
   IF stage=1 THEN
    IF get_byte(raw,value_start)<>123 THEN
     RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_KEY' USING ERRCODE='23514'; END IF;
    cursor_pos:=value_start+1; path:='$.temporal_snapshot';
    stage:=2; wanted:='source_event_ids'; CONTINUE;
   END IF;
   measured:=app_private.native_source3_scan_v3(raw,value_start,member_path,stage);
   start:=value_start+1; size:=measured[1]::integer-value_start;
   measured:=NULL; member:=NULL; EXIT;
  END IF;
  measured:=app_private.native_source3_scan_v3(raw,value_start,member_path,stage);
  cursor_pos:=measured[1]::integer; measured:=NULL;
  IF cursor_pos>=octet_length(raw) OR get_byte(raw,cursor_pos)<>44 THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_KEY' USING ERRCODE='23514'; END IF;
  cursor_pos:=cursor_pos+1;
 END LOOP;
 -- The scanner's recursive compiler scratch is gone before this hash phase.
 -- Only one canonical UUID-array slice plus fixed scalar scratch remains.
 IF size<>(CASE WHEN p_count=0 THEN 2 ELSE 39*p_count+1 END) THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_BOUND' USING ERRCODE='23514'; END IF;
 IF start+size-1>octet_length(raw) OR size+16384>524288 THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_BOUND' USING ERRCODE='23514'; END IF;
 span:=substring(raw FROM start FOR size);
 IF get_byte(span,0)<>91 OR get_byte(span,size-1)<>93 THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_SHAPE' USING ERRCODE='23514'; END IF;
 IF p_count>0 THEN
  FOR ordinal IN 0..p_count-1 LOOP
   offset_in_span:=1+39*ordinal;
   IF get_byte(span,offset_in_span)<>34 OR get_byte(span,offset_in_span+37)<>34
    OR get_byte(span,offset_in_span+38)<>(CASE WHEN ordinal=p_count-1 THEN 93 ELSE 44 END) THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_SHAPE' USING ERRCODE='23514'; END IF;
   text_id:=convert_from(substring(span FROM offset_in_span+2 FOR 36),'UTF8');
   current_id:=text_id::uuid;
   IF current_id::text IS DISTINCT FROM text_id
    OR (previous_id IS NOT NULL AND current_id<=previous_id) THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_MEMBERSHIP_ORDER' USING ERRCODE='23514'; END IF;
   previous_id:=current_id;
  END LOOP;
 END IF;
 RETURN sha256(span);
END $$;

CREATE FUNCTION app_private.native_v3_assert_source_facts(original uuid,admitted_source jsonb)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE h record; e record; actual record; link record; payload_meta record; discovered record;
 facts jsonb; ids jsonb; tracks jsonb; fact jsonb; expected_causality jsonb;
 expected_dimensions jsonb; source_bytes bigint; baseline_bytes bigint; baseline_workspace bigint;
 count integer; ordinal integer; previous_id uuid; current_event uuid; discovered_count integer:=0;
 discovered_watermark bigint:=0; retained_ms bigint; received_ms bigint;
 occurred_ms bigint; causal_deadline bigint; track_ordinal integer; matches integer;
 selected_track integer; artist text; release_key text; has_causality boolean;
 compiler_depth integer; validator_depth integer;
 parent_validator_depth integer; parent_link_count integer; parent_event uuid;
 scalar_payloads record; parent_link record; parent_payload_meta record;
BEGIN
 IF original IS NULL OR jsonb_typeof(admitted_source) IS DISTINCT FROM 'object'
  OR pg_column_compression(admitted_source) IS NOT NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_INPUT' USING ERRCODE='23514'; END IF;
 -- No whole-row or payload projection before the metadata bound.
 SELECT b.owner_user_id,b.baseline_snapshot_id,b.source_event_count,b.cutoff_at_ms,
  b.native_interaction_watermark,b.source_snapshot_track_count,
  pg_column_compression(s.snapshot_document) AS compression,
  pg_column_size(s.snapshot_document) AS baseline_size
 INTO h FROM ml.native_original_capture_v3 b
 JOIN ml.native_original_capture_format f ON f.original_request_id=b.original_request_id
  AND f.owner_user_id=b.owner_user_id AND f.original_capture_format_version=3
 JOIN ml.recommendation_input_snapshot s ON s.recommendation_input_snapshot_id=b.baseline_snapshot_id
  AND s.user_id=b.owner_user_id
 WHERE b.original_request_id=original AND b.original_capture_format_version=3
  AND b.source_codec='NATIVE_SOURCE3_F64BITS_JSON_SAFE53_V1'
  AND b.source_codec_sha256=decode(
   'eda9439b265c5e92934f8f62a333782579a14aed116385271f158819045581a2','hex');
 IF NOT FOUND OR h.source_event_count NOT BETWEEN 0 AND 10000
  OR h.source_snapshot_track_count NOT BETWEEN 0 AND 5000 OR h.compression IS NOT NULL
  OR h.baseline_size IS NULL OR h.baseline_size NOT BETWEEN 1 AND 4194300
  OR admitted_source->>'recommendation_request_id' IS DISTINCT FROM original::text
  OR admitted_source->>'owner_user_id' IS DISTINCT FROM h.owner_user_id::text THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_METADATA' USING ERRCODE='23514'; END IF;
 source_bytes:=pg_column_size(admitted_source)::bigint+4;
 baseline_bytes:=h.baseline_size::bigint+4;
 -- Empty histories only need to prove the shape and length of the array.
 -- JSONPath type() and size() return scalars from the detoasted document.
 -- Nonempty histories also own the extracted array used for per-fact matching.
 baseline_workspace:=baseline_bytes*(CASE WHEN h.source_event_count=0 THEN 1 ELSE 2 END);
 IF 2*source_bytes+baseline_workspace+128*(h.source_event_count+1)+65536>8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_COMPILER' USING ERRCODE='23514'; END IF;
 -- SOURCE remains owned by the caller. This is ADDITIONAL retained local
 -- facts/IDs/current-fact + baseline detoast/tracks + bounded scalar scratch.
 compiler_depth:=app_private.native_v3_compiler_enter(
  2*source_bytes+baseline_workspace+128*(h.source_event_count+1)+65536);
 IF compiler_depth<=1 THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_CALLER_FRAME_REQUIRED' USING ERRCODE='23514'; END IF;
 facts:=admitted_source#>'{temporal_snapshot,facts}';
 ids:=admitted_source#>'{temporal_snapshot,source_event_ids}';
 IF h.source_event_count=0 THEN
  IF NOT EXISTS (SELECT 1 FROM ml.recommendation_input_snapshot s
   WHERE s.recommendation_input_snapshot_id=h.baseline_snapshot_id AND s.user_id=h.owner_user_id
    AND jsonb_path_query_first(s.snapshot_document,
     'strict $.tracks ? (@.type() == "array").size()')=to_jsonb(h.source_snapshot_track_count)) THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_COUNTS' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT s.snapshot_document->'tracks' INTO tracks FROM ml.recommendation_input_snapshot s
   WHERE s.recommendation_input_snapshot_id=h.baseline_snapshot_id AND s.user_id=h.owner_user_id;
 END IF;
 IF jsonb_typeof(facts) IS DISTINCT FROM 'array' OR jsonb_typeof(ids) IS DISTINCT FROM 'array'
  OR (h.source_event_count>0 AND jsonb_typeof(tracks) IS DISTINCT FROM 'array')
  OR jsonb_array_length(facts)<>h.source_event_count OR jsonb_array_length(ids)<>h.source_event_count
  OR (h.source_event_count>0 AND jsonb_array_length(tracks)<>h.source_snapshot_track_count) THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_COUNTS' USING ERRCODE='23514'; END IF;
 count:=h.source_event_count;
 IF count>0 THEN
  FOR ordinal IN 0..count-1 LOOP
   -- One keyset row, all14 exact source-index columns, never a payload vector.
   SELECT x.original_request_id,x.source_event_id,x.owner_user_id,x.source_request_sha256,
    x.normalized_payload_sha256,x.device_id,x.journal_epoch,x.device_sequence,x.server_sequence,
    x.event_type,x.association_id,x.causal_request_id,x.impression_event_id,x.retained_until
   INTO e FROM ml.native_capture_source_event_v3 x
   WHERE x.original_request_id=original AND x.owner_user_id=h.owner_user_id
    AND (previous_id IS NULL OR x.source_event_id>previous_id)
   ORDER BY x.source_event_id LIMIT 1;
   IF NOT FOUND THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_INDEX_INCOMPLETE' USING ERRCODE='23514'; END IF;
   current_event:=e.source_event_id; previous_id:=current_event; fact:=facts->ordinal;
   IF ids->>ordinal IS DISTINCT FROM current_event::text
    OR fact->>'event_id' IS DISTINCT FROM current_event::text THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_ORDER' USING ERRCODE='23514'; END IF;
   -- Admitted normalized payload/ACK storage precedes the installed validator's
   -- actual graph and JSON work. Its work must be prepaid in ADDITION to local.
   SELECT pg_column_compression(s.payload) AS payload_compression,
    pg_column_size(s.payload) AS payload_size,
    pg_column_compression(i.terminal_ack) AS ack_compression,
    pg_column_size(i.terminal_ack) AS ack_size,
    pg_column_compression(i.payload) AS inbox_compression,pg_column_size(i.payload) AS inbox_size,
    pg_column_compression(u.payload) AS target_compression,
    pg_column_size(u.payload) AS target_size,
    coalesce(octet_length(s.event_type),0)::bigint+coalesce(octet_length(s.aggregate_type),0)+
     coalesce(octet_length(s.operation),0)+coalesce(octet_length(i.event_type),0)+
     coalesce(octet_length(i.aggregate_type),0)+coalesce(octet_length(i.idempotency_key),0)+
     coalesce(octet_length(i.error_code),0)+coalesce(octet_length(i.apply_status),0)+
     coalesce(octet_length(u.event_type),0)+coalesce(octet_length(l.context),0)+
     coalesce(octet_length(l.event_origin),0)+coalesce(octet_length(l.explicit_feedback),0)
      AS scalar_size,
    octet_length(s.normalized_payload_sha256) AS sync_hash_size,
    octet_length(i.normalized_payload_sha256) AS inbox_hash_size,
    octet_length(i.request_hash) AS request_hash_size
   INTO payload_meta FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
   LEFT JOIN library.user_interaction_event u ON u.interaction_id=s.event_id
    AND u.user_id=h.owner_user_id
   LEFT JOIN library.listening_event l ON l.listening_event_id=s.event_id
    AND l.user_id=h.owner_user_id
   WHERE s.event_id=current_event AND s.user_id=h.owner_user_id AND i.user_id=h.owner_user_id
    AND i.apply_status='APPLIED' AND i.ingress_validation_version=2
    AND s.ingress_validation_version=2;
   IF NOT FOUND OR payload_meta.payload_compression IS NOT NULL
    OR payload_meta.ack_compression IS NOT NULL OR payload_meta.payload_size IS NULL
    OR payload_meta.payload_size NOT BETWEEN 1 AND 8192 OR payload_meta.ack_size IS NULL
    OR payload_meta.ack_size NOT BETWEEN 1 AND 4092
    OR payload_meta.target_compression IS NOT NULL
    OR (payload_meta.target_size IS NOT NULL AND payload_meta.target_size NOT BETWEEN 1 AND 8188)
    OR payload_meta.inbox_compression IS NOT NULL OR payload_meta.inbox_size NOT BETWEEN 1 AND 64
    OR payload_meta.scalar_size>8192 OR payload_meta.sync_hash_size IS DISTINCT FROM 32
    OR payload_meta.inbox_hash_size IS DISTINCT FROM 32 OR payload_meta.request_hash_size IS DISTINCT FROM 32 THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_PAYLOAD_BOUND' USING ERRCODE='23514'; END IF;
   -- Direct normalized s/i/target copies plus construction scratch. Nested
   -- historical Source3 assertion enters its OWN frame while this remains live.
   -- Old2 causal expansion needs its separately proven caller admission.
   validator_depth:=app_private.native_v3_compiler_enter(
    8*(payload_meta.payload_size::bigint+payload_meta.ack_size::bigint+
     coalesce(payload_meta.target_size,0)::bigint+12+8192+64)+65536);
   -- Actual admitted current rows, never a claimed source-document payload.
   SELECT s.payload AS sync_payload,i.terminal_ack,u.payload AS target_payload
    INTO scalar_payloads FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
    LEFT JOIN library.user_interaction_event u ON u.interaction_id=s.event_id
     AND u.user_id=h.owner_user_id
    WHERE s.event_id=current_event AND s.user_id=h.owner_user_id AND i.user_id=h.owner_user_id
     AND i.apply_status='APPLIED';
   PERFORM app_private.native_v3_assert_flat_scalar_resource(scalar_payloads.sync_payload);
   PERFORM app_private.native_v3_assert_flat_scalar_resource(scalar_payloads.terminal_ack);
   IF scalar_payloads.target_payload IS NOT NULL THEN
    PERFORM app_private.native_v3_assert_flat_scalar_resource(scalar_payloads.target_payload);
   END IF;
   scalar_payloads:=NULL;
   -- Find the actual old validator parent path before selecting parent JSONB.
   SELECT count(*) INTO parent_link_count FROM (
    SELECT 1 FROM ml.recommendation_attribution_association a
    WHERE a.interaction_id=current_event OR a.listening_event_id=current_event LIMIT 2
   ) bounded;
   IF parent_link_count>1 THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_PARENT_RESOURCE_PATH' USING ERRCODE='23514'; END IF;
   SELECT a.owner_user_id=h.owner_user_id AS owner_ok,
    a.kind IN ('LISTEN','FEEDBACK') AS needs_parent,
    pa.owner_user_id=h.owner_user_id AS parent_owner_ok,pa.kind='IMPRESSION' AS parent_kind_ok,
    pa.interaction_id AS parent_event_id INTO parent_link
   FROM ml.recommendation_attribution_association a
   LEFT JOIN ml.recommendation_attribution_association pa
    ON pa.association_id=a.impression_association_id
   WHERE a.interaction_id=current_event OR a.listening_event_id=current_event;
   parent_event:=NULL; parent_validator_depth:=NULL;
   IF parent_link_count=1 AND parent_link.owner_ok IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_PARENT_RESOURCE_PATH' USING ERRCODE='23514'; END IF;
   IF parent_link.needs_parent IS TRUE THEN
    IF parent_link.parent_owner_ok IS DISTINCT FROM true
     OR parent_link.parent_kind_ok IS DISTINCT FROM true OR parent_link.parent_event_id IS NULL THEN
     RAISE EXCEPTION 'NATIVE_V3_SOURCE_PARENT_RESOURCE_PATH' USING ERRCODE='23514'; END IF;
    parent_event:=parent_link.parent_event_id;
    SELECT pg_column_compression(s.payload) AS payload_compression,
     pg_column_size(s.payload) AS payload_size,
     pg_column_compression(i.terminal_ack) AS ack_compression,
     pg_column_size(i.terminal_ack) AS ack_size,
     pg_column_compression(i.payload) AS inbox_compression,pg_column_size(i.payload) AS inbox_size,
     pg_column_compression(u.payload) AS target_compression,pg_column_size(u.payload) AS target_size,
     coalesce(octet_length(s.event_type),0)::bigint+coalesce(octet_length(s.aggregate_type),0)+
      coalesce(octet_length(s.operation),0)+coalesce(octet_length(i.event_type),0)+
      coalesce(octet_length(i.aggregate_type),0)+coalesce(octet_length(i.idempotency_key),0)+
      coalesce(octet_length(i.error_code),0)+coalesce(octet_length(i.apply_status),0)+
      coalesce(octet_length(u.event_type),0) AS scalar_size,
     octet_length(s.normalized_payload_sha256) AS sync_hash_size,
     octet_length(i.normalized_payload_sha256) AS inbox_hash_size,
     octet_length(i.request_hash) AS request_hash_size
    INTO parent_payload_meta FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
    JOIN library.user_interaction_event u ON u.interaction_id=s.event_id
     AND u.user_id=h.owner_user_id
    WHERE s.event_id=parent_event AND s.user_id=h.owner_user_id AND i.user_id=h.owner_user_id
     AND i.apply_status='APPLIED' AND i.ingress_validation_version=2
     AND s.ingress_validation_version=2 AND u.attribution_contract_version=2
     AND s.event_type='RECOMMENDATION_IMPRESSION_RECORDED' AND i.event_type=s.event_type
     AND u.event_type=s.event_type;
    IF NOT FOUND OR parent_payload_meta.payload_compression IS NOT NULL
     OR parent_payload_meta.ack_compression IS NOT NULL OR parent_payload_meta.payload_size IS NULL
     OR parent_payload_meta.payload_size NOT BETWEEN 1 AND 8192 OR parent_payload_meta.ack_size IS NULL
     OR parent_payload_meta.ack_size NOT BETWEEN 1 AND 4092
     OR parent_payload_meta.target_compression IS NOT NULL OR parent_payload_meta.target_size IS NULL
     OR parent_payload_meta.target_size NOT BETWEEN 1 AND 8188
     OR parent_payload_meta.inbox_compression IS NOT NULL
     OR parent_payload_meta.inbox_size NOT BETWEEN 1 AND 64
     OR parent_payload_meta.scalar_size>8192 OR parent_payload_meta.sync_hash_size IS DISTINCT FROM 32
     OR parent_payload_meta.inbox_hash_size IS DISTINCT FROM 32
     OR parent_payload_meta.request_hash_size IS DISTINCT FROM 32 THEN
     RAISE EXCEPTION 'NATIVE_V3_SOURCE_PARENT_PAYLOAD_BOUND' USING ERRCODE='23514'; END IF;
    parent_validator_depth:=app_private.native_v3_compiler_enter(
     8*(parent_payload_meta.payload_size::bigint+parent_payload_meta.ack_size::bigint+
      parent_payload_meta.target_size::bigint+12+8192+64)+65536);
    SELECT s.payload AS sync_payload,i.terminal_ack,u.payload AS target_payload
     INTO scalar_payloads FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
     JOIN library.user_interaction_event u ON u.interaction_id=s.event_id
      AND u.user_id=h.owner_user_id
     WHERE s.event_id=parent_event AND s.user_id=h.owner_user_id AND i.user_id=h.owner_user_id
      AND i.apply_status='APPLIED';
    PERFORM app_private.native_v3_assert_flat_scalar_resource(scalar_payloads.sync_payload);
    PERFORM app_private.native_v3_assert_flat_scalar_resource(scalar_payloads.terminal_ack);
    PERFORM app_private.native_v3_assert_flat_scalar_resource(scalar_payloads.target_payload);
    scalar_payloads:=NULL; parent_payload_meta:=NULL;
   END IF;
   parent_link:=NULL;
   PERFORM app_private.assert_normalized_listen_acceptance(current_event);
   IF parent_validator_depth IS NOT NULL THEN
    PERFORM app_private.native_v3_compiler_exit(parent_validator_depth);
   END IF;
   payload_meta:=NULL;
   PERFORM app_private.native_v3_compiler_exit(validator_depth);
   SELECT s.event_type,s.server_sequence,s.created_at,i.device_id,i.journal_epoch,
    i.device_sequence,i.occurred_at,i.received_at,i.request_hash,i.normalized_payload_sha256,
    CASE WHEN s.event_type='LISTENING_EVENT_RECORDED' THEN l.recording_id ELSE u.recording_id END
     AS recording_id,l.listening_event_id AS listen_id,u.interaction_id AS interaction_id,
    l.event_origin,l.played_ms,l.completion_ratio::double precision AS completion,
    coalesce(l.excluded_from_taste,false) AS excluded,l.explicit_feedback,
    l.attribution_contract_version,l.attribution_closed,u.payload->>'feedback_type' AS feedback
   INTO actual FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
   LEFT JOIN library.listening_event l ON l.listening_event_id=s.event_id AND l.user_id=h.owner_user_id
   LEFT JOIN library.user_interaction_event u ON u.interaction_id=s.event_id AND u.user_id=h.owner_user_id
   WHERE s.event_id=current_event AND s.user_id=h.owner_user_id AND i.user_id=h.owner_user_id
    AND s.ingress_validation_version=2 AND i.ingress_validation_version=2
    AND i.apply_status='APPLIED' AND i.error_code IS NULL AND s.operation='UPSERT'
    AND s.server_row_version=1 AND s.origin_device_id=i.device_id
    AND s.ingress_journal_epoch=i.journal_epoch AND s.ingress_device_sequence=i.device_sequence
    AND s.event_type=i.event_type AND s.schema_version=1 AND i.schema_version=1
    AND s.aggregate_id=s.event_id AND i.aggregate_id=s.event_id
    AND s.aggregate_type=i.aggregate_type AND s.normalized_payload_sha256=i.normalized_payload_sha256
    AND i.payload='{}'::jsonb
    AND floor(extract(epoch FROM i.received_at)*1000)<=h.cutoff_at_ms
    AND floor(extract(epoch FROM s.created_at)*1000)<=h.cutoff_at_ms
    AND NOT EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt er
     WHERE er.user_id=h.owner_user_id AND er.source_event_id=s.event_id);
   IF NOT FOUND OR actual.recording_id IS NULL OR actual.server_sequence>h.native_interaction_watermark
    OR (actual.event_type='LISTENING_EVENT_RECORDED' AND actual.interaction_id IS NOT NULL)
    OR (actual.event_type<>'LISTENING_EVENT_RECORDED' AND actual.listen_id IS NOT NULL)
    OR actual.event_type NOT IN ('LISTENING_EVENT_RECORDED','RECOMMENDATION_IMPRESSION_RECORDED',
     'RECOMMENDATION_FEEDBACK_RECORDED') THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_ACCEPTED' USING ERRCODE='23514'; END IF;
   received_ms:=floor(extract(epoch FROM actual.received_at)*1000)::bigint;
   occurred_ms:=floor(extract(epoch FROM actual.occurred_at)*1000)::bigint;
   IF e.device_id IS DISTINCT FROM actual.device_id OR e.journal_epoch IS DISTINCT FROM actual.journal_epoch
    OR e.device_sequence IS DISTINCT FROM actual.device_sequence OR e.server_sequence IS DISTINCT FROM actual.server_sequence
    OR e.event_type IS DISTINCT FROM actual.event_type OR e.source_request_sha256 IS DISTINCT FROM actual.request_hash
    OR e.normalized_payload_sha256 IS DISTINCT FROM actual.normalized_payload_sha256
    OR fact->>'owner' IS DISTINCT FROM h.owner_user_id::text
    OR fact->>'device' IS DISTINCT FROM actual.device_id::text
    OR fact->>'journal_epoch' IS DISTINCT FROM actual.journal_epoch::text
    OR fact->'device_sequence' IS DISTINCT FROM to_jsonb(actual.device_sequence)
    OR fact->'server_sequence' IS DISTINCT FROM to_jsonb(actual.server_sequence)
    OR fact->>'event_type' IS DISTINCT FROM actual.event_type
    OR fact->>'recording_id' IS DISTINCT FROM actual.recording_id::text
    OR fact->'occurred_at_ms' IS DISTINCT FROM to_jsonb(occurred_ms)
    OR fact->'received_at_ms' IS DISTINCT FROM to_jsonb(received_ms)
    OR fact->>'source_request_sha256' IS DISTINCT FROM encode(actual.request_hash,'hex')
    OR fact->>'normalized_payload_sha256' IS DISTINCT FROM encode(actual.normalized_payload_sha256,'hex')
    OR fact->'event_origin' IS DISTINCT FROM coalesce(to_jsonb(actual.event_origin),'null'::jsonb)
    OR fact->'played_ms' IS DISTINCT FROM coalesce(to_jsonb(actual.played_ms),'null'::jsonb)
    OR NOT app_private.native_source3_float_matches_v3(fact->'completion_ratio',actual.completion,'unit',true)
    OR fact->'excluded_from_taste' IS DISTINCT FROM to_jsonb(actual.excluded)
    OR fact->'feedback' IS DISTINCT FROM coalesce(to_jsonb(actual.feedback),'null'::jsonb)
    OR fact->>'explicit_feedback' IS DISTINCT FROM coalesce(actual.explicit_feedback,'NONE') THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_VALUE' USING ERRCODE='23514'; END IF;
   retained_ms:=received_ms+15552000000; expected_causality:='null'::jsonb;
   has_causality:=actual.event_type<>'LISTENING_EVENT_RECORDED'
    OR actual.attribution_contract_version=2;
   IF actual.event_type='LISTENING_EVENT_RECORDED' AND actual.attribution_closed IS DISTINCT FROM false THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_CLOSED' USING ERRCODE='23514'; END IF;
   IF has_causality THEN
    SELECT a.association_id,a.kind,a.recommendation_request_id,a.served_rank,a.recording_id,
     a.association_sha256,a.decision_sha256,a.served_item_sha256,a.serving_provenance,a.request_surface,
     a.presentation_id,a.presentation_source,a.presentation_surface,a.impression_association_id,
     p.interaction_id AS impression_event_id,a.attribution_expires_at
    INTO link FROM ml.recommendation_attribution_association a
    LEFT JOIN ml.recommendation_attribution_association p ON p.association_id=a.impression_association_id
    WHERE a.owner_user_id=h.owner_user_id AND (a.interaction_id=current_event OR a.listening_event_id=current_event);
    IF NOT FOUND OR link.recording_id IS DISTINCT FROM actual.recording_id
     OR link.kind IS DISTINCT FROM (CASE actual.event_type WHEN 'LISTENING_EVENT_RECORDED' THEN 'LISTEN'
      WHEN 'RECOMMENDATION_FEEDBACK_RECORDED' THEN 'FEEDBACK' ELSE 'IMPRESSION' END)
     OR link.attribution_expires_at IS NULL THEN
     RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_CAUSALITY' USING ERRCODE='23514'; END IF;
    IF link.kind<>'IMPRESSION' AND NOT EXISTS(
     SELECT 1 FROM ml.native_capture_source_event_v3 parent
      WHERE parent.original_request_id=original AND parent.owner_user_id=h.owner_user_id
       AND parent.source_event_id=link.impression_event_id
       AND parent.event_type='RECOMMENDATION_IMPRESSION_RECORDED'
       AND parent.association_id=link.impression_association_id
       AND parent.causal_request_id=link.recommendation_request_id
       AND parent.impression_event_id IS NULL AND parent.server_sequence<actual.server_sequence) THEN
     RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_PARENT_CLOSURE' USING ERRCODE='23514'; END IF;
    expected_causality:=jsonb_build_object('association_id',link.association_id,
     'association_sha256',encode(link.association_sha256,'hex'),'request_id',link.recommendation_request_id,
     'served_rank',link.served_rank,'decision_sha256',encode(link.decision_sha256,'hex'),
     'served_item_sha256',encode(link.served_item_sha256,'hex'),'serving_provenance',link.serving_provenance,
     'request_surface',link.request_surface,'presentation_id',link.presentation_id,
     'presentation_source',link.presentation_source,'presentation_surface',link.presentation_surface,
     'impression_association_id',link.impression_association_id,'impression_event_id',link.impression_event_id);
    retained_ms:=least(retained_ms,floor(extract(epoch FROM link.attribution_expires_at)*1000)::bigint);
    IF e.association_id IS DISTINCT FROM link.association_id
     OR e.causal_request_id IS DISTINCT FROM link.recommendation_request_id
     OR e.impression_event_id IS DISTINCT FROM link.impression_event_id THEN
     RAISE EXCEPTION 'NATIVE_V3_SOURCE_INDEX_CAUSALITY' USING ERRCODE='23514'; END IF;
   ELSIF e.association_id IS NOT NULL OR e.causal_request_id IS NOT NULL OR e.impression_event_id IS NOT NULL
    OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association a WHERE a.listening_event_id=current_event) THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_CAUSALITY' USING ERRCODE='23514';
   END IF;
   IF fact->'causality' IS DISTINCT FROM expected_causality
    OR fact->'retained_until_ms' IS DISTINCT FROM to_jsonb(retained_ms)
    OR floor(extract(epoch FROM e.retained_until)*1000)::bigint IS DISTINCT FROM retained_ms
    OR mod((extract(epoch FROM e.retained_until)*1000000)::numeric,1000)<>0 THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_RETENTION' USING ERRCODE='23514'; END IF;
   matches:=0; selected_track:=NULL;
   IF h.source_snapshot_track_count>0 THEN
    FOR track_ordinal IN 0..h.source_snapshot_track_count-1 LOOP
     IF tracks#>>ARRAY[track_ordinal::text,'recording_id']=actual.recording_id::text THEN
      matches:=matches+1; selected_track:=track_ordinal; END IF;
    END LOOP;
   END IF;
   IF matches<>1 OR tracks#>ARRAY[selected_track::text,'authorized'] IS DISTINCT FROM 'true'::jsonb
    OR tracks#>ARRAY[selected_track::text,'excluded'] IS DISTINCT FROM 'false'::jsonb THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_BASELINE' USING ERRCODE='23514'; END IF;
   artist:=tracks#>>ARRAY[selected_track::text,'artist_key'];
   release_key:=tracks#>>ARRAY[selected_track::text,'release_key'];
   IF artist IS NULL OR char_length(artist) NOT BETWEEN 1 AND 200 OR octet_length(artist)>800
    OR (release_key IS NOT NULL AND (char_length(release_key) NOT BETWEEN 1 AND 200 OR octet_length(release_key)>800)) THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_DIMENSION' USING ERRCODE='23514'; END IF;
   expected_dimensions:=jsonb_build_array(jsonb_build_object('kind','CANONICAL_ARTIST_ID','key',artist));
   IF release_key IS NOT NULL THEN expected_dimensions:=expected_dimensions||
    jsonb_build_array(jsonb_build_object('kind','CANONICAL_RELEASE_ID','key',release_key)); END IF;
   IF fact->'dimensions' IS DISTINCT FROM expected_dimensions THEN
    RAISE EXCEPTION 'NATIVE_V3_SOURCE_FACT_DIMENSION' USING ERRCODE='23514'; END IF;
   fact:=NULL; expected_causality:=NULL; expected_dimensions:=NULL;
  END LOOP;
 END IF;
 IF EXISTS(SELECT 1 FROM ml.native_capture_source_event_v3 x WHERE x.original_request_id=original
  AND (count=0 OR x.source_event_id>previous_id OR x.owner_user_id<>h.owner_user_id)) THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_INDEX_EXTRA' USING ERRCODE='23514'; END IF;
 -- Independent original discovery, including one-sided/corrupt normalized rows.
 -- One bounded scalar stream; no JSON/UUID/digest aggregation vector.
 FOR discovered IN SELECT coalesce(i.event_id,s.event_id) AS id,s.server_sequence
  FROM sync.device_event_inbox i FULL JOIN sync.sync_event s USING(event_id)
  WHERE (i.user_id=h.owner_user_id OR s.user_id=h.owner_user_id)
   AND ((i.ingress_validation_version=2 AND i.apply_status='APPLIED')
    OR (s.ingress_validation_version=2 AND s.operation='UPSERT'))
   AND floor(extract(epoch FROM coalesce(i.received_at,s.created_at))*1000)<=h.cutoff_at_ms
   AND (s.created_at IS NULL OR floor(extract(epoch FROM s.created_at)*1000)<=h.cutoff_at_ms)
  ORDER BY coalesce(i.event_id,s.event_id) LIMIT 10001
 LOOP
  IF discovered_count>=count OR ids->>discovered_count IS DISTINCT FROM discovered.id::text THEN
   RAISE EXCEPTION 'NATIVE_V3_SOURCE_DISCOVERY_INCOMPLETE' USING ERRCODE='23514'; END IF;
  discovered_count:=discovered_count+1;
  discovered_watermark:=greatest(discovered_watermark,coalesce(discovered.server_sequence,0));
 END LOOP;
 IF discovered_count<>count OR discovered_watermark IS DISTINCT FROM h.native_interaction_watermark THEN
  RAISE EXCEPTION 'NATIVE_V3_SOURCE_DISCOVERY_INCOMPLETE' USING ERRCODE='23514'; END IF;
 facts:=NULL; ids:=NULL; tracks:=NULL; fact:=NULL;
 expected_causality:=NULL; expected_dimensions:=NULL;
 actual:=NULL; link:=NULL; payload_meta:=NULL; discovered:=NULL;
 scalar_payloads:=NULL; parent_link:=NULL; parent_payload_meta:=NULL; parent_event:=NULL;
 artist:=NULL; release_key:=NULL; e:=NULL; h:=NULL;
 PERFORM app_private.native_v3_compiler_exit(compiler_depth);
END $$;
REVOKE ALL ON FUNCTION app_private.native_v3_source_membership_sha256(bytea,integer),
 app_private.native_v3_assert_source_facts(uuid,jsonb) FROM PUBLIC;
"""
)

NATIVE_ORIGINAL_FACTS_V3_DROP_SQL: Final = """
DROP FUNCTION app_private.native_v3_assert_source_facts(uuid,jsonb);
DROP FUNCTION app_private.native_v3_source_membership_sha256(bytea,integer);
"""

# Explicit bounded membership-only kernel gate: it must not install a pretend
# normalized validator or claim to compile/qualify the facts graph function.
_MEMBERSHIP_HEADER: Final = (
    "CREATE FUNCTION app_private.native_v3_source_membership_sha256(raw bytea,p_count integer)"
)
NATIVE_SOURCE_MEMBERSHIP_V3_INSTALL_SQL: Final = (
    _MEMBERSHIP_HEADER
    + NATIVE_ORIGINAL_FACTS_V3_INSTALL_SQL.split(_MEMBERSHIP_HEADER, 1)[1].split(
        "CREATE FUNCTION app_private.native_v3_assert_source_facts", 1
    )[0]
    + "REVOKE ALL ON FUNCTION app_private.native_v3_source_membership_sha256(bytea,integer) FROM PUBLIC;"
)
NATIVE_SOURCE_MEMBERSHIP_V3_DROP_SQL: Final = (
    "DROP FUNCTION app_private.native_v3_source_membership_sha256(bytea,integer);"
)
