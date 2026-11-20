# ruff: noqa: E501
"""Uninstalled, fixed pre-permission F CAPTURE registration transport.

These RPCs return bounded DATA for the original F owner. They do not issue an
F use, current process proof, SOURCE permission, B claim, or ACTIVE window. F
must prepay the genuine registration/IO hold before connection and preserve
its original nonce, process/history checks, final clock, commit and close.

The companion registration gate candidate spends the existing serial under
an issuer-armed exact idle slot/backend. PREPARE returns pre-insert DATA for
full original F checks; REGISTER then mutates in that SAME RC/fullxid. Session
advisory locks are not replay authority. The gate requires independent review.

No SQL executes on import. No new roles, sequences, initial operation creation,
runtime table access, or automatic column privileges are introduced here.
The separate rights/EXECUTE candidates require independent review and a real
installation/cost qualification before use.
"""

from __future__ import annotations

from hashlib import sha256
from types import MappingProxyType
from typing import Final

_KERNEL: Final = "native_v3_b_kernel"
_RUNTIME: Final = "native_v3_b_runtime"
_ISSUER: Final = "native_v3_b_issuer"
_SAFE53: Final = 9_007_199_254_740_991
_I64_MIN: Final = -(2**63)
_I64_MAX: Final = 2**63 - 1
_COMMON_LOCK: Final = 0x415554504C515401
_JSON_STORAGE_MAX: Final = 8188
SOURCE3_REGISTRATION_RAW_DOCUMENT_MAX_BYTES: Final = 65_536
SOURCE3_REGISTRATION_HISTORY_MAX: Final = 32
SOURCE3_REGISTRATION_QUALIFICATION_HOLDS: Final = (
    "UNQUALIFIED: companion one-shot gate transaction/ACL/caught-rollback "
    "review and terminal retirement",
    "UNQUALIFIED: exact query, nested callback, connection, UTF8 and "
    "driver-copy work/retention profile",
    "UNQUALIFIED: independently reviewed existing kernel column/lock "
    "privileges and definer/ACL audit",
    "REQUIRED: genuine original F nonce, local attempt burn, original paid "
    "SOURCE IO hold and process/history checks",
    "REQUIRED: known registration commit and terminal Session close before separate B claim",
    "REQUIRED: actual PostgreSQL concurrency, caught SAVEPOINT, catalog and privilege evidence",
)

_RESOURCE: Final = (
    "memory_bytes",
    "swap_bytes",
    "cpu_quota_us",
    "cpu_period_us",
    "pids",
    "open_files",
    "io_slots",
    "journal_bytes",
    "journal_records",
)
_ESCROW: Final = (
    "parent_open_files",
    "guardian_open_files",
    "control_open_files",
    "reader_duplicate_open_files",
    "transient_open_files",
    "guardian_pids",
    "guardian_memory_bytes",
    "guardian_cpu_quota_us",
    "guardian_cpu_period_us",
    "guardian_swap_bytes",
    "journal_bytes",
    "journal_records",
)
_LIMITS: Final = (
    "memory_max_bytes",
    "memory_swap_max_bytes",
    "cpu_quota_us",
    "cpu_period_us",
    "pids_max",
    "open_files_max",
    "maximum_payload_bytes",
    "maximum_work_units",
    "maximum_wall_ms",
    "heartbeat_interval_ms",
    "heartbeat_grace_ms",
    "cancel_poll_ms",
)
_VECTORS: Final = MappingProxyType({"resource": _RESOURCE, "escrow": _ESCROW, "limits": _LIMITS})

