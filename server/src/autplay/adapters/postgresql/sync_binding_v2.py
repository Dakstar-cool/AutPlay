"""Cursor-only B1 persistence and bounded READ COMMITTED transactions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import datetime
from enum import Enum
from threading import BoundedSemaphore, Event, Lock, Thread, current_thread, local
from time import monotonic
from typing import Any, TypeVar
from uuid import UUID

from psycopg import Error as PostgreSQLError
from sqlalchemy import URL, Connection, event, exists, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.auth import Principal
from autplay.domain.sync_binding_v2 import SyncBindingV2Error
from autplay.ports.sync_binding_v2 import BindingCursor

from .device_key_history import DEVICE_KEY_HISTORY_OWNERS
from .models.device_compatibility import DeviceCapabilityRevisionRow
from .models.sync import BootstrapSessionRow, DeviceEventInboxRow, DeviceSyncCursorRow, SyncEventRow


def require_binding_storage(session: Session) -> None:
    """Read schema/permission readiness without reading any personal row.

    Source qualification never substitutes for the serving database's readiness.
    Missing required evidence storage is unavailable even for public discovery.
    """
    for owner in (*DEVICE_KEY_HISTORY_OWNERS, DeviceCapabilityRevisionRow, DeviceSyncCursorRow):
        session.execute(select(owner.__table__).limit(0)).close()


class DatabaseDeadline:
    """One total deadline, including admission/auth/connect/read/commit."""

    def __init__(self, *, timeout_seconds: float = 15) -> None:
        if not 0 < timeout_seconds <= 15:
            raise SyncBindingV2Error("sync_binding_unavailable", 503)
        self._end = monotonic() + timeout_seconds
        self._stopped = Event()
        self.resolvers: list[Future[Any]] = []
        self.transaction_timeout_installed = False
        self.database_failed = False

    def remaining(self) -> float:
        self.check()
        return self._end - monotonic()

    def stop(self) -> None:
        self._stopped.set()

    def check(self) -> None:
        if self._stopped.is_set() or monotonic() >= self._end:
            raise SyncBindingV2Error("sync_binding_unavailable", 503)

    def before_statement(
        self,
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        self.check()
        try:
            # Separate ordinary cursor on the SAME transaction, never DECLARE
            # ... FOR SET. Use SQLAlchemy's public adapted DBAPI connection:
            # cursor.connection on async psycopg is the raw AsyncConnection.
            dbapi = conn.connection.dbapi_connection
            if dbapi is None:
                raise RuntimeError("database connection unavailable")
            settings_cursor = dbapi.cursor()
            try:
                if not self.transaction_timeout_installed:
                    remaining = max(1, int((self._end - monotonic()) * 1000))
                    settings_cursor.execute(
                        "SET LOCAL transaction_timeout = '" + str(remaining) + "ms'"
                    )
                    self.transaction_timeout_installed = True
                milliseconds = max(1, min(5000, int((self._end - monotonic()) * 1000)))
                # Raw DBAPI avoids recursive SQLAlchemy events. Only bounded
                # integers enter SQL; every setting dies with this transaction.
                settings_cursor.execute("SET LOCAL lock_timeout = '" + str(milliseconds) + "ms'")
                settings_cursor.execute(
                    "SET LOCAL statement_timeout = '" + str(milliseconds) + "ms'"
                )
            finally:
                settings_cursor.close()
        except PostgreSQLError:
            raise SyncBindingV2Error("sync_binding_unavailable", 503) from None


_ACTIVE_BUDGET: ContextVar[DatabaseDeadline | None] = ContextVar(
    "binding_database_deadline", default=None
)
_Result = TypeVar("_Result")


def current_deadline() -> DatabaseDeadline:
    return _ACTIVE_BUDGET.get() or DatabaseDeadline()


class _BindingResolverPool(ThreadPoolExecutor):
    """Retain actual OS DNS work when cancellation only stops its awaiter."""

    def __init__(self, **kwargs: Any) -> None:
        self._worker = local()
        super().__init__(initializer=self._mark_worker, **kwargs)

    def _mark_worker(self) -> None:
        # Ownership includes Future callbacks after the resolver function exits.
        self._worker.owned = True

    def owns_current_thread(self) -> bool:
        return getattr(self._worker, "owned", False)

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future = super().submit(fn, *args, **kwargs)
        budget = _ACTIVE_BUDGET.get()
        if budget is not None:
            budget.resolvers.append(future)
        return future


class BindingCloseResult(Enum):
    REQUESTED = "REQUESTED"
    CLOSED = "CLOSED"


class BindingDatabaseRuntime:
    """Two admitted operations, one authoritative DB, retained cleanup ownership.

    Five remedies compared (source reasoning only): (1) a dedicated sync pool
    and connect_timeout bound queue/per-host waits, not the entire DNS/dialect
    path; (2) lowering the existing shared pool bounds changes other owners and
    leaves those gaps; (3) NullPool with admission removes queueing but still
    needs cancellable setup; (4) structured async timeout covers setup and SQL
    but needs retained cancellation/cleanup ownership; (5) SQL-only SET LOCAL
    is too late. Choose a dedicated AsyncAdaptedQueuePool with remedy 4 and
    retained tasks/resolvers: bounded same-DB resources, real authenticator and
    existing synchronous services through SQLAlchemy run_sync, no pool mutation.
    https://docs.sqlalchemy.org/en/20/core/pooling.html
    https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
    https://www.psycopg.org/psycopg3/docs/advanced/async.html
    https://www.postgresql.org/docs/current/libpq-connect.html
    https://www.postgresql.org/docs/current/runtime-config-client.html

    A private SelectorEventLoop also supports psycopg async I/O on Windows;
    no process-global event-loop policy or pool internals are changed. There is
    no general threadpool queue and no unsafe interruption of Python threads.
    Timeout/caller cancellation closes admission for THAT operation, not its
    ownership: its task, connection and slot survive through rollback/close.
    OS DNS calls also retain their future/slot until the bounded resolver pool
    actually finishes; cancelling an asyncio DNS awaiter alone cannot stop them.
    COMMIT cancellation can have an unknown outcome. Return unavailable and
    reconcile the same original bind tuple; never infer rollback or retry SQL.
    Shutdown closes all admission, drains these tasks, then disposes the pool.

    Reentrant-close remedies compared against Python 3.14 primary docs:
    (1) split request/await-closed with one stable completion: selected;
    (2) typed owner-loop deferred closure: selected REQUESTED, never CLOSED;
    (3) run_coroutine_threadsafe only from another thread: used by the retained
    closer for disposal, with joining outside ALL owned loops/executors;
    (4) join/block on our own loop/executor: rejected (self/cyclic wait);
    (5) untracked stop callback: rejected (stop discards later continuations).
    https://docs.python.org/3.14/library/asyncio-eventloop.html
    https://docs.python.org/3.14/library/asyncio-task.html
    https://docs.python.org/3.14/library/concurrent.futures.html
    https://docs.python.org/3.14/library/threading.html#threading.Thread.join
    CLOSED means pool disposal, async-generator/default-executor shutdown, loop
    close AND DB-thread join succeeded. A failure is retained as the same exception
    for every external observer; it never becomes CLOSED. OS DNS may outlast the
    response deadline: there is no safe forced thread termination. Awaiters may
    cancel await_closed(), leaving the retained closer and its result intact.
    External shutdown() defers caller cancellation until closure settles so the
    API lifespan cannot dispose its other resources early. Source-only reasoning;
    PG/Windows and all executable qualification remain UNVERIFIED.
    """

    def __init__(
        self,
        authoritative_url: URL,
        *,
        read_only: bool = False,
        application_name: str = "autplay-sync-binding-v2",
    ) -> None:
        # M1 opts into a distinct engine on the same authoritative URL. The
        # default constructor and B1 transaction settings remain unchanged.
        self._read_only = read_only
        options = (
            (
                "-c default_transaction_read_only=on -c statement_timeout=6000 "
                "-c lock_timeout=250 -c idle_in_transaction_session_timeout=6500 "
                "-c transaction_timeout=15000"
            )
            if read_only
            else ("-c statement_timeout=5000 -c lock_timeout=5000 -c transaction_timeout=15000")
        )
        self.engine: AsyncEngine = create_async_engine(
            authoritative_url,
            echo=False,
            hide_parameters=True,
            pool_size=2,
            max_overflow=0,
            pool_timeout=0.25,
            pool_pre_ping=False,
            isolation_level="READ COMMITTED",
            **({"skip_autocommit_rollback": True} if read_only else {}),
            connect_args={
                "connect_timeout": 1 if read_only else 2,
                "application_name": application_name,
                "options": options,
            },
        )
        self._loop = asyncio.SelectorEventLoop()
        self._resolvers = _BindingResolverPool(
            max_workers=2,
            thread_name_prefix=application_name + "-dns" if read_only else "autplay-binding-dns",
        )
        self._loop.set_default_executor(self._resolvers)
        self._thread = Thread(
            target=self._serve,
            name=application_name + "-db" if read_only else "autplay-binding-db",
            daemon=False,
        )
        self._slots, self._lock = BoundedSemaphore(2), Lock()
        self._active: set[Future[Any]] = set()
        self._tasks: set[asyncio.Task[Any]] = set()  # owner-loop access only
        self._closed, self._started = False, False
        self._shutdown: Future[BindingCloseResult] | None = None
        self._closer: Thread | None = None
        self._serve_error: BaseException | None = None
        event.listen(self.engine.sync_engine, "begin", self._begin)
        event.listen(self.engine.sync_engine, "before_cursor_execute", self._before_statement)
        if read_only:
            event.listen(self.engine.sync_engine, "handle_error", self._database_error)

    def _database_error(self, context: Any) -> None:
        # The actual AuthService sanitizes storage failures into invalid auth.
        # Keep only a process-local failure bit for M1's closed 503 mapping;
        # no exception, SQL, parameters, token or identifier is retained/logged.
        budget = _ACTIVE_BUDGET.get()
        if budget is not None:
            budget.database_failed = True

    def _begin(self, connection: Connection) -> None:
        budget = _ACTIVE_BUDGET.get()
        if budget is not None:
            budget.check()
            budget.transaction_timeout_installed = False

    def _before_statement(self, *args: Any) -> None:
        budget = _ACTIVE_BUDGET.get()
        if budget is not None:
            if self._read_only:
                # Read-only M1 has connection-default bounds and its own tighter
                # clock timeout. Do not inject SET LOCAL into AUTOCOMMIT or
                # override that final-clock bound. The async total timer still
                # covers DNS/connect/auth/SQL and retained driver settlement.
                budget.check()
            else:
                budget.before_statement(*args)

    def start(self) -> None:
        with self._lock:
            if self._closed or self._started:
                raise RuntimeError("B1 database runtime cannot start")
            self._thread.start()
            self._started = True

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        except BaseException as error:
            self._serve_error = error
        finally:
            self._finish_loop()

    def _finish_loop(self) -> None:
        for cleanup in (self._loop.shutdown_asyncgens, self._loop.shutdown_default_executor):
            try:
                self._loop.run_until_complete(cleanup())
            except BaseException as error:
                self._serve_error = self._serve_error or error
        try:
            self._loop.close()
        except BaseException as error:
            self._serve_error = self._serve_error or error

    async def run(
        self,
        operation: Callable[[sessionmaker[Session], DatabaseDeadline], _Result],
        *,
        timeout_seconds: float = 15,
    ) -> _Result:
        budget = DatabaseDeadline(timeout_seconds=timeout_seconds)
        with self._lock:
            if self._closed or not self._started or not self._slots.acquire(blocking=False):
                raise SyncBindingV2Error("sync_binding_unavailable", 503)
            # This completion describes actual settlement, not cancellation of
            # a run_coroutine_threadsafe proxy (which can finish before cleanup).
            future: Future[_Result] = Future()
            future.set_running_or_notify_cancel()
            self._active.add(future)
            future.add_done_callback(self._settled)
            try:
                self._loop.call_soon_threadsafe(self._launch, operation, budget, future)
            except BaseException:
                self._active.discard(future)
                self._slots.release()
                raise
        try:
            # shield prevents the response timer/disconnect from abandoning or
            # cancelling the retained owner a second time during cleanup.
            wrapped = asyncio.wrap_future(future)
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            return await asyncio.wait_for(asyncio.shield(wrapped), budget.remaining())
        except TimeoutError, asyncio.CancelledError:
            budget.stop()
            # The worker's OWN timer cancels its I/O once; its finally block
            # still owns cleanup, including a connect that completes late.
            raise

    def _launch(
        self,
        operation: Callable[[sessionmaker[Session], DatabaseDeadline], _Result],
        budget: DatabaseDeadline,
        future: Future[_Result],
    ) -> None:
        owned = self._run_owned(operation, budget)
        try:
            task = self._loop.create_task(owned)
        except BaseException as error:
            owned.close()
            future.set_exception(error)
            return
        self._tasks.add(task)

        def completed(done: asyncio.Task[_Result]) -> None:
            self._tasks.discard(done)
            try:
                result = done.result()
            except BaseException as error:
                future.set_exception(error)
            else:
                future.set_result(result)

        task.add_done_callback(completed)

    async def _run_owned(
        self,
        operation: Callable[[sessionmaker[Session], DatabaseDeadline], _Result],
        budget: DatabaseDeadline,
    ) -> _Result:
        work = asyncio.create_task(self._work(operation, budget))
        cancelled = False
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                # Even repeated cancellation of this owner cannot cancel its
                # transaction/cleanup child. Its own total timer still applies.
                budget.stop()
                cancelled = True
            except BaseException:
                break  # retrieve the exact child outcome below
        result = work.result()
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _work(
        self,
        operation: Callable[[sessionmaker[Session], DatabaseDeadline], _Result],
        budget: DatabaseDeadline,
    ) -> _Result:
        bridge = AsyncSession(self.engine)
        token = _ACTIVE_BUDGET.set(budget)
        try:
            async with asyncio.timeout(budget.remaining()):

                def invoke(unused: Session) -> _Result:
                    # The bridge establishes SQLAlchemy's documented greenlet
                    # context, without eagerly checking out a connection. Real
                    # AuthService and B1 each own their existing Sessions/UoWs.
                    # Thus media/header/verified-JWT precedence still precedes
                    # the first checkout, including when this DB is unavailable.
                    budget.check()
                    return operation(
                        sessionmaker(
                            self.engine.sync_engine, class_=Session, expire_on_commit=False
                        ),
                        budget,
                    )

                return await bridge.run_sync(invoke)
        finally:
            # Service/auth context managers settle their own connections before
            # run_sync unwinds. Keep the bridge cleanup outside its timer too.
            cleanup = asyncio.create_task(bridge.close())
            try:
                await asyncio.shield(cleanup)
            finally:
                try:
                    if budget.resolvers:
                        await asyncio.gather(
                            *(
                                asyncio.shield(asyncio.wrap_future(item))
                                for item in budget.resolvers
                            ),
                            return_exceptions=True,
                        )
                finally:
                    _ACTIVE_BUDGET.reset(token)

    def _settled(self, future: Future[Any]) -> None:
        # Observe late errors without logging tokens, SQL parameters or bodies.
        if not future.cancelled():
            future.exception()
        with self._lock:
            self._active.discard(future)
            self._slots.release()

    def request_close(self) -> BindingCloseResult:
        """Close admission once; any thread/callback may request retained cleanup."""
        with self._lock:
            self._closed = True
            if self._shutdown is None:
                self._shutdown = Future()
                self._shutdown.set_running_or_notify_cancel()  # observers cannot cancel ownership
                self._closer = Thread(
                    target=self._close,
                    args=(tuple(self._active), self._shutdown),
                    name="autplay-binding-close",
                    daemon=False,
                )
                try:
                    self._closer.start()
                except BaseException as error:
                    self._shutdown.set_exception(error)
        return BindingCloseResult.REQUESTED

    def _close(
        self, active: tuple[Future[Any], ...], completion: Future[BindingCloseResult]
    ) -> None:
        error: BaseException | None = None
        # A dedicated closer is neither the DB thread nor an executor worker.
        # Admission is already closed; this immutable set contains every owner.
        for future in active:
            # Request failure is settled; it is not a close failure.
            with suppress(BaseException):
                future.result()
        try:
            if self._started:
                error = asyncio.run_coroutine_threadsafe(self._dispose(), self._loop).result()
            else:
                error = self._loop.run_until_complete(self._dispose())
        except BaseException as failure:
            error = failure
        finally:
            if self._started:
                try:
                    self._loop.call_soon_threadsafe(self._loop.stop)
                except BaseException as failure:
                    if error is None:
                        error = failure
                try:
                    self._thread.join()
                except BaseException as failure:
                    if error is None:
                        error = failure
            else:
                self._finish_loop()
        if error is None:
            error = self._serve_error
        if error is None:
            completion.set_result(BindingCloseResult.CLOSED)
        else:
            completion.set_exception(error)

    async def _dispose(self) -> BaseException | None:
        try:
            await self.engine.dispose()
        except BaseException as error:
            # Transport the ACTUAL outcome, including CancelledError, as a
            # value across the thread boundary. A cancelled proxy Future would
            # substitute concurrent.futures.CancelledError and lose its identity.
            return error
        return None

    def _owns_current_thread(self) -> bool:
        return (
            current_thread() is self._thread
            or current_thread() is self._closer
            or self._resolvers.owns_current_thread()
        )

    async def await_closed(self) -> BindingCloseResult:
        """Observe one stable close outcome externally; cancellation detaches only this observer."""
        self.request_close()
        if self._owns_current_thread():
            raise RuntimeError("B1 await_closed requires an external thread/event loop")
        assert self._shutdown is not None
        wrapped = asyncio.wrap_future(self._shutdown)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(wrapped)

    async def shutdown(self) -> BindingCloseResult:
        """Owner loop/executor: REQUESTED; external lifespan: settled CLOSED or failure."""
        self.request_close()
        if self._owns_current_thread():
            return BindingCloseResult.REQUESTED
        cancelled = False
        while True:
            try:
                result = await self.await_closed()
                break
            except asyncio.CancelledError:
                if self._shutdown is not None and self._shutdown.done():
                    # A retained CLOSE failure may itself be CancelledError.
                    # Replay that exact failure instead of misclassifying it as
                    # caller cancellation and retrying an already failed close.
                    self._shutdown.result()
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return result


@contextmanager
def bounded_transaction(
    session: Session, *, budget: DatabaseDeadline | None = None
) -> Iterator[DatabaseDeadline]:
    budget = budget or current_deadline()
    budget.transaction_timeout_installed = False
    budget.check()
    with session.begin():
        connection = session.connection(execution_options={"isolation_level": "READ COMMITTED"})
        managed = _ACTIVE_BUDGET.get() is budget
        if not managed:
            event.listen(connection, "before_cursor_execute", budget.before_statement)

        def before_commit(current: Session) -> None:
            budget.check()

        event.listen(session, "before_commit", before_commit)
        try:
            budget.check()
            yield budget
            budget.check()
            session.flush()
            budget.check()
            # Commit inside this scope, while the deadline listeners still exist.
            session.commit()
            budget.check()
        finally:
            if not managed:
                event.remove(connection, "before_cursor_execute", budget.before_statement)
            event.remove(session, "before_commit", before_commit)


class SqlAlchemySyncBindingRepository:
    def __init__(self, session: Session) -> None:
        self._s = session
        self._cursor: DeviceSyncCursorRow | None = None

    def lock_cursor(self, device_id: UUID) -> BindingCursor | None:
        self._cursor = self._s.get(
            DeviceSyncCursorRow, device_id, with_for_update=True, populate_existing=True
        )
        row = self._cursor
        return (
            None
            if row is None
            else BindingCursor(
                row.user_id,
                row.device_id,
                row.journal_epoch,
                row.last_pulled_server_sequence,
                row.last_acked_device_sequence,
                row.last_successful_sync_at,
            )
        )

    def has_prior_use(self, device_id: UUID) -> bool:
        # No state/expiry/owner filter: a removed cursor must not erase lineage.
        return bool(
            self._s.scalar(
                select(
                    exists().where(DeviceEventInboxRow.device_id == device_id)
                    | exists().where(SyncEventRow.origin_device_id == device_id)
                    | exists().where(BootstrapSessionRow.device_id == device_id)
                )
            )
        )

    def bind_unused(self, principal: Principal, epoch: UUID, now: datetime) -> None:
        if self._cursor is None:
            self._s.add(
                DeviceSyncCursorRow(
                    user_id=principal.user_id,
                    device_id=principal.device_id,
                    journal_epoch=epoch,
                    last_pulled_server_sequence=0,
                    last_acked_device_sequence=0,
                    last_successful_sync_at=None,
                    updated_at=now,
                )
            )
        else:
            # Application proved null epoch AND zero prior use. No checkpoint,
            # classification, device metadata or last-success field is assigned.
            self._cursor.journal_epoch = epoch
            self._cursor.updated_at = max(self._cursor.updated_at, now)
