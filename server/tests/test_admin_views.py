from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from autplay.application.admin_views import AdminViewService
from autplay.domain.admin_views import AdminConfirmationTarget, AdminDashboard, AdminPage
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError


class _Views:
    def dashboard(self, actor: WebActor) -> AdminDashboard:
        del actor
        return AdminDashboard("Private", True, 1, False)

    def confirmation_target(
        self, actor: WebActor, action: str, target_id: UUID
    ) -> AdminConfirmationTarget:
        del actor, action
        return AdminConfirmationTarget(target_id, "ANDROID_DEVICE", "Phone")

    def _page(self, actor: WebActor, *, limit: int, after: str | None = None) -> AdminPage:
        del actor, limit, after
        return AdminPage((), None)

    devices = invitations = sessions = jobs = imports = review = diagnostics = audit = _page

    def vault(self, actor: WebActor) -> object:
        del actor
        return object()

    def recovery(self, actor: WebActor) -> object:
        del actor
        return object()


def _actor(role: AccountRole = AccountRole.OWNER) -> WebActor:
    return WebActor(uuid4(), uuid4(), uuid4(), role, 0)


def test_admin_views_reject_non_admin_and_unbounded_limits() -> None:
    service = AdminViewService(_Views())
    with pytest.raises(WebAdminError, match="forbidden"):
        service.dashboard(_actor(AccountRole.USER))
    with pytest.raises(ValueError, match=r"1\.\.200"):
        service.page(_actor(), "devices", limit=201)


@pytest.mark.parametrize(
    "surface",
    ("devices", "sessions", "invitations", "jobs", "imports", "review", "audit", "diagnostics"),
)
def test_admin_views_routes_only_known_bounded_surfaces(surface: str) -> None:
    assert AdminViewService(_Views()).page(_actor(AccountRole.ADMIN), surface) == AdminPage(
        (), None
    )


def test_admin_views_reject_unknown_surface() -> None:
    with pytest.raises(WebAdminError, match="admin_surface_unavailable"):
        AdminViewService(_Views()).page(_actor(), "shell")


def test_confirmation_uses_an_owner_scoped_typed_target() -> None:
    target_id = uuid4()
    target = AdminViewService(_Views()).confirmation(_actor(), "device", target_id)
    assert target == AdminConfirmationTarget(target_id, "ANDROID_DEVICE", "Phone")
    with pytest.raises(WebAdminError, match="admin_surface_unavailable"):
        AdminViewService(_Views()).confirmation(_actor(), "shell", target_id)
