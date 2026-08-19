import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_release_audit() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root))
    try:
        return importlib.import_module("scripts.p14_release_audit")
    finally:
        sys.path.remove(str(repository_root))


p14_release_audit = _load_release_audit()


def test_android_smoke_requires_the_dev_signed_rc_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p14_release_audit, "REPOSITORY_ROOT", tmp_path)

    debug_apk = tmp_path / "apps" / "android" / "build" / "outputs" / "apk" / "debug"
    debug_apk.mkdir(parents=True)
    (debug_apk / "android-debug.apk").write_bytes(b"different-debug-signing-key")

    with pytest.raises(RuntimeError, match="dev-signed RC APK is missing"):
        p14_release_audit._android_smoke_apk(tmp_path, tmp_path / "apksigner.bat")


def test_android_smoke_selects_the_dev_signed_rc_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p14_release_audit, "REPOSITORY_ROOT", tmp_path)
    expected = tmp_path / "docs" / "release" / "artifacts" / "autplay-rc1-dev-signed.apk"
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"dev-signed-rc")
    expected_hash = hashlib.sha256(expected.read_bytes()).hexdigest()
    evidence_directory = tmp_path / "evidence"
    evidence_directory.mkdir()
    (evidence_directory / "P14_RELEASE_BUILD.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "android": {"dev_signed_rc_sha256": expected_hash},
                "device": {"physical_samsung_a55": True},
            }
        ),
        encoding="utf-8",
    )
    apksigner = tmp_path / "apksigner.bat"
    apksigner.write_text("test", encoding="utf-8")
    monkeypatch.setattr(
        p14_release_audit,
        "_run",
        lambda _arguments: (
            "Verified using v2 scheme (APK Signature Scheme v2): true\n"
            "Verified using v3 scheme (APK Signature Scheme v3): true\n"
        ),
    )

    assert p14_release_audit._android_smoke_apk(evidence_directory, apksigner) == expected

    expected.write_bytes(b"replaced-after-build")
    with pytest.raises(RuntimeError, match="does not match P14 release-build evidence"):
        p14_release_audit._android_smoke_apk(evidence_directory, apksigner)


def test_release_status_passes_only_for_a_physical_samsung_a55() -> None:
    assert p14_release_audit._release_status({"physical_samsung_a55": True}) == "PASS"
    assert p14_release_audit._release_status({"physical_samsung_a55": False}) == (
        "PASS_WITH_PHYSICAL_DEVICE_GATE_REPORTED_SEPARATELY"
    )


def test_physical_install_failure_never_uninstalls_or_clears_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p14_release_audit, "REPOSITORY_ROOT", tmp_path)
    adb = tmp_path / "adb.exe"
    apk = tmp_path / "rc.apk"
    adb.write_bytes(b"adb")
    apk.write_bytes(b"apk")
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], *, env: dict[str, str] | None = None) -> str:
        del env
        calls.append(arguments)
        if arguments[-1] == "devices":
            return "List of devices attached\nphysical-serial\tdevice\n"
        if "getprop" in arguments:
            property_name = arguments[-1]
            return {
                "ro.product.manufacturer": "samsung",
                "ro.product.model": "SM-A556E",
                "ro.product.device": "a55x",
                "ro.build.version.sdk": "36",
                "ro.product.cpu.abi": "arm64-v8a",
                "ro.hardware": "s5e8845",
                "ro.kernel.qemu": "0",
            }[property_name]
        if "install" in arguments:
            raise subprocess.CalledProcessError(
                1,
                arguments,
                stderr="INSTALL_FAILED_UPDATE_INCOMPATIBLE",
            )
        raise AssertionError(f"unexpected command: {arguments}")

    monkeypatch.setattr(p14_release_audit, "_run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        p14_release_audit._android_device_evidence(
            adb,
            apk,
            device_serial="physical-serial",
            allow_disposable_emulator_reinstall=True,
        )

    flattened = " ".join(part for arguments in calls for part in arguments)
    assert "uninstall" not in flattened
    assert "pm clear" not in flattened


def test_main_returns_nonzero_when_physical_gate_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        p14_release_audit,
        "run",
        lambda *_args, **_kwargs: {
            "status": "PASS_WITH_PHYSICAL_DEVICE_GATE_REPORTED_SEPARATELY",
            "artifacts": [],
            "android_device": {"status": "UNAVAILABLE"},
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "p14_release_audit.py",
            "--output-directory",
            str(tmp_path),
            "--java-home",
            str(tmp_path),
            "--android-home",
            str(tmp_path),
        ],
    )

    assert p14_release_audit.main() == 1
