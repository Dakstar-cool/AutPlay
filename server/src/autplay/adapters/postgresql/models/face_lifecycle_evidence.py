"""Minimal SQL projections of independent Face lifecycle evidence."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP, UUID

from ..base import Base


class FaceLifecycleLedgerBindingRow(Base):
    __table__ = Table(
        "face_lifecycle_ledger_binding",
        Base.metadata,
        Column("singleton", SmallInteger(), primary_key=True),
        Column("ledger_id", UUID(as_uuid=True), nullable=False, unique=True),
        Column("server_profile_id", UUID(as_uuid=True), nullable=False),
        Column("coverage_started_at", TIMESTAMP(timezone=True), nullable=False),
        Column(
            "bound_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=text("clock_timestamp()"),
        ),
        CheckConstraint("singleton=1", name="face_lifecycle_ledger_binding_singleton_check"),
        schema="ml",
    )


class FaceLifecycleFactRow(Base):
    __table__ = Table(
        "face_lifecycle_fact",
        Base.metadata,
        Column("fact_id", UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")),
        Column("transaction_id", BigInteger(), nullable=False),
        Column("kind", Text(), nullable=False),
        Column("entity_sha256", BYTEA(), nullable=False),
        Column("generation", BigInteger(), nullable=False),
        Column("result_sha256", BYTEA()),
        Column(
            "occurred_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=text("clock_timestamp()"),
        ),
        CheckConstraint(
            "kind IN ('REFERENCE_REMOVED','SPONSOR_RETIRED',"
            "'PROJECTION_TOMBSTONED','TIMELINE_PURGED','REFERENCE_RESTORED')",
            name="face_lifecycle_fact_kind_check",
        ),
        CheckConstraint(
            "octet_length(entity_sha256)=32", name="face_lifecycle_fact_entity_sha256_check"
        ),
        CheckConstraint(
            "generation BETWEEN 1 AND 9007199254740990", name="face_lifecycle_fact_generation_check"
        ),
        CheckConstraint(
            "result_sha256 IS NULL OR octet_length(result_sha256)=32",
            name="face_lifecycle_fact_result_sha256_check",
        ),
        CheckConstraint(
            "(kind IN ('TIMELINE_PURGED','REFERENCE_RESTORED'))=(result_sha256 IS NOT NULL)",
            name="ck_face_fact_result",
        ),
        UniqueConstraint("kind", "entity_sha256", "generation", name="uq_face_fact_entity"),
        Index("ix_face_lifecycle_fact_transaction", "transaction_id", "fact_id"),
        schema="ml",
    )


class FaceLifecycleFactReceiptRow(Base):
    __table__ = Table(
        "face_lifecycle_fact_receipt",
        Base.metadata,
        Column(
            "fact_id",
            UUID(as_uuid=True),
            ForeignKey("ml.face_lifecycle_fact.fact_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        Column("ledger_id", UUID(as_uuid=True), nullable=False),
        Column("ledger_sequence", BigInteger(), nullable=False),
        Column("ledger_digest", BYTEA(), nullable=False),
        Column("admission_mac", BYTEA(), nullable=False),
        CheckConstraint(
            "octet_length(admission_mac)=32", name="face_lifecycle_fact_receipt_admission_mac_check"
        ),
        CheckConstraint(
            "ledger_sequence>0", name="face_lifecycle_fact_receipt_ledger_sequence_check"
        ),
        CheckConstraint(
            "octet_length(ledger_digest)=32", name="face_lifecycle_fact_receipt_ledger_digest_check"
        ),
        UniqueConstraint("ledger_id", "ledger_sequence", name="uq_face_fact_ledger_sequence"),
        schema="ml",
    )


class FaceRestoreReconciliationRow(Base):
    __table__ = Table(
        "face_restore_reconciliation",
        Base.metadata,
        Column("restore_id", UUID(as_uuid=True), primary_key=True),
        Column("quarantine_generation", BigInteger(), nullable=False, unique=True),
        Column("ledger_id", UUID(as_uuid=True), nullable=False),
        Column("ledger_sequence", BigInteger(), nullable=False),
        Column("ledger_digest", BYTEA(), nullable=False),
        Column("privacy_heads_sha256", BYTEA(), nullable=False),
        Column("training_heads_sha256", BYTEA(), nullable=False),
        Column("serving_head_sha256", BYTEA(), nullable=False),
        Column(
            "reconciled_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=text("clock_timestamp()"),
        ),
        CheckConstraint(
            "ledger_sequence>=0", name="face_restore_reconciliation_ledger_sequence_check"
        ),
        *(
            CheckConstraint(
                f"octet_length({name})=32", name=f"face_restore_reconciliation_{name}_check"
            )
            for name in (
                "ledger_digest",
                "privacy_heads_sha256",
                "training_heads_sha256",
                "serving_head_sha256",
            )
        ),
        schema="ml",
    )
