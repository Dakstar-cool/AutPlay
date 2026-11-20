"""Staged raw BYTEA root preflight before PostgreSQL JSONB projection.

Only bounded string tokens and closed installed key inventories are hydrated
during the scan. Whole JSONB hydration follows complete raw admission. No
function installs itself merely because the module is imported.
"""

from __future__ import annotations

import json

from autplay.domain.native_request_wire_v3 import _ARRAYS, _OBJECTS

_SHAPES = {path: [sorted(keys) for keys in variants] for path, variants in _OBJECTS.items()}


def _json_literal(value: object) -> str:
    return "'" + json.dumps(value, sort_keys=True, separators=(",", ":")).replace("'", "''") + "'"


NATIVE_REQUEST_V3_WIRE_INSTALL_SQL = (
    r"""
CREATE FUNCTION app_private.native_v3_wire_objects() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _json_literal(_SHAPES)
    + r"""::jsonb $$;
CREATE FUNCTION app_private.native_v3_wire_arrays() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _json_literal(_ARRAYS)
    + r"""::jsonb $$;

CREATE FUNCTION app_private.native_v3_wire_string(raw bytea,p_offset integer)
RETURNS TABLE(next_position integer,decoded_value text)
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE start integer:=p_offset; current integer; token bytea; decoded jsonb;
BEGIN
 IF p_offset<0 OR p_offset>=octet_length(raw) OR get_byte(raw,p_offset)<>34 THEN
  RAISE EXCEPTION 'NATIVE_V3_RAW_STRING' USING ERRCODE='23514'; END IF;
 p_offset:=p_offset+1;
 LOOP
  IF p_offset>=octet_length(raw) OR p_offset-start>24577 THEN
   RAISE EXCEPTION 'NATIVE_V3_RAW_STRING_BOUND' USING ERRCODE='23514'; END IF;
  current:=get_byte(raw,p_offset); p_offset:=p_offset+1;
  IF current=34 THEN EXIT; END IF;
  IF current<32 THEN RAISE EXCEPTION 'NATIVE_V3_RAW_STRING' USING ERRCODE='23514'; END IF;
  IF current=92 THEN
   IF p_offset>=octet_length(raw) THEN
    RAISE EXCEPTION 'NATIVE_V3_RAW_STRING' USING ERRCODE='23514'; END IF;
   p_offset:=p_offset+1;
  END IF;
 END LOOP;
 -- The only raw substring admitted here is one <=24578-byte string token.
 token:=substring(raw FROM start+1 FOR p_offset-start);
 decoded:=convert_from(token,'UTF8')::jsonb;
 IF jsonb_typeof(decoded) IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION 'NATIVE_V3_RAW_STRING' USING ERRCODE='23514'; END IF;
 decoded_value:=decoded#>>'{}';
 IF octet_length(convert_to(decoded_value,'UTF8'))>4096
  OR decoded_value<>normalize(decoded_value,NFC)
  OR convert_to(to_jsonb(decoded_value)::text,'UTF8') IS DISTINCT FROM token THEN
  RAISE EXCEPTION 'NATIVE_V3_RAW_STRING_CANONICAL' USING ERRCODE='23514'; END IF;
 next_position:=p_offset; RETURN NEXT;
END $$;

CREATE FUNCTION app_private.native_v3_wire_value(raw bytea,p_offset integer,
 path text DEFAULT '$',depth integer DEFAULT 0) RETURNS bigint[]
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE current integer; start integer; count integer:=0; size bigint:=0; nodes bigint:=1;
 byte_work bigint:=0; key_text text; previous_key text:=NULL; child bigint[];
 found text[]:=ARRAY[]::text[]; variants jsonb; capacity integer; token text;
 string_row record; leaf_phase boolean; bound bigint;
BEGIN
 IF depth>12 OR p_offset<0 OR p_offset>=octet_length(raw) THEN
  RAISE EXCEPTION 'NATIVE_V3_RAW_BOUND' USING ERRCODE='23514'; END IF;
 current:=get_byte(raw,p_offset); start:=p_offset;
 variants:=app_private.native_v3_wire_objects()->path;
 capacity:=(app_private.native_v3_wire_arrays()->>path)::integer;
 -- Every complete leaf/component must fit the unchanged 64KiB LP codec.
 -- The large body and its track/item arrays carry only numeric size counters.
 leaf_phase:=path NOT IN ('$','$.body','$.body.snapshot_tracks','$.body.p11_items');
 bound:=CASE WHEN path='$.root_header' THEN 4096 ELSE 65536 END;
 IF variants IS NOT NULL THEN
  IF current<>123 THEN RAISE EXCEPTION 'NATIVE_V3_RAW_CONTAINER' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1; size:=5;
  IF p_offset>=octet_length(raw) THEN
   RAISE EXCEPTION 'NATIVE_V3_RAW_SYNTAX' USING ERRCODE='23514'; END IF;
  IF get_byte(raw,p_offset)<>125 THEN
   LOOP
    count:=count+1;
    IF count>4096 THEN RAISE EXCEPTION 'NATIVE_V3_RAW_MEMBERS' USING ERRCODE='23514'; END IF;
    SELECT * INTO string_row FROM app_private.native_v3_wire_string(raw,p_offset);
    key_text:=string_row.decoded_value; p_offset:=string_row.next_position;
    -- These SRFs inspect installed schema arrays, never attacker object keys.
    IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(variants) variant(value)
      WHERE variant.value ? key_text)
     OR (previous_key IS NOT NULL AND key_text COLLATE "C"<=previous_key COLLATE "C") THEN
     RAISE EXCEPTION 'NATIVE_V3_RAW_KEYS' USING ERRCODE='23514'; END IF;
    previous_key:=key_text; found:=array_append(found,key_text);
    IF p_offset>=octet_length(raw) OR get_byte(raw,p_offset)<>58 THEN
     RAISE EXCEPTION 'NATIVE_V3_RAW_SYNTAX' USING ERRCODE='23514'; END IF;
    child:=app_private.native_v3_wire_value(raw,p_offset+1,path||'.'||key_text,depth+1);
    p_offset:=child[1]::integer; size:=size+8+octet_length(key_text)+child[2];
    nodes:=nodes+2+child[3]; byte_work:=byte_work+child[4]+2*octet_length(key_text);
    IF leaf_phase AND size>bound THEN
     RAISE EXCEPTION 'NATIVE_V3_RAW_LEAF_BYTES' USING ERRCODE='23514'; END IF;
    IF p_offset>=octet_length(raw) THEN
     RAISE EXCEPTION 'NATIVE_V3_RAW_SYNTAX' USING ERRCODE='23514'; END IF;
    EXIT WHEN get_byte(raw,p_offset)<>44;
    p_offset:=p_offset+1;
   END LOOP;
  END IF;
  IF get_byte(raw,p_offset)<>125 OR NOT EXISTS(
   SELECT 1 FROM jsonb_array_elements(variants) variant(value)
   WHERE variant.value=to_jsonb(found)) THEN
   RAISE EXCEPTION 'NATIVE_V3_RAW_KEYS' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1;
 ELSIF capacity IS NOT NULL THEN
  IF current<>91 THEN RAISE EXCEPTION 'NATIVE_V3_RAW_CONTAINER' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1; size:=5;
  IF p_offset>=octet_length(raw) THEN
   RAISE EXCEPTION 'NATIVE_V3_RAW_SYNTAX' USING ERRCODE='23514'; END IF;
  IF get_byte(raw,p_offset)<>93 THEN
   LOOP
    count:=count+1;
    IF count>capacity THEN
     RAISE EXCEPTION 'NATIVE_V3_RAW_ARRAY_COUNT' USING ERRCODE='23514'; END IF;
    child:=app_private.native_v3_wire_value(raw,p_offset,path||'[]',depth+1);
    p_offset:=child[1]::integer; size:=size+4+child[2];
    nodes:=nodes+1+child[3]; byte_work:=byte_work+child[4];
    IF leaf_phase AND size>bound THEN
     RAISE EXCEPTION 'NATIVE_V3_RAW_LEAF_BYTES' USING ERRCODE='23514'; END IF;
    IF p_offset>=octet_length(raw) THEN
     RAISE EXCEPTION 'NATIVE_V3_RAW_SYNTAX' USING ERRCODE='23514'; END IF;
    EXIT WHEN get_byte(raw,p_offset)<>44;
    p_offset:=p_offset+1;
   END LOOP;
  END IF;
  IF get_byte(raw,p_offset)<>93 THEN
   RAISE EXCEPTION 'NATIVE_V3_RAW_SYNTAX' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1;
 ELSIF current=34 THEN
  SELECT * INTO string_row FROM app_private.native_v3_wire_string(raw,p_offset);
  p_offset:=string_row.next_position;
  size:=5+octet_length(convert_to(string_row.decoded_value,'UTF8'));
  byte_work:=byte_work+4*(p_offset-start);
 ELSIF current IN (45,48,49,50,51,52,53,54,55,56,57) THEN
  IF current=45 THEN p_offset:=p_offset+1; END IF;
  WHILE p_offset<octet_length(raw) AND get_byte(raw,p_offset) BETWEEN 48 AND 57 LOOP
   p_offset:=p_offset+1;
   IF p_offset-start>20 THEN
    RAISE EXCEPTION 'NATIVE_V3_RAW_INTEGER' USING ERRCODE='23514'; END IF;
  END LOOP;
  token:=convert_from(substring(raw FROM start+1 FOR p_offset-start),'UTF8');
  IF token !~ '^(0|[1-9][0-9]*|-[1-9][0-9]*)$' THEN
   RAISE EXCEPTION 'NATIVE_V3_RAW_INTEGER' USING ERRCODE='23514'; END IF;
  PERFORM token::bigint; size:=9;
 ELSIF current=110 AND substring(raw FROM p_offset+1 FOR 4)=convert_to('null','UTF8') THEN
  p_offset:=p_offset+4; size:=1;
 ELSIF current=116 AND substring(raw FROM p_offset+1 FOR 4)=convert_to('true','UTF8') THEN
  p_offset:=p_offset+4; size:=1;
 ELSIF current=102 AND substring(raw FROM p_offset+1 FOR 5)=convert_to('false','UTF8') THEN
  p_offset:=p_offset+5; size:=1;
 ELSE RAISE EXCEPTION 'NATIVE_V3_RAW_SYNTAX' USING ERRCODE='23514';
 END IF;
 IF leaf_phase AND size>bound THEN
  RAISE EXCEPTION 'NATIVE_V3_RAW_LEAF_BYTES' USING ERRCODE='23514'; END IF;
 RETURN ARRAY[p_offset::bigint,size,nodes,byte_work+(p_offset-start)];
END $$;

CREATE FUNCTION app_private.native_v3_root_preflight(raw bytea) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE result bigint[];
BEGIN
 IF raw IS NULL OR octet_length(raw) NOT BETWEEN 1 AND 2097152 THEN
  RAISE EXCEPTION 'NATIVE_V3_RAW_BYTES' USING ERRCODE='23514'; END IF;
 result:=app_private.native_v3_wire_value(raw,0);
 IF result[1]<>octet_length(raw) THEN
  RAISE EXCEPTION 'NATIVE_V3_RAW_TRAILING' USING ERRCODE='23514'; END IF;
 -- Only now can the complete admitted source be hydrated. No attacker key or
 -- oversized scalar has entered a JSONB projection/SRF during raw admission.
 RETURN convert_from(raw,'UTF8')::jsonb;
END $$;

REVOKE ALL ON FUNCTION app_private.native_v3_wire_objects(),app_private.native_v3_wire_arrays(),
 app_private.native_v3_wire_string(bytea,integer),
 app_private.native_v3_wire_value(bytea,integer,text,integer),
 app_private.native_v3_root_preflight(bytea) FROM PUBLIC;
"""
)

NATIVE_REQUEST_V3_WIRE_DROP_SQL = r"""
DROP FUNCTION app_private.native_v3_root_preflight(bytea);
DROP FUNCTION app_private.native_v3_wire_value(bytea,integer,text,integer);
DROP FUNCTION app_private.native_v3_wire_string(bytea,integer);
DROP FUNCTION app_private.native_v3_wire_arrays();
DROP FUNCTION app_private.native_v3_wire_objects();
"""
