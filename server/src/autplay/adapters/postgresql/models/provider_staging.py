"""Durable provider file ownership outlives short-lived execution accounting."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ProviderStagingRow(Base):
    __tablename__ = "provider_staging"
    # Historical execution identity, deliberately not an FK to expiring accounting.
    execution_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("account.user_account.user_id"))
    resource_type: Mapped[str] = mapped_column(Text)
    acquisition_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.job.job_id"))
    job_worker_id: Mapped[str] = mapped_column(Text)
    job_attempt: Mapped[int] = mapped_column(BigInteger)
    operation_id: Mapped[UUID] = mapped_column()
    activation_id: Mapped[UUID] = mapped_column()
    generation: Mapped[int] = mapped_column(BigInteger)
    permit_id: Mapped[UUID] = mapped_column()
    owner_run_id: Mapped[UUID] = mapped_column()
    staging_key: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    closure_kind: Mapped[str | None] = mapped_column(Text)
    closure_evidence_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    exit_code: Mapped[int | None] = mapped_column(BigInteger)
    child_pid: Mapped[int | None] = mapped_column(BigInteger)
    child_identity_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    sealed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    upload_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vault.upload_session.upload_session_id")
    )
    handed_off_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    cleanup_reason: Mapped[str | None] = mapped_column(Text)
    cleanup_claim_id: Mapped[UUID | None] = mapped_column()
    cleanup_claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    cleaned_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    scratch_claim_id: Mapped[UUID | None] = mapped_column()
    scratch_claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    scratch_retired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        UniqueConstraint("staging_key", name="provider_staging_key_key"),
        UniqueConstraint("upload_session_id", name="provider_staging_upload_key"),
        CheckConstraint(
            "resource_type IN ('INTERNET_ACQUISITION','DISCOVERY_ACQUISITION') "
            "AND job_attempt>=1 AND generation>=1 AND length(job_worker_id) BETWEEN 1 AND 200",
            name="provider_staging_identity_check",
        ),
        CheckConstraint(
            "staging_key='provider-' || replace(execution_id::text,'-','')",
            name="provider_staging_key_check",
        ),
        CheckConstraint(
            "state IN ('OWNED','EXITED','SEALED','HANDED_OFF','CLEANUP_CLAIMED','CLEANED')",
            name="provider_staging_state_check",
        ),
        CheckConstraint(
            "(state='OWNED' AND closed_at IS NULL AND closure_kind IS NULL "
            "AND closure_evidence_sha256 IS NULL AND exit_code IS NULL "
            "AND child_pid IS NULL AND child_identity_sha256 IS NULL) OR "
            "(state<>'OWNED' AND closed_at IS NOT NULL AND closure_kind IS NOT NULL "
            "AND closure_evidence_sha256 IS NOT NULL AND octet_length(closure_evidence_sha256)=32 "
            "AND ((closure_kind='NOT_STARTED' AND exit_code IS NULL AND child_pid IS NULL "
            "AND child_identity_sha256 IS NULL) OR "
            "(closure_kind IN ('PROCESS_EXIT','SUPERVISOR_EXIT') AND exit_code IS NOT NULL "
            "AND ((child_pid IS NOT NULL AND child_pid>0 AND child_identity_sha256 IS NOT NULL "
            "AND octet_length(child_identity_sha256)=32) OR "
            "(closure_kind='SUPERVISOR_EXIT' AND child_pid IS NULL "
            "AND child_identity_sha256 IS NULL)))))",
            name="provider_staging_closure_check",
        ),
        CheckConstraint(
            "(sealed_at IS NULL AND byte_size IS NULL AND sha256 IS NULL "
            "AND state IN ('OWNED','EXITED','CLEANUP_CLAIMED','CLEANED')) OR "
            "(sealed_at IS NOT NULL AND byte_size IS NOT NULL AND byte_size>0 "
            "AND sha256 IS NOT NULL AND octet_length(sha256)=32 AND exit_code=0 "
            "AND closure_kind IN ('PROCESS_EXIT','SUPERVISOR_EXIT') "
            "AND state IN ('SEALED','HANDED_OFF','CLEANUP_CLAIMED','CLEANED'))",
            name="provider_staging_sealed_check",
        ),
        CheckConstraint(
            "(state='HANDED_OFF' AND upload_session_id IS NOT NULL AND handed_off_at IS NOT NULL) "
            "OR (state<>'HANDED_OFF' AND upload_session_id IS NULL AND handed_off_at IS NULL)",
            name="provider_staging_handoff_check",
        ),
        CheckConstraint(
            "(state IN ('CLEANUP_CLAIMED','CLEANED') AND cleanup_claim_id IS NOT NULL "
            "AND cleanup_claimed_at IS NOT NULL AND cleanup_reason IS NOT NULL "
            "AND cleanup_reason IN ('CANCELLED','FAILED','AUTHORITY_REVOKED','SUPERSEDED')) OR "
            "(state NOT IN ('CLEANUP_CLAIMED','CLEANED') AND cleanup_claim_id IS NULL "
            "AND cleanup_claimed_at IS NULL AND cleanup_reason IS NULL)",
            name="provider_staging_cleanup_check",
        ),
        CheckConstraint(
            "(state='CLEANED')=(cleaned_at IS NOT NULL) AND updated_at>=created_at "
            "AND (closed_at IS NULL OR closed_at>=created_at) "
            "AND (sealed_at IS NULL OR sealed_at>=closed_at) "
            "AND (handed_off_at IS NULL OR handed_off_at>=sealed_at) "
            "AND (cleanup_claimed_at IS NULL OR cleanup_claimed_at>=closed_at) "
            "AND (cleaned_at IS NULL OR cleaned_at>=cleanup_claimed_at)",
            name="provider_staging_timestamps_check",
        ),
        Index("ix_provider_staging_target", "resource_type", "acquisition_id", "created_at"),
        Index("ix_provider_staging_cleanup", "state", "updated_at"),
        CheckConstraint(
            "(scratch_claim_id IS NULL AND scratch_claimed_at IS NULL "
            "AND scratch_retired_at IS NULL) OR (state='HANDED_OFF' "
            "AND scratch_claim_id IS NOT NULL AND scratch_claimed_at IS NOT NULL "
            "AND scratch_claimed_at>=handed_off_at AND (scratch_retired_at IS NULL "
            "OR scratch_retired_at>=scratch_claimed_at))",
            name="provider_staging_scratch_check",
        ),
        Index(
            "ix_provider_staging_scratch_pending",
            "execution_id",
            postgresql_where=text("state='HANDED_OFF' AND scratch_retired_at IS NULL"),
        ),
        {"schema": "vault"},
    )
