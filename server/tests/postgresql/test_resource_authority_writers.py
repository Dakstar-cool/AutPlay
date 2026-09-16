"""Current actor authority at the mutation boundary, including cross-protocol draining."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from psycopg import Connection
from sqlalchemy import select
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.profile_pairing import DeviceAdmissionRow, DeviceKeyBlockRow
from autplay.adapters.postgresql.models.web_admin import WebSessionRow
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.auth import AccountRole, InvalidAccessTokenError, Principal
from autplay.domain.profile_pairing import ProfilePairingError
from autplay.domain.resource_admission import AdmissionState, ResourceAdmissionError

from .test_auth_runtime import AuthRuntime, _bootstrap, _insert_user_device_session
from .test_auth_runtime import auth_runtime as auth_runtime
from .test_device_admission_s1b import _admission, _Runtime, _web_actor
from .test_device_admission_s1b import admission_runtime as admission_runtime
from .test_profile_pairing_m5b import _exchange_request, _owner
from .test_profile_pairing_m5b import pairing_service as pairing_service
from .test_resource_admission_runtime import AdmissionHarness, fence, play, present
from .test_resource_admission_runtime import admission as admission


@pytest.mark.parametrize("gate", ["revoked", "generation", "token_age", "idle", "absolute", "role"])
def test_s1_rechecks_browser_after_authentication_before_commands_or_replay(
    admission_runtime: _Runtime,
    database_connection: Connection[object],
    gate: str,
) -> None:
    runtime = admission_runtime
    actor = _web_actor(runtime, database_connection)
    _, created, _, _ = _admission(runtime, source="actor-recheck")
    _, other, _, _ = _admission(runtime, source="actor-recheck", nonce_marker=2)
    operation_id = uuid4()
    runtime.service.bind_device_admission_review(
        actor=actor,
        web_session_id=actor.web_session_id,
        locator=str(created["review_locator"]),
        operation_id=operation_id,
        request_sha256=b"b" * 32,
    )
    with runtime.sessions.begin() as session:
        row = present(session.get(WebSessionRow, actor.web_session_id))
        now = runtime.clock.value
        if gate == "revoked":
            row.revoked_at = now
        elif gate == "generation":
            row.token_generation += 1
        elif gate == "token_age":
            row.token_issued_at = now - timedelta(minutes=16)
        elif gate == "idle":
            row.idle_expires_at = now - timedelta(seconds=1)
        elif gate == "absolute":
            row.issued_at = now - timedelta(hours=12)
            row.absolute_expires_at = now - timedelta(seconds=1)
        else:
            present(session.get(UserAccountRow, actor.user_id)).role = "USER"
    actions: tuple[Callable[[], object], ...] = (
        lambda: runtime.service.bind_device_admission_review(
            actor=actor,
            web_session_id=actor.web_session_id,
            locator=str(created["review_locator"]),
            operation_id=operation_id,
            request_sha256=b"b" * 32,
        ),
        lambda: runtime.service.bind_device_admission_review(
            actor=actor,
            web_session_id=actor.web_session_id,
            locator=str(other["review_locator"]),
            operation_id=uuid4(),
            request_sha256=b"c" * 32,
        ),
        lambda: runtime.service.decide_device_admission(
            actor,
            UUID(str(created["request_id"])),
            "TRUST_DEVICE",
            uuid4(),
            b"d" * 32,
            web_session_id=actor.web_session_id,
        ),
        lambda: runtime.service.manage_trusted_key(
            principal=actor,
            thumbprint=b"t" * 32,
            action="BLOCK_FUTURE_ADMISSION",
            operation_id=uuid4(),
            request_sha256=b"e" * 32,
        ),
    )
    for action in actions:
        with pytest.raises(ProfilePairingError, match="admission_request_unavailable"):
            action()
    with runtime.sessions() as session:
        assert (
            present(session.get(DeviceAdmissionRow, UUID(str(created["request_id"])))).state
            == "PENDING"
        )
        assert (
            present(
                session.get(DeviceAdmissionRow, UUID(str(other["request_id"])))
            ).review_web_session_id
            is None
        )
        assert session.scalar(select(DeviceKeyBlockRow.user_id)) is None


@pytest.mark.parametrize("gate", ["device", "session", "role"])
def test_m5_cached_actor_cannot_issue_or_target_other_devices_after_revocation(
    pairing_service: ProfilePairingService,
    database_connection: Connection[object],
    gate: str,
) -> None:
    owner = _owner(database_connection)
    initial = pairing_service.issue_invitation(owner, uuid4(), 60)
    request, _, _ = _exchange_request(initial)
    bound, _ = pairing_service.exchange(request)
    actor = Principal(
        owner.user_id,
        UUID(str(bound["device_id"])),
        UUID(str(bound["session_id"])),
        AccountRole.OWNER,
    )
    invitation = pairing_service.issue_invitation(actor, uuid4(), 60)
    # The typed actor represents a successful earlier HTTP authentication.
    with pairing_service._sessions.begin() as session:
        if gate == "device":
            present(session.get(DeviceRow, actor.device_id)).revoked_at = datetime.now(UTC)
        elif gate == "session":
            present(session.get(UserSessionRow, actor.session_id)).revoked_at = datetime.now(UTC)
        else:
            present(session.get(UserAccountRow, actor.user_id)).role = "USER"
    actions: tuple[Callable[[], object], ...] = (
        lambda: pairing_service.issue_invitation(actor, uuid4(), 60),
        lambda: pairing_service.cancel_invitation(
            actor, UUID(str(invitation["invitation_id"])), uuid4()
        ),
        lambda: pairing_service.logout_all(actor, uuid4()),
        lambda: pairing_service.revoke_device(actor, owner.device_id, uuid4()),
    )
    for action in actions:
        with pytest.raises(ProfilePairingError):
            action()
    with pairing_service._sessions() as session:
        assert present(session.get(DeviceRow, owner.device_id)).revoked_at is None
        assert present(session.get(UserSessionRow, owner.session_id)).revoked_at is None
    assert pairing_service.issue_recovery_invitation(owner.user_id, uuid4(), 60)["user_id"] == (
        str(owner.user_id)
    )


@pytest.mark.parametrize("action", ["logout_all", "revoke_device"])
def test_generic_auth_rejects_cached_actor_before_revoking_other_sessions(
    auth_runtime: AuthRuntime,
    database_connection: Connection[object],
    action: str,
) -> None:
    pair = _bootstrap(auth_runtime)
    other = _insert_user_device_session(
        database_connection, auth_runtime, display_name=None, existing_user_id=pair.user_id
    )
    database_connection.commit()
    actor = auth_runtime.service.authenticate_access(pair.access_token)
    auth_runtime.service.logout(actor)
    with pytest.raises(InvalidAccessTokenError):
        if action == "logout_all":
            auth_runtime.service.logout_all(actor)
        else:
            auth_runtime.service.revoke_device(actor, other.device_id)
    with Session(auth_runtime.engine) as session:
        assert present(session.get(UserSessionRow, other.session_id)).revoked_at is None
        assert present(session.get(DeviceRow, other.device_id)).revoked_at is None


@pytest.mark.parametrize("action", ["logout", "logout_all", "revoke_device"])
def test_generic_auth_revocation_holds_capacity_until_transfer_closes(
    auth_runtime: AuthRuntime,
    admission: AdmissionHarness,
    action: str,
) -> None:
    auth_runtime.clock.instant = datetime.now(UTC)
    pair = _bootstrap(auth_runtime)
    actor = auth_runtime.service.authenticate_access(pair.access_token)
    other = admission.actor()
    admission.budget(playbacks=1)
    active = admission.service.acquire(actor, play())
    recording = admission.recording(actor)
    admission.service.attach(actor, fence(active), 0, recording, None)
    permit = admission.service.open_io(actor, fence(active), recording)
    waiting = admission.service.acquire(other, play())
    if action == "revoke_device":
        auth_runtime.service.revoke_device(actor, actor.device_id)
    else:
        getattr(auth_runtime.service, action)(actor)
    with pytest.raises(ResourceAdmissionError):
        admission.service.renew_io(actor, permit)
    blocked = admission.service.poll(other, waiting.operation.request.operation_id)
    assert blocked.operation.state == AdmissionState.WAITING and blocked.usage.server == 1
    admission.service.close_io(permit)
    assert (
        admission.service.poll(other, waiting.operation.request.operation_id).operation.state
        == AdmissionState.ACTIVE
    )
