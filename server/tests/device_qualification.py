"""Explicit synthetic qualification policy; never imported by production code."""

from __future__ import annotations

import pytest
from autplay.domain import device_capability_state, device_compatibility, sync_binding_v2


def set_device_qualification(
    monkeypatch: pytest.MonkeyPatch,
    *,
    families: frozenset[str] = frozenset(),
    platforms: frozenset[str] = frozenset(),
) -> None:
    """Keep negative admission evidence independent of the released registry."""
    for module in (device_compatibility, device_capability_state):
        monkeypatch.setattr(module, "IMPLEMENTED_AND_QUALIFIED_FAMILIES", families)
    for module in (sync_binding_v2, device_capability_state):
        monkeypatch.setattr(module, "IMPLEMENTED_AND_QUALIFIED_PLATFORMS", platforms)
