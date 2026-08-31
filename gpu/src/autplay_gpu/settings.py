"""Explicit isolated GPU-worker settings kept outside the CPU server project."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

_SELECTOR = re.compile(
    r"^(?:auto|uuid:GPU-[A-Za-z0-9-]{8,100}|pci:(?:(?:[0-9A-Fa-f]{4}|[0-9A-Fa-f]{8}):)?[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]|index:[0-9]{1,3})$"
)


@dataclass(frozen=True, slots=True)
class GpuWorkerSettings:
    """Selection and runtime bounds for the isolated accelerator process."""

    device_selector: str = "auto"
    minimum_total_memory_mib: int = 4096
    minimum_compute_major: int = 7
    minimum_compute_minor: int = 0
    initial_batch_size: int = 8
    maximum_oom_reductions: int = 3
    model_id: UUID | None = None
    model_cache_root: Path = field(
        default_factory=lambda: (Path.cwd() / "var" / "models").resolve()
    )

    def __post_init__(self) -> None:
        if _SELECTOR.fullmatch(self.device_selector) is None:
            raise ValueError("GPU device selector is invalid")
        if not 1 <= self.minimum_total_memory_mib <= 1_048_576:
            raise ValueError("GPU minimum memory is invalid")
        if not 0 <= self.minimum_compute_major <= 99 or not 0 <= self.minimum_compute_minor <= 99:
            raise ValueError("GPU minimum compute capability is invalid")
        if not 1 <= self.initial_batch_size <= 256:
            raise ValueError("GPU initial batch size is invalid")
        if not 0 <= self.maximum_oom_reductions <= 8:
            raise ValueError("GPU OOM reduction bound is invalid")
        if not self.model_cache_root.is_absolute():
            raise ValueError("GPU model cache root must be absolute")


def load_gpu_settings(environ: dict[str, str] | None = None) -> GpuWorkerSettings:
    """Load only documented non-secret GPU settings from the environment."""

    values = os.environ if environ is None else environ
    try:
        return GpuWorkerSettings(
            device_selector=values.get("AUTPLAY_GPU_DEVICE_SELECTOR", "auto"),
            minimum_total_memory_mib=int(values.get("AUTPLAY_GPU_MIN_MEMORY_MIB", "4096")),
            minimum_compute_major=int(values.get("AUTPLAY_GPU_MIN_COMPUTE_MAJOR", "7")),
            minimum_compute_minor=int(values.get("AUTPLAY_GPU_MIN_COMPUTE_MINOR", "0")),
            initial_batch_size=int(values.get("AUTPLAY_GPU_INITIAL_BATCH_SIZE", "8")),
            maximum_oom_reductions=int(values.get("AUTPLAY_GPU_MAX_OOM_REDUCTIONS", "3")),
            model_id=(
                None
                if not values.get("AUTPLAY_GPU_MODEL_ID")
                else UUID(values["AUTPLAY_GPU_MODEL_ID"])
            ),
            model_cache_root=Path(
                values.get(
                    "AUTPLAY_GPU_MODEL_CACHE_ROOT",
                    str((Path.cwd() / "var" / "models").resolve()),
                )
            ),
        )
    except ValueError as error:
        raise ValueError("invalid GPU worker configuration") from error


__all__ = ("GpuWorkerSettings", "load_gpu_settings")
