from __future__ import annotations

import sys
import time
from pathlib import Path

from autplay_codex.checks import CheckRunner, checks_passed
from autplay_codex.models import CheckStatus


def test_check_runner_executes_without_shell_and_stops_on_failure(tmp_path: Path) -> None:
    runner = CheckRunner(tmp_path, timeout_seconds=10)

    results = runner.run(
        (
            (sys.executable, "-c", "print('ok')"),
            (sys.executable, "-c", "raise SystemExit(7)"),
            (sys.executable, "-c", "raise SystemExit(9)"),
        )
    )

    assert [result.status for result in results] == [CheckStatus.PASSED, CheckStatus.FAILED]
    assert results[-1].return_code == 7
    assert not checks_passed(results)


def test_check_output_redacts_secrets_and_absolute_paths(tmp_path: Path) -> None:
    runner = CheckRunner(tmp_path, timeout_seconds=10)
    script = f"print('token=abc123 {tmp_path}')"

    result = runner.run(((sys.executable, "-c", script),))[0]

    assert "abc123" not in result.details
    assert str(tmp_path) not in result.details
    assert "<redacted>" in result.details
    assert "<repo>" in result.details


def test_check_output_redacts_database_dsn_credentials(tmp_path: Path) -> None:
    runner = CheckRunner(tmp_path, timeout_seconds=10)
    script = "print('postgresql+psycopg://owner:topsecret@db.internal/autplay')"

    result = runner.run(((sys.executable, "-c", script),))[0]

    assert "topsecret" not in result.details
    assert "postgresql+psycopg://<redacted>@db.internal/autplay" in result.details


def test_check_output_is_bounded_while_process_is_running(tmp_path: Path) -> None:
    runner = CheckRunner(tmp_path, timeout_seconds=10)
    script = "import sys; sys.stdout.write('A' * 2_000_000)"

    result = runner.run(((sys.executable, "-c", script),))[0]

    assert result.status is CheckStatus.PASSED
    assert len(result.details) < 8_000
    assert "capture truncated" in result.details


def test_check_timeout_stops_process_and_returns_bounded_failure(tmp_path: Path) -> None:
    runner = CheckRunner(tmp_path, timeout_seconds=1)
    script = "import time; print('started', flush=True); time.sleep(5)"

    result = runner.run(((sys.executable, "-c", script),))[0]

    assert result.status is CheckStatus.FAILED
    assert result.return_code is None
    assert "exceeded 1s timeout" in result.details
    assert "started" in result.details


def test_check_timeout_stops_descendant_processes(tmp_path: Path) -> None:
    runner = CheckRunner(tmp_path, timeout_seconds=1)
    marker = tmp_path / "child-survived.txt"
    child_script = (
        "import pathlib,signal,time; "
        "signal.signal(getattr(signal, 'SIGBREAK', signal.SIGTERM), signal.SIG_IGN); "
        "time.sleep(2); "
        f"pathlib.Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )
    parent_script = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}]); time.sleep(10)"
    )

    result = runner.run(((sys.executable, "-c", parent_script),))[0]
    time.sleep(2)

    assert result.status is CheckStatus.FAILED
    assert not marker.exists()
