"""Restore process logging after tests exercise real server entry points."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def restore_root_logging() -> Iterator[None]:
    """An entry point's captured stderr must not outlive its owning test."""
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
