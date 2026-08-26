"""Real PostgreSQL evidence for S1C friendship, presence and P13 invitation joins."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models import (
    DeviceRow,
    FriendRequestRow,
    FriendRoomInvitationRow,
    FriendshipRow,
    PresenceHeartbeatRow,
    PresenceSettingsRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.wave import WaveInvitationRow, WaveMemberRow
from autplay.adapters.postgresql.wave import SqlAlchemyWaveService
from autplay.application.social import SocialError, SocialService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import public_key_thumbprint, public_spki
from autplay.domain.wave import WaveForbidden
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker


def _principal(session: Session, name: str, now: datetime) -> Principal:
    user_id, device_id, session_id = uuid4(), uuid4(), uuid4()
    session.add(UserAccountRow(user_id=user_id, display_name=name, role="USER", status="ACTIVE"))
    session.flush()
    session.add(
        DeviceRow(
            device_id=device_id,
            user_id=user_id,
            device_name=name,
            platform="ANDROID",
            app_version="s1c",
        )
    )
    session.flush()
    session.add(
        UserSessionRow(
            session_id=session_id,
            user_id=user_id,
            device_id=device_id,
            refresh_token_hash=uuid4().bytes + uuid4().bytes,
            issued_at=now,
            expires_at=now + timedelta(days=1),
            last_rotated_at=now,
            session_mode="V2",
        )
    )
    return Principal(user_id, device_id, session_id, AccountRole.USER)


def _device_principal(session: Session, user_id: UUID, name: str, now: datetime) -> Principal:
    device_id, session_id = uuid4(), uuid4()
    session.add(
        DeviceRow(
            device_id=device_id,
            user_id=user_id,
            device_name=name,
            platform="ANDROID",
            app_version="s1c",
        )
    )
    session.flush()
    session.add(
        UserSessionRow(
            session_id=session_id,
            user_id=user_id,
            device_id=device_id,
            refresh_token_hash=uuid4().bytes + uuid4().bytes,
            issued_at=now,
            expires_at=now + timedelta(days=1),
            last_rotated_at=now,
            session_mode="V2",
        )
    )
    return Principal(user_id, device_id, session_id, AccountRole.USER)


def _instance(session: Session, key: ec.EllipticCurvePrivateKey, now: datetime) -> None:
    spki = public_spki(key)
    session.add(
        ServerInstanceRow(
            server_instance_id=uuid4(),
            identity_epoch=1,
            identity_public_key_spki=spki,
            identity_thumbprint_sha256=public_key_thumbprint(spki),
            label_hint="S1C",
            api_origin="https://api.example.test",
            stream_origin="https://stream.example.test",
            capability_revision=1,
            created_at=now,
            updated_at=now,
        )
    )


def _make_friends(
    social: SocialService, first: Principal, second: Principal, now: datetime
) -> None:
    social.command(
        first,
        {
            "operation_id": str(uuid4()),
            "action": "SEND_REQUEST",
            "contact_card": social.contact_card(second, now),
        },
        now,
    )
    social.command(
        second,
        {
            "operation_id": str(uuid4()),
            "action": "ACCEPT_REQUEST",
            "target_account_id": str(first.user_id),
        },
        now,
    )


def test_social_friend_presence_invite_and_active_room_block(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    try:
        with sessions.begin() as session:
            first = _principal(session, "First", now)
            second = _principal(session, "Second", now)
            second_other_device = _device_principal(session, second.user_id, "Second B", now)
            _instance(session, key, now)
        social = SocialService(sessions, key)
        wave = SqlAlchemyWaveService(sessions)

        card = social.contact_card(second, now)
        send_id = uuid4()
        sent = social.command(
            first,
            {
                "operation_id": str(send_id),
                "action": "SEND_REQUEST",
                "contact_card": card,
            },
            now,
        )
        assert sent == {"operation_id": str(send_id), "state": "PENDING_OUTGOING"}
        assert (
            social.command(
                first,
                {
                    "operation_id": str(send_id),
                    "action": "SEND_REQUEST",
                    "contact_card": card,
                },
                now,
            )
            == sent
        )
        accepted = social.command(
            second,
            {
                "operation_id": str(uuid4()),
                "action": "ACCEPT_REQUEST",
                "target_account_id": str(first.user_id),
            },
            now,
        )
        assert accepted["state"] == "MUTUAL"

        assert social.presence(first, second.user_id, now) == {"presence": "OFFLINE"}
        social.set_settings(
            second,
            {
                "operation_id": str(uuid4()),
                "friend_presence_visibility_enabled": True,
                "room_activity_sharing_enabled": True,
                "invite_availability_enabled": True,
            },
            now,
        )
        social.heartbeat(second, uuid4(), now)
        assert social.presence(first, second.user_id, now) == {"presence": "AVAILABLE_TO_INVITE"}

        room = wave.create(first, now, ())
        invitation = social.create_invitation(first, room.room_id, second.user_id, uuid4(), now)
        invitation_id = UUID(str(invitation["invitation_id"]))
        accept_id = uuid4()
        joined = social.accept_invitation(second, invitation_id, accept_id, now)
        assert joined["membership_state"] == "MEMBER"
        assert social.accept_invitation(second, invitation_id, accept_id, now) == joined
        assert social.presence(first, second.user_id, now) == {"presence": "IN_ROOM"}
        assert wave.snapshot(room.room_id, second, now).room_id == room.room_id
        with sessions.begin() as session:
            assert (
                session.get(
                    WaveInvitationRow,
                    {"room_id": room.room_id, "user_id": second.user_id},
                )
                is None
            )
        with pytest.raises(WaveForbidden):
            wave.join(room.code, second_other_device, now)

        with pytest.raises(SocialError, match="active_room_exit_required") as blocked:
            social.command(
                first,
                {
                    "operation_id": str(uuid4()),
                    "action": "BLOCK_USER",
                    "target_account_id": str(second.user_id),
                },
                now,
            )
        assert blocked.value.details == {"room_count": 1}
        wave.leave(room.room_id, second, now)
        result = social.command(
            first,
            {
                "operation_id": str(uuid4()),
                "action": "BLOCK_USER",
                "target_account_id": str(second.user_id),
            },
            now,
        )
        assert result["state"] == "BLOCKED"
        with pytest.raises(SocialError, match="presence_private"):
            social.presence(second, first.user_id, now)

        with sessions.begin() as session:
            assert session.scalar(select(func.count()).select_from(UserSessionRow)) == 3
    finally:
        engine.dispose()


def test_expired_request_and_invitation_can_be_recreated_before_cleanup(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    try:
        with sessions.begin() as session:
            first = _principal(session, "First", now)
            second = _principal(session, "Second", now)
            _instance(session, key, now)
        social = SocialService(sessions, key)
        card = social.contact_card(second, now)
        social.command(
            first,
            {"operation_id": str(uuid4()), "action": "SEND_REQUEST", "contact_card": card},
            now,
        )
        with sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE social.friend_request SET expires_at=:n "
                    "WHERE requester_user_id=:a AND target_user_id=:b AND state='PENDING'"
                ),
                {"n": now, "a": first.user_id, "b": second.user_id},
            )
        retried = social.command(
            first,
            {"operation_id": str(uuid4()), "action": "SEND_REQUEST", "contact_card": card},
            now,
        )
        assert retried["state"] == "PENDING_OUTGOING"
        social.command(
            second,
            {
                "operation_id": str(uuid4()),
                "action": "ACCEPT_REQUEST",
                "target_account_id": str(first.user_id),
            },
            now,
        )
        social.set_settings(
            second,
            {
                "operation_id": str(uuid4()),
                "friend_presence_visibility_enabled": False,
                "room_activity_sharing_enabled": False,
                "invite_availability_enabled": True,
            },
            now,
        )
        wave = SqlAlchemyWaveService(sessions)
        room = wave.create(first, now)
        first_invite = social.create_invitation(first, room.room_id, second.user_id, uuid4(), now)
        with sessions.begin() as session:
            invite = session.get(FriendRoomInvitationRow, UUID(str(first_invite["invitation_id"])))
            assert invite is not None
            invite.expires_at = now
        second_invite = social.create_invitation(first, room.room_id, second.user_id, uuid4(), now)
        assert second_invite["state"] == "PENDING"
        assert second_invite["invitation_id"] != first_invite["invitation_id"]
        with sessions.begin() as session:
            states = session.scalars(
                select(FriendRequestRow.state).order_by(FriendRequestRow.created_at)
            ).all()
            assert states == ["EXPIRED", "ACCEPTED"]
            invite_states = session.scalars(
                select(FriendRoomInvitationRow.state).order_by(
                    FriendRoomInvitationRow.created_at,
                    FriendRoomInvitationRow.invitation_id,
                )
            ).all()
            assert sorted(invite_states) == ["EXPIRED", "PENDING"]
    finally:
        engine.dispose()


def test_invitation_accept_rechecks_sessions_and_materializes_only_accepting_device(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    try:
        with sessions.begin() as session:
            host = _principal(session, "Host", now)
            target = _principal(session, "Target", now)
            accepting_device = _device_principal(session, target.user_id, "Target B", now)
            _instance(session, key, now)
        social = SocialService(sessions, key)
        _make_friends(social, host, target, now)
        social.set_settings(
            target,
            {
                "operation_id": str(uuid4()),
                "friend_presence_visibility_enabled": False,
                "room_activity_sharing_enabled": False,
                "invite_availability_enabled": True,
            },
            now,
        )
        wave = SqlAlchemyWaveService(sessions)
        room = wave.create(host, now)
        invitation = social.create_invitation(host, room.room_id, target.user_id, uuid4(), now)
        invitation_id = UUID(str(invitation["invitation_id"]))
        with sessions.begin() as session:
            accepting_session = session.get(UserSessionRow, accepting_device.session_id)
            assert accepting_session is not None
            accepting_session.revoked_at = now
        with pytest.raises(SocialError, match="auth_attention_required"):
            social.accept_invitation(accepting_device, invitation_id, uuid4(), now)
        with sessions.begin() as session:
            accepting_session = session.get(UserSessionRow, accepting_device.session_id)
            assert accepting_session is not None
            accepting_session.revoked_at = None
        joined = social.accept_invitation(accepting_device, invitation_id, uuid4(), now)
        assert joined["membership_state"] == "MEMBER"
        with sessions.begin() as session:
            members = session.scalars(
                select(WaveMemberRow.device_id).where(
                    WaveMemberRow.room_id == room.room_id,
                    WaveMemberRow.user_id == target.user_id,
                    WaveMemberRow.status == "JOINED",
                )
            ).all()
            assert members == [accepting_device.device_id]
            assert (
                session.get(
                    WaveInvitationRow,
                    {"room_id": room.room_id, "user_id": target.user_id},
                )
                is None
            )
        with pytest.raises(WaveForbidden):
            wave.join(room.code, target, now)
    finally:
        engine.dispose()


def test_disable_and_delete_retire_social_state_without_counterpart_disclosure(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    try:
        with sessions.begin() as session:
            first = _principal(session, "First", now)
            second = _principal(session, "Second", now)
            deleted_user_id = uuid4()
            session.add(
                UserAccountRow(
                    user_id=deleted_user_id,
                    display_name="Delete Me",
                    role="USER",
                    status="ACTIVE",
                )
            )
            _instance(session, key, now)
        social = SocialService(sessions, key)
        _make_friends(social, first, second, now)
        social.set_settings(
            second,
            {
                "operation_id": str(uuid4()),
                "friend_presence_visibility_enabled": True,
                "room_activity_sharing_enabled": False,
                "invite_availability_enabled": True,
            },
            now,
        )
        social.heartbeat(second, uuid4(), now)
        room = SqlAlchemyWaveService(sessions).create(first, now)
        social.create_invitation(first, room.room_id, second.user_id, uuid4(), now)
        low, high = sorted((first.user_id, deleted_user_id), key=lambda value: value.bytes)
        with sessions.begin() as session:
            session.add(FriendshipRow(lower_user_id=low, higher_user_id=high, created_at=now))
            session.add(
                FriendRequestRow(
                    request_id=uuid4(),
                    requester_user_id=first.user_id,
                    target_user_id=deleted_user_id,
                    state="PENDING",
                    expires_at=now + timedelta(days=1),
                    terminal_at=None,
                    created_at=now,
                )
            )
        with sessions.begin() as session:
            account = session.get(UserAccountRow, second.user_id)
            assert account is not None
            account.status = "DISABLED"
        snapshot = social.snapshot(first, now)
        friends = snapshot["friends"]
        assert isinstance(friends, list)
        assert all(
            isinstance(item, dict) and item.get("account_id") != str(second.user_id)
            for item in friends
        )
        assert snapshot["sent_room_invitations"] == []
        with sessions.begin() as session:
            assert session.get(PresenceSettingsRow, second.user_id) is None
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(PresenceHeartbeatRow)
                    .where(PresenceHeartbeatRow.user_id == second.user_id)
                )
                == 0
            )
            invite_state = session.scalar(
                select(FriendRoomInvitationRow.state).where(
                    FriendRoomInvitationRow.target_user_id == second.user_id
                )
            )
            assert invite_state == "ROOM_CHANGED"
            session.execute(delete(UserAccountRow).where(UserAccountRow.user_id == deleted_user_id))
        assert social.snapshot(first, now)["friends"] == []
        with sessions.begin() as session:
            assert session.get(UserAccountRow, deleted_user_id) is None
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(FriendshipRow)
                    .where(
                        (FriendshipRow.lower_user_id == deleted_user_id)
                        | (FriendshipRow.higher_user_id == deleted_user_id)
                    )
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(FriendRequestRow)
                    .where(
                        (FriendRequestRow.requester_user_id == deleted_user_id)
                        | (FriendRequestRow.target_user_id == deleted_user_id)
                    )
                )
                == 0
            )
    finally:
        engine.dispose()


def test_snapshot_recovers_received_pending_invite_despite_large_sent_history(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    try:
        with sessions.begin() as session:
            first = _principal(session, "First", now)
            second = _principal(session, "Second", now)
            _instance(session, key, now)
        wave = SqlAlchemyWaveService(sessions)
        first_room = wave.create(first, now)
        second_room = wave.create(second, now)
        stale_received_rooms = [wave.create(second, now) for _ in range(100)]
        received_id = uuid4()
        with sessions.begin() as session:
            for index in range(100):
                session.add(
                    FriendRoomInvitationRow(
                        invitation_id=uuid4(),
                        create_operation_id=uuid4(),
                        room_id=first_room.room_id,
                        room_epoch=1,
                        host_user_id=first.user_id,
                        host_device_id=first.device_id,
                        target_user_id=second.user_id,
                        state="CANCELLED",
                        expires_at=now + timedelta(minutes=10),
                        terminal_at=now,
                        terminal_reason="CANCELLED",
                        accepted_device_id=None,
                        accepting_session_id=None,
                        created_at=now + timedelta(seconds=index + 1),
                    )
                )
            for index, stale_room in enumerate(stale_received_rooms):
                session.add(
                    FriendRoomInvitationRow(
                        invitation_id=uuid4(),
                        create_operation_id=uuid4(),
                        room_id=stale_room.room_id,
                        room_epoch=1,
                        host_user_id=second.user_id,
                        host_device_id=second.device_id,
                        target_user_id=first.user_id,
                        state="PENDING",
                        expires_at=now - timedelta(seconds=1),
                        terminal_at=None,
                        terminal_reason=None,
                        accepted_device_id=None,
                        accepting_session_id=None,
                        created_at=now + timedelta(seconds=index + 1),
                    )
                )
            session.add(
                FriendRoomInvitationRow(
                    invitation_id=received_id,
                    create_operation_id=uuid4(),
                    room_id=second_room.room_id,
                    room_epoch=1,
                    host_user_id=second.user_id,
                    host_device_id=second.device_id,
                    target_user_id=first.user_id,
                    state="PENDING",
                    expires_at=now + timedelta(minutes=10),
                    terminal_at=None,
                    terminal_reason=None,
                    accepted_device_id=None,
                    accepting_session_id=None,
                    created_at=now,
                )
            )
        snapshot = SocialService(sessions, key).snapshot(first, now)
        received = snapshot["received_room_invitations"]
        assert isinstance(received, list)
        assert len(received) == 100
        assert received[0] == {
            "invitation_id": str(received_id),
            "state": "PENDING",
            "room_id": str(second_room.room_id),
            "room_epoch": 1,
            "expires_at": "2026-08-25T12:10:00Z",
        }
        sent = snapshot["sent_room_invitations"]
        assert isinstance(sent, list)
        assert len(sent) == 100
    finally:
        engine.dispose()
