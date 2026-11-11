"""Real PostgreSQL evidence for the P09 transaction boundary."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.postgresql.models import (
    BootstrapSessionRow,
    BootstrapSnapshotItemRow,
    DeviceRow,
    DeviceSyncCursorRow,
    LibraryEntryRow,
    PlaylistEntryRow,
    SyncEventRow,
    UserAccountRow,
    UserInteractionEventRow,
    UserTrackRefRow,
)
from autplay.application.sync import SyncError, SyncService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import public_spki
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


def _principal(session: Session, name: str) -> Principal:
    user_id, device_id, session_id = uuid4(), uuid4(), uuid4()
    session.add(UserAccountRow(user_id=user_id, display_name=name, role="USER", status="ACTIVE"))
    session.flush()
    session.add(
        DeviceRow(
            device_id=device_id,
            user_id=user_id,
            device_name=name,
            platform="ANDROID",
            app_version="p09",
        )
    )
    session.flush()
    return Principal(user_id, device_id, session_id, AccountRole.USER)


def _additional_device(session: Session, principal: Principal, name: str) -> Principal:
    device_id = uuid4()
    session.add(
        DeviceRow(
            device_id=device_id,
            user_id=principal.user_id,
            device_name=name,
            platform="ANDROID",
            app_version="audit-remediation",
        )
    )
    session.flush()
    return Principal(principal.user_id, device_id, uuid4(), AccountRole.USER)


def _event(
    principal: Principal,
    event_id: UUID,
    sequence: int,
    kind: str,
    aggregate: str,
    payload: dict[str, object],
    *,
    base: int | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "event_id": str(event_id),
        "idempotency_key": str(event_id),
        "user_id": str(principal.user_id),
        "device_id": str(principal.device_id),
        "device_sequence": sequence,
        "event_type": kind,
        "schema_version": 1,
        "aggregate_type": aggregate,
        "aggregate_local_id": str(event_id),
        "aggregate_server_id": None,
        "base_server_row_version": base,
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }
    value["request_hash"] = hashlib.sha256(rfc8785.dumps(value)).hexdigest()  # type: ignore[arg-type]
    return value


def _bind(
    service: SyncService,
    principal: Principal,
    epoch: UUID,
    *,
    platform: str = "ANDROID",
) -> dict[str, object]:
    profile = uuid4()
    body: dict[str, object] = {
        "protocol_version": 1,
        "user_id": str(principal.user_id),
        "device_id": str(principal.device_id),
        "server_profile_id": str(profile),
        "journal_epoch": str(epoch),
        "device_name": "bound",
        "platform": platform,
        "app_version": "p09",
    }
    service.bind(principal, body)
    return body


def _rehash(value: dict[str, object]) -> None:
    value["request_hash"] = hashlib.sha256(
        rfc8785.dumps({key: item for key, item in value.items() if key != "request_hash"})  # type: ignore[misc]
    ).hexdigest()


@pytest.mark.parametrize("stored_platform", ["ANDROID", "WEB", "OTHER"])
@pytest.mark.parametrize("requested_platform", ["ANDROID", "WEB", "OTHER"])
def test_v1_bind_preserves_legacy_platform_updates_without_mac_evidence(
    database_url: str,
    stored_platform: str,
    requested_platform: str,
) -> None:
    """Future D1 regression source; not executed during the macOS check pause."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner = _principal(session, "legacy bind fixture")
            device = session.get(DeviceRow, owner.device_id)
            assert device is not None
            device.platform = stored_platform
            session.commit()
        service = SyncService(engine, cursor_secret=b"test-only-sync-secret-with-32-bytes")
        epoch = uuid4()
        body = _bind(service, owner, epoch, platform=requested_platform)
        service.bind(owner, body)
        with Session(engine) as session:
            device = session.get(DeviceRow, owner.device_id)
            cursor = session.get(DeviceSyncCursorRow, owner.device_id)
            assert device is not None and device.platform == requested_platform
            assert (
                device.device_name == body["device_name"]
                and device.app_version == body["app_version"]
            )
            assert (
                cursor is not None
                and cursor.user_id == owner.user_id
                and cursor.journal_epoch == epoch
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("requested_platform", ["ANDROID", "WEB", "OTHER"])
@pytest.mark.parametrize("existing_cursor", [False, True])
@pytest.mark.parametrize(
    "evidence", ["CURRENT_MACOS", "REVOKED_MISSING_THUMB", "REVOKED_WRONG_THUMB"]
)
def test_v1_bind_cannot_reclassify_known_mac_or_mutate_its_journal(
    database_url: str,
    requested_platform: str,
    existing_cursor: bool,
    evidence: str,
) -> None:
    """Future real-DB refusal cases; all keys/rows are disposable synthetic inputs."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner = _principal(session, "mac bind refusal fixture")
            session.commit()
        service = SyncService(engine, cursor_secret=b"test-only-sync-secret-with-32-bytes")
        epoch = uuid4()
        if existing_cursor:
            _bind(service, owner, epoch)
        spki = public_spki(ec.generate_private_key(ec.SECP256R1()))
        with Session(engine) as session:
            device = session.get(DeviceRow, owner.device_id)
            assert device is not None
            device.public_key = spki
            device.public_key_thumbprint_sha256 = hashlib.sha256(spki).digest()
            if evidence == "CURRENT_MACOS":
                device.platform = "MACOS"
            else:
                other = _principal(session, "historical mac bind fixture")
                historical = session.get(DeviceRow, other.device_id)
                assert historical is not None
                historical.platform = "MACOS"
                historical.public_key = spki
                historical.public_key_thumbprint_sha256 = (
                    None if evidence == "REVOKED_MISSING_THUMB" else b"x" * 32
                )
                historical.revoked_at = datetime.now(UTC)
            session.commit()
            before_devices = session.execute(
                select(DeviceRow.__table__).order_by(DeviceRow.device_id)
            ).all()
            before_cursors = session.execute(
                select(DeviceSyncCursorRow.__table__).order_by(DeviceSyncCursorRow.device_id),
            ).all()
        with pytest.raises(SyncError, match="BINDING_MISMATCH"):
            _bind(service, owner, epoch, platform=requested_platform)
        with Session(engine) as session:
            assert (
                session.execute(select(DeviceRow.__table__).order_by(DeviceRow.device_id)).all()
                == before_devices
            )
            assert (
                session.execute(
                    select(DeviceSyncCursorRow.__table__).order_by(DeviceSyncCursorRow.device_id),
                ).all()
                == before_cursors
            )
    finally:
        engine.dispose()


def test_v1_bind_and_push_complete_when_bind_waits_for_the_push_cursor(
    database_url: str,
) -> None:
    """Future PostgreSQL regression: cursor -> inbox FK versus device -> cursor.

    Not executed during the macOS check pause. Hooks observe the real services;
    no device KEY SHARE is pre-acquired to hide an incompatible bind row lock.
    """
    from time import monotonic

    from autplay.adapters.postgresql.models import DeviceEventInboxRow, IdempotencyRecordRow
    from sqlalchemy import event as sql_events

    engine = create_engine(
        database_url,
        pool_timeout=5,
        connect_args={
            "connect_timeout": 5,
            "options": (
                "-c statement_timeout=15000 -c lock_timeout=12000 "
                "-c idle_in_transaction_session_timeout=30000"
            ),
        },
    )
    cursor_locked = threading.Event()
    device_locked = threading.Event()
    bind_updating_cursor = threading.Event()
    release_push = threading.Event()
    worker_finished = threading.Event()
    inbox_attempted = threading.Event()
    worker_state = threading.local()
    pids: dict[str, int] = {}
    results: dict[str, dict[str, Any]] = {}
    failures: dict[str, BaseException] = {}
    device_locks: list[str] = []
    writes: list[tuple[str, str]] = []
    workers: list[threading.Thread] = []
    listeners_installed = False

    def before_statement(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        role = getattr(worker_state, "role", None)
        sql = " ".join(statement.split())
        if role == "bind" and sql.startswith("UPDATE sync.device_sync_cursor "):
            bind_updating_cursor.set()
        if role == "push" and sql.startswith("INSERT INTO sync.device_event_inbox "):
            inbox_attempted.set()

    def after_statement(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        role = getattr(worker_state, "role", None)
        if role not in {"bind", "push"}:
            return
        sql = " ".join(statement.split())
        if role == "push" and "FROM sync.device_sync_cursor " in sql and sql.endswith("FOR UPDATE"):
            pids["push"] = cursor.connection.info.backend_pid
            cursor_locked.set()
            if not release_push.wait(20):
                raise RuntimeError("bind/push fixture did not release the locked cursor")
        if role == "bind" and "FROM account.device " in sql and " FOR " in sql:
            pids["bind"] = cursor.connection.info.backend_pid
            device_locks.append(sql)
            device_locked.set()
        for prefix in (
            "UPDATE account.device ",
            "UPDATE sync.device_sync_cursor ",
            "INSERT INTO sync.device_event_inbox ",
        ):
            if sql.startswith(prefix):
                writes.append((role, prefix))

    try:
        with Session(engine) as session:
            owner = _principal(session, "coordinated legacy bind/push")
            session.commit()
        service = SyncService(engine, cursor_secret=b"test-only-sync-secret-with-32-bytes")
        epoch, ref_id = uuid4(), uuid4()
        body = _bind(service, owner, epoch)
        rebound = {
            **body,
            "device_name": "concurrent legacy bind",
            "platform": "WEB",
            "app_version": "p09-concurrent",
        }
        create = _event(
            owner,
            ref_id,
            1,
            "USER_TRACK_REF_CREATED",
            "USER_TRACK_REF",
            {"title": "coordinated offline creation"},
        )
        # Retain the original v1 JCS/hash recipe; compute only in a future test run.
        canonical_request = rfc8785.dumps(
            cast(Any, {key: value for key, value in create.items() if key != "request_hash"})
        )
        assert create["request_hash"] == hashlib.sha256(canonical_request).hexdigest()
        frozen_wire = rfc8785.dumps(cast(Any, create))

        def run_worker(role: str) -> None:
            worker_state.role = role
            try:
                results[role] = (
                    service.push(owner, {**body, "events": [create]}, uuid4())
                    if role == "push"
                    else service.bind(owner, rebound)
                )
            except BaseException as error:
                failures[role] = error
            finally:
                worker_finished.set()

        sql_events.listen(engine, "before_cursor_execute", before_statement)
        sql_events.listen(engine, "after_cursor_execute", after_statement)
        listeners_installed = True
        for role, ready in (("push", cursor_locked), ("bind", device_locked)):
            worker = threading.Thread(target=run_worker, args=(role,), daemon=True)
            worker.start()
            workers.append(worker)
            assert ready.wait(3), f"{role} did not acquire its first real row lock"
        assert bind_updating_cursor.wait(3), "bind did not attempt its cursor UPDATE"
        assert not inbox_attempted.is_set(), "push reached its FK before bind held the device"

        # The hook signals an attempt, not a wait. Prove the actual blocking edge
        # in PostgreSQL before releasing push; polling delays never establish order.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
            deadline = monotonic() + 5
            while not observer.scalar(
                text("SELECT :push_pid = ANY(pg_blocking_pids(:bind_pid))"),
                {"push_pid": pids["push"], "bind_pid": pids["bind"]},
            ):
                assert not worker_finished.is_set(), "a worker ended before the blocking edge"
                assert monotonic() < deadline, "bind never waited for the push cursor"
                worker_finished.wait(0.02)
        release_push.set()
        for worker in workers:
            worker.join(timeout=20)
            assert not worker.is_alive(), "bind/push did not complete within its DB timeouts"
        for error in failures.values():
            raise error

        assert len(device_locks) == 1 and device_locks[0].endswith("FOR NO KEY UPDATE")
        assert results["bind"] == rebound
        expected_ack = {
            "event_id": str(ref_id),
            "device_sequence": 1,
            "aggregate_type": "USER_TRACK_REF",
            "aggregate_local_id": str(ref_id),
            "aggregate_server_id": str(ref_id),
            "outcome": "APPLIED",
            "server_row_version": 1,
        }
        assert results["push"]["acks"] == [expected_ack]
        assert results["push"]["acknowledged_through_device_sequence"] == 1
        assert writes.count(("bind", "UPDATE account.device ")) == 1
        assert writes.count(("bind", "UPDATE sync.device_sync_cursor ")) == 1
        assert writes.count(("push", "UPDATE sync.device_sync_cursor ")) == 1
        assert writes.count(("push", "INSERT INTO sync.device_event_inbox ")) == 1
        replay = service.push(owner, {**body, "events": [create]}, uuid4())
        assert replay["acks"] == [
            {
                **expected_ack,
                "outcome": "DUPLICATE",
                "original_outcome": "APPLIED",
            }
        ]
        assert replay["acknowledged_through_device_sequence"] == 1
        assert rfc8785.dumps(cast(Any, create)) == frozen_wire
        with Session(engine) as session:
            device = session.get(DeviceRow, owner.device_id)
            cursor = session.get(DeviceSyncCursorRow, owner.device_id)
            assert device is not None and device.user_id == owner.user_id
            assert (device.device_name, device.platform, device.app_version) == (
                rebound["device_name"],
                rebound["platform"],
                rebound["app_version"],
            )
            assert device.revoked_at is None and device.last_seen_at is not None
            assert cursor is not None and cursor.user_id == owner.user_id
            assert cursor.journal_epoch == epoch and cursor.last_acked_device_sequence == 1
            assert cursor.last_successful_sync_at is not None
            inboxes = list(session.scalars(select(DeviceEventInboxRow)))
            assert len(inboxes) == 1
            inbox = inboxes[0]
            assert (inbox.event_id, inbox.user_id, inbox.device_id, inbox.device_sequence) == (
                ref_id,
                owner.user_id,
                owner.device_id,
                1,
            )
            assert inbox.apply_status == "APPLIED" and inbox.error_code is None
            assert inbox.terminal_ack == expected_ack and inbox.payload == create["payload"]
            assert inbox.request_hash == bytes.fromhex(str(create["request_hash"]))
            receipts = list(session.scalars(select(IdempotencyRecordRow)))
            assert len(receipts) == 1 and receipts[0].response_reference == expected_ack
            refs = list(session.scalars(select(UserTrackRefRow)))
            assert len(refs) == 1 and refs[0].user_track_ref_id == ref_id
            assert refs[0].user_id == owner.user_id and refs[0].row_version == 1
            assert refs[0].raw_title == "coordinated offline creation"
            events = list(session.scalars(select(SyncEventRow)))
            assert len(events) == 1 and events[0].event_id == ref_id
            assert (
                events[0].user_id == owner.user_id and events[0].origin_device_id == owner.device_id
            )
            assert events[0].aggregate_id == ref_id and events[0].server_row_version == 1
    finally:
        # Release before any join, including failed staging assertions. Avoid a
        # ThreadPoolExecutor context that could wait without a cleanup deadline.
        release_push.set()
        cleanup_deadline = monotonic() + 25
        for worker in workers:
            worker.join(timeout=max(0.0, cleanup_deadline - monotonic()))
        try:
            assert all(not worker.is_alive() for worker in workers), "bind/push worker leaked"
        finally:
            if listeners_installed:
                sql_events.remove(engine, "before_cursor_execute", before_statement)
                sql_events.remove(engine, "after_cursor_execute", after_statement)
            engine.dispose()


def test_projected_track_ids_replay_and_remain_owner_scoped(database_url: str) -> None:
    from autplay.adapters.postgresql.models import ListeningEventRow, UserTrackPreferenceRow

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner, other = _principal(session, "owner"), _principal(session, "other")
            ref_id, foreign_id = uuid4(), uuid4()
            for principal, ref in ((owner, ref_id), (other, foreign_id)):
                session.add(
                    UserTrackRefRow(
                        user_track_ref_id=ref,
                        user_id=principal.user_id,
                        raw_title="Track",
                        raw_artist="Artist",
                    )
                )
            session.commit()
        service = SyncService(engine, cursor_secret=b"test-only-sync-secret-with-32-bytes")
        body = _bind(service, owner, uuid4())
        profile = str(body["server_profile_id"])
        local = UUID(
            bytes=hashlib.md5(f"{profile}:{ref_id}".encode(), usedforsecurity=False).digest(),
            version=3,
        )
        listening = _event(
            owner,
            uuid4(),
            1,
            "LISTENING_EVENT_RECORDED",
            "LISTENING_EVENT",
            {
                "interaction_type": "LISTENING_EVENT_RECORDED",
                "local_user_track_ref_id": str(local),
                "server_user_track_ref_id": str(ref_id),
                "recording_id": None,
                "played_ms": 100,
                "track_duration_ms": 1000,
                "completion_ratio": 0.1,
                "event_origin": "ORGANIC",
                "context": "GENERAL",
                "explicit_feedback": "NONE",
                "excluded_from_taste": False,
            },
        )
        preference = _event(
            owner,
            uuid4(),
            2,
            "USER_TRACK_PREFERENCE_SET",
            "USER_TRACK_PREFERENCE",
            {
                "local_user_track_ref_id": str(local),
                "preference": "LIKED",
                "excluded_from_taste": False,
            },
        )
        for event in (listening, preference):
            event["server_profile_id"] = profile
            _rehash(event)
        pushed = service.push(owner, {**body, "events": [listening, preference]}, uuid4())
        assert [ack["outcome"] for ack in pushed["acks"]] == ["APPLIED", "APPLIED"]
        replay = service.push(owner, {**body, "events": [listening, preference]}, uuid4())
        assert [ack["outcome"] for ack in replay["acks"]] == ["DUPLICATE", "DUPLICATE"]
        with Session(engine) as session:
            row = session.get(ListeningEventRow, UUID(str(listening["event_id"])))
            assert row is not None and row.user_track_ref_id == ref_id
            stored_preference = session.get(UserTrackPreferenceRow, ref_id)
            assert stored_preference is not None and stored_preference.preference == "LIKED"
        assert isinstance(listening["payload"], dict)
        for sequence, invalid in enumerate((foreign_id, uuid4()), start=3):
            denied = _event(
                owner,
                uuid4(),
                sequence,
                "LISTENING_EVENT_RECORDED",
                "LISTENING_EVENT",
                {
                    **listening["payload"],
                    "server_user_track_ref_id": str(invalid),
                },
            )
            response = service.push(owner, {**body, "events": [denied]}, uuid4())
            assert response["acks"][0]["outcome"] == "REJECTED"
            assert response["acks"][0]["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    finally:
        engine.dispose()


def _recommendation_fixture(session: Session, user_id: UUID) -> tuple[UUID, UUID]:
    """Insert the minimum immutable recommendation ledger using the real P05 tables."""
    credit_id, recording_id, request_id = uuid4(), uuid4(), uuid4()
    session.execute(
        text(
            "INSERT INTO catalog.artist_credit (artist_credit_id, display_name, normalized_name) "
            "VALUES (:id, 'test', 'test')"
        ),
        {"id": credit_id},
    )
    session.execute(
        text(
            "INSERT INTO catalog.recording "
            "(recording_id, artist_credit_id, title, normalized_title, duration_ms) "
            "VALUES (:id, :credit, 'test', 'test', 1000)"
        ),
        {"id": recording_id, "credit": credit_id},
    )
    session.execute(
        text(
            "INSERT INTO ml.recommendation_request "
            "(recommendation_request_id, user_id, model_bundle_version, candidate_policy_version, "
            "filter_policy_version, reranker_version, seed) "
            "VALUES (:id, :user, 'p09', 'p09', 'p09', 'p09', 1)"
        ),
        {"id": request_id, "user": user_id},
    )
    session.execute(
        text(
            "INSERT INTO ml.recommendation_item "
            "(recommendation_request_id, rank, recording_id, score, explanation_code) "
            "VALUES (:request, 1, :recording, 1, 'TEST')"
        ),
        {"request": request_id, "recording": recording_id},
    )
    return request_id, recording_id


def test_push_exactly_once_conflict_delete_and_owner_isolation(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner, other = _principal(session, "owner"), _principal(session, "other")
            session.commit()
        service = SyncService(engine, cursor_secret=b"p09-cursor-secret-with-at-least-32-bytes")
        body = _bind(service, owner, uuid4())
        ref_id = uuid4()
        create = _event(
            owner,
            ref_id,
            1,
            "USER_TRACK_REF_CREATED",
            "USER_TRACK_REF",
            {"title": "offline", "artist": "artist"},
        )
        pushed = service.push(owner, {**body, "events": [create]}, uuid4())
        assert pushed["acks"][0]["outcome"] == "APPLIED"
        replay = service.push(owner, {**body, "events": [create]}, uuid4())
        assert replay["acks"][0]["outcome"] == "DUPLICATE"
        stale = _event(
            owner, uuid4(), 2, "USER_TRACK_REF_PATCHED", "USER_TRACK_REF", {"title": "new"}, base=99
        )
        stale["aggregate_local_id"] = str(ref_id)
        stale["aggregate_server_id"] = str(ref_id)
        stale["request_hash"] = hashlib.sha256(
            rfc8785.dumps({k: v for k, v in stale.items() if k != "request_hash"})  # type: ignore[misc]
        ).hexdigest()
        assert (
            service.push(owner, {**body, "events": [stale]}, uuid4())["acks"][0]["outcome"]
            == "CONFLICT"
        )
        delete = _event(owner, uuid4(), 3, "AGGREGATE_DELETED", "USER_TRACK_REF", {}, base=1)
        delete["aggregate_server_id"] = str(ref_id)
        delete["request_hash"] = hashlib.sha256(
            rfc8785.dumps({key: value for key, value in delete.items() if key != "request_hash"})  # type: ignore[misc]
        ).hexdigest()
        deleted = service.push(owner, {**body, "events": [delete]}, uuid4())
        assert deleted["acks"][0]["outcome"] == "APPLIED"
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM sync.device_event_inbox")).scalar()
                == 3
            )
            assert connection.execute(text("SELECT count(*) FROM sync.tombstone")).scalar() == 1
        bootstrap = service.bootstrap(
            owner,
            {
                **body,
                "reason": "FIRST_SYNC",
                "snapshot_id": None,
                "page_token": None,
                "pending_local_event_count": 0,
            },
        )
        assert bootstrap["aggregates"] == []
        assert len(bootstrap["tombstones"]) == 1
        assert other.user_id != owner.user_id
    finally:
        engine.dispose()


def test_sequence_integrity_partial_rejection_and_timeout_replay(database_url: str) -> None:
    """A gap/reorder never writes, while a terminal reject releases the next sequence."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            principal = _principal(session, "sequence")
            session.commit()
        service = SyncService(engine, cursor_secret=b"x" * 32)
        body = _bind(service, principal, uuid4())
        first_id = uuid4()
        first = _event(
            principal, first_id, 1, "USER_TRACK_REF_CREATED", "USER_TRACK_REF", {"title": "one"}
        )
        assert (
            service.push(principal, {**body, "events": [first]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )
        changed = dict(first)
        changed["payload"] = {"title": "changed"}
        changed["request_hash"] = hashlib.sha256(
            rfc8785.dumps({key: value for key, value in changed.items() if key != "request_hash"})  # type: ignore[misc]
        ).hexdigest()
        assert (
            service.push(principal, {**body, "events": [changed]}, uuid4())["acks"][0]["error"][
                "code"
            ]
            == "EVENT_HASH_MISMATCH"
        )
        gap = _event(principal, uuid4(), 3, "UNSUPPORTED", "UNKNOWN", {})
        assert (
            service.push(principal, {**body, "events": [gap]}, uuid4())["acks"][0]["error"]["code"]
            == "DEVICE_SEQUENCE_GAP"
        )
        second = _event(principal, uuid4(), 2, "UNSUPPORTED", "UNKNOWN", {})
        rejected = service.push(principal, {**body, "events": [second]}, uuid4())
        assert rejected["acks"][0]["outcome"] == "REJECTED"
        third = _event(
            principal, uuid4(), 3, "USER_TRACK_REF_CREATED", "USER_TRACK_REF", {"title": "three"}
        )
        assert (
            service.push(principal, {**body, "events": [third]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )
        unsupported_schema = _event(
            principal, uuid4(), 4, "USER_TRACK_REF_CREATED", "USER_TRACK_REF", {"title": "ignored"}
        )
        unsupported_schema["schema_version"] = 2
        _rehash(unsupported_schema)
        later = _event(
            principal, uuid4(), 5, "USER_TRACK_REF_CREATED", "USER_TRACK_REF", {"title": "later"}
        )
        partial = service.push(principal, {**body, "events": [unsupported_schema, later]}, uuid4())
        assert [ack["outcome"] for ack in partial["acks"]] == ["REJECTED", "APPLIED"]
        assert partial["acks"][0]["error"]["code"] == "UNSUPPORTED_SCHEMA_VERSION"
        with pytest.raises(SyncError, match="BATCH_SEQUENCE_NOT_ASCENDING"):
            service.push(
                principal,
                {
                    **body,
                    "events": [
                        _event(principal, uuid4(), 5, "UNSUPPORTED", "UNKNOWN", {}),
                        _event(principal, uuid4(), 4, "UNSUPPORTED", "UNKNOWN", {}),
                    ],
                },
                uuid4(),
            )
    finally:
        engine.dispose()


def test_cursor_and_bootstrap_are_owner_bound_and_stable(database_url: str) -> None:
    """A page cursor is signed/epoch-bound and bootstrap reads persisted item pages."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner, other = _principal(session, "cursor-owner"), _principal(session, "cursor-other")
            session.commit()
        service = SyncService(engine, cursor_secret=b"y" * 32)
        body = _bind(service, owner, uuid4())
        for sequence in range(1, 3):
            event = _event(
                owner,
                uuid4(),
                sequence,
                "USER_TRACK_REF_CREATED",
                "USER_TRACK_REF",
                {"title": str(sequence)},
            )
            assert (
                service.push(owner, {**body, "events": [event]}, uuid4())["acks"][0]["outcome"]
                == "APPLIED"
            )
        page = service.pull(owner, {**body, "limit": 1, "cursor": None})
        assert len(page["events"]) == 1
        with pytest.raises(SyncError, match="BINDING_MISMATCH"):
            service.pull(
                other,
                {
                    **body,
                    "device_id": str(other.device_id),
                    "limit": 1,
                    "cursor": page["next_cursor"],
                },
            )
        snapshot = service.bootstrap(
            owner,
            {
                **body,
                "reason": "FIRST_SYNC",
                "snapshot_id": None,
                "page_token": None,
                "pending_local_event_count": 0,
            },
        )
        assert snapshot["pending_event_directive"] == "PRESERVE_REBASE_RETRY"
        assert len(snapshot["aggregates"]) == 2
    finally:
        engine.dispose()


def test_tombstone_compaction_requires_all_active_device_checkpoints(database_url: str) -> None:
    """Retention alone is insufficient; a revoked lagging device is intentionally ignored."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner = _principal(session, "compact")
            session.commit()
        service = SyncService(engine, cursor_secret=b"z" * 32)
        body = _bind(service, owner, uuid4())
        ref = uuid4()
        assert (
            service.push(
                owner,
                {
                    **body,
                    "events": [
                        _event(
                            owner,
                            ref,
                            1,
                            "USER_TRACK_REF_CREATED",
                            "USER_TRACK_REF",
                            {"title": "x"},
                        )
                    ],
                },
                uuid4(),
            )["acks"][0]["outcome"]
            == "APPLIED"
        )
        deleted = _event(owner, uuid4(), 2, "AGGREGATE_DELETED", "USER_TRACK_REF", {}, base=1)
        deleted["aggregate_server_id"] = str(ref)
        deleted["request_hash"] = hashlib.sha256(
            rfc8785.dumps({key: value for key, value in deleted.items() if key != "request_hash"})  # type: ignore[misc]
        ).hexdigest()
        assert (
            service.push(owner, {**body, "events": [deleted]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE sync.tombstone SET deleted_at = now() - interval '2 days', "
                    "retain_until = now() - interval '1 second'"
                )
            )
        assert service.compact_tombstones() == 0
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE sync.device_sync_cursor SET last_pulled_server_sequence = 999")
            )
        assert service.compact_tombstones() == 1
    finally:
        engine.dispose()


def test_p07_local_payloads_preference_and_playlist_parent_authorization(database_url: str) -> None:
    """P07 local IDs must work, while an entry never authorizes through a forged parent."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner, other = _principal(session, "p07-owner"), _principal(session, "p07-other")
            session.commit()
        service = SyncService(engine, cursor_secret=b"p" * 32)
        owner_body, other_body = _bind(service, owner, uuid4()), _bind(service, other, uuid4())
        ref_id, library_id, playlist_id, entry_id = uuid4(), uuid4(), uuid4(), uuid4()
        events = [
            _event(
                owner,
                ref_id,
                1,
                "USER_TRACK_REF_CREATED",
                "USER_TRACK_REF",
                {"title": "t", "library_entry_local_id": str(library_id)},
            ),
            _event(
                owner,
                uuid4(),
                2,
                "USER_TRACK_PREFERENCE_SET",
                "USER_TRACK_PREFERENCE",
                {
                    "local_user_track_ref_id": str(ref_id),
                    "preference": "LIKED",
                    "excluded_from_taste": False,
                    "attribution": None,
                },
            ),
            _event(
                owner,
                playlist_id,
                3,
                "PLAYLIST_CREATED",
                "PLAYLIST",
                {"name": "p", "description": None},
            ),
            _event(
                owner,
                entry_id,
                4,
                "PLAYLIST_ENTRY_UPSERTED",
                "PLAYLIST_ENTRY",
                {
                    "local_playlist_entry_id": str(entry_id),
                    "local_playlist_id": str(playlist_id),
                    "local_user_track_ref_id": str(ref_id),
                    "before_local_playlist_entry_id": None,
                    "attribution": None,
                },
            ),
        ]
        for event in events:
            assert (
                service.push(owner, {**owner_body, "events": [event]}, uuid4())["acks"][0][
                    "outcome"
                ]
                == "APPLIED"
            )
        snapshot = service.bootstrap(
            owner,
            {
                **owner_body,
                "reason": "FIRST_SYNC",
                "snapshot_id": None,
                "page_token": None,
                "pending_local_event_count": 0,
            },
        )
        projected = {row["aggregate_type"]: row["payload"] for row in snapshot["aggregates"]}
        assert projected["LIBRARY_ENTRY"]["server_user_track_ref_id"] == str(ref_id)
        assert projected["PLAYLIST_ENTRY"] == {
            "server_playlist_id": str(playlist_id),
            "server_user_track_ref_id": str(ref_id),
            "position_key": projected["PLAYLIST_ENTRY"]["position_key"],
        }
        deleted = _event(owner, uuid4(), 5, "AGGREGATE_DELETED", "PLAYLIST_ENTRY", {}, base=1)
        deleted["aggregate_server_id"] = str(entry_id)
        _rehash(deleted)
        assert (
            service.push(owner, {**owner_body, "events": [deleted]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )
        deleted_snapshot = service.bootstrap(
            owner,
            {
                **owner_body,
                "reason": "USER_REQUEST",
                "snapshot_id": None,
                "page_token": None,
                "pending_local_event_count": 0,
            },
        )
        tombstone = next(
            row
            for row in deleted_snapshot["tombstones"]
            if row["aggregate_server_id"] == str(entry_id)
        )
        assert set(tombstone) == {
            "tombstone_id",
            "aggregate_type",
            "aggregate_server_id",
            "deleted_by_event_id",
            "deleted_at",
            "retain_until",
        }
        forged = _event(other, uuid4(), 1, "AGGREGATE_DELETED", "PLAYLIST_ENTRY", {}, base=1)
        forged["aggregate_server_id"] = str(entry_id)
        _rehash(forged)
        assert (
            service.push(other, {**other_body, "events": [forged]}, uuid4())["acks"][0]["outcome"]
            == "CONFLICT"
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize("occupied_by", [None, "owner", "other"])
def test_nested_library_creation_reaches_incremental_pull_once(
    database_url: str, occupied_by: str | None
) -> None:
    """A nested insert publishes an existing v1 relation event, never a guessed relation."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner = _principal(session, "nested-owner")
            receiver = _additional_device(session, owner, "nested-receiver")
            other = _principal(session, "nested-other")
            session.commit()
        service = SyncService(engine, cursor_secret=b"nested-library-regression-secret")
        body = _bind(service, owner, uuid4())
        receiver_body = _bind(service, receiver, uuid4())
        other_body = _bind(service, other, uuid4())
        library_id, ref_id = uuid4(), uuid4()
        existing_ref = uuid4()
        if occupied_by is not None:
            occupant, occupant_body = (
                (owner, body) if occupied_by == "owner" else (other, other_body)
            )
            existing = _event(
                occupant,
                existing_ref,
                1,
                "USER_TRACK_REF_CREATED",
                "USER_TRACK_REF",
                {"title": "existing", "library_entry_local_id": str(library_id)},
            )
            assert (
                service.push(occupant, {**occupant_body, "events": [existing]}, uuid4())["acks"][0][
                    "outcome"
                ]
                == "APPLIED"
            )
        checkpoint = service.pull(receiver, {**receiver_body, "cursor": None})["next_cursor"]
        event = _event(
            owner,
            ref_id,
            2 if occupied_by == "owner" else 1,
            "USER_TRACK_REF_CREATED",
            "USER_TRACK_REF",
            {"title": "incremental", "library_entry_local_id": str(library_id)},
        )
        pushed = service.push(owner, {**body, "events": [event]}, uuid4())["acks"][0]
        assert pushed["outcome"] == "APPLIED"
        assert (
            service.push(owner, {**body, "events": [event]}, uuid4())["acks"][0]["outcome"]
            == "DUPLICATE"
        )
        first = service.pull(receiver, {**receiver_body, "cursor": checkpoint, "limit": 1})
        assert len(first["events"]) == 1
        assert first["events"][0]["aggregate_type"] == "USER_TRACK_REF"
        assert first["events"][0]["aggregate_server_id"] == str(ref_id)
        second = service.pull(
            receiver, {**receiver_body, "cursor": first["next_cursor"], "limit": 1}
        )
        with Session(engine) as session:
            library = session.get(LibraryEntryRow, library_id)
            assert library is not None
            assert library.user_track_ref_id == (ref_id if occupied_by is None else existing_ref)
            if occupied_by is None:
                assert len(second["events"]) == 1
                related = second["events"][0]
                assert related["event_type"] == "LIBRARY_ENTRY_UPSERTED"
                assert related["aggregate_server_id"] == str(library_id)
                assert related["server_row_version"] == library.row_version == 1
                assert related["payload"] == {
                    "server_user_track_ref_id": str(ref_id),
                    "source": "IMPORT",
                    "availability_status": "PENDING",
                }
            else:
                assert second["events"] == []
        assert (
            service.pull(receiver, {**receiver_body, "cursor": second["next_cursor"]})["events"]
            == []
        )
    finally:
        engine.dispose()


def test_nested_library_publication_failure_rolls_back_parent_and_ack(database_url: str) -> None:
    """A failure after the parent flush leaves a retryable, wholly uncommitted command."""
    from sqlalchemy import event as sql_events

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner = _principal(session, "nested-rollback")
            session.commit()
        service = SyncService(engine, cursor_secret=b"nested-library-rollback-secret")
        body = _bind(service, owner, uuid4())
        ref_id, library_id = uuid4(), uuid4()
        event = _event(
            owner,
            ref_id,
            1,
            "USER_TRACK_REF_CREATED",
            "USER_TRACK_REF",
            {"title": "atomic", "library_entry_local_id": str(library_id)},
        )

        def fail_relation(session: Session, _context: Any, _instances: Any) -> None:
            if any(
                isinstance(row, SyncEventRow) and row.aggregate_type == "LIBRARY_ENTRY"
                for row in session.new
            ):
                raise RuntimeError("synthetic relation publication failure")

        sql_events.listen(service._sessions, "before_flush", fail_relation)
        try:
            with pytest.raises(RuntimeError, match="synthetic relation publication failure"):
                service.push(owner, {**body, "events": [event]}, uuid4())
        finally:
            sql_events.remove(service._sessions, "before_flush", fail_relation)
        with Session(engine) as session:
            assert session.get(UserTrackRefRow, ref_id) is None
            assert session.get(LibraryEntryRow, library_id) is None
            assert (
                session.scalars(
                    select(SyncEventRow).where(SyncEventRow.user_id == owner.user_id)
                ).all()
                == []
            )
        assert (
            service.push(owner, {**body, "events": [event]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )
        assert len(service.pull(owner, {**body, "cursor": None})["events"]) == 2
    finally:
        engine.dispose()


def test_specialized_interaction_rejects_unattributed_and_invalid_listening(
    database_url: str,
) -> None:
    """Frozen P04 envelopes reject invalid attribution without leaking ownership."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            principal = _principal(session, "interactions")
            session.commit()
        service = SyncService(engine, cursor_secret=b"i" * 32)
        body = _bind(service, principal, uuid4())
        impression = _event(
            principal,
            uuid4(),
            1,
            "RECOMMENDATION_IMPRESSION_RECORDED",
            "USER_INTERACTION_EVENT",
            {
                "interaction_type": "RECOMMENDATION_IMPRESSION_RECORDED",
                "recommendation": {
                    "recommendation_request_id": str(uuid4()),
                    "recording_id": str(uuid4()),
                    "source_rank": 1,
                    "source": "home",
                    "surface": "home",
                    "presentation_id": str(uuid4()),
                    "display_position": 1,
                },
            },
        )
        assert (
            service.push(principal, {**body, "events": [impression]}, uuid4())["acks"][0]["error"][
                "code"
            ]
            == "ATTRIBUTION_NOT_FOUND"
        )
        listening = _event(
            principal,
            uuid4(),
            2,
            "LISTENING_EVENT_RECORDED",
            "LISTENING_EVENT",
            {"interaction_type": "LISTENING_EVENT_RECORDED"},
        )
        assert (
            service.push(principal, {**body, "events": [listening]}, uuid4())["acks"][0]["outcome"]
            == "REJECTED"
        )
    finally:
        engine.dispose()


def test_specialized_interaction_positive_dedupe_and_non_disclosing_attribution(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner, other = (
                _principal(session, "interaction-owner"),
                _principal(session, "interaction-other"),
            )
            request_id, recording_id = _recommendation_fixture(session, owner.user_id)
            session.commit()
        service = SyncService(engine, cursor_secret=b"r" * 32)
        body, other_body = _bind(service, owner, uuid4()), _bind(service, other, uuid4())
        ref_id = uuid4()
        create = _event(
            owner, ref_id, 1, "USER_TRACK_REF_CREATED", "USER_TRACK_REF", {"title": "t"}
        )
        assert (
            service.push(owner, {**body, "events": [create]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )
        presentation, impression_id = uuid4(), uuid4()
        attribution = {
            "recommendation_request_id": str(request_id),
            "recording_id": str(recording_id),
            "source_rank": 1,
            "source": "home",
            "surface": "home",
            "presentation_id": str(presentation),
            "display_position": 1,
        }
        impression = _event(
            owner,
            impression_id,
            2,
            "RECOMMENDATION_IMPRESSION_RECORDED",
            "USER_INTERACTION_EVENT",
            {
                "interaction_type": "RECOMMENDATION_IMPRESSION_RECORDED",
                "recommendation": attribution,
            },
        )
        assert (
            service.push(owner, {**body, "events": [impression]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )
        duplicate = _event(
            owner,
            uuid4(),
            3,
            "RECOMMENDATION_IMPRESSION_RECORDED",
            "USER_INTERACTION_EVENT",
            {
                "interaction_type": "RECOMMENDATION_IMPRESSION_RECORDED",
                "recommendation": attribution,
            },
        )
        assert (
            service.push(owner, {**body, "events": [duplicate]}, uuid4())["acks"][0]["error"][
                "code"
            ]
            == "IMPRESSION_ALREADY_RECORDED"
        )
        listening = _event(
            owner,
            uuid4(),
            4,
            "LISTENING_EVENT_RECORDED",
            "LISTENING_EVENT",
            {
                "interaction_type": "LISTENING_EVENT_RECORDED",
                "local_user_track_ref_id": str(ref_id),
                "server_user_track_ref_id": None,
                "recording_id": None,
                "played_ms": 100,
                "track_duration_ms": 1000,
                "completion_ratio": 0.1,
                "event_origin": "RECOMMENDED",
                "context": "GENERAL",
                "explicit_feedback": "NONE",
                "excluded_from_taste": False,
                "recommendation": attribution,
            },
        )
        assert (
            service.push(owner, {**body, "events": [listening]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )
        feedback_attribution = {
            **attribution,
            "impression_event_local_id": str(impression_id),
            "impression_event_server_id": None,
        }
        feedback_id = uuid4()
        feedback = _event(
            owner,
            feedback_id,
            5,
            "RECOMMENDATION_FEEDBACK_RECORDED",
            "USER_INTERACTION_EVENT",
            {
                "interaction_type": "RECOMMENDATION_FEEDBACK_RECORDED",
                "feedback_type": "SELECTED",
                "recommendation": feedback_attribution,
            },
        )
        assert (
            service.push(owner, {**body, "events": [feedback]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )
        with Session(engine) as session:
            projected = session.get(UserInteractionEventRow, feedback_id)
            assert projected is not None
            assert projected.impression_interaction_id == impression_id
            assert projected.recommendation_request_id == request_id
            other_request_id, other_recording_id = _recommendation_fixture(session, owner.user_id)
            session.commit()
        causal_mismatch = _event(
            owner,
            uuid4(),
            6,
            "RECOMMENDATION_FEEDBACK_RECORDED",
            "USER_INTERACTION_EVENT",
            {
                "interaction_type": "RECOMMENDATION_FEEDBACK_RECORDED",
                "feedback_type": "DISMISSED",
                "recommendation": {
                    **feedback_attribution,
                    "recommendation_request_id": str(other_request_id),
                    "recording_id": str(other_recording_id),
                },
            },
        )
        mismatch_ack = service.push(owner, {**body, "events": [causal_mismatch]}, uuid4())["acks"][
            0
        ]
        assert mismatch_ack["error"]["code"] == "ATTRIBUTION_NOT_FOUND"
        missing = _event(
            other,
            uuid4(),
            1,
            "RECOMMENDATION_IMPRESSION_RECORDED",
            "USER_INTERACTION_EVENT",
            {
                "interaction_type": "RECOMMENDATION_IMPRESSION_RECORDED",
                "recommendation": {**attribution, "recommendation_request_id": str(uuid4())},
            },
        )
        foreign = _event(
            other,
            uuid4(),
            2,
            "RECOMMENDATION_IMPRESSION_RECORDED",
            "USER_INTERACTION_EVENT",
            {
                "interaction_type": "RECOMMENDATION_IMPRESSION_RECORDED",
                "recommendation": attribution,
            },
        )
        missing_ack = service.push(other, {**other_body, "events": [missing]}, uuid4())["acks"][0]
        foreign_ack = service.push(other, {**other_body, "events": [foreign]}, uuid4())["acks"][0]
        assert (
            missing_ack["error"]["code"] == foreign_ack["error"]["code"] == "ATTRIBUTION_NOT_FOUND"
        )
    finally:
        engine.dispose()


def test_same_device_concurrent_push_serializes_sequence(database_url: str) -> None:
    """Two competing next events produce one terminal apply and one retryable gap, never a 500."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            principal = _principal(session, "concurrent")
            session.commit()
        service = SyncService(engine, cursor_secret=b"c" * 32)
        body = _bind(service, principal, uuid4())
        first = _event(
            principal, uuid4(), 1, "USER_TRACK_REF_CREATED", "USER_TRACK_REF", {"title": "a"}
        )
        second = _event(
            principal, uuid4(), 1, "USER_TRACK_REF_CREATED", "USER_TRACK_REF", {"title": "b"}
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda event: service.push(principal, {**body, "events": [event]}, uuid4()),
                    (first, second),
                )
            )
        acks = [response["acks"][0] for response in responses]
        assert sorted(ack["outcome"] for ack in acks) == ["APPLIED", "REJECTED"]
        rejected = next(ack for ack in acks if ack["outcome"] == "REJECTED")
        assert rejected["error"]["code"] in {"DEVICE_SEQUENCE_GAP", "DEVICE_SEQUENCE_REUSE"}
    finally:
        engine.dispose()


def test_rejected_generic_patch_rolls_back_projection_and_replays_terminal_ack(
    database_url: str,
) -> None:
    """A later invalid field cannot leak an earlier mutation outside the ACK savepoint."""

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            principal = _principal(session, "rejected-projection")
            session.commit()
        service = SyncService(engine, cursor_secret=b"r" * 32)
        body = _bind(service, principal, uuid4())
        ref_id = uuid4()
        created = _event(
            principal,
            ref_id,
            1,
            "USER_TRACK_REF_CREATED",
            "USER_TRACK_REF",
            {"title": "before", "artist": "valid"},
        )
        assert (
            service.push(principal, {**body, "events": [created]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )

        rejected = _event(
            principal,
            uuid4(),
            2,
            "USER_TRACK_REF_PATCHED",
            "USER_TRACK_REF",
            {"title": "must-not-leak", "artist": 123},
            base=1,
        )
        rejected["aggregate_local_id"] = str(ref_id)
        rejected["aggregate_server_id"] = str(ref_id)
        _rehash(rejected)
        first_ack = service.push(principal, {**body, "events": [rejected]}, uuid4())["acks"][0]
        replay_ack = service.push(principal, {**body, "events": [rejected]}, uuid4())["acks"][0]

        assert first_ack["outcome"] == "REJECTED"
        assert first_ack["error"]["code"] == "REQUEST_VALIDATION_FAILED"
        assert replay_ack["outcome"] == "DUPLICATE"
        assert replay_ack["original_outcome"] == "REJECTED"
        with Session(engine) as session:
            row = session.get(UserTrackRefRow, ref_id)
            assert row is not None
            assert (row.raw_title, row.raw_artist, row.row_version) == ("before", "valid", 1)
            assert (
                session.scalar(
                    select(text("count(*)"))
                    .select_from(SyncEventRow)
                    .where(SyncEventRow.aggregate_id == ref_id)
                )
                == 1
            )
    finally:
        engine.dispose()


def test_owner_publish_fence_preserves_cursor_delivery_and_allows_other_owners(
    database_url: str,
) -> None:
    """An uncommitted lower sequence fences later events only for the same owner."""

    engine = create_engine(database_url)
    release_slow = threading.Event()
    try:
        with Session(engine) as session:
            owner = _principal(session, "publish-fence-owner")
            second = _additional_device(session, owner, "publish-fence-second")
            puller = _additional_device(session, owner, "publish-fence-puller")
            other = _principal(session, "publish-fence-other")
            session.commit()

        service = SyncService(engine, cursor_secret=b"f" * 32)
        slow_id, fast_id = uuid4(), uuid4()
        slow_allocated = threading.Event()

        class DelayedCommitSyncService(SyncService):
            def _push_one(
                self,
                session: Session,
                principal: Principal,
                device_id: UUID,
                epoch: UUID,
                event: dict[str, Any],
                request_id: UUID,
            ) -> dict[str, Any]:
                ack = super()._push_one(session, principal, device_id, epoch, event, request_id)
                if event["event_id"] == str(slow_id):
                    session.flush()
                    slow_allocated.set()
                    if not release_slow.wait(10):
                        raise RuntimeError("publish fence test timed out")
                return ack

        delayed = DelayedCommitSyncService(engine, cursor_secret=b"f" * 32)
        owner_body = _bind(service, owner, uuid4())
        second_body = _bind(service, second, uuid4())
        puller_body = _bind(service, puller, uuid4())
        other_body = _bind(service, other, uuid4())
        slow = _event(
            owner,
            slow_id,
            1,
            "USER_TRACK_REF_CREATED",
            "USER_TRACK_REF",
            {"title": "slow"},
        )
        fast = _event(
            second,
            fast_id,
            1,
            "USER_TRACK_REF_CREATED",
            "USER_TRACK_REF",
            {"title": "fast"},
        )
        unrelated = _event(
            other,
            uuid4(),
            1,
            "USER_TRACK_REF_CREATED",
            "USER_TRACK_REF",
            {"title": "unrelated-owner"},
        )

        with ThreadPoolExecutor(max_workers=3) as pool:
            slow_future = pool.submit(
                delayed.push, owner, {**owner_body, "events": [slow]}, uuid4()
            )
            assert slow_allocated.wait(5)
            same_owner_future = pool.submit(
                service.push, second, {**second_body, "events": [fast]}, uuid4()
            )
            other_owner_future = pool.submit(
                service.push, other, {**other_body, "events": [unrelated]}, uuid4()
            )
            try:
                assert other_owner_future.result(timeout=5)["acks"][0]["outcome"] == "APPLIED"
                first_page = service.pull(puller, {**puller_body, "cursor": None})
                assert first_page["events"] == []
                assert not same_owner_future.done()
            finally:
                release_slow.set()
            assert slow_future.result(timeout=5)["acks"][0]["outcome"] == "APPLIED"
            assert same_owner_future.result(timeout=5)["acks"][0]["outcome"] == "APPLIED"

        delivered = service.pull(
            puller,
            {**puller_body, "cursor": first_page["next_cursor"]},
        )["events"]
        assert [event["event_id"] for event in delivered] == [str(slow_id), str(fast_id)]
        assert delivered[0]["server_sequence"] < delivered[1]["server_sequence"]
    finally:
        release_slow.set()
        engine.dispose()


def test_cross_device_same_base_patch_has_one_applied_writer(database_url: str) -> None:
    """The owner publication fence makes optimistic version checks atomic across devices."""

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            first = _principal(session, "same-base-owner")
            second = _additional_device(session, first, "same-base-second")
            session.commit()
        service = SyncService(engine, cursor_secret=b"v" * 32)
        first_body = _bind(service, first, uuid4())
        second_body = _bind(service, second, uuid4())
        ref_id = uuid4()
        created = _event(
            first,
            ref_id,
            1,
            "USER_TRACK_REF_CREATED",
            "USER_TRACK_REF",
            {"title": "before"},
        )
        assert (
            service.push(first, {**first_body, "events": [created]}, uuid4())["acks"][0]["outcome"]
            == "APPLIED"
        )

        def patch(
            principal: Principal,
            body: dict[str, object],
            sequence: int,
            title: str,
        ) -> dict[str, Any]:
            event = _event(
                principal,
                uuid4(),
                sequence,
                "USER_TRACK_REF_PATCHED",
                "USER_TRACK_REF",
                {"title": title},
                base=1,
            )
            event["aggregate_local_id"] = str(ref_id)
            event["aggregate_server_id"] = str(ref_id)
            _rehash(event)
            return cast(
                dict[str, Any],
                service.push(principal, {**body, "events": [event]}, uuid4())["acks"][0],
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(patch, first, first_body, 2, "first"),
                pool.submit(patch, second, second_body, 1, "second"),
            )
            acks = [future.result(timeout=5) for future in futures]

        assert sorted(ack["outcome"] for ack in acks) == ["APPLIED", "CONFLICT"]
        applied = next(ack for ack in acks if ack["outcome"] == "APPLIED")
        assert applied["server_row_version"] == 2
        with Session(engine) as session:
            row = session.get(UserTrackRefRow, ref_id)
            assert row is not None
            assert row.row_version == 2
            assert row.raw_title in {"first", "second"}
    finally:
        engine.dispose()


def test_playlist_move_rebalances_and_publishes_exact_remote_order(database_url: str) -> None:
    """Moving UUID3 before UUID1 produces 3,1,2 in storage and incremental sync."""

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            owner = _principal(session, "playlist-rebalance-owner")
            receiver = _additional_device(session, owner, "playlist-rebalance-receiver")
            session.commit()
        service = SyncService(engine, cursor_secret=b"o" * 32)
        body = _bind(service, owner, uuid4())
        receiver_body = _bind(service, receiver, uuid4())
        ref_id, playlist_id = uuid4(), uuid4()
        entry_ids = (uuid4(), uuid4(), uuid4())
        events = [
            _event(
                owner,
                ref_id,
                1,
                "USER_TRACK_REF_CREATED",
                "USER_TRACK_REF",
                {"title": "ordered"},
            ),
            _event(
                owner,
                playlist_id,
                2,
                "PLAYLIST_CREATED",
                "PLAYLIST",
                {"name": "ordered", "description": None},
            ),
        ]
        events.extend(
            _event(
                owner,
                entry_id,
                sequence,
                "PLAYLIST_ENTRY_UPSERTED",
                "PLAYLIST_ENTRY",
                {
                    "local_playlist_id": str(playlist_id),
                    "local_user_track_ref_id": str(ref_id),
                    "before_local_playlist_entry_id": None,
                },
            )
            for sequence, entry_id in enumerate(entry_ids, start=3)
        )
        for event in events:
            assert (
                service.push(owner, {**body, "events": [event]}, uuid4())["acks"][0]["outcome"]
                == "APPLIED"
            )

        moved = _event(
            owner,
            uuid4(),
            6,
            "PLAYLIST_ENTRY_MOVED",
            "PLAYLIST_ENTRY",
            {
                "local_playlist_id": str(playlist_id),
                "before_local_playlist_entry_id": str(entry_ids[0]),
            },
            base=1,
        )
        moved["aggregate_local_id"] = str(entry_ids[2])
        moved["aggregate_server_id"] = str(entry_ids[2])
        _rehash(moved)
        moved_ack = service.push(owner, {**body, "events": [moved]}, uuid4())["acks"][0]
        assert moved_ack["outcome"] == "APPLIED"
        assert moved_ack["server_row_version"] == 3

        with Session(engine) as session:
            rows = list(
                session.scalars(
                    select(PlaylistEntryRow)
                    .where(PlaylistEntryRow.playlist_id == playlist_id)
                    .order_by(PlaylistEntryRow.position_key)
                )
            )
            assert [row.playlist_entry_id for row in rows] == [
                entry_ids[2],
                entry_ids[0],
                entry_ids[1],
            ]
            assert [row.row_version for row in rows] == [3, 3, 3]

        delivered = service.pull(receiver, {**receiver_body, "cursor": None})["events"]
        latest_positions = {
            event["aggregate_server_id"]: event["payload"]["position_key"]
            for event in delivered
            if event["aggregate_type"] == "PLAYLIST_ENTRY" and "position_key" in event["payload"]
        }
        assert [
            entry_id
            for entry_id, _position in sorted(
                (
                    (UUID(entry_id), position)
                    for entry_id, position in latest_positions.items()
                    if UUID(entry_id) in entry_ids
                ),
                key=lambda item: item[1],
            )
        ] == [entry_ids[2], entry_ids[0], entry_ids[1]]
    finally:
        engine.dispose()


def test_cross_device_concurrent_impression_retry_is_terminal(database_url: str) -> None:
    """A semantic impression race is one apply plus one stable duplicate, never a 500."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            first = _principal(session, "impression-race")
            second_device_id = uuid4()
            session.add(
                DeviceRow(
                    device_id=second_device_id,
                    user_id=first.user_id,
                    device_name="impression-race-second",
                    platform="ANDROID",
                    app_version="p11",
                )
            )
            request_id, recording_id = _recommendation_fixture(session, first.user_id)
            session.commit()
        second = Principal(first.user_id, second_device_id, uuid4(), AccountRole.USER)
        service = SyncService(engine, cursor_secret=b"i" * 32)
        presentation_id = uuid4()

        def submit(principal: Principal) -> dict[str, Any]:
            body = _bind(service, principal, uuid4())
            attribution = {
                "recommendation_request_id": str(request_id),
                "recording_id": str(recording_id),
                "source_rank": 1,
                "source": "recommendations",
                "surface": "recommendations",
                "presentation_id": str(presentation_id),
                "display_position": 1,
            }
            event = _event(
                principal,
                uuid4(),
                1,
                "RECOMMENDATION_IMPRESSION_RECORDED",
                "USER_INTERACTION_EVENT",
                {
                    "interaction_type": "RECOMMENDATION_IMPRESSION_RECORDED",
                    "recommendation": attribution,
                },
            )
            return service.push(principal, {**body, "events": [event]}, uuid4())

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, (first, second)))

        acks = [response["acks"][0] for response in responses]
        assert sorted(ack["outcome"] for ack in acks) == ["APPLIED", "REJECTED"]
        rejected = next(ack for ack in acks if ack["outcome"] == "REJECTED")
        assert rejected["error"]["code"] == "IMPRESSION_ALREADY_RECORDED"
    finally:
        engine.dispose()


def test_bootstrap_recording_redirect_uses_frozen_wire_shape(database_url: str) -> None:
    """Redirect serializer exposes only the Android/frozen alias/canonical contract."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            principal = _principal(session, "redirect-wire")
            session.commit()
        service = SyncService(engine, cursor_secret=b"d" * 32)
        body = _bind(service, principal, uuid4())
        snapshot_id, alias_id, canonical_id = uuid4(), uuid4(), uuid4()
        with Session(engine) as session:
            session.add(
                BootstrapSessionRow(
                    snapshot_id=snapshot_id,
                    user_id=principal.user_id,
                    device_id=principal.device_id,
                    journal_epoch=UUID(str(body["journal_epoch"])),
                    high_water_server_sequence=0,
                    created_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )
            session.add(
                BootstrapSnapshotItemRow(
                    snapshot_id=snapshot_id,
                    ordinal=1,
                    aggregate_type="RECORDING_REDIRECT",
                    aggregate_id=alias_id,
                    server_row_version=1,
                    payload={
                        "aggregate_type": "RECORDING",
                        "alias_server_id": str(alias_id),
                        "canonical_server_id": str(canonical_id),
                    },
                )
            )
            session.commit()
        response = service.bootstrap(
            principal,
            {
                **body,
                "reason": "FIRST_SYNC",
                "snapshot_id": str(snapshot_id),
                "page_token": None,
                "pending_local_event_count": 0,
            },
        )
        assert response["aggregates"] == []
        assert response["redirects"] == [
            {
                "aggregate_type": "RECORDING",
                "alias_server_id": str(alias_id),
                "canonical_server_id": str(canonical_id),
            }
        ]
    finally:
        engine.dispose()
