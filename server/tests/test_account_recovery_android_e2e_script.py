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
        return importlib.import_module("account_recovery_android_e2e")
    finally:
        sys.path.remove(str(scripts))


script = _load_script()


def test_android_run_clears_only_qa_and_passes_no_recovery_secret(
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
    assert instrumentation[-1] == ("app.autplay.qa.test/androidx.test.runner.AndroidJUnitRunner")
    joined = " ".join(instrumentation).lower()
    assert "recovery_code" not in joined
    assert "account_id" not in joined
    assert "bearer" not in joined
    assert script.PRODUCTION_APPLICATION_ID not in calls[0]


def test_wrapper_drops_only_successful_commit_reply_then_forwards_outcome() -> None:
    async def scenario() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
        async def app(_scope: Any, _receive: Any, send: Any) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"cache-control", b"no-store")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"status":"ok"}'})

        wrapper = script._RecoveryE2eApp(app, uuid.uuid4(), "0" * 32)

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        commit: list[dict[str, Any]] = []

        async def send_commit(message: dict[str, Any]) -> None:
            commit.append(message)

        await wrapper(
            {"type": "http", "path": "/api/v1/recovery/commit", "method": "POST"},
            receive,
            send_commit,
        )
        outcome: list[dict[str, Any]] = []

        async def send_outcome(message: dict[str, Any]) -> None:
            outcome.append(message)

        await wrapper(
            {"type": "http", "path": "/api/v1/recovery/outcome", "method": "POST"},
            receive,
            send_outcome,
        )
        return commit, outcome, wrapper.evidence

    commit, outcome, evidence = asyncio.run(scenario())
    assert commit[0]["status"] == 503
    assert outcome[0]["status"] == 200
    assert evidence == {"committed_replies_dropped": 1, "outcome_calls": 1}


def test_report_identity_is_a_full_serial_digest() -> None:
    raw = "private-device"
    assert script._serial_sha256(raw) == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in script._serial_sha256(raw)
