"""Execute Android snapshot DAO SQL against its committed Room schema, without Gradle."""

from __future__ import annotations

import json
import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DAO = ROOT / "apps/android/src/main/kotlin/app/autplay/data/local/dao/Daos.kt"
SCHEMAS = ROOT / "apps/android/schemas/app.autplay.data.local.AutPlayDatabase"


def query(name: str) -> str:
    source = DAO.read_text(encoding="utf-8")
    end = source.index(f"suspend fun {name}(")
    annotation = source[source.rfind("@Query(", 0, end) : end]
    multiline = re.search(r'"""(.*?)"""', annotation, re.S)
    if multiline:
        return multiline.group(1)
    return re.search(r'@Query\("(.*?)"\)', annotation, re.S).group(1)


class ProfileStatisticsSnapshotQueriesTest(unittest.TestCase):
    def setUp(self) -> None:
        schema_path = max(SCHEMAS.glob("*.json"), key=lambda path: int(path.stem))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        for entity in schema["database"]["entities"]:
            if entity["tableName"] in {
                "listening_event",
                "user_track_ref",
                "track_metadata_projection",
            }:
                self.db.execute(entity["createSql"].replace("${TABLE_NAME}", entity["tableName"]))

    def tearDown(self) -> None:
        self.db.close()

    def insert(self, table: str, **values: object) -> None:
        for column in self.db.execute(f"PRAGMA table_info({table})"):
            name, kind, required, default = column[1:5]
            if name not in values and required and default is None:
                values[name] = "fixture" if kind == "TEXT" else 0
        columns = ",".join(values)
        parameters = ",".join("?" for _ in values)
        self.db.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({parameters})", tuple(values.values())
        )

    def track(
        self, track: str, owner: str = "a", recording: str | None = None, artist: str = "Artist"
    ) -> None:
        self.insert(
            "user_track_ref",
            local_user_track_ref_id=track,
            server_profile_id=owner,
            server_recording_id=recording,
            raw_title=track,
            raw_artist=artist,
        )

    def event(
        self, event: str, track: str, played: int, owner: str = "a", started: int = 1000
    ) -> None:
        self.insert(
            "listening_event",
            listening_event_id=event,
            local_user_track_ref_id=track,
            server_profile_id=owner,
            played_ms=played,
            started_at_ms=started,
        )

    def read(self, name: str, **extra: object) -> list[sqlite3.Row]:
        return self.db.execute(
            query(name), dict(profileId="a", throughInclusiveMs=1000, limit=5, **extra)
        ).fetchall()

    def test_all_time_profile_scope_actual_time_rank_and_recording_deduplication(self) -> None:
        self.track("one", recording="recording")
        self.track("alias", recording="recording")
        self.track("short", artist="Frequent")
        self.track("other", owner="b")
        self.event("old", "one", 90_000, started=-1_000_000_000_000)
        self.event("alias", "alias", 10_000)
        for index in range(3):
            self.event(f"short-{index}", "short", 1_000)
        self.event("zero", "one", 0)
        self.event("negative", "one", -100)
        self.event("future", "one", 500_000, started=1001)
        self.event("other", "other", 500_000, owner="b")
        self.db.execute(
            "UPDATE user_track_ref SET deleted_at_ms=500 WHERE local_user_track_ref_id='one'"
        )
        self.assertEqual(103_000, self.read("ownerListenedMs")[0][0])
        tracks = self.read("ownerTopTracksSnapshot")
        self.assertEqual(2, len(tracks))
        self.assertEqual("server:recording", tracks[0]["identity_key"])
        self.assertEqual(100_000, tracks[0]["listened_ms"])
        self.assertEqual("Artist", self.read("ownerTopArtistsSnapshot")[0]["artist_name"])

    def test_top_five_stable_ties_and_unknown_artists_omitted(self) -> None:
        for index in range(8):
            track = f"track-{index}"
            self.track(track, artist=" " if index == 7 else f"Artist {index}")
            self.event(track, track, 1_000)
        self.assertEqual(
            [f"track:track-{i}" for i in range(5)],
            [row["identity_key"] for row in self.read("ownerTopTracksSnapshot")],
        )
        artists = self.read("ownerTopArtistsSnapshot")
        self.assertEqual(5, len(artists))
        self.assertEqual([f"Artist {i}" for i in range(5)], [row["artist_name"] for row in artists])

    def test_genre_sources_page_all_listened_tracks_without_cross_profile_metadata(self) -> None:
        for index in range(205):
            track = f"track-{index:03d}"
            self.track(track)
            self.event(track, track, 1000)
            self.insert(
                "track_metadata_projection",
                server_profile_id="a",
                local_user_track_ref_id=track,
                payload_json='{"revision":1,"state":"READY","fields":{"genres":["Rock"]}}',
            )
        self.track("wrong-metadata")
        self.event("wrong", "wrong-metadata", 500_000)
        self.insert(
            "track_metadata_projection",
            server_profile_id="b",
            local_user_track_ref_id="wrong-metadata",
            payload_json='{"revision":1,"state":"READY","fields":{"genres":["Private"]}}',
        )
        parameters = {
            "profileId": "a",
            "throughInclusiveMs": 1000,
            "afterTrackId": "",
            "limit": 200,
        }
        first = self.db.execute(query("ownerGenreSourcesPage"), parameters).fetchall()
        parameters["afterTrackId"] = first[-1]["local_track_ref_id"]
        second = self.db.execute(query("ownerGenreSourcesPage"), parameters).fetchall()
        self.assertEqual((200, 5), (len(first), len(second)))
        self.assertEqual(205_000, sum(row["listened_ms"] for row in first + second))


if __name__ == "__main__":
    unittest.main()
