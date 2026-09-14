"""Disposable index of verified queue artifacts; receipts and audio remain authoritative."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path
from typing import Any

from .orchestrator import PlaylistDownloadError
from .queue_store import inspect_receipt, verify_receipt


def _fingerprint(directory: Path, receipt: dict[str, Any]) -> str:
    paths = (
        directory,
        directory / "receipt.json",
        directory / receipt["provider"],
        directory / receipt["provider"] / receipt["filename"],
    )
    result = []
    for path in paths:
        details = path.stat()
        result.append(
            [
                details.st_dev,
                details.st_ino,
                details.st_size,
                details.st_mtime_ns,
                details.st_ctime_ns,
            ]
        )
    return json.dumps(result)


class DownloadIndex:
    """One private SQLite cache per output root, shared by cooperating queue workers.

    Every hit still checks receipt shape, path safety and file metadata. Changed or
    expired entries require a full SHA-256 check. A broken cache falls back to full
    verification, never to a presumed download. The caller holds .acquisition.lock.
    """

    def __init__(self, output: Path, *, recheck_seconds: int = 86400) -> None:
        if not 0 <= recheck_seconds <= 86400:
            raise PlaylistDownloadError("queue_index_policy_invalid")
        self.output = output
        self.recheck_seconds = recheck_seconds
        self.lock = threading.Lock()
        self.connection: sqlite3.Connection | None = None
        self.hits = 0
        self.verified = 0
        self.unavailable = False
        path = output / ".download-index.sqlite3"
        for candidate in (
            path,
            *(Path(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm")),
        ):
            if candidate.is_symlink() or candidate.is_junction():
                raise PlaylistDownloadError("queue_index_link_rejected")
        try:
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if not stat.S_ISREG(path.stat().st_mode):
                    raise PlaylistDownloadError("queue_index_file_invalid") from None
            else:
                os.close(descriptor)
            self.connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
            self.connection.execute("PRAGMA trusted_schema=OFF")
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS verified_v1 ("
                "track_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                "sha256 TEXT NOT NULL, verified_at REAL NOT NULL) WITHOUT ROWID"
            )
            self.connection.commit()
        except (OSError, sqlite3.Error):
            self._disable()

    def _disable(self) -> None:
        self.unavailable = True
        self.close()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def verify(self, key: str, *, force: bool = False) -> dict[str, Any]:
        directory = self.output / "tracks" / key
        receipt = inspect_receipt(directory, key)
        try:
            before = _fingerprint(directory, receipt)
        except OSError as error:
            raise PlaylistDownloadError("queue_artifact_integrity_failed") from error
        now = time.time()
        with self.lock:
            try:
                row = (
                    self.connection.execute(
                        "SELECT fingerprint, sha256, verified_at "
                        "FROM verified_v1 WHERE track_key=?",
                        (key,),
                    ).fetchone()
                    if self.connection is not None
                    else None
                )
                if (
                    row is not None
                    and not force
                    and row[0] == before
                    and row[1] == receipt["sha256"]
                    and isinstance(row[2], (int, float))
                    and 0 <= now - row[2] < self.recheck_seconds
                ):
                    self.hits += 1
                    return receipt
                # A failed full check must never leave an earlier positive cache entry.
                if self.connection is not None:
                    with self.connection:
                        self.connection.execute("DELETE FROM verified_v1 WHERE track_key=?", (key,))
            except sqlite3.Error:
                self._disable()
        receipt = verify_receipt(directory, key)
        try:
            after = _fingerprint(directory, receipt)
        except OSError as error:
            raise PlaylistDownloadError("queue_artifact_changed_during_verification") from error
        if before != after:
            raise PlaylistDownloadError("queue_artifact_changed_during_verification")
        with self.lock:
            self.verified += 1
            try:
                if self.connection is not None:
                    with self.connection:
                        self.connection.execute(
                            "INSERT OR REPLACE INTO verified_v1 VALUES (?, ?, ?, ?)",
                            (key, before, receipt["sha256"], now),
                        )
            except sqlite3.Error:
                self._disable()
        return receipt

    def summary(self) -> dict[str, int | bool]:
        return {
            "cache_hits": self.hits,
            "sha256_verified": self.verified,
            "unavailable": self.unavailable,
        }
