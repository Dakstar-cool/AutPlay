# ruff: noqa: E501
"""Typed SQLAlchemy mappings for the audit PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .types import JsonValue


class CatalogChangeSetRow(Base):
    """Persistence row for ``audit.catalog_change_set``."""

    __tablename__ = "catalog_change_set"

    change_set_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    operation_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    actor_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    reversible_until: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'PLANNED'"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("change_set_id", name="catalog_change_set_pkey"),
        ForeignKeyConstraint(
            ["actor_user_id"],
            ["account.user_account.user_id"],
            name="catalog_change_set_actor_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(reason) BETWEEN 1 AND 4000",
            name="catalog_change_set_reason_check",
        ),
        CheckConstraint(
            "operation_type IN ('MERGE', 'SPLIT', 'REASSIGN', 'UNDO')",
            name="ck_catalog_change_set_operation",
        ),
        CheckConstraint(
            "actor_type IN ('SYSTEM', 'USER', 'ADMIN')",
            name="ck_catalog_change_set_actor",
        ),
        CheckConstraint(
            "(actor_type = 'SYSTEM') OR (actor_type IN ('USER', 'ADMIN') AND actor_user_id IS NOT NULL)",
            name="ck_catalog_change_set_actor_user",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_catalog_change_set_confidence",
        ),
        CheckConstraint(
            "status IN ('PLANNED', 'APPLIED', 'REVERTED', 'FAILED')",
            name="ck_catalog_change_set_status",
        ),
        {"schema": "audit"},
    )


class CatalogChangeItemRow(Base):
    """Persistence row for ``audit.catalog_change_item``."""

    __tablename__ = "catalog_change_item"

    change_item_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    change_set_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    entity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    from_snapshot: Mapped[JsonValue | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    to_snapshot: Mapped[JsonValue | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    sequence_no: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("change_item_id", name="catalog_change_item_pkey"),
        ForeignKeyConstraint(
            ["change_set_id"],
            ["audit.catalog_change_set.change_set_id"],
            name="catalog_change_item_change_set_id_fkey",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "length(entity_type) BETWEEN 1 AND 100",
            name="catalog_change_item_entity_type_check",
        ),
        CheckConstraint(
            "length(action) BETWEEN 1 AND 100",
            name="catalog_change_item_action_check",
        ),
        CheckConstraint(
            "sequence_no >= 1",
            name="catalog_change_item_sequence_no_check",
        ),
        UniqueConstraint("change_set_id", "sequence_no", name="uq_catalog_change_item_sequence"),
        {"schema": "audit"},
    )


class AuditEventRow(Base):
    """Persistence row for ``audit.audit_event``."""

    __tablename__ = "audit_event"

    audit_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    actor_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    actor_device_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    target_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    request_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    reason_code: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    metadata_sanitized: Mapped[JsonValue] = mapped_column(
        JSONB(),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("audit_event_id", name="audit_event_pkey"),
        ForeignKeyConstraint(
            ["actor_user_id"],
            ["account.user_account.user_id"],
            name="audit_event_actor_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["actor_device_id"],
            ["account.device.device_id"],
            name="audit_event_actor_device_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(action) BETWEEN 1 AND 200",
            name="audit_event_action_check",
        ),
        CheckConstraint(
            "length(target_type) BETWEEN 1 AND 100",
            name="audit_event_target_type_check",
        ),
        CheckConstraint(
            "actor_type IN ('SYSTEM', 'USER', 'ADMIN', 'WORKER')",
            name="ck_audit_event_actor_type",
        ),
        {"schema": "audit"},
    )


Index(
    "ix_audit_event_occurred_at",
    AuditEventRow.occurred_at.desc(),
)

Index(
    "ix_audit_event_target",
    AuditEventRow.target_type,
    AuditEventRow.target_id,
    AuditEventRow.occurred_at.desc(),
)


__all__ = (
    "AuditEventRow",
    "CatalogChangeItemRow",
    "CatalogChangeSetRow",
)
