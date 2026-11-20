"""Closed C current/privacy DATA queries on the original Source3 binding.

Uninstalled definitions. Original F/G before-effect admission, compiler output
slots and exact ACL/body/constraint qualification remain prerequisites. These
functions issue no filesystem grant and never call the full graph assertion.
The graph metadata projection is an observation, not its omitted full work plan.
"""

from __future__ import annotations

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
_RUNTIME: Final = "native_v3_b_runtime"
SOURCE3_C_CURRENT_COLUMNS: Final = MappingProxyType(
    {
        "consent_current": (
            ("user_id", "uuid"),
            ("decision", "text"),
            ("revision", "bigint"),
            ("policy_version", "integer"),
            ("changed_at", "timestamptz"),
        ),
        "consent_operation": (
            ("operation_id", "uuid"),
            ("user_id", "uuid"),
            ("actor_device_id", "uuid"),
            ("request_sha256", "bytea"),
            ("applied_decision", "text"),
            ("applied_revision", "bigint"),
            ("created_at", "timestamptz"),
        ),
        "erasure_receipt": (
            ("user_id", "uuid"),
            ("source_event_id", "uuid"),
            ("erasure_event_id", "uuid"),
            ("source_server_sequence", "bigint"),
            ("source_watermark", "bigint"),
            ("ledger_sequence", "bigint"),
            ("ledger_digest", "bytea"),
        ),
        "owner_floor": (
            ("user_id", "uuid"),
            ("floor_sequence", "bigint"),
            ("ledger_sequence", "bigint"),
            ("ledger_digest", "bytea"),
        ),
        "deletion_request_next": (
            ("deletion_request_id", "uuid"),
            ("user_id", "uuid"),
            ("server_instance_id", "uuid"),
            ("identity_epoch", "bigint"),
            ("identity_thumbprint_sha256", "bytea"),
            ("request_sha256", "bytea"),
            ("actor_device_id", "uuid"),
            ("actor_public_key_spki", "bytea"),
            ("code_generation", "bigint"),
            ("code_verifier_sha256", "bytea"),
            ("authority_generation", "bigint"),
            ("requested_at", "timestamptz"),
            ("cancel_before", "timestamptz"),
            ("state", "text"),
            ("revision", "bigint"),
            ("cancelled_at", "timestamptz"),
            ("cancel_operation_id", "uuid"),
            ("purge_started_at", "timestamptz"),
        ),
        "capture_consent": (
            ("consent_generation", "bigint"),
            ("consent_receipt_sha256", "bytea"),
        ),
        "io_quota": (
            ("server_instance_id", "uuid"),
            ("identity_epoch", "bigint"),
            ("io_singleton_id", "smallint"),
            ("io_server_instance_id", "uuid"),
            ("io_identity_epoch", "bigint"),
            ("io_revision", "bigint"),
            ("io_workload_version", "smallint"),
            ("io_active_limit", "integer"),
            ("io_playback_ceiling", "integer"),
            ("io_transfer_ceiling", "integer"),
            ("quota_singleton_id", "smallint"),
            ("quota_playback_ceiling", "integer"),
            ("quota_transfer_ceiling", "integer"),
        ),
        "window_enter": (
            ("window_id", "uuid"),
            ("stage_id", "uuid"),
            ("operation_id", "uuid"),
            ("server_instance_id", "uuid"),
            ("identity_epoch", "bigint"),
            ("window_state", "text"),
            ("kind", "text"),
            ("token_sha256", "bytea"),
            ("database_oid", "bigint"),
            ("session_role_oid", "bigint"),
            ("backend_pid", "bigint"),
            ("backend_start", "timestamptz"),
            ("postmaster_start", "timestamptz"),
            ("registration_xid", "text"),
            ("entered_at", "timestamptz"),
            ("window_deadline_at", "timestamptz"),
            ("terminal_at", "timestamptz"),
            ("terminal_receipt_sha256", "bytea"),
            ("stage_stage_id", "uuid"),
            ("stage_operation_id", "uuid"),
            ("phase", "text"),
            ("policy_sha256", "bytea"),
            ("stage_state", "text"),
            ("started_at", "timestamptz"),
            ("stopped_at", "timestamptz"),
            ("closed_at", "timestamptz"),
            ("heartbeat_at", "timestamptz"),
            ("stage_deadline_at", "timestamptz"),
            ("heartbeat_grace_ms", "bigint"),
            ("interlock_window_id", "uuid"),
            ("interlock_stage_id", "uuid"),
            ("interlock_operation_id", "uuid"),
            ("interlock_server_instance_id", "uuid"),
            ("interlock_identity_epoch", "bigint"),
            ("interlock_token_sha256", "bytea"),
            ("current_at", "timestamptz"),
        ),
        "clock": (("current_at", "timestamptz"),),
        "window_activate": (("entered_at", "timestamptz"), ("current_at", "timestamptz")),
        "graph_preflight": (
            ("owner_user_id", "uuid"),
            ("original_request_id", "uuid"),
            ("served_request_id", "uuid"),
            ("source_generation", "bigint"),
            ("source_event_count", "integer"),
            ("source_evidence_count", "integer"),
            ("profile_dimension_count", "integer"),
            ("source_snapshot_track_count", "integer"),
            ("candidate_universe_count", "integer"),
            ("p11_item_count", "integer"),
            ("capture_byte_length", "bigint"),
            ("request_byte_length", "bigint"),
            ("baseline_byte_length", "bigint"),
            ("ranking_byte_length", "bigint"),
            ("profile_byte_length", "bigint"),
            ("pipeline_byte_length", "bigint"),
            ("root_byte_length", "bigint"),
            ("baseline_json_bytes", "bigint"),
            ("baseline_compression", "text"),
            ("baseline_availability_bytes", "bigint"),
            ("temporal_json_bytes", "bigint"),
            ("temporal_compression", "text"),
            ("capture_sha256", "bytea"),
            ("request_sha256", "bytea"),
            ("baseline_sha256", "bytea"),
            ("ranking_sha256", "bytea"),
            ("profile_document_sha256", "bytea"),
            ("profile_sha256", "bytea"),
            ("profile_binary64_sha256", "bytea"),
            ("pipeline_sha256", "bytea"),
            ("temporal_sha256", "bytea"),
            ("root_proof_sha256", "bytea"),
        ),
    }
)

