"""Command-line entry point for reproducible Sona-Lite fixture and training evidence."""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import time_ns
from typing import cast

import rfc8785
from autplay.domain.recommendations import JsonValue

from .benchmark import SonaBenchmarkResult, benchmark_sona_checkpoint
from .dataset import load_sona_dataset
from .export import SonaOnnxExport, SonaOnnxProvenance, export_sona_onnx
from .fixture import SonaFixtureBundle, materialize_synthetic_fixture_bundle
from .model import SonaLiteConfig
from .quality_bundle import SonaQualityDatasetBundle, load_quality_approved_sona_dataset_bundle
from .trainer import (
    SonaTrainingConfig,
    SonaTrainingResult,
    load_quality_sona_checkpoint,
    load_sona_checkpoint,
    train_quality_sona_checkpoint,
    train_sona_checkpoint,
)


@dataclass(frozen=True, slots=True)
class SonaQualityBundleVerification:
    dataset_bundle_sha256: str
    dataset_approval_sha256: str
    source_approval_sha256: str
    train_manifest_sha256: str
    validation_manifest_sha256: str
    test_manifest_sha256: str
    tokenizer_manifest_sha256: str


def main(arguments: list[str] | None = None) -> int:
    parser = _parser()
    namespace = parser.parse_args(arguments)
    command = cast(str, namespace.command)
    result: (
        SonaFixtureBundle
        | SonaTrainingResult
        | SonaOnnxExport
        | SonaBenchmarkResult
        | SonaQualityBundleVerification
    )
    if command == "fixture":
        result = materialize_synthetic_fixture_bundle(
            Path(namespace.output),
            recording_count=namespace.recording_count,
        )
    elif command == "train":
        dataset_path = Path(namespace.dataset)
        codebook_size = cast(int | None, namespace.codebook_size)
        if codebook_size is None:
            codebook_size = load_sona_dataset(dataset_path).tokenizer_active_codes_per_level + 1
        result = train_sona_checkpoint(
            dataset_path,
            Path(namespace.checkpoint),
            model_config=SonaLiteConfig(
                codebook_size=codebook_size,
                model_dimensions=namespace.model_dimensions,
                encoder_layers=namespace.encoder_layers,
            ),
            training_config=SonaTrainingConfig(
                epochs=namespace.epochs,
                batch_size=namespace.batch_size,
                learning_rate=namespace.learning_rate,
                weight_decay=namespace.weight_decay,
                gradient_clip_norm=namespace.gradient_clip_norm,
                seed=namespace.seed,
                device=namespace.device,
            ),
        )
    elif command == "train-quality":
        bundle = load_quality_approved_sona_dataset_bundle(
            train_directory=Path(namespace.train_dataset),
            validation_directory=Path(namespace.validation_dataset),
            test_directory=Path(namespace.test_dataset),
            tokenizer_directory=Path(namespace.tokenizer),
            source_manifest_path=Path(namespace.source_manifest),
            catalog_manifest_path=Path(namespace.catalog_manifest),
            source_rekey_plan_path=Path(namespace.source_rekey_plan),
            source_provenance_acceptance_path=Path(namespace.source_provenance_acceptance),
            teacher_calibration_path=Path(namespace.teacher_calibration),
            teacher_manifest_path=Path(namespace.teacher_manifest),
            source_approval_path=Path(namespace.source_approval),
            dataset_approval_path=Path(namespace.dataset_approval),
            at_ms=time_ns() // 1_000_000,
        )
        codebook_size = cast(int | None, namespace.codebook_size)
        if codebook_size is None:
            codebook_size = bundle.train.tokenizer_active_codes_per_level + 1
        result = train_quality_sona_checkpoint(
            bundle,
            Path(namespace.checkpoint),
            model_config=SonaLiteConfig(
                codebook_size=codebook_size,
                model_dimensions=namespace.model_dimensions,
                encoder_layers=namespace.encoder_layers,
            ),
            training_config=SonaTrainingConfig(
                epochs=namespace.epochs,
                batch_size=namespace.batch_size,
                learning_rate=namespace.learning_rate,
                weight_decay=namespace.weight_decay,
                gradient_clip_norm=namespace.gradient_clip_norm,
                seed=namespace.seed,
                device=namespace.device,
            ),
        )
    elif command == "export":
        model, checkpoint = load_sona_checkpoint(Path(namespace.checkpoint))
        result = export_sona_onnx(
            model,
            Path(namespace.output),
            provenance=SonaOnnxProvenance(
                checkpoint_manifest_sha256=checkpoint.checkpoint_manifest_sha256,
                checkpoint_weights_sha256=checkpoint.weights_sha256,
                dataset_manifest_sha256=checkpoint.dataset_manifest_sha256,
                tokenizer_sha256=checkpoint.tokenizer_sha256,
                dataset_approval_sha256=checkpoint.dataset_approval_sha256,
                dataset_bundle_sha256=checkpoint.dataset_bundle_sha256,
                quality_provenance_eligible=checkpoint.quality_provenance_eligible,
                quality_eligible=checkpoint.quality_eligible,
            ),
        )
    elif command == "export-quality":
        bundle = _load_quality_bundle(namespace)
        model, checkpoint = load_quality_sona_checkpoint(Path(namespace.checkpoint), bundle)
        result = export_sona_onnx(
            model,
            Path(namespace.output),
            provenance=SonaOnnxProvenance(
                checkpoint_manifest_sha256=checkpoint.checkpoint_manifest_sha256,
                checkpoint_weights_sha256=checkpoint.weights_sha256,
                dataset_manifest_sha256=checkpoint.dataset_manifest_sha256,
                tokenizer_sha256=checkpoint.tokenizer_sha256,
                dataset_approval_sha256=checkpoint.dataset_approval_sha256,
                dataset_bundle_sha256=checkpoint.dataset_bundle_sha256,
                quality_provenance_eligible=checkpoint.quality_provenance_eligible,
                quality_eligible=checkpoint.quality_eligible,
            ),
            before_publish=lambda: _load_quality_bundle(namespace),
        )
    elif command == "benchmark":
        result = benchmark_sona_checkpoint(
            Path(namespace.checkpoint),
            Path(namespace.artifact),
            Path(namespace.dataset),
            Path(namespace.tokenizer),
            Path(namespace.output),
            device_preference=namespace.device,
            warmup_iterations=namespace.warmup_iterations,
            measured_iterations=namespace.measured_iterations,
        )
    elif command == "benchmark-quality":
        bundle = _load_quality_bundle(namespace)
        result = benchmark_sona_checkpoint(
            Path(namespace.checkpoint),
            Path(namespace.artifact),
            Path(namespace.test_dataset),
            Path(namespace.tokenizer),
            Path(namespace.output),
            device_preference=namespace.device,
            warmup_iterations=namespace.warmup_iterations,
            measured_iterations=namespace.measured_iterations,
            quality_bundle=bundle,
            before_publish=lambda: _load_quality_bundle(namespace),
        )
    elif command == "verify-quality-bundle":
        bundle = load_quality_approved_sona_dataset_bundle(
            train_directory=Path(namespace.train_dataset),
            validation_directory=Path(namespace.validation_dataset),
            test_directory=Path(namespace.test_dataset),
            tokenizer_directory=Path(namespace.tokenizer),
            source_manifest_path=Path(namespace.source_manifest),
            catalog_manifest_path=Path(namespace.catalog_manifest),
            source_rekey_plan_path=Path(namespace.source_rekey_plan),
            source_provenance_acceptance_path=Path(namespace.source_provenance_acceptance),
            teacher_calibration_path=Path(namespace.teacher_calibration),
            teacher_manifest_path=Path(namespace.teacher_manifest),
            source_approval_path=Path(namespace.source_approval),
            dataset_approval_path=Path(namespace.dataset_approval),
            at_ms=time_ns() // 1_000_000,
        )
        result = SonaQualityBundleVerification(
            dataset_bundle_sha256=bundle.dataset_approval.dataset_bundle_sha256,
            dataset_approval_sha256=bundle.dataset_approval.approval_sha256,
            source_approval_sha256=bundle.source_approval.approval_sha256,
            train_manifest_sha256=bundle.train.manifest_sha256,
            validation_manifest_sha256=bundle.validation.manifest_sha256,
            test_manifest_sha256=bundle.test.manifest_sha256,
            tokenizer_manifest_sha256=bundle.tokenizer.manifest_sha256,
        )
    else:
        parser.error(f"unsupported command: {command}")
    payload = cast(dict[str, JsonValue], asdict(result))
    sys.stdout.buffer.write(rfc8785.dumps(payload) + b"\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autplay-sona-training")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fixture = subparsers.add_parser("fixture", help="materialize non-quality synthetic artifacts")
    fixture.add_argument("--output", required=True)
    fixture.add_argument("--recording-count", type=int, default=1)

    train = subparsers.add_parser("train", help="train one bounded immutable checkpoint")
    train.add_argument("--dataset", required=True)
    train.add_argument("--checkpoint", required=True)
    train.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    train.add_argument("--epochs", type=int, default=1)
    train.add_argument("--batch-size", type=int, default=8)
    train.add_argument("--learning-rate", type=float, default=1e-3)
    train.add_argument("--weight-decay", type=float, default=1e-4)
    train.add_argument("--gradient-clip-norm", type=float, default=1.0)
    train.add_argument("--seed", type=int, default=0)
    train.add_argument("--codebook-size", type=int)
    train.add_argument("--model-dimensions", type=int, default=192)
    train.add_argument("--encoder-layers", type=int, default=3)

    train_quality = subparsers.add_parser(
        "train-quality",
        help="verify an approved bundle and train one provenance-eligible candidate",
    )
    _add_quality_bundle_arguments(train_quality)
    train_quality.add_argument("--checkpoint", required=True)
    train_quality.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    train_quality.add_argument("--epochs", type=int, default=1)
    train_quality.add_argument("--batch-size", type=int, default=8)
    train_quality.add_argument("--learning-rate", type=float, default=1e-3)
    train_quality.add_argument("--weight-decay", type=float, default=1e-4)
    train_quality.add_argument("--gradient-clip-norm", type=float, default=1.0)
    train_quality.add_argument("--seed", type=int, default=0)
    train_quality.add_argument("--codebook-size", type=int)
    train_quality.add_argument("--model-dimensions", type=int, default=192)
    train_quality.add_argument("--encoder-layers", type=int, default=3)

    export = subparsers.add_parser("export", help="export a verified checkpoint to ONNX")
    export.add_argument("--checkpoint", required=True)
    export.add_argument("--output", required=True)

    export_quality = subparsers.add_parser(
        "export-quality",
        help="re-verify a signed quality bundle and export its exact candidate checkpoint",
    )
    _add_quality_bundle_arguments(export_quality)
    export_quality.add_argument("--checkpoint", required=True)
    export_quality.add_argument("--output", required=True)

    benchmark = subparsers.add_parser("benchmark", help="record bounded inference latency evidence")
    benchmark.add_argument("--checkpoint", required=True)
    benchmark.add_argument("--artifact", required=True)
    benchmark.add_argument("--dataset", required=True)
    benchmark.add_argument("--tokenizer", required=True)
    benchmark.add_argument("--output", required=True)
    benchmark.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    benchmark.add_argument("--warmup-iterations", type=int, default=5)
    benchmark.add_argument("--measured-iterations", type=int, default=20)

    benchmark_quality = subparsers.add_parser(
        "benchmark-quality",
        help="re-verify a quality bundle and benchmark its committed ONNX candidate on test",
    )
    _add_quality_bundle_arguments(benchmark_quality)
    benchmark_quality.add_argument("--checkpoint", required=True)
    benchmark_quality.add_argument("--artifact", required=True)
    benchmark_quality.add_argument("--output", required=True)
    benchmark_quality.add_argument("--device", choices=("cuda",), default="cuda")
    benchmark_quality.add_argument("--warmup-iterations", type=int, default=5)
    benchmark_quality.add_argument("--measured-iterations", type=int, default=20)

    verify_quality = subparsers.add_parser(
        "verify-quality-bundle",
        help="verify one signed quality bundle with the deployment-pinned reviewer key",
    )
    _add_quality_bundle_arguments(verify_quality)
    return parser


