"""Staged PostgreSQL V3 hashing kernel; importing this module installs nothing.

The role aggregate deliberately uses a streaming target-list ProjectSet, a
private ordered-aggregate sorter and exactly one final flat BYTEA. Qualification
must inspect the actual PostgreSQL 18.4 plan before this kernel is admitted.
"""

from __future__ import annotations

import json

from autplay.domain.native_request_roles_v3 import ROLE_FIELDS_V3
from autplay.domain.recommendations import SUPPORTED_CONTEXTS


def _literal(value: object) -> str:
    """Quote installed schema constants only, never input documents or SQL names."""
    return "'" + json.dumps(value, separators=(",", ":"), sort_keys=True).replace("'", "''") + "'"


_ROLE_SCHEMA = {role: dict(fields) for role, fields in ROLE_FIELDS_V3.items()}
_ROLE_LITERALS = {
    "metadata": {"endpoint_max": 100, "pipeline_key": "cpu-baseline"},
    "query": {"schema_version": 2, "canonicalization_version": 1, "shadow": False},
    "snapshot-header": {
        "schema_version": 1,
        "selection_policy": "most_recent_added_then_recording_id_v1",
        "max_tracks": 5000,
    },
    "preferences": {"preference": "LIKE"},
    "exploration": {"seeded": True},
    "served-item": {"score_kind": "heuristic", "attribution_source": "P11", "origin": "P11"},
    "root-header": {"kind": "NATIVE_SERVED_REQUEST_ROOT_V3", "hash_format_version": 3},
}

