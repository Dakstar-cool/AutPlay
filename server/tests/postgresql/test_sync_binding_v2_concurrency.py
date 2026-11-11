"""UNEXECUTED true PG schedules, private connections, bounded settlement.

The account and device FK schedules are independent parameters. A sequential
mock, pool.map, or device-only schedule cannot qualify these lock contracts.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from threading import Barrier, Event, Lock
from time import monotonic, sleep
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.postgresql.sync_binding_v2 import BindingCloseResult
from autplay.application.device_profile_service_v2 import DeviceProfileServiceV2
from autplay.application.profile_pairing import ProfilePairingService
from autplay.application.sync import SyncService
from autplay.application.sync_binding_v2 import SyncBindingV2Service
from autplay.domain.auth import AccessTokenClaims
from autplay.domain.device_compatibility import NegotiationIdentity
from autplay.domain.sync_binding_v2 import SyncBindingV2Error, parse_binding_request
from autplay.entrypoints.api import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from psycopg import AsyncConnection, Connection
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import AsyncAdaptedQueuePool
from typed_assertions import present, require_instance

from .test_device_profile_v2 import ROTATE_DOMAIN, signed, snapshot
from .test_device_profile_v2 import profile as profile
from .test_profile_pairing_m5b import pairing_service as pairing_service
from .test_sync_binding_v2 import PREFIX, BindingHarness, assert_error, cursors
from .test_sync_binding_v2 import binding as binding
from .test_sync_binding_v2 import qualified as qualified
from .test_sync_runtime import _event


@dataclass
class Workers:
    service: SyncBindingV2Service
    pool: ThreadPoolExecutor
    futures: list[Any]
    pids: list[int]
    ready: Event
    sessions: sessionmaker[Session]
    identity: Any


@contextmanager
def private_workers(
    h: BindingHarness, database_url: str, observer: Connection[Any], *, contenders: int
) -> Iterator[Workers]:
    name, gate, ready, mutex = "b1-fixture-" + uuid4().hex, Barrier(contenders), Event(), Lock()
    engine = create_engine(
        database_url,
        pool_size=contenders + 1,
        max_overflow=0,
        pool_timeout=1,
        connect_args={
            "connect_timeout": 2,
            "application_name": name,
            (
                "options"
            ): "-c lock_timeout=5000 -c statement_timeout=5000 -c transaction_timeout=15000 "
            "-c idle_in_transaction_session_timeout=15000",
        },
    )
    sessions = sessionmaker(
        engine,
        class_=Session,
        expire_on_commit=False,
        info=h.profile.identity._sessions.kw["info"],
    )
    pids: list[int] = []
    stopping, end = Event(), monotonic() + 22

    def deadline(*args: Any) -> None:
        if stopping.is_set() or monotonic() >= end:
            raise TimeoutError("B1 private fixture deadline")

    event.listen(engine, "before_cursor_execute", deadline)

    class Identity:
        def before_identity(self, session: Session) -> None:
            pid = session.scalar(text("SELECT pg_backend_pid()"))
            with mutex:
                pids.append(pid)
            gate.wait(timeout=3)  # both actual sessions exist before M5/admission
            ready.set()

        def current_device_contract_identity(self, session: Session) -> NegotiationIdentity:
            self.before_identity(session)
            return h.profile.identity.current_device_contract_identity(session)

        def device_contract_identity(self, session: Session, now: Any) -> NegotiationIdentity:
            self.before_identity(session)
            return h.profile.identity.device_contract_identity(session, now)

        def sign_sync_binding_capabilities(self, payload: dict[str, object]) -> dict[str, object]:
            return h.profile.identity.sign_sync_binding_capabilities(payload)

        def __getattr__(self, name: str) -> Any:
            return getattr(
                h.profile.identity, name
            )  # genuine M5 owner/audit in the same private transaction

    identity = Identity()
    service = SyncBindingV2Service(
        sessions, identity=cast(ProfilePairingService, identity), policy=h.policy
    )
    pool = ThreadPoolExecutor(max_workers=max(2, contenders))
    futures: list[Future[object]] = []
    try:
        yield Workers(service, pool, futures, pids, ready, sessions, identity)
        _, pending = wait(futures, timeout=max(0, end - monotonic()))
        assert not pending, "B1 fixture workers exceeded deadline"
    finally:
        gate.abort()
        stopping.set()
        observer.rollback()
        if any(not future.done() for future in futures):
            # Only this private, generated application_name in this disposable DB.
            observer.execute(
                "SELECT pg_terminate_backend(pid,5000) FROM pg_stat_activity "
                "WHERE datname=current_database() AND application_name=%s",
                (name,),
            )
            observer.commit()
        pool.shutdown(wait=True, cancel_futures=True)
        assert all(future.done() for future in futures)
        event.remove(engine, "before_cursor_execute", deadline)
        engine.dispose()


def claims(h: BindingHarness) -> AccessTokenClaims:
    # The ordinary configured access decoder is followed by the binder's full
    # locked account/device/session revalidation. API tests cover entry auth too.
    return h.profile.identity._access.decode(h.token, now=h.profile.clock["now"])


def result_or_code(
    service: SyncBindingV2Service, h: BindingHarness, body: dict[str, Any]
) -> dict[str, object] | tuple[str, int]:
    try:
        return service.bind(h.actor, claims(h), parse_binding_request(rfc8785.dumps(body)))
    except SyncBindingV2Error as error:
        return error.code, error.status


@pytest.mark.parametrize("same_epoch", [True, False])
def test_two_actual_missing_cursor_contenders(
    qualified: BindingHarness,
    database_url: str,
    database_connection: Connection[Any],
    same_epoch: bool,
) -> None:
    h = qualified
    first = h.request()
    second = dict(first) if same_epoch else {**first, "journal_epoch": str(uuid4())}
    assert not cursors(database_connection)
    with private_workers(h, database_url, database_connection, contenders=2) as w:
        w.futures.extend(
            w.pool.submit(result_or_code, w.service, h, body) for body in (first, second)
        )
        _, pending = wait(w.futures, timeout=12)
        assert not pending
        results = [future.result(timeout=0) for future in w.futures]
        assert len(set(w.pids)) == 2
    successful = [result for result in results if isinstance(result, dict)]
    assert len(successful) == (2 if same_epoch else 1)
    assert len(cursors(database_connection)) == 1
    if same_epoch:
        assert results[0] == results[1]
    else:
        assert ("DEVICE_RESET_REQUIRED", 409) in results


@pytest.mark.parametrize("different_epoch", [False, True])
def test_actual_unique_race_has_bounded_exact_readback_not_blind_success(
    qualified: BindingHarness,
    database_url: str,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    different_epoch: bool,
) -> None:
    from autplay.adapters.postgresql.sync_binding_v2 import SqlAlchemySyncBindingRepository

    h = qualified
    body, reached, release = h.request(), Event(), Event()
    original = SqlAlchemySyncBindingRepository.bind_unused

    def held(self: Any, *args: Any, **kwargs: Any) -> None:
        reached.set()
        assert release.wait(timeout=3)
        original(self, *args, **kwargs)

    monkeypatch.setattr(SqlAlchemySyncBindingRepository, "bind_unused", held)
    with private_workers(h, database_url, database_connection, contenders=1) as w:
        w.futures.append(w.pool.submit(result_or_code, w.service, h, body))
        try:
            assert reached.wait(timeout=3)
            database_connection.execute("SET LOCAL lock_timeout='2s'")
            database_connection.execute("SET LOCAL statement_timeout='3s'")
            epoch = uuid4() if different_epoch else UUID(body["journal_epoch"])
            # A deliberately non-admission publisher forces the real unique
            # constraint path. Both parent FKs still use actual PG authority rows.
            database_connection.execute(
                (
                    "INSERT INTO sync.device_sync_cursor(user_id,device_id,journal_epoch) "
                    "VALUES(%s,%s,%s)"
                ),
                (h.actor.user_id, h.actor.device_id, epoch),
            )
            database_connection.commit()
        finally:
            release.set()
        result = w.futures[0].result(timeout=8)
        assert (
            result == ("DEVICE_RESET_REQUIRED", 409)
            if different_epoch
            else isinstance(result, dict) and result["state"] == "BOUND"
        )
    assert len(cursors(database_connection)) == 1


def test_real_lock_timeout_is_retryable_and_settles_without_cursor(
    qualified: BindingHarness, database_url: str, database_connection: Connection[Any]
) -> None:
    from autplay.adapters.postgresql.resource_limits import RESOURCE_ADMISSION_LOCK

    h = qualified
    body = h.request()
    database_connection.execute("SELECT pg_advisory_xact_lock(%s)", (RESOURCE_ADMISSION_LOCK,))
    started = monotonic()
    try:
        with private_workers(h, database_url, database_connection, contenders=1) as w:
            w.futures.append(w.pool.submit(result_or_code, w.service, h, body))
            assert w.ready.wait(timeout=3)
            assert w.futures[0].result(timeout=8) == ("sync_binding_unavailable", 503)
    finally:
        database_connection.rollback()
    assert monotonic() - started < 12 and not cursors(database_connection)


def wait_for_blocker(observer: Connection[Any], waiting: int, holder: int) -> None:
    end = monotonic() + 3
    while monotonic() < end:
        blockers = present(observer.execute("SELECT pg_blocking_pids(%s)", (waiting,)).fetchone())[
            0
        ]
        observer.commit()
        if holder in blockers:
            return
        sleep(0.01)
    raise AssertionError("expected actual cursor blocker was not observed")


@pytest.mark.parametrize("foreign_key", ["ACCOUNT", "DEVICE"])
def test_push_holds_cursor_then_inbox_fk_key_share_while_binder_waits(
    qualified: BindingHarness,
    database_url: str,
    database_connection: Connection[Any],
    foreign_key: str,
) -> None:
    h = qualified
    body = h.request()
    assert h.post(body).status_code == 200
    # Both schedules do a REAL inbox INSERT. An explicit first KEY SHARE selects
    # which parent independently exposes FOR UPDATE's old FK/cursor cycle.
    engine = create_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        connect_args={
            "connect_timeout": 2,
            ("options"): (
                "-c lock_timeout=3000 -c statement_timeout=4000 -c transaction_timeout=12000"
            ),
        },
    )
    try:
        with engine.connect() as push:
            transaction = push.begin()
            push_pid = push.scalar(text("SELECT pg_backend_pid()"))
            push.execute(
                text(
                    "SELECT device_id FROM sync.device_sync_cursor WHERE device_id=:device FOR "
                    "UPDATE"
                ),
                {"device": h.actor.device_id},
            )
            try:
                with private_workers(h, database_url, database_connection, contenders=1) as w:
                    w.futures.append(w.pool.submit(result_or_code, w.service, h, body))
                    assert w.ready.wait(timeout=3)
                    wait_for_blocker(database_connection, w.pids[0], push_pid)
                    table, column, value = (
                        ("user_account", "user_id", h.actor.user_id)
                        if foreign_key == "ACCOUNT"
                        else ("device", "device_id", h.actor.device_id)
                    )
                    push.execute(
                        text(
                            f"SELECT {column} FROM account.{table} "
                            f"WHERE {column}=:id FOR KEY SHARE NOWAIT"
                        ),
                        {"id": value},
                    )
                    push.execute(
                        text(
                            "INSERT INTO sync.device_event_inbox(event_id,device_id,user_id,"
                            "device_sequence,event_type,"
                            "schema_version,aggregate_type,aggregate_id,occurred_at,request_"
                            "hash) VALUES(:event,:device,:user,1,"
                            "'B1.Fixture',1,'B1Fixture',:aggregate,:now,:digest)"
                        ),
                        {
                            "event": uuid4(),
                            "device": h.actor.device_id,
                            "user": h.actor.user_id,
                            "aggregate": uuid4(),
                            "now": h.profile.clock["now"],
                            "digest": b"f" * 32,
                        },
                    )
                    push.execute(
                        text(
                            "UPDATE sync.device_sync_cursor SET last_acked_device_sequence=1 WHERE "
                            "device_id=:device"
                        ),
                        {"device": h.actor.device_id},
                    )
                    transaction.commit()
                    result = w.futures[0].result(timeout=6)
                    assert isinstance(result, dict) and result["state"] == "BOUND"
            finally:
                if transaction.is_active:
                    transaction.rollback()
        row = database_connection.execute(
            "SELECT last_acked_device_sequence FROM sync.device_sync_cursor WHERE device_id=%s",
            (h.actor.device_id,),
        ).fetchone()
        database_connection.commit()
        assert row == (1,)
    finally:
        engine.dispose()


@pytest.mark.parametrize("writer", ["REVOKE", "LOGOUT", "DISABLE", "REVISION"])
def test_binder_waits_behind_authority_writer_then_reads_fresh(
    qualified: BindingHarness, database_url: str, database_connection: Connection[Any], writer: str
) -> None:
    h = qualified
    body = h.request()
    # The writer owns resource admission before B1 reaches its authority rows.
    from autplay.adapters.postgresql.resource_limits import RESOURCE_ADMISSION_LOCK

    database_connection.execute("SELECT pg_advisory_xact_lock(%s)", (RESOURCE_ADMISSION_LOCK,))
    holder = database_connection.info.backend_pid
    with private_workers(h, database_url, database_connection, contenders=1) as w:
        w.futures.append(w.pool.submit(result_or_code, w.service, h, body))
        assert w.ready.wait(timeout=3)
        # Use a separate observer; committing the writer would release the lock.
        with h.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
            end = monotonic() + 3
            while monotonic() < end:
                if holder in observer.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": w.pids[0]}
                ):
                    break
                sleep(0.01)
            else:
                raise AssertionError("binder did not wait behind admission")
        if writer == "REVOKE":
            database_connection.execute(
                "UPDATE account.device SET revoked_at=now() WHERE device_id=%s",
                (h.actor.device_id,),
            )
        elif writer == "LOGOUT":
            database_connection.execute(
                "UPDATE account.user_session SET revoked_at=now() WHERE session_id=%s",
                (h.actor.session_id,),
            )
        elif writer == "DISABLE":
            h.set_policy(binding_enabled=False)
        else:
            database_connection.execute(
                "UPDATE account.resource_quota_policy SET "
                "default_devices=default_devices+1,revision=revision+1"
            )
        database_connection.commit()
        expected = (
            ("authentication_required", 401)
            if writer in {"REVOKE", "LOGOUT"}
            else (
                ("sync_binding_unavailable", 503)
                if writer == "DISABLE"
                else ("sync_binding_capability_changed", 409)
            )
        )
        assert w.futures[0].result(timeout=7) == expected
    assert not cursors(database_connection)


def test_actual_rotation_and_bind_use_two_real_locked_sessions(
    qualified: BindingHarness, database_url: str, database_connection: Connection[Any]
) -> None:
    h = qualified
    body = h.request()
    rotation, refresh = h.profile.rotation(h.binding, h.enrollment, h.refresh)
    raw = signed(rotation, h.key, ROTATE_DOMAIN)  # ONE original device proof
    with private_workers(h, database_url, database_connection, contenders=2) as w:
        rotate = DeviceProfileServiceV2(
            w.sessions,
            identity=w.identity,
            enabled=lambda family: True,
            access_tokens=h.profile.identity._access,
            access_ttl=h.profile.identity._access_ttl,
        )
        w.futures.append(w.pool.submit(result_or_code, w.service, h, body))
        w.futures.append(w.pool.submit(rotate.session_rotate, raw))
        _, pending = wait(w.futures, timeout=12)
        assert not pending and len(set(w.pids)) == 2
        binding_result = w.futures[0].result(timeout=0)
        rotated, replayed = w.futures[1].result(timeout=0)
        assert not replayed
        assert isinstance(binding_result, dict) or binding_result == (
            "authentication_required",
            401,
        )
    h.actor = replace(h.actor, session_id=UUID(rotated["session_id"]))
    h.token, h.refresh = rotated["access_token"], refresh
    now = present(database_connection.execute("SELECT clock_timestamp()").fetchone())[0]
    database_connection.commit()
    h.profile.clock["now"] = require_instance(now, datetime)
    assert h.post(body).status_code == 200
    assert len(cursors(database_connection)) == 1


@pytest.mark.parametrize("bootstrap_before_read", [True, False])
def test_actual_unbound_bootstrap_overlaps_prior_use_read_without_publishing_bound(
    qualified: BindingHarness,
    database_url: str,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_before_read: bool,
) -> None:
    from autplay.adapters.postgresql.sync_binding_v2 import (
        SqlAlchemySyncBindingRepository as Repository,
    )

    h = qualified
    body, reached, release = h.request(), Event(), Event()
    original = Repository.has_prior_use

    def coordinated(self: Any, device: UUID) -> bool:
        result = False if bootstrap_before_read else original(self, device)
        reached.set()
        assert release.wait(timeout=3)
        return original(self, device) if bootstrap_before_read else result

    monkeypatch.setattr(Repository, "has_prior_use", coordinated)
    with private_workers(h, database_url, database_connection, contenders=1) as w:
        sync = SyncService(
            w.sessions.kw["bind"], cursor_secret=b"b1-bootstrap-fixture-cursor-secret-32bytes"
        )
        w.futures.append(w.pool.submit(result_or_code, w.service, h, body))
        try:
            assert reached.wait(timeout=3)
            snapshot = sync.bootstrap(
                h.actor,
                {
                    "protocol_version": 1,
                    "device_id": body["device_id"],
                    "server_profile_id": body["server_profile_id"],
                    "journal_epoch": body["journal_epoch"],
                    "reason": "FIRST_SYNC",
                    "snapshot_id": None,
                    "page_token": None,
                    "pending_local_event_count": 0,
                },
            )
            assert snapshot["snapshot_id"] and not cursors(database_connection)
        finally:
            release.set()
        result = w.futures[0].result(timeout=7)
        if bootstrap_before_read:
            assert result == ("DEVICE_RESET_REQUIRED", 409) and not cursors(database_connection)
        else:
            # The first binder linearizes its prior-use observation before the
            # overlapping snapshot; snapshot success never supplies that bind.
            assert isinstance(result, dict) and result["state"] == "BOUND"


def test_actual_pull_checkpoint_then_waiting_bind_preserves_committed_progress(
    qualified: BindingHarness, database_url: str, database_connection: Connection[Any]
) -> None:
    h = qualified
    body = h.request()
    assert h.post(body).status_code == 200
    wire = {
        name: body[name]
        for name in ("protocol_version", "device_id", "server_profile_id", "journal_epoch")
    }
    fact = _event(
        h.actor,
        uuid4(),
        1,
        "USER_TRACK_REF_CREATED",
        "USER_TRACK_REF",
        {"title": "B1 pull fixture"},
    )
    assert (
        h.client.post(
            "/api/v1/sync/push", json={**wire, "events": [fact]}, headers=h.headers()
        ).status_code
        == 200
    )
    page = h.client.get("/api/v1/sync/pull", params=wire, headers=h.headers()).json()
    locked, release, pull_pid = Event(), Event(), []
    with private_workers(h, database_url, database_connection, contenders=1) as w:
        engine = w.sessions.kw["bind"]
        sync = SyncService(engine, cursor_secret=b"b1-pull-fixture-cursor-secret-32bytes")
        # Obtain a cursor under THIS service's real codec, then ACK its page.
        page = sync.pull(h.actor, wire)

        def held(
            conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
        ) -> None:
            if statement.lstrip().startswith("UPDATE sync.device_sync_cursor"):
                pull_pid.append(cursor.connection.info.backend_pid)
                locked.set()
                assert release.wait(timeout=3)

        event.listen(engine, "after_cursor_execute", held)
        try:
            w.futures.append(
                w.pool.submit(sync.pull, h.actor, {**wire, "cursor": page["next_cursor"]})
            )
            assert locked.wait(timeout=3)
            w.futures.append(w.pool.submit(result_or_code, w.service, h, body))
            assert w.ready.wait(timeout=3)
            wait_for_blocker(database_connection, w.pids[0], pull_pid[0])
        finally:
            release.set()
        try:
            assert w.futures[0].result(timeout=7)["protocol_version"] == 1
            assert w.futures[1].result(timeout=7)["state"] == "BOUND"
        finally:
            event.remove(engine, "after_cursor_execute", held)
    row = database_connection.execute(
        (
            "SELECT last_pulled_server_sequence,last_acked_device_sequence FROM "
            "sync.device_sync_cursor WHERE device_id=%s"
        ),
        (h.actor.device_id,),
    ).fetchone()
    database_connection.commit()
    assert row is not None and row[0] > 0 and row[1] == 1


def binding_state(connection: Connection[Any]) -> tuple[object, ...]:
    result = (
        snapshot(connection),
        cursors(connection),
        connection.execute(
            "SELECT * FROM account.device_capability_revision ORDER BY user_id,device_id"
        ).fetchall(),
    )
    connection.commit()
    return result


def await_binding_settlement(runtime: Any, *, seconds: float = 20) -> None:
    end = monotonic() + seconds
    while monotonic() < end:
        with runtime._lock:
            if not runtime._active:
                assert (
                    require_instance(runtime.engine.pool, AsyncAdaptedQueuePool).checkedout() == 0
                )
                return
        sleep(0.01)
    raise AssertionError("B1 connection/cancellation owner did not settle")


def await_binding_backend_disposal(
    connection: Connection[Any], *, name: str | None = None, pid: int | None = None
) -> None:
    """Observe server exit after client close without reusing a statistics snapshot."""
    assert (name is None) != (pid is None)
    end = monotonic() + 5
    while True:
        # PostgreSQL Terminate has no acknowledgement: client close can precede
        # backend exit. End each observer transaction so pg_stat_activity is fresh.
        connection.commit()
        remaining = present(
            connection.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND (application_name=%s OR pid=%s)",
                (name, pid),
            ).fetchone()
        )[0]
        connection.commit()
        if remaining == 0:
            return
        assert monotonic() < end, "B1 backend remained after bounded disposal observation"
        sleep(0.01)


def test_exhausting_all_ten_main_connections_does_not_queue_binding_auth_or_discovery(
    qualified: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = qualified
    body = h.request()
    # This is the API's actual main engine, not the fixture identity's engine.
    main = require_instance(
        h.client.app, FastAPI
    ).state.auth_service.unit_of_work_factory._session_factory.kw["bind"]
    runtime = require_instance(h.client.app, FastAPI).state.sync_binding_database
    with ExitStack() as held:
        connections = [held.enter_context(main.connect()) for _ in range(10)]
        assert len(connections) == main.pool.checkedout() == 10
        started = monotonic()
        assert h.client.get(PREFIX + "/discovery").status_code == 200
        assert h.capabilities()["capability_revision"] == body["expected_capability_revision"]
        before = binding_state(database_connection)
        first = h.post(body)
        assert first.status_code == 200 and monotonic() - started < 15
        assert snapshot(database_connection) == before[0]
        await_binding_settlement(runtime)
    assert main.pool.checkedout() == 0
    assert h.post(body).json() == first.json()


def test_exhausted_dedicated_pool_has_exact_503_no_effects_and_releases_checkouts(
    qualified: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = qualified
    body, entered, release = h.request(), Event(), Event()
    runtime = require_instance(h.client.app, FastAPI).state.sync_binding_database
    before = binding_state(database_connection)

    async def hold_pool() -> None:
        async with runtime.engine.connect(), runtime.engine.connect():
            entered.set()
            async with asyncio.timeout(20):
                while not release.is_set():
                    await asyncio.sleep(0.01)

    holder = asyncio.run_coroutine_threadsafe(hold_pool(), runtime._loop)
    try:
        assert (
            entered.wait(timeout=3)
            and require_instance(runtime.engine.pool, AsyncAdaptedQueuePool).checkedout() == 2
        )
        assert_error(
            h.client.post(
                PREFIX + "/bind",
                content=b"{",
                headers={"Authorization": "Bearer invalid", "Content-Type": "text/plain"},
            ),
            "sync_binding_media_type_invalid",
            415,
        )
        assert_error(
            h.client.post(
                PREFIX + "/bind",
                content=b"{",
                headers={"Authorization": "Bearer invalid", "Content-Type": "application/json"},
            ),
            "authentication_required",
            401,
        )
        for operation in ("discovery", "capabilities", "bind"):
            started = monotonic()
            response = (
                h.post(body)
                if operation == "bind"
                else h.client.get(PREFIX + "/" + operation, headers=h.headers())
            )
            assert_error(response, "sync_binding_unavailable", 503)
            assert monotonic() - started < 2
            assert binding_state(database_connection) == before
    finally:
        release.set()
        holder.result(timeout=5)  # actual holder close, not just response elapsed
        await_binding_settlement(runtime)
    assert h.post(body).status_code == 200


@pytest.mark.parametrize(
    "phase",
    ["DNS", "CONNECT", "FIRST_DIALECT", "CHECKOUT", "AUTH_QUERY", "BEFORE_COMMIT", "AFTER_COMMIT"],
)
@pytest.mark.parametrize("expire", [False, True])
def test_real_pg_delayed_admission_cancels_io_retains_cleanup_then_reconciles(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    expire: bool,
) -> None:
    """Actual connections and real auth; only a bounded await barrier is added.

    Separate assertions cover response deadline, actual cancellation delivery,
    retained owner/slot during delayed cleanup, settled checkout and server-side
    connection disposal. The fast control releases the SAME barrier normally.
    """
    import autplay.adapters.postgresql.sync_binding_v2 as adapter

    h = qualified
    body = h.request()
    entered, release, cancelled, cleanup_release = Event(), Event(), Event(), Event()
    name = "b1-deadline-fixture-" + uuid4().hex
    real_create = create_async_engine
    opened: list[Any] = []
    commit_listeners: list[tuple[str, Any]] = []
    committed_state: Any = None
    request: Any = None

    async def pause() -> None:
        entered.set()
        try:
            async with asyncio.timeout(18):
                while not release.is_set():
                    await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            cancelled.set()
            # Deliberately delay cleanup beyond the response. The production
            # owner must retain its future and slot until this settles.
            async with asyncio.timeout(5):
                while not cleanup_release.is_set():
                    await asyncio.sleep(0.01)
            raise

    def create(url: Any, **options: Any) -> AsyncEngine:
        options["connect_args"] = {**options["connect_args"], "application_name": name}
        if phase in {"DNS", "CONNECT"}:
            args, params = h.engine.dialect.create_connect_args(url)
            params.update(options["connect_args"])

            def resolve() -> Sequence[tuple[object, ...]]:
                # Real resolver result, same authoritative host/port. Holding
                # its RETURN models OS DNS that ignores asyncio cancellation.
                result = socket.getaddrinfo(url.host, url.port or 5432, type=socket.SOCK_STREAM)
                entered.set()
                assert release.wait(timeout=20)
                return result

            async def connect() -> AsyncConnection[tuple[object, ...]]:
                if phase == "DNS":
                    try:
                        await asyncio.get_running_loop().run_in_executor(None, resolve)
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise
                connection = await AsyncConnection.connect(*args, **params)  # REAL PG
                opened.append(connection)
                try:
                    if phase == "CONNECT":
                        await pause()  # before transferring ownership to the pool
                    return connection
                except BaseException:
                    await connection.close()
                    raise

            options["async_creator"] = connect
        engine = real_create(url, **options)
        if phase == "FIRST_DIALECT":
            initialize = engine.sync_engine.dialect.initialize

            def delayed_initialize(connection: Any) -> None:
                connection.connection.dbapi_connection.run_async(lambda raw: pause())
                initialize(connection)

            monkeypatch.setattr(engine.sync_engine.dialect, "initialize", delayed_initialize)
        elif phase == "CHECKOUT":

            def checkout(dbapi: Any, record: Any, proxy: Any) -> None:
                dbapi.run_async(lambda raw: pause())

            event.listen(engine.sync_engine, "checkout", checkout)
        elif phase == "AUTH_QUERY":

            def before_query(connection: Any, cursor: Any, statement: str, *args: Any) -> None:
                if "account.user_session" in statement and not entered.is_set():
                    connection.connection.dbapi_connection.run_async(lambda raw: pause())

            event.listen(engine.sync_engine, "before_cursor_execute", before_query)
        elif phase in {"BEFORE_COMMIT", "AFTER_COMMIT"}:

            def inserted(connection: Any, cursor: Any, statement: str, *args: Any) -> None:
                if statement.startswith("INSERT INTO sync.device_sync_cursor"):
                    connection.info["b1_fixture_mutation"] = True

            def before_commit(session: Session) -> None:
                if session.get_bind() is not engine.sync_engine or entered.is_set():
                    return
                connection = session.connection()
                if connection.info.pop("b1_fixture_mutation", False):
                    dbapi = connection.connection.dbapi_connection
                    if phase == "BEFORE_COMMIT":
                        present(dbapi).run_async(lambda raw: pause())
                    else:
                        session.info["b1_fixture_commit_connection"] = dbapi

            def after_commit(session: Session) -> None:
                dbapi = session.info.pop("b1_fixture_commit_connection", None)
                if dbapi is not None:
                    # SQLAlchemy after_commit is AFTER the genuine DBAPI COMMIT.
                    # No SQL is emitted from this committed Session. The saved
                    # public adapted connection only supplies the await barrier;
                    # a separate PG connection below proves the durable outcome.
                    dbapi.run_async(lambda raw: pause())

            event.listen(engine.sync_engine, "after_cursor_execute", inserted)
            for kind, listener in (
                ("before_commit", before_commit),
                ("after_commit", after_commit),
            ):
                event.listen(Session, kind, listener)
                commit_listeners.append((kind, listener))
        return engine

    monkeypatch.setattr(adapter, "create_async_engine", create)
    application = create_app(h.settings, profile_pairing_service=h.profile.identity)
    runtime = application.state.sync_binding_database
    before = binding_state(database_connection)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with TestClient(application, follow_redirects=False) as client:

            def send() -> tuple[Response, float]:
                started = monotonic()
                response = client.post(
                    PREFIX + "/bind", content=rfc8785.dumps(body), headers=h.headers()
                )
                return response, monotonic() - started

            request = pool.submit(send)
            try:
                assert entered.wait(timeout=4)
                if phase == "AFTER_COMMIT":
                    # Independently visible BEFORE release/timeout/response;
                    # this is a successful-but-lost COMMIT, never a fake BOUND.
                    row = database_connection.execute(
                        "SELECT user_id,device_id,journal_epoch,"
                        "last_pulled_server_sequence,last_acked_device_sequence,last_suc"
                        "cessful_sync_at "
                        "FROM sync.device_sync_cursor WHERE device_id=%s",
                        (h.actor.device_id,),
                    ).fetchone()
                    database_connection.commit()
                    assert row == (
                        h.actor.user_id,
                        h.actor.device_id,
                        UUID(body["journal_epoch"]),
                        0,
                        0,
                        None,
                    )
                    committed_state = binding_state(database_connection)
                    assert committed_state[0] == before[0] and committed_state[2] == before[2]
                elif phase == "BEFORE_COMMIT":
                    assert (
                        binding_state(database_connection) == before
                    )  # uncommitted insert is invisible
                if not expire:
                    release.set()
                response, elapsed = request.result(timeout=17)
                if expire:
                    assert_error(response, "sync_binding_unavailable", 503)
                    assert 14 <= elapsed < 16.5
                    assert cancelled.wait(timeout=1)  # actual I/O timeout, not elapsed alone
                    with runtime._lock:
                        assert len(runtime._active) == 1  # cleanup retains admission slot
                    assert binding_state(database_connection) == (
                        committed_state if phase == "AFTER_COMMIT" else before
                    )
                else:
                    assert response.status_code == 200, response.json()
                    assert not cancelled.is_set() and elapsed < 10
            finally:
                release.set()
                cleanup_release.set()
                request.result(timeout=7)
                await_binding_settlement(runtime, seconds=7)
            if expire and phase == "BEFORE_COMMIT":
                # Only NOW has the actual owner acknowledged rollback/close.
                # A cancelled response or invisible uncommitted row alone did
                # not prove absence of a later commit.
                assert binding_state(database_connection) == before
            # Same real original, same DB/identity/revision after failure or loss.
            retried = client.post(
                PREFIX + "/bind", content=rfc8785.dumps(body), headers=h.headers()
            )
            assert retried.status_code == 200
            result = retried.json()
            assert result["state"] == "BOUND" and all(
                result[key] == body[key]
                for key in ("user_id", "device_id", "server_profile_id", "journal_epoch")
            )
            if phase == "AFTER_COMMIT":
                # Entire cursor row (timestamps/checkpoints/classifications),
                # authority rows and shared revision retain every column value.
                assert binding_state(database_connection) == committed_state
            await_binding_settlement(runtime)
    finally:
        release.set()
        cleanup_release.set()
        _, pending = wait([request] if request is not None else [], timeout=8)
        pool.shutdown(wait=False, cancel_futures=True)
        for kind, listener in commit_listeners:
            event.remove(Session, kind, listener)
        assert not pending, "B1 HTTP fixture worker did not settle within its bound"
    assert not runtime._thread.is_alive() and not runtime._active
    assert all(connection.closed for connection in opened)
    await_binding_backend_disposal(database_connection, name=name)


@pytest.mark.parametrize("cleanup_kind", ["DNS", "TRANSACTION"])
def test_shutdown_while_both_cancelled_owners_retain_cleanup_and_slots(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    cleanup_kind: str,
) -> None:
    """UNEXECUTED: close happens while BOTH real operation owners are held.

    Cancellation of a response, a callback-controlled owner task, and a close
    observer are separate cuts. No latch is released until admission retention,
    deferred owner-loop close and absence of disposal have all been asserted.
    The actual 15s timer is retained. Every barrier/poll/join has a finite bound.
    """
    import autplay.adapters.postgresql.sync_binding_v2 as adapter
    from autplay.entrypoints.composition import build_sync_binding_authenticator

    h = qualified
    body = h.request()
    parsed = parse_binding_request(rfc8785.dumps(body))
    before = binding_state(database_connection)
    name = "b1-close-fixture-" + uuid4().hex
    release, disposing, disposed, callback_done = Event(), Event(), Event(), Event()
    entered, cancelled, resolver_closes, resolver_callback_closes = [], [], [], []
    mutex = Lock()
    real_create = create_async_engine

    async def nonwaiting_owned_close() -> BindingCloseResult:
        # The real owner path must return REQUESTED without suspending. Drive
        # it once inside a running loop, then close the observer on any unexpected
        # suspension. A regression must fail this fixture, not strand a worker
        # in a cyclic shutdown that even wait_for cancellation could not break.
        call = runtime.shutdown()
        try:
            try:
                call.send(None)
            except StopIteration as completed:
                return require_instance(completed.value, BindingCloseResult)
            raise AssertionError("owned B1 shutdown unexpectedly suspended")
        finally:
            call.close()

    def create(url: Any, **options: Any) -> AsyncEngine:
        options["connect_args"] = {**options["connect_args"], "application_name": name}
        if cleanup_kind == "DNS":
            args, params = h.engine.dialect.create_connect_args(url)
            params.update(options["connect_args"])

            def resolve() -> Sequence[tuple[object, ...]]:
                addresses = socket.getaddrinfo(url.host, url.port or 5432, type=socket.SOCK_STREAM)
                with mutex:
                    entered.append(True)
                assert release.wait(timeout=24), "held OS resolver exceeded fixture bound"
                # Reentrant close from an OWNED executor is deferred too. An
                # executor joining the DB thread here would form the old cycle.
                result = asyncio.run(nonwaiting_owned_close())
                with mutex:
                    resolver_closes.append(result)
                return addresses

            async def connect() -> AsyncConnection[tuple[object, ...]]:
                try:
                    await asyncio.get_running_loop().run_in_executor(None, resolve)
                except asyncio.CancelledError:
                    with mutex:
                        cancelled.append(True)
                    raise
                return await AsyncConnection.connect(*args, **params)

            options["async_creator"] = connect
        return real_create(url, **options)

    monkeypatch.setattr(adapter, "create_async_engine", create)
    runtime = adapter.BindingDatabaseRuntime(h.engine.url)
    if cleanup_kind == "DNS":
        actual_submit = runtime._resolvers.submit

        def submit(fn: Any, *args: Any, **kwargs: Any) -> object:
            future = actual_submit(fn, *args, **kwargs)

            def after_resolver(done: Any) -> None:
                result = asyncio.run(nonwaiting_owned_close())
                with mutex:
                    resolver_callback_closes.append(result)

            # The held resolver cannot finish before this callback attaches.
            # It executes on that worker AFTER the resolver function returned.
            future.add_done_callback(after_resolver)
            return future

        monkeypatch.setattr(runtime._resolvers, "submit", submit)
    if cleanup_kind == "TRANSACTION":

        async def hold_auth() -> None:
            with mutex:
                entered.append(True)
            try:
                async with asyncio.timeout(18):
                    while not release.is_set():
                        await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                with mutex:
                    cancelled.append(True)
                # Transaction context still owns rollback/close. Observers must
                # not confuse timeout/cancel delivery with its acknowledgement.
                async with asyncio.timeout(8):
                    while not release.is_set():
                        await asyncio.sleep(0.01)
                raise

        def hold_query(connection: Any, cursor: Any, statement: str, *args: Any) -> None:
            if "account.user_session" in statement and not release.is_set():
                connection.connection.dbapi_connection.run_async(lambda raw: hold_auth())

        event.listen(runtime.engine.sync_engine, "before_cursor_execute", hold_query)
    real_dispose = type(runtime.engine).dispose

    async def observe_dispose(engine: Any, *args: Any, **kwargs: Any) -> None:
        if engine is runtime.engine:
            disposing.set()
            assert release.is_set() and not runtime._active
        await real_dispose(engine, *args, **kwargs)
        if engine is runtime.engine:
            disposed.set()

    monkeypatch.setattr(type(runtime.engine), "dispose", observe_dispose)
    authentication = require_instance(h.client.app, FastAPI).state.auth_service

    def invoke(sessions: Any, budget: Any) -> object:
        actor, verified = build_sync_binding_authenticator(authentication, sessions)(h.token)
        assert actor == h.actor  # genuine codec, live auth repository and session
        service = SyncBindingV2Service(sessions, identity=h.profile.identity, policy=h.policy)
        return service.bind(actor, verified, parsed)

    async def until(predicate: Any, seconds: float) -> None:
        async with asyncio.timeout(seconds):
            while not predicate():
                await asyncio.sleep(0.01)

    async def scenario() -> None:
        callers: list[asyncio.Task[Any]] = []
        shutdown: asyncio.Task[Any] | None = None
        runtime.start()
        try:
            callers = [asyncio.create_task(runtime.run(invoke)) for _ in range(2)]
            await until(lambda: len(entered) == 2, 4)
            with runtime._lock:
                owners = tuple(runtime._active)
                assert len(owners) == 2 and all(not item.done() for item in owners)
            rows = database_connection.execute(
                "SELECT pid,xact_start FROM pg_stat_activity "
                "WHERE datname=current_database() AND application_name=%s",
                (name,),
            ).fetchall()
            database_connection.commit()
            assert len(rows) == (2 if cleanup_kind == "TRANSACTION" else 0)
            assert all(row[1] is not None for row in rows)
            # Exhaustion BEFORE global close must be real slot retention.
            with pytest.raises(SyncBindingV2Error) as full:
                await asyncio.wait_for(runtime.run(invoke), timeout=1)
            assert (full.value.code, full.value.status) == ("sync_binding_unavailable", 503)
            for caller in callers:
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(caller, timeout=1)

            def cancel_owner_tasks() -> None:
                assert len(runtime._tasks) == 2
                for task in tuple(runtime._tasks):
                    task.cancel()
                runtime._loop.call_soon(lambda: [task.cancel() for task in tuple(runtime._tasks)])
                callback_done.set()

            runtime._loop.call_soon_threadsafe(cancel_owner_tasks)
            await until(callback_done.is_set, 1)
            await until(lambda: len(cancelled) == 2, 17)  # actual I/O timer/cancel delivery
            with runtime._lock:
                assert set(owners) == runtime._active and all(not item.done() for item in owners)
            with pytest.raises(SyncBindingV2Error):
                await asyncio.wait_for(runtime.run(invoke), timeout=1)
            assert binding_state(database_connection) == before
            assert not release.is_set() and not disposing.is_set()

            async def owner_close() -> BindingCloseResult:
                result = await runtime.shutdown()
                assert result is adapter.BindingCloseResult.REQUESTED
                with pytest.raises(RuntimeError, match="external"):
                    await runtime.await_closed()
                return result

            requested = asyncio.run_coroutine_threadsafe(owner_close(), runtime._loop)
            assert (
                await asyncio.wait_for(asyncio.wrap_future(requested), timeout=1)
                is adapter.BindingCloseResult.REQUESTED
            )
            close = runtime._shutdown
            assert close is not None and not close.done() and not close.cancel()
            observer = asyncio.create_task(runtime.await_closed())
            await asyncio.sleep(0)
            observer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(observer, timeout=1)
            # shutdown is the lifespan API: it defers cancellation until settled
            # so the lifespan's finally cannot dispose its shared engine early.
            shutdown = asyncio.create_task(runtime.shutdown())
            await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0.05)
            assert not shutdown.done() and not close.done() and runtime._shutdown is close
            assert runtime._thread.is_alive() and runtime._loop.is_running()
            assert not disposing.is_set() and not disposed.is_set()
            assert all(not item.done() for item in owners)
            # Only now may real DNS return / real transaction rollback proceed.
            release.set()
            assert (
                await asyncio.wait_for(runtime.await_closed(), timeout=7)
                is adapter.BindingCloseResult.CLOSED
            )
            await until(shutdown.done, 1)
            with pytest.raises(asyncio.CancelledError):
                shutdown.result()
            async with asyncio.timeout(1):
                assert await runtime.await_closed() is adapter.BindingCloseResult.CLOSED
                assert await runtime.shutdown() is adapter.BindingCloseResult.CLOSED
            assert runtime._shutdown is close and disposed.is_set()
        finally:
            release.set()
            runtime.request_close()
            await asyncio.wait_for(runtime.await_closed(), timeout=8)
            if callers:
                await asyncio.wait_for(asyncio.gather(*callers, return_exceptions=True), timeout=1)
            if shutdown is not None:
                await until(shutdown.done, 1)

    asyncio.run(scenario())
    runtime._thread.join(timeout=1)
    assert runtime._closer is not None
    runtime._closer.join(timeout=1)
    assert not runtime._thread.is_alive() and not runtime._closer.is_alive()
    assert (
        runtime._loop.is_closed()
        and not runtime._active
        and require_instance(runtime.engine.pool, AsyncAdaptedQueuePool).checkedout() == 0
    )
    if cleanup_kind == "DNS":
        assert resolver_closes == [adapter.BindingCloseResult.REQUESTED] * 2
        assert resolver_callback_closes == [adapter.BindingCloseResult.REQUESTED] * 2
    assert (
        binding_state(database_connection) == before
    )  # after acknowledged cleanup, before any retry
    await_binding_backend_disposal(database_connection, name=name)
    # An independent still-live API runtime uses the same authenticator and DB.
    assert h.post(body).status_code == 200


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("failure_kind", ["NONE", "ERROR", "CANCELLED"])
def test_repeated_close_observers_replay_exact_settled_outcome_without_self_wait(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    started: bool,
    failure_kind: str,
) -> None:
    """UNEXECUTED: even a close failure typed CancelledError is a stable outcome.

    Disposal still performs its real work before the synthetic failure cut. A
    failure must not be silently replaced by CLOSED or retried forever, and an
    unstarted runtime must also settle its loop/executor rather than just close.
    """
    import autplay.adapters.postgresql.sync_binding_v2 as adapter
    from autplay.entrypoints.composition import build_sync_binding_authenticator

    h = qualified
    runtime = adapter.BindingDatabaseRuntime(h.engine.url)
    before = binding_state(database_connection)
    failure = (
        asyncio.CancelledError("synthetic B1 close failure")
        if failure_kind == "CANCELLED"
        else RuntimeError("synthetic B1 close failure")
    )
    actual_dispose = type(runtime.engine).dispose
    disposals: list[bool] = []

    async def dispose(engine: Any, *args: Any, **kwargs: Any) -> None:
        await actual_dispose(engine, *args, **kwargs)
        if engine is runtime.engine:
            disposals.append(True)
            if failure_kind != "NONE":
                raise failure

    monkeypatch.setattr(type(runtime.engine), "dispose", dispose)

    def authenticated(sessions: Any, budget: Any) -> int:
        actor, verified = build_sync_binding_authenticator(
            require_instance(h.client.app, FastAPI).state.auth_service, sessions
        )(h.token)
        assert actor == h.actor and verified.device_id == h.actor.device_id
        with sessions() as session:
            return require_instance(session.scalar(text("SELECT pg_backend_pid()")), int)

    async def scenario() -> int | None:
        pid = None
        try:
            if started:
                runtime.start()
                pid = await asyncio.wait_for(runtime.run(authenticated), timeout=5)
            async with asyncio.timeout(5):
                for observe in (runtime.await_closed, runtime.shutdown, runtime.await_closed):
                    if failure_kind == "NONE":
                        assert await observe() is adapter.BindingCloseResult.CLOSED
                    else:
                        with pytest.raises(type(failure)) as retained:
                            await observe()
                        assert retained.value is failure
            return pid
        finally:
            runtime.request_close()
            async with asyncio.timeout(5):
                if failure_kind == "NONE":
                    await runtime.await_closed()
                else:
                    with pytest.raises(type(failure)):
                        await runtime.await_closed()

    pid = asyncio.run(scenario())
    assert runtime._closer is not None
    runtime._closer.join(timeout=1)
    if started:
        runtime._thread.join(timeout=1)
    assert not runtime._closer.is_alive() and not runtime._thread.is_alive()
    assert disposals == [True] and runtime._loop.is_closed() and not runtime._active
    if pid is not None:
        await_binding_backend_disposal(database_connection, pid=pid)
    assert binding_state(database_connection) == before
