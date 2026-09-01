"""Jamendo contour backed by its official download-permission API."""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

from ..models import AcquiredArtifact, PlaylistItem, ProviderFailure, ProviderMiss
from . import jamendo


def _identity(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _content_ref(path: Path) -> str:
    """Return a bounded correlation fingerprint of the downloaded bytes."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()[:12]}"


class JamendoProvider:
    """Acquire only an exact normalized artist/title result from Jamendo."""

    name = "jamendo"
    requires_rights_confirmation = False

    def __init__(
        self,
        client_id_file: Path,
        *,
        limit: int = 20,
        timeout_seconds: int = 15,
        max_bytes: int = jamendo.DEFAULT_MAX_DOWNLOAD_BYTES,
    ) -> None:
        self._transport = jamendo.CurlTransport(client_id_file, timeout_seconds=timeout_seconds)
        self._limit = limit
        self._max_bytes = max_bytes

    def acquire(self, item: PlaylistItem, output_directory: Path) -> AcquiredArtifact:
        try:
            candidates = jamendo.search_tracks(
                f"{item.artist} - {item.title}", transport=self._transport, limit=self._limit
            )
            exact = [
                candidate
                for candidate in candidates
                if _identity(candidate.artist) == _identity(item.artist)
                and _identity(candidate.title) == _identity(item.title)
            ]
            if not exact:
                raise ProviderMiss(self.name, "exact_match_not_found")
            result = jamendo.download_track(
                jamendo.RankedTrack(exact[0], 1.0),
                output_directory,
                transport=self._transport,
                max_bytes=self._max_bytes,
            )
            artifact_ref = _content_ref(result.audio_path)
        except ProviderMiss:
            raise
        except jamendo.JamendoToolError as error:
            raise ProviderFailure(self.name, error.code) from error
        except OSError as error:
            raise ProviderFailure(self.name, "operational_failure") from error
        return AcquiredArtifact(self.name, artifact_ref)
