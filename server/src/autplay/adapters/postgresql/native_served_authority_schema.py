"""Dormant private P11 v2 served truth; no public role or rollout grants.

The migration owns execution of these statements. JSON documents enter only via
the private producer; no legacy capture or client JSON is promoted into truth.
"""

from __future__ import annotations

NATIVE_SERVED_AUTHORITY_INSTALL_SQL = r"""
CREATE FUNCTION app_private.native_served_lp(domain text, fields bytea[])
RETURNS bytea LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE result bytea; field bytea;
BEGIN
 IF domain IS NULL OR domain !~ '^[ -~]+$' OR length(domain)>200
  OR fields IS NULL OR cardinality(fields)>10001 THEN
  RAISE EXCEPTION 'native_served_lp_shape' USING ERRCODE='22023'; END IF;
 result:=convert_to(domain,'UTF8')||decode('00','hex')||int4send(cardinality(fields));
 FOREACH field IN ARRAY fields LOOP
  result:=result||CASE WHEN field IS NULL THEN decode('00','hex')
   ELSE decode('01','hex')||int4send(octet_length(field))||field END;
 END LOOP;
 RETURN result;
END; $$;

CREATE FUNCTION app_private.native_served_json_lp(
 value jsonb, schema jsonb, path text DEFAULT '$', depth integer DEFAULT 0)
RETURNS bytea LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE result bytea; child bytea; k text; v jsonb; n bigint; count integer;
BEGIN
 IF value IS NULL OR schema IS NULL OR depth>12 THEN
  RAISE EXCEPTION 'native_served_json_shape' USING ERRCODE='22023'; END IF;
 CASE jsonb_typeof(value)
 WHEN 'null' THEN result:=convert_to('n','UTF8');
 WHEN 'boolean' THEN result:=convert_to(CASE WHEN value='true' THEN 't' ELSE 'f' END,'UTF8');
 WHEN 'number' THEN
  IF value::text !~ '^-?(0|[1-9][0-9]*)$' THEN
   RAISE EXCEPTION 'native_served_json_integer' USING ERRCODE='22023'; END IF;
  n:=(value::text)::bigint; result:=convert_to('i','UTF8')||int8send(n);
 WHEN 'string' THEN
  k:=value#>>'{}'; child:=convert_to(k,'UTF8');
  IF octet_length(child)>4096 OR k<>normalize(k,NFC) THEN
   RAISE EXCEPTION 'native_served_json_string' USING ERRCODE='22023'; END IF;
  result:=convert_to('s','UTF8')||int4send(octet_length(child))||child;
 WHEN 'array' THEN
  count:=jsonb_array_length(value);
  IF count>4096 OR depth>=12 THEN
   RAISE EXCEPTION 'native_served_json_bounds' USING ERRCODE='22023'; END IF;
  result:=convert_to('a','UTF8')||int4send(count);
  FOR v IN SELECT x FROM jsonb_array_elements(value) x LOOP
   child:=app_private.native_served_json_lp(v,schema,path||'[]',depth+1);
   result:=result||int4send(octet_length(child))||child;
  END LOOP;
 WHEN 'object' THEN
  SELECT count(*) INTO count FROM jsonb_object_keys(value);
  IF count>4096 OR depth>=12 OR NOT schema ? path THEN
   RAISE EXCEPTION 'native_served_json_bounds' USING ERRCODE='22023'; END IF;
  result:=convert_to('o','UTF8')||int4send(count);
  FOR k,v IN SELECT * FROM jsonb_each(value) ORDER BY key COLLATE "C" LOOP
   IF k !~ '^[ -~]+$' OR NOT (schema->path) ? k THEN
    RAISE EXCEPTION 'native_served_json_member' USING ERRCODE='22023'; END IF;
   child:=convert_to(k,'UTF8'); result:=result||int4send(octet_length(child))||child;
   child:=app_private.native_served_json_lp(v,schema,path||'.'||k,depth+1);
   result:=result||int4send(octet_length(child))||child;
  END LOOP;
 ELSE RAISE EXCEPTION 'native_served_json_type' USING ERRCODE='22023';
 END CASE;
 IF octet_length(result)>65536 THEN
  RAISE EXCEPTION 'native_served_json_bytes' USING ERRCODE='22023'; END IF;
 RETURN result;
END; $$;

CREATE FUNCTION app_private.native_served_schema() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT '
{
 "$": ["request_id","surface","context","seed","effective_limit","endpoint_max","cutoff_at_ms",
  "pipeline_key","pipeline_version","pipeline_manifest_sha256","model_bundle_version",
  "candidate_policy_version","filter_policy_version","reranker_version",
  "interaction_watermark","catalog_snapshot","availability_snapshot_ref","request_document",
  "request_features","snapshot_document","policy_document","items"],
 "$.request_document":
  ["schema_version","canonicalization_version","user_id","surface","context","limit",
  "exploration_units_e8","seed","shadow"],
 "$.request_features": ["limit","exploration_units_e8"],
 "$.snapshot_document":
  ["schema_version","user_id","interaction_watermark","selection_policy","max_tracks","tracks"],
 "$.snapshot_document.tracks[]":
  ["recording_id","user_track_ref_id","artist_key","release_key","metadata_tokens",
  "availability","authorized","identity_status","preference","excluded","play_count",
  "organic_play_count","recommended_play_count","last_played_at_ms","added_at_ms",
  "release_date_ordinal"],
 "$.policy_document": ["tracks"],
 "$.policy_document.tracks[]": ["recording_id","identity_status","preference","excluded"],
 "$.availability_document": ["tracks"],
 "$.availability_document.tracks[]": ["recording_id","availability","authorized"],
 "$.items[]": ["rank","recording_id","score_units_e8","candidate_sources","explanation_code",
  "availability_snapshot","contributions","reason_codes","item_provenance"],
 "$.items[].availability_snapshot": ["snapshot_sha256"],
 "$.items[].contributions[]":
  ["source_key","source_version","source_rank","raw_score_units_e8","provenance"],
 "$.items[].contributions[].provenance":
  ["preference","organic_play_count","recommended_play_count_ignored_for_affinity","forgotten",
  "artist_affinity","metadata_token_overlap","release_key","seeded","previously_unplayed",
  "release_date_ordinal","cold_start_fallback"],
 "$.items[].item_provenance": ["artist_key","release_key","section"]
}'::jsonb $$;

CREATE FUNCTION app_private.native_served_json_hash(value jsonb,path text)
RETURNS bytea LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT sha256(app_private.native_served_json_lp(value,app_private.native_served_schema(),path)) $$;

CREATE FUNCTION app_private.lock_recommendation_request_v2(request_id uuid)
RETURNS void LANGUAGE sql SET search_path=pg_catalog AS $$
 SELECT pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay.recommendation-request.v2','UTF8')||decode('00','hex')||uuid_send(request_id)),
  'hex'),1,16))::bit(64)::bigint) $$;

ALTER TABLE ml.recommendation_request
 ADD COLUMN serving_contract_version integer NOT NULL DEFAULT 1,
 ADD CONSTRAINT ck_native_served_request_version CHECK (serving_contract_version IN (1,2)),
 ADD CONSTRAINT ck_native_served_request_surface CHECK (serving_contract_version=1 OR
  (serving_contract_version=2 AND surface IS NOT NULL AND surface IN
  ('recommendations','home','offline_pack')));
ALTER TABLE ml.recommendation_item
 ADD COLUMN score_units_e8 bigint,
 ADD COLUMN candidate_sources_sha256 bytea,
 ADD COLUMN availability_snapshot_sha256 bytea,
 ADD COLUMN contributions_sha256 bytea,
 ADD COLUMN reason_codes_sha256 bytea,
 ADD COLUMN item_provenance_sha256 bytea,
 ADD COLUMN baseline_item_sha256 bytea,
 ADD CONSTRAINT ck_native_served_baseline_score CHECK
  (score_units_e8 IS NULL OR score=score_units_e8::numeric/100000000);

CREATE TABLE app_private.native_served_authority_context (
 transaction_id bigint NOT NULL, recommendation_request_id uuid NOT NULL,
 operation text NOT NULL CHECK(operation IN ('CREATE','PURGE')),
 PRIMARY KEY(transaction_id,recommendation_request_id));

CREATE TABLE ml.recommendation_request_replay_payload (
 recommendation_request_id uuid PRIMARY KEY REFERENCES ml.recommendation_request ON DELETE RESTRICT,
 owner_user_id uuid NOT NULL,
 device_id uuid NOT NULL,
 journal_epoch uuid NOT NULL,
 server_instance_id uuid NOT NULL REFERENCES account.server_instance ON DELETE RESTRICT,
 identity_epoch bigint NOT NULL CHECK(identity_epoch>=1),
 effective_limit integer NOT NULL CHECK(effective_limit BETWEEN 1 AND 100),
 endpoint_max integer NOT NULL CHECK(endpoint_max=100),
 cutoff_at_ms bigint NOT NULL CHECK(cutoff_at_ms>=0),
 document jsonb NOT NULL,
 document_sha256 bytea NOT NULL CHECK(octet_length(document_sha256)=32),
 request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
 input_snapshot_sha256 bytea NOT NULL CHECK(octet_length(input_snapshot_sha256)=32),
 policy_snapshot_sha256 bytea NOT NULL CHECK(octet_length(policy_snapshot_sha256)=32),
 replay_expires_at timestamptz NOT NULL,
 FOREIGN KEY(owner_user_id,recommendation_request_id)
  REFERENCES ml.recommendation_request(user_id,recommendation_request_id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_user_id,device_id) REFERENCES account.device(user_id,device_id) ON DELETE
  RESTRICT);

CREATE TABLE ml.recommendation_serving_decision (
 recommendation_request_id uuid PRIMARY KEY REFERENCES ml.recommendation_request ON DELETE RESTRICT,
 state text NOT NULL CHECK(state='COMMITTED_P11'),
 serving_provenance text NOT NULL CHECK(serving_provenance='P11'),
 p11_ranking_sha256 bytea NOT NULL CHECK(octet_length(p11_ranking_sha256)=32),
 inference_evidence_sha256 bytea CHECK(inference_evidence_sha256 IS NULL),
 fallback_reason text CHECK(fallback_reason IS NULL),
 p11_item_count integer NOT NULL CHECK(p11_item_count BETWEEN 0 AND 100),
 p11_items_sha256 bytea NOT NULL CHECK(octet_length(p11_items_sha256)=32),
 served_item_count integer NOT NULL CHECK(served_item_count BETWEEN 0 AND 100),
 served_items_sha256 bytea NOT NULL CHECK(octet_length(served_items_sha256)=32),
 decision_sha256 bytea NOT NULL CHECK(octet_length(decision_sha256)=32),
 served_truth_expires_at timestamptz NOT NULL,
 CHECK(p11_ranking_sha256=p11_items_sha256 AND p11_item_count=served_item_count));

CREATE TABLE ml.recommendation_served_item (
 recommendation_request_id uuid NOT NULL REFERENCES ml.recommendation_request ON DELETE RESTRICT,
 source_rank integer NOT NULL CHECK(source_rank BETWEEN 1 AND 100),
 recording_id uuid NOT NULL REFERENCES catalog.recording ON DELETE RESTRICT,
 score_units_e8 bigint NOT NULL,
 score_kind text NOT NULL CHECK(score_kind='heuristic'),
 attribution_source text NOT NULL CHECK(attribution_source='P11'),
 reasons jsonb NOT NULL,
 reasons_sha256 bytea NOT NULL CHECK(octet_length(reasons_sha256)=32),
 contributions jsonb NOT NULL,
 contributions_sha256 bytea NOT NULL CHECK(octet_length(contributions_sha256)=32),
 original_p11_rank integer NOT NULL,
 origin text NOT NULL CHECK(origin='P11'),
 served_item_sha256 bytea NOT NULL CHECK(octet_length(served_item_sha256)=32),
 PRIMARY KEY(recommendation_request_id,source_rank),
 UNIQUE(recommendation_request_id,recording_id),
 UNIQUE(recommendation_request_id,source_rank,recording_id),
 CHECK(original_p11_rank=source_rank));

CREATE TABLE ml.recommendation_attribution_expiry_tombstone (
 owner_user_id uuid NOT NULL REFERENCES account.user_account ON DELETE RESTRICT,
 recommendation_request_id uuid NOT NULL,
 serving_contract_version integer NOT NULL CHECK(serving_contract_version=2),
 request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
 decision_sha256 bytea NOT NULL CHECK(octet_length(decision_sha256)=32),
 served_items_sha256 bytea NOT NULL CHECK(octet_length(served_items_sha256)=32),
 expired_at timestamptz NOT NULL,
 tombstone_expires_at timestamptz NOT NULL,
 reason text NOT NULL CHECK(reason='EXPIRED'),
 PRIMARY KEY(owner_user_id,recommendation_request_id));

CREATE TABLE app_private.native_served_authority_purge_receipt (
 owner_user_id uuid NOT NULL,
 recommendation_request_id uuid PRIMARY KEY,
 request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
 decision_sha256 bytea NOT NULL CHECK(octet_length(decision_sha256)=32),
 served_items_sha256 bytea NOT NULL CHECK(octet_length(served_items_sha256)=32),
 reason text NOT NULL CHECK(reason IN ('EXPIRED','ACCOUNT_DELETION')),
 purged_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL);
"""

