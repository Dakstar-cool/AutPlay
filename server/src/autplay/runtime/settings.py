"""Typed, explicit configuration loading for CPU-only processes.

Configuration is merged in this order, from lowest to highest precedence:
safe model defaults, base TOML sections, selected-profile TOML sections,
secret files, environment variables, and explicit caller overrides.  TOML and
secret files are read only when their paths are supplied explicitly; ``.env``
files and ambient Pydantic secret directories are intentionally disabled.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Final, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

_ENV_PREFIX: Final = "AUTPLAY_"
_MAX_CONFIG_BYTES: Final = 1_048_576
_MAX_SECRET_BYTES: Final = 65_536
_COMMON_ENV_FIELDS: Final = {
    "database_url": "DATABASE_URL",
    "database_connect_timeout_seconds": "DATABASE_CONNECT_TIMEOUT_SECONDS",
    "database_statement_timeout_ms": "DATABASE_STATEMENT_TIMEOUT_MS",
    "log_level": "LOG_LEVEL",
    "vault_root": "VAULT_ROOT",
    "vault_max_object_bytes": "VAULT_MAX_OBJECT_BYTES",
    "vault_max_chunk_bytes": "VAULT_MAX_CHUNK_BYTES",
    "vault_session_ttl_seconds": "VAULT_SESSION_TTL_SECONDS",
    "vault_tool_timeout_seconds": "VAULT_TOOL_TIMEOUT_SECONDS",
    "vault_tool_max_output_bytes": "VAULT_TOOL_MAX_OUTPUT_BYTES",
    "vault_stream_block_bytes": "VAULT_STREAM_BLOCK_BYTES",
    "vault_reconcile_batch_size": "VAULT_RECONCILE_BATCH_SIZE",
    "vault_low_disk_bytes": "VAULT_LOW_DISK_BYTES",
}
_API_ENV_FIELDS: Final = {
    "host": "API_HOST",
    "port": "API_PORT",
    "auth_signing_secret": "AUTH_SIGNING_SECRET",
    "auth_issuer": "AUTH_ISSUER",
    "auth_audience": "AUTH_AUDIENCE",
    "access_token_ttl_seconds": "ACCESS_TOKEN_TTL_SECONDS",
    "refresh_token_ttl_seconds": "REFRESH_TOKEN_TTL_SECONDS",
    "password_login_enabled": "PASSWORD_LOGIN_ENABLED",
}
_STREAM_ENV_FIELDS: Final = {
    "host": "STREAM_HOST",
    "port": "STREAM_PORT",
    "auth_signing_secret": "AUTH_SIGNING_SECRET",
    "auth_issuer": "AUTH_ISSUER",
    "auth_audience": "AUTH_AUDIENCE",
}
_WORKER_ENV_FIELDS: Final = {
    "worker_id": "WORKER_ID",
    "poll_interval_seconds": "WORKER_POLL_INTERVAL_SECONDS",
    "lease_seconds": "WORKER_LEASE_SECONDS",
    "heartbeat_seconds": "WORKER_HEARTBEAT_SECONDS",
    "max_attempts": "WORKER_MAX_ATTEMPTS",
    "retry_base_seconds": "WORKER_RETRY_BASE_SECONDS",
    "retry_max_seconds": "WORKER_RETRY_MAX_SECONDS",
}
_SECRET_FIELDS: Final = frozenset({"database_url", "auth_signing_secret"})


class RuntimeProfile(StrEnum):
    """Named runtime profiles with no implicit environment behavior."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class SettingsLoadError(RuntimeError):
    """A sanitized configuration error safe to report at process startup."""

    default_code: ClassVar[str] = "runtime_configuration_invalid"

    def __init__(self, code: str | None = None) -> None:
        self.code = code or self.default_code
        super().__init__(self.code)


class _ExplicitSettings(BaseSettings):
    """Base class whose only Pydantic source is the already-merged input."""

    model_config = SettingsConfigDict(
        extra="forbid",
        frozen=True,
        env_file=None,
        secrets_dir=None,
        validate_default=True,
    )

    profile: RuntimeProfile = RuntimeProfile.DEVELOPMENT
    database_url: SecretStr = Field(repr=False)
    database_connect_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    database_statement_timeout_ms: int = Field(default=5_000, ge=100, le=120_000)
    log_level: str = "INFO"
    vault_root: Path = Field(default_factory=lambda: Path.cwd() / "var" / "vault")
    vault_max_object_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024
    )
    vault_max_chunk_bytes: int = Field(default=1024 * 1024, ge=1, le=1024 * 1024)
    vault_session_ttl_seconds: int = Field(default=86_400, ge=60, le=604_800)
    vault_tool_timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    vault_tool_max_output_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    vault_stream_block_bytes: int = Field(default=131_072, ge=4_096, le=1_048_576)
    vault_reconcile_batch_size: int = Field(default=100, ge=1, le=10_000)
    vault_low_disk_bytes: int = Field(default=1_073_741_824, ge=0, le=1_099_511_627_776)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        """Disable implicit env, dotenv, and Pydantic secret-directory reads."""

        del settings_cls, env_settings, dotenv_settings, file_secret_settings
        return (init_settings,)

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except ArgumentError as error:
            raise ValueError("database URL must be a valid SQLAlchemy URL") from error
        if url.drivername != "postgresql+psycopg":
            raise ValueError("database URL must use postgresql+psycopg")
        if not url.database or not url.host or url.port is None or not url.username:
            raise ValueError("database URL must include user, host, port, and database")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return normalized

    @field_validator("vault_root")
    @classmethod
    def _validate_vault_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("vault root must be an absolute path")
        return value

    @model_validator(mode="after")
    def _validate_vault_bounds(self) -> Self:
        if self.vault_max_chunk_bytes > self.vault_max_object_bytes:
            raise ValueError("vault chunk limit cannot exceed object limit")
        return self


