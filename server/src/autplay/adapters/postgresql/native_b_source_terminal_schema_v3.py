"""SOURCE3 resource terminal/counter retirement, no ML writer or data custody.

Actual Python G/E/F registries are mandatory before these fixed SQL calls.
SQL records resource continuation only; no supplied digest/boolean attests a
Python drain. S2 bodies, sequences and reset semantics remain unchanged.
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
_SPECS: list[tuple[str, str, str]] = []


def _define(name: str, parameters: str, identity: str, result: str, body: str) -> str:
    signature = f"app_private.{name}({identity})"
    _SPECS.append((signature, result, body))
    return (
        f"CREATE FUNCTION app_private.{name}({parameters}) RETURNS {result}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog\n"
        f"AS $source3_terminal${body}$source3_terminal$;\n"
        f"ALTER FUNCTION {signature} OWNER TO {_KERNEL};\n"
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer;\n"
    )


_SEAL: Final = _define(
    "native_b_source3_seal_request_v3",
    "p_window uuid",
    "uuid",
    "smallint",
    "DECLARE " + _FAULT_DECL + " permission native_b_private_v3.source3_permission%ROWTYPE;"
    " binding native_b_private_v3.binding%ROWTYPE; BEGIN\n"
    + _ACTOR
    + " permission:=app_private._native_b_source3_member_scope_v3(p_window);\n"
    " v_fault_scope:=app_private._native_b_data_scope_v3();\n"
    " binding:=app_private._native_b_current_v3();\n"
    " -- Prepay the fixed next-member/EOF resource continuation while this\n"
    " -- request is still FINALIZE; no debit is legal after REQUEST_SEALED.\n"
    " PERFORM app_private._native_b_debit_v3(16384,256,0,4::smallint);\n"
    " IF NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE "
    "r.ordinal=binding.request_ordinal"
    " AND r.owner_user_id=permission.owner_user_id AND "
    "r.original_request_id=permission.original_request_id"
    " AND r.served_request_id=permission.served_request_id AND r.phase='FINALIZE'"
    " AND r.active_stage=5 AND r.clear_prepaid)"
    " OR NOT EXISTS(SELECT 1 FROM native_b_private_v3.source3_read_pin p WHERE p.singleton"
    " AND p.window_id=p_window AND p.transaction_id=pg_current_xact_id()"
    " AND p.request_ordinal=binding.request_ordinal AND p.documents_mask=127 AND p.root_consumed)"
    " OR NOT EXISTS(SELECT 1 FROM native_b_private_v3.source3_index_progress i WHERE i.singleton"
    " AND i.window_id=p_window AND i.transaction_id=pg_current_xact_id()"
    " AND i.request_ordinal=binding.request_ordinal AND i.finished AND i.cursor_read"
    " AND i.seen=i.expected_count)"
    " OR EXISTS(SELECT 1 FROM native_b_private_v3.frame)"
    " OR EXISTS(SELECT 1 FROM app_private.native_v3_compiler_frame f"
    " WHERE f.transaction_id=pg_current_xact_id() AND f.backend_pid=pg_backend_pid()) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_TERMINAL_CLOSURE' USING ERRCODE='23514'; END IF;\n"
    " DELETE FROM native_b_private_v3.source3_read_pin WHERE singleton;\n"
    " DELETE FROM native_b_private_v3.source3_index_progress WHERE singleton;\n"
    " UPDATE native_b_private_v3.request SET phase='REQUEST_SEALED'"
    " WHERE ordinal=binding.request_ordinal AND phase='FINALIZE';\n"
    " IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_TERMINAL_ONCE'; END IF;\n"
    " PERFORM app_private._native_b_source3_cursor_sealed_v3(p_window,binding.request_ordinal);\n"
    " RETURN binding.request_ordinal;\n" + _FAULT,
)

_FINISH: Final = _define(
    "native_b_source3_finish_operation_v3",
    "p_window uuid",
    "uuid",
    "void",
    "DECLARE "
    + _FAULT_DECL
    + " permission native_b_private_v3.source3_permission%ROWTYPE; BEGIN\n"
    + _ACTOR
    + " permission:=app_private._native_b_source3_bound_scope_v3(p_window);\n"
    " v_fault_scope:=app_private._native_b_data_scope_v3();\n"
    " PERFORM app_private._native_b_source3_cursor_require_eof_v3(p_window);\n"
    " IF EXISTS(SELECT 1 FROM native_b_private_v3.source3_read_pin)"
    " OR EXISTS(SELECT 1 FROM native_b_private_v3.source3_index_progress)"
    " OR EXISTS(SELECT 1 FROM app_private.native_v3_compiler_frame f"
    " WHERE f.transaction_id=pg_current_xact_id() AND f.backend_pid=pg_backend_pid()) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_FINISH_LIVE_SQL'; END IF;\n"
    " -- Unchanged S2 sealed-request/all-frame checks and session lock release.\n"
    " PERFORM app_private.native_b_finish_v3();\n" + _FAULT,
)

_RETIRE: Final = _define(
    "native_b_source3_retirement_finish_v3",
    "p_operation uuid,p_generation bigint",
    "uuid,bigint",
    "bigint",
    "BEGIN\n"
    " -- FIRST database expression delegates to unchanged S2 fresh-xid gate.\n"
    " -- No actor capture, lookup or helper may precede this call.\n"
    " PERFORM app_private.native_b_retirement_finish_v3(p_operation,p_generation);\n"
    " PERFORM app_private._native_b_source3_cursor_retire_v3(p_operation,p_generation);\n"
    " DELETE FROM native_b_private_v3.source3_read_pin;\n"
    " DELETE FROM native_b_private_v3.source3_index_progress;\n"
    " DELETE FROM native_b_private_v3.source3_c_chunk;\n"
    " -- Invoker guard requires kernel, issuer session and obsolete generation.\n"
    " DELETE FROM native_b_private_v3.source3_permission;\n"
    " PERFORM app_private._native_b_source3_registration_retire_v3(p_operation,p_generation);\n"
    " RETURN p_generation; END;\n",
)

SOURCE3_TERMINAL_SQL_SIGNATURES: Final = tuple(s for s, _, _ in _SPECS)
SOURCE3_TERMINAL_RUNTIME_SIGNATURES: Final = SOURCE3_TERMINAL_SQL_SIGNATURES[:2]
SOURCE3_TERMINAL_ISSUER_SIGNATURES: Final = SOURCE3_TERMINAL_SQL_SIGNATURES[2:]
SOURCE3_TERMINAL_BODY_DEFINITIONS: Final = MappingProxyType({s: b for s, _, b in _SPECS})
SOURCE3_TERMINAL_BODY_SHA256: Final = MappingProxyType(
    {s: sha256(b.encode()).hexdigest() for s, _, b in _SPECS}
)
SOURCE3_TERMINAL_RESULT_TYPES: Final = MappingProxyType({s: r for s, r, _ in _SPECS})
SOURCE3_TERMINAL_RESULT_SETS: Final = MappingProxyType({s: False for s, _, _ in _SPECS})
SOURCE3_TERMINAL_SQL_OWNERS: Final = MappingProxyType({s: _KERNEL for s, _, _ in _SPECS})
SOURCE3_TERMINAL_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_TERMINAL_SECURITY_DEFINER: Final = True
INSTALL_SQL: Final = _SEAL + _FINISH + _RETIRE
GRANTS_SQL_CANDIDATE: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {s} TO native_v3_b_runtime;"
        for s in SOURCE3_TERMINAL_RUNTIME_SIGNATURES
    )
    + "\nGRANT EXECUTE ON FUNCTION "
    "app_private.native_b_source3_retirement_finish_v3(uuid,bigint) TO "
    "native_v3_b_issuer;\n"
    "GRANT EXECUTE ON FUNCTION "
    "app_private.native_b_retirement_finish_v3(uuid,bigint) TO "
    "native_v3_b_kernel;\n"
    "GRANT SELECT(transaction_id,backend_pid) ON "
    "app_private.native_v3_compiler_frame TO native_v3_b_kernel;\n"
)