NATIVE_SERVED_AUTHORITY_INSTALL_SQL += r"""
CREATE FUNCTION app_private.native_served_baseline_digest(request_id uuid,item jsonb)
RETURNS bytea LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT sha256(app_private.native_served_lp('autplay.recommendation-p11-item.v2',ARRAY[
  uuid_send(request_id),int8send((item->>'rank')::bigint),
  uuid_send((item->>'recording_id')::uuid),int8send((item->>'score_units_e8')::bigint),
  app_private.native_served_json_hash(item->'candidate_sources','$.items[].candidate_sources'),
  convert_to(item->>'explanation_code','UTF8'),
  app_private.native_served_json_hash(item->'availability_snapshot','$.items[].availability_snapshot'),
  app_private.native_served_json_hash(item->'contributions','$.items[].contributions'),
  app_private.native_served_json_hash(item->'reason_codes','$.items[].reason_codes'),
  app_private.native_served_json_hash(item->'item_provenance','$.items[].item_provenance')])) $$;

CREATE FUNCTION app_private.native_served_item_digest(request_id uuid,item jsonb)
RETURNS bytea LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT sha256(app_private.native_served_lp('autplay.recommendation-served-item.v2',ARRAY[
  uuid_send(request_id),int8send((item->>'source_rank')::bigint),
  uuid_send((item->>'recording_id')::uuid),int8send((item->>'score_units_e8')::bigint),
  convert_to(item->>'score_kind','UTF8'),convert_to(item->>'attribution_source','UTF8'),
  app_private.native_served_json_hash(item->'reasons','$.items[].reason_codes'),
  app_private.native_served_json_hash(item->'contributions','$.items[].contributions'),
  int8send((item->>'original_p11_rank')::bigint),convert_to(item->>'origin','UTF8')])) $$;

CREATE FUNCTION app_private.native_served_set_digest(request_id uuid,baseline boolean)
RETURNS bytea LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE fields bytea[]; item record; n bigint;
BEGIN
 IF baseline THEN
  SELECT count(*) INTO n FROM ml.recommendation_item WHERE recommendation_request_id=request_id;
  fields:=ARRAY[int8send(n)];
  FOR item IN SELECT rank,baseline_item_sha256 AS digest FROM ml.recommendation_item
   WHERE recommendation_request_id=request_id ORDER BY rank LOOP
   fields:=fields||ARRAY[int8send(item.rank::bigint),item.digest]; END LOOP;
 ELSE
  SELECT count(*) INTO n FROM ml.recommendation_served_item WHERE
  recommendation_request_id=request_id;
  fields:=ARRAY[int8send(n)];
  FOR item IN SELECT source_rank AS rank,served_item_sha256 AS digest FROM
  ml.recommendation_served_item
   WHERE recommendation_request_id=request_id ORDER BY source_rank LOOP
   fields:=fields||ARRAY[int8send(item.rank::bigint),item.digest]; END LOOP;
 END IF;
 RETURN sha256(app_private.native_served_lp(CASE WHEN baseline
  THEN 'autplay.recommendation-p11-set.v2' ELSE 'autplay.recommendation-served-set.v2' END,fields));
END; $$;

CREATE FUNCTION app_private.native_served_decision_digest(request_id uuid)
RETURNS bytea LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT sha256(app_private.native_served_lp('autplay.recommendation-decision.v2',ARRAY[
  uuid_send(r.recommendation_request_id),uuid_send(r.user_id),uuid_send(p.device_id),
  uuid_send(p.journal_epoch),uuid_send(p.server_instance_id),int8send(p.identity_epoch),
  r.request_sha256,p.document_sha256,convert_to(r.surface,'UTF8'),
  convert_to(r.pipeline_key,'UTF8'),
  convert_to(r.pipeline_version,'UTF8'),r.pipeline_manifest_sha256,int8send(p.cutoff_at_ms),
  convert_to(d.state,'UTF8'),convert_to(d.serving_provenance,'UTF8'),d.inference_evidence_sha256,
  int8send(d.p11_item_count::bigint),d.p11_items_sha256,
  int8send(d.served_item_count::bigint),d.served_items_sha256,
  int8send((extract(epoch FROM r.created_at)*1000000)::bigint),
  int8send((extract(epoch FROM d.served_truth_expires_at)*1000000)::bigint)]))
 FROM ml.recommendation_request r JOIN ml.recommendation_request_replay_payload p
  USING(recommendation_request_id)
 JOIN ml.recommendation_serving_decision d USING(recommendation_request_id)
 WHERE r.recommendation_request_id=request_id $$;

CREATE FUNCTION app_private.check_native_served_identity(
 p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,p_identity bigint)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE cursor_row sync.device_sync_cursor%ROWTYPE;
 floor_row sync.device_erasure_floor%ROWTYPE; owner_floor sync.owner_erasure_floor%ROWTYPE;
BEGIN
 PERFORM 1 FROM account.server_instance WHERE server_instance_id=p_server
  AND identity_epoch=p_identity FOR SHARE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'native_served_identity_mismatch' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM account.user_account WHERE user_id=p_owner AND status='ACTIVE'
  AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'native_served_identity_mismatch' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM account.device WHERE device_id=p_device AND user_id=p_owner
  AND revoked_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'native_served_identity_mismatch' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(p_owner)),'hex'),1,16))::bit(64)::bigint);
 SELECT * INTO cursor_row FROM sync.device_sync_cursor WHERE device_id=p_device
  AND user_id=p_owner AND journal_epoch=p_epoch FOR UPDATE;
 IF cursor_row.device_id IS NULL OR EXISTS(SELECT 1 FROM sync.device_erasure_invalidated_epoch
  WHERE device_id=p_device AND journal_epoch=p_epoch) THEN
  RAISE EXCEPTION 'native_served_identity_mismatch' USING ERRCODE='23514'; END IF;
 SELECT * INTO floor_row FROM sync.device_erasure_floor WHERE device_id=p_device;
 SELECT * INTO owner_floor FROM sync.owner_erasure_floor WHERE user_id=p_owner;
 IF (floor_row.device_id IS NOT NULL AND NOT floor_row.bootstrap_completed)
  OR (owner_floor.user_id IS NOT NULL AND
   ((floor_row.device_id IS NULL AND
  cursor_row.last_pulled_server_sequence<owner_floor.floor_sequence)
    OR (floor_row.device_id IS NOT NULL AND (floor_row.floor_sequence<owner_floor.floor_sequence
     OR floor_row.ledger_sequence IS DISTINCT FROM owner_floor.ledger_sequence
     OR floor_row.ledger_digest IS DISTINCT FROM owner_floor.ledger_digest)))) THEN
  RAISE EXCEPTION 'erasure_wipe_required' USING ERRCODE='55000'; END IF;
END; $$;

CREATE FUNCTION app_private.lock_native_served_server()
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 -- NOWAIT also fences callers that already hold their actor/request lock.
 PERFORM 1 FROM account.server_instance ORDER BY server_instance_id FOR SHARE NOWAIT;
END; $$;

CREATE FUNCTION app_private.protect_native_served_authority()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request_id uuid; version integer; mode text;
BEGIN
 request_id:=CASE WHEN TG_OP='DELETE' THEN OLD.recommendation_request_id ELSE
  NEW.recommendation_request_id END;
 IF TG_TABLE_NAME='recommendation_request' THEN
  version:=CASE WHEN TG_OP='DELETE' THEN OLD.serving_contract_version ELSE
  NEW.serving_contract_version END;
  IF TG_OP='UPDATE' AND OLD.serving_contract_version IS DISTINCT FROM
  NEW.serving_contract_version THEN
   RAISE EXCEPTION 'native_served_version_immutable' USING ERRCODE='55000'; END IF;
 ELSE SELECT serving_contract_version INTO version FROM ml.recommendation_request
  WHERE recommendation_request_id=request_id;
 END IF;
 IF TG_OP='UPDATE' AND OLD.recommendation_request_id IS DISTINCT FROM
  NEW.recommendation_request_id AND (version=2 OR EXISTS(SELECT 1
   FROM ml.recommendation_request WHERE recommendation_request_id=OLD.recommendation_request_id
    AND serving_contract_version=2)) THEN
  RAISE EXCEPTION 'native_served_authority_immutable' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME IN ('recommendation_request','recommendation_item') AND version=1 THEN
  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END; END IF;
 IF TG_TABLE_NAME='recommendation_item' AND TG_OP='DELETE' AND version IS NULL THEN
  -- A legacy parent's FK cascade runs after that parent has disappeared.
  -- Every v2 baseline carries native E8/hash markers, even before its purge.
  IF OLD.score_units_e8 IS NULL AND OLD.baseline_item_sha256 IS NULL
   AND OLD.candidate_sources_sha256 IS NULL AND OLD.availability_snapshot_sha256 IS NULL
   AND OLD.contributions_sha256 IS NULL AND OLD.reason_codes_sha256 IS NULL
   AND OLD.item_provenance_sha256 IS NULL THEN RETURN OLD; END IF;
 END IF;
 SELECT operation INTO mode FROM app_private.native_served_authority_context
  WHERE transaction_id=txid_current() AND recommendation_request_id=request_id;
 IF NOT ((TG_OP='INSERT' AND mode='CREATE') OR (TG_OP='DELETE' AND mode='PURGE'))
  OR mode IS NULL THEN
  RAISE EXCEPTION 'native_served_authority_immutable' USING ERRCODE='55000'; END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END; $$;

CREATE FUNCTION app_private.assert_native_served_authority(request_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ml.recommendation_request%ROWTYPE; p ml.recommendation_request_replay_payload%ROWTYPE;
 d ml.recommendation_serving_decision%ROWTYPE; b ml.recommendation_item%ROWTYPE;
 s ml.recommendation_served_item%ROWTYPE; n integer; position integer:=0; item jsonb;
 projected jsonb;
BEGIN
 SELECT * INTO r FROM ml.recommendation_request WHERE recommendation_request_id=request_id;
 IF r.recommendation_request_id IS NULL THEN
  IF (NOT EXISTS(SELECT 1 FROM app_private.native_served_authority_purge_receipt
    WHERE recommendation_request_id=request_id)
   AND NOT app_private.privacy_entity_allowed('ml.recommendation_request',request_id))
   OR EXISTS(SELECT 1 FROM ml.recommendation_request_replay_payload WHERE
  recommendation_request_id=request_id)
   OR EXISTS(SELECT 1 FROM ml.recommendation_serving_decision WHERE
  recommendation_request_id=request_id)
   OR EXISTS(SELECT 1 FROM ml.recommendation_served_item WHERE recommendation_request_id=request_id)
   OR EXISTS(SELECT 1 FROM ml.recommendation_item WHERE recommendation_request_id=request_id) THEN
   RAISE EXCEPTION 'native_served_graph_incomplete' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 IF r.serving_contract_version=1 THEN RETURN; END IF;
 SELECT * INTO p FROM ml.recommendation_request_replay_payload WHERE
  recommendation_request_id=request_id;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=request_id;
 IF p.recommendation_request_id IS NULL OR d.recommendation_request_id IS NULL
  OR r.request_document IS NOT NULL OR r.request_features<>'{}'::jsonb
  OR r.recommendation_input_snapshot_id IS NOT NULL OR r.recommendation_temporal_snapshot_id IS
  NOT NULL
  OR r.sona_shadow_status IS NOT NULL OR r.shadow OR r.surface IS NULL
  OR r.surface NOT IN ('recommendations','home','offline_pack')
  OR p.replay_expires_at IS DISTINCT FROM r.created_at+interval '180 days'
  OR d.served_truth_expires_at IS DISTINCT FROM r.created_at+interval '180 days'
  OR p.owner_user_id IS DISTINCT FROM r.user_id
  OR p.document_sha256 IS DISTINCT FROM app_private.native_served_json_hash(p.document,'$')
  OR p.request_sha256 IS DISTINCT FROM
   app_private.native_served_json_hash(p.document->'request_document','$.request_document')
  OR p.input_snapshot_sha256 IS DISTINCT FROM
   app_private.native_served_json_hash(p.document->'snapshot_document','$.snapshot_document')
  OR p.policy_snapshot_sha256 IS DISTINCT FROM
   app_private.native_served_json_hash(p.document->'policy_document','$.policy_document')
  OR p.request_sha256 IS DISTINCT FROM r.request_sha256
  OR p.input_snapshot_sha256 IS DISTINCT FROM r.input_snapshot_sha256
  OR p.policy_snapshot_sha256 IS DISTINCT FROM r.policy_snapshot_sha256
  OR d.served_item_count>least(p.effective_limit,p.endpoint_max)
  OR d.decision_sha256 IS DISTINCT FROM app_private.native_served_decision_digest(request_id) THEN
  RAISE EXCEPTION 'native_served_graph_incomplete' USING ERRCODE='23514'; END IF;
 IF p.document->>'request_id' IS DISTINCT FROM request_id::text
  OR p.document->>'surface' IS DISTINCT FROM r.surface
  OR p.document->>'context' IS DISTINCT FROM r.context
  OR p.document->'seed' IS DISTINCT FROM to_jsonb(r.seed)
  OR p.document->'effective_limit' IS DISTINCT FROM to_jsonb(p.effective_limit)
  OR p.document->'endpoint_max' IS DISTINCT FROM to_jsonb(p.endpoint_max)
  OR p.document->'cutoff_at_ms' IS DISTINCT FROM to_jsonb(p.cutoff_at_ms)
  OR p.document->'interaction_watermark' IS DISTINCT FROM to_jsonb(r.interaction_watermark)
  OR p.document->'catalog_snapshot' IS DISTINCT FROM to_jsonb(r.catalog_snapshot)
  OR p.document->>'pipeline_key' IS DISTINCT FROM r.pipeline_key
  OR p.document->>'pipeline_version' IS DISTINCT FROM r.pipeline_version
  OR p.document->>'pipeline_manifest_sha256' IS DISTINCT FROM
   encode(r.pipeline_manifest_sha256,'hex')
  OR p.document->>'model_bundle_version' IS DISTINCT FROM r.model_bundle_version
  OR p.document->>'candidate_policy_version' IS DISTINCT FROM r.candidate_policy_version
  OR p.document->>'filter_policy_version' IS DISTINCT FROM r.filter_policy_version
  OR p.document->>'reranker_version' IS DISTINCT FROM r.reranker_version
  OR p.document->>'availability_snapshot_ref' IS DISTINCT FROM r.availability_snapshot_ref
  OR p.document->'request_document' IS DISTINCT FROM jsonb_build_object(
   'schema_version',2,'canonicalization_version',1,'user_id',r.user_id::text,
   'surface',r.surface,'context',r.context,'limit',p.effective_limit,
   'exploration_units_e8',p.document->'request_features'->'exploration_units_e8',
   'seed',r.seed,'shadow',false)
  OR p.document->'request_features' IS DISTINCT FROM jsonb_build_object('limit',p.effective_limit,
   'exploration_units_e8',p.document->'request_document'->'exploration_units_e8')
  OR p.document->'snapshot_document'->>'user_id' IS DISTINCT FROM r.user_id::text
  OR p.document->'snapshot_document'->'interaction_watermark' IS DISTINCT FROM
   to_jsonb(r.interaction_watermark)
  OR r.request_schema_version IS DISTINCT FROM 2
  OR r.request_canonicalization_version IS DISTINCT FROM 1
  OR NOT EXISTS(SELECT 1 FROM ml.recommendation_pipeline_version pipeline
   WHERE pipeline.pipeline_key=r.pipeline_key AND pipeline.version=r.pipeline_version
    AND pipeline.manifest_sha256=r.pipeline_manifest_sha256) THEN
  RAISE EXCEPTION 'native_served_replay_binding' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('tracks',coalesce(jsonb_agg(jsonb_build_object(
  'recording_id',x->'recording_id','identity_status',x->'identity_status',
  'preference',x->'preference','excluded',x->'excluded') ORDER BY ord),'[]'::jsonb)) INTO projected
  FROM jsonb_array_elements(p.document->'snapshot_document'->'tracks') WITH ORDINALITY AS t(x,ord);
 IF projected IS DISTINCT FROM p.document->'policy_document' THEN
  RAISE EXCEPTION 'native_served_policy_binding' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('tracks',coalesce(jsonb_agg(jsonb_build_object(
  'recording_id',x->'recording_id','availability',x->'availability','authorized',x->'authorized')
  ORDER BY ord),'[]'::jsonb)) INTO projected
  FROM jsonb_array_elements(p.document->'snapshot_document'->'tracks') WITH ORDINALITY AS t(x,ord);
 IF encode(app_private.native_served_json_hash(projected,'$.availability_document'),'hex')
  IS DISTINCT FROM r.availability_snapshot_ref THEN
  RAISE EXCEPTION 'native_served_availability_binding' USING ERRCODE='23514'; END IF;
 SELECT count(*) INTO n FROM ml.recommendation_item WHERE recommendation_request_id=request_id;
 IF n<>d.p11_item_count OR n<>jsonb_array_length(p.document->'items') THEN
  RAISE EXCEPTION 'native_served_graph_incomplete' USING ERRCODE='23514'; END IF;
 FOR b IN SELECT * FROM ml.recommendation_item WHERE recommendation_request_id=request_id ORDER
  BY rank LOOP
  position:=position+1; item:=p.document->'items'->(position-1);
  SELECT * INTO s FROM ml.recommendation_served_item
   WHERE recommendation_request_id=request_id AND source_rank=b.rank;
  IF b.rank<>position OR b.score_units_e8 IS NULL
   OR b.score<>b.score_units_e8::numeric/100000000
   OR b.baseline_item_sha256 IS DISTINCT FROM
  app_private.native_served_baseline_digest(request_id,to_jsonb(b))
   OR b.baseline_item_sha256 IS DISTINCT FROM
  app_private.native_served_baseline_digest(request_id,item)
   OR b.candidate_sources_sha256 IS DISTINCT FROM
  app_private.native_served_json_hash(to_jsonb(b.candidate_sources),'$.items[].candidate_sources')
   OR b.availability_snapshot_sha256 IS DISTINCT FROM
  app_private.native_served_json_hash(b.availability_snapshot,'$.items[].availability_snapshot')
   OR b.contributions_sha256 IS DISTINCT FROM
  app_private.native_served_json_hash(b.contributions,'$.items[].contributions')
   OR b.reason_codes_sha256 IS DISTINCT FROM
  app_private.native_served_json_hash(to_jsonb(b.reason_codes),'$.items[].reason_codes')
   OR b.item_provenance_sha256 IS DISTINCT FROM
  app_private.native_served_json_hash(b.item_provenance,'$.items[].item_provenance')
   OR b.availability_snapshot IS DISTINCT FROM
    jsonb_build_object('snapshot_sha256',r.availability_snapshot_ref)
   OR s.recommendation_request_id IS NULL OR s.recording_id IS DISTINCT FROM b.recording_id
   OR s.score_units_e8 IS DISTINCT FROM b.score_units_e8 OR s.reasons IS DISTINCT FROM
  to_jsonb(b.reason_codes)
   OR s.contributions IS DISTINCT FROM b.contributions
   OR s.reasons_sha256 IS DISTINCT FROM b.reason_codes_sha256
   OR s.contributions_sha256 IS DISTINCT FROM b.contributions_sha256
   OR s.served_item_sha256 IS DISTINCT FROM
  app_private.native_served_item_digest(request_id,to_jsonb(s)) THEN
   RAISE EXCEPTION 'native_served_graph_incomplete' USING ERRCODE='23514'; END IF;
 END LOOP;
 SELECT count(*) INTO n FROM ml.recommendation_served_item WHERE
  recommendation_request_id=request_id;
 IF n<>d.served_item_count OR d.p11_items_sha256 IS DISTINCT FROM
  app_private.native_served_set_digest(request_id,true)
  OR d.served_items_sha256 IS DISTINCT FROM
  app_private.native_served_set_digest(request_id,false) THEN
  RAISE EXCEPTION 'native_served_graph_incomplete' USING ERRCODE='23514'; END IF;
END; $$;

CREATE FUNCTION app_private.check_native_served_graph() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request_id uuid;
BEGIN
 request_id:=CASE WHEN TG_OP='DELETE' THEN OLD.recommendation_request_id ELSE
  NEW.recommendation_request_id END;
 IF TG_OP='UPDATE' AND OLD.recommendation_request_id IS DISTINCT FROM request_id
  AND EXISTS(SELECT 1 FROM ml.recommendation_request
   WHERE recommendation_request_id=OLD.recommendation_request_id AND serving_contract_version=2)
  THEN PERFORM app_private.assert_native_served_authority(OLD.recommendation_request_id); END IF;
 IF TG_TABLE_NAME='recommendation_request' THEN
  IF (TG_OP='DELETE' AND OLD.serving_contract_version=1) OR
   (TG_OP<>'DELETE' AND NEW.serving_contract_version=1) THEN RETURN NULL; END IF;
 ELSIF TG_TABLE_NAME='recommendation_item' THEN
  IF (TG_OP='DELETE' AND OLD.score_units_e8 IS NULL) OR
   (TG_OP<>'DELETE' AND NEW.score_units_e8 IS NULL) THEN RETURN NULL; END IF;
 END IF;
 PERFORM app_private.assert_native_served_authority(request_id);
 RETURN NULL;
END; $$;
"""

