from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from autplay.adapters.postgresql.import_runtime import (
    ImportEntryReport,
    ImportJobReport,
    ImportNotFoundError,
    ImportReviewCandidate,
    ImportReviewConflictError,
    ImportReviewContext,
    ImportReviewContextLimitError,
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
    public_access_source_hmac_secret=SecretStr(
        "public-access-source-hmac-secret-at-least-32-bytes"
    ),
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
    context_failure: str | None = None

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

    def review_context(
        self, principal: Principal, import_job_id: UUID, import_entry_id: UUID
    ) -> ImportReviewContext:
        assert principal == _OWNER and import_job_id == _IMPORT_ID and import_entry_id == _ENTRY_ID
        if self.context_failure == "not_found":
            raise ImportNotFoundError
        if self.context_failure == "row_cap":
            raise ImportReviewContextLimitError
        title = (
            "Current catalog title" if self.context_failure != "encoded_cap" else "\u0001" * 4096
        )
        count = 1 if self.context_failure != "encoded_cap" else 100
        return ImportReviewContext(
            _IMPORT_ID,
            _ENTRY_ID,
            _DECISION_ID,
            "REVIEW_REQUIRED",
            "REVIEW_REQUIRED",
            ("ACCEPT", "REJECT", "KEEP_UNRESOLVED", "CREATE_RECORDING"),
            tuple(
                ImportReviewCandidate(
                    rank,
                    uuid4(),
                    title,
                    "Current catalog artist",
                    None,
                    None,
                    None,
                    "T0",
                    "1",
                    bytes(32),
                    False,
                    True,
                )
                for rank in range(1, count + 1)
            ),
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


# Future I1 assertions only. No execution/qualification was authorized for this source slice.
def test_owned_review_context_is_complete_redacted_and_explicitly_current_display() -> None:
    app = create_app(_SETTINGS, auth_service=Auth(), import_service=Imports())  # type: ignore[arg-type]
    path = f"/api/v1/imports/{_IMPORT_ID}/entries/{_ENTRY_ID}/review-context"
    with TestClient(app) as client:
        unauthorized = client.get(path)
        response = client.get(path, headers={"Authorization": "Bearer good"})
    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    body = response.json()
    assert body["format"] == 1 and body["display_metadata"] == "CURRENT_CATALOG_DISPLAY"
    assert body["predecessor_decision_id"] == str(_DECISION_ID)
    candidate = body["candidates"][0]
    assert candidate["rank"] == 1 and candidate["current_title"] == "Current catalog title"
    assert candidate["available"] is True and len(candidate["evidence_sha256"]) == 64
    assert set(candidate) == {
        "rank",
        "recording_id",
        "current_title",
        "current_artist",
        "duration_ms",
        "raw_score",
        "confidence",
        "evidence_tier",
        "evidence_schema_version",
        "evidence_sha256",
        "has_hard_conflicts",
        "available",
    }


def test_owned_review_context_refuses_whole_caps_and_uses_uniform_not_found() -> None:
    path = f"/api/v1/imports/{_IMPORT_ID}/entries/{_ENTRY_ID}/review-context"
    for failure, status, code in (
        ("not_found", 404, "not_found"),
        ("row_cap", 413, "import.review_context_limit"),
        ("encoded_cap", 413, "import.review_context_limit"),
    ):
        app = create_app(
            _SETTINGS,
            auth_service=Auth(),  # type: ignore[arg-type]
            import_service=Imports(context_failure=failure),
        )
        with TestClient(app) as client:
            response = client.get(path, headers={"Authorization": "Bearer good"})
        assert response.status_code == status and response.json()["error"]["code"] == code
        assert response.headers["cache-control"] == "no-store, private"
        assert "candidates" not in response.json()
