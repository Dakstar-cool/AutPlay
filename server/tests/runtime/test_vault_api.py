"""P06 upload HTTP contract tests with no database or real Vault dependency."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from autplay.domain.auth import AccountRole, OwnedObjectNotFoundError, Principal
from autplay.domain.vault import UploadOffsetError
from autplay.entrypoints.api import create_app
from autplay.entrypoints.vault_http import UploadView
from autplay.runtime.settings import ApiSettings
from pydantic import SecretStr
from starlette.testclient import TestClient

DATABASE_URL = "postgresql+psycopg://runtime:runtime@127.0.0.1:1/autplay"
AUTH_SECRET = "runtime-test-signing-secret-at-least-32-bytes"
OWNER = Principal(uuid4(), uuid4(), uuid4(), AccountRole.OWNER)
CHUNK_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


class Auth:
    def authenticate_access(self, token: str) -> Principal:
        if token != "good":
            from autplay.domain.auth import InvalidAccessTokenError

            raise InvalidAccessTokenError()
        return OWNER


@dataclass
class Uploads:
    view: UploadView
    created: bool = True
    offset_error: bool = False
    denied: bool = False
    create_error: Exception | None = None
    playback_variant_id: UUID = field(default_factory=uuid4)

    def create(self, principal: Principal, **_: object) -> tuple[UploadView, bool]:
        assert principal == OWNER
        if self.create_error is not None:
            raise self.create_error
        return self.view, self.created

    def status(self, principal: Principal, upload_id: UUID) -> UploadView:
        del upload_id
        if self.denied or principal != OWNER:
            raise OwnedObjectNotFoundError()
        return self.view

    def append(self, principal: Principal, upload_id: UUID, **kwargs: object) -> int:
        del upload_id, kwargs
        if self.denied or principal != OWNER:
            raise OwnedObjectNotFoundError()
        if self.offset_error:
            raise UploadOffsetError()
        return self.view.offset + 3

    def complete(self, principal: Principal, upload_id: UUID) -> UploadView:
        return self.status(principal, upload_id)

    def cancel(self, principal: Principal, upload_id: UUID) -> None:
        self.status(principal, upload_id)

    def resolve_playback_variant(self, principal: Principal, user_track_ref_id: UUID) -> UUID:
        del user_track_ref_id
        if self.denied or principal != OWNER:
            raise OwnedObjectNotFoundError()
        return self.playback_variant_id


def _client(uploads: Uploads) -> TestClient:
    settings = ApiSettings(
        database_url=SecretStr(DATABASE_URL),
        auth_signing_secret=SecretStr(AUTH_SECRET),
        public_access_source_hmac_secret=SecretStr(
            "public-access-source-hmac-secret-at-least-32-bytes"
        ),
    )
    return TestClient(create_app(settings, auth_service=Auth(), upload_service=uploads))  # type: ignore[arg-type]


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer good"}


def test_create_new_replay_and_conflict_envelopes_are_no_store() -> None:
    view = UploadView(uuid4(), 0, 3, "OPEN")
    with _client(Uploads(view)) as client:
        new = client.post(
            "/api/v1/vault/uploads",
            headers={**_headers(), "Idempotency-Key": "one"},
            json={"recording_id": str(uuid4()), "expected_size": 3},
        )
    assert new.status_code == 201
    assert new.headers["cache-control"] == "no-store"
    assert new.json()["upload_id"] == str(view.upload_id)

    replay = Uploads(view, created=False)
    with _client(replay) as client:
        response = client.post(
            "/api/v1/vault/uploads",
            headers={**_headers(), "Idempotency-Key": "one"},
            json={"recording_id": str(uuid4()), "expected_size": 3},
        )
    assert response.status_code == 200


def test_playback_variant_resolution_is_authenticated_owner_scoped_and_no_store() -> None:
    view = UploadView(uuid4(), 0, 3, "OPEN")
    track_ref_id = uuid4()
    uploads = Uploads(view)
    with _client(uploads) as client:
        resolved = client.get(
            f"/api/v1/vault/user-tracks/{track_ref_id}/playback-variant",
            headers=_headers(),
        )
        unauthenticated = client.get(f"/api/v1/vault/user-tracks/{track_ref_id}/playback-variant")
    assert resolved.status_code == 200
    assert resolved.headers["cache-control"] == "no-store"
    assert resolved.json() == {"audio_variant_id": str(uploads.playback_variant_id)}
    assert unauthenticated.status_code == 401

    with _client(Uploads(view, denied=True)) as client:
        denied = client.get(
            f"/api/v1/vault/user-tracks/{track_ref_id}/playback-variant",
            headers=_headers(),
        )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "not_found"


def test_upload_head_patch_complete_status_cancel_and_bad_chunk_contract() -> None:
    view = UploadView(uuid4(), 0, 3, "OPEN")
    with _client(Uploads(view)) as client:
        head = client.head(f"/api/v1/vault/uploads/{view.upload_id}", headers=_headers())
        payload = b"abc"
        patch = client.patch(
            f"/api/v1/vault/uploads/{view.upload_id}",
            content=payload,
            headers={
                **_headers(),
                "Content-Type": "application/offset+octet-stream",
                "Content-Length": "3",
                "Upload-Offset": "0",
                "Upload-Chunk-Index": "0",
                "X-Chunk-SHA256": CHUNK_SHA256,
            },
        )
        complete = client.post(
            f"/api/v1/vault/uploads/{view.upload_id}/complete", headers=_headers()
        )
        status = client.get(f"/api/v1/vault/uploads/{view.upload_id}", headers=_headers())
        cancel = client.delete(f"/api/v1/vault/uploads/{view.upload_id}", headers=_headers())
        invalid = client.patch(
            f"/api/v1/vault/uploads/{view.upload_id}",
            content=b"abc",
            headers={**_headers(), "Content-Type": "application/offset+octet-stream"},
        )
    assert head.status_code == 204 and head.headers["upload-offset"] == "0"
    assert patch.status_code == 204 and patch.headers["upload-offset"] == "3"
    assert complete.status_code == 202 and status.status_code == 200 and cancel.status_code == 204
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "request_validation_failed"


def test_upload_offset_owner_and_auth_failures_are_masked_and_redacted() -> None:
    view = UploadView(uuid4(), 0, 3, "OPEN")
    with _client(Uploads(view, offset_error=True)) as client:
        mismatch = client.patch(
            f"/api/v1/vault/uploads/{view.upload_id}",
            content=b"abc",
            headers={
                **_headers(),
                "Content-Type": "application/offset+octet-stream",
                "Content-Length": "3",
                "Upload-Offset": "0",
                "Upload-Chunk-Index": "0",
                "X-Chunk-SHA256": CHUNK_SHA256,
            },
        )
        unauthenticated = client.get(f"/api/v1/vault/uploads/{view.upload_id}")
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "upload_offset_mismatch"
    assert unauthenticated.status_code == 401
    assert "good" not in unauthenticated.text

    with _client(Uploads(view, denied=True)) as client:
        denied = client.get(f"/api/v1/vault/uploads/{view.upload_id}", headers=_headers())
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "not_found"


def test_upload_idempotency_state_and_capacity_errors_are_stable() -> None:
    class Failure(RuntimeError):
        def __init__(self, code: str) -> None:
            self.code = code

    view = UploadView(uuid4(), 0, 3, "OPEN")
    expected = {
        "upload_idempotency_conflict": 409,
        "upload_invalid_state": 409,
        "vault_capacity_low": 507,
    }
    for code, status in expected.items():
        with _client(Uploads(view, create_error=Failure(code))) as client:
            response = client.post(
                "/api/v1/vault/uploads",
                headers={**_headers(), "Idempotency-Key": "one"},
                json={"recording_id": str(uuid4()), "expected_size": 3},
            )
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