# Types describe the closed metadata encoding, not a caller-selected schema.
# u=UUID, i=I64, z=boolean, d=raw32->hex64, t=timestamp->I64 microseconds,
# xN=bounded UTF8 text, bN=bounded BYTEA->hex, jK=closed safe53 vector K.
# Every field may be SQL NULL in projection; mandatory/current predicates below
# and original F validation decide whether a particular NULL is acceptable.
_OPERATION: Final = (
    ("operation_id", "u"),
    ("run_id", "u"),
    ("execution_id", "u"),
    ("server_instance_id", "u"),
    ("identity_epoch", "i"),
    *(
        (name, "d")
        for name in (
            "policy_sha256",
            "selection_sha256",
            "configuration_sha256",
            "source_root_sha256",
            "tokenizer_sha256",
            "approval_sha256",
            "request_sha256",
            "retention_policy_sha256",
        )
    ),
    ("request_bytes", "b65536"),
    ("retention_until", "t"),
    ("max_prep_attempts", "i"),
    ("max_final_attempts", "i"),
    ("maximum_wall_ms", "i"),
    ("maximum_work_units", "i"),
    ("work_units_used", "i"),
    ("training_resource_vector", "jresource"),
    ("state", "x16"),
    *(
        (name, "t")
        for name in (
            "created_at",
            "deadline_at",
            "cancelled_at",
            "restore_revoked_at",
            "terminal_latched_at",
            "closed_at",
        )
    ),
)
_POLICY: Final = (
    ("policy_id", "u"),
    ("revision", "i"),
    ("policy_sha256", "d"),
    ("policy_bytes", "b65536"),
    ("server_instance_id", "u"),
    ("identity_epoch", "i"),
    *(
        (name, "d")
        for name in (
            "environment_sha256",
            "implementation_sha256",
            "measurement_sha256",
            "report_sha256",
        )
    ),
    ("internal_io_policy_revision", "i"),
    ("internal_io_profile_version", "i"),
    ("prep_limits", "jlimits"),
    ("final_limits", "jlimits"),
    ("host_vector", "jresource"),
    ("created_at", "t"),
)
_STAGE: Final = (
    ("stage_id", "u"),
    ("operation_id", "u"),
    ("phase", "x8"),
    ("attempt", "i"),
    ("policy_sha256", "d"),
    ("policy_revision", "i"),
    ("stage_token_sha256", "d"),
    ("resource_vector", "jresource"),
    ("limits", "jlimits"),
    ("io_owner", "x16"),
    ("training_execution_id", "u"),
    ("state", "x16"),
    *(
        (name, "t")
        for name in (
            "created_at",
            "deadline_at",
            "started_at",
            "heartbeat_at",
            "stopped_at",
            "closed_at",
        )
    ),
    ("child_pid", "i"),
    ("process_identity_sha256", "d"),
    ("exit_kind", "x32"),
    ("exit_code", "i"),
    ("exit_evidence_sha256", "d"),
    ("cleanup_evidence_sha256", "d"),
)
_RESERVATION: Final = (
    ("escrow_generation", "u"),
    ("operation_id", "u"),
    *(
        (name, "d")
        for name in (
            "root_identity_sha256",
            "scope_identity_sha256",
            "guardian_identity_sha256",
            "control_identity_sha256",
            "policy_sha256",
            "report_sha256",
        )
    ),
    ("escrow_vector", "jescrow"),
    ("resource_vector", "jresource"),
    ("state", "x16"),
    *((name, "t") for name in ("created_at", "held_at", "releasing_at", "closed_at")),
    ("release_decision_id", "u"),
    ("release_ack_sha256", "d"),
    ("cleanup_evidence_sha256", "d"),
)
_WINDOW: Final = (
    ("window_id", "u"),
    ("stage_id", "u"),
    ("operation_id", "u"),
    ("server_instance_id", "u"),
    ("identity_epoch", "i"),
    ("token_sha256", "d"),
    ("kind", "x8"),
    ("database_oid", "i"),
    ("session_role_oid", "i"),
    ("backend_pid", "i"),
    ("backend_start", "t"),
    ("postmaster_start", "t"),
    ("registration_xid", "x20"),
    ("state", "x16"),
    *((name, "t") for name in ("created_at", "deadline_at", "entered_at", "terminal_at")),
    ("terminal_receipt_sha256", "d"),
)
_INTERLOCK: Final = (
    ("server_instance_id", "u"),
    ("operation_id", "u"),
    ("stage_id", "u"),
    ("window_id", "u"),
    ("identity_epoch", "i"),
    ("token_sha256", "d"),
    ("changed_at", "t"),
)
_REGISTRATION: Final = (
    ("window_id", "u"),
    ("stage_id", "u"),
    ("operation_id", "u"),
    ("server_instance_id", "u"),
    ("identity_epoch", "i"),
    ("window_token_sha256", "d"),
    ("database_oid", "i"),
    ("session_role_oid", "i"),
    ("backend_pid", "i"),
    ("backend_start", "t"),
    ("postmaster_start", "t"),
    ("registration_xid", "x20"),
    ("deadline_at", "t"),
)
_ACCEPTED: Final = (
    ("operation_id", "u"),
    ("stage_id", "u"),
    ("prep_receipt_sha256", "d"),
    ("exit_evidence_sha256", "d"),
    ("exit_code", "i"),
    ("accepted_at", "t"),
)
_PREP_RECEIPT: Final = (
    ("stage_id", "u"),
    ("operation_id", "u"),
    *(
        (name, "d")
        for name in (
            "numerical_evidence_sha256",
            "snapshot_sha256",
            "installed_inventory_sha256",
            "full_owner_binding_sha256",
            "receipt_sha256",
        )
    ),
    ("source_window_id", "u"),
    ("created_at", "t"),
)
_CONSUMED: Final = (
    *(
        (name, "u")
        for name in (
            "run_id",
            "operation_id",
            "execution_id",
            "prep_stage_id",
            "server_instance_id",
        )
    ),
    ("identity_epoch", "i"),
    *(
        (name, "d")
        for name in (
            "selection_sha256",
            "prep_receipt_sha256",
            "snapshot_sha256",
            "source_manifest_sha256",
            "authorized_dataset_sha256",
            "producer_provenance_sha256",
        )
    ),
    ("source_owner_key_id", "x128"),
    ("source_owner_count", "i"),
    ("source_owner_set_sha256", "d"),
    ("training_lineage_key_id", "x128"),
    ("registered_member_count", "i"),
    *(
        (name, "d")
        for name in (
            "full_owner_binding_sha256",
            "registered_member_binding_sha256",
            "registration_sha256",
            "training_input_binding_sha256",
            "receipt_sha256",
        )
    ),
    ("receipt_bytes", "b16384"),
    ("created_at", "t"),
)
_TRAINING: Final = (
    ("execution_id", "u"),
    ("run_id", "u"),
    ("state", "x16"),
    ("closed_at", "t"),
    ("retain_checkpoint", "z"),
    ("exit_code", "i"),
    ("closure_kind", "x32"),
    ("closure_evidence_sha256", "d"),
)
_IO: Final = (
    ("singleton_id", "i"),
    ("workload_version", "i"),
    ("revision", "i"),
    ("active_limit", "i"),
    ("measured_ceiling", "i"),
    ("server_instance_id", "u"),
    ("identity_epoch", "i"),
    ("environment_sha256", "x64"),
    ("workload_sha256", "x64"),
    ("report_sha256", "x64"),
    ("playback_ceiling", "i"),
    ("transfer_ceiling", "i"),
    ("initialized_at", "t"),
    ("updated_at", "t"),
)
_AUDIO: Final = (("singleton_id", "i"), ("playback_ceiling", "i"), ("transfer_ceiling", "i"))

SOURCE3_REGISTRATION_METADATA_COLUMNS: Final = MappingProxyType(
    {
        "operation": _OPERATION,
        "policy": _POLICY,
        "stage": _STAGE,
        "escrow": _RESERVATION,
        "window": _WINDOW,
        "interlock": _INTERLOCK,
        "prep_acceptance": _ACCEPTED,
        "prep_receipt": _PREP_RECEIPT,
        "consumed_input": _CONSUMED,
        "training_execution": _TRAINING,
        "internal_io_policy": _IO,
        "audio_policy": _AUDIO,
    }
)


def _columns(alias: str, fields: tuple[tuple[str, str], ...]) -> str:
    return ",".join(f"{alias}.{name}" for name, _ in fields)


