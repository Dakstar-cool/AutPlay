"""Source-only registration gate candidate; requires narrow C/F SQL review.

Uses the existing serial only while the original B slot is demonstrably IDLE.
No S2 function, role, sequence inventory or reset implementation is changed.
No recovery/reset RPC exists: ambiguous attempts remain quarantined. The fixed
internal terminal deletion requires obsolete generation, actual F CLOSED and
backend absence after unchanged S2 retirement; it changes no sequence value.
"""

from hashlib import sha256
from types import MappingProxyType
from typing import Final

_OWNER: Final = "native_v3_b_catalog_owner"
_KERNEL: Final = "native_v3_b_kernel"
_ISSUER: Final = "native_v3_b_issuer"
_RUNTIME: Final = "native_v3_b_runtime"
_SPECS: list[tuple[str, str, str, str]] = []

_TABLE: Final = """
CREATE TABLE native_b_private_v3.source3_registration_gate(
 singleton boolean PRIMARY KEY CHECK(singleton),
 state text NOT NULL CHECK(state IN('ARMED','PREPARED','REGISTERED','CLAIMED')),
 slot_generation bigint NOT NULL CHECK(slot_generation>=0),
 expected_serial bigint NOT NULL CHECK(expected_serial BETWEEN 0 AND 2),
 window_id uuid NOT NULL,stage_id uuid NOT NULL,operation_id uuid NOT NULL,
 window_token_sha256 bytea NOT NULL CHECK(octet_length(window_token_sha256)=32),
 stage_token_sha256 bytea NOT NULL CHECK(octet_length(stage_token_sha256)=32),
 database_oid oid NOT NULL,runtime_role_oid oid NOT NULL,data_pid integer NOT NULL,
 data_backend_start timestamptz NOT NULL,postmaster_start timestamptz NOT NULL,
 registration_xid xid8,
 CHECK((state='ARMED' AND expected_serial=0 AND registration_xid IS NULL)
  OR(state='PREPARED' AND expected_serial=1 AND registration_xid IS NOT NULL)
  OR(state IN('REGISTERED','CLAIMED') AND expected_serial=2 AND registration_xid IS NOT NULL))
);
ALTER TABLE native_b_private_v3.source3_registration_gate OWNER TO native_v3_b_catalog_owner;
REVOKE ALL ON native_b_private_v3.source3_registration_gate
 FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer,native_v3_b_kernel;
"""

_IDLE: Final = """
 SELECT s.* INTO STRICT slot FROM native_b_private_v3.slot s WHERE s.singleton FOR UPDATE NOWAIT;
 IF slot.state<>'IDLE'
  OR slot.generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION')
  OR EXISTS(SELECT 1 FROM native_b_private_v3.binding)
  OR EXISTS(SELECT 1 FROM native_b_private_v3.request)
  OR EXISTS(SELECT 1 FROM native_b_private_v3.frame) THEN
  RAISE EXCEPTION 'B_SOURCE3_REG_GATE_NOT_IDLE' USING ERRCODE='55000'; END IF;
"""

_DATA: Final = """
 SELECT g.* INTO STRICT gate FROM native_b_private_v3.source3_registration_gate g
  WHERE g.singleton AND g.window_id=p_window FOR UPDATE NOWAIT;
 SELECT a.backend_start INTO STRICT actual_start
  FROM app_private._native_b_activity_v3(pg_backend_pid()) a;
 IF gate.slot_generation<>slot.generation OR gate.database_oid<>(SELECT oid
  FROM pg_catalog.pg_database WHERE datname=current_database())
  OR gate.runtime_role_oid<>session_user::regrole::oid OR gate.data_pid<>pg_backend_pid()
  OR gate.data_backend_start IS DISTINCT FROM actual_start
  OR gate.postmaster_start<>pg_postmaster_start_time()
  OR gate.expected_serial<>app_private._native_b_read_v3('CONTROL','EXECUTION_SERIAL') THEN
  RAISE EXCEPTION 'B_SOURCE3_REG_GATE_IDENTITY_OR_SPENT' USING ERRCODE='23514'; END IF;
"""


