"""Current shared-training authority, independent of historical signed approval policy."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from autplay.application.training_work import TrainingWorkService
from autplay.domain.training_work import TrainingInputProvenance


@dataclass(frozen=True, slots=True)
class SonaTrainingInputBinding:
    source_sha256: str
    dataset_sha256: str
    lineage_key_id: str
    owner_tokens: tuple[str, ...]


class SonaSharedTrainingAuthority(Protocol):
    def authorize(self, binding: SonaTrainingInputBinding) -> TrainingInputProvenance: ...
    def check_running(self) -> None: ...
    def seal_checkpoint(
        self, provenance: TrainingInputProvenance, manifest_sha256: str
    ) -> None: ...


class PostgresSonaTrainingAuthority:
    """Separate current DB registry; never constructed from the historical source DB.

    Authorizes control-plane progress only. The worker composition must also retain
    its exact process/byte execution authority, and publish candidates atomically.
    """

    def __init__(self, service: TrainingWorkService, run_id: UUID) -> None:
        self._service, self._run_id = service, run_id
        self._binding: SonaTrainingInputBinding | None = None

    def authorize(self, binding: SonaTrainingInputBinding) -> TrainingInputProvenance:
        provenance = self._service.authorize_input(
            self._run_id,
            source_sha256=binding.source_sha256,
            dataset_sha256=binding.dataset_sha256,
            lineage_key_id=binding.lineage_key_id,
            owner_tokens=binding.owner_tokens,
        )
        self._binding = binding
        return provenance

    def check_running(self) -> None:
        binding = self._binding
        if binding is None:
            raise RuntimeError("training input authority missing")
        self._service.check_input(
            self._run_id,
            source_sha256=binding.source_sha256,
            dataset_sha256=binding.dataset_sha256,
            lineage_key_id=binding.lineage_key_id,
            owner_tokens=binding.owner_tokens,
        )

    def seal_checkpoint(self, provenance: TrainingInputProvenance, manifest_sha256: str) -> None:
        if provenance.run_id != self._run_id or self._binding is None:
            raise RuntimeError("training input authority missing")
        self.check_running()
        self._service.seal_checkpoint(provenance, manifest_sha256)
