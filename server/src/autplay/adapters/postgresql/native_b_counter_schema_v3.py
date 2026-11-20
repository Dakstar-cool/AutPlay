# ruff: noqa: E501
"""Isolated private B counter SQL; importing this module executes no SQL.

ROLE_SQL, INSTALL_SQL and GRANTS_SQL are separately reviewed provisioning
artifacts. Nothing imports them from a live migration or public composition.
No source, writer, global hook or successful drain is activated by this seam.
"""

from __future__ import annotations

from typing import Final

_OWNER: Final = "native_v3_b_catalog_owner"
_KERNEL: Final = "native_v3_b_kernel"
_RUNTIME: Final = "native_v3_b_runtime"
_ISSUER: Final = "native_v3_b_issuer"
_MAX: Final = 9_223_372_036_854_775_807
_OP: Final = (6_442_450_944, 768_000_000, 536_870_912)
_REQUEST: Final = (335_544_320, 40_000_000, 33_554_432)
_METRICS: Final = ("WORK_B", "NODES", "INPUT_B")
SEQUENCE_SPECS: Final = (
    *(("OP", name, maximum) for name, maximum in zip(_METRICS, _OP, strict=True)),
    *(
        (f"R{ordinal:02d}", name, maximum)
        for ordinal in range(1, 17)
        for name, maximum in zip(_METRICS, _REQUEST, strict=True)
    ),
    ("CONTROL", "CLAIM_GENERATION", _MAX),
    ("CONTROL", "BIND_LATCH", 1),
    ("CONTROL", "REQUEST_ORDINAL", 16),
    ("CONTROL", "EXECUTION_SERIAL", _MAX),
    ("CONTROL", "POISON", 1),
)

ROLE_SQL: Final = (
    "\n".join(
        f"CREATE ROLE {role} {login} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;"
        for role, login in (
            (_OWNER, "NOLOGIN"),
            (_KERNEL, "NOLOGIN"),
            (_ISSUER, "LOGIN"),
            (_RUNTIME, "LOGIN"),
        )
    )
    + "\nGRANT native_v3_b_runtime TO native_v3_b_catalog_owner WITH INHERIT TRUE, SET FALSE, ADMIN FALSE;\n"
)


def _sequence_name(scope: str, metric: str) -> str:
    return "native_b_private_v3.b_" + scope.lower() + "_" + metric.lower()


_RESET_KEYS: Final = (
    *((scope, metric) for scope, metric, _ in SEQUENCE_SPECS if scope != "CONTROL"),
    ("CONTROL", "EXECUTION_SERIAL"),
    ("CONTROL", "REQUEST_ORDINAL"),
    ("CONTROL", "BIND_LATCH"),
    ("CONTROL", "POISON"),
)
_RESET_ORDER: Final = {key: index for index, key in enumerate(_RESET_KEYS, 1)}

_STORE_SQL: Final = (
    r"""
DO $$ BEGIN
 IF to_regnamespace('app_private') IS NULL THEN
  RAISE EXCEPTION 'B_PRIVATE_SCHEMA_REQUIRED' USING ERRCODE='55000'; END IF;
END $$;
CREATE SCHEMA native_b_private_v3 AUTHORIZATION native_v3_b_catalog_owner;
GRANT USAGE,CREATE ON SCHEMA app_private TO native_v3_b_catalog_owner,native_v3_b_kernel;
CREATE TABLE native_b_private_v3.slot (
 singleton boolean PRIMARY KEY CHECK(singleton),state text NOT NULL
  CHECK(state IN ('IDLE','CLAIMED','BOUND','RETIRING','QUARANTINED')),
 generation bigint NOT NULL CHECK(generation>=0),operation_id uuid,client_incarnation uuid,
 database_oid oid,runtime_role_oid oid,data_pid integer,data_backend_start timestamptz,
 CHECK((state='IDLE' AND operation_id IS NULL AND client_incarnation IS NULL
  AND database_oid IS NULL AND runtime_role_oid IS NULL AND data_pid IS NULL AND data_backend_start IS NULL)
 OR (state<>'IDLE' AND operation_id IS NOT NULL AND client_incarnation IS NOT NULL
  AND database_oid IS NOT NULL AND runtime_role_oid IS NOT NULL AND data_pid>0 AND data_backend_start IS NOT NULL))
);
INSERT INTO native_b_private_v3.slot(singleton,state,generation) VALUES(true,'IDLE',0);
CREATE TABLE native_b_private_v3.sequence_oid (
 scope text NOT NULL CHECK(scope='OP' OR scope='CONTROL' OR scope~'^R(0[1-9]|1[0-6])$'),
 metric text NOT NULL CHECK(metric IN ('WORK_B','NODES','INPUT_B','CLAIM_GENERATION','BIND_LATCH','REQUEST_ORDINAL','EXECUTION_SERIAL','POISON')),
 sequence_oid oid NOT NULL UNIQUE,maximum bigint NOT NULL CHECK(maximum>0),
 reset_order smallint UNIQUE CHECK(reset_order BETWEEN 1 AND 55),PRIMARY KEY(scope,metric),
 CHECK((metric='CLAIM_GENERATION' AND scope='CONTROL' AND reset_order IS NULL)
  OR (metric<>'CLAIM_GENERATION' AND reset_order IS NOT NULL))
);
CREATE TABLE native_b_private_v3.binding (
 singleton boolean PRIMARY KEY CHECK(singleton),operation_id uuid NOT NULL,generation bigint NOT NULL,
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL,backend_start timestamptz NOT NULL,
 client_incarnation uuid NOT NULL,expected_serial bigint NOT NULL CHECK(expected_serial>0),
 request_ordinal smallint NOT NULL CHECK(request_ordinal BETWEEN 0 AND 16),finished boolean NOT NULL DEFAULT false
);
CREATE TABLE native_b_private_v3.request (
 ordinal smallint PRIMARY KEY CHECK(ordinal BETWEEN 1 AND 16),owner_user_id uuid NOT NULL,
 original_request_id uuid NOT NULL UNIQUE,served_request_id uuid NOT NULL UNIQUE,
 phase text NOT NULL CHECK(phase IN ('RESERVED','SOURCE_ORIGINAL','ORIGINAL_REGISTERED','EXPECTED',
  'BUILD','VERIFY','FINALIZE','SQL_WRITER','FENCE_FRAME_USERS','GRAPH_DRAINED','PY_FINAL',
  'COMPILER_LAST','REQUEST_SEALED','FAILED_TERMINAL')),
 clear_prepaid boolean NOT NULL DEFAULT false,active_stage smallint NOT NULL DEFAULT 0 CHECK(active_stage BETWEEN 0 AND 5),
 CHECK(original_request_id<>served_request_id)
);
CREATE TABLE native_b_private_v3.frame (
 depth smallint PRIMARY KEY CHECK(depth BETWEEN 1 AND 64),kind smallint NOT NULL CHECK(kind BETWEEN 1 AND 4),
 bytes bigint NOT NULL CHECK(bytes>=0),request_ordinal smallint NOT NULL,
 transaction_id xid8 NOT NULL,backend_pid integer NOT NULL,generation bigint NOT NULL
);
CREATE TABLE native_b_private_v3.dirty (
 singleton boolean PRIMARY KEY CHECK(singleton),epoch bigint NOT NULL CHECK(epoch>=0)
);
INSERT INTO native_b_private_v3.dirty VALUES(true,0);
CREATE TABLE native_b_private_v3.touch (
 relation_code smallint PRIMARY KEY CHECK(relation_code BETWEEN 1 AND 15),touched boolean NOT NULL
);
INSERT INTO native_b_private_v3.touch SELECT n,false FROM generate_series(1,15) n;
"""
    + "\n".join(
        f"CREATE SEQUENCE {_sequence_name(scope, metric)} AS bigint INCREMENT BY 1 MINVALUE 0 MAXVALUE {maximum} START WITH 0 CACHE 1 NO CYCLE;\n"
        f"SELECT setval('{_sequence_name(scope, metric)}'::regclass,0,true);\n"
        f"ALTER SEQUENCE {_sequence_name(scope, metric)} OWNER TO {_OWNER};\n"
        "INSERT INTO native_b_private_v3.sequence_oid VALUES("
        f"'{scope}','{metric}','{_sequence_name(scope, metric)}'::regclass::oid,{maximum},"
        f"{_RESET_ORDER.get((scope, metric), 'NULL')});"
        for scope, metric, maximum in SEQUENCE_SPECS
    )
    + "\n"
    + "\n".join(
        f"ALTER TABLE native_b_private_v3.{table} OWNER TO {_OWNER};"
        for table in ("slot", "sequence_oid", "binding", "request", "frame", "dirty", "touch")
    )
)

