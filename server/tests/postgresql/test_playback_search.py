from uuid import UUID, uuid4

from autplay.adapters.postgresql.playback_search import (
    prepare_playback,
    search_playback,
    search_playback_page,
)
from autplay.adapters.postgresql.shared_catalog import enable_bridge_catalog
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.domain.auth import Principal
from autplay.domain.music_search import MusicSearchKind
from sqlalchemy import text

from .test_music_library import owner
from .test_track_metadata import setup
from .test_track_metadata_acquisition import publish_audio


def test_private_and_published_search_only_prepares_playback_on_request(database_url: str) -> None:
    engine, sessions, uploader, track, _ = setup(database_url)
    try:
        variant = publish_audio(sessions, uploader, track)
        listener = owner(sessions)
        with sessions.begin() as session:

            def search(actor: Principal) -> list[dict[str, object]]:
                return search_playback(session, actor, "Original", 10, MusicSearchKind.ALL)

            assert search(listener) == []
            assert search(uploader)[0]["user_track_ref_id"] == str(track)
            assert prepare_playback(session, listener.user_id, [uuid4()]) == []
            session.execute(
                text(
                    "UPDATE vault.recording_canonical_variant "
                    "SET policy_version='acquisition-verified-upload-v1'"
                )
            )
            assert enable_bridge_catalog(session) == 1
            assert len(search(uploader)) == 1
            (hit,) = search(listener)
            assert (hit["title"], hit["artist"], hit["duration_ms"]) == (
                "Original",
                "Artist",
                180000,
            )
            assert hit["user_track_ref_id"] != str(track)
            count_ref = text("SELECT count(*) FROM library.user_track_ref WHERE user_id=:owner")
            args = {"owner": listener.user_id}
            assert session.scalar(count_ref, args) == 0
            for _ in range(2):
                assert prepare_playback(
                    session, listener.user_id, [UUID(str(hit["catalog_id"]))]
                ) == [hit["user_track_ref_id"]]
            assert session.scalar(count_ref, args) == 1
            assert (
                PostgresVaultRuntime(session).resolve_owner_playback_variant(
                    listener.user_id, UUID(str(hit["user_track_ref_id"])), include_catalog=True
                )
                == variant
            )
            assert (
                session.scalar(
                    text("SELECT count(*) FROM library.library_entry WHERE user_id=:owner"), args
                )
                == 0
            )
            session.execute(
                text(
                    "UPDATE identity.external_reference SET deleted_at=now() "
                    "WHERE external_reference_id=:id"
                ),
                {"id": UUID(str(hit["catalog_id"]))},
            )
            assert search(listener) == []
            assert prepare_playback(session, listener.user_id, [UUID(str(hit["catalog_id"]))]) == []
    finally:
        engine.dispose()


def test_vault_pages_continue_past_one_hundred_without_overlaps(database_url: str) -> None:
    from datetime import UTC, datetime

    from sqlalchemy.orm import Session

    from .test_library_search import _track

    engine, _sessions, user, _, _ = setup(database_url)
    try:
        with Session(engine) as session:
            when = datetime.now(UTC)
            expected = {
                str(_track(session, user, title=f"Needle {i}", now=when)[1]) for i in range(128)
            }
            session.flush()
            first, cursor = search_playback_page(
                session, user, "Needle", 100, MusicSearchKind.TRACK
            )
            assert len(first) == 100 and cursor
            second, end = search_playback_page(
                session, user, "Needle", 100, MusicSearchKind.TRACK, cursor
            )
            assert len(second) == 28 and end is None
            assert {row["library_entry_id"] for row in first + second} == expected
    finally:
        engine.dispose()
