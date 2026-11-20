"""Finite C metadata transport under the exclusive existing B LOGIN.

Uninstalled source-review packet. C retains every policy/current/barrier
predicate. This module returns locked data; it issues no current-use or consent
handle. Exact effective ACL/column/constraint audits are installation gates.
"""

from __future__ import annotations

from hashlib import sha256
from types import MappingProxyType
from typing import Final

from autplay.adapters.postgresql.models.native_training_execution import (
    ESCROW_VECTOR_FIELDS,
    RESOURCE_VECTOR_FIELDS,
    VALIDATION_LIMIT_FIELDS,
)
from autplay.adapters.postgresql.native_b_source_permission_schema_v3 import (
    _ACTOR,
    _FAULT,
    _FAULT_DECL,
)

_KERNEL: Final = "native_v3_b_kernel"
_OWNER: Final = "native_v3_b_catalog_owner"
_RUNTIME: Final = "native_v3_b_runtime"


def _vector_text_upper(names: tuple[str, ...]) -> int:
    # Existing immutable CHECK: exactly these ASCII keys and nonnegative
    # decimal integers of at most16 digits. Include JSONB's comma/colon spaces.
    return 2 + sum(len(name) + 2 + 2 + 16 + 2 for name in names)


SOURCE3_C_VECTOR_TEXT_UPPER: Final = MappingProxyType(
    {
        "resource": _vector_text_upper(RESOURCE_VECTOR_FIELDS),
        "escrow": _vector_text_upper(ESCROW_VECTOR_FIELDS),
        "limits": _vector_text_upper(VALIDATION_LIMIT_FIELDS),
    }
)
# This transport chunk is a restriction within the existing64KiB control
# quantum, not a new request allowance. A maximum-size document may exhaust the
# existing12-entry early cap; refusal cannot buy a replacement bootstrap.
SOURCE3_C_DOCUMENT_CHUNK_BYTES: Final = 65_536 // 4
SOURCE3_C_METADATA_COLUMNS: Final = MappingProxyType(
    {
        "early": (
            ("server_instance_id", "uuid"),
            ("identity_epoch", "bigint"),
            ("operation_id", "uuid"),
            ("escrow_generation", "uuid"),
            ("stage_id", "uuid"),
            ("window_id", "uuid"),
            ("owner_user_id", "uuid"),
            ("stage_ids", "uuid[]"),
            ("current_at", "timestamptz"),
        ),
        "operation": (
            ("operation_id", "uuid"),
            ("run_id", "uuid"),
            ("execution_id", "uuid"),
            ("server_instance_id", "uuid"),
            ("identity_epoch", "bigint"),
            ("request_byte_length", "bigint"),
            ("request_sha256", "bytea"),
            ("policy_sha256", "bytea"),
            ("selection_sha256", "bytea"),
            ("configuration_sha256", "bytea"),
            ("source_root_sha256", "bytea"),
            ("tokenizer_sha256", "bytea"),
            ("approval_sha256", "bytea"),
            ("retention_policy_sha256", "bytea"),
            ("retention_until", "timestamptz"),
            ("max_prep_attempts", "integer"),
            ("max_final_attempts", "integer"),
            ("maximum_wall_ms", "bigint"),
            ("maximum_work_units", "bigint"),
            ("state", "text"),
            ("deadline_at", "timestamptz"),
            ("cancelled_at", "timestamptz"),
            ("restore_revoked_at", "timestamptz"),
            ("terminal_latched_at", "timestamptz"),
            ("closed_at", "timestamptz"),
            ("current_at", "timestamptz"),
        ),
        "policy": (
            ("policy_id", "uuid"),
            ("revision", "bigint"),
            ("policy_sha256", "bytea"),
            ("policy_byte_length", "bigint"),
            ("server_instance_id", "uuid"),
            ("identity_epoch", "bigint"),
            ("environment_sha256", "bytea"),
            ("implementation_sha256", "bytea"),
            ("measurement_sha256", "bytea"),
            ("report_sha256", "bytea"),
            ("internal_io_policy_revision", "bigint"),
            ("internal_io_profile_version", "integer"),
        ),
        "escrow": (
            ("escrow_generation", "uuid"),
            ("operation_id", "uuid"),
            ("policy_sha256", "bytea"),
            ("report_sha256", "bytea"),
            ("escrow_vector", "jsonb"),
            ("resource_vector", "jsonb"),
            ("state", "text"),
            ("held_at", "timestamptz"),
            ("releasing_at", "timestamptz"),
            ("closed_at", "timestamptz"),
        ),
        "policy_vectors": (
            ("prep_limits", "jsonb"),
            ("final_limits", "jsonb"),
            ("host_vector", "jsonb"),
        ),
        "capture_consent": (("consent_generation", "bigint"), ("consent_receipt_sha256", "bytea")),
    }
)

