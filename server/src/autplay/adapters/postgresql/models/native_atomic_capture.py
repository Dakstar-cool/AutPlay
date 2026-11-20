"""Exact storage mapping for the private native original-time connection."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Table
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.orm import Mapped
from sqlalchemy.types import UserDefinedType

from ..base import Base


class _Xid8(UserDefinedType[str]):
    """Native xid8; the current psycopg driver decodes unknown text OIDs as str."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "XID8"

    @property
    def python_type(self) -> type[str]:
        return str


# SQLAlchemy 2.0 has no native xid8 reflection mapping. Register this exact
# native type so Alembic compare_type can distinguish it from BIGINT/NullType.
PGDialect.ischema_names.setdefault("xid8", _Xid8)

_NAME = "native_atomic_capture_connection"
_connection = Table(
    _NAME,
    Base.metadata,
    Column(
        "original_request_id",
        PG_UUID(as_uuid=True),
        ForeignKey("ml.sona_capture_bundle.recommendation_request_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "served_request_id",
        PG_UUID(as_uuid=True),
        ForeignKey("ml.recommendation_request.recommendation_request_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column(
        "owner_user_id",
        PG_UUID(as_uuid=True),
        ForeignKey("account.user_account.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("created_transaction", _Xid8(), nullable=False),
    Column("capture_sha256", BYTEA(), nullable=False),
    Column("served_document_sha256", BYTEA(), nullable=False),
    Column("decision_sha256", BYTEA(), nullable=False),
    Column("consent_generation", BigInteger(), nullable=False),
    Column("consent_receipt_sha256", BYTEA(), nullable=False),
    CheckConstraint("octet_length(capture_sha256)=32", name=f"{_NAME}_capture_sha256_check"),
    CheckConstraint(
        "octet_length(served_document_sha256)=32", name=f"{_NAME}_served_document_sha256_check"
    ),
    CheckConstraint("octet_length(decision_sha256)=32", name=f"{_NAME}_decision_sha256_check"),
    CheckConstraint("consent_generation>=1", name=f"{_NAME}_consent_generation_check"),
    CheckConstraint(
        "octet_length(consent_receipt_sha256)=32", name=f"{_NAME}_consent_receipt_sha256_check"
    ),
    CheckConstraint("original_request_id<>served_request_id", name=f"{_NAME}_check"),
    schema="ml",
)


class NativeAtomicCaptureConnectionRow(Base):
    __table__ = _connection

    original_request_id: Mapped[UUID]
    served_request_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    created_transaction: Mapped[str]
    capture_sha256: Mapped[bytes]
    served_document_sha256: Mapped[bytes]
    decision_sha256: Mapped[bytes]
    consent_generation: Mapped[int]
    consent_receipt_sha256: Mapped[bytes]


__all__ = ("NativeAtomicCaptureConnectionRow",)
