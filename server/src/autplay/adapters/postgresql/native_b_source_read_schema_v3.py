"""Closed Source3 head/origin SQL transport, pending independent ACL review.

This is an uninstalled successor. The permission row is issued by the original
issuer bridge; UUID arguments select neither an owner nor a member. These
functions grant no filesystem consent, current graph proof, or driver budget.
"""

from __future__ import annotations

from dataclasses import fields
from hashlib import sha256
from types import MappingProxyType
from typing import Final

from autplay.adapters.postgresql.native_b_source_permission_schema_v3 import (
    _ACTOR,
    _FAULT,
    _FAULT_DECL,
)
from autplay.adapters.postgresql.native_connected_source_fetch_v3 import (
    _ORIGIN_SQL,
    _PROJECTIONS,
)
from autplay.adapters.postgresql.native_served_authority_v3 import _HEAD_SQL, _ROOT_SQL
from autplay.domain.native_connected_request_v3 import (
    NativeConnectedDependencyKindV3,
    NativeP11UnvalidatedHeadV3,
)

_KERNEL: Final = "native_v3_b_kernel"
_OWNER: Final = "native_v3_b_catalog_owner"
_RUNTIME: Final = "native_v3_b_runtime"
_HEAD: Final = "native_b_private_v3.source3_head"
_PIN: Final = "native_b_private_v3.source3_read_pin"
_TEXT: Final = frozenset(
    {
        "state",
        "serving_provenance",
        "fallback_reason",
        "pipeline_key",
        "pipeline_version",
        "surface",
        "context",
        "created_transaction",
    }
)


def _column_type(name: str) -> str:
    if name.endswith("_sha256"):
        return "bytea"
    if name.endswith("_id") or name == "journal_epoch":
        return "uuid"
    return "text" if name in _TEXT else "bigint"


SOURCE3_HEAD_COLUMNS: Final = tuple(
    (entry.name, _column_type(entry.name)) for entry in fields(NativeP11UnvalidatedHeadV3)
)
SOURCE3_READ_PIN_COLUMNS: Final = (
    ("singleton", "boolean"),
    ("window_id", "uuid"),
    ("transaction_id", "xid8"),
    ("request_ordinal", "smallint"),
    ("head", _HEAD),
    ("documents_mask", "bigint"),
    ("root_consumed", "boolean"),
)
_SPECS: list[tuple[str, str, str, str]] = []
_BODIES: dict[str, str] = {}


def _define(
    name: str, arguments: str, types: str, result: str, declarations: str, target: str
) -> str:
    signature = f"app_private.{name}({types})"
    body = (
        "\nDECLARE " + _FAULT_DECL + "\n"
        " permission native_b_private_v3.source3_permission%ROWTYPE;\n"
        + declarations
        + "\nBEGIN\n"
        + _ACTOR
        + " permission:=app_private._native_b_source3_payload_scope_v3(p_window);\n"
        + " v_fault_scope:=app_private._native_b_data_scope_v3();\n"
        + target
        + _FAULT.replace(
            "PERFORM app_private._native_b_poison_v3("
            "v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid);",
            "IF v_fault_scope IS NOT NULL THEN PERFORM app_private._native_b_poison_v3("
            "v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid); END IF;",
        )
    )
    _SPECS.append((signature, result, _KERNEL, name))
    _BODIES[signature] = body
    return (
        f"CREATE FUNCTION app_private.{name}({arguments}) RETURNS {result}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $source3_read$\n"
        + body
        + "\n$source3_read$;\n"
        + f"ALTER FUNCTION {signature} OWNER TO {_KERNEL};\n"
        + f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;\n"
    )


