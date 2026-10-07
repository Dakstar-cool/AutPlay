"""Read container counters and publish independent, best-effort process heartbeats."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol

_LOGGER = logging.getLogger("autplay.worker_health")


@dataclass(frozen=True, slots=True)
class WorkerSample:
    cpu_percent: float | None
    memory_bytes: int | None
    memory_limit_bytes: int | None
    busy: bool


class WorkerHealthSink(Protocol):
    def publish(self, sample: WorkerSample, *, running: bool = True) -> None: ...


class CgroupWorkerSampler:
    """Include all child media processes; unavailable counters remain unknown."""

    def __init__(
        self,
        *,
        root: Path = Path("/sys/fs/cgroup"),
        clock: Callable[[], float] = monotonic,
        busy: Callable[[], bool] = lambda: False,
    ) -> None:
        self._root = root
        self._clock = clock
        self._busy = busy
        self._previous: tuple[float, int, float] | None = None

    def _read(self, name: str) -> str | None:
        try:
            with (self._root / name).open(encoding="ascii") as stream:
                return stream.read(4096).strip()
        except OSError, UnicodeError:
            return None

    def _integer(self, name: str) -> int | None:
        value = self._read(name)
        return int(value) if value is not None and value.isdecimal() else None

    def _cpu_limit(self) -> float | None:
        limits: list[float] = []
        quota = (self._read("cpu.max") or "").split()
        if (
            len(quota) == 2
            and all(value.isdecimal() for value in quota)
            and int(quota[0]) > 0
            and int(quota[1]) > 0
        ):
            limits.append(int(quota[0]) / int(quota[1]))
        cpuset = self._read("cpuset.cpus.effective")
        if cpuset:
            try:
                count = 0
                for part in cpuset.split(","):
                    bounds = [int(value) for value in part.split("-")]
                    if len(bounds) not in {1, 2} or min(bounds) < 0 or bounds[-1] < bounds[0]:
                        raise ValueError("invalid CPU range")
                    count += bounds[-1] - bounds[0] + 1
                if count > 0:
                    limits.append(float(count))
            except ValueError:
                pass
        return min(limits) if limits else None

    def sample(self) -> WorkerSample:
        now = self._clock()
        usage = None
        for line in (self._read("cpu.stat") or "").splitlines():
            key, _, value = line.partition(" ")
            if key == "usage_usec" and value.isdecimal():
                usage = int(value)
        limit = self._cpu_limit()
        percent = None
        if usage is not None and limit is not None:
            if self._previous is not None:
                before, old_usage, old_limit = self._previous
                if now > before and usage >= old_usage and limit == old_limit:
                    percent = min(
                        100.0, 100 * (usage - old_usage) / 1_000_000 / (now - before) / limit
                    )
            self._previous = (now, usage, limit)
        else:
            self._previous = None
        memory_limit = self._integer("memory.max")
        return WorkerSample(
            percent, self._integer("memory.current"), memory_limit or None, self._busy()
        )


class WorkerHealthReporter:
    """A telemetry failure never changes job execution or lease ownership."""

    def __init__(
        self,
        sink: WorkerHealthSink,
        sampler: CgroupWorkerSampler,
        *,
        on_close: Callable[[], None] = lambda: None,
    ) -> None:
        self._sink = sink
        self._sampler = sampler
        self._on_close = on_close
        self._failed = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="cpu-worker-health", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=3)

    def _publish(self, *, running: bool = True) -> None:
        try:
            self._sink.publish(self._sampler.sample(), running=running)
            self._failed = False
        except Exception:
            # No exception text: DB diagnostics and paths may contain private data.
            if not self._failed:
                _LOGGER.warning("worker_health_unavailable")
            self._failed = True

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._publish()
                self._stop.wait(5)
        finally:
            self._publish(running=False)
            self._on_close()