def _add_quality_bundle_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--train-dataset", required=True)
    parser.add_argument("--validation-dataset", required=True)
    parser.add_argument("--test-dataset", required=True)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--catalog-manifest", required=True)
    parser.add_argument("--source-rekey-plan", required=True)
    parser.add_argument("--source-provenance-acceptance", required=True)
    parser.add_argument("--teacher-calibration", required=True)
    parser.add_argument("--teacher-manifest", required=True)
    parser.add_argument("--source-approval", required=True)
    parser.add_argument("--dataset-approval", required=True)


def _load_quality_bundle(namespace: argparse.Namespace) -> SonaQualityDatasetBundle:
    return load_quality_approved_sona_dataset_bundle(
        train_directory=Path(namespace.train_dataset),
        validation_directory=Path(namespace.validation_dataset),
        test_directory=Path(namespace.test_dataset),
        tokenizer_directory=Path(namespace.tokenizer),
        source_manifest_path=Path(namespace.source_manifest),
        catalog_manifest_path=Path(namespace.catalog_manifest),
        source_rekey_plan_path=Path(namespace.source_rekey_plan),
        source_provenance_acceptance_path=Path(namespace.source_provenance_acceptance),
        teacher_calibration_path=Path(namespace.teacher_calibration),
        teacher_manifest_path=Path(namespace.teacher_manifest),
        source_approval_path=Path(namespace.source_approval),
        dataset_approval_path=Path(namespace.dataset_approval),
        at_ms=time_ns() // 1_000_000,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("SonaQualityBundleVerification", "main")
