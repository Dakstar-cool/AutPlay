"""Focused real PostgreSQL evidence for the durable Wave lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from autplay.adapters.postgresql.models import (
    ArtistCreditRow,
    DeviceRow,
    RecordingRow,
    UserAccountRow,
)
from autplay.adapters.postgresql.wave import SqlAlchemyWaveService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.wave import Availability, WaveConflict, WaveForbidden
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def _principal(session: Session, name: str) -> Principal:
    user_id, device_id = uuid4(), uuid4()
    session.add(UserAccountRow(user_id=user_id, display_name=name, role="USER", status="ACTIVE"))
    session.flush()
    session.add(
        DeviceRow(
            device_id=device_id,
            user_id=user_id,
            device_name=name,
            platform="ANDROID",
            app_version="p13",
        )
    )
    session.flush()
    return Principal(user_id, device_id, uuid4(), AccountRole.USER)


def test_wave_code_acl_and_idempotency(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 17, tzinfo=UTC)
    try:
        with sessions.begin() as session:
            owner, member, stranger = (
                _principal(session, "wave-owner"),
                _principal(session, "wave-member"),
                _principal(session, "wave-stranger"),
            )
            credit = ArtistCreditRow(display_name="Wave Fixture", normalized_name="wave fixture")
            session.add(credit)
            session.flush()
            recording = RecordingRow(
                artist_credit_id=credit.artist_credit_id,
                title="Wave Recording",
                normalized_title="wave recording",
            )
            session.add(recording)
            session.flush()
            recording_id = recording.recording_id
        service = SqlAlchemyWaveService(sessions)
        room = service.create(owner, now, (member.user_id,))
        with pytest.raises(WaveForbidden):
            service.join(room.code, stranger, now)
        service.join(room.code, member, now)
        assert service.snapshot(room.room_id, owner, now).host_user_id == owner.user_id
        queued = service.command(
            room.room_id,
            owner,
            "QUEUE",
            "queue-1",
            b"q" * 32,
            1,
            0,
            recording_id,
            now,
        )
        assert queued["sequence"] == 1
        assert (
            service.command(
                room.room_id,
                owner,
                "QUEUE",
                "queue-1",
                b"q" * 32,
                1,
                0,
                recording_id,
                now,
            )["idempotent"]
            is True
        )
        entry = service.snapshot(room.room_id, owner, now).queue[0]
        for principal in (owner, member):
            service.timing(room.room_id, principal, 1, 60, 25, 30, now)
        service.preflight(
            room.room_id,
            owner,
            entry.queue_entry_id,
            recording_id,
            2,
            Availability.LOCAL,
            True,
            now,
        )
        service.preflight(
            room.room_id,
            member,
            entry.queue_entry_id,
            recording_id,
            2,
            Availability.UNAVAILABLE,
            False,
            now,
        )
        assert (
            service.start(
                room.room_id,
                owner,
                entry.queue_entry_id,
                recording_id,
                2,
                1,
                now,
            )["started"]
            is False
        )
        for principal, availability in (
            (owner, Availability.LOCAL),
            (member, Availability.VAULT_STREAMABLE),
        ):
            service.timing(room.room_id, principal, 2, 60, 25, 30, now)
            service.preflight(
                room.room_id,
                principal,
                entry.queue_entry_id,
                recording_id,
                2,
                availability,
                True,
                now,
            )
        started = service.start(
            room.room_id,
            owner,
            entry.queue_entry_id,
            recording_id,
            2,
            2,
            now,
        )
        assert started["started"] is True
        assert started["effective_at"] is not None

        service.transfer_host(room.room_id, owner, member.device_id, now)
        with pytest.raises(WaveForbidden):
            service.close(room.room_id, owner, now)
        service.close(room.room_id, member, now)
        with sessions.begin() as session:
            lifecycle = list(
                session.execute(
                    text(
                        "SELECT command_sequence,command_kind FROM wave.command "
                        "WHERE room_id=:room AND command_kind IN ('TRANSFER','CLOSE') "
                        "ORDER BY command_sequence"
                    ),
                    {"room": room.room_id},
                ).tuples()
            )
        assert lifecycle == [(4, "TRANSFER"), (5, "CLOSE")]
    finally:
        engine.dispose()


def test_wave_host_loss_leave_policy_and_expiry(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 17, tzinfo=UTC)
    try:
        with sessions.begin() as session:
            owner = _principal(session, "wave-recovery-owner")
            member = _principal(session, "wave-recovery-member")
        service = SqlAlchemyWaveService(sessions)
        room = service.create(owner, now, (member.user_id,))
        service.join(room.code, member, now)
        with pytest.raises(WaveConflict):
            service.leave(room.room_id, owner, now)

        with sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE wave.member SET last_present_at=:stale "
                    "WHERE room_id=:room AND device_id=:device"
                ),
                {
                    "stale": now,
                    "room": room.room_id,
                    "device": owner.device_id,
                },
            )
        service.snapshot(room.room_id, member, now + timedelta(seconds=31))
        recovered = service.snapshot(room.room_id, member, now + timedelta(seconds=62))
        assert recovered.host_user_id == member.user_id
        assert recovered.room_epoch == 2

        expiring = service.create(owner, now)
        assert service.expire_due(now + timedelta(hours=6, seconds=1)) >= 1
        with sessions.begin() as session:
            terminal = session.execute(
                text("SELECT state,command_sequence FROM wave.room WHERE room_id=:room"),
                {"room": expiring.room_id},
            ).one()
            command = session.execute(
                text(
                    "SELECT command_kind FROM wave.command WHERE room_id=:room "
                    "ORDER BY command_sequence DESC LIMIT 1"
                ),
                {"room": expiring.room_id},
            ).scalar_one()
        assert terminal == ("EXPIRED", 1)
        assert command == "EXPIRE"
    finally:
        engine.dispose()
