"""Real-PostgreSQL gates for M6 browser authority transitions."""

from __future__ import annotations

import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.admin_commands import SqlAlchemyAdminCommandRepository
from autplay.adapters.postgresql.admin_views_runtime import SqlAlchemyAdminViewService
from autplay.adapters.postgresql.web_admin_uow import SqlAlchemyWebAdminUnitOfWorkFactory
from autplay.application.admin_commands import AdminCommandService
from autplay.application.web_admin import WebAdminService
from autplay.domain.web_admin import WebAdminError, WebSessionCredentials
from autplay.entrypoints.admin_web_http import create_admin_web_router
from autplay.runtime.web_security import canonical_form_request_hash, source_rate_key
from autplay.web.renderer import AdminTemplateRenderer
from fastapi import FastAPI
from psycopg import Connection
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def web_service(database_url: str) -> Iterator[WebAdminService]:
    engine = create_engine(database_url, pool_pre_ping=True)
    yield WebAdminService(
        SqlAlchemyWebAdminUnitOfWorkFactory(
            sessionmaker(engine, class_=Session, expire_on_commit=False)
        ),
        csrf_secret=b"m6-real-pg-csrf-secret-is-distinct-and-long",
    )
    engine.dispose()


def _seed_owner(connection: Connection[object]) -> tuple[UUID, UUID]:
    user = connection.execute(
        "INSERT INTO account.user_account (display_name, role) "
        "VALUES ('M6 owner', 'OWNER') RETURNING user_id"
    ).fetchone()
    assert user is not None
    server_id = uuid4()
    connection.execute(
        """
        INSERT INTO account.server_instance (
            server_instance_id, identity_epoch, identity_public_key_spki,
            identity_thumbprint_sha256, label_hint, api_origin, stream_origin,
            capability_revision, created_at, updated_at
        ) VALUES (%s, 1, %s, %s, 'M6 server', 'https://api.invalid',
                  'https://stream.invalid', 1, %s, %s)
        """,
        (server_id, b"s" * 65, b"t" * 32, NOW, NOW),
    )
    connection.commit()
    return UUID(str(cast(tuple[object, ...], user)[0])), server_id


