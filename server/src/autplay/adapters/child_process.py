"""Secret-free launch configuration for the fixed, process-isolated Vault worker."""

import os
import sys


def vault_child_launch() -> tuple[list[str], dict[str, str]]:
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
    return [executable, "-I", "-m", "autplay.adapters.filesystem.vault_child"], environment