def _define(name: str, parameters: str, identity: str, result: str, body: str) -> str:
    signature = f"app_private.{name}({identity})"
    _SPECS.append((signature, result, body, _OWNER))
    return (
        f"CREATE FUNCTION app_private.{name}({parameters}) RETURNS {result}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog\n"
        f"AS $source3_reg_gate${body}$source3_reg_gate$;\n"
        f"ALTER FUNCTION {signature} OWNER TO {_OWNER};\n"
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,{_RUNTIME},{_ISSUER},{_KERNEL};\n"
    )


_ARM: Final = _define(
    "native_b_source3_registration_arm_v3",
    "p_window uuid,p_stage uuid,p_operation uuid,p_token bytea,p_pid integer,p_start timestamptz",
    "uuid,uuid,uuid,bytea,integer,timestamp with time zone",
    "bigint",
    "DECLARE slot native_b_private_v3.slot%ROWTYPE; data record; stage record; item record; BEGIN\n"
    " PERFORM app_private._native_b_role_v3(false);\n"
    " IF current_setting('transaction_isolation')<>'read committed' OR "
    "p_window IS NULL OR p_stage IS NULL"
    " OR p_operation IS NULL OR p_token IS NULL OR octet_length(p_token)<>32 "
    "OR p_pid IS NULL OR p_start IS NULL THEN"
    " RAISE EXCEPTION 'B_SOURCE3_REG_ARM_ARGUMENT'; END IF;\n"
    + _IDLE
    + " IF EXISTS(SELECT 1 FROM native_b_private_v3.source3_registration_gate) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_REG_ARM_ALREADY_SPENT' USING ERRCODE='55000'; END IF;\n"
    " FOR item IN SELECT m.scope,m.metric FROM "
    "native_b_private_v3.sequence_oid m WHERE m.reset_order IS NOT NULL LOOP\n"
    " IF app_private._native_b_read_v3(item.scope,item.metric)<>0 THEN"
    " RAISE EXCEPTION 'B_SOURCE3_REG_ARM_NOT_ZERO'; END IF; END LOOP;\n"
    " PERFORM pg_stat_clear_snapshot();\n"
    " SELECT a.* INTO STRICT data FROM app_private._native_b_activity_v3(p_pid) a"
    " WHERE a.backend_start=p_start AND a.database_oid=(SELECT oid FROM "
    "pg_catalog.pg_database WHERE datname=current_database())"
    " AND a.runtime_role_oid='native_v3_b_runtime'::regrole::oid;\n"
    " SELECT s.stage_token_sha256 INTO STRICT stage FROM ml.native_validation_stage s"
    " WHERE s.stage_id=p_stage AND s.operation_id=p_operation AND s.state='RUNNING'"
    " AND s.closed_at IS NULL AND s.stopped_at IS NULL;\n"
    " INSERT INTO native_b_private_v3.source3_registration_gate "
    "VALUES(true,'ARMED',slot.generation,0,"
    "p_window,p_stage,p_operation,p_token,stage.stage_token_sha256,data.database_oid,data.runtime_role_oid,"
    "p_pid,p_start,pg_postmaster_start_time(),NULL);\n RETURN slot.generation; END;\n",
)