def _vector_valid(expr: str, kind: str) -> str:
    keys = _VECTORS[kind]
    key_array = "ARRAY[" + ",".join(f"'{key}'" for key in keys) + "]::text[]"
    numbers = " AND ".join(
        f"CASE WHEN jsonb_typeof({expr}->'{key}')='number' THEN "
        f"scale(({expr}->'{key}')::numeric)=0 AND "
        f"({expr}->'{key}')::numeric BETWEEN 0 AND {_SAFE53} ELSE false END"
        for key in keys
    )
    return (
        f"jsonb_typeof({expr})='object' AND {expr} ?& {key_array} "
        f"AND ({expr}-{key_array})='{{}}'::jsonb AND ({numbers})"
    )


def _valid(alias: str, fields: tuple[tuple[str, str], ...], *, storage: bool) -> str:
    predicates: list[str] = []
    for name, kind in fields:
        expr = f"{alias}.{name}"
        if kind.startswith("b"):
            predicate = f"octet_length({expr}) BETWEEN 0 AND {int(kind[1:])}"
        elif kind == "d":
            predicate = f"octet_length({expr})=32"
        elif kind == "t":
            predicate = (
                f"isfinite({expr}) AND (extract(epoch FROM {expr})*1000000) "
                f"BETWEEN {_I64_MIN} AND {_I64_MAX}"
            )
        elif kind.startswith("x"):
            maximum = int(kind[1:])
            predicate = (
                f"pg_column_compression({expr}) IS NULL AND pg_column_size({expr})<={maximum + 4}"
                if storage
                else f"octet_length({expr})<={maximum}"
            )
        elif kind.startswith("j"):
            predicate = (
                f"pg_column_compression({expr}) IS NULL AND pg_column_size({expr})<={_JSON_STORAGE_MAX}"
                if storage
                else _vector_valid(expr, kind[1:])
            )
        else:
            continue
        predicates.append(f"({expr} IS NULL OR ({predicate}))")
    return " AND ".join(predicates) or "true"


def _projection(alias: str, fields: tuple[tuple[str, str], ...]) -> str:
    args: list[str] = []
    for name, kind in fields:
        expr = f"{alias}.{name}"
        key = name
        if kind == "t":
            key += "_us"
            expr = f"(extract(epoch FROM {expr})*1000000)::bigint"
        elif kind == "d":
            expr = f"encode({expr},'hex')"
        elif kind.startswith("b"):
            key = {"request_bytes": "request_hex", "policy_bytes": "policy_hex"}.get(
                name, name + "_hex"
            )
            expr = f"encode({expr},'hex')"
        args.extend((f"'{key}'", expr))
    # jsonb_build_object is variadic; these fixed projections stay below its100
    # argument ceiling. No row-to-JSON conversion or personal payload is used.
    return "jsonb_build_object(" + ",".join(args) + ")"


def _object_upper(fields: tuple[tuple[str, str], ...]) -> int:
    sizes: list[tuple[str, int]] = []
    for name, kind in fields:
        if kind == "u":
            size = 38
        elif kind == "i":
            size = 20
        elif kind == "z":
            size = 5
        elif kind == "d":
            size = 66
        elif kind == "t":
            name += "_us"
            size = 20
        elif kind.startswith("x"):
            size = 2 + 6 * int(kind[1:])
        elif kind.startswith("b"):
            name = {"request_bytes": "request_hex", "policy_bytes": "policy_hex"}.get(
                name, name + "_hex"
            )
            size = 2 + 2 * int(kind[1:])
        else:
            size = 2 + sum(2 + len(key) + 2 + 16 + 2 for key in _VECTORS[kind[1:]])
        sizes.append((name, max(4, size)))
    return _closed_object_upper(tuple(sizes))


def _closed_object_upper(fields: tuple[tuple[str, int], ...]) -> int:
    # Every key is a fixed ASCII literal. Include colon/comma separator spaces;
    # the unused final comma allowance makes this a conservative upper bound.
    return 2 + sum(2 + len(name) + 2 + size + 2 for name, size in fields)


# Derived UTF8 DATA bound, including JSONB pretty separators, optional transfer,
# every maximum history entry and its fixed receipt-existence flag. This is not
# a driver copy factor, workspace admission or RPC work/IO cost qualification.
_HISTORY_ENTRY_UPPER: Final = _object_upper((*_STAGE, ("has_final_attempt_receipt", "z")))
_TRANSFER_UPPER: Final = _closed_object_upper(
    tuple(
        (name, _object_upper(fields))
        for name, fields in (
            ("prep_acceptance", _ACCEPTED),
            ("prep_receipt", _PREP_RECEIPT),
            ("consumed_input", _CONSUMED),
            ("training_execution", _TRAINING),
            ("prep_stage", _STAGE),
            ("prep_rebind_window", _WINDOW),
        )
    )
)
SOURCE3_REGISTRATION_MAX_METADATA_BYTES: Final = _closed_object_upper(
    (
        ("version", 1),
        ("kind", len("NATIVE_B_SOURCE3_REGISTRATION_DATA") + 2),
        ("boundary", len("PREPARE_CURRENT") + 2),
        ("registration", _object_upper(_REGISTRATION)),
        *(
            (name, _object_upper(fields))
            for name, fields in (
                ("operation", _OPERATION),
                ("policy", _POLICY),
                ("stage", _STAGE),
                ("escrow", _RESERVATION),
                ("window", _WINDOW),
                ("interlock", _INTERLOCK),
                ("internal_io_policy", _IO),
                ("audio_policy", _AUDIO),
            )
        ),
        ("stage_history", 2 + SOURCE3_REGISTRATION_HISTORY_MAX * (_HISTORY_ENTRY_UPPER + 2)),
        ("transfer", _TRANSFER_UPPER),
        ("has_prep_acceptance", 5),
        ("has_consumed_final", 5),
        ("current_at_us", 20),
    )
)

