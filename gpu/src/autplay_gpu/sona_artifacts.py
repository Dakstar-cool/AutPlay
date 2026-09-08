"""Hash-addressed Sona ONNX artifact, manifest and commit-marker verification."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import cast

import rfc8785
from autplay.domain.recommendations import JsonValue

from .embedding import ModelArtifactError

MAX_SONA_ARTIFACT_BYTES = 536_870_912
MAX_SONA_MANIFEST_BYTES = 131_072
MAX_SONA_COMMIT_BYTES = 16_384


@dataclass(frozen=True, slots=True)
class VerifiedSonaArtifact:
    path: Path
    artifact_sha256: str
    model_manifest_sha256: str
    tokenizer_sha256: str
    quality_eligible: bool
    payload: bytes = field(default=b"", repr=False)


class SonaArtifactStore:
    """Resolve an allowlisted committed graph without trusting a request-supplied path."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("Sona model cache root must be absolute")
        self._root = root.resolve(strict=False)

    def resolve(
        self,
        *,
        artifact_sha256: str,
        model_manifest_sha256: str,
        tokenizer_sha256: str,
    ) -> VerifiedSonaArtifact:
        for digest in (artifact_sha256, model_manifest_sha256, tokenizer_sha256):
            _validate_digest(digest)
        artifact = self._object_path(artifact_sha256)
        manifest_path = self._sidecar_path(artifact, ".manifest.json")
        commit_path = self._sidecar_path(artifact, ".commit.json")
        payload = _bounded_read(artifact, MAX_SONA_ARTIFACT_BYTES)
        if sha256(payload).hexdigest() != artifact_sha256:
            raise ModelArtifactError("Sona artifact hash mismatch")
        manifest_envelope = _read_object(manifest_path, MAX_SONA_MANIFEST_BYTES)
        if set(manifest_envelope) != {"manifest", "manifest_sha256"}:
            raise ModelArtifactError("Sona manifest envelope is invalid")
        raw_manifest = manifest_envelope.get("manifest")
        if not isinstance(raw_manifest, dict):
            raise ModelArtifactError("Sona manifest is invalid")
        manifest = cast(dict[str, JsonValue], raw_manifest)
        declared_manifest_sha256 = manifest_envelope.get("manifest_sha256")
        if (
            declared_manifest_sha256 != model_manifest_sha256
            or sha256(rfc8785.dumps(manifest)).hexdigest() != model_manifest_sha256
            or manifest.get("artifact_sha256") != artifact_sha256
        ):
            raise ModelArtifactError("Sona manifest identity mismatch")
        provenance = manifest.get("training_provenance")
        if not isinstance(provenance, dict):
            raise ModelArtifactError("Sona training provenance is invalid")
        if provenance.get("tokenizer_sha256") != tokenizer_sha256:
            raise ModelArtifactError("Sona tokenizer provenance mismatch")
        quality_eligible = provenance.get("quality_eligible")
        if not isinstance(quality_eligible, bool):
            raise ModelArtifactError("Sona quality eligibility is invalid")
        commit_envelope = _read_object(commit_path, MAX_SONA_COMMIT_BYTES)
        if set(commit_envelope) != {"commit", "commit_sha256"}:
            raise ModelArtifactError("Sona commit envelope is invalid")
        raw_commit = commit_envelope.get("commit")
        if not isinstance(raw_commit, dict):
            raise ModelArtifactError("Sona commit marker is invalid")
        commit = cast(dict[str, JsonValue], raw_commit)
        commit_sha256 = commit_envelope.get("commit_sha256")
        if (
            not isinstance(commit_sha256, str)
            or sha256(rfc8785.dumps(commit)).hexdigest() != commit_sha256
            or commit
            != {
                "schema_version": 1,
                "state": "COMMITTED",
                "artifact_sha256": artifact_sha256,
                "model_manifest_sha256": model_manifest_sha256,
            }
        ):
            raise ModelArtifactError("Sona commit marker identity mismatch")
        return VerifiedSonaArtifact(
            artifact,
            artifact_sha256,
            model_manifest_sha256,
            tokenizer_sha256,
            quality_eligible,
            payload,
        )

    def _object_path(self, digest: str) -> Path:
        candidate = self._root
        try:
            for component in ("objects", digest[:2], digest):
                candidate /= component
                if candidate.is_symlink():
                    raise OSError("Sona artifact path contains a symbolic link")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._root)
        except (OSError, ValueError) as error:
            raise ModelArtifactError("approved Sona artifact is unavailable") from error
        if not resolved.is_file():
            raise ModelArtifactError("approved Sona artifact is not a regular file")
        return resolved

    def _sidecar_path(self, artifact: Path, suffix: str) -> Path:
        candidate = Path(f"{artifact}{suffix}")
        try:
            if candidate.is_symlink():
                raise OSError("Sona sidecar is a symbolic link")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._root)
        except (OSError, ValueError) as error:
            raise ModelArtifactError("Sona artifact sidecar is unavailable") from error
        if not resolved.is_file():
            raise ModelArtifactError("Sona artifact sidecar is not a regular file")
        return resolved


def _bounded_read(path: Path, maximum: int) -> bytes:
    size = path.stat().st_size
    if size < 1 or size > maximum:
        raise ModelArtifactError("Sona artifact size is invalid")
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if len(payload) != size or len(payload) > maximum:
        raise ModelArtifactError("Sona artifact changed while reading")
    return payload


def _read_object(path: Path, maximum: int) -> dict[str, object]:
    payload = _bounded_read(path, maximum)
    if len(payload) < 2:
        raise ModelArtifactError("Sona sidecar size is invalid")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelArtifactError("Sona sidecar JSON is invalid") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ModelArtifactError("Sona sidecar must be an object")
    return cast(dict[str, object], value)


def _validate_digest(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("Sona artifact hash is invalid")


__all__ = (
    "MAX_SONA_ARTIFACT_BYTES",
    "SonaArtifactStore",
    "VerifiedSonaArtifact",
)
