from __future__ import annotations

import hashlib
import io
import json
import shutil
import threading
import time
import wave
from pathlib import Path

import pytest

from local_music_acquisition import queue
from local_music_acquisition.models import AcquiredArtifact, ProviderFailure, ProviderMiss
from local_music_acquisition.orchestrator import DownloadSession, PlaylistDownloadError
from local_music_acquisition.queue_cli import main as queue_main
from local_music_acquisition.queue_store import exclusive_lock, read_json, write_json


@pytest.fixture
def audio() -> bytes:
    if shutil.which("ffmpeg") is None:
        pytest.fail("ffmpeg is required for real queue audio validation")
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 96000)
    return output.getvalue()


class Provider:
    name = "fixture"
    requires_rights_confirmation = False

    def __init__(self, audio: bytes, *, action: str = "ok") -> None:
        self.audio = audio
        self.calls = 0
        self.action = action

    def acquire(self, item, output: Path) -> AcquiredArtifact:
        self.calls += 1
        if self.action == "miss":
            raise ProviderMiss(self.name, "exact_match_not_found")
        if self.action == "fail":
            raise ProviderFailure(self.name, "timeout")
        output.mkdir(parents=True, exist_ok=True)
        (output / "track.wav").write_bytes(self.audio)
        return AcquiredArtifact(self.name, "sha256:" + hashlib.sha256(self.audio).hexdigest()[:12])


def setup_queue(tmp_path: Path, text: str = "Artist - Track\n") -> tuple[Path, Path]:
    playlist = tmp_path / "playlist.txt"
    playlist.write_text(text, encoding="utf-8")
    root, output = tmp_path / "queue", tmp_path / "music"
    queue.enqueue(playlist, root, output)
    return root, output


def test_resume_deduplicates_rows_and_verifies_real_audio(tmp_path: Path, audio: bytes) -> None:
    root, output = setup_queue(tmp_path, "Artist - Track\nARTIST - track\nbad row\n")
    provider = Provider(audio)
    first = queue.run_queue(root, providers=(provider,))
    second = queue.run_queue(root, providers=(provider,))
    assert provider.calls == 1
    assert first == second
    assert second["downloaded"] == 1
    assert second["duplicates"] == 1
    assert second["malformed"] == 1
    receipt = read_json(next((output / "tracks").glob("*/receipt.json")))
    assert receipt["sha256"] == hashlib.sha256(audio).hexdigest()
    assert len(list(output.glob("tracks/*/*/*.wav"))) == 1
    assert "Artist" not in json.dumps(second)
    assert str(tmp_path) not in json.dumps(second)


def test_new_recovery_queue_does_not_reuse_previous_attempt_directory(
    tmp_path: Path, audio: bytes
) -> None:
    root, output = setup_queue(tmp_path)
    assert queue.run_queue(root, providers=(Provider(audio, action="miss"),))["not_found"] == 1
    recovery = tmp_path / "recovery"
    queue.enqueue(tmp_path / "playlist.txt", recovery, output)
    assert queue.run_queue(recovery, providers=(Provider(audio),))["downloaded"] == 1


@pytest.mark.parametrize("after_rename", [False, True])
def test_crash_at_publication_recovers_without_network_or_duplicate(
    tmp_path: Path, audio: bytes, monkeypatch, after_rename: bool
) -> None:
    root, output = setup_queue(tmp_path)
    provider = Provider(audio)
    publish = queue._publish

    def crash(stage, destination, key):
        if after_rename:
            publish(stage, destination, key)
        raise InterruptedError("simulated process interruption")

    monkeypatch.setattr(queue, "_publish", crash)
    with pytest.raises(InterruptedError):
        queue.run_queue(root, providers=(provider,))
    monkeypatch.setattr(queue, "_publish", publish)
    assert queue.run_queue(root, providers=(provider,))["downloaded"] == 1
    assert provider.calls == 1
    assert len(list(output.glob("tracks/*/*/*.wav"))) == 1


def test_corrupt_completed_file_needs_review_without_overwrite(
    tmp_path: Path, audio: bytes
) -> None:
    root, output = setup_queue(tmp_path)
    provider = Provider(audio)
    queue.run_queue(root, providers=(provider,))
    path = next(output.glob("tracks/*/*/*.wav"))
    path.write_bytes(b"tampered")
    summary = queue.run_queue(root, providers=(provider,))
    assert summary["needs_review"] == 1
    assert summary["downloaded"] == 0
    assert provider.calls == 1
    assert path.read_bytes() == b"tampered"


