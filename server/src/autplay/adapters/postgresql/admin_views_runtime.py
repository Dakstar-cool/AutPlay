"""Short-session facade for M6 administrative read models."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.admin_views import AdminViewService
from autplay.domain.admin_views import (
    AdminConfirmationTarget,
    AdminDashboard,
    AdminPage,
    AdminUnavailable,
    AdminVaultStatus,
)
from autplay.domain.web_admin import WebActor
from autplay.runtime.vault_disk import VaultDiskSampler

from .admin_views import PostgreSqlAdminViews


class SqlAlchemyAdminViewService:
    """Open one read-only ORM session per bounded application query."""

    def __init__(self, sessions: sessionmaker[Session], *, vault_root: Path | None = None) -> None:
        self._sessions = sessions
        self._disk = VaultDiskSampler(vault_root) if vault_root is not None else None

    def dashboard(self, actor: WebActor) -> AdminDashboard:
        with self._sessions() as session:
            value = AdminViewService(PostgreSqlAdminViews(session)).dashboard(actor)
        if value.vault is not None and self._disk is not None:
            value = replace(value, vault=replace(value.vault, disk=self._disk.snapshot()))
        return value

    def confirmation(
        self, actor: WebActor, action: str, target_id: UUID
    ) -> AdminConfirmationTarget:
        with self._sessions() as session:
            return AdminViewService(PostgreSqlAdminViews(session)).confirmation(
                actor, action, target_id
            )

    def page(
        self,
        actor: WebActor,
        surface: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> AdminPage:
        with self._sessions() as session:
            return AdminViewService(PostgreSqlAdminViews(session)).page(
                actor, surface, limit=limit, after=after
            )

    def status(self, actor: WebActor, surface: str) -> object:
        try:
            with self._sessions() as session:
                value = AdminViewService(PostgreSqlAdminViews(session)).status(actor, surface)
            if isinstance(value, AdminVaultStatus) and self._disk is not None:
                value = replace(value, disk=self._disk.snapshot())
            return value
        except SQLAlchemyError, OSError:
            if surface != "vault":
                raise
            return AdminUnavailable("vault_status_unavailable", cli_guidance=False)


__all__ = ("SqlAlchemyAdminViewService",)
