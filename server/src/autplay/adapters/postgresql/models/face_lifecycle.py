"""Face lifecycle receipts and bounded rollback retention."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    SmallInteger,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP, UUID

from ..base import Base


class FaceTimelineGcReceiptRow(Base):
    __table__ = Table(
        "face_timeline_gc_receipt",
        Base.metadata,
        Column(
            "face_timeline_id",
            UUID(as_uuid=True),
            ForeignKey("ml.face_timeline.face_timeline_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        Column("semantic_key_sha256", BYTEA(), nullable=False),
        Column("result_sha256", BYTEA(), nullable=False),
        Column("prior_generation", BigInteger(), primary_key=True),
        Column("reason", Text(), nullable=False),
        Column("removed_bytes", Integer(), nullable=False),
        Column("purged_at", TIMESTAMP(timezone=True), nullable=False),
        CheckConstraint(
            "octet_length(semantic_key_sha256)=32",
            name="face_timeline_gc_receipt_semantic_key_sha256_check",
        ),
        CheckConstraint(
            "octet_length(result_sha256)=32", name="face_timeline_gc_receipt_result_sha256_check"
        ),
        CheckConstraint(
            "prior_generation BETWEEN 1 AND 9007199254740986 AND prior_generation%4 IN (1,2)",
            name="face_timeline_gc_receipt_prior_generation_check",
        ),
        CheckConstraint(
            "reason IN ('LAST_REFERENCE','RETENTION_EXPIRED')",
            name="face_timeline_gc_receipt_reason_check",
        ),
        CheckConstraint(
            "removed_bytes BETWEEN 1 AND 1048576",
            name="face_timeline_gc_receipt_removed_bytes_check",
        ),
        schema="ml",
    )


class FaceRollbackRetentionRow(Base):
    __table__ = Table(
        "face_rollback_retention",
        Base.metadata,
        Column(
            "predecessor_activation_id",
            UUID(as_uuid=True),
            ForeignKey(
                "ml.face_timeline_activation.face_timeline_activation_id", ondelete="RESTRICT"
            ),
            primary_key=True,
        ),
        Column(
            "successor_activation_id",
            UUID(as_uuid=True),
            ForeignKey(
                "ml.face_timeline_activation.face_timeline_activation_id", ondelete="RESTRICT"
            ),
            nullable=False,
            unique=True,
        ),
        Column("retain_until", TIMESTAMP(timezone=True), nullable=False),
        schema="ml",
    )


class FaceRestoreQuarantineRow(Base):
    __table__ = Table(
        "face_restore_quarantine",
        Base.metadata,
        Column("singleton", SmallInteger(), primary_key=True),
        Column("generation", BigInteger(), nullable=False),
        Column("quarantined_at", TIMESTAMP(timezone=True)),
        Column("restore_id", UUID(as_uuid=True)),
        Column("released_at", TIMESTAMP(timezone=True)),
        CheckConstraint(
            "released_at IS NULL OR (generation>0 AND released_at>=quarantined_at)",
            name="ck_face_restore_release_time",
        ),
        CheckConstraint("singleton=1", name="face_restore_quarantine_singleton_check"),
        CheckConstraint(
            "generation BETWEEN 0 AND 9007199254740990",
            name="face_restore_quarantine_generation_check",
        ),
        CheckConstraint(
            "(generation=0 AND quarantined_at IS NULL AND restore_id IS NULL) OR "
            "(generation>0 AND quarantined_at IS NOT NULL AND restore_id IS NOT NULL)",
            name="ck_face_restore_quarantine_state",
        ),
        schema="ml",
    )


class FaceTimelineRegenerationReceiptRow(Base):
    __table__ = Table(
        "face_timeline_regeneration_receipt",
        Base.metadata,
        Column(
            "face_timeline_id",
            UUID(as_uuid=True),
            ForeignKey("ml.face_timeline.face_timeline_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        Column("restored_generation", BigInteger(), primary_key=True),
        Column(
            "face_work_id",
            UUID(as_uuid=True),
            ForeignKey("ml.face_analysis_work.face_work_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        Column("claim_token", UUID(as_uuid=True), nullable=False),
        Column("result_sha256", BYTEA(), nullable=False),
        Column("previous_published_at", TIMESTAMP(timezone=True)),
        Column("previous_purged_at", TIMESTAMP(timezone=True), nullable=False),
        Column("restored_at", TIMESTAMP(timezone=True), nullable=False),
        CheckConstraint(
            "octet_length(result_sha256)=32",
            name="face_timeline_regeneration_receipt_result_sha256_check",
        ),
        CheckConstraint(
            "restored_generation BETWEEN 5 AND 9007199254740985 AND restored_generation%4=1",
            name="face_timeline_regeneration_receipt_restored_generation_check",
        ),
        schema="ml",
    )
