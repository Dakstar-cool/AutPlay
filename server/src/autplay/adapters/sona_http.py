"""Loopback-only HTTP adapter for the isolated Sona GPU process."""

from __future__ import annotations

import http.client
import json
from collections.abc import Callable, Mapping
from typing import Protocol, cast
from urllib.parse import urlsplit

import rfc8785

from autplay.application.sona_codec import (
    SONA_MAX_TRANSPORT_BYTES,
    sona_output_from_envelope,
    sona_request_envelope,
)
from autplay.domain.sona import SonaInferenceOutput, SonaInferenceRequest

SONA_INFERENCE_PATH = "/internal/sona/v1/infer"


class _HttpResponse(Protocol):
    status: int

    def read(self, amount: int) -> bytes: ...


class _HttpConnection(Protocol):
    def request(
        self, method: str, url: str, body: object | None, headers: Mapping[str, str]
    ) -> None: ...

    def getresponse(self) -> _HttpResponse: ...

    def close(self) -> None: ...


type HttpConnectionFactory = Callable[[str, int, float], _HttpConnection]


class LoopbackSonaInferenceGateway:
    """Send bounded canonical requests only through a local SSH/loopback endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 5.0,
        connection_factory: HttpConnectionFactory | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.path != SONA_INFERENCE_PATH
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is None
        ):
            raise ValueError("Sona endpoint must be an exact 127.0.0.1 loopback URL")
        if not 0.05 <= timeout_seconds <= 30.0:
            raise ValueError("Sona inference timeout is outside the accepted bound")
        self._endpoint = endpoint
        self._port = parsed.port
        self._timeout_seconds = timeout_seconds
        self._connection_factory = connection_factory or _direct_connection

    def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
        payload = rfc8785.dumps(sona_request_envelope(request))
        if len(payload) > SONA_MAX_TRANSPORT_BYTES:
            raise RuntimeError("Sona request exceeds the transport bound")
        connection = self._connection_factory("127.0.0.1", self._port, self._timeout_seconds)
        try:
            connection.request(
                "POST",
                SONA_INFERENCE_PATH,
                payload,
                {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-AutPlay-Sona-Model-Manifest": request.model_manifest_sha256,
                },
            )
            response = connection.getresponse()
            body = response.read(SONA_MAX_TRANSPORT_BYTES + 1)
            if response.status != 200:
                raise RuntimeError("Sona GPU worker rejected inference")
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            raise RuntimeError("Sona GPU worker is unavailable") from error
        finally:
            connection.close()
        if len(body) > SONA_MAX_TRANSPORT_BYTES:
            raise RuntimeError("Sona GPU response exceeds the transport bound")
        try:
            raw = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Sona GPU response is invalid") from error
        if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
            raise RuntimeError("Sona GPU response envelope is invalid")
        try:
            output = sona_output_from_envelope(cast(Mapping[str, object], raw))
        except ValueError as error:
            raise RuntimeError("Sona GPU response integrity failed") from error
        if output.request_sha256 != request.request_sha256:
            raise RuntimeError("Sona GPU response request binding failed")
        return output


def _direct_connection(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
    """Connect to the literal loopback IP without proxy or redirect machinery."""

    return http.client.HTTPConnection(host, port, timeout=timeout)


__all__ = ("SONA_INFERENCE_PATH", "LoopbackSonaInferenceGateway")
