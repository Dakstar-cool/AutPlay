"""Byte-route authority headers are exact, bounded and purpose constrained."""

from __future__ import annotations

from typing import cast

import pytest
from starlette.requests import Request
from starlette.types import Scope

from autplay.entrypoints.resource_io_http import require_resource_io_headers
from autplay.runtime.http import ApiError

_HEADERS = [
    (b"autplay-resource-type", b"PLAY_INSTANCE"),
    (b"autplay-operation-id", b"018f47bc-2f9d-7cc2-8e39-01b4ce17cc88"),
    (b"autplay-activation-id", b"018f47bc-2f9d-7cc2-8e39-01b4ce17cc89"),
    (b"autplay-generation", b"1"),
]
_PLAYBACK = frozenset({"PLAY_INSTANCE", "DOWNLOAD_INTENT"})


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(cast(Scope, {"type": "http", "headers": headers}))


def test_exact_header_fence_and_upload_purpose() -> None:
    parsed = require_resource_io_headers(_request(_HEADERS), allowed=_PLAYBACK)
    assert parsed.resource_type == "PLAY_INSTANCE"
    assert parsed.fence.generation == 1
    with pytest.raises(ApiError) as caught:
        require_resource_io_headers(_request(_HEADERS), allowed=frozenset({"UPLOAD_INTENT"}))
    assert caught.value.code == "resource_purpose_mismatch"
    assert caught.value.status_code == 409


@pytest.mark.parametrize(
    "headers",
    [
        _HEADERS[:3],
        [*_HEADERS, _HEADERS[1]],
        [(b"autplay-resource-type", b"INTERNET_ACQUISITION"), *_HEADERS[1:]],
        [_HEADERS[0], (b"autplay-operation-id", _HEADERS[1][1].upper()), *_HEADERS[2:]],
        [_HEADERS[0], (b"autplay-operation-id", _HEADERS[1][1].replace(b"-", b"")), *_HEADERS[2:]],
        *[
            [*_HEADERS[:3], (b"autplay-generation", value)]
            for value in (b"", b"0", b"01", b"+1", b"1.0", b"1e0", b" 1", b"9007199254740992")
        ],
    ],
)
def test_malformed_or_duplicate_headers_are_rejected(headers: list[tuple[bytes, bytes]]) -> None:
    with pytest.raises(ApiError) as caught:
        require_resource_io_headers(_request(headers), allowed=_PLAYBACK)
    assert caught.value.code == "resource_request_invalid"
    assert caught.value.status_code == 400


def test_missing_headers_have_a_stable_upgrade_response() -> None:
    with pytest.raises(ApiError) as caught:
        require_resource_io_headers(_request([]), allowed=_PLAYBACK)
    assert caught.value.code == "resource_admission_required"
    assert caught.value.status_code == 409