def test_login_rotation_second_rotation_and_logout_receipt_are_atomic(
    web_service: WebAdminService, database_connection: Connection[object], database_url: str
) -> None:
    user_id, server_id = _seed_owner(database_connection)
    invitation = web_service.issue_invitation(user_id, now=NOW)
    assert invitation.server_instance_id == server_id and len(invitation.bearer) >= 43
    challenge = web_service.begin_login(now=NOW)
    request_hash = b"r" * 32
    credentials = web_service.login(
        challenge, invitation.bearer, request_hash, now=NOW + timedelta(seconds=1)
    )

    assert credentials.actor.user_id == user_id
    assert database_connection.execute(
        "SELECT count(*) FROM account.web_session WHERE revoked_at IS NULL"
    ).fetchone() == (1,)
    assert database_connection.execute(
        "SELECT count(*) FROM audit.audit_event WHERE action IN "
        "('web.invitation_issued', 'web.login_succeeded')"
    ).fetchone() == (2,)

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE account.web_session SET token_issued_at=:due, "
                    "last_activity_at=:due WHERE web_session_id=:session_id"
                ),
                {
                    "due": NOW - timedelta(minutes=16),
                    "session_id": credentials.actor.web_session_id,
                },
            )
    finally:
        engine.dispose()

    with pytest.raises(WebAdminError, match="browser_session_rotation_required"):
        web_service.authenticate(credentials.bearer, mutation=True, now=NOW)
    first_rotation = web_service.authenticate_safe_get(credentials.bearer, now=NOW)
    assert first_rotation.rotated_bearer is not None
    with pytest.raises(WebAdminError, match="authentication_required"):
        web_service.authenticate(credentials.bearer, mutation=False, now=NOW)

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE account.web_session SET token_issued_at=:due "
                    "WHERE web_session_id=:session_id"
                ),
                {
                    "due": NOW - timedelta(minutes=16),
                    "session_id": credentials.actor.web_session_id,
                },
            )
    finally:
        engine.dispose()
    assert first_rotation.rotated_bearer is not None
    second_rotation = web_service.authenticate_safe_get(first_rotation.rotated_bearer, now=NOW)
    assert second_rotation.rotated_bearer is not None
    web_service.validate_csrf(second_rotation.actor, second_rotation.csrf, uuid4())

    operation_id = uuid4()
    logout_hash = b"l" * 32
    web_service.logout_current(
        second_rotation.actor,
        operation_id,
        logout_hash,
        "owner_requested",
        now=NOW + timedelta(seconds=2),
    )
    web_service.revoked_logout_retry(
        second_rotation.rotated_bearer,
        operation_id,
        logout_hash,
        now=NOW + timedelta(seconds=3),
    )
    with pytest.raises(WebAdminError, match="authentication_required"):
        web_service.revoked_logout_retry(
            second_rotation.rotated_bearer,
            operation_id,
            b"x" * 32,
            now=NOW + timedelta(seconds=3),
        )
    assert database_connection.execute(
        "SELECT revoked_at IS NOT NULL FROM account.web_session"
    ).fetchone() == (True,)
    assert database_connection.execute(
        "SELECT action, reason_code FROM audit.audit_event WHERE request_id = %s",
        (operation_id,),
    ).fetchone() == ("web.logout_current", "owner_requested")
    database_connection.execute(
        "UPDATE account.web_session_rotation_evidence SET expires_at=%s",
        (NOW - timedelta(1),),
    )
    database_connection.execute(
        "UPDATE account.web_terminal_receipt SET receipt_expires_at=%s",
        (NOW - timedelta(1),),
    )
    database_connection.execute(
        "INSERT INTO account.web_login_rate_window "
        "(rate_key_sha256, window_started_at, expires_at, attempt_count) "
        "VALUES (%s,%s,%s,1)",
        (b"w" * 32, NOW - timedelta(hours=1), NOW - timedelta(1)),
    )
    future_expiry = datetime.now(UTC) + timedelta(hours=1)
    database_connection.execute(
        "UPDATE account.web_login_challenge SET expires_at=%s",
        (future_expiry,),
    )
    database_connection.execute(
        "UPDATE account.web_session_invitation SET expires_at=%s",
        (future_expiry,),
    )
    database_connection.commit()
    assert web_service.cleanup_expired(2) == 2
    assert web_service.cleanup_expired(100) == 3


def test_web_invitation_active_cap_is_enforced_in_real_postgresql(
    web_service: WebAdminService, database_connection: Connection[object]
) -> None:
    user_id, _ = _seed_owner(database_connection)
    for index in range(3):
        web_service.issue_invitation(user_id, now=NOW + timedelta(seconds=index))
    with pytest.raises(ValueError, match="rate_limited"):
        web_service.issue_invitation(user_id, now=NOW + timedelta(seconds=4))


def test_web_invitation_rolling_server_hour_cap_is_enforced(
    web_service: WebAdminService, database_connection: Connection[object]
) -> None:
    user_id, _ = _seed_owner(database_connection)
    for index in range(10):
        invitation = web_service.issue_invitation(user_id, now=NOW + timedelta(seconds=index))
        database_connection.execute(
            "UPDATE account.web_session_invitation SET cancelled_at = %s WHERE invitation_id = %s",
            (NOW + timedelta(seconds=index), invitation.invitation_id),
        )
        database_connection.commit()
    with pytest.raises(ValueError, match="rate_limited"):
        web_service.issue_invitation(user_id, now=NOW + timedelta(seconds=30))


