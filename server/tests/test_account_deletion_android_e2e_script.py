from __future__ import annotations

import asyncio
import hashlib
import importlib
import subprocess
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_script() -> ModuleType:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        return importlib.import_module("account_deletion_android_e2e")
    finally:
        sys.path.remove(str(scripts))


script = _load_script()


def test_android_run_clears_only_qa_and_passes_no_deletion_secret(
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
        return subprocess.CompletedProcess(arguments, 0, "OK (1 test)\n", "")

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    script._run_android(adb, "private-device", 12345)

    assert calls[0][-3:] == ["pm", "clear", script.QA_APPLICATION_ID]
    instrumentation = calls[1]
    assert instrumentation[-1] == "app.autplay.qa.test/androidx.test.runner.AndroidJUnitRunner"
    joined = " ".join(instrumentation).lower()
    assert "account_id" not in joined
    assert "recovery_code" not in joined
    assert "access_token" not in joined
    assert "refresh_token" not in joined
    assert script.PRODUCTION_APPLICATION_ID not in calls[0]


def test_wrapper_handoff_is_one_use_and_cancel_reply_is_recovered() -> None:
    async def scenario() -> tuple[list[int], dict[str, object]]:
        async def app(_scope: Any, _receive: Any, send: Any) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"cache-control", b"no-store")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"status":"ok"}'})

        wrapper = script._AccountDeletionE2eApp(
            app,
            None,
            uuid.uuid4(),
            uuid.uuid4(),
            {"account_id": "private", "recovery_code": "secret", "identity_epoch": 1},
        )

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        statuses: list[int] = []

        async def request(path: str, method: str) -> None:
            messages: list[dict[str, Any]] = []

            async def send(message: dict[str, Any]) -> None:
                messages.append(message)

            await wrapper(
                {"type": "http", "path": path, "method": method},
                receive,
                send,
            )
            start = next(
                message for message in messages if message["type"] == "http.response.start"
            )
            statuses.append(int(start["status"]))

        await request("/account-deletion-e2e/one-shot-handoff", "GET")
        await request("/account-deletion-e2e/one-shot-handoff", "GET")
        await request("/api/v1/deletion/cancel/commit", "POST")
        await request("/api/v1/deletion/cancel/outcome", "POST")
        return statuses, wrapper.evidence

    statuses, evidence = asyncio.run(scenario())
    assert statuses == [200, 410, 503, 200]
    assert evidence["cancel_replies_dropped"] == 1
    assert evidence["cancel_outcome_calls"] == 1


def test_report_identity_is_a_full_serial_digest() -> None:
    raw = "private-device"
    assert script._serial_sha256(raw) == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in script._serial_sha256(raw)
