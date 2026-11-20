"""Raw original numeric R1 profile preflight, with no whole JSONB projection.

The eight-key original RFC8785 bytes and their raw SHA remain independent of
Source3 F64BITS and the separate binary64 identity. Numeric zero has numeric
meaning here; this scanner makes no signed-zero identity claim. These SQL
functions install only when an explicit owner executes INSTALL_SQL.

Remaining-work arguments check an already admitted caller allowance. They do
not create quota, prove its provenance or authorize a later whole projection.
The compiler bound is a logical copy bound, not PostgreSQL process memory.
"""

from typing import Final

NATIVE_ORIGINAL_PROFILE_V3_MAX_BYTES: Final = 2_097_152
NATIVE_ORIGINAL_PROFILE_V3_COMPILER_LIMIT: Final = 8 * 1024 * 1024
NATIVE_ORIGINAL_PROFILE_V3_SCRATCH_BOUND: Final = 64 * 1024
NATIVE_ORIGINAL_PROFILE_V3_BYTE_WORK_FACTOR: Final = 144
NATIVE_ORIGINAL_PROFILE_V3_FIXED_BYTE_WORK: Final = 16 * 1024 * 1024
NATIVE_ORIGINAL_PROFILE_V3_NODE_VISIT_FACTOR: Final = 16
NATIVE_ORIGINAL_PROFILE_V3_STAT_DOCUMENT_BYTES: Final = 1
NATIVE_ORIGINAL_PROFILE_V3_STAT_NODE_VISITS: Final = 2
NATIVE_ORIGINAL_PROFILE_V3_STAT_BYTE_WORK: Final = 3
NATIVE_ORIGINAL_PROFILE_V3_STAT_COMPILER_UPPER: Final = 4
NATIVE_ORIGINAL_PROFILE_V3_STAT_DIMENSIONS: Final = 5
NATIVE_ORIGINAL_PROFILE_V3_STAT_SOURCE_REFERENCES: Final = 6
NATIVE_ORIGINAL_PROFILE_V3_STAT_NUMBERS: Final = 7
NATIVE_ORIGINAL_PROFILE_V3_STAT_SELF_MEMBER_START: Final = 8
NATIVE_ORIGINAL_PROFILE_V3_STAT_SELF_MEMBER_END: Final = 9
NATIVE_ORIGINAL_PROFILE_V3_STAT_SELF_HEX_START: Final = 10

NATIVE_ORIGINAL_PROFILE_V3_KEYS: Final = (
    "cutoff_at_ms",
    "dimensions",
    "feature_policy",
    "interaction_watermark",
    "maturity",
    "owner_user_id",
    "profile_sha256",
    "schema_version",
)
NATIVE_ORIGINAL_PROFILE_V3_MATURITY_KEYS: Final = (
    "artist_coverage",
    "consistency",
    "effective_signal_mass",
    "observation_span_ms",
    "organic_share",
    "plasticity_multiplier",
    "recency",
    "recency_age_ms",
    "score",
    "track_coverage",
)
NATIVE_ORIGINAL_PROFILE_V3_DIMENSION_KEYS: Final = (
    "fatigue",
    "key",
    "kind",
    "long_term",
    "momentum",
    "persistence",
    "recent",
    "recent_adjustment",
    "source_event_ids",
)
NATIVE_ORIGINAL_PROFILE_V3_RECENT_KEYS: Final = ("D3", "D7", "EPISODE", "H12", "H24")
NATIVE_ORIGINAL_PROFILE_V3_HORIZON_KEYS: Final = ("confidence", "evidence_mass", "score")


def _array(values: tuple[str, ...]) -> str:
    # Installed ASCII inventory only, never an input profile canonicalizer.
    return "ARRAY[" + ",".join("'" + value + "'" for value in values) + "]::text[]"


