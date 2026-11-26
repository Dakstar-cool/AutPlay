from uuid import uuid4

from autplay.adapters.postgresql.catalog_pages import CatalogPageCache
from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from sqlalchemy import text

from .test_track_metadata import setup
from .test_track_metadata_acquisition import publish_audio


def test_album_positions_follow_live_owner_audio(database_url: str) -> None:
    engine, sessions, owner, ref, _ = setup(database_url)
    try:
        variant = publish_audio(sessions, owner, ref)
        with sessions.begin() as session:
            row = session.execute(
                text("""
                SELECT r.recording_id, r.artist_credit_id, a.display_name
                FROM library.user_track_ref u
                JOIN catalog.recording r USING(recording_id)
                JOIN catalog.artist_credit a ON a.artist_credit_id=r.artist_credit_id
                WHERE u.user_track_ref_id=:ref
            """),
                {"ref": ref},
            ).one()
            session.execute(
                text("""
                WITH g AS (INSERT INTO catalog.release_group
                    (artist_credit_id,title,normalized_title,primary_type)
                    VALUES (:artist,'Album','album','ALBUM') RETURNING release_group_id),
                rel AS (INSERT INTO catalog.release(release_group_id,artist_credit_id,title)
                    SELECT release_group_id,:artist,'Album' FROM g RETURNING release_id),
                m AS (INSERT INTO catalog.medium(release_id,position)
                    SELECT release_id,1 FROM rel RETURNING medium_id)
                INSERT INTO catalog.release_track
                    (medium_id,recording_id,artist_credit_id,sequence_no,title)
                    SELECT medium_id,:recording,:artist,1,'Song' FROM m
            """),
                {"artist": row.artist_credit_id, "recording": row.recording_id},
            )
        pages = CatalogPageCache(sessions)
        assert [
            r["recording"]
            for r in pages.visible_album_positions(owner.user_id, "Album", row.display_name, None)
        ] == [str(row.recording_id)]
        assert pages.visible_album_positions(uuid4(), "Album", row.display_name, None) == []
        with sessions.begin() as session:
            session.execute(
                text("UPDATE vault.audio_variant SET deleted_at=now() WHERE audio_variant_id=:v"),
                {"v": variant},
            )
        assert pages.visible_album_positions(owner.user_id, "Album", row.display_name, None) == []
    finally:
        engine.dispose()


def test_tags_without_release_rows_use_only_the_playable_owners_metadata(database_url: str) -> None:
    engine, sessions, owner, ref, _ = setup(database_url)
    release, mbid = str(uuid4()), str(uuid4())
    try:
        publish_audio(sessions, owner, ref)
        with sessions.begin() as s:
            s.add(
                TrackMetadataRow(
                    user_track_ref_id=ref,
                    document={
                        "fields": {
                            "album": "Album",
                            "title": "Song",
                            "artist": "Singer feat. Guest",
                            "disc_number": 2,
                            "track_number": 3,
                            "mb_release_id": release,
                            "mb_recording_id": mbid,
                        },
                        "provenance": {
                            "mb_recording_id": {
                                "source": "MUSICBRAINZ",
                                "source_id": "musicbrainz:release:" + release,
                            }
                        },
                    },
                )
            )
        pages = CatalogPageCache(sessions)
        (row,) = pages.visible_album_positions(owner.user_id, "Album", "Singer", 2026)
        assert (row["disc"], row["sequence"], row["artist"], row["duration_ms"]) == (
            2,
            3,
            "Singer feat. Guest",
            180000,
        )
        assert pages.available_recordings(owner.user_id, [mbid]) == {mbid: row["recording"]}
        other = uuid4()
        assert pages.visible_album_positions(other, "Album", "Singer", 2026) == []
        assert pages.available_recordings(other, [mbid]) == {}
    finally:
        engine.dispose()