_TABLE_FIELDS: Final = (
    ("ml.native_training_operation", _OPERATION),
    ("ml.native_validation_policy", _POLICY),
    ("ml.native_validation_stage", _STAGE),
    ("ml.native_training_escrow_reservation", _RESERVATION),
    ("ml.native_training_source_window", _WINDOW),
    ("ml.native_training_replay_interlock", _INTERLOCK),
    ("ml.native_training_prep_acceptance", _ACCEPTED),
    ("ml.native_training_prep_attempt_receipt", _PREP_RECEIPT),
    ("ml.native_training_consumed_input", _CONSUMED),
    ("ml.training_execution", _TRAINING),
    ("account.internal_io_policy", _IO),
    ("account.resource_quota_policy", _AUDIO),
)
SOURCE3_REGISTRATION_COLUMN_RIGHTS: Final = MappingProxyType(
    {
        "SELECT": MappingProxyType(
            {
                **{table: tuple(name for name, _ in fields) for table, fields in _TABLE_FIELDS},
                "account.server_instance": ("server_instance_id", "identity_epoch"),
                "ml.native_training_final_attempt_receipt": ("stage_id",),
                "ml.native_training_consumed_final": ("run_id",),
            }
        ),
        # PostgreSQL row locking additionally needs UPDATE privilege on at least
        # one column. These are the exact proposed lock-only columns; no statement
        # in either body updates operation, stage, escrow or server identity.
        "UPDATE_LOCK_ONLY": MappingProxyType(
            {
                "account.server_instance": ("server_instance_id",),
                "ml.native_training_operation": ("operation_id",),
                "ml.native_validation_stage": ("stage_id",),
                "ml.native_training_escrow_reservation": ("escrow_generation",),
            }
        ),
        "INSERT": MappingProxyType(
            {
                "ml.native_training_source_window": tuple(
                    name
                    for name, _ in _WINDOW
                    if name not in ("entered_at", "terminal_at", "terminal_receipt_sha256")
                ),
                "ml.native_training_replay_interlock": tuple(name for name, _ in _INTERLOCK),
            }
        ),
        "UPDATE": MappingProxyType(
            {
                "ml.native_training_replay_interlock": tuple(
                    name for name, _ in _INTERLOCK if name != "server_instance_id"
                ),
            }
        ),
    }
)
SOURCE3_REGISTRATION_PRIVILEGE_STATUS: Final = (
    "CANDIDATE_ONLY_REQUIRES_INDEPENDENT_COLUMN_AND_DEFINER_REVIEW"
)


def _load(
    table: str, alias: str, target: str, fields: tuple[tuple[str, str], ...], where: str
) -> str:
    return f"""
 IF NOT EXISTS(SELECT 1 FROM {table} {alias} WHERE {where} AND ({_valid(alias, fields, storage=True)})) THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_STORAGE'; END IF;
 SELECT {_columns(alias, fields)} INTO STRICT {target} FROM {table} {alias} WHERE {where};
 IF NOT ({_valid(target, fields, storage=False)}) THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_VALUES'; END IF;
"""


_DECLARATIONS: Final = """
 v_stage_locator record; v_backend record; v_server record;
 v_operation record; v_policy record; v_stage record; v_escrow record;
 v_window record; v_interlock record; v_io record; v_audio record;
 v_history_row record; v_accepted record; v_receipt record; v_consumed record;
 v_training record; v_prep record; v_prep_window record;
 v_db oid; v_now timestamptz; v_deadline timestamptz;
 v_stage_id uuid; v_window_id uuid; v_operation_id uuid;
 v_registration record; v_token bytea; v_boundary text;
 v_history jsonb:='[]'::jsonb; v_transfer jsonb; v_data jsonb; v_result bytea;
 v_count integer:=0; v_has_final boolean;
"""

_AUTH: Final = """
 PERFORM app_private._native_b_role_v3(true);
 IF current_setting('transaction_isolation')<>'read committed'
  OR current_setting('transaction_read_only')<>'off'
  OR p_window IS NULL OR p_stage_secret IS NULL OR octet_length(p_stage_secret)<>32 THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_ACTOR' USING ERRCODE='42501'; END IF;
"""

_LOCKS: Final = """
 -- Authenticate the original raw stage nonce BEFORE touching the replay gate
 -- or any target window. Public stage_token_sha256 is not a credential.
 SELECT s.stage_id,s.operation_id,s.stage_token_sha256,o.server_instance_id,o.identity_epoch
 INTO STRICT v_stage_locator FROM ml.native_validation_stage s
 JOIN ml.native_training_operation o ON o.operation_id=s.operation_id
 WHERE s.stage_id=v_stage_id;
 IF v_stage_locator.stage_token_sha256 IS DISTINCT FROM sha256(p_stage_secret) THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_CREDENTIAL' USING ERRCODE='42501'; END IF;
 v_operation_id:=v_stage_locator.operation_id;
 SELECT d.oid INTO STRICT v_db FROM pg_catalog.pg_database d WHERE d.datname=current_database();
"""

_CURRENT_LOCKS: Final = f"""
 -- Preserve identity -> common admission -> operation -> stage ordering.
 SELECT i.server_instance_id,i.identity_epoch INTO STRICT v_server
 FROM account.server_instance i WHERE i.server_instance_id=v_stage_locator.server_instance_id FOR UPDATE;
 IF v_server.identity_epoch<>v_stage_locator.identity_epoch THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_IDENTITY'; END IF;
 PERFORM pg_advisory_xact_lock({_COMMON_LOCK});
 PERFORM 1 FROM ml.native_training_operation o WHERE o.operation_id=v_operation_id FOR UPDATE;
 PERFORM 1 FROM ml.native_validation_stage s WHERE s.stage_id=v_stage_id FOR UPDATE;
 PERFORM 1 FROM ml.native_training_escrow_reservation e WHERE e.operation_id=v_operation_id FOR UPDATE;
 PERFORM 1 FROM ml.native_training_replay_interlock i
  WHERE i.server_instance_id=v_server.server_instance_id FOR UPDATE;
"""

