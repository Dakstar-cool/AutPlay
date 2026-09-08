"""Canonical Sona transport and immutable tokenizer adapter evidence."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
import rfc8785
from autplay.adapters.sona_http import LoopbackSonaInferenceGateway
from autplay.adapters.sona_tokenizer import VerifiedSonaSemanticIdReader, _bounded_read
from autplay.application.sona_codec import (
    sona_output_envelope,
    sona_output_from_envelope,
    sona_request_envelope,
    sona_request_from_envelope,
)
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import (
    SonaAction,
    SonaCandidate,
    SonaHistoryEvent,
    SonaInferenceOutput,
    SonaInferenceRequest,
    SonaOrigin,
    SonaRankedCandidate,
    SonaSemanticId,
)

OWNER = UUID("00000000-0000-7000-8000-000000000001")
TEMPORAL = UUID("00000000-0000-7000-8000-000000000002")
BASELINE = UUID("00000000-0000-7000-8000-000000000003")
EVENT = UUID("00000000-0000-7000-8000-000000000004")
TRACK = UUID("00000000-0000-7000-8000-000000000005")
TOKENIZER = "b" * 64
MODEL = "c" * 64


def _request() -> SonaInferenceRequest:
    provisional = SonaInferenceRequest(
        OWNER,
        TEMPORAL,
        BASELINE,
        1_000,
        1,
        TOKENIZER,
        MODEL,
        17,
        (
            SonaHistoryEvent(
                EVENT,
                TRACK,
                SonaSemanticId(1, 2, 3),
                SonaAction.ORGANIC_LISTEN,
                SonaOrigin.ORGANIC,
                1,
                900,
                1,
            ),
        ),
        (SonaCandidate(TRACK, SonaSemanticId(1, 2, 3)),),
        "0" * 64,
    )
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "architecture": "SONA_LITE_SHARED_ENCODER_V1",
        "owner_user_id": str(OWNER),
        "temporal_snapshot_id": str(TEMPORAL),
        "baseline_snapshot_id": str(BASELINE),
        "cutoff_at_ms": 1_000,
        "interaction_watermark": 1,
        "tokenizer_sha256": TOKENIZER,
        "model_manifest_sha256": MODEL,
        "seed": 17,
        "history": [
            {
                "evidence_id": str(EVENT),
                "recording_id": str(TRACK),
                "semantic_id": [1, 2, 3],
                "action": 1,
                "origin": 2,
                "age_bucket": 1,
                "effective_at_ms": 900,
                "server_sequence": 1,
            }
        ],
        "candidates": [{"recording_id": str(TRACK), "semantic_id": [1, 2, 3]}],
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


def _output(request: SonaInferenceRequest) -> SonaInferenceOutput:
    return SonaInferenceOutput(
        request.request_sha256,
        (),
        (SonaRankedCandidate(TRACK, (0.1, 0.2, 0.3, 0.4), 0.5),),
    )


def test_request_and_output_codec_round_trip_and_reject_tamper() -> None:
    request = _request()
    output = _output(request)

    assert sona_request_from_envelope(sona_request_envelope(request)) == request
    assert sona_output_from_envelope(sona_output_envelope(output)) == output

    tampered = sona_request_envelope(request)
    body = tampered["request"]
    assert isinstance(body, dict)
    body["seed"] = 18
    with pytest.raises(ValueError, match="hash mismatch"):
        sona_request_from_envelope(tampered)

    oversized_seed = sona_request_envelope(request)
    oversized_body = oversized_seed["request"]
    assert isinstance(oversized_body, dict)
    oversized_body["seed"] = 1 << 63
    with pytest.raises(ValueError, match="canonical JSON domain"):
        sona_request_from_envelope(oversized_seed)


class _Response:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self, amount: int) -> bytes:
        return self._payload[:amount]


class _Connection:
    def __init__(self, response: _Response, captured: dict[str, object]) -> None:
        self._response = response
        self._captured = captured

    def request(
        self, method: str, path: str, body: object | None, headers: Mapping[str, str]
    ) -> None:
        if not isinstance(body, bytes):
            raise AssertionError("expected bytes body")
        self._captured.update(method=method, path=path, body=body, headers=dict(headers))

    def getresponse(self) -> _Response:
        return self._response

    def close(self) -> None:
        self._captured["closed"] = True


def test_loopback_gateway_round_trips_and_rejects_non_loopback_endpoint() -> None:
    request = _request()
    response = rfc8785.dumps(sona_output_envelope(_output(request)))
    captured: dict[str, object] = {}

    connection = _Connection(_Response(response), captured)

    def connection_factory(host: str, port: int, timeout: float) -> _Connection:
        captured.update(host=host, port=port, timeout=timeout)
        return connection

    gateway = LoopbackSonaInferenceGateway(
        "http://127.0.0.1:8787/internal/sona/v1/infer",
        connection_factory=connection_factory,
    )

    assert gateway.infer(request) == _output(request)
    assert captured["timeout"] == 5.0
    assert (captured["host"], captured["port"]) == ("127.0.0.1", 8787)
    assert (captured["method"], captured["path"]) == (
        "POST",
        "/internal/sona/v1/infer",
    )
    assert captured["closed"] is True
    assert sona_request_from_envelope(_json_object(captured["body"])) == request
    with pytest.raises(ValueError, match="loopback"):
        LoopbackSonaInferenceGateway("http://ii-rtx3060:8787/internal/sona/v1/infer")


def test_loopback_gateway_ignores_proxy_environment_and_rejects_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    captured: dict[str, object] = {}
    connection = _Connection(_Response(b"", status=302), captured)
    monkeypatch.setenv("HTTP_PROXY", "http://example.invalid:3128")
    monkeypatch.setattr(
        "autplay.adapters.sona_http.http.client.HTTPConnection",
        lambda host, port, timeout: (
            captured.update(host=host, port=port, timeout=timeout) or connection
        ),
    )

    gateway = LoopbackSonaInferenceGateway("http://127.0.0.1:8787/internal/sona/v1/infer")
    with pytest.raises(RuntimeError, match="rejected inference"):
        gateway.infer(request)

    assert (captured["host"], captured["port"]) == ("127.0.0.1", 8787)
    assert captured["closed"] is True


def test_tokenizer_reader_verifies_manifest_mapping_and_exact_identity(tmp_path: Path) -> None:
    mapping: list[JsonValue] = [{"recording_id": str(TRACK), "semantic_id": [1, 1, 1]}]
    mapping_bytes = rfc8785.dumps(mapping)
    manifest: dict[str, JsonValue] = {
        "active_codes_per_level": 1,
        "algorithm": "DETERMINISTIC_RESIDUAL_MINIBATCH_KMEANS_V1",
        "centroids_sha256": "e" * 64,
        "configured_codebook_size": 32,
        "embedding_dimensions": 8,
        "iterations": 10,
        "max_active_codes": 256,
        "mini_batch_size": 1_024,
        "schema_version": 1,
        "fit_manifest_sha256": TOKENIZER,
        "mapping_sha256": sha256(mapping_bytes).hexdigest(),
        "recording_count": 1,
        "seed": 17,
        "source_embeddings_retained": False,
        "source_embeddings_sha256": "f" * 64,
    }
    envelope: dict[str, JsonValue] = {
        "manifest": manifest,
        "manifest_sha256": sha256(rfc8785.dumps(manifest)).hexdigest(),
    }
    (tmp_path / "mapping.json").write_bytes(mapping_bytes)
    (tmp_path / "manifest.json").write_bytes(rfc8785.dumps(envelope))

    tokenizer_manifest_sha256 = cast(str, envelope["manifest_sha256"])
    reader = VerifiedSonaSemanticIdReader(
        tmp_path.resolve(),
        tokenizer_sha256=TOKENIZER,
        tokenizer_manifest_sha256=tokenizer_manifest_sha256,
    )

    assert reader.load((TRACK,), tokenizer_sha256=TOKENIZER)[TRACK] == SonaSemanticId(1, 1, 1)
    assert reader.expand((SonaSemanticId(1, 1, 1),), tokenizer_sha256=TOKENIZER) == {
        SonaSemanticId(1, 1, 1): (TRACK,)
    }
    with pytest.raises(RuntimeError, match="unavailable"):
        reader.load((TRACK,), tokenizer_sha256="d" * 64)

    (tmp_path / "mapping.json").write_bytes(mapping_bytes + b" ")
    with pytest.raises(ValueError, match="mapping hash mismatch"):
        VerifiedSonaSemanticIdReader(
            tmp_path.resolve(),
            tokenizer_sha256=TOKENIZER,
            tokenizer_manifest_sha256=tokenizer_manifest_sha256,
        )

    replacement_mapping: list[JsonValue] = [{"recording_id": str(TRACK), "semantic_id": [7, 8, 9]}]
    replacement_bytes = rfc8785.dumps(replacement_mapping)
    replacement_manifest = dict(manifest)
    replacement_manifest["mapping_sha256"] = sha256(replacement_bytes).hexdigest()
    replacement_envelope: dict[str, JsonValue] = {
        "manifest": replacement_manifest,
        "manifest_sha256": sha256(rfc8785.dumps(replacement_manifest)).hexdigest(),
    }
    (tmp_path / "mapping.json").write_bytes(replacement_bytes)
    (tmp_path / "manifest.json").write_bytes(rfc8785.dumps(replacement_envelope))
    with pytest.raises(ValueError, match="manifest identity mismatch"):
        VerifiedSonaSemanticIdReader(
            tmp_path.resolve(),
            tokenizer_sha256=TOKENIZER,
            tokenizer_manifest_sha256=tokenizer_manifest_sha256,
        )


def test_tokenizer_bounded_read_rejects_file_growth_after_stat() -> None:
    class ChangingPath:
        def stat(self) -> SimpleNamespace:
            return SimpleNamespace(st_size=2)

        def open(self, mode: str) -> BytesIO:
            assert mode == "rb"
            return BytesIO(b"grew")

    with pytest.raises(ValueError, match="changed while reading"):
        _bounded_read(cast(Path, ChangingPath()), 16)


def _json_object(value: object) -> Mapping[str, object]:
    import json

    if not isinstance(value, bytes):
        raise AssertionError("expected request bytes")
    parsed: Any = json.loads(value)
    if not isinstance(parsed, dict):
        raise AssertionError("expected request object")
    return parsed
