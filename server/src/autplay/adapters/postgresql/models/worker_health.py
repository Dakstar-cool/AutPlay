"""Latest CPU process observation, bounded to one row per server."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Float, ForeignKey
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WorkerHealthRow(Base):
    __tablename__ = "worker_health"

    server_instance_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("account.server_instance.server_instance_id", ondelete="CASCADE"),
        primary_key=True,
    )
    process_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    running: Mapped[bool] = mapped_column(Boolean, nullable=False)
    busy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    memory_limit_bytes: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        CheckConstraint(
            "cpu_percent IS NULL OR cpu_percent BETWEEN 0 AND 100", name="worker_cpu_range"
        ),
        CheckConstraint("memory_bytes IS NULL OR memory_bytes >= 0", name="worker_memory_range"),
        CheckConstraint(
            "memory_limit_bytes IS NULL OR memory_limit_bytes > 0", name="worker_memory_limit_range"
        ),
        {"schema": "jobs"},
    )
