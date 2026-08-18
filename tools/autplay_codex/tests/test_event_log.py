from __future__ import annotations

import json
from pathlib import Path

from autplay_codex.event_log import EventLogger


def test_event_log_is_allowlisted_redacted_and_level_controlled(tmp_path: Path) -> None:
    state_dir = tmp_path / ".state"
    logger = EventLogger(state_dir, tmp_path, log_level="info")

    logger.emit("ignored-debug", level="debug", task_id="not-written")
    logger.emit(
        "state-changed",
        task_id="task-1",
        state="planning",
        routing_reason=f"inspect {tmp_path}; token=secret-value",
    )

    lines = logger.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    document = json.loads(lines[0])
    assert document["level"] == "info"
    assert "<repo>" in document["routing_reason"]
    assert "secret-value" not in document["routing_reason"]
