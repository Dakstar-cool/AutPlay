"""Loopback-only FastAPI surface for one verified Sona CUDA runtime."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import cast

import rfc8785
from autplay.application.enrichment import AcceleratorOutOfMemory
from autplay.application.sona_codec import (
    SONA_MAX_TRANSPORT_BYTES,
    sona_output_envelope,
    sona_request_from_envelope,
)
from autplay.domain.recommendations import JsonValue
from autplay.ports.recommendations import SonaInferenceGateway
from fastapi import FastAPI, Request, Response
from starlette.requests import ClientDisconnect

from .embedding import ModelArtifactError
from .sona_artifacts import VerifiedSonaArtifact

SONA_INFERENCE_PATH = "/internal/sona/v1/infer"


def create_sona_shadow_app(
    runtime: SonaInferenceGateway, artifact: VerifiedSonaArtifact
) -> FastAPI:
    """Create a single-runtime app; callers must bind it to 127.0.0.1 only."""

    app = FastAPI(
        title="AutPlay Sona shadow worker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    inference_lock = asyncio.Lock()

    @app.get("/internal/sona/v1/ready")
    async def ready() -> dict[str, JsonValue]:
        return {
            "status": "ready",
            "runtime": "SONA_LITE_ONNX_CUDA",
            "model_manifest_sha256": artifact.model_manifest_sha256,
            "tokenizer_sha256": artifact.tokenizer_sha256,
            "quality_eligible": artifact.quality_eligible,
        }

    @app.post(SONA_INFERENCE_PATH)
    async def infer(request: Request) -> Response:
        declared_length = request.headers.get("content-length")
        try:
            if declared_length is not None and int(declared_length) > SONA_MAX_TRANSPORT_BYTES:
                return _error("sona_request_too_large", 413)
        except ValueError:
            return _error("sona_content_length_invalid", 400)
        if request.headers.get("x-autplay-sona-model-manifest") != artifact.model_manifest_sha256:
            return _error("sona_model_identity_mismatch", 409)
        try:
            body = await _read_bounded_body(request)
        except ClientDisconnect:
            return _error("sona_request_disconnected", 400)
        if body is None:
            return _error("sona_request_too_large", 413)
        try:
            raw = json.loads(body)
            if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
                raise ValueError("request envelope must be an object")
            inference_request = sona_request_from_envelope(cast(Mapping[str, object], raw))
            if (
                inference_request.model_manifest_sha256 != artifact.model_manifest_sha256
                or inference_request.tokenizer_sha256 != artifact.tokenizer_sha256
            ):
                return _error("sona_request_artifact_mismatch", 409)
            async with inference_lock:
                output = await asyncio.to_thread(runtime.infer, inference_request)
        except UnicodeDecodeError, json.JSONDecodeError, ValueError:
            return _error("sona_request_invalid", 400)
        except AcceleratorOutOfMemory:
            return _error("sona_accelerator_out_of_memory", 503)
        except ModelArtifactError, RuntimeError:
            return _error("sona_inference_unavailable", 503)
        payload = rfc8785.dumps(sona_output_envelope(output))
        if len(payload) > SONA_MAX_TRANSPORT_BYTES:
            return _error("sona_response_too_large", 503)
        return Response(payload, media_type="application/json")

    return app


async def _read_bounded_body(request: Request) -> bytes | None:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > SONA_MAX_TRANSPORT_BYTES:
            return None
        body.extend(chunk)
    return bytes(body)


def _error(code: str, status_code: int) -> Response:
    return Response(
        rfc8785.dumps({"error": code}),
        status_code=status_code,
        media_type="application/json",
    )


__all__ = ("SONA_INFERENCE_PATH", "create_sona_shadow_app")