NATIVE_SERVED_AUTHORITY_INSTALL_SQL += r"""
CREATE FUNCTION app_private.ensure_native_served_expiry(p_owner uuid,p_request uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ml.recommendation_request%ROWTYPE; d ml.recommendation_serving_decision%ROWTYPE;
BEGIN
 PERFORM app_private.lock_native_served_server();
 PERFORM 1 FROM account.user_account WHERE user_id=p_owner FOR UPDATE;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(p_owner)),
  'hex'),1,16))::bit(64)::bigint);
 PERFORM app_private.lock_recommendation_request_v2(p_request);
 SELECT * INTO r FROM ml.recommendation_request WHERE recommendation_request_id=p_request
  AND user_id=p_owner AND serving_contract_version=2;
 IF r.recommendation_request_id IS NULL THEN RETURN false; END IF;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=p_request;
 IF clock_timestamp()<d.served_truth_expires_at THEN RETURN false; END IF;
 PERFORM app_private.assert_native_served_authority(p_request);
 INSERT INTO app_private.native_served_authority_context VALUES(txid_current(),p_request,'PURGE')
  ON CONFLICT DO NOTHING;
 IF clock_timestamp()<r.created_at+interval '400 days' THEN
  INSERT INTO ml.recommendation_attribution_expiry_tombstone VALUES(
   p_owner,p_request,2,r.request_sha256,d.decision_sha256,d.served_items_sha256,
   d.served_truth_expires_at,r.created_at+interval '400 days','EXPIRED') ON CONFLICT DO NOTHING;
 END IF;
 DELETE FROM app_private.native_served_authority_context
  WHERE transaction_id=txid_current() AND recommendation_request_id=p_request;
 RETURN true;
END; $$;

CREATE FUNCTION app_private.expire_native_served_authority(p_owner uuid,p_request uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ml.recommendation_request%ROWTYPE;
BEGIN
 PERFORM app_private.lock_native_served_server();
 PERFORM 1 FROM account.user_account WHERE user_id=p_owner FOR UPDATE;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(p_owner)),
  'hex'),1,16))::bit(64)::bigint);
 PERFORM app_private.lock_recommendation_request_v2(p_request);
 SELECT * INTO r FROM ml.recommendation_request WHERE user_id=p_owner
  AND recommendation_request_id=p_request AND serving_contract_version=2;
 IF r.recommendation_request_id IS NOT NULL THEN
  IF NOT app_private.ensure_native_served_expiry(p_owner,p_request) THEN
   RETURN jsonb_build_object('outcome','NOT_EXPIRED'); END IF;
  IF clock_timestamp()<r.created_at+interval '400 days' THEN
   RETURN jsonb_build_object('outcome','ATTRIBUTION_EXPIRED'); END IF;
 ELSIF EXISTS(SELECT 1 FROM ml.recommendation_attribution_expiry_tombstone
  WHERE owner_user_id=p_owner AND recommendation_request_id=p_request
  AND tombstone_expires_at>clock_timestamp()) THEN
  RETURN jsonb_build_object('outcome','ATTRIBUTION_EXPIRED');
 END IF;
 RETURN jsonb_build_object('outcome','ATTRIBUTION_NOT_FOUND');
END; $$;

CREATE FUNCTION app_private.read_native_served_authority(p_owner uuid,p_request uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ml.recommendation_request%ROWTYPE; p ml.recommendation_request_replay_payload%ROWTYPE;
 d ml.recommendation_serving_decision%ROWTYPE; result jsonb; items jsonb;
BEGIN
 PERFORM app_private.lock_native_served_server();
 PERFORM 1 FROM account.user_account WHERE user_id=p_owner AND status='ACTIVE' AND deleted_at IS
  NULL FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('status','ATTRIBUTION_NOT_FOUND'); END IF;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(p_owner)),
  'hex'),1,16))::bit(64)::bigint);
 PERFORM app_private.lock_recommendation_request_v2(p_request);
 SELECT * INTO r FROM ml.recommendation_request WHERE recommendation_request_id=p_request
  AND user_id=p_owner AND serving_contract_version=2;
 IF r.recommendation_request_id IS NULL THEN
  IF EXISTS(SELECT 1 FROM ml.recommendation_attribution_expiry_tombstone
   WHERE owner_user_id=p_owner AND recommendation_request_id=p_request AND
  tombstone_expires_at>clock_timestamp()) THEN
   RETURN jsonb_build_object('status','REPLAY_EXPIRED'); END IF;
  RETURN jsonb_build_object('status','ATTRIBUTION_NOT_FOUND');
 END IF;
 IF app_private.ensure_native_served_expiry(p_owner,p_request) THEN
  RETURN jsonb_build_object('status',CASE WHEN clock_timestamp()<r.created_at+interval '400 days'
   THEN 'REPLAY_EXPIRED' ELSE 'ATTRIBUTION_NOT_FOUND' END); END IF;
 PERFORM app_private.assert_native_served_authority(p_request);
 SELECT * INTO p FROM ml.recommendation_request_replay_payload WHERE
  recommendation_request_id=p_request;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=p_request;
 SELECT coalesce(jsonb_agg(jsonb_build_object('source_rank',source_rank,'recording_id',recording_id,
  'score_units_e8',score_units_e8,'score_kind',score_kind,'attribution_source',attribution_source,
  'reasons',reasons,'contributions',contributions,'original_p11_rank',original_p11_rank,
  'origin',origin,'served_item_sha256',encode(served_item_sha256,'hex')) ORDER BY
  source_rank),'[]'::jsonb)
  INTO items FROM ml.recommendation_served_item WHERE recommendation_request_id=p_request;
 result:=jsonb_build_object('status','COMMITTED_P11','request_id',p_request,
  'owner_user_id',p_owner,'device_id',p.device_id,'journal_epoch',p.journal_epoch,
  'server_instance_id',p.server_instance_id,'identity_epoch',p.identity_epoch,
  'created_at',r.created_at,'served_truth_expires_at',d.served_truth_expires_at,
  'request_sha256',encode(r.request_sha256,'hex'),'document_sha256',encode(p.document_sha256,'hex'),
  'decision_sha256',encode(d.decision_sha256,'hex'),'p11_items_sha256',encode(d.p11_items_sha256,'hex'),
  'served_items_sha256',encode(d.served_items_sha256,'hex'),'served_item_count',d.served_item_count,
  'document',p.document,'served_items',items,'replayed',true);
 -- This locked database-clock check is the replay linearization point.
 IF clock_timestamp()>=d.served_truth_expires_at THEN
  PERFORM app_private.ensure_native_served_expiry(p_owner,p_request);
  RETURN jsonb_build_object('status','REPLAY_EXPIRED'); END IF;
 RETURN result;
END; $$;

CREATE FUNCTION app_private.commit_native_served_authority(
 p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,p_identity bigint,doc jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request_id uuid; created timestamptz; expires timestamptz; schema jsonb;
 document_hash bytea; request_hash bytea; input_hash bytea; policy_hash bytea;
 availability_hash text; projected jsonb; item jsonb; served jsonb; idx integer:=0;
 n integer; prior ml.recommendation_request_replay_payload%ROWTYPE; p11_hash bytea; served_hash
  bytea;
BEGIN
 schema:=app_private.native_served_schema();
 document_hash:=app_private.native_served_json_hash(doc,'$');
 IF jsonb_typeof(doc)<>'object' OR NOT doc ?& ARRAY(SELECT jsonb_array_elements_text(schema->'$'))
  OR jsonb_typeof(doc->'items') IS DISTINCT FROM 'array'
  OR jsonb_typeof(doc->'snapshot_document'->'tracks') IS DISTINCT FROM 'array'
  OR jsonb_typeof(doc->'policy_document'->'tracks') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'native_served_document_shape' USING ERRCODE='22023'; END IF;
 request_id:=(doc->>'request_id')::uuid;
 PERFORM app_private.check_native_served_identity(p_owner,p_device,p_epoch,p_server,p_identity);
 PERFORM app_private.lock_recommendation_request_v2(request_id);
 SELECT * INTO prior FROM ml.recommendation_request_replay_payload WHERE
  recommendation_request_id=request_id;
 IF prior.recommendation_request_id IS NOT NULL THEN
  IF prior.owner_user_id IS DISTINCT FROM p_owner OR prior.device_id IS DISTINCT FROM p_device
   OR prior.journal_epoch IS DISTINCT FROM p_epoch OR prior.server_instance_id IS DISTINCT FROM
  p_server
   OR prior.identity_epoch IS DISTINCT FROM p_identity OR prior.document_sha256 IS DISTINCT FROM
  document_hash THEN
   RAISE EXCEPTION 'native_served_retry_conflict' USING ERRCODE='23514'; END IF;
  RETURN app_private.read_native_served_authority(p_owner,request_id);
 END IF;
 IF EXISTS(SELECT 1 FROM ml.recommendation_request WHERE recommendation_request_id=request_id)
  OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_expiry_tombstone WHERE
  recommendation_request_id=request_id)
  OR EXISTS(SELECT 1 FROM app_private.native_served_authority_purge_receipt WHERE
  recommendation_request_id=request_id) THEN
  RAISE EXCEPTION 'native_served_request_reused' USING ERRCODE='23514'; END IF;
 created:=clock_timestamp(); expires:=created+interval '180 days';
 IF (doc->>'effective_limit')::integer NOT BETWEEN 1 AND 100
  OR (doc->>'endpoint_max')::integer<>100 OR (doc->>'cutoff_at_ms')::bigint<0
  OR (doc->>'cutoff_at_ms')::bigint>(extract(epoch FROM created)*1000)::bigint
  OR doc->>'surface' NOT IN ('recommendations','home','offline_pack')
  OR doc->'request_document' IS DISTINCT FROM jsonb_build_object(
   'schema_version',2,'canonicalization_version',1,'user_id',p_owner::text,
   'surface',doc->'surface','context',doc->'context','limit',doc->'effective_limit',
   'exploration_units_e8',doc->'request_features'->'exploration_units_e8','seed',doc->'seed','shadow',false)
  OR doc->'request_features' IS DISTINCT FROM jsonb_build_object('limit',doc->'effective_limit',
   'exploration_units_e8',doc->'request_document'->'exploration_units_e8')
  OR (doc->'request_features'->>'exploration_units_e8')::bigint NOT BETWEEN 0 AND 100000000
  OR doc->'snapshot_document'->>'user_id' IS DISTINCT FROM p_owner::text
  OR doc->'snapshot_document'->'interaction_watermark' IS DISTINCT FROM doc->'interaction_watermark'
  OR doc->'snapshot_document'->>'schema_version' IS DISTINCT FROM '1'
  OR doc->'snapshot_document'->>'selection_policy' IS DISTINCT FROM
  'most_recent_added_then_recording_id_v1'
  OR doc->'snapshot_document'->>'max_tracks' IS DISTINCT FROM '5000' THEN
  RAISE EXCEPTION 'native_served_request_binding' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM ml.recommendation_pipeline_version WHERE pipeline_key=doc->>'pipeline_key'
  AND version=doc->>'pipeline_version' AND
  encode(manifest_sha256,'hex')=doc->>'pipeline_manifest_sha256'
  AND lifecycle_status='ACTIVE' FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'native_served_pipeline_binding' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('tracks',coalesce(jsonb_agg(jsonb_build_object(
  'recording_id',x->'recording_id','identity_status',x->'identity_status',
  'preference',x->'preference','excluded',x->'excluded') ORDER BY ord),'[]'::jsonb)) INTO projected
  FROM jsonb_array_elements(doc->'snapshot_document'->'tracks') WITH ORDINALITY AS t(x,ord);
 IF projected IS DISTINCT FROM doc->'policy_document' THEN
  RAISE EXCEPTION 'native_served_policy_binding' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('tracks',coalesce(jsonb_agg(jsonb_build_object(
  'recording_id',x->'recording_id','availability',x->'availability','authorized',x->'authorized')
  ORDER BY ord),'[]'::jsonb)) INTO projected
  FROM jsonb_array_elements(doc->'snapshot_document'->'tracks') WITH ORDINALITY AS t(x,ord);
 availability_hash:=encode(app_private.native_served_json_hash(projected,'$.availability_document'),'hex');
 IF availability_hash IS DISTINCT FROM doc->>'availability_snapshot_ref' THEN
  RAISE EXCEPTION 'native_served_availability_binding' USING ERRCODE='23514'; END IF;
 request_hash:=app_private.native_served_json_hash(doc->'request_document','$.request_document');
 input_hash:=app_private.native_served_json_hash(doc->'snapshot_document','$.snapshot_document');
 policy_hash:=app_private.native_served_json_hash(doc->'policy_document','$.policy_document');
 n:=jsonb_array_length(doc->'items');
 IF n>least((doc->>'effective_limit')::integer,(doc->>'endpoint_max')::integer) THEN
  RAISE EXCEPTION 'native_served_item_count' USING ERRCODE='23514'; END IF;
 INSERT INTO app_private.native_served_authority_context VALUES(txid_current(),request_id,'CREATE');
 INSERT INTO ml.recommendation_request(recommendation_request_id,user_id,context,surface,
  pipeline_key,pipeline_version,pipeline_manifest_sha256,request_schema_version,
  request_canonicalization_version,request_sha256,input_snapshot_sha256,interaction_watermark,
  catalog_snapshot,availability_snapshot_ref,policy_snapshot_sha256,model_bundle_version,
  candidate_policy_version,filter_policy_version,reranker_version,seed,created_at,serving_contract_version)
 VALUES(request_id,p_owner,doc->>'context',doc->>'surface',doc->>'pipeline_key',doc->>'pipeline_version',
  decode(doc->>'pipeline_manifest_sha256','hex'),2,1,request_hash,input_hash,
  (doc->>'interaction_watermark')::bigint,(doc->>'catalog_snapshot')::bigint,availability_hash,
  policy_hash,doc->>'model_bundle_version',doc->>'candidate_policy_version',doc->>'filter_policy_version',
  doc->>'reranker_version',(doc->>'seed')::bigint,created,2);
 INSERT INTO ml.recommendation_request_replay_payload VALUES(request_id,p_owner,p_device,p_epoch,
  p_server,p_identity,(doc->>'effective_limit')::integer,(doc->>'endpoint_max')::integer,
  (doc->>'cutoff_at_ms')::bigint,doc,document_hash,request_hash,input_hash,policy_hash,expires);
 FOR item IN SELECT x FROM jsonb_array_elements(doc->'items') x LOOP
  idx:=idx+1;
  IF NOT item ?& ARRAY(SELECT jsonb_array_elements_text(schema->'$.items[]'))
   OR (item->>'rank')::integer IS DISTINCT FROM idx
   OR jsonb_typeof(item->'candidate_sources') IS DISTINCT FROM 'array'
   OR jsonb_typeof(item->'reason_codes') IS DISTINCT FROM 'array'
   OR jsonb_typeof(item->'contributions') IS DISTINCT FROM 'array'
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(item->'candidate_sources') x WHERE
  jsonb_typeof(x)<>'string')
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(item->'reason_codes') x WHERE
  jsonb_typeof(x)<>'string')
   OR item->'availability_snapshot' IS DISTINCT FROM
  jsonb_build_object('snapshot_sha256',availability_hash)
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(doc->'snapshot_document'->'tracks') x
    WHERE x->>'recording_id'=item->>'recording_id' AND x->'authorized'='true'::jsonb
    AND x->'excluded'='false'::jsonb AND x->>'identity_status'='ACTIVE') THEN
   RAISE EXCEPTION 'native_served_item_binding' USING ERRCODE='23514'; END IF;
  INSERT INTO ml.recommendation_item(recommendation_request_id,rank,recording_id,score,
   candidate_sources,explanation_code,availability_snapshot,contributions,reason_codes,item_provenance,
   score_units_e8,candidate_sources_sha256,availability_snapshot_sha256,contributions_sha256,
   reason_codes_sha256,item_provenance_sha256,baseline_item_sha256)
  VALUES(request_id,idx,(item->>'recording_id')::uuid,(item->>'score_units_e8')::numeric/100000000,
   ARRAY(SELECT jsonb_array_elements_text(item->'candidate_sources')),item->>'explanation_code',
   item->'availability_snapshot',item->'contributions',ARRAY(SELECT
  jsonb_array_elements_text(item->'reason_codes')),
   item->'item_provenance',(item->>'score_units_e8')::bigint,
   app_private.native_served_json_hash(item->'candidate_sources','$.items[].candidate_sources'),
   app_private.native_served_json_hash(item->'availability_snapshot','$.items[].availability_snapshot'),
   app_private.native_served_json_hash(item->'contributions','$.items[].contributions'),
   app_private.native_served_json_hash(item->'reason_codes','$.items[].reason_codes'),
   app_private.native_served_json_hash(item->'item_provenance','$.items[].item_provenance'),
   app_private.native_served_baseline_digest(request_id,item));
  served:=jsonb_build_object('source_rank',idx,'recording_id',item->'recording_id',
   'score_units_e8',item->'score_units_e8','score_kind','heuristic','attribution_source','P11',
   'reasons',item->'reason_codes','contributions',item->'contributions','original_p11_rank',idx,'origin','P11');
  INSERT INTO ml.recommendation_served_item VALUES(request_id,idx,(item->>'recording_id')::uuid,
   (item->>'score_units_e8')::bigint,'heuristic','P11',item->'reason_codes',
   app_private.native_served_json_hash(item->'reason_codes','$.items[].reason_codes'),item->'contributions',
   app_private.native_served_json_hash(item->'contributions','$.items[].contributions'),idx,'P11',
   app_private.native_served_item_digest(request_id,served));
 END LOOP;
 p11_hash:=app_private.native_served_set_digest(request_id,true);
 served_hash:=app_private.native_served_set_digest(request_id,false);
 -- Calculate the digest from final rows before the single immutable decision INSERT.
 INSERT INTO ml.recommendation_serving_decision VALUES(request_id,'COMMITTED_P11','P11',p11_hash,
  NULL,NULL,n,p11_hash,n,served_hash,
  sha256(app_private.native_served_lp('autplay.recommendation-decision.v2',ARRAY[
   uuid_send(request_id),uuid_send(p_owner),uuid_send(p_device),uuid_send(p_epoch),uuid_send(p_server),
   int8send(p_identity),request_hash,document_hash,convert_to(doc->>'surface','UTF8'),
   convert_to(doc->>'pipeline_key','UTF8'),
   convert_to(doc->>'pipeline_version','UTF8'),decode(doc->>'pipeline_manifest_sha256','hex'),
   int8send((doc->>'cutoff_at_ms')::bigint),convert_to('COMMITTED_P11','UTF8'),convert_to('P11','UTF8'),
   NULL::bytea,int8send(n::bigint),p11_hash,int8send(n::bigint),served_hash,
   int8send((extract(epoch FROM created)*1000000)::bigint),int8send((extract(epoch FROM
  expires)*1000000)::bigint)])),expires);
 PERFORM app_private.assert_native_served_authority(request_id);
 DELETE FROM app_private.native_served_authority_context WHERE transaction_id=txid_current()
  AND recommendation_request_id=request_id;
 RETURN app_private.read_native_served_authority(p_owner,request_id)
  ||jsonb_build_object('replayed',false);
END; $$;

CREATE FUNCTION app_private.purge_native_served_authority(p_owner uuid,p_request uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ml.recommendation_request%ROWTYPE; d ml.recommendation_serving_decision%ROWTYPE;
 remaining bigint;
BEGIN
 PERFORM app_private.lock_native_served_server();
 PERFORM 1 FROM account.user_account WHERE user_id=p_owner FOR UPDATE;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(p_owner)),
  'hex'),1,16))::bit(64)::bigint);
 PERFORM app_private.lock_recommendation_request_v2(p_request);
 SELECT * INTO r FROM ml.recommendation_request WHERE recommendation_request_id=p_request
  AND user_id=p_owner AND serving_contract_version=2;
 IF r.recommendation_request_id IS NULL THEN
  RETURN jsonb_build_object('status',CASE WHEN EXISTS(SELECT 1 FROM
  app_private.native_served_authority_purge_receipt
   WHERE owner_user_id=p_owner AND recommendation_request_id=p_request) THEN 'PURGED' ELSE
  'ATTRIBUTION_NOT_FOUND' END);
 END IF;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=p_request;
 IF NOT app_private.ensure_native_served_expiry(p_owner,p_request) THEN
  RAISE EXCEPTION 'native_served_retention_active' USING ERRCODE='55000'; END IF;
 IF to_regclass('ml.recommendation_attribution_association') IS NOT NULL THEN
  IF to_regprocedure(
   'app_private.purge_native_request_attributions_v2(uuid,uuid,text,integer)') IS NOT NULL THEN
   EXECUTE 'SELECT app_private.purge_native_request_attributions_v2($1,$2,$3,256)'
    USING p_owner,p_request,'SERVING_TRUTH_EXPIRED';
  END IF;
  EXECUTE 'SELECT count(*) FROM ml.recommendation_attribution_association '
   'WHERE recommendation_request_id=$1' INTO remaining USING p_request;
  IF remaining>0 THEN
   RETURN jsonb_build_object('status','PENDING','remaining_associations',remaining); END IF;
 END IF;
 INSERT INTO app_private.native_served_authority_context VALUES(txid_current(),p_request,'PURGE');
 INSERT INTO app_private.native_served_authority_purge_receipt
  VALUES(p_owner,p_request,r.request_sha256,
  d.decision_sha256,d.served_items_sha256,'EXPIRED',clock_timestamp(),r.created_at+interval
  '400 days');
 -- RESTRICT constraints remain the final authority after bounded causal child cleanup.
 DELETE FROM ml.recommendation_served_item WHERE recommendation_request_id=p_request;
 DELETE FROM ml.recommendation_serving_decision WHERE recommendation_request_id=p_request;
 DELETE FROM ml.recommendation_item WHERE recommendation_request_id=p_request;
 DELETE FROM ml.recommendation_request_replay_payload WHERE recommendation_request_id=p_request;
 DELETE FROM ml.recommendation_request WHERE recommendation_request_id=p_request;
 PERFORM app_private.assert_native_served_authority(p_request);
 DELETE FROM app_private.native_served_authority_context WHERE transaction_id=txid_current()
  AND recommendation_request_id=p_request;
 RETURN jsonb_build_object('status','PURGED');
END; $$;

CREATE FUNCTION app_private.protect_native_served_expiry() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request_id uuid;
BEGIN
 request_id:=CASE WHEN TG_OP='DELETE' THEN OLD.recommendation_request_id ELSE
  NEW.recommendation_request_id END;
 IF TG_OP='UPDATE' OR NOT EXISTS(SELECT 1 FROM app_private.native_served_authority_context
  WHERE transaction_id=txid_current() AND recommendation_request_id=request_id AND
  operation='PURGE') THEN
  RAISE EXCEPTION 'native_served_expiry_immutable' USING ERRCODE='55000'; END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END; $$;

CREATE FUNCTION app_private.purge_native_served_expiry_receipts(p_owner uuid,p_request uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE removed integer:=0; affected integer;
BEGIN
 PERFORM app_private.lock_native_served_server();
 PERFORM 1 FROM account.user_account WHERE user_id=p_owner FOR UPDATE;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
  convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(p_owner)),
  'hex'),1,16))::bit(64)::bigint);
 PERFORM app_private.lock_recommendation_request_v2(p_request);
 IF EXISTS(SELECT 1 FROM ml.recommendation_request WHERE recommendation_request_id=p_request
   AND user_id=p_owner)
  OR EXISTS(SELECT 1 FROM app_private.native_served_authority_purge_receipt
   WHERE recommendation_request_id=p_request AND owner_user_id=p_owner
    AND purged_at>=transaction_timestamp()) THEN
  -- Keep the content-bound receipt through the graph's deferred checks and commit.
  RETURN jsonb_build_object('status','GRAPH_PURGE_REQUIRED','removed',0);
 END IF;
 INSERT INTO app_private.native_served_authority_context VALUES(txid_current(),p_request,'PURGE');
 DELETE FROM ml.recommendation_attribution_expiry_tombstone WHERE owner_user_id=p_owner
  AND recommendation_request_id=p_request AND tombstone_expires_at<=clock_timestamp();
 GET DIAGNOSTICS affected=ROW_COUNT; removed:=removed+affected;
 DELETE FROM app_private.native_served_authority_purge_receipt WHERE owner_user_id=p_owner
  AND recommendation_request_id=p_request AND expires_at<=clock_timestamp();
 GET DIAGNOSTICS affected=ROW_COUNT; removed:=removed+affected;
 DELETE FROM app_private.native_served_authority_context WHERE transaction_id=txid_current()
  AND recommendation_request_id=p_request;
 RETURN jsonb_build_object('status','EXPIRED_RECEIPTS_REMOVED','removed',removed);
END; $$;

CREATE FUNCTION app_private.purge_native_served_owner(target uuid)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request_id uuid; r ml.recommendation_request%ROWTYPE;
 d ml.recommendation_serving_decision%ROWTYPE; total bigint:=0; affected bigint;
 remaining bigint; next_remaining bigint; deletion_request uuid;
BEGIN
 SELECT c.request_id INTO deletion_request FROM app_private.privacy_purge_context c
  WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=target;
 IF deletion_request IS NULL OR NOT app_private.privacy_owner_allowed(target) THEN
  RAISE EXCEPTION 'native_served_owner_purge_not_authorized' USING ERRCODE='55000'; END IF;
 -- The authoritative account purge already holds server, owner and publication locks.
 -- A cursor visits every exact owner request; child mutation batches are capped at 256.
 FOR request_id IN
  SELECT recommendation_request_id FROM ml.recommendation_request
   WHERE user_id=target AND serving_contract_version=2
  UNION SELECT recommendation_request_id FROM ml.recommendation_attribution_expiry_tombstone
   WHERE owner_user_id=target
  UNION SELECT recommendation_request_id FROM app_private.native_served_authority_purge_receipt
   WHERE owner_user_id=target
  ORDER BY 1
 LOOP
  PERFORM app_private.lock_recommendation_request_v2(request_id);
  SELECT * INTO r FROM ml.recommendation_request
   WHERE recommendation_request_id=request_id AND user_id=target AND serving_contract_version=2;
  IF r.recommendation_request_id IS NOT NULL THEN
   PERFORM app_private.assert_native_served_authority(request_id);
   SELECT * INTO d FROM ml.recommendation_serving_decision
    WHERE recommendation_request_id=request_id;
   -- This existing protected manifest remains until account.purge_account validates
   -- all deferred constraints. It is removed with the authenticated privacy context.
   INSERT INTO app_private.privacy_purge_entity
    VALUES(pg_current_xact_id(),'ml.recommendation_request',request_id) ON CONFLICT DO NOTHING;
   IF to_regclass('ml.recommendation_attribution_association') IS NOT NULL THEN
    EXECUTE 'SELECT count(*) FROM ml.recommendation_attribution_association '
     'WHERE recommendation_request_id=$1' INTO remaining USING request_id;
    WHILE remaining>0 LOOP
     IF to_regprocedure(
      'app_private.purge_native_request_attributions_v2(uuid,uuid,text,integer)') IS NULL THEN
      RAISE EXCEPTION 'native_served_causal_purge_unavailable' USING ERRCODE='55000'; END IF;
     EXECUTE 'SELECT app_private.purge_native_request_attributions_v2($1,$2,$3,256)'
      USING target,request_id,'ACCOUNT_DELETION';
     EXECUTE 'SELECT count(*) FROM ml.recommendation_attribution_association '
      'WHERE recommendation_request_id=$1' INTO next_remaining USING request_id;
     IF next_remaining>=remaining THEN
      RAISE EXCEPTION 'native_served_causal_purge_no_progress' USING ERRCODE='55000'; END IF;
     total:=total+remaining-next_remaining; remaining:=next_remaining;
    END LOOP;
   END IF;
  END IF;
  INSERT INTO app_private.native_served_authority_context
   VALUES(txid_current(),request_id,'PURGE');
  IF r.recommendation_request_id IS NOT NULL THEN
   INSERT INTO app_private.native_served_authority_purge_receipt
    VALUES(target,request_id,r.request_sha256,d.decision_sha256,d.served_items_sha256,
     'ACCOUNT_DELETION',clock_timestamp(),r.created_at+interval '400 days');
   DELETE FROM ml.recommendation_served_item WHERE recommendation_request_id=request_id;
   GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
   DELETE FROM ml.recommendation_serving_decision WHERE recommendation_request_id=request_id;
   GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
   DELETE FROM ml.recommendation_item WHERE recommendation_request_id=request_id;
   GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
   DELETE FROM ml.recommendation_request_replay_payload WHERE recommendation_request_id=request_id;
   GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
   DELETE FROM ml.recommendation_request WHERE recommendation_request_id=request_id;
   GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
   PERFORM app_private.assert_native_served_authority(request_id);
  END IF;
  DELETE FROM ml.recommendation_attribution_expiry_tombstone
   WHERE owner_user_id=target AND recommendation_request_id=request_id;
  GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
  DELETE FROM app_private.native_served_authority_purge_receipt
   WHERE owner_user_id=target AND recommendation_request_id=request_id;
  GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
  DELETE FROM app_private.native_served_authority_context
   WHERE transaction_id=txid_current() AND recommendation_request_id=request_id;
 END LOOP;
 RETURN total;
END; $$;
"""

