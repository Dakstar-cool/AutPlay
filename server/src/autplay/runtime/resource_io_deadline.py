"""Irreversible local I/O deadline shared by HTTP, renewal and retained worker threads."""

from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import TypeVar, cast

from autplay.domain.resource_admission import IO_PERMIT_TTL, ResourceAdmissionError

T = TypeVar("T")
_TTL = IO_PERMIT_TTL.total_seconds()


class IoStopped(ResourceAdmissionError):
    def __init__(self) -> None:
        super().__init__("resource_io_stale")


class ResourceIoDeadline:
    """A late database result cannot renew a locally stopped execution.

    Callers capture rpc_started_at BEFORE opening the permit, then construct this
    guard only after a successful response. No comparison of wall clocks is used.
    This guard stops application progress; it never releases durable process charge.
    """

    def __init__(self, rpc_started_at: float, *, clock: Callable[[], float] = monotonic) -> None:
        if not math.isfinite(rpc_started_at):
            raise ValueError("invalid I/O deadline")
        self._clock = clock
        self._lock = threading.Lock()
        self._deadline = rpc_started_at + _TTL
        self._stopped = False
        self._renewal: tuple[int, float] | None = None
        self._sequence = 0
        # Keep cancelled coroutine objects alive until they settle. No result from
        # these tasks is forwarded after stopping, even if cancellation was delayed.
        self._settling: set[asyncio.Future[object]] = set()

    def _remaining(self) -> float:
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            self._stopped = True
        return 0.0 if self._stopped else remaining

    def remaining(self) -> float:
        with self._lock:
            return self._remaining()

    def stopped(self) -> bool:
        return self.remaining() <= 0

    def stop(self) -> None:
        with self._lock:
            self._stopped = True

    def check(self) -> None:
        if self.stopped():
            raise IoStopped()

    def begin_renewal(self) -> int:
        """One outstanding RPC only; its start time anchors the possible extension."""
        with self._lock:
            if self._remaining() <= 0:
                raise IoStopped()
            if self._renewal is not None:
                raise RuntimeError("I/O renewal already in flight")
            self._sequence += 1
            self._renewal = self._sequence, self._clock()
            return self._sequence

    def finish_renewal(self, sequence: int, *, succeeded: bool) -> bool:
        with self._lock:
            if self._renewal is None or self._renewal[0] != sequence:
                return False
            started = self._renewal[1]
            self._renewal = None
            if not succeeded or self._remaining() <= 0:
                self._stopped = True
                return False
            self._deadline = started + _TTL
            return self._remaining() > 0

    async def run(self, action: Callable[[], Awaitable[T]], *, stop_on_cancel: bool = True) -> T:
        """Bound one receive/send/response operation without awaiting stuck cleanup.

        The retained worker/supervisor separately owns all child handles and DB
        transactions. ASGI callers must wrap EACH send/receive with this guard as
        well as the whole response, so delayed cancellation cannot start new I/O.
        """
        self.check()
        operation = asyncio.ensure_future(action())
        try:
            while True:
                self.check()
                done, _ = await asyncio.wait((operation,), timeout=min(1.0, self.remaining()))
                self.check()
                if done:
                    return operation.result()
        except BaseException as error:
            # ASGI response task groups cancel the losing send/disconnect task
            # on normal completion. Their enclosing response guard owns stopping.
            if stop_on_cancel or not isinstance(error, asyncio.CancelledError):
                self.stop()
            if not operation.done():
                operation.cancel()
                settling = cast(asyncio.Future[object], operation)
                self._settling.add(settling)
                settling.add_done_callback(self._settled)
            else:
                # Retrieve a possibly concurrent failure so it cannot leak as an
                # unhandled future exception when the deadline won the race.
                self._settled(cast(asyncio.Future[object], operation))
            raise

    def _settled(self, operation: asyncio.Future[object]) -> None:
        self._settling.discard(operation)
        if not operation.cancelled():
            operation.exception()
