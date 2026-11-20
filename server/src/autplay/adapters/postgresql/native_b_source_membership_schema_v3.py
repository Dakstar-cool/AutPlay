"""Closed finite signed-F membership, one B claim/RR and irreversible cursor.

Arrays transport the genuine original F issuer decision; they are not runtime
credentials. No discovery query, replacement budget or cross-window adoption
exists. Existing B16 and unique original/served limits remain authoritative.
"""

from hashlib import sha256
from types import MappingProxyType
from typing import Final

from autplay.adapters.postgresql.native_b_source_permission_schema_v3 import (
    _ACTOR,
    _FAULT,
    _FAULT_DECL,
)

_KERNEL: Final = "native_v3_b_kernel"
_OWNER: Final = "native_v3_b_catalog_owner"
_SPECS: list[tuple[str, str, str]] = []

_STORE: Final = """
CREATE TABLE native_b_private_v3.source3_member(
 ordinal smallint PRIMARY KEY CHECK(ordinal BETWEEN 1 AND 16),
 owner_user_id uuid NOT NULL,original_request_id uuid NOT NULL UNIQUE,
 served_request_id uuid NOT NULL UNIQUE,source_generation bigint NOT NULL
 CHECK(source_generation BETWEEN 1 AND 9007199254740991),
 consent_generation bigint NOT NULL,consent_receipt_sha256 bytea NOT NULL
 CHECK(octet_length(consent_receipt_sha256)=32)
);
CREATE TABLE native_b_private_v3.source3_cursor(
 singleton boolean PRIMARY KEY CHECK(singleton),window_id uuid NOT NULL,
 operation_id uuid NOT NULL,generation bigint NOT NULL,transaction_id xid8,
 position smallint NOT NULL CHECK(position BETWEEN 1 AND 16),
 member_count smallint NOT NULL CHECK(member_count BETWEEN 1 AND 16),
 phase text NOT NULL CHECK(phase IN('READY','READING','SEALED','EOF')),
 next_prepaid boolean NOT NULL DEFAULT false,CHECK(position<=member_count)
);
ALTER TABLE native_b_private_v3.source3_member OWNER TO native_v3_b_catalog_owner;
ALTER TABLE native_b_private_v3.source3_cursor OWNER TO native_v3_b_catalog_owner;
REVOKE ALL ON native_b_private_v3.source3_member,native_b_private_v3.source3_cursor
 FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer;
GRANT SELECT,INSERT,UPDATE,DELETE ON native_b_private_v3.source3_member,
 native_b_private_v3.source3_cursor TO native_v3_b_kernel;
"""


def _define(name: str, parameters: str, identity: str, result: str, body: str) -> str:
    signature = f"app_private.{name}({identity})"
    _SPECS.append((signature, result, body))
    return (
        f"CREATE FUNCTION app_private.{name}({parameters}) RETURNS {result}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog\n"
        f"AS $source3_members${body}$source3_members$;\n"
        f"ALTER FUNCTION {signature} OWNER TO {_KERNEL};\n"
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer;\n"
    )


