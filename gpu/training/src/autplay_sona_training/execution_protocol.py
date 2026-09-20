"""Strict private pipe documents for one retained Sona training execution."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import UUID

import rfc8785
from autplay.domain.recommendations import JsonValue
from autplay.domain.training_execution import (
    TrainingExecutionCleanupPlan,
    TrainingExecutionTicket,
)
from autplay.domain.training_work import TrainingInputProvenance

from .authority import SonaTrainingInputBinding
from .model import SonaLiteConfig
from .root_inventory import SonaTrainingRootInventory, inspect_sona_training_root
from .trainer import DevicePreference, SonaTrainingConfig, SonaTrainingResult

MAX_CHECKPOINT_ENTRIES = 8_192
MAX_CHECKPOINT_MANIFEST_BYTES = 1_048_576
MAX_CHECKPOINT_WEIGHT_COUNT = 4_096
MAX_STORAGE_SCOPE_ENTRIES = 100_000


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"invalid controlled training {name}")
    return value


def _uuid(value: object, name: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"invalid controlled training {name}")
    result = UUID(value)
    if str(result) != value:
        raise ValueError(f"noncanonical controlled training {name}")
    return result


def _int(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"invalid controlled training {name}")
    return value


def _float(value: object, name: str) -> float:
    if type(value) is not float:
        raise ValueError(f"invalid controlled training {name}")
    return value


def _device(value: object) -> DevicePreference:
    if value not in {"auto", "cpu", "cuda"}:
        raise ValueError("invalid controlled training device")
    return value


def _absolute(value: object, name: str) -> Path:
    if not isinstance(value, str):
        raise ValueError(f"invalid controlled training {name}")
    result = Path(value)
    if not result.is_absolute() or str(result) != os.path.abspath(result):
        raise ValueError(f"unsafe controlled training {name}")
    return result


def _relative(value: object, name: str) -> Path:
    if not isinstance(value, str):
        raise ValueError(f"invalid controlled training {name}")
    result = Path(value)
    if (
        result.is_absolute()
        or not result.parts
        or any(part in {"", ".", ".."} for part in result.parts)
        or result.as_posix() != value
    ):
        raise ValueError(f"unsafe controlled training {name}")
    return result


def _roots_are_disjoint(first: Path, second: Path) -> bool:
    try:
        common = os.path.commonpath((str(first), str(second)))
    except ValueError:
        return True
    normalized = os.path.normcase(common)
    return normalized not in {os.path.normcase(str(first)), os.path.normcase(str(second))}


def _directory_identity(path: Path) -> dict[str, JsonValue]:
    metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or getattr(metadata, "st_file_attributes", 0) & 0x400
    ):
        raise ValueError("controlled training output root is unsafe")
    return {
        "path": str(path),
        "device": str(metadata.st_dev),
        "inode": str(metadata.st_ino),
        "mode": str(stat.S_IMODE(metadata.st_mode)),
    }


def sona_training_execution_inventory_sha256(
    *,
    execution_id: UUID,
    run_id: UUID,
    input_root: Path,
    dataset_relative: Path,
    output_root: Path,
    input_inventory: SonaTrainingRootInventory,
    maximum_output_bytes: int,
    model_config: SonaLiteConfig,
    training_config: SonaTrainingConfig,
    publication_operation_id: UUID | None = None,
    tokenizer_relative: Path | None = None,
    artifact_name: str | None = None,
) -> str:
    document: dict[str, JsonValue] = {
        "schema_version": 2 if publication_operation_id is not None else 1,
        "execution_id": str(execution_id),
        "run_id": str(run_id),
        "input_root": str(input_root),
        "dataset_relative": dataset_relative.as_posix(),
        "input_inventory_sha256": input_inventory.inventory_sha256,
        "input_bytes": str(input_inventory.input_bytes),
        "output_root": _directory_identity(output_root),
        "maximum_output_bytes": str(maximum_output_bytes),
        "model_config": cast(dict[str, JsonValue], asdict(model_config)),
        "training_config": cast(dict[str, JsonValue], asdict(training_config)),
    }
    if publication_operation_id is not None:
        if tokenizer_relative is None or artifact_name is None:
            raise ValueError("controlled training publication binding is incomplete")
        document["publication"] = {
            "operation_id": str(publication_operation_id),
            "tokenizer_relative": tokenizer_relative.as_posix(),
            "artifact_name": artifact_name,
        }
    return sha256(rfc8785.dumps(document)).hexdigest()


@dataclass(frozen=True, slots=True)
class SonaControlledTrainingCommand:
    execution_id: UUID
    run_id: UUID
    input_root: Path
    dataset_relative: Path
    output_root: Path
    input_inventory_sha256: str
    root_inventory_sha256: str
    input_bytes: int
    maximum_output_bytes: int
    model_config: SonaLiteConfig
    training_config: SonaTrainingConfig
    publication_operation_id: UUID | None = None
    tokenizer_relative: Path | None = None
    artifact_name: str | None = None

    def __post_init__(self) -> None:
        _digest(self.input_inventory_sha256, "input inventory")
        _digest(self.root_inventory_sha256, "execution inventory")
        if (
            not self.input_root.is_absolute()
            or not self.output_root.is_absolute()
            or not _roots_are_disjoint(self.input_root, self.output_root)
            or self.dataset_relative.is_absolute()
            or not self.dataset_relative.parts
            or any(part in {"", ".", ".."} for part in self.dataset_relative.parts)
            or type(self.input_bytes) is not int
            or type(self.maximum_output_bytes) is not int
            or not 1 <= self.input_bytes <= 2**63 - 1
            or not 1 <= self.maximum_output_bytes <= 2**63 - 1
        ):
            raise ValueError("invalid controlled training command")
        publication_values = (
            self.publication_operation_id,
            self.tokenizer_relative,
            self.artifact_name,
        )
        if all(value is None for value in publication_values):
            return
        if (
            any(value is None for value in publication_values)
            or not isinstance(self.tokenizer_relative, Path)
            or self.tokenizer_relative.is_absolute()
            or not self.tokenizer_relative.parts
            or any(part in {"", ".", ".."} for part in self.tokenizer_relative.parts)
            or self.tokenizer_relative.as_posix() != str(self.tokenizer_relative).replace("\\", "/")
            or not isinstance(self.artifact_name, str)
            or Path(self.artifact_name).name != self.artifact_name
            or not self.artifact_name.endswith(".onnx")
        ):
            raise ValueError("invalid controlled training publication binding")

    @property
    def checkpoint_name(self) -> str:
        return str(self.execution_id)

    @property
    def checkpoint_path(self) -> Path:
        return self.output_root / self.checkpoint_name

    @property
    def checkpoint_staging_path(self) -> Path:
        return self.output_root / f".{self.checkpoint_name}.staging"

    @property
    def publication_directory(self) -> Path:
        return self.checkpoint_path / "publication"

    @property
    def artifact_path(self) -> Path:
        if self.artifact_name is None:
            raise ValueError("controlled training has no publication artifact")
        return self.publication_directory / self.artifact_name

    def document(self) -> dict[str, object]:
        return {
            "version": 2,
            "execution_id": str(self.execution_id),
            "run_id": str(self.run_id),
            "input_root": str(self.input_root),
            "dataset_relative": self.dataset_relative.as_posix(),
            "output_root": str(self.output_root),
            "checkpoint_name": self.checkpoint_name,
            "input_inventory_sha256": self.input_inventory_sha256,
            "root_inventory_sha256": self.root_inventory_sha256,
            "input_bytes": self.input_bytes,
            "maximum_output_bytes": self.maximum_output_bytes,
            "model_config": asdict(self.model_config),
            "training_config": asdict(self.training_config),
            "publication": (
                None
                if self.publication_operation_id is None
                else {
                    "operation_id": str(self.publication_operation_id),
                    "tokenizer_relative": self.tokenizer_relative.as_posix()
                    if self.tokenizer_relative is not None
                    else None,
                    "artifact_name": self.artifact_name,
                }
            ),
        }

    @classmethod
    def parse(cls, value: object) -> SonaControlledTrainingCommand:
        if not isinstance(value, dict) or set(value) != {
            "version",
            "execution_id",
            "run_id",
            "input_root",
            "dataset_relative",
            "output_root",
            "checkpoint_name",
            "input_inventory_sha256",
            "root_inventory_sha256",
            "input_bytes",
            "maximum_output_bytes",
            "model_config",
            "training_config",
            "publication",
        }:
            raise ValueError("invalid controlled training command document")
        if type(value["version"]) is not int or value["version"] != 2:
            raise ValueError("unsupported controlled training command")
        execution_id = _uuid(value["execution_id"], "execution identity")
        if value["checkpoint_name"] != str(execution_id):
            raise ValueError("invalid controlled training checkpoint identity")
        model = value["model_config"]
        training = value["training_config"]
        if not isinstance(model, dict) or set(model) != {
            "codebook_size",
            "model_dimensions",
            "encoder_layers",
            "action_vocabulary_size",
            "origin_vocabulary_size",
            "age_vocabulary_size",
            "seed_vocabulary_size",
        }:
            raise ValueError("invalid controlled training model config")
        if not isinstance(training, dict) or set(training) != {
            "epochs",
            "batch_size",
            "learning_rate",
            "weight_decay",
            "gradient_clip_norm",
            "seed",
            "device",
        }:
            raise ValueError("invalid controlled training optimizer config")
        publication = value["publication"]
        publication_operation_id: UUID | None = None
        tokenizer_relative: Path | None = None
        artifact_name: str | None = None
        if publication is not None:
            if not isinstance(publication, dict) or set(publication) != {
                "operation_id",
                "tokenizer_relative",
                "artifact_name",
            }:
                raise ValueError("invalid controlled training publication document")
            publication_operation_id = _uuid(publication["operation_id"], "publication operation")
            tokenizer_relative = _relative(publication["tokenizer_relative"], "tokenizer path")
            artifact = publication["artifact_name"]
            if not isinstance(artifact, str):
                raise ValueError("invalid controlled training artifact name")
            artifact_name = artifact
        return cls(
            execution_id,
            _uuid(value["run_id"], "run identity"),
            _absolute(value["input_root"], "input root"),
            _relative(value["dataset_relative"], "dataset path"),
            _absolute(value["output_root"], "output root"),
            _digest(value["input_inventory_sha256"], "input inventory"),
            _digest(value["root_inventory_sha256"], "execution inventory"),
            _int(value["input_bytes"], "input bytes"),
            _int(value["maximum_output_bytes"], "maximum output bytes"),
            SonaLiteConfig(
                codebook_size=_int(model["codebook_size"], "codebook size"),
                model_dimensions=_int(model["model_dimensions"], "model dimensions"),
                encoder_layers=_int(model["encoder_layers"], "encoder layers"),
                action_vocabulary_size=_int(
                    model["action_vocabulary_size"], "action vocabulary size"
                ),
                origin_vocabulary_size=_int(
                    model["origin_vocabulary_size"], "origin vocabulary size"
                ),
                age_vocabulary_size=_int(model["age_vocabulary_size"], "age vocabulary size"),
                seed_vocabulary_size=_int(model["seed_vocabulary_size"], "seed vocabulary size"),
            ),
            SonaTrainingConfig(
                epochs=_int(training["epochs"], "epoch count"),
                batch_size=_int(training["batch_size"], "batch size"),
                learning_rate=_float(training["learning_rate"], "learning rate"),
                weight_decay=_float(training["weight_decay"], "weight decay"),
                gradient_clip_norm=_float(training["gradient_clip_norm"], "gradient clip norm"),
                seed=_int(training["seed"], "seed"),
                device=_device(training["device"]),
            ),
            publication_operation_id,
            tokenizer_relative,
            artifact_name,
        )


@dataclass(frozen=True, slots=True)
class SonaControlledTrainingResult:
    checkpoint_manifest_sha256: str
    weights_sha256: str
    dataset_manifest_sha256: str
    optimizer_steps: int
    device_type: str
    publication_seal_sha256: str | None = None

    @classmethod
    def from_training(
        cls,
        result: SonaTrainingResult,
        *,
        publication_seal_sha256: str | None = None,
    ) -> SonaControlledTrainingResult:
        return cls(
            result.checkpoint_manifest_sha256,
            result.weights_sha256,
            result.dataset_manifest_sha256,
            result.optimizer_steps,
            result.device_type,
            publication_seal_sha256,
        )

    def document(self) -> dict[str, object]:
        return {"version": 2, **asdict(self)}

    @classmethod
    def parse(cls, value: object) -> SonaControlledTrainingResult:
        if not isinstance(value, dict):
            raise ValueError("invalid controlled training result")
        version = value.get("version")
        required = {
            "version",
            "checkpoint_manifest_sha256",
            "weights_sha256",
            "dataset_manifest_sha256",
            "optimizer_steps",
            "device_type",
        }
        if version == 2:
            required.add("publication_seal_sha256")
        if set(value) != required:
            raise ValueError("invalid controlled training result")
        if type(version) is not int or version not in {1, 2}:
            raise ValueError("unsupported controlled training result")
        steps, device = value["optimizer_steps"], value["device_type"]
        if type(steps) is not int or steps < 1 or device not in {"cpu", "cuda"}:
            raise ValueError("invalid controlled training result values")
        return cls(
            _digest(value["checkpoint_manifest_sha256"], "checkpoint manifest"),
            _digest(value["weights_sha256"], "weights"),
            _digest(value["dataset_manifest_sha256"], "dataset manifest"),
            steps,
            cast(str, device),
            (
                None
                if version == 1 or value["publication_seal_sha256"] is None
                else _digest(value["publication_seal_sha256"], "publication seal")
            ),
        )


def authorize_request(binding: SonaTrainingInputBinding) -> dict[str, object]:
    return {
        "version": 1,
        "source_sha256": binding.source_sha256,
        "dataset_sha256": binding.dataset_sha256,
        "lineage_key_id": binding.lineage_key_id,
        "owner_tokens": list(binding.owner_tokens),
    }


def parse_authorize_request(value: object) -> SonaTrainingInputBinding:
    if not isinstance(value, dict) or set(value) != {
        "version",
        "source_sha256",
        "dataset_sha256",
        "lineage_key_id",
        "owner_tokens",
    }:
        raise ValueError("invalid controlled training authorization")
    tokens = value["owner_tokens"]
    if (
        type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value["lineage_key_id"], str)
        or not isinstance(tokens, list)
        or not 1 <= len(tokens) <= 4096
        or any(not isinstance(token, str) for token in tokens)
    ):
        raise ValueError("invalid controlled training authorization values")
    return SonaTrainingInputBinding(
        _digest(value["source_sha256"], "source"),
        _digest(value["dataset_sha256"], "dataset"),
        value["lineage_key_id"],
        tuple(cast(list[str], tokens)),
    )


def check_request() -> dict[str, object]:
    return {"version": 1}


def parse_check_request(value: object) -> None:
    if value != {"version": 1}:
        raise ValueError("invalid controlled training current-authority check")


def seal_request(provenance: TrainingInputProvenance, manifest_sha256: str) -> dict[str, object]:
    return {
        "version": 1,
        "provenance": provenance.document(),
        "manifest_sha256": _digest(manifest_sha256, "checkpoint manifest"),
    }


def parse_seal_request(value: object) -> tuple[TrainingInputProvenance, str]:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "version",
            "provenance",
            "manifest_sha256",
        }
        or value["version"] != 1
    ):
        raise ValueError("invalid controlled training seal")
    return (
        TrainingInputProvenance.parse(value["provenance"]),
        _digest(value["manifest_sha256"], "checkpoint manifest"),
    )


def inventory_matches(
    command: SonaControlledTrainingCommand, inventory: SonaTrainingRootInventory
) -> bool:
    return (
        inventory.inventory_sha256 == command.input_inventory_sha256
        and inventory.input_bytes == command.input_bytes
        and sona_training_execution_inventory_sha256(
            execution_id=command.execution_id,
            run_id=command.run_id,
            input_root=command.input_root,
            dataset_relative=command.dataset_relative,
            output_root=command.output_root,
            input_inventory=inventory,
            maximum_output_bytes=command.maximum_output_bytes,
            model_config=command.model_config,
            training_config=command.training_config,
            publication_operation_id=command.publication_operation_id,
            tokenizer_relative=command.tokenizer_relative,
            artifact_name=command.artifact_name,
        )
        == command.root_inventory_sha256
    )


def checkpoint_output_bytes(checkpoint: Path) -> int:
    total = 0
    entries = 0
    pending = [checkpoint]
    while pending:
        path = pending.pop()
        entries += 1
        if entries > MAX_CHECKPOINT_ENTRIES:
            raise ValueError("controlled training output has too many entries")
        metadata = path.stat(follow_symlinks=False)
        if path.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("controlled training output contains a link")
        if stat.S_ISDIR(metadata.st_mode):
            pending.extend(path.iterdir())
        elif stat.S_ISREG(metadata.st_mode):
            total += metadata.st_size
            if total > 2**63 - 1:
                raise ValueError("controlled training output exceeds the byte bound")
        else:
            raise ValueError("controlled training output contains a non-file entry")
    return total


@dataclass(frozen=True, slots=True)
class SonaCheckpointReservation:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class SonaCheckpointVerification:
    weights_sha256: str
    optimizer_steps: int
    device_type: str


def _checkpoint_identity(path: Path) -> SonaCheckpointReservation:
    metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or getattr(metadata, "st_file_attributes", 0) & 0x400
    ):
        raise ValueError("controlled training checkpoint path is unsafe")
    return SonaCheckpointReservation(metadata.st_dev, metadata.st_ino)


def _require_scope_identity(path: Path, device: str, inode: str) -> None:
    identity = _checkpoint_identity(path)
    if (str(identity.device), str(identity.inode)) != (device, inode):
        raise ValueError("controlled training storage scope changed")


def validate_training_storage_roots(ticket: TrainingExecutionTicket) -> None:
    input_root, output_root = Path(ticket.input_root), Path(ticket.output_root)
    if input_root.parent == output_root or output_root in input_root.parents:
        raise ValueError("controlled training storage scopes overlap")
    _require_scope_identity(
        input_root.parent,
        ticket.input_scope_device,
        ticket.input_scope_inode,
    )
    _require_scope_identity(
        output_root,
        ticket.output_scope_device,
        ticket.output_scope_inode,
    )


def _require_directory_identity_absent(
    scope: Path,
    reservation: SonaCheckpointReservation,
) -> None:
    """Search the bounded exclusive scope so a renamed owned directory cannot escape cleanup."""

    original_scope = _checkpoint_identity(scope)
    pending, observed = [scope], 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                observed += 1
                if observed > MAX_STORAGE_SCOPE_ENTRIES:
                    raise ValueError("controlled training storage scope is too large")
                # CPython's Windows DirEntry.stat() can report zero st_dev/st_ino even
                # though Path.stat() exposes the stable file identity used at admission.
                # Re-open the directory entry by path so the comparison is meaningful on
                # every supported platform.
                entry_path = Path(entry.path)
                metadata = entry_path.stat(follow_symlinks=False)
                if entry_path.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
                    raise ValueError("controlled training storage scope contains a link")
                if stat.S_ISDIR(metadata.st_mode):
                    if (metadata.st_dev, metadata.st_ino) == (
                        reservation.device,
                        reservation.inode,
                    ):
                        raise ValueError("controlled training owned directory escaped cleanup")
                    pending.append(entry_path)
                elif not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("controlled training storage scope contains an unsafe entry")
    if _checkpoint_identity(scope) != original_scope:
        raise ValueError("controlled training storage scope changed")


def _path_absent(path: Path) -> bool:
    try:
        path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return True
    return False


def reserve_checkpoint_output(
    command: SonaControlledTrainingCommand,
    inventory: SonaTrainingRootInventory,
) -> SonaCheckpointReservation:
    """Reserve one exact empty staging inode after this child wins durable RUNNING."""

    if not inventory_matches(command, inventory):
        raise ValueError("controlled training root inventory mismatch")
    _directory_identity(command.output_root)
    checkpoint_cleanup = command.checkpoint_path.with_name(
        f"{command.checkpoint_path.name}.cleanup"
    )
    staging_cleanup = command.checkpoint_staging_path.with_name(
        f"{command.checkpoint_staging_path.name}.cleanup"
    )
    if any(
        not _path_absent(path)
        for path in (
            command.checkpoint_path,
            command.checkpoint_staging_path,
            checkpoint_cleanup,
            staging_cleanup,
        )
    ):
        raise FileExistsError("controlled training checkpoint target exists")
    command.checkpoint_staging_path.mkdir(mode=0o700)
    try:
        return _checkpoint_identity(command.checkpoint_staging_path)
    except BaseException:
        command.checkpoint_staging_path.rmdir()
        raise


def finalize_checkpoint_output(
    command: SonaControlledTrainingCommand,
    reservation: SonaCheckpointReservation,
    *,
    retain: bool,
    manifest_sha256: str | None,
    publication_seal_sha256: str | None = None,
) -> SonaCheckpointVerification | None:
    """Verify a successful rename or erase this exact inode before SQL exit ack."""

    return _finalize_checkpoint_paths(
        staging=command.checkpoint_staging_path,
        checkpoint=command.checkpoint_path,
        reservation=reservation,
        maximum_output_bytes=command.maximum_output_bytes,
        retain=retain,
        manifest_sha256=manifest_sha256,
        scope=command.output_root,
        expected_execution_id=command.execution_id,
        expected_run_id=command.run_id,
        expected_execution_inventory_sha256=command.root_inventory_sha256,
        expected_publication_operation_id=command.publication_operation_id,
        expected_tokenizer_relative=(
            None if command.tokenizer_relative is None else command.tokenizer_relative.as_posix()
        ),
        expected_artifact_name=command.artifact_name,
        expected_publication_seal_sha256=publication_seal_sha256,
    )


def finalize_checkpoint_cleanup(
    plan: TrainingExecutionCleanupPlan,
) -> SonaCheckpointVerification | None:
    if plan.retain_checkpoint is None:
        raise ValueError("controlled training checkpoint cleanup is undecided")
    validate_training_storage_roots(plan.ticket)
    if plan.checkpoint_device is None or plan.checkpoint_inode is None:
        return None
    output = Path(plan.ticket.output_root)
    name = str(plan.ticket.execution_id)
    return _finalize_checkpoint_paths(
        staging=output / f".{name}.staging",
        checkpoint=output / name,
        reservation=SonaCheckpointReservation(
            int(plan.checkpoint_device), int(plan.checkpoint_inode)
        ),
        maximum_output_bytes=plan.ticket.maximum_output_bytes,
        retain=plan.retain_checkpoint,
        manifest_sha256=(plan.checkpoint_manifest_sha256 if plan.retain_checkpoint else None),
        scope=output,
        expected_execution_id=plan.ticket.execution_id,
        expected_run_id=plan.ticket.run_id,
        expected_execution_inventory_sha256=plan.ticket.root_inventory_sha256.hex,
        discover_publication=True,
        expected_publication_seal_sha256=plan.publication_seal_sha256,
    )


def _finalize_checkpoint_paths(
    *,
    staging: Path,
    checkpoint: Path,
    reservation: SonaCheckpointReservation,
    maximum_output_bytes: int,
    retain: bool,
    manifest_sha256: str | None,
    scope: Path,
    expected_execution_id: UUID,
    expected_run_id: UUID,
    expected_execution_inventory_sha256: str,
    expected_publication_operation_id: UUID | None = None,
    expected_tokenizer_relative: str | None = None,
    expected_artifact_name: str | None = None,
    discover_publication: bool = False,
    expected_publication_seal_sha256: str | None = None,
) -> SonaCheckpointVerification | None:
    staging_cleanup = staging.with_name(f"{staging.name}.cleanup")
    checkpoint_cleanup = checkpoint.with_name(f"{checkpoint.name}.cleanup")
    all_paths = (staging, checkpoint, staging_cleanup, checkpoint_cleanup)
    present = [path for path in all_paths if not _path_absent(path)]
    if any(_checkpoint_identity(path) != reservation for path in present):
        raise ValueError("controlled training checkpoint identity changed")
    if retain:
        if present != [checkpoint] or manifest_sha256 is None:
            raise ValueError("controlled training checkpoint publication is incomplete")
        if checkpoint_output_bytes(checkpoint) > maximum_output_bytes:
            raise ValueError("controlled training output exceeds its admitted bound")
        envelope = cast(
            JsonValue,
            json.loads(
                _bounded_file_read(checkpoint / "manifest.json", MAX_CHECKPOINT_MANIFEST_BYTES),
                object_pairs_hook=_unique_json_object,
            ),
        )
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"manifest", "manifest_sha256"}
            or envelope.get("manifest_sha256") != manifest_sha256
            or sha256(rfc8785.dumps(envelope.get("manifest"))).hexdigest() != manifest_sha256
        ):
            raise ValueError("controlled training checkpoint manifest changed")
        manifest = envelope["manifest"]
        if not isinstance(manifest, dict):
            raise ValueError("controlled training checkpoint manifest is invalid")
        weights = manifest.get("weights")
        aggregate = manifest.get("weights_sha256")
        if (
            not isinstance(weights, list)
            or not 1 <= len(weights) <= MAX_CHECKPOINT_WEIGHT_COUNT
            or not isinstance(aggregate, str)
            or re.fullmatch(r"[0-9a-f]{64}", aggregate) is None
        ):
            raise ValueError("controlled training checkpoint weights are invalid")
        optimizer_steps = manifest.get("optimizer_steps")
        device_type = manifest.get("training_device_type")
        if (
            type(optimizer_steps) is not int
            or optimizer_steps < 1
            or device_type not in {"cpu", "cuda"}
        ):
            raise ValueError("controlled training checkpoint result is invalid")
        expected = {Path("manifest.json")}
        names: set[str] = set()
        files: set[str] = set()
        combined = sha256()
        for value in weights:
            if not isinstance(value, dict) or set(value) != {
                "name",
                "file",
                "sha256",
                "dtype",
                "shape",
            }:
                raise ValueError("controlled training checkpoint weight entry is invalid")
            name, filename = value["name"], value["file"]
            digest, dtype, shape = value["sha256"], value["dtype"], value["shape"]
            if (
                not isinstance(name, str)
                or not name
                or name in names
                or not isinstance(filename, str)
                or Path(filename).name != filename
                or filename in files
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or not isinstance(dtype, str)
                or not dtype
                or not isinstance(shape, list)
                or any(type(dimension) is not int or dimension < 0 for dimension in shape)
            ):
                raise ValueError("controlled training checkpoint weight entry is unsafe")
            path = checkpoint / "weights" / filename
            if _bounded_file_sha256(path, maximum_output_bytes) != digest:
                raise ValueError("controlled training checkpoint weight changed")
            names.add(name)
            files.add(filename)
            expected.add(Path("weights") / filename)
            combined.update(name.encode("utf-8"))
            combined.update(bytes.fromhex(digest))
        expected_directories = {Path("weights")}
        publication_values = (
            expected_publication_operation_id,
            expected_tokenizer_relative,
            expected_artifact_name,
        )
        if any(value is not None for value in publication_values):
            if not all(value is not None for value in publication_values):
                raise ValueError("controlled training publication binding is incomplete")
            from .publication import read_owned_sona_publication

            owned = read_owned_sona_publication(
                checkpoint,
                expected_execution_id=expected_execution_id,
                expected_run_id=expected_run_id,
                expected_operation_id=expected_publication_operation_id,
                expected_execution_inventory_sha256=expected_execution_inventory_sha256,
                expected_tokenizer_relative=expected_tokenizer_relative,
                expected_artifact_name=expected_artifact_name,
                expected_checkpoint_manifest_sha256=manifest_sha256,
            )
            from .publication import sona_owned_publication_seal_sha256

            if expected_publication_seal_sha256 is None or sona_owned_publication_seal_sha256(
                owned
            ) != _digest(expected_publication_seal_sha256, "publication seal"):
                raise ValueError("controlled training publication seal changed")
            expected.update(owned.relative_files)
            expected_directories.add(Path("publication"))
        elif discover_publication and not _path_absent(checkpoint / "publication"):
            from .publication import read_owned_sona_publication

            owned = read_owned_sona_publication(
                checkpoint,
                expected_execution_id=expected_execution_id,
                expected_run_id=expected_run_id,
                expected_execution_inventory_sha256=expected_execution_inventory_sha256,
                expected_checkpoint_manifest_sha256=manifest_sha256,
            )
            from .publication import sona_owned_publication_seal_sha256

            if expected_publication_seal_sha256 is None or sona_owned_publication_seal_sha256(
                owned
            ) != _digest(expected_publication_seal_sha256, "publication seal"):
                raise ValueError("controlled training publication seal changed")
            expected.update(owned.relative_files)
            expected_directories.add(Path("publication"))
        elif expected_publication_seal_sha256 is not None:
            raise ValueError("controlled training publication seal has no candidate")
        actual, directories = _checkpoint_tree(checkpoint)
        if actual != expected or directories != expected_directories:
            raise ValueError("controlled training checkpoint file set changed")
        if combined.hexdigest() != aggregate:
            raise ValueError("controlled training checkpoint aggregate changed")
        return SonaCheckpointVerification(aggregate, optimizer_steps, device_type)
    for source, tombstone in (
        (staging, staging_cleanup),
        (checkpoint, checkpoint_cleanup),
    ):
        source_present, tombstone_present = (
            not _path_absent(source),
            not _path_absent(tombstone),
        )
        if source_present and tombstone_present:
            raise ValueError("controlled training checkpoint cleanup paths conflict")
        if source_present:
            os.replace(source, tombstone)
            tombstone_present = True
        if tombstone_present:
            if _checkpoint_identity(tombstone) != reservation:
                raise ValueError("controlled training checkpoint cleanup identity changed")
            shutil.rmtree(tombstone)
    if any(not _path_absent(path) for path in all_paths):
        raise OSError("controlled training checkpoint cleanup incomplete")
    _require_directory_identity_absent(scope, reservation)
    return None


def _checkpoint_tree(root: Path) -> tuple[set[Path], set[Path]]:
    files: set[Path] = set()
    directories: set[Path] = set()
    pending = [root]
    entries = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as scan:
            children = tuple(scan)
        for child in children:
            entries += 1
            if entries > MAX_CHECKPOINT_ENTRIES:
                raise ValueError("controlled training checkpoint exceeds its entry bound")
            path = Path(child.path)
            metadata = child.stat(follow_symlinks=False)
            if child.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise ValueError("controlled training checkpoint contains a link")
            relative = path.relative_to(root)
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(relative)
                pending.append(path)
            elif stat.S_ISREG(metadata.st_mode):
                files.add(relative)
            else:
                raise ValueError("controlled training checkpoint contains an unsafe entry")
    return files, directories


def _bounded_file_read(path: Path, maximum: int) -> bytes:
    metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or getattr(metadata, "st_file_attributes", 0) & 0x400
        or not 1 <= metadata.st_size <= maximum
    ):
        raise ValueError("controlled training checkpoint file is unsafe")
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    current = path.stat(follow_symlinks=False)
    if (
        len(payload) != metadata.st_size
        or len(payload) > maximum
        or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        != (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
    ):
        raise ValueError("controlled training checkpoint file changed")
    return payload


def _bounded_file_sha256(path: Path, maximum: int) -> str:
    metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or getattr(metadata, "st_file_attributes", 0) & 0x400
        or not 1 <= metadata.st_size <= maximum
    ):
        raise ValueError("controlled training checkpoint weight is unsafe")
    digest, observed = sha256(), 0
    with path.open("rb") as stream:
        while payload := stream.read(1024 * 1024):
            observed += len(payload)
            if observed > metadata.st_size or observed > maximum:
                raise ValueError("controlled training checkpoint weight changed")
            digest.update(payload)
    current = path.stat(follow_symlinks=False)
    if observed != metadata.st_size or (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    ) != (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns):
        raise ValueError("controlled training checkpoint weight changed")
    return digest.hexdigest()


def _unique_json_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("controlled training checkpoint contains duplicate keys")
        result[key] = value
    return result


def finalize_training_input(
    command: SonaControlledTrainingCommand,
    inventory: SonaTrainingRootInventory,
    ticket: TrainingExecutionTicket,
) -> str:
    """Erase only the unchanged, exact owner-input tree after exact trainer exit."""

    validate_training_storage_roots(ticket)
    root = command.input_root
    tombstone = root.parent / f".{command.execution_id}.input-cleanup"
    root_present, tombstone_present = not _path_absent(root), not _path_absent(tombstone)
    if root_present and tombstone_present:
        raise ValueError("controlled training input cleanup paths conflict")
    if root_present:
        if not inventory_matches(command, inspect_sona_training_root(root)):
            raise ValueError("controlled training input changed before cleanup")
        os.replace(root, tombstone)
        tombstone_present = True
    if tombstone_present:
        identity = _checkpoint_identity(tombstone)
        if (str(identity.device), str(identity.inode)) != (
            inventory.root_device,
            inventory.root_inode,
        ):
            raise ValueError("controlled training input cleanup identity changed")
        shutil.rmtree(tombstone)
    if not _path_absent(root) or not _path_absent(tombstone):
        raise OSError("controlled training input cleanup incomplete")
    _require_directory_identity_absent(
        root.parent,
        SonaCheckpointReservation(int(inventory.root_device), int(inventory.root_inode)),
    )
    evidence: dict[str, JsonValue] = {
        "schema_version": 1,
        "execution_id": str(command.execution_id),
        "run_id": str(command.run_id),
        "input_root": str(command.input_root),
        "input_inventory_sha256": inventory.inventory_sha256,
        "input_bytes": str(inventory.input_bytes),
        "result": "ABSENT",
    }
    return sha256(rfc8785.dumps(evidence)).hexdigest()


def finalize_training_input_cleanup(ticket: TrainingExecutionTicket) -> str:
    """Resume a durable STOPPING cleanup after rename, partial erase or lost reply."""

    validate_training_storage_roots(ticket)
    root = Path(ticket.input_root)
    tombstone = root.parent / f".{ticket.execution_id}.input-cleanup"
    root_present, tombstone_present = not _path_absent(root), not _path_absent(tombstone)
    if root_present and tombstone_present:
        raise ValueError("controlled training input cleanup paths conflict")
    if root_present:
        inventory = inspect_sona_training_root(root)
        if (
            inventory.inventory_sha256 != ticket.input_inventory_sha256.hex
            or inventory.input_bytes != ticket.input_bytes
            or inventory.root_device != ticket.input_root_device
            or inventory.root_inode != ticket.input_root_inode
        ):
            raise ValueError("controlled training input changed before cleanup")
        os.replace(root, tombstone)
        tombstone_present = True
    if tombstone_present:
        identity = _checkpoint_identity(tombstone)
        if (str(identity.device), str(identity.inode)) != (
            ticket.input_root_device,
            ticket.input_root_inode,
        ):
            raise ValueError("controlled training input cleanup identity changed")
        shutil.rmtree(tombstone)
    if not _path_absent(root) or not _path_absent(tombstone):
        raise OSError("controlled training input cleanup incomplete")
    _require_directory_identity_absent(
        root.parent,
        SonaCheckpointReservation(int(ticket.input_root_device), int(ticket.input_root_inode)),
    )
    evidence: dict[str, JsonValue] = {
        "schema_version": 1,
        "execution_id": str(ticket.execution_id),
        "run_id": str(ticket.run_id),
        "input_root": ticket.input_root,
        "input_inventory_sha256": ticket.input_inventory_sha256.hex,
        "input_bytes": str(ticket.input_bytes),
        "result": "ABSENT",
    }
    return sha256(rfc8785.dumps(evidence)).hexdigest()


__all__ = (
    "SonaCheckpointReservation",
    "SonaCheckpointVerification",
    "SonaControlledTrainingCommand",
    "SonaControlledTrainingResult",
    "authorize_request",
    "check_request",
    "checkpoint_output_bytes",
    "finalize_checkpoint_cleanup",
    "finalize_checkpoint_output",
    "finalize_training_input",
    "finalize_training_input_cleanup",
    "inventory_matches",
    "parse_authorize_request",
    "parse_check_request",
    "parse_seal_request",
    "reserve_checkpoint_output",
    "seal_request",
    "sona_training_execution_inventory_sha256",
    "validate_training_storage_roots",
)