_TYPE_SQL: Final = (
    f"CREATE TYPE {_HEAD} AS (\n"
    + ",\n".join(f" {name} {kind}" for name, kind in SOURCE3_HEAD_COLUMNS)
    + f"\n);\nALTER TYPE {_HEAD} OWNER TO {_OWNER};\n"
    + f"CREATE TABLE {_PIN} (\n"
    " singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),\n"
    " window_id uuid NOT NULL, transaction_id xid8 NOT NULL,\n"
    " request_ordinal smallint NOT NULL CHECK(request_ordinal BETWEEN 1 AND 16),\n"
    f" head {_HEAD} NOT NULL, documents_mask bigint NOT NULL DEFAULT 0,\n"
    " root_consumed boolean NOT NULL DEFAULT false,\n"
    " CHECK(documents_mask BETWEEN 0 AND 127)\n);\n"
    + f"ALTER TABLE {_PIN} OWNER TO {_OWNER};\n"
    + f"REVOKE ALL ON {_PIN} FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer;\n"
)


def _head_query() -> str:
    query = _HEAD_SQL.strip()
    if query.count("\nFROM ml.recommendation_request_root_v3 r") != 1:
        raise ValueError("SOURCE3_HEAD_CLOSED_QUERY_CHANGED")
    query = query.replace(
        "\nFROM ml.recommendation_request_root_v3 r",
        "\nINTO STRICT actual_head\nFROM ml.recommendation_request_root_v3 r",
        1,
    )
    query = query.replace(":owner", "permission.owner_user_id").replace(
        ":served", "permission.served_request_id"
    )
    return query.replace(
        "\nFOR UPDATE OF r,f,c,b,ofmt,p,k,v,o,d",
        "\n AND r.original_request_id=permission.original_request_id"
        "\n AND k.source_generation=permission.source_generation"
        "\nFOR UPDATE OF r,f,c,b,ofmt,p,k,v,o,d",
        1,
    )


_HEAD_FUNCTION: Final = _define(
    "native_b_source3_head_v3",
    "p_window uuid",
    "uuid",
    _HEAD,
    f" actual_head {_HEAD}; binding native_b_private_v3.binding%ROWTYPE;",
    " IF EXISTS(SELECT 1 FROM native_b_private_v3.source3_read_pin) THEN\n"
    "  RAISE EXCEPTION 'B_SOURCE3_HEAD_REUSE' USING ERRCODE='23514'; END IF;\n"
    " PERFORM app_private._native_b_debit_v3(262144,4096,0,4::smallint);\n" + _head_query() + ";\n"
    " IF actual_head.server_instance_id<>permission.server_instance_id"
    " OR actual_head.identity_epoch<>permission.identity_epoch"
    " OR actual_head.consent_generation<>permission.consent_generation"
    " OR actual_head.consent_receipt_sha256<>permission.consent_receipt_sha256 THEN\n"
    "  RAISE EXCEPTION 'B_SOURCE3_HEAD_IDENTITY' USING ERRCODE='23514'; END IF;\n"
    " binding:=app_private._native_b_current_v3();\n"
    " INSERT INTO native_b_private_v3.source3_read_pin"
    "(singleton,window_id,transaction_id,request_ordinal,head)"
    " VALUES(true,permission.window_id,binding.transaction_id,"
    "binding.request_ordinal,actual_head);\n"
    " RETURN actual_head;\n",
)