_SPECS: list[tuple[str, str, str, str, str]] = []

# Implicit NULL initialization performs no database expression, preserving the
# retirement-finish first-expression xid gate. These are local, not stored rows.
_FAULT_DECL: Final = """v_fault_scope native_b_private_v3.slot%ROWTYPE;
 v_fault_role oid; v_fault_pid integer; v_fault_xid xid8;
"""
# Capture scalar actor facts before scope activation. These facts alone grant
# nothing; a missing authenticated scope still cannot target any generation.
# No separate actor SELECT may interrupt after a valid scope has been stored.
_CAPTURE_ACTOR: Final = """
 SELECT session_user::regrole::oid,pg_backend_pid(),pg_current_xact_id()
  INTO v_fault_role,v_fault_pid,v_fault_xid;
"""
_CAPTURE_DATA: Final = _CAPTURE_ACTOR + " v_fault_scope:=app_private._native_b_data_scope_v3();\n"
_DATA_SCOPED: Final = frozenset(
    (
        "_native_b_current_v3",
        "_native_b_entry_v3",
        "_native_b_debit_v3",
        "native_b_begin_request_v3",
        "native_b_py_stage_enter_v3",
        "native_b_admit_input_v3",
        "native_b_fail_v3",
        "native_b_finish_v3",
        "native_b_sql_frame_enter_v3",
        "native_b_sql_frame_grow_v3",
        "native_b_sql_frame_exit_v3",
        "native_b_sql_mark_dirty_v3",
        "native_b_original_registered_v3",
        "native_b_source_v3",
        "native_b_write_v3",
        "native_b_drain_v3",
    )
)
_ENTRY_FENCED: Final = frozenset(
    (
        "native_b_begin_request_v3",
        "native_b_py_stage_enter_v3",
        "native_b_admit_input_v3",
        "native_b_fail_v3",
        "native_b_finish_v3",
        "native_b_sql_frame_enter_v3",
        "native_b_sql_frame_grow_v3",
        "native_b_sql_frame_exit_v3",
        "native_b_sql_mark_dirty_v3",
        "native_b_original_registered_v3",
        "native_b_source_v3",
        "native_b_write_v3",
        "native_b_drain_v3",
    )
)


def _define(name: str, arguments: str, types: str, result: str, body: str, owner: str) -> str:
    if name in _ENTRY_FENCED:
        # The closed target-entry inventory replaces its single initial current
        # check with the private irreversible fence in the actual emitted SQL.
        if body.count("app_private._native_b_current_v3()") != 1:
            raise ValueError("A fenced target entry must have exactly one current check")
        body = body.replace(
            "app_private._native_b_current_v3()", "app_private._native_b_entry_v3()", 1
        )
    if "app_private._native_b_poison_v3(v_fault_scope," in body:
        if body.lstrip().startswith("DECLARE"):
            body = body.replace("DECLARE", "DECLARE " + _FAULT_DECL, 1)
        else:
            body = "\nDECLARE " + _FAULT_DECL + body
        if name in _DATA_SCOPED:
            body = body.replace("BEGIN\n", "BEGIN\n" + _CAPTURE_DATA, 1)
    _SPECS.append((name, arguments, types, result, owner))
    return (
        f"CREATE FUNCTION app_private.{name}({arguments}) RETURNS {result}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $b$\n"
        + body
        + "\n$b$;\n"
        + f"ALTER FUNCTION app_private.{name}({types}) OWNER TO {owner};\n"
        + f"REVOKE ALL ON FUNCTION app_private.{name}({types}) FROM PUBLIC;\n"
    )


_FAULT: Final = """
EXCEPTION
 WHEN query_canceled OR assert_failure THEN
  PERFORM app_private._native_b_poison_v3(v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid); RAISE;
 WHEN OTHERS THEN
  PERFORM app_private._native_b_poison_v3(v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid); RAISE;
END;
"""

_PRIMITIVES = _define(
    "_native_b_role_v3",
    "p_runtime boolean",
    "boolean",
    "void",
    r"""
BEGIN
 IF p_runtime IS NULL OR (p_runtime AND session_user::text<>'native_v3_b_runtime')
  OR (NOT p_runtime AND session_user::text<>'native_v3_b_issuer')
  OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user
   AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
   AND NOT r.rolreplication AND NOT r.rolbypassrls AND NOT r.rolinherit)
  OR pg_has_role(session_user,'native_v3_b_catalog_owner','MEMBER')
  OR pg_has_role(session_user,'native_v3_b_kernel','MEMBER')
  OR (p_runtime AND pg_has_role(session_user,'native_v3_b_issuer','MEMBER'))
  OR (NOT p_runtime AND pg_has_role(session_user,'native_v3_b_runtime','MEMBER')) THEN
  RAISE EXCEPTION 'B_AUTH_ROLE' USING ERRCODE='42501'; END IF;
END;
""",
    _OWNER,
)

_PRIMITIVES += _define(
    "_native_b_read_v3",
    "p_scope text,p_metric text",
    "text,text",
    "bigint",
    r"""
DECLARE item record; value bigint; called boolean;
BEGIN
 SELECT m.sequence_oid,m.maximum INTO item FROM native_b_private_v3.sequence_oid m
  WHERE m.scope=p_scope AND m.metric=p_metric;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_sequence s JOIN pg_catalog.pg_class c ON c.oid=s.seqrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE s.seqrelid=item.sequence_oid
   AND n.nspname='native_b_private_v3' AND c.relkind='S'
   AND c.relowner='native_v3_b_catalog_owner'::regrole AND s.seqtypid='bigint'::regtype
   AND s.seqmin=0 AND s.seqmax=item.maximum AND s.seqincrement=1 AND s.seqcache=1 AND NOT s.seqcycle) THEN
  RAISE EXCEPTION 'B_FIXED_SEQUENCE' USING ERRCODE='23514'; END IF;
 -- Only the immutable56-row map supplies this OID; no caller regclass/name.
 EXECUTE format('SELECT last_value,is_called FROM %s',item.sequence_oid::regclass) INTO value,called;
 IF called IS DISTINCT FROM true OR value<0 OR value>item.maximum THEN
  RAISE EXCEPTION 'B_SEQUENCE_VALUE' USING ERRCODE='23514'; END IF;
 RETURN value;
END;
""",
    _OWNER,
)

_PRIMITIVES += _define(
    "_native_b_set_counter_v3",
    "p_scope text,p_metric text,p_value bigint",
    "text,text,bigint",
    "void",
    r"""
DECLARE item record; old_value bigint;
BEGIN
 IF p_metric NOT IN ('WORK_B','NODES','INPUT_B') OR p_scope='CONTROL' THEN
  RAISE EXCEPTION 'B_COUNTER_KIND' USING ERRCODE='23514'; END IF;
 old_value:=app_private._native_b_read_v3(p_scope,p_metric);
 SELECT m.sequence_oid,m.maximum INTO item FROM native_b_private_v3.sequence_oid m
  WHERE m.scope=p_scope AND m.metric=p_metric;
 IF p_value IS NULL OR p_value<old_value OR p_value>item.maximum THEN
  RAISE EXCEPTION 'B_COUNTER_MONOTONE' USING ERRCODE='23514'; END IF;
 PERFORM setval(item.sequence_oid::regclass,p_value,true);
END;
""",
    _OWNER,
)

