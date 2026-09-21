"""Validated ONNX export for the exact Sona-Lite CUDA runtime contract."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path

import onnx
import rfc8785
import torch
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import SONA_MAX_CANDIDATES, SONA_MAX_HISTORY_EVENTS
from autplay.domain.training_work import TrainingInputProvenance

from .model import SonaLiteModel
from .trainer import compute_sona_model_weights_sha256

SONA_ONNX_OPSET = 20
_INPUT_NAMES = (
    "history_sids",
    "history_actions",
    "history_origins",
    "history_age_buckets",
    "history_mask",
    "candidate_sids",
    "candidate_mask",
    "seed",
)
_OUTPUT_NAMES = (
    "generated_sids",
    "generated_log_probabilities",
    "ranking_head_scores",
    "ranking_scores",
)


@dataclass(frozen=True, slots=True)
class SonaOnnxExport:
    artifact_sha256: str
    weights_sha256: str
    config_sha256: str
    model_manifest_sha256: str
    opset: int
    checkpoint_manifest_sha256: str | None
    dataset_approval_sha256: str | None
    dataset_bundle_sha256: str | None
    quality_provenance_eligible: bool
    quality_eligible: bool
    commit_sha256: str | None


@dataclass(frozen=True, slots=True)
class SonaOnnxProvenance:
    """Exact training inputs bound into the exported model manifest."""

    checkpoint_manifest_sha256: str
    checkpoint_weights_sha256: str
    dataset_manifest_sha256: str
    tokenizer_sha256: str
    dataset_approval_sha256: str | None
    dataset_bundle_sha256: str | None
    quality_provenance_eligible: bool
    quality_eligible: bool
    training_authority: TrainingInputProvenance | None = None
    tokenizer_artifact_manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        for digest in (
            self.checkpoint_manifest_sha256,
            self.checkpoint_weights_sha256,
            self.dataset_manifest_sha256,
            self.tokenizer_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("Sona ONNX provenance hash is invalid")
        approval_digests = (self.dataset_approval_sha256, self.dataset_bundle_sha256)
        if self.quality_provenance_eligible:
            for approval_digest in approval_digests:
                if (
                    approval_digest is None
                    or len(approval_digest) != 64
                    or any(character not in "0123456789abcdef" for character in approval_digest)
                ):
                    raise ValueError("Sona ONNX approval hash is invalid")
        elif any(value is not None for value in approval_digests):
            raise ValueError("Non-quality Sona ONNX provenance claims dataset approval")
        if self.quality_eligible:
            raise ValueError("Final Sona ONNX quality eligibility requires evaluation approval")
        if self.training_authority is not None:
            if not isinstance(self.training_authority, TrainingInputProvenance):
                raise ValueError("Sona ONNX training authority is invalid")
            artifact_manifest_digest = self.tokenizer_artifact_manifest_sha256
            if (
                artifact_manifest_digest is None
                or len(artifact_manifest_digest) != 64
                or any(
                    character not in "0123456789abcdef" for character in artifact_manifest_digest
                )
            ):
                raise ValueError("Sona ONNX tokenizer artifact manifest hash is invalid")
        elif self.tokenizer_artifact_manifest_sha256 is not None:
            raise ValueError("Sona ONNX tokenizer artifact binding requires training authority")


@dataclass(frozen=True, slots=True)
class _PreparedOnnxExport:
    result: SonaOnnxExport
    artifact: bytes
    manifest: bytes


def export_sona_onnx(
    model: SonaLiteModel,
    output_path: Path,
    *,
    provenance: SonaOnnxProvenance | None = None,
    before_publish: Callable[[], object] | None = None,
    maximum_total_bytes: int | None = None,
    base_output_bytes: int = 0,
) -> SonaOnnxExport:
    """Install a candidate behind a crash-consistency marker; this is not PG publication."""

    manifest_path = output_path.with_suffix(f"{output_path.suffix}.manifest.json")
    commit_path = output_path.with_suffix(f"{output_path.suffix}.commit.json")
    if output_path.exists() or manifest_path.exists() or commit_path.exists():
        raise FileExistsError("Sona ONNX output, manifest, or commit marker already exists")
    if (
        provenance is not None
        and compute_sona_model_weights_sha256(model) != provenance.checkpoint_weights_sha256
    ):
        raise ValueError("Sona ONNX model weights do not match the bound checkpoint")
    if provenance is not None and provenance.quality_provenance_eligible and before_publish is None:
        raise ValueError("Sona quality ONNX export requires signed bundle re-verification")
    if (
        type(base_output_bytes) is not int
        or base_output_bytes < 0
        or (
            maximum_total_bytes is not None
            and (type(maximum_total_bytes) is not int or not 1 <= maximum_total_bytes <= 2**63 - 1)
        )
    ):
        raise ValueError("Sona ONNX output bound is invalid")
    prepared = _prepare_sona_onnx(model, provenance=provenance)
    result = prepared.result
    commit_document: dict[str, JsonValue] = {
        "schema_version": 1,
        "state": "COMMITTED",
        "artifact_sha256": result.artifact_sha256,
        "model_manifest_sha256": result.model_manifest_sha256,
    }
    commit_sha256 = sha256(rfc8785.dumps(commit_document)).hexdigest()
    commit_envelope: dict[str, JsonValue] = {
        "commit": commit_document,
        "commit_sha256": commit_sha256,
    }
    commit_payload = rfc8785.dumps(commit_envelope)
    if (
        maximum_total_bytes is not None
        and base_output_bytes
        + len(prepared.artifact)
        + len(prepared.manifest)
        + len(commit_payload)
        > maximum_total_bytes
    ):
        raise ValueError("Sona ONNX output exceeds the admitted byte bound")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.", dir=output_path.parent))
    temporary_output = temporary / output_path.name
    temporary_manifest = temporary_output.with_suffix(f"{temporary_output.suffix}.manifest.json")
    temporary_commit = temporary_output.with_suffix(f"{temporary_output.suffix}.commit.json")
    try:
        temporary_output.write_bytes(prepared.artifact)
        temporary_manifest.write_bytes(prepared.manifest)
        temporary_commit.write_bytes(commit_payload)
        if before_publish is not None:
            before_publish()
        os.link(temporary_output, output_path)
        try:
            os.link(temporary_manifest, manifest_path)
            os.link(temporary_commit, commit_path)
        except BaseException:
            output_path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            commit_path.unlink(missing_ok=True)
            raise
        return replace(result, commit_sha256=commit_sha256)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _prepare_sona_onnx(
    model: SonaLiteModel,
    *,
    provenance: SonaOnnxProvenance | None = None,
) -> _PreparedOnnxExport:
    """Export and validate in memory so a byte bound is checked before disk writes."""

    model.eval()
    batch_size = 1
    arguments = (
        torch.zeros((batch_size, SONA_MAX_HISTORY_EVENTS, 3), dtype=torch.int64),
        torch.zeros((batch_size, SONA_MAX_HISTORY_EVENTS), dtype=torch.int64),
        torch.zeros((batch_size, SONA_MAX_HISTORY_EVENTS), dtype=torch.int64),
        torch.zeros((batch_size, SONA_MAX_HISTORY_EVENTS), dtype=torch.int64),
        torch.zeros((batch_size, SONA_MAX_HISTORY_EVENTS), dtype=torch.int64),
        torch.zeros((batch_size, SONA_MAX_CANDIDATES, 3), dtype=torch.int64),
        torch.zeros((batch_size, SONA_MAX_CANDIDATES), dtype=torch.int64),
        torch.zeros((batch_size,), dtype=torch.int64),
    )
    with torch.inference_mode():
        program = torch.onnx.export(
            model,
            arguments,
            None,
            input_names=_INPUT_NAMES,
            output_names=_OUTPUT_NAMES,
            opset_version=SONA_ONNX_OPSET,
            dynamo=True,
            external_data=False,
            optimize=True,
        )
    if program is None:
        raise RuntimeError("Sona ONNX exporter did not return a model")
    graph = program.model_proto
    onnx.checker.check_model(graph)
    if (
        tuple(value.name for value in graph.graph.input) != _INPUT_NAMES
        or tuple(value.name for value in graph.graph.output) != _OUTPUT_NAMES
    ):
        raise RuntimeError("Sona ONNX export does not match the runtime contract")

    artifact_payload = graph.SerializeToString()
    artifact_sha256 = sha256(artifact_payload).hexdigest()
    weights_sha256 = compute_sona_model_weights_sha256(model)
    if provenance is not None and weights_sha256 != provenance.checkpoint_weights_sha256:
        raise ValueError("Sona ONNX model weights changed during export")
    config_document = {"architecture": "SONA_LITE_SHARED_GRU_V1", **asdict(model.config)}
    config_sha256 = sha256(rfc8785.dumps(config_document)).hexdigest()
    manifest: dict[str, JsonValue] = {
        "schema_version": 2 if provenance is not None and provenance.training_authority else 1,
        "architecture": "SONA_LITE_SHARED_GRU_V1",
        "opset": SONA_ONNX_OPSET,
        "inputs": list(_INPUT_NAMES),
        "outputs": list(_OUTPUT_NAMES),
        "artifact_sha256": artifact_sha256,
        "weights_sha256": weights_sha256,
        "config_sha256": config_sha256,
        "training_provenance": (
            {
                "checkpoint_manifest_sha256": provenance.checkpoint_manifest_sha256,
                "checkpoint_weights_sha256": provenance.checkpoint_weights_sha256,
                "dataset_manifest_sha256": provenance.dataset_manifest_sha256,
                "tokenizer_sha256": provenance.tokenizer_sha256,
                "dataset_approval_sha256": provenance.dataset_approval_sha256,
                "dataset_bundle_sha256": provenance.dataset_bundle_sha256,
                "quality_provenance_eligible": provenance.quality_provenance_eligible,
                "quality_eligible": provenance.quality_eligible,
                **(
                    {
                        "training_authority": provenance.training_authority.document(),
                        "tokenizer_artifact_manifest_sha256": (
                            provenance.tokenizer_artifact_manifest_sha256
                        ),
                    }
                    if provenance.training_authority is not None
                    else {}
                ),
            }
            if provenance is not None
            else {"state": "UNBOUND_TEST_ONLY", "quality_eligible": False}
        ),
    }
    model_manifest_sha256 = sha256(rfc8785.dumps(manifest)).hexdigest()
    envelope: dict[str, JsonValue] = {
        "manifest": manifest,
        "manifest_sha256": model_manifest_sha256,
    }
    manifest_payload = rfc8785.dumps(envelope)
    result = SonaOnnxExport(
        artifact_sha256=artifact_sha256,
        weights_sha256=weights_sha256,
        config_sha256=config_sha256,
        model_manifest_sha256=model_manifest_sha256,
        opset=SONA_ONNX_OPSET,
        checkpoint_manifest_sha256=(
            provenance.checkpoint_manifest_sha256 if provenance is not None else None
        ),
        dataset_approval_sha256=(
            provenance.dataset_approval_sha256 if provenance is not None else None
        ),
        dataset_bundle_sha256=(
            provenance.dataset_bundle_sha256 if provenance is not None else None
        ),
        quality_provenance_eligible=(
            provenance.quality_provenance_eligible if provenance is not None else False
        ),
        quality_eligible=provenance.quality_eligible if provenance is not None else False,
        commit_sha256=None,
    )
    return _PreparedOnnxExport(result, artifact_payload, manifest_payload)


__all__ = (
    "SONA_ONNX_OPSET",
    "SonaOnnxExport",
    "SonaOnnxProvenance",
    "export_sona_onnx",
)
