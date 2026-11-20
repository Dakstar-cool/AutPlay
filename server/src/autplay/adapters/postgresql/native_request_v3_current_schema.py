"""Explicit SQL relational-current fence, without filesystem grant authority.

The actual F/G fresh prefix must already hold canonical common-owner locks and
admit/prepay the complete graph before calling this helper. No SQL token, DTO,
historical consent receipt or function invocation substitutes for that prefix or
the real independent-filesystem grant scope. Importing installs nothing.

Metadata work below is a closed logical preimage allowance, not a physical IO,
query-plan or PostgreSQL memory claim. The primary graph assertion's complete
payload work is additional and must be admitted before this call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from sqlalchemy import Connection

NATIVE_CURRENT_SOURCE_LIMIT_V3: Final = 10_000
NATIVE_CURRENT_GRAPH_FUNCTION_V3: Final = "app_private.assert_native_atomic_graph_v3(uuid)"
NATIVE_CURRENT_FUNCTION_V3: Final = "app_private.assert_native_atomic_current_v3(uuid,uuid,bigint)"
NATIVE_CURRENT_CALL_SQL_V3: Final = (
    "SELECT app_private.assert_native_atomic_current_v3(:owner,:original,:generation)"
)
NATIVE_CURRENT_REQUIRED_TABLES_V3: Final = (
    "account.user_account",
    "account.training_consent",
    "sync.sync_event",
    "sync.device_event_inbox",
    "sync.device_erasure_floor",
    "sync.device_erasure_invalidated_epoch",
    "sync.owner_erasure_floor",
    "ml.sona_event_erasure_receipt",
    "ml.native_original_capture_format",
    "ml.native_served_request_hash_format",
    "ml.native_original_capture_v3",
    "ml.native_capture_source_event_v3",
    "ml.native_capture_lineage_cursor_v3",
    "ml.native_capture_replay_v3",
    "ml.recommendation_request_root_v3",
    "ml.native_atomic_capture_connection_v3",
    "ml.recommendation_request",
    "ml.recommendation_input_snapshot",
    "ml.recommendation_serving_decision",
    "ml.recommendation_attribution_association",
    "library.listening_event",
    "library.user_interaction_event",
)
_CODEC_SHA: Final = "eda9439b265c5e92934f8f62a333782579a14aed116385271f158819045581a2"
_SAFE53: Final = 9_007_199_254_740_991


@dataclass(frozen=True, slots=True)
class NativeCurrentMetadataWorkV3:
    source_rows: int
    byte_work_bound: int
    node_visit_bound: int


def native_current_metadata_work_v3(source_rows: int) -> NativeCurrentMetadataWorkV3:
    """Prepay on the existing caller lease; this function creates no allowance.

    Two bounded counts can inspect 10,001 UUID/sequence/time metadata rows each,
    including refusals. Head/consent/floor closure reserves32KiB/4096 visits.
    Each actual source uses two key lookups plus closed sync/inbox/association/
    target/floor metadata (<2KiB scalar width), conservatively eight byte passes
    and2048 predicate visits. No JSON/bytea payload hash work is included.
    """

    if type(source_rows) is not int or not 0 <= source_rows <= NATIVE_CURRENT_SOURCE_LIMIT_V3:
        raise ValueError("NATIVE_V3_CURRENT_SOURCE_COUNT")
    return NativeCurrentMetadataWorkV3(
        source_rows,
        32_768 + 2 * 10_001 * 96 + 16_384 * source_rows,
        4_096 + 2 * 10_001 * 16 + 2_048 * source_rows,
    )


NATIVE_CURRENT_INSTALL_SQL_V3: Final = f"""
DO $native_current_precondition$
BEGIN
 IF to_regprocedure('{NATIVE_CURRENT_GRAPH_FUNCTION_V3}') IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_CURRENT_GRAPH_REQUIRED' USING ERRCODE='55000';
 END IF;
END; $native_current_precondition$;

CREATE FUNCTION app_private.assert_native_atomic_current_v3(
 p_owner uuid,p_original uuid,p_expected_generation bigint)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path=pg_catalog AS $native_current_v3$
