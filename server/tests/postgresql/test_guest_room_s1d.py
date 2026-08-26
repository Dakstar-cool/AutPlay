"""Real PostgreSQL evidence for S1D guest capability isolation and P13 integration."""

from __future__ import annotations

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models import (
    ArtistCreditRow,
    DeviceRow,
    RecordingRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.wave import SqlAlchemyWaveService
from autplay.application.guest_room import GuestRoomError, GuestRoomService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.wave import Availability
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def _bearer(byte: int) -> str:
    return base64.urlsafe_b64encode(bytes([byte]) * 32).decode().rstrip("=")


def _principal(session: Session, name: str) -> Principal:
    user_id, device_id, session_id = uuid4(), uuid4(), uuid4()
    session.add(UserAccountRow(user_id=user_id, display_name=name, role="USER", status="ACTIVE"))
    session.flush()
    session.add(
        DeviceRow(
            device_id=device_id,
            user_id=user_id,
            device_name=name,
            platform="ANDROID",
            app_version="s1d",
        )
    )
    session.flush()
    session.add(
        UserSessionRow(
            session_id=session_id,
            user_id=user_id,
            device_id=device_id,
            refresh_token_hash=hashlib.sha256(session_id.bytes).digest(),
            issued_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            last_rotated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    session.flush()
    return Principal(user_id, device_id, session_id, AccountRole.USER)


def test_guest_hash_only_exact_replay_and_strict_start(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    document_bearer, session_bearer = _bearer(17), _bearer(23)
    try:
        with sessions.begin() as session:
            host = _principal(session, "s1d-host")
            credit = ArtistCreditRow(display_name="Guest Fixture", normalized_name="guest fixture")
            session.add(credit)
            session.flush()
            recording = RecordingRow(
                artist_credit_id=credit.artist_credit_id,
                title="Guest Track",
                normalized_title="guest track",
            )
            session.add(recording)
            session.flush()
            recording_id = recording.recording_id
        wave = SqlAlchemyWaveService(sessions)
        guest = GuestRoomService(sessions)
        room = wave.create(host, now)
        issue_operation, redeem_operation = uuid4(), uuid4()
        invitation = guest.issue(host, room.room_id, issue_operation, document_bearer, 900, 1, now)
        assert (
            guest.issue(host, room.room_id, issue_operation, document_bearer, 900, 1, now)
            == invitation
        )

        capability = guest.redeem(
            invitation_id=UUID(str(invitation["invitation_id"])),
            room_id=room.room_id,
            operation_id=redeem_operation,
            document_bearer=document_bearer,
            session_bearer=session_bearer,
            display_name="  Guest   One  ",
            source_rate_key=b"s" * 32,
            now=now,
        )
        replay = guest.redeem(
            invitation_id=UUID(str(invitation["invitation_id"])),
            room_id=room.room_id,
            operation_id=redeem_operation,
            document_bearer=document_bearer,
            session_bearer=session_bearer,
            display_name="Guest One",
            source_rate_key=b"s" * 32,
            now=now,
        )
        assert replay == capability
        assert capability["display_name"] == "Guest One"
        with pytest.raises(GuestRoomError, match="guest_unavailable"):
            guest.redeem(
                invitation_id=UUID(str(invitation["invitation_id"])),
                room_id=room.room_id,
                operation_id=uuid4(),
                document_bearer=document_bearer,
                session_bearer=_bearer(29),
                display_name="Other",
                source_rate_key=b"s" * 32,
                now=now,
            )

        with sessions.begin() as session:
            stored = session.execute(
                text(
                    "SELECT encode(i.document_secret_sha256,'hex'), "
                    "encode(s.access_secret_sha256,'hex'),i.state,i.consumed_uses "
                    "FROM social.guest_invitation i JOIN social.guest_session s "
                    "USING(invitation_id)"
                )
            ).one()
        assert stored == (
            hashlib.sha256(bytes([17]) * 32).hexdigest(),
            hashlib.sha256(bytes([23]) * 32).hexdigest(),
            "DEPLETED",
            1,
        )
        assert document_bearer not in repr(stored)
        assert session_bearer not in repr(stored)

        queued = wave.command(
            room.room_id, host, "QUEUE", "s1d-queue", b"q" * 32, 1, 0, recording_id, now
        )
        assert queued["sequence"] == 1
        entry = wave.snapshot(room.room_id, host, now).queue[0]
        wave.timing(room.room_id, host, 1, 40, 5, 10, now)
        wave.preflight(
            room.room_id,
            host,
            entry.queue_entry_id,
            recording_id,
            2,
            Availability.LOCAL,
            True,
            now,
        )
        guest.timing(session_bearer, room.room_id, 1, 45, 6, 12, now)
        guest.preflight(
            session_bearer,
            room.room_id,
            entry.queue_entry_id,
            recording_id,
            2,
            Availability.DOWNLOADED,
            True,
            now,
        )
        snapshot = guest.snapshot(session_bearer, room.room_id, now)
        assert snapshot.self_role == "GUEST"
        assert snapshot.queue[0].recording_id == recording_id
        assert (
            wave.start(room.room_id, host, entry.queue_entry_id, recording_id, 2, 1, now)["started"]
            is True
        )
    finally:
        engine.dispose()


def test_guest_revoke_and_room_close_fail_closed(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    # Keep expiry behind the database clock so explicit CLOSE must own terminal classification.
    now = datetime.now(UTC) - timedelta(hours=7)
    try:
        with sessions.begin() as session:
            host = _principal(session, "s1d-revoke-host")
        wave = SqlAlchemyWaveService(sessions)
        guest = GuestRoomService(sessions)
        room = wave.create(host, now)
        invitation = guest.issue(host, room.room_id, uuid4(), _bearer(31), 900, 2, now)
        invitation_id = UUID(str(invitation["invitation_id"]))
        guest.redeem(
            invitation_id=invitation_id,
            room_id=room.room_id,
            operation_id=uuid4(),
            document_bearer=_bearer(31),
            session_bearer=_bearer(32),
            display_name="Revoked Guest",
            source_rate_key=b"r" * 32,
            now=now,
        )
        guest.revoke(host, invitation_id, uuid4(), now)
        with pytest.raises(GuestRoomError, match="guest_revoked"):
            guest.snapshot(_bearer(32), room.room_id, now)

        second = guest.issue(host, room.room_id, uuid4(), _bearer(33), 900, 1, now)
        guest.redeem(
            invitation_id=UUID(str(second["invitation_id"])),
            room_id=room.room_id,
            operation_id=uuid4(),
            document_bearer=_bearer(33),
            session_bearer=_bearer(34),
            display_name="Closed Guest",
            source_rate_key=b"r" * 32,
            now=now,
        )
        wave.close(room.room_id, host, now)
        with pytest.raises(GuestRoomError, match="guest_unavailable"):
            guest.snapshot(_bearer(34), room.room_id, now)
        with sessions.begin() as session:
            states = session.execute(
                text(
                    "SELECT i.state,s.state FROM social.guest_invitation i "
                    "JOIN social.guest_session s USING(invitation_id) "
                    "WHERE i.invitation_id=:invitation"
                ),
                {"invitation": UUID(str(second["invitation_id"]))},
            ).one()
        assert states == ("ROOM_CLOSED", "ROOM_CLOSED")
    finally:
        engine.dispose()


def test_guest_cleanup_removes_terminal_evidence_after_retention(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 26, 14, tzinfo=UTC)
    try:
        with sessions.begin() as session:
            host = _principal(session, "s1d-cleanup-host")
        wave = SqlAlchemyWaveService(sessions)
        guest = GuestRoomService(sessions)
        room = wave.create(host, now)
        invitation = guest.issue(host, room.room_id, uuid4(), _bearer(41), 900, 1, now)
        invitation_id = UUID(str(invitation["invitation_id"]))
        guest.redeem(
            invitation_id=invitation_id,
            room_id=room.room_id,
            operation_id=uuid4(),
            document_bearer=_bearer(41),
            session_bearer=_bearer(42),
            display_name="Cleanup Guest",
            source_rate_key=b"c" * 32,
            now=now,
        )
        guest.revoke(host, invitation_id, uuid4(), now)

        counts = guest.cleanup(now + timedelta(days=31), limit=100)
        assert counts["receipts"] == 3
        assert counts["sessions"] == 1
        assert counts["invitations"] == 1
        with sessions.begin() as session:
            remaining = session.execute(
                text(
                    "SELECT (SELECT count(*) FROM social.guest_invitation),"
                    "(SELECT count(*) FROM social.guest_session),"
                    "(SELECT count(*) FROM social.guest_operation_receipt)"
                )
            ).one()
        assert remaining == (0, 0, 0)
    finally:
        engine.dispose()


def test_single_use_redemption_is_linearizable_under_concurrency(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 26, 15, tzinfo=UTC)
    try:
        with sessions.begin() as session:
            host = _principal(session, "s1d-concurrent-host")
        wave = SqlAlchemyWaveService(sessions)
        guest = GuestRoomService(sessions)
        room = wave.create(host, now)
        invitation = guest.issue(host, room.room_id, uuid4(), _bearer(51), 900, 1, now)
        invitation_id = UUID(str(invitation["invitation_id"]))
        barrier = Barrier(2)

        def redeem(index: int) -> str:
            barrier.wait(timeout=10)
            try:
                result = guest.redeem(
                    invitation_id=invitation_id,
                    room_id=room.room_id,
                    operation_id=uuid4(),
                    document_bearer=_bearer(51),
                    session_bearer=_bearer(52 + index),
                    display_name=f"Concurrent {index}",
                    source_rate_key=bytes([index + 1]) * 32,
                    now=now,
                )
                return str(result.get("state", "ACTIVE"))
            except GuestRoomError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(pool.map(redeem, (0, 1)))
        assert outcomes == ["ACTIVE", "guest_unavailable"]
        with sessions.begin() as session:
            count = session.execute(
                text("SELECT count(*) FROM social.guest_session WHERE invitation_id=:id"),
                {"id": invitation_id},
            ).scalar_one()
        assert count == 1
    finally:
        engine.dispose()


def test_guest_redemption_shares_the_eight_participant_capacity(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 26, 16, tzinfo=UTC)
    try:
        with sessions.begin() as session:
            host = _principal(session, "s1d-capacity-host")
        wave = SqlAlchemyWaveService(sessions)
        guest = GuestRoomService(sessions)
        room = wave.create(host, now)
        with sessions.begin() as session:
            members = [_principal(session, f"s1d-member-{index}") for index in range(7)]
            for member in members:
                session.execute(
                    text(
                        "INSERT INTO wave.member(room_id,user_id,device_id,role,status) "
                        "VALUES (:room,:user,:device,'MEMBER','JOINED')"
                    ),
                    {
                        "room": room.room_id,
                        "user": member.user_id,
                        "device": member.device_id,
                    },
                )
        invitation = guest.issue(host, room.room_id, uuid4(), _bearer(61), 900, 1, now)
        with pytest.raises(GuestRoomError, match="room_full"):
            guest.redeem(
                invitation_id=UUID(str(invitation["invitation_id"])),
                room_id=room.room_id,
                operation_id=uuid4(),
                document_bearer=_bearer(61),
                session_bearer=_bearer(62),
                display_name="Capacity Guest",
                source_rate_key=b"f" * 32,
                now=now,
            )
    finally:
        engine.dispose()


def test_multi_use_expiry_and_host_account_retirement_fail_closed(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 26, 17, tzinfo=UTC)
    try:
        with sessions.begin() as session:
            host = _principal(session, "s1d-lifecycle-host")
        wave = SqlAlchemyWaveService(sessions)
        guest = GuestRoomService(sessions)
        room = wave.create(host, now)
        invitation = guest.issue(host, room.room_id, uuid4(), _bearer(71), 900, 2, now)
        invitation_id = UUID(str(invitation["invitation_id"]))
        for index in (72, 73):
            guest.redeem(
                invitation_id=invitation_id,
                room_id=room.room_id,
                operation_id=uuid4(),
                document_bearer=_bearer(71),
                session_bearer=_bearer(index),
                display_name=f"Multi {index}",
                source_rate_key=bytes([index]) * 32,
                now=now,
            )
        with pytest.raises(GuestRoomError, match="guest_unavailable"):
            guest.redeem(
                invitation_id=invitation_id,
                room_id=room.room_id,
                operation_id=uuid4(),
                document_bearer=_bearer(71),
                session_bearer=_bearer(74),
                display_name="Third",
                source_rate_key=b"t" * 32,
                now=now,
            )
        assert guest.snapshot(_bearer(72), room.room_id, now).self_role == "GUEST"

        expired = guest.issue(host, room.room_id, uuid4(), _bearer(75), 60, 1, now)
        with pytest.raises(GuestRoomError, match="guest_expired"):
            guest.redeem(
                invitation_id=UUID(str(expired["invitation_id"])),
                room_id=room.room_id,
                operation_id=uuid4(),
                document_bearer=_bearer(75),
                session_bearer=_bearer(76),
                display_name="Expired",
                source_rate_key=b"e" * 32,
                now=now + timedelta(seconds=61),
            )

        with sessions.begin() as session:
            session.execute(
                text("UPDATE account.user_account SET status='DISABLED' WHERE user_id=:host"),
                {"host": host.user_id},
            )
        with pytest.raises(GuestRoomError, match="guest_revoked"):
            guest.snapshot(_bearer(72), room.room_id, now)
    finally:
        engine.dispose()


def test_host_device_and_issuing_session_revocation_retire_guest_access(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 26, 18, tzinfo=UTC)
    try:
        wave = SqlAlchemyWaveService(sessions)
        guest = GuestRoomService(sessions)
        for offset, authority in enumerate(("session", "device"), start=80):
            with sessions.begin() as session:
                host = _principal(session, f"s1d-{authority}-revoke")
            room = wave.create(host, now)
            invitation = guest.issue(host, room.room_id, uuid4(), _bearer(offset), 900, 1, now)
            access = _bearer(offset + 1)
            guest.redeem(
                invitation_id=UUID(str(invitation["invitation_id"])),
                room_id=room.room_id,
                operation_id=uuid4(),
                document_bearer=_bearer(offset),
                session_bearer=access,
                display_name=f"{authority} revoked",
                source_rate_key=bytes([offset]) * 32,
                now=now,
            )
            with sessions.begin() as session:
                if authority == "session":
                    session.execute(
                        text(
                            "UPDATE account.user_session SET revoked_at=:now "
                            "WHERE session_id=:session"
                        ),
                        {"now": now, "session": host.session_id},
                    )
                else:
                    session.execute(
                        text("UPDATE account.device SET revoked_at=:now WHERE device_id=:device"),
                        {"now": now, "device": host.device_id},
                    )
            with pytest.raises(GuestRoomError, match="guest_revoked"):
                guest.snapshot(access, room.room_id, now)
            with sessions.begin() as session:
                states = session.execute(
                    text(
                        "SELECT i.state,s.state FROM social.guest_invitation i "
                        "JOIN social.guest_session s USING(invitation_id) "
                        "WHERE i.invitation_id=:invitation"
                    ),
                    {"invitation": UUID(str(invitation["invitation_id"]))},
                ).one()
            assert states == ("REVOKED", "REVOKED")
    finally:
        engine.dispose()


def test_failed_redemption_guesses_consume_durable_source_rate_limit(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 26, 19, tzinfo=UTC)
    source_key = b"b" * 32
    try:
        with sessions.begin() as session:
            host = _principal(session, "s1d-rate-host")
        wave = SqlAlchemyWaveService(sessions)
        guest = GuestRoomService(sessions)
        room = wave.create(host, now)
        invitation = guest.issue(host, room.room_id, uuid4(), _bearer(90), 900, 1, now)
        invitation_id = UUID(str(invitation["invitation_id"]))
        for value in range(100, 130):
            with pytest.raises(GuestRoomError, match="guest_unavailable"):
                guest.redeem(
                    invitation_id=invitation_id,
                    room_id=room.room_id,
                    operation_id=uuid4(),
                    document_bearer=_bearer(value),
                    session_bearer=_bearer(91),
                    display_name="Rate limited",
                    source_rate_key=source_key,
                    now=now,
                )
        with pytest.raises(GuestRoomError, match="rate_limited"):
            guest.redeem(
                invitation_id=invitation_id,
                room_id=room.room_id,
                operation_id=uuid4(),
                document_bearer=_bearer(90),
                session_bearer=_bearer(91),
                display_name="Rate limited",
                source_rate_key=source_key,
                now=now,
            )
        with sessions.begin() as session:
            attempts = session.execute(
                text(
                    "SELECT attempt_count FROM social.guest_rate_window WHERE scope='REDEEM_SOURCE'"
                )
            ).scalar_one()
        assert attempts == 30
    finally:
        engine.dispose()