_CLAIM: Final = _define(
    "native_b_source3_claim_selection_v3",
    "p_window uuid,p_operation uuid,p_client uuid,p_pid integer,p_start "
    "timestamptz,p_selection bytea,"
    "p_owners uuid[],p_originals uuid[],p_served uuid[],p_generations bigint[]",
    "uuid,uuid,uuid,integer,timestamp with time zone,bytea,uuid[],uuid[],uuid[],bigint[]",
    "bigint",
    "DECLARE " + _FAULT_DECL + " permission native_b_private_v3.source3_permission%ROWTYPE;"
    " v_generation bigint; n integer; i integer; c bigint; receipt bytea; "
    "previous uuid[]; current_key uuid[]; BEGIN\n"
    " PERFORM app_private._native_b_role_v3(false);\n" + _ACTOR + " n:=cardinality(p_owners);\n"
    " IF n IS NULL OR n NOT BETWEEN 1 AND 16 OR array_ndims(p_owners)<>1 OR "
    "array_lower(p_owners,1)<>1"
    " OR cardinality(p_originals) IS DISTINCT FROM n OR "
    "array_ndims(p_originals)<>1 OR array_lower(p_originals,1)<>1"
    " OR cardinality(p_served) IS DISTINCT FROM n OR array_ndims(p_served)<>1"
    " OR array_lower(p_served,1)<>1"
    " OR cardinality(p_generations) IS DISTINCT FROM n OR "
    "array_ndims(p_generations)<>1 OR array_lower(p_generations,1)<>1"
    " OR EXISTS(SELECT 1 FROM native_b_private_v3.source3_member) OR "
    "EXISTS(SELECT 1 FROM native_b_private_v3.source3_cursor) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_MEMBERSHIP_SHAPE'; END IF;\n"
    " FOR i IN 1..n LOOP\n"
    " current_key:=ARRAY[p_owners[i],p_originals[i],p_served[i]];\n"
    " IF array_position(current_key,NULL) IS NOT NULL OR p_originals[i]=p_served[i]"
    " OR p_generations[i] IS NULL OR p_generations[i] NOT BETWEEN 1 AND 9007199254740991"
    " OR(previous IS NOT NULL AND current_key<=previous)"
    " OR p_originals[i]=ANY(p_originals[1:i-1]) OR p_served[i]=ANY(p_served[1:i-1]) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_MEMBERSHIP_CANONICAL'; END IF; previous:=current_key; END LOOP;\n"
    " "
    "v_generation:=app_private.native_b_source3_claim_v3(p_window,p_operation,p_client,p_pid,p_start,"
    "p_owners[1],p_originals[1],p_served[1],p_generations[1],p_selection);\n"
    " SELECT s.* INTO STRICT v_fault_scope FROM native_b_private_v3.slot s WHERE s.singleton"
    " AND s.state='CLAIMED' AND s.generation=v_generation AND "
    "s.operation_id=p_operation AND s.client_incarnation=p_client;\n"
    " SELECT p.* INTO STRICT permission FROM "
    "native_b_private_v3.source3_permission p WHERE p.singleton;\n"
    " FOR i IN 1..n LOOP\n"
    " SELECT b.consent_generation,b.consent_receipt_sha256 INTO STRICT c,receipt"
    " FROM ml.native_original_capture_v3 b JOIN ml.native_capture_lineage_cursor_v3 k"
    " ON k.owner_user_id=b.owner_user_id AND k.original_request_id=b.original_request_id"
    " JOIN ml.recommendation_request_root_v3 r ON r.owner_user_id=b.owner_user_id"
    " AND r.original_request_id=b.original_request_id AND r.served_request_id=p_served[i]"
    " WHERE b.owner_user_id=p_owners[i] AND b.original_request_id=p_originals[i]"
    " AND k.state='ACTIVE' AND k.source_generation=p_generations[i];\n"
    " permission.owner_user_id:=p_owners[i]; permission.original_request_id:=p_originals[i];"
    " permission.served_request_id:=p_served[i]; permission.source_generation:=p_generations[i];"
    " permission.consent_generation:=c; permission.consent_receipt_sha256:=receipt;\n"
    " -- Closed current source-row/generation/consent checks for EVERY signed member.\n"
    " PERFORM app_private._native_b_source3_current_rows_v3(permission,false);\n"
    " INSERT INTO native_b_private_v3.source3_member "
    "VALUES(i::smallint,p_owners[i],p_originals[i],p_served[i],p_generations[i],c,receipt);"
    "\n"
    " END LOOP;\n"
    " INSERT INTO native_b_private_v3.source3_cursor "
    "VALUES(true,p_window,p_operation,v_generation,NULL,1,n,'READY',false);\n"
    " RETURN v_generation;\n" + _FAULT,
)

_MEMBER: Final = _define(
    "_native_b_source3_member_scope_v3",
    "p_window uuid",
    "uuid",
    "native_b_private_v3.source3_permission",
    "DECLARE " + _FAULT_DECL + " permission native_b_private_v3.source3_permission%ROWTYPE;"
    " cursor native_b_private_v3.source3_cursor%ROWTYPE; member "
    "native_b_private_v3.source3_member%ROWTYPE; BEGIN\n"
    + _ACTOR
    + " permission:=app_private._native_b_source3_bound_scope_v3(p_window);\n"
    " v_fault_scope:=app_private._native_b_data_scope_v3();\n"
    " SELECT c.* INTO STRICT cursor FROM native_b_private_v3.source3_cursor c"
    " WHERE c.singleton FOR UPDATE;\n"
    " IF cursor.window_id<>p_window OR "
    "cursor.generation<>permission.generation OR "
    "cursor.operation_id<>permission.operation_id"
    " OR cursor.phase='EOF' OR(cursor.transaction_id IS NOT NULL AND "
    "cursor.transaction_id<>pg_current_xact_id()) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_MEMBER_CURSOR_SCOPE'; END IF;\n"
    " IF cursor.transaction_id IS NULL THEN UPDATE "
    "native_b_private_v3.source3_cursor SET "
    "transaction_id=pg_current_xact_id() WHERE singleton; END IF;\n"
    " SELECT m.* INTO STRICT member FROM native_b_private_v3.source3_member m"
    " WHERE m.ordinal=cursor.position;\n"
    " permission.owner_user_id:=member.owner_user_id; "
    "permission.original_request_id:=member.original_request_id;"
    " permission.served_request_id:=member.served_request_id; "
    "permission.source_generation:=member.source_generation;"
    " permission.consent_generation:=member.consent_generation; "
    "permission.consent_receipt_sha256:=member.consent_receipt_sha256;\n"
    " RETURN permission;\n" + _FAULT,
)

