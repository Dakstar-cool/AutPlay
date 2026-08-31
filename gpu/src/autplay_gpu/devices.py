"""Bounded NVIDIA inventory and deterministic device selection."""

from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from autplay.domain.enrichment import AcceleratorDevice, AcceleratorSelection

MAX_NVIDIA_SMI_OUTPUT_BYTES: Final = 64 * 1024
_UUID = re.compile(r"^GPU-[A-Za-z0-9-]{8,100}$")
_PCI = re.compile(r"^(?:(?:[0-9A-Fa-f]{4}|[0-9A-Fa-f]{8}):)?[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]$")


class AcceleratorUnavailable(RuntimeError):
    """No compatible accelerator can be selected."""


class AcceleratorProbeError(RuntimeError):
    """The bounded hardware probe returned invalid or incomplete data."""


@dataclass(frozen=True, slots=True)
class DeviceRequirements:
    """Minimum accelerator requirements declared by one approved model runtime."""

    minimum_total_memory_mib: int = 1
    minimum_compute_capability: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.minimum_total_memory_mib < 1:
            raise ValueError("minimum_total_memory_mib must be positive")
        if self.minimum_compute_capability is not None and any(
            part < 0 for part in self.minimum_compute_capability
        ):
            raise ValueError("minimum_compute_capability is invalid")


type CommandRunner = Callable[[Sequence[str], float], str]


class NvidiaSmiInventory:
    """Discover every visible NVIDIA GPU and select one by stable identity."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout_seconds: float = 5.0,
        requirements: DeviceRequirements | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        if not 0.1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0.1 and 30")
        self._executable = executable or shutil.which("nvidia-smi") or "nvidia-smi"
        self._timeout_seconds = timeout_seconds
        self._requirements = requirements or DeviceRequirements()
        self._runner = runner or _run_command

    def list_devices(self) -> tuple[AcceleratorDevice, ...]:
        """Return a stable inventory without model/runtime imports."""

        fields = "index,uuid,pci.bus_id,name,memory.total,memory.free,compute_cap,driver_version"
        output = self._runner(
            (
                self._executable,
                f"--query-gpu={fields}",
                "--format=csv,noheader,nounits",
            ),
            self._timeout_seconds,
        )
        if len(output.encode("utf-8")) > MAX_NVIDIA_SMI_OUTPUT_BYTES:
            raise AcceleratorProbeError("accelerator inventory output exceeds its bound")
        devices = tuple(_parse_device(row) for row in csv.reader(io.StringIO(output)))
        if not devices:
            raise AcceleratorUnavailable("no NVIDIA accelerator is visible")
        if len({item.device_uuid for item in devices}) != len(devices):
            raise AcceleratorProbeError("accelerator inventory contains duplicate UUIDs")
        return tuple(sorted(devices, key=lambda item: item.index))

    def select(self, selector: str = "auto") -> AcceleratorSelection:
        """Select a compatible device using auto or an explicit stable selector."""

        devices = self.list_devices()
        compatible = tuple(item for item in devices if self._is_compatible(item))
        if selector == "auto":
            if not compatible:
                raise AcceleratorUnavailable("no accelerator satisfies model requirements")
            selected = max(
                compatible,
                key=lambda item: (
                    _compute_capability(item.compute_capability),
                    item.total_memory_mib,
                    item.free_memory_mib,
                    item.device_uuid,
                ),
            )
            return AcceleratorSelection(
                selector="auto",
                device=selected,
                reason="highest-compatible-compute-vram",
            )
        selected = _explicit_selection(devices, selector)
        if not self._is_compatible(selected):
            raise AcceleratorUnavailable("selected accelerator does not satisfy model requirements")
        return AcceleratorSelection(selector=selector, device=selected, reason="explicit-selector")

    def _is_compatible(self, device: AcceleratorDevice) -> bool:
        if device.total_memory_mib < self._requirements.minimum_total_memory_mib:
            return False
        required = self._requirements.minimum_compute_capability
        return required is None or _compute_capability(device.compute_capability) >= required


def _run_command(command: Sequence[str], timeout_seconds: float) -> str:
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout_seconds,
            shell=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, UnicodeError) as error:
        raise AcceleratorUnavailable("NVIDIA inventory probe failed") from error
    return completed.stdout


def _parse_device(row: list[str]) -> AcceleratorDevice:
    if len(row) != 8:
        raise AcceleratorProbeError("accelerator inventory row has an invalid field count")
    values = [value.strip() for value in row]
    try:
        index = int(values[0])
        total = int(values[4])
        free = int(values[5])
    except ValueError as error:
        raise AcceleratorProbeError("accelerator inventory contains invalid numbers") from error
    if index < 0 or total < 1 or not 0 <= free <= total:
        raise AcceleratorProbeError("accelerator inventory contains invalid resource values")
    if _UUID.fullmatch(values[1]) is None or _PCI.fullmatch(values[2]) is None:
        raise AcceleratorProbeError("accelerator inventory contains invalid stable identifiers")
    if not values[3] or not values[7]:
        raise AcceleratorProbeError("accelerator inventory omits required identity values")
    capability = None if values[6] in {"", "N/A", "[N/A]"} else values[6]
    if capability is not None:
        _compute_capability(capability)
    return AcceleratorDevice(
        vendor="NVIDIA",
        index=index,
        device_uuid=values[1],
        pci_bus_id=values[2].lower(),
        name=values[3],
        total_memory_mib=total,
        free_memory_mib=free,
        compute_capability=capability,
        driver_version=values[7],
    )


def _compute_capability(value: str | None) -> tuple[int, int]:
    if value is None:
        return 0, 0
    parts = value.split(".")
    if len(parts) != 2 or any(not part.isdigit() for part in parts):
        raise AcceleratorProbeError("compute capability is invalid")
    return int(parts[0]), int(parts[1])


def _explicit_selection(devices: tuple[AcceleratorDevice, ...], selector: str) -> AcceleratorDevice:
    if selector.startswith("uuid:"):
        value = selector.removeprefix("uuid:")
        matches = [item for item in devices if item.device_uuid == value]
    elif selector.startswith("pci:"):
        value = selector.removeprefix("pci:").lower()
        matches = [item for item in devices if item.pci_bus_id == value]
    elif selector.startswith("index:"):
        value = selector.removeprefix("index:")
        if not value.isdigit():
            raise ValueError("accelerator index selector is invalid")
        matches = [item for item in devices if item.index == int(value)]
    else:
        raise ValueError("selector must be auto, uuid:<id>, pci:<id> or index:<n>")
    if len(matches) != 1:
        raise AcceleratorUnavailable("explicit accelerator selector did not match one device")
    return matches[0]


__all__ = (
    "AcceleratorProbeError",
    "AcceleratorUnavailable",
    "DeviceRequirements",
    "NvidiaSmiInventory",
)