def test_local_cli_recovery_lists_and_revokes_without_cookie_material(
    web_service: WebAdminService, database_connection: Connection[object]
) -> None:
    user_id, _ = _seed_owner(database_connection)
    invitation = web_service.issue_invitation(user_id, now=NOW)
    challenge = web_service.begin_login(now=NOW)
    credentials = web_service.login(challenge, invitation.bearer, b"q" * 32, now=NOW)

    rows = web_service.list_browser_sessions(user_id, limit=10)
    assert len(rows) == 1 and rows[0].web_session_id == credentials.actor.web_session_id
    assert not hasattr(rows[0], "token_sha256") and not hasattr(rows[0], "csrf_sha256")
    operation_id = uuid4()
    assert web_service.revoke_browser_session_local(
        user_id, credentials.actor.web_session_id, operation_id, now=NOW
    )
    assert not web_service.revoke_browser_session_local(
        user_id, credentials.actor.web_session_id, operation_id, now=NOW
    )
    assert web_service.revoke_all_browser_sessions_local(user_id, uuid4(), now=NOW) == 0


def test_browser_actor_revokes_another_and_self_with_terminal_receipts(
    web_service: WebAdminService, database_connection: Connection[object]
) -> None:
    user_id, _ = _seed_owner(database_connection)

    def login(offset: int) -> WebSessionCredentials:
        invitation = web_service.issue_invitation(user_id, now=NOW + timedelta(seconds=offset))
        challenge = web_service.begin_login(now=NOW + timedelta(seconds=offset))
        return web_service.login(
            challenge,
            invitation.bearer,
            bytes([offset + 1]) * 32,
            now=NOW + timedelta(seconds=offset),
        )

    first, second = login(1), login(2)
    operation_id = uuid4()
    request_hash = b"v" * 32
    web_service.revoke_browser_session(
        first.actor,
        second.actor.web_session_id,
        operation_id,
        request_hash,
        "owner_requested",
        now=NOW + timedelta(seconds=3),
    )
    web_service.revoke_browser_session(
        first.actor,
        second.actor.web_session_id,
        operation_id,
        request_hash,
        "owner_requested",
        now=NOW + timedelta(seconds=4),
    )
    with pytest.raises(WebAdminError, match="operation_conflict"):
        web_service.revoke_browser_session(
            first.actor,
            second.actor.web_session_id,
            operation_id,
            b"z" * 32,
            "owner_requested",
            now=NOW + timedelta(seconds=4),
        )
    with pytest.raises(WebAdminError, match="authentication_required"):
        web_service.authenticate(second.bearer, mutation=False, now=NOW)

    self_operation = uuid4()
    self_hash = b"s" * 32
    web_service.revoke_browser_session(
        first.actor,
        first.actor.web_session_id,
        self_operation,
        self_hash,
        "owner_requested",
        now=NOW + timedelta(seconds=5),
    )
    assert (
        web_service.revoked_lifecycle_retry(
            first.bearer,
            self_operation,
            "REVOKE_BROWSER_SESSION",
            self_hash,
            now=NOW + timedelta(seconds=6),
        )
        == "APPLIED"
    )
    assert database_connection.execute(
        "SELECT count(*) FROM audit.audit_event WHERE action = 'web.browser_session_revoked'"
    ).fetchone() == (2,)