_PIN_GUARD: Final = """
 SELECT p.* INTO STRICT pin FROM native_b_private_v3.source3_read_pin p
 WHERE p.singleton AND p.window_id=permission.window_id
  AND p.transaction_id=pg_current_xact_id() AND NOT p.root_consumed
 FOR UPDATE;
 binding:=app_private._native_b_current_v3();
 IF pin.request_ordinal<>binding.request_ordinal THEN
  RAISE EXCEPTION 'B_SOURCE3_READ_PIN' USING ERRCODE='23514'; END IF;
 actual_head:=pin.head;
"""
_PARAMETERS: Final = {
    "owner": "actual_head.owner_user_id",
    "served": "actual_head.served_request_id",
    "original": "actual_head.original_request_id",
    "created_transaction": "actual_head.created_transaction",
    "source_generation": "actual_head.source_generation",
    "source_codec": "'NATIVE_SOURCE3_F64BITS_JSON_SAFE53_V1'",
    "source_codec_sha": "actual_head.source_codec_sha256",
    "baseline": "actual_head.baseline_snapshot_id",
    "capture_sha": "actual_head.capture_sha256",
    "request_sha": "actual_head.original_request_sha256",
    "baseline_sha": "actual_head.baseline_snapshot_sha256",
    "ranking_sha": "actual_head.original_ranking_sha256",
    "profile_document_sha": "actual_head.original_profile_document_sha256",
    "profile_sha": "actual_head.original_profile_sha256",
    "profile_binary64_sha": "actual_head.original_profile_binary64_sha256",
    "pipeline_sha": "actual_head.pipeline_manifest_sha256",
    "root_sha": "actual_head.root_proof_sha256",
    "request_root_sha": "actual_head.request_root_sha256",
    "decision_sha": "actual_head.decision_sha256",
    "seal_sha": "actual_head.connection_seal_sha256",
}


def _bound_query(query: str, *, digest: str, length: str) -> str:
    replacements = {**_PARAMETERS, "sha": f"actual_head.{digest}", "size": f"actual_head.{length}"}
    for name in sorted(replacements, key=len, reverse=True):
        query = query.replace(":" + name, replacements[name])
    # :: casts remain valid, but no named client parameter may enter a definer.
    if any(":" + name in query for name in replacements):
        raise ValueError("SOURCE3_UNBOUND_FIXED_QUERY")
    return query


_ORIGIN_BRANCHES: list[str] = []
SOURCE3_DOCUMENT_ORDER: Final = (*_PROJECTIONS, NativeConnectedDependencyKindV3.ROOT)
for _ordinal, (_kind, (_column, _hash_column, _length, _digest)) in enumerate(_PROJECTIONS.items()):
    _query = (
        f"SELECT {_column} INTO STRICT raw\n{_ORIGIN_SQL}\n"
        f" AND {_hash_column}=:sha AND octet_length({_column})=:size\n"
        "FOR UPDATE OF r,c,b,p,k,ofmt,f"
    )
    maximum = 4194304 if _kind is NativeConnectedDependencyKindV3.ORIGINAL_BASELINE else 2097152
    _ORIGIN_BRANCHES.append(
        f" WHEN {_ordinal + 1} THEN\n"
        f"  size:=actual_head.{_length}; expected_sha:=actual_head.{_digest};\n"
        + (
            "  IF size>65536 THEN RAISE EXCEPTION 'B_SOURCE3_PIPELINE_BYTES'; END IF;\n"
            if _ordinal == 5
            else ""
        )
        + f"  IF size IS NULL OR size<1 OR size>{maximum} THEN"
        " RAISE EXCEPTION 'B_SOURCE3_DOCUMENT_BYTES'; END IF;\n"
        + "  PERFORM app_private._native_b_debit_v3(16384+2*size,257,0,4::smallint);\n  "
        + _bound_query(_query, digest=_digest, length=_length)
        + ";\n"
    )

_DOCUMENT_FUNCTION: Final = _define(
    "native_b_source3_document_v3",
    "p_window uuid,p_kind smallint",
    "uuid,smallint",
    "bytea",
    f" pin {_PIN}%ROWTYPE; actual_head {_HEAD}; binding native_b_private_v3.binding%ROWTYPE;"
    " raw bytea; size bigint; expected_sha bytea;",
    _PIN_GUARD + " IF p_kind IS NULL OR p_kind<1 OR p_kind>6"
    " OR pin.documents_mask<>((1::bigint<<(p_kind-1))-1) THEN\n"
    "  RAISE EXCEPTION 'B_SOURCE3_DOCUMENT_ORDER' USING ERRCODE='23514'; END IF;\n"
    " CASE p_kind\n"
    + "".join(_ORIGIN_BRANCHES)
    + " ELSE RAISE EXCEPTION 'B_SOURCE3_DOCUMENT_KIND'; END CASE;\n"
    " IF octet_length(raw)<>size OR sha256(raw)<>expected_sha THEN\n"
    "  RAISE EXCEPTION 'B_SOURCE3_DOCUMENT_BINDING' USING ERRCODE='23514'; END IF;\n"
    " UPDATE native_b_private_v3.source3_read_pin"
    " SET documents_mask=documents_mask|(1::bigint<<(p_kind-1)) WHERE singleton;\n"
    " RETURN raw;\n",
)

