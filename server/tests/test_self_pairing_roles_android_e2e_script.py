from __future__ import annotations

import asyncio
import hashlib
import importlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_script() -> ModuleType:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        return importlib.import_module("self_pairing_roles_android_e2e")
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
    apk = repository / "apps" / "android" / "build" / "outputs" / "apk"
    (apk / "debug").mkdir(parents=True)
    (apk / "androidTest" / "debug").mkdir(parents=True)
    (repository / "gradlew.bat").write_bytes(b"gradle")
    (apk / "debug" / "android-debug.apk").write_bytes(b"target")
    (apk / "androidTest" / "debug" / "android-debug-androidTest.apk").write_bytes(b"test")
    java.mkdir()
    return repository, android, java


def test_build_installs_only_side_by_side_qa_packages(
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
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    _, _, _, hashes = script._build_and_install(java, android, "private-device")

    gradle = next(call for call in calls if call[0].endswith("gradlew.bat"))
    assert "-Pautplay.qaSideBySide=true" in gradle
    installs = [call for call in calls if "install" in call]
    assert len(installs) == 2
    assert all(script.PRODUCTION_APPLICATION_ID not in call for call in calls)
    assert hashes == {
        "qa_apk": hashlib.sha256(b"target").hexdigest(),
        "qa_test_apk": hashlib.sha256(b"test").hexdigest(),
    }


def test_role_run_clears_only_qa_and_passes_no_ceremony_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, android, _ = _layout(tmp_path)
    monkeypatch.setattr(script, "REPOSITORY_ROOT", repository)
    adb = android / "platform-tools" / "adb.exe"
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if "pm" in arguments:
            return subprocess.CompletedProcess(arguments, 0, "Success\n", "")
        return subprocess.CompletedProcess(arguments, 0, "OK (1 test)\n", "")

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    script._run_role(adb, "private-device", 12345)

    assert calls[0][-3:] == ["pm", "clear", script.QA_APPLICATION_ID]
    instrumentation = calls[1]
    assert instrumentation[-1] == ("app.autplay.qa.test/androidx.test.runner.AndroidJUnitRunner")
    joined = " ".join(instrumentation).lower()
    assert "secret" not in joined
    assert "bearer" not in joined
    assert "account_id" not in joined
    assert script.PRODUCTION_APPLICATION_ID not in calls[0]


def test_lost_exchange_run_force_stops_then_resumes_with_only_loopback_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, android, _ = _layout(tmp_path)
    monkeypatch.setattr(script, "REPOSITORY_ROOT", repository)
    adb = android / "platform-tools" / "adb.exe"
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if "pm" in arguments:
            return subprocess.CompletedProcess(arguments, 0, "Success\n", "")
        if "force-stop" in arguments or "pidof" in arguments:
            return subprocess.CompletedProcess(arguments, 0, "", "")
        return subprocess.CompletedProcess(arguments, 0, "OK (1 test)\n", "")

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    process_death = script._run_lost_exchange_role(adb, "private-device", 12345)

    assert calls[0][-3:] == ["pm", "clear", script.QA_APPLICATION_ID]
    staged = calls[1]
    assert script.LOST_EXCHANGE_STAGE_SELECTOR in staged
    assert staged[staged.index("selfPairingE2eBaseUrl") + 1] == "http://127.0.0.1:12345"
    assert calls[2][-3:] == ["am", "force-stop", script.QA_APPLICATION_ID]
    assert calls[3][-2:] == ["pidof", script.QA_APPLICATION_ID]
    resumed = calls[4]
    assert script.LOST_EXCHANGE_RESUME_SELECTOR in resumed
    assert resumed[resumed.index("selfPairingE2eBaseUrl") + 1] == "http://127.0.0.1:12345"
    for instrumentation in (staged, resumed):
        joined = " ".join(instrumentation).lower()
        assert "secret" not in joined
        assert "bearer" not in joined
        assert "account_id" not in joined
    assert script.PRODUCTION_APPLICATION_ID not in calls[0]
    assert process_death == {
        "force_stop_issued": True,
        "process_exit_confirmed": True,
        "fresh_instrumentation_resume": True,
    }


def test_wrapper_drops_one_committed_self_service_exchange_reply() -> None:
    async def scenario() -> tuple[list[int], int, int, list[str]]:
        forwarded: list[str] = []

        async def app(scope: dict[str, Any], _receive: Any, send: Any) -> None:
            forwarded.append(scope["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b'{"status":"ok"}'})

        wrapper = script._OneShotRoleHandoffApp(app)
        wrapper.drop_next_successful_exchange_reply()

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def request(path: str) -> int:
            messages: list[dict[str, Any]] = []

            async def send(message: dict[str, Any]) -> None:
                messages.append(message)

            await wrapper(
                {"type": "http", "method": "POST", "path": path},
                receive,
                send,
            )
            return int(
                next(
                    message["status"]
                    for message in messages
                    if message["type"] == "http.response.start"
                )
            )

        statuses = [
            await request("/api/v1/other/exchange"),
            await request("/api/v1/pairing/self-service/id/exchange"),
            await request("/api/v1/pairing/self-service/id/exchange"),
        ]
        return (
            statuses,
            wrapper.exchange_attempts,
            wrapper.committed_exchange_replies_dropped,
            forwarded,
        )

    statuses, attempts, dropped, forwarded = asyncio.run(scenario())
    assert statuses == [200, 503, 200]
    assert attempts == 2
    assert dropped == 1
    assert forwarded == [
        "/api/v1/other/exchange",
        "/api/v1/pairing/self-service/id/exchange",
        "/api/v1/pairing/self-service/id/exchange",
    ]


def test_apk_identity_mismatch_stops_before_install(
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
        script._build_and_install(java, android, "private-device")
    assert not any("install" in call for call in calls)


def test_report_identity_is_a_full_serial_digest() -> None:
    raw = "private-device"
    assert script._serial_sha256(raw) == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in script._serial_sha256(raw)
