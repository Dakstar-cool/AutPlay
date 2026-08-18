from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from autplay.adapters.postgresql.import_runtime import (
    ImportEntryReport,
    ImportJobReport,
    ImportReviewConflictError,
    ImportReviewResult,
    ImportStartResult,
)
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.import_identity import MAX_IMPORT_BYTES
from autplay.domain.jobs import CancelRequestResult
from autplay.entrypoints.api import create_app
from autplay.runtime.settings import ApiSettings
from pydantic import SecretStr
from starlette.testclient import TestClient

_SETTINGS = ApiSettings(
    database_url=SecretStr("postgresql+psycopg://runtime:runtime@127.0.0.1:1/autplay"),
    auth_signing_secret=SecretStr("runtime-test-signing-secret-at-least-32-bytes"),
)
_OWNER = Principal(uuid4(), uuid4(), uuid4(), AccountRole.OWNER)
_IMPORT_ID = uuid4()
_ENTRY_ID = uuid4()
_DECISION_ID = uuid4()


class Auth:
    def authenticate_access(self, token: str) -> Principal:
        if token != "good":
            from autplay.domain.auth import InvalidAccessTokenError

            raise InvalidAccessTokenError()
        return _OWNER


@dataclass
class Imports:
    last_payload: bytes | None = None

    def start(
        self,
        principal: Principal,
        *,
        payload: bytes,
        format_name: str,
        schema_version: str,
        mode: str,
    ) -> ImportStartResult:
        assert principal == _OWNER
        assert format_name == "CSV" and schema_version == "1" and mode == "LIBRARY_ONLY"
        self.last_payload = payload
        return ImportStartResult(_IMPORT_ID, uuid4(), replayed=False)

    def report(
        self,
        principal: Principal,
        import_job_id: UUID,
        *,
        limit: int = 200,
        after: str | None = None,
    ) -> ImportJobReport:
        assert principal == _OWNER and import_job_id == _IMPORT_ID and limit <= 1_000
        assert after is None
        return ImportJobReport(
            _IMPORT_ID,
            uuid4(),
            "COMPLETED",
            1,
            1,
            "autplay.generic-user-export",
            "1.0.0",
            "1",
            {"REVIEW_REQUIRED": 1},
            (
                ImportEntryReport(
                    "row:00000001:abcd",
                    _ENTRY_ID,
                    "REVIEW_REQUIRED",
                    "REVIEW_REQUIRED",
                    _DECISION_ID,
                    2,
                    1,
                    None,
                ),
            ),
            None,
        )

    def cancel(self, principal: Principal, import_job_id: UUID) -> CancelRequestResult:
        assert principal == _OWNER and import_job_id == _IMPORT_ID
        return CancelRequestResult.REQUESTED

    def resume(self, principal: Principal, import_job_id: UUID) -> ImportStartResult:
        assert principal == _OWNER and import_job_id == _IMPORT_ID
        return ImportStartResult(_IMPORT_ID, uuid4(), replayed=False)

    def review(
        self,
        principal: Principal,
        import_job_id: UUID,
        import_entry_id: UUID,
        *,
        predecessor_decision_id: UUID,
        action: str,
        selected_rank: int | None,
        idempotency_key: str,
    ) -> ImportReviewResult:
        assert principal == _OWNER and import_job_id == _IMPORT_ID
        assert import_entry_id == _ENTRY_ID
        assert predecessor_decision_id == _DECISION_ID
        assert action == "ACCEPT" and selected_rank == 1 and idempotency_key == "review-1"
        return ImportReviewResult(uuid4(), _ENTRY_ID, "MANUAL_MATCH", uuid4(), replayed=False)


class ConflictingImports(Imports):
    def review(
        self,
        principal: Principal,
        import_job_id: UUID,
        import_entry_id: UUID,
        *,
        predecessor_decision_id: UUID,
        action: str,
        selected_rank: int | None,
        idempotency_key: str,
    ) -> ImportReviewResult:
        del (
            principal,
            import_job_id,
            import_entry_id,
            predecessor_decision_id,
            action,
            selected_rank,
            idempotency_key,
        )
        raise ImportReviewConflictError


def test_import_routes_are_authenticated_bounded_and_redacted() -> None:
    imports = Imports()
    app = create_app(
        _SETTINGS,
        auth_service=Auth(),  # type: ignore[arg-type]
        import_service=imports,
    )
    headers = {"Authorization": "Bearer good"}
    with TestClient(app) as client:
        unauthorized = client.post("/api/v1/imports?format=CSV", content=b"x")
        created = client.post(
            "/api/v1/imports?format=CSV",
            content=b"title,artist\nSong,Artist",
            headers=headers,
        )
        report = client.get(f"/api/v1/imports/{_IMPORT_ID}", headers=headers)
        cancel = client.post(f"/api/v1/imports/{_IMPORT_ID}/cancel", headers=headers)
        resume = client.post(f"/api/v1/imports/{_IMPORT_ID}/resume", headers=headers)
        review = client.post(
            f"/api/v1/imports/{_IMPORT_ID}/entries/{_ENTRY_ID}/review",
            headers=headers,
            json={
                "predecessor_decision_id": str(_DECISION_ID),
                "action": "ACCEPT",
                "selected_rank": 1,
                "idempotency_key": "review-1",
            },
        )
        oversized = client.post(
            "/api/v1/imports?format=CSV",
            content=b"x",
            headers={**headers, "Content-Length": str(2 * 1024 * 1024 + 1)},
        )
        chunked_oversized = client.post(
            "/api/v1/imports?format=CSV",
            content=iter((b"x" * MAX_IMPORT_BYTES, b"y")),
            headers=headers,
        )

    assert unauthorized.status_code == 401
    assert created.status_code == 202 and created.json()["replayed"] is False
    assert imports.last_payload == b"title,artist\nSong,Artist"
    assert report.status_code == 200 and report.headers["cache-control"] == "no-store"
    serialized = report.text.casefold()
    assert "raw_payload" not in serialized
    assert "source_url" not in serialized
    assert "credential" not in serialized
    assert cancel.json() == {"result": "REQUESTED"}
    assert resume.status_code == 202
    assert review.json()["status"] == "MANUAL_MATCH"
    assert oversized.status_code == 413
    assert chunked_oversized.status_code == 413


def test_manual_review_idempotency_conflict_is_stable_409() -> None:
    app = create_app(
        _SETTINGS,
        auth_service=Auth(),  # type: ignore[arg-type]
        import_service=ConflictingImports(),
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/imports/{_IMPORT_ID}/entries/{_ENTRY_ID}/review",
            headers={"Authorization": "Bearer good"},
            json={
                "predecessor_decision_id": str(_DECISION_ID),
                "action": "ACCEPT",
                "selected_rank": 1,
                "idempotency_key": "reused-key",
            },
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "import.review_conflict"
