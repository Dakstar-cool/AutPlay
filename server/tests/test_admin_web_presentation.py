from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from autplay.domain.admin_views import (
    AdminDashboard,
    AdminDeviceItem,
    AdminPage,
    AdminReviewItem,
    AdminUnavailable,
    AdminVaultStatus,
)
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor
from autplay.web.presentation import dashboard_context, navigation, page_context, status_context
from autplay.web.renderer import AdminTemplateRenderer


def _base(surface: str, locale: str) -> dict[str, object]:
    return {
        "authenticated": True,
        "development_mode": False,
        "flash": None,
        "language_url": f"/admin/{surface}?lang={'ru' if locale == 'en' else 'en'}",
        "navigation": navigation(surface),
        "page_title": "Admin",
    }


def test_device_page_is_bounded_autoescaped_and_action_is_fixed() -> None:
    now = datetime.now(UTC)
    device_id = uuid4()
    context = page_context(
        AdminPage(
            (AdminDeviceItem(device_id, '<script>alert("x")</script>', "ANDROID", now),),
            str(uuid4()),
        ),
        "devices",
        locale="en",
    )
    html = AdminTemplateRenderer().render(
        "table.html", locale="en", context=_base("devices", "en") | context
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert f"/admin/confirm/device/{device_id}?lang=en" in html
    assert "/admin/devices?after=" in html


def test_review_page_excludes_personal_import_and_media_payload() -> None:
    item = AdminReviewItem(uuid4(), uuid4(), "REVIEW_REQUIRED")
    context = page_context(AdminPage((item,), None), "review", locale="ru")
    html = AdminTemplateRenderer().render(
        "table.html", locale="ru", context=_base("review", "ru") | context
    )

    assert "REVIEW_REQUIRED" in html
    assert "Track" not in html and "Artist" not in html and "Album" not in html
    assert str(item.import_entry_id) not in html
    assert str(item.import_job_id) not in html


def test_vault_and_recovery_status_never_render_paths() -> None:
    vault = status_context(
        AdminVaultStatus(3, 2048, 0, 2, 0, 1, 0, datetime.now(UTC), False),
        "vault",
        locale="ru",
    )
    recovery = status_context(AdminUnavailable("unavailable"), "recovery", locale="en")

    vault_html = AdminTemplateRenderer().render(
        "status.html", locale="ru", context=_base("vault", "ru") | vault
    )
    recovery_html = AdminTemplateRenderer().render(
        "status.html", locale="en", context=_base("recovery", "en") | recovery
    )

    assert "2,0 KiB" in vault_html
    assert "C:\\" not in vault_html and "/var/" not in vault_html
    assert "local administrative CLI" in recovery_html


def test_session_expiry_format_is_locale_stable() -> None:
    now = datetime.now(UTC)
    item = AdminDeviceItem(uuid4(), "Phone", "ANDROID", now - timedelta(minutes=1))
    context = page_context(AdminPage((item,), None), "devices", locale="en")
    assert "UTC" in str(context["rows"])


def test_dashboard_names_all_bounded_health_components() -> None:
    actor = WebActor(uuid4(), uuid4(), uuid4(), AccountRole.OWNER, 0)
    context = dashboard_context(
        AdminDashboard(
            "Private",
            True,
            7,
            False,
            postgresql_ready=True,
            worker_status="UNKNOWN",
            vault_status="DEGRADED",
        ),
        actor,
        locale="en",
    )
    health = context["health"]
    assert isinstance(health, tuple)
    assert [item["label"] for item in health] == [
        "component_api",
        "component_postgresql",
        "component_worker",
        "component_vault",
    ]
    html = AdminTemplateRenderer().render(
        "dashboard.html", locale="en", context=_base("dashboard", "en") | context
    )
    assert "PostgreSQL" in html and "CPU worker" in html and "Vault" in html