_PRIMITIVES += _define(
    "_native_b_poison_v3",
    "p_scope native_b_private_v3.slot,p_actor_role oid,p_actor_pid integer,p_actor_xid xid8",
    "native_b_private_v3.slot,oid,integer,xid8",
    "void",
    r"""
DECLARE current_slot native_b_private_v3.slot%ROWTYPE; actual_start timestamptz;
BEGIN
 -- No token is acquired merely by observing a current row or accepting caller
 -- locators. Unauthenticated/pre-ownership failures never poison another claim.
 IF p_scope.singleton IS DISTINCT FROM true OR p_scope.operation_id IS NULL
  OR p_scope.client_incarnation IS NULL OR p_actor_role IS NULL OR p_actor_pid IS NULL
  OR p_actor_xid IS NULL OR p_actor_role<>session_user::regrole::oid
  OR p_actor_pid<>pg_backend_pid() OR p_actor_xid IS DISTINCT FROM pg_current_xact_id_if_assigned()
  OR p_actor_role NOT IN ('native_v3_b_runtime'::regrole::oid,'native_v3_b_issuer'::regrole::oid) THEN
  RETURN; END IF;
 PERFORM app_private._native_b_role_v3(p_actor_role='native_v3_b_runtime'::regrole::oid);
 -- The failed EXCEPTION subtransaction already unwound. Acquire a NEW shared
 -- lock; blocking is deliberate. A temporary issuer lock that rolls back must
 -- not silently lose a genuine current owner's terminal poison.
 SELECT s.* INTO current_slot FROM native_b_private_v3.slot s WHERE s.singleton FOR SHARE;
 IF current_slot.singleton IS DISTINCT FROM true
  OR current_slot.state NOT IN ('CLAIMED','BOUND','RETIRING')
  OR current_slot.state IS DISTINCT FROM p_scope.state
  OR current_slot.generation IS DISTINCT FROM p_scope.generation
  OR p_scope.generation IS DISTINCT FROM app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION')
  OR ROW(current_slot.operation_id,current_slot.client_incarnation,current_slot.database_oid,
    current_slot.runtime_role_oid,current_slot.data_pid,current_slot.data_backend_start)
   IS DISTINCT FROM ROW(p_scope.operation_id,p_scope.client_incarnation,p_scope.database_oid,
    p_scope.runtime_role_oid,p_scope.data_pid,p_scope.data_backend_start)
  OR p_scope.database_oid<>(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database()) THEN
  RETURN; END IF;
 IF p_actor_role='native_v3_b_runtime'::regrole::oid THEN
  IF p_scope.state NOT IN ('CLAIMED','BOUND') OR p_actor_role<>p_scope.runtime_role_oid
   OR p_actor_pid<>p_scope.data_pid THEN RETURN; END IF;
  SELECT a.backend_start INTO actual_start FROM app_private._native_b_activity_v3(p_actor_pid) a;
  IF actual_start IS DISTINCT FROM p_scope.data_backend_start THEN RETURN; END IF;
 END IF;
 -- Issuer actor is its authenticated login/PID/fullxid. It receives no new
 -- membership to inspect issuer backend_start; p_scope retains the DATA tuple.
 PERFORM setval('native_b_private_v3.b_control_poison'::regclass,1,true);
EXCEPTION
 WHEN serialization_failure THEN
  -- An old RR locker cannot refresh/adopt the new generation. No global fallback.
  RETURN;
END;
""",
    _OWNER,
)

_PRIMITIVES += _define(
    "_native_b_activity_v3",
    "p_pid integer",
    "integer",
    "TABLE(database_oid oid,runtime_role_oid oid,backend_pid integer,backend_start timestamptz)",
    r"""
BEGIN
 IF p_pid IS NULL OR p_pid<=0 OR pg_size_bytes(current_setting('track_activity_query_size'))>4096 THEN
  RAISE EXCEPTION 'B_BACKEND_METADATA_BOUND' USING ERRCODE='23514'; END IF;
 -- Pass an exact PID to avoid pg_stat_activity's ALL-backend result tuplestore.
 -- The complete one-row server tuple's query field is clipped to the checked
 --4KiB setting. The public result exposes only these four fixed scalars.
 RETURN QUERY SELECT a.datid,a.usesysid,a.pid,a.backend_start
  FROM pg_catalog.pg_stat_get_activity(p_pid) a WHERE a.pid=p_pid;
END;
""",
    _OWNER,
)

_PRIMITIVES += _define(
    "_native_b_data_scope_v3",
    "",
    "",
    "native_b_private_v3.slot",
    r"""
DECLARE slot native_b_private_v3.slot%ROWTYPE; binding native_b_private_v3.binding%ROWTYPE;
 actual_start timestamptz; current_generation bigint;
BEGIN
 PERFORM app_private._native_b_role_v3(true);
"""
    + _CAPTURE_ACTOR
    + r"""
 SELECT s.* INTO slot FROM native_b_private_v3.slot s WHERE s.singleton FOR SHARE;
 current_generation:=app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION');
 SELECT b.* INTO binding FROM native_b_private_v3.binding b WHERE b.singleton;
 SELECT a.backend_start INTO actual_start FROM app_private._native_b_activity_v3(pg_backend_pid()) a;
 -- Immutable issued identity FIRST. No latch/serial/poison/ordinal consistency
 -- condition may precede fault-scope capture for the authenticated owner.
 IF slot.singleton IS DISTINCT FROM true OR slot.state<>'BOUND'
  OR slot.generation<>current_generation OR binding.singleton IS DISTINCT FROM true
  OR binding.generation<>slot.generation OR binding.operation_id<>slot.operation_id
  OR binding.client_incarnation<>slot.client_incarnation
  OR binding.transaction_id<>pg_current_xact_id() OR binding.backend_pid<>pg_backend_pid()
  OR binding.backend_start IS DISTINCT FROM actual_start OR slot.data_pid<>pg_backend_pid()
  OR slot.data_backend_start IS DISTINCT FROM actual_start
  OR slot.database_oid<>(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
  OR slot.runtime_role_oid<>session_user::regrole::oid THEN
  RAISE EXCEPTION 'B_CURRENT_IDENTITY' USING ERRCODE='23514'; END IF;
 v_fault_scope:=slot;
 IF current_setting('transaction_isolation')<>'repeatable read' THEN
  RAISE EXCEPTION 'B_RR_REQUIRED' USING ERRCODE='23514'; END IF;
 RETURN slot;
"""
    + _FAULT,
    _OWNER,
)

_PRIMITIVES += _define(
    "_native_b_current_v3",
    "",
    "",
    "native_b_private_v3.binding",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE;
BEGIN
 -- _define inserts authenticated data-scope capture before this consistency
 -- work, and every outer data entry independently captures before invoking us.
 SELECT b.* INTO binding FROM native_b_private_v3.binding b WHERE b.singleton;
 IF binding.singleton IS DISTINCT FROM true
  OR binding.generation IS DISTINCT FROM v_fault_scope.generation
  OR binding.operation_id IS DISTINCT FROM v_fault_scope.operation_id
  OR binding.finished OR app_private._native_b_read_v3('CONTROL','BIND_LATCH')<>1
  OR app_private._native_b_read_v3('CONTROL','POISON')<>0
  OR app_private._native_b_read_v3('CONTROL','EXECUTION_SERIAL')<>binding.expected_serial
  OR app_private._native_b_read_v3('CONTROL','REQUEST_ORDINAL')<>binding.request_ordinal
  OR (SELECT count(*) FROM native_b_private_v3.request)<>binding.request_ordinal THEN
  RAISE EXCEPTION 'B_CURRENT_BINDING' USING ERRCODE='23514'; END IF;
 RETURN binding;
"""
    + _FAULT,
    _OWNER,
)

_PRIMITIVES += _define(
    "_native_b_entry_v3",
    "",
    "",
    "native_b_private_v3.binding",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE; serial bigint;
BEGIN
 binding:=app_private._native_b_current_v3();
 IF binding.expected_serial=9223372036854775807 THEN
  RAISE EXCEPTION 'B_ENTRY_SERIAL_EXHAUSTED' USING ERRCODE='54000'; END IF;
 -- First irreversible admission marker, before caller phase/argument checks.
 -- Publication is transactional; SERIAL is not. A later caught failure leaves
 -- a mismatch even if the scoped poison helper itself cannot complete.
 serial:=nextval('native_b_private_v3.b_control_execution_serial'::regclass);
 IF serial<>binding.expected_serial+1 THEN
  RAISE EXCEPTION 'B_ENTRY_SERIAL' USING ERRCODE='23514'; END IF;
 UPDATE native_b_private_v3.binding b SET expected_serial=serial
  WHERE b.singleton AND b.expected_serial=binding.expected_serial
   AND b.generation=binding.generation AND b.operation_id=binding.operation_id
   AND b.client_incarnation=binding.client_incarnation AND b.transaction_id=binding.transaction_id
   AND b.backend_pid=binding.backend_pid AND b.backend_start IS NOT DISTINCT FROM binding.backend_start
   AND b.request_ordinal=binding.request_ordinal AND NOT b.finished
  RETURNING b.* INTO binding;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_ENTRY_PUBLICATION' USING ERRCODE='23514'; END IF;
 RETURN binding;
"""
    + _FAULT,
    _OWNER,
)

