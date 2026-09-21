"""Production composition for one admitted owner-derived train-and-publish operation."""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.adapters.filesystem.vault_process import ProcessTreeFactory
from autplay.adapters.postgresql.internal_io import internal_io_policy
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.training_work import (
    TrainingExecutionRow,
    TrainingRunRow,
)
from autplay.adapters.postgresql.training_execution import PostgresTrainingExecutionRepository
from autplay.application.training_work import TrainingWorkError, TrainingWorkService
from autplay.domain.training_execution import TrainingExecutionTicket
from autplay.domain.vault import Sha256Digest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from .authority import PostgresSonaTrainingAuthority
from .controlled_execution import SonaTrainingProcessCoordinator, SonaTrainingStorageScopes
from .dataset import SONA_SOURCE_KIND_OWNER_APPROVED, load_sona_dataset_header
from .execution_protocol import (
    SonaControlledTrainingCommand,
    SonaControlledTrainingResult,
    finalize_checkpoint_cleanup,
    sona_training_execution_inventory_sha256,
)
from .export import SonaOnnxExport
from .model import SonaLiteConfig
from .publication import publish_owned_sona_checkpoint
from .root_inventory import SonaTrainingRootInventory, inspect_sona_training_root
from .trainer import SonaTrainingConfig


@dataclass(frozen=True, slots=True)
class SonaControlledPublication:
    run_id: str
    execution_id: str
    publication_operation_id: str
    checkpoint_manifest_sha256: str
    artifact_sha256: str
    model_manifest_sha256: str
    optimizer_steps: int
    device_type: str


def _relative(value: Path, name: str) -> Path:
    if (
        value.is_absolute()
        or not value.parts
        or any(part in {"", ".", ".."} for part in value.parts)
        or value.as_posix() != str(value).replace("\\", "/")
    ):
        raise ValueError(f"unsafe controlled training {name}")
    return value


def _absolute(value: Path, name: str) -> Path:
    if not value.is_absolute() or str(value) != os.path.abspath(value):
        raise ValueError(f"unsafe controlled training {name}")
    return value


def _identity(sessions: sessionmaker[Session]) -> ServerInstanceRow:
    with sessions.begin() as session:
        policy = internal_io_policy(session)
        assert policy.server_instance_id is not None
        identity = session.get(ServerInstanceRow, policy.server_instance_id)
        if identity is None or identity.identity_epoch != policy.identity_epoch:
            raise TrainingWorkError("training_identity_unavailable")
        return identity


def _result(
    run_id: UUID,
    execution_id: UUID,
    publication_operation_id: UUID,
    trained: SonaControlledTrainingResult,
    exported: SonaOnnxExport,
) -> SonaControlledPublication:
    return SonaControlledPublication(
        str(run_id),
        str(execution_id),
        str(publication_operation_id),
        trained.checkpoint_manifest_sha256,
        exported.artifact_sha256,
        exported.model_manifest_sha256,
        trained.optimizer_steps,
        trained.device_type,
    )


def _replay_result(
    run_id: UUID,
    execution_id: UUID,
    publication_operation_id: UUID,
    row: TrainingExecutionRow,
    exported: SonaOnnxExport,
) -> SonaControlledPublication:
    if (
        row.checkpoint_manifest_sha256 is None
        or row.checkpoint_optimizer_steps is None
        or row.checkpoint_device_type is None
    ):
        raise TrainingWorkError("training_execution_unconfirmed")
    return SonaControlledPublication(
        str(run_id),
        str(execution_id),
        str(publication_operation_id),
        row.checkpoint_manifest_sha256.hex(),
        exported.artifact_sha256,
        exported.model_manifest_sha256,
        row.checkpoint_optimizer_steps,
        row.checkpoint_device_type,
    )


def _validate_replay_request(
    row: TrainingExecutionRow,
    *,
    run_id: UUID,
    input_root: Path,
    dataset_relative: Path,
    output_root: Path,
    maximum_output_bytes: int,
    model_config: SonaLiteConfig,
    training_config: SonaTrainingConfig,
    publication_operation_id: UUID,
    tokenizer_relative: Path,
    artifact_name: str,
) -> str:
    inventory = SonaTrainingRootInventory(
        row.input_inventory_sha256.hex(),
        row.input_bytes,
        0,
        row.input_root_device,
        row.input_root_inode,
    )
    expected = sona_training_execution_inventory_sha256(
        execution_id=row.execution_id,
        run_id=run_id,
        input_root=input_root,
        dataset_relative=dataset_relative,
        output_root=output_root,
        input_inventory=inventory,
        maximum_output_bytes=maximum_output_bytes,
        model_config=model_config,
        training_config=training_config,
        publication_operation_id=publication_operation_id,
        tokenizer_relative=tokenizer_relative,
        artifact_name=artifact_name,
    )
    if (
        row.run_id != run_id
        or row.input_root != str(input_root)
        or row.output_root != str(output_root)
        or row.maximum_output_bytes != maximum_output_bytes
        or row.root_inventory_sha256.hex() != expected
    ):
        raise TrainingWorkError("training_execution_conflict")
    return expected


