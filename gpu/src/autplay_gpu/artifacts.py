"""Private hash-addressed model artifact cache for the isolated GPU process."""

from __future__ import annotations

from pathlib import Path

from autplay.domain.enrichment import ApprovedEmbeddingModel

from .embedding import ModelArtifactError


class ModelArtifactStore:
    """Resolve a registry model only through its immutable weights hash."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("model cache root must be absolute")
        self._root = root.resolve(strict=False)

    def resolve(self, model: ApprovedEmbeddingModel) -> Path:
        """Return ``objects/<prefix>/<sha256>`` without trusting a job path or URL."""

        digest = model.weights_sha256.hex()
        components = ("objects", digest[:2], digest)
        candidate = self._root
        try:
            for component in components:
                candidate /= component
                if candidate.is_symlink():
                    raise OSError("model cache path contains a symbolic link")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._root)
        except (OSError, ValueError) as error:
            raise ModelArtifactError("approved model artifact is unavailable") from error
        if not resolved.is_file():
            raise ModelArtifactError("approved model artifact is not a regular file")
        return resolved


__all__ = ("ModelArtifactStore",)
