"""Small provider-neutral contracts shared by the local acquisition contours."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlaylistItem:
    """One sanitized playlist row."""

    row_number: int
    artist: str
    title: str
    album: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class AcquiredArtifact:
    """Provider result with a truncated byte fingerprint for correlation only."""

    provider: str
    artifact_ref: str


class ProviderMiss(RuntimeError):
    """A safe, genuine no-match result that permits the next contour."""

    def __init__(self, provider: str, code: str) -> None:
        super().__init__(f"{provider}.{code}")
        self.provider = provider
        self.code = code


class ProviderFailure(RuntimeError):
    """A provider/permission/transport failure that must stop fallback for the row."""

    def __init__(self, provider: str, code: str) -> None:
        super().__init__(f"{provider}.{code}")
        self.provider = provider
        self.code = code
