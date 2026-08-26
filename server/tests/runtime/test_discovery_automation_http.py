from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from autplay.application.discovery_automation import (
    AUTO_IMPORT_CONFIRMATION,
    CandidateActionResult,
    DiscoveryAutomationService,
    DiscoveryRunView,
    PolicyMutation,
    PolicyMutationResult,
    PolicyView,
    ReleaseCandidateView,
)
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import AuthenticatedWebSession, WebActor, WebAdminError
from autplay.entrypoints.admin_web_http import WebAdminHttp
from autplay.entrypoints.discovery_automation_http import create_discovery_automation_router
from autplay.web.renderer import AdminTemplateRenderer
from fastapi import FastAPI
from starlette.testclient import TestClient

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


class _Web:
    def __init__(self) -> None:
        self.actor = WebActor(uuid4(), uuid4(), uuid4(), AccountRole.OWNER, 0)

    def authenticate_safe_get(
        self, bearer: bytes, *, head: bool = False
    ) -> AuthenticatedWebSession:
        del head
        return self.authenticate(bearer, mutation=False)

    def authenticate(self, bearer: bytes, *, mutation: bool) -> AuthenticatedWebSession:
        del mutation
        if bearer != b"session":
            raise WebAdminError("authentication_required")
        return AuthenticatedWebSession(self.actor, b"c" * 32)

    def validate_csrf(self, actor: WebActor, csrf: bytes, operation_id: UUID) -> None:
        if actor != self.actor or csrf != b"c" * 32 or not isinstance(operation_id, UUID):
            raise WebAdminError("csrf_invalid")


class _Automation:
    def __init__(self) -> None:
        self.policy = PolicyView(
            uuid4(), uuid4(), "20", "SCHEDULED", "REVIEW_REQUIRED", True, 1, None, NOW
        )
        self.run = DiscoveryRunView(
            uuid4(), self.policy.policy_id, 1, "QUEUED", 0, 0, 0, NOW, None, None
        )
        self.commands: list[tuple[UUID, PolicyMutation]] = []
        self.run_now_calls: list[tuple[UUID, UUID, UUID]] = []
        self.candidate_calls: list[tuple[UUID, UUID, int]] = []
        self.candidate_actions: list[tuple[str, UUID, UUID, UUID]] = []
        self.candidate_id = uuid4()

    def policies(self, actor: WebActor, *, limit: int = 100) -> tuple[PolicyView, ...]:
        assert limit == 100
        assert actor.user_id
        return (self.policy,)

    def runs(self, actor: WebActor, *, limit: int = 50) -> tuple[DiscoveryRunView, ...]:
        assert limit == 50
        assert actor.user_id
        return (self.run,)

    def candidates(
        self, actor: WebActor, run_id: UUID, *, limit: int = 50
    ) -> tuple[ReleaseCandidateView, ...]:
        self.candidate_calls.append((actor.user_id, run_id, limit))
        return (
            ReleaseCandidateView(
                self.candidate_id,
                run_id,
                "Fresh Track",
                "Open Artist",
                None,
                NOW,
                "FOUND",
                "NOT_STARTED",
                False,
            ),
        )

    def set_policy(
        self, actor: WebActor, command: PolicyMutation, *, now: datetime
    ) -> PolicyMutationResult:
        assert now == NOW
        self.commands.append((actor.user_id, command))
        return PolicyMutationResult(self.policy, False)

    def run_now(
        self, actor: WebActor, policy_id: UUID, operation_id: UUID, *, now: datetime
    ) -> DiscoveryRunView:
        assert now == NOW
        self.run_now_calls.append((actor.user_id, policy_id, operation_id))
        return self.run

    def select_candidate(
        self, actor: WebActor, candidate_id: UUID, operation_id: UUID, *, now: datetime
    ) -> CandidateActionResult:
        return self._candidate_action("select", actor, candidate_id, operation_id, now)

    def retry_candidate(
        self, actor: WebActor, candidate_id: UUID, operation_id: UUID, *, now: datetime
    ) -> CandidateActionResult:
        return self._candidate_action("retry", actor, candidate_id, operation_id, now)

    def ignore_candidate(
        self, actor: WebActor, candidate_id: UUID, operation_id: UUID, *, now: datetime
    ) -> CandidateActionResult:
        return self._candidate_action("ignore", actor, candidate_id, operation_id, now)

    def _candidate_action(
        self, action: str, actor: WebActor, candidate_id: UUID, operation_id: UUID, now: datetime
    ) -> CandidateActionResult:
        assert now == NOW
        self.candidate_actions.append((action, actor.user_id, candidate_id, operation_id))
        return CandidateActionResult(candidate_id, "SELECTED", "QUEUED", False)