# No caller can choose a weaker phase. Original protected publication selects
# prebind or bound mode. Both helpers burn an irreversible marker before a
# target refusal; early helper must reject reuse after bound publication.
_SCOPE: Final = """
 IF EXISTS(SELECT 1 FROM native_b_private_v3.source3_permission p
    WHERE p.singleton AND p.bound_transaction_id IS NOT NULL) THEN
 permission:=app_private._native_b_source3_member_scope_v3(p_window);
 ELSE
  permission:=app_private._native_b_source3_early_scope_v3(p_window);
 END IF;
"""
_SPECS: list[tuple[str, str]] = []
_BODIES: dict[str, str] = {}


def _define(name: str, result: str, declarations: str, target: str) -> str:
    signature = f"app_private.{name}(uuid)"
    body = (
        "\nDECLARE "
        + _FAULT_DECL
        + " permission native_b_private_v3.source3_permission%ROWTYPE;\n"
        + declarations
        + "\nBEGIN\n"
        + _ACTOR
        + _SCOPE
        + " SELECT s.* INTO STRICT v_fault_scope FROM native_b_private_v3.slot s"
        " WHERE s.singleton AND s.operation_id=permission.operation_id"
        " AND s.generation=permission.generation"
        " AND s.client_incarnation=permission.client_incarnation;\n"
        + target
        + _FAULT.replace(
            "PERFORM app_private._native_b_poison_v3("
            "v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid);",
            "IF v_fault_scope IS NOT NULL THEN PERFORM app_private._native_b_poison_v3("
            "v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid); END IF;",
        )
    )
    _SPECS.append((signature, result))
    _BODIES[signature] = body
    return (
        f"CREATE FUNCTION app_private.{name}(p_window uuid) RETURNS {result}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $source3_meta$\n"
        + body
        + "\n$source3_meta$;\n"
        + f"ALTER FUNCTION {signature} OWNER TO {_KERNEL};\n"
        + f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;\n"
    )


