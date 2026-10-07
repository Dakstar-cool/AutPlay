"""One bounded in-flight filesystem observation; stale or failed reads stay unknown."""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from autplay.domain.admin_views import AdminDiskUsage


def _read_disk(root: Path) -> tuple[int, int, int]:
    value = shutil.disk_usage(root)
    return value.total, value.used, value.free


class VaultDiskSampler:
    """A slow mount cannot block HTTP requests or spawn an unbounded thread backlog."""

    def __init__(
        self,
        root: Path,
        *,
        read: Callable[[Path], tuple[int, int, int]] = _read_disk,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._root = root
        self._read = read
        self._clock = clock
        self._lock = threading.Lock()
        self._running = False
        self._last_attempt: float | None = None
        self._sample_at: float | None = None
        self._sample: AdminDiskUsage | None = None
        self.snapshot()

    def snapshot(self) -> AdminDiskUsage | None:
        now = self._clock()
        with self._lock:
            if not self._running and (self._last_attempt is None or now - self._last_attempt >= 10):
                self._running = True
                self._last_attempt = now
                threading.Thread(target=self._observe, name="vault-disk-usage", daemon=True).start()
            if self._sample_at is None or not 0 <= now - self._sample_at <= 30:
                return None
            return self._sample

    def _observe(self) -> None:
        sample = None
        try:
            total, used, free = self._read(self._root)
            if total > 0 and 0 <= used <= total and 0 <= free <= total and used + free <= total:
                sample = AdminDiskUsage(total, used, free, datetime.now(UTC))
        except OSError, ValueError:
            pass
        finally:
            with self._lock:
                self._sample = sample
                self._sample_at = self._clock() if sample is not None else None
                self._running = False
