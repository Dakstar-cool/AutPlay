"""Verified checkpoint export with atomic current-registry publication and exact retry."""

from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import UUID

import rfc8785
from autplay.application.training_work import TrainingWorkError, TrainingWorkService
from autplay.domain.recommendations import JsonValue
from autplay.domain.training_work import TrainingInputProvenance

from .authority import SonaSharedTrainingAuthority
from .dataset import SONA_SOURCE_KIND_OWNER_APPROVED
from .export import SonaOnnxExport, SonaOnnxProvenance, export_sona_onnx
from .quality_bundle import (
    SonaQualityDatasetBundle,
    reverify_quality_approved_sona_dataset_bundle,
)
from .tokenizer import SONA_MAX_TOKENIZER_MANIFEST_BYTES, load_sona_tokenizer
from .trainer import (
    SONA_MAX_CHECKPOINT_MANIFEST_BYTES,
    load_quality_sona_checkpoint,
    load_sona_checkpoint,
)

SONA_OWNED_PUBLICATION_INTENT_MAX_BYTES = 16_384


@dataclass(frozen=True, slots=True)
class SonaOwnedPublication:
    execution_id: UUID
    run_id: UUID
    operation_id: UUID
    execution_inventory_sha256: str
    tokenizer_relative: str
    artifact_name: str
    checkpoint_manifest_sha256: str
    exported: SonaOnnxExport
    provenance: TrainingInputProvenance
    hashes: dict[str, str]

    @property
    def relative_files(self) -> frozenset[Path]:
        artifact = Path("publication") / self.artifact_name
        return frozenset(
            {
                artifact,
                artifact.with_suffix(f"{artifact.suffix}.manifest.json"),
                artifact.with_suffix(f"{artifact.suffix}.commit.json"),
                Path("publication") / "intent.json",
            }
        )


