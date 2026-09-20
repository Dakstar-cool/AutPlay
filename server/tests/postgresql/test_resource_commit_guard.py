"""Real PostgreSQL upload-finalization authority and nonblocking lock order."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models import (
    DeviceRow,
    LibraryEntryRow,
    UploadSessionRow,
    UserAccountRow,
    UserSessionRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.audit import CatalogChangeSetRow
from autplay.adapters.postgresql.models.catalog import RecordingRow
from autplay.adapters.postgresql.models.identity import RecordingRedirectRow
from autplay.adapters.postgresql.models.resource_admission import ResourceAdmissionRow
from autplay.adapters.postgresql.resource_commit_guard import require_device_upload_commit
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.domain.auth import Principal
from autplay.domain.resource_admission import (
    IoPermit,
    ResourceAdmissionError,
    ResourceKind,
    ResourceRequest,
)
from sqlalchemy import event, select, text

from .test_resource_admission_runtime import AdmissionHarness, fence, present
from .test_resource_admission_runtime import admission as admission


def _upload(
    admission: AdmissionHarness, *, legacy: bool = False
) -> tuple[Principal, UUID, IoPermit]:
    actor = admission.actor(legacy=legacy)
    admission.budget()
    upload = admission.upload(actor)
    operation = admission.service.acquire(
        actor, ResourceRequest(uuid4(), ResourceKind.TRANSFER, "UPLOAD_INTENT", uuid4(), upload)
    )
    return actor, upload, admission.service.open_io(actor, fence(operation), upload)


def _write(
    admission: AdmissionHarness,
    actor: Principal,
    upload: UUID,
    permit: IoPermit,
    *,
    stopped: Callable[[], bool] = lambda: False,
    flush: bool = True,
) -> None:
    with admission.sessions.begin() as session:
        row = present(session.get(UploadSessionRow, upload, with_for_update=True))
        row.received_size = 1
        if flush:
            session.flush()
        require_device_upload_commit(session, actor, upload, permit, stopped=stopped)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("flush", [False, True])
def test_valid_upload_guard_commits_without_admission_mutation(
    admission: AdmissionHarness, legacy: bool, flush: bool
) -> None:
    actor, upload, permit = _upload(admission, legacy=legacy)
    _write(admission, actor, upload, permit, flush=flush)
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, upload)).received_size == 1
        assert present(session.get(ResourceAdmissionRow, permit.fence.operation_id)).generation == 1


@pytest.mark.parametrize(
    "gate",
    [
        "entry",
        "ref_deleted",
        "recording",
        "redirect",
        "upload_expiry",
        "sealed",
    ],
)
def test_lost_target_authorization_rolls_back_chunk(admission: AdmissionHarness, gate: str) -> None:
    actor, upload, permit = _upload(admission)
    redirect_target = admission.recording(actor) if gate == "redirect" else None
    with admission.sessions.begin() as session:
        row = present(session.get(UploadSessionRow, upload))
        ref = present(
            session.scalar(
                select(UserTrackRefRow).where(
                    UserTrackRefRow.recording_id == row.target_recording_id,
                    UserTrackRefRow.user_id == actor.user_id,
                )
            )
        )
        if gate == "entry":
            entry = present(
                session.scalar(
                    select(LibraryEntryRow).where(
                        LibraryEntryRow.user_track_ref_id == ref.user_track_ref_id,
                    )
                )
            )
            entry.removed_at = datetime.now(UTC)
        elif gate == "ref_deleted":
            ref.deleted_at = datetime.now(UTC)
        elif gate == "recording":
            present(session.get(RecordingRow, row.target_recording_id)).deleted_at = datetime.now(
                UTC
            )
        elif gate == "redirect":
            change = CatalogChangeSetRow(operation_type="MERGE", actor_type="SYSTEM", reason="test")
            session.add(change)
            session.flush()
            session.add(
                RecordingRedirectRow(
                    source_recording_id=row.target_recording_id,
                    target_recording_id=redirect_target,
                    change_set_id=change.change_set_id,
                    reason="test",
                )
            )
        elif gate == "upload_expiry":
            row.created_at = datetime.now(UTC) - timedelta(hours=1)
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            row.state, row.sealed_at = "SEALED", datetime.now(UTC)
    with pytest.raises(ResourceAdmissionError, match="resource_io_stale"):
        _write(admission, actor, upload, permit)
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, upload)).received_size == 0


def test_alternate_live_library_path_preserves_upload_authority(
    admission: AdmissionHarness,
) -> None:
    actor, upload, permit = _upload(admission)
    with admission.sessions.begin() as session:
        row = present(session.get(UploadSessionRow, upload))
        ref = present(
            session.scalar(
                select(UserTrackRefRow).where(
                    UserTrackRefRow.recording_id == row.target_recording_id,
                )
            )
        )
        entry = present(
            session.scalar(
                select(LibraryEntryRow).where(
                    LibraryEntryRow.user_track_ref_id == ref.user_track_ref_id,
                )
            )
        )
        entry.removed_at = datetime.now(UTC)
        # The live-ref uniqueness constraint preserves this ref. Re-adding the
        # track creates a new active entry after the old entry's removal.
        session.flush()
        session.add(
            LibraryEntryRow(
                user_id=actor.user_id,
                user_track_ref_id=ref.user_track_ref_id,
                source="LOCAL",
                availability_status="LOCAL",
            )
        )
    _write(admission, actor, upload, permit)


@pytest.mark.parametrize("target", ["entry", "recording"])
def test_busy_target_proof_fails_without_waiting(admission: AdmissionHarness, target: str) -> None:
    actor, upload, permit = _upload(admission)
    with admission.sessions.begin() as updating:
        row = present(updating.get(UploadSessionRow, upload))
        if target == "recording":
            present(updating.get(RecordingRow, row.target_recording_id, with_for_update=True))
        else:
            present(
                updating.scalar(
                    select(LibraryEntryRow)
                    .where(
                        LibraryEntryRow.user_id == actor.user_id,
                    )
                    .with_for_update()
                )
            )
        with pytest.raises(ResourceAdmissionError, match="resource_commit_busy"):
            _write(admission, actor, upload, permit)


@pytest.mark.parametrize("gate", ["device", "session", "generation", "lease", "permit", "fence"])
def test_failed_precommit_guard_rolls_back_staged_chunk_metadata(
    admission: AdmissionHarness, gate: str
) -> None:
    actor, upload, permit = _upload(admission)
    with admission.sessions.begin() as session:
        if gate == "device":
            present(session.get(DeviceRow, actor.device_id)).revoked_at = datetime.now(UTC)
        elif gate == "session":
            present(session.get(UserSessionRow, actor.session_id)).revoked_at = datetime.now(UTC)
        elif gate == "generation":
            present(session.get(UserAccountRow, actor.user_id)).authority_generation += 1
        elif gate == "lease":
            session.execute(
                text(
                    "UPDATE account.resource_admission "
                    "SET created_at=created_at-interval '1 minute',"
                    "lease_until=clock_timestamp()-interval '1 second'"
                )
            )
        elif gate == "permit":
            session.execute(
                text(
                    "UPDATE account.resource_io_permit "
                    "SET opened_at=opened_at-interval '10 seconds',"
                    "renewed_at=renewed_at-interval '10 seconds',"
                    "expires_at=clock_timestamp()-interval '6 seconds'"
                )
            )
        else:
            permit = replace(permit, fence=replace(permit.fence, activation_id=uuid4()))
    with pytest.raises(ResourceAdmissionError, match="resource_io_stale"):
        _write(admission, actor, upload, permit)
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, upload)).received_size == 0


def test_stop_observed_after_locks_prevents_late_commit(admission: AdmissionHarness) -> None:
    actor, upload, permit = _upload(admission)
    calls = 0

    def stopped() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    with pytest.raises(ResourceAdmissionError, match="resource_io_stale"):
        _write(admission, actor, upload, permit, stopped=stopped)
    assert calls == 2
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, upload)).received_size == 0


def test_nowait_guard_breaks_upload_account_lock_inversion(admission: AdmissionHarness) -> None:
    actor, upload, permit = _upload(admission)
    account_locked, upload_locked = Event(), Event()

    def revoker() -> None:
        with admission.sessions.begin() as session:
            lock_resource_admission(session)
            present(session.get(UserAccountRow, actor.user_id, with_for_update=True))
            account_locked.set()
            assert upload_locked.wait(2)
            present(session.get(UploadSessionRow, upload, with_for_update=True))

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(revoker)
        try:
            assert account_locked.wait(2)
            with (
                pytest.raises(ResourceAdmissionError, match="resource_commit_busy"),
                admission.sessions.begin() as session,
            ):
                row = present(session.get(UploadSessionRow, upload, with_for_update=True))
                row.received_size = 1
                session.flush()
                upload_locked.set()
                require_device_upload_commit(session, actor, upload, permit, stopped=lambda: False)
        finally:
            upload_locked.set()
        future.result(timeout=2)
    with admission.sessions() as session:
        assert present(session.get(UploadSessionRow, upload)).received_size == 0


def test_shared_permit_lock_keeps_close_after_commit(admission: AdmissionHarness) -> None:
    actor, upload, permit = _upload(admission)
    attempted = Event()

    def before_execute(
        _conn: object,
        _cursor: object,
        statement: str,
        _params: object,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.startswith("DELETE FROM account.resource_io_permit"):
            attempted.set()

    event.listen(admission.engine, "before_cursor_execute", before_execute)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with admission.sessions.begin() as session:
                present(
                    session.get(UploadSessionRow, upload, with_for_update=True)
                ).received_size = 1
                session.flush()
                require_device_upload_commit(session, actor, upload, permit, stopped=lambda: False)
                closing = pool.submit(admission.service.close_io, permit)
                assert attempted.wait(2)
                with pytest.raises(TimeoutError):
                    closing.result(timeout=0.1)
            closing.result(timeout=2)
    finally:
        event.remove(admission.engine, "before_cursor_execute", before_execute)
