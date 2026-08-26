"""Typed SQLAlchemy mappings for the account PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class UserAccountRow(Base):
    """Persistence row for ``account.user_account``."""

    __tablename__ = "user_account"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    display_name: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'USER'"),
    )
    status: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        server_default=text("'ACTIVE'"),
    )
    settings_version: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        server_default=text("1"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        server_default=text("1"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        PrimaryKeyConstraint("user_id", name="user_account_pkey"),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 200",
            name="user_account_display_name_check",
        ),
        CheckConstraint(
            "settings_version >= 1",
            name="user_account_settings_version_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="user_account_row_version_check",
        ),
        CheckConstraint(
            "role IN ('OWNER', 'ADMIN', 'USER')",
            name="ck_user_account_role",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED')",
            name="ck_user_account_status",
        ),
        {"schema": "account"},
    )


class DeviceRow(Base):
    """Persistence row for ``account.device``."""

    __tablename__ = "device"

    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    device_name: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    app_version: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    public_key: Mapped[bytes | None] = mapped_column(
        BYTEA(),
        nullable=True,
    )
    public_key_thumbprint_sha256: Mapped[bytes | None] = mapped_column(BYTEA(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    row_version: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        server_default=text("1"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        PrimaryKeyConstraint("device_id", name="device_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="device_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(device_name) BETWEEN 1 AND 200",
            name="device_device_name_check",
        ),
        CheckConstraint(
            "length(app_version) BETWEEN 1 AND 100",
            name="device_app_version_check",
        ),
        CheckConstraint(
            "row_version >= 1",
            name="device_row_version_check",
        ),
        CheckConstraint(
            "public_key_thumbprint_sha256 IS NULL "
            "OR octet_length(public_key_thumbprint_sha256) = 32",
            name="ck_device_public_key_thumbprint_len",
        ),
        UniqueConstraint("user_id", "device_id", name="uq_device_user_pair"),
        CheckConstraint(
            "platform IN ('ANDROID', 'WEB', 'OTHER')",
            name="ck_device_platform",
        ),
        {"schema": "account"},
    )


class UserSessionRow(Base):
    """Persistence row for ``account.user_session``."""

    __tablename__ = "user_session"

    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    refresh_token_hash: Mapped[bytes] = mapped_column(
        BYTEA(),
        nullable=False,
    )
    issued_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    last_rotated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    family_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    generation: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    session_mode: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default=text("'LEGACY'")
    )

    __table_args__ = (
        PrimaryKeyConstraint("session_id", name="user_session_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="user_session_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["device_id"],
            ["account.device.device_id"],
            name="user_session_device_id_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("refresh_token_hash", name="user_session_refresh_token_hash_key"),
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_user_session_device_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("user_id", "device_id", "session_id", name="uq_user_session_actor"),
        CheckConstraint(
            "octet_length(refresh_token_hash) = 32",
            name="ck_user_session_hash_len",
        ),
        CheckConstraint(
            "expires_at > issued_at",
            name="ck_user_session_expiry",
        ),
        CheckConstraint(
            "generation IS NULL OR generation >= 0",
            name="ck_user_session_generation",
        ),
        CheckConstraint("session_mode IN ('LEGACY', 'V2')", name="ck_user_session_mode"),
        {"schema": "account"},
    )


Index(
    "ix_device_user_active",
    DeviceRow.user_id,
    DeviceRow.last_seen_at.desc(),
    postgresql_where=text("revoked_at IS NULL"),
)

Index(
    "ix_user_session_user_active",
    UserSessionRow.user_id,
    UserSessionRow.expires_at,
    postgresql_where=text("revoked_at IS NULL"),
)


__all__ = (
    "DeviceRow",
    "UserAccountRow",
    "UserSessionRow",
)