def test_retry_backoff_and_explicit_retry_budget(tmp_path: Path, audio: bytes) -> None:
    root, _output = setup_queue(tmp_path)
    provider = Provider(audio, action="fail")
    first = queue.run_queue(root, providers=(provider,), max_attempts=2)
    assert first["retry"] == 1
    queue.run_queue(root, providers=(provider,), max_attempts=2)
    assert provider.calls == 1
    state_path = next((root / "jobs").glob("*.json"))
    state = read_json(state_path)
    state["next_retry"] = 0
    write_json(state_path, state)
    assert queue.run_queue(root, providers=(provider,), max_attempts=2)["failed"] == 1
    queue.retry_unsuccessful(root)
    provider.action = "ok"
    assert queue.run_queue(root, providers=(provider,), max_attempts=2)["downloaded"] == 1
    assert read_json(state_path)["attempts"] == 3


def test_no_match_is_not_retried_without_explicit_request(tmp_path: Path, audio: bytes) -> None:
    root, _output = setup_queue(tmp_path)
    provider = Provider(audio, action="miss")
    assert queue.run_queue(root, providers=(provider,))["not_found"] == 1
    queue.retry_unsuccessful(root)
    queue.run_queue(root, providers=(provider,))
    assert provider.calls == 1
    queue.retry_unsuccessful(root, include_not_found=True)
    provider.action = "ok"
    assert queue.run_queue(root, providers=(provider,))["downloaded"] == 1


def test_persistent_miss_cache_skips_only_previous_miss_on_retry(
    tmp_path: Path, audio: bytes
) -> None:
    root, _ = setup_queue(tmp_path)
    missing = Provider(audio, action="miss")
    missing.name = "missing"
    recovering = Provider(audio, action="fail")
    settings = {"miss_cache_ttl_seconds": 3600, "miss_cache_namespace": "a" * 64}
    assert queue.run_queue(root, providers=(missing, recovering), **settings)["retry"] == 1
    state_path = next((root / "jobs").glob("*.json"))
    state = read_json(state_path)
    state["next_retry"] = 0
    write_json(state_path, state)
    recovering.action = "ok"
    assert queue.run_queue(root, providers=(missing, recovering), **settings)["downloaded"] == 1
    assert missing.calls == 1
    assert recovering.calls == 2
    assert read_json(root / "runtime.json")["miss_cache"]["hits"] == 1


def test_explicit_retry_invalidates_cached_miss(tmp_path: Path, audio: bytes) -> None:
    root, _ = setup_queue(tmp_path)
    provider = Provider(audio, action="miss")
    settings = {"miss_cache_ttl_seconds": 3600, "miss_cache_namespace": "a" * 64}
    assert queue.run_queue(root, providers=(provider,), **settings)["not_found"] == 1
    queue.retry_unsuccessful(root, include_not_found=True)
    provider.action = "ok"
    assert queue.run_queue(root, providers=(provider,), **settings)["downloaded"] == 1


def test_pause_resume_and_read_only_status(tmp_path: Path, audio: bytes, capsys) -> None:
    root, _output = setup_queue(tmp_path)
    provider = Provider(audio)
    assert queue_main(["pause", "--queue-dir", str(root)]) == 0
    assert queue.run_queue(root, providers=(provider,))["paused"]
    assert provider.calls == 0
    assert queue_main(["resume", "--queue-dir", str(root)]) == 0
    assert queue.run_queue(root, providers=(provider,))["downloaded"] == 1
    assert queue_main(["status", "--queue-dir", str(root)]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["downloaded"] == 1


def test_queue_over_500_tracks_is_durable_without_batch_scripts(tmp_path: Path) -> None:
    root, _output = setup_queue(tmp_path, "".join(f"Artist - Track {i}\n" for i in range(1001)))
    assert queue.queue_status(root)["unique_tracks"] == 1001


def test_changed_input_is_rejected_without_changing_manifest(tmp_path: Path) -> None:
    root, output = setup_queue(tmp_path)
    before = (root / "queue.json").read_bytes()
    playlist = tmp_path / "playlist.txt"
    playlist.write_text("Other - Track\n")
    with pytest.raises(PlaylistDownloadError, match="queue_input_changed"):
        queue.enqueue(playlist, root, output)
    assert (root / "queue.json").read_bytes() == before


def test_exclusive_worker_lock(tmp_path: Path, audio: bytes) -> None:
    root, _output = setup_queue(tmp_path)
    provider = Provider(audio)
    with (
        exclusive_lock(root / "queue.lock"),
        pytest.raises(PlaylistDownloadError, match="queue_already_running"),
    ):
        queue.run_queue(root, providers=(provider,))
    assert provider.calls == 0


def test_bad_audio_is_never_published(tmp_path: Path) -> None:
    root, output = setup_queue(tmp_path)
    provider = Provider(b"<html>not audio</html>")
    assert queue.run_queue(root, providers=(provider,), max_attempts=1)["failed"] == 1
    assert not (output / "tracks").exists()


def test_disk_space_preflight_does_not_consume_attempt(
    tmp_path: Path, audio: bytes, monkeypatch
) -> None:
    root, _output = setup_queue(tmp_path)
    provider = Provider(audio)
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(queue.shutil, "disk_usage", lambda _: usage._replace(free=0))
    with pytest.raises(PlaylistDownloadError, match="queue_disk_space_low"):
        queue.run_queue(root, providers=(provider,))
    assert provider.calls == 0
    assert queue.queue_status(root)["pending"] == 1


def test_circuit_has_bounded_half_open_probe(tmp_path: Path, audio: bytes, monkeypatch) -> None:
    from local_music_acquisition import orchestrator
    from local_music_acquisition.models import PlaylistItem

    provider = Provider(audio, action="fail")
    now = [10.0]
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: now[0])
    session = DownloadSession((provider,), frozenset(), failure_threshold=1, cooldown_seconds=5)
    item = PlaylistItem(1, "Artist", "Track")
    assert session.download(item, tmp_path).status == "failed"
    assert not session.available
    assert session.download(item, tmp_path).error_code == "fixture.circuit_open"
    assert provider.calls == 1
    now[0] += 5
    provider.action = "ok"
    assert session.available
    assert session.download(item, tmp_path).status == "downloaded"


