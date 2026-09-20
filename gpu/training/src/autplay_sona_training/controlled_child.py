"""Fixed credential-free child for one retained owner-derived training execution."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from contextlib import redirect_stdout, suppress
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from autplay.adapters.filesystem.inventory_protocol import MAX_INVENTORY_REPLY_BYTES
from autplay.adapters.filesystem.vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)
from autplay.domain.training_work import TrainingInputProvenance

from .authority import SonaSharedTrainingAuthority, SonaTrainingInputBinding
from .dataset import SONA_SOURCE_KIND_OWNER_APPROVED, load_sona_dataset_header
from .execution_protocol import (
    SonaControlledTrainingCommand,
    SonaControlledTrainingResult,
    authorize_request,
    check_request,
    checkpoint_output_bytes,
    inventory_matches,
    seal_request,
)
from .publication import (
    export_owned_sona_checkpoint,
    sona_owned_publication_seal_sha256,
)
from .root_inventory import inspect_sona_training_root
from .trainer import train_sona_checkpoint


class _PipeAuthority(SonaSharedTrainingAuthority):
    def __init__(self, source: BinaryIO, destination: BinaryIO) -> None:
        self._source, self._destination = source, destination

    def _request(self, tag: bytes, document: dict[str, object]) -> dict[str, object]:
        write_frame(
            self._destination,
            tag,
            encode_document(document, maximum=MAX_INVENTORY_REPLY_BYTES),
        )
        response_tag, payload = read_frame(self._source, maximum=MAX_INVENTORY_REPLY_BYTES)
        if response_tag != b"K":
            raise ChildProtocolError()
        return decode_document(payload, maximum=MAX_INVENTORY_REPLY_BYTES)

    def authorize(self, binding: SonaTrainingInputBinding) -> TrainingInputProvenance:
        response = self._request(b"A", authorize_request(binding))
        if set(response) != {"version", "provenance"} or response["version"] != 1:
            raise ChildProtocolError()
        return TrainingInputProvenance.parse(response["provenance"])

    def check_running(self) -> None:
        if self._request(b"C", check_request()) != {"version": 1}:
            raise ChildProtocolError()

    def seal_checkpoint(self, provenance: TrainingInputProvenance, manifest_sha256: str) -> None:
        if self._request(b"S", seal_request(provenance, manifest_sha256)) != {"version": 1}:
            raise ChildProtocolError()


def _safe_directory(path: Path, name: str) -> None:
    metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or getattr(metadata, "st_file_attributes", 0) & 0x400
    ):
        raise ValueError(f"controlled training {name} is unsafe")


def execute(
    command: SonaControlledTrainingCommand,
    source: BinaryIO,
    destination: BinaryIO,
) -> SonaControlledTrainingResult:
    inventory = inspect_sona_training_root(command.input_root)
    if not inventory_matches(command, inventory):
        raise ValueError("controlled training root inventory mismatch")
    _safe_directory(command.output_root, "output root")
    dataset = command.input_root / command.dataset_relative
    _safe_directory(dataset, "dataset")
    checkpoint = command.output_root / command.checkpoint_name
    if checkpoint.exists() or checkpoint.is_symlink():
        raise ValueError("controlled training checkpoint target exists")
    header = load_sona_dataset_header(dataset)
    if header.source_kind != SONA_SOURCE_KIND_OWNER_APPROVED:
        raise ValueError("controlled training requires owner-derived input")

    def validate_candidate(temporary: Path) -> None:
        if checkpoint_output_bytes(temporary) > command.maximum_output_bytes:
            raise ValueError("controlled training output exceeds its admitted bound")
        if not inventory_matches(command, inspect_sona_training_root(command.input_root)):
            raise ValueError("controlled training root changed during execution")

    authority = _PipeAuthority(source, destination)
    try:
        result = train_sona_checkpoint(
            dataset,
            checkpoint,
            model_config=command.model_config,
            training_config=command.training_config,
            shared_training_authority=authority,
            maximum_checkpoint_bytes=command.maximum_output_bytes,
            before_seal=validate_candidate,
            checkpoint_staging_directory=command.checkpoint_staging_path,
        )
        publication_seal_sha256: str | None = None
        if command.publication_operation_id is not None:
            if command.tokenizer_relative is None or command.artifact_name is None:
                raise ValueError("controlled training publication binding is incomplete")
            tokenizer = command.input_root / command.tokenizer_relative
            _safe_directory(tokenizer, "tokenizer")
            base_output_bytes = checkpoint_output_bytes(checkpoint)
            with redirect_stdout(sys.stderr):
                owned = export_owned_sona_checkpoint(
                    checkpoint,
                    tokenizer,
                    artifact_name=command.artifact_name,
                    execution_id=command.execution_id,
                    run_id=command.run_id,
                    operation_id=command.publication_operation_id,
                    execution_inventory_sha256=command.root_inventory_sha256,
                    tokenizer_relative=command.tokenizer_relative.as_posix(),
                    maximum_output_bytes=command.maximum_output_bytes,
                    checkpoint_output_bytes=base_output_bytes,
                    authority=authority,
                )
            publication_seal_sha256 = sona_owned_publication_seal_sha256(owned)
            if checkpoint_output_bytes(checkpoint) > command.maximum_output_bytes:
                raise ValueError("controlled training output exceeds its admitted bound")
            if not inventory_matches(command, inspect_sona_training_root(command.input_root)):
                raise ValueError("controlled training root changed during publication")
            authority.check_running()
        return SonaControlledTrainingResult.from_training(
            result,
            publication_seal_sha256=publication_seal_sha256,
        )
    except BaseException:
        with suppress(OSError):
            if checkpoint.is_dir() and not checkpoint.is_symlink():
                shutil.rmtree(checkpoint)
        raise


def main() -> int:
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    try:
        write_frame(
            destination,
            b"H",
            encode_document({"pid": os.getpid(), "nonce": uuid4().hex}),
        )
        tag, payload = read_frame(source, maximum=MAX_INVENTORY_REPLY_BYTES)
        if tag != b"G":
            raise ChildProtocolError()
        command = SonaControlledTrainingCommand.parse(
            decode_document(payload, maximum=MAX_INVENTORY_REPLY_BYTES)
        )
        result = execute(command, source, destination)
        write_frame(
            destination,
            b"R",
            encode_document(result.document(), maximum=MAX_INVENTORY_REPLY_BYTES),
        )
        return 0
    except BaseException:
        with suppress(ValueError, OSError):
            write_frame(
                destination,
                b"E",
                encode_document({"version": 1, "code": "controlled_training_failed"}),
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
