"""Private, explicitly installed source3 F64-bits raw codec.

This is distinct from both the frozen source2 codec and the root3 I64 codec.
The caller precharges the complete reservation before invoking SQL, including
failed attempts. Remaining-budget arguments are defensive checks, not a ledger.
No function here grants consent, creates source authority, or installs itself.
"""

from __future__ import annotations

import json

from autplay.domain.native_original_capture_v3 import _ARRAYS, _ENUMS, _LITERALS, _OBJECTS

NATIVE_SOURCE3_SQL_FIXED_BYTE_WORK = 16 * 1024 * 1024
NATIVE_SOURCE3_SQL_BYTE_WORK_FACTOR = 144
NATIVE_SOURCE3_SQL_NODE_VISIT_FACTOR = 16
NATIVE_SOURCE3_SQL_LOGICAL_LIMIT = 8 * 1024 * 1024
NATIVE_SOURCE3_SQL_SCRATCH_BOUND = 1024 * 1024
# PostgreSQL arrays use one-based indexes. The result contains structural facts
# and full conservative precharge amounts; it grants no subsequent hydration.
NATIVE_SOURCE3_SQL_STAT_DOCUMENT_BYTES = 1
NATIVE_SOURCE3_SQL_STAT_NODE_VISITS = 2
NATIVE_SOURCE3_SQL_STAT_BYTE_WORK = 3
NATIVE_SOURCE3_SQL_STAT_JSONB_ESTIMATE = 4

_SHAPES = {path: sorted(fields) for path, fields in _OBJECTS.items()}
_RULES = {
    path + "." + key: rule for path, fields in _OBJECTS.items() for key, rule in fields.items()
}
_RULES.update({path + "[]": rule for path, (_, rule) in _ARRAYS.items()})
_RULES["$"] = "object"


def _literal(value: object) -> str:
    return "'" + json.dumps(value, sort_keys=True, separators=(",", ":")).replace("'", "''") + "'"