_SHAPES: Final = {
    "$": NATIVE_ORIGINAL_PROFILE_V3_KEYS,
    "$.feature_policy": ("content_sha256", "key", "version"),
    "$.maturity": NATIVE_ORIGINAL_PROFILE_V3_MATURITY_KEYS,
    "$.dimensions[]": NATIVE_ORIGINAL_PROFILE_V3_DIMENSION_KEYS,
    "$.dimensions[].recent": NATIVE_ORIGINAL_PROFILE_V3_RECENT_KEYS,
    "$.dimensions[].long_term": NATIVE_ORIGINAL_PROFILE_V3_HORIZON_KEYS,
    **{
        "$.dimensions[].recent." + key: NATIVE_ORIGINAL_PROFILE_V3_HORIZON_KEYS
        for key in NATIVE_ORIGINAL_PROFILE_V3_RECENT_KEYS
    },
}
_RULES: Final = {
    **dict.fromkeys(_SHAPES, "object"),
    "$.dimensions": "array",
    "$.dimensions[].source_event_ids": "array",
    "$.dimensions[].source_event_ids[]": "uuid",
    "$.owner_user_id": "uuid",
    "$.profile_sha256": "hex",
    "$.feature_policy.content_sha256": "hex",
    "$.feature_policy.key": "text",
    "$.feature_policy.version": "text",
    "$.dimensions[].key": "text",
    "$.dimensions[].kind": "kind",
    "$.cutoff_at_ms": "safe",
    "$.interaction_watermark": "safe",
    "$.schema_version": "one",
    **{
        "$.maturity." + key: "i64"
        for key in ("artist_coverage", "track_coverage", "observation_span_ms", "recency_age_ms")
    },
    **{
        "$.maturity." + key: rule
        for key, rule in (
            ("score", "unit"),
            ("effective_signal_mass", "nonnegative"),
            ("recency", "unit"),
            ("consistency", "unit"),
            ("organic_share", "unit"),
            ("plasticity_multiplier", "plasticity"),
        )
    },
    **{
        path + "." + key: rule
        for path in (
            "$.dimensions[].long_term",
            *("$.dimensions[].recent." + key for key in NATIVE_ORIGINAL_PROFILE_V3_RECENT_KEYS),
        )
        for key, rule in (
            ("score", "signed"),
            ("confidence", "unit"),
            ("evidence_mass", "nonnegative"),
        )
    },
    **{
        "$.dimensions[]." + key: rule
        for key, rule in (
            ("fatigue", "unit"),
            ("momentum", "signed"),
            ("persistence", "unit"),
            ("recent_adjustment", "signed"),
        )
    },
}

