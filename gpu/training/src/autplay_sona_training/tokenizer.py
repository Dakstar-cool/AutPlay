"""Deterministic learned residual tokenizer for three-level Sona Semantic IDs."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import UUID

import numpy as np
import numpy.typing as npt
import rfc8785
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import SONA_CODEBOOK_SIZE, SONA_SID_DEPTH, SonaSemanticId

from .npy import read_canonical_npy

type Float32Array = npt.NDArray[np.float32]
type Int64Array = npt.NDArray[np.int64]

SONA_MAX_TOKENIZER_RECORDINGS = 65_536
SONA_MAX_EMBEDDING_DIMENSIONS = 1_024
SONA_MAX_ACTIVE_TOKENIZER_CODES = 256
SONA_TOKENIZER_MINIBATCH_SIZE = 1_024
SONA_TOKENIZER_ASSIGNMENT_BLOCK = 1_024
SONA_MAX_TOKENIZER_MANIFEST_BYTES = 1_048_576
_TOKENIZER_ALGORITHM = "DETERMINISTIC_RESIDUAL_MINIBATCH_KMEANS_V1"


@dataclass(frozen=True, slots=True)
class SonaTokenizerFit:
    """Immutable in-memory tokenizer result ready for artifact serialization."""

    recording_ids: tuple[UUID, ...]
    semantic_ids: tuple[SonaSemanticId, ...]
    centroids: Float32Array
    source_embeddings_sha256: str
    manifest_sha256: str
    configured_codebook_size: int
    iterations: int
    seed: int

    def __post_init__(self) -> None:
        if not 2 <= self.configured_codebook_size <= SONA_CODEBOOK_SIZE:
            raise ValueError("Sona tokenizer configured codebook is outside canonical bounds")
        if not 1 <= self.iterations <= 100 or self.seed < 0:
            raise ValueError("Sona tokenizer fit parameters are outside canonical bounds")
        if len(self.recording_ids) != len(self.semantic_ids) or len(set(self.recording_ids)) != len(
            self.recording_ids
        ):
            raise ValueError("Sona tokenizer mapping must contain unique aligned recordings")
        if (
            self.centroids.ndim != 3
            or self.centroids.shape[0] != SONA_SID_DEPTH
            or self.centroids.shape[1]
            != min(
                self.configured_codebook_size - 1,
                len(self.recording_ids),
                SONA_MAX_ACTIVE_TOKENIZER_CODES,
            )
            or not 1 <= self.centroids.shape[2] <= SONA_MAX_EMBEDDING_DIMENSIONS
            or self.centroids.dtype != np.float32
            or not np.isfinite(self.centroids).all()
        ):
            raise ValueError("Sona tokenizer centroids have an invalid shape or dtype")
        active_codes = self.centroids.shape[1]
        if any(
            any(code <= 0 or code > active_codes for code in semantic_id.values)
            for semantic_id in self.semantic_ids
        ):
            raise ValueError("Sona tokenizer mapping references code without a centroid")
        for digest in (self.source_embeddings_sha256, self.manifest_sha256):
            if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
                raise ValueError("Sona tokenizer hash is invalid")

    def mapping(self) -> dict[UUID, SonaSemanticId]:
        return dict(zip(self.recording_ids, self.semantic_ids, strict=True))


def fit_residual_tokenizer(
    recording_ids: tuple[UUID, ...],
    embeddings: npt.NDArray[np.floating],
    *,
    source_embeddings_sha256: str,
    codebook_size: int = SONA_CODEBOOK_SIZE,
    iterations: int = 20,
    seed: int = 0,
) -> SonaTokenizerFit:
    """Fit three deterministic residual k-means levels with code zero reserved."""

    if len(source_embeddings_sha256) != 64 or any(
        value not in "0123456789abcdef" for value in source_embeddings_sha256
    ):
        raise ValueError("source_embeddings_sha256 is invalid")
    values = np.asarray(embeddings, dtype=np.float32)
    if (
        not recording_ids
        or len(recording_ids) > SONA_MAX_TOKENIZER_RECORDINGS
        or len(recording_ids) != len(set(recording_ids))
        or values.ndim != 2
        or values.shape[0] != len(recording_ids)
        or not 1 <= values.shape[1] <= SONA_MAX_EMBEDDING_DIMENSIONS
        or not np.isfinite(values).all()
    ):
        raise ValueError("Sona tokenizer inputs are empty, misaligned, duplicate, or non-finite")
    if not 2 <= codebook_size <= SONA_CODEBOOK_SIZE:
        raise ValueError("Sona tokenizer codebook size is outside the accepted bound")
    if not 1 <= iterations <= 100 or seed < 0:
        raise ValueError("Sona tokenizer iterations or seed are invalid")

    order = tuple(sorted(range(len(recording_ids)), key=lambda index: recording_ids[index].hex))
    ordered_ids = tuple(recording_ids[index] for index in order)
    ordered = values[np.asarray(order, dtype=np.int64)].copy()
    computed_source_sha256 = _source_embeddings_sha256(ordered_ids, ordered)
    if computed_source_sha256 != source_embeddings_sha256:
        raise ValueError("source_embeddings_sha256 does not match canonical embedding bytes")
    norms = np.linalg.norm(ordered, axis=1, keepdims=True)
    ordered = np.divide(ordered, norms, out=np.zeros_like(ordered), where=norms > 0.0)
    residual = ordered.copy()
    active_codes = min(
        codebook_size - 1,
        len(ordered_ids),
        SONA_MAX_ACTIVE_TOKENIZER_CODES,
    )
    levels: list[Float32Array] = []
    assignments: list[Int64Array] = []
    for level in range(SONA_SID_DEPTH):
        centroids, assignment = _fit_kmeans(
            residual,
            cluster_count=active_codes,
            iterations=iterations,
            seed=seed + level,
        )
        levels.append(centroids)
        assignments.append(assignment + 1)
        residual = residual - centroids[assignment]
    centroid_tensor = np.stack(levels).astype(np.float32, copy=False)
    semantic_ids = tuple(
        SonaSemanticId(*(int(assignments[level][index]) for level in range(SONA_SID_DEPTH)))
        for index in range(len(ordered_ids))
    )
    centroid_sha256 = sha256(centroid_tensor.astype("<f4", copy=False).tobytes()).hexdigest()
    document = {
        "schema_version": 1,
        "algorithm": _TOKENIZER_ALGORITHM,
        "semantic_id_depth": SONA_SID_DEPTH,
        "configured_codebook_size": codebook_size,
        "active_codes_per_level": active_codes,
        "max_active_codes": SONA_MAX_ACTIVE_TOKENIZER_CODES,
        "mini_batch_size": SONA_TOKENIZER_MINIBATCH_SIZE,
        "embedding_dimensions": values.shape[1],
        "iterations": iterations,
        "seed": seed,
        "source_embeddings_sha256": source_embeddings_sha256,
        "centroids_sha256": centroid_sha256,
        "mapping": [
            {"recording_id": str(recording_id), "semantic_id": list(semantic_id.values)}
            for recording_id, semantic_id in zip(ordered_ids, semantic_ids, strict=True)
        ],
    }
    manifest_sha256 = sha256(rfc8785.dumps(document)).hexdigest()
    return SonaTokenizerFit(
        recording_ids=ordered_ids,
        semantic_ids=semantic_ids,
        centroids=centroid_tensor,
        source_embeddings_sha256=source_embeddings_sha256,
        manifest_sha256=manifest_sha256,
        configured_codebook_size=codebook_size,
        iterations=iterations,
        seed=seed,
    )


def materialize_sona_tokenizer(fit: SonaTokenizerFit, output_directory: Path) -> str:
    """Write a hash-verified tokenizer without retaining source embeddings."""

    if output_directory.exists():
        raise FileExistsError(f"Sona tokenizer output already exists: {output_directory}")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=output_directory.parent)
    )
    try:
        centroids_path = temporary / "centroids.npy"
        with centroids_path.open("wb") as stream:
            np.save(stream, fit.centroids, allow_pickle=False)
        mapping: list[JsonValue] = [
            {"recording_id": str(recording_id), "semantic_id": list(semantic_id.values)}
            for recording_id, semantic_id in zip(fit.recording_ids, fit.semantic_ids, strict=True)
        ]
        mapping_path = temporary / "mapping.json"
        mapping_path.write_bytes(rfc8785.dumps(mapping))
        document: dict[str, JsonValue] = {
            "schema_version": 1,
            "algorithm": _TOKENIZER_ALGORITHM,
            "fit_manifest_sha256": fit.manifest_sha256,
            "source_embeddings_sha256": fit.source_embeddings_sha256,
            "source_embeddings_retained": False,
            "configured_codebook_size": fit.configured_codebook_size,
            "active_codes_per_level": fit.centroids.shape[1],
            "embedding_dimensions": fit.centroids.shape[2],
            "max_active_codes": SONA_MAX_ACTIVE_TOKENIZER_CODES,
            "mini_batch_size": SONA_TOKENIZER_MINIBATCH_SIZE,
            "iterations": fit.iterations,
            "seed": fit.seed,
            "centroids_sha256": sha256(centroids_path.read_bytes()).hexdigest(),
            "mapping_sha256": sha256(mapping_path.read_bytes()).hexdigest(),
            "recording_count": len(fit.recording_ids),
        }
        artifact_manifest_sha256 = sha256(rfc8785.dumps(document)).hexdigest()
        envelope: dict[str, JsonValue] = {
            "manifest": document,
            "manifest_sha256": artifact_manifest_sha256,
        }
        (temporary / "manifest.json").write_bytes(rfc8785.dumps(envelope))
        os.replace(temporary, output_directory)
        return artifact_manifest_sha256
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def load_sona_tokenizer(directory: Path) -> SonaTokenizerFit:
    """Load and verify an immutable tokenizer artifact."""

    manifest_path = directory / "manifest.json"
    if manifest_path.stat().st_size > SONA_MAX_TOKENIZER_MANIFEST_BYTES:
        raise ValueError("Sona tokenizer manifest exceeds the accepted bound")
    envelope = _expect_object(
        cast(JsonValue, json.loads(manifest_path.read_bytes())),
        "tokenizer envelope",
    )
    document = _expect_object(envelope.get("manifest"), "tokenizer manifest")
    artifact_manifest_sha256 = _expect_string(
        envelope.get("manifest_sha256"), "tokenizer manifest_sha256"
    )
    _validate_sha256(artifact_manifest_sha256, "tokenizer manifest_sha256")
    if sha256(rfc8785.dumps(document)).hexdigest() != artifact_manifest_sha256:
        raise ValueError("Sona tokenizer artifact manifest hash mismatch")
    if _expect_int(document.get("schema_version"), "schema_version") != 1:
        raise ValueError("Sona tokenizer artifact schema is unsupported")
    if _expect_string(document.get("algorithm"), "algorithm") != _TOKENIZER_ALGORITHM:
        raise ValueError("Sona tokenizer artifact algorithm is unsupported")
    centroids_path = directory / "centroids.npy"
    mapping_path = directory / "mapping.json"
    recording_count = _expect_int(document.get("recording_count"), "recording_count")
    configured_codebook_size = _expect_int(
        document.get("configured_codebook_size"), "configured_codebook_size"
    )
    active_codes = _expect_int(document.get("active_codes_per_level"), "active_codes_per_level")
    embedding_dimensions = _expect_int(document.get("embedding_dimensions"), "embedding_dimensions")
    iterations = _expect_int(document.get("iterations"), "iterations")
    seed = _expect_int(document.get("seed"), "seed")
    if (
        not 1 <= recording_count <= SONA_MAX_TOKENIZER_RECORDINGS
        or not 2 <= configured_codebook_size <= SONA_CODEBOOK_SIZE
        or active_codes
        != min(
            configured_codebook_size - 1,
            recording_count,
            SONA_MAX_ACTIVE_TOKENIZER_CODES,
        )
        or not 1 <= embedding_dimensions <= SONA_MAX_EMBEDDING_DIMENSIONS
        or _expect_int(document.get("max_active_codes"), "max_active_codes")
        != SONA_MAX_ACTIVE_TOKENIZER_CODES
        or _expect_int(document.get("mini_batch_size"), "mini_batch_size")
        != SONA_TOKENIZER_MINIBATCH_SIZE
        or not 1 <= iterations <= 100
        or seed < 0
    ):
        raise ValueError("Sona tokenizer artifact dimensions are outside canonical bounds")
    centroids_payload = read_canonical_npy(
        centroids_path,
        expected_shape=(SONA_SID_DEPTH, active_codes, embedding_dimensions),
        expected_dtype=np.dtype(np.float32),
    )
    maximum_mapping_bytes = recording_count * 160 + 4_096
    with mapping_path.open("rb") as stream:
        mapping_payload = stream.read(maximum_mapping_bytes + 1)
    if len(mapping_payload) > maximum_mapping_bytes:
        raise ValueError("Sona tokenizer mapping file size is outside canonical bounds")
    if sha256(centroids_payload).hexdigest() != _expect_string(
        document.get("centroids_sha256"), "centroids_sha256"
    ) or sha256(mapping_payload).hexdigest() != _expect_string(
        document.get("mapping_sha256"), "mapping_sha256"
    ):
        raise ValueError("Sona tokenizer artifact payload hash mismatch")
    centroids = cast(Float32Array, np.load(BytesIO(centroids_payload), allow_pickle=False))
    mapping_values = _expect_list(cast(JsonValue, json.loads(mapping_payload)), "tokenizer mapping")
    recording_ids: list[UUID] = []
    semantic_ids: list[SonaSemanticId] = []
    for raw_value in mapping_values:
        value = _expect_object(raw_value, "tokenizer mapping entry")
        recording_ids.append(UUID(_expect_string(value.get("recording_id"), "recording_id")))
        codes = _expect_list(value.get("semantic_id"), "semantic_id")
        if len(codes) != SONA_SID_DEPTH:
            raise ValueError("Sona tokenizer Semantic ID depth is invalid")
        semantic_id = SonaSemanticId(*(_expect_int(code, "semantic code") for code in codes))
        if any(code <= 0 or code > active_codes for code in semantic_id.values):
            raise ValueError("Sona tokenizer mapping references code without a centroid")
        semantic_ids.append(semantic_id)
    if recording_count != len(recording_ids):
        raise ValueError("Sona tokenizer artifact recording count mismatch")
    fit = SonaTokenizerFit(
        recording_ids=tuple(recording_ids),
        semantic_ids=tuple(semantic_ids),
        centroids=centroids,
        source_embeddings_sha256=_expect_string(
            document.get("source_embeddings_sha256"), "source_embeddings_sha256"
        ),
        manifest_sha256=_expect_string(document.get("fit_manifest_sha256"), "fit_manifest_sha256"),
        configured_codebook_size=configured_codebook_size,
        iterations=iterations,
        seed=seed,
    )
    if tuple(sorted(fit.recording_ids, key=lambda value: value.hex)) != fit.recording_ids:
        raise ValueError("Sona tokenizer mapping order is not canonical")
    centroid_sha256 = sha256(fit.centroids.astype("<f4", copy=False).tobytes()).hexdigest()
    fit_document: dict[str, JsonValue] = {
        "schema_version": 1,
        "algorithm": _TOKENIZER_ALGORITHM,
        "semantic_id_depth": SONA_SID_DEPTH,
        "configured_codebook_size": fit.configured_codebook_size,
        "active_codes_per_level": min(
            fit.configured_codebook_size - 1,
            len(fit.recording_ids),
            SONA_MAX_ACTIVE_TOKENIZER_CODES,
        ),
        "max_active_codes": SONA_MAX_ACTIVE_TOKENIZER_CODES,
        "mini_batch_size": SONA_TOKENIZER_MINIBATCH_SIZE,
        "embedding_dimensions": fit.centroids.shape[2],
        "iterations": fit.iterations,
        "seed": fit.seed,
        "source_embeddings_sha256": fit.source_embeddings_sha256,
        "centroids_sha256": centroid_sha256,
        "mapping": mapping_values,
    }
    if sha256(rfc8785.dumps(fit_document)).hexdigest() != fit.manifest_sha256:
        raise ValueError("Sona tokenizer fit manifest hash mismatch")
    return fit


def _fit_kmeans(
    values: Float32Array,
    *,
    cluster_count: int,
    iterations: int,
    seed: int,
) -> tuple[Float32Array, Int64Array]:
    initial = (np.arange(cluster_count, dtype=np.int64) * len(values)) // cluster_count
    initial = np.remainder(initial + seed, len(values))
    centroids = values[initial].copy()
    counts = np.zeros((cluster_count,), dtype=np.int64)
    batch_size = min(SONA_TOKENIZER_MINIBATCH_SIZE, len(values))
    for iteration in range(iterations):
        start = (seed * 1_009 + iteration * batch_size) % len(values)
        indices = np.remainder(np.arange(batch_size, dtype=np.int64) + start, len(values))
        batch = values[indices]
        assignment = _assign_nearest(batch, centroids)
        for cluster in range(cluster_count):
            members = batch[assignment == cluster]
            if len(members):
                previous_count = int(counts[cluster])
                current_count = previous_count + len(members)
                combined = (
                    centroids[cluster].astype(np.float64) * previous_count
                    + members.sum(axis=0, dtype=np.float64)
                ) / current_count
                centroids[cluster] = combined.astype(np.float32)
                counts[cluster] = current_count
    return centroids.astype(np.float32, copy=False), _assign_nearest(values, centroids)


def _assign_nearest(values: Float32Array, centroids: Float32Array) -> Int64Array:
    assignments = np.empty((len(values),), dtype=np.int64)
    centroid_norms = np.sum(centroids * centroids, axis=1, dtype=np.float32)
    for start in range(0, len(values), SONA_TOKENIZER_ASSIGNMENT_BLOCK):
        batch = values[start : start + SONA_TOKENIZER_ASSIGNMENT_BLOCK]
        value_norms = np.sum(batch * batch, axis=1, dtype=np.float32)[:, np.newaxis]
        distances = value_norms + centroid_norms[np.newaxis, :] - 2.0 * (batch @ centroids.T)
        assignments[start : start + len(batch)] = np.argmin(distances, axis=1)
    return assignments


def compute_source_embeddings_sha256(
    recording_ids: tuple[UUID, ...], embeddings: npt.NDArray[np.floating]
) -> str:
    """Hash canonical recording IDs and exact little-endian float32 embedding bytes."""

    values = np.asarray(embeddings, dtype=np.float32)
    if (
        not recording_ids
        or len(recording_ids) > SONA_MAX_TOKENIZER_RECORDINGS
        or len(recording_ids) != len(set(recording_ids))
        or values.ndim != 2
        or values.shape[0] != len(recording_ids)
        or not 1 <= values.shape[1] <= SONA_MAX_EMBEDDING_DIMENSIONS
        or not np.isfinite(values).all()
    ):
        raise ValueError("Sona source embedding snapshot is invalid or outside bounds")
    order = tuple(sorted(range(len(recording_ids)), key=lambda index: recording_ids[index].hex))
    ordered_ids = tuple(recording_ids[index] for index in order)
    ordered = values[np.asarray(order, dtype=np.int64)]
    return _source_embeddings_sha256(ordered_ids, ordered)


def _source_embeddings_sha256(
    ordered_ids: tuple[UUID, ...], ordered_embeddings: Float32Array
) -> str:
    metadata: dict[str, JsonValue] = {
        "schema_version": 1,
        "format": "CANONICAL_RECORDING_UUID_AND_FLOAT32_LE_V1",
        "recording_ids": [str(value) for value in ordered_ids],
        "embedding_dimensions": ordered_embeddings.shape[1],
    }
    digest = sha256(rfc8785.dumps(metadata))
    digest.update(ordered_embeddings.astype("<f4", copy=False).tobytes())
    return digest.hexdigest()


def _expect_object(value: JsonValue | None, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _expect_list(value: JsonValue | None, field: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def _expect_string(value: JsonValue | None, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _expect_int(value: JsonValue | None, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} is not a lowercase SHA-256 digest")


__all__ = (
    "SONA_MAX_ACTIVE_TOKENIZER_CODES",
    "SONA_MAX_EMBEDDING_DIMENSIONS",
    "SONA_MAX_TOKENIZER_RECORDINGS",
    "SonaTokenizerFit",
    "compute_source_embeddings_sha256",
    "fit_residual_tokenizer",
    "load_sona_tokenizer",
    "materialize_sona_tokenizer",
)