for _table in (
    "recommendation_request",
    "recommendation_request_replay_payload",
    "recommendation_item",
    "recommendation_serving_decision",
    "recommendation_served_item",
):
    NATIVE_SERVED_AUTHORITY_INSTALL_SQL += f"""
CREATE TRIGGER z_native_served_immutable BEFORE INSERT OR UPDATE OR DELETE ON ml.{_table}
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_native_served_authority();
CREATE CONSTRAINT TRIGGER z_native_served_complete AFTER INSERT OR UPDATE OR DELETE ON ml.{_table}
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
  app_private.check_native_served_graph();
"""

NATIVE_SERVED_AUTHORITY_INSTALL_SQL += """
CREATE TRIGGER z_native_served_expiry_immutable BEFORE INSERT OR UPDATE OR DELETE
 ON ml.recommendation_attribution_expiry_tombstone FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_native_served_expiry();
CREATE TRIGGER z_native_served_receipt_immutable BEFORE INSERT OR UPDATE OR DELETE
 ON app_private.native_served_authority_purge_receipt FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_native_served_expiry();
REVOKE ALL ON app_private.native_served_authority_context,
 app_private.native_served_authority_purge_receipt,
 ml.recommendation_request_replay_payload,ml.recommendation_serving_decision,
 ml.recommendation_served_item,ml.recommendation_attribution_expiry_tombstone FROM PUBLIC;
"""

