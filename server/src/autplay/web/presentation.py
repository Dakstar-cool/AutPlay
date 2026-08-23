"""Pure M6 view-model mapping for strict server-rendered templates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from autplay.domain.admin_views import (
    AdminAuditItem,
    AdminDashboard,
    AdminDeviceItem,
    AdminImportItem,
    AdminInvitationItem,
    AdminJobItem,
    AdminPage,
    AdminReviewItem,
    AdminSessionItem,
    AdminUnavailable,
    AdminVaultStatus,
)
from autplay.domain.web_admin import WebActor

from .renderer import format_bytes, format_datetime


@dataclass(frozen=True, slots=True)
class NavigationItem:
    href: str
    label: str
    current: bool


_NAVIGATION: Final = (
    ("/admin/", "nav_dashboard", "dashboard"),
    ("/admin/devices", "nav_devices", "devices"),
    ("/admin/sessions", "nav_sessions", "sessions"),
    ("/admin/invitations", "nav_invitations", "invitations"),
    ("/admin/vault", "nav_vault", "vault"),
    ("/admin/jobs", "nav_jobs", "jobs"),
    ("/admin/review", "nav_review", "review"),
    ("/admin/recovery", "nav_recovery", "recovery"),
    ("/admin/diagnostics", "nav_diagnostics", "diagnostics"),
    ("/admin/audit", "nav_audit", "audit"),
    ("/admin/discovery", "nav_discovery", "discovery"),
)


def navigation(surface: str, *, discovery_enabled: bool = False) -> tuple[NavigationItem, ...]:
    return tuple(
        NavigationItem(href, label, current=surface == candidate)
        for href, label, candidate in _NAVIGATION
        if discovery_enabled or candidate != "discovery"
    )


def dashboard_context(
    dashboard: AdminDashboard, actor: WebActor, *, locale: str
) -> dict[str, object]:
    del locale

    def health(label: str, status: str, detail: str) -> dict[str, str]:
        normalized = status.upper()
        return {
            "label": label,
            "status": {
                "HEALTHY": "status_healthy",
                "DEGRADED": "status_degraded",
                "UNAVAILABLE": "status_unavailable",
            }.get(normalized, "status_unknown"),
            "tone": {
                "HEALTHY": "good",
                "DEGRADED": "warn",
                "UNAVAILABLE": "bad",
            }.get(normalized, "warn"),
            "detail": detail,
        }

    return {
        "server_label": dashboard.label,
        "account_label": "OWNER" if actor.role.value == "OWNER" else "ADMIN",
        "role": actor.role.value,
        "health": (
            health(
                "component_api",
                "HEALTHY" if dashboard.api_ready else "UNAVAILABLE",
                "component_api_detail",
            ),
            health(
                "component_postgresql",
                "HEALTHY" if dashboard.postgresql_ready else "UNAVAILABLE",
                "component_postgresql_detail",
            ),
            health("component_worker", dashboard.worker_status, "component_worker_detail"),
            health("component_vault", dashboard.vault_status, "component_vault_detail"),
        ),
        "facts": (
            {"label": "version", "value": dashboard.build_version},
            {"label": "capability_revision", "value": dashboard.capability_revision},
            {
                "label": "recovery_status",
                "value": "status_healthy" if dashboard.recovery_available else "status_unavailable",
            },
        ),
        "logout_current_url": f"/admin/confirm/logout-current/{actor.web_session_id}",
        "logout_all_url": f"/admin/confirm/logout-all/{actor.user_id}",
    }


def page_context(page: AdminPage, surface: str, *, locale: str) -> dict[str, object]:
    columns = _columns(surface)
    rows = tuple(_row(item, surface, locale=locale) for item in page.items)
    return {
        "page_title_key": f"{surface}_title",
        "page_intro_key": f"{surface}_intro",
        "notice_key": "no_secret_notice" if surface == "invitations" else None,
        "columns": columns,
        "rows": rows,
        "actions": any(row["actions"] for row in rows),
        "previous_url": None,
        "next_url": (
            f"/admin/{surface}?after={page.next_after}&lang={locale}"
            if page.next_after is not None
            else None
        ),
    }


def status_context(value: object, surface: str, *, locale: str) -> dict[str, object]:
    if isinstance(value, AdminUnavailable):
        return {
            "page_title_key": f"{surface}_title",
            "page_intro_key": f"{surface}_intro",
            "tone": "unavailable",
            "status_key": "status_unavailable",
            "unavailable": True,
            "facts": (),
        }
    if isinstance(value, AdminVaultStatus):
        return {
            "page_title_key": "vault_title",
            "page_intro_key": "vault_intro",
            "tone": "healthy" if value.unhealthy_replicas == 0 else "degraded",
            "status_key": (
                "status_healthy" if value.unhealthy_replicas == 0 else "status_degraded"
            ),
            "unavailable": False,
            "facts": (
                {"label": "vault_objects", "value": value.object_count},
                {"label": "vault_bytes", "value": format_bytes(value.committed_bytes, locale)},
                {"label": "vault_quarantined", "value": value.quarantined_objects},
                {"label": "vault_replicas", "value": value.available_replicas},
                {"label": "vault_unhealthy", "value": value.unhealthy_replicas},
                {"label": "uploads_open", "value": value.uploads_open},
                {"label": "uploads_quarantined", "value": value.uploads_quarantined},
            ),
        }
    raise ValueError("unsupported admin status view")


def _columns(surface: str) -> tuple[dict[str, str], ...]:
    mapping: dict[str, tuple[tuple[str, str], ...]] = {
        "devices": (("name", "name"), ("platform", "type"), ("created", "created")),
        "invitations": (("created", "created"), ("expires", "expires"), ("state", "status")),
        "sessions": (
            ("kind", "type"),
            ("state", "status"),
            ("expires", "expires"),
            ("current", "current"),
        ),
        "jobs": (
            ("kind", "type"),
            ("state", "status"),
            ("created", "created"),
            ("progress", "details"),
        ),
        "imports": (("adapter", "type"), ("mode", "details"), ("created", "created")),
        "review": (("state", "status"),),
        "audit": (
            ("time", "time"),
            ("action", "action"),
            ("target", "target"),
            ("reason", "reason"),
        ),
        "diagnostics": (
            ("time", "time"),
            ("action", "action"),
            ("target", "target"),
            ("reason", "reason"),
        ),
    }
    try:
        return tuple({"key": key, "label": label} for key, label in mapping[surface])
    except KeyError as error:
        raise ValueError("unsupported admin page") from error


def _row(item: object, surface: str, *, locale: str) -> dict[str, object]:
    actions: tuple[dict[str, str], ...] = ()
    if isinstance(item, AdminDeviceItem):
        actions = ({"href": f"/admin/confirm/device/{item.device_id}", "label": "revoke"},)
        values: dict[str, object] = {
            "name": item.label,
            "platform": item.platform,
            "created": format_datetime(item.created_at, locale),
        }
    elif isinstance(item, AdminInvitationItem):
        actions = ({"href": f"/admin/confirm/invitation/{item.invitation_id}", "label": "cancel"},)
        values = {
            "created": format_datetime(item.issued_at, locale),
            "expires": format_datetime(item.expires_at, locale),
            "state": "TERMINAL" if item.terminal else "ACTIVE",
        }
    elif isinstance(item, AdminSessionItem):
        if item.kind.startswith("ANDROID"):
            actions = ({"href": f"/admin/confirm/session/{item.session_id}", "label": "revoke"},)
        elif not item.current:
            actions = (
                {"href": f"/admin/confirm/browser-session/{item.session_id}", "label": "revoke"},
            )
        values = {
            "kind": item.kind,
            "state": item.state,
            "expires": format_datetime(item.expires_at, locale),
            "current": "✓" if item.current else "—",
        }
    elif isinstance(item, AdminJobItem):
        progress = "—"
        if item.progress_current is not None:
            progress = str(item.progress_current)
            if item.progress_total is not None:
                progress = f"{progress}/{item.progress_total}"
        values = {
            "kind": item.kind,
            "state": item.state,
            "created": format_datetime(item.created_at, locale),
            "progress": progress,
        }
    elif isinstance(item, AdminImportItem):
        values = {
            "adapter": item.adapter_id,
            "mode": item.mode,
            "created": format_datetime(item.created_at, locale),
        }
    elif isinstance(item, AdminReviewItem):
        values = {"state": item.status}
    elif isinstance(item, AdminAuditItem):
        values = {
            "time": format_datetime(item.occurred_at, locale),
            "action": item.action,
            "target": item.target_type,
            "reason": item.reason_code or "—",
        }
    else:
        raise ValueError(f"unsupported item for {surface}")
    return values | {"actions": actions}


__all__ = ("dashboard_context", "navigation", "page_context", "status_context")
