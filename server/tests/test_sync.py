"""Fast deterministic evidence for P09 protocol primitives."""

from __future__ import annotations

from uuid import UUID

import pytest
from autplay.application.sync import (
    CATALOG_ARTIST_ID_V1,
    OpaqueCursor,
    SyncError,
    _catalog_enabled,
    _ensure_ascending,
    _safe,
)
from autplay.entrypoints.sync_http import _call
from autplay.runtime.http import ApiError, install_error_handlers
from fastapi import FastAPI
from starlette.testclient import TestClient


def test_cursor_is_bound_to_owner_device_and_epoch() -> None:
    codec = OpaqueCursor(b"x" * 32)
    user = UUID("00000000-0000-0000-0000-000000000001")
    device = UUID("00000000-0000-0000-0000-000000000002")
    epoch = UUID("00000000-0000-0000-0000-000000000003")
    value = codec.encode(user_id=user, device_id=device, epoch=epoch, sequence=42)

    assert codec.decode(value, user_id=user, device_id=device, epoch=epoch) == 42
    with pytest.raises(SyncError, match="CURSOR_INVALID"):
        codec.decode(value, user_id=user, device_id=device, epoch=UUID(int=4))
    bootstrap = codec.encode_bootstrap(
        snapshot_id=UUID(int=9), user_id=user, device_id=device, epoch=epoch, ordinal=42
    )
    assert (
        codec.decode_bootstrap(
            bootstrap, snapshot_id=UUID(int=9), user_id=user, device_id=device, epoch=epoch
        )
        == 42
    )
    with pytest.raises(SyncError, match="BOOTSTRAP_SNAPSHOT_INVALID"):
        codec.decode_bootstrap(
            bootstrap, snapshot_id=UUID(int=10), user_id=user, device_id=device, epoch=epoch
        )


def test_payload_guard_rejects_secrets_and_excessive_nesting() -> None:
    assert _safe({"ordinary_key": ["value", 1, None]})
    assert not _safe({"access_token": "never"})
    nested: object = "leaf"
    for _ in range(33):
        nested = {"nested": nested}
    assert not _safe(nested)


def test_catalog_artist_capability_is_explicit_and_ignores_unknown_strings() -> None:
    assert not _catalog_enabled({})
    assert not _catalog_enabled({"capabilities": ["FUTURE_CATALOG_V9"]})
    assert _catalog_enabled({"capabilities": ["FUTURE_CATALOG_V9", CATALOG_ARTIST_ID_V1]})
    assert _catalog_enabled({"catalog_projection_version": 1})


def test_batch_order_is_contiguous_without_silent_sorting() -> None:
    _ensure_ascending([{"device_sequence": 3}, {"device_sequence": 4}])
    with pytest.raises(SyncError, match="BATCH_SEQUENCE_NOT_ASCENDING"):
        _ensure_ascending([{"device_sequence": 4}, {"device_sequence": 3}])


@pytest.mark.parametrize(
    ("raised", "code", "status"),
    [
        ("CURSOR_INVALID", "CURSOR_INVALID", 410),
        ("JOURNAL_RESET_REQUIRED", "DEVICE_RESET_REQUIRED", 409),
    ],
)
def test_reset_errors_use_frozen_bootstrap_envelope(raised: str, code: str, status: int) -> None:
    with pytest.raises(ApiError) as captured:
        _call(lambda: (_ for _ in ()).throw(SyncError(raised)))
    error = captured.value
    assert error.status_code == status
    assert error.code == code
    assert error.retryable is False
    assert error.details == {"bootstrap_required": True}


@pytest.mark.parametrize(
    ("raised", "code", "status"),
    [
        ("CURSOR_INVALID", "CURSOR_INVALID", 410),
        ("JOURNAL_RESET_REQUIRED", "DEVICE_RESET_REQUIRED", 409),
    ],
)
def test_reset_http_envelope_is_exact(raised: str, code: str, status: int) -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/sync-test")
    def sync_test() -> dict[str, object]:
        return _call(lambda: (_ for _ in ()).throw(SyncError(raised)))

    with TestClient(app) as client:
        response = client.get("/sync-test", headers={"X-Request-ID": str(UUID(int=7))})
    assert response.status_code == status
    error = response.json()["error"]
    assert error == {
        "code": code,
        "message": "The sync request could not be applied.",
        "retryable": False,
        "bootstrap_required": True,
        "request_id": error["request_id"],
    }
    UUID(error["request_id"])
