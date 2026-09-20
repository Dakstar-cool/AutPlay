"""Command-line entry point for reproducible Sona-Lite fixture and training evidence."""

from __future__ import annotations

import argparse
import sys
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
from pathlib import Path
from time import time_ns
from typing import cast
from uuid import UUID

import rfc8785
from autplay.domain.recommendations import JsonValue

from .benchmark import SonaBenchmarkResult, benchmark_sona_checkpoint
from .dataset import SONA_SOURCE_KIND_SYNTHETIC, load_sona_dataset_header
from .export import SonaOnnxExport, SonaOnnxProvenance, export_sona_onnx
from .fixture import (
    SonaFixtureBundle,
    materialize_synthetic_fixture_bundle,
    verify_synthetic_fixture_header,
)
from .model import SonaLiteConfig
from .production import SonaControlledPublication
from .quality_bundle import SonaQualityDatasetBundle, load_quality_approved_sona_dataset_bundle
from .trainer import (
    SonaTrainingConfig,
    SonaTrainingResult,
    load_quality_sona_checkpoint,
    load_sona_checkpoint,
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


@dataclass(frozen=True, slots=True)
class SonaExecutionRecovery:
    recovered_count: int
    execution_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SonaRestoreDrain:
    resource_executions: int
    ingest_executions: int
    ingest_cleanup_executions: int
    metadata_executions: int
    maintenance_executions: int
    training_executions: int
    checked_pids: int
    checked_cgroups: int
    recovered_training_executions: int


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
        | SonaExecutionRecovery
        | SonaRestoreDrain
        | SonaControlledPublication
    )
    if command == "restore-drain":
        result = _restore_drain(namespace)
    elif command == "recover-executions":
        result = _recover_executions(namespace)
    elif command == "controlled-train-publish":
        try:
            # Keep stdout as a single canonical JSON document even when an
            # exporter dependency emits progress with plain ``print`` calls.
            with redirect_stdout(sys.stderr):
                result = _controlled_train_publish(namespace)
        except Exception:
            sys.stderr.write('{"error":"controlled_training_publication_unavailable"}\n')
            return 4
    elif command == "fixture":
        result = materialize_synthetic_fixture_bundle(
            Path(namespace.output),
            recording_count=namespace.recording_count,
        )
    elif command == "train":
        dataset_path = Path(namespace.dataset)
        header = load_sona_dataset_header(dataset_path)
        if header.source_kind != SONA_SOURCE_KIND_SYNTHETIC:
            return _shared_execution_unavailable()
        verify_synthetic_fixture_header(header)
        codebook_size = cast(int | None, namespace.codebook_size)
        if codebook_size is None:
            codebook_size = header.tokenizer_active_codes_per_level + 1
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
        # This standalone entry point has no retained execution composition. Refuse
        # before the historical bundle reader touches any owner-derived payload.
        return _shared_execution_unavailable()
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


def _shared_execution_unavailable() -> int:
    sys.stderr.write('{"error":"shared_training_execution_not_configured"}\n')
    return 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autplay-sona-training")
    subparsers = parser.add_subparsers(dest="command", required=True)

    recovery = subparsers.add_parser(
        "recover-executions",
        help="resume durable exact-exit cleanup inside configured exclusive roots",
    )
    recovery.add_argument("--input-root", required=True)
    recovery.add_argument("--output-root", required=True)
    recovery.add_argument("--limit", type=int, choices=range(1, 1001), default=100)

    restore = subparsers.add_parser(
        "restore-drain",
        help="offline-close restored process reservations and drain training cleanup",
    )
    restore.add_argument("--input-root", required=True)
    restore.add_argument("--output-root", required=True)
    restore.add_argument("--limit", type=int, choices=range(1, 1001), default=100)

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
        help="owner quality training requires configured controlled execution",
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

    controlled = subparsers.add_parser(
        "controlled-train-publish",
        help="admit, retain, train and publish one owner-derived run",
    )
    controlled.add_argument("--run-id", required=True)
    controlled.add_argument("--execution-id", required=True)
    controlled.add_argument("--publication-operation-id", required=True)
    controlled.add_argument(
        "--participant",
        action="append",
        required=True,
        help="exact UUID:consent-revision; repeat once per contributor",
    )
    controlled.add_argument("--input-root", required=True)
    controlled.add_argument("--dataset-relative", default="dataset")
    controlled.add_argument("--output-root", required=True)
    controlled.add_argument("--tokenizer-relative", default="tokenizer")
    controlled.add_argument("--artifact-name", required=True)
    controlled.add_argument("--lineage-key-id", required=True)
    controlled.add_argument("--lineage-key-file", required=True)
    controlled.add_argument("--maximum-output-bytes", type=int, required=True)
    controlled.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    controlled.add_argument("--epochs", type=int, default=1)
    controlled.add_argument("--batch-size", type=int, default=8)
    controlled.add_argument("--learning-rate", type=float, default=1e-3)
    controlled.add_argument("--weight-decay", type=float, default=1e-4)
    controlled.add_argument("--gradient-clip-norm", type=float, default=1.0)
    controlled.add_argument("--seed", type=int, default=0)
    controlled.add_argument("--codebook-size", type=int, required=True)
    controlled.add_argument("--model-dimensions", type=int, default=192)
    controlled.add_argument("--encoder-layers", type=int, default=3)

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


