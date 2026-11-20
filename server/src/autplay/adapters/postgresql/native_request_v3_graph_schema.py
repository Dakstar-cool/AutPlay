"""Dormant immutable Source3 graph and retained served-truth assertions.

These helpers acquire no owner locks and grant no current consent. The private
caller admits and prepays the complete graph on its existing work owner before
calling; deferred use must share that admission and dirty-epoch accounting.
Installation refuses a missing actual original-payload assertion. No passing
stand-in, old Source2 closure, or historical digest supplies that assertion.
"""

from __future__ import annotations

from autplay.domain.adaptive_recommendations import DEFAULT_ADAPTIVE_FEATURE_POLICY

NATIVE_REQUEST_V3_GRAPH_REQUIRED_FUNCTIONS = (
    "app_private.native_v3_compiler_enter(bigint)",
    "app_private.native_v3_compiler_grow(integer,bigint)",
    "app_private.native_v3_compiler_exit(integer)",
    "app_private.native_v3_wire_value(bytea,integer,text,integer)",
    "app_private.native_v3_root_preflight(bytea)",
    "app_private.native_v3_role_hash(text,jsonb)",
    "app_private.native_v3_role_set_digest(text,uuid,jsonb,jsonb)",
    "app_private.native_served_baseline_digest(uuid,jsonb)",
    "app_private.native_served_item_digest(uuid,jsonb)",
    "app_private.native_served_set_digest(uuid,boolean)",
    "app_private.native_v3_item_rows_byte_length(uuid,uuid,text)",
    "app_private.assert_native_original_payloads_v3(uuid)",
)


def _prerequisites() -> str:
    checks = "\n".join(
        f"IF to_regprocedure('{function}') IS NULL THEN "
        "RAISE EXCEPTION 'NATIVE_V3_GRAPH_REAL_HELPER_REQUIRED' USING ERRCODE='55000'; END IF;"
        for function in NATIVE_REQUEST_V3_GRAPH_REQUIRED_FUNCTIONS
    )
    return "DO $$ BEGIN\n" + checks + "\nEND $$;\n"


