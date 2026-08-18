"""GPU-only resolution of verified local Vault audio sources."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from autplay.adapters.postgresql.models.vault import (
    AudioVariantRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.domain.enrichment import EmbeddingJobTarget
from sqlalchemy import select
from sqlalchemy.orm import Session

from .preprocessing import AudioPreprocessingError, VerifiedAudioSource

type SessionFactory = Callable[[], Session]


class PostgresFilesystemAudioSourceResolver:
    """Resolve one typed enrichment target to verified immutable local CAS bytes."""

    def __init__(self, sessions: SessionFactory, vault_root: Path) -> None:
        if not vault_root.is_absolute():
            raise ValueError("Vault root must be absolute")
        self._sessions = sessions
        self._vault_root = vault_root.resolve(strict=False)

    def resolve(self, target: EmbeddingJobTarget) -> VerifiedAudioSource:
        """Fail closed unless target, object and local replica are all eligible."""

        with self._sessions() as session:
            row = session.execute(
                select(
                    AudioVariantRow.duration_ms,
                    VaultObjectRow.byte_size,
                    VaultObjectRow.sha256,
                    VaultReplicaRow.storage_key,
                )
                .join(
                    VaultObjectRow,
                    VaultObjectRow.vault_object_id == AudioVariantRow.vault_object_id,
                )
                .join(
                    VaultReplicaRow,
                    VaultReplicaRow.vault_object_id == VaultObjectRow.vault_object_id,
                )
                .where(
                    AudioVariantRow.audio_variant_id == target.audio_variant_id,
                    AudioVariantRow.recording_id == target.recording_id,
                    AudioVariantRow.validation_status == "VALID",
                    AudioVariantRow.deleted_at.is_(None),
                    VaultObjectRow.commit_status == "COMMITTED",
                    VaultReplicaRow.storage_backend == "LOCAL_FILESYSTEM",
                    VaultReplicaRow.replica_status == "AVAILABLE",
                    VaultReplicaRow.verified_at.is_not(None),
                )
                .limit(1)
            ).one_or_none()
        if row is None:
            raise AudioPreprocessingError("verified Vault source metadata is unavailable")
        duration_ms, byte_size, sha256, storage_key = row
        digest = bytes(sha256)
        if len(digest) != 32 or storage_key != digest.hex():
            raise AudioPreprocessingError("Vault replica identity does not match immutable bytes")
        path = self._safe_object_path(digest.hex())
        if path.stat().st_size != int(byte_size) or _sha256(path) != digest:
            raise AudioPreprocessingError("Vault source bytes do not match verified metadata")
        return VerifiedAudioSource(
            path=path,
            duration_ms=int(duration_ms),
            byte_size=int(byte_size),
            sha256=digest,
        )

    def _safe_object_path(self, digest: str) -> Path:
        components = ("objects", digest[:2], digest[2:4], digest)
        candidate = self._vault_root
        try:
            for component in components:
                candidate /= component
                if candidate.is_symlink():
                    raise OSError("Vault object path contains a symbolic link")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._vault_root)
        except (OSError, ValueError) as error:
            raise AudioPreprocessingError("verified Vault source file is unavailable") from error
        if not resolved.is_file():
            raise AudioPreprocessingError("verified Vault source is not a regular file")
        return resolved


def _sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    except OSError as error:
        raise AudioPreprocessingError("verified Vault source cannot be read") from error
    return digest.digest()


__all__ = ("PostgresFilesystemAudioSourceResolver",)
