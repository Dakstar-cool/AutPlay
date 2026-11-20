"""Global release-scan cadence and immutable administrator change history."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Integer, PrimaryKeyConstraint, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ScanScheduleRow(Base):
    __tablename__ = "scan_schedule"

    singleton_id: Mapped[int] = mapped_column(Integer(), nullable=False)
    interval_hours: Mapped[int] = mapped_column(Integer(), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    changed_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("singleton_id", name="scan_schedule_pkey"),
        CheckConstraint("singleton_id = 1", name="ck_scan_schedule_singleton"),
        CheckConstraint("interval_hours BETWEEN 1 AND 168", name="ck_scan_schedule_interval"),
        CheckConstraint("revision >= 1", name="ck_scan_schedule_revision"),
        {"schema": "discovery"},
    )


class ScanScheduleRevisionRow(Base):
    __tablename__ = "scan_schedule_revision"

    revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    interval_hours: Mapped[int] = mapped_column(Integer(), nullable=False)
    previous_interval_hours: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    changed_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    operation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, unique=True
    )
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False, server_default=text("'ADMIN'"))

    __table_args__ = (
        PrimaryKeyConstraint("revision", name="scan_schedule_revision_pkey"),
        CheckConstraint(
            "interval_hours BETWEEN 1 AND 168", name="ck_scan_schedule_revision_interval"
        ),
        CheckConstraint(
            "previous_interval_hours IS NULL OR previous_interval_hours BETWEEN 1 AND 168",
            name="ck_scan_schedule_revision_previous",
        ),
        CheckConstraint("revision >= 1", name="ck_scan_schedule_revision_number"),
        CheckConstraint("reason IN ('DEFAULT', 'ADMIN')", name="ck_scan_schedule_revision_reason"),
        CheckConstraint(
            "(reason = 'DEFAULT' AND operation_id IS NULL AND changed_by IS NULL) OR "
            "(reason = 'ADMIN' AND operation_id IS NOT NULL AND changed_by IS NOT NULL)",
            name="ck_scan_schedule_revision_actor",
        ),
        {"schema": "discovery"},
    )


__all__ = ("ScanScheduleRevisionRow", "ScanScheduleRow")