def export_and_publish_sona_checkpoint(
    checkpoint_directory: Path,
    tokenizer_directory: Path,
    output_path: Path,
    *,
    authority: TrainingWorkService,
    operation_id: UUID,
    expected_run_id: UUID | None = None,
    quality_bundle: SonaQualityDatasetBundle | None = None,
    before_registry_publish: Callable[[], None] | None = None,
) -> SonaOnnxExport:
    """Keep installed candidates inert until the exact five hashes commit in current PG.

    The caller must retain its independent byte/process execution permit. This library
    does not admit a standalone CLI, acquire capacity or claim writer exit/cleanup.
    """

    existing: tuple[SonaOnnxExport, TrainingInputProvenance, dict[str, str]] | None = None
    sidecar = output_path.with_suffix(f"{output_path.suffix}.manifest.json")
    commit = output_path.with_suffix(f"{output_path.suffix}.commit.json")
    if any(path.exists() for path in (output_path, sidecar, commit)):
        existing = _read_candidate(output_path)
        exported, provenance, hashes = existing
        if expected_run_id is not None and provenance.run_id != expected_run_id:
            raise TrainingWorkError("training_publication_run_mismatch")
        # Exact committed replay needs no renewed grant or expired historical inputs.
        if authority.is_published(provenance.run_id, hashes, input_provenance=provenance):
            authority.publish(provenance.run_id, operation_id, hashes, input_provenance=provenance)
            return exported
        # Rehashable sidecars cannot prove these bytes came from the verified exporter.
        # Retain them until the controlled writer/cleanup protocol can reconcile them.
        raise TrainingWorkError("training_unpublished_candidate_reconciliation_required")

    checkpoint = _manifest(
        checkpoint_directory / "manifest.json", SONA_MAX_CHECKPOINT_MANIFEST_BYTES
    )
    if (
        checkpoint.get("schema_version") != 4
        or checkpoint.get("source_kind") != SONA_SOURCE_KIND_OWNER_APPROVED
    ):
        raise ValueError("Sona publication requires a checkpoint with current owner authority")
    provenance = TrainingInputProvenance.parse(checkpoint.get("training_authority"))
    if expected_run_id is not None and provenance.run_id != expected_run_id:
        raise TrainingWorkError("training_publication_run_mismatch")
    checkpoint_digest = sha256(rfc8785.dumps(checkpoint)).hexdigest()
    authority.check_checkpoint(provenance, checkpoint_digest)
    if quality_bundle is None:
        model, result = load_sona_checkpoint(checkpoint_directory)
    else:
        model, result = load_quality_sona_checkpoint(checkpoint_directory, quality_bundle)
    if result.training_authority != provenance:
        raise ValueError("Sona checkpoint authority changed during verification")
    tokenizer_path = tokenizer_directory / "manifest.json"
    snapshot = _bounded_read(tokenizer_path, SONA_MAX_TOKENIZER_MANIFEST_BYTES)
    tokenizer_manifest = _manifest_payload(snapshot)
    tokenizer = load_sona_tokenizer(tokenizer_directory)
    if (
        _bounded_read(tokenizer_path, SONA_MAX_TOKENIZER_MANIFEST_BYTES) != snapshot
        or tokenizer.manifest_sha256 != result.tokenizer_sha256
    ):
        raise ValueError("Sona publication tokenizer binding mismatch")
    tokenizer_artifact_digest = sha256(rfc8785.dumps(tokenizer_manifest)).hexdigest()

    def check_current() -> None:
        if quality_bundle is not None:
            # The dedicated loader uses wall-clock time; repeat that frozen approval
            # check just before candidate installation, independently of live consent.
            from time import time_ns

            reverify_quality_approved_sona_dataset_bundle(
                quality_bundle, at_ms=time_ns() // 1_000_000
            )
        authority.check_checkpoint(provenance, result.checkpoint_manifest_sha256)

    if existing is None:
        exported = export_sona_onnx(
            model,
            output_path,
            provenance=SonaOnnxProvenance(
                checkpoint_manifest_sha256=result.checkpoint_manifest_sha256,
                checkpoint_weights_sha256=result.weights_sha256,
                dataset_manifest_sha256=result.dataset_manifest_sha256,
                tokenizer_sha256=result.tokenizer_sha256,
                dataset_approval_sha256=result.dataset_approval_sha256,
                dataset_bundle_sha256=result.dataset_bundle_sha256,
                quality_provenance_eligible=result.quality_provenance_eligible,
                quality_eligible=result.quality_eligible,
                training_authority=provenance,
                tokenizer_artifact_manifest_sha256=tokenizer_artifact_digest,
            ),
            before_publish=check_current,
        )
        # Read installed bytes, not the exporter return value, into the final transaction.
        installed = _read_candidate(output_path)
        if installed[0] != exported:
            raise ValueError("Sona installed candidate changed after verified export")
        existing = installed
    exported, stored_provenance, hashes = existing
    if (
        stored_provenance != provenance
        or exported.checkpoint_manifest_sha256 != result.checkpoint_manifest_sha256
        or exported.weights_sha256 != result.weights_sha256
        or exported.config_sha256
        != sha256(
            rfc8785.dumps({"architecture": "SONA_LITE_SHARED_GRU_V1", **asdict(model.config)})
        ).hexdigest()
        or exported.dataset_approval_sha256 != result.dataset_approval_sha256
        or exported.dataset_bundle_sha256 != result.dataset_bundle_sha256
        or exported.quality_provenance_eligible != result.quality_provenance_eligible
        or hashes["tokenizer_sha256"] != result.tokenizer_sha256
        or hashes["tokenizer_manifest_sha256"] != tokenizer_artifact_digest
    ):
        raise ValueError("Sona installed candidate does not match its verified checkpoint")
    check_current()
    if before_registry_publish is not None:
        before_registry_publish()
    authority.publish(provenance.run_id, operation_id, hashes, input_provenance=provenance)
    return exported


