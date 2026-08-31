from __future__ import annotations

import pytest

from autplay_gpu.devices import (
    AcceleratorUnavailable,
    DeviceRequirements,
    NvidiaSmiInventory,
)

INVENTORY = "\n".join(
    (
        "0, GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee, 00000000:01:00.0, "
        "NVIDIA RTX 3060, 12288, 9000, 8.6, 600.1",
        "1, GPU-11111111-2222-3333-4444-555555555555, 0000:02:00.0, "
        "NVIDIA RTX Future, 24576, 18000, 9.0, 600.1",
    )
)


def _runner(command: object, timeout: float) -> str:
    del command, timeout
    return INVENTORY


def test_auto_selects_highest_compatible_device_and_records_reason() -> None:
    selected = NvidiaSmiInventory(runner=_runner).select("auto")
    assert selected.device.name == "NVIDIA RTX Future"
    assert selected.device.device_uuid == "GPU-11111111-2222-3333-4444-555555555555"
    assert selected.reason == "highest-compatible-compute-vram"


@pytest.mark.parametrize(
    ("selector", "index"),
    (
        ("uuid:GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", 0),
        ("pci:00000000:01:00.0", 0),
        ("pci:0000:02:00.0", 1),
        ("index:0", 0),
    ),
)
def test_explicit_stable_selectors(selector: str, index: int) -> None:
    assert NvidiaSmiInventory(runner=_runner).select(selector).device.index == index


def test_selected_device_must_satisfy_model_requirements() -> None:
    inventory = NvidiaSmiInventory(
        runner=_runner,
        requirements=DeviceRequirements(
            minimum_total_memory_mib=20_000,
            minimum_compute_capability=(9, 0),
        ),
    )
    with pytest.raises(AcceleratorUnavailable):
        inventory.select("index:0")
    assert inventory.select("auto").device.index == 1


def test_inventory_rejects_malformed_probe_output() -> None:
    inventory = NvidiaSmiInventory(runner=lambda command, timeout: "broken")
    with pytest.raises(Exception, match="field count"):
        inventory.list_devices()
