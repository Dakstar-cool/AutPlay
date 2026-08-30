"""Bearer API evidence for the post-A1C Android management surface."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from autplay.application.discovery_automation import (
    CandidateActionResult,
    DiscoveryRunView,
    PolicyMutation,
    PolicyMutationResult,
    PolicyView,
    ReleaseCandidateView,
)
from autplay.domain.auth import AccountRole, Principal
from autplay.entrypoints.discovery_automation_api import (
    create_discovery_automation_api_router,
)
from autplay.runtime.http import RequestRuntimeMiddleware, install_error_handlers
from autplay.runtime.metrics import RuntimeMetrics
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
ARTIST_ID = UUID("22222222-2222-4222-8222-222222222222")
POLICY_ID = UUID("33333333-3333-4333-8333-333333333333")
RUN_ID = UUID("44444444-4444-4444-8444-444444444444")
CANDIDATE_ID = UUID("55555555-5555-4555-8555-555555555555")


class FakeAutomation:
    def __init__(self) -> None:
        self.actor_ids: list[UUID] = []
        self.last_policy: PolicyMutation | None = None

    def policies(self, actor: Principal, *, limit: int) -> tuple[PolicyView, ...]:
        self.actor_ids.append(actor.user_id)
        assert limit == 100
        return (
            PolicyView(
                POLICY_ID,
                ARTIST_ID,
                "20",
                "SCHEDULED",
                "REVIEW_REQUIRED",
                True,
                3,
                NOW,
                NOW,
            ),
        )

    def runs(self, actor: Principal, *, limit: int) -> tuple[DiscoveryRunView, ...]:
        self.actor_ids.append(actor.user_id)
        assert limit == 50
        return (self._run(),)

    def candidates(
        self, actor: Principal, run_id: UUID, *, limit: int
    ) -> tuple[ReleaseCandidateView, ...]:
        self.actor_ids.append(actor.user_id)
        assert run_id == RUN_ID and limit == 50
        return (
            ReleaseCandidateView(
                CANDIDATE_ID,
                RUN_ID,
                "Release",
                "Artist",
                None,
                NOW,
                "PENDING_REVIEW",
                "NOT_REQUESTED",
                False,
            ),
        )

    def set_policy(
        self, actor: Principal, command: PolicyMutation, *, now: datetime
    ) -> PolicyMutationResult:
        self.actor_ids.append(actor.user_id)
        self.last_policy = command
        assert now == NOW
        return PolicyMutationResult(
            PolicyView(
                POLICY_ID,
                command.canonical_artist_id,
                command.provider_artist_id,
                command.discovery_mode,
                command.import_mode,
                command.automation_enabled,
                1,
                None,
                None,
            ),
            False,
        )

    def run_now(
        self, actor: Principal, policy_id: UUID, operation_id: UUID, *, now: datetime
    ) -> DiscoveryRunView:
        del operation_id
        self.actor_ids.append(actor.user_id)
        assert policy_id == POLICY_ID and now == NOW
        return self._run()

    def select_candidate(
        self, actor: Principal, candidate_id: UUID, operation_id: UUID, *, now: datetime
    ) -> CandidateActionResult:
        del operation_id
        self.actor_ids.append(actor.user_id)
        assert candidate_id == CANDIDATE_ID and now == NOW
        return CandidateActionResult(candidate_id, "SELECTED", "QUEUED", False)

    retry_candidate = select_candidate
    ignore_candidate = select_candidate

    @staticmethod
    def _run() -> DiscoveryRunView:
        return DiscoveryRunView(RUN_ID, POLICY_ID, 3, "COMPLETED", 4, 1, 1, NOW, NOW, None)


def _client(service: FakeAutomation) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestRuntimeMiddleware, metrics=RuntimeMetrics())

    def authenticated(request: Request) -> None:
        request.state.principal = Principal(OWNER_ID, uuid4(), uuid4(), AccountRole.OWNER)

    app.include_router(
        create_discovery_automation_api_router(
            service,  # type: ignore[arg-type]
            authenticated=authenticated,
            now=lambda: NOW,
        ),
        prefix="/api/v1",
    )
    return TestClient(app)


def test_snapshot_and_candidates_are_owner_scoped_bounded_and_private() -> None:
    service = FakeAutomation()
    client = _client(service)

    snapshot = client.get("/api/v1/discovery/automation/snapshot")
    candidates = client.get(f"/api/v1/discovery/automation/runs/{RUN_ID}/candidates")

    assert snapshot.status_code == 200
    assert snapshot.json()["policies"][0]["revision"] == 3
    assert snapshot.json()["runs"][0]["run_id"] == str(RUN_ID)
    assert candidates.status_code == 200
    assert candidates.json()["candidates"][0]["candidate_id"] == str(CANDIDATE_ID)
    assert snapshot.headers["cache-control"] == "private, no-store, max-age=0"
    assert snapshot.headers["vary"] == "Authorization"
    assert service.actor_ids == [OWNER_ID, OWNER_ID, OWNER_ID]


def test_auto_import_requires_exact_explicit_consequence_confirmation() -> None:
    service = FakeAutomation()
    client = _client(service)
    body = {
        "contract_version": "release-discovery-v1",
        "schema_version": 1,
        "operation_id": str(uuid4()),
        "action": "SET_ARTIST_POLICY",
        "canonical_artist_id": str(ARTIST_ID),
        "provider_artist_id": "20",
        "discovery_mode": "SCHEDULED",
        "import_mode": "AUTO_IMPORT",
        "automation_enabled": True,
        "expected_policy_revision": None,
        "consequence_confirmation": "wrong",
    }

    invalid = client.post("/api/v1/discovery/automation/commands", json=body)
    body["consequence_confirmation"] = (
        "AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1"
    )
    accepted = client.post("/api/v1/discovery/automation/commands", json=body)

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "discovery_automation_invalid"
    assert accepted.status_code == 200
    assert accepted.json()["action"] == "SET_ARTIST_POLICY"
    assert service.last_policy is not None
    assert service.last_policy.confirmation_code == body["consequence_confirmation"]


def test_action_specific_body_is_strict_and_candidate_command_is_dispatched() -> None:
    service = FakeAutomation()
    client = _client(service)
    invalid = client.post(
        "/api/v1/discovery/automation/commands",
        json={
            "contract_version": "release-discovery-v1",
            "schema_version": 1,
            "operation_id": str(uuid4()),
            "action": "START_DISCOVERY",
            "policy_id": str(POLICY_ID),
            "candidate_id": str(CANDIDATE_ID),
        },
    )
    selected = client.post(
        "/api/v1/discovery/automation/commands",
        json={
            "contract_version": "release-discovery-v1",
            "schema_version": 1,
            "operation_id": str(uuid4()),
            "action": "SELECT_CANDIDATE",
            "candidate_id": str(CANDIDATE_ID),
        },
    )

    assert invalid.status_code == 422
    assert selected.status_code == 200
    assert selected.json()["candidate"] == {
        "candidate_id": str(CANDIDATE_ID),
        "disposition": "SELECTED",
        "acquisition_state": "QUEUED",
    }
