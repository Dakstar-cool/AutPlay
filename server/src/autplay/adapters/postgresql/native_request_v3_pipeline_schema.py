"""Private current-baseline pipeline guard; importing this module installs nothing.

This is a per-use check under a transaction-held row lock, not an immutability
claim for the old registry. The SQL and work factors await PostgreSQL 18.4
qualification. Callers must prepay the exported work on their existing G meter
before executing the helper; this module creates no allowance or SQL grant.
"""

from hashlib import sha256
from typing import Final

import rfc8785

from autplay.application.recommendations import baseline_pipeline_definition, pipeline_manifest_document

NATIVE_REQUEST_V3_BASELINE_PIPELINE_KEY: Final = "cpu-baseline"
NATIVE_REQUEST_V3_BASELINE_PIPELINE_VERSION: Final = "1"
NATIVE_REQUEST_V3_BASELINE_MANIFEST_SHA256: Final = (
    "50857a693fccfa10e7822d8bdf3b16c1cd3da99c3d8f0a52b7ebabacfe67c3cb"
)
# Frozen from the unchanged P11 baseline manifest. Never caller-selected.
NATIVE_REQUEST_V3_BASELINE_MANIFEST_BYTES: Final = (
    b'{"canonicalization_version":1,"components":[{"config_sha256":"44136fa355'
    b'b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a","key":"preferenc'
    b'es","kind":"candidate_generator","version":"1"},{"config_sha256":"44136f'
    b'a355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a","key":"histo'
    b'ry","kind":"candidate_generator","version":"1"},{"config_sha256":"44136f'
    b'a355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a","key":"artis'
    b't_release_metadata","kind":"candidate_generator","version":"1"},{"config'
    b'_sha256":"44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff'
    b'8a","key":"freshness","kind":"candidate_generator","version":"1"},{"conf'
    b'ig_sha256":"44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caa'
    b'ff8a","key":"exploration","kind":"candidate_generator","version":"1"},{"'
    b'config_sha256":"44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f6'
    b'1caaff8a","key":"mandatory-filters","kind":"filter","version":"1"},{"con'
    b'fig_sha256":"44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61ca'
    b'aff8a","key":"deterministic-heuristic-ranker","kind":"ranker","version":'
    b'"1"},{"config_sha256":"44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e831'
    b'0c060f61caaff8a","key":"deterministic-diversity-reranker","kind":"rerank'
    b'er","version":"1"},{"config_sha256":"44136fa355b3678a1146ad16f7e8649e94f'
    b'b4fc21fe77e8310c060f61caaff8a","key":"baseline-user-representation","kin'
    b'd":"representation","version":"1"}],"generator_budgets":[["preferences",'
    b'1000],["history",1000],["artist_release_metadata",1000],["freshness",100'
    b'0],["exploration",1000]],"implementation_revision":"p11-cpu-v1","max_art'
    b'ist_repeat":2,"max_release_repeat":1,"pipeline_key":"cpu-baseline","requ'
    b'est_schema_version":1,"version":"1"}'
)
NATIVE_REQUEST_V3_PIPELINE_MAX_RAW_BYTES: Final = 65_536
P11_V2_MANIFEST_SHA256: Final = "e989567355ba0e1a264f98ca8327005d40926a72f6e65e185166eae005bd4ce4"
P11_V2_MANIFEST_BYTES: Final = rfc8785.dumps(pipeline_manifest_document(baseline_pipeline_definition("2")))
if sha256(P11_V2_MANIFEST_BYTES).hexdigest() != P11_V2_MANIFEST_SHA256:
    raise RuntimeError("source3_p11_v2_manifest_drift")
_MAX_MANIFEST_BYTES: Final = max(len(P11_V2_MANIFEST_BYTES), len(NATIVE_REQUEST_V3_BASELINE_MANIFEST_BYTES))
# pg_column_size(external-uncompressed) excludes its four-byte varlena header.
NATIVE_REQUEST_V3_PIPELINE_MAX_STORED_BYTES: Final = 8_188
NATIVE_REQUEST_V3_PIPELINE_MAX_JSONB_BYTES: Final = 8_192
NATIVE_REQUEST_V3_PIPELINE_METADATA_BYTES: Final = 4_096
# Input bytea inspect/copy bound, fixed RHS UTF8/parse copies, bounded actual
# detoast/JSONB equality work, then small catalog/row descriptor work. These are
# logical work reservations, not an assertion about physical page IO or plans.
NATIVE_REQUEST_V3_PIPELINE_GUARD_BYTE_WORK: Final = (
    3 * NATIVE_REQUEST_V3_PIPELINE_MAX_RAW_BYTES
    + 8 * _MAX_MANIFEST_BYTES
    + 8 * NATIVE_REQUEST_V3_PIPELINE_MAX_JSONB_BYTES
    + NATIVE_REQUEST_V3_PIPELINE_METADATA_BYTES
)
NATIVE_REQUEST_V3_PIPELINE_GUARD_NODE_VISITS: Final = (
    8 * _MAX_MANIFEST_BYTES
    + 8 * NATIVE_REQUEST_V3_PIPELINE_MAX_JSONB_BYTES
    + 1_024
)
NATIVE_REQUEST_V3_PIPELINE_GUARD_CALL_SQL: Final = (
    "SELECT app_private.native_v3_assert_current_baseline_pipeline(%s)"
)