_BEGIN: Final = _define(
    "_native_b_source3_begin_member_v3",
    "p_window uuid",
    "uuid",
    "smallint",
    "DECLARE " + _FAULT_DECL + " permission native_b_private_v3.source3_permission%ROWTYPE;"
    " cursor native_b_private_v3.source3_cursor%ROWTYPE; ordinal smallint; BEGIN\n"
    + _ACTOR
    + " permission:=app_private._native_b_source3_member_scope_v3(p_window);\n"
    " v_fault_scope:=app_private._native_b_data_scope_v3();\n"
    " SELECT c.* INTO STRICT cursor FROM native_b_private_v3.source3_cursor c"
    " WHERE c.singleton FOR UPDATE;\n"
    " IF cursor.phase<>'READY' OR cursor.next_prepaid THEN RAISE EXCEPTION "
    "'B_SOURCE3_MEMBER_BEGIN_ORDER'; END IF;\n"
    " "
    "ordinal:=app_private.native_b_begin_request_v3(permission.owner_user_id,permission.original_request_id,permission.served_request_id);"
    "\n"
    " IF ordinal<>cursor.position THEN RAISE EXCEPTION 'B_SOURCE3_MEMBER_BEGIN_ORDINAL'; END IF;\n"
    " UPDATE native_b_private_v3.source3_cursor SET phase='READING' WHERE "
    "singleton; RETURN ordinal;\n" + _FAULT,
)

_SEALED: Final = _define(
    "_native_b_source3_cursor_sealed_v3",
    "p_window uuid,p_ordinal smallint",
    "uuid,smallint",
    "void",
    "DECLARE binding native_b_private_v3.binding%ROWTYPE; BEGIN\n"
    " binding:=app_private._native_b_current_v3();\n"
    " UPDATE native_b_private_v3.source3_cursor SET phase='SEALED',next_prepaid=true"
    " WHERE singleton AND window_id=p_window AND "
    "generation=binding.generation AND transaction_id=pg_current_xact_id()"
    " AND position=p_ordinal AND position=binding.request_ordinal AND "
    "phase='READING' AND NOT next_prepaid;\n"
    " IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_CURSOR_SEAL_ONCE'; END IF; END;\n",
)

_NEXT: Final = _define(
    "native_b_source3_cursor_next_v3",
    "p_window uuid",
    "uuid",
    "TABLE(ordinal smallint,owner_user_id uuid,original_request_id "
    "uuid,served_request_id uuid,source_generation bigint,eof boolean)",
    "DECLARE " + _FAULT_DECL + " permission native_b_private_v3.source3_permission%ROWTYPE;"
    " cursor native_b_private_v3.source3_cursor%ROWTYPE; BEGIN\n"
    + _ACTOR
    + " permission:=app_private._native_b_source3_bound_scope_v3(p_window);\n"
    " v_fault_scope:=app_private._native_b_data_scope_v3();\n"
    " SELECT c.* INTO STRICT cursor FROM native_b_private_v3.source3_cursor c"
    " WHERE c.singleton FOR UPDATE;\n"
    " IF cursor.window_id<>p_window OR "
    "cursor.generation<>permission.generation OR "
    "cursor.transaction_id<>pg_current_xact_id()"
    " OR cursor.phase<>'SEALED' OR NOT cursor.next_prepaid"
    " OR NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE "
    "r.ordinal=cursor.position AND r.phase='REQUEST_SEALED') THEN"
    " RAISE EXCEPTION 'B_SOURCE3_CURSOR_NEXT_ORDER'; END IF;\n"
    " IF cursor.position=cursor.member_count THEN\n"
    " UPDATE native_b_private_v3.source3_cursor SET "
    "phase='EOF',next_prepaid=false WHERE singleton;\n"
    " RETURN QUERY SELECT "
    "cursor.position,NULL::uuid,NULL::uuid,NULL::uuid,NULL::bigint,true; "
    "RETURN; END IF;\n"
    " UPDATE native_b_private_v3.source3_cursor SET "
    "position=position+1,phase='READY',next_prepaid=false WHERE singleton;\n"
    " RETURN QUERY SELECT "
    "m.ordinal,m.owner_user_id,m.original_request_id,m.served_request_id,m.source_generation,false"
    " FROM native_b_private_v3.source3_member m WHERE m.ordinal=cursor.position+1;\n" + _FAULT,
)

