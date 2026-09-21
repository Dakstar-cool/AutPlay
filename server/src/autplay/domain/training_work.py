"""Compact, versioned training input authority carried through candidate artifacts."""

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from autplay.domain.recommendations import JsonValue


@dataclass(frozen=True, slots=True)
class TrainingInputProvenance:
    run_id: UUID
    server_instance_id: UUID
    identity_epoch: int
    binding_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.run_id, UUID)
            or not isinstance(self.server_instance_id, UUID)
            or type(self.identity_epoch) is not int
            or not 1 <= self.identity_epoch <= 9_007_199_254_740_991
            or not isinstance(self.binding_sha256, str)
            or len(self.binding_sha256) != 64
            or any(value not in "0123456789abcdef" for value in self.binding_sha256)
        ):
            raise ValueError("invalid training input provenance")

    def document(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "run_id": str(self.run_id),
            "server_instance_id": str(self.server_instance_id),
            "identity_epoch": self.identity_epoch,
            "binding_sha256": self.binding_sha256,
        }

    @classmethod
    def parse(cls, value: object) -> TrainingInputProvenance:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "run_id",
            "server_instance_id",
            "identity_epoch",
            "binding_sha256",
        }:
            raise ValueError("invalid training input provenance document")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("unsupported training input provenance schema")
        if not isinstance(value["run_id"], str) or not isinstance(value["server_instance_id"], str):
            raise ValueError("invalid training input provenance identity")
        run_id, server_id = UUID(value["run_id"]), UUID(value["server_instance_id"])
        if str(run_id) != value["run_id"] or str(server_id) != value["server_instance_id"]:
            raise ValueError("noncanonical training input provenance identity")
        return cls(
            run_id,
            server_id,
            cast(int, value["identity_epoch"]),
            cast(str, value["binding_sha256"]),
        )
