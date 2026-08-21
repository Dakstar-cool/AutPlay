"""Typed runtime configuration precedence and safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from autplay.runtime.settings import (
    ApiSettings,
    RuntimeProfile,
    SettingsLoadError,
    WorkerSettings,
    load_api_settings,
    load_worker_settings,
)
from pydantic import SecretStr

DATABASE_URL = "postgresql+psycopg://runtime_user:database-password@127.0.0.1:5432/autplay"
AUTH_SECRET = "api-signing-secret-with-at-least-thirty-two-bytes"


def test_missing_required_settings_raise_one_sanitized_error() -> None:
    with pytest.raises(SettingsLoadError) as exc_info:
        load_api_settings(environ={})

    assert exc_info.value.code == "runtime_configuration_invalid"
    assert str(exc_info.value) == "runtime_configuration_invalid"


def test_explicit_precedence_and_secret_files(tmp_path: Path) -> None:
    database_secret = tmp_path / "database.secret"
    auth_secret = tmp_path / "auth.secret"
    database_secret.write_text(DATABASE_URL + "\n", encoding="utf-8")
    auth_secret.write_text(AUTH_SECRET + "\n", encoding="utf-8")
    config_file = tmp_path / "autplay.toml"
    config_file.write_text(
        """
profile = "development"

[common]
database_url_file = "database.secret"
log_level = "warning"

[api]
auth_signing_secret_file = "auth.secret"
port = 8000

[profiles.test.common]
log_level = "error"

[profiles.test.api]
port = 8001
""".strip(),
        encoding="utf-8",
    )

    settings = load_api_settings(
        config_file=config_file,
        environ={
            "AUTPLAY_PROFILE": "test",
            "AUTPLAY_DATABASE_URL": DATABASE_URL.replace("database-password", "env-password"),
            "AUTPLAY_API_PORT": "8002",
        },
        overrides={"port": 8003},
    )

    assert settings.profile is RuntimeProfile.TEST
    assert settings.port == 8003
    assert settings.log_level == "ERROR"
    assert "env-password" in settings.database_url.get_secret_value()
    assert settings.auth_signing_secret.get_secret_value() == AUTH_SECRET


def test_direct_secret_precedence_discards_lower_file_key(tmp_path: Path) -> None:
    config_file = tmp_path / "autplay.toml"
    config_file.write_text(
        """
[common]
database_url_file = "missing-database.secret"