_FUNCTIONS = (
    "native_served_lp(text,bytea[])",
    "native_served_json_lp(jsonb,jsonb,text,integer)",
    "native_served_schema()",
    "native_served_json_hash(jsonb,text)",
    "lock_recommendation_request_v2(uuid)",
    "native_served_baseline_digest(uuid,jsonb)",
    "native_served_item_digest(uuid,jsonb)",
    "native_served_set_digest(uuid,boolean)",
    "native_served_decision_digest(uuid)",
    "check_native_served_identity(uuid,uuid,uuid,uuid,bigint)",
    "lock_native_served_server()",
    "protect_native_served_authority()",
    "assert_native_served_authority(uuid)",
    "check_native_served_graph()",
    "ensure_native_served_expiry(uuid,uuid)",
    "expire_native_served_authority(uuid,uuid)",
    "read_native_served_authority(uuid,uuid)",
    "commit_native_served_authority(uuid,uuid,uuid,uuid,bigint,jsonb)",
    "purge_native_served_authority(uuid,uuid)",
    "protect_native_served_expiry()",
    "purge_native_served_expiry_receipts(uuid,uuid)",
    "purge_native_served_owner(uuid)",
)
for _function in _FUNCTIONS:
    NATIVE_SERVED_AUTHORITY_INSTALL_SQL += (
        f"REVOKE ALL ON FUNCTION app_private.{_function} FROM PUBLIC;\n"
    )