NATIVE_REQUEST_V3_PIPELINE_INSTALL_SQL: Final = f"""
CREATE FUNCTION app_private.native_p11_source_version(manifest bytea)
RETURNS integer LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
 CASE manifest
 WHEN decode('{NATIVE_REQUEST_V3_BASELINE_MANIFEST_SHA256}','hex') THEN RETURN 1;
 WHEN decode('{P11_V2_MANIFEST_SHA256}','hex') THEN RETURN 2;
 ELSE RAISE EXCEPTION 'NATIVE_SOURCE3_UNKNOWN_PIPELINE' USING ERRCODE='23514';
 END CASE;
END $$;
REVOKE ALL ON FUNCTION app_private.native_p11_source_version(bytea) FROM PUBLIC;

CREATE FUNCTION app_private.native_p11_source_policy(manifest bytea)
RETURNS bytea LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT decode(CASE app_private.native_p11_source_version(manifest)
  WHEN 1 THEN '683a44ed369682c9e2e08d10d932ba09a537a3d18992cdbb00f0418c0a93519c'
  WHEN 2 THEN '1d59d0a8c6e75348b2a5dcffd618dc401fb029f209d9c263bf3969f9003971d1' END,'hex')
$$;
REVOKE ALL ON FUNCTION app_private.native_p11_source_policy(bytea) FROM PUBLIC;

CREATE FUNCTION app_private.native_p11_snapshot_header(manifest bytea,owner_id uuid,watermark bigint)
RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('schema_version',v,'user_id',owner_id,'interaction_watermark',watermark,
  'selection_policy','most_recent_added_then_recording_id_v'||v,
  'max_tracks',CASE v WHEN 2 THEN 1024 ELSE 5000 END)
 FROM app_private.native_p11_source_version(manifest) v
$$;
REVOKE ALL ON FUNCTION app_private.native_p11_snapshot_header(bytea,uuid,bigint) FROM PUBLIC;

CREATE FUNCTION app_private.native_p11_source_candidate(v integer,track jsonb,surface text)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT coalesce(v IN (1,2) AND track->'authorized'='true'::jsonb
  AND track->'excluded'='false'::jsonb AND track->>'preference'<>'DISLIKED'
  AND (track->>'identity_status'='ACTIVE'
       OR (v=2 AND track->>'identity_status'='PROVISIONAL' AND owned))
  AND CASE WHEN surface='offline_pack' THEN track->>'availability'='LOCAL'
       OR (v=2 AND track->>'availability'='VAULT' AND owned)
      ELSE track->>'availability' IN ('LOCAL','VAULT','EXTERNAL') END,false)
 FROM (SELECT track->>'user_track_ref_id' IS NOT NULL AND track->>'owner_identity_decision_id' IS NOT NULL
       AND track->>'verified_audio_variant_id' IS NOT NULL AS owned) evidence
$$;
REVOKE ALL ON FUNCTION app_private.native_p11_source_candidate(integer,jsonb,text) FROM PUBLIC;

CREATE FUNCTION app_private.native_v3_assert_current_baseline_pipeline(raw bytea)
RETURNS void LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog AS $native_v3_pipeline$
DECLARE
 expected_version text; expected_sha bytea;
 compression text; stored_bytes integer; actual_key text; actual_version text;
 actual_sha bytea; allowed_collation boolean; matches boolean;
BEGIN
 IF raw IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_PIPELINE_INPUT' USING ERRCODE='23514'; END IF;
 IF octet_length(raw)>{NATIVE_REQUEST_V3_PIPELINE_MAX_RAW_BYTES} THEN
  RAISE EXCEPTION 'NATIVE_V3_PIPELINE_INPUT' USING ERRCODE='23514'; END IF;
 IF raw=decode('{NATIVE_REQUEST_V3_BASELINE_MANIFEST_BYTES.hex()}','hex') THEN
  expected_version:='1'; expected_sha:=decode('{NATIVE_REQUEST_V3_BASELINE_MANIFEST_SHA256}','hex');
 ELSIF raw=decode('{P11_V2_MANIFEST_BYTES.hex()}','hex') THEN
  expected_version:='2'; expected_sha:=decode('{P11_V2_MANIFEST_SHA256}','hex');
 ELSE
  RAISE EXCEPTION 'NATIVE_V3_PIPELINE_INPUT' USING ERRCODE='23514'; END IF;

 -- JSONB string equality uses DEFAULT_COLLATION_OID. Its provider 'd' points
 -- at the database provider/locale. Deterministic libc resolves string ties
 -- bytewise; ordered JSONB comparison also preserves array position.
 SELECT d.datlocprovider='c'
   AND ((d.datcollate COLLATE "C") IN ('C','POSIX','C.UTF-8','C.utf8')
     OR ((d.datcollate COLLATE "C")='en_US.utf8' AND pg_encoding_to_char(d.encoding)='UTF8'))
   AND c.collprovider='d' AND c.collisdeterministic
 INTO allowed_collation
 FROM pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_collation c
 WHERE d.datname=current_database() AND c.oid=100;
 IF allowed_collation IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'NATIVE_V3_PIPELINE_COLLATION' USING ERRCODE='23514'; END IF;

 -- FIRST actual-row read is metadata only. Keep both builtins directly on the
 -- table attribute: no whole-row argument, wrapper or text projection.
 SELECT pg_column_compression(pv.manifest),pg_column_size(pv.manifest),
  pv.pipeline_key,pv.version,pv.manifest_sha256
 INTO compression,stored_bytes,actual_key,actual_version,actual_sha
 FROM ml.recommendation_pipeline_version pv
 WHERE pv.pipeline_key='{NATIVE_REQUEST_V3_BASELINE_PIPELINE_KEY}'
  AND pv.version=expected_version
 FOR SHARE OF pv;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'NATIVE_V3_PIPELINE_MISSING' USING ERRCODE='23514'; END IF;
 IF compression IS NOT NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_PIPELINE_COMPRESSION' USING ERRCODE='23514'; END IF;
 IF stored_bytes IS NULL OR stored_bytes<1
  OR stored_bytes>{NATIVE_REQUEST_V3_PIPELINE_MAX_STORED_BYTES} THEN
  RAISE EXCEPTION 'NATIVE_V3_PIPELINE_SIZE' USING ERRCODE='23514'; END IF;
 IF actual_key IS DISTINCT FROM '{NATIVE_REQUEST_V3_BASELINE_PIPELINE_KEY}'
  OR actual_version IS DISTINCT FROM expected_version
  OR actual_sha IS DISTINCT FROM expected_sha THEN
  RAISE EXCEPTION 'NATIVE_V3_PIPELINE_IDENTITY' USING ERRCODE='23514'; END IF;
 IF sha256(raw) IS DISTINCT FROM actual_sha THEN
  RAISE EXCEPTION 'NATIVE_V3_PIPELINE_HASH' USING ERRCODE='23514'; END IF;

 -- Separate PL/pgSQL statement: actual detoast/equality starts only after the
 -- locked tuple's metadata was admitted. Never stringify the actual JSONB,
 -- including compact Numerics with enormous decimal output.
 SELECT pv.manifest=(convert_from(raw,'UTF8')::jsonb)
 INTO matches
 FROM ml.recommendation_pipeline_version pv
 WHERE pv.pipeline_key='{NATIVE_REQUEST_V3_BASELINE_PIPELINE_KEY}'
  AND pv.version=expected_version;
 IF matches IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'NATIVE_V3_PIPELINE_CONTENT' USING ERRCODE='23514'; END IF;
END $native_v3_pipeline$;
REVOKE ALL ON FUNCTION app_private.native_v3_assert_current_baseline_pipeline(bytea) FROM PUBLIC;
"""

NATIVE_REQUEST_V3_PIPELINE_DROP_SQL: Final = (
    "DROP FUNCTION app_private.native_v3_assert_current_baseline_pipeline(bytea);"
    "DROP FUNCTION app_private.native_p11_source_candidate(integer,jsonb,text);"
    "DROP FUNCTION app_private.native_p11_snapshot_header(bytea,uuid,bigint);"
    "DROP FUNCTION app_private.native_p11_source_policy(bytea);"
    "DROP FUNCTION app_private.native_p11_source_version(bytea);"
)