_LOAD_CURRENT: Final = (
    _load(
        "ml.native_training_operation",
        "o",
        "v_operation",
        _OPERATION,
        "o.operation_id=v_operation_id",
    )
    + _load("ml.native_validation_stage", "s", "v_stage", _STAGE, "s.stage_id=v_stage_id")
    + _load(
        "ml.native_validation_policy",
        "p",
        "v_policy",
        _POLICY,
        "p.policy_sha256=v_operation.policy_sha256",
    )
    + _load(
        "ml.native_training_escrow_reservation",
        "e",
        "v_escrow",
        _RESERVATION,
        "e.operation_id=v_operation_id",
    )
    + _load("account.internal_io_policy", "p", "v_io", _IO, "p.singleton_id=1")
    + _load("account.resource_quota_policy", "p", "v_audio", _AUDIO, "p.singleton_id=1")
)

_CURRENT_PREDICATES: Final = """
 v_now:=clock_timestamp();
 IF (v_stage.operation_id<>v_operation.operation_id OR v_stage.stage_token_sha256 IS DISTINCT FROM sha256(p_stage_secret)
  OR v_operation.server_instance_id<>v_server.server_instance_id OR v_operation.identity_epoch<>v_server.identity_epoch
  OR v_operation.state NOT IN ('PREP','TRAIN','FINAL') OR v_operation.cancelled_at IS NOT NULL
  OR v_operation.restore_revoked_at IS NOT NULL OR v_operation.terminal_latched_at IS NOT NULL OR v_operation.closed_at IS NOT NULL
  OR v_operation.max_prep_attempts NOT BETWEEN 1 AND 16 OR v_operation.max_final_attempts NOT BETWEEN 1 AND 16
  OR v_stage.state<>'RUNNING' OR v_stage.phase<>v_operation.state OR v_stage.phase NOT IN ('PREP','FINAL')
  OR v_stage.started_at IS NULL OR v_stage.heartbeat_at IS NULL OR v_stage.child_pid NOT BETWEEN 1 AND 2147483647
  OR v_stage.child_pid IS NULL OR v_stage.process_identity_sha256 IS NULL
  OR v_stage.started_at>v_stage.heartbeat_at OR v_stage.heartbeat_at>v_now
  OR v_stage.stopped_at IS NOT NULL OR v_stage.closed_at IS NOT NULL OR v_stage.exit_kind IS NOT NULL
  OR v_stage.exit_evidence_sha256 IS NOT NULL OR v_stage.cleanup_evidence_sha256 IS NOT NULL
  OR v_stage.policy_sha256<>v_policy.policy_sha256 OR v_stage.policy_revision<>v_policy.revision
  OR v_stage.policy_sha256<>v_operation.policy_sha256
  OR v_policy.server_instance_id<>v_server.server_instance_id OR v_policy.identity_epoch<>v_server.identity_epoch
  OR v_policy.internal_io_profile_version<>4 OR v_policy.internal_io_policy_revision<>v_io.revision
  OR v_io.workload_version<>4 OR v_io.active_limit IS NULL OR v_io.server_instance_id<>v_server.server_instance_id
  OR v_io.identity_epoch<>v_server.identity_epoch OR v_audio.playback_ceiling IS NULL OR v_audio.transfer_ceiling IS NULL
  OR v_io.playback_ceiling IS NULL OR v_io.transfer_ceiling IS NULL
  OR v_audio.playback_ceiling>v_io.playback_ceiling OR v_audio.transfer_ceiling>v_io.transfer_ceiling
  OR v_escrow.state<>'HELD' OR v_escrow.held_at IS NULL OR v_escrow.releasing_at IS NOT NULL OR v_escrow.closed_at IS NOT NULL
  OR v_escrow.root_identity_sha256 IS NULL OR v_escrow.scope_identity_sha256 IS NULL
  OR v_escrow.guardian_identity_sha256 IS NULL OR v_escrow.control_identity_sha256 IS NULL
  OR v_escrow.policy_sha256<>v_policy.policy_sha256 OR v_escrow.report_sha256<>v_policy.report_sha256
  OR sha256(v_operation.request_bytes) IS DISTINCT FROM v_operation.request_sha256
  OR sha256(v_policy.policy_bytes) IS DISTINCT FROM v_policy.policy_sha256) IS DISTINCT FROM false THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_CURRENT'; END IF;
 IF (v_stage.limits IS DISTINCT FROM CASE v_stage.phase WHEN 'PREP' THEN v_policy.prep_limits ELSE v_policy.final_limits END
  OR v_stage.io_owner<>CASE v_stage.phase WHEN 'FINAL' THEN 'TRAINING' ELSE 'SELF' END
  OR (v_stage.phase='FINAL' AND v_stage.training_execution_id IS DISTINCT FROM v_operation.execution_id)
  OR (v_stage.phase='PREP' AND v_stage.training_execution_id IS NOT NULL)) IS DISTINCT FROM false THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_POLICY'; END IF;
 v_deadline:=least(v_stage.deadline_at,v_operation.deadline_at,v_operation.retention_until);
 IF v_deadline<=v_now OR v_stage.limits->'heartbeat_grace_ms' IS NULL
  OR (v_stage.limits->'heartbeat_grace_ms')::numeric NOT BETWEEN 1 AND 9007199254740991
  OR v_now>=v_stage.heartbeat_at+((v_stage.limits->'heartbeat_grace_ms')::numeric*interval '1 millisecond') THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_EXPIRED'; END IF;
 -- Original F additionally verifies canonical request/policy, actual process,
 -- retained authorization deadline, signed limits, work accounting and history.
"""

