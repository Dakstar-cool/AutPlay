"""Authenticated SSR routes for manual Jamendo discovery and staging."""

from __future__ import annotations

import base64
import hmac
import json
import time
from typing import Protocol
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from starlette.datastructures import UploadFile
from starlette.responses import HTMLResponse, RedirectResponse, Response

from autplay.adapters.postgresql.discovery_runtime import (
    BulkDiscoveryError,
    BulkPreviewResult,
    BulkStartResult,
)
from autplay.adapters.postgresql.import_runtime import (
    ImportCollectionArtist,
    ImportRuntimeError,
    ImportStartResult,
)
from autplay.domain.discovery import (
    BulkArtistResolution,
    DiscoveryCandidate,
    DiscoveryError,
    ProviderArtist,
    ProviderArtistTracks,
)
from autplay.domain.import_identity import MAX_IMPORT_BYTES, ImportEnvelopeError
from autplay.domain.web_admin import AuthenticatedWebSession, WebActor, WebAdminError
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


class ManualDiscoveryHttp(Protocol):
    def search(
        self, owner_id: UUID, query: str, *, limit: int = 20
    ) -> tuple[DiscoveryCandidate, ...]: ...

    def lookup_for_acquisition(self, provider_track_id: str) -> DiscoveryCandidate: ...

    def resolve_artists(
        self,
        owner_id: UUID,
        artists: tuple[tuple[str, int], ...],
    ) -> tuple[BulkArtistResolution, ...]: ...

    def preview_artist_tracks(
        self,
        owner_id: UUID,
        artists: tuple[ProviderArtist, ...],
        *,
        max_tracks_per_artist: int = 25,
        max_tracks_total: int = 200,
    ) -> tuple[ProviderArtistTracks, ...]: ...


class CollectionImportHttp(Protocol):
    def start_for_web(
        self,
        actor: WebActor,
        *,
        payload: bytes,
        operation_id: UUID,
        format_name: str = "TXT",
        schema_version: str = "1",
    ) -> ImportStartResult: ...

    def collection_artists(
        self,
        actor: WebActor,
        import_job_id: UUID,
        *,
        limit: int = 100,
    ) -> tuple[ImportCollectionArtist, ...]: ...


class BulkDiscoveryHttp(Protocol):
    def require_provider_available(self, actor: WebActor) -> None: ...

    def require_eligible_artists(self, actor: WebActor, artist_names: tuple[str, ...]) -> None: ...

    def save_preview(
        self,
        actor: WebActor,
        *,
        import_job_id: UUID,
        operation_id: UUID,
        resolutions: tuple[BulkArtistResolution, ...],
        pages: tuple[ProviderArtistTracks, ...],
    ) -> BulkPreviewResult: ...

    def start(
        self,
        actor: WebActor,
        *,
        bulk_operation_id: UUID,
        operation_id: UUID,
    ) -> BulkStartResult: ...

    def start_search_acquisition(
        self,
        actor: WebActor,
        *,
        operation_id: UUID,
        evidence: DiscoveryCandidate,
    ) -> BulkStartResult: ...

    def status(self, actor: WebActor, *, bulk_operation_id: UUID) -> BulkStartResult: ...


