from __future__ import annotations

from pathlib import Path

import pytest

from autplay_gpu.settings import load_gpu_settings


def test_gpu_settings_support_auto_and_stable_manual_selection() -> None:
    assert load_gpu_settings({}).device_selector == "auto"
    assert (
        load_gpu_settings({"AUTPLAY_GPU_DEVICE_SELECTOR": "pci:00000000:01:00.0"}).device_selector
        == "pci:00000000:01:00.0"
    )
    model_cache_root = (Path.cwd() / "fixture-model-cache").resolve()
    explicit = load_gpu_settings(
        {
            "AUTPLAY_GPU_DEVICE_SELECTOR": "uuid:GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "AUTPLAY_GPU_MIN_MEMORY_MIB": "12000",
            "AUTPLAY_GPU_MIN_COMPUTE_MAJOR": "8",
            "AUTPLAY_GPU_MIN_COMPUTE_MINOR": "6",
            "AUTPLAY_GPU_MODEL_ID": "11111111-2222-3333-4444-555555555555",
            "AUTPLAY_GPU_MODEL_CACHE_ROOT": str(model_cache_root),
        }
    )
    assert explicit.minimum_total_memory_mib == 12_000
    assert (explicit.minimum_compute_major, explicit.minimum_compute_minor) == (8, 6)
    assert str(explicit.model_id) == "11111111-2222-3333-4444-555555555555"
    assert explicit.model_cache_root == model_cache_root


def test_gpu_settings_reject_ambiguous_name_or_malformed_selector() -> None:
    with pytest.raises(ValueError, match="invalid GPU worker configuration"):
        load_gpu_settings({"AUTPLAY_GPU_DEVICE_SELECTOR": "name:RTX 3060"})