NATIVE_REQUEST_V3_HASH_INSTALL_SQL = (
    r"""
CREATE FUNCTION app_private.native_v3_role_schema() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _literal(_ROLE_SCHEMA)
    + r"""::jsonb $$;

CREATE FUNCTION app_private.native_v3_role_literals() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _literal(_ROLE_LITERALS)
    + r"""::jsonb $$;

CREATE FUNCTION app_private.native_v3_contexts() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _literal(sorted(SUPPORTED_CONTEXTS))
    + r"""::jsonb $$;

CREATE FUNCTION app_private.native_v3_closed_role(role text,value jsonb)
RETURNS void LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE fields jsonb; k text; code text; actual jsonb; integer_value bigint;
 lower_bound bigint; upper_bound bigint;
BEGIN
 fields:=app_private.native_v3_role_schema()->role;
 IF fields IS NULL OR jsonb_typeof(value) IS DISTINCT FROM 'object'
  OR (SELECT count(*) FROM jsonb_object_keys(value))<>
   (SELECT count(*) FROM jsonb_object_keys(fields))
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(fields) key WHERE NOT value ? key) THEN
  RAISE EXCEPTION 'NATIVE_V3_ROLE_KEYS' USING ERRCODE='23514'; END IF;
 FOR k,code IN SELECT entry.key,entry.value FROM jsonb_each_text(fields) entry LOOP
  actual:=value->k;
  IF right(code,1)='?' THEN
   IF actual='null'::jsonb THEN CONTINUE; END IF;
   code:=left(code,length(code)-1);
  END IF;
  IF code IN ('I','Z','P','L','E','R','T','N') THEN
   IF jsonb_typeof(actual) IS DISTINCT FROM 'number' OR actual::text !~ '^-?(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'NATIVE_V3_ROLE_INTEGER' USING ERRCODE='23514'; END IF;
   integer_value:=(actual::text)::bigint;
   lower_bound:=CASE code WHEN 'I' THEN '-9223372036854775808'::bigint
    WHEN 'P' THEN 1 WHEN 'L' THEN 1 WHEN 'R' THEN 1 ELSE 0 END;
   upper_bound:=CASE code WHEN 'L' THEN 100 WHEN 'E' THEN 100000000
    WHEN 'R' THEN 1000 WHEN 'T' THEN 5000 WHEN 'N' THEN 100 ELSE 9223372036854775807 END;
   IF integer_value NOT BETWEEN lower_bound AND upper_bound THEN
    RAISE EXCEPTION 'NATIVE_V3_ROLE_INTEGER' USING ERRCODE='23514'; END IF;
  ELSIF code='B' THEN
   IF jsonb_typeof(actual) IS DISTINCT FROM 'boolean' THEN
    RAISE EXCEPTION 'NATIVE_V3_ROLE_BOOLEAN' USING ERRCODE='23514'; END IF;
  ELSIF code IN ('S','U','D') THEN
   IF jsonb_typeof(actual) IS DISTINCT FROM 'string'
    OR octet_length(convert_to(actual#>>'{}','UTF8'))>4096
    OR (actual#>>'{}')<>normalize(actual#>>'{}',NFC) THEN
    RAISE EXCEPTION 'NATIVE_V3_ROLE_TEXT' USING ERRCODE='23514'; END IF;
   IF code='U' AND ((actual#>>'{}')::uuid)::text IS DISTINCT FROM actual#>>'{}' THEN
    RAISE EXCEPTION 'NATIVE_V3_ROLE_UUID' USING ERRCODE='23514'; END IF;
   IF code='D' AND (actual#>>'{}') !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'NATIVE_V3_ROLE_DIGEST' USING ERRCODE='23514'; END IF;
  ELSIF code IN ('A','C') THEN
   IF jsonb_typeof(actual) IS DISTINCT FROM 'array' OR jsonb_array_length(actual)>4096 THEN
    RAISE EXCEPTION 'NATIVE_V3_ROLE_ARRAY' USING ERRCODE='23514'; END IF;
   IF code='A' AND EXISTS(SELECT 1 FROM jsonb_array_elements(actual) child
    WHERE jsonb_typeof(child) IS DISTINCT FROM 'string'
     OR octet_length(convert_to(child#>>'{}','UTF8'))>4096
     OR (child#>>'{}')<>normalize(child#>>'{}',NFC)) THEN
    RAISE EXCEPTION 'NATIVE_V3_ROLE_TEXT' USING ERRCODE='23514'; END IF;
  ELSIF code='O' AND jsonb_typeof(actual) IS DISTINCT FROM 'object' THEN
   RAISE EXCEPTION 'NATIVE_V3_ROLE_OBJECT' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF role='snapshot-header' AND value->'schema_version'='2'::jsonb THEN
  IF value->>'selection_policy' IS DISTINCT FROM 'most_recent_added_then_recording_id_v2'
   OR value->'max_tracks' IS DISTINCT FROM '1024'::jsonb THEN
   RAISE EXCEPTION 'NATIVE_V3_ROLE_LITERAL' USING ERRCODE='23514'; END IF;
 ELSIF EXISTS(SELECT 1 FROM jsonb_each(app_private.native_v3_role_literals()->role) literal
  WHERE native_v3_closed_role.value->literal.key IS DISTINCT FROM literal.value) THEN
  RAISE EXCEPTION 'NATIVE_V3_ROLE_LITERAL' USING ERRCODE='23514'; END IF;
 IF role IN ('metadata','query') AND
  (value->>'surface' NOT IN ('recommendations','home','offline_pack')
   OR NOT app_private.native_v3_contexts() ? (value->>'context')) THEN
  RAISE EXCEPTION 'NATIVE_V3_ROLE_QUERY' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION app_private.native_v3_lp_size(value jsonb,schema jsonb,
 path text DEFAULT '$',depth integer DEFAULT 0) RETURNS integer
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE size integer; count integer; child_size integer; key_text text; index integer;
BEGIN
 IF value IS NULL OR schema IS NULL OR depth>12 THEN
  RAISE EXCEPTION 'NATIVE_V3_LEAF_SHAPE' USING ERRCODE='23514'; END IF;
 CASE jsonb_typeof(value)
 WHEN 'null','boolean' THEN size:=1;
 WHEN 'number' THEN
  IF value::text !~ '^-?(0|[1-9][0-9]*)$' THEN
   RAISE EXCEPTION 'NATIVE_V3_LEAF_INTEGER' USING ERRCODE='23514'; END IF;
  PERFORM (value::text)::bigint; size:=9;
 WHEN 'string' THEN
  key_text:=value#>>'{}'; child_size:=octet_length(convert_to(key_text,'UTF8'));
  IF child_size>4096 OR key_text<>normalize(key_text,NFC) THEN
   RAISE EXCEPTION 'NATIVE_V3_LEAF_TEXT' USING ERRCODE='23514'; END IF;
  size:=5+child_size;
 WHEN 'array' THEN
  count:=jsonb_array_length(value);
  IF count>4096 OR depth>=12 THEN
   RAISE EXCEPTION 'NATIVE_V3_LEAF_BOUND' USING ERRCODE='23514'; END IF;
  size:=5;
  IF count>0 THEN
   FOR index IN 0..count-1 LOOP
    child_size:=app_private.native_v3_lp_size(value->index,schema,path||'[]',depth+1);
    size:=size+4+child_size;
    IF size>65536 THEN
     RAISE EXCEPTION 'NATIVE_V3_LEAF_BYTES' USING ERRCODE='23514'; END IF;
   END LOOP;
  END IF;
 WHEN 'object' THEN
  SELECT count(*) INTO count FROM jsonb_object_keys(value);
  IF count>4096 OR depth>=12 OR NOT schema ? path THEN
   RAISE EXCEPTION 'NATIVE_V3_LEAF_BOUND' USING ERRCODE='23514'; END IF;
  size:=5;
  FOR key_text IN SELECT entry.key FROM jsonb_object_keys(value) entry(key) LOOP
   IF key_text !~ '^[ -~]+$' OR NOT (schema->path) ? key_text THEN
    RAISE EXCEPTION 'NATIVE_V3_LEAF_MEMBER' USING ERRCODE='23514'; END IF;
   child_size:=app_private.native_v3_lp_size(value->key_text,schema,
    path||'.'||key_text,depth+1);
   size:=size+8+octet_length(convert_to(key_text,'UTF8'))+child_size;
   IF size>65536 THEN
    RAISE EXCEPTION 'NATIVE_V3_LEAF_BYTES' USING ERRCODE='23514'; END IF;
  END LOOP;
 ELSE RAISE EXCEPTION 'NATIVE_V3_LEAF_TYPE' USING ERRCODE='23514';
 END CASE;
 RETURN size;
END $$;

CREATE FUNCTION app_private.native_v3_role_lp_schema(role text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE result jsonb; provenance_keys jsonb;
BEGIN
 IF NOT app_private.native_v3_role_schema() ? role THEN
  RAISE EXCEPTION 'NATIVE_V3_ROLE_UNKNOWN' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('$',jsonb_agg(key ORDER BY key COLLATE "C")) INTO result
  FROM jsonb_object_keys(app_private.native_v3_role_schema()->role) key;
 IF role IN ('p11-item','served-item') THEN
  SELECT jsonb_agg(DISTINCT field.field_key ORDER BY field.field_key) INTO provenance_keys
   FROM jsonb_each(app_private.native_v3_role_schema()) kinds(kind_key,kind_fields),
    LATERAL jsonb_object_keys(kinds.kind_fields) field(field_key)
   WHERE kinds.kind_key IN
    ('preferences','history','artist_release_metadata','freshness','exploration');
  result:=result||jsonb_build_object('$.contributions[]',
    (SELECT jsonb_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(
     app_private.native_v3_role_schema()->'contribution') key),
    '$.contributions[].provenance',provenance_keys);
  IF role='p11-item' THEN
   result:=result||jsonb_build_object('$.availability_snapshot','["snapshot_sha256"]'::jsonb,
    '$.item_provenance','["artist_key","release_key","section"]'::jsonb);
  END IF;
 END IF;
 RETURN result;
END $$;

-- Common track roles contain only scalars and one string array. A small,
-- uncompressed JSONB leaf fits the work slot before any object/array iteration.
-- Encode and type-check it in one ordered walk using the same installed schema.
CREATE FUNCTION app_private.native_v3_small_set_leaf(role text,leaf jsonb) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE field_types jsonb; label text; value jsonb; code text; child bytea;
 name_bytes bytea; encoded bytea; token jsonb; utf8 bytea; n integer; seen integer:=0;
 number_text text; scalar_text text;
BEGIN
 IF role NOT IN ('snapshot-track','policy-track','availability-track','candidate')
  OR role IS NULL OR jsonb_typeof(leaf) IS DISTINCT FROM 'object'
  OR pg_column_compression(leaf) IS NOT NULL OR pg_column_size(leaf)>2048 THEN
  RAISE EXCEPTION 'NATIVE_V3_SET_INPUT' USING ERRCODE='23514'; END IF;
 field_types:=app_private.native_v3_role_schema()->role;
 IF role IN ('snapshot-track','policy-track') AND leaf ? 'owner_identity_decision_id' THEN
  field_types:=field_types||'{"owner_identity_decision_id":"U"}'::jsonb;
 END IF;
 IF role IN ('snapshot-track','availability-track') AND leaf ? 'verified_audio_variant_id' THEN
  field_types:=field_types||'{"verified_audio_variant_id":"U"}'::jsonb;
 END IF;
 SELECT count(*) INTO n FROM jsonb_object_keys(field_types);
 encoded:=convert_to('o','UTF8')||int4send(n);
 FOR label,value IN SELECT e.key,e.value FROM jsonb_each(leaf) e ORDER BY e.key COLLATE "C" LOOP
  code:=field_types->>label;
  IF code IS NULL THEN RAISE EXCEPTION 'NATIVE_V3_ROLE_KEYS' USING ERRCODE='23514'; END IF;
  seen:=seen+1;
  IF right(code,1)='?' AND value='null'::jsonb THEN child:=convert_to('n','UTF8');
  ELSE
   code:=rtrim(code,'?');
   CASE code
   WHEN 'U','S' THEN
    scalar_text:=value#>>'{}';
    IF jsonb_typeof(value)<>'string' OR scalar_text<>normalize(scalar_text,NFC) THEN
     RAISE EXCEPTION 'NATIVE_V3_ROLE_TEXT' USING ERRCODE='23514'; END IF;
    IF code='U' AND (scalar_text::uuid)::text<>scalar_text THEN
     RAISE EXCEPTION 'NATIVE_V3_ROLE_UUID' USING ERRCODE='23514'; END IF;
    utf8:=convert_to(scalar_text,'UTF8');
    child:=convert_to('s','UTF8')||int4send(octet_length(utf8))||utf8;
   WHEN 'B' THEN
    IF jsonb_typeof(value)<>'boolean' THEN
     RAISE EXCEPTION 'NATIVE_V3_ROLE_BOOLEAN' USING ERRCODE='23514'; END IF;
    child:=convert_to(CASE WHEN value='true'::jsonb THEN 't' ELSE 'f' END,'UTF8');
   WHEN 'Z','I' THEN
    number_text:=value::text;
    IF jsonb_typeof(value)<>'number' OR number_text !~ '^-?(0|[1-9][0-9]*)$'
     OR (code='Z' AND left(number_text,1)='-') THEN
     RAISE EXCEPTION 'NATIVE_V3_ROLE_INTEGER' USING ERRCODE='23514'; END IF;
    child:=convert_to('i','UTF8')||int8send(number_text::bigint);
   WHEN 'A' THEN
    IF jsonb_typeof(value)<>'array' THEN
     RAISE EXCEPTION 'NATIVE_V3_ROLE_ARRAY' USING ERRCODE='23514'; END IF;
    child:=convert_to('a','UTF8')||int4send(jsonb_array_length(value));
    FOR token IN SELECT e FROM jsonb_array_elements(value) e LOOP
     scalar_text:=token#>>'{}';
     IF jsonb_typeof(token)<>'string' OR scalar_text<>normalize(scalar_text,NFC) THEN
      RAISE EXCEPTION 'NATIVE_V3_ROLE_TEXT' USING ERRCODE='23514'; END IF;
     utf8:=convert_to(scalar_text,'UTF8');
     child:=child||int4send(5+octet_length(utf8))||convert_to('s','UTF8')
      ||int4send(octet_length(utf8))||utf8;
    END LOOP;
   ELSE RAISE EXCEPTION 'NATIVE_V3_ROLE_UNKNOWN' USING ERRCODE='23514';
   END CASE;
  END IF;
  name_bytes:=convert_to(label,'UTF8');
  encoded:=encoded||int4send(octet_length(name_bytes))||name_bytes||int4send(octet_length(child))||child;
 END LOOP;
 IF seen<>n OR octet_length(encoded)>65536 THEN
  RAISE EXCEPTION 'NATIVE_V3_ROLE_KEYS' USING ERRCODE='23514'; END IF;
 RETURN sha256(encoded);
END $$;

CREATE FUNCTION app_private.native_v3_role_hash(role text,value jsonb) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE contribution jsonb; schema jsonb;
BEGIN
 IF role IN ('snapshot-track','policy-track','availability-track','candidate')
  AND pg_column_compression(value) IS NULL AND pg_column_size(value)<=2048 THEN
  RETURN app_private.native_v3_small_set_leaf(role,value); END IF;
 schema:=app_private.native_v3_role_lp_schema(role);
 -- This bounded indexed walk precedes every closed-role array inspection and
 -- the old encoder. It does not expand array elements through a FunctionScan.
 PERFORM app_private.native_v3_lp_size(value,schema);
 PERFORM app_private.native_v3_closed_role(role,value);
 IF role IN ('p11-item','served-item') THEN
  FOR contribution IN SELECT child FROM jsonb_array_elements(value->'contributions') child LOOP
   PERFORM app_private.native_v3_closed_role('contribution',contribution);
   IF contribution->>'source_key' NOT IN
    ('preferences','history','artist_release_metadata','freshness','exploration') THEN
    RAISE EXCEPTION 'NATIVE_V3_PROVENANCE_SOURCE' USING ERRCODE='23514'; END IF;
   PERFORM app_private.native_v3_closed_role(contribution->>'source_key',
    contribution->'provenance');
  END LOOP;
  IF role='p11-item' THEN
   PERFORM app_private.native_v3_closed_role('availability-reference',
    value->'availability_snapshot');
   PERFORM app_private.native_v3_closed_role('item-provenance',value->'item_provenance');
  END IF;
 END IF;
 RETURN sha256(app_private.native_served_json_lp(value,schema));
END $$;

CREATE FUNCTION app_private.native_v3_role_segment(role text,request_id uuid,ordinal bigint,
 count integer,tracks jsonb,candidates jsonb) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE leaf jsonb; leaf_hash bytea; domain text;
BEGIN
 IF role NOT IN ('snapshot-track','policy-track','availability-track','candidate')
  OR ordinal NOT BETWEEN 0 AND count OR count NOT BETWEEN 0 AND 5000 THEN
  RAISE EXCEPTION 'NATIVE_V3_SET_INPUT' USING ERRCODE='23514'; END IF;
 domain:='autplay.recommendation-'||role||'-set.v3';
 IF ordinal=0 THEN
  RETURN convert_to(domain,'UTF8')||decode('00','hex')||int4send(count+2)
   ||decode('0100000010','hex')||uuid_send(request_id)
   ||decode('0100000008','hex')||int8send(count::bigint);
 END IF;
 IF role='candidate' THEN
  leaf:=jsonb_build_object('recording_id',candidates->(ordinal::integer-1));
 ELSE
  leaf:=tracks->(ordinal::integer-1);
  IF role='policy-track' THEN
   -- Subtract unrelated fields through an explicit projection. The optional
   -- ownership evidence participates in the hash only when physically present.
   leaf:=jsonb_build_object('recording_id',leaf->'recording_id',
    'identity_status',leaf->'identity_status','preference',leaf->'preference','excluded',leaf->'excluded')
    || CASE WHEN leaf ? 'owner_identity_decision_id'
       THEN jsonb_build_object('owner_identity_decision_id',leaf->'owner_identity_decision_id')
       ELSE '{}'::jsonb END;
  ELSIF role='availability-track' THEN
   leaf:=jsonb_build_object('recording_id',leaf->'recording_id',
    'availability',leaf->'availability','authorized',leaf->'authorized')
    || CASE WHEN leaf ? 'verified_audio_variant_id'
       THEN jsonb_build_object('verified_audio_variant_id',leaf->'verified_audio_variant_id')
       ELSE '{}'::jsonb END;
  END IF;
 END IF;
 leaf_hash:=sha256(app_private.native_served_lp('autplay.recommendation-'||role||'-leaf.v3',
  ARRAY[uuid_send(request_id),int8send(ordinal),app_private.native_v3_role_hash(role,leaf)]));
 RETURN decode('0100000028','hex')||int8send(ordinal)||leaf_hash;
END $$;

CREATE FUNCTION app_private.native_v3_role_set_digest(role text,request_id uuid,
 tracks jsonb,candidates jsonb) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog SET enable_presorted_aggregate=off AS $$
DECLARE count integer;
BEGIN
 IF request_id IS NULL OR role IS NULL OR role NOT IN
  ('snapshot-track','policy-track','availability-track','candidate')
  OR jsonb_typeof(tracks) IS DISTINCT FROM 'array'
  OR jsonb_typeof(candidates) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'NATIVE_V3_SET_INPUT' USING ERRCODE='23514'; END IF;
 count:=CASE WHEN role='candidate' THEN jsonb_array_length(candidates)
  ELSE jsonb_array_length(tracks) END;
 IF count>5000 THEN RAISE EXCEPTION 'NATIVE_V3_SET_COUNT' USING ERRCODE='23514'; END IF;
 -- The SRF belongs to the target list: ProjectSet, never a FROM FunctionScan.
 -- No materialized input CTE, leaf array, post-aggregate concatenation or
 -- external presorted aggregate may retain a second full role representation.
 RETURN (SELECT sha256(string_agg(
   app_private.native_v3_role_segment(role,request_id,ordinal,count,tracks,candidates),
   ''::bytea ORDER BY ordinal))
  FROM (SELECT generate_series(0,count)::bigint AS ordinal OFFSET 0) positions);
END $$;

REVOKE ALL ON FUNCTION app_private.native_v3_role_schema(),
 app_private.native_v3_role_literals(),app_private.native_v3_contexts(),
 app_private.native_v3_lp_size(jsonb,jsonb,text,integer),
 app_private.native_v3_closed_role(text,jsonb),app_private.native_v3_role_lp_schema(text),
 app_private.native_v3_role_hash(text,jsonb),app_private.native_v3_small_set_leaf(text,jsonb),
 app_private.native_v3_role_segment(text,uuid,bigint,integer,jsonb,jsonb),
 app_private.native_v3_role_set_digest(text,uuid,jsonb,jsonb) FROM PUBLIC;
"""
)

NATIVE_REQUEST_V3_HASH_DROP_SQL = r"""
DROP FUNCTION app_private.native_v3_role_set_digest(text,uuid,jsonb,jsonb);
DROP FUNCTION app_private.native_v3_role_segment(text,uuid,bigint,integer,jsonb,jsonb);
DROP FUNCTION app_private.native_v3_role_hash(text,jsonb);
DROP FUNCTION app_private.native_v3_small_set_leaf(text,jsonb);
DROP FUNCTION app_private.native_v3_role_lp_schema(text);
DROP FUNCTION app_private.native_v3_closed_role(text,jsonb);
DROP FUNCTION app_private.native_v3_lp_size(jsonb,jsonb,text,integer);
DROP FUNCTION app_private.native_v3_contexts();
DROP FUNCTION app_private.native_v3_role_literals();
DROP FUNCTION app_private.native_v3_role_schema();
"""