def test_open_circuits_do_not_exhaust_entire_playlist(tmp_path: Path, audio: bytes) -> None:
    root, _output = setup_queue(tmp_path, "".join(f"Artist - Track {i}\n" for i in range(30)))
    provider = Provider(audio, action="fail")
    summary = queue.run_queue(root, providers=(provider,), max_workers=1)
    assert provider.calls == 2
    assert summary["retry"] == 2
    assert summary["pending"] == 28


def test_failed_provider_bytes_stay_outside_published_music(tmp_path: Path, audio: bytes) -> None:
    root, output = setup_queue(tmp_path)
    bad = Provider(b"not audio")
    bad.name = "bad"
    good = Provider(audio)
    summary = queue.run_queue(root, providers=(bad, good))
    assert summary["downloaded"] == 1
    assert len(list(output.glob("tracks/*/*/*.wav"))) == 1
    assert len(list(output.glob(".acquire/*/*/quarantine/*/bad/*.wav"))) == 1


def test_needs_review_requires_full_verification_even_with_valid_cache(tmp_path, audio):
    root, _output = setup_queue(tmp_path)
    provider = Provider(audio)
    queue.run_queue(root, providers=(provider,))
    path = next((root / "jobs").glob("*.json"))
    state = read_json(path)
    state["state"] = "needs_review"
    write_json(path, state)
    assert queue.run_queue(root, providers=(provider,))["downloaded"] == 1
    assert read_json(root / "runtime.json")["index"]["sha256_verified"] == 1
    assert read_json(root / "runtime.json")["index"]["cache_hits"] == 0


def test_large_cp1251_playlist_round_trips_without_unreadable_manifest(tmp_path: Path) -> None:
    playlist = tmp_path / "playlist.txt"
    text = "".join(f"{'\u0410' * 3980}\t{'\u0411' * 3980}{number}\n" for number in range(240))
    payload = text.encode("cp1251")
    assert len(payload) < 2 * 1024 * 1024
    playlist.write_bytes(payload)
    root, output = tmp_path / "queue", tmp_path / "music"
    queue.enqueue(playlist, root, output)
    assert queue.queue_status(root)["unique_tracks"] == 240


def test_directory_creation_flushes_parent_before_receipt(tmp_path: Path, monkeypatch) -> None:
    from local_music_acquisition import queue_store

    calls = []
    monkeypatch.setattr(queue_store, "sync_directory", lambda path: calls.append(path))
    leaf = tmp_path / "new" / "jobs"
    queue_store.write_json(leaf / "receipt.json", {"ok": True})
    assert tmp_path in calls
    assert tmp_path / "new" in calls
    assert calls.index(tmp_path) < calls.index(tmp_path / "new")
    assert calls[-1] == leaf


@pytest.mark.parametrize("other_action", ["miss", "fail", "ok"])
@pytest.mark.parametrize("blocked_first", [False, True])
def test_deferred_fallback_preserves_real_outcome(tmp_path, audio, other_action, blocked_first):
    from local_music_acquisition.models import PlaylistItem

    blocked = Provider(audio)
    blocked.name = "blocked"
    other = Provider(audio, action=other_action)
    providers = (blocked, other) if blocked_first else (other, blocked)
    session = DownloadSession(providers, frozenset())
    lane = next(lane for lane in session.lanes if lane.provider.name == "blocked")
    lane.circuit_open, lane.opened_at = True, time.monotonic()
    outcome = session.download(PlaylistItem(1, "Artist", "Track"), tmp_path)
    assert blocked.calls == 0
    if other_action == "ok":
        assert outcome.status == "downloaded" and not outcome.deferred
    elif other_action == "fail":
        assert outcome.error_code == "fixture.timeout" and not outcome.deferred
    else:
        assert outcome.status == "failed" and outcome.deferred
        assert outcome.error_code == "blocked.circuit_open"