def export_owned_sona_checkpoint(
    checkpoint_directory: Path,
    tokenizer_directory: Path,
    *,
    artifact_name: str,
    execution_id: UUID,
    run_id: UUID,
    operation_id: UUID,
    execution_inventory_sha256: str,
    tokenizer_relative: str,
    maximum_output_bytes: int,
    checkpoint_output_bytes: int,
    authority: SonaSharedTrainingAuthority,
) -> SonaOwnedPublication:
    """Export inside the retained child and bind every file to its owned directory."""

    publication_directory = checkpoint_directory / "publication"
    _digest({"value": execution_inventory_sha256}, "value")
    if (
        Path(artifact_name).name != artifact_name
        or not artifact_name.endswith(".onnx")
        or publication_directory.exists()
        or checkpoint_output_bytes < 1
        or maximum_output_bytes <= SONA_OWNED_PUBLICATION_INTENT_MAX_BYTES
    ):
        raise ValueError("invalid controlled Sona publication output")
    model, result = load_sona_checkpoint(checkpoint_directory)
    provenance = result.training_authority
    if provenance is None or provenance.run_id != run_id:
        raise ValueError("Sona checkpoint publication authority is invalid")
    authority.check_running()
    tokenizer_path = tokenizer_directory / "manifest.json"
    snapshot = _bounded_read(tokenizer_path, SONA_MAX_TOKENIZER_MANIFEST_BYTES)
    tokenizer_manifest = _manifest_payload(snapshot)
    tokenizer = load_sona_tokenizer(tokenizer_directory)
    if (
        _bounded_read(tokenizer_path, SONA_MAX_TOKENIZER_MANIFEST_BYTES) != snapshot
        or tokenizer.manifest_sha256 != result.tokenizer_sha256
    ):
        raise ValueError("Sona publication tokenizer binding mismatch")
    tokenizer_artifact_digest = sha256(rfc8785.dumps(tokenizer_manifest)).hexdigest()
    artifact = publication_directory / artifact_name

    def check_current() -> None:
        authority.check_running()

    try:
        exported = export_sona_onnx(
            model,
            artifact,
            provenance=SonaOnnxProvenance(
                checkpoint_manifest_sha256=result.checkpoint_manifest_sha256,
                checkpoint_weights_sha256=result.weights_sha256,
                dataset_manifest_sha256=result.dataset_manifest_sha256,
                tokenizer_sha256=result.tokenizer_sha256,
                dataset_approval_sha256=result.dataset_approval_sha256,
                dataset_bundle_sha256=result.dataset_bundle_sha256,
                quality_provenance_eligible=result.quality_provenance_eligible,
                quality_eligible=result.quality_eligible,
                training_authority=provenance,
                tokenizer_artifact_manifest_sha256=tokenizer_artifact_digest,
            ),
            before_publish=check_current,
            maximum_total_bytes=(maximum_output_bytes - SONA_OWNED_PUBLICATION_INTENT_MAX_BYTES),
            base_output_bytes=checkpoint_output_bytes,
        )
        installed, stored_provenance, trusted_hashes = _read_candidate(artifact)
        if installed != exported or stored_provenance != provenance:
            raise ValueError("Sona installed candidate changed after verified export")
        if exported.commit_sha256 is None:
            raise ValueError("Sona installed candidate has no commit identity")
        authority.check_running()
        intent: dict[str, JsonValue] = {
            "schema_version": 1,
            "execution_id": str(execution_id),
            "run_id": str(run_id),
            "publication_operation_id": str(operation_id),
            "execution_inventory_sha256": execution_inventory_sha256,
            "tokenizer_relative": tokenizer_relative,
            "artifact_name": artifact_name,
            "checkpoint_manifest_sha256": result.checkpoint_manifest_sha256,
            "artifact_sha256": exported.artifact_sha256,
            "model_manifest_sha256": exported.model_manifest_sha256,
            "commit_sha256": exported.commit_sha256,
        }
        envelope: dict[str, JsonValue] = {
            "intent": intent,
            "intent_sha256": sha256(rfc8785.dumps(intent)).hexdigest(),
        }
        payload = rfc8785.dumps(envelope)
        if len(payload) > SONA_OWNED_PUBLICATION_INTENT_MAX_BYTES:
            raise ValueError("Sona owned publication intent exceeds its bound")
        (publication_directory / "intent.json").write_bytes(payload)
        if (
            checkpoint_output_bytes
            + sum(
                path.stat(follow_symlinks=False).st_size
                for path in (
                    artifact,
                    artifact.with_suffix(f"{artifact.suffix}.manifest.json"),
                    artifact.with_suffix(f"{artifact.suffix}.commit.json"),
                    publication_directory / "intent.json",
                )
            )
            > maximum_output_bytes
        ):
            raise ValueError("controlled training output exceeds its admitted bound")
        owned = read_owned_sona_publication(
            checkpoint_directory,
            expected_execution_id=execution_id,
            expected_run_id=run_id,
            expected_operation_id=operation_id,
            expected_execution_inventory_sha256=execution_inventory_sha256,
            expected_tokenizer_relative=tokenizer_relative,
            expected_artifact_name=artifact_name,
            expected_checkpoint_manifest_sha256=result.checkpoint_manifest_sha256,
        )
        if (
            owned.exported != exported
            or owned.provenance != provenance
            or owned.hashes != trusted_hashes
        ):
            raise ValueError("Sona owned publication changed after verified export")
        return owned
    except BaseException:
        shutil.rmtree(publication_directory, ignore_errors=True)
        raise