_STEP: Final = _define(
    "_native_b_source3_registration_step_v3",
    "p_window uuid,p_stage uuid,p_secret bytea,p_token bytea,p_register boolean",
    "uuid,uuid,bytea,bytea,boolean",
    "void",
    "DECLARE slot native_b_private_v3.slot%ROWTYPE; gate "
    "native_b_private_v3.source3_registration_gate%ROWTYPE;"
    " actual_start timestamptz; serial bigint; BEGIN\n"
    " PERFORM app_private._native_b_role_v3(true);\n"
    " IF current_setting('transaction_isolation')<>'read committed' OR "
    "p_stage IS NULL OR p_secret IS NULL"
    " OR octet_length(p_secret)<>32 OR p_token IS NULL OR "
    "octet_length(p_token)<>32 OR p_register IS NULL THEN"
    " RAISE EXCEPTION 'B_SOURCE3_REG_GATE_ARGUMENT'; END IF;\n"
    + _IDLE
    + _DATA
    + " IF gate.stage_id<>p_stage OR gate.stage_token_sha256 IS DISTINCT FROM sha256(p_secret)"
    " OR gate.window_token_sha256 IS DISTINCT FROM p_token"
    " OR (NOT p_register AND gate.state<>'ARMED')"
    " OR (p_register AND (gate.state<>'PREPARED' OR "
    "gate.registration_xid<>pg_current_xact_id())) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_REG_GATE_CREDENTIAL_OR_ORDER'; END IF;\n"
    " -- Only after authenticating the exact idle slot/backend/nonce. This is"
    " the irreversible part: a rolled back mirror cannot restore the serial.\n"
    " serial:=nextval('native_b_private_v3.b_control_execution_serial'::regclass);\n"
    " IF serial<>gate.expected_serial+1 THEN RAISE EXCEPTION 'B_SOURCE3_REG_GATE_SERIAL'; END IF;\n"
    " UPDATE native_b_private_v3.source3_registration_gate SET expected_serial=serial,"
    "state=CASE WHEN p_register THEN 'REGISTERED' ELSE 'PREPARED' "
    "END,registration_xid=pg_current_xact_id()"
    " WHERE singleton; END;\n",
)

_CURRENT: Final = _define(
    "_native_b_source3_registration_current_gate_v3",
    "p_window uuid,p_stage uuid,p_secret bytea,p_token bytea,p_registered boolean",
    "uuid,uuid,bytea,bytea,boolean",
    "void",
    "DECLARE slot native_b_private_v3.slot%ROWTYPE; gate "
    "native_b_private_v3.source3_registration_gate%ROWTYPE;"
    " actual_start timestamptz; BEGIN\n PERFORM app_private._native_b_role_v3(true);\n"
    + _IDLE
    + _DATA
    + " IF p_secret IS NULL OR octet_length(p_secret)<>32 OR p_registered IS "
    "NULL OR gate.stage_token_sha256 IS DISTINCT FROM sha256(p_secret)"
    " OR gate.stage_id IS DISTINCT FROM p_stage OR gate.window_token_sha256 "
    "IS DISTINCT FROM p_token"
    " OR gate.state<>(CASE WHEN p_registered THEN 'REGISTERED' ELSE 'PREPARED'"
    " END) OR gate.registration_xid<>pg_current_xact_id() THEN"
    " RAISE EXCEPTION 'B_SOURCE3_REG_GATE_CURRENT'; END IF; END;\n",
)

_CLAIM: Final = _define(
    "_native_b_source3_registration_claim_v3",
    "p_window uuid,p_operation uuid,p_pid integer,p_start timestamptz",
    "uuid,uuid,integer,timestamp with time zone",
    "void",
    "DECLARE slot native_b_private_v3.slot%ROWTYPE; gate "
    "native_b_private_v3.source3_registration_gate%ROWTYPE; BEGIN\n"
    " PERFORM app_private._native_b_role_v3(false);\n"
    + _IDLE
    + " SELECT g.* INTO STRICT gate FROM native_b_private_v3.source3_registration_gate g"
    " WHERE g.singleton AND g.window_id=p_window FOR UPDATE NOWAIT;\n"
    " IF gate.slot_generation<>slot.generation OR gate.state<>'REGISTERED' OR"
    " gate.expected_serial<>2"
    " OR gate.operation_id IS DISTINCT FROM p_operation OR gate.data_pid IS DISTINCT FROM p_pid"
    " OR gate.data_backend_start IS DISTINCT FROM p_start OR "
    "gate.postmaster_start<>pg_postmaster_start_time()"
    " OR gate.registration_xid=pg_current_xact_id()"
    " OR app_private._native_b_read_v3('CONTROL','EXECUTION_SERIAL')<>2"
    " OR NOT EXISTS(SELECT 1 FROM ml.native_training_source_window w WHERE "
    "w.window_id=gate.window_id"
    " AND w.stage_id=gate.stage_id AND w.operation_id=gate.operation_id AND "
    "w.token_sha256=gate.window_token_sha256"
    " AND w.registration_xid=gate.registration_xid::text AND "
    "w.database_oid=gate.database_oid::bigint"
    " AND w.session_role_oid=gate.runtime_role_oid::bigint AND w.backend_pid=gate.data_pid"
    " AND w.backend_start=gate.data_backend_start AND w.postmaster_start=gate.postmaster_start"
    " AND w.state='REGISTERED' AND w.entered_at IS NULL AND w.terminal_at IS NULL) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_REG_GATE_CLAIM'; END IF;\n"
    " -- This helper is INTERNAL, called only immediately before unchanged S2"
    " claim in the same source3 definer call/slot lock. Failure leaves a mismatch.\n"
    " UPDATE native_b_private_v3.source3_registration_gate SET state='CLAIMED' WHERE singleton;\n"
    " PERFORM setval('native_b_private_v3.b_control_execution_serial'::regclass,0,true); END;\n",
)

