"""Secret-free launch configuration for the fixed, process-isolated Vault worker."""

import os
import re
import sys


def vault_child_launch() -> tuple[list[str], dict[str, str]]:
    return _python_child_launch("autplay.adapters.filesystem.vault_child")


def provider_maintenance_child_launch() -> tuple[list[str], dict[str, str]]:
    return _python_child_launch("autplay.adapters.filesystem.provider_maintenance_child")


def vault_inventory_child_launch() -> tuple[list[str], dict[str, str]]:
    return _python_child_launch("autplay.adapters.filesystem.vault_inventory_child")


def ingest_child_launch() -> tuple[list[str], dict[str, str]]:
    arguments, environment = _python_child_launch("autplay.adapters.filesystem.ingest_child")
    environment["PATH"] = os.environ.get("PATH", "")
    return arguments, environment


def metadata_child_launch(*, proxy: str | None = None) -> tuple[list[str], dict[str, str]]:
    arguments, environment = _python_child_launch("autplay.adapters.filesystem.metadata_child")
    environment["PATH"] = os.environ.get("PATH", "")
    if proxy is not None:
        environment["AUTPLAY_METADATA_PROXY"] = proxy
    return arguments, environment


def training_child_launch() -> tuple[list[str], dict[str, str]]:
    """Launch the optional training package from its own pinned environment."""

    return _python_child_launch("autplay_sona_training.controlled_child")


def provider_child_launch() -> tuple[list[str], dict[str, str]]:
    arguments, environment = _python_child_launch("autplay.adapters.filesystem.provider_child")
    # Only the configured provider proxy and executable search path are inherited.
    # Application database/session credentials never enter the child environment.
    environment["PATH"] = os.environ.get("PATH", "")
    if proxy := os.environ.get("AUTPLAY_MUSIC_PROXY"):
        environment["AUTPLAY_MUSIC_PROXY"] = proxy
    return arguments, environment


def discovery_child_launch(client_id: str) -> tuple[list[str], dict[str, str]]:
    """Pass only the explicitly configured Jamendo credential to its fixed child."""
    if re.fullmatch(r"[A-Za-z0-9_-]{4,100}", client_id) is None:
        raise ValueError("discovery_provider_configuration_invalid")
    arguments, environment = _python_child_launch("autplay.adapters.filesystem.discovery_child")
    environment["AUTPLAY_JAMENDO_CLIENT_ID"] = client_id
    return arguments, environment


def _python_child_launch(module: str) -> tuple[list[str], dict[str, str]]:
    environment = {"LANG": "C", "LC_ALL": "C"}
    executable = sys.executable
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
            if value := os.environ.get(name):
                environment[name] = value
        # CPython multiprocessing uses the same workaround (bpo-35797).
        # Waiting on a venv redirector would not prove its actual child exited.
        base = getattr(sys, "_base_executable", None)
        if not isinstance(base, str) or not base:
            raise RuntimeError("vault_child_interpreter_unavailable")
        if os.path.normcase(base) != os.path.normcase(executable):
            environment["__PYVENV_LAUNCHER__"] = executable
            executable = base
    return [executable, "-I", "-m", module], environment