_TYPES: Final = (
    "\n".join(
        f"CREATE TYPE native_b_private_v3.source3_c_{kind} AS ("
        + ",".join(f"{name} {typ}" for name, typ in columns)
        + f");\nALTER TYPE native_b_private_v3.source3_c_{kind} OWNER TO {_OWNER};"
        for kind, columns in SOURCE3_C_METADATA_COLUMNS.items()
    )
    + "\n"
)
_EARLY: Final = _define(
    "native_b_source3_c_early_v3",
    "native_b_private_v3.source3_c_early",
    " v_epoch bigint; v_id uuid; v_stages uuid[]:='{}'::uuid[]; v_bound integer; v_max integer;",
    """
 PERFORM set_config('lock_timeout','1ms',true);
 SELECT s.identity_epoch INTO STRICT v_epoch FROM account.server_instance s
 WHERE s.server_instance_id=permission.server_instance_id FOR UPDATE NOWAIT;
 IF v_epoch<>permission.identity_epoch THEN RAISE EXCEPTION 'B_SOURCE3_C_IDENTITY'; END IF;
 PERFORM p.singleton_id FROM account.internal_io_policy p WHERE p.singleton_id=1 FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_C_IO'; END IF;
 PERFORM p.singleton_id FROM account.resource_quota_policy p
 WHERE p.singleton_id=1 FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_C_QUOTA'; END IF;
 PERFORM p.policy_id FROM ml.native_validation_policy p
 JOIN ml.native_training_operation o ON o.policy_sha256=p.policy_sha256
 WHERE o.operation_id=permission.operation_id FOR UPDATE OF p NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_C_POLICY'; END IF;
 SELECT o.max_prep_attempts+o.max_final_attempts INTO STRICT v_max
 FROM ml.native_training_operation o WHERE o.operation_id=permission.operation_id FOR UPDATE NOWAIT;
 IF v_max NOT BETWEEN 2 AND 32 THEN RAISE EXCEPTION 'B_SOURCE3_C_STAGE_BOUND'; END IF;
 FOR v_id IN SELECT s.stage_id FROM ml.native_validation_stage s
  WHERE s.operation_id=permission.operation_id ORDER BY s.stage_id LIMIT v_max+1 FOR UPDATE NOWAIT
 LOOP v_stages:=array_append(v_stages,v_id); END LOOP;
 IF cardinality(v_stages)>v_max THEN RAISE EXCEPTION 'B_SOURCE3_C_STAGE_HISTORY'; END IF;
 PERFORM e.escrow_generation FROM ml.native_training_escrow_reservation e
 WHERE e.operation_id=permission.operation_id FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_C_ESCROW'; END IF;
 PERFORM s.stage_id FROM ml.native_validation_stage s
 WHERE s.stage_id=permission.stage_id FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_C_STAGE'; END IF;
 PERFORM w.window_id FROM ml.native_training_source_window w
 WHERE w.window_id=permission.window_id FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_C_WINDOW'; END IF;
 PERFORM i.window_id FROM ml.native_training_replay_interlock i
 WHERE i.window_id=permission.window_id FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_C_INTERLOCK'; END IF;
 PERFORM u.user_id FROM account.user_account u WHERE u.user_id=permission.owner_user_id
  AND u.status='ACTIVE' AND u.deleted_at IS NULL FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_C_OWNER'; END IF;
 PERFORM c.user_id FROM account.training_consent c WHERE c.user_id=permission.owner_user_id
  AND c.decision='GRANTED' AND c.policy_version=1 FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_C_CONSENT'; END IF;
 RETURN ROW(permission.server_instance_id,v_epoch,permission.operation_id,
  permission.escrow_generation,permission.stage_id,permission.window_id,
  permission.owner_user_id,v_stages,clock_timestamp())::native_b_private_v3.source3_c_early;
""",
)

_OPERATION: Final = _define(
    "native_b_source3_c_operation_v3",
    "native_b_private_v3.source3_c_operation",
    " value native_b_private_v3.source3_c_operation;",
    " SELECT o.operation_id,o.run_id,o.execution_id,o.server_instance_id,o.identity_epoch,"
    "octet_length(o.request_bytes)::bigint,o.request_sha256,o.policy_sha256,o.selection_sha256,"
    "o.configuration_sha256,o.source_root_sha256,o.tokenizer_sha256,o.approval_sha256,"
    "o.retention_policy_sha256,o.retention_until,o.max_prep_attempts,o.max_final_attempts,"
    "o.maximum_wall_ms,o.maximum_work_units,o.state,o.deadline_at,o.cancelled_at,"
    "o.restore_revoked_at,o.terminal_latched_at,o.closed_at,clock_timestamp() INTO STRICT value"
    " FROM ml.native_training_operation o"
    " WHERE o.operation_id=permission.operation_id FOR UPDATE NOWAIT;\n"
    " RETURN value;\n",
)
_POLICY: Final = _define(
    "native_b_source3_c_policy_v3",
    "native_b_private_v3.source3_c_policy",
    " value native_b_private_v3.source3_c_policy;",
    " SELECT p.policy_id,p.revision,p.policy_sha256,octet_length(p.policy_bytes)::bigint,"
    "p.server_instance_id,p.identity_epoch,p.environment_sha256,p.implementation_sha256,"
    "p.measurement_sha256,p.report_sha256,p.internal_io_policy_revision,"
    "p.internal_io_profile_version"
    " INTO STRICT value FROM ml.native_validation_policy p"
    " JOIN ml.native_training_operation o ON o.policy_sha256=p.policy_sha256"
    " WHERE o.operation_id=permission.operation_id FOR UPDATE OF p NOWAIT;\n RETURN value;\n",
)
_ESCROW: Final = _define(
    "native_b_source3_c_escrow_v3",
    "native_b_private_v3.source3_c_escrow",
    " value native_b_private_v3.source3_c_escrow;",
    " SELECT e.escrow_generation,e.operation_id,e.policy_sha256,e.report_sha256,e.escrow_vector,"
    "e.resource_vector,e.state,e.held_at,e.releasing_at,e.closed_at INTO STRICT value"
    " FROM ml.native_training_escrow_reservation e WHERE e.operation_id=permission.operation_id"
    " FOR UPDATE NOWAIT;\n RETURN value;\n",
)
_POLICY_VECTORS: Final = _define(
    "native_b_source3_c_policy_vectors_v3",
    "native_b_private_v3.source3_c_policy_vectors",
    " value native_b_private_v3.source3_c_policy_vectors;",
    " SELECT p.prep_limits,p.final_limits,p.host_vector INTO STRICT value"
    " FROM ml.native_validation_policy p"
    " JOIN ml.native_training_operation o ON o.policy_sha256=p.policy_sha256"
    " WHERE o.operation_id=permission.operation_id FOR UPDATE OF p NOWAIT;\n RETURN value;\n",
)
_CONSENT: Final = _define(
    "native_b_source3_c_capture_consent_v3",
    "native_b_private_v3.source3_c_capture_consent",
    " value native_b_private_v3.source3_c_capture_consent;",
    " SELECT b.consent_generation,b.consent_receipt_sha256 INTO STRICT value"
    " FROM ml.native_original_capture_v3 b WHERE b.owner_user_id=permission.owner_user_id"
    " AND b.original_request_id=permission.original_request_id FOR SHARE NOWAIT;\n RETURN value;\n",
)
_CHUNK_TABLE: Final = """
CREATE TABLE native_b_private_v3.source3_c_chunk(
 kind smallint PRIMARY KEY CHECK(kind IN(1,2)),window_id uuid NOT NULL,
 generation bigint NOT NULL,transaction_id xid8 NOT NULL,
 byte_length bigint NOT NULL CHECK(byte_length BETWEEN 2 AND 65536),
 document_sha256 bytea NOT NULL CHECK(octet_length(document_sha256)=32),
 next_offset bigint NOT NULL DEFAULT 0 CHECK(next_offset BETWEEN 0 AND 65536)
);
ALTER TABLE native_b_private_v3.source3_c_chunk OWNER TO native_v3_b_catalog_owner;
REVOKE ALL ON native_b_private_v3.source3_c_chunk
 FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer;
GRANT SELECT,INSERT,UPDATE,DELETE ON native_b_private_v3.source3_c_chunk TO native_v3_b_kernel;
"""