class ApiSettings(_ExplicitSettings):
    """Validated settings available only to the HTTP API process."""

    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65_535)
    auth_signing_secret: SecretStr = Field(repr=False, min_length=32, max_length=4_096)
    auth_issuer: str = Field(default="autplay", min_length=1, max_length=200)
    auth_audience: str = Field(default="autplay-android", min_length=1, max_length=200)
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=900)
    refresh_token_ttl_seconds: int = Field(default=2_592_000, ge=3_600, le=7_776_000)
    password_login_enabled: bool = False

    @field_validator("host")
    @classmethod
    def _validate_host(cls, value: str) -> str:
        if not value or any(character.isspace() for character in value):
            raise ValueError("API host must be a non-empty host literal")
        return value

    @model_validator(mode="after")
    def _validate_auth_contract(self) -> Self:
        if self.password_login_enabled:
            raise ValueError("password login requires an approved credential persistence contract")
        if self.refresh_token_ttl_seconds <= self.access_token_ttl_seconds:
            raise ValueError("refresh token TTL must exceed access token TTL")
        return self


class WorkerSettings(_ExplicitSettings):
    """Validated settings available only to the CPU worker process."""

    worker_id: str | None = Field(default=None, min_length=1, max_length=300)
    poll_interval_seconds: float = Field(default=1.0, ge=0.05, le=60.0)
    lease_seconds: int = Field(default=120, ge=10, le=3_600)
    heartbeat_seconds: int = Field(default=30, ge=1, le=1_800)
    max_attempts: int = Field(default=5, ge=1, le=20)
    retry_base_seconds: float = Field(default=2.0, ge=0.1, le=3_600.0)
    retry_max_seconds: float = Field(default=300.0, ge=1.0, le=86_400.0)

    @model_validator(mode="after")
    def _validate_worker_timing(self) -> Self:
        if self.heartbeat_seconds * 2 >= self.lease_seconds:
            raise ValueError("worker heartbeat must be less than half the lease")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry maximum must not be less than retry base")
        return self


class StreamSettings(_ExplicitSettings):
    """Validated settings for the isolated authorized streaming process."""

    host: str = "127.0.0.1"
    port: int = Field(default=8788, ge=1, le=65_535)
    auth_signing_secret: SecretStr = Field(repr=False, min_length=32, max_length=4_096)
    auth_issuer: str = Field(default="autplay", min_length=1, max_length=200)
    auth_audience: str = Field(default="autplay-android", min_length=1, max_length=200)

    @field_validator("host")
    @classmethod
    def _validate_host(cls, value: str) -> str:
        if not value or any(character.isspace() for character in value):
            raise ValueError("stream host must be a non-empty host literal")
        return value


def load_api_settings(
    *,
    overrides: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
    config_file: Path | None = None,
) -> ApiSettings:
    """Load API settings using the documented explicit precedence."""

    return _load_settings(
        ApiSettings,
        component="api",
        component_env_fields=_API_ENV_FIELDS,
        overrides=overrides,
        environ=environ,
        config_file=config_file,
    )


def load_worker_settings(
    *,
    overrides: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
    config_file: Path | None = None,
) -> WorkerSettings:
    """Load CPU-worker settings without reading any API signing secret."""

    return _load_settings(
        WorkerSettings,
        component="worker",
        component_env_fields=_WORKER_ENV_FIELDS,
        overrides=overrides,
        environ=environ,
        config_file=config_file,
    )


def load_stream_settings(
    *,
    overrides: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
    config_file: Path | None = None,
) -> StreamSettings:
    """Load stream settings without CPU worker-specific tool configuration."""

    return _load_settings(
        StreamSettings,
        component="stream",
        component_env_fields=_STREAM_ENV_FIELDS,
        overrides=overrides,
        environ=environ,
        config_file=config_file,
    )


