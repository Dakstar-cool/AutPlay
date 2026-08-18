"""FastAPI application factory and CPU-only API process entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Final

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from autplay.adapters.postgresql.readiness import (
    PostgreSQLReadinessProbe,
    ReadinessProbe,
)
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.auth import AuthService
from autplay.entrypoints.auth_http import bearer_authentication, create_auth_router
from autplay.entrypoints.composition import (
    build_auth_service,
    build_import_service,
    build_library_service,
    build_recommendation_service,
    build_stream_lookup,
    build_sync_service,
    build_vault_http_service,
    build_wave_service,
)
from autplay.entrypoints.import_http import ImportHttpService, create_import_router
from autplay.entrypoints.library_http import LibraryQueryService, create_library_router
from autplay.entrypoints.recommendation_http import (
    RecommendationHttpService,
    create_recommendation_router,
)
from autplay.entrypoints.sync_http import create_sync_router
from autplay.entrypoints.vault_http import UploadService, create_vault_router
from autplay.entrypoints.wave_http import create_wave_router
from autplay.runtime.http import (
    RequestRuntimeMiddleware,
    error_response,
    install_error_handlers,
)
from autplay.runtime.logging import configure_json_logging
from autplay.runtime.metrics import RuntimeMetrics
from autplay.runtime.settings import ApiSettings, SettingsLoadError, load_api_settings

API_V1_PREFIX: Final = "/api/v1"
SERVICE_NAME: Final = "autplay-api"


def create_app(
    settings: ApiSettings | None = None,
    *,
    readiness_probe: ReadinessProbe | None = None,
    metrics: RuntimeMetrics | None = None,
    auth_service: AuthService | None = None,
    upload_service: UploadService | None = None,
    library_service: LibraryQueryService | None = None,
    import_service: ImportHttpService | None = None,
    recommendation_service: RecommendationHttpService | None = None,
    sync_service: object | None = None,
    wave_service: Any | None = None,
) -> FastAPI:
    """Create one API instance without connecting to PostgreSQL at import time."""

    resolved_settings = settings or load_api_settings()
    runtime_metrics = metrics or RuntimeMetrics()
    engine = create_runtime_engine(resolved_settings)
    probe = readiness_probe or PostgreSQLReadinessProbe(engine)
    authentication = auth_service or build_auth_service(resolved_settings, engine)
    uploads = upload_service or build_vault_http_service(resolved_settings, engine)
    library = library_service or build_library_service(engine)
    imports = import_service or build_import_service(engine)
    recommendations = recommendation_service or build_recommendation_service(engine)
    sync = sync_service or build_sync_service(resolved_settings, engine)
    wave = wave_service or build_wave_service(engine)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        del application
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="AutPlay API",
        version="0.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.metrics = runtime_metrics
    app.state.readiness_probe = probe
    app.state.auth_service = authentication
    install_error_handlers(app)
    app.add_middleware(RequestRuntimeMiddleware, metrics=runtime_metrics)
    api_router = APIRouter(prefix=API_V1_PREFIX)
    api_router.include_router(create_auth_router(authentication))
    api_router.include_router(
        create_vault_router(uploads, authenticated=bearer_authentication(authentication))
    )
    api_router.include_router(
        create_library_router(library, authenticated=bearer_authentication(authentication))
    )
    api_router.include_router(
        create_import_router(imports, authenticated=bearer_authentication(authentication))
    )
    api_router.include_router(
        create_recommendation_router(
            recommendations, authenticated=bearer_authentication(authentication)
        )
    )
    api_router.include_router(
        create_sync_router(sync, authenticated=bearer_authentication(authentication))  # type: ignore[arg-type]
    )
    api_router.include_router(
        create_wave_router(
            wave,
            authenticated=bearer_authentication(authentication),
            auth_service=authentication,
            source_lookup=build_stream_lookup(engine),
            metrics=runtime_metrics,
        )
    )
    app.include_router(api_router)

    @app.get("/health/live", include_in_schema=False)
    async def health_live() -> dict[str, str]:
        return {"status": "live", "component": "api"}

    @app.get("/health/ready", include_in_schema=False)
    async def health_ready(request: Request) -> Response:
        result = await run_in_threadpool(probe.check)
        runtime_metrics.set_readiness(result.component, ready=result.ready)
        if not result.ready:
            return error_response(
                request_id=str(request.state.request_id),
                code=result.code or "service_not_ready",
                message="A required service component is not ready.",
                status_code=503,
                retryable=True,
            )
        return Response(
            content=json.dumps({"status": "ready", "component": "api"}),
            media_type="application/json",
            status_code=200,
        )

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        content, content_type = runtime_metrics.render()
        return Response(content=content, headers={"Content-Type": content_type})

    return app


def main(argv: Sequence[str] | None = None) -> int:
    """Validate configuration and run one bounded Uvicorn API process."""

    parser = argparse.ArgumentParser(prog="autplay-api")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate configuration without opening a network listener",
    )
    arguments = parser.parse_args(argv)
    try:
        settings = load_api_settings()
    except SettingsLoadError as error:
        sys.stderr.write(
            json.dumps({"event": error.code, "service": SERVICE_NAME}, separators=(",", ":")) + "\n"
        )
        return 2
    if arguments.check_config:
        sys.stdout.write('{"status":"ok","service":"autplay-api"}\n')
        return 0

    configure_json_logging(service=SERVICE_NAME, level=settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        date_header=True,
        limit_concurrency=32,
        log_config=None,
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=10,
        timeout_keep_alive=5,
        workers=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("API_V1_PREFIX", "SERVICE_NAME", "create_app", "main")
