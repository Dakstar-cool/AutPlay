from __future__ import annotations

import hashlib
import importlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        return importlib.import_module("test_l1_process_death")
    finally:
        sys.path.remove(str(scripts))


script = _load_script()


def test_physical_device_requires_side_by_side_qa_package() -> None:
    with pytest.raises(RuntimeError, match="isolated --qa-side-by-side"):
        script._require_safe_target(is_emulator=False, qa_side_by_side=False)

    script._require_safe_target(is_emulator=False, qa_side_by_side=True)
    script._require_safe_target(is_emulator=True, qa_side_by_side=False)


def test_apk_identity_is_checked_before_physical_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apk = tmp_path / "target.apk"
    apk.write_bytes(b"apk")
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(
            arguments,
            0,
            "package: name='app.autplay.qa' versionCode='3'\n",
            "",
        )

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    script._assert_apk_package(tmp_path / "aapt", apk, "app.autplay.qa")
    with pytest.raises(RuntimeError, match="APK identity mismatch"):
        script._assert_apk_package(tmp_path / "aapt", apk, "app.autplay")

    assert len(calls) == 2


def test_receipt_identity_uses_full_serial_digest() -> None:
    raw = "private-device"
    digest = script._serial_sha256(raw)
    assert digest == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in digest