def _chunk(kind: int, name: str, length_query: str, raw_query: str) -> str:
    return _define(
        f"native_b_source3_c_{name}_chunk_v3",
        "bytea",
        " progress native_b_private_v3.source3_c_chunk%ROWTYPE;"
        " v_length bigint; v_sha bytea; raw bytea;",
        length_query + " IF v_length IS NULL OR v_length NOT BETWEEN 2 AND 65536"
        " OR octet_length(v_sha)<>32 THEN\n"
        "  RAISE EXCEPTION 'B_SOURCE3_C_DOCUMENT_DESCRIPTOR'; END IF;\n"
        " INSERT INTO native_b_private_v3.source3_c_chunk"
        "(kind,window_id,generation,transaction_id,byte_length,document_sha256)"
        f" VALUES({kind},permission.window_id,permission.generation,"
        "pg_current_xact_id(),v_length,v_sha)"
        " ON CONFLICT(kind) DO UPDATE SET window_id=excluded.window_id,"
        "generation=excluded.generation,transaction_id=excluded.transaction_id,"
        "byte_length=excluded.byte_length,document_sha256=excluded.document_sha256,next_offset=0"
        " WHERE source3_c_chunk.window_id<>excluded.window_id"
        " OR source3_c_chunk.generation<>excluded.generation;\n"
        " SELECT p.* INTO STRICT progress FROM native_b_private_v3.source3_c_chunk p"
        f" WHERE p.kind={kind} FOR UPDATE;\n"
        " IF progress.transaction_id<>pg_current_xact_id()"
        " OR progress.window_id<>permission.window_id"
        " OR progress.generation<>permission.generation OR progress.byte_length<>v_length"
        " OR progress.document_sha256<>v_sha OR progress.next_offset>progress.byte_length THEN\n"
        "  RAISE EXCEPTION 'B_SOURCE3_C_DOCUMENT_CHUNK_STATE'; END IF;\n"
        " -- A completed full comparison starts a newly paid bounded pass.\n"
        " -- Partial passes never reset; rollback cannot restore the B serial.\n"
        " IF progress.next_offset=progress.byte_length THEN\n"
        "  progress.next_offset:=0; UPDATE native_b_private_v3.source3_c_chunk"
        f" SET next_offset=0 WHERE kind={kind}; END IF;\n"
        + raw_query
        + f" IF octet_length(raw)<>least({SOURCE3_C_DOCUMENT_CHUNK_BYTES},"
        "progress.byte_length-progress.next_offset) THEN\n"
        "  RAISE EXCEPTION 'B_SOURCE3_C_DOCUMENT_CHUNK_BYTES'; END IF;\n"
        " UPDATE native_b_private_v3.source3_c_chunk"
        f" SET next_offset=next_offset+octet_length(raw) WHERE kind={kind};\n"
        " RETURN raw;\n",
    )