_RETIRE: Final = _define(
    "_native_b_source3_registration_retire_v3",
    "p_operation uuid,p_generation bigint",
    "uuid,bigint",
    "void",
    "DECLARE slot native_b_private_v3.slot%ROWTYPE; gate "
    "native_b_private_v3.source3_registration_gate%ROWTYPE;"
    " actual_start timestamptz; BEGIN\n PERFORM app_private._native_b_role_v3(false);\n"
    " SELECT s.* INTO STRICT slot FROM native_b_private_v3.slot s WHERE "
    "s.singleton FOR UPDATE NOWAIT;\n"
    " SELECT g.* INTO STRICT gate FROM "
    "native_b_private_v3.source3_registration_gate g WHERE g.singleton FOR "
    "UPDATE NOWAIT;\n"
    " IF p_operation IS NULL OR p_generation IS NULL OR slot.state<>'IDLE' OR"
    " slot.generation<>p_generation"
    " OR "
    "p_generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION')"
    " OR p_generation<2"
    " OR gate.state<>'CLAIMED' OR gate.expected_serial<>2 OR gate.operation_id<>p_operation"
    " OR gate.slot_generation<>p_generation-2 OR gate.postmaster_start<>pg_postmaster_start_time()"
    " OR app_private._native_b_read_v3('CONTROL','EXECUTION_SERIAL')<>0"
    " OR EXISTS(SELECT 1 FROM native_b_private_v3.binding) OR EXISTS(SELECT 1"
    " FROM native_b_private_v3.request)"
    " OR EXISTS(SELECT 1 FROM native_b_private_v3.frame) THEN RAISE EXCEPTION"
    " 'B_SOURCE3_GATE_RETIRE_FENCE'; END IF;\n"
    " PERFORM pg_stat_clear_snapshot();\n"
    " SELECT a.backend_start INTO actual_start FROM "
    "app_private._native_b_activity_v3(gate.data_pid) a;\n"
    " IF (FOUND AND actual_start IS NULL) OR actual_start IS NOT DISTINCT "
    "FROM gate.data_backend_start THEN"
    " RAISE EXCEPTION 'B_SOURCE3_GATE_RETIRE_BACKEND_PRESENT'; END IF;\n"
    " IF NOT EXISTS(SELECT 1 FROM ml.native_training_source_window w WHERE "
    "w.window_id=gate.window_id"
    " AND w.stage_id=gate.stage_id AND w.operation_id=gate.operation_id AND "
    "w.token_sha256=gate.window_token_sha256"
    " AND w.registration_xid=gate.registration_xid::text AND "
    "w.database_oid=gate.database_oid::bigint"
    " AND w.session_role_oid=gate.runtime_role_oid::bigint AND w.backend_pid=gate.data_pid"
    " AND w.backend_start=gate.data_backend_start AND w.postmaster_start=gate.postmaster_start"
    " AND w.state='CLOSED' AND w.terminal_at IS NOT NULL AND "
    "octet_length(w.terminal_receipt_sha256)=32) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_GATE_RETIRE_F_CLOSED_REQUIRED'; END IF;\n"
    " -- No reset here. Only the exact obsolete gate is deleted after unchanged"
    " S2 retirement and independently visible F CLOSED/backend absence.\n"
    " DELETE FROM native_b_private_v3.source3_registration_gate WHERE singleton; END;\n",
)

