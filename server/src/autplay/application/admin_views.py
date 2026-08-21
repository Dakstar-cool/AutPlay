"""Application guard for bounded, typed M6 read-only administration."""

from __future__ import annotations

from uuid import UUID

from autplay.domain.admin_views import AdminConfirmationTarget, AdminDashboard, AdminPage
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError
from autplay.ports.admin_views import AdminViews


class AdminViewService:
    def __init__(self, views: AdminViews) -> None:
        self._views = views

    def dashboard(self, actor: WebActor) -> AdminDashboard:
        self._authorize(actor)
        return self._views.dashboard(actor)

    def confirmation(
        self, actor: WebActor, action: str, target_id: UUID
    ) -> AdminConfirmationTarget:
        self._authorize(actor)
        if action not in {
            "invitation",
            "device",
            "session",
            "browser-session",
            "logout-current",
            "logout-all",
        }:
            raise WebAdminError("admin_surface_unavailable")
        return self._views.confirmation_target(actor, action, target_id)

    def page(
        self, actor: WebActor, surface: str, *, limit: int = 100, after: str | None = None
    ) -> AdminPage:
        self._authorize(actor)
        if not 1 <= limit <= 200:
            raise ValueError("admin page limit must be within 1..200")
        if surface == "devices":
            return self._views.devices(actor, limit=limit, after=after)
        if surface == "invitations":
            return self._views.invitations(actor, limit=limit, after=after)
        if surface == "audit":
            return self._views.audit(actor, limit=limit, after=after)
        if surface == "sessions":
            return self._views.sessions(actor, limit=limit, after=after)
        if surface == "jobs":
            return self._views.jobs(actor, limit=limit, after=after)
        if surface == "imports":
            return self._views.imports(actor, limit=limit, after=after)
        if surface == "review":
            return self._views.review(actor, limit=limit, after=after)
        if surface == "diagnostics":
            return self._views.diagnostics(actor, limit=limit, after=after)
        raise WebAdminError("admin_surface_unavailable")

    def status(self, actor: WebActor, surface: str) -> object:
        self._authorize(actor)
        if surface == "vault":
            return self._views.vault(actor)
        if surface == "recovery":
            return self._views.recovery(actor)
        raise WebAdminError("admin_surface_unavailable")

    @staticmethod
    def _authorize(actor: WebActor) -> None:
        if actor.role not in {AccountRole.OWNER, AccountRole.ADMIN}:
            raise WebAdminError("forbidden")


__all__ = ("AdminViewService",)
