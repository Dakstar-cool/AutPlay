"""Retained process and measured byte ownership for one shared-training run."""

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .vault import Sha256Digest


@dataclass(frozen=True, slots=True)
class TrainingExecutionTicket:
    execution_id: UUID
    run_id: UUID
    input_root: str
    output_root: str
    input_root_device: str
    input_root_inode: str
    input_scope_device: str
    input_scope_inode: str
    output_scope_device: str
    output_scope_inode: str
    input_inventory_sha256: Sha256Digest
    root_inventory_sha256: Sha256Digest
    input_bytes: int
    maximum_output_bytes: int

    def __post_init__(self) -> None:
        input_path, output_path = Path(self.input_root), Path(self.output_root)
        try:
            common = os.path.commonpath((self.input_root, self.output_root))
        except ValueError:
            common = ""
        if (
            not isinstance(self.input_root, str)
            or not self.input_root
            or len(self.input_root) > 4096
            or not isinstance(self.output_root, str)
            or not self.output_root
            or len(self.output_root) > 4096
            or not isinstance(self.input_root_device, str)
            or not self.input_root_device.isdecimal()
            or not isinstance(self.input_root_inode, str)
            or not self.input_root_inode.isdecimal()
            or any(
                not isinstance(value, str) or not value.isdecimal() or len(value) > 32
                for value in (
                    self.input_scope_device,
                    self.input_scope_inode,
                    self.output_scope_device,
                    self.output_scope_inode,
                )
            )
            or len(self.input_root_device) > 32
            or len(self.input_root_inode) > 32
            or not input_path.is_absolute()
            or not output_path.is_absolute()
            or str(input_path) != os.path.abspath(input_path)
            or str(output_path) != os.path.abspath(output_path)
            or input_path.name != str(self.execution_id)
            or os.path.normcase(common)
            in {
                os.path.normcase(self.input_root),
                os.path.normcase(self.output_root),
            }
            or type(self.input_bytes) is not int
            or type(self.maximum_output_bytes) is not int
            or not 1 <= self.input_bytes <= 2**63 - 1
            or not 1 <= self.maximum_output_bytes <= 2**63 - 1
        ):
            raise ValueError("invalid training execution byte bounds")

    @property
    def kind(self) -> str:
        return "SHARED_TRAINING"

    @property
    def owner_run_id(self) -> UUID:
        return self.run_id


@dataclass(frozen=True, slots=True)
class TrainingExecutionCleanupPlan:
    ticket: TrainingExecutionTicket
    checkpoint_device: str | None
    checkpoint_inode: str | None
    retain_checkpoint: bool | None
    checkpoint_manifest_sha256: str | None
    checkpoint_weights_sha256: str | None
    checkpoint_optimizer_steps: int | None
    checkpoint_device_type: str | None
    publication_seal_sha256: str | None = None

    def __post_init__(self) -> None:
        device, inode = self.checkpoint_device, self.checkpoint_inode
        bound = device is not None or inode is not None
        candidate_values = (
            self.checkpoint_manifest_sha256,
            self.checkpoint_weights_sha256,
            self.checkpoint_optimizer_steps,
            self.checkpoint_device_type,
        )
        candidate = any(value is not None for value in candidate_values)
        if (
            bound != (device is not None and inode is not None)
            or (
                device is not None
                and inode is not None
                and (
                    not device.isdecimal()
                    or not inode.isdecimal()
                    or len(device) > 32
                    or len(inode) > 32
                )
            )
            or self.retain_checkpoint not in {None, False, True}
            or candidate != all(value is not None for value in candidate_values)
            or (
                candidate
                and (
                    not bound
                    or any(
                        not isinstance(digest, str)
                        or len(digest) != 64
                        or any(character not in "0123456789abcdef" for character in digest)
                        for digest in (
                            self.checkpoint_manifest_sha256,
                            self.checkpoint_weights_sha256,
                        )
                    )
                    or type(self.checkpoint_optimizer_steps) is not int
                    or self.checkpoint_optimizer_steps < 1
                    or self.checkpoint_device_type not in {"cpu", "cuda"}
                )
            )
            or (self.retain_checkpoint in {None, True} and not candidate)
            or (
                self.publication_seal_sha256 is not None
                and (
                    not candidate
                    or len(self.publication_seal_sha256) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in self.publication_seal_sha256
                    )
                )
            )
        ):
            raise ValueError("invalid training execution cleanup plan")


__all__ = ["TrainingExecutionCleanupPlan", "TrainingExecutionTicket"]
