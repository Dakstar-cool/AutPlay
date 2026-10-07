from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from autplay.adapters.postgresql.admin_views_runtime import SqlAlchemyAdminViewService
from autplay.application.admin_views import AdminViewService
from autplay.domain.admin_views import (
    AdminDiskUsage,
    AdminUnavailable,
    AdminVaultIssue,
    AdminVaultStatus,
)
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError
from autplay.web.presentation import dashboard_context
from autplay.web.renderer import AdminTemplateRenderer, read_static_asset
from autplay.web.vault import vault_context
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from .test_admin_web_http import _client, _Views


def _vault() -> AdminVaultStatus:
    return AdminVaultStatus(
        300,
        2048,
        0,
        300,
        0,
        1,
        59,
        None,
        False,
        uploads_failed=2,
        disk=AdminDiskUsage(
            100 * 1024**3, 60 * 1024**3, 40 * 1024**3, datetime(2026, 10, 6, 11, 15, tzinfo=UTC)
        ),
        issues=(
            AdminVaultIssue(
                "upload",
                "QUARANTINED",
                "media_validation_failed",
                59,
                datetime(2026, 10, 6, 10, 0, tzinfo=UTC),
            ),
            AdminVaultIssue("upload", "FAILED", "private/path?token=secret", 2, None),
        ),
    )


@pytest.mark.parametrize("locale", ("en", "ru"))
def test_vault_explains_upload_failures_without_claiming_saved_music_is_corrupt(
    locale: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_Views, "status", lambda self, actor, surface: _vault())
    client, web = _client(AdminTemplateRenderer())
    client.cookies.set("__Host-autplay_admin", "session")
    web.rotate = True
    result = client.get(f"/admin/vault?lang={locale}")
    assert result.status_code == 200
    assert "59" in result.text and "media_validation_failed" in result.text
    assert "token=secret" not in result.text and "private/path" not in result.text
    assert "vault_error_unknown" not in result.text
    assert "pill-warn" in result.text and 'data-unavailable="false"' in result.text
    assert "rotated" in result.headers["set-cookie"]
    assert result.headers["cache-control"] == "no-store"
    assert "content-security-policy" in result.headers
    assert (
        "does not indicate damage" if locale == "en" else "не означает повреждение"
    ) in result.text
    filtered = client.get(f"/admin/vault?lang={locale}&scope=replica")
    assert filtered.status_code == 200 and "media_validation_failed" not in filtered.text
    invalid = client.get(f"/admin/vault?lang={locale}&scope=https://untrusted.invalid")
    assert invalid.status_code == 200 and "untrusted.invalid" not in invalid.text
    assert 'aria-current="page"' in invalid.text


def test_vault_unavailable_response_has_retry_no_secrets_and_rotates_session() -> None:
    client, web = _client(AdminTemplateRenderer())
    client.cookies.set("__Host-autplay_admin", "session")
    web.rotate = True
    result = client.get("/admin/vault?lang=ru")
    assert result.status_code == 503 and result.headers["retry-after"] == "10"
    assert "vault_status_unavailable" not in result.text
    assert 'data-unavailable="true"' in result.text
    assert "Обновить состояние" in result.text
    assert "rotated" in result.headers["set-cookie"]
    assert result.headers["cache-control"] == "no-store"


def test_vault_requires_admin_authority_and_get_never_mutates() -> None:
    client, web = _client(AdminTemplateRenderer())
    anonymous = client.get("/admin/vault?lang=ru", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/admin/login?lang=ru"
    client.cookies.set("__Host-autplay_admin", "session")
    web.actor = WebActor(uuid4(), uuid4(), uuid4(), AccountRole.USER, 0)
    assert client.get("/admin/vault").status_code == 403
    assert web.mutation_calls == 0 and not web.commands.calls
    assert client.post("/admin/vault").status_code == 405


def test_vault_backend_failure_is_redacted_and_authority_failures_are_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = WebActor(uuid4(), uuid4(), uuid4(), AccountRole.OWNER, 0)

    def unavailable(self: AdminViewService, actor: WebActor, surface: str) -> object:
        raise OperationalError("private connection", None, RuntimeError("secret"))

    monkeypatch.setattr(AdminViewService, "status", unavailable)
    value = SqlAlchemyAdminViewService(sessionmaker()).status(actor, "vault")
    assert value == AdminUnavailable("vault_status_unavailable", cli_guidance=False)
    with pytest.raises(OperationalError):
        SqlAlchemyAdminViewService(sessionmaker()).status(actor, "recovery")

    def forbidden(self: AdminViewService, actor: WebActor, surface: str) -> object:
        raise WebAdminError("forbidden")

    monkeypatch.setattr(AdminViewService, "status", forbidden)
    with pytest.raises(WebAdminError, match="forbidden"):
        SqlAlchemyAdminViewService(sessionmaker()).status(actor, "vault")


def test_vault_healthy_empty_state_and_unknown_values_are_safe() -> None:
    value = AdminVaultStatus(0, 0, 0, 0, 0, 0, 0, None, False)
    context = vault_context(value, locale="en")
    assert context["status_key"] == "status_healthy" and not context["warnings"]
    assert vault_context(object(), locale="en")["unavailable"]
    for name in ("vault-v1.js", "vault-v1.css"):
        payload, digest = read_static_asset(name)
        assert payload and len(digest) == 64


def test_vault_authentication_database_outage_returns_retryable_error_without_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, web = _client(AdminTemplateRenderer())

    def unavailable(bearer: bytes, *, head: bool = False) -> object:
        raise OperationalError("private database", None, RuntimeError("secret"))

    monkeypatch.setattr(web, "authenticate_safe_get", unavailable)
    result = client.get("/admin/vault?lang=ru")
    assert result.status_code == 503 and result.headers["retry-after"] == "10"
    assert "private database" not in result.text and "secret" not in result.text
    assert 'data-unavailable="true"' in result.text
    assert "primary-nav-desktop" not in result.text


def test_vault_http_service_outage_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(self: _Views, actor: WebActor, surface: str) -> object:
        raise OperationalError("private database", None, RuntimeError("secret"))

    monkeypatch.setattr(_Views, "status", unavailable)
    client, _ = _client(AdminTemplateRenderer())
    client.cookies.set("__Host-autplay_admin", "session")
    result = client.get("/admin/vault?lang=en")
    assert result.status_code == 503
    assert "private database" not in result.text and "secret" not in result.text


def test_dashboard_distinguishes_failed_upload_history_from_integrity_errors() -> None:
    from autplay.domain.admin_views import AdminDashboard

    actor = WebActor(uuid4(), uuid4(), uuid4(), AccountRole.OWNER, 0)
    context = dashboard_context(
        AdminDashboard("Test", True, 1, False, vault_status="DEGRADED", vault=_vault()),
        actor,
        locale="en",
    )
    assert isinstance(context["health"], tuple)
    vault = context["health"][-1]
    assert vault["detail"] == "vault_upload_attention_detail"
    assert {item["label"] for item in vault["metrics"]} == {
        "uploads_quarantined",
        "vault_uploads_failed",
    }
