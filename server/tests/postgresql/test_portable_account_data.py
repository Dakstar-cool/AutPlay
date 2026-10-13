"""Whole account data transfers through real disposable PostgreSQL domains."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

import pytest
from autplay.adapters.postgresql.models import (
    ListeningEventRow,
    PlaylistEntryRow,
    PlaylistRow,
    PortableAccountRecordRow,
    UserTrackPreferenceRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.portable_taste import PortableTasteSignalRow
from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from autplay.adapters.postgresql.recommendations import SqlAlchemyRecommendationRuntime
from autplay.application.portable_account_data import (
    PortableAccountDataService,
    validate_portable_record,
)
from autplay.application.social import SocialService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import ProfilePairingError
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select, text

from .conftest import DatabaseHarness
from .identity_factory import (
    QueryRef,
    append_evaluation,
    append_review,
    force_deferred_constraints,
    insert_release_set,
    make_candidates,
)
from .test_portable_accounts import binding_body, passport, proof, services

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _register(
    database_url: str, key: ec.EllipticCurvePrivateKey, user_id: UUID
) -> tuple[Any, PortableAccountDataService, Principal]:
    engine, account, _ = services(database_url)
    command = binding_body(
        account.challenge("REGISTER", "test-source", document=passport(key, user_id)),
        ec.generate_private_key(ec.SECP256R1()),
    )
    result = account.complete(command, proof(key, command))
    actor = Principal(
        user_id, UUID(str(result["device_id"])), UUID(str(result["session_id"])), AccountRole.USER
    )
    return engine, PortableAccountDataService(account.sessions), actor


def _listening(
    event: UUID, track: UUID, now: datetime, aliases: list[UUID] | None = None
) -> dict[str, Any]:
    return {
        "record_id": str(event),
        "kind": "LISTENING",
        "document": {
            "version": 1,
            "updated_at_ms": int(now.timestamp() * 1000),
            "event_ids": [str(v) for v in (aliases or [event])],
            "track_key": str(track),
            "title": "Portable title",
            "artist": "Portable artist",
            "genres": ["Rock"],
            "started_at_ms": int(now.timestamp() * 1000),
            "played_ms": 45000,
            "duration_ms": 180000,
            "excluded_from_taste": False,
            "event_origin": "ORGANIC",
            "context": "GENERAL",
        },
    }


def test_legacy_account_moves_between_servers_with_stable_domain_copies(
    database_harness: DatabaseHarness,
    migrated_template_database: str,
    database_url: str,
) -> None:
    target_name = database_harness.create_database(template=migrated_template_database)
    root, owner = ec.generate_private_key(ec.SECP256R1()), uuid4()
    source_engine, source, actor = _register(database_url, root, owner)
    target_engine = None
    try:
        target_engine, target, target_actor = _register(
            database_harness.database_url(target_name), root, owner
        )
        now = datetime.now(UTC) - timedelta(days=1)
        track, event, playlist, entry, taste = (uuid4() for _ in range(5))
        with source.sessions.begin() as session:
            session.add(
                UserTrackRefRow(
                    user_track_ref_id=track,
                    user_id=owner,
                    raw_title="Legacy title",
                    raw_artist="Legacy artist",
                )
            )
            session.add(
                PlaylistRow(
                    playlist_id=playlist,
                    owner_user_id=owner,
                    name="Legacy list",
                    visibility="PUBLIC",
                    updated_at=now,
                )
            )
            session.flush()
            session.add_all(
                [
                    TrackMetadataRow(
                        user_track_ref_id=track,
                        state="READY",
                        document={
                            "fields": {
                                "title": "Metadata title",
                                "artist": "Metadata artist",
                                "genres": ["Rock"],
                            },
                            "raw_source": {"private_authority": "omitted"},
                        },
                    ),
                    ListeningEventRow(
                        listening_event_id=event,
                        user_id=owner,
                        device_id=actor.device_id,
                        user_track_ref_id=track,
                        started_at=now,
                        created_at=now,
                        played_ms=45000,
                    ),
                    UserTrackPreferenceRow(
                        user_track_ref_id=track, preference="LIKED", rating=5, updated_at=now
                    ),
                    PlaylistEntryRow(
                        playlist_entry_id=entry,
                        playlist_id=playlist,
                        user_track_ref_id=track,
                        added_by_user_id=owner,
                        position_key="A",
                        updated_at=now,
                    ),
                    PortableTasteSignalRow(
                        user_id=owner,
                        record_id=taste,
                        user_track_ref_id=track,
                        interaction="SKIP",
                        weight=-500,
                        occurred_at_ms=int(now.timestamp() * 1000),
                        updated_at=now,
                    ),
                ]
            )
        exports: list[dict[str, Any]] = []
        for name in ("history", "preferences", "playlists", "playlist_entries", "tastes"):
            page = source.get(actor, name, EPOCH, UUID(int=0))
            items = cast(list[dict[str, Any]], page["records"])
            assert len(items) == 1
            for record in items:
                validate_portable_record(record)
                exports.append(record)
        # Import the entry first: no dependency on batching or source download order.
        target.put(target_actor, {"account_id": str(owner), "records": list(reversed(exports))})
        before = target.get(target_actor, "records", EPOCH, UUID(int=0))
        target.put(target_actor, {"account_id": str(owner), "records": exports})
        assert target.get(target_actor, "records", EPOCH, UUID(int=0)) == before
        with target.sessions() as session:
            assert session.scalar(select(func.count()).select_from(ListeningEventRow)) == 1
            assert session.scalar(select(func.count()).select_from(UserTrackPreferenceRow)) == 1
            assert session.scalar(select(func.count()).select_from(PortableTasteSignalRow)) == 1
            applied_playlist = session.get(PlaylistRow, playlist)
            applied_entry = session.get(PlaylistEntryRow, entry)
            assert applied_playlist is not None and applied_playlist.name == "Legacy list"
            assert applied_playlist.visibility == "PRIVATE"
            assert applied_entry is not None and applied_entry.removed_at is None
            applied_track = session.get(UserTrackRefRow, applied_entry.user_track_ref_id)
            assert applied_track is not None and applied_track.recording_id is None
            assert applied_track.resolution_status == "UNRESOLVED"
            assert session.scalar(text("SELECT count(*) FROM vault.acquisition_record")) == 0
        for name in ("history", "preferences", "playlists", "playlist_entries", "tastes"):
            moved = cast(
                list[dict[str, Any]], target.get(target_actor, name, EPOCH, UUID(int=0))["records"]
            )
            original = cast(
                list[dict[str, Any]], source.get(actor, name, EPOCH, UUID(int=0))["records"]
            )
            assert moved == original
        summary = SocialService(target.sessions, None).owner_profile_statistics(
            target_actor, datetime.now(UTC)
        )
        assert summary["listened_ms"] == 45000
        assert summary["top_genres"] == [{"genre": "Rock", "listened_ms": 45000}]
    finally:
        source_engine.dispose()
        if target_engine is not None:
            target_engine.dispose()
        database_harness.drop_database(target_name)


def test_alias_bridge_and_clock_rollback_do_not_lose_or_double_count_records(
    database_url: str,
) -> None:
    engine, data, actor = _register(database_url, ec.generate_private_key(ec.SECP256R1()), uuid4())
    now = datetime.now(UTC) - timedelta(days=1)
    high, low, track = UUID(int=400), UUID(int=100), uuid4()
    try:
        data.put(
            actor, {"account_id": str(actor.user_id), "records": [_listening(high, track, now)]}
        )
        data.put(
            actor, {"account_id": str(actor.user_id), "records": [_listening(low, track, now)]}
        )
        with data.sessions.begin() as session:
            future_clock = datetime.now(UTC) + timedelta(days=1)
            session.execute(
                text("UPDATE account.portable_record SET updated_at=:time"), {"time": future_clock}
            )
        bridge = _listening(high, track, now, [high, low])
        accepted = data.put(actor, {"account_id": str(actor.user_id), "records": [bridge]})
        assert accepted["accepted"] == [str(low)]
        page = data.get(actor, "records", future_clock, high)
        records = cast(list[dict[str, Any]], page["records"])
        assert len(records) == 1 and records[0]["record_id"] == str(low)
        before = data.get(actor, "records", EPOCH, UUID(int=0))
        data.put(actor, {"account_id": str(actor.user_id), "records": [bridge]})
        assert data.get(actor, "records", EPOCH, UUID(int=0)) == before
        variant = _listening(low, track, now, [low, high])
        variant["document"].update(artist="Z artist", context="CYCLING", event_origin="PLAYLIST")
        data.put(actor, {"account_id": str(actor.user_id), "records": [variant]})
        converged = data.get(actor, "records", EPOCH, UUID(int=0))
        data.put(actor, {"account_id": str(actor.user_id), "records": [bridge]})
        data.put(actor, {"account_id": str(actor.user_id), "records": [variant]})
        assert data.get(actor, "records", EPOCH, UUID(int=0)) == converged
        merged = cast(list[dict[str, Any]], converged["records"])[0]["document"]
        assert merged["context"] == "CYCLING" and merged["event_origin"] == "ORGANIC"
        with data.sessions() as session:
            assert session.scalar(select(func.count()).select_from(ListeningEventRow)) == 2
            assert session.scalar(select(func.count()).select_from(PortableAccountRecordRow)) == 1
        summary = SocialService(data.sessions, None).owner_profile_statistics(
            actor, datetime.now(UTC)
        )
        assert summary["listened_ms"] == 45000
        assert cast(list[dict[str, Any]], summary["top_tracks"])[0]["play_session_count"] == 1
        bad = _listening(high, track, now, [high, low])
        bad["document"]["played_ms"] = 45001
        with pytest.raises(ProfilePairingError, match="account_record_identity_conflict"):
            data.put(actor, {"account_id": str(actor.user_id), "records": [bad]})
        with pytest.raises(ProfilePairingError, match="account_identity_conflict"):
            data.put(actor, {"account_id": str(uuid4()), "records": [bridge]})
    finally:
        engine.dispose()


def test_portable_taste_enters_recommendations_only_after_owner_identity_and_availability(
    database_harness: DatabaseHarness,
    database_name: str,
    database_url: str,
) -> None:
    engine, data, actor = _register(database_url, ec.generate_private_key(ec.SECP256R1()), uuid4())
    now = datetime.now(UTC) - timedelta(days=1)
    track_key, taste_id = uuid4(), uuid4()
    record: dict[str, Any] = {
        "record_id": str(taste_id),
        "kind": "TASTE",
        "document": {
            "version": 1,
            "updated_at_ms": int(now.timestamp() * 1000),
            "track_key": str(track_key),
            "interaction": "LIKE",
            "weight": 1000,
        },
    }
    try:
        data.put(actor, {"account_id": str(actor.user_id), "records": [record]})
        runtime = SqlAlchemyRecommendationRuntime(data.sessions)
        assert (
            runtime.capture(
                actor.user_id, retained_until=datetime.now(UTC) + timedelta(days=1)
            ).tracks
            == ()
        )
        track_id = uuid5(actor.user_id, "autplay:portable-track:v1:" + str(track_key))
        with database_harness.connect(database_name) as connection:
            releases = insert_release_set(connection)
            candidates = make_candidates(connection, releases, 1)
            recording = candidates[0].recording_id
            predecessor = append_evaluation(
                connection,
                QueryRef("USER_TRACK_REF", "user_track_ref_id", track_id, actor.user_id),
                releases,
                candidates,
                state="REVIEW_REQUIRED",
            )
            append_review(
                connection,
                predecessor,
                action="ACCEPT",
                actor_user_id=actor.user_id,
                project=True,
            )
            force_deferred_constraints(connection)
            connection.execute(
                """INSERT INTO library.library_entry
                (user_id,user_track_ref_id,source,availability_status)
                VALUES (%s,%s,'LOCAL','LOCAL')""",
                (actor.user_id, track_id),
            )
            connection.commit()
        snapshot = runtime.capture(
            actor.user_id, retained_until=datetime.now(UTC) + timedelta(days=1)
        )
        assert len(snapshot.tracks) == 1
        assert snapshot.tracks[0].recording_id == recording
        assert snapshot.tracks[0].preference == "LIKED"
        assert snapshot.tracks[0].organic_play_count == 0  # Taste is never fake history.
        assert snapshot.tracks[0].authorized is True
        newer = {
            **record,
            "document": {
                **record["document"],
                "updated_at_ms": int((now + timedelta(hours=1)).timestamp() * 1000),
                "interaction": "EXCLUDE",
                "weight": 0,
            },
        }
        data.put(actor, {"account_id": str(actor.user_id), "records": [newer]})
        assert (
            runtime.capture(actor.user_id, retained_until=datetime.now(UTC) + timedelta(days=1))
            .tracks[0]
            .excluded
        )
    finally:
        engine.dispose()