def read_owned_sona_publication(
    checkpoint_directory: Path,
    *,
    expected_execution_id: UUID | None = None,
    expected_run_id: UUID | None = None,
    expected_operation_id: UUID | None = None,
    expected_execution_inventory_sha256: str | None = None,
    expected_tokenizer_relative: str | None = None,
    expected_artifact_name: str | None = None,
    expected_checkpoint_manifest_sha256: str | None = None,
) -> SonaOwnedPublication:
    """Verify the exact self-contained child output without trusting filenames."""

    directory = checkpoint_directory / "publication"
    envelope = _object(
        _bounded_read(directory / "intent.json", SONA_OWNED_PUBLICATION_INTENT_MAX_BYTES)
    )
    intent = envelope.get("intent")
    if (
        set(envelope) != {"intent", "intent_sha256"}
        or not isinstance(intent, dict)
        or set(intent)
        != {
            "schema_version",
            "execution_id",
            "run_id",
            "publication_operation_id",
            "execution_inventory_sha256",
            "tokenizer_relative",
            "artifact_name",
            "checkpoint_manifest_sha256",
            "artifact_sha256",
            "model_manifest_sha256",
            "commit_sha256",
        }
        or envelope["intent_sha256"] != sha256(rfc8785.dumps(intent)).hexdigest()
        or intent["schema_version"] != 1
    ):
        raise ValueError("Sona owned publication intent is invalid")
    execution_id = _canonical_uuid(intent["execution_id"], "execution")
    run_id = _canonical_uuid(intent["run_id"], "run")
    operation_id = _canonical_uuid(intent["publication_operation_id"], "operation")
    inventory = _digest(intent, "execution_inventory_sha256")
    checkpoint_digest = _digest(intent, "checkpoint_manifest_sha256")
    tokenizer_relative = intent["tokenizer_relative"]
    artifact_name = intent["artifact_name"]
    if (
        not isinstance(tokenizer_relative, str)
        or Path(tokenizer_relative).is_absolute()
        or not Path(tokenizer_relative).parts
        or any(part in {"", ".", ".."} for part in Path(tokenizer_relative).parts)
        or Path(tokenizer_relative).as_posix() != tokenizer_relative
        or not isinstance(artifact_name, str)
        or Path(artifact_name).name != artifact_name
        or not artifact_name.endswith(".onnx")
    ):
        raise ValueError("Sona owned publication paths are invalid")
    artifact = directory / artifact_name
    exported, provenance, hashes = _read_candidate(artifact)
    if (
        provenance.run_id != run_id
        or exported.checkpoint_manifest_sha256 != checkpoint_digest
        or exported.artifact_sha256 != _digest(intent, "artifact_sha256")
        or exported.model_manifest_sha256 != _digest(intent, "model_manifest_sha256")
        or exported.commit_sha256 != _digest(intent, "commit_sha256")
    ):
        raise ValueError("Sona owned publication binding is invalid")
    owned = SonaOwnedPublication(
        execution_id,
        run_id,
        operation_id,
        inventory,
        tokenizer_relative,
        artifact_name,
        checkpoint_digest,
        exported,
        provenance,
        hashes,
    )
    metadata = directory.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or directory.is_symlink()
        or getattr(metadata, "st_file_attributes", 0) & 0x400
    ):
        raise ValueError("Sona owned publication directory is unsafe")
    actual: set[Path] = set()
    for path in directory.iterdir():
        child = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(child.st_mode)
            or path.is_symlink()
            or getattr(child, "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Sona owned publication file set changed")
        actual.add(path.relative_to(checkpoint_directory))
    if actual != set(owned.relative_files):
        raise ValueError("Sona owned publication file set changed")
    expected = (
        (expected_execution_id, owned.execution_id),
        (expected_run_id, owned.run_id),
        (expected_operation_id, owned.operation_id),
        (expected_execution_inventory_sha256, owned.execution_inventory_sha256),
        (expected_tokenizer_relative, owned.tokenizer_relative),
        (expected_artifact_name, owned.artifact_name),
        (expected_checkpoint_manifest_sha256, owned.checkpoint_manifest_sha256),
    )
    if any(wanted is not None and wanted != observed for wanted, observed in expected):
        raise TrainingWorkError("training_publication_binding_mismatch")
    return owned


