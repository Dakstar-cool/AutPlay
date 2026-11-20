# ruff: noqa: E501
"""Explicit single-member Source3 permission and S2-preserving SQL gates.

This successor installs nothing on import. The existing authenticated B issuer
is the permission issuer: its fixed F/G pre-effect bridge must resolve the real
original selection before calling ``native_b_source3_claim_v3``. Member UUIDs
and the selection digest are transported data, never self-authenticating proof.
That original Python publisher is a separate, still required implementation.
It must enforce exactly one member in its original G selection, rather than
presenting one row taken from an otherwise unproved multi-member selection.

The early gate advances the EXISTING execution serial before metadata work.
Its transactional mirror makes a caught savepoint failure terminal even when
the scoped poison handler fails. The new source-specific bind consequently
mirrors S2's bind rather than invoking its zero-serial-only ordinary entry.
All old S2 functions, roles,56 sequences/55 reset keys and caps remain unchanged.
The explicit SOURCE3-exclusive ACL profile revokes ordinary data RPC reachability
for the same runtime login; it cannot coexist with the ordinary runtime profile
in one database. Its closed wrappers preserve pre-bind and bound serial fences.
SQL concurrency, bootstrap cost and the original publisher/terminal join remain
unqualified until independent review and the root-owned database window.
"""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from sqlalchemy import Connection

_OWNER: Final = "native_v3_b_catalog_owner"
_KERNEL: Final = "native_v3_b_kernel"
_RUNTIME: Final = "native_v3_b_runtime"
_ISSUER: Final = "native_v3_b_issuer"
_TABLE: Final = "native_b_private_v3.source3_permission"
_SAFE53: Final = 9_007_199_254_740_991
SOURCE3_MAX_EARLY_ENTRIES: Final = 12
# Three reserved control quanta +12 early entries +one bind are exactly the
# unchanged bootstrap1MiB/65536. No early payload/graph allocation is admitted.
SOURCE3_EARLY_BYTE_QUANTUM: Final = 65_536
SOURCE3_EARLY_NODE_QUANTUM: Final = 4096

SOURCE3_REVOKED_RUNTIME_SIGNATURES: Final = (
    "app_private.native_b_backend_identity_v3()",
    "app_private.native_b_bind_v3(uuid,bigint,uuid)",
    "app_private.native_b_begin_request_v3(uuid,uuid,uuid)",
    "app_private.native_b_py_stage_enter_v3(smallint)",
    "app_private.native_b_admit_input_v3(bigint)",
    "app_private.native_b_fail_v3()",
    "app_private.native_b_finish_v3()",
    "app_private.native_b_source_v3()",
    "app_private.native_b_write_v3()",
    "app_private.native_b_drain_v3()",
)
SOURCE3_DELEGATED_SIGNATURES: Final = (
    "app_private.native_b_backend_identity_v3()",
    "app_private.native_b_begin_request_v3(uuid,uuid,uuid)",
    "app_private.native_b_py_stage_enter_v3(smallint)",
    "app_private.native_b_admit_input_v3(bigint)",
    "app_private.native_b_original_registered_v3()",
)
SOURCE3_EXCLUSIVE_PROFILE: Final = "NATIVE_B_SOURCE3_SINGLE_MEMBER_EXCLUSIVE_V1"
SOURCE3_BOOTSTRAP_STATUS: Final = "PROPOSED_LOGICAL_QUANTA_NOT_SQL_COST_QUALIFIED"
SOURCE3_PROFILE_REVIEW_REQUIREMENTS: Final = (
    "Independent root/C approval of the exclusive ACL and exact reachable SECURITY DEFINER census",
    "Actual PostgreSQL stale/prepared-call, savepoint/poison-failure and role ACL evidence",
    "Measured/admitted early metadata and complete nested C guard/bootstrap cost",
    "Original F/G selection-to-issuer bridge and exact canonical source-routing client/capture",
    "Closed read-completion facade before the kernel-only original-registered marker",
    "Real source-specific terminal retirement and permission cleanup; no consumed finish shortcut",
)

# Exact column/type order is also C's catalog-audit contract. No payload values
# or independent filesystem grant are stored in this small identity row.
SOURCE3_PERMISSION_COLUMNS: Final = (
    ("singleton", "boolean"),
    ("window_id", "uuid"),
    ("operation_id", "uuid"),
    ("stage_id", "uuid"),
    ("server_instance_id", "uuid"),
    ("identity_epoch", "bigint"),
    ("window_token_sha256", "bytea"),
    ("escrow_generation", "uuid"),
    ("generation", "bigint"),
    ("client_incarnation", "uuid"),
    ("database_oid", "oid"),
    ("runtime_role_oid", "oid"),
    ("data_pid", "integer"),
    ("data_backend_start", "timestamp with time zone"),
    ("postmaster_start", "timestamp with time zone"),
    ("owner_user_id", "uuid"),
    ("original_request_id", "uuid"),
    ("served_request_id", "uuid"),
    ("source_generation", "bigint"),
    ("selection_sha256", "bytea"),
    ("consent_generation", "bigint"),
    ("consent_receipt_sha256", "bytea"),
    ("issued_transaction_id", "xid8"),
    ("issued_at", "timestamp with time zone"),
    ("expires_at", "timestamp with time zone"),
    ("early_transaction_id", "xid8"),
    ("expected_serial", "bigint"),
    ("bound_transaction_id", "xid8"),
    ("bound_at", "timestamp with time zone"),
)
_MUTABLE: Final = frozenset(
    {"early_transaction_id", "expected_serial", "bound_transaction_id", "bound_at"}
)
_IMMUTABLE: Final = tuple(name for name, _ in SOURCE3_PERMISSION_COLUMNS if name not in _MUTABLE)

SOURCE3_REQUIRED_B_FUNCTIONS: Final = (
    "app_private.native_b_claim_v3(uuid,uuid,integer,timestamp with time zone)",
    "app_private._native_b_role_v3(boolean)",
    "app_private._native_b_activity_v3(integer)",
    "app_private._native_b_read_v3(text,text)",
    "app_private._native_b_set_counter_v3(text,text,bigint)",
    "app_private._native_b_data_scope_v3()",
    "app_private._native_b_current_v3()",
    "app_private._native_b_entry_v3()",
    "app_private._native_b_debit_v3(bigint,bigint,bigint,smallint)",
    "app_private._native_b_poison_v3(native_b_private_v3.slot,oid,integer,xid8)",
    *SOURCE3_REVOKED_RUNTIME_SIGNATURES,
    "app_private.native_b_original_registered_v3()",
)

_PRECONDITIONS: Final = (
    "DO $source3_preconditions$ BEGIN\n"
    + "\n".join(
        f" IF to_regprocedure('{signature}') IS NULL THEN "
        "RAISE EXCEPTION 'B_SOURCE3_S2_PREDECESSOR_REQUIRED' USING ERRCODE='55000'; END IF;"
        for signature in SOURCE3_REQUIRED_B_FUNCTIONS
    )
    + "\n IF (SELECT count(*) FROM native_b_private_v3.sequence_oid)<>56 "
    "OR (SELECT count(*) FROM native_b_private_v3.sequence_oid WHERE reset_order IS NOT NULL)<>55 "
    "THEN RAISE EXCEPTION 'B_SOURCE3_FIXED_INVENTORY' USING ERRCODE='55000'; END IF;\n"
    "END; $source3_preconditions$;\n"
)

_TABLE_SQL: Final = f"""
CREATE TABLE {_TABLE} (
 singleton boolean PRIMARY KEY CHECK(singleton),
 window_id uuid NOT NULL UNIQUE,operation_id uuid NOT NULL,stage_id uuid NOT NULL,
 server_instance_id uuid NOT NULL,identity_epoch bigint NOT NULL
  CHECK(identity_epoch BETWEEN 1 AND {_SAFE53}),
 window_token_sha256 bytea NOT NULL CHECK(octet_length(window_token_sha256)=32),
 escrow_generation uuid NOT NULL,generation bigint NOT NULL CHECK(generation>0),
 client_incarnation uuid NOT NULL,database_oid oid NOT NULL,runtime_role_oid oid NOT NULL,
 data_pid integer NOT NULL CHECK(data_pid>0),data_backend_start timestamptz NOT NULL,
 postmaster_start timestamptz NOT NULL CHECK(data_backend_start>=postmaster_start),
 owner_user_id uuid NOT NULL,original_request_id uuid NOT NULL,served_request_id uuid NOT NULL,
 source_generation bigint NOT NULL CHECK(source_generation BETWEEN 1 AND {_SAFE53}),
 selection_sha256 bytea NOT NULL CHECK(octet_length(selection_sha256)=32),
 consent_generation bigint NOT NULL CHECK(consent_generation BETWEEN 1 AND {_SAFE53}),
 consent_receipt_sha256 bytea NOT NULL CHECK(octet_length(consent_receipt_sha256)=32),
 issued_transaction_id xid8 NOT NULL CHECK(issued_transaction_id<>'0'::xid8),
 issued_at timestamptz NOT NULL,expires_at timestamptz NOT NULL CHECK(expires_at>issued_at),
 early_transaction_id xid8,expected_serial bigint NOT NULL DEFAULT 0
  CHECK(expected_serial BETWEEN 0 AND {SOURCE3_MAX_EARLY_ENTRIES + 1}),
 bound_transaction_id xid8,bound_at timestamptz,
 CHECK(original_request_id<>served_request_id),
 CHECK((early_transaction_id IS NULL AND expected_serial=0 AND bound_transaction_id IS NULL)
  OR (early_transaction_id IS NOT NULL AND early_transaction_id<>'0'::xid8 AND expected_serial>0)),
 CHECK((bound_transaction_id IS NULL AND bound_at IS NULL)
  OR (bound_transaction_id=early_transaction_id AND bound_at IS NOT NULL))
);
ALTER TABLE {_TABLE} OWNER TO {_OWNER};
REVOKE ALL ON TABLE {_TABLE} FROM PUBLIC,{_RUNTIME},{_ISSUER};
"""