_REQUEST_CHUNK: Final = _chunk(
    1,
    "request",
    " SELECT octet_length(o.request_bytes),o.request_sha256 INTO STRICT v_length,v_sha"
    " FROM ml.native_training_operation o WHERE o.operation_id=permission.operation_id;\n",
    " SELECT substring(o.request_bytes FROM (progress.next_offset+1)::integer"
    f" FOR {SOURCE3_C_DOCUMENT_CHUNK_BYTES}) INTO STRICT raw"
    " FROM ml.native_training_operation o WHERE o.operation_id=permission.operation_id;\n",
)
_POLICY_CHUNK: Final = _chunk(
    2,
    "policy",
    " SELECT octet_length(p.policy_bytes),p.policy_sha256 INTO STRICT v_length,v_sha"
    " FROM ml.native_validation_policy p"
    " JOIN ml.native_training_operation o ON o.policy_sha256=p.policy_sha256"
    " WHERE o.operation_id=permission.operation_id;\n",
    " SELECT substring(p.policy_bytes FROM (progress.next_offset+1)::integer"
    f" FOR {SOURCE3_C_DOCUMENT_CHUNK_BYTES}) INTO STRICT raw"
    " FROM ml.native_validation_policy p"
    " JOIN ml.native_training_operation o ON o.policy_sha256=p.policy_sha256"
    " WHERE o.operation_id=permission.operation_id;\n",
)
SOURCE3_C_METADATA_SQL_SIGNATURES: Final = tuple(signature for signature, _ in _SPECS)
SOURCE3_C_METADATA_RESULT_TYPES: Final = MappingProxyType(dict(_SPECS))
SOURCE3_C_METADATA_RESULT_SETS: Final = MappingProxyType({key: False for key, _ in _SPECS})
SOURCE3_C_METADATA_COMPOSITE_COLUMNS: Final = MappingProxyType(
    {
        f"native_b_private_v3.source3_c_{kind}": columns
        for kind, columns in SOURCE3_C_METADATA_COLUMNS.items()
    }
)
SOURCE3_C_METADATA_BODY_DEFINITIONS: Final = MappingProxyType(dict(_BODIES))
SOURCE3_C_METADATA_BODY_SHA256: Final = MappingProxyType(
    {key: sha256(value.encode()).hexdigest() for key, value in _BODIES.items()}
)
SOURCE3_C_METADATA_SQL_OWNERS: Final = MappingProxyType({key: _KERNEL for key in _BODIES})
SOURCE3_C_METADATA_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_C_METADATA_SECURITY_DEFINER: Final = True
GRANTS_SQL: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_RUNTIME};"
        for signature in SOURCE3_C_METADATA_SQL_SIGNATURES
    )
    + "\n"
)
INSTALL_SQL: Final = (
    _TYPES
    + _CHUNK_TABLE
    + _EARLY
    + _OPERATION
    + _POLICY
    + _ESCROW
    + _POLICY_VECTORS
    + _CONSENT
    + _REQUEST_CHUNK
    + _POLICY_CHUNK
    + GRANTS_SQL
)
DROP_SQL: Final = (
    "\n".join(
        f"DROP FUNCTION {signature};" for signature in reversed(SOURCE3_C_METADATA_SQL_SIGNATURES)
    )
    + "\nDROP TABLE native_b_private_v3.source3_c_chunk;\n"
    + "\n".join(
        f"DROP TYPE native_b_private_v3.source3_c_{kind};"
        for kind in reversed(tuple(SOURCE3_C_METADATA_COLUMNS))
    )
    + "\n"
)
