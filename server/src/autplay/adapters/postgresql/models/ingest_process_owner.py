"""Exact ingest startup registration; privacy cascades follow its execution parent."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class IngestProcessOwnerRow(Base):
    __tablename__ = "ingest_process_owner"
    execution_id: Mapped[UUID] = mapped_column(primary_key=True)
    work_execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vault.ingest_execution.execution_id", ondelete="CASCADE")
    )
    cleanup_execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vault.ingest_cleanup_execution.execution_id", ondelete="CASCADE")
    )
    owner_run_id: Mapped[UUID] = mapped_column()
    host_sha256: Mapped[bytes] = mapped_column(BYTEA)
    owner_pid: Mapped[int] = mapped_column(BigInteger)
    owner_created: Mapped[int] = mapped_column(BigInteger)
    tree_name: Mapped[str] = mapped_column(Text, unique=True)
    platform: Mapped[str] = mapped_column(Text, server_default=text("'WINDOWS'"))
    tree_device: Mapped[int | None] = mapped_column(BigInteger)
    tree_inode: Mapped[int | None] = mapped_column(BigInteger)
    child_pid: Mapped[int | None] = mapped_column(BigInteger)
    child_created: Mapped[int | None] = mapped_column(BigInteger)
    child_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    checked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    inspection_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    exit_receipt_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    exit_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(work_execution_id,cleanup_execution_id)=1 "
            "AND execution_id=coalesce(work_execution_id,cleanup_execution_id)",
            name="ingest_process_owner_execution_check",
        ),
        CheckConstraint(
            "octet_length(host_sha256)=32 AND owner_pid BETWEEN 1 AND 4294967295 "
            "AND owner_created>0 AND ((platform='WINDOWS' AND "
            "tree_name='Local\\AutPlay-Ingest-'||replace(execution_id::text,'-','')) OR "
            "(platform='LINUX' AND "
            "tree_name='autplay-ingest-'||replace(execution_id::text,'-','')))",
            name="ingest_process_owner_identity_check",
        ),
        CheckConstraint(
            "(platform='WINDOWS' AND tree_device IS NULL AND tree_inode IS NULL) OR "
            "(platform='LINUX' AND ((child_pid IS NULL AND tree_device IS NULL "
            "AND tree_inode IS NULL) OR (child_pid IS NOT NULL AND tree_device IS NOT NULL "
            "AND tree_device>0 AND tree_inode IS NOT NULL AND tree_inode>0)))",
            name="ingest_process_owner_tree_check",
        ),
        CheckConstraint(
            "(child_pid IS NULL AND child_created IS NULL AND child_identity_sha256 IS NULL) OR "
            "(child_pid IS NOT NULL AND child_created IS NOT NULL "
            "AND child_identity_sha256 IS NOT NULL "
            "AND child_pid BETWEEN 1 AND 4294967295 AND child_created>0 "
            "AND octet_length(child_identity_sha256)=32)",
            name="ingest_process_owner_child_check",
        ),
        CheckConstraint(
            "inspection_count>=0 AND (checked_at IS NULL)=(inspection_count=0)",
            name="ingest_process_owner_schedule_check",
        ),
        CheckConstraint(
            "(closed_at IS NULL AND exit_receipt_sha256 IS NULL AND exit_receipt IS NULL) OR "
            "(closed_at IS NOT NULL AND exit_receipt_sha256 IS NOT NULL "
            "AND octet_length(exit_receipt_sha256)=32 AND (exit_receipt IS NULL OR "
            "(jsonb_typeof(exit_receipt)='object' AND octet_length(exit_receipt::text)<=4096)))",
            name="ingest_process_owner_exit_check",
        ),
        Index(
            "ix_ingest_process_owner_pending",
            "host_sha256",
            text("checked_at NULLS FIRST"),
            "execution_id",
            postgresql_where=text("closed_at IS NULL"),
        ),
        {"schema": "vault"},
    )
