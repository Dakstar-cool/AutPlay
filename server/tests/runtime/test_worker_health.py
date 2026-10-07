from __future__ import annotations

import threading
from pathlib import Path

import pytest
from autplay.runtime.worker_health import CgroupWorkerSampler, WorkerHealthReporter, WorkerSample


def _counters(root: Path, usage: int, quota: str = "100000 100000") -> None:
    for name, value in {
        "cpu.stat": f"usage_usec {usage}\nuser_usec 0\nsystem_usec 0\n",
        "cpu.max": quota,
        "cpuset.cpus.effective": "0-3,6",
        "memory.current": "104857600",
        "memory.max": "536870912",
    }.items():
        (root / name).write_text(value, encoding="ascii")


def test_container_usage_uses_allocated_quota_and_resets_on_counter_changes(tmp_path: Path) -> None:
    now = [10.0]
    busy = threading.Event()
    sampler = CgroupWorkerSampler(root=tmp_path, clock=lambda: now[0], busy=busy.is_set)
    _counters(tmp_path, 1_000_000)
    first = sampler.sample()
    assert first.cpu_percent is None
    assert first.memory_bytes == 104857600 and first.memory_limit_bytes == 536870912
    now[0] += 5
    busy.set()
    _counters(tmp_path, 3_500_000)
    assert sampler.sample().cpu_percent == pytest.approx(50)
    assert sampler.sample().busy
    now[0] += 5
    _counters(tmp_path, 1)
    assert sampler.sample().cpu_percent is None
    now[0] += 5
    _counters(tmp_path, 2_500_001, "50000 100000")
    assert sampler.sample().cpu_percent is None
    now[0] += 5
    _counters(tmp_path, 5_000_001, "50000 100000")
    assert sampler.sample().cpu_percent == pytest.approx(100)


def test_unlimited_cpu_uses_cpuset_and_unavailable_values_never_become_zero(tmp_path: Path) -> None:
    now = [0.0]
    sampler = CgroupWorkerSampler(root=tmp_path, clock=lambda: now[0])
    assert sampler.sample() == WorkerSample(None, None, None, False)
    _counters(tmp_path, 0, "max 100000")
    (tmp_path / "memory.max").write_text("max", encoding="ascii")
    assert sampler.sample().memory_limit_bytes is None
    now[0] = 5
    _counters(tmp_path, 5_000_000, "max 100000")
    assert sampler.sample().cpu_percent == pytest.approx(20)
    (tmp_path / "cpu.stat").write_text("unavailable", encoding="ascii")
    now[0] += 5
    assert sampler.sample().cpu_percent is None


def test_reporter_is_independent_of_jobs_and_publishes_a_stop_signal(tmp_path: Path) -> None:
    ready = threading.Event()
    observations: list[tuple[WorkerSample, bool]] = []
    closed = threading.Event()

    class Sink:
        def publish(self, sample: WorkerSample, *, running: bool = True) -> None:
            observations.append((sample, running))
            ready.set()

    reporter = WorkerHealthReporter(
        Sink(), CgroupWorkerSampler(root=tmp_path, busy=lambda: True), on_close=closed.set
    )
    reporter.start()
    assert ready.wait(2)
    reporter.stop()
    assert closed.is_set()
    assert [running for _, running in observations] == [True, False]
    assert observations[0][0].busy


def test_telemetry_failures_are_redacted_and_do_not_escape(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class Sink:
        def publish(self, sample: WorkerSample, *, running: bool = True) -> None:
            raise RuntimeError("postgresql://private-secret/path")

    reporter = WorkerHealthReporter(Sink(), CgroupWorkerSampler(root=tmp_path))
    reporter._publish()
    assert "worker_health_unavailable" in caplog.text
    assert "private-secret" not in caplog.text
