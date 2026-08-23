"""Manual-only provider search and idempotent acquisition staging."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import UUID

from autplay.domain.discovery import (
    BulkArtistResolution,
    DiscoveryCandidate,
    DiscoveryError,
    ProviderArtist,
    ProviderArtistTracks,
    StagedAcquisition,
)
from autplay.ports.discovery import DiscoveryProvider

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ManualDiscoveryService:
    """Keep provider evidence separate and stage only one explicitly selected track."""

    def __init__(
        self,
        provider: DiscoveryProvider,
        *,
        staging_root: Path,
        max_download_bytes: int,
        minimum_request_interval_seconds: float = 1.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not staging_root.is_absolute():
            raise ValueError("discovery staging root must be absolute")
        if not 1_024 <= max_download_bytes <= 1024 * 1024 * 1024:
            raise ValueError("discovery download limit is invalid")
        if not 1.0 <= minimum_request_interval_seconds <= 60.0:
            raise ValueError("discovery request interval is invalid")
        self._provider = provider
        self._staging_root = staging_root
        self._max_download_bytes = max_download_bytes
        self._request_interval = minimum_request_interval_seconds
        self._monotonic_clock = monotonic_clock
        self._sleeper = sleeper
        self._gate_lock = threading.Lock()
        self._last_request = 0.0

    def search(
        self, owner_id: UUID, query: str, *, limit: int = 20
    ) -> tuple[DiscoveryCandidate, ...]:
        """Return provider evidence without creating Catalog or library state."""

        del owner_id
        self._wait_for_request_slot()
        return self._provider.search(query, limit=limit)

    def resolve_artists(
        self,
        owner_id: UUID,
        artists: Sequence[tuple[str, int]],
    ) -> tuple[BulkArtistResolution, ...]:
        """Resolve only one exact normalized provider artist per selected import name."""

        del owner_id
        if not 1 <= len(artists) <= 20:
            raise DiscoveryError("discovery_artist_selection_invalid")
        results: list[BulkArtistResolution] = []
        seen: set[str] = set()
        for name, track_count in artists:
            normalized = _identity_name(name)
            if not normalized or normalized in seen or not 1 <= track_count <= 10_000:
                raise DiscoveryError("discovery_artist_selection_invalid")
            seen.add(normalized)
            self._wait_for_request_slot()
            provider_matches = self._provider.search_artists(name, limit=3)
            exact = tuple(
                artist for artist in provider_matches if _identity_name(artist.name) == normalized
            )
            if len(exact) == 1:
                state = "EXACT_MATCH"
                provider_artist: ProviderArtist | None = exact[0]
            elif exact:
                state = "AMBIGUOUS"
                provider_artist = None
            else:
                state = "NOT_FOUND"
                provider_artist = None
            results.append(BulkArtistResolution(name, track_count, state, provider_artist))
        return tuple(results)

    def preview_artist_tracks(
        self,
        owner_id: UUID,
        artists: Sequence[ProviderArtist],
        *,
        max_tracks_per_artist: int = 25,
        max_tracks_total: int = 200,
    ) -> tuple[ProviderArtistTracks, ...]:
        """Return the top half of each selected catalog within the accepted A1B caps."""

        del owner_id
        if (
            not 1 <= len(artists) <= 20
            or not 1 <= max_tracks_per_artist <= 25
            or not 1 <= max_tracks_total <= 200
        ):
            raise DiscoveryError("discovery_artist_selection_invalid")
        remaining = max_tracks_total
        pages: list[ProviderArtistTracks] = []
        seen: set[str] = set()
        for artist in artists:
            if artist.provider_artist_id in seen:
                raise DiscoveryError("discovery_artist_selection_invalid")
            seen.add(artist.provider_artist_id)
            if remaining == 0:
                break
            self._wait_for_request_slot()
            page = self._provider.top_tracks(
                artist.provider_artist_id,
                limit=min(max_tracks_per_artist, remaining),
            )
            half_count = (page.total_count + 1) // 2
            selected = page.tracks[: min(half_count, max_tracks_per_artist, remaining)]
            pages.append(ProviderArtistTracks(page.provider_artist_id, page.total_count, selected))
            remaining -= len(selected)
        return tuple(pages)

    def acquire(
        self,
        owner_id: UUID,
        provider_track_id: str,
        *,
        operation_id: UUID,
    ) -> StagedAcquisition:
        """Revalidate permission and stage bytes; never report Vault readiness."""

        owner_root = self._staging_root / str(owner_id) / "jamendo"
        operation_root = owner_root / str(operation_id)
        if operation_root.exists():
            return self._existing(operation_root, provider_track_id, operation_id)
        try:
            owner_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise DiscoveryError("discovery_staging_unavailable") from error

        self._wait_for_request_slot()
        candidate = self._provider.lookup(provider_track_id)
        if not candidate.acquisition_allowed:
            raise DiscoveryError("discovery_not_eligible")

        temporary_root = Path(tempfile.mkdtemp(prefix=".autplay-jamendo-", dir=owner_root))
        audio_name = f"{_filename(candidate.artist)} - {_filename(candidate.title)}.mp3"
        audio_path = temporary_root / audio_name
        attribution_name = "attribution.jamendo.json"
        attribution_path = temporary_root / attribution_name
        try:
            self._wait_for_request_slot()
            byte_count = self._provider.acquire(
                candidate,
                audio_path,
                max_bytes=self._max_download_bytes,
            )
            attribution_path.write_text(
                json.dumps(
                    {
                        "acquisition_state": "STAGED_NOT_READY",
                        "album": candidate.album,
                        "artist": candidate.artist,
                        "byte_count": byte_count,
                        "download_permission": "audiodownload_allowed=true",
                        "license_url": candidate.license_url,
                        "operation_id": str(operation_id),
                        "provider": "Jamendo",
                        "provider_api_version": "3.0",
                        "provider_track_id": candidate.provider_track_id,
                        "share_url": candidate.share_url,
                        "title": candidate.title,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            try:
                os.rename(temporary_root, operation_root)
            except OSError as error:
                if operation_root.exists():
                    return self._existing(
                        operation_root, provider_track_id, operation_id, duplicate=True
                    )
                raise DiscoveryError("discovery_staging_unavailable") from error
            return StagedAcquisition(
                str(operation_id),
                candidate.provider_track_id,
                audio_name,
                attribution_name,
                byte_count,
            )
        except DiscoveryError:
            raise
        except (OSError, UnicodeError, TypeError, ValueError) as error:
            raise DiscoveryError("discovery_staging_unavailable") from error
        finally:
            if temporary_root.exists():
                shutil.rmtree(temporary_root, ignore_errors=True)

    def staged_audio_path(self, owner_id: UUID, result: StagedAcquisition) -> Path:
        """Return one receipt-verified path for the internal Vault handoff only."""

        try:
            operation_id = UUID(result.operation_id)
        except ValueError as error:
            raise DiscoveryError("discovery_operation_conflict") from error
        operation_root = self._staging_root / str(owner_id) / "jamendo" / str(operation_id)
        verified = self._existing(
            operation_root,
            result.provider_track_id,
            operation_id,
            duplicate=result.duplicate,
        )
        if verified.audio_name != result.audio_name:
            raise DiscoveryError("discovery_operation_conflict")
        candidate = (operation_root / verified.audio_name).resolve(strict=True)
        try:
            candidate.relative_to(operation_root.resolve(strict=True))
        except (OSError, ValueError) as error:
            raise DiscoveryError("discovery_staging_unavailable") from error
        return candidate

    def lookup_for_acquisition(self, provider_track_id: str) -> DiscoveryCandidate:
        """Revalidate current provider permission before the materialization transaction."""

        self._wait_for_request_slot()
        candidate = self._provider.lookup(provider_track_id)
        if not candidate.acquisition_allowed:
            raise DiscoveryError("discovery_not_eligible")
        return candidate

    def _existing(
        self,
        operation_root: Path,
        provider_track_id: str,
        operation_id: UUID,
        *,
        duplicate: bool = True,
    ) -> StagedAcquisition:
        try:
            attribution_path = operation_root / "attribution.jamendo.json"
            document = json.loads(attribution_path.read_text(encoding="utf-8"))
            audio_files = tuple(operation_root.glob("*.mp3"))
            if (
                not isinstance(document, dict)
                or document.get("operation_id") != str(operation_id)
                or document.get("provider_track_id") != provider_track_id
                or len(audio_files) != 1
                or not audio_files[0].is_file()
                or not isinstance(document.get("byte_count"), int)
                or document["byte_count"] != audio_files[0].stat().st_size
            ):
                raise ValueError("staging receipt mismatch")
            return StagedAcquisition(
                str(operation_id),
                provider_track_id,
                audio_files[0].name,
                attribution_path.name,
                document["byte_count"],
                duplicate,
            )
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise DiscoveryError("discovery_operation_conflict") from error

    def _wait_for_request_slot(self) -> None:
        with self._gate_lock:
            now = self._monotonic_clock()
            delay = self._request_interval - (now - self._last_request)
            if delay > 0:
                self._sleeper(delay)
            self._last_request = self._monotonic_clock()


def _filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _INVALID_FILENAME.sub("_", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    return normalized[:90].rstrip(" .") or "track"


def _identity_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


__all__ = ("ManualDiscoveryService",)
