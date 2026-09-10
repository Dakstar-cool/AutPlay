"""Optional Yandex Music contour backed by a pinned downloader library."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import ymd.core as ymd_core
from Crypto.Cipher import AES
from yandex_music import Client, Track
from yandex_music.exceptions import UnauthorizedError, YandexMusicError

from ..models import AcquiredArtifact, PlaylistItem, ProviderFailure, ProviderMiss
from .jamendo import sanitize_filename

_QUALITY = {
    "low": ymd_core.CoreTrackQuality.LOW,
    "normal": ymd_core.CoreTrackQuality.NORMAL,
    "lossless": ymd_core.CoreTrackQuality.LOSSLESS,
}


def _identity(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    folded = re.sub(r"[^\w]+", " ", folded, flags=re.UNICODE)
    return " ".join(folded.split())


@dataclass(frozen=True, slots=True)
class _TitleIdentity:
    title: str
    versions: tuple[str, ...]


def _title_identity(title: str, explicit_version: object = None) -> _TitleIdentity:
    """Keep recording-version evidence separate from the base title."""

    match = re.fullmatch(r"(.*?)\s*[\(\[]([^\)\]]+)[\)\]]\s*", title)
    embedded_version = match.group(2) if match else None
    base_title = match.group(1) if match else title
    versions = {
        normalized
        for raw in (embedded_version, explicit_version)
        if isinstance(raw, str) and (normalized := _identity(raw))
    }
    return _TitleIdentity(_identity(base_title), tuple(sorted(versions)))


def _content_ref(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()[:12]}"


def _load_token(path: Path) -> str:
    try:
        details = path.stat()
        if not stat.S_ISREG(details.st_mode) or path.is_symlink():
            raise OSError
        if os.name != "nt" and stat.S_IMODE(details.st_mode) & 0o077:
            raise ProviderFailure("yandex", "token_file_permissions_invalid")
        if not 20 <= details.st_size <= 4096:
            raise ProviderFailure("yandex", "token_file_invalid")
        token = path.read_text(encoding="utf-8").strip()
    except ProviderFailure:
        raise
    except (OSError, UnicodeError) as error:
        raise ProviderFailure("yandex", "token_file_unavailable") from error
    if not 20 <= len(token) <= 4096 or any(character.isspace() for character in token):
        raise ProviderFailure("yandex", "token_file_invalid")
    return token


def _track_matches(track: Track, *, artist: str, title: str) -> bool:
    if not isinstance(track.title, str) or _title_identity(
        track.title,
        getattr(track, "version", None),
    ) != _title_identity(title):
        return False
    names = track.artists_name()
    if not names or any(not isinstance(name, str) for name in names):
        return False
    requested = _identity(artist)
    possible_artists = {_identity(name) for name in names}
    possible_artists.add(_identity(" ".join(names)))
    return requested in possible_artists


def _validate_audio(path: Path, suffix: str, *, deadline: float) -> None:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(64)
    except OSError as error:
        raise ProviderFailure("yandex", "audio_invalid") from error
    if size < 1024:
        raise ProviderFailure("yandex", "audio_invalid")
    valid = {
        ".mp3": header.startswith(b"ID3")
        or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0),
        ".flac": header.startswith(b"fLaC"),
        ".m4a": len(header) >= 12 and header[4:8] == b"ftyp",
    }.get(suffix, False)
    if not valid:
        raise ProviderFailure("yandex", "audio_invalid")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderFailure("yandex", "download_timeout")
    try:
        completed = subprocess.run(
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
            check=False,
            capture_output=True,
            timeout=remaining,
        )
    except subprocess.TimeoutExpired as error:
        raise ProviderFailure("yandex", "download_timeout") from error
    except OSError as error:
        raise ProviderFailure("yandex", "audio_probe_unavailable") from error
    if completed.returncode != 0:
        raise ProviderFailure("yandex", "audio_invalid")


def _copy_exclusive(source: Path, destination: Path) -> None:
    created = False
    try:
        with destination.open("xb") as target:
            created = True
            with source.open("rb") as origin:
                shutil.copyfileobj(origin, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        if created:
            with suppress(OSError):
                destination.unlink()
        raise


def _publish(source: Path, output_directory: Path, stem: str, suffix: str) -> Path:
    fallback_errnos = {
        errno.EACCES,
        errno.EPERM,
        errno.EXDEV,
        getattr(errno, "ENOTSUP", -1),
        getattr(errno, "EOPNOTSUPP", -1),
    }
    for index in range(100_000):
        candidate_stem = stem if index == 0 else f"{stem} ({index})"
        destination = output_directory / f"{candidate_stem}{suffix}"
        try:
            os.link(source, destination)
        except FileExistsError:
            continue
        except OSError as error:
            if error.errno not in fallback_errnos:
                raise ProviderFailure("yandex", "publish_failed") from error
            try:
                _copy_exclusive(source, destination)
            except FileExistsError:
                continue
            except OSError as copy_error:
                raise ProviderFailure("yandex", "publish_failed") from copy_error
        return destination
    raise ProviderFailure("yandex", "publish_name_exhausted")


def _download_track_bounded(
    client: Client,
    download_info: Any,
    destination: Path,
    *,
    max_bytes: int,
    deadline: float,
    activity_timeout_seconds: int,
) -> None:
    urls = getattr(download_info, "urls", None)
    if not isinstance(urls, list) or not urls or not isinstance(urls[0], str):
        raise ProviderFailure("yandex", "download_response_invalid")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderFailure("yandex", "download_timeout")
    timeout = max(0.001, min(float(activity_timeout_seconds), remaining))
    proxies = getattr(getattr(client, "request", None), "proxies", None)
    cipher = None
    key = getattr(download_info, "decryption_key", None)
    if key:
        try:
            cipher = AES.new(key=bytes.fromhex(key), nonce=bytes(12), mode=AES.MODE_CTR)
        except (TypeError, ValueError) as error:
            raise ProviderFailure("yandex", "download_response_invalid") from error
    try:
        with requests.get(
            urls[0],
            stream=True,
            timeout=(timeout, timeout),
            proxies=proxies,
        ) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > max_bytes:
                        raise ProviderFailure("yandex", "download_size_invalid")
                except ValueError as error:
                    raise ProviderFailure("yandex", "download_response_invalid") from error
            total = 0
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if time.monotonic() > deadline:
                        raise ProviderFailure("yandex", "download_timeout")
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ProviderFailure("yandex", "download_size_invalid")
                    handle.write(cipher.decrypt(chunk) if cipher is not None else chunk)
                handle.flush()
                os.fsync(handle.fileno())
    except ProviderFailure:
        raise
    except (OSError, requests.RequestException) as error:
        raise ProviderFailure("yandex", "download_failed") from error


class YandexProvider:
    """Search and acquire one strict exact match without exposing the OAuth token."""

    name = "yandex"
    requires_rights_confirmation = True

    def __init__(
        self,
        token_file: Path,
        *,
        quality: str = "normal",
        timeout_seconds: int = 10,
        retry_count: int = 1,
        retry_delay_seconds: int = 1,
        search_limit: int = 20,
        max_bytes: int = 200 * 1024 * 1024,
    ) -> None:
        if quality not in _QUALITY:
            raise ValueError("yandex_quality_invalid")
        if not 5 <= timeout_seconds <= 30:
            raise ValueError("yandex_timeout_invalid")
        if not 1 <= retry_count <= 3 or not 0 <= retry_delay_seconds <= 10:
            raise ValueError("yandex_retry_invalid")
        if not 1 <= search_limit <= 50:
            raise ValueError("yandex_search_limit_invalid")
        if not 1024 <= max_bytes <= 1024 * 1024 * 1024:
            raise ValueError("yandex_max_bytes_invalid")
        self._token_file = token_file
        self._quality = _QUALITY[quality]
        self._timeout_seconds = timeout_seconds
        self._retry_count = retry_count
        self._retry_delay_seconds = retry_delay_seconds
        self._search_limit = search_limit
        self._max_bytes = max_bytes
        self._client: Client | None = None

    def _client_or_raise(self) -> Client:
        if self._client is not None:
            return self._client
        token = _load_token(self._token_file)
        try:
            self._client = ymd_core.init_client(
                token,
                self._timeout_seconds,
                self._retry_count,
                self._retry_delay_seconds,
            )
        except UnauthorizedError as error:
            raise ProviderFailure(self.name, "authentication_failed") from error
        except (YandexMusicError, RuntimeError, ValueError, KeyError, TypeError) as error:
            raise ProviderFailure(self.name, "initialization_failed") from error
        return self._client

    def acquire(self, item: PlaylistItem, output_directory: Path) -> AcquiredArtifact:
        deadline = time.monotonic() + self._timeout_seconds
        client = self._client_or_raise()
        try:
            search = client.search(
                f"{item.artist} - {item.title}",
                nocorrect=True,
                type_="track",
                page=0,
            )
        except UnauthorizedError as error:
            raise ProviderFailure(self.name, "authentication_failed") from error
        except YandexMusicError as error:
            raise ProviderFailure(self.name, "search_failed") from error
        results = getattr(getattr(search, "tracks", None), "results", None)
        if not isinstance(results, list):
            raise ProviderFailure(self.name, "search_response_invalid")
        exact = [
            track
            for track in results[: self._search_limit]
            if isinstance(track, Track)
            and _track_matches(track, artist=item.artist, title=item.title)
        ]
        if not exact:
            raise ProviderMiss(self.name, "exact_match_not_found")
        if len(exact) != 1:
            raise ProviderMiss(self.name, "ambiguous_match")
        match = exact[0]
        try:
            downloadable = ymd_core.to_downloadable_track(
                match, self._quality, output_directory / "download"
            )
        except UnauthorizedError as error:
            raise ProviderFailure(self.name, "authentication_failed") from error
        except (YandexMusicError, RuntimeError, ValueError, KeyError, TypeError) as error:
            raise ProviderFailure(self.name, "download_failed") from error
        suffix = downloadable.path.suffix.casefold()
        try:
            output_directory.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=".autplay-yandex-",
                suffix=f"{suffix}.part",
                dir=output_directory,
            )
            os.close(descriptor)
        except OSError as error:
            raise ProviderFailure(self.name, "output_directory_unavailable") from error
        temporary = Path(name)
        try:
            _download_track_bounded(
                client,
                downloadable.download_info,
                temporary,
                max_bytes=self._max_bytes,
                deadline=deadline,
                activity_timeout_seconds=self._timeout_seconds,
            )
            _validate_audio(temporary, suffix, deadline=deadline)
            destination = _publish(
                temporary,
                output_directory,
                sanitize_filename(f"{item.artist} - {item.title}"),
                suffix,
            )
        finally:
            temporary.unlink(missing_ok=True)
        version = _title_identity(match.title, getattr(match, "version", None)).versions
        return AcquiredArtifact(
            self.name,
            _content_ref(destination),
            identity_version=" / ".join(version) or None,
        )