_HISTORY: Final = f"""
 -- Count metadata BEFORE hydrating any history vectors or text.
 SELECT count(*) INTO v_count FROM (SELECT s.stage_id FROM ml.native_validation_stage s
  WHERE s.operation_id=v_operation_id LIMIT {SOURCE3_REGISTRATION_HISTORY_MAX + 1}) bounded_history;
 IF v_count<1 OR v_count>{SOURCE3_REGISTRATION_HISTORY_MAX}
  OR v_count>v_operation.max_prep_attempts+v_operation.max_final_attempts
  OR (SELECT count(*) FROM ml.native_validation_stage s
   WHERE s.operation_id=v_operation_id AND s.closed_at IS NULL)<>1 THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_HISTORY'; END IF;
 FOR v_history_row IN SELECT s.stage_id FROM ml.native_validation_stage s
  WHERE s.operation_id=v_operation_id ORDER BY s.phase DESC,s.attempt LOOP
{_load("ml.native_validation_stage", "s", "v_prep", _STAGE, "s.stage_id=v_history_row.stage_id")}
  SELECT EXISTS(SELECT 1 FROM ml.native_training_final_attempt_receipt r
   WHERE r.stage_id=v_prep.stage_id) INTO v_has_final;
  v_history:=v_history||jsonb_build_array({_projection("v_prep", _STAGE)}
   ||jsonb_build_object('has_final_attempt_receipt',v_has_final));
 END LOOP;
"""

_TRANSFER: Final = f"""
 v_transfer:=NULL;
 IF v_stage.phase='FINAL' THEN
{_load("ml.native_training_prep_acceptance", "a", "v_accepted", _ACCEPTED, "a.operation_id=v_operation_id")}
{_load("ml.native_validation_stage", "s", "v_prep", _STAGE, "s.stage_id=v_accepted.stage_id")}
{_load("ml.native_training_prep_attempt_receipt", "r", "v_receipt", _PREP_RECEIPT, "r.stage_id=v_accepted.stage_id")}
{_load("ml.native_training_consumed_input", "c", "v_consumed", _CONSUMED, "c.run_id=v_operation.run_id")}
{_load("ml.training_execution", "t", "v_training", _TRAINING, "t.execution_id=v_operation.execution_id")}
{_load("ml.native_training_source_window", "w", "v_prep_window", _WINDOW, "w.window_id=v_receipt.source_window_id")}
  v_transfer:=jsonb_build_object('prep_acceptance',{_projection("v_accepted", _ACCEPTED)},
   'prep_stage',{_projection("v_prep", _STAGE)},'prep_receipt',{_projection("v_receipt", _PREP_RECEIPT)},
   'consumed_input',{_projection("v_consumed", _CONSUMED)},'training_execution',{_projection("v_training", _TRAINING)},
   'prep_rebind_window',{_projection("v_prep_window", _WINDOW)});
 END IF;
"""

_BACKEND: Final = """
 SELECT d.oid AS database_oid,r.oid AS session_role_oid,a.pid AS backend_pid,
  a.backend_start,pg_postmaster_start_time() AS postmaster_start
 INTO STRICT v_backend FROM pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_roles r
 JOIN pg_catalog.pg_stat_activity a ON a.pid=pg_backend_pid()
 WHERE d.datname=current_database() AND r.rolname=session_user;
 IF v_backend.database_oid<>v_db OR v_backend.backend_start IS NULL THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_BACKEND'; END IF;
"""

_RESULT_WINDOW: Final = f"""
{_load("ml.native_training_source_window", "w", "v_window", _WINDOW, "w.window_id=v_window_id")}
{_load("ml.native_training_replay_interlock", "i", "v_interlock", _INTERLOCK, "i.server_instance_id=v_server.server_instance_id")}
 IF (v_window.stage_id<>v_stage_id OR v_window.operation_id<>v_operation_id
  OR v_window.server_instance_id<>v_server.server_instance_id OR v_window.identity_epoch<>v_server.identity_epoch
  OR v_window.kind<>'CAPTURE' OR v_window.state<>'REGISTERED' OR v_window.entered_at IS NOT NULL
  OR v_window.terminal_at IS NOT NULL OR v_window.terminal_receipt_sha256 IS NOT NULL
  OR v_window.database_oid<>v_backend.database_oid OR v_window.session_role_oid<>v_backend.session_role_oid
  OR v_window.backend_pid<>v_backend.backend_pid OR v_window.backend_start<>v_backend.backend_start
  OR v_window.postmaster_start<>v_backend.postmaster_start OR v_window.registration_xid<>pg_current_xact_id()::text
  OR v_interlock.operation_id IS DISTINCT FROM v_operation_id OR v_interlock.stage_id IS DISTINCT FROM v_stage_id
  OR v_interlock.window_id IS DISTINCT FROM v_window_id OR v_interlock.identity_epoch IS DISTINCT FROM v_server.identity_epoch
  OR v_interlock.token_sha256 IS DISTINCT FROM v_window.token_sha256) IS DISTINCT FROM false THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_BINDING'; END IF;
"""
_RESULT_DATA: Final = f"""
 SELECT v_window_id AS window_id,v_stage_id AS stage_id,v_operation_id AS operation_id,
  v_server.server_instance_id AS server_instance_id,v_server.identity_epoch AS identity_epoch,
  v_token AS window_token_sha256,v_backend.database_oid AS database_oid,
  v_backend.session_role_oid AS session_role_oid,v_backend.backend_pid AS backend_pid,
  v_backend.backend_start AS backend_start,v_backend.postmaster_start AS postmaster_start,
  pg_current_xact_id()::text AS registration_xid,v_deadline AS deadline_at INTO v_registration;
 v_data:=jsonb_build_object('version',3,'kind','NATIVE_B_SOURCE3_REGISTRATION_DATA','boundary',v_boundary,
  'registration',{_projection("v_registration", _REGISTRATION)},
  'operation',{_projection("v_operation", _OPERATION)},'policy',{_projection("v_policy", _POLICY)},
  'escrow',{_projection("v_escrow", _RESERVATION)},'stage',{_projection("v_stage", _STAGE)},
  'stage_history',v_history,'window',WINDOW_PROJECTION,
  'interlock',INTERLOCK_PROJECTION,'transfer',v_transfer,
  'internal_io_policy',{_projection("v_io", _IO)},'audio_policy',{_projection("v_audio", _AUDIO)},
  'has_prep_acceptance',EXISTS(SELECT 1 FROM ml.native_training_prep_acceptance a WHERE a.operation_id=v_operation_id),
  'has_consumed_final',EXISTS(SELECT 1 FROM ml.native_training_consumed_final c WHERE c.run_id=v_operation.run_id));
 -- This clock follows F's real external I/process checks when called through
 -- the current RPC, and follows all local bounded projection work in either.
 v_now:=clock_timestamp();
 IF v_now>=v_deadline
  OR v_now>=v_stage.heartbeat_at+((v_stage.limits->'heartbeat_grace_ms')::numeric*interval '1 millisecond') THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_FINAL_CLOCK'; END IF;
 v_data:=v_data||jsonb_build_object('current_at_us',(extract(epoch FROM v_now)*1000000)::bigint);
 v_result:=convert_to(v_data::text,'UTF8');
 IF octet_length(v_result)>{SOURCE3_REGISTRATION_MAX_METADATA_BYTES} THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_METADATA_BOUND'; END IF;
 -- Also refuse expiry during UTF8 serialization. The returned clock remains
 -- the preceding observed SQL clock, never a caller-provided deadline/grant.
 v_now:=clock_timestamp();
 IF v_now>=v_deadline
  OR v_now>=v_stage.heartbeat_at+((v_stage.limits->'heartbeat_grace_ms')::numeric*interval '1 millisecond') THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_FINAL_CLOCK'; END IF;
 RETURN v_result;
"""
_RESULT: Final = _RESULT_WINDOW + _RESULT_DATA.replace(
    "WINDOW_PROJECTION", _projection("v_window", _WINDOW)
).replace("INTERLOCK_PROJECTION", _projection("v_interlock", _INTERLOCK))
_PREPARE_RESULT: Final = _RESULT_DATA.replace("WINDOW_PROJECTION", "NULL").replace(
    "INTERLOCK_PROJECTION", "NULL"
)

