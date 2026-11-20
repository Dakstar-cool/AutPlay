# ruff: noqa: E501
"""Dormant Source3 pack successors; not an installer or an admission grant.

Only J-owned new definitions. Old0101 wire/hash/guards remain unchanged.
No SQL executes on import. B's typed classifiers and C's lifecycle must be
installed coherently before binding these functions. Real F/G admission must
prepay complete repeated graph work, concatenation work and returned outputs;
the existing SQL compiler stack accounts only temporary allocation. Surface
reachability, full caller ownership and real PG tests remain root-held gates.
"""

PACK_V3_FUNCTIONS = (
    "native_pack_payload_v3(ml.offline_pack_binding_receipt)",
    "assert_native_offline_pack_v3(uuid)",
    "create_native_offline_pack_v3(uuid,uuid,uuid,uuid,bigint,uuid,uuid)",
    "read_native_offline_pack_v3(uuid,uuid,uuid,uuid,bigint,uuid,boolean)",
)

PACK_V3_DEFINITIONS_SQL = r"""
CREATE FUNCTION app_private.native_pack_payload_v3(b ml.offline_pack_binding_receipt)
 RETURNS bytea LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE header text; prefix text; suffix text; body bytea; part bytea; leaf jsonb;
 n integer; idx integer; frame integer; row_frame integer; meta record;
 marker constant text := '"items": []'; marker_at integer;
BEGIN
 IF current_setting('transaction_isolation')<>'repeatable read' THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_TRANSACTION' USING ERRCODE='23514'; END IF;
 IF b.pack_id IS NULL OR b.owner_user_id IS NULL OR b.device_id IS NULL
  OR b.recommendation_request_id IS NULL OR b.serving_contract_version IS DISTINCT FROM 2
  OR b.served_item_count IS NULL OR b.served_item_count NOT BETWEEN 0 AND 100
  OR NOT EXISTS(SELECT 1 FROM ml.recommendation_request_root_v3 r
   JOIN ml.native_served_request_hash_format f ON f.served_request_id=r.served_request_id
   WHERE r.served_request_id=b.recommendation_request_id AND r.owner_user_id=b.owner_user_id
    AND r.device_id=b.device_id AND r.request_hash_format_version=3
    AND r.original_capture_format_version=3 AND f.request_hash_format_version=3
    AND f.owner_user_id=r.owner_user_id AND f.created_transaction=r.created_transaction)
  OR NOT EXISTS(SELECT 1 FROM ml.recommendation_request q
   JOIN ml.recommendation_serving_decision d USING(recommendation_request_id)
   WHERE q.recommendation_request_id=b.recommendation_request_id AND q.user_id=b.owner_user_id
    AND q.surface='offline_pack' AND q.serving_contract_version=2 AND d.state='COMMITTED_P11'
    AND d.serving_provenance='P11' AND d.decision_sha256=b.decision_sha256
    AND d.served_item_count=b.served_item_count AND d.served_items_sha256=b.served_items_sha256) THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_PAYLOAD_BINDING' USING ERRCODE='23514'; END IF;
 SELECT count(*) INTO n FROM (SELECT 1 FROM ml.recommendation_served_item
  WHERE recommendation_request_id=b.recommendation_request_id LIMIT 101) bounded;
 IF n IS DISTINCT FROM b.served_item_count THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_ITEM_COUNT' USING ERRCODE='23514'; END IF;
 -- Typed metadata is not semantic truth. Validate the actual retained graph
 -- before any JSON serialization, including direct private payload calls.
 PERFORM app_private.assert_native_served_truth_v3(b.recommendation_request_id);
 -- The caller's actual owner prepays the returned <=2MiB bytea. This frame
 -- covers temporary concatenation and header; it is not an output grant.
 frame:=app_private.native_v3_compiler_enter(2162688);
 header:=jsonb_build_object('payload_version',2,'serving_contract_version',2,
  'offline_pack_id',b.pack_id,'user_id',b.owner_user_id,'device_id',b.device_id,
  'recommendation_request_id',b.recommendation_request_id,
  'decision_sha256',encode(b.decision_sha256,'hex'),'served_item_count',b.served_item_count,
  'served_items_sha256',encode(b.served_items_sha256,'hex'),
  'created_at_ms',floor(extract(epoch FROM b.created_at)*1000)::bigint,
  'expires_at_ms',floor(extract(epoch FROM b.content_expires_at)*1000)::bigint,
  'items','[]'::jsonb)::text;
 marker_at:=strpos(header,marker);
 IF marker_at=0 OR strpos(substr(header,marker_at+length(marker)),marker)<>0 THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_HEADER' USING ERRCODE='23514'; END IF;
 prefix:=substr(header,1,marker_at-1)||'"items": [';
 suffix:=']'||substr(header,marker_at+length(marker));
 body:=convert_to(prefix,'UTF8');
 FOR idx IN 1..n LOOP
  -- Metadata only before JSON hydration; physical compression is never a cap.
  SELECT pg_column_compression(s.reasons) AS reason_compression,
   pg_column_compression(s.contributions) AS contribution_compression,
   pg_column_size(s.reasons) AS reason_bytes,pg_column_size(s.contributions) AS contribution_bytes,
   octet_length(s.score_kind) AS kind_bytes,octet_length(s.attribution_source) AS source_bytes,
   octet_length(s.origin) AS origin_bytes INTO meta
   FROM ml.recommendation_served_item s
   WHERE s.recommendation_request_id=b.recommendation_request_id AND s.source_rank=idx;
  IF NOT FOUND OR meta.reason_compression IS NOT NULL OR meta.contribution_compression IS NOT NULL
   OR meta.reason_bytes NOT BETWEEN 1 AND 65536 OR meta.contribution_bytes NOT BETWEEN 1 AND 65536
   OR meta.kind_bytes NOT BETWEEN 1 AND 32 OR meta.source_bytes NOT BETWEEN 1 AND 32
   OR meta.origin_bytes NOT BETWEEN 1 AND 32 THEN
   RAISE EXCEPTION 'NATIVE_V3_PACK_ROW_BOUND' USING ERRCODE='23514'; END IF;
  row_frame:=app_private.native_v3_compiler_enter(
   14*(meta.reason_bytes::bigint+meta.contribution_bytes::bigint)+65536);
  -- Actual SERVED bytes and values are retained verbatim. Do not rebuild from
  -- root.body.p11_items or normalize contributions: that changes old wire/hash.
  SELECT jsonb_build_object('source_rank',source_rank,'recording_id',recording_id,
   'score',score_units_e8::numeric/100000000,'score_units_e8',score_units_e8,
   'score_kind',score_kind,'attribution_source',attribution_source,'reason_codes',reasons,
   'contributions',contributions,'original_p11_rank',original_p11_rank,
   'origin',origin,'served_item_sha256',encode(served_item_sha256,'hex')) INTO leaf
   FROM ml.recommendation_served_item WHERE recommendation_request_id=b.recommendation_request_id
    AND source_rank=idx;
  part:=convert_to(leaf::text,'UTF8');
  IF octet_length(body)+(CASE WHEN idx=1 THEN 0 ELSE 2 END)+octet_length(part)
   +octet_length(suffix)>2097152 THEN
   RAISE EXCEPTION 'NATIVE_PACK_PAYLOAD_BOUND' USING ERRCODE='22023'; END IF;
  IF idx>1 THEN body:=body||convert_to(', ','UTF8'); END IF;
  body:=body||part;
  leaf:=NULL;part:=NULL;meta:=NULL;
  PERFORM app_private.native_v3_compiler_exit(row_frame);
 END LOOP;
 body:=body||convert_to(suffix,'UTF8');
 header:=NULL;prefix:=NULL;suffix:=NULL;leaf:=NULL;part:=NULL;meta:=NULL;
 PERFORM app_private.native_v3_compiler_exit(frame);
 RETURN body;
END $$;

CREATE FUNCTION app_private.assert_native_offline_pack_v3(pid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ml.offline_pack_binding_receipt%ROWTYPE;p ml.offline_recommendation_pack%ROWTYPE;
 a ml.recommendation_attribution_association%ROWTYPE;q record;
 d ml.recommendation_serving_decision%ROWTYPE;r record; frame integer; payload_bytes integer;
BEGIN
 IF current_setting('transaction_isolation')<>'repeatable read' THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_TRANSACTION' USING ERRCODE='23514'; END IF;
 IF pid IS NULL THEN RETURN; END IF;
 SELECT * INTO b FROM ml.offline_pack_binding_receipt WHERE pack_id=pid;
 SELECT offline_pack_id,attribution_contract_version INTO p.offline_pack_id,p.attribution_contract_version
 FROM ml.offline_recommendation_pack WHERE offline_pack_id=pid;
 SELECT association_id INTO a.association_id FROM ml.recommendation_attribution_association WHERE offline_pack_id=pid;
 IF b.pack_id IS NULL THEN
  IF a.association_id IS NOT NULL OR p.attribution_contract_version=2 THEN
   RAISE EXCEPTION 'NATIVE_PACK_INCOMPLETE' USING ERRCODE='23514'; END IF;
  IF app_private.native_v3_resolve_pack_format(NULL,pid) IS DISTINCT FROM 3 THEN
   RAISE EXCEPTION 'NATIVE_V3_PACK_FORMAT' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 PERFORM app_private.lock_recommendation_request_v2(b.recommendation_request_id);
 PERFORM app_private.assert_native_served_truth_v3(b.recommendation_request_id);
 SELECT recommendation_request_id,user_id,serving_contract_version,surface,
 pipeline_key,pipeline_version,input_snapshot_sha256,catalog_snapshot,model_bundle_version,created_at
 INTO q FROM ml.recommendation_request WHERE recommendation_request_id=b.recommendation_request_id;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=b.recommendation_request_id;
 SELECT owner_user_id,device_id,journal_epoch,server_instance_id,identity_epoch,
 served_request_id,request_hash_format_version,original_capture_format_version,created_transaction
 INTO r FROM ml.recommendation_request_root_v3 WHERE served_request_id=b.recommendation_request_id;
 IF r.served_request_id IS NULL OR r.request_hash_format_version IS DISTINCT FROM 3
  OR r.original_capture_format_version IS DISTINCT FROM 3
  OR r.owner_user_id IS DISTINCT FROM q.user_id THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_ROOT' USING ERRCODE='23514'; END IF;

 IF q.serving_contract_version IS DISTINCT FROM 2 OR q.surface IS DISTINCT FROM 'offline_pack'
  OR q.user_id IS DISTINCT FROM b.owner_user_id OR r.device_id IS DISTINCT FROM b.device_id
  OR d.state IS DISTINCT FROM 'COMMITTED_P11' OR d.serving_provenance IS DISTINCT FROM 'P11'
  OR b.decision_sha256 IS DISTINCT FROM d.decision_sha256
  OR b.served_item_count IS DISTINCT FROM d.served_item_count
  OR b.served_items_sha256 IS DISTINCT FROM d.served_items_sha256
  OR b.attribution_expires_at IS DISTINCT FROM q.created_at+interval '180 days'
  OR b.created_at<q.created_at OR b.binding_sha256 IS DISTINCT FROM app_private.native_pack_binding_hash(b) THEN
  RAISE EXCEPTION 'NATIVE_PACK_BINDING' USING ERRCODE='23514'; END IF;
 IF p.offline_pack_id IS NULL THEN
  IF a.association_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt x
   WHERE x.pack_id=pid AND x.stage='CONTENT' AND x.owner_user_id=b.owner_user_id
    AND x.binding_sha256=b.binding_sha256 AND x.payload_sha256=b.pack_payload_sha256) THEN
   RAISE EXCEPTION 'NATIVE_PACK_INCOMPLETE' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 -- Only scalar metadata may be read before the payload output slot is retained.
 SELECT octet_length(payload) INTO payload_bytes FROM ml.offline_recommendation_pack WHERE offline_pack_id=pid;
 IF payload_bytes IS NULL OR payload_bytes NOT BETWEEN 1 AND 2097152
  OR NOT EXISTS(SELECT 1 FROM ml.offline_recommendation_pack x WHERE x.offline_pack_id=pid
   AND octet_length(x.pipeline_key)<=4096 AND octet_length(x.pipeline_version)<=4096
   AND octet_length(x.model_bundle_version)<=4096)
  OR NOT EXISTS(SELECT 1 FROM ml.recommendation_attribution_association x WHERE x.offline_pack_id=pid
   AND x.kind='OFFLINE_PACK' AND octet_length(x.serving_provenance)<=32
   AND octet_length(x.request_surface)<=32) THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_STORED_BOUND' USING ERRCODE='23514'; END IF;
 frame:=app_private.native_v3_compiler_enter(payload_bytes+2097152+65536);
 SELECT offline_pack_id,user_id,device_id,recommendation_request_id,pipeline_key,pipeline_version,
  input_snapshot_sha256,catalog_snapshot,model_bundle_version,payload_version,payload_encoding,payload,
  payload_sha256,created_at,expires_at,attribution_contract_version
 INTO p.offline_pack_id,p.user_id,p.device_id,p.recommendation_request_id,p.pipeline_key,p.pipeline_version,
  p.input_snapshot_sha256,p.catalog_snapshot,p.model_bundle_version,p.payload_version,p.payload_encoding,p.payload,
  p.payload_sha256,p.created_at,p.expires_at,p.attribution_contract_version
 FROM ml.offline_recommendation_pack WHERE offline_pack_id=pid;
 SELECT * INTO a FROM ml.recommendation_attribution_association WHERE offline_pack_id=pid;
 IF p.payload_version IS DISTINCT FROM 2 OR p.payload_encoding IS DISTINCT FROM 'RAW_JSON' THEN
  RAISE EXCEPTION 'NATIVE_PACK_INCOMPLETE' USING ERRCODE='23514'; END IF;
 IF p.attribution_contract_version IS DISTINCT FROM 2 OR p.user_id IS DISTINCT FROM b.owner_user_id
  OR p.device_id IS DISTINCT FROM b.device_id OR p.recommendation_request_id IS NOT NULL
  OR p.pipeline_key IS DISTINCT FROM q.pipeline_key OR p.pipeline_version IS DISTINCT FROM q.pipeline_version
  OR p.input_snapshot_sha256 IS DISTINCT FROM q.input_snapshot_sha256
  OR p.catalog_snapshot IS DISTINCT FROM q.catalog_snapshot OR p.model_bundle_version IS DISTINCT FROM q.model_bundle_version
  OR p.created_at IS DISTINCT FROM b.created_at OR p.expires_at IS DISTINCT FROM b.content_expires_at
  OR p.payload_sha256 IS DISTINCT FROM b.pack_payload_sha256 OR p.payload_sha256 IS DISTINCT FROM sha256(p.payload)
  OR p.payload IS DISTINCT FROM app_private.native_pack_payload_v3(b)
  OR a.kind IS DISTINCT FROM 'OFFLINE_PACK' OR a.owner_user_id IS DISTINCT FROM b.owner_user_id
  OR a.recommendation_request_id IS DISTINCT FROM b.recommendation_request_id
  OR a.serving_provenance IS DISTINCT FROM d.serving_provenance OR a.request_surface IS DISTINCT FROM q.surface
  OR a.attribution_expires_at IS DISTINCT FROM b.attribution_expires_at
  OR a.decision_sha256 IS DISTINCT FROM b.decision_sha256 OR a.pack_served_item_count IS DISTINCT FROM b.served_item_count
  OR a.pack_served_items_sha256 IS DISTINCT FROM b.served_items_sha256
  OR a.pack_payload_sha256 IS DISTINCT FROM b.pack_payload_sha256
  OR a.original_request_sha256 IS DISTINCT FROM b.binding_sha256 OR a.acceptance_sha256 IS DISTINCT FROM b.binding_sha256
  OR a.association_sha256 IS DISTINCT FROM app_private.native_pack_association_hash(a) THEN
  RAISE EXCEPTION 'NATIVE_PACK_INCOMPLETE' USING ERRCODE='23514'; END IF;
 p:=NULL;a:=NULL;q:=NULL;r:=NULL;
 PERFORM app_private.native_v3_compiler_exit(frame);
END $$;

CREATE FUNCTION app_private.create_native_offline_pack_v3(
 p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,p_identity bigint,p_request uuid,p_pack uuid)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ml.offline_pack_binding_receipt%ROWTYPE;a ml.recommendation_attribution_association%ROWTYPE;
 q record;d ml.recommendation_serving_decision%ROWTYPE;
 r record; frame integer;body bytea;actual_time timestamptz;
BEGIN
 IF current_setting('transaction_isolation')<>'repeatable read' THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_TRANSACTION' USING ERRCODE='23514'; END IF;
 IF p_request IS NULL OR p_pack IS NULL THEN RAISE EXCEPTION 'NATIVE_PACK_COMMAND' USING ERRCODE='22023';END IF;
 PERFORM app_private.check_native_served_identity(p_owner,p_device,p_epoch,p_server,p_identity);
 PERFORM app_private.lock_recommendation_request_v2(p_request);
 SELECT * INTO b FROM ml.offline_pack_binding_receipt WHERE pack_id=p_pack;
 IF b.pack_id IS NOT NULL THEN
  IF b.owner_user_id IS DISTINCT FROM p_owner OR b.device_id IS DISTINCT FROM p_device
   OR b.recommendation_request_id IS DISTINCT FROM p_request THEN
   RAISE EXCEPTION 'NATIVE_PACK_RETRY_CONFLICT' USING ERRCODE='23514';END IF;
  PERFORM app_private.assert_native_offline_pack_v3(p_pack);
  IF clock_timestamp()>=b.attribution_expires_at THEN RETURN jsonb_build_object('status','ATTRIBUTION_EXPIRED');END IF;
  RETURN jsonb_build_object('status',CASE WHEN clock_timestamp()>=b.content_expires_at THEN 'PACK_EXPIRED' ELSE 'COMMITTED' END,
   'pack_id',p_pack,'binding_sha256',encode(b.binding_sha256,'hex'),'replayed',true);
 END IF;
 IF EXISTS(SELECT 1 FROM ml.offline_recommendation_pack WHERE offline_pack_id=p_pack)
  OR EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt WHERE pack_id=p_pack) THEN
  RAISE EXCEPTION 'NATIVE_PACK_ID_REUSED' USING ERRCODE='23514';END IF;
 PERFORM app_private.assert_native_served_truth_v3(p_request);
 SELECT recommendation_request_id,user_id,serving_contract_version,surface,
 pipeline_key,pipeline_version,input_snapshot_sha256,catalog_snapshot,model_bundle_version,created_at
 INTO q FROM ml.recommendation_request WHERE recommendation_request_id=p_request AND user_id=p_owner;
 SELECT * INTO d FROM ml.recommendation_serving_decision WHERE recommendation_request_id=p_request;
 SELECT owner_user_id,device_id,journal_epoch,server_instance_id,identity_epoch,
 served_request_id,request_hash_format_version,original_capture_format_version,created_transaction
 INTO r FROM ml.recommendation_request_root_v3 WHERE served_request_id=p_request;
 IF r.served_request_id IS NULL OR r.request_hash_format_version IS DISTINCT FROM 3
  OR r.original_capture_format_version IS DISTINCT FROM 3
  OR r.owner_user_id IS DISTINCT FROM q.user_id THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_ROOT' USING ERRCODE='23514'; END IF;

 IF q.serving_contract_version IS DISTINCT FROM 2 OR q.surface IS DISTINCT FROM 'offline_pack'
  OR d.state IS DISTINCT FROM 'COMMITTED_P11' OR r.device_id IS DISTINCT FROM p_device
  OR r.journal_epoch IS DISTINCT FROM p_epoch OR r.server_instance_id IS DISTINCT FROM p_server
  OR r.identity_epoch IS DISTINCT FROM p_identity THEN
  RAISE EXCEPTION 'NATIVE_PACK_REQUEST_BINDING' USING ERRCODE='23514';END IF;
 IF r.created_transaction IS NULL OR r.created_transaction=pg_current_xact_id() THEN
  RAISE EXCEPTION 'NATIVE_PACK_COMMITTED_DECISION_REQUIRED' USING ERRCODE='23514';END IF;
 actual_time:=clock_timestamp();
 b.pack_id:=p_pack;b.owner_user_id:=p_owner;b.device_id:=p_device;b.recommendation_request_id:=p_request;
 b.serving_contract_version:=2;b.decision_sha256:=d.decision_sha256;b.served_item_count:=d.served_item_count;
 b.served_items_sha256:=d.served_items_sha256;b.created_at:=actual_time;
 b.attribution_expires_at:=q.created_at+interval '180 days';
 b.content_expires_at:=least(actual_time+interval '7 days',b.attribution_expires_at);
 frame:=app_private.native_v3_compiler_enter(2162688);
 body:=app_private.native_pack_payload_v3(b);b.pack_payload_sha256:=sha256(body);
 b.binding_sha256:=app_private.native_pack_binding_hash(b);
 IF octet_length(body)>2097152 THEN RAISE EXCEPTION 'NATIVE_PACK_PAYLOAD_BOUND' USING ERRCODE='22023';END IF;
 -- PostgreSQL time under the shared request lock is the creation linearization point.
 IF clock_timestamp()>=b.attribution_expires_at THEN
  body:=NULL;
  PERFORM app_private.native_v3_compiler_exit(frame);
  PERFORM app_private.ensure_native_served_expiry_v3(p_owner,p_request);
  RETURN jsonb_build_object('status','ATTRIBUTION_EXPIRED');END IF;
 INSERT INTO app_private.native_pack_context VALUES(pg_current_xact_id(),p_pack,p_owner,'CREATE',b.binding_sha256);
 INSERT INTO ml.offline_pack_binding_receipt SELECT (b).*;
 INSERT INTO ml.offline_recommendation_pack(offline_pack_id,user_id,device_id,pipeline_key,pipeline_version,
  input_snapshot_sha256,catalog_snapshot,model_bundle_version,payload_version,payload_encoding,payload,
  payload_sha256,created_at,expires_at,attribution_contract_version)
 VALUES(p_pack,p_owner,p_device,q.pipeline_key,q.pipeline_version,q.input_snapshot_sha256,q.catalog_snapshot,
  q.model_bundle_version,2,'RAW_JSON',body,b.pack_payload_sha256,b.created_at,b.content_expires_at,2);
 a.association_id:=uuidv7();a.owner_user_id:=p_owner;a.recommendation_request_id:=p_request;a.kind:='OFFLINE_PACK';
 a.serving_provenance:='P11';a.request_surface:=q.surface;a.wire_attribution_version:=1;
 a.offline_pack_id:=p_pack;a.attribution_expires_at:=b.attribution_expires_at;
 a.original_request_sha256:=b.binding_sha256;a.acceptance_sha256:=b.binding_sha256;
 a.decision_sha256:=b.decision_sha256;a.pack_served_item_count:=b.served_item_count;
 a.pack_served_items_sha256:=b.served_items_sha256;a.pack_payload_sha256:=b.pack_payload_sha256;
 a.association_sha256:=app_private.native_pack_association_hash(a);
 INSERT INTO ml.recommendation_attribution_association SELECT (a).*;
 DELETE FROM app_private.native_pack_context WHERE transaction_id=pg_current_xact_id() AND pack_id=p_pack;
 body:=NULL;a:=NULL;q:=NULL;r:=NULL;
 PERFORM app_private.native_v3_compiler_exit(frame);
 PERFORM app_private.assert_native_offline_pack_v3(p_pack);
 PERFORM app_private.classify_native_pack_format_v3(p_owner,p_pack);
 IF clock_timestamp()>=b.attribution_expires_at THEN
  PERFORM app_private.ensure_native_served_expiry_v3(p_owner,p_request);
  PERFORM app_private.purge_native_offline_pack_v2(p_owner,p_pack,'SERVING_TRUTH_EXPIRED');
  RETURN jsonb_build_object('status','ATTRIBUTION_EXPIRED'); END IF;
 IF clock_timestamp()>=b.content_expires_at THEN
  PERFORM app_private.purge_native_offline_pack_v2(p_owner,p_pack,'PACK_EXPIRED');
  RETURN jsonb_build_object('status','PACK_EXPIRED','pack_id',p_pack,
   'binding_sha256',encode(b.binding_sha256,'hex'),'replayed',false); END IF;
 RETURN jsonb_build_object('status','COMMITTED','pack_id',p_pack,
  'binding_sha256',encode(b.binding_sha256,'hex'),'replayed',false);
END $$;

CREATE FUNCTION app_private.read_native_offline_pack_v3(
 p_owner uuid,p_device uuid,p_epoch uuid,p_server uuid,p_identity bigint,p_pack uuid,p_payload boolean)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ml.offline_pack_binding_receipt%ROWTYPE;body bytea; payload_bytes integer; frame integer; response jsonb;
BEGIN
 IF current_setting('transaction_isolation')<>'repeatable read' THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_TRANSACTION' USING ERRCODE='23514'; END IF;
 IF p_payload IS NULL THEN RAISE EXCEPTION 'NATIVE_PACK_COMMAND' USING ERRCODE='22023';END IF;
 PERFORM app_private.check_native_served_identity(p_owner,p_device,p_epoch,p_server,p_identity);
 IF app_private.native_v3_resolve_pack_format(p_owner,p_pack) IS DISTINCT FROM 3 THEN
  RAISE EXCEPTION 'NATIVE_V3_PACK_FORMAT' USING ERRCODE='23514'; END IF;
 SELECT * INTO b FROM ml.offline_pack_binding_receipt WHERE pack_id=p_pack AND owner_user_id=p_owner AND device_id=p_device;
 IF b.pack_id IS NULL THEN
  RETURN jsonb_build_object('status',CASE WHEN EXISTS(SELECT 1 FROM ml.native_offline_pack_purge_receipt
   WHERE pack_id=p_pack AND owner_user_id=p_owner AND stage='BINDING') THEN 'ATTRIBUTION_EXPIRED' ELSE 'ATTRIBUTION_NOT_FOUND' END);
 END IF;
 PERFORM app_private.lock_recommendation_request_v2(b.recommendation_request_id);
 SELECT * INTO b FROM ml.offline_pack_binding_receipt WHERE pack_id=p_pack AND owner_user_id=p_owner AND device_id=p_device;
 IF b.pack_id IS NULL THEN RETURN jsonb_build_object('status','ATTRIBUTION_EXPIRED');END IF;
 PERFORM app_private.assert_native_offline_pack_v3(p_pack);
 -- Receipt-only reads never hydrate the payload. Preserve final clock after
 -- construction as well as after assertion: no stale COMMITTED publication.
 IF p_payload THEN
  SELECT octet_length(payload) INTO payload_bytes FROM ml.offline_recommendation_pack WHERE offline_pack_id=p_pack;
  IF payload_bytes IS NOT NULL AND payload_bytes NOT BETWEEN 1 AND 2097152 THEN
   RAISE EXCEPTION 'NATIVE_PACK_PAYLOAD_BOUND' USING ERRCODE='23514'; END IF;
 END IF;
 frame:=app_private.native_v3_compiler_enter(CASE WHEN p_payload
  THEN 5*coalesce(payload_bytes,0)+65536 ELSE 65536 END);
 IF p_payload THEN SELECT payload INTO body FROM ml.offline_recommendation_pack WHERE offline_pack_id=p_pack; END IF;
 -- The final locked SQL-clock observation linearizes both payload and receipt reads.
 IF clock_timestamp()>=b.attribution_expires_at THEN
  body:=NULL;PERFORM app_private.native_v3_compiler_exit(frame);
  PERFORM app_private.ensure_native_served_expiry_v3(p_owner,b.recommendation_request_id);
  PERFORM app_private.purge_native_offline_pack_v2(p_owner,p_pack,'SERVING_TRUTH_EXPIRED');
  RETURN jsonb_build_object('status','ATTRIBUTION_EXPIRED');END IF;
 IF p_payload AND clock_timestamp()>=b.content_expires_at THEN
  body:=NULL;PERFORM app_private.native_v3_compiler_exit(frame);
  PERFORM app_private.purge_native_offline_pack_v2(p_owner,p_pack,'PACK_EXPIRED');
  RETURN jsonb_build_object('status','PACK_EXPIRED');END IF;
 response:=jsonb_build_object('status','COMMITTED','pack_id',p_pack,'request_id',b.recommendation_request_id,
  'serving_contract_version',2,'decision_sha256',encode(b.decision_sha256,'hex'),
  'served_item_count',b.served_item_count,'served_items_sha256',encode(b.served_items_sha256,'hex'),
  'pack_payload_sha256',encode(b.pack_payload_sha256,'hex'),'binding_sha256',encode(b.binding_sha256,'hex'),
  'content_expires_at',b.content_expires_at,'attribution_expires_at',b.attribution_expires_at)
  ||CASE WHEN p_payload THEN jsonb_build_object('payload_b64',encode(body,'base64')) ELSE '{}'::jsonb END;
 body:=NULL;
 -- Response output ownership belongs to the admitted private caller. A final
 -- SQL-clock fence includes base64/JSON construction work, not just row reads.
 IF clock_timestamp()>=b.attribution_expires_at THEN
  response:=NULL;PERFORM app_private.native_v3_compiler_exit(frame);
  PERFORM app_private.ensure_native_served_expiry_v3(p_owner,b.recommendation_request_id);
  PERFORM app_private.purge_native_offline_pack_v2(p_owner,p_pack,'SERVING_TRUTH_EXPIRED');
  RETURN jsonb_build_object('status','ATTRIBUTION_EXPIRED');END IF;
 IF p_payload AND clock_timestamp()>=b.content_expires_at THEN
  response:=NULL;PERFORM app_private.native_v3_compiler_exit(frame);
  PERFORM app_private.purge_native_offline_pack_v2(p_owner,p_pack,'PACK_EXPIRED');
  RETURN jsonb_build_object('status','PACK_EXPIRED');END IF;
 PERFORM app_private.native_v3_compiler_exit(frame);
 RETURN response;
END $$;

REVOKE ALL ON FUNCTION app_private.native_pack_payload_v3(ml.offline_pack_binding_receipt) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.assert_native_offline_pack_v3(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.create_native_offline_pack_v3(uuid,uuid,uuid,uuid,bigint,uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.read_native_offline_pack_v3(uuid,uuid,uuid,uuid,bigint,uuid,boolean) FROM PUBLIC;
"""
