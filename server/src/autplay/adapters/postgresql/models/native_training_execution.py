"""Private metadata for staged native training; imported only by an explicit overlay.

No shared Base registry is extended. These rows carry reservations and historical
facts, not a substitute for the session-bound authority/admission repository.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..base import NAMING_CONVENTION
from .account import UserAccountRow
from .training_work import TrainingExecutionRow, TrainingParticipantRow, TrainingRunRow


class NativeTrainingBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(NAMING_CONVENTION))


def _hashes(name: str, *columns: str) -> CheckConstraint:
    return CheckConstraint(
        " AND ".join(f"octet_length({column})=32" for column in columns), name=name
    )


def _positive(name: str, *columns: str) -> CheckConstraint:
    return CheckConstraint(
        " AND ".join(f"{column} BETWEEN 1 AND 9007199254740991" for column in columns), name=name
    )


def _document(name: str, data: str, digest: str, maximum: int = 65536) -> CheckConstraint:
    return CheckConstraint(
        f"octet_length({data}) BETWEEN 2 AND {maximum} AND {digest}=sha256({data})", name=name
    )


RESOURCE_VECTOR_FIELDS = (
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
ESCROW_VECTOR_FIELDS = (
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
VALIDATION_LIMIT_FIELDS = (
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


def _vector(
    name: str, column: str, fields: tuple[str, ...], *, nullable: bool = False
) -> CheckConstraint:
    keys = ",".join(f"'{key}'" for key in fields)
    checks = [
        f"jsonb_typeof({column})='object'",
        f"{column} ?& ARRAY[{keys}]",
        f"{column} - ARRAY[{keys}] = '{{}}'::jsonb",
    ]
    for field in fields:
        value = f"{column}->'{field}'"
        checks.append(
            f"CASE WHEN jsonb_typeof({value})='number' "
            f"AND ({column}->>'{field}') ~ '^[0-9]{{1,16}}$' "
            f"THEN ({column}->>'{field}')::numeric BETWEEN 0 AND 9007199254740991 ELSE false END"
        )
    if fields == RESOURCE_VECTOR_FIELDS:
        checks.extend(
            [
                f"({column}->>'cpu_period_us')::numeric BETWEEN 1000 AND 1000000",
                f"({column}->>'pids')::numeric<=2147483647 "
                f"AND ({column}->>'open_files')::numeric<=2147483647",
                f"({column}->>'memory_bytes')::numeric+({column}->>'swap_bytes')::numeric<=9007199254740991",
                f"(({column}->>'journal_bytes')::numeric=0)=(({column}->>'journal_records')::numeric=0)",
                f"({column}->>'journal_records')::numeric<=({column}->>'journal_bytes')::numeric",
            ]
        )
    elif fields == VALIDATION_LIMIT_FIELDS:
        checks.extend(
            f"({column}->>'{field}')::numeric>0"
            for field in fields
            if field != "memory_swap_max_bytes"
        )
        checks.extend(
            [
                f"({column}->>'cpu_period_us')::numeric BETWEEN 1000 AND 1000000",
                f"({column}->>'cpu_quota_us')::numeric>=1000",
                f"({column}->>'pids_max')::numeric<=2147483647 "
                f"AND ({column}->>'open_files_max')::numeric<=2147483647",
                f"({column}->>'memory_max_bytes')::numeric+"
                f"({column}->>'memory_swap_max_bytes')::numeric<=9007199254740991",
                f"({column}->>'heartbeat_interval_ms')::numeric<({column}->>'heartbeat_grace_ms')::numeric",
                f"({column}->>'heartbeat_grace_ms')::numeric<({column}->>'maximum_wall_ms')::numeric",
                f"({column}->>'cancel_poll_ms')::numeric<({column}->>'maximum_wall_ms')::numeric",
            ]
        )
    elif fields == ESCROW_VECTOR_FIELDS:
        checks.extend(
            f"({column}->>'{field}')::numeric>0"
            for field in (
                "guardian_pids",
                "guardian_memory_bytes",
                "journal_bytes",
                "journal_records",
            )
        )
        checks.append(f"({column}->>'guardian_pids')::numeric<=2147483647")
        checks.append(f"({column}->>'guardian_cpu_quota_us')::numeric>=1000")
        checks.append(f"({column}->>'guardian_cpu_period_us')::numeric BETWEEN 1000 AND 1000000")
        checks.append(
            f"({column}->>'guardian_memory_bytes')::numeric+"
            f"({column}->>'guardian_swap_bytes')::numeric<=9007199254740991"
        )
        checks.append(
            f"({column}->>'journal_records')::numeric<=({column}->>'journal_bytes')::numeric"
        )
        checks.append(
            "+".join(
                f"({column}->>'{field}')::numeric"
                for field in fields
                if field.endswith("open_files")
            )
            + "<=2147483647"
        )
    expression = " AND ".join(checks)
    return CheckConstraint(
        f"{column} IS NULL OR ({expression})" if nullable else expression, name=name
    )


class NativeValidationPolicyRow(NativeTrainingBase):
    __tablename__ = "native_validation_policy"
    policy_id: Mapped[UUID] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    policy_sha256: Mapped[bytes] = mapped_column(BYTEA, unique=True)
    policy_bytes: Mapped[bytes] = mapped_column(BYTEA)
    server_instance_id: Mapped[UUID]
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    environment_sha256: Mapped[bytes] = mapped_column(BYTEA)
    implementation_sha256: Mapped[bytes] = mapped_column(BYTEA)
    measurement_sha256: Mapped[bytes] = mapped_column(BYTEA)
    report_sha256: Mapped[bytes] = mapped_column(BYTEA)
    internal_io_policy_revision: Mapped[int] = mapped_column(BigInteger)
    internal_io_profile_version: Mapped[int] = mapped_column(Integer)
    prep_limits: Mapped[dict[str, int]] = mapped_column(JSONB)
    final_limits: Mapped[dict[str, int]] = mapped_column(JSONB)
    host_vector: Mapped[dict[str, int]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        _positive(
            "ck_native_policy_counters", "revision", "identity_epoch", "internal_io_policy_revision"
        ),
        _hashes(
            "ck_native_policy_roots",
            "policy_sha256",
            "environment_sha256",
            "implementation_sha256",
            "measurement_sha256",
            "report_sha256",
        ),
        _document("ck_native_policy_document", "policy_bytes", "policy_sha256"),
        CheckConstraint("internal_io_profile_version=4", name="ck_native_policy_io_profile"),
        _vector("ck_native_policy_prep_limits", "prep_limits", VALIDATION_LIMIT_FIELDS),
        _vector("ck_native_policy_final_limits", "final_limits", VALIDATION_LIMIT_FIELDS),
        _vector("ck_native_policy_host_vector", "host_vector", RESOURCE_VECTOR_FIELDS),
        {"schema": "ml"},
    )


class NativeTrainingOperationRow(NativeTrainingBase):
    __tablename__ = "native_training_operation"
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(unique=True)
    execution_id: Mapped[UUID] = mapped_column(unique=True)
    server_instance_id: Mapped[UUID]
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    policy_sha256: Mapped[bytes] = mapped_column(
        BYTEA, ForeignKey("ml.native_validation_policy.policy_sha256")
    )
    selection_sha256: Mapped[bytes] = mapped_column(BYTEA)
    configuration_sha256: Mapped[bytes] = mapped_column(BYTEA)
    source_root_sha256: Mapped[bytes] = mapped_column(BYTEA)
    tokenizer_sha256: Mapped[bytes] = mapped_column(BYTEA)
    approval_sha256: Mapped[bytes] = mapped_column(BYTEA)
    request_bytes: Mapped[bytes] = mapped_column(BYTEA)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA)
    retention_policy_sha256: Mapped[bytes] = mapped_column(BYTEA)
    retention_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    max_prep_attempts: Mapped[int] = mapped_column(Integer)
    max_final_attempts: Mapped[int] = mapped_column(Integer)
    maximum_wall_ms: Mapped[int] = mapped_column(BigInteger)
    maximum_work_units: Mapped[int] = mapped_column(BigInteger)
    work_units_used: Mapped[int] = mapped_column(BigInteger)
    training_resource_vector: Mapped[dict[str, int] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    restore_revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    terminal_latched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        _hashes(
            "ck_native_operation_roots",
            "policy_sha256",
            "selection_sha256",
            "configuration_sha256",
            "source_root_sha256",
            "tokenizer_sha256",
            "approval_sha256",
            "request_sha256",
            "retention_policy_sha256",
        ),
        _positive(
            "ck_native_operation_counters",
            "identity_epoch",
            "maximum_wall_ms",
            "maximum_work_units",
        ),
        _document("ck_native_operation_request", "request_bytes", "request_sha256"),
        _vector(
            "ck_native_operation_training_vector",
            "training_resource_vector",
            RESOURCE_VECTOR_FIELDS,
            nullable=True,
        ),
        CheckConstraint(
            "max_prep_attempts BETWEEN 1 AND 16 AND max_final_attempts BETWEEN 1 AND 16"
            " AND work_units_used BETWEEN 0 AND maximum_work_units",
            name="ck_native_operation_attempts",
        ),
        CheckConstraint(
            "state IN ('PREP','TRAIN','FINAL','CLEANUP','CLOSED') AND "
            "deadline_at>created_at AND retention_until>=deadline_at AND "
            "((state='CLOSED')=(closed_at IS NOT NULL))",
            name="ck_native_operation_state",
        ),
        UniqueConstraint(
            "operation_id",
            "server_instance_id",
            "identity_epoch",
            name="uq_native_operation_identity",
        ),
        UniqueConstraint("operation_id", "run_id", "execution_id", name="uq_native_operation_run"),
        {"schema": "ml"},
    )


class NativeValidationStageRow(NativeTrainingBase):
    __tablename__ = "native_validation_stage"
    stage_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.native_training_operation.operation_id")
    )
    phase: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer)
    policy_sha256: Mapped[bytes] = mapped_column(
        BYTEA, ForeignKey("ml.native_validation_policy.policy_sha256")
    )
    policy_revision: Mapped[int] = mapped_column(BigInteger)
    stage_token_sha256: Mapped[bytes] = mapped_column(BYTEA, unique=True)
    resource_vector: Mapped[dict[str, int]] = mapped_column(JSONB)
    limits: Mapped[dict[str, int]] = mapped_column(JSONB)
    io_owner: Mapped[str] = mapped_column(Text)
    training_execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(TrainingExecutionRow.__table__.c.execution_id)
    )
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    child_pid: Mapped[int | None] = mapped_column(BigInteger)
    process_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    exit_kind: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(BigInteger)
    exit_evidence_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    cleanup_evidence_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        _hashes(
            "ck_native_stage_roots",
            "policy_sha256",
            "stage_token_sha256",
            "process_identity_sha256",
            "exit_evidence_sha256",
            "cleanup_evidence_sha256",
        ),
        _positive("ck_native_stage_policy", "policy_revision"),
        _vector("ck_native_stage_resources", "resource_vector", RESOURCE_VECTOR_FIELDS),
        _vector("ck_native_stage_limits", "limits", VALIDATION_LIMIT_FIELDS),
        CheckConstraint(
            "phase IN ('PREP','FINAL') AND attempt BETWEEN 1 AND 16 AND state IN "
            "('PREPARED','RUNNING','STOPPING','CLOSED') AND deadline_at>created_at",
            name="ck_native_stage_state",
        ),
        CheckConstraint(
            "(phase='PREP' AND io_owner='SELF' AND training_execution_id IS NULL AND "
            "resource_vector->>'io_slots'='1') OR (phase='FINAL' AND "
            "io_owner='TRAINING' AND training_execution_id IS NOT NULL AND "
            "resource_vector->>'io_slots'='0')",
            name="ck_native_stage_io_owner",
        ),
        CheckConstraint(
            "(child_pid IS NULL AND process_identity_sha256 IS NULL AND started_at IS "
            "NULL AND heartbeat_at IS NULL) OR (child_pid IS NOT NULL AND child_pid "
            "BETWEEN 1 AND 2147483647 AND process_identity_sha256 IS NOT NULL AND "
            "started_at IS NOT NULL AND heartbeat_at IS NOT NULL AND "
            "heartbeat_at>=started_at)",
            name="ck_native_stage_child",
        ),
        CheckConstraint(
            "(state='PREPARED' AND started_at IS NULL AND stopped_at IS NULL AND "
            "closed_at IS NULL) OR (state='RUNNING' AND started_at IS NOT NULL AND "
            "stopped_at IS NULL AND closed_at IS NULL) OR (state='STOPPING' AND "
            "stopped_at IS NOT NULL AND closed_at IS NULL) OR (state='CLOSED' AND "
            "stopped_at IS NOT NULL AND closed_at IS NOT NULL AND closed_at>=stopped_at"
            " AND exit_evidence_sha256 IS NOT NULL AND cleanup_evidence_sha256 IS NOT "
            "NULL)",
            name="ck_native_stage_times",
        ),
        CheckConstraint(
            "exit_kind IS NULL OR exit_kind IN ('NOT_STARTED','PROCESS_EXIT','SUPERVISOR_EXIT')",
            name="ck_native_stage_exit_kind",
        ),
        CheckConstraint(
            "(exit_evidence_sha256 IS NULL AND exit_kind IS NULL AND exit_code IS NULL) OR "
            "(exit_evidence_sha256 IS NOT NULL AND exit_kind IS NOT NULL AND "
            "((exit_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL) OR "
            "(exit_kind IN ('PROCESS_EXIT','SUPERVISOR_EXIT') AND child_pid IS NOT NULL)))",
            name="ck_native_stage_exit_evidence",
        ),
        UniqueConstraint("operation_id", "phase", "attempt", name="uq_native_stage_attempt"),
        UniqueConstraint("stage_id", "operation_id", name="uq_native_stage_operation"),
        Index(
            "uq_native_stage_unclosed",
            "operation_id",
            "phase",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
        {"schema": "ml"},
    )


class NativeTrainingEscrowReservationRow(NativeTrainingBase):
    __tablename__ = "native_training_escrow_reservation"
    escrow_generation: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.native_training_operation.operation_id"), unique=True
    )
    root_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    scope_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    guardian_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    control_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    policy_sha256: Mapped[bytes] = mapped_column(
        BYTEA, ForeignKey("ml.native_validation_policy.policy_sha256")
    )
    report_sha256: Mapped[bytes] = mapped_column(BYTEA)
    escrow_vector: Mapped[dict[str, int]] = mapped_column(JSONB)
    resource_vector: Mapped[dict[str, int]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    held_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    releasing_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    release_decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "ml.native_training_recovery_decision.decision_id",
            use_alter=True,
            name="fk_native_escrow_release_decision",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    release_ack_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    cleanup_evidence_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    __table_args__ = (
        _hashes(
            "ck_native_escrow_roots",
            "root_identity_sha256",
            "scope_identity_sha256",
            "guardian_identity_sha256",
            "control_identity_sha256",
            "policy_sha256",
            "report_sha256",
            "release_ack_sha256",
            "cleanup_evidence_sha256",
        ),
        _vector("ck_native_escrow_vector", "escrow_vector", ESCROW_VECTOR_FIELDS),
        _vector("ck_native_escrow_resources", "resource_vector", RESOURCE_VECTOR_FIELDS),
        CheckConstraint("resource_vector->>'io_slots'='0'", name="ck_native_escrow_no_io_slot"),
        CheckConstraint(
            "(state='RESERVED' AND held_at IS NULL AND releasing_at IS NULL AND "
            "closed_at IS NULL) OR (state='HELD' AND held_at IS NOT NULL AND "
            "root_identity_sha256 IS NOT NULL AND scope_identity_sha256 IS NOT NULL AND"
            " control_identity_sha256 IS NOT NULL AND guardian_identity_sha256 IS NOT "
            "NULL AND releasing_at IS NULL AND closed_at IS NULL) OR (state='RELEASING'"
            " AND releasing_at IS NOT NULL AND release_decision_id IS NOT NULL AND "
            "closed_at IS NULL) OR (state='CLOSED' AND releasing_at IS NOT NULL AND "
            "closed_at IS NOT NULL AND closed_at>=releasing_at AND release_decision_id "
            "IS NOT NULL AND release_ack_sha256 IS NOT NULL AND cleanup_evidence_sha256"
            " IS NOT NULL)",
            name="ck_native_escrow_state",
        ),
        UniqueConstraint("escrow_generation", "operation_id", name="uq_native_escrow_operation"),
        {"schema": "ml"},
    )


class NativeTrainingSourceWindowRow(NativeTrainingBase):
    __tablename__ = "native_training_source_window"
    window_id: Mapped[UUID] = mapped_column(primary_key=True)
    stage_id: Mapped[UUID]
    operation_id: Mapped[UUID]
    server_instance_id: Mapped[UUID]
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    token_sha256: Mapped[bytes] = mapped_column(BYTEA, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    database_oid: Mapped[int] = mapped_column(BigInteger)
    session_role_oid: Mapped[int] = mapped_column(BigInteger)
    backend_pid: Mapped[int] = mapped_column(BigInteger)
    backend_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    postmaster_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    registration_xid: Mapped[str] = mapped_column(
        Text, server_default=text("pg_current_xact_id()::text")
    )
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    entered_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    terminal_receipt_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    __table_args__ = (
        ForeignKeyConstraint(
            ["stage_id", "operation_id"],
            ["ml.native_validation_stage.stage_id", "ml.native_validation_stage.operation_id"],
        ),
        ForeignKeyConstraint(
            ["operation_id", "server_instance_id", "identity_epoch"],
            [
                "ml.native_training_operation.operation_id",
                "ml.native_training_operation.server_instance_id",
                "ml.native_training_operation.identity_epoch",
            ],
        ),
        _hashes("ck_native_window_roots", "token_sha256", "terminal_receipt_sha256"),
        CheckConstraint(
            "identity_epoch BETWEEN 1 AND 9007199254740991 AND database_oid BETWEEN 1 "
            "AND 4294967295 AND session_role_oid BETWEEN 1 AND 4294967295 AND "
            "backend_pid BETWEEN 1 AND 2147483647 AND backend_start>=postmaster_start",
            name="ck_native_window_backend",
        ),
        CheckConstraint(
            "kind IN ('CAPTURE','REBIND') AND deadline_at>created_at AND "
            "((state='REGISTERED' AND entered_at IS NULL AND terminal_at IS NULL) OR "
            "(state='ACTIVE' AND entered_at IS NOT NULL AND terminal_at IS NULL) OR "
            "(state IN ('REVOKED','CLOSED') AND terminal_at IS NOT NULL AND "
            "terminal_receipt_sha256 IS NOT NULL))",
            name="ck_native_window_state",
        ),
        UniqueConstraint(
            "window_id",
            "operation_id",
            "stage_id",
            "server_instance_id",
            "identity_epoch",
            "token_sha256",
            name="uq_native_window_exact",
        ),
        UniqueConstraint("stage_id", "kind", name="uq_native_window_stage_kind"),
        Index(
            "uq_native_window_live_stage",
            "stage_id",
            unique=True,
            postgresql_where=text("terminal_at IS NULL"),
        ),
        {"schema": "ml"},
    )


class NativeTrainingReplayInterlockRow(NativeTrainingBase):
    __tablename__ = "native_training_replay_interlock"
    server_instance_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[UUID | None]
    stage_id: Mapped[UUID | None]
    window_id: Mapped[UUID | None]
    identity_epoch: Mapped[int | None] = mapped_column(BigInteger)
    token_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "window_id",
                "operation_id",
                "stage_id",
                "server_instance_id",
                "identity_epoch",
                "token_sha256",
            ],
            [
                "ml.native_training_source_window.window_id",
                "ml.native_training_source_window.operation_id",
                "ml.native_training_source_window.stage_id",
                "ml.native_training_source_window.server_instance_id",
                "ml.native_training_source_window.identity_epoch",
                "ml.native_training_source_window.token_sha256",
            ],
        ),
        CheckConstraint(
            "num_nonnulls(operation_id,stage_id,window_id,identity_epoch,token_sha256) IN (0,5)",
            name="ck_native_interlock_complete",
        ),
        _hashes("ck_native_interlock_token", "token_sha256"),
        {"schema": "ml"},
    )


class _ReceiptColumns:
    receipt_bytes: Mapped[bytes] = mapped_column(BYTEA)
    receipt_sha256: Mapped[bytes] = mapped_column(BYTEA)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


class NativeTrainingPrepAttemptReceiptRow(_ReceiptColumns, NativeTrainingBase):
    __tablename__ = "native_training_prep_attempt_receipt"
    stage_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[UUID]
    numerical_evidence_sha256: Mapped[bytes] = mapped_column(BYTEA)
    snapshot_sha256: Mapped[bytes] = mapped_column(BYTEA)
    installed_inventory_sha256: Mapped[bytes] = mapped_column(BYTEA)
    full_owner_binding_sha256: Mapped[bytes] = mapped_column(BYTEA)
    source_window_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.native_training_source_window.window_id")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["stage_id", "operation_id"],
            ["ml.native_validation_stage.stage_id", "ml.native_validation_stage.operation_id"],
        ),
        _hashes(
            "ck_native_prep_receipt_roots",
            "receipt_sha256",
            "numerical_evidence_sha256",
            "snapshot_sha256",
            "installed_inventory_sha256",
            "full_owner_binding_sha256",
        ),
        _document("ck_native_prep_receipt_document", "receipt_bytes", "receipt_sha256"),
        UniqueConstraint("stage_id", "receipt_sha256", name="uq_native_prep_receipt_exact"),
        {"schema": "ml"},
    )


class NativeTrainingPrepAcceptanceRow(NativeTrainingBase):
    __tablename__ = "native_training_prep_acceptance"
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.native_training_operation.operation_id"), primary_key=True
    )
    stage_id: Mapped[UUID] = mapped_column(unique=True)
    prep_receipt_sha256: Mapped[bytes] = mapped_column(BYTEA)
    exit_evidence_sha256: Mapped[bytes] = mapped_column(BYTEA)
    exit_code: Mapped[int] = mapped_column(Integer)
    accepted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["stage_id", "prep_receipt_sha256"],
            [
                "ml.native_training_prep_attempt_receipt.stage_id",
                "ml.native_training_prep_attempt_receipt.receipt_sha256",
            ],
        ),
        _hashes("ck_native_prep_acceptance_roots", "prep_receipt_sha256", "exit_evidence_sha256"),
        CheckConstraint("exit_code=0", name="ck_native_prep_acceptance_success"),
        {"schema": "ml"},
    )


class NativeTrainingFinalAttemptReceiptRow(_ReceiptColumns, NativeTrainingBase):
    __tablename__ = "native_training_final_attempt_receipt"
    stage_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[UUID]
    candidate_seal_sha256: Mapped[bytes] = mapped_column(BYTEA)
    consumed_input_receipt_sha256: Mapped[bytes] = mapped_column(BYTEA)
    snapshot_sha256: Mapped[bytes] = mapped_column(BYTEA)
    installed_inventory_sha256: Mapped[bytes] = mapped_column(BYTEA)
    source_window_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.native_training_source_window.window_id")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["stage_id", "operation_id"],
            ["ml.native_validation_stage.stage_id", "ml.native_validation_stage.operation_id"],
        ),
        _hashes(
            "ck_native_final_receipt_roots",
            "receipt_sha256",
            "candidate_seal_sha256",
            "consumed_input_receipt_sha256",
            "snapshot_sha256",
            "installed_inventory_sha256",
        ),
        _document("ck_native_final_receipt_document", "receipt_bytes", "receipt_sha256"),
        UniqueConstraint("stage_id", "receipt_sha256", name="uq_native_final_receipt_exact"),
        {"schema": "ml"},
    )


class NativeTrainingRecoveryDecisionRow(_ReceiptColumns, NativeTrainingBase):
    __tablename__ = "native_training_recovery_decision"
    decision_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[UUID]
    stage_id: Mapped[UUID]
    window_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ml.native_training_source_window.window_id")
    )
    escrow_generation: Mapped[UUID]
    server_instance_id: Mapped[UUID]
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    revoked_stage_token_sha256: Mapped[bytes] = mapped_column(BYTEA)
    revoked_window_token_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    backend_incarnation_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    exit_evidence_sha256: Mapped[bytes] = mapped_column(BYTEA)
    terminal_receipt_sha256: Mapped[bytes] = mapped_column(BYTEA)
    __table_args__ = (
        ForeignKeyConstraint(
            ["stage_id", "operation_id"],
            ["ml.native_validation_stage.stage_id", "ml.native_validation_stage.operation_id"],
        ),
        ForeignKeyConstraint(
            ["escrow_generation", "operation_id"],
            [
                "ml.native_training_escrow_reservation.escrow_generation",
                "ml.native_training_escrow_reservation.operation_id",
            ],
        ),
        ForeignKeyConstraint(
            ["operation_id", "server_instance_id", "identity_epoch"],
            [
                "ml.native_training_operation.operation_id",
                "ml.native_training_operation.server_instance_id",
                "ml.native_training_operation.identity_epoch",
            ],
        ),
        _hashes(
            "ck_native_recovery_roots",
            "receipt_sha256",
            "revoked_stage_token_sha256",
            "revoked_window_token_sha256",
            "backend_incarnation_sha256",
            "exit_evidence_sha256",
            "terminal_receipt_sha256",
        ),
        _document("ck_native_recovery_document", "receipt_bytes", "receipt_sha256"),
        CheckConstraint(
            "(window_id IS NULL)=(revoked_window_token_sha256 IS NULL) AND (window_id "
            "IS NULL)=(backend_incarnation_sha256 IS NULL)",
            name="ck_native_recovery_window",
        ),
        {"schema": "ml"},
    )


class NativeTrainingConsumedInputRow(_ReceiptColumns, NativeTrainingBase):
    __tablename__ = "native_training_consumed_input"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey(TrainingRunRow.__table__.c.run_id), primary_key=True
    )
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey(TrainingExecutionRow.__table__.c.execution_id), unique=True
    )
    prep_stage_id: Mapped[UUID] = mapped_column(ForeignKey("ml.native_validation_stage.stage_id"))
    server_instance_id: Mapped[UUID]
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    selection_sha256: Mapped[bytes] = mapped_column(BYTEA)
    prep_receipt_sha256: Mapped[bytes] = mapped_column(BYTEA)
    snapshot_sha256: Mapped[bytes] = mapped_column(BYTEA)
    source_manifest_sha256: Mapped[bytes] = mapped_column(BYTEA)
    authorized_dataset_sha256: Mapped[bytes] = mapped_column(BYTEA)
    producer_provenance_sha256: Mapped[bytes] = mapped_column(BYTEA)
    source_owner_key_id: Mapped[str] = mapped_column(Text)
    source_owner_count: Mapped[int] = mapped_column(Integer)
    source_owner_set_sha256: Mapped[bytes] = mapped_column(BYTEA)
    training_lineage_key_id: Mapped[str] = mapped_column(Text)
    registered_member_count: Mapped[int] = mapped_column(Integer)
    full_owner_binding_sha256: Mapped[bytes] = mapped_column(BYTEA)
    registered_member_binding_sha256: Mapped[bytes] = mapped_column(BYTEA)
    registration_sha256: Mapped[bytes] = mapped_column(BYTEA)
    training_input_binding_sha256: Mapped[bytes] = mapped_column(BYTEA)
    __table_args__ = (
        ForeignKeyConstraint(
            ["operation_id", "run_id", "execution_id"],
            [
                "ml.native_training_operation.operation_id",
                "ml.native_training_operation.run_id",
                "ml.native_training_operation.execution_id",
            ],
        ),
        _hashes(
            "ck_native_consumed_input_roots",
            "receipt_sha256",
            "selection_sha256",
            "prep_receipt_sha256",
            "snapshot_sha256",
            "source_manifest_sha256",
            "authorized_dataset_sha256",
            "producer_provenance_sha256",
            "source_owner_set_sha256",
            "full_owner_binding_sha256",
            "registered_member_binding_sha256",
            "registration_sha256",
            "training_input_binding_sha256",
        ),
        _document("ck_native_consumed_input_document", "receipt_bytes", "receipt_sha256", 16384),
        _positive("ck_native_consumed_input_epoch", "identity_epoch"),
        CheckConstraint(
            "source_owner_count BETWEEN 1 AND 4096 AND "
            "source_owner_count=registered_member_count AND source_owner_key_id ~ "
            "'^[A-Za-z0-9_.-]{1,128}$' AND training_lineage_key_id ~ "
            "'^[A-Za-z0-9_.-]{1,128}$'",
            name="ck_native_consumed_input_members",
        ),
        UniqueConstraint("run_id", "receipt_sha256", name="uq_native_consumed_input_exact"),
        {"schema": "ml"},
    )


class NativeTrainingConsumedOwnerRow(NativeTrainingBase):
    __tablename__ = "native_training_consumed_owner"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.native_training_consumed_input.run_id"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(UserAccountRow.__table__.c.user_id, ondelete="CASCADE"), primary_key=True
    )
    source_owner_tag: Mapped[bytes] = mapped_column(BYTEA)
    training_owner_tag: Mapped[bytes] = mapped_column(BYTEA)
    training_consent_revision: Mapped[int] = mapped_column(BigInteger)
    source_owner_evidence_sha256: Mapped[bytes] = mapped_column(BYTEA)
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "user_id"],
            [TrainingParticipantRow.__table__.c.run_id, TrainingParticipantRow.__table__.c.user_id],
            ondelete="CASCADE",
        ),
        _hashes(
            "ck_native_consumed_owner_roots",
            "source_owner_tag",
            "training_owner_tag",
            "source_owner_evidence_sha256",
        ),
        _positive("ck_native_consumed_owner_revision", "training_consent_revision"),
        UniqueConstraint("run_id", "source_owner_tag", name="uq_native_consumed_source_tag"),
        UniqueConstraint("run_id", "training_owner_tag", name="uq_native_consumed_training_tag"),
        Index("ix_native_consumed_owner_user", "user_id", "run_id"),
        {"schema": "ml"},
    )


class NativeTrainingConsumedFinalRow(_ReceiptColumns, NativeTrainingBase):
    __tablename__ = "native_training_consumed_final"
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    execution_id: Mapped[UUID] = mapped_column(unique=True)
    final_stage_id: Mapped[UUID]
    server_instance_id: Mapped[UUID]
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    consumed_input_receipt_sha256: Mapped[bytes] = mapped_column(BYTEA)
    candidate_seal_sha256: Mapped[bytes] = mapped_column(BYTEA)
    final_seal_receipt_sha256: Mapped[bytes] = mapped_column(BYTEA)
    exit_evidence_sha256: Mapped[bytes] = mapped_column(BYTEA)
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "consumed_input_receipt_sha256"],
            [
                "ml.native_training_consumed_input.run_id",
                "ml.native_training_consumed_input.receipt_sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["final_stage_id", "final_seal_receipt_sha256"],
            [
                "ml.native_training_final_attempt_receipt.stage_id",
                "ml.native_training_final_attempt_receipt.receipt_sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["operation_id", "run_id", "execution_id"],
            [
                "ml.native_training_operation.operation_id",
                "ml.native_training_operation.run_id",
                "ml.native_training_operation.execution_id",
            ],
        ),
        _hashes(
            "ck_native_consumed_final_roots",
            "receipt_sha256",
            "consumed_input_receipt_sha256",
            "candidate_seal_sha256",
            "final_seal_receipt_sha256",
            "exit_evidence_sha256",
        ),
        _document("ck_native_consumed_final_document", "receipt_bytes", "receipt_sha256", 16384),
        _positive("ck_native_consumed_final_epoch", "identity_epoch"),
        {"schema": "ml"},
    )


NATIVE_TRAINING_ROWS = (
    NativeValidationPolicyRow,
    NativeTrainingOperationRow,
    NativeValidationStageRow,
    NativeTrainingEscrowReservationRow,
    NativeTrainingSourceWindowRow,
    NativeTrainingReplayInterlockRow,
    NativeTrainingPrepAttemptReceiptRow,
    NativeTrainingPrepAcceptanceRow,
    NativeTrainingFinalAttemptReceiptRow,
    NativeTrainingRecoveryDecisionRow,
    NativeTrainingConsumedInputRow,
    NativeTrainingConsumedOwnerRow,
    NativeTrainingConsumedFinalRow,
)
NATIVE_TRAINING_TABLES: tuple[Table, ...] = tuple(
    cast(Table, row.__table__) for row in NATIVE_TRAINING_ROWS
)