_SPECS: list[str] = []
_BODIES: dict[str, str] = {}


def _define(
    kind: str, target: str, *, selector: bool = False, early: bool = False, declarations: str = ""
) -> str:
    arguments = "p_window uuid" + (",p_selector uuid" if selector else "")
    identity = "uuid,uuid" if selector else "uuid"
    signature = f"app_private.native_b_source3_c_current_{kind}_v3({identity})"
    result = f"native_b_private_v3.source3_c_current_{kind}"
    scope = (
        " IF EXISTS(SELECT 1 FROM native_b_private_v3.source3_permission p"
        " WHERE p.singleton AND p.bound_transaction_id IS NOT NULL) THEN\n"
        "  permission:=app_private._native_b_source3_member_scope_v3(p_window);\n"
        " ELSE permission:=app_private._native_b_source3_early_scope_v3(p_window); END IF;\n"
        if early
        else " permission:=app_private._native_b_source3_member_scope_v3(p_window);\n"
    )
    body = (
        "\nDECLARE "
        + _FAULT_DECL
        + f" permission native_b_private_v3.source3_permission%ROWTYPE; value {result};"
        + " frame integer; "
        + declarations
        + "\nBEGIN\n"
        + _ACTOR
        + scope
        + " SELECT s.* INTO STRICT v_fault_scope FROM native_b_private_v3.slot s"
        " WHERE s.singleton AND s.operation_id=permission.operation_id"
        " AND s.generation=permission.generation"
        " AND s.client_incarnation=permission.client_incarnation;\n"
        # This is the closed scalar logical work, not the physical G plan nor
        # a full graph allowance. Prebind retains its existing finite quantum.
        " IF permission.bound_transaction_id IS NOT NULL THEN\n"
        "  PERFORM app_private._native_b_debit_v3(16384,256,0,4::smallint); END IF;\n"
        " frame:=app_private.native_v3_compiler_enter(65536);\n"
        + target
        + " value:=NULL; PERFORM app_private.native_v3_compiler_exit(frame); RETURN;\n"
        + _FAULT.replace(
            "PERFORM app_private._native_b_poison_v3("
            "v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid);",
            "IF v_fault_scope IS NOT NULL THEN PERFORM app_private._native_b_poison_v3("
            "v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid); END IF;",
        )
    )
    _SPECS.append(signature)
    _BODIES[signature] = body
    return (
        f"CREATE FUNCTION {signature.split('(')[0]}({arguments}) RETURNS SETOF {result}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog "
        "AS $source3_current_metadata$\n" + body + "\n$source3_current_metadata$;\n"
        f"ALTER FUNCTION {signature} OWNER TO {_KERNEL};\n"
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,native_v3_b_issuer;\n"
    )