def _ticket_from_row(row: TrainingExecutionRow) -> TrainingExecutionTicket:
    return TrainingExecutionTicket(
        row.execution_id,
        row.run_id,
        row.input_root,
        row.output_root,
        row.input_root_device,
        row.input_root_inode,
        row.input_scope_device,
        row.input_scope_inode,
        row.output_scope_device,
        row.output_scope_inode,
        Sha256Digest(row.input_inventory_sha256),
        Sha256Digest(row.root_inventory_sha256),
        row.input_bytes,
        row.maximum_output_bytes,
    )


def run_controlled_training_publication(
    engine: Engine,
    consent_ledger: FilesystemTrainingConsentLedger,
    *,
    run_id: UUID,
    execution_id: UUID,
    publication_operation_id: UUID,
    participants: Mapping[UUID, int],
    input_root: Path,
    dataset_relative: Path,
    output_root: Path,
    tokenizer_relative: Path,
    artifact_name: str,
    lineage_key_id: str,
    lineage_key: bytes,
    maximum_output_bytes: int,
    model_config: SonaLiteConfig,
    training_config: SonaTrainingConfig,
    tree_factory: ProcessTreeFactory,
) -> SonaControlledPublication:
    """Register, admit, retain, train and publish before releasing global capacity."""

    input_root = _absolute(input_root, "input root")
    output_root = _absolute(output_root, "output root")
    dataset_relative = _relative(dataset_relative, "dataset path")
    tokenizer_relative = _relative(tokenizer_relative, "tokenizer path")
    if (
        input_root.name != str(execution_id)
        or not 1 <= len(participants) <= 4096
        or not 32 <= len(lineage_key) <= 1024
        or not 1 <= len(lineage_key_id) <= 128
        or type(maximum_output_bytes) is not int
        or not 1 <= maximum_output_bytes <= 2**63 - 1
        or Path(artifact_name).name != artifact_name
        or not artifact_name.endswith(".onnx")
    ):
        raise ValueError("invalid controlled training production request")
    input_scope = input_root.parent
    storage = SonaTrainingStorageScopes(input_scope, output_root)
    dataset = input_root / dataset_relative
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    identity = _identity(sessions)
    work = TrainingWorkService(
        sessions,
        server_instance_id=identity.server_instance_id,
        identity_epoch=identity.identity_epoch,
        lineage_key_id=lineage_key_id,
        lineage_key=lineage_key,
        consent_ledger=consent_ledger,
    )
    repository = PostgresTrainingExecutionRepository(sessions, work)
    with sessions.begin() as session:
        existing_run = session.get(TrainingRunRow, run_id)
        existing_execution = session.get(TrainingExecutionRow, execution_id)
    if existing_execution is not None:
        if existing_execution.state != "CLOSED" or existing_run is None:
            raise TrainingWorkError("training_execution_reconciliation_required")
        execution_inventory = _validate_replay_request(
            existing_execution,
            run_id=run_id,
            input_root=input_root,
            dataset_relative=dataset_relative,
            output_root=output_root,
            maximum_output_bytes=maximum_output_bytes,
            model_config=model_config,
            training_config=training_config,
            publication_operation_id=publication_operation_id,
            tokenizer_relative=tokenizer_relative,
            artifact_name=artifact_name,
        )
        ticket = _ticket_from_row(existing_execution)
        storage.validate(ticket)
        plan = repository.cleanup_plan(ticket)
        if (
            plan is None
            or plan.retain_checkpoint is not True
            or plan.publication_seal_sha256 is None
        ):
            raise TrainingWorkError("training_execution_unconfirmed")
        verified = finalize_checkpoint_cleanup(plan)
        if (
            verified is None
            or verified.weights_sha256 != plan.checkpoint_weights_sha256
            or verified.optimizer_steps != plan.checkpoint_optimizer_steps
            or verified.device_type != plan.checkpoint_device_type
        ):
            raise TrainingWorkError("training_execution_unconfirmed")
        exported = publish_owned_sona_checkpoint(
            output_root / str(execution_id),
            authority=work,
            expected_execution_id=execution_id,
            expected_run_id=run_id,
            expected_operation_id=publication_operation_id,
            expected_execution_inventory_sha256=execution_inventory,
            expected_tokenizer_relative=tokenizer_relative.as_posix(),
            expected_artifact_name=artifact_name,
            expected_publication_seal_sha256=plan.publication_seal_sha256,
        )
        return _replay_result(
            run_id,
            execution_id,
            publication_operation_id,
            existing_execution,
            exported,
        )
    if existing_run is not None and existing_run.phase == "PUBLISHED":
        raise TrainingWorkError("training_execution_unconfirmed")

    header = load_sona_dataset_header(dataset)
    expected_tokens = tuple(
        sorted(hmac.new(lineage_key, owner.bytes, sha256).hexdigest() for owner in participants)
    )
    if (
        header.source_kind != SONA_SOURCE_KIND_OWNER_APPROVED
        or header.owner_lineage_key_id != lineage_key_id
        or header.owner_lineage_tokens != expected_tokens
    ):
        raise TrainingWorkError("training_input_binding_mismatch")

    registered = work.register(
        run_id,
        source_sha256=header.source_manifest_sha256,
        participants=participants,
    )
    if registered.phase == "PREPARING":
        registered = work.ready(run_id, header.manifest_sha256)
    if registered.phase not in {"READY", "RUNNING"} or (
        registered.dataset_sha256 is not None
        and registered.dataset_sha256 != header.manifest_sha256
    ):
        raise TrainingWorkError("training_phase_conflict")

    inventory = inspect_sona_training_root(input_root)
    input_scope_identity = input_scope.stat(follow_symlinks=False)
    output_scope_identity = output_root.stat(follow_symlinks=False)
    execution_inventory = sona_training_execution_inventory_sha256(
        execution_id=execution_id,
        run_id=run_id,
        input_root=input_root,
        dataset_relative=dataset_relative,
        output_root=output_root,
        input_inventory=inventory,
        maximum_output_bytes=maximum_output_bytes,
        model_config=model_config,
        training_config=training_config,
        publication_operation_id=publication_operation_id,
        tokenizer_relative=tokenizer_relative,
        artifact_name=artifact_name,
    )
    ticket = TrainingExecutionTicket(
        execution_id,
        run_id,
        str(input_root),
        str(output_root),
        inventory.root_device,
        inventory.root_inode,
        str(input_scope_identity.st_dev),
        str(input_scope_identity.st_ino),
        str(output_scope_identity.st_dev),
        str(output_scope_identity.st_ino),
        Sha256Digest(bytes.fromhex(inventory.inventory_sha256)),
        Sha256Digest(bytes.fromhex(execution_inventory)),
        inventory.input_bytes,
        maximum_output_bytes,
    )
    command = SonaControlledTrainingCommand(
        execution_id,
        run_id,
        input_root,
        dataset_relative,
        output_root,
        inventory.inventory_sha256,
        execution_inventory,
        inventory.input_bytes,
        maximum_output_bytes,
        model_config,
        training_config,
        publication_operation_id,
        tokenizer_relative,
        artifact_name,
    )
    coordinator = SonaTrainingProcessCoordinator(
        repository,
        tree_factory=tree_factory,
        storage_scopes=storage,
        maximum=1,
    )
    published: list[SonaOnnxExport] = []

    def publish_before_cleanup(checkpoint: Path, trained: SonaControlledTrainingResult) -> None:
        if trained.publication_seal_sha256 is None:
            raise TrainingWorkError("training_publication_seal_missing")
        exported = publish_owned_sona_checkpoint(
            checkpoint,
            authority=work,
            expected_execution_id=execution_id,
            expected_run_id=run_id,
            expected_operation_id=publication_operation_id,
            expected_execution_inventory_sha256=execution_inventory,
            expected_tokenizer_relative=tokenizer_relative.as_posix(),
            expected_artifact_name=artifact_name,
            expected_publication_seal_sha256=trained.publication_seal_sha256,
        )
        if exported.checkpoint_manifest_sha256 != trained.checkpoint_manifest_sha256:
            raise TrainingWorkError("training_publication_binding_mismatch")
        published.append(exported)

    trained = coordinator.run(
        ticket,
        command,
        PostgresSonaTrainingAuthority(work, run_id),
        before_cleanup=publish_before_cleanup,
    )
    if len(published) != 1:
        raise TrainingWorkError("training_publication_unavailable")
    return _result(run_id, execution_id, publication_operation_id, trained, published[0])


__all__ = ("SonaControlledPublication", "run_controlled_training_publication")