_ROOT_FUNCTION: Final = _define(
    "native_b_source3_root_v3",
    "p_window uuid",
    "uuid",
    "bytea",
    f" pin {_PIN}%ROWTYPE; actual_head {_HEAD};"
    " binding native_b_private_v3.binding%ROWTYPE; raw bytea;",
    _PIN_GUARD + " IF NOT EXISTS(SELECT 1 FROM native_b_private_v3.source3_index_progress i"
    " WHERE i.singleton AND i.window_id=p_window AND i.transaction_id=pg_current_xact_id()"
    " AND i.request_ordinal=binding.request_ordinal AND i.finished AND i.cursor_read"
    " AND i.seen=i.expected_count AND i.expected_count=actual_head.source_event_count"
    " AND i.byte_length=actual_head.source_index_byte_length) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_ROOT_INDEX_CURSOR_CLOSURE'; END IF;\n"
    " IF pin.documents_mask<>63 OR actual_head.root_byte_length<1"
    " OR actual_head.root_byte_length>2097152 THEN\n"
    "  RAISE EXCEPTION 'B_SOURCE3_ROOT_ORDER_OR_BYTES' USING ERRCODE='23514'; END IF;\n"
    " PERFORM app_private._native_b_debit_v3("
    "16384+2*actual_head.root_byte_length,257,0,4::smallint);\n"
    + _bound_query(
        _ROOT_SQL.strip().replace("\nFROM ml.", " INTO STRICT raw\nFROM ml.", 1),
        digest="root_proof_sha256",
        length="root_byte_length",
    )
    + ";\n"
    " IF octet_length(raw)<>actual_head.root_byte_length"
    " OR sha256(raw)<>actual_head.root_proof_sha256 THEN\n"
    "  RAISE EXCEPTION 'B_SOURCE3_ROOT_BINDING' USING ERRCODE='23514'; END IF;\n"
    " UPDATE native_b_private_v3.source3_read_pin"
    " SET documents_mask=127,root_consumed=true WHERE singleton;\n"
    " RETURN raw;\n",
)

_COMPLETE_FUNCTION: Final = _define(
    "native_b_source3_read_complete_v3",
    "p_window uuid",
    "uuid",
    "void",
    f" pin {_PIN}%ROWTYPE; binding native_b_private_v3.binding%ROWTYPE;"
    f" actual_head {_HEAD}; raw_input bigint;",
    " SELECT p.* INTO STRICT pin FROM native_b_private_v3.source3_read_pin p"
    " WHERE p.singleton AND p.window_id=permission.window_id"
    " AND p.transaction_id=pg_current_xact_id() FOR UPDATE;\n"
    " binding:=app_private._native_b_current_v3(); actual_head:=pin.head;\n"
    " IF pin.request_ordinal<>binding.request_ordinal"
    " OR pin.documents_mask<>127 OR NOT pin.root_consumed THEN\n"
    "  RAISE EXCEPTION 'B_SOURCE3_READ_CLOSURE_REQUIRED'; END IF;\n"
    " raw_input:=actual_head.root_byte_length+actual_head.capture_byte_length"
    "+actual_head.original_request_byte_length+actual_head.baseline_byte_length"
    "+actual_head.original_ranking_byte_length+actual_head.original_profile_byte_length"
    "+actual_head.pipeline_byte_length;\n"
    " IF app_private._native_b_read_v3('R'||lpad(binding.request_ordinal::text,2,'0'),'INPUT_B')"
    " <8388608+raw_input THEN RAISE EXCEPTION 'B_SOURCE3_READ_INPUT_REQUIRED'; END IF;\n"
    " -- Expense-state continuation ONLY. It neither attests a Python original"
    " nor seals a request, publishes source bytes, grants consent or writes ML.\n"
    " -- Original07dd/owned-kernel provenance and exact W/F+outcomes ownership"
    " are independently enforced by the fixed Python reader/G registries.\n"
    " PERFORM app_private.native_b_source3_original_registered_v3(p_window);\n"
    " RETURN;\n",
)