_PRIMITIVES += _define(
    "_native_b_terminal_v3",
    "p_operation uuid",
    "uuid",
    "void",
    r"""
DECLARE slot native_b_private_v3.slot%ROWTYPE; actual_start timestamptz;
 binding native_b_private_v3.binding%ROWTYPE; outcome text;
BEGIN
 PERFORM app_private._native_b_role_v3(false);
 SELECT s.* INTO slot FROM native_b_private_v3.slot s WHERE s.singleton FOR UPDATE NOWAIT;
 IF slot.singleton IS DISTINCT FROM true OR slot.state NOT IN ('CLAIMED','BOUND','RETIRING') OR slot.generation<>
  app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION') THEN
  RAISE EXCEPTION 'B_RETIREMENT_GENERATION' USING ERRCODE='23514'; END IF;
 IF slot.operation_id IS DISTINCT FROM p_operation THEN
  RAISE EXCEPTION 'B_RETIREMENT_LOCATOR' USING ERRCODE='23514'; END IF;
 PERFORM pg_stat_clear_snapshot();
 SELECT a.backend_start INTO actual_start FROM app_private._native_b_activity_v3(slot.data_pid) a;
 IF FOUND AND actual_start IS NULL THEN
  RAISE EXCEPTION 'B_BACKEND_IDENTITY_UNREADABLE' USING ERRCODE='55000'; END IF;
 IF actual_start IS NOT DISTINCT FROM slot.data_backend_start THEN
  -- Alive is permitted ONLY for the exact committed successful finish path.
  -- An abort/rolled-back first bind requires actual original backend absence;
  -- idle, free advisory lock or missing rollbackable binding is not death.
  SELECT b.* INTO binding FROM native_b_private_v3.binding b WHERE b.singleton;
  IF NOT FOUND OR slot.state NOT IN ('BOUND','RETIRING') OR NOT binding.finished
   OR binding.operation_id<>slot.operation_id
   OR (slot.state='BOUND' AND binding.generation<>slot.generation)
   OR (slot.state='RETIRING' AND (slot.generation=0 OR binding.generation<>slot.generation-1))
   OR binding.backend_pid<>slot.data_pid OR binding.backend_start IS DISTINCT FROM slot.data_backend_start
   OR binding.client_incarnation<>slot.client_incarnation
   OR EXISTS(SELECT 1 FROM native_b_private_v3.frame)
   OR EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE r.phase<>'REQUEST_SEALED') THEN
   RAISE EXCEPTION 'B_ABORT_BACKEND_STILL_PRESENT' USING ERRCODE='55000'; END IF;
  -- Public lock metadata exposes no query text or personal source data.
  IF EXISTS(SELECT 1 FROM pg_catalog.pg_locks l WHERE l.pid=slot.data_pid AND l.locktype='virtualxid'
   AND l.mode='ExclusiveLock' AND l.granted) THEN
   RAISE EXCEPTION 'B_DATA_TRANSACTION_ACTIVE' USING ERRCODE='55000'; END IF;
  outcome:=pg_xact_status(binding.transaction_id);
  IF outcome IS DISTINCT FROM 'committed' THEN
   RAISE EXCEPTION 'B_DATA_OUTCOME_UNKNOWN' USING ERRCODE='55000'; END IF;
 END IF;
 -- Supplementary exclusion: successful data finish, or actual backend death,
 -- must have released its session lock. Row exclusion remains mandatory too.
 IF NOT pg_try_advisory_xact_lock(714928136450029::bigint) THEN
  RAISE EXCEPTION 'B_DATA_SESSION_ACTIVE' USING ERRCODE='55000'; END IF;
 -- Exact trusted issuer code must ALSO prove its actual client graph clear or
 -- process-incarnation death and known transaction outcome. SQL accepts no bool.
END;
""",
    _OWNER,
)

_PRIMITIVES += _define(
    "_native_b_reset55_v3",
    "p_operation uuid,p_generation bigint",
    "uuid,bigint",
    "void",
    r"""
DECLARE slot native_b_private_v3.slot%ROWTYPE; item record; n integer:=0;
BEGIN
 PERFORM app_private._native_b_role_v3(false);
 SELECT s.* INTO slot FROM native_b_private_v3.slot s WHERE s.singleton FOR UPDATE NOWAIT;
 IF slot.singleton IS DISTINCT FROM true OR slot.state<>'RETIRING' OR slot.operation_id IS DISTINCT FROM p_operation
  OR slot.generation IS DISTINCT FROM p_generation
  OR slot.generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION')
  OR (SELECT count(*) FROM native_b_private_v3.sequence_oid)<>56
  OR (SELECT count(*) FROM native_b_private_v3.sequence_oid WHERE reset_order IS NOT NULL)<>55 THEN
  RAISE EXCEPTION 'B_RESET_FENCE' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT m.scope,m.metric,m.sequence_oid,m.reset_order FROM native_b_private_v3.sequence_oid m
  WHERE m.reset_order BETWEEN 1 AND 55 ORDER BY m.reset_order LOOP
  IF item.metric='CLAIM_GENERATION' OR item.reset_order<>n+1 THEN
   RAISE EXCEPTION 'B_RESET_INVENTORY' USING ERRCODE='23514'; END IF;
  PERFORM app_private._native_b_read_v3(item.scope,item.metric);
  PERFORM setval(item.sequence_oid::regclass,0,true);
  n:=n+1;
 END LOOP;
 IF n<>55 OR slot.generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION') THEN
  RAISE EXCEPTION 'B_RESET_INCOMPLETE' USING ERRCODE='23514'; END IF;
END;
""",
    _OWNER,
)

_DEBIT = _define(
    "_native_b_debit_v3",
    "p_work bigint,p_nodes bigint,p_input bigint,p_reason smallint",
    "bigint,bigint,bigint,smallint",
    "smallint",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE; v_ordinal smallint; scope text; serial bigint;
 rw bigint; rn bigint; ri bigint; ow bigint; onn bigint; oi bigint; work bigint; nodes bigint;
 first_work bigint:=0; first_nodes bigint:=0;
BEGIN
 binding:=app_private._native_b_current_v3();
 IF binding.expected_serial=9223372036854775807 THEN
  RAISE EXCEPTION 'B_SERIAL_EXHAUSTED' USING ERRCODE='54000'; END IF;
 -- SERIAL advances BEFORE amount/cap refusal. It is never adopted on retry.
 serial:=nextval('native_b_private_v3.b_control_execution_serial'::regclass);
 IF serial<>binding.expected_serial+1 OR p_work IS NULL OR p_nodes IS NULL OR p_input IS NULL OR p_reason IS NULL
  OR p_work<0 OR p_nodes<0 OR p_input<0 OR p_reason NOT IN (1,2,3,4,5)
  OR p_work>335544320-65536 OR p_nodes>40000000-4096 OR p_input>33554432 THEN
  RAISE EXCEPTION 'B_DEBIT_ARGUMENT' USING ERRCODE='23514'; END IF;
 v_ordinal:=binding.request_ordinal;
 IF p_reason=1 THEN
  IF v_ordinal>=16 OR (v_ordinal>0 AND NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r
   WHERE r.ordinal=v_ordinal AND r.phase='REQUEST_SEALED')) THEN
   RAISE EXCEPTION 'B_REQUEST_LIMIT_OR_UNSEALED' USING ERRCODE='54000'; END IF;
  v_ordinal:=nextval('native_b_private_v3.b_control_request_ordinal'::regclass)::smallint;
  IF v_ordinal<>binding.request_ordinal+1 OR p_work<>0 OR p_nodes<>0 OR p_input<>8388608 THEN
   RAISE EXCEPTION 'B_BEGIN_ESCROW' USING ERRCODE='23514'; END IF;
  IF v_ordinal=1 THEN
   -- Attribute the ALREADY spent operation bootstrap to request1 too. Do not
   -- charge OP again; this preserves the conservative first-request ceiling.
   first_work:=1048576; first_nodes:=65536;
  END IF;
 ELSIF v_ordinal=0 OR NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE r.ordinal=v_ordinal) THEN
  RAISE EXCEPTION 'B_REQUEST_REQUIRED' USING ERRCODE='23514';
 END IF;
 IF p_reason IN (3,4,5) AND NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r
  WHERE r.ordinal=v_ordinal AND r.clear_prepaid AND r.phase NOT IN ('RESERVED','FAILED_TERMINAL','REQUEST_SEALED')) THEN
  RAISE EXCEPTION 'B_SQL_TARGET_PHASE' USING ERRCODE='23514'; END IF;
 scope:='R'||lpad(v_ordinal::text,2,'0'); work:=p_work+65536; nodes:=p_nodes+4096;
 rw:=app_private._native_b_read_v3(scope,'WORK_B'); rn:=app_private._native_b_read_v3(scope,'NODES');
 ri:=app_private._native_b_read_v3(scope,'INPUT_B');
 ow:=app_private._native_b_read_v3('OP','WORK_B'); onn:=app_private._native_b_read_v3('OP','NODES');
 oi:=app_private._native_b_read_v3('OP','INPUT_B');
 -- Subtract BEFORE adding; no bigint overflow or free failed composite.
 IF rw>335544320-work-first_work OR rn>40000000-nodes-first_nodes OR ri>33554432-p_input
  OR ow>6442450944-work OR onn>768000000-nodes OR oi>536870912-p_input THEN
  RAISE EXCEPTION 'B_BUDGET_EXHAUSTED' USING ERRCODE='54000'; END IF;
 PERFORM app_private._native_b_set_counter_v3('OP','WORK_B',ow+work);
 PERFORM app_private._native_b_set_counter_v3(scope,'WORK_B',rw+work+first_work);
 PERFORM app_private._native_b_set_counter_v3('OP','NODES',onn+nodes);
 PERFORM app_private._native_b_set_counter_v3(scope,'NODES',rn+nodes+first_nodes);
 PERFORM app_private._native_b_set_counter_v3('OP','INPUT_B',oi+p_input);
 PERFORM app_private._native_b_set_counter_v3(scope,'INPUT_B',ri+p_input);
 UPDATE native_b_private_v3.binding b SET expected_serial=serial,request_ordinal=v_ordinal
  WHERE b.singleton AND b.expected_serial=binding.expected_serial;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SERIAL_PUBLICATION' USING ERRCODE='23514'; END IF;
 RETURN v_ordinal;
