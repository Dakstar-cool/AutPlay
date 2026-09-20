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
        return importlib.import_module("p14_android_server_e2e")
    finally:
        sys.path.remove(str(scripts))


script = _load_script()


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    repository = tmp_path / "repo"
    android = tmp_path / "android"
    java = tmp_path / "java"
    (android / "platform-tools").mkdir(parents=True)
    (android / "build-tools" / "36.1.0").mkdir(parents=True)
    (android / "platform-tools" / "adb.exe").write_bytes(b"adb")
    (android / "build-tools" / "36.1.0" / "aapt.exe").write_bytes(b"aapt")
    (repository / "apps" / "android" / "build" / "outputs" / "apk" / "debug").mkdir(parents=True)
    (repository / "apps" / "android" / "build" / "outputs" / "apk" / "androidTest" / "debug").mkdir(
        parents=True
    )
    (repository / "gradlew.bat").write_bytes(b"gradle")
    (
        repository
        / "apps"
        / "android"
        / "build"
        / "outputs"
        / "apk"
        / "debug"
        / "android-debug.apk"
    ).write_bytes(b"target")
    (
        repository
        / "apps"
        / "android"
        / "build"
        / "outputs"
        / "apk"
        / "androidTest"
        / "debug"
        / "android-debug-androidTest.apk"
    ).write_bytes(b"test")
    java.mkdir()
    return repository, android, java


def test_joined_harness_uses_side_by_side_packages_and_no_secret_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, android, java = _layout(tmp_path)
    monkeypatch.setattr(script, "REPOSITORY_ROOT", repository)
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if "aapt.exe" in arguments[0]:
            package = (
                script.QA_TEST_APPLICATION_ID
                if "androidTest" in arguments[-1]
                else script.QA_APPLICATION_ID
            )
            return subprocess.CompletedProcess(
                arguments, 0, f"package: name='{package}' versionCode='3'\n", ""
            )
        if "instrument" in arguments:
            return subprocess.CompletedProcess(arguments, 0, "OK (1 test)\n", "")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    hashes = script._run_gradle(
        java_home=java, android_home=android, serial="private-device", port=12345
    )

    gradle = next(call for call in calls if call[0].endswith("gradlew.bat"))
    assert "-Pautplay.qaSideBySide=true" in gradle
    assert not any("Token" in argument or "token" in argument for argument in gradle)
    instrumentation = next(call for call in calls if "instrument" in call)
    assert instrumentation[-1] == "app.autplay.qa.test/androidx.test.runner.AndroidJUnitRunner"
    assert hashes == {
        "app.autplay.qa": hashlib.sha256(b"target").hexdigest(),
        "app.autplay.qa.test": hashlib.sha256(b"test").hexdigest(),
    }
    assert calls[-1][-3:] == ["reverse", "--remove", "tcp:12345"]


def test_apk_identity_mismatch_stops_before_install_and_cleans_reverse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, android, java = _layout(tmp_path)
    monkeypatch.setattr(script, "REPOSITORY_ROOT", repository)
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if "aapt.exe" in arguments[0]:
            return subprocess.CompletedProcess(
                arguments, 0, "package: name='app.autplay' versionCode='3'\n", ""
            )
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="side-by-side APK identity mismatch"):
        script._run_gradle(
            java_home=java, android_home=android, serial="private-device", port=12345
        )

    assert not any("install" in call for call in calls)
    assert calls[-1][-3:] == ["reverse", "--remove", "tcp:12345"]


def test_report_identity_is_a_full_serial_digest() -> None:
    raw = "private-device"
    assert script._serial_sha256(raw) == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in script._serial_sha256(raw)
