"""Real M6 authority, quota CAS, inherited settings and atomic policy publication."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.admin_commands import SqlAlchemyAdminCommandRepository
from autplay.adapters.postgresql.models import UserAccountRow
from autplay.adapters.postgresql.models.public_access import (
    AccountInvitationRow,
    AccountProvisioningLinkRow,
)
from autplay.adapters.postgresql.models.resource_admission import (
    AccountQuotaOverrideRow,
    QuotaOperationReceiptRow,
    ResourceAdmissionRow,
    ResourceIoPermitRow,
)
from autplay.adapters.postgresql.models.web_admin import WebSessionRow, WebTerminalReceiptRow
from autplay.adapters.postgresql.resource_admission import (
    SqlAlchemyResourceAdmissionUnitOfWorkFactory,
)
from autplay.adapters.postgresql.resource_policy import SqlAlchemyResourcePolicyUnitOfWorkFactory
from autplay.adapters.postgresql.web_admin_uow import SqlAlchemyWebAdminUnitOfWorkFactory
from autplay.application.resource_admission import ResourceAdmissionService
from autplay.application.resource_policy import ResourcePolicyService
from autplay.application.web_admin import WebAdminService
from autplay.domain.admin_commands import AdminCommand
from autplay.domain.auth import AccountRole
from autplay.domain.resource_admission import (
    AccountLimitOverride,
    AccountLimits,
    AdmissionState,
    ResourceAdmissionError,
)
from autplay.domain.resource_policy import GlobalResourceLimits, QuotaChange
from autplay.domain.web_admin import WebActor, WebAdminError
from psycopg import Connection
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .test_resource_admission_runtime import AdmissionHarness, fence, play, present
from .test_web_admin_m6 import _seed_owner


@dataclass
class PolicyHarness:
    sessions: sessionmaker[Session]
    service: ResourcePolicyService
    web: WebAdminService
    actor: WebActor
    admissions: AdmissionHarness

    def account(self, *, linked: bool) -> UUID:
        user_id, invitation = uuid4(), uuid4()
        now = datetime.now(UTC)
        with self.sessions.begin() as session:
            session.add(UserAccountRow(user_id=user_id, role="USER", display_name="Invited test"))
            session.flush()
            if linked:
                session.add(
                    AccountInvitationRow(
                        invitation_id=invitation,
                        issued_by_user_id=self.actor.user_id,
                        display_name="Invited test",
                        secret_sha256=hashlib.sha256(invitation.bytes).digest(),
                        issued_at=now,
                        expires_at=now + timedelta(hours=1),
                        consumed_at=now,
                    )
                )
                session.flush()
                session.add(
                    AccountProvisioningLinkRow(
                        user_id=user_id,
                        invitation_id=invitation,
                        issued_by_user_id=self.actor.user_id,
                        created_at=now,
                    )
                )
        return user_id


@pytest.fixture
def policy(database_url: str, database_connection: Connection[object]) -> Iterator[PolicyHarness]:
    owner, _ = _seed_owner(database_connection)
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    web = WebAdminService(
        SqlAlchemyWebAdminUnitOfWorkFactory(sessions),
        b"resource-policy-csrf-test-secret-32-bytes",
    )
    invitation = web.issue_invitation(owner)
    actor = web.login(web.begin_login(), invitation.bearer, b"q" * 32).actor
    service = ResourcePolicyService(SqlAlchemyResourcePolicyUnitOfWorkFactory(sessions))
    admissions = AdmissionHarness(
        engine,
        sessions,
        ResourceAdmissionService(
            SqlAlchemyResourceAdmissionUnitOfWorkFactory(sessions),
        ),
    )
    try:
        yield PolicyHarness(sessions, service, web, actor, admissions)
    finally:
        engine.dispose()


def test_defaults_partial_override_and_reset_keep_monotonic_revision(policy: PolicyHarness) -> None:
    target = policy.account(linked=True)
    change = QuotaChange(uuid4(), 1, AccountLimits(7, 3, 4))
    first = policy.service.apply(policy.actor, change)
    assert first["global_revision"] == 2
    override = QuotaChange(uuid4(), 2, AccountLimitOverride(transfers=1), target, 0)
    policy.service.apply(policy.actor, override)
    snapshot = policy.service.view(policy.actor, target)
    assert snapshot.effective == AccountLimits(7, 3, 1)
    policy.service.apply(policy.actor, QuotaChange(uuid4(), 2, AccountLimitOverride(), target, 1))
    snapshot = policy.service.view(policy.actor, target)
    assert snapshot.account_revision == 2 and snapshot.effective == AccountLimits(7, 3, 4)
    assert policy.service.apply(policy.actor, change) == first
    with policy.sessions() as session:
        assert present(session.get(AccountQuotaOverrideRow, target)).revision == 2
        assert session.scalar(select(func.count()).select_from(QuotaOperationReceiptRow)) == 3
    with pytest.raises(ResourceAdmissionError, match="resource_revision_stale"):
        policy.service.apply(
            policy.actor, QuotaChange(uuid4(), 2, AccountLimitOverride(devices=2), target, 0)
        )
    with pytest.raises(ResourceAdmissionError, match="resource_operation_conflict"):
        policy.service.apply(policy.actor, replace(change, values=AccountLimits(8, 3, 4)))


def test_two_tabs_cannot_overwrite_each_other(policy: PolicyHarness) -> None:
    changes = [QuotaChange(uuid4(), 1, AccountLimits(value, 2, 2)) for value in (6, 7)]

    def save(change: QuotaChange) -> str:
        try:
            return str(policy.service.apply(policy.actor, change)["outcome"])
        except ResourceAdmissionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, changes))
    assert sorted(results) == ["APPLIED", "resource_revision_stale"]
    assert policy.service.view(policy.actor).global_revision == 2


@pytest.mark.parametrize("gate", ["revoked", "generation", "token_age", "idle", "absolute", "role"])
def test_policy_revalidates_exact_authority_inside_transaction_even_on_replay(
    policy: PolicyHarness,
    gate: str,
) -> None:
    change = QuotaChange(uuid4(), 1, AccountLimits(6, 2, 2))
    policy.service.apply(policy.actor, change)
    with policy.sessions.begin() as session:
        web = present(session.get(WebSessionRow, policy.actor.web_session_id))
        now = datetime.now(UTC)
        if gate == "revoked":
            web.revoked_at = now
        elif gate == "generation":
            web.token_generation += 1
        elif gate == "token_age":
            web.token_issued_at = now - timedelta(minutes=16)
        elif gate == "idle":
            web.idle_expires_at = now - timedelta(seconds=1)
        elif gate == "absolute":
            web.issued_at = now - timedelta(hours=12)
            web.absolute_expires_at = now - timedelta(seconds=1)
        else:
            session.add(
                UserAccountRow(user_id=uuid4(), display_name="Replacement owner", role="OWNER")
            )
            session.flush()
            present(session.get(UserAccountRow, policy.actor.user_id)).role = "ADMIN"
    with pytest.raises(WebAdminError, match="authentication_required"):
        policy.service.apply(policy.actor, change)
    with policy.sessions() as session:
        assert session.scalar(select(func.count()).select_from(QuotaOperationReceiptRow)) == 1


def test_owner_scope_excludes_unlinked_accounts_and_admin_cannot_edit(
    policy: PolicyHarness,
) -> None:
    stranger = policy.account(linked=False)
    with pytest.raises(WebAdminError, match="forbidden"):
        policy.service.view(policy.actor, stranger)
    with pytest.raises(WebAdminError, match="forbidden"):
        policy.service.apply(
            policy.actor, QuotaChange(uuid4(), 1, AccountLimitOverride(devices=1), stranger, 0)
        )
    with pytest.raises(WebAdminError, match="authentication_required"):
        policy.service.apply(
            replace(policy.actor, role=AccountRole.ADMIN), QuotaChange(uuid4(), 1, AccountLimits())
        )
    own = policy.service.apply(
        policy.actor,
        QuotaChange(
            uuid4(),
            1,
            AccountLimitOverride(devices=3),
            policy.actor.user_id,
            0,
        ),
    )
    effective = own["effective"]
    assert isinstance(effective, dict) and effective["devices"] == 3


def test_common_m6_operation_collision_and_audit_failure_leave_policy_unchanged(
    policy: PolicyHarness,
) -> None:
    with policy.sessions() as session:
        prior = session.scalar(select(WebTerminalReceiptRow.operation_id).limit(1))
        assert prior is not None
    with pytest.raises(ResourceAdmissionError, match="resource_operation_conflict"):
        policy.service.apply(policy.actor, QuotaChange(prior, 1, AccountLimits(8, 2, 2)))
    with policy.sessions.begin() as session:
        session.execute(
            text("""
        CREATE FUNCTION audit.quota_test_abort() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.action LIKE 'resource_quota.%' THEN RAISE EXCEPTION 'quota audit abort';
        END IF; RETURN NEW; END $$;
        CREATE TRIGGER quota_test_abort BEFORE INSERT ON audit.audit_event
        FOR EACH ROW EXECUTE FUNCTION audit.quota_test_abort();
        """)
        )
    operation = uuid4()
    with pytest.raises(DBAPIError, match="quota audit abort"):
        policy.service.apply(policy.actor, QuotaChange(operation, 1, AccountLimits(8, 2, 2)))
    assert policy.service.view(policy.actor).global_revision == 1
    with policy.sessions() as session:
        assert session.get(QuotaOperationReceiptRow, operation) is None
        assert session.get(WebTerminalReceiptRow, operation) is None


def test_budget_is_measured_and_increase_promotes_without_restart(policy: PolicyHarness) -> None:
    with pytest.raises(ResourceAdmissionError, match="resource_budget_unconfigured"):
        policy.service.apply(policy.actor, QuotaChange(uuid4(), 1, GlobalResourceLimits(2, 2)))
    a, b = policy.admissions.actor(), policy.admissions.actor()
    policy.admissions.budget(playbacks=1)
    first = policy.admissions.service.acquire(a, play())
    second = policy.admissions.service.acquire(b, play())
    assert second.operation.state == AdmissionState.WAITING
    with pytest.raises(ResourceAdmissionError, match="resource_budget_exceeded"):
        policy.service.apply(policy.actor, QuotaChange(uuid4(), 2, GlobalResourceLimits(17, 2)))
    policy.service.apply(policy.actor, QuotaChange(uuid4(), 2, GlobalResourceLimits(2, 2)))
    second = policy.admissions.service.poll(b, second.operation.request.operation_id)
    assert second.operation.state == AdmissionState.ACTIVE
    policy.service.apply(policy.actor, QuotaChange(uuid4(), 3, GlobalResourceLimits(1, 1)))
    assert policy.admissions.service.renew(a, fence(first)).usage.server == 2
    assert policy.admissions.service.renew(b, fence(second)).usage.server == 2


@pytest.mark.parametrize("target_type", ["DEVICE", "USER_SESSION"])
def test_admin_revoke_stops_renewal_and_preserves_draining_capacity(
    policy: PolicyHarness,
    target_type: str,
) -> None:
    admission = policy.admissions
    actor = admission.actor(policy.actor.user_id)
    other = admission.actor()
    admission.budget(playbacks=1)
    first = admission.service.acquire(actor, play())
    recording = admission.recording(actor)
    admission.service.attach(actor, fence(first), 0, recording, None)
    permit = admission.service.open_io(actor, fence(first), recording)
    waiting = admission.service.acquire(other, play())
    target = actor.device_id if target_type == "DEVICE" else actor.session_id
    command = AdminCommand(policy.actor, uuid4(), target, hashlib.sha256(target.bytes).digest())
    repository = SqlAlchemyAdminCommandRepository(policy.sessions)
    result = repository.execute(command, action="resource_test.revoked", target_type=target_type)
    assert result["outcome"] == "APPLIED"
    assert (
        repository.execute(command, action="resource_test.revoked", target_type=target_type)
        == result
    )
    with policy.sessions() as session:
        assert present(
            session.get(ResourceAdmissionRow, first.operation.request.operation_id)
        ).state == ("EXPIRED")
        assert session.get(ResourceIoPermitRow, permit.permit_id) is not None
    with pytest.raises(ResourceAdmissionError):
        admission.service.renew_io(actor, permit)
    blocked = admission.service.poll(other, waiting.operation.request.operation_id)
    assert blocked.operation.state == AdmissionState.WAITING and blocked.usage.server == 1
    admission.service.close_io(permit)
    granted = admission.service.poll(other, waiting.operation.request.operation_id)
    assert granted.operation.state == AdmissionState.ACTIVE and granted.usage.server == 1


@pytest.mark.parametrize("gate", ["revoked", "generation", "token_age", "idle", "absolute"])
def test_admin_device_mutation_revalidates_browser_authority(
    policy: PolicyHarness, gate: str
) -> None:
    actor = policy.admissions.actor(policy.actor.user_id)
    command = AdminCommand(policy.actor, uuid4(), actor.device_id, b"a" * 32)
    with policy.sessions.begin() as session:
        row = present(session.get(WebSessionRow, policy.actor.web_session_id))
        now = datetime.now(UTC)
        if gate == "revoked":
            row.revoked_at = now
        elif gate == "generation":
            row.token_generation += 1
        elif gate == "token_age":
            row.token_issued_at = now - timedelta(minutes=16)
        elif gate == "idle":
            row.idle_expires_at = now - timedelta(seconds=1)
        else:
            row.issued_at = now - timedelta(hours=12)
            row.absolute_expires_at = now - timedelta(seconds=1)
    with pytest.raises(WebAdminError, match="authentication_required"):
        SqlAlchemyAdminCommandRepository(policy.sessions).execute(
            command, action="resource_test.revoked", target_type="DEVICE"
        )


def test_quota_receipt_cleanup_is_bounded_and_keeps_recent_replay(policy: PolicyHarness) -> None:
    changes = [QuotaChange(uuid4(), revision, AccountLimits()) for revision in (1, 2, 3)]
    for change in changes:
        policy.service.apply(policy.actor, change)
    with policy.sessions.begin() as session:
        for change in changes[:2]:
            present(session.get(QuotaOperationReceiptRow, change.operation_id)).created_at = (
                datetime.now(UTC) - timedelta(days=8)
            )
    assert policy.admissions.service.sweep(maximum=1) == 1
    with policy.sessions() as session:
        assert session.scalar(select(func.count()).select_from(QuotaOperationReceiptRow)) == 2
    assert policy.admissions.service.sweep(maximum=1) == 1
    assert policy.service.apply(policy.actor, changes[2])["outcome"] == "APPLIED"
