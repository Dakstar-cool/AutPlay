"""Authenticated A1C policy and run views; provider data never crosses this boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from autplay.application.discovery_automation import (
    AUTO_IMPORT_CONFIRMATION,
    DiscoveryAutomationError,
    DiscoveryAutomationService,
    DiscoveryRunView,
    PolicyMutation,
    PolicyView,
    ReleaseCandidateView,
)
from autplay.domain.web_admin import AuthenticatedWebSession, WebAdminError
from autplay.entrypoints.admin_web_http import Renderer, WebAdminHttp
from autplay.runtime.web_security import (
    WebCookieProfile,
    apply_admin_security_headers,
    decode_request_integrity_token,
    encode_request_integrity_token,
    parse_urlencoded_form,
    require_exact_origin,
)
from autplay.web.presentation import navigation
from autplay.web.renderer import resolve_locale


def create_discovery_automation_router(
    *,
    web: WebAdminHttp,
    automation: DiscoveryAutomationService,
    renderer: Renderer,
    origin: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    """Create the owner-scoped A1C Admin Web surface under a distinct route prefix."""

    cookies = WebCookieProfile.for_origin(origin)
    router = APIRouter(prefix="/admin/discovery/automation")

    def render_page(
        request: Request,
        authenticated: AuthenticatedWebSession,
        *,
        policies: tuple[PolicyView, ...] = (),
        runs: tuple[DiscoveryRunView, ...] = (),
        candidates: tuple[ReleaseCandidateView, ...] = (),
        selected_run_id: UUID | None = None,
        notice: str | None = None,
    ) -> Response:
        locale = resolve_locale(
            request.query_params.get("lang"), request.headers.get("accept-language")
        )
        content = renderer.render(
            "discovery_automation.html",
            locale=locale,
            context={
                "page_title": "Discovery automation",
                "authenticated": True,
                "navigation": navigation(
                    "discovery-automation",
                    discovery_enabled=True,
                    discovery_automation_enabled=True,
                ),
                "flash": None,
                "development_mode": not cookies.secure,
                "language_url": (
                    f"/admin/discovery/automation?lang={'ru' if locale == 'en' else 'en'}"
                ),
                "csrf_token": encode_request_integrity_token(authenticated.csrf),
                "policy_operation_id": str(uuid4()),
                "policies": policies,
                "runs": runs,
                "candidates": candidates,
                "selected_run_id": str(selected_run_id) if selected_run_id is not None else None,
                "run_operation_ids": {str(policy.policy_id): str(uuid4()) for policy in policies},
                "candidate_operation_ids": {
                    str(candidate.candidate_id): {
                        action: str(uuid4()) for action in ("select", "retry", "ignore")
                    }
                    for candidate in candidates
                },
                "auto_import_confirmation": AUTO_IMPORT_CONFIRMATION,
                "notice": notice,
            },
        )
        value = apply_admin_security_headers(HTMLResponse(content))
        if authenticated.rotated_bearer is not None:
            cookies.set_session(value, authenticated.rotated_bearer.decode(), max_age=1800)
        return value

    def authenticated_safe(request: Request) -> AuthenticatedWebSession | Response:
        try:
            return web.authenticate_safe_get(request.cookies.get(cookies.session_name, "").encode())
        except WebAdminError:
            value = apply_admin_security_headers(RedirectResponse("/admin/login", status_code=303))
            cookies.clear_session(value)
            return value

    @router.get("")
    def page(request: Request) -> Response:
        authenticated = authenticated_safe(request)
        if isinstance(authenticated, Response):
            return authenticated
        try:
            return render_page(
                request,
                authenticated,
                policies=automation.policies(authenticated.actor, limit=100),
                runs=automation.runs(authenticated.actor, limit=50),
            )
        except ValueError, DiscoveryAutomationError:
            return _error(renderer, request, cookies, status=403)

    @router.get("/runs/{run_id}")
    def run_detail(run_id: UUID, request: Request) -> Response:
        authenticated = authenticated_safe(request)
        if isinstance(authenticated, Response):
            return authenticated
        try:
            return render_page(
                request,
                authenticated,
                policies=automation.policies(authenticated.actor, limit=100),
                runs=automation.runs(authenticated.actor, limit=50),
                candidates=automation.candidates(authenticated.actor, run_id, limit=50),
                selected_run_id=run_id,
            )
        except ValueError, DiscoveryAutomationError:
            return _error(renderer, request, cookies, status=404)

    @router.post("/set-policy")
    async def set_policy(request: Request) -> Response:
        try:
            require_exact_origin(request.scope, origin)
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset(
                    {
                        "automation_enabled",
                        "canonical_artist_id",
                        "confirmation_code",
                        "csrf_token",
                        "discovery_mode",
                        "expected_revision",
                        "import_mode",
                        "operation_id",
                        "provider_artist_id",
                    }
                ),
            )
            operation_id = UUID(form["operation_id"])
            expected_revision = _optional_revision(form["expected_revision"])
            confirmation = form["confirmation_code"] or None
            command = PolicyMutation(
                canonical_artist_id=UUID(form["canonical_artist_id"]),
                provider_artist_id=form["provider_artist_id"],
                discovery_mode=form["discovery_mode"],
                import_mode=form["import_mode"],
                automation_enabled=_boolean(form["automation_enabled"]),
                expected_revision=expected_revision,
                operation_id=operation_id,
                confirmation_code=confirmation,
            )
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(), mutation=True
            )
            web.validate_csrf(
                authenticated.actor,
                decode_request_integrity_token(form["csrf_token"]),
                operation_id,
            )
            result = automation.set_policy(authenticated.actor, command, now=now())
            return render_page(
                request,
                authenticated,
                policies=automation.policies(authenticated.actor, limit=100),
                runs=automation.runs(authenticated.actor, limit=50),
                notice="Policy replayed." if result.replayed else "Policy saved.",
            )
        except ValueError, WebAdminError, DiscoveryAutomationError:
            return _error(renderer, request, cookies, status=403)

    @router.post("/run-now")
    async def run_now(request: Request) -> Response:
        try:
            require_exact_origin(request.scope, origin)
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset({"csrf_token", "operation_id", "policy_id"}),
            )
            operation_id = UUID(form["operation_id"])
            policy_id = UUID(form["policy_id"])
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(), mutation=True
            )
            web.validate_csrf(
                authenticated.actor,
                decode_request_integrity_token(form["csrf_token"]),
                operation_id,
            )
            run = automation.run_now(authenticated.actor, policy_id, operation_id, now=now())
            return render_page(
                request,
                authenticated,
                policies=automation.policies(authenticated.actor, limit=100),
                runs=automation.runs(authenticated.actor, limit=50),
                notice=f"Run {run.state}.",
            )
        except ValueError, WebAdminError, DiscoveryAutomationError:
            return _error(renderer, request, cookies, status=403)

    @router.post("/candidates/{action}")
    async def candidate_action(action: str, request: Request) -> Response:
        """Issue one explicit owner-bound candidate command through the application seam."""

        try:
            command = {
                "select": automation.select_candidate,
                "retry": automation.retry_candidate,
                "ignore": automation.ignore_candidate,
            }[action]
            require_exact_origin(request.scope, origin)
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset({"candidate_id", "csrf_token", "operation_id", "run_id"}),
            )
            candidate_id = UUID(form["candidate_id"])
            run_id = UUID(form["run_id"])
            operation_id = UUID(form["operation_id"])
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(), mutation=True
            )
            web.validate_csrf(
                authenticated.actor,
                decode_request_integrity_token(form["csrf_token"]),
                operation_id,
            )
            result = command(authenticated.actor, candidate_id, operation_id, now=now())
            return render_page(
                request,
                authenticated,
                policies=automation.policies(authenticated.actor, limit=100),
                runs=automation.runs(authenticated.actor, limit=50),
                candidates=automation.candidates(authenticated.actor, run_id, limit=50),
                selected_run_id=run_id,
                notice=(
                    f"Candidate {action} replayed."
                    if result.replayed
                    else f"Candidate {action} requested."
                ),
            )
        except KeyError, ValueError, WebAdminError, DiscoveryAutomationError:
            return _error(renderer, request, cookies, status=403)

    @router.head("", include_in_schema=False)
    def page_head(request: Request) -> Response:
        authenticated = authenticated_safe(request)
        if isinstance(authenticated, Response):
            return authenticated
        return apply_admin_security_headers(Response(status_code=200))

    return router


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("boolean is invalid")


def _optional_revision(value: str) -> int | None:
    if value == "":
        return None
    revision = int(value)
    if revision < 1:
        raise ValueError("revision is invalid")
    return revision


def _error(
    renderer: Renderer, request: Request, cookies: WebCookieProfile, *, status: int
) -> Response:
    locale = resolve_locale(
        request.query_params.get("lang"), request.headers.get("accept-language")
    )
    content = renderer.render(
        "error.html",
        locale=locale,
        context={
            "page_title": "Unavailable",
            "authenticated": False,
            "navigation": (),
            "flash": None,
            "development_mode": not cookies.secure,
            "language_url": f"{request.url.path}?lang={'ru' if locale == 'en' else 'en'}",
            "error_code": "discovery_automation_unavailable",
            "title": "Unavailable",
            "message": "This action is unavailable.",
            "retry_url": "/admin/discovery/automation",
        },
    )
    return apply_admin_security_headers(HTMLResponse(content, status_code=status))


__all__ = ("create_discovery_automation_router",)
