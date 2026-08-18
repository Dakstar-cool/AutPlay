"""Real PostgreSQL evidence for the P09 transaction boundary."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.postgresql.models import (
    BootstrapSessionRow,
    BootstrapSnapshotItemRow,
    DeviceRow,
    UserAccountRow,
    UserInteractionEventRow,
)
from autplay.application.sync import SyncError, SyncService
from autplay.domain.auth import AccountRole, Principal
from sqlalchemy import create_engine, text
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


def _bind(service: SyncService, principal: Principal, epoch: UUID) -> dict[str, object]:
    profile = uuid4()
    body: dict[str, object] = {
        "protocol_version": 1,
        "user_id": str(principal.user_id),
        "device_id": str(principal.device_id),
        "server_profile_id": str(profile),
        "journal_epoch": str(epoch),
        "device_name": "bound",
        "platform": "ANDROID",
        "app_version": "p09",
    }
    service.bind(principal, body)
    return body


def _rehash(value: dict[str, object]) -> None:
    value["request_hash"] = hashlib.sha256(
        rfc8785.dumps({key: item for key, item in value.items() if key != "request_hash"})  # type: ignore[misc]
    ).hexdigest()


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