# Cutover is explicit and quiescent, never a per-request ACL toggle. The lock
# excludes ordinary slot users while the new profile is checked and committed.
# Its actual prepared-call/concurrency semantics still require PostgreSQL proof.
SOURCE3_EXCLUSIVE_PRECONDITION_SQL: Final = """
LOCK TABLE native_b_private_v3.slot IN ACCESS EXCLUSIVE MODE NOWAIT;
DO $source3_exclusive$
DECLARE state text; generation bigint;
BEGIN
 SELECT s.state,s.generation INTO STRICT state,generation
  FROM native_b_private_v3.slot s WHERE s.singleton;
 IF state<>'IDLE' OR generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION')
  OR EXISTS(SELECT 1 FROM native_b_private_v3.binding)
  OR EXISTS(SELECT 1 FROM native_b_private_v3.request)
  OR EXISTS(SELECT 1 FROM native_b_private_v3.frame) THEN
  RAISE EXCEPTION 'B_SOURCE3_EXCLUSIVE_QUIESCENCE_REQUIRED' USING ERRCODE='55000'; END IF;
END; $source3_exclusive$;
"""

_FAULT_DECL: Final = """
 v_fault_scope native_b_private_v3.slot;
 v_fault_role oid; v_fault_pid integer; v_fault_xid xid8;
"""
_ACTOR: Final = """
 SELECT session_user::regrole::oid,pg_backend_pid(),pg_current_xact_id()
  INTO v_fault_role,v_fault_pid,v_fault_xid;
"""
_FAULT: Final = """
EXCEPTION
 WHEN query_canceled OR assert_failure THEN
  PERFORM app_private._native_b_poison_v3(v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid);
  RAISE;
 WHEN OTHERS THEN
  PERFORM app_private._native_b_poison_v3(v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid);
  RAISE;
END;
"""
_specs: list[tuple[str, str, str, str, str]] = []


def _function(
    name: str, args: str, identity: str, result: str, body: str, *, owner: str = _KERNEL
) -> str:
    _specs.append((name, identity, result, body, owner))
    return (
        f"CREATE FUNCTION app_private.{name}({args}) RETURNS {result}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog\n"
        f"AS $source3${body}$source3$;\n"
        f"ALTER FUNCTION app_private.{name}({identity}) OWNER TO {owner};\n"
        f"REVOKE ALL ON FUNCTION app_private.{name}({identity}) "
        f"FROM PUBLIC,{_RUNTIME},{_ISSUER};\n"
    )


_GUARD_BODY: Final = (
    """
BEGIN
 IF TG_TABLE_SCHEMA<>'native_b_private_v3' OR TG_TABLE_NAME<>'source3_permission'
  OR TG_LEVEL<>'ROW' OR TG_WHEN<>'BEFORE' OR TG_NARGS<>0
  OR current_user::text<>'native_v3_b_kernel' THEN
  RAISE EXCEPTION 'B_SOURCE3_PRIVATE_DML' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' THEN
  PERFORM app_private._native_b_role_v3(false);
  IF NEW.issued_transaction_id<>pg_current_xact_id()
   OR NEW.early_transaction_id IS NOT NULL OR NEW.expected_serial<>0
   OR NEW.bound_transaction_id IS NOT NULL OR NEW.bound_at IS NOT NULL THEN
   RAISE EXCEPTION 'B_SOURCE3_ISSUER_INSERT' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 ELSIF TG_OP='DELETE' THEN
  PERFORM app_private._native_b_role_v3(false);
  IF OLD.generation>=app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION') THEN
   RAISE EXCEPTION 'B_SOURCE3_PERMISSION_STILL_CURRENT' USING ERRCODE='23514'; END IF;
  RETURN OLD;
 ELSIF TG_OP<>'UPDATE' THEN
  RAISE EXCEPTION 'B_SOURCE3_PRIVATE_DML' USING ERRCODE='42501'; END IF;
 PERFORM app_private._native_b_role_v3(true);
 IF """
    + "\n  OR ".join(f"NEW.{name} IS DISTINCT FROM OLD.{name}" for name in _IMMUTABLE)
    + """
  OR OLD.expected_serial=9223372036854775807
  OR NEW.expected_serial<>OLD.expected_serial+1
  OR NEW.early_transaction_id IS DISTINCT FROM pg_current_xact_id()
  OR (OLD.early_transaction_id IS NOT NULL
      AND NEW.early_transaction_id IS DISTINCT FROM OLD.early_transaction_id)
  OR OLD.bound_transaction_id IS NOT NULL
  OR (NEW.bound_transaction_id IS NOT NULL
      AND NEW.bound_transaction_id IS DISTINCT FROM pg_current_xact_id())
  OR (NEW.bound_transaction_id IS NULL AND NEW.bound_at IS NOT NULL)
  OR (NEW.bound_transaction_id IS NOT NULL AND NEW.bound_at IS NULL) THEN
  RAISE EXCEPTION 'B_SOURCE3_IMMUTABLE_PERMISSION' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END;
"""
)
_GUARD_SQL: Final = _function(
    "_native_b_source3_permission_guard_v3", "", "", "trigger", _GUARD_BODY
)
# This table guard is INVOKER: the actual fixed modifying function must already
# be running as kernel. A definer trigger would erase that provenance check.
_GUARD_SQL_INVOKER: Final = _GUARD_SQL.replace("SECURITY DEFINER", "SECURITY INVOKER", 1)

_TRUNCATE_BODY: Final = """
BEGIN
 RAISE EXCEPTION 'B_SOURCE3_TRUNCATE_FORBIDDEN' USING ERRCODE='0A000';
END;
"""
_NO_TRUNCATE_SQL: Final = _function(
    "_native_b_source3_no_truncate_v3", "", "", "trigger", _TRUNCATE_BODY
)

# Existing kernel deliberately has no direct sequence privileges. These two
# closed owner primitives add no public RPC, reset/amount API or role grant.
# They authenticate the actual pre-bind data identity again before mutation.
_PREBIND_PRIMITIVE_AUTH: Final = """
 PERFORM app_private._native_b_role_v3(true);
 SELECT session_user::regrole::oid,pg_backend_pid(),pg_current_xact_id()
  INTO v_fault_role,v_fault_pid,v_fault_xid;
 SELECT s.* INTO slot FROM native_b_private_v3.slot s WHERE s.singleton FOR SHARE;
 SELECT activity.backend_start INTO actual_start
  FROM app_private._native_b_activity_v3(pg_backend_pid()) activity;
 IF slot.singleton IS DISTINCT FROM true OR slot.state<>'CLAIMED'
  OR slot.generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION')
  OR slot.data_pid<>pg_backend_pid() OR slot.data_backend_start IS DISTINCT FROM actual_start
  OR slot.database_oid<>(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
  OR slot.runtime_role_oid<>session_user::regrole::oid THEN
  RAISE EXCEPTION 'B_SOURCE3_PREBIND_IDENTITY' USING ERRCODE='23514'; END IF;
 v_fault_scope:=slot;
 SELECT p.* INTO permission FROM native_b_private_v3.source3_permission p
  WHERE p.singleton AND p.generation=slot.generation AND p.operation_id=slot.operation_id
   AND p.client_incarnation=slot.client_incarnation AND p.database_oid=slot.database_oid
   AND p.runtime_role_oid=slot.runtime_role_oid AND p.data_pid=slot.data_pid
   AND p.data_backend_start=slot.data_backend_start;
 IF NOT FOUND OR permission.bound_transaction_id IS NOT NULL
  OR EXISTS(SELECT 1 FROM native_b_private_v3.binding)
  OR app_private._native_b_read_v3('CONTROL','POISON')<>0
  OR app_private._native_b_read_v3('CONTROL','REQUEST_ORDINAL')<>0
  OR permission.expected_serial<>app_private._native_b_read_v3('CONTROL','EXECUTION_SERIAL')
  OR permission.expected_serial=9223372036854775807 THEN
  RAISE EXCEPTION 'B_SOURCE3_PREBIND_CONSISTENCY' USING ERRCODE='23514'; END IF;
"""
_SERIAL_SQL: Final = _function(
    "_native_b_source3_prebind_serial_v3",
    "",
    "",
    "bigint",
    "\nDECLARE "
    + _FAULT_DECL
    + """
 slot native_b_private_v3.slot%ROWTYPE;
 permission native_b_private_v3.source3_permission%ROWTYPE;
 actual_start timestamptz; serial bigint;
BEGIN
"""
    + _PREBIND_PRIMITIVE_AUTH
    + """
 serial:=nextval('native_b_private_v3.b_control_execution_serial'::regclass);
 IF serial<>permission.expected_serial+1 THEN
  RAISE EXCEPTION 'B_SOURCE3_PREBIND_SERIAL' USING ERRCODE='23514'; END IF;
 RETURN serial;
"""
    + _FAULT,
    owner=_OWNER,
)
_LATCH_SQL: Final = _function(
    "_native_b_source3_bind_latch_v3",
    "",
    "",
    "void",
    "\nDECLARE "
    + _FAULT_DECL
    + """
 slot native_b_private_v3.slot%ROWTYPE;
 permission native_b_private_v3.source3_permission%ROWTYPE;
 actual_start timestamptz;
BEGIN
"""
    + _PREBIND_PRIMITIVE_AUTH
    + """
 IF app_private._native_b_read_v3('CONTROL','BIND_LATCH')<>0 THEN
  RAISE EXCEPTION 'B_SOURCE3_PREBIND_LATCH' USING ERRCODE='23514'; END IF;
 PERFORM setval('native_b_private_v3.b_control_bind_latch'::regclass,1,true);
"""
    + _FAULT,
    owner=_OWNER,
)

