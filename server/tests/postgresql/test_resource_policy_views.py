"""Live quota editor projections preserve charge semantics and OWNER account scope."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from autplay.adapters.postgresql.models import UserAccountRow
from autplay.adapters.postgresql.models.resource_admission import ResourceAdmissionRow
from autplay.adapters.postgresql.models.web_admin import WebSessionRow
from autplay.domain.web_admin import WebAdminError
from sqlalchemy import select

from .test_resource_admission_runtime import fence, play, present
from .test_resource_policy import PolicyHarness
from .test_resource_policy import policy as policy


def test_editor_counts_draining_capacity_and_scoped_queue_without_renewing(
    policy: PolicyHarness,
) -> None:
    target, outsider = policy.account(linked=True), policy.account(linked=False)
    actor = policy.admissions.actor(target)
    stranger = policy.admissions.actor(outsider)
    recording = policy.admissions.recording(actor)
    policy.admissions.budget(playbacks=1)
    running = policy.admissions.service.acquire(actor, play())
    activation = fence(running)
    policy.admissions.service.attach(actor, activation, 0, recording, None)
    permit = policy.admissions.service.open_io(actor, activation, recording)
    queued = policy.admissions.service.acquire(actor, play())
    policy.admissions.service.acquire(stranger, play())
    policy.admissions.service.release(actor, activation)
    account_view = policy.service.editor(policy.actor, target)
    server_view = policy.service.editor(policy.actor)
    assert account_view.target is not None and account_view.target.user_id == target
    assert account_view.usage.devices == 1
    assert account_view.usage.playbacks == 1
    assert account_view.usage.waiting_playbacks == 1
    assert account_view.usage.transfers == account_view.usage.waiting_transfers == 0
    assert server_view.target is None
    assert server_view.usage.playbacks == 1
    assert server_view.usage.waiting_playbacks == 2
    with policy.sessions() as session:
        stored = present(session.get(ResourceAdmissionRow, queued.operation.request.operation_id))
        assert stored.waiting_until == queued.operation.waiting_until
    with pytest.raises(WebAdminError, match="forbidden"):
        policy.service.editor(policy.actor, outsider)
    policy.admissions.service.close_io(permit)
    assert policy.service.editor(policy.actor).usage.playbacks == 1


def test_accounts_are_bounded_paginated_and_recheck_current_web_authority(
    policy: PolicyHarness,
) -> None:
    visible = {policy.account(linked=True) for _ in range(51)} | {policy.actor.user_id}
    hidden, deleted = policy.account(linked=False), policy.account(linked=True)
    with policy.sessions.begin() as session:
        present(session.get(UserAccountRow, deleted)).deleted_at = datetime.now(UTC)
    first = policy.service.accounts(policy.actor)
    assert len(first.items) == 50 and first.next_cursor == first.items[-1].user_id
    second = policy.service.accounts(policy.actor, first.next_cursor)
    assert len(second.items) == 2 and second.next_cursor is None
    identifiers = [item.user_id for item in (*first.items, *second.items)]
    assert set(identifiers) == visible
    assert identifiers == sorted(visible)
    assert hidden not in identifiers and deleted not in identifiers
    with policy.sessions.begin() as session:
        current = present(
            session.scalar(
                select(WebSessionRow).where(
                    WebSessionRow.web_session_id == policy.actor.web_session_id,
                )
            )
        )
        current.revoked_at = datetime.now(UTC)
    with pytest.raises(WebAdminError, match="authentication_required"):
        policy.service.accounts(policy.actor)
    with pytest.raises(WebAdminError, match="authentication_required"):
        policy.service.editor(policy.actor)
