"""Verified Sona artifact and loopback worker protocol evidence."""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
import rfc8785
from autplay.application.sona_codec import (
    SONA_MAX_TRANSPORT_BYTES,
    sona_output_from_envelope,
    sona_request_envelope,
)
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import (
    SonaCandidate,
    SonaInferenceOutput,
    SonaInferenceRequest,
    SonaRankedCandidate,
    SonaSemanticId,
)
from starlette.types import ASGIApp, Message, Scope

from autplay_gpu.embedding import ModelArtifactError
from autplay_gpu.sona_artifacts import (
    SonaArtifactStore,
    VerifiedSonaArtifact,
    _bounded_read,
)
from autplay_gpu.sona_http import create_sona_shadow_app

ARTIFACT = "a" * 64
MODEL = "b" * 64
TOKENIZER = "c" * 64
OWNER = UUID("00000000-0000-7000-8000-000000000001")
TEMPORAL = UUID("00000000-0000-7000-8000-000000000002")
BASELINE = UUID("00000000-0000-7000-8000-000000000003")
TRACK = UUID("00000000-0000-7000-8000-000000000004")


def _install_artifact(root: Path, payload: bytes) -> tuple[Path, str, str]:
    artifact_sha256 = sha256(payload).hexdigest()
    path = root / "objects" / artifact_sha256[:2] / artifact_sha256
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    manifest: dict[str, JsonValue] = {
        "schema_version": 1,
        "architecture": "SONA_LITE_SHARED_GRU_V1",
        "artifact_sha256": artifact_sha256,
        "training_provenance": {
            "tokenizer_sha256": TOKENIZER,
            "quality_eligible": False,
        },
    }
    model_manifest_sha256 = sha256(rfc8785.dumps(manifest)).hexdigest()
    manifest_envelope: dict[str, JsonValue] = {
        "manifest": manifest,
        "manifest_sha256": model_manifest_sha256,
    }
    Path(f"{path}.manifest.json").write_bytes(rfc8785.dumps(manifest_envelope))
    commit: dict[str, JsonValue] = {
        "schema_version": 1,
        "state": "COMMITTED",
        "artifact_sha256": artifact_sha256,
        "model_manifest_sha256": model_manifest_sha256,
    }
    commit_envelope: dict[str, JsonValue] = {
        "commit": commit,
        "commit_sha256": sha256(rfc8785.dumps(commit)).hexdigest(),
    }
    Path(f"{path}.commit.json").write_bytes(rfc8785.dumps(commit_envelope))
    return path, artifact_sha256, model_manifest_sha256


def test_artifact_store_requires_exact_graph_manifest_tokenizer_and_commit(tmp_path: Path) -> None:
    path, artifact_sha256, model_manifest_sha256 = _install_artifact(tmp_path, b"fixture-onnx")
    store = SonaArtifactStore(tmp_path.resolve())

    verified = store.resolve(
        artifact_sha256=artifact_sha256,
        model_manifest_sha256=model_manifest_sha256,
        tokenizer_sha256=TOKENIZER,
    )

    assert verified.path == path.resolve()
    assert verified.payload == b"fixture-onnx"
    assert not verified.quality_eligible
    with pytest.raises(ModelArtifactError, match="tokenizer provenance"):
        store.resolve(
            artifact_sha256=artifact_sha256,
            model_manifest_sha256=model_manifest_sha256,
            tokenizer_sha256="d" * 64,
        )

    Path(f"{path}.commit.json").unlink()
    with pytest.raises(ModelArtifactError, match="sidecar"):
        store.resolve(
            artifact_sha256=artifact_sha256,
            model_manifest_sha256=model_manifest_sha256,
            tokenizer_sha256=TOKENIZER,
        )


def test_artifact_bounded_read_rejects_file_growth_after_stat() -> None:
    class ChangingPath:
        def stat(self) -> SimpleNamespace:
            return SimpleNamespace(st_size=2)

        def open(self, mode: str) -> BytesIO:
            assert mode == "rb"
            return BytesIO(b"grew")

    with pytest.raises(ModelArtifactError, match="changed while reading"):
        _bounded_read(cast(Path, ChangingPath()), 16)


