"""M6 bundled template, localization, and escaping tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.resources import files

from autplay.web.renderer import (
    AdminTemplateRenderer,
    format_bytes,
    format_count,
    format_datetime,
    read_static_asset,
    resolve_locale,
)


def test_login_template_escapes_untrusted_values_and_has_accessible_form() -> None:
    rendered = AdminTemplateRenderer().render(
        "login.html",
        locale="en",
        context={
            "authenticated": False,
            "development_mode": False,
            "error_code": '<script>alert("x")</script>',
            "flash": None,
            "language_url": "/admin/login?lang=ru",
            "challenge_id": "challenge",
            "operation_id": "operation",
            "page_title": "Sign in",
            "preauth_nonce": "nonce",
        },
    )

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert '<label for="browser-invitation">' in rendered
    assert 'type="password"' in rendered
    assert 'autocomplete="off"' in rendered
    assert '<main id="main" tabindex="-1">' in rendered


def test_russian_dashboard_and_locale_formatters_are_resource_backed() -> None:
    rendered = AdminTemplateRenderer().render(
        "dashboard.html",
        locale="ru",
        context={
            "account_label": "Владелец",
            "authenticated": True,
            "development_mode": True,
            "facts": [],
            "flash": None,
            "health": [],
            "language_url": "/admin/?lang=en",
            "logout_all_url": "/admin/confirm/logout-all/user",
            "logout_current_url": "/admin/confirm/logout-current/session",
            "navigation": [],
            "page_title": "Состояние сервера",
            "role": "OWNER",
            "server_label": "Личный сервер",
        },
    )

    assert '<html lang="ru">' in rendered
    assert "Состояние сервера" in rendered
    assert "Режим разработки" in rendered
    value = datetime(2026, 8, 21, 12, 30, tzinfo=UTC)
    assert format_datetime(value, "en") == "Aug 21, 2026, 12:30 UTC"
    assert format_datetime(value, "ru") == "21 авг. 2026, 12:30 UTC"
    assert format_count(12_345, "en") == "12,345"
    assert format_count(12_345, "ru") == "12\N{NO-BREAK SPACE}345"
    assert format_bytes(1_572_864, "en") == "1.5 MiB"
    assert format_bytes(1_572_864, "ru") == "1,5 MiB"


def test_locale_selection_is_bounded() -> None:
    assert resolve_locale("ru", "en-US") == "ru"
    assert resolve_locale(None, "fr-FR, ru-RU;q=0.8") == "ru"
    assert resolve_locale("de", "en-US") == "en"


def test_catalogs_have_exact_key_parity_and_static_asset_is_integrity_checked() -> None:
    catalogs = {
        locale: json.loads(
            files("autplay.web").joinpath("i18n", f"{locale}.json").read_text(encoding="utf-8")
        )
        for locale in ("en", "ru")
    }
    assert set(catalogs["en"]) == set(catalogs["ru"])
    payload, digest = read_static_asset("admin-v1.css")
    assert b"prefers-reduced-motion" in payload
    assert digest == "10e85268761bb7635618153b49f823c2b99349dca5d407cb920ffce79b3a2d39"