def test_real_http_login_lists_and_exactly_cancels_m5_invitation(
    web_service: WebAdminService, database_connection: Connection[object], database_url: str
) -> None:
    user_id, server_id = _seed_owner(database_connection)
    enrollment_id = uuid4()
    now = datetime.now(UTC)
    database_connection.execute(
        "INSERT INTO account.enrollment_invitation "
        "(invitation_id, server_instance_id, user_id, issued_by_user_id, "
        "invitation_secret_hash, issued_at, expires_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (
            enrollment_id,
            server_id,
            user_id,
            user_id,
            b"e" * 32,
            now,
            now + timedelta(minutes=10),
        ),
    )
    database_connection.commit()
    browser_invitation = web_service.issue_invitation(user_id, now=now)

    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = FastAPI()
    app.include_router(
        create_admin_web_router(
            web=web_service,
            views=SqlAlchemyAdminViewService(sessions),
            commands=AdminCommandService(SqlAlchemyAdminCommandRepository(sessions)),
            renderer=AdminTemplateRenderer(),
            origin="https://admin.test",
            source_secret=b"m6-http-source-secret-is-distinct",
        )
    )
    try:
        client = TestClient(app, base_url="https://admin.test")
        login = client.get("/admin/login")
        hidden = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', login.text))
        preauth_cookie = client.cookies.get("__Host-autplay_login")
        assert preauth_cookie is not None
        login_form = {**hidden, "browser_invitation": browser_invitation.bearer.decode()}
        signed_in = client.post(
            "/admin/login",
            data=login_form,
            headers={"Origin": "https://admin.test"},
            follow_redirects=False,
        )
        assert signed_in.status_code == 303
        request_hash = canonical_form_request_hash("POST", "/admin/login", login_form)
        rate_source = source_rate_key(b"m6-http-source-secret-is-distinct", "testclient")
        for _ in range(9):
            web_service.login_rate_gate(
                rate_source, browser_invitation.bearer, request_hash, now=now
            )
        client.cookies.set("__Host-autplay_login", preauth_cookie, path="/")
        lost_response_retry = client.post(
            "/admin/login",
            data=login_form,
            headers={"Origin": "https://admin.test"},
        )
        assert lost_response_retry.status_code == 403
        assert "browser_login_outcome_unknown" in lost_response_retry.text
        assert "retry-after" not in lost_response_retry.headers
        invitation_page = client.get("/admin/invitations")
        assert invitation_page.status_code == 200
        assert str(enrollment_id) in invitation_page.text

        confirmation = client.get(f"/admin/confirm/invitation/{enrollment_id}")
        form = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', confirmation.text))
        first = client.post(
            f"/admin/confirm/invitation/{enrollment_id}",
            data=form,
            headers={"Origin": "https://admin.test"},
            follow_redirects=False,
        )
        replay = client.post(
            f"/admin/confirm/invitation/{enrollment_id}",
            data=form,
            headers={"Origin": "https://admin.test"},
            follow_redirects=False,
        )
        assert first.status_code == replay.status_code == 303
    finally:
        engine.dispose()

    assert database_connection.execute(
        "SELECT cancelled_at IS NOT NULL FROM account.enrollment_invitation WHERE invitation_id=%s",
        (enrollment_id,),
    ).fetchone() == (True,)
    assert database_connection.execute(
        "SELECT count(*) FROM audit.audit_event "
        "WHERE action='web.enrollment_invitation_cancelled' AND target_id=%s",
        (enrollment_id,),
    ).fetchone() == (1,)


def test_concurrent_browser_login_consumes_one_invitation_once(
    web_service: WebAdminService, database_connection: Connection[object]
) -> None:
    user_id, _ = _seed_owner(database_connection)
    invitation = web_service.issue_invitation(user_id)
    challenge = web_service.begin_login()
    request_hash = b"c" * 32

    def exchange() -> str:
        try:
            web_service.login(challenge, invitation.bearer, request_hash)
        except WebAdminError as error:
            return error.code
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _: exchange(), range(2)))
    assert outcomes == ["browser_invitation_unavailable", "created"]
    with pytest.raises(WebAdminError, match="browser_login_outcome_unknown"):
        web_service.login_retry_outcome(challenge.login_operation_id, request_hash)
    assert database_connection.execute(
        "SELECT count(*) FROM account.web_session WHERE user_id=%s",
        (user_id,),
    ).fetchone() == (1,)