def create_discovery_admin_router(
    *,
    web: WebAdminHttp,
    discovery: ManualDiscoveryHttp,
    imports: CollectionImportHttp | None = None,
    bulk: BulkDiscoveryHttp | None = None,
    renderer: Renderer,
    origin: str,
    token_secret: bytes,
    discovery_automation_enabled: bool = False,
) -> APIRouter:
    """Create a separate manual-only surface; no Android/public API route is added."""

    if len(token_secret) < 32:
        raise ValueError("discovery selection secret must be at least 32 bytes")
    cookies = WebCookieProfile.for_origin(origin)
    router = APIRouter(prefix="/admin/discovery")

    def render_page(
        request: Request,
        authenticated: AuthenticatedWebSession,
        *,
        candidates: tuple[DiscoveryCandidate, ...] = (),
        query: str = "",
        flash_key: str | None = None,
        import_job_id: UUID | None = None,
        artists: tuple[ImportCollectionArtist, ...] = (),
        artist_resolutions: tuple[BulkArtistResolution, ...] = (),
        bulk_pages: tuple[ProviderArtistTracks, ...] = (),
        bulk_preview_result: BulkPreviewResult | None = None,
        bulk_start_result: BulkStartResult | None = None,
    ) -> Response:
        locale = resolve_locale(
            request.query_params.get("lang"), request.headers.get("accept-language")
        )
        rows = tuple(
            {
                "artist": candidate.artist,
                "title": candidate.title,
                "album": candidate.album or "—",
                "duration": _duration(candidate.duration_seconds),
                "license_url": candidate.license_url,
                "share_url": candidate.share_url,
                "available": candidate.acquisition_allowed,
                "selection_token": _encode_selection(
                    authenticated.actor.user_id,
                    candidate.provider_track_id,
                    token_secret,
                ),
                "operation_id": str(uuid4()),
            }
            for candidate in candidates
        )
        content = renderer.render(
            "discovery.html",
            locale=locale,
            context={
                "page_title": "Discovery",
                "authenticated": True,
                "navigation": navigation(
                    "discovery",
                    discovery_enabled=True,
                    discovery_automation_enabled=discovery_automation_enabled,
                ),
                "flash": None,
                "development_mode": not cookies.secure,
                "language_url": f"/admin/discovery?lang={'ru' if locale == 'en' else 'en'}",
                "csrf_token": encode_request_integrity_token(authenticated.csrf),
                "search_operation_id": str(uuid4()),
                "query": query,
                "rows": rows,
                "flash_key": flash_key,
                "collection_import_enabled": imports is not None,
                "import_operation_id": str(uuid4()),
                "import_job_id": str(import_job_id) if import_job_id is not None else None,
                "artists": artists,
                "artist_resolutions": artist_resolutions,
                "bulk_pages": bulk_pages,
                "bulk_track_count": sum(len(page.tracks) for page in bulk_pages),
                "bulk_preview_result": bulk_preview_result,
                "bulk_start_result": bulk_start_result,
                "bulk_start_operation_id": str(uuid4()),
                "bulk_start_token": (
                    _encode_bulk_start(
                        authenticated.actor.user_id,
                        bulk_preview_result.bulk_operation_id,
                        token_secret,
                    )
                    if bulk_preview_result is not None
                    else None
                ),
            },
        )
        value = apply_admin_security_headers(HTMLResponse(content))
        if authenticated.rotated_bearer is not None:
            cookies.set_session(value, authenticated.rotated_bearer.decode(), max_age=1800)
        return value

    def authenticate_safe(request: Request) -> AuthenticatedWebSession | Response:
        try:
            return web.authenticate_safe_get(request.cookies.get(cookies.session_name, "").encode())
        except WebAdminError:
            value = apply_admin_security_headers(RedirectResponse("/admin/login", status_code=303))
            cookies.clear_session(value)
            return value

    @router.get("")
    def page(request: Request) -> Response:
        authenticated = authenticate_safe(request)
        if isinstance(authenticated, Response):
            return authenticated
        return render_page(request, authenticated)

    @router.post("/search")
    async def search(request: Request) -> Response:
        try:
            if bulk is None:
                raise DiscoveryError("discovery_search_unavailable")
            require_exact_origin(request.scope, origin)
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset({"csrf_token", "operation_id", "query"}),
            )
            operation_id = UUID(form["operation_id"])
            query = " ".join(form["query"].split())
            if not 1 <= len(query) <= 200:
                raise ValueError("query is invalid")
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(), mutation=True
            )
            web.validate_csrf(
                authenticated.actor,
                decode_request_integrity_token(form["csrf_token"]),
                operation_id,
            )
            bulk.require_provider_available(authenticated.actor)
            candidates = discovery.search(authenticated.actor.user_id, query, limit=20)
            return render_page(request, authenticated, candidates=candidates, query=query)
        except ValueError, WebAdminError, DiscoveryError, BulkDiscoveryError:
            return _error(renderer, request, "discovery_search_unavailable", 403, cookies)

    @router.post("/import")
    async def import_collection(request: Request) -> Response:
        uploaded: UploadFile | None = None
        try:
            if imports is None:
                raise DiscoveryError("discovery_import_unavailable")
            require_exact_origin(request.scope, origin)
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(), mutation=True
            )
            form = await request.form(max_files=1, max_fields=2, max_part_size=MAX_IMPORT_BYTES)
            if set(form) != {"csrf_token", "operation_id", "collection"}:
                raise ValueError("import form fields are invalid")
            csrf_token = form["csrf_token"]
            raw_operation_id = form["operation_id"]
            raw_uploaded = form["collection"]
            if (
                not isinstance(csrf_token, str)
                or not isinstance(raw_operation_id, str)
                or not isinstance(raw_uploaded, UploadFile)
                or raw_uploaded.filename is None
                or not raw_uploaded.filename.casefold().endswith(".txt")
                or raw_uploaded.content_type
                not in {"text/plain", "application/octet-stream", "text/csv"}
            ):
                raise ValueError("import upload is invalid")
            uploaded = raw_uploaded
            operation_id = UUID(raw_operation_id)
            web.validate_csrf(
                authenticated.actor,
                decode_request_integrity_token(csrf_token),
                operation_id,
            )
            payload = await uploaded.read(MAX_IMPORT_BYTES + 1)
            if not 1 <= len(payload) <= MAX_IMPORT_BYTES:
                raise ValueError("import upload size is invalid")
            started = imports.start_for_web(
                authenticated.actor, payload=payload, operation_id=operation_id
            )
            artists = imports.collection_artists(
                authenticated.actor, started.import_job_id, limit=100
            )
            return render_page(
                request,
                authenticated,
                flash_key=(
                    "collection_import_replayed" if started.replayed else "collection_imported"
                ),
                import_job_id=started.import_job_id,
                artists=artists,
            )
        except ValueError, WebAdminError, DiscoveryError, ImportEnvelopeError, ImportRuntimeError:
            return _error(renderer, request, "discovery_import_unavailable", 403, cookies)
        finally:
            if uploaded is not None:
                await uploaded.close()

    @router.get("/collections/{import_job_id}/expand")
    def expand_collection(import_job_id: UUID, request: Request) -> Response:
        try:
            if imports is None:
                raise DiscoveryError("discovery_import_unavailable")
            authenticated = web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode()
            )
            artists = imports.collection_artists(authenticated.actor, import_job_id, limit=100)
            return render_page(
                request,
                authenticated,
                import_job_id=import_job_id,
                artists=artists,
            )
        except ValueError, WebAdminError, DiscoveryError:
            return _error(renderer, request, "discovery_collection_not_found", 404, cookies)

    @router.post("/bulk-preview")
    async def bulk_preview(request: Request) -> Response:
        try:
            if imports is None:
                raise DiscoveryError("discovery_import_unavailable")
            require_exact_origin(request.scope, origin)
            body = await request.body()
            if (
                not 1 <= len(body) <= 8_192
                or request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
                != "application/x-www-form-urlencoded"
            ):
                raise ValueError("bulk preview form is invalid")
            parsed = parse_qs(
                body.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=24,
            )
            if set(parsed) != {"csrf_token", "operation_id", "import_job_id", "artist"}:
                raise ValueError("bulk preview form fields are invalid")
            if any(
                len(parsed[field]) != 1 for field in ("csrf_token", "operation_id", "import_job_id")
            ):
                raise ValueError("bulk preview singleton field is duplicated")
            selected_names = tuple(parsed["artist"])
            if not 1 <= len(selected_names) <= 20 or len(set(selected_names)) != len(
                selected_names
            ):
                raise ValueError("bulk preview artist selection is invalid")
            operation_id = UUID(parsed["operation_id"][0])
            import_job_id = UUID(parsed["import_job_id"][0])
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(), mutation=True
            )
            web.validate_csrf(
                authenticated.actor,
                decode_request_integrity_token(parsed["csrf_token"][0]),
                operation_id,
            )
            available = imports.collection_artists(authenticated.actor, import_job_id, limit=100)
            by_name = {artist.name: artist for artist in available}
            if any(name not in by_name for name in selected_names):
                raise DiscoveryError("discovery_artist_selection_invalid")
            selected = tuple(
                (by_name[name].name, by_name[name].track_count) for name in selected_names
            )
            if bulk is None:
                raise DiscoveryError("discovery_bulk_unavailable")
            bulk.require_eligible_artists(authenticated.actor, tuple(name for name, _ in selected))
            resolutions = discovery.resolve_artists(authenticated.actor.user_id, selected)
            exact = tuple(
                resolution.provider_artist
                for resolution in resolutions
                if resolution.provider_artist is not None
            )
            pages = (
                discovery.preview_artist_tracks(authenticated.actor.user_id, exact) if exact else ()
            )
            preview_result = (
                bulk.save_preview(
                    authenticated.actor,
                    import_job_id=import_job_id,
                    operation_id=operation_id,
                    resolutions=resolutions,
                    pages=pages,
                )
                if bulk is not None and pages
                else None
            )
            return render_page(
                request,
                authenticated,
                import_job_id=import_job_id,
                artists=available,
                artist_resolutions=resolutions,
                bulk_pages=pages,
                bulk_preview_result=preview_result,
            )
        except UnicodeError, ValueError, WebAdminError, DiscoveryError, BulkDiscoveryError:
            return _error(renderer, request, "discovery_bulk_preview_unavailable", 403, cookies)

    @router.post("/bulk-start")
    async def bulk_start(request: Request) -> Response:
        try:
            if bulk is None:
                raise DiscoveryError("discovery_bulk_unavailable")
            require_exact_origin(request.scope, origin)
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset({"csrf_token", "operation_id", "bulk_start_token"}),
            )
            operation_id = UUID(form["operation_id"])
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(), mutation=True
            )
            web.validate_csrf(
                authenticated.actor,
                decode_request_integrity_token(form["csrf_token"]),
                operation_id,
            )
            bulk_operation_id = _decode_bulk_start(
                form["bulk_start_token"], authenticated.actor.user_id, token_secret
            )
            result = bulk.start(
                authenticated.actor,
                bulk_operation_id=bulk_operation_id,
                operation_id=operation_id,
            )
            return render_page(
                request,
                authenticated,
                flash_key="bulk_start_replayed" if result.replayed else "bulk_started",
                bulk_start_result=result,
            )
        except ValueError, WebAdminError, DiscoveryError, BulkDiscoveryError:
            return _error(renderer, request, "discovery_bulk_start_unavailable", 403, cookies)

    @router.post("/acquire")
    async def acquire(request: Request) -> Response:
        try:
            if bulk is None:
                raise DiscoveryError("discovery_acquisition_unavailable")
            require_exact_origin(request.scope, origin)
            form = parse_urlencoded_form(
                request.headers.get("content-type"),
                await request.body(),
                allowed_fields=frozenset({"csrf_token", "operation_id", "selection_token"}),
            )
            operation_id = UUID(form["operation_id"])
            authenticated = web.authenticate(
                request.cookies.get(cookies.session_name, "").encode(), mutation=True
            )
            web.validate_csrf(
                authenticated.actor,
                decode_request_integrity_token(form["csrf_token"]),
                operation_id,
            )
            track_id = _decode_selection(
                form["selection_token"], authenticated.actor.user_id, token_secret
            )
            bulk.require_provider_available(authenticated.actor)
            evidence = discovery.lookup_for_acquisition(track_id)
            result = bulk.start_search_acquisition(
                authenticated.actor,
                operation_id=operation_id,
                evidence=evidence,
            )
            return render_page(
                request,
                authenticated,
                flash_key=("discovery_queue_replayed" if result.replayed else "discovery_queued"),
                bulk_start_result=result,
            )
        except ValueError, WebAdminError, DiscoveryError, BulkDiscoveryError:
            return _error(renderer, request, "discovery_acquisition_unavailable", 403, cookies)

    @router.get("/operations/{bulk_operation_id}")
    def operation_status(bulk_operation_id: UUID, request: Request) -> Response:
        try:
            if bulk is None:
                raise DiscoveryError("discovery_bulk_unavailable")
            authenticated = web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode()
            )
            result = bulk.status(
                authenticated.actor,
                bulk_operation_id=bulk_operation_id,
            )
            return render_page(request, authenticated, bulk_start_result=result)
        except ValueError, WebAdminError, DiscoveryError, BulkDiscoveryError:
            return _error(renderer, request, "discovery_operation_not_found", 404, cookies)

    @router.head("", include_in_schema=False)
    def page_head(request: Request) -> Response:
        try:
            web.authenticate_safe_get(
                request.cookies.get(cookies.session_name, "").encode(), head=True
            )
        except WebAdminError:
            return apply_admin_security_headers(RedirectResponse("/admin/login", status_code=303))
        return apply_admin_security_headers(Response(status_code=200))

    return router