# Only fixed scalar attributes are touched. Full policy, independent ledgers,
# raw payload graph and its compiler budget remain C's actual paid authority.
_CURRENT_ROWS_BODY: Final = """
DECLARE deadline timestamptz; wall_time timestamptz:=clock_timestamp();
BEGIN
 IF p_active IS NULL THEN
  RAISE EXCEPTION 'B_SOURCE3_FIXED_BOUNDARY' USING ERRCODE='23514'; END IF;
 SELECT least(w.deadline_at,s.deadline_at,o.deadline_at,o.retention_until,
   b.expires_at,k.expires_at,r.expires_at,c.expires_at,replay.expires_at,p.expires_at)
 INTO deadline
 FROM ml.native_training_source_window w
 JOIN ml.native_validation_stage s ON s.stage_id=w.stage_id AND s.operation_id=w.operation_id
 JOIN ml.native_training_operation o ON o.operation_id=w.operation_id
 JOIN ml.native_training_escrow_reservation escrow ON escrow.operation_id=o.operation_id
 JOIN ml.native_training_replay_interlock interlock
  ON interlock.server_instance_id=w.server_instance_id AND interlock.operation_id=w.operation_id
  AND interlock.stage_id=w.stage_id AND interlock.window_id=w.window_id
  AND interlock.identity_epoch=w.identity_epoch AND interlock.token_sha256=w.token_sha256
 JOIN account.server_instance server ON server.server_instance_id=w.server_instance_id
  AND server.identity_epoch=w.identity_epoch
 JOIN account.user_account owner_row ON owner_row.user_id=p.owner_user_id
 JOIN account.training_consent consent ON consent.user_id=owner_row.user_id
 JOIN ml.native_original_capture_v3 b ON b.owner_user_id=p.owner_user_id
  AND b.original_request_id=p.original_request_id
 JOIN ml.native_capture_lineage_cursor_v3 k ON k.owner_user_id=b.owner_user_id
  AND k.original_request_id=b.original_request_id
 JOIN ml.native_original_capture_format ofmt ON ofmt.owner_user_id=b.owner_user_id
  AND ofmt.original_request_id=b.original_request_id
 JOIN ml.native_atomic_capture_connection_v3 c ON c.owner_user_id=b.owner_user_id
  AND c.original_request_id=b.original_request_id AND c.served_request_id=p.served_request_id
 JOIN ml.recommendation_request_root_v3 r ON r.owner_user_id=c.owner_user_id
  AND r.original_request_id=c.original_request_id AND r.served_request_id=c.served_request_id
 JOIN ml.native_served_request_hash_format fmt ON fmt.owner_user_id=r.owner_user_id
  AND fmt.served_request_id=r.served_request_id
 JOIN ml.native_capture_replay_v3 replay ON replay.owner_user_id=b.owner_user_id
  AND replay.original_request_id=b.original_request_id
 WHERE w.window_id=p.window_id AND w.operation_id=p.operation_id AND w.stage_id=p.stage_id
  AND w.server_instance_id=p.server_instance_id AND w.identity_epoch=p.identity_epoch
  AND w.token_sha256=p.window_token_sha256 AND w.kind='CAPTURE'
  AND w.database_oid=p.database_oid::bigint AND w.session_role_oid=p.runtime_role_oid::bigint
  AND w.backend_pid=p.data_pid AND w.backend_start=p.data_backend_start
  AND w.postmaster_start=p.postmaster_start AND w.postmaster_start=pg_postmaster_start_time()
  AND w.registration_xid<>pg_current_xact_id()::text
  AND w.terminal_at IS NULL AND w.terminal_receipt_sha256 IS NULL
  AND ((NOT p_active AND w.state='REGISTERED' AND w.entered_at IS NULL)
       OR (p_active AND w.state='ACTIVE' AND w.entered_at IS NOT NULL))
  AND s.state='RUNNING' AND s.stopped_at IS NULL AND s.closed_at IS NULL
  AND s.heartbeat_at IS NOT NULL AND s.heartbeat_at<=wall_time
  AND s.policy_sha256=o.policy_sha256
  AND o.server_instance_id=p.server_instance_id AND o.identity_epoch=p.identity_epoch
  AND o.selection_sha256=p.selection_sha256 AND o.state IN ('PREP','TRAIN','FINAL')
  AND o.cancelled_at IS NULL AND o.restore_revoked_at IS NULL
  AND o.terminal_latched_at IS NULL AND o.closed_at IS NULL
  AND escrow.escrow_generation=p.escrow_generation AND escrow.state='HELD'
  AND escrow.closed_at IS NULL AND escrow.releasing_at IS NULL
  AND escrow.policy_sha256=o.policy_sha256
  AND owner_row.status='ACTIVE' AND owner_row.deleted_at IS NULL
  AND consent.decision='GRANTED' AND consent.policy_version=1
  AND consent.revision=p.consent_generation AND b.consent_generation=p.consent_generation
  AND b.consent_receipt_sha256=p.consent_receipt_sha256
  AND k.state='ACTIVE' AND k.source_generation=p.source_generation
  AND b.original_capture_format_version=3 AND ofmt.original_capture_format_version=3
  AND r.original_capture_format_version=3 AND r.request_hash_format_version=3
  AND c.original_capture_format_version=3 AND c.request_hash_format_version=3
  AND fmt.request_hash_format_version=3
  AND b.source_codec='NATIVE_SOURCE3_F64BITS_JSON_SAFE53_V1'
  AND b.source_codec_sha256=decode(
   'eda9439b265c5e92934f8f62a333782579a14aed116385271f158819045581a2','hex')
  AND r.source_codec_sha256=b.source_codec_sha256 AND c.source_codec_sha256=b.source_codec_sha256
 FOR SHARE OF w,s,o,escrow,interlock,server,owner_row,consent,b,k,ofmt,c,r,fmt,replay NOWAIT;
 IF NOT FOUND OR deadline IS NULL OR deadline<=clock_timestamp() THEN
  RAISE EXCEPTION 'B_SOURCE3_CURRENT_METADATA' USING ERRCODE='23514'; END IF;
 RETURN deadline;
END;
"""
_CURRENT_ROWS_SQL: Final = _function(
    "_native_b_source3_current_rows_v3",
    f"p {_TABLE},p_active boolean",
    f"{_TABLE},boolean",
    "timestamp with time zone",
    _CURRENT_ROWS_BODY,
)

