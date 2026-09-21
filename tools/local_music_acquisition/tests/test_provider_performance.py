from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from local_music_acquisition.models import (
    AcquiredArtifact,
    PlaylistItem,
    ProviderFailure,
    ProviderMiss,
)
from local_music_acquisition.orchestrator import DownloadSession, PlaylistDownloadError
from local_music_acquisition.provider_cache import CachedProvider, ProviderMissCache


class Provider:
    name = "fixture"
    requires_rights_confirmation = False
    max_parallelism = 2

    def __init__(self, action: str = "miss") -> None:
        self.action = action
        self.calls = 0

    def acquire(self, item: PlaylistItem, output: Path) -> AcquiredArtifact:
        self.calls += 1
        if self.action == "miss":
            raise ProviderMiss(self.name, "exact_match_not_found")
        if self.action == "unavailable":
            raise ProviderMiss(self.name, "source_unavailable")
        if self.action == "fail":
            raise ProviderFailure(self.name, "timeout")
        return AcquiredArtifact(self.name, "sha256:0123456789ab")


def item(number: int = 1, duration: float | None = None) -> PlaylistItem:
    return PlaylistItem(
        number, "Private Artist", f"Private Track {number}", expected_duration_seconds=duration
    )


def session(provider: Provider, root: Path, *, namespace: str = "a" * 64) -> DownloadSession:
    cache = ProviderMissCache(root, namespace, 60)
    return DownloadSession((CachedProvider(provider, cache),), frozenset())


def test_cache_survives_restart_without_raw_metadata(tmp_path: Path) -> None:
    first = Provider()
    assert session(first, tmp_path).download(item(), tmp_path).status == "not_found"
    second = Provider("ok")
    restored = session(second, tmp_path)
    assert restored.download(item(), tmp_path).status == "not_found"
    assert second.calls == 0
    assert restored.metrics()["fixture"]["cached_misses"] == 1
    assert restored.metrics()["fixture"]["requests"] == 0
    assert "Private" not in next(tmp_path.glob("*.json")).read_text()


@pytest.mark.parametrize("changed", ["expired", "future", "namespace", "duration", "corrupt"])
def test_stale_or_invalid_miss_never_blocks_a_fresh_search(tmp_path: Path, changed: str) -> None:
    session(Provider(), tmp_path).download(item(), tmp_path)
    path = next(tmp_path.glob("*.json"))
    record = json.loads(path.read_text())
    if changed in {"expired", "future"}:
        record["providers"]["fixture"]["checked_unix"] += -61 if changed == "expired" else 60
        path.write_text(json.dumps(record))
    if changed == "corrupt":
        path.write_text("invalid json")
    provider = Provider("ok")
    active = session(provider, tmp_path, namespace="b" * 64 if changed == "namespace" else "a" * 64)
    result = active.download(item(duration=120 if changed == "duration" else None), tmp_path)
    assert result.status == "downloaded"
    assert provider.calls == 1


@pytest.mark.parametrize("action", ["fail", "unavailable"])
def test_transient_and_availability_outcomes_are_never_cached(tmp_path: Path, action: str) -> None:
    provider = Provider(action)
    session(provider, tmp_path).download(item(), tmp_path)
    provider.action = "ok"
    assert session(provider, tmp_path).download(item(), tmp_path).status == "downloaded"
    assert provider.calls == 2


def test_cached_miss_does_not_reset_real_failure_count(tmp_path: Path) -> None:
    provider = Provider()
    current = session(provider, tmp_path)
    current.download(item(1), tmp_path)
    provider.action = "fail"
    current.download(item(2), tmp_path)
    current.download(item(1), tmp_path)
    current.download(item(3), tmp_path)
    assert current.metrics()["fixture"]["circuit_open"] is True
    assert provider.calls == 3


def test_optional_cache_write_failure_preserves_miss_and_disables_cache(
    tmp_path: Path, monkeypatch
) -> None:
    from local_music_acquisition import provider_cache

    writes = 0

    def fail(*args):
        nonlocal writes
        writes += 1
        raise OSError("disk failure")

    monkeypatch.setattr(provider_cache, "write_json", fail)
    provider = Provider()
    current = session(provider, tmp_path)
    assert current.download(item(1), tmp_path).status == "not_found"
    assert current.download(item(2), tmp_path).status == "not_found"
    assert current.metrics()["fixture"]["failures"] == 0
    assert writes == 1


