"""OWNER-scoped quota forms using existing exact-origin M6 browser authority."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from sqlalchemy.exc import DBAPIError
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, RedirectResponse, Response

from autplay.application.resource_policy import ResourcePolicyService
from autplay.domain.resource_admission import (
    AccountLimitOverride,
    AccountLimits,
    ResourceAdmissionError,
)
from autplay.domain.resource_policy import GlobalResourceLimits, QuotaChange
from autplay.domain.web_admin import WebAdminError
from autplay.entrypoints.admin_web_http import Renderer, WebAdminHttp
from autplay.runtime.web_security import (
    MAX_FORM_BYTES,
    WebCookieProfile,
    WebRequestRejected,
    apply_admin_security_headers,
    decode_request_integrity_token,
    encode_request_integrity_token,
    parse_urlencoded_form,
    require_exact_origin,
)
from autplay.web.presentation import navigation
from autplay.web.renderer import resolve_locale


def create_resource_policy_web_router(
    *,
    web: WebAdminHttp,
    policy: ResourcePolicyService,
    renderer: Renderer,
    origin: str,
    passkeys_enabled: bool = False,
    discovery_enabled: bool = False,
    discovery_automation_enabled: bool = False,
) -> APIRouter:
    """Composition enables this surface only alongside complete resource enforcement."""
    cookies = WebCookieProfile.for_origin(origin)
    router = APIRouter(prefix="/admin/quotas")

    def locale(request: Request) -> str:
        return resolve_locale(
            request.query_params.get("lang"), request.headers.get("accept-language")
        )

    def base(request: Request) -> dict[str, object]:
        return {
            "authenticated": True,
            "development_mode": not cookies.secure,
            "page_title": "Resource limits",
            "flash": None,
            "navigation": navigation(
                "quotas",
                quotas_enabled=True,
                passkeys_enabled=passkeys_enabled,
                discovery_enabled=discovery_enabled,
                discovery_automation_enabled=discovery_automation_enabled,
            ),
            "language_url": f"{request.url.path}?lang={'en' if locale(request) == 'ru' else 'ru'}",
        }

    def problem(request: Request, error: Exception) -> Response:
        key, status = "quota_error_values", 400
        if isinstance(error, (WebAdminError, WebRequestRejected)):
            key, status = "quota_error_authority", 403
        elif isinstance(error, DBAPIError) or (
            isinstance(error, ResourceAdmissionError)
            and error.code in {"resource_budget_unconfigured", "resource_admission_unavailable"}
        ):
            key, status = "quota_error_unavailable", 503
        elif isinstance(error, ResourceAdmissionError) and error.code in {
            "resource_revision_stale",
            "resource_operation_conflict",
            "resource_budget_exceeded",
        }:
            key, status = "quota_error_conflict", 409
        response = apply_admin_security_headers(
            HTMLResponse(
                renderer.render(
                    "quota_error.html",
                    locale=locale(request),
                    context={**base(request), "error_key": key},
                ),
                status_code=status,
            )
        )
        if status == 503:
            response.headers["Retry-After"] = "5"
        return response

    def get_page(request: Request, target: UUID | None) -> Response:
        try:
            authenticated = web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode()
            )
        except WebAdminError:
            return apply_admin_security_headers(RedirectResponse("/admin/login", status_code=303))
        except DBAPIError as error:
            return problem(request, error)
        try:
            cursor = request.query_params.getlist("after")
            if len(cursor) > 1:
                raise ValueError("duplicate cursor")
            after = _uuid(cursor[0]) if cursor else None
            view = policy.editor(authenticated.actor, target)
            accounts = policy.accounts(authenticated.actor, after) if target is None else None
            response = apply_admin_security_headers(
                HTMLResponse(
                    renderer.render(
                        "quotas.html",
                        locale=locale(request),
                        context={
                            **base(request),
                            "view": view,
                            "accounts": accounts,
                            "csrf_token": encode_request_integrity_token(authenticated.csrf),
                            "operation_id": str(uuid4()),
                            "budget_operation_id": str(uuid4()),
                        },
                    )
                )
            )
        except (ValueError, ResourceAdmissionError, WebAdminError, DBAPIError) as error:
            response = problem(request, error)
        if authenticated.rotated_bearer is not None:
            cookies.set_session(response, authenticated.rotated_bearer.decode(), max_age=1800)
        return response

    @router.get("")
    def overview(request: Request) -> Response:
        return get_page(request, None)

    @router.get("/accounts/{target}")
    def account(target: UUID, request: Request) -> Response:
        return get_page(request, target)

    def apply(
        request: Request, fields: dict[str, str], action: str, target: UUID | None
    ) -> Response:
        try:
            operation = _uuid(fields["operation_id"])
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(),
                mutation=True,
            )
            web.validate_csrf(
                authenticated.actor,
                decode_request_integrity_token(fields["csrf_token"]),
                operation,
            )
            expected = _number(fields["expected_global_revision"], minimum=1)
            values: AccountLimits | AccountLimitOverride | GlobalResourceLimits
            if target is not None:
                values = AccountLimitOverride(
                    *(
                        _number(fields[name], minimum=1) if fields[name] else None
                        for name in ("devices", "playbacks", "transfers")
                    )
                )
            elif action == "defaults":
                values = AccountLimits(
                    *(
                        _number(fields[name], minimum=1)
                        for name in ("devices", "playbacks", "transfers")
                    )
                )
            else:
                values = GlobalResourceLimits(
                    _number(fields["playbacks"], minimum=1),
                    _number(fields["transfers"], minimum=1),
                )
            policy.apply(
                authenticated.actor,
                QuotaChange(
                    operation,
                    expected,
                    values,
                    target,
                    _number(fields["expected_account_revision"], minimum=0)
                    if target is not None
                    else None,
                ),
            )
            path = f"/admin/quotas/accounts/{target}" if target else "/admin/quotas"
            return apply_admin_security_headers(
                RedirectResponse(
                    f"{path}?lang={locale(request)}",
                    status_code=303,
                )
            )
        except (ValueError, KeyError, ResourceAdmissionError, WebAdminError, DBAPIError) as error:
            return problem(request, error)

    async def post(request: Request, action: str, target: UUID | None = None) -> Response:
        try:
            require_exact_origin(request.scope, origin)
            required = {
                "operation_id",
                "csrf_token",
                "expected_global_revision",
                "playbacks",
                "transfers",
            }
            if action != "budget":
                required.add("devices")
            if target is not None:
                required.add("expected_account_revision")
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_FORM_BYTES:
                    raise ValueError("form too large")
                body.extend(chunk)
            if len(request.headers.getlist("content-type")) != 1:
                raise ValueError("invalid content type")
            fields = parse_urlencoded_form(
                request.headers.get("content-type"),
                bytes(body),
                allowed_fields=frozenset(required),
            )
            if set(fields) != required:
                raise ValueError("missing fields")
            return await run_in_threadpool(apply, request, fields, action, target)
        except ValueError as error:
            return problem(request, error)

    @router.post("/defaults")
    async def defaults(request: Request) -> Response:
        return await post(request, "defaults")

    @router.post("/budget")
    async def budget(request: Request) -> Response:
        return await post(request, "budget")

    @router.post("/accounts/{target}")
    async def override(target: UUID, request: Request) -> Response:
        return await post(request, "override", target)

    return router


def _uuid(value: str) -> UUID:
    result = UUID(value)
    if str(result) != value:
        raise ValueError("invalid identifier")
    return result


def _number(value: str, *, minimum: int) -> int:
    if len(value) > 16:
        raise ValueError("invalid number")
    number = int(value)
    if str(number) != value or not minimum <= number < 2**53:
        raise ValueError("invalid number")
    return number