def publish_owned_sona_checkpoint(
    checkpoint_directory: Path,
    *,
    authority: TrainingWorkService,
    expected_execution_id: UUID,
    expected_run_id: UUID,
    expected_operation_id: UUID,
    expected_execution_inventory_sha256: str,
    expected_tokenizer_relative: str,
    expected_artifact_name: str,
    expected_publication_seal_sha256: str,
) -> SonaOnnxExport:
    """Publish only bytes produced inside the exact exited retained child."""

    owned = read_owned_sona_publication(
        checkpoint_directory,
        expected_execution_id=expected_execution_id,
        expected_run_id=expected_run_id,
        expected_operation_id=expected_operation_id,
        expected_execution_inventory_sha256=expected_execution_inventory_sha256,
        expected_tokenizer_relative=expected_tokenizer_relative,
        expected_artifact_name=expected_artifact_name,
    )
    if sona_owned_publication_seal_sha256(owned) != _digest(
        {"value": expected_publication_seal_sha256}, "value"
    ):
        raise TrainingWorkError("training_publication_seal_mismatch")
    checkpoint = _manifest(
        checkpoint_directory / "manifest.json", SONA_MAX_CHECKPOINT_MANIFEST_BYTES
    )
    provenance = TrainingInputProvenance.parse(checkpoint.get("training_authority"))
    checkpoint_digest = sha256(rfc8785.dumps(checkpoint)).hexdigest()
    artifact = checkpoint_directory / "publication" / owned.artifact_name
    candidate = _manifest(artifact.with_suffix(f"{artifact.suffix}.manifest.json"), 131_072)
    candidate_provenance = candidate.get("training_provenance")
    if (
        checkpoint.get("schema_version") != 4
        or checkpoint.get("source_kind") != SONA_SOURCE_KIND_OWNER_APPROVED
        or checkpoint.get("quality_eligible") is not False
        or provenance != owned.provenance
        or checkpoint_digest != owned.checkpoint_manifest_sha256
        or not isinstance(candidate_provenance, dict)
    ):
        raise ValueError("Sona owned publication checkpoint binding changed")
    model_config = checkpoint.get("model_config")
    if not isinstance(model_config, dict):
        raise ValueError("Sona checkpoint model config is invalid")
    expected_config_sha256 = sha256(
        rfc8785.dumps({"architecture": "SONA_LITE_SHARED_GRU_V1", **model_config})
    ).hexdigest()
    if (
        owned.exported.weights_sha256 != _digest(checkpoint, "weights_sha256")
        or owned.exported.config_sha256 != expected_config_sha256
        or owned.hashes["checkpoint_sha256"] != checkpoint_digest
        or owned.hashes["tokenizer_sha256"] != _digest(checkpoint, "tokenizer_sha256")
        or _digest(candidate_provenance, "dataset_manifest_sha256")
        != _digest(checkpoint, "dataset_manifest_sha256")
        or owned.exported.dataset_approval_sha256
        != _optional_digest(checkpoint, "dataset_approval_sha256")
        or owned.exported.dataset_bundle_sha256
        != _optional_digest(checkpoint, "dataset_bundle_sha256")
        or owned.exported.quality_provenance_eligible
        != checkpoint.get("quality_provenance_eligible")
        or candidate_provenance.get("quality_eligible") is not False
    ):
        raise ValueError("Sona owned publication dependencies changed")
    if authority.is_published(
        expected_run_id,
        owned.hashes,
        input_provenance=provenance,
    ):
        authority.publish(
            expected_run_id,
            expected_operation_id,
            owned.hashes,
            input_provenance=provenance,
        )
        return owned.exported
    authority.check_checkpoint(provenance, checkpoint_digest)
    authority.publish(
        expected_run_id,
        expected_operation_id,
        owned.hashes,
        input_provenance=provenance,
    )
    return owned.exported


