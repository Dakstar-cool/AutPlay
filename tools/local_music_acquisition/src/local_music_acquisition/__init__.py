"""Portable, local-only music acquisition pipeline."""

from .models import AcquiredArtifact, PlaylistItem, ProviderFailure, ProviderMiss
from .orchestrator import download_playlist

__all__ = [
    "AcquiredArtifact",
    "PlaylistItem",
    "ProviderFailure",
    "ProviderMiss",
    "download_playlist",
]
