"""Exact-origin browser passkey endpoints; cookies remain HttpOnly M6 authority."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from autplay.application.web_passkeys import PasskeyOptions, WebPasskeyService
from autplay.domain.web_admin import WebAdminError
from autplay.entrypoints.admin_web_http import Renderer, WebAdminHttp
from autplay.runtime.web_security import (
    MAX_FORM_BYTES,
    WebCookieProfile,
    apply_admin_security_headers,
    canonical_form_request_hash,
    decode_request_integrity_token,
    encode_request_integrity_token,
    parse_urlencoded_form,
    require_exact_origin,
    source_rate_key,
)
from autplay.web.presentation import navigation
from autplay.web.renderer import read_static_asset, resolve_locale


def create_web_passkey_router(
    *,
    web: WebAdminHttp,
    passkeys: WebPasskeyService,
    renderer: Renderer,
    origin: str,
    source_secret: bytes,
    discovery_enabled: bool = False,
    discovery_automation_enabled: bool = False,
) -> APIRouter:
    if len(source_secret) < 32:
        raise ValueError("passkey source secret must contain at least 32 bytes")
    cookies = WebCookieProfile.for_origin(origin)
    router = APIRouter(prefix="/admin")

    def failure(error: Exception) -> Response:
        code = error.code if isinstance(error, WebAdminError) else "passkey_invalid"
        status = 429 if code == "rate_limited" else 403
        if code in {
            "operation_conflict",
            "browser_login_outcome_unknown",
            "browser_session_rotation_required",
        }:
            status = 409
        value = apply_admin_security_headers(JSONResponse({"error": code}, status_code=status))
        if status == 429:
            value.headers["Retry-After"] = "900"
        return value

    async def form(request: Request, fields: frozenset[str]) -> dict[str, str]:
        require_exact_origin(request.scope, origin)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_FORM_BYTES:
                raise ValueError("form too large")
            body.extend(chunk)
        return parse_urlencoded_form(
            request.headers.get("content-type"), bytes(body), allowed_fields=fields
        )

    def options_response(value: PasskeyOptions) -> Response:
        return apply_admin_security_headers(
            JSONResponse(
                {
                    "ceremony_id": str(value.ceremony_id),
                    "operation_id": str(value.operation_id),
                    "publicKey": json.loads(value.options_json),
                }
            )
        )

    def source(request: Request) -> bytes:
        return source_rate_key(source_secret, request.client.host if request.client else None)

    @router.get("/static/passkeys-v1.js")
    def javascript() -> Response:
        payload, digest = read_static_asset("passkeys-v1.js")
        value = apply_admin_security_headers(
            PlainTextResponse(payload, media_type="text/javascript")
        )
        value.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        value.headers["ETag"] = f'"sha256-{digest}"'
        return value

    @router.get("/passkeys")
    def settings(request: Request) -> Response:
        try:
            authenticated = web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode()
            )
            keys = passkeys.list_passkeys(authenticated.actor)
        except WebAdminError:
            return apply_admin_security_headers(RedirectResponse("/admin/login", status_code=303))
        locale = resolve_locale(
            request.query_params.get("lang"), request.headers.get("accept-language")
        )
        value = apply_admin_security_headers(
            HTMLResponse(
                renderer.render(
                    "passkeys.html",
                    locale=locale,
                    context={
                        "authenticated": True,
                        "development_mode": False,
                        "page_title": "Passkeys",
                        "flash": None,
                        "language_url": f"/admin/passkeys?lang={'en' if locale == 'ru' else 'ru'}",
                        "navigation": navigation(
                            "passkeys",
                            discovery_enabled=discovery_enabled,
                            discovery_automation_enabled=discovery_automation_enabled,
                            passkeys_enabled=True,
                        ),
                        "passkeys": keys,
                        "csrf_token": encode_request_integrity_token(authenticated.csrf),
                        "operation_id": str(uuid4()),
                        "revoke_operations": {key.passkey_id: str(uuid4()) for key in keys},
                    },
                )
            )
        )
        if authenticated.rotated_bearer is not None:
            cookies.set_session(value, authenticated.rotated_bearer.decode(), max_age=1800)
        return value

    @router.post("/passkeys/options")
    async def registration_options(request: Request) -> Response:
        try:
            fields = await form(request, frozenset({"csrf_token", "operation_id"}))
            return await run_in_threadpool(registration_options_sync, request, fields)
        except (ValueError, KeyError, WebAdminError) as error:
            return failure(error)

    def registration_options_sync(request: Request, fields: dict[str, str]) -> Response:
        try:
            operation = UUID(fields["operation_id"])
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(), mutation=True
            )
            web.validate_csrf(
                authenticated.actor, decode_request_integrity_token(fields["csrf_token"]), operation
            )
            web.login_challenge_rate_gate(source(request))
            value = passkeys.begin_registration(authenticated.actor, operation)
            return options_response(value)
        except (ValueError, KeyError, WebAdminError) as error:
            return failure(error)

    @router.post("/passkeys/verify")
    async def registration_verify(request: Request) -> Response:
        try:
            fields = await form(
                request,
                frozenset({"csrf_token", "operation_id", "ceremony_id", "credential", "label"}),
            )
            return await run_in_threadpool(registration_verify_sync, request, fields)
        except (ValueError, KeyError, WebAdminError) as error:
            return failure(error)

    def registration_verify_sync(request: Request, fields: dict[str, str]) -> Response:
        try:
            operation = UUID(fields["operation_id"])
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(), mutation=True
            )
            web.validate_csrf(
                authenticated.actor, decode_request_integrity_token(fields["csrf_token"]), operation
            )
            web.login_rate_gate(
                source(request),
                fields["ceremony_id"].encode(),
                canonical_form_request_hash("POST", request.url.path, fields),
            )
            identifier = passkeys.finish_registration(
                authenticated.actor,
                UUID(fields["ceremony_id"]),
                operation,
                fields["credential"],
                fields["label"],
            )
            return apply_admin_security_headers(JSONResponse({"passkey_id": str(identifier)}))
        except (ValueError, KeyError, WebAdminError) as error:
            return failure(error)

    @router.post("/login/passkey/options")
    async def login_options(request: Request) -> Response:
        try:
            fields = await form(request, frozenset({"preauth_nonce"}))
            return await run_in_threadpool(login_options_sync, request, fields)
        except (ValueError, KeyError, WebAdminError) as error:
            return failure(error)

    def login_options_sync(request: Request, fields: dict[str, str]) -> Response:
        try:
            web.login_challenge_rate_gate(source(request))
            value = passkeys.begin_login(
                request.cookies.get(cookies.preauth_name, "").encode(),
                fields["preauth_nonce"].encode(),
            )
            return options_response(value)
        except (ValueError, KeyError, WebAdminError) as error:
            return failure(error)

    @router.post("/login/passkey/verify")
    async def login_verify(request: Request) -> Response:
        try:
            fields = await form(
                request, frozenset({"preauth_nonce", "ceremony_id", "operation_id", "credential"})
            )
            return await run_in_threadpool(login_verify_sync, request, fields)
        except (ValueError, KeyError, WebAdminError) as error:
            return failure(error)

    def login_verify_sync(request: Request, fields: dict[str, str]) -> Response:
        try:
            web.login_rate_gate(
                source(request),
                fields["ceremony_id"].encode(),
                canonical_form_request_hash("POST", request.url.path, fields),
            )
            credentials = passkeys.finish_login(
                UUID(fields["ceremony_id"]),
                UUID(fields["operation_id"]),
                request.cookies.get(cookies.preauth_name, "").encode(),
                fields["preauth_nonce"].encode(),
                fields["credential"],
            )
            value = apply_admin_security_headers(JSONResponse({"signed_in": True}))
            cookies.set_session(value, credentials.bearer.decode(), max_age=1800)
            cookies.clear_preauth(value)
            return value
        except (ValueError, KeyError, WebAdminError) as error:
            return failure(error)

    @router.post("/passkeys/{passkey_id}/revoke")
    async def revoke(passkey_id: UUID, request: Request) -> Response:
        try:
            fields = await form(request, frozenset({"csrf_token", "operation_id"}))
            return await run_in_threadpool(revoke_sync, request, fields, passkey_id)
        except (ValueError, KeyError, WebAdminError) as error:
            return failure(error)

    def revoke_sync(request: Request, fields: dict[str, str], passkey_id: UUID) -> Response:
        try:
            operation = UUID(fields["operation_id"])
            request_hash = canonical_form_request_hash("POST", request.url.path, fields)
            bearer = request.cookies.get(cookies.session_name, "").encode()
            try:
                authenticated = web.authenticate(bearer, mutation=True)
            except WebAdminError as error:
                if error.code == "browser_session_rotation_required":
                    raise
                web.revoked_lifecycle_retry(bearer, operation, "REVOKE_WEB_PASSKEY", request_hash)
                value = apply_admin_security_headers(
                    RedirectResponse("/admin/login", status_code=303)
                )
                cookies.clear_session(value)
                return value
            web.validate_csrf(
                authenticated.actor, decode_request_integrity_token(fields["csrf_token"]), operation
            )
            passkeys.revoke(authenticated.actor, passkey_id, operation, request_hash)
            return apply_admin_security_headers(
                RedirectResponse("/admin/passkeys", status_code=303)
            )
        except (ValueError, KeyError, WebAdminError) as error:
            return failure(error)

    return router