_CLAIM_BODY: Final = (
    "\nDECLARE "
    + _FAULT_DECL
    + """
 v_generation bigint; permission native_b_private_v3.source3_permission%ROWTYPE;
 selected record; wall_time timestamptz;
BEGIN
 PERFORM app_private._native_b_role_v3(false);
"""
    + _ACTOR
    + """
 -- Only the genuine fixed F/G original-registry bridge may call this issuer
 -- endpoint. The fields below transport that decision; SQL hashes/UUIDs do
 -- not claim it. No runtime EXECUTE or caller-selected SQL/publisher exists.
 PERFORM app_private._native_b_source3_registration_claim_v3(p_window,p_operation,p_data_pid,p_data_start);
 v_generation:=app_private.native_b_claim_v3(p_operation,p_client,p_data_pid,p_data_start);
 SELECT slot.* INTO v_fault_scope FROM native_b_private_v3.slot slot
  WHERE slot.singleton AND slot.state='CLAIMED' AND slot.generation=v_generation
   AND slot.operation_id=p_operation AND slot.client_incarnation=p_client;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_ISSUED_CLAIM' USING ERRCODE='23514'; END IF;
 IF p_window IS NULL OR p_owner IS NULL OR p_original IS NULL OR p_served IS NULL
  OR p_original=p_served OR p_source_generation IS NULL
  OR p_source_generation NOT BETWEEN 1 AND 9007199254740991
  OR p_selection_sha256 IS NULL OR octet_length(p_selection_sha256)<>32 THEN
  RAISE EXCEPTION 'B_SOURCE3_MEMBER_TRANSPORT' USING ERRCODE='23514'; END IF;
 wall_time:=clock_timestamp();
 SELECT w.stage_id,w.server_instance_id,w.identity_epoch,w.token_sha256,w.postmaster_start,
  escrow.escrow_generation,b.consent_generation,b.consent_receipt_sha256,
  least(w.deadline_at,s.deadline_at,o.deadline_at,o.retention_until,
   b.expires_at,k.expires_at,r.expires_at,c.expires_at,replay.expires_at) AS expires_at
 INTO STRICT selected
 FROM ml.native_training_source_window w
 JOIN ml.native_validation_stage s ON s.stage_id=w.stage_id AND s.operation_id=w.operation_id
 JOIN ml.native_training_operation o ON o.operation_id=w.operation_id
 JOIN ml.native_training_escrow_reservation escrow ON escrow.operation_id=o.operation_id
 JOIN ml.native_original_capture_v3 b ON b.owner_user_id=p_owner
  AND b.original_request_id=p_original
 JOIN ml.native_capture_lineage_cursor_v3 k ON k.owner_user_id=b.owner_user_id
  AND k.original_request_id=b.original_request_id
 JOIN ml.native_atomic_capture_connection_v3 c ON c.owner_user_id=b.owner_user_id
  AND c.original_request_id=b.original_request_id AND c.served_request_id=p_served
 JOIN ml.recommendation_request_root_v3 r ON r.owner_user_id=c.owner_user_id
  AND r.original_request_id=c.original_request_id AND r.served_request_id=c.served_request_id
 JOIN ml.native_capture_replay_v3 replay ON replay.owner_user_id=b.owner_user_id
  AND replay.original_request_id=b.original_request_id
 WHERE w.window_id=p_window AND w.operation_id=p_operation
  AND w.kind='CAPTURE' AND w.state='REGISTERED' AND w.entered_at IS NULL
  AND w.terminal_at IS NULL AND w.registration_xid<>pg_current_xact_id()::text
  AND w.database_oid=v_fault_scope.database_oid::bigint
  AND w.session_role_oid=v_fault_scope.runtime_role_oid::bigint
  AND w.backend_pid=p_data_pid AND w.backend_start=p_data_start
  AND w.postmaster_start=pg_postmaster_start_time()
  AND o.selection_sha256=p_selection_sha256 AND k.state='ACTIVE'
  AND k.source_generation=p_source_generation
 FOR SHARE OF w,s,o,escrow,b,k,c,r,replay NOWAIT;
 IF selected.expires_at IS NULL OR selected.expires_at<=wall_time THEN
  RAISE EXCEPTION 'B_SOURCE3_PERMISSION_EXPIRED' USING ERRCODE='23514'; END IF;
 DELETE FROM native_b_private_v3.source3_permission;
 INSERT INTO native_b_private_v3.source3_permission(
  singleton,window_id,operation_id,stage_id,server_instance_id,identity_epoch,
  window_token_sha256,escrow_generation,generation,client_incarnation,database_oid,
  runtime_role_oid,data_pid,data_backend_start,postmaster_start,owner_user_id,
  original_request_id,served_request_id,source_generation,selection_sha256,
  consent_generation,consent_receipt_sha256,issued_transaction_id,issued_at,expires_at)
 VALUES(true,p_window,p_operation,selected.stage_id,selected.server_instance_id,
  selected.identity_epoch,selected.token_sha256,selected.escrow_generation,v_generation,p_client,
  v_fault_scope.database_oid,v_fault_scope.runtime_role_oid,p_data_pid,p_data_start,
  selected.postmaster_start,p_owner,p_original,p_served,p_source_generation,p_selection_sha256,
  selected.consent_generation,selected.consent_receipt_sha256,pg_current_xact_id(),wall_time,
  selected.expires_at) RETURNING * INTO permission;
 PERFORM app_private._native_b_source3_current_rows_v3(permission,false);
 IF permission.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'B_SOURCE3_PERMISSION_EXPIRED' USING ERRCODE='23514'; END IF;
 RETURN v_generation;
"""
    + _FAULT
)
_CLAIM_SQL: Final = _function(
    "native_b_source3_claim_v3",
    "p_window uuid,p_operation uuid,p_client uuid,p_data_pid integer,p_data_start timestamptz,"
    "p_owner uuid,p_original uuid,p_served uuid,p_source_generation "
    "bigint,p_selection_sha256 bytea",
    "uuid,uuid,uuid,integer,timestamp with time zone,uuid,uuid,uuid,bigint,bytea",
    "bigint",
    _CLAIM_BODY,
)

_PREBIND_ENTRY_BODY: Final = (
    "\nDECLARE "
    + _FAULT_DECL
    + """
 slot native_b_private_v3.slot%ROWTYPE;
 permission native_b_private_v3.source3_permission%ROWTYPE;
 actual_start timestamptz; serial bigint;
BEGIN
 PERFORM app_private._native_b_role_v3(true);
"""
    + _ACTOR
    + """
 SELECT s.* INTO slot FROM native_b_private_v3.slot s WHERE s.singleton FOR SHARE;
 SELECT activity.backend_start INTO actual_start
  FROM app_private._native_b_activity_v3(pg_backend_pid()) activity;
 IF slot.singleton IS DISTINCT FROM true OR slot.state<>'CLAIMED'
  OR slot.generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION')
  OR slot.data_pid<>pg_backend_pid()
  OR slot.data_backend_start IS DISTINCT FROM actual_start
  OR slot.database_oid<>(SELECT oid FROM pg_catalog.pg_database
                                 WHERE datname=current_database())
  OR slot.runtime_role_oid<>session_user::regrole::oid THEN
  RAISE EXCEPTION 'B_SOURCE3_EARLY_IDENTITY' USING ERRCODE='23514'; END IF;
 v_fault_scope:=slot;
 SELECT p.* INTO permission FROM native_b_private_v3.source3_permission p
  WHERE p.singleton AND p.generation=v_fault_scope.generation
   AND p.operation_id=v_fault_scope.operation_id AND p.client_incarnation=v_fault_scope.client_incarnation
   AND p.database_oid=v_fault_scope.database_oid AND p.runtime_role_oid=v_fault_scope.runtime_role_oid
   AND p.data_pid=v_fault_scope.data_pid AND p.data_backend_start=v_fault_scope.data_backend_start;
 IF NOT FOUND OR permission.bound_transaction_id IS NOT NULL
  OR EXISTS(SELECT 1 FROM native_b_private_v3.binding)
  OR app_private._native_b_read_v3('CONTROL','BIND_LATCH')<>0
  OR app_private._native_b_read_v3('CONTROL','REQUEST_ORDINAL')<>0
  OR app_private._native_b_read_v3('CONTROL','POISON')<>0
  OR permission.expected_serial<>app_private._native_b_read_v3('CONTROL','EXECUTION_SERIAL')
  OR permission.expected_serial=9223372036854775807
  OR (permission.early_transaction_id IS NOT NULL
      AND permission.early_transaction_id<>pg_current_xact_id()) THEN
  RAISE EXCEPTION 'B_SOURCE3_EARLY_CONSISTENCY' USING ERRCODE='23514'; END IF;
 -- Identity/consistency only precede this irreversible pre-bind marker.
 -- A later argument/metadata failure rolls back this CAS, never the sequence.
 serial:=app_private._native_b_source3_prebind_serial_v3();
 IF serial<>permission.expected_serial+1 THEN
  RAISE EXCEPTION 'B_SOURCE3_EARLY_SERIAL' USING ERRCODE='23514'; END IF;
 -- Refuse overflow AFTER the nonrollback marker: no repeatable free probe.
 IF permission.expected_serial>=12 THEN
  RAISE EXCEPTION 'B_SOURCE3_EARLY_QUANTUM_EXHAUSTED' USING ERRCODE='54000'; END IF;
 UPDATE native_b_private_v3.source3_permission p SET expected_serial=serial,
  early_transaction_id=pg_current_xact_id()
 WHERE p.singleton AND p.generation=permission.generation
  AND p.operation_id=permission.operation_id AND p.client_incarnation=permission.client_incarnation
  AND p.database_oid=permission.database_oid AND p.runtime_role_oid=permission.runtime_role_oid
  AND p.data_pid=permission.data_pid AND p.data_backend_start=permission.data_backend_start
  AND p.expected_serial=permission.expected_serial AND p.bound_transaction_id IS NULL
  AND p.early_transaction_id IS NOT DISTINCT FROM permission.early_transaction_id
 RETURNING p.* INTO permission;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_EARLY_CAS' USING ERRCODE='23514'; END IF;
 IF p_window IS NULL OR p_window<>permission.window_id
  OR current_setting('transaction_isolation')<>'repeatable read'
  OR permission.issued_transaction_id=pg_current_xact_id()
  OR permission.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'B_SOURCE3_EARLY_PHASE' USING ERRCODE='23514'; END IF;
 RETURN permission;
"""
    + _FAULT
)
_PREBIND_ENTRY_SQL: Final = _function(
    "_native_b_source3_prebind_entry_v3", "p_window uuid", "uuid", _TABLE, _PREBIND_ENTRY_BODY
)
_EARLY_BODY: Final = (
    "\nDECLARE "
    + _FAULT_DECL
    + """
 permission native_b_private_v3.source3_permission%ROWTYPE;
BEGIN
"""
    + _ACTOR
    + """
 permission:=app_private._native_b_source3_prebind_entry_v3(p_window);
 SELECT s.* INTO STRICT v_fault_scope FROM native_b_private_v3.slot s
  WHERE s.singleton AND s.state='CLAIMED' AND s.generation=permission.generation
   AND s.operation_id=permission.operation_id AND s.client_incarnation=permission.client_incarnation
   AND s.data_pid=pg_backend_pid() AND s.data_backend_start=permission.data_backend_start;
 PERFORM app_private._native_b_source3_current_rows_v3(permission,false);
 RETURN permission;
"""
    + _FAULT
)
_EARLY_SQL: Final = _function(
    "_native_b_source3_early_scope_v3", "p_window uuid", "uuid", _TABLE, _EARLY_BODY
)