NATIVE_ORIGINAL_PROFILE_V3_INSTALL_SQL: Final = (
    "CREATE FUNCTION app_private.native_original_profile_shape_v3(path text) RETURNS text[] "
    "LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT CASE path "
    + " ".join("WHEN '" + path + "' THEN " + _array(keys) for path, keys in _SHAPES.items())
    + " ELSE NULL END $$;\n"
    "CREATE FUNCTION app_private.native_original_profile_rule_v3(path text) RETURNS text "
    "LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT CASE path "
    + " ".join("WHEN '" + path + "' THEN '" + rule + "'" for path, rule in _RULES.items())
    + " ELSE NULL END $$;\n"
    + r"""
CREATE FUNCTION app_private.native_original_profile_string_v3(raw bytea,p_offset integer)
RETURNS TABLE(next_position integer,decoded_value text)
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $profile_string$
DECLARE start integer:=p_offset; current integer; escaped integer; hex text;
 token bytea; decoded jsonb;
BEGIN
 IF raw IS NULL OR p_offset IS NULL OR p_offset<0 OR p_offset>=octet_length(raw)
  OR get_byte(raw,p_offset)<>34 THEN
  RAISE EXCEPTION 'PROFILE3_STRING' USING ERRCODE='23514'; END IF;
 p_offset:=p_offset+1;
 LOOP
  IF p_offset>=octet_length(raw) OR p_offset-start>1201 THEN
   RAISE EXCEPTION 'PROFILE3_STRING_BOUND' USING ERRCODE='23514'; END IF;
  current:=get_byte(raw,p_offset); p_offset:=p_offset+1;
  IF current=34 THEN EXIT; END IF;
  IF current<32 THEN RAISE EXCEPTION 'PROFILE3_STRING' USING ERRCODE='23514'; END IF;
  IF current=92 THEN
   IF p_offset>=octet_length(raw) THEN
    RAISE EXCEPTION 'PROFILE3_STRING' USING ERRCODE='23514'; END IF;
   escaped:=get_byte(raw,p_offset); p_offset:=p_offset+1;
   IF escaped=117 THEN
    IF p_offset+4>octet_length(raw) THEN
     RAISE EXCEPTION 'PROFILE3_STRING' USING ERRCODE='23514'; END IF;
    hex:=convert_from(substring(raw FROM p_offset+1 FOR 4),'UTF8');
    IF hex !~ '^00[01][0-9a-f]$' OR hex IN ('0000','0008','0009','000a','000c','000d') THEN
     RAISE EXCEPTION 'PROFILE3_STRING_ESCAPE' USING ERRCODE='23514'; END IF;
    p_offset:=p_offset+4;
   ELSIF escaped NOT IN (34,92,98,102,110,114,116) THEN
    RAISE EXCEPTION 'PROFILE3_STRING_ESCAPE' USING ERRCODE='23514';
   END IF;
  END IF;
 END LOOP;
 token:=substring(raw FROM start+1 FOR p_offset-start);
 -- Only one bounded string token is parsed. No whole document or numeric
 -- token is cast here, and no JSONB text/float output acts as a canonicalizer.
 decoded:=convert_from(token,'UTF8')::jsonb;
 decoded_value:=decoded#>>'{}';
 IF jsonb_typeof(decoded) IS DISTINCT FROM 'string' OR length(decoded_value)>200
  OR octet_length(convert_to(decoded_value,'UTF8'))>800 THEN
  RAISE EXCEPTION 'PROFILE3_STRING_BOUND' USING ERRCODE='23514'; END IF;
 next_position:=p_offset; RETURN NEXT;
EXCEPTION WHEN character_not_in_repertoire OR invalid_text_representation THEN
 RAISE EXCEPTION 'PROFILE3_STRING' USING ERRCODE='23514';
END; $profile_string$;

CREATE FUNCTION app_private.native_original_profile_scan_v3(raw bytea,p_offset integer,
 path text DEFAULT '$',depth integer DEFAULT 0) RETURNS bigint[]
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $profile_scan$
DECLARE
 start integer:=p_offset; current integer; count integer:=0; capacity integer;
 keys text[]; role text; key_text text; key_token bytea; member_start integer;
 nodes bigint:=1; jsonb_bound bigint:=0; dimensions bigint:=0; refs bigint:=0;
 numbers bigint:=0; self_start bigint:=0; self_end bigint:=0; self_hex bigint:=0;
 child bigint[]; string_row record; token text; mantissa text; exponent_text text;
 exponent integer:=0; exponent_position integer; digits integer; number double precision;
 integer_value bigint; lower_bound double precision; upper_bound double precision;
BEGIN
 IF raw IS NULL OR octet_length(raw) NOT BETWEEN 1 AND 2097152
  OR path IS NULL OR octet_length(path)>160 OR depth IS NULL OR depth NOT BETWEEN 0 AND 6
  OR p_offset IS NULL OR p_offset<0 OR p_offset>=octet_length(raw) THEN
  RAISE EXCEPTION 'PROFILE3_BOUND' USING ERRCODE='23514'; END IF;
 role:=app_private.native_original_profile_rule_v3(path);
 IF role IS NULL THEN RAISE EXCEPTION 'PROFILE3_PATH' USING ERRCODE='23514'; END IF;
 current:=get_byte(raw,p_offset);
 keys:=app_private.native_original_profile_shape_v3(path);
 IF keys IS NOT NULL THEN
  IF current<>123 THEN RAISE EXCEPTION 'PROFILE3_CONTAINER' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1; jsonb_bound:=4;
  IF p_offset>=octet_length(raw) THEN
   RAISE EXCEPTION 'PROFILE3_JSON' USING ERRCODE='23514'; END IF;
  IF get_byte(raw,p_offset)<>125 THEN
   LOOP
    count:=count+1;
    IF count>cardinality(keys) THEN
     RAISE EXCEPTION 'PROFILE3_KEYS' USING ERRCODE='23514'; END IF;
    key_text:=keys[count]; member_start:=p_offset;
    key_token:=convert_to('"'||key_text||'":','UTF8');
    IF substring(raw FROM p_offset+1 FOR octet_length(key_token)) IS DISTINCT FROM key_token THEN
     RAISE EXCEPTION 'PROFILE3_KEYS' USING ERRCODE='23514'; END IF;
    p_offset:=p_offset+octet_length(key_token);
    child:=app_private.native_original_profile_scan_v3(raw,p_offset,path||'.'||key_text,depth+1);
    IF path='$' AND key_text='profile_sha256' THEN
     self_start:=member_start-1; self_end:=child[1]; self_hex:=p_offset+1;
    END IF;
    p_offset:=child[1]::integer; nodes:=nodes+2+child[2];
    jsonb_bound:=jsonb_bound+11+octet_length(key_text)+child[3];
    dimensions:=dimensions+child[4]; refs:=refs+child[5]; numbers:=numbers+child[6];
    IF p_offset>=octet_length(raw) THEN
     RAISE EXCEPTION 'PROFILE3_JSON' USING ERRCODE='23514'; END IF;
    EXIT WHEN get_byte(raw,p_offset)<>44; p_offset:=p_offset+1;
   END LOOP;
  END IF;
  IF p_offset>=octet_length(raw) OR get_byte(raw,p_offset)<>125 OR count<>cardinality(keys) THEN
   RAISE EXCEPTION 'PROFILE3_KEYS' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1;
 ELSIF role='array' THEN
  capacity:=CASE path WHEN '$.dimensions' THEN 512
   WHEN '$.dimensions[].source_event_ids' THEN 64 ELSE 0 END;
  IF current<>91 OR capacity=0 THEN
   RAISE EXCEPTION 'PROFILE3_CONTAINER' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1; jsonb_bound:=4;
  IF p_offset>=octet_length(raw) THEN
   RAISE EXCEPTION 'PROFILE3_JSON' USING ERRCODE='23514'; END IF;
  IF get_byte(raw,p_offset)<>93 THEN
   LOOP
    count:=count+1;
    IF count>capacity THEN
     RAISE EXCEPTION 'PROFILE3_ARRAY_COUNT' USING ERRCODE='23514'; END IF;
    child:=app_private.native_original_profile_scan_v3(raw,p_offset,path||'[]',depth+1);
    p_offset:=child[1]::integer; nodes:=nodes+1+child[2]; jsonb_bound:=jsonb_bound+7+child[3];
    dimensions:=dimensions+child[4]; refs:=refs+child[5]; numbers:=numbers+child[6];
    IF p_offset>=octet_length(raw) THEN
     RAISE EXCEPTION 'PROFILE3_JSON' USING ERRCODE='23514'; END IF;
    EXIT WHEN get_byte(raw,p_offset)<>44; p_offset:=p_offset+1;
   END LOOP;
  END IF;
  IF p_offset>=octet_length(raw) OR get_byte(raw,p_offset)<>93 THEN
   RAISE EXCEPTION 'PROFILE3_JSON' USING ERRCODE='23514'; END IF;
  p_offset:=p_offset+1;
  IF path='$.dimensions' THEN dimensions:=count; ELSE refs:=refs+count; END IF;
 ELSIF current=34 AND role IN ('uuid','hex','kind','text') THEN
  SELECT * INTO string_row FROM app_private.native_original_profile_string_v3(raw,p_offset);
  p_offset:=string_row.next_position; token:=string_row.decoded_value;
  IF (role='uuid' AND (length(token)<>36 OR token::uuid::text COLLATE "C"<>token COLLATE "C"))
   OR (role='hex' AND token !~ '^[0-9a-f]{64}$')
   OR (role='kind' AND token COLLATE "C" NOT IN
    ('CANONICAL_ARTIST_ID','CANONICAL_RELEASE_ID','BOUNDED_METADATA_TOKEN'))
   OR (role='text' AND length(token)<1) THEN
   RAISE EXCEPTION 'PROFILE3_SCALAR' USING ERRCODE='23514'; END IF;
  jsonb_bound:=octet_length(convert_to(token,'UTF8'));
 ELSIF current=45 OR current BETWEEN 48 AND 57 THEN
  IF role NOT IN ('one','i64','safe','unit','signed','nonnegative','plasticity') THEN
   RAISE EXCEPTION 'PROFILE3_NUMBER' USING ERRCODE='23514'; END IF;
  WHILE p_offset<octet_length(raw) AND get_byte(raw,p_offset) IN
   (43,45,46,48,49,50,51,52,53,54,55,56,57,101) LOOP
   p_offset:=p_offset+1;
   IF p_offset-start>32 THEN
    RAISE EXCEPTION 'PROFILE3_NUMBER_BOUND' USING ERRCODE='23514'; END IF;
  END LOOP;
  token:=convert_from(substring(raw FROM start+1 FOR p_offset-start),'UTF8');
  IF role IN ('one','i64','safe') THEN
   IF length(token)>19 OR token !~ '^(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'PROFILE3_INTEGER' USING ERRCODE='23514'; END IF;
   integer_value:=token::bigint;
   IF (role='one' AND integer_value<>1)
    OR (role='safe' AND integer_value>9007199254740991) THEN
    RAISE EXCEPTION 'PROFILE3_INTEGER' USING ERRCODE='23514'; END IF;
   jsonb_bound:=32;
  ELSE
   IF token !~ '^-?(0|[1-9][0-9]*)([.][0-9]+)?(e[+-]?(0|[1-9][0-9]*))?$' THEN
    RAISE EXCEPTION 'PROFILE3_NUMBER' USING ERRCODE='23514'; END IF;
   exponent_position:=position('e' IN token);
   IF exponent_position>0 THEN
    exponent_text:=substring(token FROM exponent_position+1);
    IF length(exponent_text)>4 THEN
     RAISE EXCEPTION 'PROFILE3_NUMBER_BOUND' USING ERRCODE='23514'; END IF;
    exponent:=exponent_text::integer;
    IF exponent NOT BETWEEN -324 AND 308 THEN
     RAISE EXCEPTION 'PROFILE3_NUMBER_BOUND' USING ERRCODE='23514'; END IF;
   END IF;
   number:=token::double precision;
   lower_bound:=CASE role WHEN 'signed' THEN -1 WHEN 'plasticity' THEN 0.5 ELSE 0 END;
   upper_bound:=CASE role WHEN 'plasticity' THEN 2
    WHEN 'nonnegative' THEN '1.7976931348623157e308'::double precision ELSE 1 END;
   IF number NOT BETWEEN lower_bound AND upper_bound THEN
    RAISE EXCEPTION 'PROFILE3_NUMBER' USING ERRCODE='23514'; END IF;
   mantissa:=split_part(token,'e',1);
   digits:=length(replace(replace(mantissa,'.',''),'-',''));
   -- Numeric base10000 needs <=ceil((digits+abs(exponent))/4) digit pairs;
   -- 32 additional bytes cover varlena/header/alignment without expansion.
   jsonb_bound:=32+2*((digits+abs(exponent)+3)/4);
  END IF;
  numbers:=1;
 ELSE RAISE EXCEPTION 'PROFILE3_SCALAR' USING ERRCODE='23514';
 END IF;
 RETURN ARRAY[p_offset::bigint,nodes,jsonb_bound,dimensions,refs,numbers,
  self_start,self_end,self_hex];
EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation THEN
 RAISE EXCEPTION 'PROFILE3_NUMBER' USING ERRCODE='23514';
END; $profile_scan$;

CREATE FUNCTION app_private.native_original_profile_preflight_v3(raw bytea,
 remaining_byte_work bigint,remaining_node_visits bigint) RETURNS bigint[]
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $profile_preflight$
DECLARE result bigint[]; raw_size bigint; byte_work bigint; node_visits bigint; compiler bigint;
BEGIN
 IF raw IS NULL OR octet_length(raw) NOT BETWEEN 1 AND 2097152 THEN
  RAISE EXCEPTION 'PROFILE3_BYTES' USING ERRCODE='23514'; END IF;
 raw_size:=octet_length(raw); byte_work:=144*raw_size+16777216;
 node_visits:=16*(raw_size+1);
 IF remaining_byte_work IS NULL OR remaining_node_visits IS NULL
  OR remaining_byte_work<byte_work OR remaining_node_visits<node_visits THEN
  RAISE EXCEPTION 'PROFILE3_WORK_BOUND' USING ERRCODE='23514'; END IF;
 result:=app_private.native_original_profile_scan_v3(raw,0);
 IF result[1]<>raw_size THEN
  RAISE EXCEPTION 'PROFILE3_TRAILING' USING ERRCODE='23514'; END IF;
 compiler:=2*raw_size+result[3]+65536;
 IF result[2]*16>node_visits OR compiler>8388608 OR result[4]>512 OR result[5]>32768
  OR result[7]<=0 OR result[8]<=result[7] OR result[9]<=result[7] THEN
  RAISE EXCEPTION 'PROFILE3_LOGICAL_BOUND' USING ERRCODE='23514'; END IF;
 -- Removing [self_start,self_end) including its leading comma yields the
 -- exact frozen7-key original preimage. Nothing is hashed or reformatted here.
 RETURN ARRAY[raw_size,node_visits,byte_work,compiler,result[4],result[5],result[6],
  result[7],result[8],result[9]];
END; $profile_preflight$;

REVOKE ALL ON FUNCTION app_private.native_original_profile_shape_v3(text),
 app_private.native_original_profile_rule_v3(text),
 app_private.native_original_profile_string_v3(bytea,integer),
 app_private.native_original_profile_scan_v3(bytea,integer,text,integer),
 app_private.native_original_profile_preflight_v3(bytea,bigint,bigint) FROM PUBLIC;
"""
)

NATIVE_ORIGINAL_PROFILE_V3_DROP_SQL: Final = r"""
DROP FUNCTION app_private.native_original_profile_preflight_v3(bytea,bigint,bigint) RESTRICT;
DROP FUNCTION app_private.native_original_profile_scan_v3(bytea,integer,text,integer) RESTRICT;
DROP FUNCTION app_private.native_original_profile_string_v3(bytea,integer) RESTRICT;
DROP FUNCTION app_private.native_original_profile_rule_v3(text) RESTRICT;
DROP FUNCTION app_private.native_original_profile_shape_v3(text) RESTRICT;
"""
