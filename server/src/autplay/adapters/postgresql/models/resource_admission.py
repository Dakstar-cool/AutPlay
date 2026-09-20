"""Policy, fair queue and fenced I/O permits shared by all API and worker processes."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ResourceQuotaPolicyRow(Base):
    __tablename__ = "resource_quota_policy"
    singleton_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    default_devices: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    default_playbacks: Mapped[int] = mapped_column(Integer, server_default=text("2"))
    default_transfers: Mapped[int] = mapped_column(Integer, server_default=text("2"))
    global_playbacks: Mapped[int | None] = mapped_column(Integer)
    global_transfers: Mapped[int | None] = mapped_column(Integer)
    playback_ceiling: Mapped[int | None] = mapped_column(Integer)
    transfer_ceiling: Mapped[int | None] = mapped_column(Integer)
    budget_evidence: Mapped[str | None] = mapped_column(Text)
    grant_sequence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        CheckConstraint("singleton_id=1", name="quota_policy_singleton_check"),
        CheckConstraint("revision>=1 AND grant_sequence>=0", name="quota_policy_versions_check"),
        CheckConstraint(
            "default_devices BETWEEN 1 AND 1000000 AND default_playbacks BETWEEN 1 AND 1000000 "
            "AND default_transfers BETWEEN 1 AND 1000000",
            name="quota_policy_defaults_check",
        ),
        CheckConstraint(
            "(global_playbacks IS NULL AND global_transfers IS NULL AND playback_ceiling IS NULL "
            "AND transfer_ceiling IS NULL AND budget_evidence IS NULL) OR "
            "(global_playbacks IS NOT NULL AND global_transfers IS NOT NULL "
            "AND playback_ceiling IS NOT NULL AND transfer_ceiling IS NOT NULL "
            "AND budget_evidence IS NOT NULL AND length(budget_evidence) BETWEEN 1 AND 240 "
            "AND global_playbacks BETWEEN 1 AND playback_ceiling "
            "AND global_transfers BETWEEN 1 AND transfer_ceiling "
            "AND playback_ceiling BETWEEN 1 AND 1000000 AND transfer_ceiling BETWEEN "
            "1 AND 1000000)",
            name="quota_policy_measured_budget_check",
        ),
        {"schema": "account"},
    )


class AccountQuotaOverrideRow(Base):
    __tablename__ = "account_quota_override"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(BigInteger)
    devices: Mapped[int | None] = mapped_column(Integer)
    playbacks: Mapped[int | None] = mapped_column(Integer)
    transfers: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint("revision>=1", name="quota_override_revision_check"),
        CheckConstraint(
            "(devices IS NULL OR devices BETWEEN 1 AND 1000000) "
            "AND (playbacks IS NULL OR playbacks BETWEEN 1 AND 1000000) "
            "AND (transfers IS NULL OR transfers BETWEEN 1 AND 1000000)",
            name="quota_override_limits_check",
        ),
        {"schema": "account"},
    )


class ResourceGrantCursorRow(Base):
    __tablename__ = "resource_grant_cursor"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    last_grant: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        CheckConstraint("kind IN ('PLAYBACK','TRANSFER')", name="quota_grant_kind_check"),
        CheckConstraint("last_grant>=0", name="quota_grant_sequence_check"),
        {"schema": "account"},
    )


class ResourceAdmissionRow(Base):
    __tablename__ = "resource_admission"
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    authority_generation: Mapped[int] = mapped_column(BigInteger)
    authority_kind: Mapped[str] = mapped_column(Text)
    device_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    session_family_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    session_mode: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("jobs.job.job_id"))
    job_worker_id: Mapped[str | None] = mapped_column(Text)
    job_attempt: Mapped[int | None] = mapped_column(Integer)
    acquisition_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("discovery.acquisition_attempt.acquisition_attempt_id")
    )
    source_authorization_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    source_authorization_revision: Mapped[int | None] = mapped_column(BigInteger)
    policy_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    policy_revision: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(Text)
    resource_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    target_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    request_sha256: Mapped[bytes] = mapped_column(BYTEA)
    state: Mapped[str] = mapped_column(Text)
    activation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    waiting_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    enqueued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    claim_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    attachment_revision: Mapped[int] = mapped_column(BigInteger)
    current_recording_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("catalog.recording.recording_id")
    )
    next_recording_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("catalog.recording.recording_id")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="admission_device_owner_fkey",
        ),
        ForeignKeyConstraint(
            ["source_authorization_id", "source_authorization_revision"],
            [
                "discovery.source_authorization.authorization_id",
                "discovery.source_authorization.revision",
            ],
            name="admission_source_revision_fkey",
        ),
        ForeignKeyConstraint(
            ["policy_id", "policy_revision"],
            [
                "discovery.artist_policy_revision.policy_id",
                "discovery.artist_policy_revision.revision",
            ],
            name="admission_policy_revision_fkey",
        ),
        UniqueConstraint(
            "operation_id", "activation_id", "generation", name="admission_activation_key"
        ),
        CheckConstraint(
            "authority_generation>=1 AND generation>=0 AND attachment_revision>=0",
            name="admission_versions_check",
        ),
        CheckConstraint("octet_length(request_sha256)=32", name="admission_request_hash_check"),
        CheckConstraint(
            "(source_authorization_id IS NULL AND source_authorization_revision IS NULL) OR "
            "(source_authorization_id IS NOT NULL AND source_authorization_revision IS NOT NULL "
            "AND source_authorization_revision>=1)",
            name="admission_source_shape_check",
        ),
        CheckConstraint(
            "(policy_id IS NULL AND policy_revision IS NULL) OR "
            "(policy_id IS NOT NULL AND policy_revision IS NOT NULL AND policy_revision>=1)",
            name="admission_policy_shape_check",
        ),
        CheckConstraint(
            "state IN ('WAITING','ACTIVE','RELEASED','EXPIRED')", name="admission_state_check"
        ),
        CheckConstraint(
            "updated_at>=enqueued_at AND enqueued_at>=created_at "
            "AND (lease_until IS NULL OR lease_until>created_at) "
            "AND (waiting_until IS NULL OR waiting_until>created_at)",
            name="admission_deadlines_check",
        ),
        CheckConstraint(
            "(authority_kind='DEVICE_SESSION' AND device_id IS NOT NULL AND "
            "session_family_id IS NOT NULL "
            "AND session_mode IS NOT NULL AND session_mode IN ('V2','LEGACY') AND "
            "acquisition_attempt_id IS NULL) OR "
            "(authority_kind='LOCAL_BRIDGE' AND kind='TRANSFER' AND device_id IS NOT NULL "
            "AND session_family_id IS NULL AND session_mode IS NULL AND job_id IS NULL "
            "AND acquisition_attempt_id IS NULL AND source_authorization_id IS NULL "
            "AND source_authorization_revision IS NULL AND policy_id IS NULL "
            "AND policy_revision IS NULL) OR "
            "(authority_kind='SERVER_ACQUISITION' AND kind='TRANSFER' AND device_id IS NULL "
            "AND session_family_id IS NULL AND session_mode IS NULL AND "
            "acquisition_attempt_id IS NOT NULL "
            "AND job_id IS NOT NULL AND source_authorization_id IS NOT NULL AND "
            "source_authorization_revision IS NOT NULL)",
            name="admission_authority_shape_check",
        ),
        CheckConstraint(
            "(job_id IS NULL AND job_worker_id IS NULL AND job_attempt IS NULL) OR "
            "(job_id IS NOT NULL AND job_worker_id IS NOT NULL AND job_attempt IS NOT NULL "
            "AND length(job_worker_id) BETWEEN 1 AND 120 AND job_attempt>=1)",
            name="admission_job_fence_check",
        ),
        CheckConstraint(
            "(kind='PLAYBACK' AND resource_type='PLAY_INSTANCE' AND target_id IS NULL) OR "
            "(kind='TRANSFER' AND target_id IS NOT NULL AND resource_type IN "
            "('DOWNLOAD_INTENT','UPLOAD_INTENT','INTERNET_ACQUISITION','DISCOVERY_ACQUISITION') "
            "AND current_recording_id IS NULL AND next_recording_id IS NULL)",
            name="admission_resource_shape_check",
        ),
        CheckConstraint(
            "(resource_type<>'DISCOVERY_ACQUISITION' OR "
            "(authority_kind='SERVER_ACQUISITION' AND target_id=acquisition_attempt_id)) "
            "AND (authority_kind<>'SERVER_ACQUISITION' OR resource_type='DISCOVERY_ACQUISITION') "
            "AND (authority_kind<>'LOCAL_BRIDGE' OR resource_type='UPLOAD_INTENT') "
            "AND (resource_type<>'INTERNET_ACQUISITION' OR "
            "(authority_kind='DEVICE_SESSION' AND job_id IS NOT NULL))",
            name="admission_worker_target_check",
        ),
        CheckConstraint(
            "(activation_id IS NULL AND generation=0) OR (activation_id IS NOT NULL "
            "AND generation>=1)",
            name="admission_activation_shape_check",
        ),
        CheckConstraint(
            "state<>'ACTIVE' OR (activation_id IS NOT NULL AND lease_until IS NOT "
            "NULL AND claim_until IS NOT NULL)",
            name="admission_active_shape_check",
        ),
        CheckConstraint(
            "state<>'WAITING' OR (job_id IS NULL AND waiting_until IS NOT NULL) OR "
            "(job_id IS NOT NULL AND waiting_until IS NULL)",
            name="admission_waiting_shape_check",
        ),
        CheckConstraint(
            "state NOT IN ('RELEASED','EXPIRED') OR terminal_at IS NOT NULL",
            name="admission_terminal_shape_check",
        ),
        Index("ix_admission_account_state", "user_id", "kind", "state"),
        Index(
            "ix_admission_waiting", "kind", "enqueued_at", postgresql_where=text("state='WAITING'")
        ),
        Index("ix_admission_lease_expiry", "lease_until", postgresql_where=text("state='ACTIVE'")),
        {"schema": "account"},
    )


class ResourceIoPermitRow(Base):
    __tablename__ = "resource_io_permit"
    permit_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    opened_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    renewed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "permit_id",
            "operation_id",
            "activation_id",
            "generation",
            "target_id",
            name="io_permit_execution_key",
        ),
        ForeignKeyConstraint(
            ["operation_id", "activation_id", "generation"],
            [
                "account.resource_admission.operation_id",
                "account.resource_admission.activation_id",
                "account.resource_admission.generation",
            ],
            name="io_permit_activation_fkey",
        ),
        CheckConstraint("generation>=1", name="io_permit_generation_check"),
        CheckConstraint(
            "renewed_at>=opened_at AND expires_at>renewed_at AND "
            "expires_at<=renewed_at+interval '5 seconds'",
            name="io_permit_deadline_check",
        ),
        Index("ix_io_permit_operation", "operation_id"),
        Index("ix_io_permit_expiry", "expires_at"),
        {"schema": "account"},
    )


class ResourceIoExecutionRow(Base):
    """Unclosed rows retain capacity; elapsed time never proves process death."""

    __tablename__ = "resource_io_execution"
    execution_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    permit_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    owner_run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    actual_target_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    child_pid: Mapped[int | None] = mapped_column(BigInteger)
    child_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    stop_requested_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(BigInteger)
    closure_kind: Mapped[str | None] = mapped_column(Text)
    closure_evidence_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    __table_args__ = (
        UniqueConstraint("permit_id", name="io_execution_permit_key"),
        ForeignKeyConstraint(
            ["permit_id", "operation_id", "activation_id", "generation", "target_id"],
            [
                f"account.resource_io_permit.{name}"
                for name in (
                    "permit_id",
                    "operation_id",
                    "activation_id",
                    "generation",
                    "target_id",
                )
            ],
            name="io_execution_permit_fkey",
        ),
        CheckConstraint("generation>=1", name="io_execution_generation_check"),
        CheckConstraint(
            "kind IN ('VAULT_STREAM','VAULT_UPLOAD','PROVIDER')",
            name="io_execution_kind_check",
        ),
        CheckConstraint(
            "state IN ('PREPARED','RUNNING','STOPPING','ORPHANED','CLOSED')",
            name="io_execution_state_check",
        ),
        CheckConstraint(
            "heartbeat_at>=created_at AND (started_at IS NULL OR started_at>=created_at) "
            "AND (stop_requested_at IS NULL OR stop_requested_at>=created_at) "
            "AND (closed_at IS NULL OR closed_at>=heartbeat_at)",
            name="io_execution_timestamps_check",
        ),
        CheckConstraint(
            "(child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL) OR "
            "(child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL "
            "AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL)",
            name="io_execution_child_check",
        ),
        CheckConstraint(
            "(state<>'PREPARED' OR child_pid IS NULL) "
            "AND (state<>'RUNNING' OR child_pid IS NOT NULL) "
            "AND (state<>'STOPPING' OR stop_requested_at IS NOT NULL)",
            name="io_execution_running_check",
        ),
        CheckConstraint(
            "(state<>'CLOSED' AND closed_at IS NULL AND exit_code IS NULL AND closure_kind "
            "IS NULL AND closure_evidence_sha256 IS NULL) OR "
            "(state='CLOSED' AND closed_at IS NOT NULL AND closure_kind IS NOT NULL "
            "AND closure_evidence_sha256 IS NOT NULL "
            "AND octet_length(closure_evidence_sha256)=32 AND "
            "((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL) OR "
            "(closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL) "
            "OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL)))",
            name="io_execution_closure_check",
        ),
        Index(
            "ix_io_execution_unclosed", "operation_id", postgresql_where=text("closed_at IS NULL")
        ),
        Index(
            "ix_io_execution_owner",
            "owner_run_id",
            "heartbeat_at",
            postgresql_where=text("closed_at IS NULL"),
        ),
        Index(
            "ix_io_execution_writer",
            "kind",
            "actual_target_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL AND kind IN ('VAULT_UPLOAD','PROVIDER')"),
        ),
        {"schema": "account"},
    )


class QuotaOperationReceiptRow(Base):
    __tablename__ = "quota_operation_receipt"
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    # Historical evidence only. Current WebSession is revalidated for every replay.
    web_session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    web_generation: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(Text)
    target_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("account.user_account.user_id"))
    request_sha256: Mapped[bytes] = mapped_column(BYTEA)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "action IN ('DEFAULTS','OVERRIDE','BUDGET')", name="quota_receipt_action_check"
        ),
        CheckConstraint("octet_length(request_sha256)=32", name="quota_receipt_hash_check"),
        CheckConstraint(
            "(action='OVERRIDE' AND target_user_id IS NOT NULL) OR "
            "(action IN ('DEFAULTS','BUDGET') AND target_user_id IS NULL)",
            name="quota_receipt_target_check",
        ),
        CheckConstraint(
            "(web_session_id IS NULL AND web_generation IS NULL) OR "
            "(web_session_id IS NOT NULL AND web_generation IS NOT NULL AND web_generation>=0)",
            name="quota_receipt_web_check",
        ),
        CheckConstraint(
            "jsonb_typeof(result)='object' AND octet_length(result::text)<=16384",
            name="quota_receipt_result_check",
        ),
        Index("ix_quota_receipt_created", "created_at"),
        {"schema": "account"},
    )
