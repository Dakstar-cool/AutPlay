"""Release-watch owner isolation and exclusion against disposable PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models import (
    ArtistCreditNameRow,
    ArtistCreditRow,
    ArtistRow,
    DeviceRow,
    ListeningEventRow,
    RecordingRow,
    UserAccountRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.release_watch import ReleaseWatchRepository
from autplay.adapters.postgresql.watch_auto_download_consent import WatchAutoDownloadRepository
from autplay.application.discovery_automation import AUTO_IMPORT_CONFIRMATION

from .test_account_deletion import PairingHarness
from .test_account_deletion import base_pair as base_pair
from .test_account_deletion import pair as pair
from .test_privacy_purge import overdue


def _owner(session: Session, name: str) -> tuple[UUID, UUID]:
    account = UserAccountRow(display_name=name, role="USER", status="ACTIVE")
    session.add(account)
    session.flush()
    device = DeviceRow(
        user_id=account.user_id, device_name=name, platform="ANDROID", app_version="m11"
    )
    session.add(device)
    session.flush()
    return account.user_id, device.device_id


def _artist(session: Session, name: str) -> tuple[UUID, tuple[UUID, UUID]]:
    artist = ArtistRow(
        name=name, sort_name=name, normalized_name=name.casefold(), identity_status="ACTIVE"
    )
    credit = ArtistCreditRow(display_name=name, normalized_name=name.casefold())
    session.add_all((artist, credit))
    session.flush()
    session.add(
        ArtistCreditNameRow(
            artist_credit_id=credit.artist_credit_id,
            position=0,
            artist_id=artist.artist_id,
            credited_name=name,
            role="PRIMARY",
        )
    )
    recordings = (
        RecordingRow(
            artist_credit_id=credit.artist_credit_id,
            title=f"{name} one",
            normalized_title=f"{name.casefold()} one",
            identity_status="ACTIVE",
        ),
        RecordingRow(
            artist_credit_id=credit.artist_credit_id,
            title=f"{name} two",
            normalized_title=f"{name.casefold()} two",
            identity_status="ACTIVE",
        ),
    )
    session.add_all(recordings)
    session.flush()
    return artist.artist_id, (recordings[0].recording_id, recordings[1].recording_id)


def _plays(
    session: Session, owner_id: UUID, device_id: UUID, recordings: tuple[UUID, UUID], now: datetime
) -> None:
    for index, recording_id in enumerate((recordings[0], recordings[0], recordings[1])):
        ref = UserTrackRefRow(
            user_id=owner_id, resolution_status="UNRESOLVED", raw_title=f"fixture {index}"
        )
        session.add(ref)
        session.flush()
        session.add(
            ListeningEventRow(
                user_id=owner_id,
                device_id=device_id,
                user_track_ref_id=ref.user_track_ref_id,
                recording_id=recording_id,
                started_at=now - timedelta(hours=index + 1),
                played_ms=60000,
                track_duration_ms=120000,
                completion_ratio=0.5,
            )
        )
    session.flush()


def test_follow_with_zero_history_and_exclusion_survives_refresh(database_url: str) -> None:
    engine = create_engine(database_url)
    now = datetime.now(UTC)
    try:
        with Session(engine) as session:
            first_owner, first_device = _owner(session, "watch-owner-a")
            second_owner, second_device = _owner(session, "watch-owner-b")
            first_artist, first_recordings = _artist(session, "Watch A")
            second_artist, second_recordings = _artist(session, "Watch B")
            manual_artist, _ = _artist(session, "Manual only")
            _plays(session, first_owner, first_device, first_recordings, now)
            _plays(session, second_owner, second_device, second_recordings, now)

            repository = ReleaseWatchRepository(session)
            assert (
                repository.set_manual_follow(first_owner, manual_artist, follow=True, now=now) == 1
            )
            assert (
                repository.set_manual_follow(first_owner, manual_artist, follow=True, now=now) == 1
            )
            first = repository.refresh_favorites(first_owner, now=now)
            second = repository.refresh_favorites(second_owner, now=now)
            assert (first.generation, first.favorite_count) == (1, 1)
            assert (second.generation, second.favorite_count) == (1, 1)
            assert repository.refresh_favorites(first_owner, now=now + timedelta(hours=1)) == first
            first_reasons = {
                item.artist_id: item.reason for item in repository.list_effective(first_owner)
            }
            assert first_reasons == {
                first_artist: "LISTENING_HISTORY",
                manual_artist: "MANUAL_FOLLOW",
            }
            assert [item.artist_id for item in repository.list_effective(second_owner)] == [
                second_artist
            ]
            assert (
                repository.set_manual_follow(first_owner, first_artist, follow=False, now=now) == 1
            )
            assert repository.list_exclusions(first_owner) == [first_artist]
            later = now + timedelta(days=1, seconds=1)
            assert repository.refresh_favorites(first_owner, now=later).generation == 2
            assert (
                session.scalar(
                    text("""
                    SELECT projected_at FROM library.favorite_artist_projection
                    WHERE user_id=:owner AND artist_id=:artist
                """),
                    {"owner": first_owner, "artist": first_artist},
                )
                == now
            )
            assert [item.artist_id for item in repository.list_effective(first_owner)] == [
                manual_artist
            ]
            assert (
                repository.set_manual_follow(first_owner, first_artist, follow=True, now=later) == 2
            )
            assert {item.artist_id for item in repository.list_effective(first_owner)} == {
                first_artist,
                manual_artist,
            }
            assert (
                session.scalar(
                    text("SELECT count(*) FROM jobs.job WHERE user_id=:owner"),
                    {"owner": first_owner},
                )
                == 0
            )
            session.rollback()
    finally:
        engine.dispose()


def test_account_purge_removes_watch_choices_and_favorite_evidence(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    with Session(pair.engine) as session, session.begin():
        favorite_artist, recordings = _artist(session, "Purge favorite")
        manual_artist, _ = _artist(session, "Purge manual")
        _plays(session, pair.actor.user_id, pair.actor.device_id, recordings, now)
        repository = ReleaseWatchRepository(session)
        repository.set_manual_follow(pair.actor.user_id, manual_artist, follow=True, now=now)
        assert repository.refresh_favorites(pair.actor.user_id, now=now).favorite_count == 1
        session.execute(
            text("""
            INSERT INTO library.favorite_artist_refresh_retry
              (user_id,attempted_at,retry_after) VALUES(:owner,:now,:later)
        """),
            {"owner": pair.actor.user_id, "now": now, "later": now + timedelta(hours=24)},
        )
        assert {item.artist_id for item in repository.list_effective(pair.actor.user_id)} == {
            favorite_artist,
            manual_artist,
        }
        session.execute(text("UPDATE discovery.watch_auto_download_gate SET enabled=true"))
        assert (
            WatchAutoDownloadRepository(session)
            .set(
                pair.actor.user_id,
                enabled=True,
                expected_revision=0,
                confirmation_code=AUTO_IMPORT_CONFIRMATION,
                now=now,
            )
            .enabled
        )
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as connection:
        connection.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
        for table in (
            "library.artist_watch_choice",
            "library.favorite_artist_refresh",
            "library.favorite_artist_refresh_retry",
            "library.favorite_artist_projection",
            "discovery.watch_auto_download_consent_revision",
            "discovery.watch_auto_download_consent",
        ):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
