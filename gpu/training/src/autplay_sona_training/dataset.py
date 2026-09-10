"""Immutable owner-safe tensor datasets for bounded Sona-Lite training."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, fields
from hashlib import sha256
from hmac import new as new_hmac
from io import BytesIO
from pathlib import Path
from typing import cast

import numpy as np
import numpy.typing as npt
import rfc8785
from autplay.application.sona import sona_inference_request_document
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import (
    SONA_CODEBOOK_SIZE,
    SONA_MAX_CANDIDATES,
    SONA_MAX_HISTORY_EVENTS,
    SONA_RANKING_HEADS,
    SONA_SID_DEPTH,
)
from autplay.domain.sona_approval import VerifiedSonaSourceApproval
from autplay.domain.sona_training import SONA_MAX_LABEL_DELAY_MS, SonaTrainingExample

from .npy import read_canonical_npy
from .tokenizer import SONA_MAX_ACTIVE_TOKENIZER_CODES

SONA_DATASET_SCHEMA_VERSION = 3
SONA_MAX_DATASET_EXAMPLES = 4_096
SONA_MAX_DATASET_MANIFEST_BYTES = 1_048_576
SONA_SOURCE_KIND_OWNER_APPROVED = "OWNER_PROVENANCE_EXPORT_V1"
SONA_SOURCE_KIND_SYNTHETIC = "SYNTHETIC_FIXTURE_V1"

_DATASET_ENVELOPE_KEYS = frozenset({"manifest", "manifest_sha256"})
_DATASET_TENSOR_ENTRY_KEYS = frozenset({"name", "file", "sha256", "dtype", "shape"})
_DATASET_MANIFEST_KEYS_V3 = frozenset(
    {
        "schema_version",
        "format",
        "split",
        "data_classification",
        "quality_eligible",
        "example_count",
        "ordered_examples_sha256",
        "tokenizer_sha256",
        "tokenizer_active_codes_per_level",
        "source_model_manifest_sha256",
        "source_kind",
        "source_manifest_sha256",
        "teacher_key",
        "teacher_version",
        "teacher_manifest_sha256",
        "contains_raw_owner_ids",
        "contains_raw_recording_ids",
        "owner_lineage_scheme",
        "owner_lineage_key_id",
        "owner_lineage_tokens",
        "tensors",
    }
)
_DATASET_MANIFEST_KEYS_V2 = frozenset(
    {
        "schema_version",
        "format",
        "split",
        "data_classification",
        "quality_eligible",
        "example_count",
        "ordered_examples_sha256",
        "tokenizer_sha256",
        "tokenizer_active_codes_per_level",
        "source_model_manifest_sha256",
        "contains_raw_owner_ids",
        "contains_raw_recording_ids",
        "owner_lineage_scheme",
        "owner_lineage_tokens",
        "tensors",
    }
)

type Float32Array = npt.NDArray[np.float32]
type Int64Array = npt.NDArray[np.int64]
type BytesArray = npt.NDArray[np.bytes_]


@dataclass(frozen=True, slots=True)
class SonaTensorDataset:
    """Verified tensors that contain no raw owner or recording identifiers."""

    history_sids: Int64Array
    history_actions: Int64Array
    history_origins: Int64Array
    history_age_buckets: Int64Array
    history_mask: Int64Array
    candidate_sids: Int64Array
    candidate_mask: Int64Array
    seed: Int64Array
    target_sids: Int64Array
    ranking_labels: Float32Array
    ranking_label_mask: Float32Array
    teacher_probabilities: Float32Array
    teacher_mask: Float32Array
    request_sha256: BytesArray
    example_sha256: BytesArray
    cutoff_at_ms: Int64Array
    observed_at_ms: Int64Array
    manifest_sha256: str
    tokenizer_sha256: str
    tokenizer_active_codes_per_level: int
    source_model_manifest_sha256: str
    source_kind: str
    source_manifest_sha256: str
    teacher_key: str
    teacher_version: str
    teacher_manifest_sha256: str
    owner_lineage_key_id: str
    split: str
    data_classification: str
    quality_eligible: bool
    owner_lineage_tokens: tuple[str, ...]
    quality_approval_sha256: str | None = None

    @property
    def example_count(self) -> int:
        return int(self.target_sids.shape[0])


_TENSOR_FIELDS = tuple(
    field.name
    for field in fields(SonaTensorDataset)
    if field.name
    not in {
        "manifest_sha256",
        "tokenizer_sha256",
        "tokenizer_active_codes_per_level",
        "source_model_manifest_sha256",
        "source_kind",
        "source_manifest_sha256",
        "teacher_key",
        "teacher_version",
        "teacher_manifest_sha256",
        "owner_lineage_key_id",
        "split",
        "data_classification",
        "quality_eligible",
        "owner_lineage_tokens",
        "quality_approval_sha256",
    }
)
_LEGACY_V2_TENSOR_FIELDS = tuple(
    name
    for name in _TENSOR_FIELDS
    if name not in {"request_sha256", "cutoff_at_ms", "observed_at_ms"}
)


def materialize_sona_dataset(
    examples: tuple[SonaTrainingExample, ...],
    output_directory: Path,
    *,
    split: str,
    data_classification: str,
    quality_eligible: bool,
    owner_lineage_hmac_key: bytes,
    tokenizer_active_codes_per_level: int,
) -> str:
    """Write a permanently synthetic/non-quality tensor dataset."""

    return _materialize_sona_dataset(
        examples,
        output_directory,
        split=split,
        data_classification=data_classification,
        quality_eligible=quality_eligible,
        source_kind=SONA_SOURCE_KIND_SYNTHETIC,
        source_manifest_sha256=sha256(b"autplay:sona:synthetic-source:v1").hexdigest(),
        owner_lineage_key_id="synthetic-source-v1",
        owner_lineage_hmac_key=owner_lineage_hmac_key,
        tokenizer_active_codes_per_level=tokenizer_active_codes_per_level,
    )


def materialize_quality_candidate_sona_dataset(
    examples: tuple[SonaTrainingExample, ...],
    output_directory: Path,
    *,
    split: str,
    source_approval: VerifiedSonaSourceApproval,
    approved_request_sha256s: frozenset[str],
    owner_lineage_hmac_key: bytes,
    tokenizer_active_codes_per_level: int,
) -> str:
    """Materialize one owner-source split only from signed request membership."""

    for digest in approved_request_sha256s:
        _validate_sha256(digest, "approved request SHA-256")
    if (
        len(approved_request_sha256s) != source_approval.request_count
        or _set_sha256(approved_request_sha256s) != source_approval.request_set_sha256
    ):
        raise ValueError("Sona approved request membership does not match its source approval")
    request_sha256s = {example.request.request_sha256 for example in examples}
    if not request_sha256s or not request_sha256s <= approved_request_sha256s:
        raise ValueError("Sona dataset example is absent from its approved source membership")
    return _materialize_sona_dataset(
        examples,
        output_directory,
        split=split,
        data_classification="APPROVED_OWNER_SAFE",
        quality_eligible=False,
        source_kind=SONA_SOURCE_KIND_OWNER_APPROVED,
        source_manifest_sha256=source_approval.source_manifest_sha256,
        owner_lineage_key_id=source_approval.owner_lineage_key_id,
        owner_lineage_hmac_key=owner_lineage_hmac_key,
        tokenizer_active_codes_per_level=tokenizer_active_codes_per_level,
    )


def _materialize_sona_dataset(
    examples: tuple[SonaTrainingExample, ...],
    output_directory: Path,
    *,
    split: str,
    data_classification: str,
    quality_eligible: bool,
    source_kind: str,
    source_manifest_sha256: str,
    owner_lineage_key_id: str,
    owner_lineage_hmac_key: bytes,
    tokenizer_active_codes_per_level: int,
) -> str:
    """Write content-addressed owner-safe tensors with derived provenance labels."""

    _validate_label(split, "split")
    _validate_label(data_classification, "data_classification")
    if quality_eligible:
        raise ValueError("Quality-eligible Sona dataset approval is not implemented")
    if source_kind not in (SONA_SOURCE_KIND_OWNER_APPROVED, SONA_SOURCE_KIND_SYNTHETIC):
        raise ValueError("Sona dataset source kind is unsupported")
    _validate_sha256(source_manifest_sha256, "source_manifest_sha256")
    _validate_label(owner_lineage_key_id, "owner_lineage_key_id")
    if not 32 <= len(owner_lineage_hmac_key) <= 1_024:
        raise ValueError("Sona owner-lineage HMAC key is outside the accepted bound")
    if not 1 <= tokenizer_active_codes_per_level <= SONA_MAX_ACTIVE_TOKENIZER_CODES:
        raise ValueError("Sona tokenizer active-code count is outside the accepted bound")
    if not 1 <= len(examples) <= SONA_MAX_DATASET_EXAMPLES:
        raise ValueError("Sona dataset is empty or exceeds the accepted bound")
    if any(
        sha256(rfc8785.dumps(sona_inference_request_document(example.request))).hexdigest()
        != example.request.request_sha256
        for example in examples
    ):
        raise ValueError("Sona dataset request hash is not derived from its canonical snapshot")
    tokenizer_hashes = {example.request.tokenizer_sha256 for example in examples}
    source_model_hashes = {example.request.model_manifest_sha256 for example in examples}
    teacher_keys = {example.teacher_key for example in examples}
    teacher_versions = {example.teacher_version for example in examples}
    teacher_hashes = {example.teacher_manifest_sha256 for example in examples}
    if (
        len(tokenizer_hashes) != 1
        or len(source_model_hashes) != 1
        or len(teacher_keys) != 1
        or len(teacher_versions) != 1
        or len(teacher_hashes) != 1
    ):
        raise ValueError("Sona dataset must use one tokenizer and source model manifest")
    tensors = _tensorize_examples(examples)
    tokenizer_sha256 = next(iter(tokenizer_hashes))
    source_model_manifest_sha256 = next(iter(source_model_hashes))
    owner_lineage_tokens = tuple(
        sorted(
            {
                new_hmac(
                    owner_lineage_hmac_key,
                    example.request.owner_user_id.bytes,
                    sha256,
                ).hexdigest()
                for example in examples
            }
        )
    )
    return _write_dataset_directory(
        tensors,
        output_directory,
        split=split,
        data_classification=data_classification,
        quality_eligible=quality_eligible,
        tokenizer_sha256=tokenizer_sha256,
        tokenizer_active_codes_per_level=tokenizer_active_codes_per_level,
        source_model_manifest_sha256=source_model_manifest_sha256,
        source_kind=source_kind,
        source_manifest_sha256=source_manifest_sha256,
        teacher_key=next(iter(teacher_keys)),
        teacher_version=next(iter(teacher_versions)),
        teacher_manifest_sha256=next(iter(teacher_hashes)),
        owner_lineage_key_id=owner_lineage_key_id,
        owner_lineage_tokens=owner_lineage_tokens,
    )


def load_sona_dataset(directory: Path) -> SonaTensorDataset:
    """Load and fully verify one immutable Sona tensor dataset."""

    manifest_path = directory / "manifest.json"
    if manifest_path.stat().st_size > SONA_MAX_DATASET_MANIFEST_BYTES:
        raise ValueError("Sona dataset manifest exceeds the accepted bound")
    envelope = _load_json_object(manifest_path)
    _require_exact_keys(envelope, _DATASET_ENVELOPE_KEYS, "dataset envelope")
    document = _expect_object(envelope.get("manifest"), "manifest")
    schema_version = _expect_int(document.get("schema_version"), "schema_version")
    if schema_version == SONA_DATASET_SCHEMA_VERSION:
        _require_exact_keys(document, _DATASET_MANIFEST_KEYS_V3, "dataset manifest")
        tensor_fields = _TENSOR_FIELDS
    elif schema_version == 2:
        _require_exact_keys(document, _DATASET_MANIFEST_KEYS_V2, "legacy dataset manifest")
        if (
            _expect_bool(document.get("quality_eligible"), "quality_eligible")
            or _expect_string(document.get("data_classification"), "data_classification")
            != "SYNTHETIC_FIXTURE"
        ):
            raise ValueError("Legacy Sona v2 datasets are accepted only as synthetic evidence")
        tensor_fields = _LEGACY_V2_TENSOR_FIELDS
    else:
        raise ValueError("Sona dataset schema version is unsupported")
    manifest_sha256 = _expect_string(envelope.get("manifest_sha256"), "manifest_sha256")
    _validate_sha256(manifest_sha256, "manifest_sha256")
    if sha256(rfc8785.dumps(document)).hexdigest() != manifest_sha256:
        raise ValueError("Sona dataset manifest hash mismatch")
    if (
        _expect_string(document.get("format"), "format") != "OWNER_SAFE_FIXED_TENSORS_V1"
        or _expect_bool(document.get("contains_raw_owner_ids"), "contains_raw_owner_ids")
        or _expect_bool(document.get("contains_raw_recording_ids"), "contains_raw_recording_ids")
        or _expect_string(document.get("owner_lineage_scheme"), "owner_lineage_scheme")
        != "HMAC_SHA256_OWNER_UUID_BYTES_V1"
    ):
        raise ValueError("Sona dataset privacy or format declaration is invalid")
    example_count = _expect_int(document.get("example_count"), "example_count")
    if not 1 <= example_count <= SONA_MAX_DATASET_EXAMPLES:
        raise ValueError("Sona dataset example count is outside the accepted bound")
    tensor_entries = _expect_list(document.get("tensors"), "tensors")
    arrays: dict[str, npt.NDArray[np.generic]] = {}
    expected_shapes = _expected_tensor_shapes(example_count)
    for raw_entry in tensor_entries:
        entry = _expect_object(raw_entry, "tensor entry")
        _require_exact_keys(entry, _DATASET_TENSOR_ENTRY_KEYS, "tensor entry")
        name = _expect_string(entry.get("name"), "tensor name")
        if name not in tensor_fields or name in arrays:
            raise ValueError("Sona dataset tensor name is unknown or duplicated")
        relative_path = _expect_string(entry.get("file"), "tensor file")
        if relative_path != f"{name}.npy":
            raise ValueError("Sona dataset tensor file is not canonical")
        tensor_path = directory / relative_path
        expected_dtype = _expect_string(entry.get("dtype"), "tensor dtype")
        expected_shape = tuple(
            _expect_int(value, "tensor shape")
            for value in _expect_list(entry.get("shape"), "tensor shape")
        )
        canonical_dtype = _expected_dtype(name)
        if expected_shape != expected_shapes[name] or expected_dtype != canonical_dtype.str:
            raise ValueError("Sona dataset tensor declaration is outside canonical bounds")
        tensor_payload = read_canonical_npy(
            tensor_path,
            expected_shape=expected_shape,
            expected_dtype=canonical_dtype,
        )
        digest = _expect_string(entry.get("sha256"), "tensor sha256")
        _validate_sha256(digest, "tensor sha256")
        if sha256(tensor_payload).hexdigest() != digest:
            raise ValueError("Sona dataset tensor hash mismatch")
        array = np.load(BytesIO(tensor_payload), allow_pickle=False)
        if array.dtype.str != expected_dtype or array.shape != expected_shape:
            raise ValueError("Sona dataset tensor shape or dtype mismatch")
        arrays[name] = array
    if set(arrays) != set(tensor_fields):
        raise ValueError("Sona dataset tensor set is incomplete")
    expected_files = {"manifest.json", *(f"{name}.npy" for name in tensor_fields)}
    actual_entries = tuple(directory.iterdir())
    if {entry.name for entry in actual_entries} != expected_files or any(
        not entry.is_file() or entry.is_symlink() for entry in actual_entries
    ):
        raise ValueError("Sona dataset directory contains undeclared or unsafe files")
    legacy_request_sha256 = cast(BytesArray, arrays["example_sha256"].copy())
    legacy_cutoff_at_ms = np.zeros((example_count,), dtype=np.int64)
    legacy_observed_at_ms = np.ones((example_count,), dtype=np.int64)
    dataset = SonaTensorDataset(
        history_sids=cast(Int64Array, arrays["history_sids"]),
        history_actions=cast(Int64Array, arrays["history_actions"]),
        history_origins=cast(Int64Array, arrays["history_origins"]),
        history_age_buckets=cast(Int64Array, arrays["history_age_buckets"]),
        history_mask=cast(Int64Array, arrays["history_mask"]),
        candidate_sids=cast(Int64Array, arrays["candidate_sids"]),
        candidate_mask=cast(Int64Array, arrays["candidate_mask"]),
        seed=cast(Int64Array, arrays["seed"]),
        target_sids=cast(Int64Array, arrays["target_sids"]),
        ranking_labels=cast(Float32Array, arrays["ranking_labels"]),
        ranking_label_mask=cast(Float32Array, arrays["ranking_label_mask"]),
        teacher_probabilities=cast(Float32Array, arrays["teacher_probabilities"]),
        teacher_mask=cast(Float32Array, arrays["teacher_mask"]),
        request_sha256=(
            cast(BytesArray, arrays["request_sha256"])
            if schema_version == SONA_DATASET_SCHEMA_VERSION
            else legacy_request_sha256
        ),
        example_sha256=cast(BytesArray, arrays["example_sha256"]),
        cutoff_at_ms=(
            cast(Int64Array, arrays["cutoff_at_ms"])
            if schema_version == SONA_DATASET_SCHEMA_VERSION
            else legacy_cutoff_at_ms
        ),
        observed_at_ms=(
            cast(Int64Array, arrays["observed_at_ms"])
            if schema_version == SONA_DATASET_SCHEMA_VERSION
            else legacy_observed_at_ms
        ),
        manifest_sha256=manifest_sha256,
        tokenizer_sha256=_expect_string(document.get("tokenizer_sha256"), "tokenizer_sha256"),
        tokenizer_active_codes_per_level=_expect_int(
            document.get("tokenizer_active_codes_per_level"),
            "tokenizer_active_codes_per_level",
        ),
        source_model_manifest_sha256=_expect_string(
            document.get("source_model_manifest_sha256"),
            "source_model_manifest_sha256",
        ),
        source_kind=(
            _expect_string(document.get("source_kind"), "source_kind")
            if schema_version == SONA_DATASET_SCHEMA_VERSION
            else SONA_SOURCE_KIND_SYNTHETIC
        ),
        source_manifest_sha256=(
            _expect_string(document.get("source_manifest_sha256"), "source_manifest_sha256")
            if schema_version == SONA_DATASET_SCHEMA_VERSION
            else sha256(f"legacy-v2:{manifest_sha256}".encode()).hexdigest()
        ),
        teacher_key=(
            _expect_string(document.get("teacher_key"), "teacher_key")
            if schema_version == SONA_DATASET_SCHEMA_VERSION
            else "legacy-synthetic"
        ),
        teacher_version=(
            _expect_string(document.get("teacher_version"), "teacher_version")
            if schema_version == SONA_DATASET_SCHEMA_VERSION
            else "2"
        ),
        teacher_manifest_sha256=(
            _expect_string(document.get("teacher_manifest_sha256"), "teacher_manifest_sha256")
            if schema_version == SONA_DATASET_SCHEMA_VERSION
            else sha256(b"autplay:sona:legacy-v2-teacher").hexdigest()
        ),
        owner_lineage_key_id=(
            _expect_string(document.get("owner_lineage_key_id"), "owner_lineage_key_id")
            if schema_version == SONA_DATASET_SCHEMA_VERSION
            else "legacy-v2"
        ),
        split=_expect_string(document.get("split"), "split"),
        data_classification=_expect_string(
            document.get("data_classification"), "data_classification"
        ),
        quality_eligible=_expect_bool(document.get("quality_eligible"), "quality_eligible"),
        owner_lineage_tokens=tuple(
            _expect_string(value, "owner lineage token")
            for value in _expect_list(document.get("owner_lineage_tokens"), "owner_lineage_tokens")
        ),
    )
    _validate_loaded_dataset(dataset, example_count=example_count)
    if dataset.quality_eligible:
        raise ValueError("Quality-eligible Sona dataset approval is not implemented")
    ordered_examples_sha256 = _expect_string(
        document.get("ordered_examples_sha256"), "ordered_examples_sha256"
    )
    if sha256(dataset.example_sha256.tobytes()).hexdigest() != ordered_examples_sha256:
        raise ValueError("Sona dataset ordered example hash mismatch")
    return dataset


def _tensorize_examples(examples: tuple[SonaTrainingExample, ...]) -> SonaTensorDataset:
    count = len(examples)
    head_count = len(SONA_RANKING_HEADS)
    history_sids = np.zeros((count, SONA_MAX_HISTORY_EVENTS, SONA_SID_DEPTH), dtype=np.int64)
    history_actions = np.zeros((count, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    history_origins = np.zeros((count, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    history_age_buckets = np.zeros((count, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    history_mask = np.zeros((count, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    candidate_sids = np.zeros((count, SONA_MAX_CANDIDATES, SONA_SID_DEPTH), dtype=np.int64)
    candidate_mask = np.zeros((count, SONA_MAX_CANDIDATES), dtype=np.int64)
    seed = np.zeros((count,), dtype=np.int64)
    target_sids = np.zeros((count, SONA_SID_DEPTH), dtype=np.int64)
    ranking_labels = np.zeros((count, SONA_MAX_CANDIDATES, head_count), dtype=np.float32)
    ranking_label_mask = np.zeros_like(ranking_labels)
    teacher_probabilities = np.zeros_like(ranking_labels)
    teacher_mask = np.zeros_like(ranking_labels)
    request_sha256 = np.empty((count,), dtype="S64")
    example_sha256 = np.empty((count,), dtype="S64")
    cutoff_at_ms = np.zeros((count,), dtype=np.int64)
    observed_at_ms = np.zeros((count,), dtype=np.int64)
    for index, example in enumerate(examples):
        request = example.request
        history_start = SONA_MAX_HISTORY_EVENTS - len(request.history)
        for event_index, event in enumerate(request.history, history_start):
            history_sids[index, event_index] = event.semantic_id.values
            history_actions[index, event_index] = int(event.action)
            history_origins[index, event_index] = int(event.origin)
            history_age_buckets[index, event_index] = event.age_bucket
            history_mask[index, event_index] = 1
        for candidate_index, candidate in enumerate(request.candidates):
            candidate_sids[index, candidate_index] = candidate.semantic_id.values
            candidate_mask[index, candidate_index] = 1
            target = example.ranking_targets[candidate_index]
            ranking_labels[index, candidate_index] = target.labels
            ranking_label_mask[index, candidate_index] = target.label_mask
            teacher_probabilities[index, candidate_index] = target.teacher_probabilities
            teacher_mask[index, candidate_index] = 1.0
        seed[index] = request.seed
        target_sids[index] = example.target_semantic_id.values
        request_sha256[index] = request.request_sha256.encode("ascii")
        example_sha256[index] = example.example_sha256.encode("ascii")
        cutoff_at_ms[index] = request.cutoff_at_ms
        observed_at_ms[index] = example.observed_at_ms
    return SonaTensorDataset(
        history_sids,
        history_actions,
        history_origins,
        history_age_buckets,
        history_mask,
        candidate_sids,
        candidate_mask,
        seed,
        target_sids,
        ranking_labels,
        ranking_label_mask,
        teacher_probabilities,
        teacher_mask,
        request_sha256,
        example_sha256,
        cutoff_at_ms,
        observed_at_ms,
        manifest_sha256="0" * 64,
        tokenizer_sha256=examples[0].request.tokenizer_sha256,
        tokenizer_active_codes_per_level=int(target_sids.max(initial=1)),
        source_model_manifest_sha256=examples[0].request.model_manifest_sha256,
        source_kind="unmaterialized",
        source_manifest_sha256="0" * 64,
        teacher_key=examples[0].teacher_key,
        teacher_version=examples[0].teacher_version,
        teacher_manifest_sha256=examples[0].teacher_manifest_sha256,
        owner_lineage_key_id="unmaterialized",
        split="unmaterialized",
        data_classification="unmaterialized",
        quality_eligible=False,
        owner_lineage_tokens=(),
    )


def _write_dataset_directory(
    tensors: SonaTensorDataset,
    output_directory: Path,
    *,
    split: str,
    data_classification: str,
    quality_eligible: bool,
    tokenizer_sha256: str,
    tokenizer_active_codes_per_level: int,
    source_model_manifest_sha256: str,
    source_kind: str,
    source_manifest_sha256: str,
    teacher_key: str,
    teacher_version: str,
    teacher_manifest_sha256: str,
    owner_lineage_key_id: str,
    owner_lineage_tokens: tuple[str, ...],
) -> str:
    if output_directory.exists():
        raise FileExistsError(f"Sona dataset output already exists: {output_directory}")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=output_directory.parent)
    )
    try:
        tensor_entries: list[JsonValue] = []
        for name in _TENSOR_FIELDS:
            array = cast(npt.NDArray[np.generic], getattr(tensors, name))
            path = temporary / f"{name}.npy"
            with path.open("wb") as stream:
                np.save(stream, array, allow_pickle=False)
            tensor_entries.append(
                {
                    "name": name,
                    "file": path.name,
                    "sha256": sha256(path.read_bytes()).hexdigest(),
                    "dtype": array.dtype.str,
                    "shape": list(array.shape),
                }
            )
        ordered_examples_sha256 = sha256(tensors.example_sha256.tobytes()).hexdigest()
        document: dict[str, JsonValue] = {
            "schema_version": SONA_DATASET_SCHEMA_VERSION,
            "format": "OWNER_SAFE_FIXED_TENSORS_V1",
            "split": split,
            "data_classification": data_classification,
            "quality_eligible": quality_eligible,
            "example_count": tensors.example_count,
            "ordered_examples_sha256": ordered_examples_sha256,
            "tokenizer_sha256": tokenizer_sha256,
            "tokenizer_active_codes_per_level": tokenizer_active_codes_per_level,
            "source_model_manifest_sha256": source_model_manifest_sha256,
            "source_kind": source_kind,
            "source_manifest_sha256": source_manifest_sha256,
            "teacher_key": teacher_key,
            "teacher_version": teacher_version,
            "teacher_manifest_sha256": teacher_manifest_sha256,
            "contains_raw_owner_ids": False,
            "contains_raw_recording_ids": False,
            "owner_lineage_scheme": "HMAC_SHA256_OWNER_UUID_BYTES_V1",
            "owner_lineage_key_id": owner_lineage_key_id,
            "owner_lineage_tokens": list(owner_lineage_tokens),
            "tensors": tensor_entries,
        }
        manifest_sha256 = sha256(rfc8785.dumps(document)).hexdigest()
        envelope: dict[str, JsonValue] = {
            "manifest": document,
            "manifest_sha256": manifest_sha256,
        }
        (temporary / "manifest.json").write_bytes(rfc8785.dumps(envelope))
        os.replace(temporary, output_directory)
        return manifest_sha256
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _validate_loaded_dataset(dataset: SonaTensorDataset, *, example_count: int) -> None:
    expected_shapes = _expected_tensor_shapes(example_count)
    for name, shape in expected_shapes.items():
        if cast(npt.NDArray[np.generic], getattr(dataset, name)).shape != shape:
            raise ValueError("Sona dataset contains an invalid tensor shape")
    if any(
        value.dtype != np.int64
        for value in (
            dataset.history_sids,
            dataset.history_actions,
            dataset.history_origins,
            dataset.history_age_buckets,
            dataset.history_mask,
            dataset.candidate_sids,
            dataset.candidate_mask,
            dataset.seed,
            dataset.target_sids,
            dataset.cutoff_at_ms,
            dataset.observed_at_ms,
        )
    ):
        raise ValueError("Sona dataset integer tensor dtype is invalid")
    if any(
        value.dtype != np.float32
        for value in (
            dataset.ranking_labels,
            dataset.ranking_label_mask,
            dataset.teacher_probabilities,
            dataset.teacher_mask,
        )
    ):
        raise ValueError("Sona dataset floating tensor dtype is invalid")
    if dataset.example_sha256.dtype != np.dtype("S64") or dataset.request_sha256.dtype != np.dtype(
        "S64"
    ):
        raise ValueError("Sona dataset request or example hash dtype is invalid")
    if (
        not np.isin(dataset.history_mask, (0, 1)).all()
        or not np.isin(dataset.candidate_mask, (0, 1)).all()
    ):
        raise ValueError("Sona dataset input mask is invalid")
    if np.any(np.diff(dataset.history_mask, axis=1) < 0) or np.any(
        np.diff(dataset.candidate_mask, axis=1) > 0
    ):
        raise ValueError("Sona dataset padding masks are not canonical")
    if (
        np.any((dataset.history_actions < 0) | (dataset.history_actions > 9))
        or np.any((dataset.history_origins < 0) | (dataset.history_origins > 5))
        or np.any((dataset.history_age_buckets < 0) | (dataset.history_age_buckets > 63))
        or np.any(dataset.seed < 0)
    ):
        raise ValueError("Sona dataset categorical input is outside the model vocabulary")
    active_history = dataset.history_mask == 1
    active_candidates = dataset.candidate_mask == 1
    if np.any(dataset.history_sids[active_history] <= 0) or np.any(
        dataset.candidate_sids[active_candidates] <= 0
    ):
        raise ValueError("Sona dataset active Semantic ID contains reserved code zero")
    if (
        np.any(dataset.history_sids[~active_history] != 0)
        or np.any(dataset.history_actions[~active_history] != 0)
        or np.any(dataset.history_origins[~active_history] != 0)
        or np.any(dataset.history_age_buckets[~active_history] != 0)
        or np.any(dataset.candidate_sids[~active_candidates] != 0)
    ):
        raise ValueError("Sona dataset padding contains non-zero values")
    if (
        not np.isin(dataset.ranking_label_mask, (0.0, 1.0)).all()
        or not np.isin(dataset.teacher_mask, (0.0, 1.0)).all()
    ):
        raise ValueError("Sona dataset supervision mask is invalid")
    if (
        not 1 <= dataset.tokenizer_active_codes_per_level <= SONA_MAX_ACTIVE_TOKENIZER_CODES
        or dataset.history_sids.max(initial=0) > dataset.tokenizer_active_codes_per_level
        or dataset.candidate_sids.max(initial=0) > dataset.tokenizer_active_codes_per_level
        or dataset.target_sids.max(initial=0) > dataset.tokenizer_active_codes_per_level
    ):
        raise ValueError("Sona dataset Semantic ID escapes the bound tokenizer coverage")
    if (
        dataset.target_sids.min(initial=1) <= 0
        or dataset.target_sids.max(initial=0) >= SONA_CODEBOOK_SIZE
        or dataset.candidate_sids.min(initial=0) < 0
        or dataset.candidate_sids.max(initial=0) >= SONA_CODEBOOK_SIZE
    ):
        raise ValueError("Sona dataset Semantic ID is outside the accepted codebook")
    if (
        not np.isfinite(dataset.ranking_labels).all()
        or not np.isfinite(dataset.teacher_probabilities).all()
    ):
        raise ValueError("Sona dataset contains non-finite supervision")
    if np.any((dataset.ranking_labels < 0.0) | (dataset.ranking_labels > 1.0)) or np.any(
        (dataset.teacher_probabilities < 0.0) | (dataset.teacher_probabilities > 1.0)
    ):
        raise ValueError("Sona dataset probability is outside the accepted range")
    expanded_candidate_mask = np.repeat(
        dataset.candidate_mask[..., np.newaxis], len(SONA_RANKING_HEADS), axis=2
    ).astype(np.float32)
    if not np.array_equal(dataset.teacher_mask, expanded_candidate_mask):
        raise ValueError("Sona dataset teacher mask is not aligned to original candidates")
    if np.any(dataset.ranking_label_mask > expanded_candidate_mask):
        raise ValueError("Sona dataset observed labels escape the original candidates")
    for row in range(example_count):
        active_candidates = dataset.candidate_sids[row][dataset.candidate_mask[row] == 1]
        if not np.any(np.all(active_candidates == dataset.target_sids[row], axis=1)):
            raise ValueError("Sona dataset target is absent from original candidates")
    for digest in (
        dataset.manifest_sha256,
        dataset.tokenizer_sha256,
        dataset.source_model_manifest_sha256,
        dataset.source_manifest_sha256,
        dataset.teacher_manifest_sha256,
    ):
        _validate_sha256(digest, "dataset digest")
    if dataset.source_kind not in (SONA_SOURCE_KIND_OWNER_APPROVED, SONA_SOURCE_KIND_SYNTHETIC):
        raise ValueError("Sona dataset source kind is unsupported")
    _validate_label(dataset.teacher_key, "teacher_key")
    _validate_label(dataset.teacher_version, "teacher_version")
    _validate_label(dataset.owner_lineage_key_id, "owner_lineage_key_id")
    if (
        not dataset.owner_lineage_tokens
        or tuple(sorted(set(dataset.owner_lineage_tokens))) != dataset.owner_lineage_tokens
    ):
        raise ValueError("Sona dataset owner lineage is empty, duplicate, or unordered")
    for token in dataset.owner_lineage_tokens:
        _validate_sha256(token, "owner lineage token")
    _validate_label(dataset.split, "split")
    _validate_label(dataset.data_classification, "data_classification")
    decoded_hashes = tuple(value.decode("ascii") for value in dataset.example_sha256.tolist())
    decoded_request_hashes = tuple(
        value.decode("ascii") for value in dataset.request_sha256.tolist()
    )
    if len(set(decoded_hashes)) != len(decoded_hashes):
        raise ValueError("Sona dataset contains duplicate examples")
    if len(set(decoded_request_hashes)) != len(decoded_request_hashes):
        raise ValueError("Sona dataset contains duplicate requests")
    for digest in (*decoded_hashes, *decoded_request_hashes):
        _validate_sha256(digest, "example_sha256")
    if (
        np.any(dataset.cutoff_at_ms < 0)
        or np.any(dataset.observed_at_ms <= dataset.cutoff_at_ms)
        or np.any(dataset.observed_at_ms > dataset.cutoff_at_ms + SONA_MAX_LABEL_DELAY_MS)
    ):
        raise ValueError("Sona dataset request or observation time is invalid")


def _expected_tensor_shapes(example_count: int) -> dict[str, tuple[int, ...]]:
    return {
        "history_sids": (example_count, SONA_MAX_HISTORY_EVENTS, SONA_SID_DEPTH),
        "history_actions": (example_count, SONA_MAX_HISTORY_EVENTS),
        "history_origins": (example_count, SONA_MAX_HISTORY_EVENTS),
        "history_age_buckets": (example_count, SONA_MAX_HISTORY_EVENTS),
        "history_mask": (example_count, SONA_MAX_HISTORY_EVENTS),
        "candidate_sids": (example_count, SONA_MAX_CANDIDATES, SONA_SID_DEPTH),
        "candidate_mask": (example_count, SONA_MAX_CANDIDATES),
        "seed": (example_count,),
        "target_sids": (example_count, SONA_SID_DEPTH),
        "ranking_labels": (example_count, SONA_MAX_CANDIDATES, len(SONA_RANKING_HEADS)),
        "ranking_label_mask": (
            example_count,
            SONA_MAX_CANDIDATES,
            len(SONA_RANKING_HEADS),
        ),
        "teacher_probabilities": (
            example_count,
            SONA_MAX_CANDIDATES,
            len(SONA_RANKING_HEADS),
        ),
        "teacher_mask": (example_count, SONA_MAX_CANDIDATES, len(SONA_RANKING_HEADS)),
        "request_sha256": (example_count,),
        "example_sha256": (example_count,),
        "cutoff_at_ms": (example_count,),
        "observed_at_ms": (example_count,),
    }


def _expected_dtype(name: str) -> np.dtype[np.generic]:
    if name in {"example_sha256", "request_sha256"}:
        return np.dtype("S64")
    if name in {
        "ranking_labels",
        "ranking_label_mask",
        "teacher_probabilities",
        "teacher_mask",
    }:
        return np.dtype(np.float32)
    return np.dtype(np.int64)


def _load_json_object(path: Path) -> dict[str, JsonValue]:
    parsed = cast(JsonValue, json.loads(path.read_bytes()))
    return _expect_object(parsed, str(path))


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


def _expect_bool(value: JsonValue | None, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_exact_keys(value: dict[str, JsonValue], expected: frozenset[str], field: str) -> None:
    if set(value) != expected:
        raise ValueError(f"Sona {field} has missing or unknown fields")


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} is not a lowercase SHA-256 digest")


def _set_sha256(values: frozenset[str]) -> str:
    return sha256(rfc8785.dumps(sorted(values))).hexdigest()


def _validate_label(value: str, field: str) -> None:
    if not 1 <= len(value) <= 64 or any(
        not (character.isascii() and (character.isalnum() or character in "-_."))
        for character in value
    ):
        raise ValueError(f"Sona dataset {field} is invalid")


__all__ = (
    "SONA_DATASET_SCHEMA_VERSION",
    "SONA_MAX_DATASET_EXAMPLES",
    "SONA_SOURCE_KIND_OWNER_APPROVED",
    "SONA_SOURCE_KIND_SYNTHETIC",
    "SonaTensorDataset",
    "load_sona_dataset",
    "materialize_quality_candidate_sona_dataset",
    "materialize_sona_dataset",
)