def test_parallelism_is_bounded_and_network_calls_overlap(tmp_path: Path) -> None:
    lock = threading.Lock()
    pair = threading.Barrier(2)
    active = maximum = 0

    class Parallel(Provider):
        def acquire(self, item: PlaylistItem, output: Path) -> AcquiredArtifact:
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            pair.wait(timeout=3)
            with lock:
                active -= 1
            return AcquiredArtifact(self.name, "sha256:0123456789ab")

    current = DownloadSession((Parallel(),), frozenset(), provider_concurrency={"fixture": 2})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda n: current.download(item(n), tmp_path), range(4)))
    assert all(result.status == "downloaded" for result in results)
    assert maximum == 2
    assert current.metrics()["fixture"]["requests"] == 4


def test_late_success_cannot_close_an_open_circuit(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class LateSuccess(Provider):
        def acquire(self, entry: PlaylistItem, output: Path) -> AcquiredArtifact:
            if entry.row_number == 1:
                started.set()
                assert release.wait(timeout=3)
                return AcquiredArtifact(self.name, "sha256:0123456789ab")
            raise ProviderFailure(self.name, "timeout")

    current = DownloadSession(
        (LateSuccess(),), frozenset(), failure_threshold=1, provider_concurrency={"fixture": 2}
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        late = pool.submit(current.download, item(1), tmp_path)
        assert started.wait(timeout=3)
        assert current.download(item(2), tmp_path).status == "failed"
        release.set()
        assert late.result(timeout=3).status == "downloaded"
    assert current.metrics()["fixture"]["circuit_open"] is True


def test_half_open_permits_only_one_network_probe(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Probe(Provider):
        def acquire(self, entry: PlaylistItem, output: Path) -> AcquiredArtifact:
            if self.action == "fail":
                raise ProviderFailure(self.name, "timeout")
            started.set()
            assert release.wait(timeout=3)
            return AcquiredArtifact(self.name, "sha256:0123456789ab")

    provider = Probe("fail")
    current = DownloadSession(
        (provider,), frozenset(), failure_threshold=1, provider_concurrency={"fixture": 2}
    )
    current.download(item(), tmp_path)
    current.lanes[0].opened_at -= 61
    provider.action = "ok"
    with ThreadPoolExecutor(max_workers=2) as pool:
        probe = pool.submit(current.download, item(2), tmp_path)
        assert started.wait(timeout=3)
        assert current.download(item(3), tmp_path).deferred
        release.set()
        assert probe.result(timeout=3).status == "downloaded"
    assert current.metrics()["fixture"]["circuit_open"] is False


def test_serial_only_provider_cannot_be_parallelized() -> None:
    provider = Provider()
    provider.max_parallelism = 1
    with pytest.raises(PlaylistDownloadError, match="provider_concurrency_invalid"):
        DownloadSession((provider,), frozenset(), provider_concurrency={"fixture": 2})


def test_old_success_cannot_erase_failure_after_recovery_probe(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Recovery(Provider):
        def acquire(self, entry: PlaylistItem, output: Path) -> AcquiredArtifact:
            if entry.row_number == 1:
                started.set()
                assert release.wait(timeout=3)
            elif entry.row_number != 4:
                raise ProviderFailure(self.name, "timeout")
            return AcquiredArtifact(self.name, "sha256:0123456789ab")

    current = DownloadSession((Recovery(),), frozenset(), provider_concurrency={"fixture": 2})
    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(current.download, item(1), tmp_path)
        assert started.wait(timeout=3)
        current.download(item(2), tmp_path)
        current.download(item(3), tmp_path)
        assert current.lanes[0].circuit_open
        current.lanes[0].opened_at -= 61
        current.download(item(4), tmp_path)
        current.download(item(5), tmp_path)
        release.set()
        old.result(timeout=3)
        current.download(item(6), tmp_path)
    assert current.lanes[0].circuit_open


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--yt-dlp-concurrency", "2"),
        ("--soundcloud-concurrency", "2"),
        ("--miss-cache-ttl-seconds", "60"),
    ],
)
def test_performance_options_are_not_silently_ignored(
    tmp_path: Path, flag: str, value: str, capsys
) -> None:
    from local_music_acquisition.cli import main

    assert main(["missing.txt", "--output-dir", str(tmp_path), flag, value]) == 2
    assert "performance_options_require_queue" in capsys.readouterr().err


def test_invalid_cache_directory_preserves_search_result(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    root.write_text("occupied")
    current = session(Provider(), root)
    assert current.download(item(), tmp_path).status == "not_found"
    assert current.metrics()["fixture"]["failures"] == 0


def test_cache_lookup_permission_error_does_not_abort_download(tmp_path: Path, monkeypatch) -> None:
    provider = Provider("ok")
    current = session(provider, tmp_path)
    monkeypatch.setattr(Path, "exists", lambda _: (_ for _ in ()).throw(PermissionError()))
    assert current.download(item(), tmp_path).status == "downloaded"
    assert provider.calls == 1
