"""Short-session facade for M6 administrative read models."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from autplay.application.admin_views import AdminViewService
from autplay.domain.admin_views import AdminConfirmationTarget, AdminDashboard, AdminPage
from autplay.domain.web_admin import WebActor

from .admin_views import PostgreSqlAdminViews


class SqlAlchemyAdminViewService:
    """Open one read-only ORM session per bounded application query."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def dashboard(self, actor: WebActor) -> AdminDashboard:
        with self._sessions() as session:
            return AdminViewService(PostgreSqlAdminViews(session)).dashboard(actor)

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
        with self._sessions() as session:
            return AdminViewService(PostgreSqlAdminViews(session)).status(actor, surface)


__all__ = ("SqlAlchemyAdminViewService",)