NATIVE_SERVED_AUTHORITY_INSTALL_SQL += r"""
DO $$
DECLARE previous text; anchor text:=
 '-- This frozen list is a deletion order, not recursive foreign-key cascade discovery.';
BEGIN
 SELECT pg_get_functiondef('account.purge_account(uuid,uuid)'::regprocedure) INTO previous;
 IF previous IS NULL OR strpos(previous,anchor)=0
  OR strpos(substr(previous,strpos(previous,anchor)+length(anchor)),anchor)>0 THEN
  RAISE EXCEPTION 'native_served_privacy_hook_shape' USING ERRCODE='55000'; END IF;
 INSERT INTO app_private.privacy_prior_definition(name,definition)
  VALUES('0092:account.purge_account(uuid,uuid)',previous);
 EXECUTE replace(previous,anchor,
  'total:=total+app_private.purge_native_served_owner(target);'||chr(10)||' '||anchor);
END $$;
"""

NATIVE_SERVED_AUTHORITY_DROP_SQL = r"""
LOCK TABLE ml.recommendation_request,ml.recommendation_item,
 ml.recommendation_request_replay_payload,ml.recommendation_serving_decision,
 ml.recommendation_served_item,ml.recommendation_attribution_expiry_tombstone,
 app_private.native_served_authority_context,app_private.native_served_authority_purge_receipt
 IN ACCESS EXCLUSIVE MODE;
DO $$ DECLARE previous text;
BEGIN
 IF EXISTS(SELECT 1 FROM ml.recommendation_request WHERE serving_contract_version=2)
  OR EXISTS(SELECT 1 FROM ml.recommendation_request_replay_payload)
  OR EXISTS(SELECT 1 FROM ml.recommendation_serving_decision)
  OR EXISTS(SELECT 1 FROM ml.recommendation_served_item)
  OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_expiry_tombstone)
  OR EXISTS(SELECT 1 FROM app_private.native_served_authority_context)
  OR EXISTS(SELECT 1 FROM app_private.native_served_authority_purge_receipt)
  OR EXISTS(SELECT 1 FROM ml.recommendation_item WHERE score_units_e8 IS NOT NULL
   OR baseline_item_sha256 IS NOT NULL OR candidate_sources_sha256 IS NOT NULL
   OR availability_snapshot_sha256 IS NOT NULL OR contributions_sha256 IS NOT NULL
   OR reason_codes_sha256 IS NOT NULL OR item_provenance_sha256 IS NOT NULL) THEN
  RAISE EXCEPTION 'native_served_downgrade_evidence_retained' USING ERRCODE='55000'; END IF;
 SELECT definition INTO previous FROM app_private.privacy_prior_definition
  WHERE name='0092:account.purge_account(uuid,uuid)';
 IF previous IS NULL THEN
  RAISE EXCEPTION 'native_served_privacy_hook_missing' USING ERRCODE='55000'; END IF;
 EXECUTE previous;
 DELETE FROM app_private.privacy_prior_definition
  WHERE name='0092:account.purge_account(uuid,uuid)';
END $$;
"""
for _table in (
    "recommendation_request",
    "recommendation_request_replay_payload",
    "recommendation_item",
    "recommendation_serving_decision",
    "recommendation_served_item",
):
    NATIVE_SERVED_AUTHORITY_DROP_SQL += (
        f"DROP TRIGGER z_native_served_complete ON ml.{_table};\n"
        f"DROP TRIGGER z_native_served_immutable ON ml.{_table};\n"
    )
