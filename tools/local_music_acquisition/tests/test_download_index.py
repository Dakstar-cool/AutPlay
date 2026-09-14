from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from local_music_acquisition import download_index
from local_music_acquisition.download_index import DownloadIndex
from local_music_acquisition.models import PlaylistItem
from local_music_acquisition.orchestrator import PlaylistDownloadError
from local_music_acquisition.queue import _key
from local_music_acquisition.queue_store import read_json, write_json


def committed(output: Path, number: int = 1) -> tuple[str, Path]:
    key = _key(PlaylistItem(number, "Artist", f"Track {number}"))
    directory = output / "tracks" / key
    (directory / "fixture").mkdir(parents=True)
    path = directory / "fixture" / "track.wav"
    payload = b"fixture audio bytes"
    path.write_bytes(payload)
    write_json(
        directory / "receipt.json",
        {
            "schema_version": 1,
            "key": key,
            "provider": "fixture",
            "filename": path.name,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    return key, path


def test_persistent_index_reuses_verification_and_uses_primary_key(tmp_path, monkeypatch):
    key, _path = committed(tmp_path)
    with closing(DownloadIndex(tmp_path)) as index:
        index.verify(key)
        assert index.verified == 1
        assert index.connection is not None
        plan = index.connection.execute(
            "EXPLAIN QUERY PLAN SELECT sha256 FROM verified_v1 WHERE track_key=?", (key,)
        ).fetchall()
        assert "PRIMARY KEY" in str(plan) and "SCAN" not in str(plan)
    monkeypatch.setattr(
        download_index, "verify_receipt", lambda *_: pytest.fail("rehashing cache hit")
    )
    with closing(DownloadIndex(tmp_path)) as index:
        index.verify(key)
        assert index.hits == 1


@pytest.mark.parametrize(
    "change", ["same_size", "removed", "receipt", "expired", "clock_backwards"]
)
def test_stale_or_changed_entries_never_bypass_verification(tmp_path, monkeypatch, change):
    key, path = committed(tmp_path)
    now = [100.0]
    monkeypatch.setattr(download_index.time, "time", lambda: now[0])
    with closing(DownloadIndex(tmp_path, recheck_seconds=10)) as index:
        index.verify(key)
        if change == "same_size":
            path.write_bytes(b"x" * path.stat().st_size)
        elif change == "removed":
            path.unlink()
        elif change == "receipt":
            receipt_path = path.parent.parent / "receipt.json"
            receipt = read_json(receipt_path)
            receipt["sha256"] = "0" * 64
            write_json(receipt_path, receipt)
        elif change == "expired":
            now[0] += 10
        else:
            now[0] -= 1
        if change in {"expired", "clock_backwards"}:
            index.verify(key)
            assert index.verified == 2
        else:
            with pytest.raises(PlaylistDownloadError):
                index.verify(key)
        assert index.hits == 0


def test_forced_verification_and_missing_cache_rebuild(tmp_path):
    key, _path = committed(tmp_path)
    for _ in range(2):
        with closing(DownloadIndex(tmp_path, recheck_seconds=0)) as index:
            index.verify(key)
            index.verify(key)
            assert index.verified == 2 and index.hits == 0
        (tmp_path / ".download-index.sqlite3").unlink()


def test_corrupt_cache_falls_back_to_real_receipts_without_false_success(tmp_path):
    key, path = committed(tmp_path)
    cache = tmp_path / ".download-index.sqlite3"
    cache.write_bytes(b"corrupt database")
    with closing(DownloadIndex(tmp_path)) as index:
        assert index.unavailable
        index.verify(key)
        path.write_bytes(b"corrupt artifact")
        with pytest.raises(PlaylistDownloadError):
            index.verify(key)
    assert cache.read_bytes() == b"corrupt database"


def test_parallel_index_writes_are_reusable_after_restart(tmp_path):
    keys = [committed(tmp_path, number)[0] for number in range(20)]
    with closing(DownloadIndex(tmp_path)) as index, ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(index.verify, keys))
        assert {receipt["key"] for receipt in receipts} == set(keys)
        assert index.verified == 20
    with closing(DownloadIndex(tmp_path)) as index:
        for key in keys:
            index.verify(key)
        assert index.hits == 20 and not index.unavailable


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink/permission contract")
def test_index_permissions_and_linked_audio_rejected(tmp_path):
    key, path = committed(tmp_path)
    with closing(DownloadIndex(tmp_path)) as index:
        index.verify(key)
        assert (tmp_path / ".download-index.sqlite3").stat().st_mode & 0o777 == 0o600
        target = tmp_path / "replacement.wav"
        path.rename(target)
        path.symlink_to(target)
        with pytest.raises(PlaylistDownloadError):
            index.verify(key)


def test_key_normalizes_case_unicode_and_spacing_without_merging_versions():
    base = _key(PlaylistItem(1, "Artist", "Track", "Album"))
    assert base == _key(PlaylistItem(99, " ARTIST ", "\uff34rack", " ALBUM "))
    assert base != _key(PlaylistItem(1, "Artist", "Track (Live)", "Album"))
    assert base != _key(PlaylistItem(1, "Artist feat. Other", "Track", "Album"))
    assert base != _key(PlaylistItem(1, "Artist", "Track", "Other Album"))
    # Existing queue manifests and published paths must retain their original keys.
    legacy = hashlib.sha256(json.dumps(["artist", "track", "album"]).encode()).hexdigest()
    assert base == legacy


def test_failed_audit_discards_cached_success_even_if_metadata_is_unchanged(tmp_path, monkeypatch):
    key, path = committed(tmp_path)
    original = download_index._fingerprint
    with closing(DownloadIndex(tmp_path)) as index:
        receipt = index.verify(key)
        fingerprint = original(path.parent.parent, receipt)
        # Simulate silent disk corruption: the metadata itself supplies no invalidation.
        monkeypatch.setattr(download_index, "_fingerprint", lambda *_: fingerprint)
        path.write_bytes(b"x" * path.stat().st_size)
        with pytest.raises(PlaylistDownloadError):
            index.verify(key, force=True)
    with closing(DownloadIndex(tmp_path)) as index:
        with pytest.raises(PlaylistDownloadError):
            index.verify(key)
        assert index.hits == 0
