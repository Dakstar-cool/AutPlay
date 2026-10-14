"""Real database gates for session history retention, filters and mixed pagination."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql import admin_views
from autplay.adapters.postgresql.models.account import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.web_admin import WebSessionRow
from autplay.domain.admin_views import AdminSessionFilters, AdminSessionItem
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor
from sqlalchemy import create_engine, func, literal, select
from sqlalchemy.orm import Session


def test_session_history_filters_retention_and_pagination(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 10, 14, 12, tzinfo=UTC)
    monkeypatch.setattr(func, "statement_timestamp", lambda: literal(now))
    engine = create_engine(database_url)
    owner, other, server, other_server, device, current = (uuid4() for _ in range(6))
    actor = WebActor(server, owner, current, AccountRole.OWNER, 0)
    ids: dict[str, UUID] = {}
    cases = (
        ("short", now + timedelta(hours=12), None),
        ("week", now + timedelta(days=3), None),
        ("later", now + timedelta(days=20), None),
        ("expired", now - timedelta(days=2), None),
        ("boundary", now - timedelta(days=7), None),
        ("old_expired", now - timedelta(days=7, seconds=1), None),
        ("revoked", now + timedelta(days=1), now - timedelta(days=2)),
        ("old_revoked", now + timedelta(days=1), now - timedelta(days=8)),
        ("late_revoke", now - timedelta(days=8), now - timedelta(days=1)),
    )
    try:
        with Session(engine) as session:
            session.add_all(
                UserAccountRow(user_id=identifier, display_name="Session history", role="OWNER")
                for identifier in (owner, other)
            )
            session.flush()
            session.add(
                DeviceRow(
                    device_id=device,
                    user_id=owner,
                    device_name="Test phone",
                    platform="ANDROID",
                    app_version="history-test",
                )
            )
            for server_id in (server, other_server):
                session.add(
                    ServerInstanceRow(
                        server_instance_id=server_id,
                        identity_epoch=1,
                        identity_public_key_spki=b"s" * 65,
                        identity_thumbprint_sha256=server_id.bytes * 2,
                        label_hint="Test server",
                        api_origin="https://api.invalid",
                        stream_origin="https://stream.invalid",
                        capability_revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.flush()
            for kind in ("android", "web"):
                for name, expiry, revoked_at in cases:
                    identifier = uuid4()
                    ids[f"{kind}_{name}"] = identifier
                    issued = now - timedelta(days=30)
                    if kind == "android":
                        session.add(
                            UserSessionRow(
                                session_id=identifier,
                                user_id=owner,
                                device_id=device,
                                refresh_token_hash=identifier.bytes * 2,
                                issued_at=issued,
                                expires_at=expiry,
                                revoked_at=revoked_at,
                                last_rotated_at=issued,
                                session_mode="LEGACY",
                            )
                        )
                    else:
                        session.add(_browser(identifier, owner, server, issued, expiry, revoked_at))
            ids["current"] = current
            ids["idle_expired"], ids["idle_old"], ids["absolute_expired"] = (
                uuid4() for _ in range(3)
            )
            session.add(_browser(current, owner, server, now, now + timedelta(hours=1)))
            for name, expiry in (
                ("idle_expired", now - timedelta(days=1)),
                ("idle_old", now - timedelta(days=8)),
            ):
                row = _browser(ids[name], owner, server, now - timedelta(days=30), expiry)
                row.absolute_expires_at = now + timedelta(days=1)
                session.add(row)
            row = _browser(
                ids["absolute_expired"],
                owner,
                server,
                now - timedelta(days=30),
                now + timedelta(days=1),
            )
            row.absolute_expires_at = now - timedelta(days=1)
            session.add(row)
            session.add(_browser(uuid4(), other, server, now, now + timedelta(hours=1)))
            session.add(_browser(uuid4(), owner, other_server, now, now + timedelta(hours=1)))
            session.commit()

        # Include both expiration clocks and active sessions issued more than seven days ago.
        active = {
            f"{kind}_{name}" for kind in ("android", "web") for name in ("short", "week", "later")
        }
        active.add("current")
        expired = {
            f"{kind}_{name}" for kind in ("android", "web") for name in ("expired", "boundary")
        } | {"idle_expired", "absolute_expired"}
        revoked = {"android_revoked", "web_revoked"}
        visible = active | expired | revoked
        expectations: tuple[tuple[AdminSessionFilters, set[str]], ...] = (
            (AdminSessionFilters(), visible),
            (AdminSessionFilters(state="ACTIVE"), active),
            (AdminSessionFilters(state="EXPIRED"), expired),
            (AdminSessionFilters(state="REVOKED"), revoked),
            (AdminSessionFilters(expires="expired"), expired),
            (
                AdminSessionFilters(expires="24h"),
                {"android_short", "web_short", "current"} | revoked,
            ),
            (AdminSessionFilters(expires="7d"), active - {"android_later", "web_later"} | revoked),
            (AdminSessionFilters(expires="later"), {"android_later", "web_later"}),
            (AdminSessionFilters(current="yes"), {"current"}),
            (AdminSessionFilters(current="no"), visible - {"current"}),
            (AdminSessionFilters(action="revoke"), active - {"current"}),
            (AdminSessionFilters(action="none"), visible - active | {"current"}),
            (AdminSessionFilters("ACTIVE", "24h", "no", "revoke"), {"android_short", "web_short"}),
            (AdminSessionFilters("REVOKED", current="yes"), set()),
        )
        with Session(engine) as session:
            views = admin_views.PostgreSqlAdminViews(session)
            for filters, names in expectations:
                # Small pages force filtering before LIMIT across both session tables.
                found: list[AdminSessionItem] = []
                after = None
                for _ in range(len(ids) + 1):
                    page = views.sessions(actor, limit=2, after=after, filters=filters)
                    assert all(isinstance(item, AdminSessionItem) for item in page.items)
                    found.extend(item for item in page.items if isinstance(item, AdminSessionItem))
                    if page.next_after is None:
                        break
                    after = page.next_after
                else:
                    pytest.fail("session pagination did not terminate")
                assert [item.session_id for item in found] == sorted(
                    (ids[name] for name in names), reverse=True
                ), filters
                for item in found:
                    expected = (
                        "ACTIVE"
                        if item.session_id in {ids[name] for name in active}
                        else (
                            "REVOKED"
                            if item.session_id in {ids[name] for name in revoked}
                            else "EXPIRED"
                        )
                    )
                    assert item.state == expected
                    assert item.current == (item.session_id == current)
            # Reads leave all authentication records intact, including hidden history.
            assert session.scalar(select(func.count()).select_from(UserSessionRow)) == 9
            assert session.scalar(select(func.count()).select_from(WebSessionRow)) == 15
            page = views.sessions(actor, limit=100)
            idle = next(
                item
                for item in page.items
                if isinstance(item, AdminSessionItem) and item.session_id == ids["idle_expired"]
            )
            assert idle.expires_at == now - timedelta(days=1)
    finally:
        engine.dispose()


def _browser(
    identifier: UUID,
    owner: UUID,
    server: UUID,
    issued: datetime,
    expiry: datetime,
    revoked_at: datetime | None = None,
) -> WebSessionRow:
    return WebSessionRow(
        web_session_id=identifier,
        family_id=identifier,
        server_instance_id=server,
        user_id=owner,
        token_generation=0,
        token_sha256=identifier.bytes * 2,
        csrf_sha256=b"c" * 32,
        issued_at=issued,
        token_issued_at=issued,
        last_activity_at=issued,
        idle_expires_at=expiry,
        absolute_expires_at=expiry,
        revoked_at=revoked_at,
    )