DECLARE
 h record; e record; a record; last_event uuid; next_event uuid;
 expected_count bigint; actual_count bigint; visited bigint:=0;
 floor_sequence bigint:=0; expected_deadline_ms numeric;
 minimum_deadline timestamptz; wall_time timestamptz;
BEGIN
 IF p_owner IS NULL OR p_original IS NULL OR p_expected_generation IS NULL
  OR p_expected_generation NOT BETWEEN 1 AND {_SAFE53} THEN
  RAISE EXCEPTION 'NATIVE_V3_CURRENT_INPUT' USING ERRCODE='23514'; END IF;
 IF to_regprocedure('{NATIVE_CURRENT_GRAPH_FUNCTION_V3}') IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_CURRENT_GRAPH_REQUIRED' USING ERRCODE='55000'; END IF;

 -- Canonical F/G common-owner locks are a caller prerequisite. In particular,
 -- do not call the old identity-lock helper here in opposite lock order.
 PERFORM 1 FROM account.user_account u
 WHERE u.user_id=p_owner AND u.status='ACTIVE' AND u.deleted_at IS NULL;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'NATIVE_V3_CURRENT_ACCOUNT' USING ERRCODE='23514'; END IF;

 -- Fixed scalar attributes only: no rowtypes, JSONB, bytea documents or old
 -- JSONB validators. All eight live source3 relations must remain present.
 SELECT b.cutoff_at_ms,b.native_interaction_watermark,b.source_event_count,
  b.consent_generation,b.created_at,b.expires_at,k.source_generation,
  c.served_request_id
 INTO h
 FROM ml.native_original_capture_v3 b
 JOIN ml.native_original_capture_format f ON f.owner_user_id=b.owner_user_id
  AND f.original_request_id=b.original_request_id
 JOIN ml.native_capture_lineage_cursor_v3 k ON k.owner_user_id=b.owner_user_id
  AND k.original_request_id=b.original_request_id
 JOIN ml.native_capture_replay_v3 p ON p.owner_user_id=b.owner_user_id
  AND p.original_request_id=b.original_request_id
 JOIN ml.native_atomic_capture_connection_v3 c ON c.owner_user_id=b.owner_user_id
  AND c.original_request_id=b.original_request_id
 JOIN ml.recommendation_request_root_v3 r ON r.owner_user_id=c.owner_user_id
  AND r.original_request_id=c.original_request_id AND r.served_request_id=c.served_request_id
 JOIN ml.native_served_request_hash_format sf ON sf.owner_user_id=r.owner_user_id
  AND sf.served_request_id=r.served_request_id
 JOIN ml.recommendation_request original ON original.user_id=b.owner_user_id
  AND original.recommendation_request_id=b.original_request_id
 JOIN ml.recommendation_request served ON served.user_id=r.owner_user_id
  AND served.recommendation_request_id=r.served_request_id
 JOIN ml.recommendation_serving_decision d
  ON d.recommendation_request_id=r.served_request_id
 JOIN ml.recommendation_input_snapshot baseline ON baseline.user_id=b.owner_user_id
  AND baseline.recommendation_input_snapshot_id=b.baseline_snapshot_id
 WHERE b.owner_user_id=p_owner AND b.original_request_id=p_original
  AND b.original_capture_format_version=3 AND f.original_capture_format_version=3
  AND c.original_capture_format_version=3 AND r.original_capture_format_version=3
  AND sf.request_hash_format_version=3 AND r.request_hash_format_version=3
  AND c.request_hash_format_version=3 AND original.serving_contract_version=1
  AND served.serving_contract_version=2 AND c.served_request_id<>p_original
  AND b.source_codec='NATIVE_SOURCE3_F64BITS_JSON_SAFE53_V1'
  AND b.source_codec_sha256=decode('{_CODEC_SHA}','hex')
  AND r.source_codec_sha256=b.source_codec_sha256
  AND c.source_codec_sha256=b.source_codec_sha256
  AND k.state='ACTIVE' AND k.source_generation=p_expected_generation
  AND k.source_event_count=b.source_event_count
  AND k.cutoff_at_ms=b.cutoff_at_ms
  AND k.native_interaction_watermark=b.native_interaction_watermark
  AND c.consent_generation=b.consent_generation AND r.consent_generation=b.consent_generation
  AND (app_private.native_p11_source_version(b.pipeline_manifest_sha256)=1 OR EXISTS(
    SELECT 1 FROM ml.sona_native_capture full_catalogue
    WHERE full_catalogue.request_id=b.original_request_id AND full_catalogue.user_id=p_owner
     AND full_catalogue.consent_revision=b.consent_generation
     AND full_catalogue.payload_sha256=b.catalogue_payload_sha256
     AND full_catalogue.retained_until>=b.expires_at))
  AND c.consent_receipt_sha256=b.consent_receipt_sha256
  AND r.consent_receipt_sha256=b.consent_receipt_sha256
  AND c.capture_sha256=b.bundle_sha256 AND r.capture_sha256=b.bundle_sha256
  AND c.eligibility_policy_sha256=b.eligibility_policy_sha256
  AND r.eligibility_policy_sha256=b.eligibility_policy_sha256
  AND c.request_root_sha256=r.request_root_sha256 AND c.root_proof_sha256=r.root_proof_sha256
  AND c.input_origin_sha256=r.input_origin_sha256 AND c.decision_sha256=d.decision_sha256
  AND p.request_sha256=b.request_sha256 AND original.request_sha256=b.request_sha256
  AND r.original_request_sha256=b.request_sha256
  AND p.original_ranking_sha256=b.original_ranking_sha256
  AND r.original_ranking_sha256=b.original_ranking_sha256
  AND p.baseline_snapshot_id=b.baseline_snapshot_id
  AND r.baseline_snapshot_id=b.baseline_snapshot_id
  AND p.baseline_snapshot_sha256=b.baseline_snapshot_sha256
  AND r.baseline_snapshot_sha256=b.baseline_snapshot_sha256
  AND p.temporal_snapshot_id=b.temporal_snapshot_id
  AND r.temporal_snapshot_id=b.temporal_snapshot_id
  AND p.temporal_snapshot_sha256=b.temporal_snapshot_sha256
  AND r.temporal_snapshot_sha256=b.temporal_snapshot_sha256
  AND b.created_transaction IS NOT NULL
  AND b.created_transaction<>'0'::xid8
  AND f.created_transaction=b.created_transaction AND sf.created_transaction=b.created_transaction
  AND k.created_transaction=b.created_transaction AND p.created_transaction=b.created_transaction
  AND r.created_transaction=b.created_transaction AND c.created_transaction=b.created_transaction
  AND f.created_at=b.created_at AND sf.created_at=b.created_at AND k.created_at=b.created_at
  AND p.created_at=b.created_at AND r.created_at=b.created_at AND c.created_at=b.created_at
  AND f.expires_at=b.expires_at AND sf.expires_at=b.expires_at AND k.expires_at=b.expires_at
  AND p.expires_at=b.expires_at AND r.expires_at=b.expires_at AND c.expires_at=b.expires_at
  AND b.expires_at=b.created_at+interval '15552000 seconds'
  AND d.served_truth_expires_at=b.expires_at AND baseline.retained_until>=b.expires_at
  AND d.state='COMMITTED_P11' AND d.serving_provenance='P11'
 FOR SHARE OF b,f,k,p,c,r,sf,original,served,d,baseline;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'NATIVE_V3_CURRENT_LIVE_BINDING' USING ERRCODE='23514'; END IF;

 PERFORM 1 FROM account.training_consent tc WHERE tc.user_id=p_owner
  AND tc.decision='GRANTED' AND tc.policy_version=1 AND tc.revision=h.consent_generation
 FOR SHARE OF tc;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'NATIVE_V3_CURRENT_CONSENT' USING ERRCODE='23514'; END IF;
 wall_time:=clock_timestamp();
 IF h.created_at>wall_time OR h.expires_at<=wall_time
  OR h.cutoff_at_ms<>floor(extract(epoch FROM h.created_at)*1000)
  OR h.source_event_count NOT BETWEEN 0 AND 10000 THEN
  RAISE EXCEPTION 'NATIVE_V3_CURRENT_TIME' USING ERRCODE='23514'; END IF;
 minimum_deadline:=h.expires_at;
 SELECT coalesce(max(owner_floor.floor_sequence),0) INTO floor_sequence
 FROM sync.owner_erasure_floor owner_floor WHERE owner_floor.user_id=p_owner;

 SELECT count(*) INTO expected_count FROM (
  SELECT 1 FROM ml.native_capture_source_event_v3
  WHERE owner_user_id=p_owner AND original_request_id=p_original LIMIT 10001) bounded;
 SELECT count(*) INTO actual_count FROM (
  SELECT 1 FROM sync.device_event_inbox i FULL JOIN sync.sync_event s USING(event_id)
  WHERE (i.user_id=p_owner OR s.user_id=p_owner)
   AND ((i.ingress_validation_version=2 AND i.apply_status='APPLIED')
    OR (s.ingress_validation_version=2 AND s.operation='UPSERT'))
   AND floor(extract(epoch FROM coalesce(i.received_at,s.created_at))*1000)<=h.cutoff_at_ms
   AND (s.created_at IS NULL OR floor(extract(epoch FROM s.created_at)*1000)<=h.cutoff_at_ms)
   AND (s.server_sequence<=h.native_interaction_watermark OR s.server_sequence IS NULL)
  LIMIT 10001) bounded;
 IF expected_count<>h.source_event_count OR actual_count<>expected_count
  OR expected_count>10000 THEN
  RAISE EXCEPTION 'NATIVE_V3_CURRENT_MEMBERSHIP' USING ERRCODE='23514'; END IF;

 LOOP
  SELECT source_event_id INTO next_event FROM ml.native_capture_source_event_v3
  WHERE owner_user_id=p_owner AND original_request_id=p_original
   AND (last_event IS NULL OR source_event_id>last_event)
  ORDER BY source_event_id LIMIT 1;
  EXIT WHEN NOT FOUND;
  SELECT source_event_id,source_request_sha256,normalized_payload_sha256,device_id,journal_epoch,
   device_sequence,server_sequence,event_type,association_id,causal_request_id,impression_event_id,
   retained_until INTO STRICT e FROM ml.native_capture_source_event_v3
  WHERE owner_user_id=p_owner AND original_request_id=p_original AND source_event_id=next_event
  FOR SHARE;
  SELECT s.event_id AS sync_id,i.event_id AS inbox_id,
   s.user_id AS sync_owner,i.user_id AS inbox_owner,
   (s.event_type=e.event_type) AS sync_type_ok,(i.event_type=e.event_type) AS inbox_type_ok,
   s.ingress_validation_version AS sv,
   i.ingress_validation_version AS iv,s.schema_version AS ss,i.schema_version AS ins,
   s.server_row_version,(s.operation='UPSERT') AS upsert,s.server_sequence,
   i.device_sequence,s.origin_device_id,
   i.device_id,s.ingress_journal_epoch,i.journal_epoch,s.ingress_device_sequence,i.request_hash,
   s.normalized_payload_sha256 AS sync_sha,i.normalized_payload_sha256 AS inbox_sha,
   (s.aggregate_type=i.aggregate_type) AS same_aggregate,
   (s.aggregate_type=CASE WHEN e.event_type='LISTENING_EVENT_RECORDED'
    THEN 'LISTENING_EVENT' ELSE 'USER_INTERACTION_EVENT' END) AS aggregate_kind_ok,
   s.aggregate_id AS sync_aggregate_id,i.aggregate_id AS inbox_aggregate_id,
   (i.apply_status='APPLIED') AS applied,(i.error_code IS NULL) AS error_free,
   i.received_at,s.created_at,
   l.user_id AS listen_owner,l.attribution_contract_version AS listen_contract,l.attribution_closed,
   l.recording_id AS listen_recording,t.user_id AS interaction_owner,
   t.recording_id AS interaction_recording,
   ca.association_id,ca.kind,ca.recommendation_request_id,ca.recording_id,ca.attribution_expires_at,
   ca.impression_association_id,pa.interaction_id AS parent_event,pa.kind AS parent_kind,
   pa.owner_user_id AS parent_owner,pa.recommendation_request_id AS parent_request
  INTO STRICT a FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
  LEFT JOIN library.listening_event l ON l.listening_event_id=s.event_id
  LEFT JOIN library.user_interaction_event t ON t.interaction_id=s.event_id
  LEFT JOIN ml.recommendation_attribution_association ca ON ca.owner_user_id=p_owner
   AND (ca.interaction_id=s.event_id OR ca.listening_event_id=s.event_id)
  LEFT JOIN ml.recommendation_attribution_association pa
   ON pa.association_id=ca.impression_association_id
  WHERE s.event_id=next_event;
  IF a.sync_owner IS DISTINCT FROM p_owner OR a.inbox_owner IS DISTINCT FROM p_owner
   OR a.sv IS DISTINCT FROM 2 OR a.iv IS DISTINCT FROM 2
   OR a.ss IS DISTINCT FROM 1 OR a.ins IS DISTINCT FROM 1
   OR a.server_row_version IS DISTINCT FROM 1 OR a.upsert IS DISTINCT FROM true
   OR a.applied IS DISTINCT FROM true OR a.error_free IS DISTINCT FROM true
   OR a.sync_type_ok IS DISTINCT FROM true OR a.inbox_type_ok IS DISTINCT FROM true
   OR a.device_id IS DISTINCT FROM e.device_id OR a.origin_device_id IS DISTINCT FROM e.device_id
   OR a.journal_epoch IS DISTINCT FROM e.journal_epoch
   OR a.ingress_journal_epoch IS DISTINCT FROM e.journal_epoch
   OR a.device_sequence IS DISTINCT FROM e.device_sequence
   OR a.ingress_device_sequence IS DISTINCT FROM e.device_sequence
   OR a.server_sequence IS DISTINCT FROM e.server_sequence
   OR a.request_hash IS DISTINCT FROM e.source_request_sha256
   OR a.sync_sha IS DISTINCT FROM e.normalized_payload_sha256
   OR a.inbox_sha IS DISTINCT FROM e.normalized_payload_sha256
   OR a.same_aggregate IS DISTINCT FROM true OR a.aggregate_kind_ok IS DISTINCT FROM true
   OR a.sync_aggregate_id IS DISTINCT FROM next_event
   OR a.inbox_aggregate_id IS DISTINCT FROM next_event
   OR e.server_sequence>h.native_interaction_watermark OR e.server_sequence<=floor_sequence
   OR floor(extract(epoch FROM a.received_at)*1000)>h.cutoff_at_ms
   OR floor(extract(epoch FROM a.created_at)*1000)>h.cutoff_at_ms
   OR EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt er
    WHERE er.user_id=p_owner AND er.source_event_id=next_event)
   OR EXISTS(SELECT 1 FROM sync.device_erasure_invalidated_epoch ie
    WHERE ie.device_id=e.device_id AND ie.journal_epoch=e.journal_epoch)
   OR EXISTS(SELECT 1 FROM sync.device_erasure_floor df
    WHERE df.device_id=e.device_id AND df.floor_sequence>=e.server_sequence) THEN
   RAISE EXCEPTION 'NATIVE_V3_CURRENT_SOURCE' USING ERRCODE='23514'; END IF;
  expected_deadline_ms:=floor(extract(epoch FROM a.received_at)*1000)+15552000000;
  IF e.event_type='LISTENING_EVENT_RECORDED' AND a.listen_contract IS DISTINCT FROM 2 THEN
   IF a.listen_owner IS DISTINCT FROM p_owner OR a.attribution_closed IS DISTINCT FROM false
    OR e.association_id IS NOT NULL OR e.causal_request_id IS NOT NULL
    OR e.impression_event_id IS NOT NULL OR a.association_id IS NOT NULL THEN
    RAISE EXCEPTION 'NATIVE_V3_CURRENT_CAUSAL' USING ERRCODE='23514'; END IF;
  ELSE
   IF a.association_id IS DISTINCT FROM e.association_id
    OR a.recommendation_request_id IS DISTINCT FROM e.causal_request_id
    OR a.kind IS DISTINCT FROM (CASE e.event_type WHEN 'LISTENING_EVENT_RECORDED' THEN 'LISTEN'
     WHEN 'RECOMMENDATION_IMPRESSION_RECORDED' THEN 'IMPRESSION'
     WHEN 'RECOMMENDATION_FEEDBACK_RECORDED' THEN 'FEEDBACK' ELSE '' END)
    OR a.recording_id IS DISTINCT FROM (CASE WHEN e.event_type='LISTENING_EVENT_RECORDED'
     THEN a.listen_recording ELSE a.interaction_recording END)
    OR (e.event_type='LISTENING_EVENT_RECORDED'
     AND (a.listen_owner IS DISTINCT FROM p_owner OR a.attribution_closed IS DISTINCT FROM false))
    OR (e.event_type<>'LISTENING_EVENT_RECORDED' AND a.interaction_owner IS DISTINCT FROM p_owner)
    OR (e.event_type='RECOMMENDATION_IMPRESSION_RECORDED'
     AND (e.impression_event_id IS NOT NULL OR a.impression_association_id IS NOT NULL))
    OR (e.event_type<>'RECOMMENDATION_IMPRESSION_RECORDED' AND
     (e.impression_event_id IS NULL OR a.parent_event IS DISTINCT FROM e.impression_event_id
      OR a.parent_kind IS DISTINCT FROM 'IMPRESSION' OR a.parent_owner IS DISTINCT FROM p_owner
      OR a.parent_request IS DISTINCT FROM e.causal_request_id OR NOT EXISTS(
       SELECT 1 FROM ml.native_capture_source_event_v3 parent
       WHERE parent.owner_user_id=p_owner AND parent.original_request_id=p_original
        AND parent.source_event_id=e.impression_event_id
        AND parent.event_type='RECOMMENDATION_IMPRESSION_RECORDED'
        AND parent.server_sequence<e.server_sequence))) THEN
    RAISE EXCEPTION 'NATIVE_V3_CURRENT_CAUSAL' USING ERRCODE='23514'; END IF;
   IF a.attribution_expires_at IS NULL THEN
    RAISE EXCEPTION 'NATIVE_V3_CURRENT_CAUSAL' USING ERRCODE='23514'; END IF;
   expected_deadline_ms:=least(expected_deadline_ms,
    floor(extract(epoch FROM a.attribution_expires_at)*1000));
  END IF;
  IF extract(epoch FROM e.retained_until)*1000000<>expected_deadline_ms*1000
   OR e.retained_until<=clock_timestamp() THEN
   RAISE EXCEPTION 'NATIVE_V3_CURRENT_RETENTION' USING ERRCODE='23514'; END IF;
  minimum_deadline:=least(minimum_deadline,e.retained_until);
  last_event:=next_event; visited:=visited+1;
 END LOOP;
 IF visited<>expected_count THEN
  RAISE EXCEPTION 'NATIVE_V3_CURRENT_MEMBERSHIP' USING ERRCODE='23514'; END IF;

 -- Caller already admitted and prepaid primary graph work before this call.
 -- This real installed primary assertion never grants filesystem authority.
 PERFORM app_private.assert_native_atomic_graph_v3(p_original);
 IF minimum_deadline<=clock_timestamp() THEN
  RAISE EXCEPTION 'NATIVE_V3_CURRENT_RETENTION' USING ERRCODE='23514'; END IF;
