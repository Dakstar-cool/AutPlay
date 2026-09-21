"""One independently measured global internal byte-work budget."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class InternalIoPolicyRow(Base):
    __tablename__ = "internal_io_policy"
    workload_version: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    singleton_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    active_limit: Mapped[int | None] = mapped_column(Integer)
    measured_ceiling: Mapped[int | None] = mapped_column(Integer)
    server_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account.server_instance.server_instance_id")
    )
    identity_epoch: Mapped[int | None] = mapped_column(BigInteger)
    environment_sha256: Mapped[str | None] = mapped_column(Text)
    workload_sha256: Mapped[str | None] = mapped_column(Text)
    report_sha256: Mapped[str | None] = mapped_column(Text)
    playback_ceiling: Mapped[int | None] = mapped_column(Integer)
    transfer_ceiling: Mapped[int | None] = mapped_column(Integer)
    initialized_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        CheckConstraint("workload_version IN (1,2,3)", name="internal_io_workload_version_check"),
        CheckConstraint("singleton_id=1 AND revision>=1", name="internal_io_policy_identity_check"),
        CheckConstraint(
            "(active_limit IS NULL AND measured_ceiling IS NULL AND server_instance_id IS NULL "
            "AND identity_epoch IS NULL AND environment_sha256 IS NULL AND workload_sha256 IS NULL "
            "AND report_sha256 IS NULL AND playback_ceiling IS NULL AND transfer_ceiling IS NULL "
            "AND initialized_at IS NULL) OR "
            "(active_limit IS NOT NULL AND measured_ceiling IS NOT NULL "
            "AND server_instance_id IS NOT NULL AND identity_epoch IS NOT NULL "
            "AND environment_sha256 IS NOT NULL AND workload_sha256 IS NOT NULL "
            "AND report_sha256 IS NOT NULL AND playback_ceiling IS NOT NULL "
            "AND transfer_ceiling IS NOT NULL AND initialized_at IS NOT NULL "
            "AND active_limit BETWEEN 1 AND measured_ceiling "
            "AND measured_ceiling BETWEEN 1 AND 1000000 "
            "AND identity_epoch>=1 AND environment_sha256 ~ '^[a-f0-9]{64}$' "
            "AND workload_sha256 ~ '^[a-f0-9]{64}$' AND report_sha256 ~ '^[a-f0-9]{64}$' "
            "AND playback_ceiling BETWEEN 1 AND 1000000 AND transfer_ceiling BETWEEN 1 AND 1000000 "
            "AND updated_at>=initialized_at)",
            name="internal_io_policy_measurement_check",
        ),
        {"schema": "account"},
    )