NATIVE_ORIGINAL_CAPTURE_V3_INSTALL_SQL = (
    r"""
CREATE FUNCTION app_private.native_source3_shapes_v3() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _literal(_SHAPES)
    + r"""::jsonb $$;
CREATE FUNCTION app_private.native_source3_arrays_v3() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _literal(_ARRAYS)
    + r"""::jsonb $$;
CREATE FUNCTION app_private.native_source3_rules_v3() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _literal(_RULES)
    + r"""::jsonb $$;
CREATE FUNCTION app_private.native_source3_literals_v3() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _literal(_LITERALS)
    + r"""::jsonb $$;
CREATE FUNCTION app_private.native_source3_enums_v3() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT """
    + _literal({key: sorted(values) for key, values in _ENUMS.items()})
    + r"""::jsonb $$;

CREATE FUNCTION app_private.native_source3_float_bits_v3(encoded text,rule text) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE bits bytea; magnitude bytea; exponent integer; negative boolean; zero_value boolean;
BEGIN
 IF encoded IS NULL OR octet_length(encoded)<>16 OR encoded !~ '^[0-9a-f]{16}$'
  OR rule IS NULL OR rule NOT IN ('unit','signed','nonnegative','plasticity') THEN
  RAISE EXCEPTION 'SOURCE3_FLOAT_BITS' USING ERRCODE='23514'; END IF;
 bits:=decode(encoded,'hex'); negative:=get_byte(bits,0)>=128;
 magnitude:=set_byte(bits,0,get_byte(bits,0)&127);
 exponent:=((get_byte(magnitude,0)&127)<<4)|(get_byte(magnitude,1)>>4);
 zero_value:=magnitude=decode('0000000000000000','hex');
 IF exponent=2047
  OR (rule<>'signed' AND negative AND NOT zero_value)
  OR (rule IN ('unit','signed') AND magnitude>decode('3ff0000000000000','hex'))
  OR (rule='plasticity' AND (negative OR magnitude<decode('3fe0000000000000','hex')
      OR magnitude>decode('4000000000000000','hex'))) THEN
  RAISE EXCEPTION 'SOURCE3_NUMBER' USING ERRCODE='23514'; END IF;
 -- Both zero signs survive. No decimal parsing or floating arithmetic occurs.
 RETURN bits;
END $$;

CREATE FUNCTION app_private.native_source3_float_matches_v3(encoded jsonb,
 actual double precision,rule text,nullable boolean) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
 IF nullable IS NULL OR rule IS NULL
  OR rule NOT IN ('unit','signed','nonnegative','plasticity') THEN
  RAISE EXCEPTION 'SOURCE3_NUMBER' USING ERRCODE='23514'; END IF;
 IF encoded='null'::jsonb THEN RETURN nullable AND actual IS NULL; END IF;
 IF jsonb_typeof(encoded) IS DISTINCT FROM 'string' OR actual IS NULL THEN RETURN false; END IF;
 RETURN app_private.native_source3_float_bits_v3(encoded#>>'{}',rule)=float8send(actual);
END $$;

CREATE FUNCTION app_private.native_source3_string_v3(raw bytea,p_offset integer)
RETURNS TABLE(next_position integer,decoded_value text)
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE start integer:=p_offset; current integer; token bytea; decoded jsonb;
BEGIN
 IF raw IS NULL OR p_offset IS NULL OR p_offset<0 OR p_offset>=octet_length(raw)
  OR get_byte(raw,p_offset)<>34 THEN
  RAISE EXCEPTION 'SOURCE3_STRING' USING ERRCODE='23514'; END IF;
 p_offset:=p_offset+1;
 LOOP
  IF p_offset>=octet_length(raw) OR p_offset-start>24577 THEN
   RAISE EXCEPTION 'SOURCE3_STRING_BOUND' USING ERRCODE='23514'; END IF;
  current:=get_byte(raw,p_offset); p_offset:=p_offset+1;
  IF current=34 THEN EXIT; END IF;
  IF current<32 THEN RAISE EXCEPTION 'SOURCE3_STRING' USING ERRCODE='23514'; END IF;
  IF current=92 THEN
   IF p_offset>=octet_length(raw) THEN
    RAISE EXCEPTION 'SOURCE3_STRING' USING ERRCODE='23514'; END IF;
   p_offset:=p_offset+1;
  END IF;
 END LOOP;
 -- Only this bounded token is hydrated; source strings preserve Unicode form.
 token:=substring(raw FROM start+1 FOR p_offset-start);
 decoded:=convert_from(token,'UTF8')::jsonb;
 IF jsonb_typeof(decoded) IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION 'SOURCE3_STRING' USING ERRCODE='23514'; END IF;
 decoded_value:=decoded#>>'{}';
 IF octet_length(convert_to(decoded_value,'UTF8'))>4096
  OR convert_to(to_jsonb(decoded_value)::text,'UTF8') IS DISTINCT FROM token THEN
  RAISE EXCEPTION 'SOURCE3_STRING_CANONICAL' USING ERRCODE='23514'; END IF;
 next_position:=p_offset; RETURN NEXT;
END $$;

CREATE FUNCTION app_private.native_source3_scalar_v3(value jsonb,rule text) RETURNS void
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE kind text:=jsonb_typeof(value); text_value text; integer_value bigint;
 expected jsonb; lower_bound bigint; upper_bound bigint;
BEGIN
 IF rule IS NULL THEN RAISE EXCEPTION 'SOURCE3_PATH' USING ERRCODE='23514'; END IF;
 IF right(rule,1)='?' THEN
  IF kind='null' THEN RETURN; END IF; rule:=left(rule,length(rule)-1);
 END IF;
 expected:=app_private.native_source3_literals_v3()->rule;
 IF expected IS NOT NULL THEN
  IF value IS DISTINCT FROM expected OR kind IS DISTINCT FROM jsonb_typeof(expected) THEN
   RAISE EXCEPTION 'SOURCE3_LITERAL' USING ERRCODE='23514'; END IF;
 ELSIF app_private.native_source3_enums_v3() ? rule THEN
  IF kind IS DISTINCT FROM 'string' OR NOT (app_private.native_source3_enums_v3()->rule ?
      (value#>>'{}')) THEN RAISE EXCEPTION 'SOURCE3_ENUM' USING ERRCODE='23514'; END IF;
 ELSIF rule IN ('unit','signed','nonnegative','plasticity') THEN
  IF kind IS DISTINCT FROM 'string' THEN
   RAISE EXCEPTION 'SOURCE3_FLOAT_BITS' USING ERRCODE='23514'; END IF;
  PERFORM app_private.native_source3_float_bits_v3(value#>>'{}',rule);
 ELSIF rule IN ('q','p','t','rank','format') THEN
  text_value:=value::text;
  IF kind IS DISTINCT FROM 'number' OR text_value !~ '^(0|[1-9][0-9]*)$'
   OR octet_length(text_value)>16 THEN
   RAISE EXCEPTION 'SOURCE3_INTEGER' USING ERRCODE='23514'; END IF;
  integer_value:=text_value::bigint;
  lower_bound:=CASE WHEN rule IN ('p','rank') THEN 1 WHEN rule='format' THEN 2 ELSE 0 END;
  upper_bound:=CASE WHEN rule='t' THEN 5000 WHEN rule='rank' THEN 100
   WHEN rule='format' THEN 3 ELSE 9007199254740991 END;
  IF integer_value NOT BETWEEN lower_bound AND upper_bound THEN
   RAISE EXCEPTION 'SOURCE3_INTEGER' USING ERRCODE='23514'; END IF;
 ELSIF rule='b' THEN
  IF kind IS DISTINCT FROM 'boolean' THEN
   RAISE EXCEPTION 'SOURCE3_BOOLEAN' USING ERRCODE='23514'; END IF;
 ELSIF rule IN ('s','dimensionkey','u','d') THEN
  IF kind IS DISTINCT FROM 'string' THEN
   RAISE EXCEPTION 'SOURCE3_STRING' USING ERRCODE='23514'; END IF;
  text_value:=value#>>'{}';
  IF (rule='dimensionkey' AND char_length(text_value) NOT BETWEEN 1 AND 200)
   OR (rule='u' AND text_value !~
    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
   OR (rule='d' AND text_value !~ '^[0-9a-f]{64}$') THEN
   RAISE EXCEPTION 'SOURCE3_SCALAR' USING ERRCODE='23514'; END IF;
 ELSIF rule='reason' THEN
  IF value IS DISTINCT FROM 'null'::jsonb AND value IS DISTINCT FROM '"UNIVERSE_OVER_CAP"'::jsonb
   THEN RAISE EXCEPTION 'SOURCE3_REASON' USING ERRCODE='23514'; END IF;
 ELSE RAISE EXCEPTION 'SOURCE3_CONTAINER_PATH' USING ERRCODE='23514';
 END IF;
END $$;

CREATE FUNCTION app_private.native_source3_scan_v3(raw bytea,p_offset integer,
 path text DEFAULT '$',depth integer DEFAULT 0) RETURNS bigint[]
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE current integer; start integer:=p_offset; count integer:=0; nodes bigint:=1;
 jsonb_bound bigint:=0; key_text text; child bigint[]; keys jsonb; array_spec jsonb;
 role text; capacity integer; token text; scalar jsonb; string_row record;
 previous_uuid text:=NULL; current_uuid text; child_start integer; index integer;
 counts bigint[]:=ARRAY[0,0,0,0,0,0,0]::bigint[];
 causal_token bytea; request_offset integer;
BEGIN
 IF raw IS NULL OR octet_length(raw) NOT BETWEEN 1 AND 2097152
  OR path IS NULL OR octet_length(path)>256
  OR depth IS NULL OR depth>12 OR depth<0 OR p_offset IS NULL
  OR p_offset<0 OR p_offset>=octet_length(raw) THEN
  RAISE EXCEPTION 'SOURCE3_BOUND' USING ERRCODE='23514'; END IF;
 current:=get_byte(raw,p_offset); role:=app_private.native_source3_rules_v3()->>path;
 IF role IS NULL THEN RAISE EXCEPTION 'SOURCE3_PATH' USING ERRCODE='23514'; END IF;
 IF role='object?' AND substring(raw FROM p_offset+1 FOR 4)=convert_to('null','UTF8') THEN
  RETURN ARRAY[(p_offset+4)::bigint,1::bigint,0::bigint]||counts; END IF;
 keys:=app_private.native_source3_shapes_v3()->path;
 array_spec:=app_private.native_source3_arrays_v3()->path;
 IF keys IS NOT NULL THEN
  IF current<>123 THEN RAISE EXCEPTION 'SOURCE3_CONTAINER' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1; jsonb_bound:=4;
  IF p_offset>=octet_length(raw) THEN
   RAISE EXCEPTION 'SOURCE3_JSON' USING ERRCODE='23514'; END IF;
  IF get_byte(raw,p_offset)<>125 THEN
   LOOP
    -- Exact installed ASCII key order closes both unknown and duplicate keys.
    IF count>=jsonb_array_length(keys) THEN
     RAISE EXCEPTION 'SOURCE3_KEYS' USING ERRCODE='23514'; END IF;
    SELECT * INTO string_row FROM app_private.native_source3_string_v3(raw,p_offset);
    key_text:=string_row.decoded_value; p_offset:=string_row.next_position;
    IF key_text IS DISTINCT FROM keys->>count THEN
     RAISE EXCEPTION 'SOURCE3_KEYS' USING ERRCODE='23514'; END IF;
    count:=count+1;
    IF p_offset>=octet_length(raw) OR get_byte(raw,p_offset)<>58 THEN
     RAISE EXCEPTION 'SOURCE3_JSON' USING ERRCODE='23514'; END IF;
    child:=app_private.native_source3_scan_v3(raw,p_offset+1,path||'.'||key_text,depth+1);
    p_offset:=child[1]::integer; nodes:=nodes+2+child[2];
    FOR index IN 1..7 LOOP counts[index]:=counts[index]+child[index+3]; END LOOP;
    -- Two JEntries, key UTF8 and conservative four-byte child alignment.
    jsonb_bound:=jsonb_bound+11+octet_length(key_text)+child[3];
    IF p_offset>=octet_length(raw) THEN
     RAISE EXCEPTION 'SOURCE3_JSON' USING ERRCODE='23514'; END IF;
    EXIT WHEN get_byte(raw,p_offset)<>44; p_offset:=p_offset+1;
   END LOOP;
  END IF;
  IF get_byte(raw,p_offset)<>125 OR count<>jsonb_array_length(keys) THEN
   RAISE EXCEPTION 'SOURCE3_KEYS' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1;
 ELSIF array_spec IS NOT NULL THEN
  IF current<>91 THEN RAISE EXCEPTION 'SOURCE3_CONTAINER' USING ERRCODE='23514'; END IF;
  capacity:=(array_spec->>0)::integer; p_offset:=p_offset+1; jsonb_bound:=4;
  IF p_offset>=octet_length(raw) THEN
   RAISE EXCEPTION 'SOURCE3_JSON' USING ERRCODE='23514'; END IF;
  IF get_byte(raw,p_offset)<>93 THEN
   LOOP
    count:=count+1; IF count>capacity THEN
     RAISE EXCEPTION 'SOURCE3_ARRAY_COUNT' USING ERRCODE='23514'; END IF;
    child_start:=p_offset;
    IF path IN ('$.candidate_recording_ids','$.temporal_snapshot.source_event_ids') THEN
     -- Both installed arrays contain only canonical, quoted, lowercase ASCII UUIDs.
     -- The ordinary scalar scan charges two nodes and 7+36 bytes per child.
     IF depth>=12 OR p_offset+38>octet_length(raw) OR get_byte(raw,p_offset)<>34
      OR get_byte(raw,p_offset+37)<>34 THEN
      RAISE EXCEPTION 'SOURCE3_SCALAR' USING ERRCODE='23514'; END IF;
     current_uuid:=convert_from(substring(raw FROM p_offset+2 FOR 36),'UTF8');
     IF current_uuid COLLATE "C" !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      THEN RAISE EXCEPTION 'SOURCE3_SCALAR' USING ERRCODE='23514'; END IF;
     p_offset:=p_offset+38; nodes:=nodes+2; jsonb_bound:=jsonb_bound+43;
     IF previous_uuid IS NOT NULL AND current_uuid COLLATE "C"<=previous_uuid COLLATE "C" THEN
      RAISE EXCEPTION 'SOURCE3_UUID_ORDER' USING ERRCODE='23514'; END IF;
     previous_uuid:=current_uuid;
    ELSE
     child:=app_private.native_source3_scan_v3(raw,p_offset,path||'[]',depth+1);
     p_offset:=child[1]::integer; nodes:=nodes+1+child[2]; jsonb_bound:=jsonb_bound+7+child[3];
     FOR index IN 1..7 LOOP counts[index]:=counts[index]+child[index+3]; END LOOP;
    END IF;
    IF path='$.temporal_snapshot.artifact_authority.causal_requests' THEN
     -- A closed seven-member causal descriptor is <1024 bytes. Locate its
     -- request UUID in that bounded raw fragment; hydrate only the UUID token.
     IF p_offset-child_start>1024 THEN
      RAISE EXCEPTION 'SOURCE3_CAUSAL_BOUND' USING ERRCODE='23514'; END IF;
     causal_token:=substring(raw FROM child_start+1 FOR p_offset-child_start);
     request_offset:=position(convert_to('"request_id":','UTF8') IN causal_token);
     IF request_offset<1 THEN
      RAISE EXCEPTION 'SOURCE3_KEYS' USING ERRCODE='23514'; END IF;
     SELECT * INTO string_row FROM app_private.native_source3_string_v3(
      raw,child_start+request_offset+12);
     current_uuid:=string_row.decoded_value;
     IF previous_uuid IS NOT NULL AND current_uuid COLLATE "C"<=previous_uuid COLLATE "C" THEN
      RAISE EXCEPTION 'SOURCE3_UUID_ORDER' USING ERRCODE='23514'; END IF;
     previous_uuid:=current_uuid;
    END IF;
    IF p_offset>=octet_length(raw) THEN
     RAISE EXCEPTION 'SOURCE3_JSON' USING ERRCODE='23514'; END IF;
    EXIT WHEN get_byte(raw,p_offset)<>44; p_offset:=p_offset+1;
   END LOOP;
  END IF;
  IF get_byte(raw,p_offset)<>93 THEN
   RAISE EXCEPTION 'SOURCE3_JSON' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1;
  CASE path
   WHEN '$.candidate_recording_ids' THEN counts[3]:=count;
   WHEN '$.temporal_snapshot.facts' THEN counts[4]:=count;
   WHEN '$.temporal_snapshot.source_evidence' THEN counts[5]:=count;
   WHEN '$.temporal_snapshot.source_event_ids' THEN counts[6]:=count;
   WHEN '$.temporal_snapshot.adaptive_profile.dimensions' THEN counts[7]:=count;
   ELSE NULL;
  END CASE;
 ELSIF current=34 THEN
  SELECT * INTO string_row FROM app_private.native_source3_string_v3(raw,p_offset);
  p_offset:=string_row.next_position; scalar:=to_jsonb(string_row.decoded_value);
  jsonb_bound:=octet_length(convert_to(string_row.decoded_value,'UTF8'));
  PERFORM app_private.native_source3_scalar_v3(scalar,role);
 ELSIF current IN (48,49,50,51,52,53,54,55,56,57) THEN
  WHILE p_offset<octet_length(raw) AND get_byte(raw,p_offset) BETWEEN 48 AND 57 LOOP
   p_offset:=p_offset+1;
   IF p_offset-start>16 THEN
    RAISE EXCEPTION 'SOURCE3_INTEGER' USING ERRCODE='23514'; END IF;
  END LOOP;
  token:=convert_from(substring(raw FROM start+1 FOR p_offset-start),'UTF8');
  IF token !~ '^(0|[1-9][0-9]*)$' THEN
   RAISE EXCEPTION 'SOURCE3_INTEGER' USING ERRCODE='23514'; END IF;
  scalar:=to_jsonb(token::bigint); PERFORM app_private.native_source3_scalar_v3(scalar,role);
  jsonb_bound:=32;
  IF path='$.source_snapshot_track_count' THEN counts[1]:=token::bigint;
  ELSIF path='$.candidate_universe_count' THEN counts[2]:=token::bigint; END IF;
 ELSIF current=110 AND substring(raw FROM p_offset+1 FOR 4)=convert_to('null','UTF8') THEN
  p_offset:=p_offset+4; PERFORM app_private.native_source3_scalar_v3('null'::jsonb,role);
 ELSIF current=116 AND substring(raw FROM p_offset+1 FOR 4)=convert_to('true','UTF8') THEN
  p_offset:=p_offset+4; PERFORM app_private.native_source3_scalar_v3('true'::jsonb,role);
 ELSIF current=102 AND substring(raw FROM p_offset+1 FOR 5)=convert_to('false','UTF8') THEN
  p_offset:=p_offset+5; PERFORM app_private.native_source3_scalar_v3('false'::jsonb,role);
 ELSE RAISE EXCEPTION 'SOURCE3_JSON' USING ERRCODE='23514';
 END IF;
 RETURN ARRAY[p_offset::bigint,nodes,jsonb_bound]||counts;
END $$;

CREATE FUNCTION app_private.native_source3_preflight_v3(raw bytea,
 remaining_byte_work bigint,remaining_node_visits bigint)
RETURNS bigint[]
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE result bigint[]; raw_size bigint; byte_work bigint; node_visits bigint;
BEGIN
 IF raw IS NULL OR octet_length(raw) NOT BETWEEN 1 AND 2097152 THEN
  RAISE EXCEPTION 'SOURCE3_BYTES' USING ERRCODE='23514'; END IF;
 raw_size:=octet_length(raw);
 byte_work:=144*raw_size+16777216; node_visits:=16*(raw_size+1);
 -- The owner precharges these full bounds even when this invocation fails.
 IF remaining_byte_work IS NULL OR remaining_node_visits IS NULL
  OR remaining_byte_work<byte_work OR remaining_node_visits<node_visits THEN
  RAISE EXCEPTION 'SOURCE3_WORK_BOUND' USING ERRCODE='23514'; END IF;
 result:=app_private.native_source3_scan_v3(raw,0);
 IF result[1]<>raw_size THEN
  RAISE EXCEPTION 'SOURCE3_TRAILING' USING ERRCODE='23514'; END IF;
 -- This phase retains raw plus bounded token/schema/recursive scratch only.
 -- The returned JSONB estimate is not permission to hydrate a whole document.
 IF result[2]*16>node_visits OR raw_size+1048576>8388608 THEN
  RAISE EXCEPTION 'SOURCE3_LOGICAL_BOUND' USING ERRCODE='23514'; END IF;
 -- Raw scan slots4..10 hold T,C,candidate-count,E,F,source-count,D.
 IF result[5]>result[4] OR result[6]<>result[5] OR result[7]<>result[9]
  OR result[8]>2*result[7] THEN
  RAISE EXCEPTION 'SOURCE3_COUNTS' USING ERRCODE='23514'; END IF;
 -- The owner must separately prepay and bound all live copies of any later
 -- convert_from(raw,'UTF8')::jsonb. No whole-source JSON projection occurs here.
 RETURN ARRAY[raw_size,node_visits,byte_work,result[3]];
END $$;

REVOKE ALL ON FUNCTION app_private.native_source3_shapes_v3(),
 app_private.native_source3_arrays_v3(),app_private.native_source3_rules_v3(),
 app_private.native_source3_literals_v3(),app_private.native_source3_enums_v3(),
 app_private.native_source3_float_bits_v3(text,text),
 app_private.native_source3_float_matches_v3(jsonb,double precision,text,boolean),
 app_private.native_source3_string_v3(bytea,integer),
 app_private.native_source3_scalar_v3(jsonb,text),
 app_private.native_source3_scan_v3(bytea,integer,text,integer),
 app_private.native_source3_preflight_v3(bytea,bigint,bigint) FROM PUBLIC;
"""
)

NATIVE_ORIGINAL_CAPTURE_V3_DROP_SQL = r"""
DROP FUNCTION app_private.native_source3_preflight_v3(bytea,bigint,bigint);
DROP FUNCTION app_private.native_source3_scan_v3(bytea,integer,text,integer);
DROP FUNCTION app_private.native_source3_scalar_v3(jsonb,text);
DROP FUNCTION app_private.native_source3_string_v3(bytea,integer);
DROP FUNCTION app_private.native_source3_float_matches_v3(jsonb,double precision,text,boolean);
DROP FUNCTION app_private.native_source3_float_bits_v3(text,text);
DROP FUNCTION app_private.native_source3_enums_v3();
DROP FUNCTION app_private.native_source3_literals_v3();
DROP FUNCTION app_private.native_source3_rules_v3();
DROP FUNCTION app_private.native_source3_arrays_v3();
DROP FUNCTION app_private.native_source3_shapes_v3();
"""
