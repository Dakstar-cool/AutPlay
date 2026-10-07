"""Authenticated control page for the operator-owned acquisition spool."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.responses import HTMLResponse, RedirectResponse, Response

from autplay.application.acquisition_control import MAX_PLAYLIST_BYTES, AcquisitionControlService
from autplay.domain.discovery import DiscoveryCandidate, DiscoveryError, ProviderArtistTracks
from autplay.domain.web_admin import AuthenticatedWebSession, WebAdminError
from autplay.entrypoints.admin_web_http import Renderer, WebAdminHttp
from autplay.entrypoints.discovery_admin_http import ManualDiscoveryHttp
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


def create_acquisition_control_router(
    *,
    web: WebAdminHttp,
    acquisition: AcquisitionControlService,
    renderer: Renderer,
    origin: str,
    discovery_enabled: bool = False,
    discovery: ManualDiscoveryHttp | None = None,
) -> APIRouter:
    cookies = WebCookieProfile.for_origin(origin)
    router = APIRouter(prefix="/admin/acquisition")

    def locale(request: Request) -> str:
        return resolve_locale(
            request.query_params.get("lang"), request.headers.get("accept-language")
        )

    def page_response(
        request: Request,
        authenticated: AuthenticatedWebSession,
        *,
        notice: str | None = None,
        search_results: tuple[DiscoveryCandidate, ...] = (),
        artist_results: tuple[ProviderArtistTracks, ...] = (),
        search_mode: str | None = None,
    ) -> Response:
        selected = locale(request)
        queues = acquisition.queues(authenticated.actor)
        content = renderer.render(
            "acquisition_control.html",
            locale=selected,
            context={
                "page_title": "Acquisition",
                "authenticated": True,
                "navigation": navigation(
                    "acquisition", acquisition_enabled=True, discovery_enabled=discovery_enabled
                ),
                "flash": None,
                "development_mode": not cookies.secure,
                "language_url": f"/admin/acquisition?lang={'ru' if selected == 'en' else 'en'}",
                "csrf_token": encode_request_integrity_token(authenticated.csrf),
                "upload_operation_id": str(uuid4()),
                "track_operation_id": str(uuid4()),
                "group_operation_id": str(uuid4()),
                "search_operation_id": str(uuid4()),
                "artist_search_operation_id": str(uuid4()),
                "selection_operation_id": str(uuid4()),
                "search_results": search_results,
                "artist_results": artist_results,
                "search_mode": search_mode,
                "live": request.query_params.get("live") == "1",
                "discovery_enabled": discovery_enabled,
                "queues": queues,
                "agent_online": acquisition.agent_online(authenticated.actor),
                "agent_sources": acquisition.agent_sources(authenticated.actor),
                "action_operation_ids": {
                    str(item.queue_id): {
                        action: str(uuid4())
                        for action in (
                            "run",
                            "pause",
                            "resume",
                            "retry",
                            "retry_not_found",
                            "verify",
                        )
                    }
                    for item in queues
                },
                "configure_operation_ids": {str(item.queue_id): str(uuid4()) for item in queues},
                "notice": notice,
            },
        )
        response = apply_admin_security_headers(HTMLResponse(content))
        if authenticated.rotated_bearer is not None:
            cookies.set_session(response, authenticated.rotated_bearer.decode(), max_age=1800)
        return response

    def authorized(request: Request, csrf: str, operation_id: str) -> AuthenticatedWebSession:
        require_exact_origin(request.scope, origin)
        authenticated = web.authenticate(
            request.cookies.get(cookies.session_name, "").encode(), mutation=True
        )
        web.validate_csrf(
            authenticated.actor, decode_request_integrity_token(csrf), UUID(operation_id)
        )
        return authenticated

    def problem(request: Request, code: str, status: int = 400) -> Response:
        selected = locale(request)
        return apply_admin_security_headers(
            HTMLResponse(
                renderer.render(
                    "error.html",
                    locale=selected,
                    context={
                        "page_title": "Acquisition",
                        "authenticated": False,
                        "navigation": (),
                        "flash": None,
                        "development_mode": not cookies.secure,
                        "language_url": (
                            f"/admin/acquisition?lang={'ru' if selected == 'en' else 'en'}"
                        ),
                        "title": "Acquisition unavailable",
                        "message": "The acquisition request could not be completed.",
                        "error_code": code,
                        "retry_url": None,
                    },
                ),
                status_code=status,
            )
        )

    @router.get("")
    def page(request: Request) -> Response:
        try:
            authenticated = web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode()
            )
            notice = (
                "acquisition_submitted"
                if request.query_params.get("submitted") == "1"
                else "acquisition_action_queued"
                if request.query_params.get("action") == "1"
                else None
            )
            return page_response(request, authenticated, notice=notice)
        except WebAdminError as error:
            if error.code == "authentication_required":
                return apply_admin_security_headers(
                    RedirectResponse("/admin/login", status_code=303)
                )
            return problem(request, error.code, 403)

    @router.post("/upload")
    async def upload(request: Request) -> Response:
        uploaded: UploadFile | None = None
        try:
            require_exact_origin(request.scope, origin)
            form = await request.form(max_files=1, max_fields=5, max_part_size=MAX_PLAYLIST_BYTES)
            if set(form) != {
                "csrf_token",
                "operation_id",
                "playlist",
                "workers",
                "yt_dlp_concurrency",
                "soundcloud_concurrency",
            }:
                raise ValueError("form invalid")
            uploaded = form["playlist"] if isinstance(form["playlist"], UploadFile) else None
            if uploaded is None or not (uploaded.filename or "").casefold().endswith(".txt"):
                raise ValueError("file invalid")
            csrf, operation_id = form["csrf_token"], form["operation_id"]
            raw_workers = form["workers"]
            raw_youtube = form["yt_dlp_concurrency"]
            raw_soundcloud = form["soundcloud_concurrency"]
            if not all(
                isinstance(value, str)
                for value in (csrf, operation_id, raw_workers, raw_youtube, raw_soundcloud)
            ):
                raise ValueError("form invalid")
            assert isinstance(csrf, str) and isinstance(operation_id, str)
            assert isinstance(raw_workers, str)
            assert isinstance(raw_youtube, str) and isinstance(raw_soundcloud, str)
            authenticated = authorized(request, csrf, operation_id)
            payload = await uploaded.read(MAX_PLAYLIST_BYTES + 1)
            acquisition.submit(
                authenticated.actor,
                UUID(operation_id),
                payload,
                workers=int(raw_workers),
                yt_dlp_concurrency=int(raw_youtube),
                soundcloud_concurrency=int(raw_soundcloud),
            )
            return apply_admin_security_headers(
                RedirectResponse("/admin/acquisition?submitted=1", status_code=303)
            )
        except ValueError, TypeError, KeyError, WebAdminError:
            return problem(request, "acquisition_input_invalid")
        finally:
            if uploaded is not None:
                await uploaded.close()

    @router.post("/track")
    async def track(request: Request) -> Response:
        try:
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset(
                    {
                        "csrf_token",
                        "operation_id",
                        "artist",
                        "title",
                        "workers",
                        "yt_dlp_concurrency",
                        "soundcloud_concurrency",
                    }
                ),
            )
            authenticated = authorized(request, form["csrf_token"], form["operation_id"])
            acquisition.submit_track(
                authenticated.actor,
                UUID(form["operation_id"]),
                artist=form["artist"],
                title=form["title"],
                workers=int(form["workers"]),
                yt_dlp_concurrency=int(form["yt_dlp_concurrency"]),
                soundcloud_concurrency=int(form["soundcloud_concurrency"]),
            )
            return apply_admin_security_headers(
                RedirectResponse("/admin/acquisition?submitted=1", status_code=303)
            )
        except ValueError, TypeError, KeyError, WebAdminError:
            return problem(request, "acquisition_input_invalid")

    @router.post("/artist-group")
    async def artist_group(request: Request) -> Response:
        try:
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset(
                    {
                        "csrf_token",
                        "operation_id",
                        "artist",
                        "titles",
                        "workers",
                        "yt_dlp_concurrency",
                        "soundcloud_concurrency",
                    }
                ),
            )
            authenticated = authorized(request, form["csrf_token"], form["operation_id"])
            acquisition.submit_artist_group(
                authenticated.actor,
                UUID(form["operation_id"]),
                artist=form["artist"],
                titles=form["titles"],
                workers=int(form["workers"]),
                yt_dlp_concurrency=int(form["yt_dlp_concurrency"]),
                soundcloud_concurrency=int(form["soundcloud_concurrency"]),
            )
            return apply_admin_security_headers(
                RedirectResponse("/admin/acquisition?submitted=1", status_code=303)
            )
        except ValueError, TypeError, KeyError, WebAdminError:
            return problem(request, "acquisition_input_invalid")

    @router.post("/search")
    async def search(request: Request) -> Response:
        try:
            if discovery is None:
                raise ValueError("discovery unavailable")
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset({"csrf_token", "operation_id", "query"}),
            )
            authenticated = authorized(request, form["csrf_token"], form["operation_id"])
            query = " ".join(form["query"].split())
            if not 1 <= len(query) <= 200:
                raise ValueError("query invalid")
            results = await run_in_threadpool(
                discovery.search, authenticated.actor.user_id, query, limit=20
            )
            return page_response(
                request, authenticated, search_results=results, search_mode="track"
            )
        except ValueError, KeyError, WebAdminError, DiscoveryError:
            return problem(request, "acquisition_search_unavailable")

    @router.post("/artist-search")
    async def artist_search(request: Request) -> Response:
        try:
            if discovery is None:
                raise ValueError("discovery unavailable")
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset({"csrf_token", "operation_id", "artist"}),
            )
            authenticated = authorized(request, form["csrf_token"], form["operation_id"])
            artist = " ".join(form["artist"].split())
            if not 1 <= len(artist) <= 200:
                raise ValueError("artist invalid")
            resolved = await run_in_threadpool(
                discovery.resolve_artists, authenticated.actor.user_id, ((artist, 1),)
            )
            exact = tuple(item.provider_artist for item in resolved if item.provider_artist)
            pages = (
                await run_in_threadpool(
                    discovery.preview_artist_tracks,
                    authenticated.actor.user_id,
                    exact,
                    max_tracks_per_artist=25,
                    max_tracks_total=25,
                )
                if exact
                else ()
            )
            return page_response(request, authenticated, artist_results=pages, search_mode="artist")
        except ValueError, KeyError, WebAdminError, DiscoveryError:
            return problem(request, "acquisition_search_unavailable")

    @router.post("/selection")
    async def selection(request: Request) -> Response:
        try:
            require_exact_origin(request.scope, origin)
            form = await request.form(max_files=0, max_fields=30)
            if set(form) != {
                "csrf_token",
                "operation_id",
                "selection",
                "workers",
                "yt_dlp_concurrency",
                "soundcloud_concurrency",
            }:
                raise ValueError("selection form invalid")
            csrf, operation_id = form["csrf_token"], form["operation_id"]
            if not isinstance(csrf, str) or not isinstance(operation_id, str):
                raise ValueError("selection form invalid")
            selected = form.getlist("selection")
            if not 1 <= len(selected) <= 25 or any(
                not isinstance(value, str) for value in selected
            ):
                raise ValueError("selection invalid")
            rows: list[str] = []
            for value in selected:
                assert isinstance(value, str)
                parts = value.split("\t")
                if len(parts) != 2 or any(
                    not 1 <= len(part) <= 200 or any(not char.isprintable() for char in part)
                    for part in parts
                ):
                    raise ValueError("selection invalid")
                rows.append(value)
            authenticated = authorized(request, csrf, operation_id)
            acquisition.submit(
                authenticated.actor,
                UUID(operation_id),
                ("\n".join(rows) + "\n").encode(),
                workers=int(str(form["workers"])),
                yt_dlp_concurrency=int(str(form["yt_dlp_concurrency"])),
                soundcloud_concurrency=int(str(form["soundcloud_concurrency"])),
            )
            return apply_admin_security_headers(
                RedirectResponse("/admin/acquisition?submitted=1", status_code=303)
            )
        except ValueError, KeyError, WebAdminError:
            return problem(request, "acquisition_input_invalid")

    @router.post("/{queue_id}/configure")
    async def configure(queue_id: UUID, request: Request) -> Response:
        try:
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset(
                    {
                        "csrf_token",
                        "operation_id",
                        "workers",
                        "yt_dlp_concurrency",
                        "soundcloud_concurrency",
                    }
                ),
            )
            authenticated = authorized(request, form["csrf_token"], form["operation_id"])
            acquisition.configure(
                authenticated.actor,
                UUID(form["operation_id"]),
                queue_id,
                workers=int(form["workers"]),
                yt_dlp_concurrency=int(form["yt_dlp_concurrency"]),
                soundcloud_concurrency=int(form["soundcloud_concurrency"]),
            )
            return apply_admin_security_headers(
                RedirectResponse("/admin/acquisition?action=1", status_code=303)
            )
        except ValueError, TypeError, KeyError, WebAdminError:
            return problem(request, "acquisition_action_invalid")

    @router.post("/{queue_id}/{action}")
    async def action(queue_id: UUID, action: str, request: Request) -> Response:
        try:
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset({"csrf_token", "operation_id"}),
            )
            authenticated = authorized(request, form["csrf_token"], form["operation_id"])
            acquisition.action(authenticated.actor, UUID(form["operation_id"]), queue_id, action)
            return apply_admin_security_headers(
                RedirectResponse("/admin/acquisition?action=1", status_code=303)
            )
        except ValueError, TypeError, KeyError, WebAdminError:
            return problem(request, "acquisition_action_invalid")

    return router
