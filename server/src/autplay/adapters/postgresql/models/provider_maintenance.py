"""One durable maintenance process may own the configured Vault at a time."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ProviderMaintenanceRow(Base):
    __tablename__ = "provider_maintenance"
    execution_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_run_id: Mapped[UUID] = mapped_column()
    provider_execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vault.provider_staging.execution_id")
    )
    claim_id: Mapped[UUID] = mapped_column()
    orphan_claim_id: Mapped[UUID | None] = mapped_column()
    upload_claim_id: Mapped[UUID | None] = mapped_column()
    storage_key: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    singleton_id: Mapped[int] = mapped_column(SmallInteger)
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    child_pid: Mapped[int | None] = mapped_column(BigInteger)
    child_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    closure_kind: Mapped[str | None] = mapped_column(Text)
    closure_evidence_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    exit_code: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        ForeignKeyConstraint(
            ["upload_claim_id", "storage_key"],
            ["vault.upload_cleanup_claim.claim_id", "vault.upload_cleanup_claim.storage_key"],
            name="provider_maintenance_upload_claim_fkey",
        ),
        ForeignKeyConstraint(
            ["orphan_claim_id", "storage_key"],
            ["vault.orphan_object_claim.claim_id", "vault.orphan_object_claim.storage_key"],
            name="provider_maintenance_orphan_claim_fkey",
        ),
        CheckConstraint(
            "(action IN ('CLEANUP','SCRATCH') AND provider_execution_id IS NOT NULL "
            "AND orphan_claim_id IS NULL AND upload_claim_id IS NULL AND storage_key IS NULL) OR "
            "(action IN ('ORPHAN_OBJECT','ORPHAN_MISSING') AND provider_execution_id IS NULL "
            "AND orphan_claim_id IS NOT NULL AND upload_claim_id IS NULL "
            "AND orphan_claim_id=claim_id AND storage_key IS NOT NULL) OR "
            "(action='INVENTORY' AND provider_execution_id IS NULL "
            "AND orphan_claim_id IS NULL AND upload_claim_id IS NULL "
            "AND storage_key IS NULL AND claim_id=execution_id) OR "
            "(action='UPLOAD_CLEANUP' AND provider_execution_id IS NULL "
            "AND orphan_claim_id IS NULL AND upload_claim_id IS NOT NULL "
            "AND upload_claim_id=claim_id AND storage_key IS NOT NULL)",
            name="provider_maintenance_target_check",
        ),
        CheckConstraint(
            "singleton_id=1 AND action IN "
            "('CLEANUP','SCRATCH','ORPHAN_OBJECT','ORPHAN_MISSING','INVENTORY','UPLOAD_CLEANUP') "
            "AND state IN ('PREPARED','RUNNING','CLOSED')",
            name="provider_maintenance_state_check",
        ),
        CheckConstraint(
            "(child_pid IS NULL AND child_identity_sha256 IS NULL AND started_at IS NULL) OR "
            "(child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL "
            "AND octet_length(child_identity_sha256)=32 AND started_at IS NOT NULL "
            "AND started_at>=created_at)",
            name="provider_maintenance_child_check",
        ),
        CheckConstraint(
            "(state='PREPARED' AND started_at IS NULL AND closed_at IS NULL) OR "
            "(state='RUNNING' AND started_at IS NOT NULL AND closed_at IS NULL) OR "
            "(state='CLOSED' AND closed_at IS NOT NULL AND closed_at>=created_at "
            "AND (started_at IS NULL OR closed_at>=started_at))",
            name="provider_maintenance_times_check",
        ),
        CheckConstraint(
            "(state<>'CLOSED' AND closure_kind IS NULL AND closure_evidence_sha256 IS NULL "
            "AND exit_code IS NULL) OR (state='CLOSED' AND closure_kind IS NOT NULL "
            "AND closure_evidence_sha256 IS NOT NULL AND octet_length(closure_evidence_sha256)=32 "
            "AND ((closure_kind='NOT_STARTED' AND child_pid IS NULL AND exit_code IS NULL) "
            "OR (closure_kind='PROCESS_EXIT' AND child_pid IS NOT NULL AND exit_code IS NOT NULL) "
            "OR (closure_kind='SUPERVISOR_EXIT' AND exit_code IS NOT NULL)))",
            name="provider_maintenance_closure_check",
        ),
        Index(
            "uq_provider_maintenance_active",
            "singleton_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
        {"schema": "vault"},
    )