def _load_settings[SettingsT: _ExplicitSettings](
    settings_type: type[SettingsT],
    *,
    component: str,
    component_env_fields: Mapping[str, str],
    overrides: Mapping[str, object] | None,
    environ: Mapping[str, str] | None,
    config_file: Path | None,
) -> SettingsT:
    environment = os.environ if environ is None else environ
    explicit = dict(overrides or {})
    selected_file = config_file
    if selected_file is None and (raw_file := environment.get(f"{_ENV_PREFIX}CONFIG_FILE")):
        selected_file = Path(raw_file)

    try:
        document = _read_config_document(selected_file)
        profile_value = explicit.get(
            "profile",
            environment.get(
                f"{_ENV_PREFIX}PROFILE",
                document.get("profile", RuntimeProfile.DEVELOPMENT.value),
            ),
        )
        profile = RuntimeProfile(str(profile_value))
        merged = _config_values(document, component=component, profile=profile)
        env_fields = {**_COMMON_ENV_FIELDS, **component_env_fields}
        _merge_secret_files(
            merged,
            environment=environment,
            env_fields=env_fields,
            config_directory=selected_file.parent if selected_file is not None else None,
            explicit=explicit,
        )
        for field_name, env_suffix in env_fields.items():
            if (value := environment.get(f"{_ENV_PREFIX}{env_suffix}")) is not None:
                merged[field_name] = value
        merged["profile"] = profile
        merged.update(explicit)
        if settings_type is ApiSettings and _is_true(merged.get("password_login_enabled")):
            raise SettingsLoadError("password_login_persistence_contract_missing")
        return settings_type.model_validate(merged)
    except SettingsLoadError:
        raise
    except OSError, UnicodeError, ValueError, TypeError, tomllib.TOMLDecodeError:
        raise SettingsLoadError from None


def _read_config_document(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    if path.stat().st_size > _MAX_CONFIG_BYTES:
        raise ValueError("configuration file is too large")
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    return dict(document)


def _config_values(
    document: Mapping[str, object],
    *,
    component: str,
    profile: RuntimeProfile,
) -> dict[str, object]:
    allowed_top_level = {"profile", "common", "api", "stream", "worker", "profiles"}
    if set(document) - allowed_top_level:
        raise ValueError("configuration contains unknown top-level keys")
    merged: dict[str, object] = {}
    _merge_table(merged, document.get("common"))
    _merge_table(merged, document.get(component))
    profiles = _require_table(document.get("profiles"), optional=True)
    if profiles is not None:
        profile_table = _require_table(profiles.get(profile.value), optional=True)
        if profile_table is not None:
            _merge_table(merged, profile_table.get("common"))
            _merge_table(merged, profile_table.get(component))
    return merged


def _merge_table(target: dict[str, object], value: object) -> None:
    table = _require_table(value, optional=True)
    if table is not None:
        target.update(table)


def _require_table(
    value: object,
    *,
    optional: bool,
) -> dict[str, object] | None:
    if value is None and optional:
        return None
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("configuration section must be a TOML table")
    return value


def _merge_secret_files(
    merged: dict[str, object],
    *,
    environment: Mapping[str, str],
    env_fields: Mapping[str, str],
    config_directory: Path | None,
    explicit: Mapping[str, object],
) -> None:
    for field_name in _SECRET_FIELDS.intersection(env_fields):
        config_file_key = f"{field_name}_file"
        if field_name in explicit:
            merged.pop(config_file_key, None)
            continue
        env_suffix = env_fields[field_name]
        if f"{_ENV_PREFIX}{env_suffix}" in environment:
            merged.pop(config_file_key, None)
            continue
        file_value = environment.get(f"{_ENV_PREFIX}{env_suffix}_FILE")
        if file_value is None and config_file_key in merged:
            raw_config_file = merged.pop(config_file_key)
            if not isinstance(raw_config_file, str):
                raise ValueError("secret file path must be a string")
            secret_path = Path(raw_config_file)
            if not secret_path.is_absolute() and config_directory is not None:
                secret_path = config_directory / secret_path
        elif file_value is not None:
            merged.pop(config_file_key, None)
            secret_path = Path(file_value)
        else:
            continue
        merged[field_name] = _read_secret(secret_path)


def _read_secret(path: Path) -> str:
    if path.stat().st_size > _MAX_SECRET_BYTES:
        raise ValueError("secret file is too large")
    value = path.read_text(encoding="utf-8")
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if not value or "\x00" in value:
        raise ValueError("secret file is empty or invalid")
    return value


def _is_true(value: object) -> bool:
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


__all__ = (
    "ApiSettings",
    "RuntimeProfile",
    "SettingsLoadError",
    "StreamSettings",
    "WorkerSettings",
    "load_api_settings",
    "load_stream_settings",
    "load_worker_settings",
)
