"""Verified immutable Recording-to-Semantic-ID mapping for Sona shadow requests."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import cast
from uuid import UUID

import rfc8785

from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import SONA_CODEBOOK_SIZE, SonaSemanticId

MAX_TOKENIZER_MANIFEST_BYTES = 65_536
MAX_TOKENIZER_RECORDINGS = 65_536
MAX_TOKENIZER_MAPPING_BYTES = MAX_TOKENIZER_RECORDINGS * 160 + 4_096
MAX_ACTIVE_TOKENIZER_CODES = 256
MAX_EMBEDDING_DIMENSIONS = 1_024
TOKENIZER_MINIBATCH_SIZE = 1_024
TOKENIZER_ALGORITHM = "DETERMINISTIC_RESIDUAL_MINIBATCH_KMEANS_V1"
TOKENIZER_MANIFEST_KEYS = {
    "active_codes_per_level",
    "algorithm",
    "centroids_sha256",
    "configured_codebook_size",
    "embedding_dimensions",
    "fit_manifest_sha256",
    "iterations",
    "mapping_sha256",
    "max_active_codes",
    "mini_batch_size",
    "recording_count",
    "schema_version",
    "seed",
    "source_embeddings_retained",
    "source_embeddings_sha256",
}


class VerifiedSonaSemanticIdReader:
    """Load one hash-bound mapping once and expose only the requested recording IDs."""

    def __init__(
        self,
        tokenizer_root: Path,
        *,
        tokenizer_sha256: str,
        tokenizer_manifest_sha256: str,
    ) -> None:
        if not tokenizer_root.is_absolute():
            raise ValueError("Sona tokenizer root must be absolute")
        root = tokenizer_root.resolve(strict=True)
        manifest_path = _regular_child(root, "manifest.json")
        mapping_path = _regular_child(root, "mapping.json")
        manifest_bytes = _bounded_read(manifest_path, MAX_TOKENIZER_MANIFEST_BYTES)
        envelope = _json_object(manifest_bytes, "Sona tokenizer manifest")
        if set(envelope) != {"manifest", "manifest_sha256"}:
            raise ValueError("Sona tokenizer manifest envelope is invalid")
        manifest = envelope.get("manifest")
        if not isinstance(manifest, dict):
            raise ValueError("Sona tokenizer manifest is invalid")
        manifest = cast(dict[str, JsonValue], manifest)
        declared_manifest_sha256 = envelope.get("manifest_sha256")
        computed_manifest_sha256 = sha256(rfc8785.dumps(manifest)).hexdigest()
        if (
            declared_manifest_sha256 != tokenizer_manifest_sha256
            or computed_manifest_sha256 != tokenizer_manifest_sha256
        ):
            raise ValueError("Sona tokenizer manifest identity mismatch")
        if set(manifest) != TOKENIZER_MANIFEST_KEYS:
            raise ValueError("Sona tokenizer manifest keys are invalid")
        if manifest.get("fit_manifest_sha256") != tokenizer_sha256:
            raise ValueError("Sona tokenizer identity mismatch")
        recording_count = _manifest_integer(manifest, "recording_count")
        configured_codebook_size = _manifest_integer(manifest, "configured_codebook_size")
        active_codes = _manifest_integer(manifest, "active_codes_per_level")
        embedding_dimensions = _manifest_integer(manifest, "embedding_dimensions")
        iterations = _manifest_integer(manifest, "iterations")
        seed = _manifest_integer(manifest, "seed")
        if (
            _manifest_integer(manifest, "schema_version") != 1
            or manifest.get("algorithm") != TOKENIZER_ALGORITHM
            or not 1 <= recording_count <= MAX_TOKENIZER_RECORDINGS
            or not 2 <= configured_codebook_size <= SONA_CODEBOOK_SIZE
            or active_codes
            != min(configured_codebook_size - 1, recording_count, MAX_ACTIVE_TOKENIZER_CODES)
            or not 1 <= embedding_dimensions <= MAX_EMBEDDING_DIMENSIONS
            or _manifest_integer(manifest, "max_active_codes") != MAX_ACTIVE_TOKENIZER_CODES
            or _manifest_integer(manifest, "mini_batch_size") != TOKENIZER_MINIBATCH_SIZE
            or not 1 <= iterations <= 100
            or seed < 0
            or manifest.get("source_embeddings_retained") is not False
        ):
            raise ValueError("Sona tokenizer manifest bounds are invalid")
        for digest_key in ("centroids_sha256", "mapping_sha256", "source_embeddings_sha256"):
            _manifest_digest(manifest, digest_key)
        mapping_bytes = _bounded_read(
            mapping_path,
            min(MAX_TOKENIZER_MAPPING_BYTES, recording_count * 160 + 4_096),
        )
        if manifest.get("mapping_sha256") != sha256(mapping_bytes).hexdigest():
            raise ValueError("Sona tokenizer mapping hash mismatch")
        raw_mapping = _json_array(mapping_bytes, "Sona tokenizer mapping")
        if len(raw_mapping) != recording_count:
            raise ValueError("Sona tokenizer recording count mismatch")
        parsed: dict[UUID, SonaSemanticId] = {}
        order: list[str] = []
        for raw in raw_mapping:
            if not isinstance(raw, dict) or set(raw) != {"recording_id", "semantic_id"}:
                raise ValueError("Sona tokenizer mapping row is invalid")
            recording_value = raw.get("recording_id")
            semantic_value = raw.get("semantic_id")
            if not isinstance(recording_value, str) or not isinstance(semantic_value, list):
                raise ValueError("Sona tokenizer mapping row types are invalid")
            if len(semantic_value) != 3 or any(
                isinstance(value, bool) or not isinstance(value, int) for value in semantic_value
            ):
                raise ValueError("Sona tokenizer Semantic ID is invalid")
            recording_id = UUID(recording_value)
            if recording_id in parsed:
                raise ValueError("Sona tokenizer contains duplicate recordings")
            semantic_id = SonaSemanticId(*cast(list[int], semantic_value))
            if any(code <= 0 or code > active_codes for code in semantic_id.values):
                raise ValueError("Sona tokenizer mapping references an inactive code")
            parsed[recording_id] = semantic_id
            order.append(recording_id.hex)
        if order != sorted(order):
            raise ValueError("Sona tokenizer mapping order is unstable")
        self._tokenizer_sha256 = tokenizer_sha256
        self._tokenizer_manifest_sha256 = tokenizer_manifest_sha256
        self._mapping: Mapping[UUID, SonaSemanticId] = MappingProxyType(parsed)
        reverse: dict[SonaSemanticId, list[UUID]] = {}
        for recording_id, semantic_id in parsed.items():
            reverse.setdefault(semantic_id, []).append(recording_id)
        self._reverse: Mapping[SonaSemanticId, tuple[UUID, ...]] = MappingProxyType(
            {
                semantic_id: tuple(sorted(recording_ids, key=lambda value: value.hex))
                for semantic_id, recording_ids in reverse.items()
            }
        )

    @property
    def tokenizer_manifest_sha256(self) -> str:
        return self._tokenizer_manifest_sha256

    def load(
        self, recording_ids: Sequence[UUID], *, tokenizer_sha256: str
    ) -> Mapping[UUID, SonaSemanticId]:
        if tokenizer_sha256 != self._tokenizer_sha256:
            raise RuntimeError("Sona tokenizer version is unavailable")
        return MappingProxyType(
            {
                recording_id: self._mapping[recording_id]
                for recording_id in dict.fromkeys(recording_ids)
                if recording_id in self._mapping
            }
        )

    def expand(
        self, semantic_ids: Sequence[SonaSemanticId], *, tokenizer_sha256: str
    ) -> Mapping[SonaSemanticId, tuple[UUID, ...]]:
        if tokenizer_sha256 != self._tokenizer_sha256:
            raise RuntimeError("Sona tokenizer version is unavailable")
        return MappingProxyType(
            {
                semantic_id: self._reverse[semantic_id]
                for semantic_id in dict.fromkeys(semantic_ids)
                if semantic_id in self._reverse
            }
        )


def _regular_child(root: Path, name: str) -> Path:
    candidate = root / name
    if candidate.is_symlink():
        raise ValueError("Sona tokenizer path cannot be a symbolic link")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("Sona tokenizer path escapes its root") from error
    if not resolved.is_file():
        raise ValueError("Sona tokenizer artifact is not a regular file")
    return resolved


def _bounded_read(path: Path, maximum: int) -> bytes:
    size = path.stat().st_size
    if size < 2 or size > maximum:
        raise ValueError("Sona tokenizer artifact size is invalid")
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if len(payload) != size or len(payload) > maximum:
        raise ValueError("Sona tokenizer artifact changed while reading")
    return payload


def _json_object(payload: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _json_array(payload: bytes, label: str) -> list[object]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return cast(list[object], value)


def _manifest_integer(manifest: Mapping[str, JsonValue], key: str) -> int:
    value = manifest.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Sona tokenizer {key} must be an integer")
    return value


def _manifest_digest(manifest: Mapping[str, JsonValue], key: str) -> str:
    value = manifest.get(key)
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"Sona tokenizer {key} must be a SHA-256 digest")
    return value


__all__ = (
    "MAX_TOKENIZER_MANIFEST_BYTES",
    "MAX_TOKENIZER_MAPPING_BYTES",
    "MAX_TOKENIZER_RECORDINGS",
    "VerifiedSonaSemanticIdReader",
)