_EOF: Final = _define(
    "_native_b_source3_cursor_require_eof_v3",
    "p_window uuid",
    "uuid",
    "void",
    "DECLARE binding native_b_private_v3.binding%ROWTYPE; cursor "
    "native_b_private_v3.source3_cursor%ROWTYPE; BEGIN\n"
    " binding:=app_private._native_b_current_v3();\n"
    " SELECT c.* INTO STRICT cursor FROM native_b_private_v3.source3_cursor c WHERE c.singleton;\n"
    " IF cursor.window_id<>p_window OR cursor.generation<>binding.generation "
    "OR cursor.transaction_id<>pg_current_xact_id()"
    " OR cursor.phase<>'EOF' OR cursor.next_prepaid OR cursor.position<>cursor.member_count"
    " OR binding.request_ordinal<>cursor.member_count"
    " OR(SELECT count(*) FROM native_b_private_v3.request)<>cursor.member_count"
    " OR EXISTS(SELECT 1 FROM native_b_private_v3.source3_member m LEFT JOIN "
    "native_b_private_v3.request r ON r.ordinal=m.ordinal"
    " WHERE r.ordinal IS NULL OR r.owner_user_id<>m.owner_user_id OR "
    "r.original_request_id<>m.original_request_id"
    " OR r.served_request_id<>m.served_request_id OR r.phase<>'REQUEST_SEALED') THEN"
    " RAISE EXCEPTION 'B_SOURCE3_CURSOR_EOF_REQUIRED'; END IF; END;\n",
)

_RETIRE: Final = _define(
    "_native_b_source3_cursor_retire_v3",
    "p_operation uuid,p_generation bigint",
    "uuid,bigint",
    "void",
    "BEGIN PERFORM app_private._native_b_role_v3(false);\n"
    " IF p_generation IS NULL OR p_generation<1 OR NOT EXISTS(SELECT 1 FROM "
    "native_b_private_v3.slot s WHERE s.singleton"
    " AND s.state='IDLE' AND s.generation=p_generation AND "
    "s.generation=app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION'))"
    " OR NOT EXISTS(SELECT 1 FROM native_b_private_v3.source3_cursor c WHERE "
    "c.singleton AND c.operation_id=p_operation"
    " AND c.generation=p_generation-1) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_CURSOR_RETIRE_FENCE'; END IF;\n"
    " DELETE FROM native_b_private_v3.source3_member; DELETE FROM "
    "native_b_private_v3.source3_cursor; END;\n",
)

SOURCE3_MEMBERSHIP_SQL_SIGNATURES: Final = tuple(s for s, _, _ in _SPECS)
SOURCE3_MEMBERSHIP_ISSUER_SIGNATURES: Final = SOURCE3_MEMBERSHIP_SQL_SIGNATURES[:1]
SOURCE3_MEMBERSHIP_RUNTIME_SIGNATURES: Final = (SOURCE3_MEMBERSHIP_SQL_SIGNATURES[4],)
SOURCE3_MEMBERSHIP_INTERNAL_SIGNATURES: Final = tuple(
    s
    for s in SOURCE3_MEMBERSHIP_SQL_SIGNATURES
    if s not in (*SOURCE3_MEMBERSHIP_ISSUER_SIGNATURES, *SOURCE3_MEMBERSHIP_RUNTIME_SIGNATURES)
)
SOURCE3_MEMBERSHIP_BODY_DEFINITIONS: Final = MappingProxyType({s: b for s, _, b in _SPECS})
SOURCE3_MEMBERSHIP_BODY_SHA256: Final = MappingProxyType(
    {s: sha256(b.encode()).hexdigest() for s, _, b in _SPECS}
)
SOURCE3_MEMBERSHIP_RESULT_TYPES: Final = MappingProxyType({s: r for s, r, _ in _SPECS})
SOURCE3_MEMBERSHIP_RESULT_SETS: Final = MappingProxyType(
    {s: r.startswith("TABLE(") for s, r, _ in _SPECS}
)
SOURCE3_MEMBERSHIP_SQL_OWNERS: Final = MappingProxyType({s: _KERNEL for s, _, _ in _SPECS})
SOURCE3_MEMBERSHIP_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_MEMBERSHIP_SECURITY_DEFINER: Final = True
INSTALL_SQL: Final = _STORE + _CLAIM + _MEMBER + _BEGIN + _SEALED + _NEXT + _EOF + _RETIRE
GRANTS_SQL_CANDIDATE: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {s} TO native_v3_b_issuer;"
        for s in SOURCE3_MEMBERSHIP_ISSUER_SIGNATURES
    )
    + "\n"
    + "\n".join(
        f"GRANT EXECUTE ON FUNCTION {s} TO native_v3_b_runtime;"
        for s in SOURCE3_MEMBERSHIP_RUNTIME_SIGNATURES
    )
    + "\nREVOKE EXECUTE ON FUNCTION "
    "app_private.native_b_source3_claim_v3(uuid,uuid,uuid,integer,timestamp "
    "with time zone,uuid,uuid,uuid,bigint,bytea) FROM native_v3_b_issuer;\n"
)
