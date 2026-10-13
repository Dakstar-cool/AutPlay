"""Exercise the shipped Room sampling SQL on SQLite without Android or server access."""

from pathlib import Path
import re
import sqlite3


ROOT = Path(__file__).resolve().parents[2]
DAO = ROOT / "apps/android/src/main/kotlin/app/autplay/data/local/dao/Daos.kt"
SQL = re.search(
    r'@Query\(\s*"""(.*?)""",\s*\)\s*suspend fun randomPlaybackCandidates',
    DAO.read_text(encoding="utf-8"),
    re.S,
).group(1)


def database():
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE user_track_ref(local_user_track_ref_id TEXT PRIMARY KEY,
            server_profile_id TEXT, deleted_at_ms INTEGER, server_user_track_ref_id TEXT,
            server_recording_id TEXT, resolution_status TEXT);
        CREATE TABLE library_entry(local_user_track_ref_id TEXT, server_profile_id TEXT,
            removed_at_ms INTEGER);
        CREATE TABLE local_audio_state(local_user_track_ref_id TEXT, status TEXT,
            persisted_uri_permission INTEGER, content_uri TEXT);
        CREATE TABLE download_intent(local_user_track_ref_id TEXT, server_profile_id TEXT, state TEXT);
    """)
    return db


def track(db, name, owner="owner", *, vault=False, removed=None, deleted=None):
    db.execute("INSERT INTO user_track_ref VALUES(?,?,?,?,?,?)",
               (name, owner, deleted, name if vault else None, name if vault else None,
                "RESOLVED" if vault else "UNRESOLVED"))
    db.execute("INSERT INTO library_entry VALUES(?,?,?)", (name, owner, removed))


def sample(db, *, owner="owner", vault=False, limit=200):
    return [row[0] for row in db.execute(SQL, {"profileId": owner, "vaultAvailable": vault, "limit": limit})]


def test_offline_sources_require_readable_local_audio_or_completed_owned_download():
    with database() as db:
        for name in ("saf", "media", "revoked", "missing", "download", "pending", "wrong-download", "vault", "metadata"):
            track(db, name, vault=name == "vault")
        db.executemany("INSERT INTO local_audio_state VALUES(?,?,?,?)", [
            ("saf", "AVAILABLE", 1, "content://documents/song"),
            ("media", "AVAILABLE", 0, "content://media/external/audio/media/1"),
            ("revoked", "AVAILABLE", 0, "content://documents/revoked"),
            ("missing", "MISSING", 1, "content://documents/missing"),
        ])
        db.executemany("INSERT INTO download_intent VALUES(?,?,?)", [
            ("download", "owner", "COMPLETED"), ("pending", "owner", "PENDING"),
            ("wrong-download", "another", "COMPLETED"),
        ])
        assert set(sample(db)) == {"saf", "media", "download"}
        assert set(sample(db, vault=True)) == {"saf", "media", "download", "vault"}


def test_owner_removal_resolution_and_duplicate_library_entries_are_guarded():
    with database() as db:
        track(db, "ready", vault=True)
        track(db, "other", owner="another", vault=True)
        track(db, "removed", vault=True, removed=1)
        track(db, "deleted", vault=True, deleted=1)
        track(db, "unresolved", vault=True)
        db.execute("UPDATE user_track_ref SET resolution_status='UNRESOLVED' WHERE local_user_track_ref_id='unresolved'")
        db.execute("INSERT INTO library_entry VALUES('ready','owner',NULL)")
        assert sample(db, vault=True) == ["ready"]
        track(db, "legacy", owner="legacy-unscoped")
        db.execute("INSERT INTO download_intent VALUES('legacy',NULL,'COMPLETED')")
        assert sample(db, owner="legacy-unscoped") == ["legacy"]


def test_sampling_is_bounded_random_and_reaches_beyond_sorted_ui_window():
    with database() as db:
        for number in range(5_200):
            track(db, f"track-{number}", vault=number >= 5_000)
        first, second = sample(db, vault=True), sample(db, vault=True)
        assert len(first) == len(set(first)) == len(second) == 200
        assert first != second
        assert all(int(name.split("-")[1]) >= 5_000 for name in first + second)
        assert len(sample(db, vault=True, limit=1)) == 1
