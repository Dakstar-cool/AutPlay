"""Retained ownership of a single authenticated public catalog network request."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, Text
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class CatalogExecutionRow(Base):
    __tablename__ = "catalog_execution"
    execution_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_run_id: Mapped[UUID] = mapped_column()
    user_id: Mapped[UUID] = mapped_column()
    device_id: Mapped[UUID] = mapped_column()
    session_id: Mapped[UUID] = mapped_column()
    authority_generation: Mapped[int] = mapped_column(BigInteger)
    request_id: Mapped[UUID] = mapped_column()
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
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
        ForeignKeyConstraint(
            ["user_id", "device_id", "session_id"],
            [
                "account.user_session.user_id",
                "account.user_session.device_id",
                "account.user_session.session_id",
            ],
            name="catalog_execution_actor_fkey",
        ),
        CheckConstraint(
            "authority_generation>0 AND state IN ('PREPARED','RUNNING','CLOSED') "
            "AND deadline_at>created_at AND deadline_at<=created_at+interval '30 seconds'",
            name="catalog_execution_target_check",
        ),
        CheckConstraint(
            "(child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL "
            "AND heartbeat_at IS NULL AND io_deadline_at IS NULL) OR "
            "(child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL "
            "AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL "
            "AND started_at>=created_at AND heartbeat_at IS NOT NULL AND heartbeat_at>=started_at "
            "AND io_deadline_at IS NOT NULL AND io_deadline_at>heartbeat_at "
            "AND io_deadline_at<=heartbeat_at+interval '5 seconds' "
            "AND io_deadline_at<=deadline_at)",
            name="catalog_execution_child_check",
        ),
        CheckConstraint(
            "(state='PREPARED' AND started_at IS NULL AND closed_at IS NULL) OR "
            "(state='RUNNING' AND started_at IS NOT NULL AND closed_at IS NULL) OR "
            "(state='CLOSED' AND closed_at IS NOT NULL AND closed_at>=created_at "
            "AND (heartbeat_at IS NULL OR closed_at>=heartbeat_at))",
            name="catalog_execution_times_check",
        ),
        CheckConstraint(
            "(state<>'CLOSED' AND closure_kind IS NULL AND closure_evidence_sha256 IS NULL "
            "AND exit_code IS NULL) OR (state='CLOSED' AND closure_kind IS NOT NULL "
            "AND closure_evidence_sha256 IS NOT NULL AND octet_length(closure_evidence_sha256)=32 "
            "AND ((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL) "
            "OR (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL) "
            "OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL)))",
            name="catalog_execution_closure_check",
        ),
        {"schema": "library"},
    )
