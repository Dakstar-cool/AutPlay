"""Explicit current-authority recording port for synthetic owner-shaped test inputs."""

from uuid import UUID

from autplay.application.training_work import TrainingWorkError
from autplay.domain.training_work import TrainingInputProvenance
from autplay_sona_training.authority import SonaTrainingInputBinding


class RecordingAuthority:
    def __init__(self, *, refuse_start: bool = False, refuse_check: int | None = None) -> None:
        self.bindings: list[SonaTrainingInputBinding] = []
        self.checks = 0
        self.refuse_start, self.refuse_check = refuse_start, refuse_check

    def authorize(self, binding: SonaTrainingInputBinding) -> TrainingInputProvenance:
        self.bindings.append(binding)
        if self.refuse_start:
            raise TrainingWorkError("training_input_binding_mismatch")
        return TrainingInputProvenance(UUID(int=555), UUID(int=777), 1, "f" * 64)

    def check_running(self) -> None:
        self.checks += 1
        if self.checks == self.refuse_check:
            raise TrainingWorkError("training_run_invalidated")

    def seal_checkpoint(self, provenance: TrainingInputProvenance, manifest_sha256: str) -> None:
        if not self.bindings or provenance.run_id != UUID(int=555):
            raise TrainingWorkError("training_checkpoint_seal_required")
