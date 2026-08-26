"""Real PostgreSQL evidence for S2 private profile statistics sharing."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models import (
    DeviceRow,
    FriendshipRow,
    ProfileStatisticsSettingsRow,
    UserAccountRow,
    UserBlockRow,
    UserSessionRow,
)
from autplay.application.social import SocialError, SocialService
from autplay.domain.auth import AccountRole, Principal
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker


def _principal(
    session: Session, name: str, now: datetime, *, user_id: UUID | None = None
) -> Principal:
    user_id = user_id or uuid4()
    device_id, session_id = uuid4(), uuid4()
    session.add(UserAccountRow(user_id=user_id, display_name=name, role="USER", status="ACTIVE"))
    session.flush()
    session.add(
        DeviceRow(
            device_id=device_id,
            user_id=user_id,
            device_name=name,
            platform="ANDROID",
            app_version="s2",
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


def _friend(session: Session, first: UUID, second: UUID, now: datetime) -> None:
    lower, higher = sorted((first, second), key=lambda value: value.bytes)
    session.add(FriendshipRow(lower_user_id=lower, higher_user_id=higher, created_at=now))


def _set_visibility(
    service: SocialService,
    principal: Principal,
    *,
    enabled: bool,
    expected_revision: int,
    now: datetime,
) -> dict[str, object]:
    return service.set_profile_statistics_settings(
        principal,
        {
            "operation_id": str(uuid4()),
            "expected_revision": expected_revision,
            "friends_can_view_statistics": enabled,
        },
        now,
    )


def test_profile_statistics_policy_default_revision_and_stale_on_protection(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    try:
        with sessions.begin() as session:
            owner = _principal(session, "Statistics owner", now)
        service = SocialService(sessions, None)

        assert service.get_profile_statistics_settings(owner, now) == {
            "schema_version": 1,
            "friends_can_view_statistics": False,
            "revision": 0,
        }
        with sessions.begin() as session:
            assert session.get(ProfileStatisticsSettingsRow, owner.user_id) is None

        operation_id = uuid4()
        enable_body: dict[str, object] = {
            "operation_id": str(operation_id),
            "expected_revision": 0,
            "friends_can_view_statistics": True,
        }
        enabled = service.set_profile_statistics_settings(owner, enable_body, now)
        assert enabled == {
            "schema_version": 1,
            "operation_id": str(operation_id),
            "friends_can_view_statistics": True,
            "revision": 1,
        }
        assert service.set_profile_statistics_settings(owner, enable_body, now) == enabled
        with pytest.raises(SocialError, match="operation_conflict"):
            service.set_profile_statistics_settings(
                owner,
                {**enable_body, "friends_can_view_statistics": False},
                now,
            )

        disable_id = uuid4()
        disable_body: dict[str, object] = {
            "operation_id": str(disable_id),
            "expected_revision": 0,
            "friends_can_view_statistics": False,
        }
        disabled = service.set_profile_statistics_settings(owner, disable_body, now)
        assert disabled["revision"] == 2
        assert disabled["friends_can_view_statistics"] is False
        assert service.set_profile_statistics_settings(owner, disable_body, now) == disabled

        with pytest.raises(SocialError, match="operation_conflict"):
            service.set_profile_statistics_settings(
                owner,
                {
                    "operation_id": str(uuid4()),
                    "expected_revision": 1,
                    "friends_can_view_statistics": True,
                },
                now,
            )

        disabled_again = _set_visibility(
            service, owner, enabled=False, expected_revision=0, now=now
        )
        assert disabled_again["revision"] == 3
        assert service.get_profile_statistics_settings(owner, now)["revision"] == 3
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("action", "expected_state"),
    (("REMOVE_FRIEND", "REMOVED"), ("BLOCK_USER", "BLOCKED")),
)
def test_friend_statistics_read_and_relationship_revocation_do_not_deadlock(
    database_url: str, action: str, expected_state: str
) -> None:
    """A reader holding the lower account cannot deadlock a high-UUID actor command."""
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    reader_has_lower_account = Event()
    command_reached_session_validation = Event()

    class CoordinatedReader(SocialService):
        def _lock_account_rows(
            self, session: Session, first: UUID, second: UUID
        ) -> dict[UUID, UserAccountRow]:
            ordered = sorted({first, second}, key=lambda value: value.bytes)
            first_row = session.get(UserAccountRow, ordered[0], with_for_update=True)
            assert first_row is not None
            reader_has_lower_account.set()
            # The former command order reached actor-session validation while holding
            # only the high account. The current one instead waits on this lock.
            command_reached_session_validation.wait(timeout=2)
            second_row = (
                first_row
                if len(ordered) == 1
                else session.get(UserAccountRow, ordered[1], with_for_update=True)
            )
            assert second_row is not None
            return {first_row.user_id: first_row, second_row.user_id: second_row}

    class CoordinatedCommand(SocialService):
        def _active_principal(
            self, session: Session, principal: Principal, instant: datetime
        ) -> UserSessionRow:
            result = super()._active_principal(session, principal, instant)
            command_reached_session_validation.set()
            return result

    try:
        with sessions.begin() as session:
            # The command actor must sort after the statistics viewer to reproduce
            # the historical lock-order inversion deterministically.
            viewer = _principal(session, "Lower viewer", now, user_id=UUID(int=1))
            owner = _principal(session, "Higher owner", now, user_id=UUID(int=2))
            _friend(session, viewer.user_id, owner.user_id, now)
        owner_service = SocialService(sessions, None)
        _set_visibility(owner_service, owner, enabled=True, expected_revision=0, now=now)
        reader_service = CoordinatedReader(sessions, None)
        command_service = CoordinatedCommand(sessions, None)

        with ThreadPoolExecutor(max_workers=2) as executor:
            read = executor.submit(
                reader_service.friend_profile_statistics, viewer, owner.user_id, now
            )
            assert reader_has_lower_account.wait(timeout=2)
            command = executor.submit(
                command_service.command,
                owner,
                {
                    "operation_id": str(uuid4()),
                    "action": action,
                    "target_account_id": str(viewer.user_id),
                },
                now,
            )
            projection = read.result(timeout=8)
            result = command.result(timeout=8)

        assert projection["schema_version"] == 1
        assert result["state"] == expected_state
        with pytest.raises(SocialError, match="profile_statistics_unavailable"):
            owner_service.friend_profile_statistics(viewer, owner.user_id, now)
    finally:
        engine.dispose()


def test_friend_projection_cutoffs_allowlist_and_immediate_revocation(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    try:
        with sessions.begin() as session:
            viewer = _principal(session, "Viewer", now)
            owner = _principal(session, "Owner", now)
            _friend(session, viewer.user_id, owner.user_id, now)
            artist_credit_id = session.execute(
                text(
                    "INSERT INTO catalog.artist_credit (display_name,normalized_name) "
                    "VALUES ('S2 artist','s2 artist') RETURNING artist_credit_id"
                )
            ).scalar_one()
            recording_id = session.execute(
                text(
                    "INSERT INTO catalog.recording (artist_credit_id,title,normalized_title) "
                    "VALUES (:credit,'S2 track','s2 track') RETURNING recording_id"
                ),
                {"credit": artist_credit_id},
            ).scalar_one()
            ref_recording_a = session.execute(
                text(
                    "INSERT INTO library.user_track_ref (user_id,raw_title) "
                    "VALUES (:user_id,'recording-a') RETURNING user_track_ref_id"
                ),
                {"user_id": owner.user_id},
            ).scalar_one()
            ref_recording_b = session.execute(
                text(
                    "INSERT INTO library.user_track_ref (user_id,raw_title) "
                    "VALUES (:user_id,'recording-b') RETURNING user_track_ref_id"
                ),
                {"user_id": owner.user_id},
            ).scalar_one()
            ref_unresolved = session.execute(
                text(
                    "INSERT INTO library.user_track_ref (user_id,raw_title) "
                    "VALUES (:user_id,'unresolved') RETURNING user_track_ref_id"
                ),
                {"user_id": owner.user_id},
            ).scalar_one()
            ref_old = session.execute(
                text(
                    "INSERT INTO library.user_track_ref (user_id,raw_title) "
                    "VALUES (:user_id,'old') RETURNING user_track_ref_id"
                ),
                {"user_id": owner.user_id},
            ).scalar_one()

            def event(
                started_at: datetime,
                played_ms: int,
                user_track_ref_id: UUID,
                *,
                recording: UUID | None = None,
                excluded: bool = False,
            ) -> None:
                session.execute(
                    text(
                        "INSERT INTO library.listening_event "
                        "(user_id,device_id,user_track_ref_id,recording_id,started_at,played_ms,"
                        "excluded_from_taste) VALUES "
                        "(:user_id,:device_id,:ref,:recording,:started_at,:played_ms,:excluded)"
                    ),
                    {
                        "user_id": owner.user_id,
                        "device_id": owner.device_id,
                        "ref": user_track_ref_id,
                        "recording": recording,
                        "started_at": started_at,
                        "played_ms": played_ms,
                        "excluded": excluded,
                    },
                )

            event(datetime(2026, 8, 18, tzinfo=UTC), 100, ref_recording_a, recording=recording_id)
            event(datetime(2026, 8, 20, tzinfo=UTC), 200, ref_recording_b, recording=recording_id)
            event(datetime(2026, 8, 17, 23, 59, tzinfo=UTC), 300, ref_unresolved)
            event(datetime(2026, 7, 26, tzinfo=UTC), 400, ref_unresolved)
            event(datetime(2026, 7, 25, 23, 59, tzinfo=UTC), 500, ref_old)
            event(datetime(2026, 8, 25, tzinfo=UTC), 600, ref_old)
            event(datetime(2026, 8, 20, tzinfo=UTC), 700, ref_old, excluded=True)
            event(datetime(2026, 8, 20, tzinfo=UTC), 0, ref_old)

        service = SocialService(sessions, None)
        assert (
            _set_visibility(service, owner, enabled=True, expected_revision=0, now=now)["revision"]
            == 1
        )

        projection = service.friend_profile_statistics(viewer, owner.user_id, now)
        assert projection == {
            "schema_version": 1,
            "through_utc_date": "2026-08-24",
            "windows": [
                {
                    "window": "LAST_7_COMPLETE_DAYS",
                    "play_session_count": 2,
                    "listened_ms": 300,
                    "unique_track_count": 1,
                },
                {
                    "window": "LAST_30_COMPLETE_DAYS",
                    "play_session_count": 4,
                    "listened_ms": 1000,
                    "unique_track_count": 2,
                },
                {
                    "window": "LAST_365_COMPLETE_DAYS",
                    "play_session_count": 5,
                    "listened_ms": 1500,
                    "unique_track_count": 3,
                },
            ],
        }
        assert len(json.dumps(projection, separators=(",", ":")).encode()) < 2048
        assert set(projection) == {"schema_version", "through_utc_date", "windows"}
        windows = cast(list[dict[str, object]], projection["windows"])
        assert all(
            set(window)
            == {
                "window",
                "play_session_count",
                "listened_ms",
                "unique_track_count",
            }
            for window in windows
        )

        _set_visibility(service, owner, enabled=False, expected_revision=0, now=now)
        with pytest.raises(SocialError, match="profile_statistics_unavailable"):
            service.friend_profile_statistics(viewer, owner.user_id, now)

        _set_visibility(service, owner, enabled=True, expected_revision=2, now=now)
        with sessions.begin() as session:
            lower, higher = sorted((viewer.user_id, owner.user_id), key=lambda value: value.bytes)
            friendship = session.get(
                FriendshipRow, {"lower_user_id": lower, "higher_user_id": higher}
            )
            assert friendship is not None
            session.delete(friendship)
        with pytest.raises(SocialError, match="profile_statistics_unavailable"):
            service.friend_profile_statistics(viewer, owner.user_id, now)

        with sessions.begin() as session:
            _friend(session, viewer.user_id, owner.user_id, now)
            session.add(
                UserBlockRow(
                    blocker_user_id=owner.user_id,
                    blocked_user_id=viewer.user_id,
                    blocked_at=now,
                    unblocked_at=None,
                )
            )
        with pytest.raises(SocialError, match="profile_statistics_unavailable"):
            service.friend_profile_statistics(viewer, owner.user_id, now)

        with sessions.begin() as session:
            session.execute(delete(UserBlockRow))
            session.add(
                UserBlockRow(
                    blocker_user_id=viewer.user_id,
                    blocked_user_id=owner.user_id,
                    blocked_at=now,
                    unblocked_at=None,
                )
            )
        with pytest.raises(SocialError, match="profile_statistics_unavailable"):
            service.friend_profile_statistics(viewer, owner.user_id, now)

        with sessions.begin() as session:
            session.execute(delete(UserBlockRow))
            account = session.get(UserAccountRow, owner.user_id)
            assert account is not None
            account.status = "DISABLED"
        with sessions.begin() as session:
            assert session.get(ProfileStatisticsSettingsRow, owner.user_id) is None
        with pytest.raises(SocialError, match="profile_statistics_unavailable"):
            service.friend_profile_statistics(viewer, owner.user_id, now)

        removable_id = uuid4()
        with sessions.begin() as session:
            session.add(
                UserAccountRow(
                    user_id=removable_id,
                    display_name="Removable",
                    role="USER",
                    status="ACTIVE",
                )
            )
            session.flush()
            session.add(
                ProfileStatisticsSettingsRow(
                    user_id=removable_id,
                    friends_can_view_statistics=True,
                    revision=1,
                    updated_at=now,
                )
            )
        with sessions.begin() as session:
            removable = session.get(UserAccountRow, removable_id)
            assert removable is not None
            session.delete(removable)
        with sessions.begin() as session:
            assert session.get(ProfileStatisticsSettingsRow, removable_id) is None
            assert (
                session.scalar(
                    select(UserAccountRow.user_id).where(UserAccountRow.user_id == removable_id)
                )
                is None
            )
    finally:
        engine.dispose()
