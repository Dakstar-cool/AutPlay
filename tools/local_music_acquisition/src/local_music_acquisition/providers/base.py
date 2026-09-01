"""Provider protocol for the sequential acquisition pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import AcquiredArtifact, PlaylistItem


class AcquisitionProvider(Protocol):
    """One ordered provider contour."""

    name: str
    requires_rights_confirmation: bool

    def acquire(self, item: PlaylistItem, output_directory: Path) -> AcquiredArtifact: ...
