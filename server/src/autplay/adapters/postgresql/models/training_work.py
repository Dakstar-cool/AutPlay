"""Current-authority training state; candidates do not confer serving authority."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class TrainingRunRow(Base):
    __tablename__ = "training_run"
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    server_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.server_instance.server_instance_id")
    )
    identity_epoch: Mapped[int] = mapped_column(BigInteger)
    lineage_key_id: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[bytes] = mapped_column(BYTEA)
    dataset_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    request_sha256: Mapped[bytes] = mapped_column(BYTEA)
    participant_count: Mapped[int] = mapped_column(Integer)
    registration_xid: Mapped[str] = mapped_column(
        Text, server_default=text("pg_current_xact_id()::text")
    )
    phase: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    publication_operation_id: Mapped[UUID | None] = mapped_column()
    publication_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    publication_hashes: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint(
            "phase IN ('PREPARING','READY','RUNNING','INVALIDATED','PUBLISHED') "
            "AND revision>=1 AND identity_epoch>=1 AND length(lineage_key_id) BETWEEN 1 AND 128 "
            "AND participant_count BETWEEN 1 AND 4096",
            name="training_run_state_check",
        ),
        CheckConstraint(
            "octet_length(source_sha256)=32 AND octet_length(request_sha256)=32 "
            "AND (dataset_sha256 IS NULL OR octet_length(dataset_sha256)=32)",
            name="training_run_hash_check",
        ),
        CheckConstraint(
            "(phase='PUBLISHED' AND dataset_sha256 IS NOT NULL "
            "AND publication_operation_id IS NOT NULL AND publication_sha256 IS NOT NULL "
            "AND octet_length(publication_sha256)=32 AND publication_hashes IS NOT NULL "
            "AND jsonb_typeof(publication_hashes)='object') OR "
            "(phase<>'PUBLISHED' AND publication_operation_id IS NULL "
            "AND publication_sha256 IS NULL AND publication_hashes IS NULL)",
            name="training_run_publication_check",
        ),
        CheckConstraint(
            "phase NOT IN ('READY','RUNNING','PUBLISHED') OR dataset_sha256 IS NOT NULL",
            name="training_run_dataset_check",
        ),
        UniqueConstraint(
            "publication_operation_id", name="training_run_publication_operation_unique"
        ),
        {"schema": "ml"},
    )


class TrainingParticipantRow(Base):
    __tablename__ = "training_participant"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("ml.training_run.run_id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id"), primary_key=True
    )
    consent_revision: Mapped[int] = mapped_column(BigInteger)
    owner_tag: Mapped[bytes] = mapped_column(BYTEA)
    __table_args__ = (
        CheckConstraint(
            "consent_revision>=1 AND octet_length(owner_tag)=32",
            name="training_participant_consent_check",
        ),
        UniqueConstraint("run_id", "owner_tag", name="training_participant_tag_unique"),
        Index("ix_training_participant_owner", "user_id", "run_id"),
        {"schema": "ml"},
    )


class TrainingPublicationRevocationRow(Base):
    __tablename__ = "training_publication_revocation"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("ml.training_run.run_id"), primary_key=True)
    owner_tag: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    deletion_request_id: Mapped[UUID]
    revoked_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "octet_length(owner_tag)=32",
            name="training_publication_revocation_owner_tag_check",
        ),
        {"schema": "ml"},
    )


class TrainingCleanupClaimRow(Base):
    __tablename__ = "training_cleanup_claim"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("ml.training_run.run_id"), primary_key=True)
    phase: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "(phase='PENDING' AND completed_at IS NULL) OR "
            "(phase='COMPLETE' AND completed_at IS NOT NULL AND completed_at>=requested_at)",
            name="training_cleanup_claim_state_check",
        ),
        {"schema": "ml"},
    )


class TrainingCheckpointRow(Base):
    __tablename__ = "training_checkpoint"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("ml.training_run.run_id"), primary_key=True)
    manifest_sha256: Mapped[bytes] = mapped_column(BYTEA)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("clock_timestamp()")
    )
    __table_args__ = (
        CheckConstraint("octet_length(manifest_sha256)=32", name="training_checkpoint_hash_check"),
        {"schema": "ml"},
    )


class TrainingExecutionRow(Base):
    __tablename__ = "training_execution"
    execution_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("ml.training_run.run_id"))
    input_root: Mapped[str] = mapped_column(Text)
    output_root: Mapped[str] = mapped_column(Text)
    input_root_device: Mapped[str] = mapped_column(Text)
    input_root_inode: Mapped[str] = mapped_column(Text)
    input_scope_device: Mapped[str] = mapped_column(Text)
    input_scope_inode: Mapped[str] = mapped_column(Text)
    output_scope_device: Mapped[str] = mapped_column(Text)
    output_scope_inode: Mapped[str] = mapped_column(Text)
    input_inventory_sha256: Mapped[bytes] = mapped_column(BYTEA)
    root_inventory_sha256: Mapped[bytes] = mapped_column(BYTEA)
    input_bytes: Mapped[int] = mapped_column(BigInteger)
    maximum_output_bytes: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    io_deadline_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    cleanup_started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    input_cleaned_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    input_cleanup_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    checkpoint_device: Mapped[str | None] = mapped_column(Text)
    checkpoint_inode: Mapped[str | None] = mapped_column(Text)
    retain_checkpoint: Mapped[bool | None]
    checkpoint_manifest_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    checkpoint_weights_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    checkpoint_optimizer_steps: Mapped[int | None] = mapped_column(BigInteger)
    checkpoint_device_type: Mapped[str | None] = mapped_column(Text)
    publication_seal_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    child_pid: Mapped[int | None] = mapped_column(BigInteger)
    child_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    closure_kind: Mapped[str | None] = mapped_column(Text)
    closure_evidence_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    exit_code: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        CheckConstraint(
            "octet_length(input_inventory_sha256)=32 "
            "AND octet_length(root_inventory_sha256)=32 "
            "AND input_bytes BETWEEN 1 AND 9223372036854775807 "
            "AND maximum_output_bytes BETWEEN 1 AND 9223372036854775807 "
            "AND length(input_root) BETWEEN 1 AND 4096 "
            "AND length(output_root) BETWEEN 1 AND 4096 "
            "AND input_root_device ~ '^[0-9]{1,32}$' "
            "AND input_root_inode ~ '^[0-9]{1,32}$' "
            "AND input_scope_device ~ '^[0-9]{1,32}$' "
            "AND input_scope_inode ~ '^[0-9]{1,32}$' "
            "AND output_scope_device ~ '^[0-9]{1,32}$' "
            "AND output_scope_inode ~ '^[0-9]{1,32}$' "
            "AND input_root ~ ('[\\\\/]'||execution_id::text||'$') "
            "AND input_root<>output_root "
            "AND state IN ('PREPARED','RUNNING','STOPPING','CLOSED')",
            name="training_execution_target_check",
        ),
        CheckConstraint(
            "(child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL "
            "AND heartbeat_at IS NULL AND io_deadline_at IS NULL) OR "
            "(child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL "
            "AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL "
            "AND started_at>=created_at AND heartbeat_at IS NOT NULL AND heartbeat_at>=started_at "
            "AND io_deadline_at IS NOT NULL AND io_deadline_at>heartbeat_at "
            "AND io_deadline_at<=heartbeat_at+interval '5 seconds')",
            name="training_execution_child_check",
        ),
        CheckConstraint(
            "(state='PREPARED' AND started_at IS NULL AND cleanup_started_at IS NULL "
            "AND closed_at IS NULL) OR "
            "(state='RUNNING' AND started_at IS NOT NULL AND cleanup_started_at IS NULL "
            "AND closed_at IS NULL) OR "
            "(state='STOPPING' AND cleanup_started_at IS NOT NULL AND closed_at IS NULL) OR "
            "(state='CLOSED' AND closed_at IS NOT NULL AND closed_at>=created_at "
            "AND cleanup_started_at IS NOT NULL AND closed_at>=cleanup_started_at "
            "AND (heartbeat_at IS NULL OR closed_at>=heartbeat_at))",
            name="training_execution_times_check",
        ),
        CheckConstraint(
            "(state<>'CLOSED' AND input_cleaned_at IS NULL AND input_cleanup_sha256 IS NULL) OR "
            "(state='CLOSED' AND input_cleaned_at IS NOT NULL "
            "AND input_cleanup_sha256 IS NOT NULL AND octet_length(input_cleanup_sha256)=32 "
            "AND closed_at>=input_cleaned_at)",
            name="training_execution_cleanup_check",
        ),
        CheckConstraint(
            "(checkpoint_device IS NULL AND checkpoint_inode IS NULL) OR "
            "(checkpoint_device IS NOT NULL AND checkpoint_inode IS NOT NULL "
            "AND checkpoint_device ~ '^[0-9]{1,32}$' "
            "AND checkpoint_inode ~ '^[0-9]{1,32}$')",
            name="training_execution_checkpoint_identity_check",
        ),
        CheckConstraint(
            "(state NOT IN ('STOPPING','CLOSED') AND retain_checkpoint IS NULL "
            "AND checkpoint_manifest_sha256 IS NULL AND checkpoint_weights_sha256 IS NULL "
            "AND checkpoint_optimizer_steps IS NULL AND checkpoint_device_type IS NULL) OR "
            "(state IN ('STOPPING','CLOSED') "
            "AND (state<>'CLOSED' OR retain_checkpoint IS NOT NULL) "
            "AND ((checkpoint_manifest_sha256 IS NULL AND checkpoint_weights_sha256 IS NULL "
            "AND checkpoint_optimizer_steps IS NULL AND checkpoint_device_type IS NULL "
            "AND retain_checkpoint=false) OR "
            "(checkpoint_device IS NOT NULL AND checkpoint_manifest_sha256 IS NOT NULL "
            "AND octet_length(checkpoint_manifest_sha256)=32 "
            "AND checkpoint_weights_sha256 IS NOT NULL "
            "AND octet_length(checkpoint_weights_sha256)=32 "
            "AND checkpoint_optimizer_steps>=1 "
            "AND checkpoint_device_type IN ('cpu','cuda'))))",
            name="training_execution_checkpoint_result_check",
        ),
        CheckConstraint(
            "publication_seal_sha256 IS NULL OR "
            "(state IN ('STOPPING','CLOSED') "
            "AND checkpoint_manifest_sha256 IS NOT NULL "
            "AND octet_length(publication_seal_sha256)=32)",
            name="training_execution_publication_seal_check",
        ),
        CheckConstraint(
            "(state NOT IN ('STOPPING','CLOSED') AND closure_kind IS NULL "
            "AND closure_evidence_sha256 IS NULL AND exit_code IS NULL) OR "
            "(state IN ('STOPPING','CLOSED') AND closure_kind IS NOT NULL "
            "AND closure_evidence_sha256 IS NOT NULL AND octet_length(closure_evidence_sha256)=32 "
            "AND ((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL) "
            "OR (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL) "
            "OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL)))",
            name="training_execution_closure_check",
        ),
        Index(
            "uq_training_execution_run_open",
            "run_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
        {"schema": "ml"},
    )
