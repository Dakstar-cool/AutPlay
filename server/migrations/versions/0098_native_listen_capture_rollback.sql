CREATE OR REPLACE FUNCTION app_private.assert_native_capture_closure(request_id uuid,initial boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ml.sona_capture_bundle%ROWTYPE; q ml.recommendation_request%ROWTYPE;
 d jsonb; t jsonb; ids jsonb; fact_ids jsonb; indexed_ids jsonb; f jsonb; a jsonb;
 native_watermark bigint; cutoff bigint; source_id uuid; source_row record;
 discovered_ids jsonb; discovered_watermark bigint;
BEGIN
 SELECT * INTO b FROM ml.sona_capture_bundle WHERE recommendation_request_id=request_id;
 -- Existing privacy/expiry/root deletes own the cascade; no child resurrects a root.
 IF b.recommendation_request_id IS NULL THEN RETURN; END IF;
 d:=convert_from(b.bundle_document,'UTF8')::jsonb;
 -- Pre-existing minimal legacy fixtures are not a declaration of native-v2 authority.
 IF d='{}'::jsonb OR (d->'schema_version'='1'::jsonb
  AND d->>'kind'='SONA_CAPTURE_BUNDLE_V1' AND NOT d ? 'temporal_snapshot') THEN RETURN; END IF;
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' OR d->'schema_version' IS DISTINCT FROM '2'::jsonb
  OR d->>'kind' IS DISTINCT FROM 'SONA_CAPTURE_BUNDLE_V2' THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_DOCUMENT_VERSION' USING ERRCODE='23514'; END IF;
 t:=d->'temporal_snapshot';
 IF jsonb_typeof(t) IS DISTINCT FROM 'object' OR t->'schema_version' IS DISTINCT FROM '2'::jsonb
  OR t->>'kind' IS DISTINCT FROM 'ORIGINAL_REQUEST_NATIVE_TEMPORAL_V2'
  OR d->'quality_eligible' IS DISTINCT FROM 'false'::jsonb
  OR d->>'admission_blocker' IS DISTINCT FROM 'NATIVE_SCHEMA2_SOURCE_PRODUCER_UNAVAILABLE'
  OR t->>'source_scope' IS DISTINCT FROM 'ALL_VISIBLE_APPLIED_NORMALIZED_V2_AT_CUTOFF'
  OR t->'supported_source_types' IS DISTINCT FROM
   '["LISTENING_EVENT_RECORDED","RECOMMENDATION_FEEDBACK_RECORDED",
     "RECOMMENDATION_IMPRESSION_RECORDED"]'::jsonb
  OR jsonb_typeof(t->'native_interaction_watermark') IS DISTINCT FROM 'number'
  OR (t->>'native_interaction_watermark') !~ '^[0-9]+$'
  OR jsonb_typeof(t->'cutoff_at_ms') IS DISTINCT FROM 'number'
  OR (t->>'cutoff_at_ms') !~ '^[0-9]+$' THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_TEMPORAL_SHAPE' USING ERRCODE='23514'; END IF;
 native_watermark:=(t->>'native_interaction_watermark')::bigint;
 cutoff:=(t->>'cutoff_at_ms')::bigint;
 SELECT * INTO q FROM ml.recommendation_request WHERE recommendation_request_id=request_id;
 IF native_watermark NOT BETWEEN 0 AND 9007199254740991 OR cutoff NOT BETWEEN 0 AND 9007199254740991
  OR cutoff<>b.cutoff_at_ms OR cutoff<>floor(extract(epoch FROM b.created_at)*1000)::bigint
  OR d->'cutoff_at_ms' IS DISTINCT FROM t->'cutoff_at_ms'
  OR d->'native_interaction_watermark' IS DISTINCT FROM t->'native_interaction_watermark'
  OR d->>'recommendation_request_id' IS DISTINCT FROM request_id::text
  OR d->>'owner_user_id' IS DISTINCT FROM b.user_id::text
  OR t->>'owner_user_id' IS DISTINCT FROM b.user_id::text
  OR d->>'baseline_snapshot_id' IS DISTINCT FROM q.recommendation_input_snapshot_id::text
  OR t->'baseline_snapshot_id' IS DISTINCT FROM d->'baseline_snapshot_id'
  OR d->>'baseline_snapshot_sha256' IS DISTINCT FROM encode(b.baseline_snapshot_sha256,'hex')
  OR t->'baseline_snapshot_sha256' IS DISTINCT FROM d->'baseline_snapshot_sha256'
  OR d->>'temporal_snapshot_sha256' IS DISTINCT FROM encode(b.temporal_snapshot_sha256,'hex')
  OR d->>'request_sha256' IS DISTINCT FROM encode(q.request_sha256,'hex')
  OR d->>'candidate_membership_sha256' IS DISTINCT FROM encode(b.candidate_membership_sha256,'hex')
  OR d->>'p11_ranking_sha256' IS DISTINCT FROM encode(b.p11_ranking_sha256,'hex')
  OR d->>'consent_receipt_sha256' IS DISTINCT FROM encode(b.consent_receipt_sha256,'hex')
  OR d->'consent_generation' IS DISTINCT FROM to_jsonb(b.consent_generation)
  OR d->'interaction_watermark' IS DISTINCT FROM to_jsonb(b.interaction_watermark)
  OR d->'universe_count' IS DISTINCT FROM to_jsonb(b.universe_count)
  OR d->'eligible_count' IS DISTINCT FROM to_jsonb(b.eligible_count)
  OR d->'ineligibility_reason' IS DISTINCT FROM
   coalesce(to_jsonb(b.ineligibility_reason),'null'::jsonb)
  OR d->>'temporal_snapshot_id' IS NULL
  OR (d->>'temporal_snapshot_id')::uuid::text IS DISTINCT FROM d->>'temporal_snapshot_id'
  OR t->'temporal_snapshot_id' IS DISTINCT FROM d->'temporal_snapshot_id'
  OR t->>'expires_at' IS NULL OR (t->>'expires_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
  OR (t->>'expires_at')::timestamptz IS DISTINCT FROM b.expires_at
  OR jsonb_typeof(t->'server_identity') IS DISTINCT FROM 'object'
  OR NOT EXISTS(SELECT 1 FROM account.server_instance si
   WHERE si.server_instance_id::text=t->'server_identity'->>'server_instance_id'
    AND to_jsonb(si.identity_epoch)=t->'server_identity'->'identity_epoch') THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_ROOT_BINDING' USING ERRCODE='23514'; END IF;
 ids:=t->'source_event_ids';
 IF jsonb_typeof(ids) IS DISTINCT FROM 'array' OR jsonb_typeof(t->'facts') IS DISTINCT FROM 'array'
  OR jsonb_typeof(d->'candidate_recording_ids') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_CLOSURE_SHAPE' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(ids)>10000 OR jsonb_array_length(t->'facts')<>jsonb_array_length(ids)
  OR jsonb_array_length(d->'candidate_recording_ids')<>b.eligible_count
  OR t->>'source_membership_sha256' IS NULL
  OR (t->>'source_membership_sha256') !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_CLOSURE_BOUND' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(ids) value WHERE jsonb_typeof(value)<>'string')
  OR ids IS DISTINCT FROM (SELECT coalesce(jsonb_agg(id::text ORDER BY id),'[]'::jsonb)
   FROM (SELECT DISTINCT value::uuid AS id FROM jsonb_array_elements_text(ids)) canonical) THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_CLOSURE_IDENTITIES' USING ERRCODE='23514'; END IF;
 SELECT coalesce(jsonb_agg(value->'event_id' ORDER BY ordinal),'[]'::jsonb) INTO fact_ids
  FROM jsonb_array_elements(t->'facts') WITH ORDINALITY AS fact(value,ordinal);
 SELECT coalesce(jsonb_agg(source_event_id::text ORDER BY source_event_id),'[]'::jsonb)
  INTO indexed_ids FROM ml.sona_capture_source_event
  WHERE recommendation_request_id=request_id AND user_id=b.user_id;
 IF fact_ids IS DISTINCT FROM ids OR indexed_ids IS DISTINCT FROM ids THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_CLOSURE_MEMBERSHIP' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM ml.sona_capture_lineage_cursor c
  WHERE c.recommendation_request_id=request_id AND c.user_id=b.user_id AND c.expires_at=b.expires_at
   AND (NOT initial OR c.state='ACTIVE')) THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_CURSOR_REQUIRED' USING ERRCODE='23514'; END IF;
 -- Root bytes cannot change. Child checks need only preserve their exact dependency set;
 -- expensive accepted-source checks run once for the deferred root insertion.
 IF NOT initial THEN RETURN; END IF;
 -- Original completeness is independently checked against the same locked SQL
 -- snapshot, rather than trusting a caller's selected set or lower watermark.
 SELECT coalesce(jsonb_agg(event_id::text ORDER BY event_id),'[]'::jsonb),
  coalesce(max(server_sequence),0) INTO discovered_ids,discovered_watermark
 FROM (
  SELECT coalesce(i.event_id,s.event_id) AS event_id,s.server_sequence
  FROM sync.device_event_inbox i FULL JOIN sync.sync_event s USING(event_id)
  WHERE (i.user_id=b.user_id OR s.user_id=b.user_id)
   AND ((i.ingress_validation_version=2 AND i.apply_status='APPLIED')
    OR (s.ingress_validation_version=2 AND s.operation='UPSERT'))
   AND floor(extract(epoch FROM coalesce(i.received_at,s.created_at))*1000)<=cutoff
   AND (s.created_at IS NULL OR floor(extract(epoch FROM s.created_at)*1000)<=cutoff)
  ORDER BY coalesce(i.event_id,s.event_id) LIMIT 10001
 ) sources;
 IF ids IS DISTINCT FROM discovered_ids OR native_watermark<>discovered_watermark
  OR cutoff>floor(extract(epoch FROM clock_timestamp())*1000)::bigint
  OR b.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_DISCOVERY_INCOMPLETE' USING ERRCODE='23514'; END IF;
 FOR f IN SELECT value FROM jsonb_array_elements(t->'facts') LOOP
  source_id:=(f->>'event_id')::uuid;
  PERFORM app_private.assert_normalized_listen_acceptance(source_id);
  SELECT s.*,i.device_id,i.journal_epoch,i.device_sequence,i.occurred_at,i.received_at,
   i.request_hash,i.apply_status,i.error_code,i.ingress_validation_version AS inbox_version,
   i.event_type AS inbox_type,i.schema_version AS inbox_schema,
   i.aggregate_type AS inbox_aggregate_type,i.aggregate_id AS inbox_aggregate_id,
   i.normalized_payload_sha256 AS inbox_payload_sha256,i.payload AS inbox_payload,
   coalesce(l.recording_id,u.recording_id) AS recording_id
   INTO source_row FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
   LEFT JOIN library.listening_event l ON l.listening_event_id=s.event_id
   LEFT JOIN library.user_interaction_event u ON u.interaction_id=s.event_id
   WHERE s.event_id=source_id AND s.user_id=b.user_id AND i.user_id=b.user_id;
  IF NOT FOUND OR source_row.ingress_validation_version<>2 OR source_row.inbox_version<>2
   OR source_row.apply_status<>'APPLIED' OR source_row.error_code IS NOT NULL
   OR source_row.operation<>'UPSERT' OR source_row.server_sequence>native_watermark
   OR source_row.server_row_version IS DISTINCT FROM 1
   OR source_row.origin_device_id IS DISTINCT FROM source_row.device_id
   OR source_row.ingress_device_sequence IS DISTINCT FROM source_row.device_sequence
   OR source_row.ingress_journal_epoch IS DISTINCT FROM source_row.journal_epoch
   OR source_row.event_type IS DISTINCT FROM source_row.inbox_type
   OR source_row.schema_version IS DISTINCT FROM 1 OR source_row.inbox_schema IS DISTINCT FROM 1
   OR source_row.aggregate_id IS DISTINCT FROM source_id
   OR source_row.inbox_aggregate_id IS DISTINCT FROM source_id
   OR source_row.aggregate_type IS DISTINCT FROM source_row.inbox_aggregate_type
   OR source_row.normalized_payload_sha256 IS DISTINCT FROM source_row.inbox_payload_sha256
   OR source_row.normalized_payload_sha256 IS DISTINCT FROM
    app_private.sync_payload_sha256_pg_v1(source_row.payload)
   OR source_row.inbox_payload IS DISTINCT FROM '{}'::jsonb
   OR floor(extract(epoch FROM source_row.received_at)*1000)::bigint>cutoff
   OR floor(extract(epoch FROM source_row.created_at)*1000)::bigint>cutoff
   OR f->>'owner' IS DISTINCT FROM b.user_id::text
   OR f->>'device' IS DISTINCT FROM source_row.device_id::text
   OR f->>'journal_epoch' IS DISTINCT FROM source_row.journal_epoch::text
   OR f->'device_sequence' IS DISTINCT FROM to_jsonb(source_row.device_sequence)
   OR f->'server_sequence' IS DISTINCT FROM to_jsonb(source_row.server_sequence)
   OR f->>'event_type' IS DISTINCT FROM source_row.event_type
   OR f->>'recording_id' IS DISTINCT FROM source_row.recording_id::text
   OR f->'occurred_at_ms' IS DISTINCT FROM
    to_jsonb(floor(extract(epoch FROM source_row.occurred_at)*1000)::bigint)
   OR f->'received_at_ms' IS DISTINCT FROM
    to_jsonb(floor(extract(epoch FROM source_row.received_at)*1000)::bigint)
   OR f->>'source_request_sha256' IS DISTINCT FROM encode(source_row.request_hash,'hex')
   OR f->>'normalized_payload_sha256' IS DISTINCT FROM
    encode(source_row.normalized_payload_sha256,'hex')
   OR EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt er
    WHERE er.user_id=b.user_id AND er.source_event_id=source_id) THEN
   RAISE EXCEPTION 'NATIVE_CAPTURE_ACCEPTED_SOURCE_BINDING' USING ERRCODE='23514'; END IF;
  IF source_row.event_type='LISTENING_EVENT_RECORDED' THEN
   IF f->'causality' IS DISTINCT FROM 'null'::jsonb THEN
    RAISE EXCEPTION 'NATIVE_CAPTURE_LISTEN_CAUSALITY' USING ERRCODE='23514'; END IF;
  ELSIF source_row.event_type IN
   ('RECOMMENDATION_IMPRESSION_RECORDED','RECOMMENDATION_FEEDBACK_RECORDED') THEN
   SELECT jsonb_build_object('association_id',link.association_id,
    'association_sha256',encode(link.association_sha256,'hex'),
    'request_id',link.recommendation_request_id,'served_rank',link.served_rank,
    'decision_sha256',encode(link.decision_sha256,'hex'),
    'served_item_sha256',encode(link.served_item_sha256,'hex'),
    'serving_provenance',link.serving_provenance,'request_surface',link.request_surface,
    'presentation_id',link.presentation_id,'presentation_source',link.presentation_source,
    'presentation_surface',link.presentation_surface,'impression_association_id',link.impression_association_id,
    'impression_event_id',parent.interaction_id) INTO a
    FROM ml.recommendation_attribution_association link
    LEFT JOIN ml.recommendation_attribution_association parent
     ON parent.association_id=link.impression_association_id
    WHERE link.owner_user_id=b.user_id AND link.interaction_id=source_id;
   IF a IS NULL OR f->'causality' IS DISTINCT FROM a OR
    (source_row.event_type='RECOMMENDATION_FEEDBACK_RECORDED' AND
     (NOT ids @> jsonb_build_array(a->>'impression_event_id') OR NOT EXISTS(
      SELECT 1 FROM jsonb_array_elements(t->'facts') parent
      WHERE parent->>'event_id'=a->>'impression_event_id'
       AND parent->>'event_type'='RECOMMENDATION_IMPRESSION_RECORDED'))) THEN
    RAISE EXCEPTION 'NATIVE_CAPTURE_CAUSAL_CLOSURE' USING ERRCODE='23514'; END IF;
  ELSE RAISE EXCEPTION 'NATIVE_CAPTURE_SOURCE_TYPE' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(t->'facts') AS expiry_fact(value)
  WHERE (expiry_fact.value->>'retained_until_ms')::bigint<=
   floor(extract(epoch FROM clock_timestamp())*1000)::bigint)
  OR b.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_RETENTION_EXPIRED' USING ERRCODE='23514'; END IF;
END $$;
