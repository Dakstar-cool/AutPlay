"""Hitmo CDP contour using the existing exact-match browser workflow."""

from __future__ import annotations

import re
from pathlib import Path

from ..matching import SEARCH_LIMIT
from ..models import AcquiredArtifact, PlaylistItem, ProviderFailure, ProviderMiss
from ..related import RelatedCandidate
from . import hitmo

_SAFE_CODE = re.compile(r"[a-z0-9_.-]{1,100}")


class HitmoProvider:
    """Acquire one exact Hitmo result through an already running local CDP browser."""

    name = "hitmo"
    requires_rights_confirmation = True
    requires_proxy = False

    def __init__(
        self,
        *,
        cdp_endpoint: str,
        timeout_seconds: float = 120.0,
        max_bytes: int = 200 * 1024 * 1024,
    ) -> None:
        self._cdp_endpoint = cdp_endpoint
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes

    def discover(self, item: PlaylistItem) -> list[RelatedCandidate]:
        candidates: list[dict[str, object]] = []
        try:
            summary = hitmo.download_hitmo_tracks(
                title=item.title,
                artist=item.artist,
                result_limit=SEARCH_LIMIT,
                timeout_seconds=self._timeout_seconds,
                download=False,
                browser="cdp",
                cdp_endpoint=self._cdp_endpoint,
                candidate_sink=candidates,
            )
            results = summary.get("results")
            if (
                not isinstance(results, list)
                or len(results) != 1
                or not isinstance(results[0], dict)
            ):
                raise ProviderFailure(self.name, "result_invalid")
            status = results[0].get("status")
            if status not in {"matched", "exact_match_not_found"}:
                code = (
                    status
                    if isinstance(status, str) and _SAFE_CODE.fullmatch(status)
                    else "result_invalid"
                )
                raise ProviderFailure(self.name, code)
            return [RelatedCandidate.parse(c, self.name) for c in candidates]
        except ProviderFailure:
            raise
        except (RuntimeError, ValueError, TypeError, AttributeError) as error:
            code = str(error)
            raise ProviderFailure(
                self.name, code if _SAFE_CODE.fullmatch(code) else "discovery_failed"
            ) from error

    def acquire(self, item: PlaylistItem, output_directory: Path) -> AcquiredArtifact:
        try:
            summary = hitmo.download_hitmo_tracks(
                title=item.title,
                artist=item.artist,
                download_dir=output_directory,
                result_limit=SEARCH_LIMIT,
                timeout_seconds=self._timeout_seconds,
                max_bytes=self._max_bytes,
                download=True,
                rights_confirmed=True,
                browser="cdp",
                cdp_endpoint=self._cdp_endpoint,
            )
        except RuntimeError as error:
            code = str(error)
            if _SAFE_CODE.fullmatch(code) is None:
                code = "provider_failed"
            raise ProviderFailure(self.name, code) from error
        except OSError as error:
            raise ProviderFailure(self.name, "operational_failure") from error
        results = summary.get("results")
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            raise ProviderFailure(self.name, "result_invalid")
        result = results[0]
        status = result.get("status")
        if status == "exact_match_not_found":
            raise ProviderMiss(self.name, "exact_match_not_found")
        if status != "downloaded":
            code = (
                status
                if isinstance(status, str) and _SAFE_CODE.fullmatch(status)
                else "result_invalid"
            )
            raise ProviderFailure(self.name, code)
        artifact_ref = result.get("file_ref")
        if (
            not isinstance(artifact_ref, str)
            or re.fullmatch(r"sha256:[0-9a-f]{12}", artifact_ref) is None
        ):
            raise ProviderFailure(self.name, "result_invalid")
        expected = result.get("expected_duration_seconds")
        return AcquiredArtifact(
            self.name,
            artifact_ref,
            identity_version="recording-match-v2",
            expected_duration_seconds=float(expected)
            if isinstance(expected, (int, float))
            else None,
        )