_BIND_BODY: Final = (
    "\nDECLARE "
    + _FAULT_DECL
    + """
 slot native_b_private_v3.slot%ROWTYPE;
 permission native_b_private_v3.source3_permission%ROWTYPE;
 actual_start timestamptz; serial bigint;
BEGIN
 PERFORM app_private._native_b_role_v3(true);
"""
    + _ACTOR
    + """
 SELECT s.* INTO slot FROM native_b_private_v3.slot s WHERE s.singleton FOR SHARE;
 SELECT activity.backend_start INTO actual_start
  FROM app_private._native_b_activity_v3(pg_backend_pid()) activity;
 IF slot.singleton IS DISTINCT FROM true OR slot.state<>'CLAIMED'
  OR slot.operation_id IS DISTINCT FROM p_operation
  OR slot.client_incarnation IS DISTINCT FROM p_client
  OR slot.generation IS DISTINCT FROM p_generation
  OR slot.generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION')
  OR slot.data_pid<>pg_backend_pid()
  OR slot.data_backend_start IS DISTINCT FROM actual_start
  OR slot.database_oid<>(SELECT oid FROM pg_catalog.pg_database
                                 WHERE datname=current_database())
  OR slot.runtime_role_oid<>session_user::regrole::oid THEN
  RAISE EXCEPTION 'B_SOURCE3_BIND_IDENTITY' USING ERRCODE='23514'; END IF;
 v_fault_scope:=slot;
 SELECT p.* INTO permission FROM native_b_private_v3.source3_permission p
  WHERE p.singleton AND p.generation=p_generation AND p.operation_id=p_operation
   AND p.client_incarnation=p_client AND p.database_oid=v_fault_scope.database_oid
   AND p.runtime_role_oid=v_fault_scope.runtime_role_oid AND p.data_pid=pg_backend_pid()
   AND p.data_backend_start=actual_start;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM native_b_private_v3.binding)
  OR app_private._native_b_read_v3('CONTROL','BIND_LATCH')<>0
  OR app_private._native_b_read_v3('CONTROL','POISON')<>0
  OR app_private._native_b_read_v3('CONTROL','REQUEST_ORDINAL')<>0
  OR permission.expected_serial<>app_private._native_b_read_v3('CONTROL','EXECUTION_SERIAL')
  OR permission.expected_serial=9223372036854775807
  OR permission.bound_transaction_id IS NOT NULL THEN
  RAISE EXCEPTION 'B_SOURCE3_FIRST_BIND' USING ERRCODE='23514'; END IF;
 -- Same S2 irreversible latch, before RR/advisory/window/early-scope refusal.
 PERFORM app_private._native_b_source3_bind_latch_v3();
 IF current_setting('transaction_isolation')<>'repeatable read'
  OR p_window IS NULL OR p_window<>permission.window_id
  OR permission.early_transaction_id IS DISTINCT FROM pg_current_xact_id()
  OR permission.expected_serial<=0 OR permission.issued_transaction_id=pg_current_xact_id()
  OR permission.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'B_SOURCE3_RR_EARLY_SCOPE_REQUIRED' USING ERRCODE='23514'; END IF;
 IF NOT pg_try_advisory_lock(714928136450029::bigint) THEN
  RAISE EXCEPTION 'B_SESSION_SLOT_BUSY' USING ERRCODE='55000'; END IF;
 serial:=app_private._native_b_source3_prebind_serial_v3();
 IF serial<>permission.expected_serial+1
  OR app_private._native_b_read_v3('OP','WORK_B')<>0
  OR app_private._native_b_read_v3('OP','NODES')<>0
  OR app_private._native_b_read_v3('OP','INPUT_B')<>0 THEN
  RAISE EXCEPTION 'B_FIRST_BIND_BOOTSTRAP' USING ERRCODE='23514'; END IF;
 PERFORM app_private._native_b_set_counter_v3('OP','WORK_B',1048576);
 PERFORM app_private._native_b_set_counter_v3('OP','NODES',65536);
 INSERT INTO native_b_private_v3.binding VALUES(true,p_operation,p_generation,pg_current_xact_id(),
  pg_backend_pid(),actual_start,p_client,serial,0,false);
 UPDATE native_b_private_v3.slot SET state='BOUND' WHERE singleton;
 UPDATE native_b_private_v3.source3_permission p SET expected_serial=serial,
  bound_transaction_id=pg_current_xact_id(),bound_at=clock_timestamp()
 WHERE p.singleton AND p.operation_id=p_operation AND p.generation=p_generation
  AND p.client_incarnation=p_client AND p.data_pid=pg_backend_pid()
  AND p.data_backend_start=actual_start AND p.runtime_role_oid=session_user::regrole::oid
  AND p.database_oid=v_fault_scope.database_oid
  AND p.early_transaction_id=pg_current_xact_id() AND p.bound_transaction_id IS NULL
  AND p.expected_serial=permission.expected_serial RETURNING p.* INTO permission;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_BIND_CAS' USING ERRCODE='23514'; END IF;
 -- F_ENTER has not run yet: keep the registered-window check here.
 PERFORM app_private._native_b_source3_current_rows_v3(permission,false);
"""
    + _FAULT
)
_BIND_SQL: Final = _function(
    "native_b_source3_bind_v3",
    "p_window uuid,p_operation uuid,p_generation bigint,p_client uuid",
    "uuid,uuid,bigint,uuid",
    "void",
    _BIND_BODY,
)

_BOUND_BODY: Final = (
    "\nDECLARE "
    + _FAULT_DECL
    + """
 binding native_b_private_v3.binding%ROWTYPE;
 permission native_b_private_v3.source3_permission%ROWTYPE;
 actual_state text;
BEGIN
"""
    + _ACTOR
    + """
 PERFORM app_private._native_b_role_v3(true);
 SELECT s.state INTO actual_state FROM native_b_private_v3.slot s WHERE s.singleton;
 IF actual_state='CLAIMED' THEN
  -- This is not a pre-auth harmless ordinary RPC any more: source metadata
  -- may already have run. Burn its own pre-bind entry BEFORE phase refusal.
  PERFORM app_private._native_b_source3_prebind_entry_v3(p_window);
  RAISE EXCEPTION 'B_SOURCE3_BOUND_REQUIRED' USING ERRCODE='23514';
 END IF;
 v_fault_scope:=app_private._native_b_data_scope_v3();
 binding:=app_private._native_b_entry_v3();
 -- No target/argument refusal occurs before the accepted irreversible marker.
 SELECT p.* INTO permission FROM native_b_private_v3.source3_permission p
 WHERE p.singleton AND p.operation_id=binding.operation_id AND p.generation=binding.generation
  AND p.client_incarnation=binding.client_incarnation AND p.data_pid=binding.backend_pid
  AND p.data_backend_start=binding.backend_start AND p.runtime_role_oid=session_user::regrole::oid
  AND p.database_oid=v_fault_scope.database_oid
  AND p.early_transaction_id=binding.transaction_id
  AND p.bound_transaction_id=binding.transaction_id;
 IF NOT FOUND OR p_window IS NULL OR p_window<>permission.window_id
  OR permission.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'B_SOURCE3_BOUND_SCOPE' USING ERRCODE='23514'; END IF;
 RETURN permission;
"""
    + _FAULT
)
_BOUND_SQL: Final = _function(
    "_native_b_source3_bound_scope_v3", "p_window uuid", "uuid", _TABLE, _BOUND_BODY
)
_PAYLOAD_BODY: Final = (
    "\nDECLARE "
    + _FAULT_DECL
    + """
 binding native_b_private_v3.binding%ROWTYPE;
 permission native_b_private_v3.source3_permission%ROWTYPE;
BEGIN
"""
    + _ACTOR
    + """
 permission:=app_private._native_b_source3_member_scope_v3(p_window);
 v_fault_scope:=app_private._native_b_data_scope_v3();
 binding:=app_private._native_b_current_v3();
 IF NOT EXISTS(SELECT 1 FROM native_b_private_v3.request request
    WHERE request.ordinal=binding.request_ordinal AND request.phase='SOURCE_ORIGINAL'
     AND request.active_stage=1 AND request.clear_prepaid
     AND request.owner_user_id=permission.owner_user_id
     AND request.original_request_id=permission.original_request_id
     AND request.served_request_id=permission.served_request_id) THEN
  RAISE EXCEPTION 'B_SOURCE3_PAYLOAD_SCOPE' USING ERRCODE='23514'; END IF;
 -- Pay the closed metadata slice before its relational lookup. Full graph,
 -- payload IO/compiler/driver copies are separate mandatory facade precharges.
 PERFORM app_private._native_b_debit_v3(16384,256,0,4::smallint);
 PERFORM app_private._native_b_source3_current_rows_v3(permission,true);
 RETURN permission;
"""
    + _FAULT
)
_PAYLOAD_SQL: Final = _function(
    "_native_b_source3_payload_scope_v3", "p_window uuid", "uuid", _TABLE, _PAYLOAD_BODY
)

