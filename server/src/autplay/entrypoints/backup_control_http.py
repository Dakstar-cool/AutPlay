"""Authenticated Admin Web controls for the out-of-process backup agent."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, RedirectResponse, Response

from autplay.application.backup_control import BackupControlService
from autplay.domain.web_admin import AuthenticatedWebSession, WebAdminError
from autplay.entrypoints.admin_web_http import Renderer, WebAdminHttp
from autplay.runtime.web_security import (
    MAX_FORM_BYTES,
    WebCookieProfile,
    apply_admin_security_headers,
    decode_request_integrity_token,
    encode_request_integrity_token,
    parse_urlencoded_form,
    require_exact_origin,
)
from autplay.web.presentation import navigation
from autplay.web.renderer import resolve_locale


def create_backup_control_router(
    *,
    web: WebAdminHttp,
    backups: BackupControlService,
    renderer: Renderer,
    origin: str,
    passkeys_enabled: bool = False,
    discovery_enabled: bool = False,
    discovery_automation_enabled: bool = False,
) -> APIRouter:
    cookies = WebCookieProfile.for_origin(origin)
    router = APIRouter(prefix="/admin/recovery")

    def locale(request: Request) -> str:
        return resolve_locale(
            request.query_params.get("lang"), request.headers.get("accept-language")
        )

    def base(request: Request) -> dict[str, object]:
        return {
            "authenticated": True,
            "development_mode": not cookies.secure,
            "page_title": "Backups",
            "flash": None,
            "navigation": navigation(
                "recovery",
                passkeys_enabled=passkeys_enabled,
                discovery_enabled=discovery_enabled,
                discovery_automation_enabled=discovery_automation_enabled,
            ),
            "language_url": (
                f"{request.url.path}?lang={'en' if locale(request) == 'ru' else 'ru'}"
            ),
        }

    def problem(request: Request, error: Exception) -> Response:
        code = getattr(error, "code", "backup_request_invalid")
        allowed = {
            "forbidden",
            "backup_control_unavailable",
            "backup_request_invalid",
            "backup_target_invalid",
            "backup_size_invalid",
            "backup_warning_invalid",
            "backup_schedule_invalid",
            "backup_policy_stale",
            "backup_policy_missing",
            "backup_already_active",
        }
        error_key = (
            f"backup_error_{code}"
            if code in allowed
            else "backup_error_backup_request_invalid"
        )
        status = 403 if code == "forbidden" else 409
        if code == "backup_control_unavailable":
            status = 503
        response = apply_admin_security_headers(
            HTMLResponse(
                renderer.render(
                    "backup_control.html",
                    locale=locale(request),
                    context={
                        **base(request),
                        "unavailable": True,
                        "error_key": error_key,
                    },
                ),
                status_code=status,
            )
        )
        if status == 503:
            response.headers["Retry-After"] = "5"
        return response

    @router.get("")
    def page(request: Request) -> Response:
        try:
            authenticated = web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode()
            )
            snapshot = backups.snapshot(authenticated.actor)
        except WebAdminError as error:
            if error.code == "authentication_required":
                return apply_admin_security_headers(
                    RedirectResponse("/admin/login", status_code=303)
                )
            return problem(request, error)
        policy = snapshot.policy
        flash = None
        if request.query_params.get("saved") == "1":
            flash = "backup_policy_saved"
        elif request.query_params.get("requested") == "1":
            flash = "backup_requested"
        response = apply_admin_security_headers(
            HTMLResponse(
                renderer.render(
                    "backup_control.html",
                    locale=locale(request),
                    context={
                        **base(request),
                        "flash": flash,
                        "unavailable": False,
                        "snapshot": snapshot,
                        "policy": policy,
                        "policy_operation_id": str(uuid4()),
                        "request_operation_id": str(uuid4()),
                        "csrf_token": encode_request_integrity_token(authenticated.csrf),
                    },
                )
            )
        )
        if authenticated.rotated_bearer is not None:
            cookies.set_session(response, authenticated.rotated_bearer.decode(), max_age=1800)
        return response

    async def fields(request: Request, allowed: frozenset[str]) -> dict[str, str]:
        require_exact_origin(request.scope, origin)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_FORM_BYTES:
                raise ValueError("form too large")
            body.extend(chunk)
        if len(request.headers.getlist("content-type")) != 1:
            raise ValueError("invalid content type")
        value = parse_urlencoded_form(
            request.headers.get("content-type"), bytes(body), allowed_fields=allowed
        )
        if set(value) != allowed:
            raise ValueError("missing fields")
        return value

    def authorize(
        request: Request, form: dict[str, str]
    ) -> tuple[AuthenticatedWebSession, UUID]:
        operation_id = _uuid(form["operation_id"])
        authenticated = web.authenticate(
            request.cookies.get(cookies.session_name, "").encode(), mutation=True
        )
        web.validate_csrf(
            authenticated.actor,
            decode_request_integrity_token(form["csrf_token"]),
            operation_id,
        )
        return authenticated, operation_id

    @router.post("/policy")
    async def policy(request: Request) -> Response:
        try:
            form = await fields(
                request,
                frozenset(
                    {
                        "csrf_token",
                        "operation_id",
                        "expected_revision",
                        "target_id",
                        "max_backup_gib",
                        "warning_percent",
                        "schedule_mode",
                        "schedule_weekday",
                        "schedule_hour",
                    }
                ),
            )
            authenticated, _ = authorize(request, form)
            schedule_mode = form["schedule_mode"]
            schedule_weekday = (
                _integer(form["schedule_weekday"], 1, 7)
                if schedule_mode == "automatic"
                else None
            )
            schedule_hour = (
                _integer(form["schedule_hour"], 0, 23)
                if schedule_mode == "automatic"
                else None
            )
            await run_in_threadpool(
                backups.configure,
                authenticated.actor,
                target_id=form["target_id"],
                max_backup_bytes=_integer(form["max_backup_gib"], 1, 1_048_576) * 1024**3,
                warning_percent=_integer(form["warning_percent"], 50, 99),
                expected_revision=_integer(form["expected_revision"], 0, 2**31 - 1),
                schedule_mode=schedule_mode,
                schedule_weekday=schedule_weekday,
                schedule_hour=schedule_hour,
            )
        except (KeyError, ValueError, WebAdminError) as error:
            return problem(request, error)
        return apply_admin_security_headers(
            RedirectResponse(f"/admin/recovery?lang={locale(request)}&saved=1", status_code=303)
        )

    @router.post("/request")
    async def request_backup(request: Request) -> Response:
        try:
            form = await fields(
                request,
                frozenset(
                    {"csrf_token", "operation_id", "expected_revision"}
                ),
            )
            authenticated, operation_id = authorize(request, form)
            await run_in_threadpool(
                backups.request,
                authenticated.actor,
                operation_id,
                _integer(form["expected_revision"], 1, 2**31 - 1),
            )
        except (KeyError, ValueError, WebAdminError) as error:
            return problem(request, error)
        return apply_admin_security_headers(
            RedirectResponse(
                f"/admin/recovery?lang={locale(request)}&requested=1", status_code=303
            )
        )

    return router


def _uuid(value: str) -> UUID:
    result = UUID(value)
    if str(result) != value:
        raise ValueError("invalid identifier")
    return result


def _integer(value: str, minimum: int, maximum: int) -> int:
    if len(value) > 16:
        raise ValueError("invalid integer")
    result = int(value)
    if str(result) != value or not minimum <= result <= maximum:
        raise ValueError("invalid integer")
    return result


__all__ = ("create_backup_control_router",)
