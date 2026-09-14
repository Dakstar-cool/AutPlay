from __future__ import annotations

import hashlib
import io
import json
import shutil
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
