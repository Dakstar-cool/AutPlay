from __future__ import annotations

import json
import os
import stat
from uuid import UUID, uuid4

import pytest

from local_music_acquisition.admin_agent import AcquisitionAgent
from local_music_acquisition.queue import enqueue
from local_music_acquisition.queue_store import write_json


def test_agent_requires_explicit_operator_source_policy(tmp_path, monkeypatch) -> None:
    for provider in ("HITMO", "YOUTUBE", "SOUNDCLOUD", "BANDCAMP"):
        monkeypatch.delenv(f"ACQUISITION_ENABLE_{provider}", raising=False)
    monkeypatch.delenv("ACQUISITION_JAMENDO_ID", raising=False)
    monkeypatch.delenv("ACQUISITION_YANDEX_TOKEN", raising=False)
    launcher = tmp_path / "run-queue.sh"
    launcher.write_text("#!/bin/sh\n")
    with pytest.raises(ValueError, match="acquisition_agent_source_policy_missing"):
        AcquisitionAgent(tmp_path / "control", tmp_path / "queues", tmp_path / "music", launcher)
    token = tmp_path / "yandex-token"
    token.write_text("private")
    monkeypatch.setenv("ACQUISITION_YANDEX_TOKEN", str(token))
    agent = AcquisitionAgent(
        tmp_path / "control", tmp_path / "queues", tmp_path / "music", launcher
    )
    assert agent.enabled_sources == ("yandex",)


def test_agent_applies_bounded_concurrency_without_launching(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ACQUISITION_ENABLE_HITMO", "1")
    control = tmp_path / "control"
    for name in ("requests", "playlists", "status"):
        (control / name).mkdir(parents=True)
    launcher = tmp_path / "run-queue.sh"
    launcher.write_text("#!/bin/sh\n")
    queue_id = uuid4()
    (control / "status" / f"{queue_id}.json").write_text(
        json.dumps(
            {
                "state": "queued",
                "requested": 0,
                "paused": False,
                "workers": 2,
                "yt_dlp_concurrency": 1,
                "soundcloud_concurrency": 1,
                **dict.fromkeys(
                    (
                        "pending",
                        "running",
                        "retry",
                        "downloaded",
                        "not_found",
                        "failed",
                        "needs_review",
                    ),
                    0,
                ),
            }
        )
    )
    command_id = uuid4()
    (control / "requests" / f"{command_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "action": "configure",
                "queue_id": str(queue_id),
                "workers": 4,
                "yt_dlp_concurrency": 2,
                "soundcloud_concurrency": 2,
            }
        )
    )
    AcquisitionAgent(control, tmp_path / "queues", tmp_path / "music", launcher).tick()
    status = json.loads((control / "status" / f"{queue_id}.json").read_text())
    assert (status["workers"], status["yt_dlp_concurrency"], status["soundcloud_concurrency"]) == (
        4,
        2,
        2,
    )


class _Process:
    returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