def _request() -> SonaInferenceRequest:
    candidate = SonaCandidate(TRACK, SonaSemanticId(1, 1, 1))
    provisional = SonaInferenceRequest(
        OWNER,
        TEMPORAL,
        BASELINE,
        1_000,
        0,
        TOKENIZER,
        MODEL,
        17,
        (),
        (candidate,),
        "0" * 64,
    )
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "architecture": "SONA_LITE_SHARED_ENCODER_V1",
        "owner_user_id": str(OWNER),
        "temporal_snapshot_id": str(TEMPORAL),
        "baseline_snapshot_id": str(BASELINE),
        "cutoff_at_ms": 1_000,
        "interaction_watermark": 0,
        "tokenizer_sha256": TOKENIZER,
        "model_manifest_sha256": MODEL,
        "seed": 17,
        "history": [],
        "candidates": [{"recording_id": str(TRACK), "semantic_id": [1, 1, 1]}],
    }
    return SonaInferenceRequest(
        provisional.owner_user_id,
        provisional.temporal_snapshot_id,
        provisional.baseline_snapshot_id,
        provisional.cutoff_at_ms,
        provisional.interaction_watermark,
        provisional.tokenizer_sha256,
        provisional.model_manifest_sha256,
        provisional.seed,
        provisional.history,
        provisional.candidates,
        sha256(rfc8785.dumps(document)).hexdigest(),
    )


class _Runtime:
    def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
        return SonaInferenceOutput(
            request.request_sha256,
            (),
            (SonaRankedCandidate(TRACK, (0.1, 0.2, 0.3, 0.4), 0.5),),
        )


def test_worker_app_accepts_only_matching_canonical_model_request(tmp_path: Path) -> None:
    artifact = VerifiedSonaArtifact(tmp_path, ARTIFACT, MODEL, TOKENIZER, False)
    app = create_sona_shadow_app(_Runtime(), artifact)
    request = _request()
    body = rfc8785.dumps(sona_request_envelope(request))

    status, response = asyncio.run(
        _asgi_post(
            app,
            "/internal/sona/v1/infer",
            body,
            ((b"x-autplay-sona-model-manifest", MODEL.encode("ascii")),),
        )
    )

    assert status == 200
    parsed = json.loads(response)
    assert sona_output_from_envelope(cast(dict[str, object], parsed)).request_sha256 == (
        request.request_sha256
    )

    status, response = asyncio.run(
        _asgi_post(
            app,
            "/internal/sona/v1/infer",
            body,
            ((b"x-autplay-sona-model-manifest", ("d" * 64).encode("ascii")),),
        )
    )
    assert status == 409
    assert json.loads(response) == {"error": "sona_model_identity_mismatch"}

    status, response = asyncio.run(
        _asgi_post(
            app,
            "/internal/sona/v1/infer",
            b"x" * (SONA_MAX_TRANSPORT_BYTES + 1),
            ((b"x-autplay-sona-model-manifest", MODEL.encode("ascii")),),
            include_content_length=False,
        )
    )
    assert status == 413
    assert json.loads(response) == {"error": "sona_request_too_large"}

    oversized_seed_body = body.replace(b'"seed":17', b'"seed":9223372036854775808')
    assert oversized_seed_body != body
    status, response = asyncio.run(
        _asgi_post(
            app,
            "/internal/sona/v1/infer",
            oversized_seed_body,
            ((b"x-autplay-sona-model-manifest", MODEL.encode("ascii")),),
        )
    )
    assert status == 400
    assert json.loads(response) == {"error": "sona_request_invalid"}


async def _asgi_post(
    app: ASGIApp,
    path: str,
    body: bytes,
    headers: tuple[tuple[bytes, bytes], ...],
    *,
    include_content_length: bool = True,
) -> tuple[int, bytes]:
    sent: list[Message] = []
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    request_headers = headers
    if include_content_length:
        request_headers = (*headers, (b"content-length", str(len(body)).encode("ascii")))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": request_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8787),
    }
    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    payload = b"".join(
        cast(bytes, message.get("body", b""))
        for message in sent
        if message["type"] == "http.response.body"
    )
    return cast(int, start["status"]), payload