def test_parallel_waiters_do_not_spend_budget_when_circuit_opens(tmp_path, audio, monkeypatch):
    from local_music_acquisition import orchestrator

    root, output = setup_queue(tmp_path, "".join(f"Artist - Track {i}\n" for i in range(4)))
    provider = Provider(audio, action="fail")
    barrier = threading.Barrier(4)
    invoke = orchestrator._ProviderLane.invoke

    def simultaneous(lane, item, directory):
        barrier.wait(timeout=5)
        return invoke(lane, item, directory)

    monkeypatch.setattr(orchestrator._ProviderLane, "invoke", simultaneous)
    summary = queue.run_queue(root, providers=(provider,), max_workers=4, max_attempts=1)
    assert provider.calls == 2
    assert summary["failed"] == 2 and summary["retry"] == 2
    states = [read_json(path) for path in (root / "jobs").glob("*.json")]
    assert all(state["attempts"] == 1 for state in states)
    deferred = [state for state in states if state["state"] == "retry"]
    assert all(state["attempt_budget_reset"] == 1 for state in deferred)
    assert all(state["next_retry"] > time.time() for state in deferred)
    metrics = read_json(root / "runtime.json")
    assert metrics["providers"]["fixture"]["requests"] == 2
    assert metrics["providers"]["fixture"]["deferred"] == 2
    assert "Artist" not in json.dumps(metrics) and str(tmp_path) not in json.dumps(metrics)
    monkeypatch.setattr(orchestrator._ProviderLane, "invoke", invoke)
    for path in (root / "jobs").glob("*.json"):
        state = read_json(path)
        if state["state"] == "retry":
            state["next_retry"] = 0
            write_json(path, state)
    assert queue.run_queue(root, providers=(provider,), max_attempts=1)["failed"] == 4
    assert provider.calls == 4
    assert len(list(output.glob(".acquire/*/*/2"))) == 2
    queue.retry_unsuccessful(root)
    provider.action = "ok"
    assert queue.run_queue(root, providers=(provider,), max_attempts=1)["downloaded"] == 4


def test_new_queue_reuses_index_and_verify_command_preserves_pause(tmp_path, audio, capsys):
    root, output = setup_queue(tmp_path)
    provider = Provider(audio)
    queue.run_queue(root, providers=(provider,))
    second = tmp_path / "second-queue"
    queue.enqueue(tmp_path / "playlist.txt", second, output)
    assert queue.run_queue(second, providers=(provider,))["downloaded"] == 1
    assert provider.calls == 1
    assert read_json(second / "runtime.json")["index"]["cache_hits"] == 1
    write_json(second / "pause", {"paused": True})
    assert queue_main(["verify", "--queue-dir", str(second)]) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["paused"] and report["index"]["sha256_verified"] == 1
    assert report["index"]["cache_hits"] == 0
    assert provider.calls == 1


def test_paused_queue_cli_exits_successfully_without_requests(tmp_path, audio, monkeypatch):
    from local_music_acquisition import cli

    root, output = setup_queue(tmp_path)
    provider = Provider(audio)
    monkeypatch.setattr(cli, "YtDlpProvider", lambda **kwargs: provider)
    write_json(root / "pause", {"paused": True})
    result = cli.main(
        [
            str(tmp_path / "playlist.txt"),
            "--output-dir",
            str(output),
            "--queue-dir",
            str(root),
            "--disable-jamendo",
            "--disable-hitmo",
        ]
    )
    assert result == 0 and provider.calls == 0


def test_file_disappearing_during_index_check_does_not_abort_other_jobs(
    tmp_path, audio, monkeypatch
):
    from local_music_acquisition import download_index

    root, output = setup_queue(tmp_path, "Artist - One\nArtist - Two\n")
    provider = Provider(audio)
    queue.run_queue(root, providers=(provider,))
    original = download_index.verify_receipt
    removed = []

    def remove_after_hash(directory, key):
        receipt = original(directory, key)
        if not removed:
            path = directory / receipt["provider"] / receipt["filename"]
            removed.append(path)
            path.unlink()
        return receipt

    monkeypatch.setattr(download_index, "verify_receipt", remove_after_hash)
    summary = queue.run_queue(root, providers=(provider,), index_recheck_seconds=0)
    assert summary["downloaded"] == 1 and summary["needs_review"] == 1
    assert provider.calls == 2
    assert len(list(output.glob("tracks/*/*/*.wav"))) == 1
