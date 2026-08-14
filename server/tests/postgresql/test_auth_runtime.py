"""Real-PostgreSQL owner bootstrap and device-session tests."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from threading import Barrier, Event, local
from typing import Any
from uuid import UUID

import pytest
from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthUnitOfWorkFactory
from autplay.adapters.security.tokens import Hs256AccessTokenCodec, OpaqueRefreshTokenCodec
from autplay.adapters.system import Uuid7Generator
from autplay.application.auth import AuthService, BootstrapOwnerCommand
from autplay.domain.auth import (
    AccountRole,
    DeviceDescription,
    DevicePlatform,
    InvalidAccessTokenError,
    InvalidRefreshTokenError,
    OwnedObjectNotFoundError,
    OwnerAlreadyBootstrappedError,
    Principal,
    RefreshTokenReplayError,
    TokenPair,
)
from autplay.entrypoints.admin import run_bootstrap
from psycopg import Connection
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

ACCESS_SECRET = b"p03-test-access-secret-with-at-least-thirty-two-bytes"
ISSUER = "autplay-p03-test"
AUDIENCE = "autplay-api-test"
INITIAL_TIME = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)


class MutableClock:
    """Deterministic aware clock for session expiry and audit evidence."""

    def __init__(self, instant: datetime) -> None:
        self.instant = instant

    def now(self) -> datetime:
        return self.instant

    def advance(self, delta: timedelta) -> None:
        self.instant += delta


@dataclass(frozen=True, slots=True)
class AuthRuntime:
    service: AuthService
    clock: MutableClock
    access_tokens: Hs256AccessTokenCodec
    refresh_tokens: OpaqueRefreshTokenCodec
    engine: Engine


@pytest.fixture
def auth_runtime(database_url: str) -> Iterator[AuthRuntime]:
    engine: Engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    clock = MutableClock(INITIAL_TIME)
    access_tokens = Hs256AccessTokenCodec(
        ACCESS_SECRET,
        issuer=ISSUER,
        audience=AUDIENCE,
        max_ttl=timedelta(minutes=15),
        clock_skew=timedelta(0),
    )
    refresh_tokens = OpaqueRefreshTokenCodec()
    service = AuthService(
        unit_of_work_factory=SqlAlchemyAuthUnitOfWorkFactory(sessions),
        clock=clock,
        ids=Uuid7Generator(),
        access_tokens=access_tokens,
        refresh_tokens=refresh_tokens,
        access_token_ttl=timedelta(minutes=10),
        refresh_token_ttl=timedelta(days=30),
    )
    try:
        yield AuthRuntime(service, clock, access_tokens, refresh_tokens, engine)
    finally:
        engine.dispose()


def test_local_owner_bootstrap_is_atomic_real_and_one_time(
    auth_runtime: AuthRuntime,
    database_connection: Connection[Any],
) -> None:
    """Bootstrap persists one OWNER/device/session/audit and only a refresh digest."""

    pair = _bootstrap(auth_runtime)
    account = database_connection.execute(
        "SELECT user_id, display_name, role, status FROM account.user_account"
    ).fetchone()
    device = database_connection.execute(
        "SELECT device_id, user_id, device_name, platform, app_version, revoked_at "
        "FROM account.device"
    ).fetchone()
    session = database_connection.execute(
        "SELECT session_id, user_id, device_id, refresh_token_hash, issued_at, "
        "expires_at, revoked_at FROM account.user_session"
    ).fetchone()
    audit = database_connection.execute(
        "SELECT actor_type, actor_user_id, actor_device_id, action, target_type, "
        "target_id, metadata_sanitized FROM audit.audit_event"
    ).fetchone()

    assert account == (pair.user_id, "AutPlay Owner", "OWNER", "ACTIVE")
    assert device == (
        pair.device_id,
        pair.user_id,
        "Owner Phone",
        "ANDROID",
        "p03-test",
        None,
    )
    assert session is not None
    assert session[:3] == (pair.session_id, pair.user_id, pair.device_id)
    assert session[3] == hashlib.sha256(pair.refresh_token.encode("ascii")).digest()
    assert len(session[3]) == 32
    assert session[4:] == (
        INITIAL_TIME,
        INITIAL_TIME + timedelta(days=30),
        None,
    )
    assert audit == (
        "SYSTEM",
        pair.user_id,
        pair.device_id,
        "auth.owner_bootstrapped",
        "USER_ACCOUNT",
        pair.user_id,
        {},
    )
    assert auth_runtime.service.authenticate_access(pair.access_token) == Principal(
        pair.user_id,
        pair.device_id,
        pair.session_id,
        AccountRole.OWNER,
    )

    with pytest.raises(OwnerAlreadyBootstrappedError):
        _bootstrap(auth_runtime)
    assert database_connection.execute("SELECT count(*) FROM account.user_account").fetchone() == (
        1,
    )
    assert database_connection.execute("SELECT count(*) FROM account.user_session").fetchone() == (
        1,
    )


def test_concurrent_owner_bootstrap_creates_exactly_one_bundle(
    auth_runtime: AuthRuntime,
    database_connection: Connection[Any],
) -> None:
    """The advisory lock makes simultaneous first-run attempts exactly-once."""

    start = Barrier(2)

    def _attempt() -> tuple[str, TokenPair | None]:
        start.wait(timeout=5)
        try:
            return "created", _bootstrap(auth_runtime)
        except OwnerAlreadyBootstrappedError:
            return "already_exists", None

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="auth-bootstrap") as executor:
        attempts = (executor.submit(_attempt), executor.submit(_attempt))
        outcomes = [future.result(timeout=10) for future in attempts]

    assert sorted(status for status, _pair in outcomes) == ["already_exists", "created"]
    created = [pair for _status, pair in outcomes if pair is not None]
    assert len(created) == 1
    assert database_connection.execute("SELECT count(*) FROM account.user_account").fetchone() == (
        1,
    )
    assert database_connection.execute("SELECT count(*) FROM account.device").fetchone() == (1,)
    assert database_connection.execute("SELECT count(*) FROM account.user_session").fetchone() == (
        1,
    )
    assert database_connection.execute("SELECT count(*) FROM audit.audit_event").fetchone() == (1,)


def test_admin_cli_emits_tokens_once_without_logging_or_echoing_on_failure(
    auth_runtime: AuthRuntime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The trusted CLI intentionally writes tokens to stdout, never to logs or errors."""

    stdout = StringIO()
    stderr = StringIO()
    args = (
        "bootstrap-owner",
        "--display-name",
        "CLI Owner",
        "--device-name",
        "CLI Device",
        "--platform",
        "OTHER",
        "--app-version",
        "p03-test",
    )
    assert run_bootstrap(auth_runtime.service, args, stdout=stdout, stderr=stderr) == 0
    document = json.loads(stdout.getvalue())
    assert isinstance(document["access_token"], str)
    assert isinstance(document["refresh_token"], str)
    assert stderr.getvalue() == ""
    assert caplog.records == []

    failed_stdout = StringIO()
    failed_stderr = StringIO()
    assert (
        run_bootstrap(
            auth_runtime.service,
            args,
            stdout=failed_stdout,
            stderr=failed_stderr,
        )
        == 4
    )
    assert failed_stdout.getvalue() == ""
    assert "owner_already_bootstrapped" in failed_stderr.getvalue()
    assert document["access_token"] not in failed_stderr.getvalue()
    assert document["refresh_token"] not in failed_stderr.getvalue()
    assert caplog.records == []


