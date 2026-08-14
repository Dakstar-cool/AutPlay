"""Process-level assembly for authentication runtime dependencies."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthUnitOfWorkFactory
from autplay.adapters.security.tokens import Hs256AccessTokenCodec, OpaqueRefreshTokenCodec
from autplay.adapters.system import SystemClock, Uuid7Generator
from autplay.application.auth import AuthService
from autplay.runtime.settings import ApiSettings


def build_auth_service(settings: ApiSettings, engine: Engine) -> AuthService:
    """Assemble authentication without opening a database connection eagerly."""

    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    access_ttl = timedelta(seconds=settings.access_token_ttl_seconds)
    return AuthService(
        unit_of_work_factory=SqlAlchemyAuthUnitOfWorkFactory(sessions),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        access_tokens=Hs256AccessTokenCodec(
            settings.auth_signing_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            max_ttl=access_ttl,
        ),
        refresh_tokens=OpaqueRefreshTokenCodec(),
        access_token_ttl=access_ttl,
        refresh_token_ttl=timedelta(seconds=settings.refresh_token_ttl_seconds),
    )


__all__ = ("build_auth_service",)