SOURCE3_REGISTRATION_GATE_SQL_SIGNATURES: Final = tuple(spec[0] for spec in _SPECS)
SOURCE3_REGISTRATION_GATE_ISSUER_SIGNATURES: Final = (SOURCE3_REGISTRATION_GATE_SQL_SIGNATURES[0],)
SOURCE3_REGISTRATION_GATE_INTERNAL_SIGNATURES: Final = SOURCE3_REGISTRATION_GATE_SQL_SIGNATURES[1:]
SOURCE3_REGISTRATION_GATE_RUNTIME_SIGNATURES: Final = ()
SOURCE3_REGISTRATION_GATE_RESULT_TYPES: Final = MappingProxyType({s: r for s, r, _, _ in _SPECS})
SOURCE3_REGISTRATION_GATE_RESULT_SETS: Final = MappingProxyType({s: False for s, _, _, _ in _SPECS})
SOURCE3_REGISTRATION_GATE_BODY_DEFINITIONS: Final = MappingProxyType(
    {s: b for s, _, b, _ in _SPECS}
)
SOURCE3_REGISTRATION_GATE_BODY_SHA256: Final = MappingProxyType(
    {s: sha256(b.encode()).hexdigest() for s, _, b, _ in _SPECS}
)
SOURCE3_REGISTRATION_GATE_SQL_OWNERS: Final = MappingProxyType({s: o for s, _, _, o in _SPECS})
SOURCE3_REGISTRATION_GATE_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_REGISTRATION_GATE_SECURITY_DEFINER: Final = True
INSTALL_SQL: Final = _TABLE + _ARM + _STEP + _CURRENT + _CLAIM + _RETIRE
GRANTS_SQL_CANDIDATE: Final = (
    "GRANT USAGE ON SCHEMA ml TO native_v3_b_catalog_owner;\n"
    "GRANT EXECUTE ON FUNCTION "
    "app_private._native_b_source3_registration_retire_v3(uuid,bigint) TO "
    "native_v3_b_kernel;\n"
    "GRANT SELECT(terminal_at,terminal_receipt_sha256) ON "
    "ml.native_training_source_window TO native_v3_b_catalog_owner;\n"
    "GRANT EXECUTE ON FUNCTION "
    "app_private.native_b_source3_registration_arm_v3(uuid,uuid,uuid,bytea,integer,timestamp"
    " with time zone) TO native_v3_b_issuer;\n"
    "GRANT EXECUTE ON FUNCTION "
    "app_private._native_b_source3_registration_step_v3(uuid,uuid,bytea,bytea,boolean)"
    " TO native_v3_b_kernel;\n"
    "GRANT EXECUTE ON FUNCTION "
    "app_private._native_b_source3_registration_current_gate_v3(uuid,uuid,bytea,bytea,boolean)"
    " TO native_v3_b_kernel;\n"
    "GRANT EXECUTE ON FUNCTION "
    "app_private._native_b_source3_registration_claim_v3(uuid,uuid,integer,timestamp"
    " with time zone) TO native_v3_b_kernel;\n"
    # Owner arm/claim need this explicit reviewed finite projection; runtime
    # still has no application table privilege or serial UPDATE privilege.
    "GRANT "
    "SELECT(stage_id,operation_id,stage_token_sha256,state,closed_at,stopped_at)"
    " ON ml.native_validation_stage TO native_v3_b_catalog_owner;\n"
    "GRANT "
    "SELECT(window_id,stage_id,operation_id,token_sha256,registration_xid,database_oid,session_role_oid,backend_pid,backend_start,postmaster_start,state,entered_at,terminal_at)"
    " ON ml.native_training_source_window TO native_v3_b_catalog_owner;\n"
)