"""
    + _FAULT,
    _OWNER,
)

_CONTROL = _define(
    "native_b_claim_v3",
    "p_operation uuid,p_client uuid,p_data_pid integer,p_data_start timestamptz",
    "uuid,uuid,integer,timestamptz",
    "bigint",
    r"""
DECLARE slot native_b_private_v3.slot%ROWTYPE; data record; v_generation bigint; item record;
BEGIN
 PERFORM app_private._native_b_role_v3(false);
"""
    + _CAPTURE_ACTOR
    + r"""
 IF current_setting('transaction_isolation')<>'read committed' OR p_operation IS NULL OR p_client IS NULL
  OR p_data_pid IS NULL OR p_data_start IS NULL THEN
  RAISE EXCEPTION 'B_CLAIM_ARGUMENT' USING ERRCODE='23514'; END IF;
 SELECT s.* INTO slot FROM native_b_private_v3.slot s WHERE s.singleton FOR UPDATE NOWAIT;
 v_generation:=app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION');
 IF slot.singleton IS DISTINCT FROM true OR slot.state<>'IDLE' OR slot.generation<>v_generation OR v_generation=9223372036854775807
  OR EXISTS(SELECT 1 FROM native_b_private_v3.binding) OR EXISTS(SELECT 1 FROM native_b_private_v3.request)
  OR EXISTS(SELECT 1 FROM native_b_private_v3.frame) THEN
  RAISE EXCEPTION 'B_CLAIM_UNAVAILABLE' USING ERRCODE='55000'; END IF;
 PERFORM pg_stat_clear_snapshot();
 SELECT a.database_oid,a.runtime_role_oid,a.backend_start INTO data
  FROM app_private._native_b_activity_v3(p_data_pid) a
  WHERE a.backend_start=p_data_start AND a.database_oid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
   AND a.runtime_role_oid='native_v3_b_runtime'::regrole::oid;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_ACTUAL_DATA_BACKEND' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT m.scope,m.metric FROM native_b_private_v3.sequence_oid m WHERE m.reset_order IS NOT NULL LOOP
  IF app_private._native_b_read_v3(item.scope,item.metric)<>0 THEN
   RAISE EXCEPTION 'B_IDLE_NOT_ZERO' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF (SELECT count(*) FROM native_b_private_v3.sequence_oid)<>56 THEN
  RAISE EXCEPTION 'B_FIXED_INVENTORY' USING ERRCODE='23514'; END IF;
 -- Never reset/adopt generation. A rollback after this advance quarantines.
 v_generation:=nextval('native_b_private_v3.b_control_claim_generation'::regclass);
 IF v_generation<>slot.generation+1 THEN RAISE EXCEPTION 'B_CLAIM_GENERATION' USING ERRCODE='23514'; END IF;
 UPDATE native_b_private_v3.slot SET state='CLAIMED',generation=v_generation,operation_id=p_operation,
  client_incarnation=p_client,database_oid=data.database_oid,runtime_role_oid=data.runtime_role_oid,
  data_pid=p_data_pid,data_backend_start=p_data_start WHERE singleton;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_CLAIM_PUBLICATION' USING ERRCODE='23514'; END IF;
 -- Token C is tied to this function's OWN nextval and authenticated new claim,
 -- never a current row adopted by a refused busy/stale claim. On unwind the
 -- old IDLE row versus G=C already revokes; scoped poison then refuses mutation.
 SELECT s.* INTO v_fault_scope FROM native_b_private_v3.slot s
  WHERE s.singleton AND s.state='CLAIMED' AND s.operation_id=p_operation AND s.generation=v_generation;
 -- Already-zero initialization is fixed and occurs only after revocation.
 FOR item IN SELECT m.sequence_oid FROM native_b_private_v3.sequence_oid m
  WHERE m.reset_order IS NOT NULL ORDER BY m.reset_order LOOP
  PERFORM setval(item.sequence_oid::regclass,0,true);
 END LOOP;
 RETURN v_generation;
"""
    + _FAULT,
    _OWNER,
)

_CONTROL += _define(
    "native_b_retirement_prepare_v3",
    "p_operation uuid,p_generation bigint,p_client uuid",
    "uuid,bigint,uuid",
    "bigint",
    r"""
DECLARE slot native_b_private_v3.slot%ROWTYPE; v_generation bigint;
BEGIN
 PERFORM app_private._native_b_role_v3(false);
"""
    + _CAPTURE_ACTOR
    + r"""
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'B_ISSUER_RC_REQUIRED' USING ERRCODE='23514'; END IF;
 SELECT s.* INTO slot FROM native_b_private_v3.slot s WHERE s.singleton FOR UPDATE NOWAIT;
 v_generation:=app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION');
 IF slot.singleton IS DISTINCT FROM true OR slot.state NOT IN ('CLAIMED','BOUND') OR slot.operation_id IS DISTINCT FROM p_operation
  OR slot.generation IS DISTINCT FROM p_generation OR slot.client_incarnation IS DISTINCT FROM p_client
  OR slot.generation<>v_generation OR v_generation=9223372036854775807 THEN
  RAISE EXCEPTION 'B_RETIREMENT_PREPARE' USING ERRCODE='55000'; END IF;
 -- Expected N/incarnation come from the exact issued client, not observed G.
 -- UUID reuse cannot make an old prepare own a newly issued generation.
 v_fault_scope:=slot;
 PERFORM app_private._native_b_terminal_v3(p_operation);
 -- Transaction A: advance F BEFORE ANY reset/control-row deletion. No resets
 -- here. The exact trusted issuer must observe successful COMMIT before B.
 v_generation:=nextval('native_b_private_v3.b_control_claim_generation'::regclass);
 IF v_generation<>slot.generation+1 THEN RAISE EXCEPTION 'B_RETIREMENT_GENERATION' USING ERRCODE='23514'; END IF;
 -- Locally issued F, not a reread/adoption of whatever generation is current.
 v_fault_scope.generation:=v_generation; v_fault_scope.state:='RETIRING';
 UPDATE native_b_private_v3.slot SET state='RETIRING',generation=v_generation WHERE singleton;
 RETURN v_generation;
"""
    + _FAULT,
    _OWNER,
)

_CONTROL += _define(
    "native_b_retirement_finish_v3",
    "p_operation uuid,p_generation bigint",
    "uuid,bigint",
    "void",
    r"""
DECLARE slot native_b_private_v3.slot%ROWTYPE; item record;
BEGIN
 -- FIRST database expression: excludes A+B in one transaction/subtransaction.
 IF pg_current_xact_id_if_assigned() IS NOT NULL THEN
  RAISE EXCEPTION 'B_RETIREMENT_FRESH_TRANSACTION' USING ERRCODE='23514'; END IF;
 PERFORM app_private._native_b_role_v3(false);