_HEAD_PRELUDE_FUNCTION: Final = _define(
    "native_b_source3_head_prelude_v3",
    "p_window uuid",
    "uuid",
    "TABLE("
    + ",".join(f"{name} {kind}" for name, kind in SOURCE3_HEAD_COLUMNS)
    + ",protected_membership_serial bigint)",
    f" actual_head {_HEAD}; binding native_b_private_v3.binding%ROWTYPE; depth integer;",
    " -- One existing protected HEAD evaluation, never expanded function().*.\n"
    " -- Additional local/returned fixed scalar carriers are held before call.\n"
    " PERFORM app_private._native_b_debit_v3(65536,4096,0,4::smallint);\n"
    " depth:=app_private.native_v3_compiler_enter(65536);\n"
    " actual_head:=app_private.native_b_source3_head_v3(p_window);\n"
    " binding:=app_private._native_b_current_v3();\n"
    " RETURN QUERY SELECT (actual_head).*,binding.expected_serial;\n"
    " actual_head:=NULL; binding:=NULL;\n"
    " PERFORM app_private.native_v3_compiler_exit(depth); RETURN;\n",
)

SOURCE3_READ_SQL_SIGNATURES: Final = tuple(spec[0] for spec in _SPECS)
SOURCE3_READ_BODY_DEFINITIONS: Final = MappingProxyType(dict(_BODIES))
SOURCE3_READ_BODY_SHA256: Final = MappingProxyType(
    {signature: sha256(body.encode()).hexdigest() for signature, body in _BODIES.items()}
)
SOURCE3_READ_SQL_OWNERS: Final = MappingProxyType({spec[0]: spec[2] for spec in _SPECS})
SOURCE3_READ_RESULT_TYPES: Final = MappingProxyType({spec[0]: spec[1] for spec in _SPECS})
SOURCE3_READ_RESULT_SETS: Final = MappingProxyType(
    {spec[0]: spec[1].startswith("TABLE(") for spec in _SPECS}
)
SOURCE3_READ_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_READ_SECURITY_DEFINER: Final = True
GRANTS_SQL: Final = (
    f"GRANT SELECT,INSERT,UPDATE,DELETE ON {_PIN} TO {_KERNEL};\n"
    + "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_RUNTIME};"
        for signature in SOURCE3_READ_SQL_SIGNATURES
    )
    + "\n"
)
# Exact application column grants and the C graph/facts facade are separate
# reviewed prerequisites. This packet does not grant broad ML SELECT to either
# LOGIN or silently widen the non-login kernel's table privileges.
INSTALL_SQL: Final = (
    _TYPE_SQL
    + _HEAD_FUNCTION
    + _HEAD_PRELUDE_FUNCTION
    + _DOCUMENT_FUNCTION
    + _ROOT_FUNCTION
    + _COMPLETE_FUNCTION
    + GRANTS_SQL
)
DROP_SQL: Final = (
    "\n".join(f"DROP FUNCTION {signature};" for signature in reversed(SOURCE3_READ_SQL_SIGNATURES))
    + f"\nDROP TABLE {_PIN};\nDROP TYPE {_HEAD};\n"
)