[api]
auth_signing_secret_file = "missing-auth.secret"
""".strip(),
        encoding="utf-8",
    )

    settings = load_api_settings(
        config_file=config_file,
        environ={
            "AUTPLAY_DATABASE_URL": DATABASE_URL,
            "AUTPLAY_AUTH_SIGNING_SECRET": AUTH_SECRET,
        },
    )

    assert settings.database_url.get_secret_value() == DATABASE_URL
    assert settings.auth_signing_secret.get_secret_value() == AUTH_SECRET


def test_secret_values_are_redacted_from_model_representations() -> None:
    settings = ApiSettings(
        database_url=SecretStr(DATABASE_URL),
        auth_signing_secret=SecretStr(AUTH_SECRET),
    )

    rendered = f"{settings!r}\n{settings.model_dump()}\n{settings.model_dump_json()}"

    assert "database-password" not in rendered
    assert AUTH_SECRET not in rendered
    assert "**********" in rendered


def test_password_login_cannot_be_enabled_without_persistence_contract() -> None:
    with pytest.raises(SettingsLoadError) as exc_info:
        load_api_settings(
            environ={
                "AUTPLAY_DATABASE_URL": DATABASE_URL,
                "AUTPLAY_AUTH_SIGNING_SECRET": AUTH_SECRET,
                "AUTPLAY_PASSWORD_LOGIN_ENABLED": "true",
            }
        )

    assert exc_info.value.code == "password_login_persistence_contract_missing"


def test_admin_web_requires_exact_origin_and_separate_secret() -> None:
    base = {
        "database_url": DATABASE_URL,
        "auth_signing_secret": AUTH_SECRET,
        "admin_web_enabled": True,
    }
    with pytest.raises(SettingsLoadError):
        load_api_settings(overrides=base, environ={})

    settings = load_api_settings(
        overrides=base
        | {
            "admin_web_origin": "http://127.0.0.1:8787",
            "admin_web_source_hmac_secret": "b" * 32,
            "admin_web_csrf_hmac_secret": "c" * 32,
        },
        environ={},
    )

    assert settings.admin_web_enabled is True
    assert settings.admin_web_origin == "http://127.0.0.1:8787"


@pytest.mark.parametrize(
    ("origin", "profile"),
    (
        ("http://192.168.1.2:8787", RuntimeProfile.DEVELOPMENT),
        ("http://127.0.0.1:8787", RuntimeProfile.PRODUCTION),
        ("https://example.test:443", RuntimeProfile.PRODUCTION),
        ("https://Example.test", RuntimeProfile.PRODUCTION),
        ("https://example.test/admin", RuntimeProfile.PRODUCTION),
    ),
)
def test_admin_web_rejects_ambiguous_or_unsafe_origin(origin: str, profile: RuntimeProfile) -> None:
    with pytest.raises(ValueError):
        ApiSettings(
            profile=profile,
            database_url=SecretStr(DATABASE_URL),
            auth_signing_secret=SecretStr(AUTH_SECRET),
            admin_web_enabled=True,
            admin_web_origin=origin,
            admin_web_source_hmac_secret=SecretStr("b" * 32),
            admin_web_csrf_hmac_secret=SecretStr("c" * 32),
        )


def test_admin_web_requires_distinct_source_and_csrf_hmac_secrets() -> None:
    with pytest.raises(ValueError):
        ApiSettings(
            database_url=SecretStr(DATABASE_URL),
            auth_signing_secret=SecretStr(AUTH_SECRET),
            admin_web_enabled=True,
            admin_web_origin="http://127.0.0.1:8787",
            admin_web_source_hmac_secret=SecretStr("b" * 32),
            admin_web_csrf_hmac_secret=SecretStr("b" * 32),
        )


def test_worker_settings_never_receive_api_signing_secret() -> None:
    settings = load_worker_settings(
        environ={
            "AUTPLAY_DATABASE_URL": DATABASE_URL,
            "AUTPLAY_AUTH_SIGNING_SECRET": AUTH_SECRET,
            "AUTPLAY_WORKER_LEASE_SECONDS": "90",
            "AUTPLAY_WORKER_HEARTBEAT_SECONDS": "20",
        }
    )

    assert isinstance(settings, WorkerSettings)
    assert "auth_signing_secret" not in WorkerSettings.model_fields
    assert settings.lease_seconds == 90
    assert settings.heartbeat_seconds == 20


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    (
        ({"database_url": "sqlite:///:memory:", "auth_signing_secret": AUTH_SECRET}, None),
        (
            {
                "database_url": DATABASE_URL,
                "auth_signing_secret": "too-short",
            },
            None,
        ),
    ),
)
def test_invalid_values_never_escape_in_startup_error(
    overrides: dict[str, object], expected_code: str | None
) -> None:
    del expected_code
    with pytest.raises(SettingsLoadError) as exc_info:
        load_api_settings(overrides=overrides, environ={})

    assert str(exc_info.value) == "runtime_configuration_invalid"
    assert "sqlite" not in str(exc_info.value)
    assert "too-short" not in str(exc_info.value)


def test_worker_timing_is_bounded() -> None:
    with pytest.raises(SettingsLoadError):
        load_worker_settings(
            environ={"AUTPLAY_DATABASE_URL": DATABASE_URL},
            overrides={"lease_seconds": 60, "heartbeat_seconds": 30},
        )
