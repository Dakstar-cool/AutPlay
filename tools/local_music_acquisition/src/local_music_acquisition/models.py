"""Small provider-neutral contracts shared by the local acquisition contours."""

from __future__ import annotations

from dataclasses import dataclass

PROVIDER_MISS_CODES = frozenset(
    {
        "exact_match_not_found",
        "ambiguous_match",
        "drm_protected",
        "preview_only",
        "source_unavailable",
        "original_download_unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class PlaylistItem:
    """One sanitized playlist row."""

    row_number: int
    artist: str
    title: str
    album: str | None = None
    error_code: str | None = None
    expected_duration_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class AcquiredArtifact:
    """Provider result with a truncated byte fingerprint for correlation only."""

    provider: str
    artifact_ref: str
    identity_version: str | None = None
    expected_duration_seconds: float | None = None


class ProviderMiss(RuntimeError):
    """A safe, genuine no-match result that permits the next contour."""

    def __init__(self, provider: str, code: str) -> None:
        super().__init__(f"{provider}.{code}")
        self.provider = provider
        self.code = code


class ProviderFailure(RuntimeError):
    """A terminal provider failure handled by the configured fallback policy."""

    def __init__(self, provider: str, code: str) -> None:
        super().__init__(f"{provider}.{code}")
        self.provider = provider
        self.code = code