_TYPES: Final = (
    "\n".join(
        f"CREATE TYPE native_b_private_v3.source3_c_current_{kind} AS ("
        + ",".join(f"{name} {datatype}" for name, datatype in columns)
        + f");\nALTER TYPE native_b_private_v3.source3_c_current_{kind} OWNER TO {_OWNER};\n"
        + f"REVOKE ALL ON TYPE native_b_private_v3.source3_c_current_{kind}"
        + " FROM PUBLIC,native_v3_b_issuer;\n"
        + f"GRANT USAGE ON TYPE native_b_private_v3.source3_c_current_{kind}"
        + f" TO {_KERNEL},{_RUNTIME};"
        for kind, columns in SOURCE3_C_CURRENT_COLUMNS.items()
    )
    + "\n"
)
_CONSENT: Final = _define(
    "consent_current",
    " SELECT c.user_id,c.decision,c.revision,c.policy_version,c.changed_at INTO value"
    " FROM account.training_consent c WHERE c.user_id=permission.owner_user_id"
    " FOR UPDATE NOWAIT; IF FOUND THEN RETURN NEXT value; END IF;\n",
)
_OPERATION: Final = _define(
    "consent_operation",
    " IF p_selector IS NULL THEN RAISE EXCEPTION 'B_SOURCE3_C_INTENT_SELECTOR'; END IF;\n"
    " SELECT octet_length(c.request_sha256) INTO length FROM account.training_consent_operation c"
    " WHERE c.user_id=permission.owner_user_id AND c.operation_id=p_selector;\n"
    " IF FOUND THEN IF length IS DISTINCT FROM 32 THEN"
    " RAISE EXCEPTION 'B_SOURCE3_C_OPERATION_HASH'; END IF;\n"
    " SELECT c.operation_id,c.user_id,c.actor_device_id,c.request_sha256,c.applied_decision,"
    "c.applied_revision,c.created_at INTO STRICT value FROM account.training_consent_operation c"
    " WHERE c.user_id=permission.owner_user_id AND c.operation_id=p_selector;"
    " RETURN NEXT value; END IF;\n",
    selector=True,
    declarations="length integer;",
)
_ERASURE: Final = _define(
    "erasure_receipt",
    " IF p_selector IS NULL THEN RAISE EXCEPTION 'B_SOURCE3_C_EVENT_SELECTOR'; END IF;\n"
    " SELECT octet_length(e.ledger_digest) INTO length FROM ml.sona_event_erasure_receipt e"
    " WHERE e.user_id=permission.owner_user_id AND e.source_event_id=p_selector"
    " FOR UPDATE NOWAIT;\n"
    " IF FOUND THEN IF length IS DISTINCT FROM 32 THEN"
    " RAISE EXCEPTION 'B_SOURCE3_C_ERASURE_HASH'; END IF;\n"
    " SELECT e.user_id,e.source_event_id,e.erasure_event_id,e.source_server_sequence,"
    "e.source_watermark,e.ledger_sequence,e.ledger_digest INTO STRICT value"
    " FROM ml.sona_event_erasure_receipt e WHERE e.user_id=permission.owner_user_id"
    " AND e.source_event_id=p_selector; RETURN NEXT value; END IF;\n",
    selector=True,
    declarations="length integer;",
)
_FLOOR: Final = _define(
    "owner_floor",
    " SELECT octet_length(f.ledger_digest) INTO length FROM sync.owner_erasure_floor f"
    " WHERE f.user_id=permission.owner_user_id FOR UPDATE NOWAIT;\n"
    " IF FOUND THEN IF length IS NOT NULL AND length<>32 THEN"
    " RAISE EXCEPTION 'B_SOURCE3_C_FLOOR_HASH'; END IF;\n"
    " SELECT f.user_id,f.floor_sequence,f.ledger_sequence,f.ledger_digest INTO STRICT value"
    " FROM sync.owner_erasure_floor f WHERE f.user_id=permission.owner_user_id;"
    " RETURN NEXT value; END IF;\n",
    declarations="length integer;",
)
_DELETION: Final = _define(
    "deletion_request_next",
    " SELECT d.deletion_request_id,octet_length(d.actor_public_key_spki),"
    "octet_length(d.identity_thumbprint_sha256),octet_length(d.request_sha256),"
    "octet_length(d.code_verifier_sha256) INTO selected,key_length,identity_length,"
    "request_length,code_length FROM account.account_deletion_request d"
    " WHERE d.user_id=permission.owner_user_id"
    " AND (p_selector IS NULL OR d.deletion_request_id>p_selector)"
    " ORDER BY d.deletion_request_id LIMIT 1 FOR UPDATE NOWAIT;\n"
    " IF FOUND THEN IF key_length NOT BETWEEN 32 AND 256 OR key_length IS NULL"
    " OR identity_length IS DISTINCT FROM 32 OR request_length IS DISTINCT FROM 32"
    " OR code_length IS DISTINCT FROM 32 THEN"
    " RAISE EXCEPTION 'B_SOURCE3_C_DELETION_BYTES'; END IF;\n"
    " SELECT d.deletion_request_id,d.user_id,d.server_instance_id,d.identity_epoch,"
    "d.identity_thumbprint_sha256,d.request_sha256,d.actor_device_id,d.actor_public_key_spki,"
    "d.code_generation,d.code_verifier_sha256,d.authority_generation,d.requested_at,"
    "d.cancel_before,d.state,d.revision,d.cancelled_at,d.cancel_operation_id,d.purge_started_at"
    " INTO STRICT value FROM account.account_deletion_request d"
    " WHERE d.user_id=permission.owner_user_id AND d.deletion_request_id=selected;"
    " RETURN NEXT value; END IF;\n",
    selector=True,
    declarations="selected uuid; key_length integer; identity_length integer;"
    " request_length integer; code_length integer;",
)
_CAPTURE: Final = _define(
    "capture_consent",
    " SELECT octet_length(c.consent_receipt_sha256) INTO STRICT length"
    " FROM ml.native_original_capture_v3 c WHERE c.owner_user_id=permission.owner_user_id"
    " AND c.original_request_id=permission.original_request_id FOR SHARE NOWAIT;\n"
    " IF length IS DISTINCT FROM 32 THEN RAISE EXCEPTION 'B_SOURCE3_C_CAPTURE_HASH'; END IF;\n"
    " SELECT c.consent_generation,c.consent_receipt_sha256 INTO STRICT value"
    " FROM ml.native_original_capture_v3 c WHERE c.owner_user_id=permission.owner_user_id"
    " AND c.original_request_id=permission.original_request_id; RETURN NEXT value;\n",
    declarations="length integer;",
)
_IO: Final = _define(
    "io_quota",
    " SELECT s.server_instance_id,s.identity_epoch,i.singleton_id,i.server_instance_id,"
    "i.identity_epoch,i.revision,i.workload_version,i.active_limit,i.playback_ceiling,"
    "i.transfer_ceiling,q.singleton_id,q.playback_ceiling,q.transfer_ceiling INTO STRICT value"
    " FROM account.server_instance s CROSS JOIN account.internal_io_policy i"
    " CROSS JOIN account.resource_quota_policy q"
    " WHERE s.server_instance_id=permission.server_instance_id"
    " AND i.singleton_id=1 AND q.singleton_id=1 FOR UPDATE OF s,i,q NOWAIT;"
    " RETURN NEXT value;\n",
    early=True,
)
_WINDOW: Final = _define(
    "window_enter",
    " SELECT pg_column_size(s.limits),pg_column_compression(s.limits),"
    "octet_length(w.registration_xid) INTO STRICT limits_bytes,compression,xid_bytes"
    " FROM ml.native_training_source_window w JOIN ml.native_validation_stage s"
    " ON s.stage_id=w.stage_id AND s.operation_id=w.operation_id"
    " WHERE w.window_id=permission.window_id AND w.operation_id=permission.operation_id"
    " FOR UPDATE OF w,s NOWAIT;\n"
    " IF limits_bytes NOT BETWEEN 1 AND 4096 OR compression IS NOT NULL"
    " OR xid_bytes NOT BETWEEN 1 AND 20 THEN"
    " RAISE EXCEPTION 'B_SOURCE3_C_WINDOW_SCALAR_BOUND'; END IF;\n"
    " SELECT (s.limits->'heartbeat_grace_ms')::numeric INTO STRICT grace"
    " FROM ml.native_validation_stage s WHERE s.stage_id=permission.stage_id"
    " AND s.operation_id=permission.operation_id;\n"
    " IF grace IS NULL OR scale(grace)<>0 OR grace NOT BETWEEN 1 AND 9007199254740991 THEN"
    " RAISE EXCEPTION 'B_SOURCE3_C_HEARTBEAT_SCALAR'; END IF;\n"
    " SELECT w.window_id,w.stage_id,w.operation_id,w.server_instance_id,w.identity_epoch,"
    "w.state,w.kind,w.token_sha256,w.database_oid,w.session_role_oid,w.backend_pid,"
    "w.backend_start,w.postmaster_start,w.registration_xid,w.entered_at,w.deadline_at,"
    "w.terminal_at,w.terminal_receipt_sha256,s.stage_id,s.operation_id,s.phase,s.policy_sha256,"
    "s.state,s.started_at,s.stopped_at,s.closed_at,s.heartbeat_at,s.deadline_at,grace::bigint,"
    "r.window_id,r.stage_id,r.operation_id,r.server_instance_id,r.identity_epoch,r.token_sha256,"
    "clock_timestamp() INTO STRICT value FROM ml.native_training_source_window w"
    " JOIN ml.native_validation_stage s ON s.stage_id=w.stage_id AND s.operation_id=w.operation_id"
    " JOIN ml.native_training_replay_interlock r ON r.server_instance_id=w.server_instance_id"
    " WHERE w.window_id=permission.window_id AND w.operation_id=permission.operation_id"
    " FOR UPDATE OF w,s,r NOWAIT; RETURN NEXT value; grace:=NULL;\n",
    declarations="limits_bytes bigint; compression text; xid_bytes integer; grace numeric;",
)
_CLOCK: Final = _define("clock", " value.current_at:=clock_timestamp(); RETURN NEXT value;\n")
_ACTIVATE: Final = _define(
    "window_activate",
    " -- F/C retain their actual process/current/ledger predicates. This closed\n"
    " -- transition accepts no caller owner, state, clock or deadline.\n"
    " PERFORM app_private._native_b_source3_current_rows_v3(permission,false);\n"
    " UPDATE ml.native_training_source_window w SET state='ACTIVE',entered_at=clock_timestamp()"
    " WHERE w.window_id=permission.window_id AND w.stage_id=permission.stage_id"
    " AND w.operation_id=permission.operation_id AND w.token_sha256=permission.window_token_sha256"
    " AND w.database_oid=permission.database_oid::bigint AND "
    "w.session_role_oid=permission.runtime_role_oid::bigint"
    " AND w.backend_pid=pg_backend_pid() AND w.backend_start=permission.data_backend_start"
    " AND w.postmaster_start=pg_postmaster_start_time() AND "
    "w.registration_xid<>pg_current_xact_id()::text"
    " AND w.state='REGISTERED' AND w.entered_at IS NULL AND w.terminal_at IS NULL"
    " AND w.terminal_receipt_sha256 IS NULL AND clock_timestamp()<w.deadline_at"
    " RETURNING w.entered_at INTO STRICT value.entered_at;\n"
    " value.current_at:=clock_timestamp();\n"
    " IF value.current_at>=permission.expires_at THEN RAISE EXCEPTION "
    "'B_SOURCE3_C_ENTER_EXPIRED'; END IF;\n"
    " RETURN NEXT value;\n",
)
_GRAPH: Final = _define(
    "graph_preflight",
    " SELECT b.owner_user_id,b.original_request_id,r.served_request_id,k.source_generation,"
    "b.source_event_count,b.source_evidence_count,b.profile_dimension_count,"
    "b.source_snapshot_track_count,b.candidate_universe_count,b.p11_item_count,"
    "octet_length(b.bundle_document),octet_length(p.request_document),"
    "octet_length(p.baseline_document),octet_length(p.ranking_document),"
    "octet_length(p.original_profile_document),octet_length(p.pipeline_document),"
    "octet_length(r.root_document),pg_column_size(bs.snapshot_document),"
    "pg_column_compression(bs.snapshot_document),octet_length(bs.availability_snapshot),"
    "pg_column_size(tm.snapshot_document),pg_column_compression(tm.snapshot_document),"
    "b.bundle_sha256,p.request_sha256,p.baseline_snapshot_sha256,p.original_ranking_sha256,"
    "p.original_profile_document_sha256,p.original_profile_sha256,"
    "p.original_profile_binary64_sha256,p.pipeline_document_sha256,"
    "b.temporal_snapshot_sha256,r.root_proof_sha256 INTO STRICT value"
    " FROM ml.native_original_capture_v3 b JOIN ml.native_capture_replay_v3 p"
    " ON p.owner_user_id=b.owner_user_id AND p.original_request_id=b.original_request_id"
    " JOIN ml.native_capture_lineage_cursor_v3 k ON k.owner_user_id=b.owner_user_id"
    " AND k.original_request_id=b.original_request_id"
    " JOIN ml.recommendation_request_root_v3 r ON r.owner_user_id=b.owner_user_id"
    " AND r.original_request_id=b.original_request_id"
    " AND r.served_request_id=permission.served_request_id"
    " JOIN ml.recommendation_input_snapshot bs ON bs.user_id=b.owner_user_id"
    " AND bs.recommendation_input_snapshot_id=b.baseline_snapshot_id"
    " JOIN ml.recommendation_temporal_snapshot tm ON tm.user_id=b.owner_user_id"
    " AND tm.recommendation_temporal_snapshot_id=b.temporal_snapshot_id"
    " WHERE b.owner_user_id=permission.owner_user_id"
    " AND b.original_request_id=permission.original_request_id"
    " FOR SHARE OF b,p,k,r,bs,tm NOWAIT; RETURN NEXT value;\n",
)