_PREPARE_BODY: Final = (
    "DECLARE\n"
    + _DECLARATIONS
    + "BEGIN\n"
    + _AUTH
    + " IF p_stage IS NULL OR p_window_token_sha IS NULL OR "
    "octet_length(p_window_token_sha)<>32 THEN"
    " RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_ARGUMENT'; END IF;\n"
    " v_stage_id:=p_stage; v_window_id:=p_window; "
    "v_token:=p_window_token_sha; v_boundary:='PREPARE';\n" + _LOCKS + " PERFORM "
    "app_private._native_b_source3_registration_step_v3(p_window,p_stage,p_stage_secret,p_window_token_sha,false);"
    "\n"
    + _CURRENT_LOCKS
    + _LOAD_CURRENT
    + _CURRENT_PREDICATES
    + _HISTORY
    + _TRANSFER
    + _BACKEND
    + " IF EXISTS(SELECT 1 FROM ml.native_training_source_window w WHERE w.window_id=p_window"
    " OR w.token_sha256=p_window_token_sha OR(w.stage_id=v_stage_id AND w.kind='CAPTURE'))"
    " OR EXISTS(SELECT 1 FROM ml.native_training_replay_interlock i WHERE "
    "i.server_instance_id=v_server.server_instance_id"
    " AND i.window_id IS NOT NULL) THEN RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_REPLAY'; END IF;\n"
    + _PREPARE_RESULT
    + "END;\n"
)
_PREPARE_CURRENT_BODY: Final = _PREPARE_BODY.replace(
    "v_boundary:='PREPARE'", "v_boundary:='PREPARE_CURRENT'"
).replace(
    "PERFORM "
    "app_private._native_b_source3_registration_step_v3(p_window,p_stage,p_stage_secret,p_window_token_sha,false)",
    "PERFORM "
    "app_private._native_b_source3_registration_current_gate_v3(p_window,p_stage,p_stage_secret,p_window_token_sha,false)",
)

_REGISTER_BODY: Final = (
    "DECLARE\n"
    + _DECLARATIONS
    + "BEGIN\n"
    + _AUTH
    + """
 IF p_stage IS NULL OR p_window_token_sha IS NULL OR octet_length(p_window_token_sha)<>32 THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_ARGUMENT'; END IF;
 v_stage_id:=p_stage; v_window_id:=p_window; v_token:=p_window_token_sha; v_boundary:='REGISTER';
"""
    + _LOCKS
    + " PERFORM "
    "app_private._native_b_source3_registration_step_v3(p_window,p_stage,p_stage_secret,p_window_token_sha,true);"
    "\n"
    + _CURRENT_LOCKS
    + _LOAD_CURRENT
    + _CURRENT_PREDICATES
    + _HISTORY
    + _TRANSFER
    + _BACKEND
    + """
 IF EXISTS(SELECT 1 FROM ml.native_training_source_window w
  WHERE w.window_id=p_window OR w.token_sha256=p_window_token_sha
   OR (w.stage_id=v_stage_id AND w.kind='CAPTURE'))
  OR EXISTS(SELECT 1 FROM ml.native_training_replay_interlock i
   WHERE i.server_instance_id=v_server.server_instance_id AND i.window_id IS NOT NULL) THEN
  RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_REPLAY'; END IF;
 v_now:=clock_timestamp();
 IF v_now>=v_deadline THEN RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_EXPIRED'; END IF;
 INSERT INTO ml.native_training_source_window
 (window_id,stage_id,operation_id,server_instance_id,identity_epoch,token_sha256,kind,
 database_oid,session_role_oid,backend_pid,backend_start,postmaster_start,registration_xid,
  state,created_at,deadline_at)
 VALUES(p_window,v_stage_id,v_operation_id,v_server.server_instance_id,v_server.identity_epoch,
 p_window_token_sha,'CAPTURE',v_backend.database_oid,v_backend.session_role_oid,v_backend.backend_pid,
 v_backend.backend_start,v_backend.postmaster_start,pg_current_xact_id()::text,'REGISTERED',v_now,v_deadline);
 INSERT INTO ml.native_training_replay_interlock
 (server_instance_id,operation_id,stage_id,window_id,identity_epoch,token_sha256,changed_at)
 VALUES(v_server.server_instance_id,v_operation_id,v_stage_id,p_window,v_server.identity_epoch,p_window_token_sha,v_now)
 ON CONFLICT(server_instance_id) DO UPDATE SET operation_id=excluded.operation_id,
 stage_id=excluded.stage_id,window_id=excluded.window_id,identity_epoch=excluded.identity_epoch,
 token_sha256=excluded.token_sha256,changed_at=excluded.changed_at
 WHERE native_training_replay_interlock.window_id IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'B_SOURCE3_REGISTRATION_INTERLOCK'; END IF;
"""
    + _RESULT
    + "END;\n"
)