def test_refresh_rotation_retains_old_hash_absolute_expiry_and_replay_revokes_device(
    auth_runtime: AuthRuntime,
    database_connection: Connection[Any],
) -> None:
    """Old generations remain detectable and replay kills only that device's sessions."""

    first = _bootstrap(auth_runtime)
    auth_runtime.clock.advance(timedelta(hours=1))

    unknown = auth_runtime.refresh_tokens.issue().token
    with pytest.raises(InvalidRefreshTokenError):
        auth_runtime.service.rotate_refresh(unknown)
    assert _active_session_count(database_connection, first.user_id, first.device_id) == 1

    second = auth_runtime.service.rotate_refresh(first.refresh_token)
    rows = database_connection.execute(
        "SELECT session_id, refresh_token_hash, expires_at, revoked_at "
        "FROM account.user_session ORDER BY issued_at"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == first.session_id
    assert rows[0][1] == hashlib.sha256(first.refresh_token.encode("ascii")).digest()
    assert rows[0][2] == first.refresh_expires_at
    assert rows[0][3] == auth_runtime.clock.now()
    assert rows[1][0] == second.session_id
    assert rows[1][1] == hashlib.sha256(second.refresh_token.encode("ascii")).digest()
    assert rows[1][2] == first.refresh_expires_at == second.refresh_expires_at
    assert rows[1][3] is None
    with pytest.raises(InvalidAccessTokenError):
        auth_runtime.service.authenticate_access(first.access_token)
    assert (
        auth_runtime.service.authenticate_access(second.access_token).session_id
        == second.session_id
    )

    with pytest.raises(RefreshTokenReplayError):
        auth_runtime.service.rotate_refresh(first.refresh_token)
    assert _active_session_count(database_connection, first.user_id, first.device_id) == 0
    with pytest.raises(InvalidAccessTokenError):
        auth_runtime.service.authenticate_access(second.access_token)
    actions = database_connection.execute(
        "SELECT action, reason_code FROM audit.audit_event ORDER BY occurred_at, audit_event_id"
    ).fetchall()
    assert ("auth.refresh_rotated", None) in actions
    assert (
        "auth.refresh_replay_detected",
        "KNOWN_REVOKED_GENERATION",
    ) in actions


def test_access_reload_and_device_commands_fail_closed_across_users(
    auth_runtime: AuthRuntime,
    database_connection: Connection[Any],
) -> None:
    """Signed claims cannot cross user/device/session ownership boundaries."""

    owner_pair = _bootstrap(auth_runtime)
    other = _insert_user_device_session(
        database_connection,
        auth_runtime,
        display_name="Other User",
    )
    database_connection.commit()
    other_principal = Principal(
        other.user_id,
        other.device_id,
        other.session_id,
        AccountRole.USER,
    )
    other_access = _issue_access(auth_runtime, other_principal)
    assert auth_runtime.service.authenticate_access(other_access) == other_principal

    crossed = Principal(
        owner_pair.user_id,
        other.device_id,
        other.session_id,
        AccountRole.OWNER,
    )
    with pytest.raises(InvalidAccessTokenError):
        auth_runtime.service.authenticate_access(_issue_access(auth_runtime, crossed))

    owner_principal = auth_runtime.service.authenticate_access(owner_pair.access_token)
    with pytest.raises(OwnedObjectNotFoundError):
        auth_runtime.service.revoke_device(owner_principal, other.device_id)
    assert _active_session_count(database_connection, other.user_id, other.device_id) == 1
    assert database_connection.execute(
        "SELECT revoked_at FROM account.device WHERE device_id = %s", (other.device_id,)
    ).fetchone() == (None,)

    assert auth_runtime.service.revoke_device(owner_principal, owner_pair.device_id) == 1
    with pytest.raises(InvalidAccessTokenError):
        auth_runtime.service.authenticate_access(owner_pair.access_token)
    assert auth_runtime.service.authenticate_access(other_access) == other_principal


def test_logout_all_revokes_only_principal_sessions_and_preserves_devices(
    auth_runtime: AuthRuntime,
    database_connection: Connection[Any],
) -> None:
    """Logout-all is user-scoped and is distinct from permanent device revoke."""

    owner_pair = _bootstrap(auth_runtime)
    owner_second = _insert_user_device_session(
        database_connection,
        auth_runtime,
        display_name=None,
        existing_user_id=owner_pair.user_id,
    )
    other = _insert_user_device_session(database_connection, auth_runtime, display_name="Other")
    database_connection.commit()

    principal = auth_runtime.service.authenticate_access(owner_pair.access_token)
    assert auth_runtime.service.logout_all(principal) == 2
    assert database_connection.execute(
        "SELECT count(*) FROM account.user_session WHERE user_id = %s AND revoked_at IS NULL",
        (owner_pair.user_id,),
    ).fetchone() == (0,)
    assert database_connection.execute(
        "SELECT count(*) FROM account.device WHERE user_id = %s AND revoked_at IS NULL",
        (owner_pair.user_id,),
    ).fetchone() == (2,)
    assert _active_session_count(database_connection, other.user_id, other.device_id) == 1
    assert (
        auth_runtime.service.authenticate_access(
            _issue_access(
                auth_runtime,
                Principal(other.user_id, other.device_id, other.session_id, AccountRole.USER),
            )
        ).user_id
        == other.user_id
    )
    with pytest.raises(InvalidAccessTokenError):
        auth_runtime.service.authenticate_access(
            _issue_access(
                auth_runtime,
                Principal(
                    owner_pair.user_id,
                    owner_second.device_id,
                    owner_second.session_id,
                    AccountRole.OWNER,
                ),
            )
        )


def test_logout_all_account_lock_serializes_refresh_rotation(
    auth_runtime: AuthRuntime,
    database_connection: Connection[Any],
) -> None:
    """A rotation waiting behind logout-all cannot insert a surviving generation."""

    pair = _bootstrap(auth_runtime)
    principal = auth_runtime.service.authenticate_access(pair.access_token)
    logout_lock_acquired = Event()
    allow_logout_update = Event()
    rotation_lock_attempted = Event()
    rotation_finished = Event()
    operation = _OperationContext()

    def _is_account_lock(statement: str) -> bool:
        normalized = " ".join(statement.split())
        return "FROM account.user_account" in normalized and "FOR UPDATE" in normalized

    def _before_cursor_execute(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if operation.name == "rotate" and _is_account_lock(statement):
            rotation_lock_attempted.set()

    def _after_cursor_execute(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if operation.name == "logout" and _is_account_lock(statement):
            logout_lock_acquired.set()
            if not allow_logout_update.wait(timeout=5):
                raise RuntimeError("test did not release logout-all account lock")

    def _logout_all() -> int:
        operation.name = "logout"
        return auth_runtime.service.logout_all(principal)

    def _rotate() -> TokenPair:
        operation.name = "rotate"
        try:
            return auth_runtime.service.rotate_refresh(pair.refresh_token)
        finally:
            rotation_finished.set()

    event.listen(auth_runtime.engine, "before_cursor_execute", _before_cursor_execute)
    event.listen(auth_runtime.engine, "after_cursor_execute", _after_cursor_execute)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="auth-race")
    try:
        logout_future = executor.submit(_logout_all)
        assert logout_lock_acquired.wait(timeout=5)
        rotation_future = executor.submit(_rotate)
        assert rotation_lock_attempted.wait(timeout=5)
        assert not rotation_finished.wait(timeout=0.1)

        allow_logout_update.set()
        assert logout_future.result(timeout=5) == 1
        with pytest.raises(RefreshTokenReplayError):
            rotation_future.result(timeout=5)
    finally:
        allow_logout_update.set()
        executor.shutdown(wait=True, cancel_futures=True)
        event.remove(auth_runtime.engine, "before_cursor_execute", _before_cursor_execute)
        event.remove(auth_runtime.engine, "after_cursor_execute", _after_cursor_execute)

    assert _active_session_count(database_connection, pair.user_id, pair.device_id) == 0
    assert database_connection.execute(
        "SELECT count(*) FROM account.user_session WHERE user_id = %s",
        (pair.user_id,),
    ).fetchone() == (1,)


class _OperationContext(local):
    """Thread-local operation label used only by the deterministic race test."""

    name: str | None = None


@dataclass(frozen=True, slots=True)
class InsertedSession:
    user_id: UUID
    device_id: UUID
    session_id: UUID


def _bootstrap(runtime: AuthRuntime) -> TokenPair:
    return runtime.service.bootstrap_owner(
        BootstrapOwnerCommand(
            display_name="AutPlay Owner",
            device=DeviceDescription(
                name="Owner Phone",
                platform=DevicePlatform.ANDROID,
                app_version="p03-test",
            ),
        )
    )


def _insert_user_device_session(
    connection: Connection[Any],
    runtime: AuthRuntime,
    *,
    display_name: str | None,
    existing_user_id: UUID | None = None,
) -> InsertedSession:
    user_id = existing_user_id
    if user_id is None:
        row = connection.execute(
            "INSERT INTO account.user_account (display_name, role) "
            "VALUES (%s, 'USER') RETURNING user_id",
            (display_name,),
        ).fetchone()
        if row is None or not isinstance(row[0], UUID):
            raise AssertionError("user fixture did not return UUID")
        user_id = row[0]
    device_id = uuid.uuid7()
    session_id = uuid.uuid7()
    refresh = runtime.refresh_tokens.issue()
    connection.execute(
        "INSERT INTO account.device "
        "(device_id, user_id, device_name, platform, app_version) "
        "VALUES (%s, %s, 'fixture-device', 'OTHER', 'p03-test')",
        (device_id, user_id),
    )
    connection.execute(
        "INSERT INTO account.user_session "
        "(session_id, user_id, device_id, refresh_token_hash, issued_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            session_id,
            user_id,
            device_id,
            refresh.sha256,
            runtime.clock.now(),
            runtime.clock.now() + timedelta(days=30),
        ),
    )
    return InsertedSession(user_id, device_id, session_id)


def _issue_access(runtime: AuthRuntime, principal: Principal) -> str:
    return runtime.access_tokens.issue(
        principal,
        token_id=uuid.uuid7(),
        issued_at=runtime.clock.now(),
        expires_at=runtime.clock.now() + timedelta(minutes=10),
    )


def _active_session_count(connection: Connection[Any], user_id: UUID, device_id: UUID) -> int:
    row = connection.execute(
        "SELECT count(*) FROM account.user_session "
        "WHERE user_id = %s AND device_id = %s AND revoked_at IS NULL",
        (user_id, device_id),
    ).fetchone()
    if row is None or not isinstance(row[0], int):
        raise AssertionError("active session count did not return int")
    return row[0]