def _error(
    renderer: Renderer,
    request: Request,
    code: str,
    status: int,
    cookies: WebCookieProfile,
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
            "error_code": code,
            "title": "Unavailable",
            "message": "This action is unavailable.",
            "retry_url": "/admin/discovery",
        },
    )
    return apply_admin_security_headers(HTMLResponse(content, status_code=status))


def _encode_selection(owner_id: UUID, track_id: str, secret: bytes) -> str:
    payload = json.dumps(
        {"exp": int(time.time()) + 900, "owner": str(owner_id), "track": track_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.digest(secret, payload, "sha256")
    return _base64url(payload + signature)


def _decode_selection(token: str, owner_id: UUID, secret: bytes) -> str:
    if not 1 <= len(token) <= 512:
        raise ValueError("selection token is invalid")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload, signature = raw[:-32], raw[-32:]
        if len(signature) != 32 or not hmac.compare_digest(
            signature, hmac.digest(secret, payload, "sha256")
        ):
            raise ValueError("selection token is invalid")
        document: object = json.loads(payload)
        if not isinstance(document, dict):
            raise ValueError("selection token is invalid")
        track_id = document.get("track")
        if (
            document.get("owner") != str(owner_id)
            or not isinstance(document.get("exp"), int)
            or document["exp"] < int(time.time())
            or not isinstance(track_id, str)
        ):
            raise ValueError("selection token is invalid")
        return track_id
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("selection token is invalid") from error


def _encode_bulk_start(owner_id: UUID, bulk_operation_id: UUID, secret: bytes) -> str:
    payload = json.dumps(
        {
            "bulk": str(bulk_operation_id),
            "exp": int(time.time()) + 900,
            "owner": str(owner_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return _base64url(payload + hmac.digest(secret, payload, "sha256"))


def _decode_bulk_start(token: str, owner_id: UUID, secret: bytes) -> UUID:
    if not 1 <= len(token) <= 512:
        raise ValueError("bulk start token is invalid")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload, signature = raw[:-32], raw[-32:]
        if len(signature) != 32 or not hmac.compare_digest(
            signature, hmac.digest(secret, payload, "sha256")
        ):
            raise ValueError("bulk start token is invalid")
        document: object = json.loads(payload)
        if not isinstance(document, dict):
            raise ValueError("bulk start token is invalid")
        if (
            document.get("owner") != str(owner_id)
            or not isinstance(document.get("exp"), int)
            or document["exp"] < int(time.time())
            or not isinstance(document.get("bulk"), str)
        ):
            raise ValueError("bulk start token is invalid")
        return UUID(document["bulk"])
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("bulk start token is invalid") from error


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _duration(seconds: int) -> str:
    minutes, remaining = divmod(seconds, 60)
    return f"{minutes:02d}:{remaining:02d}"


__all__ = (
    "BulkDiscoveryHttp",
    "CollectionImportHttp",
    "ManualDiscoveryHttp",
    "create_discovery_admin_router",
)