_CURRENT_BODY: Final = (
    "DECLARE\n"
    + _DECLARATIONS
    + "BEGIN\n"
    + _AUTH
    + """
 SELECT w.stage_id,w.token_sha256 INTO STRICT v_stage_id,v_token FROM ml.native_training_source_window w WHERE w.window_id=p_window;
 v_window_id:=p_window; v_boundary:='CURRENT';
"""
    + _LOCKS
    + " PERFORM "
    "app_private._native_b_source3_registration_current_gate_v3(p_window,v_stage_id,p_stage_secret,v_token,true);"
    "\n"
    + _CURRENT_LOCKS
    + _LOAD_CURRENT
    + _CURRENT_PREDICATES
    + _HISTORY
    + _TRANSFER
    + _BACKEND
    + _RESULT
    + "END;\n"
)

_SPECS: Final = (
    (
        "app_private.native_b_source3_prepare_capture_v3(uuid,uuid,bytea,bytea)",
        "p_window uuid,p_stage uuid,p_stage_secret bytea,p_window_token_sha bytea",
        _PREPARE_BODY,
    ),
    (
        "app_private.native_b_source3_prepare_current_v3(uuid,uuid,bytea,bytea)",
        "p_window uuid,p_stage uuid,p_stage_secret bytea,p_window_token_sha bytea",
        _PREPARE_CURRENT_BODY,
    ),
    (
        "app_private.native_b_source3_register_capture_v3(uuid,uuid,bytea,bytea)",
        "p_window uuid,p_stage uuid,p_stage_secret bytea,p_window_token_sha bytea",
        _REGISTER_BODY,
    ),
    (
        "app_private.native_b_source3_registration_current_v3(uuid,bytea)",
        "p_window uuid,p_stage_secret bytea",
        _CURRENT_BODY,
    ),
)
SOURCE3_REGISTRATION_SQL_SIGNATURES: Final = tuple(signature for signature, _, _ in _SPECS)
SOURCE3_REGISTRATION_RUNTIME_SIGNATURES: Final = SOURCE3_REGISTRATION_SQL_SIGNATURES
SOURCE3_REGISTRATION_INTERNAL_SIGNATURES: Final = ()
SOURCE3_REGISTRATION_REQUIRED_FUNCTIONS: Final = (
    "app_private._native_b_role_v3(boolean)",
    "app_private._native_b_source3_registration_step_v3(uuid,uuid,bytea,bytea,boolean)",
    "app_private._native_b_source3_registration_current_gate_v3(uuid,uuid,bytea,bytea,boolean)",
)
SOURCE3_REGISTRATION_SQL_OWNERS: Final = MappingProxyType({key: _KERNEL for key, _, _ in _SPECS})
SOURCE3_REGISTRATION_BODY_DEFINITIONS: Final = MappingProxyType(
    {key: body for key, _, body in _SPECS}
)
SOURCE3_REGISTRATION_BODY_SHA256: Final = MappingProxyType(
    {key: sha256(body.encode("utf-8")).hexdigest() for key, _, body in _SPECS}
)
SOURCE3_REGISTRATION_RESULT_TYPES: Final = MappingProxyType({key: "bytea" for key, _, _ in _SPECS})
SOURCE3_REGISTRATION_RESULT_SETS: Final = MappingProxyType({key: False for key, _, _ in _SPECS})
SOURCE3_REGISTRATION_DATA_FIELDS: Final = MappingProxyType(
    {
        "operation": _OPERATION,
        "policy": _POLICY,
        "escrow": _RESERVATION,
        "stage": _STAGE,
        "window": _WINDOW,
        "interlock": _INTERLOCK,
        "internal_io_policy": _IO,
        "audio_policy": _AUDIO,
        "registration": _REGISTRATION,
        "prep_acceptance": _ACCEPTED,
        "prep_receipt": _PREP_RECEIPT,
        "consumed_input": _CONSUMED,
        "training_execution": _TRAINING,
        "prep_stage": _STAGE,
        "prep_rebind_window": _WINDOW,
    }
)
SOURCE3_REGISTRATION_DATA_VECTORS: Final = _VECTORS
SOURCE3_REGISTRATION_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_REGISTRATION_SECURITY_DEFINER: Final = True


def _definition(signature: str, parameters: str, body: str) -> str:
    name = signature.split("(", 1)[0]
    return (
        f"CREATE OR REPLACE FUNCTION {name}({parameters}) RETURNS bytea\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog\n"
        f"AS $native_b_source_registration_v3$\n{body}$native_b_source_registration_v3$;\n"
        f"ALTER FUNCTION {signature} OWNER TO {_KERNEL};\n"
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,{_RUNTIME},{_ISSUER};\n"
    )


NATIVE_B_SOURCE_REGISTRATION_V3_INSTALL_SQL: Final = "\n".join(
    _definition(signature, parameters, body) for signature, parameters, body in _SPECS
)
# Deliberately separate: importing or installing the function definitions alone
# does not grant pre-permission execution or the candidate column privileges.
NATIVE_B_SOURCE_REGISTRATION_V3_GRANTS_SQL_CANDIDATE: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_RUNTIME};"
        for signature in SOURCE3_REGISTRATION_RUNTIME_SIGNATURES
    )
    + "\n"
)
NATIVE_B_SOURCE_REGISTRATION_V3_DROP_SQL: Final = (
    "\n".join(
        f"DROP FUNCTION {signature};" for signature in reversed(SOURCE3_REGISTRATION_SQL_SIGNATURES)
    )
    + "\n"
)
