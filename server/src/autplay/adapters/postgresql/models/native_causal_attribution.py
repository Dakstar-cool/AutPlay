"""Private immutable impression associations and bounded lifecycle receipts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


def _check(field: str, expression: str, *, receipt: bool = False) -> CheckConstraint:
    prefix = "native_attr_purge" if receipt else "native_attr"
    return CheckConstraint(expression, name=f"{prefix}_{field}_check")


class RecommendationAttributionAssociationRow(Base):
    """Only an exact committed v2 P11 item can authorize this private edge."""

    __tablename__ = "recommendation_attribution_association"

    association_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    recommendation_request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text(), nullable=False)
    served_rank: Mapped[int | None] = mapped_column(Integer())
    recording_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    serving_provenance: Mapped[str] = mapped_column(Text(), nullable=False)
    request_surface: Mapped[str] = mapped_column(Text(), nullable=False)
    presentation_source: Mapped[str | None] = mapped_column(Text())
    presentation_surface: Mapped[str | None] = mapped_column(Text())
    wire_attribution_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    presentation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    interaction_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    listening_event_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    attribution_expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    original_request_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    acceptance_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    decision_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    served_item_sha256: Mapped[bytes | None] = mapped_column(BYTEA())
    association_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    impression_association_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    impression_association_kind: Mapped[str | None] = mapped_column(Text())
    offline_pack_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    pack_served_item_count: Mapped[int | None] = mapped_column(Integer())
    pack_served_items_sha256: Mapped[bytes | None] = mapped_column(BYTEA())
    pack_payload_sha256: Mapped[bytes | None] = mapped_column(BYTEA())

    __table_args__ = (
        _check("kind", "kind IN ('IMPRESSION','FEEDBACK','LISTEN','OFFLINE_PACK')"),
        _check(
            "target",
            "(kind IN ('IMPRESSION','FEEDBACK') AND interaction_id IS NOT NULL "
            "AND listening_event_id IS NULL AND offline_pack_id IS NULL) OR "
            "(kind='LISTEN' AND interaction_id IS NULL AND listening_event_id IS NOT NULL "
            "AND offline_pack_id IS NULL) OR (kind='OFFLINE_PACK' AND interaction_id IS NULL "
            "AND listening_event_id IS NULL AND offline_pack_id IS NOT NULL)",
        ),
        _check(
            "impression_parent",
            "(kind IN ('IMPRESSION','OFFLINE_PACK') AND impression_association_id IS NULL "
            "AND impression_association_kind IS NULL) OR "
            "(kind IN ('FEEDBACK','LISTEN') AND impression_association_id IS NOT NULL "
            "AND impression_association_kind IS NOT NULL "
            "AND impression_association_kind='IMPRESSION')",
        ),
        _check("served_rank", "served_rank BETWEEN 1 AND 1000"),
        _check("serving_provenance", "serving_provenance='P11'"),
        _check("request_surface", "request_surface IN ('recommendations','home','offline_pack')"),
        _check("presentation_source", "presentation_source ~ '^[a-z][a-z0-9_]{0,99}$'"),
        _check("presentation_surface", "presentation_surface ~ '^[a-z][a-z0-9_]{0,99}$'"),
        _check("wire_attribution_version", "wire_attribution_version=1"),
        _check("original_request_sha256", "octet_length(original_request_sha256)=32"),
        _check("acceptance_sha256", "octet_length(acceptance_sha256)=32"),
        _check("decision_sha256", "octet_length(decision_sha256)=32"),
        _check("served_item_sha256", "octet_length(served_item_sha256)=32"),
        _check("association_sha256", "octet_length(association_sha256)=32"),
        CheckConstraint(
            "(kind='OFFLINE_PACK' AND served_rank IS NULL AND recording_id IS NULL "
            "AND presentation_id IS NULL AND presentation_source IS NULL "
            "AND presentation_surface IS NULL AND served_item_sha256 IS NULL "
            "AND pack_served_item_count IS NOT NULL AND pack_served_item_count BETWEEN 0 AND 100 "
            "AND pack_served_items_sha256 IS NOT NULL "
            "AND octet_length(pack_served_items_sha256)=32 "
            "AND pack_payload_sha256 IS NOT NULL AND octet_length(pack_payload_sha256)=32) "
            "OR (kind<>'OFFLINE_PACK' AND served_rank IS NOT NULL AND recording_id IS NOT NULL "
            "AND presentation_id IS NOT NULL AND presentation_source IS NOT NULL "
            "AND presentation_surface IS NOT NULL AND served_item_sha256 IS NOT NULL "
            "AND pack_served_item_count IS NULL AND pack_served_items_sha256 IS NULL "
            "AND pack_payload_sha256 IS NULL)",
            name="ck_native_pack_association_shape",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "offline_pack_id"],
            [
                "ml.offline_recommendation_pack.user_id",
                "ml.offline_recommendation_pack.offline_pack_id",
            ],
            name="fk_native_pack_association_target",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "listening_event_id", "recording_id"],
            [
                "library.listening_event.user_id",
                "library.listening_event.listening_event_id",
                "library.listening_event.recording_id",
            ],
            name="fk_attribution_owner_listen",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "recommendation_request_id"],
            [
                "ml.recommendation_request.user_id",
                "ml.recommendation_request.recommendation_request_id",
            ],
            name="fk_attribution_owner_request",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["recommendation_request_id", "served_rank", "recording_id"],
            [
                "ml.recommendation_served_item.recommendation_request_id",
                "ml.recommendation_served_item.source_rank",
                "ml.recommendation_served_item.recording_id",
            ],
            name="fk_attribution_served_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "interaction_id", "recording_id"],
            [
                "library.user_interaction_event.user_id",
                "library.user_interaction_event.interaction_id",
                "library.user_interaction_event.recording_id",
            ],
            name="fk_attribution_owner_interaction",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "association_id",
            "owner_user_id",
            "presentation_id",
            "recommendation_request_id",
            "served_rank",
            "recording_id",
            "serving_provenance",
            "request_surface",
            "kind",
            name="uq_attribution_impression_parent",
        ),
        ForeignKeyConstraint(
            [
                "impression_association_id",
                "owner_user_id",
                "presentation_id",
                "recommendation_request_id",
                "served_rank",
                "recording_id",
                "serving_provenance",
                "request_surface",
                "impression_association_kind",
            ],
            [
                "ml.recommendation_attribution_association." + field
                for field in (
                    "association_id",
                    "owner_user_id",
                    "presentation_id",
                    "recommendation_request_id",
                    "served_rank",
                    "recording_id",
                    "serving_provenance",
                    "request_surface",
                    "kind",
                )
            ],
            name="fk_attribution_impression_parent",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_attribution_listen_parent",
            "impression_association_id",
            "association_id",
            postgresql_where=text("kind='LISTEN'"),
        ),
        Index(
            "ix_attribution_feedback_parent",
            "impression_association_id",
            "association_id",
            postgresql_where=text("kind='FEEDBACK'"),
        ),
        Index(
            "uq_attribution_impression_presentation",
            "owner_user_id",
            "presentation_id",
            "recommendation_request_id",
            "served_rank",
            unique=True,
            postgresql_where=text("kind='IMPRESSION'"),
        ),
        Index(
            "ix_attribution_request_expiry",
            "attribution_expires_at",
            "recommendation_request_id",
            "association_id",
        ),
        {"schema": "ml"},
    )


class NativeAttributionPurgeReceiptRow(Base):
    """Non-payload owner evidence; fixed removal boundary is request age 400 days."""

    __tablename__ = "native_attribution_purge_receipt"

    association_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    interaction_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, unique=True)
    source_server_sequence: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    original_request_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    acceptance_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    association_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    target_sha256: Mapped[bytes] = mapped_column(BYTEA(), nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    attribution_expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    purged_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    retain_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id"], ["account.user_account.user_id"], ondelete="CASCADE"
        ),
        _check("source_server_sequence", "source_server_sequence>0", receipt=True),
        _check("original_request_sha256", "octet_length(original_request_sha256)=32", receipt=True),
        _check("acceptance_sha256", "octet_length(acceptance_sha256)=32", receipt=True),
        _check("association_sha256", "octet_length(association_sha256)=32", receipt=True),
        _check("target_sha256", "octet_length(target_sha256)=32", receipt=True),
        _check(
            "reason",
            "reason IN ('SERVING_TRUTH_EXPIRED','OWNER_EVENT_ERASURE','ACCOUNT_DELETION')",
            receipt=True,
        ),
        _check(
            "retain_until", "retain_until=attribution_expires_at+interval '220 days'", receipt=True
        ),
        Index("ix_native_attribution_receipt_expiry", "retain_until", "association_id"),
        {"schema": "ml"},
    )


__all__ = ("NativeAttributionPurgeReceiptRow", "RecommendationAttributionAssociationRow")