_BOUND_WRAPPER_PREFIX: Final = (
    "\nDECLARE "
    + _FAULT_DECL
    + """
 permission native_b_private_v3.source3_permission%ROWTYPE;
BEGIN
"""
    + _ACTOR
    + """
 permission:=app_private._native_b_source3_member_scope_v3(p_window);
 v_fault_scope:=app_private._native_b_data_scope_v3();
"""
)
_IDENTITY_BODY: Final = """
BEGIN
 PERFORM app_private._native_b_role_v3(true);
 -- Four actual backend scalars only; no member lookup, admission or output.
 RETURN QUERY SELECT identity.database_oid,identity.runtime_role_oid,
  identity.backend_pid,identity.backend_start
 FROM app_private.native_b_backend_identity_v3() identity
 WHERE identity.database_oid=(SELECT oid FROM pg_catalog.pg_database
                              WHERE datname=current_database())
  AND identity.runtime_role_oid=session_user::regrole::oid
  AND identity.backend_pid=pg_backend_pid();
END;
"""
_IDENTITY_SQL: Final = _function(
    "native_b_source3_backend_identity_v3",
    "",
    "",
    "TABLE(database_oid oid,runtime_role_oid oid,backend_pid integer,backend_start timestamptz)",
    _IDENTITY_BODY,
)
_BEGIN_SQL: Final = _function(
    "native_b_source3_begin_request_v3",
    "p_window uuid",
    "uuid",
    "smallint",
    _BOUND_WRAPPER_PREFIX
    + """
 -- These IDs are consistency transport only; authority was the issuer permit.
 RETURN app_private._native_b_source3_begin_member_v3(p_window);
"""
    + _FAULT,
)
_STAGE_SQL: Final = _function(
    "native_b_source3_py_stage_enter_v3",
    "p_window uuid,p_stage smallint",
    "uuid,smallint",
    "void",
    _BOUND_WRAPPER_PREFIX
    + """
 -- Unchanged S2 checks6 CLEAR then1 SOURCE,2 EXPECTED,3 BUILD,4 VERIFY,5 FINALIZE.
 -- Wrong arguments/phases occur after this wrapper's irreversible entry.
 PERFORM app_private.native_b_py_stage_enter_v3(p_stage);
"""
    + _FAULT,
)
_INPUT_SQL: Final = _function(
    "native_b_source3_admit_input_v3",
    "p_window uuid,p_bytes bigint",
    "uuid,bigint",
    "void",
    _BOUND_WRAPPER_PREFIX
    + """
 -- Same nonrefundable input caps; this does not authenticate source bytes.
 PERFORM app_private.native_b_admit_input_v3(p_bytes);
"""
    + _FAULT,
)
_REGISTERED_SQL: Final = _function(
    "native_b_source3_original_registered_v3",
    "p_window uuid",
    "uuid",
    "void",
    _BOUND_WRAPPER_PREFIX
    + """
 -- KERNEL ONLY. The real closed completion facade must first establish all
 -- seven original documents/outcomes/W/F ownership. No runtime ready setter.
 -- This advances continuation, not independent source/consent authority.
 PERFORM app_private.native_b_original_registered_v3();
"""
    + _FAULT,
)
_FAIL_BODY: Final = (
    "\nDECLARE "
    + _FAULT_DECL
    + """
 permission native_b_private_v3.source3_permission%ROWTYPE;
 actual_state text;
BEGIN
"""
    + _ACTOR
    + """
 PERFORM app_private._native_b_role_v3(true);
 SELECT s.state INTO actual_state FROM native_b_private_v3.slot s WHERE s.singleton;
 IF actual_state='CLAIMED' THEN
  permission:=app_private._native_b_source3_prebind_entry_v3(p_window);
 ELSE
  permission:=app_private._native_b_source3_bound_scope_v3(p_window);
 END IF;
 SELECT s.* INTO STRICT v_fault_scope FROM native_b_private_v3.slot s
  WHERE s.singleton AND s.generation=permission.generation AND s.operation_id=permission.operation_id
   AND s.client_incarnation=permission.client_incarnation AND s.data_pid=pg_backend_pid()
   AND s.data_backend_start=permission.data_backend_start;
 PERFORM app_private._native_b_poison_v3(v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid);
"""
    + _FAULT
)
_FAIL_SQL: Final = _function(
    "native_b_source3_fail_v3", "p_window uuid", "uuid", "void", _FAIL_BODY
)

_TRIGGERS: Final = f"""
CREATE TRIGGER native_b_source3_permission_guard_v3 BEFORE INSERT OR UPDATE OR DELETE
 ON {_TABLE} FOR EACH ROW EXECUTE FUNCTION app_private._native_b_source3_permission_guard_v3();
ALTER TABLE {_TABLE} ENABLE ALWAYS TRIGGER native_b_source3_permission_guard_v3;
CREATE TRIGGER native_b_source3_no_truncate_v3 BEFORE TRUNCATE
 ON {_TABLE} FOR EACH STATEMENT EXECUTE FUNCTION app_private._native_b_source3_no_truncate_v3();
ALTER TABLE {_TABLE} ENABLE ALWAYS TRIGGER native_b_source3_no_truncate_v3;
"""

SOURCE3_SQL_SIGNATURES: Final = tuple(
    f"app_private.{name}({identity})" for name, identity, _, _, _ in _specs
)
SOURCE3_SQL_OWNERS: Final = {
    f"app_private.{name}({identity})": owner for name, identity, _, _, owner in _specs
}
SOURCE3_SQL_BODY_DEFINITIONS: Final = {
    f"app_private.{name}({identity})": body for name, identity, _, body, _ in _specs
}
SOURCE3_SQL_BODY_SHA256: Final = {
    signature: sha256(body.encode("utf-8")).hexdigest()
    for signature, body in SOURCE3_SQL_BODY_DEFINITIONS.items()
}
SOURCE3_SQL_SECURITY_DEFINER: Final = {
    signature: "_native_b_source3_permission_guard_v3(" not in signature
    for signature in SOURCE3_SQL_SIGNATURES
}
SOURCE3_SQL_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_SQL_RESULT_TYPES: Final = {
    f"app_private.{name}({identity})": result for name, identity, result, _, _ in _specs
}
SOURCE3_SQL_RETURN_TYPES: Final = {
    signature: "record" if result.startswith("TABLE(") else result
    for signature, result in SOURCE3_SQL_RESULT_TYPES.items()
}
SOURCE3_SQL_RETURNS_SET: Final = {
    signature: result.startswith("TABLE(") for signature, result in SOURCE3_SQL_RESULT_TYPES.items()
}
SOURCE3_ISSUER_SIGNATURE: Final = (
    "app_private.native_b_source3_claim_selection_v3(uuid,uuid,uuid,integer,timestamp"
    " with time zone,"
    "bytea,uuid[],uuid[],uuid[],bigint[])"
)
SOURCE3_BIND_SIGNATURE: Final = "app_private.native_b_source3_bind_v3(uuid,uuid,bigint,uuid)"
SOURCE3_RUNTIME_SIGNATURES: Final = (
    "app_private.native_b_source3_backend_identity_v3()",
    SOURCE3_BIND_SIGNATURE,
    "app_private.native_b_source3_begin_request_v3(uuid)",
    "app_private.native_b_source3_py_stage_enter_v3(uuid,smallint)",
    "app_private.native_b_source3_admit_input_v3(uuid,bigint)",
    "app_private.native_b_source3_fail_v3(uuid)",
)
SOURCE3_CLIENT_ROUTING: Final = {
    "app_private.native_b_backend_identity_v3()": SOURCE3_RUNTIME_SIGNATURES[0],
    "app_private.native_b_bind_v3(uuid,bigint,uuid)": SOURCE3_BIND_SIGNATURE,
    "app_private.native_b_begin_request_v3(uuid,uuid,uuid)": SOURCE3_RUNTIME_SIGNATURES[2],
    "app_private.native_b_py_stage_enter_v3(smallint)": SOURCE3_RUNTIME_SIGNATURES[3],
    "app_private.native_b_admit_input_v3(bigint)": SOURCE3_RUNTIME_SIGNATURES[4],
    "app_private.native_b_fail_v3()": SOURCE3_RUNTIME_SIGNATURES[5],
}
SOURCE3_REGISTERED_SIGNATURE: Final = "app_private.native_b_source3_original_registered_v3(uuid)"
SOURCE3_INTERNAL_SIGNATURES: Final = tuple(
    signature
    for signature in SOURCE3_SQL_SIGNATURES
    if signature != SOURCE3_ISSUER_SIGNATURE and signature not in SOURCE3_RUNTIME_SIGNATURES
)

