"""Human-readable Vault diagnostics with fixed navigation and safe error codes."""

from __future__ import annotations

from datetime import UTC, datetime

from autplay.domain.admin_vault import safe_vault_error, vault_error_reason
from autplay.domain.admin_views import AdminDiskUsage, AdminVaultIssue, AdminVaultStatus
from autplay.web.renderer import format_bytes, format_count, format_datetime


def vault_context(value: object, *, locale: str, scope: str = "all") -> dict[str, object]:
    selected = scope if scope in {"all", "upload", "object", "replica"} else "all"
    context: dict[str, object] = {
        "scope": selected,
        "refresh_url": f"/admin/vault?lang={locale}&scope={selected}",
        "unavailable": not isinstance(value, AdminVaultStatus),
        "observed_at": format_datetime(datetime.now(UTC), locale),
        "summary": (),
        "warnings": (),
        "issues": (),
        "issues_truncated": False,
        "has_issues": False,
        "last_verified_at": "—",
        "filters": tuple(
            {
                "label": f"vault_filter_{item}",
                "href": f"/admin/vault?lang={locale}&scope={item}",
                "current": item == selected,
            }
            for item in ("all", "upload", "object", "replica")
        ),
        "tone": "bad",
        "status_key": "status_unavailable",
        "health_detail": "vault_unavailable_detail",
        "disk": disk_context(None, locale=locale),
    }
    if not isinstance(value, AdminVaultStatus):
        return context
    warnings = tuple(
        {
            "label": label,
            "count": format_count(count, locale),
            "detail": detail,
            "guidance": guidance,
        }
        for label, count, detail, guidance in (
            (
                "vault_missing_replicas",
                value.missing_replicas,
                "vault_problem_missing",
                "vault_guidance_missing",
            ),
            (
                "vault_corrupt_replicas",
                value.corrupt_replicas,
                "vault_problem_corrupt",
                "vault_guidance_integrity",
            ),
            (
                "vault_quarantined_replicas",
                value.quarantined_replicas,
                "vault_problem_replica_quarantine",
                "vault_guidance_integrity",
            ),
            (
                "vault_quarantined",
                value.quarantined_objects,
                "vault_problem_object_quarantine",
                "vault_guidance_integrity",
            ),
            (
                "uploads_quarantined",
                value.uploads_quarantined,
                "vault_problem_upload_quarantine",
                "vault_guidance_upload",
            ),
            (
                "vault_uploads_failed",
                value.uploads_failed,
                "vault_problem_upload_failed",
                "vault_guidance_upload",
            ),
        )
        if count
    )
    if value.unhealthy_replicas and not any(
        (
            value.missing_replicas,
            value.corrupt_replicas,
            value.quarantined_replicas,
        )
    ):
        warnings = (
            {
                "label": "vault_unhealthy",
                "count": format_count(value.unhealthy_replicas, locale),
                "detail": "vault_problem_replica_quarantine",
                "guidance": "vault_guidance_integrity",
            },
            *warnings,
        )
    integrity_issue = value.unhealthy_replicas or value.quarantined_objects
    context.update(
        {
            "summary": (
                {"label": "vault_objects", "value": format_count(value.object_count, locale)},
                {"label": "vault_bytes", "value": format_bytes(value.committed_bytes, locale)},
                {
                    "label": "vault_replicas",
                    "value": format_count(value.available_replicas, locale),
                },
                {"label": "uploads_open", "value": format_count(value.uploads_open, locale)},
            ),
            "warnings": warnings,
            "has_issues": bool(warnings),
            "tone": "warn" if warnings else "good",
            "status_key": "status_degraded" if warnings else "status_healthy",
            "health_detail": (
                "vault_attention_detail"
                if integrity_issue
                else "vault_upload_attention_detail"
                if warnings
                else "vault_healthy_detail"
            ),
            "last_verified_at": format_datetime(value.last_verified_at, locale),
            "issues": tuple(
                _issue(item, locale)
                for item in value.issues
                if selected == "all" or item.scope == selected
            ),
            "issues_truncated": value.issues_truncated,
            "disk": disk_context(value.disk, locale=locale),
        }
    )
    return context


def disk_context(value: AdminDiskUsage | None, *, locale: str) -> dict[str, object]:
    if value is None or value.total_bytes <= 0:
        return {"available": False}
    percent = min(100.0, max(0.0, 100 * value.used_bytes / value.total_bytes))
    rendered = f"{percent:.1f}%"
    if locale == "ru":
        rendered = rendered.replace(".", ",")
    return {
        "available": True,
        "percent": percent,
        "percent_text": rendered,
        "used": format_bytes(value.used_bytes, locale),
        "total": format_bytes(value.total_bytes, locale),
        "free": format_bytes(value.free_bytes, locale),
        "observed_at": format_datetime(value.observed_at, locale),
    }


def _issue(item: AdminVaultIssue, locale: str) -> dict[str, object]:
    code = safe_vault_error(item.error_code)
    reason = vault_error_reason(code)
    scope = item.scope if item.scope in {"upload", "object", "replica"} else "unknown"
    state = (
        item.state if item.state in {"FAILED", "QUARANTINED", "MISSING", "CORRUPT"} else "UNKNOWN"
    )
    if scope == "replica":
        reason = {"MISSING": "missing", "CORRUPT": "integrity"}.get(state, "unknown")
    return {
        "scope_key": f"vault_scope_{scope}",
        "state_key": f"vault_state_{state.lower()}",
        "reason_key": f"vault_reason_{reason}",
        "guidance_key": f"vault_guidance_{reason}",
        "code": code if code != "vault_error_unknown" else None,
        "count": format_count(item.count, locale),
        "last_occurred_at": format_datetime(item.last_occurred_at, locale),
    }