"""
    + _CAPTURE_ACTOR
    + r"""
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'B_ISSUER_RC_REQUIRED' USING ERRCODE='23514'; END IF;
 SELECT s.* INTO slot FROM native_b_private_v3.slot s WHERE s.singleton FOR UPDATE NOWAIT;
 IF slot.singleton IS DISTINCT FROM true OR slot.state<>'RETIRING' OR slot.operation_id IS DISTINCT FROM p_operation
  OR slot.generation IS DISTINCT FROM p_generation
  OR slot.generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION') THEN
  RAISE EXCEPTION 'B_RETIREMENT_FENCE' USING ERRCODE='23514'; END IF;
 v_fault_scope:=slot;
 PERFORM app_private._native_b_terminal_v3(p_operation);
 PERFORM app_private._native_b_reset55_v3(p_operation,p_generation);
 IF (SELECT count(*) FROM native_b_private_v3.request)>16 OR (SELECT count(*) FROM native_b_private_v3.frame)>64
  OR (SELECT count(*) FROM native_b_private_v3.binding)>1 OR (SELECT count(*) FROM native_b_private_v3.touch)<>15 THEN
  RAISE EXCEPTION 'B_RETIREMENT_CONTROL_BOUND' USING ERRCODE='23514'; END IF;
 DELETE FROM native_b_private_v3.frame;
 DELETE FROM native_b_private_v3.request;
 DELETE FROM native_b_private_v3.binding;
 UPDATE native_b_private_v3.dirty SET epoch=0 WHERE singleton;
 UPDATE native_b_private_v3.touch SET touched=false;
 FOR item IN SELECT m.scope,m.metric FROM native_b_private_v3.sequence_oid m WHERE m.reset_order IS NOT NULL LOOP
  IF app_private._native_b_read_v3(item.scope,item.metric)<>0 THEN
   RAISE EXCEPTION 'B_RETIREMENT_NOT_ZERO' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM native_b_private_v3.frame) OR EXISTS(SELECT 1 FROM native_b_private_v3.request)
  OR EXISTS(SELECT 1 FROM native_b_private_v3.binding)
  OR p_generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION') THEN
  RAISE EXCEPTION 'B_RETIREMENT_INCOMPLETE' USING ERRCODE='23514'; END IF;
 UPDATE native_b_private_v3.slot SET state='IDLE',operation_id=NULL,client_incarnation=NULL,
  database_oid=NULL,runtime_role_oid=NULL,data_pid=NULL,data_backend_start=NULL
  WHERE singleton AND state='RETIRING' AND generation=p_generation;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_RETIREMENT_PUBLICATION' USING ERRCODE='23514'; END IF;
 -- A return is not COMMIT proof. Unknown B outcome is locally quarantined;
 -- the controller must not adopt a stored IDLE or retry/reset as a new claim.
"""
    + _FAULT,
    _OWNER,
)

_DATA = _define(
    "native_b_backend_identity_v3",
    "",
    "",
    "TABLE(database_oid oid,runtime_role_oid oid,backend_pid integer,backend_start timestamptz)",
    r"""
BEGIN
 PERFORM app_private._native_b_role_v3(true);
 RETURN QUERY SELECT a.database_oid,a.runtime_role_oid,a.backend_pid,a.backend_start
  FROM app_private._native_b_activity_v3(pg_backend_pid()) a
  WHERE a.runtime_role_oid=session_user::regrole::oid;
END;
""",
    _KERNEL,
)

_DATA += _define(
    "native_b_bind_v3",
    "p_operation uuid,p_generation bigint,p_client uuid",
    "uuid,bigint,uuid",
    "void",
    r"""
DECLARE slot native_b_private_v3.slot%ROWTYPE; actual_start timestamptz; serial bigint;
BEGIN
 PERFORM app_private._native_b_role_v3(true);
"""
    + _CAPTURE_ACTOR
    + r"""
 SELECT s.* INTO slot FROM native_b_private_v3.slot s WHERE s.singleton FOR SHARE;
 SELECT a.backend_start INTO actual_start FROM app_private._native_b_activity_v3(pg_backend_pid()) a;
 IF slot.singleton IS DISTINCT FROM true OR slot.state<>'CLAIMED' OR slot.operation_id IS DISTINCT FROM p_operation
  OR slot.client_incarnation IS DISTINCT FROM p_client OR slot.generation IS DISTINCT FROM p_generation
  OR slot.generation<>app_private._native_b_read_v3('CONTROL','CLAIM_GENERATION')
  OR slot.data_pid<>pg_backend_pid() OR slot.data_backend_start IS DISTINCT FROM actual_start
  OR slot.database_oid<>(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
  OR slot.runtime_role_oid<>session_user::regrole::oid THEN
  RAISE EXCEPTION 'B_FIRST_BIND_IDENTITY' USING ERRCODE='23514'; END IF;
 v_fault_scope:=slot;
 IF EXISTS(SELECT 1 FROM native_b_private_v3.binding)
  OR app_private._native_b_read_v3('CONTROL','BIND_LATCH')<>0
  OR app_private._native_b_read_v3('CONTROL','POISON')<>0
  OR app_private._native_b_read_v3('CONTROL','REQUEST_ORDINAL')<>0
  OR app_private._native_b_read_v3('CONTROL','EXECUTION_SERIAL')<>0 THEN
  RAISE EXCEPTION 'B_FIRST_BIND' USING ERRCODE='23514'; END IF;
 -- FIRST irreversible bind admission, before post-authentication refusals.
 PERFORM setval('native_b_private_v3.b_control_bind_latch'::regclass,1,true);
 IF current_setting('transaction_isolation')<>'repeatable read' THEN
  RAISE EXCEPTION 'B_RR_REQUIRED' USING ERRCODE='23514'; END IF;
 IF NOT pg_try_advisory_lock(714928136450029::bigint) THEN
  RAISE EXCEPTION 'B_SESSION_SLOT_BUSY' USING ERRCODE='55000'; END IF;
 serial:=nextval('native_b_private_v3.b_control_execution_serial'::regclass);
 IF serial<>1 OR app_private._native_b_read_v3('OP','WORK_B')<>0
  OR app_private._native_b_read_v3('OP','NODES')<>0 OR app_private._native_b_read_v3('OP','INPUT_B')<>0 THEN
  RAISE EXCEPTION 'B_FIRST_BIND_BOOTSTRAP' USING ERRCODE='23514'; END IF;
 -- Includes fixed claim/prepare/finish quanta:196608B/12288 nodes are NOT
 -- returned to targets. Remaining bootstrap metadata headroom851968B/53248.
 PERFORM app_private._native_b_set_counter_v3('OP','WORK_B',1048576);
 PERFORM app_private._native_b_set_counter_v3('OP','NODES',65536);
 INSERT INTO native_b_private_v3.binding VALUES(true,p_operation,p_generation,pg_current_xact_id(),
  pg_backend_pid(),actual_start,p_client,1,0,false);
 UPDATE native_b_private_v3.slot SET state='BOUND' WHERE singleton;
"""
    + _FAULT,
    _OWNER,
)

_DATA += _define(
    "native_b_begin_request_v3",
    "p_owner uuid,p_original uuid,p_served uuid",
    "uuid,uuid,uuid",
    "smallint",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE; v_ordinal smallint;
BEGIN
 binding:=app_private._native_b_current_v3();
 IF p_owner IS NULL OR p_original IS NULL OR p_served IS NULL OR p_original=p_served
  OR EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE r.original_request_id IN (p_original,p_served)
   OR r.served_request_id IN (p_original,p_served)) OR EXISTS(SELECT 1 FROM native_b_private_v3.frame) THEN
  RAISE EXCEPTION 'B_REQUEST_IDS_OR_FRAMES' USING ERRCODE='23514'; END IF;
 -- The begin's fixed64KiB/4096 quantum is the preburned terminal-refusal tail.
 -- PY_OUTPUT_ESCROW is charged ONCE to both cumulative INPUT_B counters.
 v_ordinal:=app_private._native_b_debit_v3(0,0,8388608,1::smallint);
 INSERT INTO native_b_private_v3.request(ordinal,owner_user_id,original_request_id,served_request_id,phase)
  VALUES(v_ordinal,p_owner,p_original,p_served,'RESERVED');
 RETURN v_ordinal;
"""
    + _FAULT,
    _KERNEL,
)

