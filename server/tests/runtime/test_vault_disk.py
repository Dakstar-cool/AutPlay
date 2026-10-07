from __future__ import annotations

import threading
from pathlib import Path

from autplay.runtime.vault_disk import VaultDiskSampler


def test_disk_sampling_never_blocks_requests_and_expires_a_hanging_read() -> None:
    now = [0.0]
    calls: list[Path] = []
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def read(root: Path) -> tuple[int, int, int]:
        calls.append(root)
        started.set()
        assert release.wait(5)
        completed.set()
        return 100, 60, 40

    sampler = VaultDiskSampler(Path("vault"), read=read, clock=lambda: now[0])
    assert started.wait(2)
    for _ in range(100):
        assert sampler.snapshot() is None
    assert len(calls) == 1
    thread = next(t for t in threading.enumerate() if t.name == "vault-disk-usage")
    release.set()
    assert completed.wait(2)
    # Synchronize with the sampler's publication, without timing-dependent sleeps.
    thread.join(2)
    sample = sampler.snapshot()
    assert sample is not None and (sample.total_bytes, sample.used_bytes, sample.free_bytes) == (
        100,
        60,
        40,
    )
    release.clear()
    started.clear()
    now[0] = 10.0
    assert sampler.snapshot() == sample
    assert started.wait(2)
    now[0] = 31.0
    for _ in range(100):
        assert sampler.snapshot() is None
    assert len(calls) == 2
    thread = next(t for t in threading.enumerate() if t.name == "vault-disk-usage")
    release.set()
    thread.join(2)


def test_missing_disk_is_unknown_and_sampling_is_rate_limited() -> None:
    now = [0.0]
    calls = [0]
    started = threading.Event()
    release = threading.Event()

    def read(root: Path) -> tuple[int, int, int]:
        calls[0] += 1
        started.set()
        assert release.wait(5)
        raise OSError("private mount path")

    sampler = VaultDiskSampler(Path("vault"), read=read, clock=lambda: now[0])
    assert started.wait(2)
    thread = next(t for t in threading.enumerate() if t.name == "vault-disk-usage")
    release.set()
    thread.join(2)
    assert sampler.snapshot() is None and calls[0] == 1
    now[0] = 9.0
    assert sampler.snapshot() is None and calls[0] == 1
