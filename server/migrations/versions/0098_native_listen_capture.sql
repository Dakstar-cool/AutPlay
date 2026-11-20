CREATE OR REPLACE FUNCTION app_private.assert_native_capture_closure(request_id uuid,initial boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ml.sona_capture_bundle%ROWTYPE; q ml.recommendation_request%ROWTYPE;
 d jsonb; t jsonb; ids jsonb; fact_ids jsonb; indexed_ids jsonb; f jsonb; a jsonb;
 native_watermark bigint; cutoff bigint; source_id uuid; source_row record;
 discovered_ids jsonb; discovered_watermark bigint; causal_expiry timestamptz;
 listen_signals text[]; listen_outcome text; listen_effective_ms bigint;
 listen_time_class text; evidence jsonb;
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
  OR cutoff IS DISTINCT FROM b.cutoff_at_ms
  OR cutoff IS DISTINCT FROM floor(extract(epoch FROM b.created_at)*1000)::bigint
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
 IF jsonb_array_length(ids)>10000
  OR jsonb_array_length(t->'facts') IS DISTINCT FROM jsonb_array_length(ids)
  OR jsonb_array_length(d->'candidate_recording_ids') IS DISTINCT FROM b.eligible_count
  OR t->>'source_membership_sha256' IS NULL
  OR (t->>'source_membership_sha256') !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'NATIVE_CAPTURE_CLOSURE_BOUND' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(ids) value
  WHERE jsonb_typeof(value) IS DISTINCT FROM 'string')
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
 IF ids IS DISTINCT FROM discovered_ids OR native_watermark IS DISTINCT FROM discovered_watermark
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
   coalesce(l.recording_id,u.recording_id) AS recording_id,
   l.attribution_contract_version AS listen_attribution_version,
   l.attribution_closed AS listen_attribution_closed,l.event_origin,l.played_ms,
   l.completion_ratio,l.excluded_from_taste,l.explicit_feedback
   INTO source_row FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
   LEFT JOIN library.listening_event l ON l.listening_event_id=s.event_id
   LEFT JOIN library.user_interaction_event u ON u.interaction_id=s.event_id
   WHERE s.event_id=source_id AND s.user_id=b.user_id AND i.user_id=b.user_id;
  IF NOT FOUND OR source_row.ingress_validation_version IS DISTINCT FROM 2
   OR source_row.inbox_version IS DISTINCT FROM 2
   OR source_row.apply_status IS DISTINCT FROM 'APPLIED' OR source_row.error_code IS NOT NULL
   OR source_row.operation IS DISTINCT FROM 'UPSERT' OR source_row.server_sequence IS NULL
   OR source_row.server_sequence>native_watermark
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
   IF source_row.listen_attribution_version IS DISTINCT FROM 2 THEN
    -- Preserve the installed noncausal fact shape and retained original bytes.
    IF f->'causality' IS DISTINCT FROM 'null'::jsonb OR EXISTS(
     SELECT 1 FROM ml.recommendation_attribution_association link
     WHERE link.listening_event_id=source_id) THEN
     RAISE EXCEPTION 'NATIVE_CAPTURE_LISTEN_CAUSALITY' USING ERRCODE='23514'; END IF;
   ELSE
    -- The installed normalized acceptance validator dispatched above checks
    -- exact LISTEN command/association hashes, live served truth and parent.
    SELECT jsonb_build_object('association_id',link.association_id,
     'association_sha256',encode(link.association_sha256,'hex'),
     'request_id',link.recommendation_request_id,'served_rank',link.served_rank,
     'decision_sha256',encode(link.decision_sha256,'hex'),
     'served_item_sha256',encode(link.served_item_sha256,'hex'),
     'serving_provenance',link.serving_provenance,'request_surface',link.request_surface,
     'presentation_id',link.presentation_id,'presentation_source',link.presentation_source,
     'presentation_surface',link.presentation_surface,
     'impression_association_id',link.impression_association_id,
     'impression_event_id',parent.interaction_id),link.attribution_expires_at
     INTO a,causal_expiry
     FROM ml.recommendation_attribution_association link
     LEFT JOIN ml.recommendation_attribution_association parent
      ON parent.association_id=link.impression_association_id
     WHERE link.owner_user_id=b.user_id AND link.listening_event_id=source_id
      AND link.kind='LISTEN';
    IF a IS NULL OR causal_expiry IS NULL
     OR source_row.listen_attribution_closed IS DISTINCT FROM false
     OR source_row.event_origin IS DISTINCT FROM 'RECOMMENDED'
     OR f->'causality' IS DISTINCT FROM a
     OR f->'event_origin' IS DISTINCT FROM to_jsonb(source_row.event_origin)
     OR f->'played_ms' IS DISTINCT FROM to_jsonb(source_row.played_ms)
     OR f->'completion_ratio' IS DISTINCT FROM
      coalesce(to_jsonb(source_row.completion_ratio),'null'::jsonb)
     OR f->'excluded_from_taste' IS DISTINCT FROM to_jsonb(source_row.excluded_from_taste)
     OR f->'explicit_feedback' IS DISTINCT FROM to_jsonb(source_row.explicit_feedback)
     OR f->'feedback' IS DISTINCT FROM 'null'::jsonb
     OR f->'retained_until_ms' IS DISTINCT FROM to_jsonb(least(
      floor(extract(epoch FROM source_row.received_at)*1000)::bigint+15552000000,
      floor(extract(epoch FROM causal_expiry)*1000)::bigint)) THEN
     RAISE EXCEPTION 'NATIVE_CAPTURE_LISTEN_FACT_BINDING' USING ERRCODE='23514'; END IF;
    IF NOT ids @> jsonb_build_array(a->>'impression_event_id') OR NOT EXISTS(
     SELECT 1 FROM jsonb_array_elements(t->'facts') parent
     WHERE parent->>'event_id'=a->>'impression_event_id'
      AND parent->>'event_type'='RECOMMENDATION_IMPRESSION_RECORDED'
      AND parent->'owner' IS NOT DISTINCT FROM f->'owner'
      AND parent->'device' IS NOT DISTINCT FROM f->'device'
      AND parent->'recording_id' IS NOT DISTINCT FROM f->'recording_id'
      AND parent->'causality'->'association_id' IS NOT DISTINCT FROM a->'impression_association_id'
      AND parent->'causality'->'impression_association_id' IS NOT DISTINCT FROM 'null'::jsonb
      AND parent->'causality'->'impression_event_id' IS NOT DISTINCT FROM 'null'::jsonb
      AND parent->'retained_until_ms' IS NOT DISTINCT FROM to_jsonb(least(
       (parent->>'received_at_ms')::bigint+15552000000,
       floor(extract(epoch FROM causal_expiry)*1000)::bigint))
      AND (parent->>'server_sequence')::bigint<source_row.server_sequence) THEN
     RAISE EXCEPTION 'NATIVE_CAPTURE_LISTEN_PARENT_CLOSURE' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(t->'source_evidence') IS DISTINCT FROM 'array' THEN
     RAISE EXCEPTION 'NATIVE_CAPTURE_LISTEN_EVIDENCE_SHAPE' USING ERRCODE='23514'; END IF;
    -- The following rules are the immutable ADR-048 feature policy, not a
    -- configurable second normalization policy. Bind the exact policy hash.
    IF t->>'feature_policy_sha256' IS DISTINCT FROM
     '6fc6c3348475618696eec0ad171515d50e6f586ebf515f67f5add4ef245179db'
     OR d->'feature_policy_sha256' IS DISTINCT FROM t->'feature_policy_sha256' THEN
     RAISE EXCEPTION 'NATIVE_CAPTURE_LISTEN_SIGNAL_BINDING' USING ERRCODE='23514'; END IF;
    listen_outcome:=NULL;
    IF source_row.excluded_from_taste THEN
     listen_signals:=ARRAY['EXCLUDE_FROM_TASTE'];
    ELSE
     listen_signals:=ARRAY['FINALIZED_RECOMMENDATION_LISTEN'];
     IF source_row.completion_ratio<=0.2 OR source_row.played_ms<30000 THEN
      listen_outcome:='FINALIZED_SHORT_LISTEN_SKIP';
     ELSIF source_row.completion_ratio>=0.8 THEN
      listen_outcome:='FINALIZED_COMPLETION';
     END IF;
     IF listen_outcome IS NOT NULL THEN
      listen_signals:=array_append(listen_signals,listen_outcome); END IF;
    END IF;
    listen_effective_ms:=(f->>'occurred_at_ms')::bigint;
    IF listen_effective_ms>(f->>'received_at_ms')::bigint+300000 THEN
     listen_effective_ms:=(f->>'received_at_ms')::bigint;
     listen_time_class:='FUTURE_SKEW_CLAMPED';
    ELSIF (f->>'received_at_ms')::bigint-listen_effective_ms>2592000000 THEN
     listen_time_class:='PAST_SKEW_RECENT_DISABLED';
    ELSIF (f->>'received_at_ms')::bigint-listen_effective_ms>300000 THEN
     listen_time_class:='DELAYED_WITHIN_POLICY';
    ELSE listen_time_class:='TRUSTED_EVENT_TIME'; END IF;
    IF listen_effective_ms>cutoff THEN
     listen_effective_ms:=(f->>'received_at_ms')::bigint;
     listen_time_class:='FUTURE_SKEW_CLAMPED'; END IF;
    -- Impression is support only. Each expected LISTEN signal occurs once;
    -- neither an empty nor a duplicated/extra signal array is accepted.
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(t->'source_evidence') AS ev(value)
     WHERE value->>'source_event_id'=a->>'impression_event_id')
     OR (SELECT count(*) FROM jsonb_array_elements(t->'source_evidence') AS ev(value)
      WHERE value->>'source_event_id'=source_id::text) IS DISTINCT FROM cardinality(listen_signals)
     OR (SELECT count(DISTINCT value->>'signal')
      FROM jsonb_array_elements(t->'source_evidence') AS ev(value)
      WHERE value->>'source_event_id'=source_id::text) IS DISTINCT FROM cardinality(listen_signals)
     OR (SELECT count(DISTINCT value->>'evidence_id')
      FROM jsonb_array_elements(t->'source_evidence') AS ev(value)
      WHERE value->>'source_event_id'=source_id::text) IS DISTINCT FROM cardinality(listen_signals) THEN
     RAISE EXCEPTION 'NATIVE_CAPTURE_LISTEN_SIGNAL_BINDING' USING ERRCODE='23514'; END IF;
    FOR evidence IN SELECT value FROM jsonb_array_elements(t->'source_evidence') AS ev(value)
     WHERE value->>'source_event_id'=source_id::text LOOP
     IF jsonb_typeof(evidence) IS DISTINCT FROM 'object'
      OR evidence->'schema_version' IS DISTINCT FROM '2'::jsonb
      OR evidence->'source_event_id' IS DISTINCT FROM f->'event_id'
      OR evidence->'owner_user_id' IS DISTINCT FROM f->'owner'
      OR evidence->'device_id' IS DISTINCT FROM f->'device'
      OR evidence->'device_sequence' IS DISTINCT FROM f->'device_sequence'
      OR evidence->'server_sequence' IS DISTINCT FROM f->'server_sequence'
      OR evidence->'source_event_type' IS DISTINCT FROM f->'event_type'
      OR evidence->'recording_id' IS DISTINCT FROM f->'recording_id'
      OR evidence->'dimensions' IS DISTINCT FROM f->'dimensions'
      OR evidence->'occurred_at_ms' IS DISTINCT FROM f->'occurred_at_ms'
      OR evidence->'received_at_ms' IS DISTINCT FROM f->'received_at_ms'
      OR evidence->'effective_at_ms' IS DISTINCT FROM to_jsonb(listen_effective_ms)
      OR evidence->>'time_classification' IS DISTINCT FROM listen_time_class
      OR evidence->'source_request_sha256' IS DISTINCT FROM f->'source_request_sha256'
      OR evidence->'association_id' IS DISTINCT FROM a->'association_id'
      OR jsonb_typeof(evidence->'signal') IS DISTINCT FROM 'string'
      OR NOT (evidence->>'signal'=ANY(listen_signals))
      OR evidence->'excluded_from_taste' IS DISTINCT FROM to_jsonb(source_row.excluded_from_taste)
      OR evidence->>'origin_lane' IS DISTINCT FROM
       (CASE WHEN source_row.excluded_from_taste THEN 'EXCLUSION' ELSE 'RECOMMENDATION' END)
      OR evidence->'quality_weight' IS DISTINCT FROM to_jsonb(
       CASE WHEN source_row.excluded_from_taste THEN 0 ELSE 1 END)
      OR evidence->>'derivation_key' IS DISTINCT FROM (CASE evidence->>'signal'
       WHEN 'EXCLUDE_FROM_TASTE' THEN 'EXCLUSION_PROJECTION_V1'
       WHEN 'FINALIZED_RECOMMENDATION_LISTEN' THEN 'BASE_LISTEN_V1'
       ELSE 'OUTCOME_CLASSIFIER_V1' END)
      OR evidence->'signed_strength' IS DISTINCT FROM to_jsonb(CASE evidence->>'signal'
       WHEN 'EXCLUDE_FROM_TASTE' THEN 0::numeric
       WHEN 'FINALIZED_RECOMMENDATION_LISTEN' THEN 0.2
       WHEN 'FINALIZED_COMPLETION' THEN 0.3 ELSE -0.45 END)
      OR evidence ?| ARRAY['recommendation_request_id','impression_event_id',
       'recommendation_source_rank'] THEN
      RAISE EXCEPTION 'NATIVE_CAPTURE_LISTEN_SIGNAL_BINDING' USING ERRCODE='23514'; END IF;
    END LOOP;
   END IF;
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