_DATA += _define(
    "native_b_py_stage_enter_v3",
    "p_stage smallint",
    "smallint",
    "void",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE; request native_b_private_v3.request%ROWTYPE;
 work bigint; nodes bigint; required text; next_phase text;
BEGIN
 binding:=app_private._native_b_current_v3();
 SELECT r.* INTO request FROM native_b_private_v3.request r WHERE r.ordinal=binding.request_ordinal;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_REQUEST_REQUIRED' USING ERRCODE='23514'; END IF;
 IF p_stage=6 THEN
  IF request.phase<>'RESERVED' OR request.clear_prepaid OR request.active_stage<>0 THEN
   RAISE EXCEPTION 'B_CLEAR_TAIL_FIRST_ONCE' USING ERRCODE='23514'; END IF;
  work:=4194304; nodes:=1000000;
 ELSE
  IF NOT request.clear_prepaid OR p_stage IS NULL OR p_stage NOT BETWEEN 1 AND 5
   OR p_stage<>request.active_stage+1 THEN
   RAISE EXCEPTION 'B_FIXED_STAGE_ORDER' USING ERRCODE='23514'; END IF;
  CASE p_stage
   WHEN 1 THEN work:=16777216; nodes:=1500000; required:='RESERVED'; next_phase:='SOURCE_ORIGINAL';
   WHEN 2 THEN work:=8388608; nodes:=1000000; required:='ORIGINAL_REGISTERED'; next_phase:='EXPECTED';
   WHEN 3 THEN work:=8388608; nodes:=1000000; required:='EXPECTED'; next_phase:='BUILD';
   WHEN 4 THEN work:=8388608; nodes:=1000000; required:='BUILD'; next_phase:='VERIFY';
   WHEN 5 THEN work:=4194304; nodes:=500000; required:='VERIFY'; next_phase:='FINALIZE';
  END CASE;
  IF request.phase<>required THEN RAISE EXCEPTION 'B_FIXED_STAGE_PHASE' USING ERRCODE='23514'; END IF;
 END IF;
 PERFORM app_private._native_b_debit_v3(work,nodes,0,2::smallint);
 IF p_stage=6 THEN UPDATE native_b_private_v3.request SET clear_prepaid=true WHERE ordinal=binding.request_ordinal;
 ELSE UPDATE native_b_private_v3.request SET phase=next_phase,active_stage=p_stage WHERE ordinal=binding.request_ordinal;
 END IF;
"""
    + _FAULT,
    _KERNEL,
)

_DATA += _define(
    "native_b_admit_input_v3",
    "p_bytes bigint",
    "bigint",
    "void",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE;
BEGIN
 binding:=app_private._native_b_current_v3();
 IF NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE r.ordinal=binding.request_ordinal
  AND r.phase='SOURCE_ORIGINAL' AND r.clear_prepaid) OR p_bytes IS NULL OR p_bytes<=0 THEN
  RAISE EXCEPTION 'B_INPUT_PHASE' USING ERRCODE='23514'; END IF;
 PERFORM app_private._native_b_debit_v3(p_bytes,1,p_bytes,3::smallint);
"""
    + _FAULT,
    _KERNEL,
)

_DATA += _define(
    "native_b_fail_v3",
    "",
    "",
    "void",
    r"""
BEGIN
 PERFORM app_private._native_b_current_v3();
 PERFORM app_private._native_b_poison_v3(v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid);
"""
    + _FAULT,
    _KERNEL,
)

_DATA += _define(
    "native_b_finish_v3",
    "",
    "",
    "void",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE;
BEGIN
 binding:=app_private._native_b_current_v3();
 IF EXISTS(SELECT 1 FROM native_b_private_v3.frame)
  OR EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE r.phase<>'REQUEST_SEALED') THEN
  RAISE EXCEPTION 'B_OPERATION_NOT_SEALED' USING ERRCODE='23514'; END IF;
 -- No caller can seal a request. With source/write/drain disabled only the
 -- zero-request counter operation can finish successfully in this packet.
 UPDATE native_b_private_v3.binding SET finished=true WHERE singleton;
 IF NOT pg_advisory_unlock(714928136450029::bigint) THEN
  RAISE EXCEPTION 'B_SESSION_SLOT_LOCK' USING ERRCODE='23514'; END IF;
"""
    + _FAULT,
    _KERNEL,
)

_FRAMES = _define(
    "native_b_sql_frame_enter_v3",
    "p_kind smallint,p_bytes bigint",
    "smallint,bigint",
    "smallint",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE; v_depth integer; used bigint; cap bigint;
BEGIN
 binding:=app_private._native_b_current_v3();
 PERFORM app_private._native_b_debit_v3(0,0,0,5::smallint);
 IF NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE r.ordinal=binding.request_ordinal
  AND r.clear_prepaid AND r.phase IN ('SOURCE_ORIGINAL','ORIGINAL_REGISTERED','EXPECTED','BUILD',
   'VERIFY','FINALIZE','SQL_WRITER','FENCE_FRAME_USERS','GRAPH_DRAINED')) THEN
  RAISE EXCEPTION 'B_SQL_FRAME_PHASE' USING ERRCODE='23514'; END IF;
 SELECT coalesce(max(f.depth),0)+1 INTO v_depth FROM native_b_private_v3.frame f;
 cap:=CASE p_kind WHEN 1 THEN 8192 WHEN 2 THEN 32768 WHEN 3 THEN 524288 WHEN 4 THEN 8388608 END;
 SELECT coalesce(sum(f.bytes),0) INTO used FROM native_b_private_v3.frame f WHERE f.kind=p_kind;
 IF cap IS NULL OR p_bytes IS NULL OR p_bytes<0 OR p_bytes>cap OR used>cap-p_bytes OR v_depth>64
  OR EXISTS(SELECT 1 FROM native_b_private_v3.frame f WHERE f.transaction_id<>binding.transaction_id
   OR f.backend_pid<>binding.backend_pid OR f.generation<>binding.generation
   OR f.request_ordinal<>binding.request_ordinal) THEN
  RAISE EXCEPTION 'B_SQL_FRAME_CAP' USING ERRCODE='54000'; END IF;
 -- SQL-only actual local objects. PY held D8KiB/S96KiB and the cumulative
 -- request INPUT_B/output escrow can NEVER enter this refundable table.
 INSERT INTO native_b_private_v3.frame VALUES(v_depth::smallint,p_kind,p_bytes,binding.request_ordinal,
  binding.transaction_id,binding.backend_pid,binding.generation);
 RETURN v_depth::smallint;
"""
    + _FAULT,
    _KERNEL,
)

_FRAMES += _define(
    "native_b_sql_frame_grow_v3",
    "p_depth smallint,p_total bigint",
    "smallint,bigint",
    "void",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE; frame native_b_private_v3.frame%ROWTYPE; used bigint; cap bigint;
BEGIN
 binding:=app_private._native_b_current_v3();
 PERFORM app_private._native_b_debit_v3(0,0,0,5::smallint);
 IF NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE r.ordinal=binding.request_ordinal
  AND r.clear_prepaid AND r.phase IN ('SOURCE_ORIGINAL','ORIGINAL_REGISTERED','EXPECTED','BUILD',
   'VERIFY','FINALIZE','SQL_WRITER','FENCE_FRAME_USERS','GRAPH_DRAINED')) THEN
  RAISE EXCEPTION 'B_SQL_FRAME_PHASE' USING ERRCODE='23514'; END IF;
 SELECT f.* INTO frame FROM native_b_private_v3.frame f WHERE f.depth=p_depth;
 IF NOT FOUND OR p_depth<>(SELECT max(f.depth) FROM native_b_private_v3.frame f)
  OR frame.transaction_id<>binding.transaction_id OR frame.backend_pid<>binding.backend_pid
  OR frame.generation<>binding.generation OR frame.request_ordinal<>binding.request_ordinal THEN
  RAISE EXCEPTION 'B_SQL_FRAME_LIFO' USING ERRCODE='23514'; END IF;
 cap:=CASE frame.kind WHEN 1 THEN 8192 WHEN 2 THEN 32768 WHEN 3 THEN 524288 WHEN 4 THEN 8388608 END;
 SELECT coalesce(sum(f.bytes),0) INTO used FROM native_b_private_v3.frame f WHERE f.kind=frame.kind AND f.depth<>p_depth;
 IF p_total IS NULL OR p_total<frame.bytes OR p_total>cap OR used>cap-p_total THEN
  RAISE EXCEPTION 'B_SQL_FRAME_GROW' USING ERRCODE='54000'; END IF;
 UPDATE native_b_private_v3.frame f SET bytes=p_total WHERE f.depth=p_depth;
