"""Optional M6 browser adapter; all authority and mutations stay in injected services."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from autplay.application.web_admin import LoginChallenge
from autplay.domain.admin_commands import AdminCommand
from autplay.domain.admin_views import AdminConfirmationTarget, AdminDashboard, AdminPage
from autplay.domain.web_admin import (
    AuthenticatedWebSession,
    WebActor,
    WebAdminError,
    WebSessionCredentials,
)
from autplay.runtime.web_security import (
    WebCookieProfile,
    apply_admin_security_headers,
    canonical_form_request_hash,
    decode_request_integrity_token,
    encode_request_integrity_token,
    parse_urlencoded_form,
    require_exact_origin,
    source_rate_key,
)
from autplay.web.presentation import dashboard_context, navigation, page_context, status_context
from autplay.web.renderer import read_static_asset, resolve_locale


class Renderer(Protocol):
    def render(
        self, template: str, *, locale: str, context: Mapping[str, object] | None = None
    ) -> str: ...


class AdminViewsHttp(Protocol):
    def dashboard(self, actor: WebActor) -> AdminDashboard: ...
    def confirmation(
        self, actor: WebActor, action: str, target_id: UUID
    ) -> AdminConfirmationTarget: ...
    def page(
        self,
        actor: WebActor,
        surface: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> AdminPage: ...
    def status(self, actor: WebActor, surface: str) -> object: ...


class AdminCommandsHttp(Protocol):
    """Narrow command surface; the HTTP adapter never accesses persistence directly."""

    def cancel_enrollment_invitation(self, command: AdminCommand) -> dict[str, object]: ...
    def revoke_android_device(self, command: AdminCommand) -> dict[str, object]: ...
    def revoke_android_session(self, command: AdminCommand) -> dict[str, object]: ...


class WebAdminHttp(Protocol):
    def begin_login(self) -> LoginChallenge: ...
    def login_challenge_rate_gate(self, source_key: bytes) -> None: ...
    def login_rate_gate(
        self, source_key: bytes, invitation: bytes, request_sha256: bytes
    ) -> None: ...
    def login_retry_outcome(self, operation_id: UUID, request_sha256: bytes) -> None: ...
    def login(
        self, challenge: LoginChallenge, invitation: bytes, request_sha256: bytes
    ) -> WebSessionCredentials: ...
    def authenticate(self, bearer: bytes, *, mutation: bool) -> AuthenticatedWebSession: ...
    def authenticate_safe_get(
        self, bearer: bytes, *, head: bool = False
    ) -> AuthenticatedWebSession: ...
    def validate_csrf(self, actor: WebActor, csrf: bytes, operation_id: UUID) -> None: ...
    def revoked_lifecycle_retry(
        self, bearer: bytes, operation_id: UUID, action: str, request_sha256: bytes
    ) -> str: ...
    def logout_current(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None = None,
    ) -> None: ...
    def logout_all_browser(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None = None,
    ) -> None: ...
    def revoke_browser_session(
        self,
        actor: WebActor,
        target_session_id: UUID,
        operation_id: UUID,
        request_sha256: bytes,
        reason_code: str | None = None,
    ) -> None: ...


def create_admin_web_router(
    *,
    web: WebAdminHttp,
    views: AdminViewsHttp,
    commands: AdminCommandsHttp,
    renderer: Renderer,
    origin: str,
    source_secret: bytes,
    discovery_enabled: bool = False,
) -> APIRouter:
    if len(source_secret) < 32:
        raise ValueError("admin Web source secret must be at least 32 bytes")
    cookies = WebCookieProfile.for_origin(origin)
    router = APIRouter(prefix="/admin")

    def render(template: str, request: Request | None = None, **context: object) -> str:
        locale = resolve_locale(
            request.query_params.get("lang") if request is not None else None,
            request.headers.get("accept-language") if request is not None else None,
        )
        path = request.url.path if request is not None else "/admin/"
        base = {
            "page_title": "Admin",
            "authenticated": False,
            "navigation": (),
            "flash": None,
            "development_mode": not cookies.secure,
            "language_url": f"{path}?lang={'ru' if locale == 'en' else 'en'}",
            **context,
        }
        return renderer.render(template, locale=locale, context=base)

    def response(content: str, status: int = 200) -> Response:
        return apply_admin_security_headers(HTMLResponse(content, status_code=status))

    def error(code: str, status: int, *, retry_after: int | None = None) -> Response:
        value = response(
            render(
                "error.html",
                error_code=code,
                title="Unavailable",
                message="This action is unavailable.",
                retry_url=None,
            ),
            status,
        )
        if retry_after is not None:
            value.headers["Retry-After"] = str(retry_after)
        return value

    @router.get("/static/admin-v1.css")
    def static_css() -> Response:
        asset_bytes, asset_digest = read_static_asset("admin-v1.css")
        value = apply_admin_security_headers(PlainTextResponse(asset_bytes, media_type="text/css"))
        value.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        value.headers["ETag"] = f'"sha256-{asset_digest}"'
        value.headers["X-Content-Type-Options"] = "nosniff"
        return value

    @router.get("/login")
    def login_get(request: Request) -> Response:
        try:
            web.login_challenge_rate_gate(
                source_rate_key(source_secret, request.client.host if request.client else None)
            )
        except WebAdminError as failure:
            if failure.code == "rate_limited":
                return error("rate_limited", 429, retry_after=900)
            return error("browser_login_unavailable", 403)
        challenge = web.begin_login()
        value = response(
            render(
                "login.html",
                preauth_nonce=challenge.nonce.decode(),
                challenge_id=str(challenge.challenge_id),
                operation_id=str(challenge.login_operation_id),
                error_code=None,
            )
        )
        cookies.set_preauth(value, challenge.cookie.decode())
        return value

    @router.post("/login")
    async def login_post(request: Request) -> Response:
        try:
            require_exact_origin(request.scope, origin)
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset(
                    {"preauth_nonce", "challenge_id", "operation_id", "browser_invitation"}
                ),
            )
            cookie = request.cookies.get(cookies.preauth_name, "")
            challenge = LoginChallenge(
                UUID(form["challenge_id"]),
                UUID(form["operation_id"]),
                cookie.encode(),
                form["preauth_nonce"].encode(),
                datetime.now(UTC) + timedelta(minutes=5),
            )
            request_hash = canonical_form_request_hash("POST", "/admin/login", form)
        except ValueError, WebAdminError:
            value = error("browser_login_unavailable", 403)
            cookies.clear_preauth(value)
            return value
        try:
            web.login_retry_outcome(challenge.login_operation_id, request_hash)
        except WebAdminError as outcome:
            if outcome.code == "browser_login_outcome_unknown":
                value = error(outcome.code, 403)
                cookies.clear_preauth(value)
                return value
            if outcome.code != "browser_invitation_unavailable":
                value = error("browser_login_unavailable", 403)
                cookies.clear_preauth(value)
                return value
        try:
            web.login_rate_gate(
                source_rate_key(source_secret, request.client.host if request.client else None),
                form["browser_invitation"].encode(),
                request_hash,
            )
        except WebAdminError as failure:
            code = "rate_limited" if failure.code == "rate_limited" else "browser_login_unavailable"
            value = error(
                code,
                429 if code == "rate_limited" else 403,
                retry_after=900 if code == "rate_limited" else None,
            )
            cookies.clear_preauth(value)
            return value
        try:
            credentials = web.login(
                challenge,
                form["browser_invitation"].encode(),
                request_hash,
            )
        except WebAdminError:
            try:
                web.login_retry_outcome(challenge.login_operation_id, request_hash)
            except WebAdminError as outcome:
                value = error(outcome.code, 403)
                cookies.clear_preauth(value)
                return value
            value = error("browser_invitation_unavailable", 403)
            cookies.clear_preauth(value)
            return value
        value = apply_admin_security_headers(RedirectResponse("/admin/", status_code=303))
        cookies.set_session(value, credentials.bearer.decode(), max_age=1800)
        cookies.clear_preauth(value)
        return value

    @router.get("/")
    def dashboard(request: Request) -> Response:
        try:
            authenticated = web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode()
            )
            actor = authenticated.actor
            dashboard_value = views.dashboard(actor)
        except WebAdminError:
            value = apply_admin_security_headers(RedirectResponse("/admin/login", status_code=303))
            cookies.clear_session(value)
            return value
        locale = resolve_locale(
            request.query_params.get("lang"), request.headers.get("accept-language")
        )
        value = response(
            render(
                "dashboard.html",
                request,
                authenticated=True,
                navigation=navigation("dashboard", discovery_enabled=discovery_enabled),
                **dashboard_context(dashboard_value, actor, locale=locale),
            )
        )
        if authenticated.rotated_bearer is not None:
            cookies.set_session(value, authenticated.rotated_bearer.decode(), max_age=1800)
        return value

    @router.head("/", include_in_schema=False)
    def dashboard_head(request: Request) -> Response:
        try:
            web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode(), head=True
            )
        except WebAdminError:
            value = apply_admin_security_headers(RedirectResponse("/admin/login", status_code=303))
            cookies.clear_session(value)
            return value
        return apply_admin_security_headers(Response(status_code=200))

    def confirmation_details(action: str) -> tuple[str, str, str]:
        try:
            return {
                "invitation": (
                    "web.enrollment_invitation_cancelled",
                    "cancel",
                    "/admin/invitations",
                ),
                "device": ("web.android_device_revoked", "revoke", "/admin/devices"),
                "session": ("web.android_session_revoked", "revoke", "/admin/sessions"),
                "browser-session": ("REVOKE_BROWSER_SESSION", "revoke", "/admin/sessions"),
                "logout-current": ("LOGOUT_CURRENT_BROWSER", "sign_out", "/admin/login"),
                "logout-all": ("LOGOUT_ALL_BROWSER", "logout_all", "/admin/login"),
            }[action]
        except KeyError as error:
            raise WebAdminError("admin_surface_unavailable") from error

    @router.get("/confirm/{action}/{target_id}")
    def confirmation(action: str, target_id: UUID, request: Request) -> Response:
        try:
            authenticated = web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode()
            )
        except WebAdminError:
            value = apply_admin_security_headers(RedirectResponse("/admin/login", status_code=303))
            cookies.clear_session(value)
            return value
        try:
            receipt_action, label_key, cancel_url = confirmation_details(action)
            target = views.confirmation(authenticated.actor, action, target_id)
        except WebAdminError:
            return error("forbidden", 403)
        value = response(
            render(
                "confirm.html",
                request,
                authenticated=True,
                navigation=navigation("sessions", discovery_enabled=discovery_enabled),
                consequence_key=(
                    "confirm_cancel"
                    if action == "invitation"
                    else (
                        "logout_current_consequence"
                        if action == "logout-current"
                        else (
                            "logout_all_consequence" if action == "logout-all" else "confirm_revoke"
                        )
                    )
                ),
                confirm_label_key=label_key,
                action_url=f"/admin/confirm/{action}/{target_id}",
                csrf_token=encode_request_integrity_token(authenticated.csrf),
                operation_id=str(uuid4()),
                target_name=target.label,
                target_name_key={
                    "ANDROID_DEVICE": "target_android_device",
                    "ENROLLMENT_INVITATION": "target_enrollment_invitation",
                    "ANDROID_SESSION": "target_android_session",
                    "BROWSER_SESSION": "target_browser_session",
                    "CURRENT_BROWSER_SESSION": "target_current_browser_session",
                    "ALL_BROWSER_SESSIONS": "target_all_browser_sessions",
                }[target.kind],
                hidden_target=str(target.target_id),
                reason_code="admin_web_confirmation",
                cancel_url=cancel_url,
                receipt_action=receipt_action,
            )
        )
        if authenticated.rotated_bearer is not None:
            cookies.set_session(value, authenticated.rotated_bearer.decode(), max_age=1800)
        return value

    @router.post("/confirm/{action}/{target_id}")
    async def confirmation_post(action: str, target_id: UUID, request: Request) -> Response:
        try:
            receipt_action, _, redirect_url = confirmation_details(action)
            require_exact_origin(request.scope, origin)
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset({"csrf_token", "operation_id", "target", "reason_code"}),
            )
            operation_id = UUID(form["operation_id"])
            if UUID(form["target"]) != target_id or not 1 <= len(form["reason_code"]) <= 64:
                raise ValueError("confirmation target or reason is invalid")
            request_hash = canonical_form_request_hash("POST", request.url.path, form)
        except ValueError, WebAdminError:
            return error("request_validation_failed", 400)
        bearer = request.cookies.get(cookies.session_name, "").encode()
        try:
            authenticated = web.authenticate(bearer, mutation=True)
        except WebAdminError as failure:
            if failure.code == "browser_session_rotation_required":
                return error(failure.code, 409)
            try:
                web.revoked_lifecycle_retry(bearer, operation_id, receipt_action, request_hash)
            except WebAdminError:
                return error("authentication_required", 403)
            value = apply_admin_security_headers(RedirectResponse(redirect_url, status_code=303))
            cookies.clear_session(value)
            return value
        actor = authenticated.actor
        if action == "logout-current" and target_id != actor.web_session_id:
            return error("forbidden", 403)
        if action == "logout-all" and target_id != actor.user_id:
            return error("forbidden", 403)
        try:
            web.validate_csrf(
                actor, decode_request_integrity_token(form["csrf_token"]), operation_id
            )
            command = AdminCommand(
                actor, operation_id, target_id, request_hash, form["reason_code"]
            )
            if action == "invitation":
                commands.cancel_enrollment_invitation(command)
            elif action == "device":
                commands.revoke_android_device(command)
            elif action == "session":
                commands.revoke_android_session(command)
            elif action == "browser-session":
                web.revoke_browser_session(
                    actor, target_id, operation_id, request_hash, form["reason_code"]
                )
            elif action == "logout-current":
                web.logout_current(actor, operation_id, request_hash, form["reason_code"])
            else:
                web.logout_all_browser(actor, operation_id, request_hash, form["reason_code"])
        except ValueError, WebAdminError:
            return error("admin_command_unavailable", 403)
        value = apply_admin_security_headers(RedirectResponse(redirect_url, status_code=303))
        if (
            action in {"logout-current", "logout-all", "browser-session"}
            and target_id == actor.web_session_id
        ):
            cookies.clear_session(value)
        if action == "logout-all":
            cookies.clear_session(value)
        return value

    @router.get("/{surface}")
    def page(surface: str, request: Request) -> Response:
        try:
            authenticated = web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode()
            )
            actor = authenticated.actor
        except WebAdminError:
            value = apply_admin_security_headers(RedirectResponse("/admin/login", status_code=303))
            cookies.clear_session(value)
            return value
        locale = resolve_locale(
            request.query_params.get("lang"), request.headers.get("accept-language")
        )
        try:
            if surface in {"vault", "recovery"}:
                context = status_context(views.status(actor, surface), surface, locale=locale)
                template = "status.html"
            else:
                page_value = views.page(actor, surface, after=request.query_params.get("after"))
                context = page_context(page_value, surface, locale=locale)
                template = "table.html"
        except ValueError, WebAdminError:
            return error("admin_surface_unavailable", 404)
        value = response(
            render(
                template,
                request,
                authenticated=True,
                navigation=navigation(surface, discovery_enabled=discovery_enabled),
                **context,
            )
        )
        if authenticated.rotated_bearer is not None:
            cookies.set_session(value, authenticated.rotated_bearer.decode(), max_age=1800)
        return value

    @router.head("/{surface}", include_in_schema=False)
    def page_head(surface: str, request: Request) -> Response:
        del surface
        try:
            web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode(), head=True
            )
        except WebAdminError:
            value = apply_admin_security_headers(RedirectResponse("/admin/login", status_code=303))
            cookies.clear_session(value)
            return value
        return apply_admin_security_headers(Response(status_code=200))

    return router
