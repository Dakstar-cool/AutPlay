"""Typed rows for S1C same-server social state."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class FriendRequestRow(Base):
    __tablename__ = "friend_request"
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    requester_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    target_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(Text())
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        PrimaryKeyConstraint("request_id", name="friend_request_pkey"),
        ForeignKeyConstraint(
            ["requester_user_id"],
            ["account.user_account.user_id"],
            name="friend_request_requester_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_user_id"],
            ["account.user_account.user_id"],
            name="friend_request_target_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "requester_user_id <> target_user_id", name="ck_social_friend_request_pair"
        ),
        CheckConstraint(
            "state IN ('PENDING','ACCEPTED','DECLINED','CANCELLED','BLOCKED','EXPIRED')",
            name="ck_social_friend_request_state",
        ),
        Index(
            "uq_social_pending_friend_request",
            "requester_user_id",
            "target_user_id",
            unique=True,
            postgresql_where=text("state = 'PENDING'"),
        ),
        Index("ix_social_friend_request_target", "target_user_id", "expires_at"),
        {"schema": "social"},
    )


class FriendshipRow(Base):
    __tablename__ = "friendship"
    lower_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    higher_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        PrimaryKeyConstraint("lower_user_id", "higher_user_id", name="friendship_pkey"),
        ForeignKeyConstraint(
            ["lower_user_id"],
            ["account.user_account.user_id"],
            name="friendship_lower_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["higher_user_id"],
            ["account.user_account.user_id"],
            name="friendship_higher_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("lower_user_id < higher_user_id", name="ck_social_friendship_order"),
        {"schema": "social"},
    )


class UserBlockRow(Base):
    __tablename__ = "user_block"
    blocker_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    blocked_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    blocked_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    unblocked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        PrimaryKeyConstraint("blocker_user_id", "blocked_user_id", name="user_block_pkey"),
        ForeignKeyConstraint(
            ["blocker_user_id"],
            ["account.user_account.user_id"],
            name="user_block_blocker_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["blocked_user_id"],
            ["account.user_account.user_id"],
            name="user_block_blocked_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("blocker_user_id <> blocked_user_id", name="ck_social_user_block_pair"),
        Index(
            "ix_social_user_block_active",
            "blocker_user_id",
            "blocked_user_id",
            unique=True,
            postgresql_where=text("unblocked_at IS NULL"),
        ),
        {"schema": "social"},
    )


class PresenceSettingsRow(Base):
    __tablename__ = "presence_settings"
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    friend_presence_visibility_enabled: Mapped[bool] = mapped_column(
        Boolean(), server_default=text("false")
    )
    room_activity_sharing_enabled: Mapped[bool] = mapped_column(
        Boolean(), server_default=text("false")
    )
    invite_availability_enabled: Mapped[bool] = mapped_column(
        Boolean(), server_default=text("false")
    )
    revision: Mapped[int] = mapped_column(BigInteger(), server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        PrimaryKeyConstraint("user_id", name="presence_settings_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="presence_settings_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision >= 1", name="ck_social_presence_settings_revision"),
        {"schema": "social"},
    )


class ProfileStatisticsSettingsRow(Base):
    """Server-owned S2 friend visibility policy; absent means private at revision zero."""

    __tablename__ = "profile_statistics_settings"
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    friends_can_view_statistics: Mapped[bool] = mapped_column(
        Boolean(), server_default=text("false")
    )
    revision: Mapped[int] = mapped_column(BigInteger(), server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        PrimaryKeyConstraint("user_id", name="profile_statistics_settings_pkey"),
        ForeignKeyConstraint(
            ["user_id"],
            ["account.user_account.user_id"],
            name="profile_statistics_settings_user_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision >= 0", name="ck_social_profile_statistics_settings_revision"),
        {"schema": "social"},
    )


class PresenceHeartbeatRow(Base):
    __tablename__ = "presence_heartbeat"
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    request_sha256: Mapped[bytes] = mapped_column(BYTEA())
    last_heartbeat_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    fresh_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "device_id", name="presence_heartbeat_pkey"),
        ForeignKeyConstraint(
            ["user_id", "device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_social_presence_heartbeat_device_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["session_id"],
            ["account.user_session.session_id"],
            name="presence_heartbeat_session_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("octet_length(request_sha256) = 32", name="ck_social_presence_hash"),
        CheckConstraint("fresh_until > last_heartbeat_at", name="ck_social_presence_expiry"),
        Index("ix_social_presence_fresh", "user_id", "fresh_until"),
        {"schema": "social"},
    )


class FriendRoomInvitationRow(Base):
    __tablename__ = "friend_room_invitation"
    invitation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    create_operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    room_epoch: Mapped[int] = mapped_column(BigInteger())
    host_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    host_device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    target_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(Text())
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(Text())
    accepted_device_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    accepting_session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        PrimaryKeyConstraint("invitation_id", name="friend_room_invitation_pkey"),
        UniqueConstraint("create_operation_id", name="uq_social_room_invitation_create_operation"),
        ForeignKeyConstraint(
            ["room_id"],
            ["wave.room.room_id"],
            name="friend_room_invitation_room_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["host_user_id", "host_device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_social_room_invitation_host_device",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_user_id"],
            ["account.user_account.user_id"],
            name="friend_room_invitation_target_user_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["accepted_device_id"],
            ["account.device.device_id"],
            name="friend_room_invitation_accepted_device_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["accepting_session_id"],
            ["account.user_session.session_id"],
            name="friend_room_invitation_accepting_session_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("room_epoch >= 1", name="ck_social_room_invitation_epoch"),
        CheckConstraint(
            "state IN ('PENDING','ACCEPTED','CANCELLED','EXPIRED','BLOCKED','FULL','ROOM_CHANGED')",
            name="ck_social_room_invitation_state",
        ),
        Index("ix_social_room_invitation_target", "target_user_id", "expires_at"),
        Index(
            "uq_social_pending_room_target",
            "room_id",
            "target_user_id",
            unique=True,
            postgresql_where=text("state = 'PENDING'"),
        ),
        {"schema": "social"},
    )


class SocialOperationReceiptRow(Base):
    __tablename__ = "operation_receipt"
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    actor_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    actor_device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text())
    request_sha256: Mapped[bytes] = mapped_column(BYTEA())
    result_code: Mapped[str] = mapped_column(Text())
    result_target_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    result_room_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    result_json: Mapped[str] = mapped_column(Text())
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        PrimaryKeyConstraint("operation_id", name="operation_receipt_pkey"),
        ForeignKeyConstraint(
            ["actor_user_id", "actor_device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_social_operation_receipt_actor_device",
            ondelete="RESTRICT",
        ),
        CheckConstraint("octet_length(request_sha256) = 32", name="ck_social_operation_hash"),
        CheckConstraint("length(result_code) BETWEEN 1 AND 64", name="ck_social_result_code"),
        CheckConstraint("octet_length(result_json) <= 2048", name="ck_social_result_json_size"),
        Index("ix_social_operation_receipt_expiry", "expires_at"),
        {"schema": "social"},
    )


class SocialRateWindowRow(Base):
    __tablename__ = "rate_window"
    rate_key_sha256: Mapped[bytes] = mapped_column(BYTEA())
    scope: Mapped[str] = mapped_column(Text())
    window_started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer())
    __table_args__ = (
        PrimaryKeyConstraint("rate_key_sha256", name="rate_window_pkey"),
        CheckConstraint("octet_length(rate_key_sha256) = 32", name="ck_social_rate_key"),
        CheckConstraint("attempt_count >= 1", name="ck_social_rate_attempts"),
        CheckConstraint("expires_at > window_started_at", name="ck_social_rate_expiry"),
        Index("ix_social_rate_window_expiry", "expires_at"),
        {"schema": "social"},
    )


class GuestInvitationRow(Base):
    __tablename__ = "guest_invitation"
    invitation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    room_epoch: Mapped[int] = mapped_column(BigInteger())
    host_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    host_device_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    host_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    document_secret_sha256: Mapped[bytes] = mapped_column(BYTEA(), unique=True)
    role: Mapped[str] = mapped_column(Text(), server_default=text("'GUEST'"))
    allowed_actions: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        server_default=text(
            "ARRAY['ROOM_SNAPSHOT','ROOM_EVENTS','ROOM_PRESENCE','ROOM_PREFLIGHT',"
            "'ROOM_TIMING','ROOM_LEAVE']::text[]"
        ),
    )
    state: Mapped[str] = mapped_column(Text(), server_default=text("'PENDING'"))
    max_uses: Mapped[int] = mapped_column(SmallInteger(), server_default=text("1"))
    consumed_uses: Mapped[int] = mapped_column(SmallInteger(), server_default=text("0"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        UniqueConstraint("invitation_id", "room_id", name="uq_social_guest_invitation_room"),
        ForeignKeyConstraint(
            ["room_id"],
            ["wave.room.room_id"],
            name="guest_invitation_room_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["host_user_id", "host_device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_social_guest_invitation_host_device",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["host_user_id", "host_device_id", "host_session_id"],
            [
                "account.user_session.user_id",
                "account.user_session.device_id",
                "account.user_session.session_id",
            ],
            name="fk_social_guest_invitation_host_session",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "octet_length(document_secret_sha256)=32", name="ck_social_guest_invitation_hash"
        ),
        CheckConstraint("room_epoch>=1", name="ck_social_guest_invitation_epoch"),
        CheckConstraint("role='GUEST'", name="ck_social_guest_invitation_role"),
        CheckConstraint(
            "allowed_actions=ARRAY['ROOM_SNAPSHOT','ROOM_EVENTS','ROOM_PRESENCE','ROOM_PREFLIGHT','ROOM_TIMING','ROOM_LEAVE']::text[]",
            name="ck_social_guest_invitation_actions",
        ),
        CheckConstraint(
            "state IN ('PENDING','DEPLETED','REVOKED','EXPIRED','ROOM_CLOSED')",
            name="ck_social_guest_invitation_state",
        ),
        CheckConstraint(
            "max_uses BETWEEN 1 AND 8 AND consumed_uses BETWEEN 0 AND max_uses",
            name="ck_social_guest_invitation_uses",
        ),
        CheckConstraint("expires_at>created_at", name="ck_social_guest_invitation_expiry"),
        CheckConstraint(
            "(state='PENDING' AND terminal_at IS NULL) OR "
            "(state<>'PENDING' AND terminal_at IS NOT NULL)",
            name="ck_social_guest_invitation_terminal",
        ),
        Index("ix_social_guest_invitation_room_state", "room_id", "state", "expires_at"),
        Index("ix_social_guest_invitation_expiry", "expires_at"),
        {"schema": "social"},
    )


class GuestSessionRow(Base):
    __tablename__ = "guest_session"
    guest_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    invitation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    room_epoch: Mapped[int] = mapped_column(BigInteger())
    access_secret_sha256: Mapped[bytes] = mapped_column(BYTEA(), unique=True)
    display_name: Mapped[str] = mapped_column(Text())
    role: Mapped[str] = mapped_column(Text(), server_default=text("'GUEST'"))
    allowed_actions: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        server_default=text(
            "ARRAY['ROOM_SNAPSHOT','ROOM_EVENTS','ROOM_PRESENCE','ROOM_PREFLIGHT',"
            "'ROOM_TIMING','ROOM_LEAVE']::text[]"
        ),
    )
    state: Mapped[str] = mapped_column(Text(), server_default=text("'ACTIVE'"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    last_present_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        UniqueConstraint("guest_session_id", "room_id", name="uq_social_guest_session_room"),
        ForeignKeyConstraint(
            ["invitation_id", "room_id"],
            ["social.guest_invitation.invitation_id", "social.guest_invitation.room_id"],
            name="fk_social_guest_session_invitation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "octet_length(access_secret_sha256)=32", name="ck_social_guest_session_hash"
        ),
        CheckConstraint("room_epoch>=1", name="ck_social_guest_session_epoch"),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 40 AND display_name !~ '[[:cntrl:]]'",
            name="ck_social_guest_session_name",
        ),
        CheckConstraint("role='GUEST'", name="ck_social_guest_session_role"),
        CheckConstraint(
            "allowed_actions=ARRAY['ROOM_SNAPSHOT','ROOM_EVENTS','ROOM_PRESENCE','ROOM_PREFLIGHT','ROOM_TIMING','ROOM_LEAVE']::text[]",
            name="ck_social_guest_session_actions",
        ),
        CheckConstraint(
            "state IN ('ACTIVE','LEFT','REVOKED','EXPIRED','ROOM_CLOSED')",
            name="ck_social_guest_session_state",
        ),
        CheckConstraint("expires_at>created_at", name="ck_social_guest_session_expiry"),
        CheckConstraint(
            "last_present_at IS NULL OR last_present_at>=created_at",
            name="ck_social_guest_session_presence",
        ),
        CheckConstraint(
            "(state='ACTIVE' AND terminal_at IS NULL) OR "
            "(state<>'ACTIVE' AND terminal_at IS NOT NULL)",
            name="ck_social_guest_session_terminal",
        ),
        Index("ix_social_guest_session_room_state", "room_id", "state", "expires_at"),
        Index("ix_social_guest_session_expiry", "expires_at"),
        {"schema": "social"},
    )


class GuestOperationReceiptRow(Base):
    __tablename__ = "guest_operation_receipt"
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    actor_kind: Mapped[str] = mapped_column(Text())
    actor_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    actor_device_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    actor_secret_sha256: Mapped[bytes | None] = mapped_column(BYTEA())
    actor_guest_session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text())
    request_sha256: Mapped[bytes] = mapped_column(BYTEA())
    result_code: Mapped[str] = mapped_column(Text())
    result_invitation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    result_guest_session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    result_room_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    result_json: Mapped[str] = mapped_column(Text())
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["actor_user_id", "actor_device_id"],
            ["account.device.user_id", "account.device.device_id"],
            name="fk_social_guest_operation_host",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["actor_guest_session_id"],
            ["social.guest_session.guest_session_id"],
            name="fk_social_guest_operation_actor_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["result_invitation_id"],
            ["social.guest_invitation.invitation_id"],
            name="fk_social_guest_operation_invitation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["result_guest_session_id"],
            ["social.guest_session.guest_session_id"],
            name="fk_social_guest_operation_result_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["result_room_id"],
            ["wave.room.room_id"],
            name="fk_social_guest_operation_room",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "actor_kind IN ('HOST','DOCUMENT','GUEST')", name="ck_social_guest_operation_actor_kind"
        ),
        CheckConstraint(
            "(actor_kind='HOST' AND actor_user_id IS NOT NULL AND "
            "actor_device_id IS NOT NULL AND actor_secret_sha256 IS NULL AND "
            "actor_guest_session_id IS NULL) OR "
            "(actor_kind='DOCUMENT' AND actor_user_id IS NULL AND "
            "actor_device_id IS NULL AND octet_length(actor_secret_sha256)=32 AND "
            "actor_guest_session_id IS NULL) OR "
            "(actor_kind='GUEST' AND actor_user_id IS NULL AND "
            "actor_device_id IS NULL AND actor_secret_sha256 IS NULL AND "
            "actor_guest_session_id IS NOT NULL)",
            name="ck_social_guest_operation_actor",
        ),
        CheckConstraint(
            "action IN ('ISSUE','REDEEM','REVOKE','LEAVE')", name="ck_social_guest_operation_action"
        ),
        CheckConstraint("octet_length(request_sha256)=32", name="ck_social_guest_operation_hash"),
        CheckConstraint(
            "length(result_code) BETWEEN 1 AND 64 AND octet_length(result_json)<=2048",
            name="ck_social_guest_operation_result",
        ),
        CheckConstraint("expires_at>created_at", name="ck_social_guest_operation_expiry"),
        Index("ix_social_guest_operation_expiry", "expires_at"),
        {"schema": "social"},
    )


class GuestPreflightRow(Base):
    __tablename__ = "guest_preflight"
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    guest_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    queue_entry_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    recording_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    queue_version: Mapped[int] = mapped_column(BigInteger())
    availability: Mapped[str] = mapped_column(Text())
    final_ready: Mapped[bool] = mapped_column(Boolean(), server_default=text("false"))
    source_checked_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        PrimaryKeyConstraint("room_id", "guest_session_id", "queue_entry_id"),
        ForeignKeyConstraint(
            ["guest_session_id", "room_id"],
            ["social.guest_session.guest_session_id", "social.guest_session.room_id"],
            name="fk_social_guest_preflight_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["queue_entry_id", "room_id"],
            ["wave.queue_entry.queue_entry_id", "wave.queue_entry.room_id"],
            name="fk_social_guest_preflight_queue",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["recording_id"],
            ["catalog.recording.recording_id"],
            name="guest_preflight_recording_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("queue_version>=1", name="ck_social_guest_preflight_version"),
        CheckConstraint(
            "availability IN ('LOCAL','DOWNLOADED','VAULT_STREAMABLE','UNAVAILABLE')",
            name="ck_social_guest_preflight_availability",
        ),
        CheckConstraint("expires_at>source_checked_at", name="ck_social_guest_preflight_expiry"),
        Index("ix_social_guest_preflight_expiry", "expires_at"),
        {"schema": "social"},
    )


class GuestTimingReportRow(Base):
    __tablename__ = "guest_timing_report"
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    guest_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    command_sequence: Mapped[int] = mapped_column(BigInteger())
    rtt_ms: Mapped[int] = mapped_column(Integer())
    offset_ms: Mapped[int] = mapped_column(Integer())
    uncertainty_ms: Mapped[int] = mapped_column(Integer())
    start_skew_ms: Mapped[int | None] = mapped_column(Integer())
    drift_ms: Mapped[int | None] = mapped_column(Integer())
    reported_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    __table_args__ = (
        PrimaryKeyConstraint("room_id", "guest_session_id", "command_sequence"),
        ForeignKeyConstraint(
            ["guest_session_id", "room_id"],
            ["social.guest_session.guest_session_id", "social.guest_session.room_id"],
            name="fk_social_guest_timing_session",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "rtt_ms BETWEEN 0 AND 1000 AND uncertainty_ms BETWEEN 0 AND 100 "
            "AND abs(offset_ms)<=86400000",
            name="ck_social_guest_timing_bounds",
        ),
        Index("ix_social_guest_timing_reported", "reported_at"),
        {"schema": "social"},
    )


class GuestRateWindowRow(Base):
    __tablename__ = "guest_rate_window"
    rate_key_sha256: Mapped[bytes] = mapped_column(BYTEA(), primary_key=True)
    scope: Mapped[str] = mapped_column(Text())
    window_started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer())
    __table_args__ = (
        CheckConstraint("octet_length(rate_key_sha256)=32", name="ck_social_guest_rate_hash"),
        CheckConstraint("attempt_count>=1", name="ck_social_guest_rate_attempts"),
        CheckConstraint("expires_at>window_started_at", name="ck_social_guest_rate_expiry"),
        Index("ix_social_guest_rate_expiry", "expires_at"),
        {"schema": "social"},
    )


__all__ = (
    "FriendRequestRow",
    "FriendRoomInvitationRow",
    "FriendshipRow",
    "GuestInvitationRow",
    "GuestOperationReceiptRow",
    "GuestPreflightRow",
    "GuestRateWindowRow",
    "GuestSessionRow",
    "GuestTimingReportRow",
    "PresenceHeartbeatRow",
    "PresenceSettingsRow",
    "ProfileStatisticsSettingsRow",
    "SocialOperationReceiptRow",
    "SocialRateWindowRow",
    "UserBlockRow",
)
