"""Account-wide statistics stay private and include the caller's other devices."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from autplay.application.social import SocialError, SocialService
from autplay.domain.auth import AccountRole, Principal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def test_owner_statistics_include_both_devices_and_never_another_account(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    owner_id, other_id, device_a, device_b, other_device, session_id = (uuid4() for _ in range(6))
    try:
        with sessions.begin() as session:
            session.add_all(
                [
                    UserAccountRow(
                        user_id=owner_id, display_name="Owner", role="USER", status="ACTIVE"
                    ),
                    UserAccountRow(
                        user_id=other_id, display_name="Other", role="USER", status="ACTIVE"
                    ),
                ]
            )
            session.flush()
            for device, account in (
                (device_a, owner_id),
                (device_b, owner_id),
                (other_device, other_id),
            ):
                session.add(
                    DeviceRow(
                        device_id=device,
                        user_id=account,
                        device_name="Test phone",
                        platform="ANDROID",
                        app_version="test",
                    )
                )
            session.flush()
            session.add(
                UserSessionRow(
                    session_id=session_id,
                    user_id=owner_id,
                    device_id=device_a,
                    refresh_token_hash=uuid4().bytes + uuid4().bytes,
                    issued_at=now,
                    expires_at=now + timedelta(days=1),
                    last_rotated_at=now,
                    session_mode="V2",
                )
            )
            for account, device, played, title, started in (
                (owner_id, device_a, 10_000, "First", now - timedelta(days=1)),
                (owner_id, device_b, 20_000, "Second", now),
                (other_id, other_device, 999_000, "Private other history", now),
                (owner_id, device_a, 88_000, "Future event", now + timedelta(days=1)),
            ):
                ref = uuid4()
                session.execute(
                    text("""
                    INSERT INTO library.user_track_ref
                        (user_track_ref_id,user_id,raw_title,raw_artist)
                    VALUES (:ref,:account,:title,'Test artist')
                """),
                    {"ref": ref, "account": account, "title": title},
                )
                session.add(
                    TrackMetadataRow(
                        user_track_ref_id=ref,
                        state="READY",
                        document={"fields": {"genres": ["Rock", "Rock"]}},
                    )
                )
                session.execute(
                    text("""
                    INSERT INTO library.listening_event
                        (listening_event_id,user_id,device_id,user_track_ref_id,started_at,played_ms)
                    VALUES (:event,:account,:device,:ref,:started,:played)
                """),
                    {
                        "event": uuid4(),
                        "account": account,
                        "device": device,
                        "ref": ref,
                        "started": started,
                        "played": played,
                    },
                )
        principal = Principal(owner_id, device_a, session_id, AccountRole.USER)
        service = SocialService(sessions, None)
        result = service.owner_profile_statistics(principal, now)
        assert result["account_id"] == str(owner_id)
        assert result["listened_ms"] == 30_000
        tracks = cast(list[dict[str, object]], result["top_tracks"])
        artists = cast(list[dict[str, object]], result["top_artists"])
        assert [row["title"] for row in tracks] == ["Second", "First"]
        assert artists[0]["listened_ms"] == 30_000
        assert result["top_genres"] == [{"genre": "Rock", "listened_ms": 30_000}]
        with sessions.begin() as session:
            row = session.get(UserSessionRow, session_id)
            assert row is not None
            row.revoked_at = now
        with pytest.raises(SocialError, match="auth_attention_required"):
            service.owner_profile_statistics(principal, now)
    finally:
        engine.dispose()
