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
        return importlib.import_module("self_pairing_device_quota_android_e2e")
    finally:
        sys.path.remove(str(scripts))


script = _load_script()


def test_android_run_clears_only_qa_and_passes_only_loopback_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    adb = tmp_path / "adb.exe"
    adb.write_bytes(b"adb")
    monkeypatch.setattr(script, "REPOSITORY_ROOT", repository)
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if "pm" in arguments:
            return subprocess.CompletedProcess(arguments, 0, "Success\n", "")
        if "force-stop" in arguments or "pidof" in arguments:
            return subprocess.CompletedProcess(arguments, 0, "", "")
        return subprocess.CompletedProcess(arguments, 0, "OK (1 test)\n", "")

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    process_death = script._run_android(adb, "private-device", 12345)

    assert calls[0][-3:] == ["pm", "clear", script.QA_APPLICATION_ID]
    instrumentation = calls[1]
    assert instrumentation[-1] == ("app.autplay.qa.test/androidx.test.runner.AndroidJUnitRunner")
    assert instrumentation.count("-e") == 2
    assert instrumentation[instrumentation.index("selfPairingQuotaE2eBaseUrl") + 1] == (
        "http://127.0.0.1:12345"
    )
    joined = " ".join(instrumentation).lower()
    assert "secret" not in joined
    assert "bearer" not in joined
    assert "account_id" not in joined
    assert script.PRODUCTION_APPLICATION_ID not in calls[0]
    assert calls[2][-3:] == ["am", "force-stop", script.QA_APPLICATION_ID]
    assert calls[3][-2:] == ["pidof", script.QA_APPLICATION_ID]
    resumed = calls[4]
    assert script.RESUME_TEST_SELECTOR in resumed
    assert resumed[resumed.index("selfPairingQuotaE2eBaseUrl") + 1] == ("http://127.0.0.1:12345")
    ui_instrumentation = calls[5]
    assert ui_instrumentation[-1] == ("app.autplay.qa.test/androidx.test.runner.AndroidJUnitRunner")
    assert script.UI_TEST_SELECTOR in ui_instrumentation
    assert "selfPairingQuotaE2eBaseUrl" not in ui_instrumentation
    assert "http://127.0.0.1:12345" not in ui_instrumentation
    assert process_death == {
        "force_stop_issued": True,
        "process_exit_confirmed": True,
        "fresh_instrumentation_resume": True,
    }


def test_ui_evidence_pull_is_bounded_and_manifest_binds_every_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    adb = tmp_path / "adb.exe"
    adb.write_bytes(b"adb")
    monkeypatch.setattr(script, "REPOSITORY_ROOT", repository)
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        name = arguments[-2].rsplit("/", 1)[-1]
        Path(arguments[-1]).write_bytes(name.encode())
        return subprocess.CompletedProcess(arguments, 0, "pulled\n", "")

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    output = tmp_path / "evidence" / "report.json"
    hashes = script._pull_ui_evidence(adb, "private-device", output)
    output.write_text("{}\n", encoding="utf-8")
    script._write_hash_manifest(output, hashes)

    expected_keys = {f"self-pairing-device-quota-ui/{name}" for name in script.UI_SCREENSHOTS}
    assert set(hashes) == expected_keys
    assert len(calls) == len(script.UI_SCREENSHOTS)
    assert all(script.QA_APPLICATION_ID in call[-2] for call in calls)
    manifest = output.with_suffix(".sha256.json").read_text(encoding="utf-8")
    assert output.name in manifest
    assert all(key in manifest for key in expected_keys)


def test_wrapper_counts_only_exchange_posts() -> None:
    async def scenario() -> tuple[int, list[str]]:
        forwarded: list[str] = []

        async def app(scope: dict[str, Any], _receive: Any, send: Any) -> None:
            forwarded.append(scope["path"])
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        wrapper = script._QuotaHandoffApp(app)

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message: dict[str, Any]) -> None:
            return None

        for method, path in (
            ("POST", "/api/v1/pairing/self-service/id/exchange"),
            ("GET", "/api/v1/pairing/self-service/id/exchange"),
            ("POST", "/api/v1/pairing/self-service/id/poll"),
            ("POST", "/api/v1/pairing/self-service/id/exchange"),
        ):
            await wrapper({"type": "http", "method": method, "path": path}, receive, send)
        return wrapper.exchange_attempts, forwarded

    attempts, forwarded = asyncio.run(scenario())
    assert attempts == 2
    assert len(forwarded) == 4


def test_report_identity_is_a_full_serial_digest() -> None:
    raw = "private-device"
    assert script._serial_sha256(raw) == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in script._serial_sha256(raw)
