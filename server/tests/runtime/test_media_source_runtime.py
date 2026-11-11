"""UNEXECUTED M1 bounded-owner controls. No database or native probing at authoring."""

from __future__ import annotations

import asyncio
import socket
from threading import Event, Lock
from typing import Any

import pytest
from autplay.adapters.postgresql.media_source_runtime import MediaSourceDatabaseRuntime
from autplay.adapters.postgresql.sync_binding_v2 import BindingCloseResult
from autplay.domain.sync_binding_v2 import SyncBindingV2Error
from autplay.runtime.settings import load_api_settings
from sqlalchemy import URL
from sqlalchemy.util import await_only


def runtime() -> MediaSourceDatabaseRuntime:
    # No DB connection is requested by these synthetic resolver/owner callbacks.
    return MediaSourceDatabaseRuntime(
        URL.create(
            "postgresql+psycopg", host="127.0.0.1", port=5432, database="unused_synthetic_m1"
        )
    )


def test_setting_is_separate_default_false_and_has_explicit_environment_name() -> None:
    required = {
        "database_url": "postgresql+psycopg://autplay:synthetic@127.0.0.1:5432/autplay",
        "auth_signing_secret": "M1-synthetic-signing-secret-at-least-32-bytes",
        "public_access_source_hmac_secret": "M1-synthetic-source-secret-at-least-32-bytes",
    }
    disabled = load_api_settings(overrides=required, environ={})
    enabled = load_api_settings(
        overrides=required, environ={"AUTPLAY_MEDIA_SOURCE_SNAPSHOT_V1_ENABLED": "true"}
    )
    assert disabled.media_source_snapshot_v1_enabled is False
    assert enabled.media_source_snapshot_v1_enabled is True
    assert enabled.device_compatibility_v2_enabled is False
    assert enabled.sync_binding_v2_enabled is False


def test_timed_out_dns_retains_both_slots_and_shutdown_until_actual_resolver_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, entered, guard = Event(), Event(), Lock()
    count = 0

    def blocked_dns(*args: Any, **kwargs: Any) -> list[Any]:
        nonlocal count
        with guard:
            count += 1
            if count == 2:
                entered.set()
        if not release.wait(5):
            raise AssertionError("synthetic resolver release missing")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 5432))]

    monkeypatch.setattr(socket, "getaddrinfo", blocked_dns)

    async def exercise() -> None:
        owner = runtime()
        owner.start()

        def resolve(sessions: Any, budget: Any) -> Any:
            return await_only(owner._loop.getaddrinfo("synthetic.invalid", 5432))

        first = asyncio.create_task(owner.run(resolve, timeout_seconds=0.1))
        second = asyncio.create_task(owner.run(resolve, timeout_seconds=0.1))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            outcomes = await asyncio.gather(first, second, return_exceptions=True)
            assert all(isinstance(result, TimeoutError) for result in outcomes)
            with pytest.raises(SyncBindingV2Error):
                await owner.run(lambda sessions, budget: "must not queue")
            assert count == 2
            assert owner.request_close() is BindingCloseResult.REQUESTED
            assert owner._shutdown is not None and not owner._shutdown.done()
            release.set()
            assert await owner.await_closed() is BindingCloseResult.CLOSED
        finally:
            release.set()
            await asyncio.gather(first, second, return_exceptions=True)
            await owner.shutdown()

    asyncio.run(exercise())


def test_reentrant_request_and_shutdown_never_join_or_await_the_owner_itself() -> None:
    async def exercise() -> None:
        owner = runtime()
        owner.start()

        def reentrant(sessions: Any, budget: Any) -> str:
            assert owner.request_close() is BindingCloseResult.REQUESTED
            assert await_only(owner.shutdown()) is BindingCloseResult.REQUESTED
            with pytest.raises(RuntimeError, match="external"):
                await_only(owner.await_closed())
            return "settled operation"

        try:
            assert await owner.run(reentrant) == "settled operation"
            assert await owner.await_closed() is BindingCloseResult.CLOSED
            assert await owner.shutdown() is BindingCloseResult.CLOSED
            with pytest.raises(SyncBindingV2Error):
                await owner.run(lambda sessions, budget: "closed")
        finally:
            await owner.shutdown()

    asyncio.run(exercise())