EXCEPTION WHEN no_data_found OR too_many_rows THEN
 RAISE EXCEPTION 'NATIVE_V3_CURRENT_SOURCE' USING ERRCODE='23514';
END; $native_current_v3$;
REVOKE ALL ON FUNCTION {NATIVE_CURRENT_FUNCTION_V3} FROM PUBLIC;
"""
NATIVE_CURRENT_DROP_SQL_V3: Final = f"DROP FUNCTION {NATIVE_CURRENT_FUNCTION_V3} RESTRICT;"


def install(connection: Connection) -> None:
    """Explicit caller-owned migration transaction, never ordinary startup."""

    from sqlalchemy import text

    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise RuntimeError("native_v3_current_install_transaction_required")
    for target in NATIVE_CURRENT_REQUIRED_TABLES_V3:
        if (
            connection.scalar(text("SELECT to_regclass(:target) IS NOT NULL"), {"target": target})
            is not True
        ):
            raise RuntimeError("native_v3_current_install_table_missing")
    if (
        connection.scalar(
            text("SELECT to_regprocedure(:target) IS NOT NULL"),
            {"target": NATIVE_CURRENT_GRAPH_FUNCTION_V3},
        )
        is not True
    ):
        raise RuntimeError("native_v3_current_install_graph_missing")
    connection.exec_driver_sql(NATIVE_CURRENT_INSTALL_SQL_V3)
