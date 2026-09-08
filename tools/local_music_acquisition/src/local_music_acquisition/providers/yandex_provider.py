"""Optional Yandex Music contour backed by a pinned downloader library."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path

import ymd.api as ymd_api
import ymd.core as ymd_core
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
    if not isinstance(track.title, str) or _identity(track.title) != _identity(title):
        return False
    names = track.artists_name()
    if not names or any(not isinstance(name, str) for name in names):
        return False
    requested = _identity(artist)
    possible_artists = {_identity(name) for name in names}
    possible_artists.add(_identity(" ".join(names)))
    return requested in possible_artists


def _validate_audio(payload: bytes, suffix: str) -> None:
    if len(payload) < 1024:
        raise ProviderFailure("yandex", "audio_invalid")
    header = payload[:64]
    valid = {
        ".mp3": header.startswith(b"ID3")
        or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0),
        ".flac": header.startswith(b"fLaC"),
        ".m4a": len(header) >= 12 and header[4:8] == b"ftyp",
    }.get(suffix, False)
    if not valid:
        raise ProviderFailure("yandex", "audio_invalid")


def _publish(payload: bytes, output_directory: Path, stem: str, suffix: str) -> Path:
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=".autplay-yandex-", suffix=f"{suffix}.part", dir=output_directory
        )
    except OSError as error:
        raise ProviderFailure("yandex", "output_directory_unavailable") from error
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        for index in range(100_000):
            candidate_stem = stem if index == 0 else f"{stem} ({index})"
            destination = output_directory / f"{candidate_stem}{suffix}"
            try:
                os.link(temporary, destination)
            except FileExistsError:
                continue
            except OSError as error:
                raise ProviderFailure("yandex", "publish_failed") from error
            return destination
        raise ProviderFailure("yandex", "publish_name_exhausted")
    finally:
        temporary.unlink(missing_ok=True)


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
        try:
            downloadable = ymd_core.to_downloadable_track(
                exact[0], self._quality, output_directory / "download"
            )
            payload = ymd_api.download_track(client, downloadable.download_info)
        except UnauthorizedError as error:
            raise ProviderFailure(self.name, "authentication_failed") from error
        except (YandexMusicError, RuntimeError, ValueError, KeyError, TypeError) as error:
            raise ProviderFailure(self.name, "download_failed") from error
        if not isinstance(payload, bytes) or len(payload) > self._max_bytes:
            raise ProviderFailure(self.name, "download_size_invalid")
        suffix = downloadable.path.suffix.casefold()
        _validate_audio(payload, suffix)
        destination = _publish(
            payload,
            output_directory,
            sanitize_filename(f"{item.artist} - {item.title}"),
            suffix,
        )
        return AcquiredArtifact(self.name, _content_ref(destination))