def _client() -> tuple[TestClient, _Web, _Automation]:
    web = _Web()
    automation = _Automation()
    app = FastAPI()
    app.include_router(
        create_discovery_automation_router(
            web=cast(WebAdminHttp, web),
            automation=cast(DiscoveryAutomationService, automation),
            renderer=AdminTemplateRenderer(),
            origin="https://admin.test",
            now=lambda: NOW,
        )
    )
    client = TestClient(app, base_url="https://admin.test")
    client.cookies.set("__Host-autplay_admin", "session")
    return client, web, automation


def _fields(html: str, action: str) -> dict[str, str]:
    match = re.search(
        rf'<form method="post" action="{re.escape(action)}"[^>]*>(.*?)</form>', html, re.DOTALL
    )
    assert match is not None
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', match.group(1)))


def test_policy_run_and_candidate_views_are_owner_scoped_and_redacted() -> None:
    client, web, automation = _client()
    page = client.get("/admin/discovery/automation?lang=en")

    assert page.status_code == 200
    assert "Discovery automation" in page.text
    assert "No raw provider payload" not in page.text
    assert "no-store" in page.headers["cache-control"]
    assert "content-security-policy" in page.headers

    policy_form = _fields(page.text, "/admin/discovery/automation/set-policy")
    policy_form.update(
        {
            "canonical_artist_id": str(automation.policy.canonical_artist_id),
            "provider_artist_id": "20",
            "discovery_mode": "SCHEDULED",
            "import_mode": "AUTO_IMPORT",
            "automation_enabled": "true",
            "expected_revision": "1",
            "confirmation_code": AUTO_IMPORT_CONFIRMATION,
        }
    )
    saved = client.post(
        "/admin/discovery/automation/set-policy",
        data=policy_form,
        headers={"Origin": "https://admin.test"},
    )

    assert saved.status_code == 200
    assert automation.commands[0][0] == web.actor.user_id
    assert automation.commands[0][1].confirmation_code == AUTO_IMPORT_CONFIRMATION

    run_form = _fields(saved.text, "/admin/discovery/automation/run-now")
    ran = client.post(
        "/admin/discovery/automation/run-now",
        data=run_form,
        headers={"Origin": "https://admin.test"},
    )
    assert ran.status_code == 200
    assert automation.run_now_calls[0][0:2] == (web.actor.user_id, automation.policy.policy_id)

    detail = client.get(f"/admin/discovery/automation/runs/{automation.run.run_id}")
    assert detail.status_code == 200 and "Fresh Track" in detail.text
    assert automation.candidate_calls == [(web.actor.user_id, automation.run.run_id, 50)]


def test_candidate_actions_are_explicit_owner_scoped_and_csrf_protected() -> None:
    client, web, automation = _client()
    detail = client.get(f"/admin/discovery/automation/runs/{automation.run.run_id}")

    for action in ("select", "retry", "ignore"):
        form = _fields(detail.text, f"/admin/discovery/automation/candidates/{action}")
        response = client.post(
            f"/admin/discovery/automation/candidates/{action}",
            data=form,
            headers={"Origin": "https://admin.test"},
        )
        assert response.status_code == 200
        detail = response

    assert [item[0] for item in automation.candidate_actions] == ["select", "retry", "ignore"]
    assert all(
        item[1:3] == (web.actor.user_id, automation.candidate_id)
        for item in automation.candidate_actions
    )

    invalid = client.post(
        "/admin/discovery/automation/candidates/invalid",
        data={"candidate_id": str(automation.candidate_id)},
        headers={"Origin": "https://admin.test"},
    )
    assert invalid.status_code == 403


def test_mutations_require_exact_origin_csrf_and_exact_auto_import_confirmation() -> None:
    client, _, automation = _client()
    page = client.get("/admin/discovery/automation")
    form = _fields(page.text, "/admin/discovery/automation/set-policy")
    form.update(
        {
            "canonical_artist_id": str(automation.policy.canonical_artist_id),
            "provider_artist_id": "20",
            "discovery_mode": "SCHEDULED",
            "import_mode": "AUTO_IMPORT",
            "automation_enabled": "true",
            "expected_revision": "",
            "confirmation_code": "different",
        }
    )

    wrong_origin = client.post(
        "/admin/discovery/automation/set-policy",
        data=form,
        headers={"Origin": "https://wrong.test"},
    )
    assert wrong_origin.status_code == 403 and automation.commands == []

    rejected_confirmation = client.post(
        "/admin/discovery/automation/set-policy",
        data=form,
        headers={"Origin": "https://admin.test"},
    )
    assert rejected_confirmation.status_code == 403 and automation.commands == []
