"""Retained managed native metadata, deliberately independent of source/account FKs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class NativeExportRow(Base):
    __tablename__ = "native_export"
    export_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    allocation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    allocation_sha256: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    state: Mapped[str] = mapped_column(Text)
    inventory: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    manifest_sha256: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("kind IN ('PACKET','AGGREGATE','DATASET')", name="ck_native_export_kind"),
        CheckConstraint(
            "state IN ('ALLOCATED','READY','INVALIDATED','DELETED')", name="ck_native_export_state"
        ),
        CheckConstraint(
            "allocation_sha256 ~ '^[0-9a-f]{64}$' AND "
            "(manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_native_export_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(allocation)='object' AND octet_length(allocation::text)<=524288 "
            "AND jsonb_typeof(inventory)='array' AND jsonb_array_length(inventory)"
            "<=CASE WHEN kind='DATASET' THEN 63 ELSE 11 END",
            name="ck_native_export_metadata",
        ),
        CheckConstraint(
            "state<>'READY' OR (manifest_sha256 IS NOT NULL AND jsonb_array_length(inventory)>0)",
            name="ck_native_export_ready",
        ),
        Index("ix_native_export_work", "state", "expires_at", "export_id", "generation"),
        {"schema": "ml"},
    )


class NativeExportOwnerRow(Base):
    __tablename__ = "native_export_owner"
    export_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_tag: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(
            ["export_id", "generation"],
            ["ml.native_export.export_id", "ml.native_export.generation"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("owner_tag ~ '^[0-9a-f]{64}$'", name="ck_native_export_owner_tag"),
        Index("ix_native_export_owner_lookup", "user_id", "export_id", "generation"),
        {"schema": "ml"},
    )


class NativeExportDependencyRow(Base):
    __tablename__ = "native_export_dependency"
    export_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[UUID] = mapped_column(primary_key=True)
    packet_id: Mapped[UUID] = mapped_column(primary_key=True)
    packet_generation: Mapped[UUID] = mapped_column(primary_key=True)
    manifest_sha256: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(
            ["export_id", "generation"],
            ["ml.native_export.export_id", "ml.native_export.generation"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["packet_id", "packet_generation"],
            ["ml.native_export.export_id", "ml.native_export.generation"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_native_export_dependency_hash"
        ),
        CheckConstraint("export_id<>packet_id", name="ck_native_export_dependency_self"),
        Index("ix_native_export_dependency_parent", "packet_id", "packet_generation"),
        {"schema": "ml"},
    )
