"""Real PostgreSQL proof for fair admission, reconnect fencing and bounded draining."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import (
    AudioVariantRow,
    DeviceRow,
    LibraryEntryRow,
    UploadSessionRow,
    UserAccountRow,
    UserSessionRow,
    UserTrackRefRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.adapters.postgresql.models.resource_admission import ResourceAdmissionRow
from autplay.adapters.postgresql.resource_admission import (
    SqlAlchemyResourceAdmissionUnitOfWorkFactory,
)
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.application.music_library import MusicLibraryService
from autplay.application.resource_admission import ResourceAdmissionService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.resource_admission import (
    ActivationFence,
    AdmissionState,
    AdmissionStatus,
    ResourceAdmissionError,
    ResourceKind,
    ResourceRequest,
)


@dataclass
class AdmissionHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    service: ResourceAdmissionService

    def actor(self, user_id: UUID | None = None, *, legacy: bool = False) -> Principal:
        now = datetime.now(UTC)
        user_id, device_id, session_id = user_id or uuid4(), uuid4(), uuid4()
        with self.sessions.begin() as session:
            if session.get(UserAccountRow, user_id) is None:
                session.add(UserAccountRow(user_id=user_id, display_name="Quota test", role="USER"))
                session.flush()
            session.add(
                DeviceRow(
                    device_id=device_id,
                    user_id=user_id,
                    device_name="Phone",
                    platform="ANDROID",
                    app_version="synthetic-test",
                )
            )
            session.flush()
            session.add(
                UserSessionRow(
                    session_id=session_id,
                    user_id=user_id,
                    device_id=device_id,
                    refresh_token_hash=hashlib.sha256(session_id.bytes).digest(),
                    issued_at=now,
                    expires_at=now + timedelta(days=90),
                    session_mode="LEGACY" if legacy else "V2",
                    family_id=None if legacy else session_id,
                    generation=0,
                )
            )
        return Principal(user_id, device_id, session_id, AccountRole.USER)

    def budget(self, playbacks: int = 2, transfers: int = 2, account_playbacks: int = 2) -> None:
        with self.sessions.begin() as session:
            lock_resource_admission(session)
            session.execute(
                text(
                    "UPDATE account.resource_quota_policy "
                    "SET global_playbacks=:p,global_transfers=:t,"
                    "playback_ceiling=16,transfer_ceiling=16,budget_evidence='synthetic-not-deployment',"
                    "default_playbacks=:a,revision=revision+1"
                ),
                {"p": playbacks, "t": transfers, "a": account_playbacks},
            )

    def recording(self, actor: Principal) -> UUID:
        with self.sessions.begin() as session:
            ref = UserTrackRefRow(
                user_id=actor.user_id, raw_title="Synthetic song", raw_artist="Test"
            )
            session.add(ref)
            session.flush()
            session.add(
                LibraryEntryRow(
                    user_id=actor.user_id,
                    user_track_ref_id=ref.user_track_ref_id,
                    source="LOCAL",
                    availability_status="LOCAL",
                )
            )
            ref_id = ref.user_track_ref_id
        return MusicLibraryService(self.sessions, None).prepare(actor, ref_id)

    def upload(self, actor: Principal) -> UUID:
        recording = self.recording(actor)
        upload_id = uuid4()
        with self.sessions.begin() as session:
            session.add(
                UploadSessionRow(
                    upload_session_id=upload_id,
                    user_id=actor.user_id,
                    device_id=actor.device_id,
                    target_recording_id=recording,
                    idempotency_key=str(upload_id),
                    request_hash=hashlib.sha256(upload_id.bytes).digest(),
                    expected_size=100,
                    chunk_size=1024,
                    max_chunks=1,
                    staging_key=upload_id.hex,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        return upload_id

    def variant(self, actor: Principal) -> UUID:
        recording = self.recording(actor)
        with self.sessions.begin() as session:
            obj = VaultObjectRow(
                sha256=hashlib.sha256(recording.bytes).digest(),
                byte_size=100,
                detected_mime_type="audio/flac",
                commit_status="COMMITTED",
                committed_at=datetime.now(UTC),
            )
            session.add(obj)
            session.flush()
            session.add(
                VaultReplicaRow(
                    vault_object_id=obj.vault_object_id,
                    storage_backend="LOCAL_FILESYSTEM",
                    storage_key=obj.sha256.hex(),
                    replica_status="AVAILABLE",
                    verified_at=datetime.now(UTC),
                )
            )
            variant = AudioVariantRow(
                recording_id=recording,
                vault_object_id=obj.vault_object_id,
                codec="flac",
                container="flac",
                sample_rate_hz=48000,
                channels=2,
                duration_ms=1000,
            )
            session.add(variant)
            session.flush()
            return variant.audio_variant_id


@pytest.fixture
def admission(database_url: str) -> Iterator[AdmissionHarness]:
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    harness = AdmissionHarness(
        engine,
        sessions,
        ResourceAdmissionService(
            SqlAlchemyResourceAdmissionUnitOfWorkFactory(sessions),
        ),
    )
    try:
        yield harness
    finally:
        engine.dispose()


def play() -> ResourceRequest:
    return ResourceRequest(uuid4(), ResourceKind.PLAYBACK, "PLAY_INSTANCE", uuid4())


def present[T](value: T | None) -> T:
    """Assert a fixture row exists before checking or changing its persisted fields."""
    assert value is not None
    return value


def fence(status: AdmissionStatus) -> ActivationFence:
    result = status.operation.fence
    assert status.operation.state == AdmissionState.ACTIVE and result is not None
    return result


def test_budget_required_then_exact_acquire_never_renews_and_binds_authority(
    admission: AdmissionHarness,
) -> None:
    actor, stranger = admission.actor(), admission.actor()
    request = play()
    with pytest.raises(ResourceAdmissionError, match="resource_budget_unconfigured"):
        admission.service.acquire(actor, request)
    admission.budget()
    first = admission.service.acquire(actor, request)
    replay = admission.service.acquire(actor, request)
    assert replay.operation.fence == first.operation.fence
    assert replay.operation.lease_until == first.operation.lease_until
    assert replay.usage.account == replay.usage.server == 1
    with pytest.raises(ResourceAdmissionError, match="resource_operation_conflict"):
        admission.service.acquire(actor, replace(request, resource_id=uuid4()))
    with pytest.raises(ResourceAdmissionError):
        admission.service.poll(stranger, request.operation_id)
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ResourceAdmissionRow)) == 1


def test_queue_skips_busy_phone_rotates_accounts_and_lowering_preserves_renewal(
    admission: AdmissionHarness,
) -> None:
    a = admission.actor()
    a_other, b, c = admission.actor(a.user_id), admission.actor(), admission.actor()
    admission.budget(playbacks=1)
    active = admission.service.acquire(a, play())
    same_phone = admission.service.acquire(a, play())
    other_phone = admission.service.acquire(a_other, play())
    b_wait = admission.service.acquire(b, play())
    c_wait = admission.service.acquire(c, play())
    assert same_phone.waiting_reason == "DEVICE_CAPACITY"
    admission.service.release(a, fence(active))
    # Previously unserved B and C precede A even though A enqueued earlier.
    b_active = admission.service.poll(b, b_wait.operation.request.operation_id)
    admission.service.release(b, fence(b_active))
    c_active = admission.service.poll(c, c_wait.operation.request.operation_id)
    assert other_phone.operation.state == AdmissionState.WAITING
    admission.budget(playbacks=3)
    admission.service.renew(c, fence(c_active))
    a_active = admission.service.poll(a, same_phone.operation.request.operation_id)
    a_second = admission.service.poll(a_other, other_phone.operation.request.operation_id)
    assert a_active.operation.state == a_second.operation.state == AdmissionState.ACTIVE
    admission.budget(playbacks=1, account_playbacks=1)
    assert admission.service.renew(a, fence(a_active)).usage.account == 2
    assert admission.service.renew(a_other, fence(a_second)).usage.server == 3
    waiting = admission.service.acquire(b, play())
    assert waiting.operation.state == AdmissionState.WAITING


def test_attachments_and_release_keep_capacity_until_permits_close(
    admission: AdmissionHarness,
) -> None:
    actor, other = admission.actor(), admission.actor()
    admission.budget(playbacks=1)
    one, two, three = (admission.recording(actor) for _ in range(3))
    foreign = admission.recording(other)
    active = admission.service.acquire(actor, play())
    token = fence(active)
    attached = admission.service.attach(actor, token, 0, one, two)
    assert admission.service.attach(actor, token, 0, one, two).operation.attachment_revision == 1
    with pytest.raises(ResourceAdmissionError, match="resource_target_unavailable"):
        admission.service.attach(actor, token, 1, one, foreign)
    p1 = admission.service.open_io(actor, token, one)
    p2 = admission.service.open_io(actor, token, two)
    p3 = admission.service.open_io(actor, token, one)
    with pytest.raises(ResourceAdmissionError, match="resource_io_busy"):
        admission.service.open_io(actor, token, two)
    with pytest.raises(ResourceAdmissionError, match="resource_attachment_draining"):
        admission.service.attach(actor, token, attached.operation.attachment_revision, two, three)
    admission.service.close_io(p1)
    admission.service.close_io(p3)
    changed = admission.service.attach(actor, token, 1, two, three)
    assert changed.operation.attachment_revision == 2
    queued = admission.service.acquire(other, play())
    released = admission.service.release(actor, token)
    assert released.usage.server == 1
    with pytest.raises(ResourceAdmissionError, match="resource_activation_stale"):
        admission.service.renew_io(actor, p2)
    assert (
        admission.service.poll(other, queued.operation.request.operation_id).operation.state
        == AdmissionState.WAITING
    )
    admission.service.close_io(p2)
    assert (
        admission.service.poll(other, queued.operation.request.operation_id).operation.state
        == AdmissionState.ACTIVE
    )


def test_upload_download_share_slots_and_exact_targets(admission: AdmissionHarness) -> None:
    actor, other = admission.actor(), admission.actor()
    admission.budget()
    upload, variant = admission.upload(actor), admission.variant(actor)
    up_request = ResourceRequest(uuid4(), ResourceKind.TRANSFER, "UPLOAD_INTENT", uuid4(), upload)
    down_request = ResourceRequest(
        uuid4(), ResourceKind.TRANSFER, "DOWNLOAD_INTENT", uuid4(), variant
    )
    up = admission.service.acquire(actor, up_request)
    down = admission.service.acquire(actor, down_request)
    assert down.usage.account == 2
    third = admission.service.acquire(
        actor, replace(up_request, operation_id=uuid4(), resource_id=uuid4())
    )
    assert third.operation.state == AdmissionState.WAITING
    assert admission.service.acquire(actor, play()).operation.state == AdmissionState.ACTIVE
    with pytest.raises(ResourceAdmissionError, match="resource_target_unavailable"):
        admission.service.acquire(other, replace(down_request, operation_id=uuid4()))
    with pytest.raises(ResourceAdmissionError, match="resource_target_mismatch"):
        admission.service.open_io(actor, fence(up), variant)
    permit = admission.service.open_io(actor, fence(down), variant)
    with pytest.raises(ResourceAdmissionError, match="resource_io_busy"):
        admission.service.open_io(actor, fence(down), variant)
    admission.service.close_io(permit)


def test_expiry_reactivation_and_cleanup_never_accept_old_fence(
    admission: AdmissionHarness,
) -> None:
    actor = admission.actor(legacy=True)
    admission.budget()
    request = play()
    old = admission.service.acquire(actor, request)
    old_fence = fence(old)
    with admission.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE account.resource_admission SET created_at=created_at-interval '1 minute',"
                "lease_until=clock_timestamp()-interval '1 second',"
                "claim_until=clock_timestamp()-interval '1 second'"
            )
        )
    current = admission.service.acquire(actor, request)
    assert fence(current).generation == old_fence.generation + 1
    assert fence(current).activation_id != old_fence.activation_id
    for action in (admission.service.renew, admission.service.release):
        with pytest.raises(ResourceAdmissionError, match="resource_activation_stale"):
            action(actor, old_fence)
    admission.service.release(actor, fence(current))
    with admission.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE account.resource_admission "
                "SET terminal_at=clock_timestamp()-interval '8 days'"
            )
        )
    assert admission.service.sweep() >= 1
    recreated = admission.service.acquire(actor, request)
    assert fence(recreated).generation == 1
    with pytest.raises(ResourceAdmissionError, match="resource_activation_stale"):
        admission.service.release(actor, old_fence)


def test_normal_rotation_preserves_lease_but_generation_and_logout_revoke_it(
    admission: AdmissionHarness,
) -> None:
    actor = admission.actor()
    admission.budget()
    active = admission.service.acquire(actor, play())
    next_session = uuid4()
    with admission.sessions.begin() as session:
        now = datetime.now(UTC)
        old = session.get(UserSessionRow, actor.session_id)
        assert old is not None
        old.revoked_at = now
        session.add(
            UserSessionRow(
                session_id=next_session,
                user_id=actor.user_id,
                device_id=actor.device_id,
                refresh_token_hash=hashlib.sha256(next_session.bytes).digest(),
                issued_at=now,
                expires_at=now + timedelta(days=90),
                session_mode="V2",
                family_id=actor.session_id,
                generation=1,
            )
        )
    rotated = replace(actor, session_id=next_session)
    assert admission.service.renew(rotated, fence(active)).operation.fence == fence(active)
    with pytest.raises(ResourceAdmissionError):
        admission.service.renew(actor, fence(active))
    with admission.sessions.begin() as session:
        account = session.get(UserAccountRow, actor.user_id)
        assert account is not None
        account.authority_generation += 1
    with pytest.raises(ResourceAdmissionError):
        admission.service.renew(rotated, fence(active))


def _process_acquire(args: tuple[str, Principal]) -> str:
    url, actor = args
    engine = create_engine(url)
    try:
        service = ResourceAdmissionService(
            SqlAlchemyResourceAdmissionUnitOfWorkFactory(sessionmaker(engine))
        )
        return service.acquire(actor, play()).operation.state.value
    finally:
        engine.dispose()


def test_expired_reacquire_joins_after_existing_waiter_and_poll_keeps_position(
    admission: AdmissionHarness,
) -> None:
    actor, blocker = admission.actor(), admission.actor()
    admission.budget(playbacks=1)
    old_request = play()
    old = admission.service.acquire(actor, old_request)
    with admission.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE account.resource_admission SET created_at=created_at-interval '1 minute',"
                "lease_until=clock_timestamp()-interval '1 second',"
                "claim_until=clock_timestamp()-interval '1 second'"
            )
        )
    blocking = admission.service.acquire(blocker, play())
    older_waiter = admission.service.acquire(actor, play())
    requeued = admission.service.acquire(actor, old_request)
    assert requeued.operation.enqueued_at > older_waiter.operation.enqueued_at
    assert requeued.operation.created_at < old.operation.created_at
    assert (
        admission.service.poll(actor, old_request.operation_id).operation.enqueued_at
        == requeued.operation.enqueued_at
    )
    assert (
        admission.service.acquire(actor, old_request).operation.enqueued_at
        == requeued.operation.enqueued_at
    )
    admission.service.release(blocker, fence(blocking))
    assert (
        admission.service.poll(actor, older_waiter.operation.request.operation_id).operation.state
        == AdmissionState.ACTIVE
    )
    assert (
        admission.service.poll(actor, old_request.operation_id).operation.state
        == AdmissionState.WAITING
    )


def test_reactivation_waits_for_old_io_and_stale_close_cannot_close_new_permit(
    admission: AdmissionHarness,
) -> None:
    actor = admission.actor()
    admission.budget(playbacks=1)
    recording = admission.recording(actor)
    request = play()
    old = admission.service.acquire(actor, request)
    admission.service.attach(actor, fence(old), 0, recording, None)
    old_permit = admission.service.open_io(actor, fence(old), recording)
    with admission.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE account.resource_admission SET created_at=created_at-interval '1 minute',"
                "lease_until=clock_timestamp()-interval '1 second'"
            )
        )
    pending = admission.service.acquire(actor, request)
    assert pending.operation.state == AdmissionState.WAITING and pending.usage.server == 1
    with pytest.raises(ResourceAdmissionError, match="resource_activation_stale"):
        admission.service.renew(actor, fence(old))
    admission.service.close_io(old_permit)
    successor = admission.service.poll(actor, request.operation_id)
    assert fence(successor).generation == fence(old).generation + 1
    admission.service.attach(
        actor, fence(successor), successor.operation.attachment_revision, recording, None
    )
    current_permit = admission.service.open_io(actor, fence(successor), recording)
    admission.service.close_io(old_permit)
    assert admission.service.renew_io(actor, current_permit).permit_id == current_permit.permit_id


def test_permit_deadline_cannot_be_revived_and_queue_is_bounded(
    admission: AdmissionHarness,
) -> None:
    actor = admission.actor()
    admission.budget(playbacks=1)
    recording = admission.recording(actor)
    current = admission.service.acquire(actor, play())
    admission.service.attach(actor, fence(current), 0, recording, None)
    permit = admission.service.open_io(actor, fence(current), recording)
    with admission.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE account.resource_io_permit "
                "SET opened_at=clock_timestamp()-interval '8 seconds',"
                "renewed_at=clock_timestamp()-interval '6 seconds',"
                "expires_at=clock_timestamp()-interval '2 seconds'"
            )
        )
    with pytest.raises(ResourceAdmissionError, match="resource_io_stale"):
        admission.service.renew_io(actor, permit)
    waiters = [admission.service.acquire(actor, play()) for _ in range(20)]
    with pytest.raises(ResourceAdmissionError, match="resource_queue_full"):
        admission.service.acquire(actor, play())
    admission.service.cancel_waiting(
        actor, waiters[0].operation.request.operation_id, waiters[0].operation.request_sha256
    )
    assert admission.service.acquire(actor, play()).operation.state == AdmissionState.WAITING


def test_separate_processes_cannot_overshoot_shared_capacity(
    admission: AdmissionHarness,
    database_url: str,
) -> None:
    admission.budget(playbacks=2)
    actors = [admission.actor() for _ in range(8)]
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as executor:
        states = list(executor.map(_process_acquire, [(database_url, actor) for actor in actors]))
    assert states.count("ACTIVE") == 2
    assert states.count("WAITING") == 6
