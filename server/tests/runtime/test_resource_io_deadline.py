"""Local monotonic permission cannot revive through slow or overlapping renewals."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

import pytest

from autplay.runtime.resource_io_deadline import IoStopped, ResourceIoDeadline


@dataclass
class Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


def test_initial_and_renewed_deadlines_are_anchored_before_database_rpc() -> None:
    clock = Clock(2)
    guard = ResourceIoDeadline(0, clock=clock)
    assert guard.remaining() == 3
    sequence = guard.begin_renewal()
    clock.now = 4
    assert guard.finish_renewal(sequence, succeeded=True)
    assert guard.remaining() == 3  # Start at 2, expiry at 7, not response at 4 + 5.
    clock.now = 7
    assert guard.stopped()
    with pytest.raises(IoStopped):
        guard.check()


@pytest.mark.parametrize("reason", ["deadline", "explicit", "failure"])
def test_stop_is_irreversible_even_when_a_renewal_response_arrives(reason: str) -> None:
    clock = Clock(2)
    guard = ResourceIoDeadline(0, clock=clock)
    sequence = guard.begin_renewal()
    if reason == "deadline":
        clock.now = 5
    elif reason == "explicit":
        guard.stop()
    assert not guard.finish_renewal(sequence, succeeded=reason != "failure")
    assert guard.stopped()
    clock.now = 1  # Even a broken injected clock cannot undo a recorded stop.
    assert guard.stopped()
    with pytest.raises(IoStopped):
        guard.begin_renewal()


def test_only_one_renewal_can_be_in_flight_and_old_response_cannot_change_new_one() -> None:
    clock = Clock(1)
    guard = ResourceIoDeadline(0, clock=clock)
    first = guard.begin_renewal()
    with pytest.raises(RuntimeError, match="already in flight"):
        guard.begin_renewal()
    assert guard.finish_renewal(first, succeeded=True)
    clock.now = 2
    second = guard.begin_renewal()
    assert not guard.finish_renewal(first, succeeded=True)
    assert guard.finish_renewal(second, succeeded=True)
    assert guard.remaining() == 5


def test_slow_initial_response_never_starts_an_action() -> None:
    async def run() -> None:
        guard = ResourceIoDeadline(0, clock=Clock(6))
        called = False

        async def action() -> None:
            nonlocal called
            called = True

        with pytest.raises(IoStopped):
            await guard.run(action)
        assert not called

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["receive", "send"])
def test_stuck_http_operation_stops_by_deadline_even_if_cancellation_is_delayed(phase: str) -> None:
    async def run() -> None:
        guard = ResourceIoDeadline(monotonic() - 4.9)
        allow_cleanup = asyncio.Event()
        entered = asyncio.Event()
        completed = asyncio.Event()
        progressed: list[str] = []

        async def pending() -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await allow_cleanup.wait()
                try:
                    guard.check()
                    progressed.append(phase)
                except IoStopped:
                    pass
                finally:
                    completed.set()

        task = asyncio.create_task(guard.run(pending))
        await entered.wait()
        with pytest.raises(IoStopped):
            await asyncio.wait_for(task, timeout=1)
        assert guard.stopped() and not progressed
        allow_cleanup.set()
        await asyncio.wait_for(completed.wait(), timeout=1)
        assert not progressed

    asyncio.run(run())


def test_success_and_caller_cancellation_preserve_correct_result() -> None:
    async def run() -> None:
        guard = ResourceIoDeadline(monotonic())

        async def success() -> bytes:
            return b"bounded bytes"

        assert await guard.run(success) == b"bounded bytes"
        task = asyncio.create_task(guard.run(asyncio.Event().wait))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert guard.stopped()

    asyncio.run(run())