"""
    + _FAULT,
    _KERNEL,
)

_FRAMES += _define(
    "native_b_sql_frame_exit_v3",
    "p_depth smallint",
    "smallint",
    "void",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE; frame native_b_private_v3.frame%ROWTYPE;
BEGIN
 binding:=app_private._native_b_current_v3();
 PERFORM app_private._native_b_debit_v3(0,0,0,5::smallint);
 IF NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE r.ordinal=binding.request_ordinal
  AND r.clear_prepaid AND r.phase IN ('SOURCE_ORIGINAL','ORIGINAL_REGISTERED','EXPECTED','BUILD',
   'VERIFY','FINALIZE','SQL_WRITER','FENCE_FRAME_USERS','GRAPH_DRAINED')) THEN
  RAISE EXCEPTION 'B_SQL_FRAME_PHASE' USING ERRCODE='23514'; END IF;
 SELECT f.* INTO frame FROM native_b_private_v3.frame f WHERE f.depth=p_depth;
 IF NOT FOUND OR p_depth<>(SELECT max(f.depth) FROM native_b_private_v3.frame f)
  OR frame.transaction_id<>binding.transaction_id OR frame.backend_pid<>binding.backend_pid
  OR frame.generation<>binding.generation OR frame.request_ordinal<>binding.request_ordinal THEN
  RAISE EXCEPTION 'B_SQL_FRAME_LIFO' USING ERRCODE='23514'; END IF;
 -- Only a reviewed SQL body may call after dropping ALL child aliases and
 -- transferring returned data to a still-held parent. No runtime EXECUTE.
 DELETE FROM native_b_private_v3.frame f WHERE f.depth=p_depth;
"""
    + _FAULT,
    _KERNEL,
)

_FRAMES += _define(
    "native_b_sql_mark_dirty_v3",
    "p_relation smallint",
    "smallint",
    "void",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE; v_epoch bigint;
BEGIN
 binding:=app_private._native_b_current_v3();
 PERFORM app_private._native_b_debit_v3(0,0,0,4::smallint);
 IF NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE r.ordinal=binding.request_ordinal
  AND r.clear_prepaid AND r.phase IN ('SOURCE_ORIGINAL','ORIGINAL_REGISTERED','EXPECTED','BUILD','VERIFY','FINALIZE','SQL_WRITER')) THEN
  RAISE EXCEPTION 'B_DIRTY_FENCE' USING ERRCODE='23514'; END IF;
 IF p_relation IS NULL OR p_relation NOT BETWEEN 1 AND 15 THEN
  RAISE EXCEPTION 'B_FIXED_TOUCH' USING ERRCODE='23514'; END IF;
 SELECT d.epoch INTO v_epoch FROM native_b_private_v3.dirty d WHERE d.singleton;
 IF v_epoch=9223372036854775807 THEN RAISE EXCEPTION 'B_DIRTY_EXHAUSTED' USING ERRCODE='54000'; END IF;
 UPDATE native_b_private_v3.dirty SET epoch=v_epoch+1 WHERE singleton;
 UPDATE native_b_private_v3.touch SET touched=true WHERE relation_code=p_relation;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_FIXED_TOUCH' USING ERRCODE='23514'; END IF;
 -- NO memo is issued. No15-table hooks exist in this isolated packet.
"""
    + _FAULT,
    _KERNEL,
)

_FRAMES += _define(
    "native_b_original_registered_v3",
    "",
    "",
    "void",
    r"""
DECLARE binding native_b_private_v3.binding%ROWTYPE;
BEGIN
 binding:=app_private._native_b_current_v3();
 PERFORM app_private._native_b_debit_v3(0,0,0,4::smallint);
 UPDATE native_b_private_v3.request SET phase='ORIGINAL_REGISTERED'
  WHERE ordinal=binding.request_ordinal AND phase='SOURCE_ORIGINAL' AND clear_prepaid AND active_stage=1;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_ORIGINAL_PHASE' USING ERRCODE='23514'; END IF;
 -- Kernel-only continuation, not provenance. The sole source entry below
 -- unconditionally refuses; no current authenticated producer can reach this.
"""
    + _FAULT,
    _KERNEL,
)

for _entry, _phase, _error in (
    ("native_b_source_v3", "SOURCE_ORIGINAL", "B_SOURCE_UNAVAILABLE"),
    ("native_b_write_v3", "FINALIZE", "B_SOURCE_WRITE_UNAVAILABLE"),
    ("native_b_drain_v3", "SQL_WRITER", "B_DRAIN_UNAVAILABLE"),
):
    _DATA += _define(
        _entry,
        "",
        "",
        "void",
        """
DECLARE binding native_b_private_v3.binding%ROWTYPE;
BEGIN
 binding:=app_private._native_b_current_v3();
 IF NOT EXISTS(SELECT 1 FROM native_b_private_v3.request r WHERE r.ordinal=binding.request_ordinal
  AND r.phase='"""
        + _phase
        + """' AND r.clear_prepaid) THEN
  RAISE EXCEPTION 'B_SOURCE_PHASE' USING ERRCODE='23514'; END IF;
 -- Unconditional literal refusal. No GUC, callback, caller bool, supplied
 -- bundle or role change can enable source/write/drain in this packet.
 RAISE EXCEPTION '"""
        + _error
        + """' USING ERRCODE='55000';
"""
        + _FAULT,
        _KERNEL,
    )

_STORAGE_BOUND: Final = r"""
DO $storage$
DECLARE total bigint; relations bigint;
BEGIN
 SELECT coalesce(sum(pg_total_relation_size(m.sequence_oid::regclass)),0) INTO total
  FROM native_b_private_v3.sequence_oid m;
 SELECT sum(pg_total_relation_size(n::regclass)) INTO relations FROM (VALUES
  ('native_b_private_v3.slot'),('native_b_private_v3.sequence_oid'),('native_b_private_v3.binding'),
  ('native_b_private_v3.request'),('native_b_private_v3.frame'),('native_b_private_v3.dirty'),
  ('native_b_private_v3.touch')) AS fixed(n);
 IF total>4194304-relations OR (SELECT count(*) FROM native_b_private_v3.sequence_oid)<>56 THEN
  RAISE EXCEPTION 'B_PRIVATE_STORAGE_BOUND' USING ERRCODE='54000'; END IF;
 -- Private files only. Shared system-catalog storage, process RSS, CPU and
 -- latency are NOT measured/bounded by this4MiB private-storage predicate.
END $storage$;
"""

INSTALL_SQL: Final = _STORE_SQL + _PRIMITIVES + _DEBIT + _CONTROL + _DATA + _FRAMES + _STORAGE_BOUND
FUNCTIONS: Final = tuple(f"app_private.{name}({types})" for name, _, types, _, _ in _SPECS)
ISSUER_FUNCTIONS: Final = tuple(
    signature
    for signature in FUNCTIONS
    if signature.startswith(
        (
            "app_private.native_b_claim_v3(",
            "app_private.native_b_retirement_prepare_v3(",
            "app_private.native_b_retirement_finish_v3(",
        )
    )
)
DATA_FUNCTIONS: Final = tuple(
    signature
    for signature in FUNCTIONS
    if signature.startswith(
        tuple(
            "app_private." + name + "("
            for name in (
                "native_b_backend_identity_v3",
                "native_b_bind_v3",
                "native_b_begin_request_v3",
                "native_b_py_stage_enter_v3",
                "native_b_admit_input_v3",
                "native_b_fail_v3",
                "native_b_finish_v3",
                "native_b_source_v3",
                "native_b_write_v3",
                "native_b_drain_v3",
            )
        )
    )
)
KERNEL_FUNCTIONS: Final = tuple(
    signature
    for signature in FUNCTIONS
    if signature not in DATA_FUNCTIONS
    and signature not in ISSUER_FUNCTIONS
    and not signature.startswith(
        ("app_private._native_b_reset55_v3(", "app_private._native_b_terminal_v3(")
    )
)

GRANTS_SQL: Final = (
    "REVOKE ALL ON SCHEMA native_b_private_v3 FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer;\n"
    "REVOKE ALL ON ALL TABLES IN SCHEMA native_b_private_v3 FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer;\n"
    "REVOKE ALL ON ALL SEQUENCES IN SCHEMA native_b_private_v3 FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer,native_v3_b_kernel;\n"
    "GRANT USAGE ON SCHEMA app_private TO native_v3_b_runtime,native_v3_b_issuer;\n"
    "GRANT USAGE ON SCHEMA native_b_private_v3 TO native_v3_b_kernel;\n"
    "GRANT SELECT,INSERT,UPDATE,DELETE ON native_b_private_v3.binding,native_b_private_v3.request,"
    "native_b_private_v3.frame,native_b_private_v3.dirty,native_b_private_v3.touch TO native_v3_b_kernel;\n"
    "GRANT SELECT,UPDATE ON native_b_private_v3.slot TO native_v3_b_kernel;\n"
    + "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_RUNTIME};" for signature in DATA_FUNCTIONS
    )
    + "\n"
    + "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_ISSUER};" for signature in ISSUER_FUNCTIONS
    )
    + "\n"
    + "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_KERNEL};" for signature in KERNEL_FUNCTIONS
    )
    + "\n"
)