SOURCE3_C_CURRENT_SQL_SIGNATURES: Final = tuple(_SPECS)
SOURCE3_C_CURRENT_RESULT_TYPES: Final = MappingProxyType(
    {
        signature: "native_b_private_v3.source3_c_current_"
        + signature.split("native_b_source3_c_current_", 1)[1].split("_v3(", 1)[0]
        for signature in _SPECS
    }
)
SOURCE3_C_CURRENT_RESULT_SETS: Final = MappingProxyType({key: True for key in _SPECS})
SOURCE3_C_CURRENT_COMPOSITE_COLUMNS: Final = MappingProxyType(
    {
        f"native_b_private_v3.source3_c_current_{kind}": columns
        for kind, columns in SOURCE3_C_CURRENT_COLUMNS.items()
    }
)
SOURCE3_C_CURRENT_BODY_DEFINITIONS: Final = MappingProxyType(dict(_BODIES))
SOURCE3_C_CURRENT_BODY_SHA256: Final = MappingProxyType(
    {signature: sha256(body.encode()).hexdigest() for signature, body in _BODIES.items()}
)
SOURCE3_C_CURRENT_SQL_OWNERS: Final = MappingProxyType({key: _KERNEL for key in _SPECS})
SOURCE3_C_CURRENT_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_C_CURRENT_SECURITY_DEFINER: Final = True
SOURCE3_C_CURRENT_REQUIRED_SQL: Final = (
    "app_private._native_b_source3_member_scope_v3(uuid)",
    "app_private._native_b_source3_bound_scope_v3(uuid)",
    "app_private._native_b_source3_early_scope_v3(uuid)",
    "app_private._native_b_source3_current_rows_v3(native_b_private_v3.source3_permission,boolean)",
    "app_private._native_b_debit_v3(bigint,bigint,bigint,smallint)",
    "app_private._native_b_poison_v3(native_b_private_v3.slot,oid,integer,xid8)",
    "app_private.native_v3_compiler_enter(bigint)",
    "app_private.native_v3_compiler_exit(integer)",
)
# Exact read-only columns; UPDATE of the first scalar is a PostgreSQL row-lock
# privilege only for these non-login functions, never runtime table authority.
SOURCE3_C_CURRENT_TABLE_COLUMNS: Final = MappingProxyType(
    {
        "account.training_consent": (
            "user_id",
            "decision",
            "revision",
            "policy_version",
            "changed_at",
        ),
        "account.training_consent_operation": tuple(
            name for name, _ in SOURCE3_C_CURRENT_COLUMNS["consent_operation"]
        ),
        "ml.sona_event_erasure_receipt": tuple(
            name for name, _ in SOURCE3_C_CURRENT_COLUMNS["erasure_receipt"]
        ),
        "sync.owner_erasure_floor": tuple(
            name for name, _ in SOURCE3_C_CURRENT_COLUMNS["owner_floor"]
        ),
        "account.account_deletion_request": tuple(
            name for name, _ in SOURCE3_C_CURRENT_COLUMNS["deletion_request_next"]
        ),
        "account.server_instance": ("server_instance_id", "identity_epoch"),
        "account.internal_io_policy": (
            "singleton_id",
            "server_instance_id",
            "identity_epoch",
            "revision",
            "workload_version",
            "active_limit",
            "playback_ceiling",
            "transfer_ceiling",
        ),
        "account.resource_quota_policy": ("singleton_id", "playback_ceiling", "transfer_ceiling"),
        "ml.native_training_source_window": (
            "window_id",
            "stage_id",
            "operation_id",
            "server_instance_id",
            "identity_epoch",
            "state",
            "kind",
            "token_sha256",
            "database_oid",
            "session_role_oid",
            "backend_pid",
            "backend_start",
            "postmaster_start",
            "registration_xid",
            "entered_at",
            "deadline_at",
            "terminal_at",
            "terminal_receipt_sha256",
        ),
        "ml.native_validation_stage": (
            "stage_id",
            "operation_id",
            "phase",
            "policy_sha256",
            "state",
            "started_at",
            "stopped_at",
            "closed_at",
            "heartbeat_at",
            "deadline_at",
            "limits",
        ),
        "ml.native_training_replay_interlock": (
            "window_id",
            "stage_id",
            "operation_id",
            "server_instance_id",
            "identity_epoch",
            "token_sha256",
        ),
        "ml.native_original_capture_v3": (
            "owner_user_id",
            "original_request_id",
            "baseline_snapshot_id",
            "temporal_snapshot_id",
            "consent_generation",
            "consent_receipt_sha256",
            "source_event_count",
            "source_evidence_count",
            "profile_dimension_count",
            "source_snapshot_track_count",
            "candidate_universe_count",
            "p11_item_count",
            "bundle_document",
            "bundle_sha256",
            "temporal_snapshot_sha256",
        ),
        "ml.native_capture_replay_v3": (
            "owner_user_id",
            "original_request_id",
            "request_document",
            "baseline_document",
            "ranking_document",
            "original_profile_document",
            "pipeline_document",
            "request_sha256",
            "baseline_snapshot_sha256",
            "original_ranking_sha256",
            "original_profile_document_sha256",
            "original_profile_sha256",
            "original_profile_binary64_sha256",
            "pipeline_document_sha256",
        ),
        "ml.native_capture_lineage_cursor_v3": (
            "owner_user_id",
            "original_request_id",
            "source_generation",
        ),
        "ml.recommendation_request_root_v3": (
            "owner_user_id",
            "original_request_id",
            "served_request_id",
            "root_document",
            "root_proof_sha256",
        ),
        "ml.recommendation_input_snapshot": (
            "user_id",
            "recommendation_input_snapshot_id",
            "snapshot_document",
            "availability_snapshot",
        ),
        "ml.recommendation_temporal_snapshot": (
            "user_id",
            "recommendation_temporal_snapshot_id",
            "snapshot_document",
        ),
    }
)
COLUMN_GRANTS_SQL: Final = (
    "GRANT USAGE ON SCHEMA account,ml,sync TO native_v3_b_kernel;\n"
    + "\n".join(
        f"GRANT SELECT({','.join(columns)}),UPDATE({columns[0]}) ON {table} TO {_KERNEL};"
        for table, columns in SOURCE3_C_CURRENT_TABLE_COLUMNS.items()
    )
    + "\n"
)
GRANTS_SQL: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_RUNTIME};"
        for signature in SOURCE3_C_CURRENT_SQL_SIGNATURES
    )
    + "\n"
)
TRANSITION_GRANTS_SQL_CANDIDATE: Final = (
    "GRANT UPDATE(state,entered_at) ON ml.native_training_source_window TO native_v3_b_kernel;\n"
)
DEPENDENCY_GRANTS_SQL: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_KERNEL};"
        for signature in SOURCE3_C_CURRENT_REQUIRED_SQL
    )
    + "\n"
)
INSTALL_SQL: Final = (
    "DO $source3_current_metadata_required$ BEGIN\n"
    + "\n".join(
        f" IF to_regprocedure('{signature}') IS NULL THEN RAISE EXCEPTION"
        " 'B_SOURCE3_C_CURRENT_PREREQUISITE_REQUIRED' USING ERRCODE='55000'; END IF;"
        for signature in SOURCE3_C_CURRENT_REQUIRED_SQL
    )
    + "\nEND $source3_current_metadata_required$;\n"
    + _TYPES
    + _CONSENT
    + _OPERATION
    + _ERASURE
    + _FLOOR
    + _DELETION
    + _CAPTURE
    + _IO
    + _WINDOW
    + _CLOCK
    + _ACTIVATE
    + _GRAPH
    + GRANTS_SQL
)
DROP_SQL: Final = (
    "\n".join(
        f"DROP FUNCTION {signature};" for signature in reversed(SOURCE3_C_CURRENT_SQL_SIGNATURES)
    )
    + "\n"
    + "\n".join(
        f"DROP TYPE native_b_private_v3.source3_c_current_{kind};"
        for kind in reversed(tuple(SOURCE3_C_CURRENT_COLUMNS))
    )
    + "\n"
)
