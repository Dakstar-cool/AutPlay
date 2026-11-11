"""Immutable original P11 collection, separate from admitted model datasets."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class SonaNativeCaptureRow(Base):
    __tablename__ = "sona_native_capture"
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("ml.recommendation_request.recommendation_request_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.user_account.user_id", ondelete="CASCADE")
    )
    consent_revision: Mapped[int] = mapped_column(BigInteger)
    surface: Mapped[str] = mapped_column(Text)
    collection_status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    retained_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    payload: Mapped[bytes] = mapped_column(BYTEA)
    payload_sha256: Mapped[bytes] = mapped_column(BYTEA)
    __table_args__ = (
        CheckConstraint("consent_revision>=1", name="sona_native_capture_consent_revision_check"),
        CheckConstraint(
            "surface IN ('recommendations','home','offline_pack')",
            name="sona_native_capture_surface_check",
        ),
        CheckConstraint(
            "collection_status IN ('COLLECTED','SOURCE_LIMIT','SOURCE_PROVENANCE_GAP')",
            name="sona_native_capture_collection_status_check",
        ),
        CheckConstraint(
            "retained_until>created_at AND retained_until<=created_at+interval '180 days'",
            name="sona_native_capture_retention",
        ),
        CheckConstraint(
            "octet_length(payload) BETWEEN 1 AND 16777216 AND octet_length(payload_sha256)=32 "
            "AND sha256(payload)=payload_sha256",
            name="sona_native_capture_payload",
        ),
        Index("sona_native_capture_owner_time", "user_id", "created_at"),
        Index("sona_native_capture_expiry", "retained_until", "request_id"),
        {"schema": "ml"},
    )