# Application-table rights are explicit per-column. Runtime/issuer receive none.
# UPDATE on one fixed key per locking-selected table is required by PostgreSQL;
# it belongs exclusively to the non-login kernel with no reachable generic DML.
SOURCE3_METADATA_COLUMNS: Final = {
    "ml.native_training_source_window": (
        "window_id",
        "operation_id",
        "stage_id",
        "server_instance_id",
        "identity_epoch",
        "token_sha256",
        "kind",
        "database_oid",
        "session_role_oid",
        "backend_pid",
        "backend_start",
        "postmaster_start",
        "registration_xid",
        "state",
        "deadline_at",
        "entered_at",
        "terminal_at",
        "terminal_receipt_sha256",
    ),
    "ml.native_validation_stage": (
        "stage_id",
        "operation_id",
        "policy_sha256",
        "state",
        "deadline_at",
        "heartbeat_at",
        "stopped_at",
        "closed_at",
    ),
    "ml.native_training_operation": (
        "operation_id",
        "server_instance_id",
        "identity_epoch",
        "policy_sha256",
        "selection_sha256",
        "state",
        "deadline_at",
        "retention_until",
        "cancelled_at",
        "restore_revoked_at",
        "terminal_latched_at",
        "closed_at",
    ),
    "ml.native_training_escrow_reservation": (
        "operation_id",
        "escrow_generation",
        "state",
        "policy_sha256",
        "closed_at",
        "releasing_at",
    ),
    "ml.native_training_replay_interlock": (
        "server_instance_id",
        "operation_id",
        "stage_id",
        "window_id",
        "identity_epoch",
        "token_sha256",
    ),
    "account.server_instance": ("server_instance_id", "identity_epoch"),
    "account.user_account": ("user_id", "status", "deleted_at"),
    "account.training_consent": ("user_id", "decision", "policy_version", "revision"),
    "ml.native_original_capture_v3": (
        "owner_user_id",
        "original_request_id",
        "original_capture_format_version",
        "consent_generation",
        "consent_receipt_sha256",
        "expires_at",
        "source_codec",
        "source_codec_sha256",
    ),
    "ml.native_capture_lineage_cursor_v3": (
        "owner_user_id",
        "original_request_id",
        "source_generation",
        "state",
        "expires_at",
    ),
    "ml.native_original_capture_format": (
        "owner_user_id",
        "original_request_id",
        "original_capture_format_version",
    ),
    "ml.native_atomic_capture_connection_v3": (
        "owner_user_id",
        "original_request_id",
        "served_request_id",
        "original_capture_format_version",
        "request_hash_format_version",
        "source_codec_sha256",
        "expires_at",
    ),
    "ml.recommendation_request_root_v3": (
        "owner_user_id",
        "original_request_id",
        "served_request_id",
        "original_capture_format_version",
        "request_hash_format_version",
        "source_codec_sha256",
        "expires_at",
    ),
    "ml.native_served_request_hash_format": (
        "owner_user_id",
        "served_request_id",
        "request_hash_format_version",
    ),
    "ml.native_capture_replay_v3": ("owner_user_id", "original_request_id", "expires_at"),
}
SOURCE3_METADATA_LOCK_COLUMNS: Final = {
    table: columns[0] for table, columns in SOURCE3_METADATA_COLUMNS.items()
}

SOURCE3_EXCLUSIVE_ACL_SQL: Final = (
    "-- Deliberate isolated SOURCE3 profile; never restore per request.\n"
    + "\n".join(
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,{_RUNTIME};"
        for signature in SOURCE3_REVOKED_RUNTIME_SIGNATURES
    )
    + f"\nREVOKE ALL ON FUNCTION app_private.native_b_claim_v3"
    f"(uuid,uuid,integer,timestamp with time zone) FROM PUBLIC,{_ISSUER};\n"
)
GRANTS_SQL: Final = (
    SOURCE3_EXCLUSIVE_ACL_SQL
    + f"GRANT SELECT({','.join(name for name, _ in SOURCE3_PERMISSION_COLUMNS)}),"
    f"INSERT({','.join(name for name, _ in SOURCE3_PERMISSION_COLUMNS)}),"
    f"UPDATE({','.join(sorted(_MUTABLE))}) ON {_TABLE} TO {_KERNEL};\n"
    f"GRANT DELETE ON {_TABLE} TO {_KERNEL};\n"
    f"GRANT EXECUTE ON FUNCTION app_private.native_b_claim_v3"
    f"(uuid,uuid,integer,timestamp with time zone) TO {_KERNEL};\n"
    + "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_RUNTIME};"
        for signature in SOURCE3_RUNTIME_SIGNATURES
    )
    + "\n"
    f"GRANT EXECUTE ON FUNCTION app_private._native_b_source3_prebind_serial_v3(),"
    f"app_private._native_b_source3_bind_latch_v3() TO {_KERNEL};\n"
    + "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_KERNEL};"
        for signature in SOURCE3_DELEGATED_SIGNATURES
    )
    + "\n"
    "GRANT USAGE ON SCHEMA account,ml TO native_v3_b_kernel;\n"
    + "\n".join(
        f"GRANT SELECT({','.join(columns)}),UPDATE({SOURCE3_METADATA_LOCK_COLUMNS[table]}) "
        f"ON {table} TO {_KERNEL};"
        for table, columns in SOURCE3_METADATA_COLUMNS.items()
    )
    + "\n"
)
INSTALL_SQL: Final = (
    _PRECONDITIONS
    + SOURCE3_EXCLUSIVE_PRECONDITION_SQL
    + _TABLE_SQL
    + _GUARD_SQL_INVOKER
    + _NO_TRUNCATE_SQL
    + _TRIGGERS
    + _SERIAL_SQL
    + _LATCH_SQL
    + _CURRENT_ROWS_SQL
    + _CLAIM_SQL
    + _PREBIND_ENTRY_SQL
    + _EARLY_SQL
    + _BIND_SQL
    + _BOUND_SQL
    + _PAYLOAD_SQL
    + _IDENTITY_SQL
    + _BEGIN_SQL
    + _STAGE_SQL
    + _INPUT_SQL
    + _REGISTERED_SQL
    + _FAIL_SQL
    + GRANTS_SQL
)
DROP_SQL: Final = (
    "\n".join(
        f"DROP FUNCTION {signature} RESTRICT;" for signature in reversed(SOURCE3_SQL_SIGNATURES[2:])
    )
    + f"\nDROP TABLE {_TABLE} RESTRICT;\n"
    + "\n".join(
        f"DROP FUNCTION {signature} RESTRICT;" for signature in reversed(SOURCE3_SQL_SIGNATURES[:2])
    )
)


