"""HTTP-independent single-range policy for authorized Vault streams."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI
from starlette.responses import Response

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.auth import AuthService
from autplay.entrypoints.auth_http import bearer_authentication
from autplay.ports.vault import VaultStorage
from autplay.runtime.http import RequestRuntimeMiddleware, install_error_handlers
from autplay.runtime.logging import configure_json_logging
from autplay.runtime.metrics import RuntimeMetrics
from autplay.runtime.settings import SettingsLoadError, StreamSettings, load_stream_settings

if TYPE_CHECKING:
    from autplay.entrypoints.stream_http import StreamLookup


@dataclass(frozen=True, slots=True)
class StreamRange:
    """A selected inclusive byte interval, or the full representation."""

    start: int
    end: int
    partial: bool

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def select_range(
    *, range_header: str | None, if_range: str | None, etag: str, size: int
) -> StreamRange:
    """Parse one RFC 9110 byte range after authorization has completed.

    Date and weak ``If-Range`` validators deliberately fall back to a full
    response because this service only issues strong SHA-256 entity tags.
    """

    if size < 1:
        raise ValueError("stream size must be positive")
    if range_header is None or not _if_range_matches(if_range, etag):
        return StreamRange(start=0, end=size - 1, partial=False)
    if not range_header.startswith("bytes="):
        raise ValueError("invalid range unit")
    specification = range_header[6:]
    if not specification or "," in specification:
        raise ValueError("multiple or empty ranges are unsupported")
    start_text, separator, end_text = specification.partition("-")
    if separator != "-" or (not start_text and not end_text):
        raise ValueError("invalid range syntax")
    try:
        if start_text:
            start = int(start_text)
            if start < 0 or start >= size:
                raise ValueError("range start is unsatisfiable")
            end = size - 1 if not end_text else min(int(end_text), size - 1)
            if end < start:
                raise ValueError("range end precedes start")
        else:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise ValueError("invalid suffix length")
            start = max(0, size - suffix_length)
            end = size - 1
    except ValueError:
        raise ValueError("invalid or unsatisfiable range") from None
    return StreamRange(start=start, end=end, partial=True)


def _if_range_matches(value: str | None, etag: str) -> bool:
    return value is None or (value == etag and not value.startswith("W/"))


def create_stream_app(
    settings: StreamSettings,
    *,
    lookup: StreamLookup,
    auth_service: AuthService,
    metrics: RuntimeMetrics | None = None,
    storage: VaultStorage | None = None,
) -> FastAPI:
    """Create an isolated stream process with no worker/tool imports."""

    runtime_metrics = metrics or RuntimeMetrics()
    resolved_storage = storage or FilesystemVaultStorage(settings.vault_root)
    from autplay.entrypoints.stream_http import create_stream_router

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        del application
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.metrics = runtime_metrics
    install_error_handlers(app)
    app.add_middleware(RequestRuntimeMiddleware, metrics=runtime_metrics)
    app.include_router(
        create_stream_router(
            lookup,
            resolved_storage,
            authenticated=bearer_authentication(auth_service),
        ),
        prefix="/api/v1",
    )

    @app.get("/health/live", include_in_schema=False)
    async def health_live() -> dict[str, str]:
        return {"status": "live", "component": "stream"}

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        content, content_type = runtime_metrics.render()
        return Response(content=content, headers={"Content-Type": content_type})

    return app


def main(argv: Sequence[str] | None = None) -> int:
    """Validate stream configuration; composition is supplied by the P06 runtime."""

    parser = argparse.ArgumentParser(prog="autplay-stream")
    parser.add_argument("--check-config", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        settings = load_stream_settings()
    except SettingsLoadError as error:
        sys.stderr.write(json.dumps({"event": error.code, "service": "autplay-stream"}) + "\n")
        return 2
    if arguments.check_config:
        sys.stdout.write('{"status":"ok","service":"autplay-stream"}\n')
        return 0
    configure_json_logging(service="autplay-stream", level=settings.log_level)
    from autplay.entrypoints.composition import build_stream_auth_service, build_stream_lookup

    engine = create_runtime_engine(settings)
    try:
        app = create_stream_app(
            settings,
            lookup=build_stream_lookup(engine),
            auth_service=build_stream_auth_service(settings, engine),
        )
        uvicorn.run(
            app,
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
    finally:
        engine.dispose()
    return 0


__all__ = ("StreamRange", "create_stream_app", "main", "select_range")
