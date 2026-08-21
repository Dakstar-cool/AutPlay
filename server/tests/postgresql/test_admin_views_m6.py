"""Real-PostgreSQL owner-scope and pagination gates for M6 read models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.admin_views import PostgreSqlAdminViews
from autplay.domain.admin_views import (
    AdminAuditItem,
    AdminConfirmationTarget,
    AdminDeviceItem,
    AdminSessionItem,
)
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError
from psycopg import Connection
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _user(connection: Connection[object], name: str) -> UUID:
    row = connection.execute(
        "INSERT INTO account.user_account (display_name, role) VALUES (%s, 'OWNER') "
        "RETURNING user_id",
        (name,),
    ).fetchone()
    assert row is not None
    return UUID(str(cast(tuple[object, ...], row)[0]))


def test_owner_scope_cursor_sessions_audit_and_vault_are_bounded(
    database_connection: Connection[object], database_url: str
) -> None:
    owner, other = (
        _user(database_connection, "M6 views owner"),
        _user(database_connection, "M6 other"),
    )
    server_id, browser_id = uuid4(), uuid4()
    other_device = uuid4()
    database_connection.execute(
        """
        INSERT INTO account.server_instance (
          server_instance_id, identity_epoch, identity_public_key_spki,
          identity_thumbprint_sha256, label_hint, api_origin, stream_origin,
          capability_revision, created_at, updated_at
        ) VALUES (%s, 1, %s, %s, 'Views server', 'https://api.invalid',
                  'https://stream.invalid', 1, %s, %s)
        """,
        (server_id, b"s" * 65, b"t" * 32, NOW, NOW),
    )
    owner_devices = [uuid4() for _ in range(4)]
    for index, device_id in enumerate(owner_devices):
        database_connection.execute(
            """
            INSERT INTO account.device (
              device_id, user_id, device_name, platform, app_version, created_at
            ) VALUES (%s, %s, %s, 'ANDROID', 'm6-test', %s)
            """,
            (device_id, owner, f"Owner device {index}", NOW + timedelta(seconds=index)),
        )
    database_connection.execute(
        "INSERT INTO account.device "
        "(device_id, user_id, device_name, platform, app_version, created_at) "
        "VALUES (%s, %s, 'Other secret device', 'ANDROID', 'm6-test', %s)",
        (other_device, other, NOW),
    )
    android_session = uuid4()
    database_connection.execute(
        """
        INSERT INTO account.user_session (
          session_id, user_id, device_id, refresh_token_hash, issued_at, expires_at,
          last_rotated_at, session_mode
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'LEGACY')
        """,
        (
            android_session,
            owner,
            owner_devices[0],
            b"r" * 32,
            NOW,
            NOW + timedelta(days=1),
            NOW,
        ),
    )
    database_connection.execute(
        """
        INSERT INTO account.web_session (
          web_session_id, family_id, server_instance_id, user_id, token_generation,
          token_sha256, csrf_sha256, issued_at, token_issued_at, last_activity_at,
          idle_expires_at, absolute_expires_at
        ) VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            browser_id,
            browser_id,
            server_id,
            owner,
            b"b" * 32,
            b"c" * 32,
            NOW,
            NOW,
            NOW,
            NOW + timedelta(minutes=30),
            NOW + timedelta(hours=12),
        ),
    )
    database_connection.execute(
        """
        INSERT INTO audit.audit_event (
          occurred_at, actor_type, actor_user_id, action, target_type, target_id,
          reason_code, metadata_sanitized
        ) VALUES (%s, 'ADMIN', %s, 'web.test', 'DEVICE', %s, 'safe_reason', '{}')
        """,
        (NOW, owner, owner_devices[0]),
    )
    vault_object = uuid4()
    database_connection.execute(
        """
        INSERT INTO vault.vault_object (
          vault_object_id, sha256, byte_size, detected_mime_type, commit_status,
          committed_at, last_verified_at
        ) VALUES (%s, %s, 2048, 'audio/flac', 'COMMITTED', %s, %s)
        """,
        (vault_object, b"v" * 32, NOW, NOW),
    )
    database_connection.execute(
        """
        INSERT INTO vault.vault_replica (
          vault_object_id, storage_backend, storage_key, replica_status, verified_at
        ) VALUES (%s, 'FILESYSTEM', 'private/path/never-rendered', 'AVAILABLE', %s)
        """,
        (vault_object, NOW),
    )
    database_connection.commit()

    engine = create_engine(database_url)
    actor = WebActor(server_id, owner, browser_id, AccountRole.OWNER, 0)
    try:
        with Session(engine) as session:
            views = PostgreSqlAdminViews(session)
            first = views.devices(actor, limit=2)
            second = views.devices(actor, limit=2, after=first.next_after)
            sessions = views.sessions(actor, limit=10)
            audit = views.audit(actor, limit=10)
            vault = views.vault(actor)
            confirmation = views.confirmation_target(actor, "device", owner_devices[0])
            with pytest.raises(WebAdminError, match="forbidden"):
                views.confirmation_target(actor, "device", other_device)
    finally:
        engine.dispose()

    first_ids = [item.device_id for item in first.items if isinstance(item, AdminDeviceItem)]
    second_ids = [item.device_id for item in second.items if isinstance(item, AdminDeviceItem)]
    assert len(first_ids) == 2 and len(second_ids) == 2
    assert set(first_ids).isdisjoint(second_ids)
    assert set(first_ids + second_ids) == set(owner_devices)
    assert all(
        isinstance(item, AdminDeviceItem) and "Other" not in item.label
        for item in (*first.items, *second.items)
    )
    session_items = [item for item in sessions.items if isinstance(item, AdminSessionItem)]
    assert {item.session_id for item in session_items} == {android_session, browser_id}
    assert next(item for item in session_items if item.session_id == browser_id).current
    audit_items = [item for item in audit.items if isinstance(item, AdminAuditItem)]
    assert [(item.action, item.reason_code) for item in audit_items] == [
        ("web.test", "safe_reason")
    ]
    assert vault.object_count == 1 and vault.committed_bytes == 2048
    assert vault.available_replicas == 1 and vault.unhealthy_replicas == 0
    assert "path" not in repr(vault).lower()
    assert confirmation == AdminConfirmationTarget(
        owner_devices[0], "ANDROID_DEVICE", "Owner device 0"
    )