def sona_owned_publication_seal_sha256(owned: SonaOwnedPublication) -> str:
    """Seal the exact trusted-child tuple independently of mutable sidecars."""

    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "execution_id": str(owned.execution_id),
        "run_id": str(owned.run_id),
        "publication_operation_id": str(owned.operation_id),
        "execution_inventory_sha256": owned.execution_inventory_sha256,
        "tokenizer_relative": owned.tokenizer_relative,
        "artifact_name": owned.artifact_name,
        "checkpoint_manifest_sha256": owned.checkpoint_manifest_sha256,
        "artifact_sha256": owned.exported.artifact_sha256,
        "model_manifest_sha256": owned.exported.model_manifest_sha256,
        "commit_sha256": owned.exported.commit_sha256,
        "publication_hashes": cast(dict[str, JsonValue], owned.hashes),
    }
    if owned.exported.commit_sha256 is None:
        raise ValueError("Sona owned publication has no commit identity")
    return sha256(rfc8785.dumps(document)).hexdigest()


def _read_candidate(
    output_path: Path,
) -> tuple[SonaOnnxExport, TrainingInputProvenance, dict[str, str]]:
    manifest = _manifest(output_path.with_suffix(f"{output_path.suffix}.manifest.json"), 131_072)
    raw_provenance = manifest.get("training_provenance")
    if (
        type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 2
        or not isinstance(raw_provenance, dict)
    ):
        raise ValueError("Sona candidate requires current training provenance")
    provenance = raw_provenance
    input_provenance = TrainingInputProvenance.parse(provenance.get("training_authority"))
    artifact_digest = sha256(_bounded_read(output_path, 536_870_912)).hexdigest()
    manifest_digest = sha256(rfc8785.dumps(manifest)).hexdigest()
    if _digest(manifest, "artifact_sha256") != artifact_digest:
        raise ValueError("Sona candidate artifact hash mismatch")
    marker_payload = _bounded_read(
        output_path.with_suffix(f"{output_path.suffix}.commit.json"), 16_384
    )
    marker = _object(marker_payload)
    expected: dict[str, JsonValue] = {
        "schema_version": 1,
        "state": "COMMITTED",
        "artifact_sha256": artifact_digest,
        "model_manifest_sha256": manifest_digest,
    }
    if (
        set(marker) != {"commit", "commit_sha256"}
        or marker["commit"] != expected
        or marker["commit_sha256"] != sha256(rfc8785.dumps(expected)).hexdigest()
    ):
        raise ValueError("Sona candidate commit identity mismatch")
    if (
        type(provenance.get("quality_provenance_eligible")) is not bool
        or provenance.get("quality_eligible") is not False
        or type(manifest.get("opset")) is not int
    ):
        raise ValueError("Sona candidate quality/opset declaration is invalid")
    exported = SonaOnnxExport(
        artifact_digest,
        _digest(manifest, "weights_sha256"),
        _digest(manifest, "config_sha256"),
        manifest_digest,
        cast(int, manifest["opset"]),
        _digest(provenance, "checkpoint_manifest_sha256"),
        _optional_digest(provenance, "dataset_approval_sha256"),
        _optional_digest(provenance, "dataset_bundle_sha256"),
        cast(bool, provenance["quality_provenance_eligible"]),
        False,
        marker["commit_sha256"],
    )
    return (
        exported,
        input_provenance,
        {
            "artifact_sha256": artifact_digest,
            "manifest_sha256": manifest_digest,
            "checkpoint_sha256": cast(str, exported.checkpoint_manifest_sha256),
            "tokenizer_manifest_sha256": _digest(provenance, "tokenizer_artifact_manifest_sha256"),
            "tokenizer_sha256": _digest(provenance, "tokenizer_sha256"),
        },
    )


