"""Real PostgreSQL smoke for M6-C terminal receipt command semantics."""

from __future__ import annotations

from uuid import uuid4

import pytest
from autplay.adapters.postgresql.admin_commands import SqlAlchemyAdminCommandRepository
from autplay.application.admin_commands import AdminCommandService
from autplay.domain.admin_commands import AdminCommand
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness


def test_cancel_invitation_replays_exact_receipt_and_audits(
    database_harness: DatabaseHarness, database_name: str
) -> None:
    server_id, user_id, web_id, invitation_id, operation_id = (uuid4() for _ in range(5))
    with database_harness.connect(database_name) as connection:
        connection.execute(
            """
            INSERT INTO account.user_account (
              user_id, display_name, role, status, created_at, updated_at
            ) VALUES (%s, 'owner', 'OWNER', 'ACTIVE', now(), now())
            """,
            (user_id,),
        )
        connection.execute(
            """
            INSERT INTO account.server_instance (
              server_instance_id, identity_epoch, identity_public_key_spki,
              identity_thumbprint_sha256, label_hint, api_origin, stream_origin,
              capability_revision, created_at, updated_at
            ) VALUES (%s, 1, %s, %s, 'server', 'https://example.test',
                      'https://example.test', 1, now(), now())
            """,
            (server_id, b"k" * 64, b"h" * 32),
        )
        connection.execute(
            """
            INSERT INTO account.web_session (
              web_session_id, family_id, server_instance_id, user_id, token_generation,
              token_sha256, csrf_sha256, issued_at, token_issued_at, last_activity_at,
              idle_expires_at, absolute_expires_at
            ) VALUES (%s,%s,%s,%s,0,%s,%s,now(),now(),now(),
                      now()+interval '30 minutes',now()+interval '12 hours')
            """,
            (web_id, uuid4(), server_id, user_id, b"t" * 32, b"c" * 32),
        )
        connection.execute(
            """
            INSERT INTO account.enrollment_invitation (
              invitation_id, server_instance_id, user_id, issued_by_user_id,
              invitation_secret_hash, issued_at, expires_at
            ) VALUES (%s,%s,%s,%s,%s,now(),now()+interval '5 minutes')
            """,
            (invitation_id, server_id, user_id, user_id, b"i" * 32),
        )
        connection.commit()
    engine = create_engine(database_harness.database_url(database_name))
    service = AdminCommandService(
        SqlAlchemyAdminCommandRepository(sessionmaker(engine, class_=Session))
    )
    actor = WebActor(server_id, user_id, web_id, AccountRole.OWNER, 0)
    command = AdminCommand(actor, operation_id, invitation_id, b"r" * 32)
    assert service.cancel_enrollment_invitation(command)["outcome"] == "APPLIED"
    assert service.cancel_enrollment_invitation(command)["outcome"] == "APPLIED"
    with pytest.raises(WebAdminError, match="operation_conflict"):
        service.cancel_enrollment_invitation(
            AdminCommand(actor, operation_id, invitation_id, b"x" * 32)
        )
    with database_harness.connect(database_name) as connection:
        receipt = connection.execute(
            "SELECT receipt_expires_at - terminal_at >= interval '12 hours' "
            "FROM account.web_terminal_receipt WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()
        audit = connection.execute(
            "SELECT count(*) FROM audit.audit_event WHERE request_id=%s", (operation_id,)
        ).fetchone()
    assert receipt == (True,)
    assert audit == (1,)
    engine.dispose()


def test_revoke_android_device_and_session_are_owner_scoped_and_atomic(
    database_harness: DatabaseHarness, database_name: str
) -> None:
    server_id, user_id, other_id, web_id = (uuid4() for _ in range(4))
    device_id, second_device, other_device = (uuid4() for _ in range(3))
    device_sessions = [uuid4(), uuid4()]
    single_session = uuid4()
    with database_harness.connect(database_name) as connection:
        for account_id, label in ((user_id, "owner"), (other_id, "other")):
            connection.execute(
                "INSERT INTO account.user_account "
                "(user_id, display_name, role, status, created_at, updated_at) "
                "VALUES (%s, %s, 'OWNER', 'ACTIVE', now(), now())",
                (account_id, label),
            )
        connection.execute(
            """
            INSERT INTO account.server_instance (
              server_instance_id, identity_epoch, identity_public_key_spki,
              identity_thumbprint_sha256, label_hint, api_origin, stream_origin,
              capability_revision, created_at, updated_at
            ) VALUES (%s, 1, %s, %s, 'server', 'https://example.test',
                      'https://example.test', 1, now(), now())
            """,
            (server_id, b"k" * 64, b"h" * 32),
        )
        connection.execute(
            """
            INSERT INTO account.web_session (
              web_session_id, family_id, server_instance_id, user_id, token_generation,
              token_sha256, csrf_sha256, issued_at, token_issued_at, last_activity_at,
              idle_expires_at, absolute_expires_at
            ) VALUES (%s,%s,%s,%s,0,%s,%s,now(),now(),now(),
                      now()+interval '30 minutes',now()+interval '12 hours')
            """,
            (web_id, uuid4(), server_id, user_id, b"t" * 32, b"c" * 32),
        )
        for target, owner, name in (
            (device_id, user_id, "owned"),
            (second_device, user_id, "second"),
            (other_device, other_id, "other"),
        ):
            connection.execute(
                "INSERT INTO account.device "
                "(device_id, user_id, device_name, platform, app_version) "
                "VALUES (%s, %s, %s, 'ANDROID', 'm6-test')",
                (target, owner, name),
            )
        for index, session_id in enumerate(device_sessions):
            connection.execute(
                """
                INSERT INTO account.user_session (
                  session_id, user_id, device_id, refresh_token_hash, issued_at,
                  expires_at, last_rotated_at, session_mode
                ) VALUES (%s,%s,%s,%s,now(),now()+interval '1 day',now(),'LEGACY')
                """,
                (session_id, user_id, device_id, bytes([index + 1]) * 32),
            )
        connection.execute(
            """
            INSERT INTO account.user_session (
              session_id, user_id, device_id, refresh_token_hash, issued_at,
              expires_at, last_rotated_at, session_mode
            ) VALUES (%s,%s,%s,%s,now(),now()+interval '1 day',now(),'LEGACY')
            """,
            (single_session, user_id, second_device, b"z" * 32),
        )
        connection.commit()

    engine = create_engine(database_harness.database_url(database_name))
    service = AdminCommandService(
        SqlAlchemyAdminCommandRepository(sessionmaker(engine, class_=Session))
    )
    actor = WebActor(server_id, user_id, web_id, AccountRole.OWNER, 0)
    device_operation, session_operation = uuid4(), uuid4()
    assert (
        service.revoke_android_device(
            AdminCommand(actor, device_operation, device_id, b"d" * 32, "owner_requested")
        )["outcome"]
        == "APPLIED"
    )
    assert (
        service.revoke_android_session(
            AdminCommand(actor, session_operation, single_session, b"s" * 32, "owner_requested")
        )["outcome"]
        == "APPLIED"
    )
    with pytest.raises(WebAdminError, match="forbidden"):
        service.revoke_android_device(AdminCommand(actor, uuid4(), other_device, b"o" * 32))
    with database_harness.connect(database_name) as connection:
        device_state = connection.execute(
            "SELECT revoked_at IS NOT NULL FROM account.device WHERE device_id=%s", (device_id,)
        ).fetchone()
        device_session_count = connection.execute(
            "SELECT count(*) FROM account.user_session "
            "WHERE device_id=%s AND revoked_at IS NOT NULL",
            (device_id,),
        ).fetchone()
        single_state = connection.execute(
            "SELECT revoked_at IS NOT NULL FROM account.user_session WHERE session_id=%s",
            (single_session,),
        ).fetchone()
        audit_count = connection.execute(
            "SELECT count(*) FROM audit.audit_event WHERE request_id IN (%s,%s)",
            (device_operation, session_operation),
        ).fetchone()
    assert device_state == (True,) and device_session_count == (2,)
    assert single_state == (True,) and audit_count == (2,)
    engine.dispose()
