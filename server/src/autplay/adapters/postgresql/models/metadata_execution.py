"""Retain metadata byte ownership and globally paced provider requests."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class MetadataExecutionRow(Base):
    __tablename__ = "metadata_execution"
    execution_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_run_id: Mapped[UUID] = mapped_column()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    user_track_ref_id: Mapped[UUID] = mapped_column(
        ForeignKey("library.user_track_ref.user_track_ref_id")
    )
    generation: Mapped[int] = mapped_column(BigInteger)
    authority_generation: Mapped[int] = mapped_column(BigInteger)
    audio: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.job.job_id"))
    worker_id: Mapped[str] = mapped_column(Text)
    attempt_no: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    io_deadline_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    child_pid: Mapped[int | None] = mapped_column(BigInteger)
    child_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    closure_kind: Mapped[str | None] = mapped_column(Text)
    closure_evidence_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    exit_code: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        CheckConstraint(
            "generation>0 AND authority_generation>0 "
            "AND length(worker_id) BETWEEN 1 AND 300 AND attempt_no>0 "
            "AND state IN ('PREPARED','RUNNING','CLOSED')",
            name="metadata_execution_target_check",
        ),
        CheckConstraint(
            "(child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL "
            "AND heartbeat_at IS NULL AND io_deadline_at IS NULL) OR "
            "(child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL "
            "AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL "
            "AND started_at>=created_at AND heartbeat_at IS NOT NULL AND heartbeat_at>=started_at "
            "AND io_deadline_at IS NOT NULL AND io_deadline_at>heartbeat_at "
            "AND io_deadline_at<=heartbeat_at+interval '5 seconds')",
            name="metadata_execution_child_check",
        ),
        CheckConstraint(
            "(state='PREPARED' AND started_at IS NULL AND closed_at IS NULL) OR "
            "(state='RUNNING' AND started_at IS NOT NULL AND closed_at IS NULL) OR "
            "(state='CLOSED' AND closed_at IS NOT NULL AND closed_at>=created_at "
            "AND (heartbeat_at IS NULL OR closed_at>=heartbeat_at))",
            name="metadata_execution_times_check",
        ),
        CheckConstraint(
            "(state<>'CLOSED' AND closure_kind IS NULL AND closure_evidence_sha256 IS NULL "
            "AND exit_code IS NULL) OR (state='CLOSED' AND closure_kind IS NOT NULL "
            "AND closure_evidence_sha256 IS NOT NULL AND octet_length(closure_evidence_sha256)=32 "
            "AND ((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL) "
            "OR (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL) "
            "OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL)))",
            name="metadata_execution_closure_check",
        ),
        CheckConstraint(
            "audio IS NULL OR (jsonb_typeof(audio)='object' AND octet_length(audio::text)<=2048)",
            name="metadata_execution_audio_check",
        ),
        Index(
            "uq_metadata_execution_ref",
            "user_track_ref_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
        {"schema": "library"},
    )


class MetadataProviderGateRow(Base):
    __tablename__ = "metadata_provider_gate"
    singleton_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("library.metadata_execution.execution_id")
    )
    request_id: Mapped[UUID | None] = mapped_column()
    next_request_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        CheckConstraint("singleton_id=1", name="metadata_provider_gate_singleton_check"),
        CheckConstraint(
            "(execution_id IS NULL)=(request_id IS NULL)",
            name="metadata_provider_gate_owner_check",
        ),
        {"schema": "library"},
    )
