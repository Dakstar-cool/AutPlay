"""Bounded deterministic-schedule trainer and safe Sona-Lite checkpoints."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
from math import ceil, isfinite
from pathlib import Path
from time import time_ns
from typing import Literal, cast

import numpy as np
import numpy.typing as npt
import rfc8785
import torch
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import SONA_MAX_CANDIDATES, SONA_MAX_HISTORY_EVENTS, SONA_SID_DEPTH
from torch import Tensor

from .dataset import SONA_MAX_DATASET_EXAMPLES, SonaTensorDataset, load_sona_dataset
from .model import SonaLiteConfig, SonaLiteModel
from .npy import read_canonical_npy
from .objective import compute_torch_sona_losses
from .quality_bundle import (
    SonaQualityDatasetBundle,
    reverify_quality_approved_sona_dataset_bundle,
)
from .tokenizer import SONA_MAX_ACTIVE_TOKENIZER_CODES

SONA_CHECKPOINT_SCHEMA_VERSION = 3
SONA_MAX_TRAINING_EPOCHS = 100
SONA_MAX_TRAINING_BATCH = 64
SONA_MAX_CHECKPOINT_MANIFEST_BYTES = 1_048_576
SONA_MAX_CHECKPOINT_WEIGHT_FILES = 128
_CHECKPOINT_FORMAT = "PICKLE_FREE_NUMPY_STATE_V1"
_CHECKPOINT_ARCHITECTURE = "SONA_LITE_SHARED_GRU_V1"

type DevicePreference = Literal["auto", "cpu", "cuda"]


@dataclass(frozen=True, slots=True)
class SonaTrainingConfig:
    """Immutable bounded optimizer and schedule settings."""

    epochs: int = 1
    batch_size: int = 8
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    gradient_clip_norm: float = 1.0
    seed: int = 0
    device: DevicePreference = "auto"

    def __post_init__(self) -> None:
        if not 1 <= self.epochs <= SONA_MAX_TRAINING_EPOCHS:
            raise ValueError("Sona training epoch count is outside the accepted bound")
        if not 1 <= self.batch_size <= SONA_MAX_TRAINING_BATCH:
            raise ValueError("Sona training batch size is outside the accepted bound")
        if (
            not isfinite(self.learning_rate)
            or not 0.0 < self.learning_rate <= 1.0
            or not isfinite(self.weight_decay)
            or not 0.0 <= self.weight_decay <= 1.0
            or not isfinite(self.gradient_clip_norm)
            or not 0.0 < self.gradient_clip_norm <= 1_000.0
        ):
            raise ValueError("Sona training optimizer configuration is invalid")
        if self.seed < 0 or self.device not in ("auto", "cpu", "cuda"):
            raise ValueError("Sona training seed or device preference is invalid")


@dataclass(frozen=True, slots=True)
class SonaTrainingResult:
    checkpoint_manifest_sha256: str
    weights_sha256: str
    dataset_manifest_sha256: str
    model_config_sha256: str
    training_config_sha256: str
    tokenizer_sha256: str
    tokenizer_active_codes_per_level: int
    dataset_approval_sha256: str | None
    dataset_bundle_sha256: str | None
    source_approval_sha256: str | None
    epoch_total_losses: tuple[float, ...]
    optimizer_steps: int
    device_type: str
    quality_provenance_eligible: bool
    quality_eligible: bool


def train_sona_checkpoint(
    dataset_directory: Path,
    checkpoint_directory: Path,
    *,
    model_config: SonaLiteConfig,
    training_config: SonaTrainingConfig,
) -> SonaTrainingResult:
    """Train one bounded model and write a hash-verified, pickle-free checkpoint."""

    dataset = load_sona_dataset(dataset_directory)
    return _train_sona_dataset(
        dataset,
        checkpoint_directory,
        model_config=model_config,
        training_config=training_config,
        dataset_approval_sha256=None,
        dataset_bundle_sha256=None,
        source_approval_sha256=None,
        before_publish=None,
    )


def train_quality_sona_checkpoint(
    bundle: SonaQualityDatasetBundle,
    checkpoint_directory: Path,
    *,
    model_config: SonaLiteConfig,
    training_config: SonaTrainingConfig,
) -> SonaTrainingResult:
    """Train only from an atomically verified quality bundle, yielding a candidate artifact."""

    if (
        not bundle.train.quality_eligible
        or bundle.train.quality_approval_sha256 != bundle.dataset_approval.approval_sha256
        or bundle.dataset_approval.source_approval_sha256 != bundle.source_approval.approval_sha256
    ):
        raise ValueError("Sona quality training bundle approval binding is invalid")
    return _train_sona_dataset(
        bundle.train,
        checkpoint_directory,
        model_config=model_config,
        training_config=training_config,
        dataset_approval_sha256=bundle.dataset_approval.approval_sha256,
        dataset_bundle_sha256=bundle.dataset_approval.dataset_bundle_sha256,
        source_approval_sha256=bundle.source_approval.approval_sha256,
        before_publish=lambda: reverify_quality_approved_sona_dataset_bundle(
            bundle,
            at_ms=time_ns() // 1_000_000,
        ),
    )


def _train_sona_dataset(
    dataset: SonaTensorDataset,
    checkpoint_directory: Path,
    *,
    model_config: SonaLiteConfig,
    training_config: SonaTrainingConfig,
    dataset_approval_sha256: str | None,
    dataset_bundle_sha256: str | None,
    source_approval_sha256: str | None,
    before_publish: Callable[[], object] | None,
) -> SonaTrainingResult:
    _validate_model_compatibility(dataset, model_config)
    device = _select_device(training_config.device)
    _configure_determinism(device, training_config.seed)
    model = SonaLiteModel(model_config).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=training_config.learning_rate,
        weight_decay=training_config.weight_decay,
    )
    epoch_losses: list[float] = []
    optimizer_steps = 0
    for epoch in range(training_config.epochs):
        model.train()
        generator = torch.Generator(device="cpu")
        generator.manual_seed(training_config.seed + epoch)
        order = torch.randperm(dataset.example_count, generator=generator).numpy()
        weighted_loss_sum = 0.0
        sample_count = 0
        for start in range(0, dataset.example_count, training_config.batch_size):
            indices = order[start : start + training_config.batch_size]
            tensors = _batch_to_torch(dataset, indices=indices, device=device)
            optimizer.zero_grad(set_to_none=True)
            decoder_logits, ranking_logits = model.training_forward(
                tensors["history_sids"],
                tensors["history_actions"],
                tensors["history_origins"],
                tensors["history_age_buckets"],
                tensors["history_mask"],
                tensors["candidate_sids"],
                tensors["candidate_mask"],
                tensors["seed"],
                tensors["target_sids"],
            )
            losses = compute_torch_sona_losses(
                decoder_logits,
                ranking_logits,
                tensors["target_sids"],
                tensors["ranking_labels"],
                tensors["ranking_label_mask"],
                tensors["teacher_probabilities"],
                tensors["teacher_mask"],
            )
            if not torch.isfinite(losses.total).item():
                raise RuntimeError("Sona training produced a non-finite loss")
            losses.total.backward()  # type: ignore[no-untyped-call]
            torch.nn.utils.clip_grad_norm_(model.parameters(), training_config.gradient_clip_norm)
            optimizer.step()
            batch_count = len(indices)
            weighted_loss_sum += float(losses.total.detach().cpu().item()) * batch_count
            sample_count += batch_count
            optimizer_steps += 1
        epoch_losses.append(weighted_loss_sum / sample_count)
    return _write_checkpoint(
        model,
        checkpoint_directory,
        dataset=dataset,
        training_config=training_config,
        epoch_total_losses=tuple(epoch_losses),
        optimizer_steps=optimizer_steps,
        device_type=device.type,
        dataset_approval_sha256=dataset_approval_sha256,
        dataset_bundle_sha256=dataset_bundle_sha256,
        source_approval_sha256=source_approval_sha256,
        before_publish=before_publish,
    )


def load_sona_checkpoint(
    checkpoint_directory: Path,
    *,
    device: torch.device | str = "cpu",
) -> tuple[SonaLiteModel, SonaTrainingResult]:
    """Load a non-quality checkpoint; signed quality ancestry requires the dedicated loader."""

    model, result = _load_sona_checkpoint(checkpoint_directory, device=device)
    if result.quality_provenance_eligible:
        raise ValueError("Sona quality checkpoint requires signed bundle re-verification")
    return model, result


def load_quality_sona_checkpoint(
    checkpoint_directory: Path,
    bundle: SonaQualityDatasetBundle,
    *,
    device: torch.device | str = "cpu",
) -> tuple[SonaLiteModel, SonaTrainingResult]:
    """Re-verify signed ancestry at current wall-clock time and load the exact candidate."""

    current = reverify_quality_approved_sona_dataset_bundle(
        bundle,
        at_ms=time_ns() // 1_000_000,
    )
    model, result = _load_sona_checkpoint(checkpoint_directory, device=device)
    if (
        not result.quality_provenance_eligible
        or result.dataset_manifest_sha256 != current.train.manifest_sha256
        or result.tokenizer_sha256 != current.tokenizer.manifest_sha256
        or result.dataset_approval_sha256 != current.dataset_approval.approval_sha256
        or result.dataset_bundle_sha256 != current.dataset_approval.dataset_bundle_sha256
        or result.source_approval_sha256 != current.source_approval.approval_sha256
    ):
        raise ValueError("Sona quality checkpoint does not match its re-verified signed bundle")
    return model, result


def _load_sona_checkpoint(
    checkpoint_directory: Path,
    *,
    device: torch.device | str = "cpu",
) -> tuple[SonaLiteModel, SonaTrainingResult]:
    """Verify hashes and load a pickle-free checkpoint without trusting quality claims."""

    manifest_path = checkpoint_directory / "manifest.json"
    if manifest_path.stat().st_size > SONA_MAX_CHECKPOINT_MANIFEST_BYTES:
        raise ValueError("Sona checkpoint manifest exceeds the accepted bound")
    envelope = _load_json_object(manifest_path)
    document = _expect_object(envelope.get("manifest"), "manifest")
    manifest_sha256 = _expect_string(envelope.get("manifest_sha256"), "manifest_sha256")
    _validate_sha256(manifest_sha256, "checkpoint manifest_sha256")
    if sha256(rfc8785.dumps(document)).hexdigest() != manifest_sha256:
        raise ValueError("Sona checkpoint manifest hash mismatch")
    if (
        _expect_int(document.get("schema_version"), "schema_version")
        != SONA_CHECKPOINT_SCHEMA_VERSION
    ):
        raise ValueError("Sona checkpoint schema version is unsupported")
    if _expect_string(document.get("format"), "format") != _CHECKPOINT_FORMAT:
        raise ValueError("Sona checkpoint format is unsupported")
    if _expect_string(document.get("architecture"), "architecture") != _CHECKPOINT_ARCHITECTURE:
        raise ValueError("Sona checkpoint architecture is unsupported")
    tokenizer_active_codes_per_level = _expect_int(
        document.get("tokenizer_active_codes_per_level"),
        "tokenizer_active_codes_per_level",
    )
    if not 1 <= tokenizer_active_codes_per_level <= SONA_MAX_ACTIVE_TOKENIZER_CODES:
        raise ValueError("Sona checkpoint tokenizer coverage is outside canonical bounds")
    model_config_document = _expect_object(document.get("model_config"), "model_config")
    model_config = SonaLiteConfig(
        codebook_size=_expect_int(model_config_document.get("codebook_size"), "codebook_size"),
        model_dimensions=_expect_int(
            model_config_document.get("model_dimensions"), "model_dimensions"
        ),
        encoder_layers=_expect_int(model_config_document.get("encoder_layers"), "encoder_layers"),
        action_vocabulary_size=_expect_int(
            model_config_document.get("action_vocabulary_size"), "action_vocabulary_size"
        ),
        origin_vocabulary_size=_expect_int(
            model_config_document.get("origin_vocabulary_size"), "origin_vocabulary_size"
        ),
        age_vocabulary_size=_expect_int(
            model_config_document.get("age_vocabulary_size"), "age_vocabulary_size"
        ),
        seed_vocabulary_size=_expect_int(
            model_config_document.get("seed_vocabulary_size"), "seed_vocabulary_size"
        ),
    )
    model_config_sha256 = _expect_string(document.get("model_config_sha256"), "model_config_sha256")
    if sha256(rfc8785.dumps(model_config_document)).hexdigest() != model_config_sha256:
        raise ValueError("Sona checkpoint model config hash mismatch")
    if model_config.codebook_size != tokenizer_active_codes_per_level + 1:
        raise ValueError("Sona checkpoint model vocabulary does not match tokenizer coverage")
    training_config_document = _expect_object(document.get("training_config"), "training_config")
    training_config_sha256 = _expect_string(
        document.get("training_config_sha256"), "training_config_sha256"
    )
    if sha256(rfc8785.dumps(training_config_document)).hexdigest() != training_config_sha256:
        raise ValueError("Sona checkpoint training config hash mismatch")
    training_config = SonaTrainingConfig(
        epochs=_expect_int(training_config_document.get("epochs"), "epochs"),
        batch_size=_expect_int(training_config_document.get("batch_size"), "batch_size"),
        learning_rate=_expect_float(training_config_document.get("learning_rate"), "learning_rate"),
        weight_decay=_expect_float(training_config_document.get("weight_decay"), "weight_decay"),
        gradient_clip_norm=_expect_float(
            training_config_document.get("gradient_clip_norm"), "gradient_clip_norm"
        ),
        seed=_expect_int(training_config_document.get("seed"), "seed"),
        device=_expect_device(training_config_document.get("device")),
    )
    weight_entries = _expect_list(document.get("weights"), "weights")
    if not 1 <= len(weight_entries) <= SONA_MAX_CHECKPOINT_WEIGHT_FILES:
        raise ValueError("Sona checkpoint weight count is outside the accepted bound")
    model = SonaLiteModel(model_config)
    expected_state = model.state_dict()
    if len(weight_entries) != len(expected_state):
        raise ValueError("Sona checkpoint weight set size is invalid")
    loaded_state: dict[str, Tensor] = {}
    overall_weights = sha256()
    for raw_entry in weight_entries:
        entry = _expect_object(raw_entry, "weight entry")
        name = _expect_string(entry.get("name"), "weight name")
        if name not in expected_state or name in loaded_state:
            raise ValueError("Sona checkpoint weight is unknown or duplicated")
        file_name = _expect_string(entry.get("file"), "weight file")
        if Path(file_name).name != file_name or not file_name.endswith(".npy"):
            raise ValueError("Sona checkpoint weight file is not canonical")
        path = checkpoint_directory / "weights" / file_name
        digest = _expect_string(entry.get("sha256"), "weight sha256")
        _validate_sha256(digest, "weight sha256")
        expected = expected_state[name]
        declared_dtype = _expect_string(entry.get("dtype"), "weight dtype")
        declared_shape = tuple(
            _expect_int(value, "weight shape")
            for value in _expect_list(entry.get("shape"), "weight shape")
        )
        expected_dtype = str(expected.detach().cpu().numpy().dtype)
        if declared_shape != tuple(expected.shape) or declared_dtype != expected_dtype:
            raise ValueError("Sona checkpoint weight declaration is outside canonical bounds")
        weight_payload = read_canonical_npy(
            path,
            expected_shape=declared_shape,
            expected_dtype=np.dtype(expected_dtype),
        )
        if sha256(weight_payload).hexdigest() != digest:
            raise ValueError("Sona checkpoint weight hash mismatch")
        array = np.load(BytesIO(weight_payload), allow_pickle=False)
        if tuple(array.shape) != declared_shape or str(array.dtype) != declared_dtype:
            raise ValueError("Sona checkpoint weight shape or dtype mismatch")
        overall_weights.update(name.encode("utf-8"))
        overall_weights.update(bytes.fromhex(digest))
        loaded_state[name] = torch.from_numpy(array.copy())
    if set(loaded_state) != set(expected_state):
        raise ValueError("Sona checkpoint weight set is incomplete")
    weights_sha256 = _expect_string(document.get("weights_sha256"), "weights_sha256")
    if overall_weights.hexdigest() != weights_sha256:
        raise ValueError("Sona checkpoint aggregate weights hash mismatch")
    model.load_state_dict(loaded_state, strict=True)
    model.to(device)
    model.eval()
    losses = tuple(
        _expect_float(value, "epoch loss")
        for value in _expect_list(document.get("epoch_total_losses"), "epoch_total_losses")
    )
    dataset_example_count = _expect_int(
        document.get("dataset_example_count"), "dataset_example_count"
    )
    optimizer_steps = _expect_int(document.get("optimizer_steps"), "optimizer_steps")
    expected_steps = training_config.epochs * ceil(
        dataset_example_count / training_config.batch_size
    )
    if (
        not 1 <= dataset_example_count <= SONA_MAX_DATASET_EXAMPLES
        or len(losses) != training_config.epochs
        or any(value < 0.0 for value in losses)
        or optimizer_steps != expected_steps
    ):
        raise ValueError("Sona checkpoint training summary is invalid")
    training_device_type = _expect_string(
        document.get("training_device_type"), "training_device_type"
    )
    if training_device_type not in ("cpu", "cuda"):
        raise ValueError("Sona checkpoint training device type is invalid")
    if training_config.device != "auto" and training_device_type != training_config.device:
        raise ValueError("Sona checkpoint configured and recorded training devices disagree")
    result = SonaTrainingResult(
        checkpoint_manifest_sha256=manifest_sha256,
        weights_sha256=weights_sha256,
        dataset_manifest_sha256=_expect_string(
            document.get("dataset_manifest_sha256"), "dataset_manifest_sha256"
        ),
        model_config_sha256=model_config_sha256,
        training_config_sha256=training_config_sha256,
        tokenizer_sha256=_expect_string(document.get("tokenizer_sha256"), "tokenizer_sha256"),
        tokenizer_active_codes_per_level=tokenizer_active_codes_per_level,
        dataset_approval_sha256=_expect_optional_string(
            document.get("dataset_approval_sha256"), "dataset_approval_sha256"
        ),
        dataset_bundle_sha256=_expect_optional_string(
            document.get("dataset_bundle_sha256"), "dataset_bundle_sha256"
        ),
        source_approval_sha256=_expect_optional_string(
            document.get("source_approval_sha256"), "source_approval_sha256"
        ),
        epoch_total_losses=losses,
        optimizer_steps=optimizer_steps,
        device_type=training_device_type,
        quality_provenance_eligible=_expect_bool(
            document.get("quality_provenance_eligible"), "quality_provenance_eligible"
        ),
        quality_eligible=_expect_bool(document.get("quality_eligible"), "quality_eligible"),
    )
    for digest in (
        result.dataset_manifest_sha256,
        result.model_config_sha256,
        result.training_config_sha256,
        result.tokenizer_sha256,
    ):
        _validate_sha256(digest, "checkpoint digest")
    approval_digests = (
        result.dataset_approval_sha256,
        result.dataset_bundle_sha256,
        result.source_approval_sha256,
    )
    if result.quality_provenance_eligible:
        for approval_digest in approval_digests:
            if approval_digest is None:
                raise ValueError("Sona quality checkpoint provenance is incomplete")
            _validate_sha256(approval_digest, "checkpoint approval digest")
    elif any(value is not None for value in approval_digests):
        raise ValueError("Non-quality Sona checkpoint claims approval ancestry")
    if result.quality_eligible:
        raise ValueError("Final quality eligibility requires offline evaluation approval")
    return model, result


def compute_sona_model_weights_sha256(model: SonaLiteModel) -> str:
    """Hash an in-memory model exactly as the canonical checkpoint writer does."""

    overall_weights = sha256()
    for name, tensor in sorted(model.state_dict().items()):
        stream = BytesIO()
        np.save(stream, tensor.detach().cpu().contiguous().numpy(), allow_pickle=False)
        overall_weights.update(name.encode("utf-8"))
        overall_weights.update(sha256(stream.getvalue()).digest())
    return overall_weights.hexdigest()


def _batch_to_torch(
    dataset: SonaTensorDataset,
    *,
    indices: npt.NDArray[np.int64],
    device: torch.device,
) -> dict[str, Tensor]:
    names = (
        "history_sids",
        "history_actions",
        "history_origins",
        "history_age_buckets",
        "history_mask",
        "candidate_sids",
        "candidate_mask",
        "seed",
        "target_sids",
        "ranking_labels",
        "ranking_label_mask",
        "teacher_probabilities",
        "teacher_mask",
    )
    return {
        name: torch.from_numpy(
            np.ascontiguousarray(cast(npt.NDArray[np.generic], getattr(dataset, name))[indices])
        ).to(device)
        for name in names
    }


def _select_device(preference: DevicePreference) -> torch.device:
    if preference == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("Sona CUDA training was requested but CUDA is unavailable")
        return torch.device("cuda")
    if preference == "auto" and torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def _configure_determinism(device: torch.device, seed: int) -> None:
    if device.type == "cuda":
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    torch.manual_seed(seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def _validate_model_compatibility(dataset: SonaTensorDataset, model_config: SonaLiteConfig) -> None:
    maximum_code = max(
        int(dataset.history_sids.max(initial=0)),
        int(dataset.candidate_sids.max(initial=0)),
        int(dataset.target_sids.max(initial=0)),
    )
    if (
        model_config.codebook_size != dataset.tokenizer_active_codes_per_level + 1
        or maximum_code > dataset.tokenizer_active_codes_per_level
    ):
        raise ValueError("Sona model vocabulary does not match the bound tokenizer coverage")
    if (
        dataset.history_actions.max(initial=0) >= model_config.action_vocabulary_size
        or dataset.history_origins.max(initial=0) >= model_config.origin_vocabulary_size
        or dataset.history_age_buckets.max(initial=0) >= model_config.age_vocabulary_size
    ):
        raise ValueError("Sona dataset categorical value escapes the configured vocabulary")
    if dataset.history_sids.shape[1:] != (SONA_MAX_HISTORY_EVENTS, SONA_SID_DEPTH):
        raise ValueError("Sona dataset history shape is incompatible with the model")
    if dataset.candidate_sids.shape[1:] != (SONA_MAX_CANDIDATES, SONA_SID_DEPTH):
        raise ValueError("Sona dataset candidate shape is incompatible with the model")


def _write_checkpoint(
    model: SonaLiteModel,
    checkpoint_directory: Path,
    *,
    dataset: SonaTensorDataset,
    training_config: SonaTrainingConfig,
    epoch_total_losses: tuple[float, ...],
    optimizer_steps: int,
    device_type: str,
    dataset_approval_sha256: str | None,
    dataset_bundle_sha256: str | None,
    source_approval_sha256: str | None,
    before_publish: Callable[[], object] | None,
) -> SonaTrainingResult:
    if checkpoint_directory.exists():
        raise FileExistsError(f"Sona checkpoint output already exists: {checkpoint_directory}")
    checkpoint_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{checkpoint_directory.name}.", dir=checkpoint_directory.parent)
    )
    try:
        weights_directory = temporary / "weights"
        weights_directory.mkdir()
        weight_entries: list[JsonValue] = []
        overall_weights = sha256()
        for index, (name, tensor) in enumerate(sorted(model.state_dict().items())):
            path = weights_directory / f"{index:04d}.npy"
            array = tensor.detach().cpu().contiguous().numpy()
            with path.open("wb") as stream:
                np.save(stream, array, allow_pickle=False)
            digest = sha256(path.read_bytes()).hexdigest()
            overall_weights.update(name.encode("utf-8"))
            overall_weights.update(bytes.fromhex(digest))
            weight_entries.append(
                {
                    "name": name,
                    "file": path.name,
                    "sha256": digest,
                    "dtype": str(array.dtype),
                    "shape": list(array.shape),
                }
            )
        weights_sha256 = overall_weights.hexdigest()
        if compute_sona_model_weights_sha256(model) != weights_sha256:
            raise RuntimeError("Sona checkpoint in-memory and persisted weight hashes disagree")
        model_config_document = cast(dict[str, JsonValue], asdict(model.config))
        training_config_document = cast(dict[str, JsonValue], asdict(training_config))
        model_config_sha256 = sha256(rfc8785.dumps(model_config_document)).hexdigest()
        training_config_sha256 = sha256(rfc8785.dumps(training_config_document)).hexdigest()
        quality_provenance_eligible = dataset.quality_eligible
        approval_digests = (
            dataset_approval_sha256,
            dataset_bundle_sha256,
            source_approval_sha256,
        )
        if quality_provenance_eligible:
            for approval_digest in approval_digests:
                if approval_digest is None:
                    raise ValueError("Sona quality checkpoint provenance is incomplete")
                _validate_sha256(approval_digest, "checkpoint approval digest")
        elif any(value is not None for value in approval_digests):
            raise ValueError("Non-quality Sona checkpoint cannot claim approval ancestry")
        document: dict[str, JsonValue] = {
            "schema_version": SONA_CHECKPOINT_SCHEMA_VERSION,
            "format": _CHECKPOINT_FORMAT,
            "architecture": _CHECKPOINT_ARCHITECTURE,
            "dataset_manifest_sha256": dataset.manifest_sha256,
            "dataset_example_count": dataset.example_count,
            "tokenizer_sha256": dataset.tokenizer_sha256,
            "tokenizer_active_codes_per_level": dataset.tokenizer_active_codes_per_level,
            "data_classification": dataset.data_classification,
            "dataset_approval_sha256": dataset_approval_sha256,
            "dataset_bundle_sha256": dataset_bundle_sha256,
            "source_approval_sha256": source_approval_sha256,
            "quality_provenance_eligible": quality_provenance_eligible,
            "quality_eligible": False,
            "model_config": model_config_document,
            "model_config_sha256": model_config_sha256,
            "training_config": training_config_document,
            "training_config_sha256": training_config_sha256,
            "training_device_type": device_type,
            "epoch_total_losses": list(epoch_total_losses),
            "optimizer_steps": optimizer_steps,
            "weights_sha256": weights_sha256,
            "weights": weight_entries,
        }
        manifest_sha256 = sha256(rfc8785.dumps(document)).hexdigest()
        envelope: dict[str, JsonValue] = {
            "manifest": document,
            "manifest_sha256": manifest_sha256,
        }
        (temporary / "manifest.json").write_bytes(rfc8785.dumps(envelope))
        if before_publish is not None:
            before_publish()
        os.replace(temporary, checkpoint_directory)
        return SonaTrainingResult(
            checkpoint_manifest_sha256=manifest_sha256,
            weights_sha256=weights_sha256,
            dataset_manifest_sha256=dataset.manifest_sha256,
            model_config_sha256=model_config_sha256,
            training_config_sha256=training_config_sha256,
            tokenizer_sha256=dataset.tokenizer_sha256,
            tokenizer_active_codes_per_level=dataset.tokenizer_active_codes_per_level,
            dataset_approval_sha256=dataset_approval_sha256,
            dataset_bundle_sha256=dataset_bundle_sha256,
            source_approval_sha256=source_approval_sha256,
            epoch_total_losses=epoch_total_losses,
            optimizer_steps=optimizer_steps,
            device_type=device_type,
            quality_provenance_eligible=quality_provenance_eligible,
            quality_eligible=False,
        )
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _load_json_object(path: Path) -> dict[str, JsonValue]:
    parsed = cast(JsonValue, json.loads(path.read_bytes()))
    return _expect_object(parsed, str(path))


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1_048_576):
            digest.update(chunk)
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


def _expect_optional_string(value: JsonValue | None, field: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    return value


def _expect_int(value: JsonValue | None, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value


def _expect_float(value: JsonValue | None, field: str) -> float:
    if not isinstance(value, (float, int)) or isinstance(value, bool) or not isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _expect_bool(value: JsonValue | None, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _expect_device(value: JsonValue | None) -> DevicePreference:
    device = _expect_string(value, "device")
    if device not in ("auto", "cpu", "cuda"):
        raise ValueError("device must be auto, cpu, or cuda")
    return cast(DevicePreference, device)


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} is not a lowercase SHA-256 digest")


__all__ = (
    "SONA_CHECKPOINT_SCHEMA_VERSION",
    "SONA_MAX_TRAINING_BATCH",
    "SONA_MAX_TRAINING_EPOCHS",
    "DevicePreference",
    "SonaTrainingConfig",
    "SonaTrainingResult",
    "load_sona_checkpoint",
    "train_quality_sona_checkpoint",
    "train_sona_checkpoint",
)
