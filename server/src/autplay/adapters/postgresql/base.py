"""Shared SQLAlchemy metadata for the PostgreSQL persistence adapter."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "pk": "%(table_name)s_pkey",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "%(constraint_name)s",
}


class Base(DeclarativeBase):
    """Declarative base owned exclusively by the PostgreSQL adapter."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


__all__ = ("NAMING_CONVENTION", "Base")