def test_invitation_issue_and_login_use_one_deadlock_free_account_lock_order(
    web_service: WebAdminService, database_connection: Connection[object]
) -> None:
    user_id, _ = _seed_owner(database_connection)
    invitation = web_service.issue_invitation(user_id, now=NOW)
    challenge = web_service.begin_login(now=NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        issued = executor.submit(
            web_service.issue_invitation, user_id, now=NOW + timedelta(seconds=1)
        )
        logged_in = executor.submit(
            web_service.login,
            challenge,
            invitation.bearer,
            b"d" * 32,
            now=NOW + timedelta(seconds=1),
        )
        assert issued.result(timeout=10).user_id == user_id
        assert logged_in.result(timeout=10).actor.user_id == user_id


def test_cross_session_lifecycle_commands_complete_without_deadlock(
    web_service: WebAdminService, database_connection: Connection[object]
) -> None:
    user_id, _ = _seed_owner(database_connection)

    def login(offset: int) -> WebSessionCredentials:
        invitation = web_service.issue_invitation(user_id, now=NOW + timedelta(seconds=offset))
        challenge = web_service.begin_login(now=NOW + timedelta(seconds=offset))
        return web_service.login(
            challenge,
            invitation.bearer,
            bytes([offset]) * 32,
            now=NOW + timedelta(seconds=offset),
        )

    first, second = login(1), login(2)

    def logout_all() -> str:
        try:
            web_service.logout_all_browser(
                first.actor,
                uuid4(),
                b"a" * 32,
                "owner_requested",
                now=NOW + timedelta(seconds=3),
            )
        except WebAdminError as error:
            return error.code
        return "LOGGED_OUT_ALL"

    def revoke_first() -> str:
        try:
            web_service.revoke_browser_session(
                second.actor,
                first.actor.web_session_id,
                uuid4(),
                b"b" * 32,
                "owner_requested",
                now=NOW + timedelta(seconds=3),
            )
        except WebAdminError as error:
            return error.code
        return "APPLIED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        logout_future = executor.submit(logout_all)
        revoke_future = executor.submit(revoke_first)
        outcomes = {logout_future.result(timeout=10), revoke_future.result(timeout=10)}
    assert outcomes <= {"LOGGED_OUT_ALL", "APPLIED", "authentication_required"}
    assert database_connection.execute(
        "SELECT count(*) FROM account.web_session WHERE user_id=%s AND revoked_at IS NULL",
        (user_id,),
    ).fetchone() == (0,)


def test_cleanup_bounds_anonymous_challenges_invitations_and_expired_sessions(
    web_service: WebAdminService, database_connection: Connection[object]
) -> None:
    user_id, _ = _seed_owner(database_connection)
    old = NOW - timedelta(days=2)
    invitation = web_service.issue_invitation(user_id, now=old)
    challenge = web_service.begin_login(now=old)
    web_service.login(challenge, invitation.bearer, b"e" * 32, now=old)

    assert web_service.cleanup_expired(100) == 4
    for table in (
        "web_login_challenge",
        "web_session_invitation",
        "web_terminal_receipt",
        "web_session",
    ):
        assert database_connection.execute(f"SELECT count(*) FROM account.{table}").fetchone() == (
            0,
        )


def test_anonymous_challenge_rate_state_is_bounded_and_cleaned(
    web_service: WebAdminService, database_connection: Connection[object]
) -> None:
    for _ in range(60):
        web_service.login_challenge_rate_gate(b"opaque-source", now=NOW)
    with pytest.raises(WebAdminError, match="rate_limited"):
        web_service.login_challenge_rate_gate(b"opaque-source", now=NOW)
    assert database_connection.execute(
        "SELECT count(*) FROM account.web_login_rate_window"
    ).fetchone() == (2,)
    database_connection.execute(
        "UPDATE account.web_login_rate_window SET expires_at=%s", (NOW - timedelta(1),)
    )
    database_connection.commit()
    assert web_service.cleanup_expired(100) == 2