def _canonical_uuid(value: str) -> UUID:
    result = UUID(value)
    if str(result) != value:
        raise ValueError("noncanonical UUID")
    return result


def _participants(values: list[str]) -> dict[UUID, int]:
    result: dict[UUID, int] = {}
    for value in values:
        owner_value, separator, revision_value = value.partition(":")
        owner = _canonical_uuid(owner_value)
        if separator != ":" or owner in result or not revision_value.isdecimal():
            raise ValueError("invalid controlled training participant")
        revision = int(revision_value)
        if not 1 <= revision <= 2**53 - 1:
            raise ValueError("invalid controlled training participant revision")
        result[owner] = revision
    if not 1 <= len(result) <= 4096:
        raise ValueError("invalid controlled training participant count")
    return result


def _controlled_train_publish(namespace: argparse.Namespace) -> SonaControlledPublication:
    import os
    from functools import partial

    from autplay.adapters.filesystem.vault_process import ProcessTreeFactory
    from autplay.adapters.linux_process_tree import LinuxCgroupTree
    from autplay.adapters.postgresql.readiness import PostgreSQLReadinessProbe
    from autplay.adapters.postgresql.runtime_database import create_runtime_engine
    from autplay.adapters.windows_process_tree import WindowsJobTree
    from autplay.entrypoints.privacy_deletion import enforce_privacy_restore_guard
    from autplay.entrypoints.training_consent_restore import (
        build_training_consent_ledger,
        enforce_training_consent_restore_guard,
    )
    from autplay.runtime.settings import load_worker_settings

    from .production import run_controlled_training_publication

    input_root = Path(namespace.input_root)
    dataset_relative = Path(namespace.dataset_relative)
    key_path = Path(namespace.lineage_key_file)
    if not key_path.is_absolute() or key_path.is_symlink():
        raise ValueError("unsafe controlled training lineage key file")
    key_size = key_path.stat(follow_symlinks=False).st_size
    if not 32 <= key_size <= 1024:
        raise ValueError("invalid controlled training lineage key")
    lineage_key = bytearray(key_path.read_bytes())
    if len(lineage_key) != key_size:
        raise ValueError("controlled training lineage key changed")

    settings = load_worker_settings()
    engine = create_runtime_engine(settings)
    try:
        if not PostgreSQLReadinessProbe(engine).check().ready:
            raise RuntimeError("database_unavailable")
        enforce_privacy_restore_guard(settings, engine)
        enforce_training_consent_restore_guard(settings, engine)
        ledger = build_training_consent_ledger(settings)
        if ledger is None:
            raise RuntimeError("training_consent_evidence_unavailable")
        tree_factory: ProcessTreeFactory
        if os.name == "nt":
            tree_factory = WindowsJobTree
        elif settings.worker_cgroup_root is not None:
            tree_factory = partial(LinuxCgroupTree, settings.worker_cgroup_root)
        else:
            raise RuntimeError("resource_process_tree_unavailable")
        return run_controlled_training_publication(
            engine,
            ledger,
            run_id=_canonical_uuid(namespace.run_id),
            execution_id=_canonical_uuid(namespace.execution_id),
            publication_operation_id=_canonical_uuid(namespace.publication_operation_id),
            participants=_participants(namespace.participant),
            input_root=input_root,
            dataset_relative=dataset_relative,
            output_root=Path(namespace.output_root),
            tokenizer_relative=Path(namespace.tokenizer_relative),
            artifact_name=namespace.artifact_name,
            lineage_key_id=namespace.lineage_key_id,
            lineage_key=bytes(lineage_key),
            maximum_output_bytes=namespace.maximum_output_bytes,
            model_config=SonaLiteConfig(
                codebook_size=namespace.codebook_size,
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
            tree_factory=tree_factory,
        )
    finally:
        lineage_key[:] = b"\x00" * len(lineage_key)
        engine.dispose()


def _recover_executions(namespace: argparse.Namespace) -> SonaExecutionRecovery:
    from autplay.adapters.postgresql.runtime_database import create_runtime_engine
    from autplay.adapters.postgresql.training_execution import (
        PostgresTrainingExecutionRepository,
    )
    from autplay.entrypoints.privacy_deletion import enforce_privacy_restore_guard
    from autplay.runtime.settings import load_worker_settings
    from sqlalchemy.orm import Session, sessionmaker

    from .controlled_execution import (
        SonaTrainingCleanupRecovery,
        SonaTrainingStorageScopes,
    )

    settings = load_worker_settings()
    engine = create_runtime_engine(settings)
    try:
        enforce_privacy_restore_guard(settings, engine)
        repository = PostgresTrainingExecutionRepository(
            sessionmaker(engine, class_=Session, expire_on_commit=False)
        )
        completed: tuple[UUID, ...] = SonaTrainingCleanupRecovery(
            repository,
            SonaTrainingStorageScopes(
                Path(namespace.input_root),
                Path(namespace.output_root),
            ),
        ).recover_pending(limit=namespace.limit)
        return SonaExecutionRecovery(
            recovered_count=len(completed),
            execution_ids=tuple(str(execution) for execution in completed),
        )
    finally:
        engine.dispose()


def _restore_drain(namespace: argparse.Namespace) -> SonaRestoreDrain:
    from autplay.adapters.offline_process_evidence import OfflineProcessEvidenceProbe
    from autplay.adapters.postgresql.offline_execution_drain import (
        PostgresOfflineExecutionDrain,
    )
    from autplay.adapters.postgresql.readiness import PostgreSQLReadinessProbe
    from autplay.adapters.postgresql.runtime_database import create_runtime_engine
    from autplay.adapters.postgresql.training_execution import (
        PostgresTrainingExecutionRepository,
    )
    from autplay.runtime.settings import load_worker_settings
    from sqlalchemy.orm import Session, sessionmaker

    from .controlled_execution import SonaTrainingCleanupRecovery, SonaTrainingStorageScopes

    settings = load_worker_settings()
    engine = create_runtime_engine(settings)
    try:
        readiness = PostgreSQLReadinessProbe(engine).check()
        if not readiness.ready:
            raise RuntimeError(readiness.code or "database_unavailable")
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        closed = PostgresOfflineExecutionDrain(
            sessions,
            OfflineProcessEvidenceProbe(settings.worker_cgroup_root),
        ).close_restored_reservations()
        recovery = SonaTrainingCleanupRecovery(
            PostgresTrainingExecutionRepository(sessions),
            SonaTrainingStorageScopes(Path(namespace.input_root), Path(namespace.output_root)),
        )
        recovered: list[UUID] = []
        while batch := recovery.recover_pending(limit=namespace.limit):
            recovered.extend(batch)
        return SonaRestoreDrain(
            closed.resource_executions,
            closed.ingest_executions,
            closed.ingest_cleanup_executions,
            closed.metadata_executions,
            closed.maintenance_executions,
            closed.training_stopping,
            closed.checked_pids,
            closed.checked_cgroups,
            len(recovered),
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "SonaExecutionRecovery",
    "SonaQualityBundleVerification",
    "SonaRestoreDrain",
    "main",
)