def _digest(document: dict[str, JsonValue], key: str) -> str:
    value = document.get(key)
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("Sona publication digest is invalid")
    return value


def _optional_digest(document: dict[str, JsonValue], key: str) -> str | None:
    return None if document.get(key) is None else _digest(document, key)


def _canonical_uuid(value: object, name: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"Sona publication {name} identity is invalid")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(f"Sona publication {name} identity is invalid") from error
    if str(parsed) != value:
        raise ValueError(f"Sona publication {name} identity is noncanonical")
    return parsed


def _manifest(path: Path, maximum: int) -> dict[str, JsonValue]:
    return _manifest_payload(_bounded_read(path, maximum))


def _manifest_payload(payload: bytes) -> dict[str, JsonValue]:
    envelope = _object(payload)
    document = envelope.get("manifest")
    if (
        set(envelope) != {"manifest", "manifest_sha256"}
        or not isinstance(document, dict)
        or envelope["manifest_sha256"] != sha256(rfc8785.dumps(document)).hexdigest()
    ):
        raise ValueError("Sona publication manifest identity mismatch")
    return document


def _object(payload: bytes) -> dict[str, JsonValue]:
    def unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate Sona publication key")
            result[key] = value
        return result

    value = cast(JsonValue, json.loads(payload, object_pairs_hook=unique))
    if not isinstance(value, dict):
        raise ValueError("Sona publication JSON must be an object")
    return value


def _bounded_read(path: Path, maximum: int) -> bytes:
    metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or getattr(metadata, "st_file_attributes", 0) & 0x400
        or not 1 <= metadata.st_size <= maximum
    ):
        raise ValueError("Sona publication input is unsafe")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError("Sona publication input changed")
        value = stream.read(maximum + 1)
    current = path.stat(follow_symlinks=False)
    if (
        len(value) != metadata.st_size
        or len(value) > maximum
        or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        != (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
    ):
        raise ValueError("Sona publication input changed")
    return value