def audit_source3_permission_v3(connection: Connection) -> None:
    """Read exact installed definitions/columns/ACL; never create or grant them.

    The trusted C source factory invokes this only within its already-funded
    actual-login audit. It is not a source permit, F/G proof or enable switch.
    """
    from sqlalchemy import text

    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise RuntimeError("B_SOURCE3_AUDIT_TRANSACTION")
    for role in (_RUNTIME, _ISSUER):
        principal = connection.execute(
            text("""
            SELECT r.rolcanlogin,r.rolinherit,r.rolsuper,r.rolcreatedb,r.rolcreaterole,
             r.rolreplication,r.rolbypassrls,
             EXISTS(SELECT 1 FROM pg_auth_members membership WHERE membership.member=r.oid)
            FROM pg_roles r WHERE r.rolname=:role
            """),
            {"role": role},
        ).one_or_none()
        if principal is None or tuple(principal) != (
            True,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
        ):
            raise RuntimeError("B_SOURCE3_AUDIT_EXCLUSIVE_PRINCIPAL")
    for signature in SOURCE3_REVOKED_RUNTIME_SIGNATURES:
        allowed, grant_option = connection.execute(
            text("""
            SELECT has_function_privilege(:role,:signature,'EXECUTE'),
             has_function_privilege(:role,:signature,'EXECUTE WITH GRANT OPTION')
            """),
            {"role": _RUNTIME, "signature": signature},
        ).one()
        if allowed is not False or grant_option is not False:
            raise RuntimeError("B_SOURCE3_AUDIT_ORDINARY_RPC_REACHABLE")
    if (
        connection.scalar(
            text("""
        SELECT has_function_privilege(:role,
         'app_private.native_b_claim_v3(uuid,uuid,integer,timestamp with time zone)','EXECUTE')
        """),
            {"role": _ISSUER},
        )
        is not False
    ):
        raise RuntimeError("B_SOURCE3_AUDIT_ORDINARY_CLAIM_REACHABLE")
    for signature, expected in SOURCE3_SQL_BODY_DEFINITIONS.items():
        row = connection.execute(
            text("""
            SELECT p.prosrc,p.prosecdef,p.provolatile,p.proconfig,l.lanname,
             p.proowner::regrole::text,p.prokind,p.proleakproof,p.prosupport::oid::bigint,
             p.prorettype=to_regtype(:result_type),p.proretset,p.pronargdefaults
            FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            WHERE p.oid=to_regprocedure(:signature)
            """),
            {"signature": signature, "result_type": SOURCE3_SQL_RETURN_TYPES[signature]},
        ).one_or_none()
        if row is None or tuple(row) != (
            expected,
            SOURCE3_SQL_SECURITY_DEFINER[signature],
            "v",
            list(SOURCE3_SQL_PROCONFIG),
            "plpgsql",
            SOURCE3_SQL_OWNERS[signature],
            "f",
            False,
            0,
            True,
            SOURCE3_SQL_RETURNS_SET[signature],
            0,
        ):
            raise RuntimeError("B_SOURCE3_AUDIT_DEFINITION")
        allowed_roles = {SOURCE3_SQL_OWNERS[signature]}
        if signature in SOURCE3_RUNTIME_SIGNATURES:
            allowed_roles.add(_RUNTIME)
        elif signature == SOURCE3_ISSUER_SIGNATURE:
            allowed_roles.add(_ISSUER)
        elif SOURCE3_SQL_OWNERS[signature] == _OWNER:
            allowed_roles.add(_KERNEL)
        unexpected = connection.scalar(
            text("""
            SELECT EXISTS(SELECT 1 FROM pg_proc p,
             LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
             WHERE p.oid=to_regprocedure(:signature) AND
              (acl.grantee=0 OR NOT EXISTS(SELECT 1 FROM pg_roles role
                WHERE role.oid=acl.grantee AND role.rolname=ANY(CAST(:roles AS text[])))
               OR (acl.is_grantable AND acl.grantee<>p.proowner)))
            """),
            {"signature": signature, "roles": sorted(allowed_roles)},
        )
        if unexpected is not False:
            raise RuntimeError("B_SOURCE3_AUDIT_UNEXPECTED_EXECUTOR")
        for role in (_RUNTIME, _ISSUER):
            actual, grant_option = connection.execute(
                text("""
                SELECT has_function_privilege(:role,:signature,'EXECUTE'),
                 has_function_privilege(:role,:signature,'EXECUTE WITH GRANT OPTION')
                """),
                {"role": role, "signature": signature},
            ).one()
            allowed = (role == _RUNTIME and signature in SOURCE3_RUNTIME_SIGNATURES) or (
                role == _ISSUER and signature == SOURCE3_ISSUER_SIGNATURE
            )
            if actual is not allowed or grant_option:
                raise RuntimeError("B_SOURCE3_AUDIT_FUNCTION_ACL")
    if (
        connection.scalar(
            text("""
        SELECT p.proargnames=ARRAY['database_oid','runtime_role_oid','backend_pid','backend_start']
         AND p.proargmodes=ARRAY['t','t','t','t']::"char"[]
         AND p.proallargtypes=ARRAY['oid'::regtype::oid,'oid'::regtype::oid,
          'integer'::regtype::oid,'timestamp with time zone'::regtype::oid]
        FROM pg_proc p
        WHERE p.oid=to_regprocedure('app_private.native_b_source3_backend_identity_v3()')
        """),
        )
        is not True
    ):
        raise RuntimeError("B_SOURCE3_AUDIT_BACKEND_IDENTITY_SHAPE")
    columns = connection.execute(
        text("""
        SELECT a.attname,format_type(a.atttypid,a.atttypmod)
        FROM pg_catalog.pg_attribute a WHERE a.attrelid=to_regclass(:table)
         AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum
        """),
        {"table": _TABLE},
    ).all()
    if tuple(tuple(row) for row in columns) != SOURCE3_PERMISSION_COLUMNS:
        raise RuntimeError("B_SOURCE3_AUDIT_COLUMNS")
    shape = connection.execute(
        text("""
        SELECT c.relowner::regrole::text,c.relkind,c.relpersistence,c.relrowsecurity,
         c.relforcerowsecurity,c.relispartition,
         EXISTS(SELECT 1 FROM pg_inherits i WHERE i.inhparent=c.oid OR i.inhrelid=c.oid)
        FROM pg_class c WHERE c.oid=to_regclass(:table)
        """),
        {"table": _TABLE},
    ).one_or_none()
    if shape is None or tuple(shape) != (_OWNER, "r", "p", False, False, False, False):
        raise RuntimeError("B_SOURCE3_AUDIT_TABLE_SHAPE")
    triggers = connection.execute(
        text("""
        SELECT t.tgname,t.tgtype::int,t.tgenabled,t.tgnargs,t.tgqual IS NULL,
         t.tgattr::text,t.tgargs,t.tgdeferrable,t.tginitdeferred,t.tgconstraint::bigint,
         t.tgfoid=to_regprocedure(CASE t.tgname
          WHEN 'native_b_source3_permission_guard_v3'
           THEN 'app_private._native_b_source3_permission_guard_v3()'
          WHEN 'native_b_source3_no_truncate_v3'
           THEN 'app_private._native_b_source3_no_truncate_v3()' ELSE NULL END)
        FROM pg_trigger t WHERE t.tgrelid=to_regclass(:table) AND NOT t.tgisinternal
        ORDER BY t.tgname
        """),
        {"table": _TABLE},
    ).all()
    if len(triggers) != 2:
        raise RuntimeError("B_SOURCE3_AUDIT_TRIGGER_COUNT")
    for row in triggers:
        truncate = row[0] == "native_b_source3_no_truncate_v3"
        if (
            tuple(row[:6])
            != (
                "native_b_source3_no_truncate_v3"
                if truncate
                else "native_b_source3_permission_guard_v3",
                34 if truncate else 31,
                "A",
                0,
                True,
                "",
            )
            or bytes(row[6]) != b""
            or tuple(row[7:]) != (False, False, 0, True)
        ):
            raise RuntimeError("B_SOURCE3_AUDIT_TRIGGER")
    for role in (_RUNTIME, _ISSUER):
        forbidden = connection.scalar(
            text("""
            SELECT EXISTS(SELECT 1 FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',
             'TRUNCATE','REFERENCES','TRIGGER','MAINTAIN']) privilege
             WHERE has_table_privilege(:role,:table,privilege))
             OR EXISTS(SELECT 1 FROM pg_attribute a
              CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) privilege
              WHERE a.attrelid=to_regclass(:table) AND a.attnum>0 AND NOT a.attisdropped
               AND has_column_privilege(:role,:table,a.attname,privilege))
            """),
            {"role": role, "table": _TABLE},
        )
        if forbidden is not False:
            raise RuntimeError("B_SOURCE3_AUDIT_PRIVATE_ACL")


__all__ = [
    "DROP_SQL",
    "GRANTS_SQL",
    "INSTALL_SQL",
    "SOURCE3_BIND_SIGNATURE",
    "SOURCE3_BOOTSTRAP_STATUS",
    "SOURCE3_CLIENT_ROUTING",
    "SOURCE3_DELEGATED_SIGNATURES",
    "SOURCE3_EARLY_BYTE_QUANTUM",
    "SOURCE3_EARLY_NODE_QUANTUM",
    "SOURCE3_EXCLUSIVE_ACL_SQL",
    "SOURCE3_EXCLUSIVE_PRECONDITION_SQL",
    "SOURCE3_EXCLUSIVE_PROFILE",
    "SOURCE3_INTERNAL_SIGNATURES",
    "SOURCE3_ISSUER_SIGNATURE",
    "SOURCE3_MAX_EARLY_ENTRIES",
    "SOURCE3_METADATA_COLUMNS",
    "SOURCE3_METADATA_LOCK_COLUMNS",
    "SOURCE3_PERMISSION_COLUMNS",
    "SOURCE3_PROFILE_REVIEW_REQUIREMENTS",
    "SOURCE3_REGISTERED_SIGNATURE",
    "SOURCE3_REQUIRED_B_FUNCTIONS",
    "SOURCE3_REVOKED_RUNTIME_SIGNATURES",
    "SOURCE3_RUNTIME_SIGNATURES",
    "SOURCE3_SQL_BODY_DEFINITIONS",
    "SOURCE3_SQL_BODY_SHA256",
    "SOURCE3_SQL_OWNERS",
    "SOURCE3_SQL_PROCONFIG",
    "SOURCE3_SQL_RESULT_TYPES",
    "SOURCE3_SQL_RETURNS_SET",
    "SOURCE3_SQL_RETURN_TYPES",
    "SOURCE3_SQL_SECURITY_DEFINER",
    "SOURCE3_SQL_SIGNATURES",
    "audit_source3_permission_v3",
]