NATIVE_REQUEST_V3_GRAPH_INSTALL_SQL = (
    _prerequisites()
    + r"""
-- Historical lookups must stay indexed even after the live connection is gone.
CREATE UNIQUE INDEX native_root_v3_original_unique
 ON ml.recommendation_request_root_v3(original_request_id);

CREATE FUNCTION app_private.native_v3_decision_digest(p_served uuid) RETURNS bytea
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT sha256(app_private.native_served_lp('autplay.recommendation-decision.v3',ARRAY[
  int8send(3::bigint),uuid_send(v.recommendation_request_id),uuid_send(v.user_id),
  uuid_send(r.device_id),uuid_send(r.journal_epoch),uuid_send(r.server_instance_id),
  int8send(r.identity_epoch),v.request_sha256,r.request_root_sha256,
  convert_to(v.surface,'UTF8'),convert_to(v.pipeline_key,'UTF8'),
  convert_to(v.pipeline_version,'UTF8'),v.pipeline_manifest_sha256,int8send(r.cutoff_at_ms),
  convert_to(d.state,'UTF8'),convert_to(d.serving_provenance,'UTF8'),d.inference_evidence_sha256,
  int8send(d.p11_item_count::bigint),d.p11_items_sha256,
  int8send(d.served_item_count::bigint),d.served_items_sha256,
  int8send((extract(epoch FROM v.created_at)*1000000)::bigint),
  int8send((extract(epoch FROM d.served_truth_expires_at)*1000000)::bigint)]))
 FROM ml.recommendation_request_root_v3 r
 JOIN ml.recommendation_request v ON v.recommendation_request_id=r.served_request_id
  AND v.user_id=r.owner_user_id
 JOIN ml.recommendation_serving_decision d ON d.recommendation_request_id=r.served_request_id
 WHERE r.served_request_id=p_served
$$;

CREATE FUNCTION app_private.native_v3_connection_seal(p_served uuid) RETURNS bytea
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT sha256(app_private.native_served_lp('autplay.recommendation-origin-connection.v3',ARRAY[
  uuid_send(r.original_request_id),uuid_send(r.served_request_id),r.input_origin_sha256,
  r.request_root_sha256,d.decision_sha256]))
 FROM ml.recommendation_request_root_v3 r
 JOIN ml.recommendation_serving_decision d ON d.recommendation_request_id=r.served_request_id
 WHERE r.served_request_id=p_served
$$;

-- A successful served-only check may be reused inside its original RR transaction.
-- Every consulted relation contributes the exact identities of its visible row
-- versions. At most 101 rows from either item relation enter this comparison.
-- Consent, temporal facts and expiry checks run separately on every current use.
CREATE TABLE app_private.native_v3_served_check (
 transaction_id xid8 NOT NULL, backend_pid integer NOT NULL,
 served_request_id uuid NOT NULL, versions bytea NOT NULL CHECK(octet_length(versions)<=8192),
 PRIMARY KEY(transaction_id,backend_pid,served_request_id)
);
REVOKE ALL ON TABLE app_private.native_v3_served_check FROM PUBLIC;
CREATE FUNCTION app_private.native_v3_clear_served_check() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 DELETE FROM app_private.native_v3_served_check
 WHERE transaction_id=NEW.transaction_id AND backend_pid=NEW.backend_pid
  AND served_request_id=NEW.served_request_id;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER native_v3_clear_served_check AFTER INSERT
 ON app_private.native_v3_served_check DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app_private.native_v3_clear_served_check();

CREATE FUNCTION app_private.native_v3_served_row_versions(p_served uuid) RETURNS bytea
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 WITH root AS (
  SELECT served_request_id,original_request_id,baseline_snapshot_id FROM ml.recommendation_request_root_v3
  WHERE served_request_id=p_served AND created_transaction=pg_current_xact_id()
 ), selected AS (
  SELECT 1 AS role,0 AS ordinal,v.tableoid,v.ctid,v.xmin,v.cmin FROM ml.recommendation_request_root_v3 v
   WHERE v.served_request_id=p_served
  UNION ALL SELECT 2,0,v.tableoid,v.ctid,v.xmin,v.cmin FROM ml.native_served_request_hash_format v
   WHERE v.served_request_id=p_served
  UNION ALL SELECT 3,0,v.tableoid,v.ctid,v.xmin,v.cmin FROM ml.recommendation_request v
   WHERE v.recommendation_request_id=p_served
  UNION ALL SELECT 4,0,v.tableoid,v.ctid,v.xmin,v.cmin FROM ml.recommendation_serving_decision v
   WHERE v.recommendation_request_id=p_served
  UNION ALL SELECT 5,0,v.tableoid,v.ctid,v.xmin,v.cmin FROM ml.native_original_capture_v3 v
   JOIN root ON root.original_request_id=v.original_request_id
  UNION ALL SELECT 6,0,v.tableoid,v.ctid,v.xmin,v.cmin FROM ml.recommendation_input_snapshot v
   JOIN root ON root.baseline_snapshot_id=v.recommendation_input_snapshot_id
  UNION ALL (SELECT 7,v.rank,v.tableoid,v.ctid,v.xmin,v.cmin FROM ml.recommendation_item v
   WHERE v.recommendation_request_id=p_served ORDER BY v.rank LIMIT 101)
  UNION ALL (SELECT 8,v.source_rank,v.tableoid,v.ctid,v.xmin,v.cmin FROM ml.recommendation_served_item v
   WHERE v.recommendation_request_id=p_served ORDER BY v.source_rank LIMIT 101)
 )
 SELECT string_agg(int4send(role)||int4send(ordinal)||oidsend(tableoid)||tidsend(ctid)
  ||xidsend(xmin)||cidsend(cmin),''::bytea ORDER BY role,ordinal)
 FROM selected WHERE EXISTS(SELECT 1 FROM root)
$$;
REVOKE ALL ON FUNCTION app_private.native_v3_clear_served_check(),
 app_private.native_v3_served_row_versions(uuid) FROM PUBLIC;

CREATE FUNCTION app_private.assert_native_served_truth_v3(p_served uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r ml.recommendation_request_root_v3%ROWTYPE;
 d ml.recommendation_serving_decision%ROWTYPE;
 f ml.native_served_request_hash_format%ROWTYPE;
 proof jsonb; body jsonb; header jsonb; metadata jsonb; identity jsonb;
 tracks jsonb; candidates jsonb:='[]'::jsonb; leaf jsonb; served_leaf jsonb;
 scalar record; actual record; served record; scan bigint[]; n integer; idx integer;
 eligible integer:=0; t integer; item_count integer; last_candidate uuid;
 id_sha bytea; origin_sha bytea; calculated bytea; key text; role text; domain text;
 root_bytes bigint; compiler_peak bigint; jsonb_bound bigint; baseline_size bigint;
 first_source integer; source_position integer; track_ordinal integer; proof_root_sha text;
 pipeline_sources text[]:=ARRAY['preferences','history','artist_release_metadata',
  'freshness','exploration']; contribution jsonb;
 compiler_depth integer; decode_depth integer; baseline_depth integer; fetch_depth integer;
 snapshot_ref jsonb; retained_bytes bigint; validated_versions bytea;
BEGIN
 IF p_served IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_GRAPH_INPUT' USING ERRCODE='23514'; END IF;
 IF current_setting('transaction_isolation')='read committed' THEN
  PERFORM app_private.lock_native_served_server();
  PERFORM app_private.lock_recommendation_request_v2(p_served);
 END IF;
 -- Charge the bounded row-version projection and compare it before any proof hydration.
 IF current_setting('transaction_isolation')='repeatable read' AND EXISTS(
  SELECT 1 FROM ml.recommendation_request_root_v3 v WHERE v.served_request_id=p_served
   AND v.created_transaction=pg_current_xact_id()) THEN
  compiler_depth:=app_private.native_v3_compiler_enter(65536);
  validated_versions:=app_private.native_v3_served_row_versions(p_served);
  IF EXISTS(SELECT 1 FROM app_private.native_v3_served_check c
   WHERE c.transaction_id=pg_current_xact_id() AND c.backend_pid=pg_backend_pid()
    AND c.served_request_id=p_served AND c.versions=validated_versions) THEN
   validated_versions:=NULL;
   PERFORM app_private.native_v3_compiler_exit(compiler_depth);
   RETURN;
  END IF;
  -- Only the <=8KiB version string transfers into the fixed metadata allowance.
  PERFORM app_private.native_v3_compiler_exit(compiler_depth);
 END IF;
 -- Fixed metadata first. Do not hydrate a complete BYTEA/JSONB row as a size probe.
 SELECT octet_length(root_document) INTO root_bytes
 FROM ml.recommendation_request_root_v3 WHERE served_request_id=p_served;
 IF root_bytes IS NULL OR root_bytes NOT BETWEEN 1 AND 2097152 THEN
  RAISE EXCEPTION 'NATIVE_V3_SERVED_ROOT_REQUIRED' USING ERRCODE='23514'; END IF;
 retained_bytes:=root_bytes+262144;
 compiler_depth:=app_private.native_v3_compiler_enter(retained_bytes);
 SELECT root_document || ''::bytea INTO r.root_document FROM ml.recommendation_request_root_v3
 WHERE served_request_id=p_served;
 scan:=app_private.native_v3_wire_value(r.root_document,0,'$',0);
 IF scan[1]<>root_bytes THEN
  RAISE EXCEPTION 'NATIVE_V3_RAW_TRAILING' USING ERRCODE='23514'; END IF;
 -- Raw BYTEA + transient UTF8 + conservative JSONB scalar/container accounting,
 -- one <=512KiB set preimage, bounded leaf/component copies and fixed metadata.
 -- Every allocation phase is included before native_v3_root_preflight hydrates.
 jsonb_bound:=root_bytes+32*scan[3]+65536;
 compiler_peak:=greatest(2*root_bytes+jsonb_bound,root_bytes+2*jsonb_bound)+1048576;
 IF compiler_peak>8388608 THEN
  RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
 decode_depth:=app_private.native_v3_compiler_enter(greatest(0,compiler_peak-retained_bytes));
 -- The scan above admitted this exact in-memory value and its closed shape.
 proof:=convert_from(r.root_document,'UTF8')::jsonb;
 r.root_document:=NULL;
 SELECT * INTO r FROM ml.recommendation_request_root_v3 WHERE served_request_id=p_served;
 SELECT * INTO f FROM ml.native_served_request_hash_format WHERE served_request_id=p_served;
 -- Shared request can contain personal JSON. Bound physical/logical container
 -- sizes before SELECTing its fixed permitted scalar projection below.
 IF NOT EXISTS(SELECT 1 FROM ml.recommendation_request q
  WHERE q.recommendation_request_id=p_served AND q.request_document IS NULL
   AND CASE WHEN pg_column_compression(q.request_features) IS NULL
    AND pg_column_size(q.request_features)<=8 THEN q.request_features='{}'::jsonb
    ELSE false END
   AND q.sona_shadow_document IS NULL AND q.sona_shadow_status IS NULL
   AND octet_length(q.pipeline_key)<=4096 AND octet_length(q.pipeline_version)<=4096
   AND octet_length(q.model_bundle_version)<=4096
   AND octet_length(q.candidate_policy_version)<=4096
   AND octet_length(q.filter_policy_version)<=4096 AND octet_length(q.reranker_version)<=4096
   AND octet_length(q.availability_snapshot_ref)=64) THEN
  RAISE EXCEPTION 'NATIVE_V3_SERVED_REQUEST_SHAPE' USING ERRCODE='23514'; END IF;
 SELECT recommendation_request_id,user_id,context,surface,serving_contract_version,
  pipeline_key,pipeline_version,pipeline_manifest_sha256,request_schema_version,
  request_canonicalization_version,request_sha256,input_snapshot_sha256,
  temporal_snapshot_sha256,adaptive_feature_policy_sha256,
  recommendation_input_snapshot_id,recommendation_temporal_snapshot_id,
  interaction_watermark,catalog_snapshot,availability_snapshot_ref,policy_snapshot_sha256,
  model_bundle_version,candidate_policy_version,filter_policy_version,reranker_version,
  seed,shadow,created_at
 INTO scalar FROM ml.recommendation_request WHERE recommendation_request_id=p_served;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=p_served;
 IF scalar.recommendation_request_id IS NULL OR d.recommendation_request_id IS NULL
  OR f.served_request_id IS NULL OR f.request_hash_format_version<>3
  OR r.request_hash_format_version<>3 OR r.original_capture_format_version<>3
  OR f.owner_user_id IS DISTINCT FROM r.owner_user_id
  OR scalar.user_id IS DISTINCT FROM r.owner_user_id OR scalar.serving_contract_version<>2
  OR scalar.request_schema_version<>2 OR scalar.request_canonicalization_version<>1
  OR scalar.temporal_snapshot_sha256 IS DISTINCT FROM r.temporal_snapshot_sha256
  OR scalar.adaptive_feature_policy_sha256 IS DISTINCT FROM
   decode('__FEATURE_POLICY__','hex')
  OR scalar.shadow OR (scalar.recommendation_input_snapshot_id IS NOT NULL
   AND scalar.recommendation_input_snapshot_id IS DISTINCT FROM r.baseline_snapshot_id)
  OR (scalar.recommendation_temporal_snapshot_id IS NOT NULL
   AND scalar.recommendation_temporal_snapshot_id IS DISTINCT FROM r.temporal_snapshot_id)
  OR r.served_request_id=r.original_request_id OR r.created_transaction IS NULL
  OR r.created_transaction IS DISTINCT FROM f.created_transaction
  OR r.created_at IS DISTINCT FROM scalar.created_at OR f.created_at IS DISTINCT FROM r.created_at
  OR r.expires_at IS DISTINCT FROM r.created_at+interval '15552000 seconds'
  OR f.expires_at IS DISTINCT FROM r.expires_at
  OR d.served_truth_expires_at IS DISTINCT FROM r.expires_at
  OR d.state IS DISTINCT FROM 'COMMITTED_P11' OR d.serving_provenance IS DISTINCT FROM 'P11'
  OR d.inference_evidence_sha256 IS NOT NULL OR d.fallback_reason IS NOT NULL
  OR scalar.pipeline_key IS DISTINCT FROM 'cpu-baseline'
  OR scalar.pipeline_version IS DISTINCT FROM app_private.native_p11_source_version(scalar.pipeline_manifest_sha256)::text
  OR r.root_proof_sha256 IS DISTINCT FROM sha256(r.root_document)
  OR r.source_codec_sha256 IS DISTINCT FROM decode(
   'eda9439b265c5e92934f8f62a333782579a14aed116385271f158819045581a2','hex') THEN
  RAISE EXCEPTION 'NATIVE_V3_SERVED_GRAPH_BINDING' USING ERRCODE='23514'; END IF;
 r.root_document:=NULL;
 proof_root_sha:=proof->>'root_sha256';
 body:=proof->'body'; header:=proof->'root_header';
 snapshot_ref:=proof->'snapshot_ref';
 IF proof->'hash_format_version' IS DISTINCT FROM '3'::jsonb OR (
    (proof->'proof_version'='3'::jsonb AND proof->>'kind'='NATIVE_SERVED_REQUEST_PROOF_V3'
     AND snapshot_ref IS NULL AND r.referenced_snapshot_id IS NULL)
    OR (proof->'proof_version'='4'::jsonb AND proof->>'kind'='NATIVE_SERVED_REQUEST_PROOF_V4'
     AND r.referenced_snapshot_id=r.baseline_snapshot_id
     AND snapshot_ref IS NOT NULL AND body->'snapshot_tracks'='[]'::jsonb
     AND snapshot_ref=jsonb_build_object('owner_user_id',r.owner_user_id,
      'snapshot_id',r.baseline_snapshot_id,'sha256',encode(r.baseline_snapshot_sha256,'hex'),
      'track_count',r.source_snapshot_track_count))
   ) IS NOT TRUE THEN
  RAISE EXCEPTION 'NATIVE_V3_ROOT_ENVELOPE' USING ERRCODE='23514'; END IF;
 proof:=NULL;
 metadata:=body->'metadata';
 identity:=body->'identity'; tracks:=body->'snapshot_tracks';
 IF body->>'request_id' IS DISTINCT FROM p_served::text
  OR header->>'request_id' IS DISTINCT FROM p_served::text
  OR identity IS DISTINCT FROM jsonb_build_object('owner_user_id',r.owner_user_id,
   'device_id',r.device_id,'journal_epoch',r.journal_epoch,
   'server_instance_id',r.server_instance_id,'identity_epoch',r.identity_epoch)
  OR metadata IS DISTINCT FROM jsonb_build_object('surface',scalar.surface,
   'context',scalar.context,'seed',scalar.seed,'effective_limit',r.effective_limit,
   'endpoint_max',r.endpoint_max,'cutoff_at_ms',r.cutoff_at_ms,'pipeline_key',scalar.pipeline_key,
   'pipeline_version',scalar.pipeline_version,
   'pipeline_manifest_sha256',encode(scalar.pipeline_manifest_sha256,'hex'),
   'model_bundle_version',scalar.model_bundle_version,
   'candidate_policy_version',scalar.candidate_policy_version,
   'filter_policy_version',scalar.filter_policy_version,'reranker_version',scalar.reranker_version,
   'interaction_watermark',scalar.interaction_watermark,'catalog_snapshot',scalar.catalog_snapshot,
   'availability_snapshot_ref',scalar.availability_snapshot_ref)
  OR body->'request_document' IS DISTINCT FROM jsonb_build_object('schema_version',2,
   'canonicalization_version',1,'user_id',r.owner_user_id,'surface',scalar.surface,
   'context',scalar.context,'seed',scalar.seed,'shadow',false,'limit',r.effective_limit,
   'exploration_units_e8',r.exploration_units_e8)
  OR body->'request_features' IS DISTINCT FROM jsonb_build_object('limit',r.effective_limit,
   'exploration_units_e8',r.exploration_units_e8)
  OR body->'snapshot_header' IS DISTINCT FROM app_private.native_p11_snapshot_header(
     scalar.pipeline_manifest_sha256,r.owner_user_id,scalar.interaction_watermark)
  OR (scalar.pipeline_version='2' AND snapshot_ref IS NULL)
  OR body->'origin' IS DISTINCT FROM jsonb_build_object('kind','ORIGINAL_CAPTURE_P11_V3',
   'original_request_id',r.original_request_id,
   'capture_sha256',encode(r.capture_sha256,'hex'),'consent_generation',r.consent_generation,
   'consent_receipt_sha256',encode(r.consent_receipt_sha256,'hex')) THEN
  RAISE EXCEPTION 'NATIVE_V3_ROOT_BODY_BINDING' USING ERRCODE='23514'; END IF;
 PERFORM app_private.native_v3_role_hash('identity',identity);
 id_sha:=sha256(app_private.native_served_lp('autplay.recommendation-identity.v3',ARRAY[
  uuid_send(r.owner_user_id),uuid_send(r.device_id),uuid_send(r.journal_epoch),
  uuid_send(r.server_instance_id),int8send(r.identity_epoch)]));
 origin_sha:=sha256(app_private.native_served_lp('autplay.recommendation-input-origin.v3',ARRAY[
  uuid_send(p_served),uuid_send(r.original_request_id),id_sha,r.capture_sha256,
  int8send(r.consent_generation),r.consent_receipt_sha256]));
 IF header->>'identity_sha256' IS DISTINCT FROM encode(id_sha,'hex')
  OR header->>'original_capture_binding_sha256' IS DISTINCT FROM encode(origin_sha,'hex')
  OR r.input_origin_sha256 IS DISTINCT FROM origin_sha THEN
  RAISE EXCEPTION 'NATIVE_V3_ROOT_ORIGIN' USING ERRCODE='23514'; END IF;
 FOR role,key,domain IN SELECT * FROM (VALUES
  ('metadata','metadata','autplay.recommendation-request-metadata.v3'),
  ('query','request_document',NULL::text),
  ('features','request_features','autplay.recommendation-request-features.v3'),
  ('snapshot-header','snapshot_header','autplay.recommendation-snapshot-header.v3')
 ) roles(role,key,domain) LOOP
  calculated:=app_private.native_v3_role_hash(role,body->key);
  IF domain IS NOT NULL THEN
   calculated:=sha256(app_private.native_served_lp(domain,ARRAY[calculated])); END IF;
  key:=CASE role WHEN 'metadata' THEN 'metadata_sha256' WHEN 'query' THEN 'query_document_sha256'
   WHEN 'features' THEN 'request_features_sha256' ELSE 'snapshot_header_sha256' END;
  IF header->>key IS DISTINCT FROM encode(calculated,'hex') THEN
   RAISE EXCEPTION 'NATIVE_V3_ROOT_COMPONENT' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF snapshot_ref IS NOT NULL THEN
  SELECT pg_column_size(bs.snapshot_document) INTO STRICT baseline_size
  FROM ml.recommendation_input_snapshot bs
  WHERE bs.recommendation_input_snapshot_id=r.baseline_snapshot_id AND bs.user_id=r.owner_user_id
   AND bs.input_snapshot_sha256=r.baseline_snapshot_sha256
   AND pg_column_compression(bs.snapshot_document) IS NULL;
  -- Only compact body/header projections and fixed scalars survive root decoding.
  IF 65536+pg_column_size(body)+pg_column_size(header)+pg_column_size(metadata)
   +pg_column_size(identity)+pg_column_size(snapshot_ref)>retained_bytes THEN
   RAISE EXCEPTION 'NATIVE_V4_RETAINED_BOUND' USING ERRCODE='23514'; END IF;
  PERFORM app_private.native_v3_compiler_exit(decode_depth);
  baseline_depth:=app_private.native_v3_compiler_enter(baseline_size);
  fetch_depth:=app_private.native_v3_compiler_enter(baseline_size);
  IF NOT EXISTS(SELECT 1 FROM ml.recommendation_input_snapshot bs
   WHERE bs.recommendation_input_snapshot_id=r.baseline_snapshot_id AND bs.user_id=r.owner_user_id
    AND bs.input_snapshot_sha256=r.baseline_snapshot_sha256
    AND bs.snapshot_document-'tracks'=body->'snapshot_header') THEN
   RAISE EXCEPTION 'NATIVE_V4_SNAPSHOT_HEADER' USING ERRCODE='23514'; END IF;
  SELECT bs.snapshot_document->'tracks' INTO STRICT tracks
  FROM ml.recommendation_input_snapshot bs
  WHERE bs.recommendation_input_snapshot_id=r.baseline_snapshot_id AND bs.user_id=r.owner_user_id;
  PERFORM app_private.native_v3_compiler_exit(fetch_depth);
  decode_depth:=app_private.native_v3_compiler_enter(greatest(0,compiler_peak-retained_bytes));
  -- The three independently recomputed ordered role hashes below also bind
  -- the retained tracks after the original training capture has been purged.
 END IF;
 t:=jsonb_array_length(tracks); item_count:=jsonb_array_length(body->'p11_items');
 IF t<>r.source_snapshot_track_count OR (scalar.pipeline_version='2' AND t>1024) OR item_count<>r.p11_item_count
  OR r.p11_item_count<>d.p11_item_count OR r.served_item_count<>d.served_item_count
  OR r.served_item_count<>item_count OR item_count>r.effective_limit THEN
  RAISE EXCEPTION 'NATIVE_V3_ROOT_COUNTS' USING ERRCODE='23514'; END IF;
 IF snapshot_ref IS NULL AND EXISTS(SELECT 1 FROM ml.native_original_capture_v3
  WHERE original_request_id=r.original_request_id AND owner_user_id=r.owner_user_id) THEN
  SELECT pg_column_size(bs.snapshot_document) INTO baseline_size
   FROM ml.recommendation_input_snapshot bs
   WHERE bs.recommendation_input_snapshot_id=r.baseline_snapshot_id
    AND bs.user_id=r.owner_user_id AND pg_column_compression(bs.snapshot_document) IS NULL;
  IF baseline_size IS NULL OR compiler_peak+2*baseline_size>8388608 THEN
   RAISE EXCEPTION 'NATIVE_V3_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
  baseline_depth:=app_private.native_v3_compiler_enter(2*baseline_size);
  IF tracks IS DISTINCT FROM (SELECT bs.snapshot_document->'tracks'
   FROM ml.recommendation_input_snapshot bs
   WHERE bs.recommendation_input_snapshot_id=r.baseline_snapshot_id
    AND bs.user_id=r.owner_user_id) THEN
   RAISE EXCEPTION 'NATIVE_V3_ROOT_BASELINE_TRACKS' USING ERRCODE='23514'; END IF;
  -- The comparison's temporary whole snapshot/tracks are gone at this point.
  PERFORM app_private.native_v3_compiler_exit(baseline_depth);
 END IF;
 -- Keep baseline order for the three track roles. Candidate membership uses a
 -- separate <=5000 UUID-only sort; never reorder the retained baseline itself.
 -- Validate each closed track and bind its position before projecting eligibility.
 calculated:=app_private.native_v3_role_set_digest('snapshot-track',p_served,tracks,candidates);
 IF header->>'snapshot_tracks_sha256' IS DISTINCT FROM encode(calculated,'hex') THEN
  RAISE EXCEPTION 'NATIVE_V3_ROOT_SET' USING ERRCODE='23514'; END IF;
 SELECT coalesce(jsonb_agg(track->'recording_id' ORDER BY (track->>'recording_id')::uuid),
  '[]'::jsonb) INTO candidates
 FROM (SELECT tracks->generate_series(0,t-1) AS track OFFSET 0) rows
 WHERE app_private.native_p11_source_candidate(scalar.pipeline_version::integer, track, scalar.surface);
 eligible:=jsonb_array_length(candidates);
 FOR idx IN 0..eligible-1 LOOP
  IF last_candidate IS NOT NULL AND (candidates->>idx)::uuid<=last_candidate THEN
   RAISE EXCEPTION 'NATIVE_V3_DUPLICATE_TRACK' USING ERRCODE='23514'; END IF;
  last_candidate:=(candidates->>idx)::uuid;
 END LOOP;
 IF (SELECT count(DISTINCT (track->>'recording_id')::uuid)
  FROM (SELECT tracks->generate_series(0,t-1) AS track OFFSET 0) rows)<>t
  OR eligible<>r.candidate_universe_count THEN
  RAISE EXCEPTION 'NATIVE_V3_ROOT_CANDIDATES' USING ERRCODE='23514'; END IF;
 FOR role,key IN SELECT * FROM (VALUES
  ('policy-track','policy_tracks_sha256'),('availability-track','availability_tracks_sha256'),
  ('candidate','candidate_universe_sha256')) roles(role,key) LOOP
  calculated:=app_private.native_v3_role_set_digest(role,p_served,tracks,candidates);
  IF header->>key IS DISTINCT FROM encode(calculated,'hex') THEN
   RAISE EXCEPTION 'NATIVE_V3_ROOT_SET' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF scalar.input_snapshot_sha256 IS DISTINCT FROM decode(header->>'snapshot_tracks_sha256','hex')
  OR scalar.policy_snapshot_sha256 IS DISTINCT FROM decode(header->>'policy_tracks_sha256','hex')
  OR scalar.availability_snapshot_ref IS DISTINCT FROM header->>'availability_tracks_sha256'
  OR scalar.request_sha256 IS DISTINCT FROM decode(header->>'query_document_sha256','hex')
  OR header->'snapshot_track_count' IS DISTINCT FROM to_jsonb(t)
  OR header->'candidate_universe_count' IS DISTINCT FROM to_jsonb(eligible)
  OR header->'p11_item_count' IS DISTINCT FROM to_jsonb(item_count)
  OR header->'served_item_count' IS DISTINCT FROM to_jsonb(item_count) THEN
  RAISE EXCEPTION 'NATIVE_V3_SERVED_HASH_DOMAINS' USING ERRCODE='23514'; END IF;
 -- Both scans are bounded before any item JSON hydration. Actual row components
 -- are projected explicitly; to_jsonb(whole common row) is never used.
 IF (SELECT count(*) FROM (SELECT 1 FROM ml.recommendation_item
   WHERE recommendation_request_id=p_served LIMIT 101) bounded)<>item_count
  OR (SELECT count(*) FROM (SELECT 1 FROM ml.recommendation_served_item
   WHERE recommendation_request_id=p_served LIMIT 101) bounded)<>item_count THEN
  RAISE EXCEPTION 'NATIVE_V3_SERVED_ITEMS_COUNT' USING ERRCODE='23514'; END IF;
 FOR idx IN 1..item_count LOOP
  leaf:=body->'p11_items'->(idx-1);
  PERFORM app_private.native_v3_role_hash('p11-item',leaf);
  IF (leaf->>'rank')::integer<>idx OR NOT candidates ? (leaf->>'recording_id') THEN
   RAISE EXCEPTION 'NATIVE_V3_SERVED_ITEM_MEMBERSHIP' USING ERRCODE='23514'; END IF;
  SELECT position INTO track_ordinal FROM
   (SELECT generate_series(0,t-1) AS position OFFSET 0) positions
   WHERE tracks#>>ARRAY[position::text,'recording_id']=leaf->>'recording_id';
  IF track_ordinal IS NULL OR leaf->'item_provenance'->'artist_key' IS DISTINCT FROM
   tracks#>ARRAY[track_ordinal::text,'artist_key']
   OR leaf->'item_provenance'->'release_key' IS DISTINCT FROM
    tracks#>ARRAY[track_ordinal::text,'release_key'] THEN
   RAISE EXCEPTION 'NATIVE_V3_SERVED_ITEM_TRACK' USING ERRCODE='23514'; END IF;
  first_source:=0;
  FOR contribution IN SELECT value FROM jsonb_array_elements(leaf->'contributions') LOOP
   source_position:=array_position(pipeline_sources,contribution->>'source_key');
   IF source_position IS NULL OR source_position<=first_source
    OR contribution->>'source_version'<>'1' THEN
    RAISE EXCEPTION 'NATIVE_V3_ITEM_PIPELINE' USING ERRCODE='23514'; END IF;
   first_source:=source_position;
  END LOOP;
  -- Physical compressed bytes cannot be used as a logical-size allowance.
  IF NOT EXISTS(SELECT 1 FROM ml.recommendation_item a
   WHERE a.recommendation_request_id=p_served AND a.rank=idx
    AND pg_column_compression(a.contributions) IS NULL
    AND pg_column_compression(a.availability_snapshot) IS NULL
    AND pg_column_compression(a.item_provenance) IS NULL
    AND pg_column_compression(a.candidate_sources) IS NULL
    AND pg_column_compression(a.reason_codes) IS NULL
    AND pg_column_size(a.contributions)<=65536 AND pg_column_size(a.availability_snapshot)<=8192
    AND pg_column_size(a.item_provenance)<=16384 AND pg_column_size(a.candidate_sources)<=65536
    AND pg_column_size(a.reason_codes)<=65536 AND octet_length(a.explanation_code)<=4096)
   OR NOT EXISTS(SELECT 1 FROM ml.recommendation_served_item a
    WHERE a.recommendation_request_id=p_served AND a.source_rank=idx
     AND pg_column_compression(a.contributions) IS NULL
     AND pg_column_compression(a.reasons) IS NULL
     AND pg_column_size(a.contributions)<=65536 AND pg_column_size(a.reasons)<=65536) THEN
   RAISE EXCEPTION 'NATIVE_V3_ITEM_COMPILER_BOUND' USING ERRCODE='23514'; END IF;
  SELECT jsonb_build_object('rank',a.rank,'recording_id',a.recording_id,
   'score_units_e8',a.score_units_e8,'candidate_sources',a.candidate_sources,
   'explanation_code',a.explanation_code,'availability_snapshot',a.availability_snapshot,
   'contributions',a.contributions,'reason_codes',a.reason_codes,'item_provenance',a.item_provenance)
   AS value,a.score,a.score_units_e8,a.candidate_sources_sha256,a.availability_snapshot_sha256,
   a.contributions_sha256,a.reason_codes_sha256,a.item_provenance_sha256,a.baseline_item_sha256
   INTO actual FROM ml.recommendation_item a
   WHERE a.recommendation_request_id=p_served AND a.rank=idx;
  served_leaf:=jsonb_build_object('source_rank',idx,'recording_id',leaf->'recording_id',
   'score_units_e8',leaf->'score_units_e8','score_kind','heuristic','attribution_source','P11',
   'reasons',leaf->'reason_codes','contributions',leaf->'contributions','original_p11_rank',idx,
   'origin','P11');
  SELECT jsonb_build_object('source_rank',a.source_rank,'recording_id',a.recording_id,
   'score_units_e8',a.score_units_e8,'score_kind',a.score_kind,'attribution_source',a.attribution_source,
   'reasons',a.reasons,'contributions',a.contributions,'original_p11_rank',a.original_p11_rank,
   'origin',a.origin) AS value,a.reasons_sha256,a.contributions_sha256,a.served_item_sha256
  INTO served FROM ml.recommendation_served_item a
   WHERE a.recommendation_request_id=p_served AND a.source_rank=idx;
  IF actual.value IS DISTINCT FROM leaf OR served.value IS DISTINCT FROM served_leaf
   OR actual.score IS DISTINCT FROM actual.score_units_e8::numeric/100000000
   OR actual.candidate_sources_sha256 IS DISTINCT FROM app_private.native_served_json_hash(
    leaf->'candidate_sources','$.items[].candidate_sources')
   OR actual.availability_snapshot_sha256 IS DISTINCT FROM app_private.native_served_json_hash(
    leaf->'availability_snapshot','$.items[].availability_snapshot')
   OR actual.contributions_sha256 IS DISTINCT FROM app_private.native_served_json_hash(
    leaf->'contributions','$.items[].contributions')
   OR actual.reason_codes_sha256 IS DISTINCT FROM app_private.native_served_json_hash(
    leaf->'reason_codes','$.items[].reason_codes')
   OR actual.item_provenance_sha256 IS DISTINCT FROM app_private.native_served_json_hash(
    leaf->'item_provenance','$.items[].item_provenance')
   OR actual.baseline_item_sha256 IS DISTINCT FROM
    app_private.native_served_baseline_digest(p_served,leaf)
   OR served.reasons_sha256 IS DISTINCT FROM actual.reason_codes_sha256
   OR served.contributions_sha256 IS DISTINCT FROM actual.contributions_sha256
   OR served.served_item_sha256 IS DISTINCT FROM
    app_private.native_served_item_digest(p_served,served_leaf)
   OR leaf->'availability_snapshot' IS DISTINCT FROM jsonb_build_object(
    'snapshot_sha256',header->>'availability_tracks_sha256') THEN
   RAISE EXCEPTION 'NATIVE_V3_SERVED_ITEMS_BINDING' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF snapshot_ref IS NOT NULL THEN
  tracks:=NULL; candidates:=NULL; leaf:=NULL; served_leaf:=NULL; contribution:=NULL;
  actual:=NULL; served:=NULL;
  PERFORM app_private.native_v3_compiler_exit(decode_depth);
  PERFORM app_private.native_v3_compiler_exit(baseline_depth);
  decode_depth:=app_private.native_v3_compiler_enter(greatest(0,compiler_peak-retained_bytes));
 END IF;
 calculated:=app_private.native_served_set_digest(p_served,true);
 IF calculated IS DISTINCT FROM d.p11_items_sha256
  OR calculated IS DISTINCT FROM d.p11_ranking_sha256
  OR header->>'p11_items_sha256' IS DISTINCT FROM encode(calculated,'hex') THEN
  RAISE EXCEPTION 'NATIVE_V3_P11_SET' USING ERRCODE='23514'; END IF;
 calculated:=app_private.native_served_set_digest(p_served,false);
 IF calculated IS DISTINCT FROM d.served_items_sha256
  OR header->>'served_items_sha256' IS DISTINCT FROM encode(calculated,'hex') THEN
  RAISE EXCEPTION 'NATIVE_V3_SERVED_SET' USING ERRCODE='23514'; END IF;
 calculated:=sha256(app_private.native_served_lp('autplay.recommendation-request-root.v3',
  ARRAY[app_private.native_v3_role_hash('root-header',header)]));
 IF calculated IS DISTINCT FROM r.request_root_sha256
  OR proof_root_sha IS DISTINCT FROM encode(calculated,'hex')
  OR d.decision_sha256 IS DISTINCT FROM app_private.native_v3_decision_digest(p_served) THEN
  RAISE EXCEPTION 'NATIVE_V3_ROOT_OR_DECISION' USING ERRCODE='23514'; END IF;
 -- A scalar resource length is independently remeasured from actual SERVED
 -- rows; original/P11 leaves cannot stand in for this separate role stream.
 -- Drop the last loop's projections before the fused measurer owns its real
 --512KiB parent and per-row child on the same repeatable-read compiler stack.
 -- Its complete extra work, including refusals, needs caller prepayment.
 leaf:=NULL; served_leaf:=NULL; contribution:=NULL; actual:=NULL; served:=NULL;
 IF r.served_items_byte_length IS DISTINCT FROM
  app_private.native_v3_item_rows_byte_length(r.owner_user_id,p_served,
   CASE current_setting('transaction_isolation') WHEN 'read committed' THEN 'RC_SERVED'
    ELSE 'SERVED' END) THEN
  RAISE EXCEPTION 'NATIVE_V3_SERVED_ITEMS_BYTES' USING ERRCODE='23514'; END IF;
 -- No variable JSONB/BYTEA or common-row projection may survive its frame.
 proof:=NULL; body:=NULL; header:=NULL; metadata:=NULL; identity:=NULL;
 tracks:=NULL; candidates:=NULL; leaf:=NULL; served_leaf:=NULL; contribution:=NULL;
 PERFORM app_private.native_v3_compiler_exit(decode_depth);
 id_sha:=NULL; origin_sha:=NULL; calculated:=NULL; proof_root_sha:=NULL;
 IF validated_versions IS NOT NULL THEN
  INSERT INTO app_private.native_v3_served_check VALUES(
   pg_current_xact_id(),pg_backend_pid(),p_served,validated_versions)
  ON CONFLICT (transaction_id,backend_pid,served_request_id)
   DO UPDATE SET versions=EXCLUDED.versions;
  validated_versions:=NULL;
 END IF;
 r:=NULL; d:=NULL; f:=NULL; scalar:=NULL; actual:=NULL; served:=NULL;
 PERFORM app_private.native_v3_compiler_exit(compiler_depth);
END $$;

CREATE FUNCTION app_private.assert_native_atomic_graph_v3(p_original uuid) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE served uuid; n integer; live boolean; compiler_depth integer;
BEGIN
 IF p_original IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_GRAPH_INPUT' USING ERRCODE='23514'; END IF;
 compiler_depth:=app_private.native_v3_compiler_enter(65536);
 SELECT count(*) INTO n FROM (SELECT 1 FROM ml.recommendation_request_root_v3
  WHERE original_request_id=p_original LIMIT 2) bounded;
 IF n<>1 THEN
  RAISE EXCEPTION 'NATIVE_V3_GRAPH_ROOT_CARDINALITY' USING ERRCODE='23514'; END IF;
 SELECT served_request_id INTO served FROM ml.recommendation_request_root_v3
  WHERE original_request_id=p_original;
 PERFORM app_private.assert_native_served_truth_v3(served);
 SELECT EXISTS(SELECT 1 FROM ml.native_original_capture_v3
  WHERE original_request_id=p_original) INTO live;
 IF live THEN
  -- Actual independently implemented original/source association closure. The
  -- installer above requires it; no optional dispatch or passing default.
  PERFORM app_private.assert_native_original_payloads_v3(p_original);
  IF NOT EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection_v3 c
   JOIN ml.native_original_capture_v3 b ON b.original_request_id=c.original_request_id
    AND b.owner_user_id=c.owner_user_id
   JOIN ml.recommendation_request_root_v3 r ON r.served_request_id=c.served_request_id
    AND r.original_request_id=c.original_request_id AND r.owner_user_id=c.owner_user_id
   WHERE c.original_request_id=p_original AND c.served_request_id=served
    AND c.original_capture_format_version=3 AND c.request_hash_format_version=3
    AND c.source_codec_sha256=b.source_codec_sha256 AND c.source_codec_sha256=r.source_codec_sha256
    AND c.capture_sha256=b.bundle_sha256 AND c.capture_sha256=r.capture_sha256
    AND c.eligibility_policy_sha256=b.eligibility_policy_sha256
    AND c.eligibility_policy_sha256=r.eligibility_policy_sha256
    AND c.consent_generation=b.consent_generation AND c.consent_generation=r.consent_generation
    AND c.consent_receipt_sha256=b.consent_receipt_sha256
    AND c.consent_receipt_sha256=r.consent_receipt_sha256
    AND c.input_origin_sha256=r.input_origin_sha256 AND c.root_proof_sha256=r.root_proof_sha256
    AND c.request_root_sha256=r.request_root_sha256
    AND c.decision_sha256=app_private.native_v3_decision_digest(served)
    AND c.connection_seal_sha256=app_private.native_v3_connection_seal(served)
    AND c.created_transaction=b.created_transaction AND c.created_transaction=r.created_transaction
    AND c.created_at=b.created_at AND c.created_at=r.created_at
    AND c.expires_at=b.expires_at AND c.expires_at=r.expires_at) THEN
   RAISE EXCEPTION 'NATIVE_V3_ATOMIC_CONNECTION' USING ERRCODE='23514'; END IF;
 ELSIF EXISTS(SELECT 1 FROM ml.native_capture_source_event_v3 WHERE original_request_id=p_original)
  OR EXISTS(SELECT 1 FROM ml.native_capture_lineage_cursor_v3 WHERE original_request_id=p_original)
  OR EXISTS(SELECT 1 FROM ml.native_capture_replay_v3 WHERE original_request_id=p_original)
  OR EXISTS(SELECT 1 FROM ml.native_atomic_capture_connection_v3
   WHERE original_request_id=p_original OR served_request_id=served) THEN
  RAISE EXCEPTION 'NATIVE_V3_REMOVAL_PARTIAL_GRAPH' USING ERRCODE='23514';
 END IF;
 -- Historical root validity deliberately does not imply an extant original,
 -- live replay/cursor, current consent, current source set, or clock freshness.
 PERFORM app_private.native_v3_compiler_exit(compiler_depth);
END $$;

REVOKE ALL ON FUNCTION app_private.native_v3_decision_digest(uuid),
 app_private.native_v3_connection_seal(uuid),app_private.assert_native_served_truth_v3(uuid),
 app_private.assert_native_atomic_graph_v3(uuid) FROM PUBLIC;
"""
)

NATIVE_REQUEST_V3_GRAPH_INSTALL_SQL = NATIVE_REQUEST_V3_GRAPH_INSTALL_SQL.replace(
    "__FEATURE_POLICY__", DEFAULT_ADAPTIVE_FEATURE_POLICY.content_sha256
)

NATIVE_REQUEST_V3_GRAPH_DROP_SQL = r"""
DROP FUNCTION app_private.assert_native_atomic_graph_v3(uuid);
DROP FUNCTION app_private.assert_native_served_truth_v3(uuid);
DROP FUNCTION app_private.native_v3_served_row_versions(uuid);
DROP TRIGGER native_v3_clear_served_check ON app_private.native_v3_served_check;
DROP FUNCTION app_private.native_v3_clear_served_check();
DROP TABLE app_private.native_v3_served_check;
DROP FUNCTION app_private.native_v3_connection_seal(uuid);
DROP FUNCTION app_private.native_v3_decision_digest(uuid);
DROP INDEX ml.native_root_v3_original_unique;
"""
