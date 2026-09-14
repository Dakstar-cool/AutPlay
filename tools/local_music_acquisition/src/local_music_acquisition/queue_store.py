"""Private, atomic local-filesystem receipts for the standalone acquisition worker."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .audio_validation import audio_duration
from .orchestrator import PlaylistDownloadError

AUDIO_SUFFIXES = frozenset({".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"})


def sync_directory(path: Path) -> None:
    if sys.platform != "win32":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def make_directory(path: Path) -> None:
    """Persist newly created directory entries before writing receipts beneath them."""
    if path.is_symlink() or path.is_junction():
        raise PlaylistDownloadError("queue_link_directory_rejected")
    if not path.exists():
        make_directory(path.parent)
        path.mkdir(exist_ok=True, mode=0o700)
    elif not path.is_dir():
        raise PlaylistDownloadError("queue_directory_invalid")
    # Also covers a parent another cooperating worker has only just created.
    sync_directory(path.parent)


def write_json(path: Path, value: object) -> None:
    make_directory(path.parent)
    descriptor, name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path, *, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    try:
        if path.is_symlink() or path.stat().st_size > max_bytes:
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError) as error:
        raise PlaylistDownloadError("queue_receipt_invalid") from error


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Hold a kernel lock, automatically released even after an unclean process exit."""
    make_directory(path.parent)
    with path.open("a+b") as handle:
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise PlaylistDownloadError("queue_already_running") from error
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def audio_receipt(
    directory: Path, *, max_bytes: int, expected_seconds: float | None = None
) -> dict[str, object]:
    files = [path for path in directory.iterdir() if path.suffix.lower() in AUDIO_SUFFIXES]
    if len(files) != 1:
        raise PlaylistDownloadError("queue_audio_count_invalid")
    path = files[0]
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or not 1 <= details.st_size <= max_bytes:
        raise PlaylistDownloadError("queue_audio_size_invalid")
    try:
        duration = audio_duration(path, expected_seconds=expected_seconds)
    except ValueError as error:
        raise PlaylistDownloadError("queue_" + str(error)) from error
    try:
        probe = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PlaylistDownloadError("queue_audio_probe_failed") from error
    if probe.returncode:
        raise PlaylistDownloadError("queue_audio_invalid")
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())
    sync_directory(directory)
    return {
        "filename": path.name,
        "bytes": details.st_size,
        "sha256": file_digest(path),
        "duration_seconds": duration,
        "expected_duration_seconds": expected_seconds,
    }


def verify_receipt(directory: Path, key: str) -> dict[str, Any]:
    receipt = read_json(directory / "receipt.json")
    filename = receipt.get("filename")
    provider = receipt.get("provider")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("key") != key
        or not isinstance(filename, str)
        or Path(filename).name != filename
        or not isinstance(provider, str)
        or Path(provider).name != provider
        or provider in {"", ".", ".."}
    ):
        raise PlaylistDownloadError("queue_receipt_invalid")
    path = directory / provider / filename
    try:
        if (
            directory.is_symlink()
            or path.parent.is_symlink()
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != receipt.get("bytes")
            or file_digest(path) != receipt.get("sha256")
        ):
            raise ValueError
    except (OSError, ValueError) as error:
        raise PlaylistDownloadError("queue_artifact_integrity_failed") from error
    return receipt