def test_agent_uses_fixed_launcher_and_operator_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ACQUISITION_ENABLE_HITMO", "1")
    control = tmp_path / "control"
    for name in ("requests", "playlists", "status"):
        (control / name).mkdir(parents=True)
    launcher = tmp_path / "run-queue.sh"
    launcher.write_text("#!/bin/sh\n")
    queue_id = uuid4()
    (control / "playlists" / f"{queue_id}.txt").write_text("Artist\tSong\n")
    (control / "status" / f"{queue_id}.json").write_text(
        json.dumps(
            {
                "state": "queued",
                "requested": 0,
                "paused": False,
                "workers": 2,
                "yt_dlp_concurrency": 1,
                "soundcloud_concurrency": 2,
                **dict.fromkeys(
                    (
                        "pending",
                        "running",
                        "retry",
                        "downloaded",
                        "not_found",
                        "failed",
                        "needs_review",
                    ),
                    0,
                ),
            }
        )
    )
    (control / "requests" / f"{queue_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "action": "submit",
                "queue_id": str(queue_id),
                "workers": 2,
                "yt_dlp_concurrency": 1,
                "soundcloud_concurrency": 2,
            }
        )
    )
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []

    def start(arguments: list[str], **kwargs: object) -> _Process:
        calls.append(arguments)
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        environments.append(environment)
        return _Process()

    monkeypatch.setattr("local_music_acquisition.admin_agent.subprocess.Popen", start)
    agent = AcquisitionAgent(control, tmp_path / "queues", tmp_path / "music", launcher)
    agent.tick()
    assert calls == [
        [
            str(launcher),
            str(control / "playlists" / f"{queue_id}.txt"),
            str(tmp_path / "queues" / str(queue_id)),
            str(tmp_path / "music"),
            "--workers",
            "2",
            "--yt-dlp-concurrency",
            "1",
            "--soundcloud-concurrency",
            "2",
        ]
    ]
    assert environments[0]["ACQUISITION_ENABLE_HITMO"] == "1"
    assert environments[0]["ACQUISITION_ENABLE_YOUTUBE"] == "0"
    assert (control / "handled" / f"{queue_id}.json").is_file()
    assert json.loads((control / "status" / f"{queue_id}.json").read_text())["state"] == "running"
    assert json.loads((control / "agent.json").read_text())["active_queue"] == str(queue_id)
    assert json.loads((control / "agent.json").read_text())["sources"] == ["hitmo"]
    if os.name != "nt":
        assert stat.S_IMODE((control / "agent.json").stat().st_mode) == 0o660
    enqueue(
        control / "playlists" / f"{queue_id}.txt",
        tmp_path / "queues" / str(queue_id),
        tmp_path / "music",
    )
    write_json(
        tmp_path / "queues" / str(queue_id) / "runtime.json",
        {
            "providers": {
                "yt_dlp": {
                    "requests": 3,
                    "downloaded": 1,
                    "misses": 1,
                    "failures": 1,
                    "deferred": 0,
                    "circuit_open": True,
                    "private_url": "do-not-publish",
                },
                "unapproved": {"private_url": "do-not-publish"},
            },
        },
    )
    pause_id = uuid4()
    (control / "requests" / f"{pause_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "action": "pause",
                "queue_id": str(queue_id),
            }
        )
    )
    agent.tick()
    assert (tmp_path / "queues" / str(queue_id) / "pause").is_file()
    assert json.loads((control / "status" / f"{queue_id}.json").read_text())["paused"] is True
    published = (control / "status" / f"{queue_id}.json").read_text()
    assert "do-not-publish" not in published
    assert json.loads(published)["providers"]["yt_dlp"]["circuit_open"] is True
    another = uuid4()
    (control / "playlists" / f"{another}.txt").write_text("Artist\tOther\n")
    enqueue(
        control / "playlists" / f"{another}.txt",
        tmp_path / "queues" / str(another),
        tmp_path / "music",
    )
    (control / "status" / f"{another}.json").write_text(
        json.dumps(
            {
                "state": "queued",
                "requested": 1,
                "paused": False,
                "workers": 2,
                "yt_dlp_concurrency": 1,
                "soundcloud_concurrency": 1,
                **dict.fromkeys(
                    (
                        "pending",
                        "running",
                        "retry",
                        "downloaded",
                        "not_found",
                        "failed",
                        "needs_review",
                    ),
                    0,
                ),
            }
        )
    )
    run_request = uuid4()
    request_path = control / "requests" / f"{run_request}.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "action": "run",
                "queue_id": str(another),
            }
        )
    )
    agent.tick()
    assert request_path.is_file() and len(calls) == 1


def test_agent_starts_submissions_in_arrival_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ACQUISITION_ENABLE_HITMO", "1")
    control = tmp_path / "control"
    for name in ("requests", "playlists", "status"):
        (control / name).mkdir(parents=True)
    launcher = tmp_path / "run-queue.sh"
    launcher.write_text("#!/bin/sh\n")
    earlier = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    later = UUID("00000000-0000-4000-8000-000000000000")
    for index, queue_id in enumerate((earlier, later)):
        (control / "playlists" / f"{queue_id}.txt").write_text("Artist\tSong\n")
        (control / "status" / f"{queue_id}.json").write_text(
            json.dumps(
                {
                    "state": "queued",
                    "requested": 0,
                    "paused": False,
                    "workers": 2,
                    "yt_dlp_concurrency": 1,
                    "soundcloud_concurrency": 1,
                    **dict.fromkeys(
                        (
                            "pending",
                            "running",
                            "retry",
                            "downloaded",
                            "not_found",
                            "failed",
                            "needs_review",
                        ),
                        0,
                    ),
                }
            )
        )
        request = control / "requests" / f"{queue_id}.json"
        request.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "action": "submit",
                    "queue_id": str(queue_id),
                    "workers": 2,
                    "yt_dlp_concurrency": 1,
                    "soundcloud_concurrency": 1,
                }
            )
        )
        timestamp = 1_000_000_000 + index * 1_000_000_000
        os.utime(request, ns=(timestamp, timestamp))

    started: list[str] = []

    def start(arguments: list[str], **_kwargs: object) -> _Process:
        started.append(arguments[1])
        return _Process()

    monkeypatch.setattr("local_music_acquisition.admin_agent.subprocess.Popen", start)
    agent = AcquisitionAgent(control, tmp_path / "queues", tmp_path / "music", launcher)
    agent.tick()
    assert started == [str(control / "playlists" / f"{earlier}.txt")]
    assert (control / "requests" / f"{later}.json").is_file()
