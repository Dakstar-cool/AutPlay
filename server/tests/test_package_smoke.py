"""P01 package and dependency compatibility smoke tests."""

from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest

PACKAGE_MODULES = (
    "autplay",
    "autplay.adapters",
    "autplay.application",
    "autplay.domain",
    "autplay.entrypoints",
    "autplay.ports",
)

RUNTIME_MODULES = (
    "alembic",
    "fastapi",
    "psycopg",
    "pydantic",
    "pydantic_settings",
    "sqlalchemy",
)

ACCELERATOR_PREFIXES = (
    "cupy",
    "cuda",
    "jax",
    "nvidia",
    "onnxruntime",
    "tensorflow",
    "torch",
    "transformers",
)


@pytest.mark.parametrize("module_name", PACKAGE_MODULES)
def test_package_boundary_imports(module_name: str) -> None:
    """Every approved architecture package imports without feature code."""
    module = importlib.import_module(module_name)

    assert module.__all__ == ()


@pytest.mark.parametrize("module_name", RUNTIME_MODULES)
def test_pinned_runtime_baseline_imports(module_name: str) -> None:
    """The agreed P01 server dependency set is mutually import-compatible."""
    importlib.import_module(module_name)


def test_autplay_import_has_no_accelerator_side_effects() -> None:
    """Importing the CPU package must not load CUDA or ML runtimes."""
    importlib.import_module("autplay")

    loaded_accelerators = sorted(
        module_name
        for module_name in sys.modules
        if module_name.split(".", maxsplit=1)[0] in ACCELERATOR_PREFIXES
    )
    assert loaded_accelerators == []


def test_isolated_gpu_project_is_not_installed_in_cpu_environment() -> None:
    """The server lock cannot accidentally compose or import the optional GPU project."""

    assert importlib.util.find_spec("autplay_gpu") is None