NATIVE_SERVED_AUTHORITY_DROP_SQL += """
DROP TABLE app_private.native_served_authority_purge_receipt;
DROP TABLE ml.recommendation_attribution_expiry_tombstone;
DROP TABLE ml.recommendation_served_item;
DROP TABLE ml.recommendation_serving_decision;
DROP TABLE ml.recommendation_request_replay_payload;
DROP TABLE app_private.native_served_authority_context;
"""
for _function in reversed(_FUNCTIONS):
    NATIVE_SERVED_AUTHORITY_DROP_SQL += f"DROP FUNCTION app_private.{_function};\n"
NATIVE_SERVED_AUTHORITY_DROP_SQL += """
ALTER TABLE ml.recommendation_item DROP CONSTRAINT ck_native_served_baseline_score,
 DROP COLUMN baseline_item_sha256,DROP COLUMN item_provenance_sha256,
 DROP COLUMN reason_codes_sha256,DROP COLUMN contributions_sha256,
 DROP COLUMN availability_snapshot_sha256,DROP COLUMN candidate_sources_sha256,
 DROP COLUMN score_units_e8;
ALTER TABLE ml.recommendation_request DROP CONSTRAINT ck_native_served_request_surface,
 DROP CONSTRAINT ck_native_served_request_version,DROP COLUMN serving_contract_version;
"""
