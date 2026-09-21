"""Actual control-pool isolation, query bounds and retained transaction ownership."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import ExitStack
from time import monotonic
from uuid import uuid4

import pytest
from autplay.adapters.postgresql.models import UploadSessionRow
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.domain.resource_admission import ResourceKind, ResourceRequest
from autplay.entrypoints.resource_composition import ResourceIoRuntime
from autplay.runtime.settings import ApiSettings
from pydantic import SecretStr
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, TimeoutError

from .test_resource_admission_runtime import AdmissionHarness, admission, fence, play, present
from .test_vault_io_coordinator import assert_closed, eventually

__all__ = ["admission"]


def settings(harness: AdmissionHarness) -> ApiSettings:
    return ApiSettings(
        database_url=SecretStr(harness.engine.url.render_as_string(hide_password=False)),
        auth_signing_secret=SecretStr("synthetic-control-signing-secret-at-least-32"),
        public_access_source_hmac_secret=SecretStr("synthetic-control-source-secret-at-least-32"),
    )


def test_control_admission_survives_exhausted_data_pool(admission: AdmissionHarness) -> None:
    admission.budget()
    actor = admission.actor()
    config = settings(admission)
    data = create_runtime_engine(config)
    runtime = ResourceIoRuntime(config)
    try:
        with ExitStack() as held:
            for _ in range(10):
                held.enter_context(data.connect())
            active = fence(runtime.service.acquire(actor, play()))
            runtime.service.renew(actor, active)
            runtime.service.release(actor, active)
            assert runtime.engine.pool is not data.pool
    finally:
        asyncio.run(runtime.shutdown())
        data.dispose()


def test_control_pool_and_queries_have_independent_short_bounds(
    admission: AdmissionHarness,
) -> None:
    runtime = ResourceIoRuntime(settings(admission))
    try:
        with ExitStack() as held:
            for _ in range(4):
                held.enter_context(runtime.engine.connect())
            started = monotonic()
            with pytest.raises(TimeoutError), runtime.engine.connect():
                pytest.fail("fifth control connection exceeded its pool bound")
            assert monotonic() - started < 2
        with runtime.engine.connect() as connection:
            assert connection.scalar(text("SHOW statement_timeout")) == "1s"
            assert connection.scalar(text("SHOW lock_timeout")) == "500ms"
            assert connection.scalar(text("SHOW application_name")) == "autplay-resource-control"
            with pytest.raises(DBAPIError) as timed_out:
                connection.execute(text("SELECT pg_sleep(2)"))
            assert getattr(timed_out.value.orig, "sqlstate", None) == "57014"
        with admission.engine.begin() as holder:
            holder.execute(text("SELECT * FROM account.resource_quota_policy FOR UPDATE"))
            with runtime.engine.begin() as contender, pytest.raises(DBAPIError) as locked:
                contender.execute(text("SELECT * FROM account.resource_quota_policy FOR UPDATE"))
            assert getattr(locked.value.orig, "sqlstate", None) == "55P03"
    finally:
        asyncio.run(runtime.shutdown())


def test_shutdown_preserves_control_pool_until_retained_upload_transaction_ends(
    admission: AdmissionHarness,
) -> None:
    admission.budget()
    actor = admission.actor()
    upload_id = admission.upload(actor)
    runtime = ResourceIoRuntime(settings(admission), maximum=1)
    active = fence(
        runtime.service.acquire(
            actor,
            ResourceRequest(uuid4(), ResourceKind.TRANSFER, "UPLOAD_INTENT", upload_id, upload_id),
        )
    )
    entered, allow_exit = threading.Event(), threading.Event()
    disposals: list[bool] = []
    event.listen(runtime.engine, "engine_disposed", lambda engine: disposals.append(True))
    runtime.start()

    async def scenario() -> None:
        try:
            io = await runtime.coordinator.open(
                actor, active, resource_type="UPLOAD_INTENT", target_id=upload_id
            )

            def held_transaction() -> None:
                with admission.sessions.begin() as session:
                    row = present(session.get(UploadSessionRow, upload_id, with_for_update=True))
                    row.received_size = 1
                    entered.set()
                    assert allow_exit.wait(10)
                    io.deadline.check()

            waiter = asyncio.create_task(io.perform(held_transaction))
            await eventually(entered.is_set)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert await runtime.shutdown(timeout=0) == (io.identifier,)
            assert not runtime.disposed and not disposals
            runtime.service.release(actor, active)
            assert runtime.service.poll(actor, active.operation_id).usage.server == 1
            with pytest.raises(DBAPIError), admission.sessions.begin() as contender:
                contender.get(UploadSessionRow, upload_id, with_for_update={"nowait": True})
            allow_exit.set()
            # No second shutdown call is needed for eventual disposal.
            await eventually(lambda: runtime.disposed)
            assert disposals == [True]
            assert_closed(admission, runtime.coordinator)
        finally:
            allow_exit.set()
            assert not await runtime.shutdown()

    asyncio.run(scenario())
    assert disposals == [True]
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, upload_id)).received_size == 0


def test_duplicate_start_does_not_dispose_a_live_runtime(admission: AdmissionHarness) -> None:
    runtime = ResourceIoRuntime(settings(admission))
    runtime.start()
    try:
        with pytest.raises(RuntimeError, match="cannot start twice"):
            runtime.start()
        assert not runtime.disposed
    finally:
        assert not asyncio.run(runtime.shutdown())
    assert runtime.disposed
    with pytest.raises(RuntimeError, match="after shutdown"):
        runtime.start()


def test_concurrent_start_cannot_dispose_the_first_live_runtime(
    admission: AdmissionHarness,
) -> None:
    entered, release = threading.Event(), threading.Event()
    calls_lock = threading.Lock()
    calls = 0

    class PausedRuntime(ResourceIoRuntime):
        @property
        def disposed(self) -> bool:
            nonlocal calls
            with calls_lock:
                calls += 1
                first = calls == 1
            if first:
                entered.set()
                assert release.wait(5)
            return self._disposed.is_set()

    runtime = PausedRuntime(settings(admission))

    def start() -> str:
        try:
            runtime.start()
            return "started"
        except RuntimeError:
            return "duplicate"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(start)
            assert entered.wait(5)
            second = pool.submit(start)
            try:
                with pytest.raises(FutureTimeout):
                    second.result(timeout=0.2)
            finally:
                release.set()
            assert first.result(timeout=5) == "started"
            assert second.result(timeout=5) == "duplicate"
        assert not runtime.disposed
    finally:
        release.set()
        assert not asyncio.run(runtime.shutdown())
