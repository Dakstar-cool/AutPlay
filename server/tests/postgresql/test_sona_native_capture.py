"""Original-request collection, consent fences and atomicity on real PostgreSQL."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event
from typing import Any
from uuid import UUID, uuid4, uuid7

import pytest
import rfc8785
from autplay.adapters.postgresql.models.sync import DeviceEventInboxRow, SyncEventRow
from autplay.adapters.postgresql.recommendations import (
    SqlAlchemyOfflinePackRepository,
    SqlAlchemyRecommendationRuntime,
)
from autplay.adapters.postgresql.sona_native_capture import (
    SqlAlchemySonaNativeCapture,
    cleanup_native_capture,
)
from autplay.application.recommendations import (
    RecommendationService,
    StaticRecommendationVersionRegistry,
)
from autplay.application.sona_shadow import recommendation_ranking_document
from autplay.domain.recommendations import RecommendationQuery, RecommendationSurface
from autplay.domain.training_consent import TrainingConsentEvidenceError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness
from .test_adaptive_recommendation_runtime import _recording
from .test_recommendation_runtime import _seed_many_candidates, _vault_recording
from .test_training_consent import PairingHarness, command, service
from .test_training_consent import base_pair as base_pair
from .test_training_consent import pair as pair


def _runtime(
    pair: PairingHarness, *, enabled: bool = True
) -> tuple[RecommendationService, SqlAlchemySonaNativeCapture]:
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    capture = SqlAlchemySonaNativeCapture(service(pair))
    runtime = SqlAlchemyRecommendationRuntime(sessions, native_capture=capture if enabled else None)
    return RecommendationService(
        snapshots=runtime,
        traces=runtime,
        registry=StaticRecommendationVersionRegistry(),
        ids=uuid7,
        clock=lambda: datetime.now(UTC),
        packs=SqlAlchemyOfflinePackRepository(runtime),
        atomic_writer=runtime if enabled else None,
    ), capture


def _track(pair: PairingHarness) -> UUID:
    with Session(pair.engine) as session, session.begin():
        provider = uuid4()
        session.execute(
            text(
                "INSERT INTO identity.source_provider "
                "(provider_id,provider_key,display_name,adapter_id,adapter_version) "
                "VALUES (:id,'capture.fixture','Capture fixture','fixture','1')"
            ),
            {"id": provider},
        )
        recording = _vault_recording(session, pair.actor.user_id, index=0, provider_id=provider)
        return recording


def _fact(
    pair: PairingHarness,
    *,
    sequence: int = 1,
    proven: bool = True,
    recording_id: UUID | None = None,
) -> UUID:
    identifier = uuid4()
    with Session(pair.engine) as session, session.begin():
        values = {
            "event_id": identifier,
            "user_id": pair.actor.user_id,
            "event_type": "LISTENING_EVENT_RECORDED",
            "schema_version": 1,
            "aggregate_type": "LISTENING_EVENT",
            "aggregate_id": identifier,
            "payload": {
                "interaction_type": "LISTENING_EVENT_RECORDED",
                "played_ms": 1000,
                "recording_id": str(recording_id) if recording_id is not None else None,
            },
        }
        session.add(SyncEventRow(**values, origin_device_id=pair.actor.device_id))
        if proven:
            session.add(
                DeviceEventInboxRow(
                    **values,
                    device_id=pair.actor.device_id,
                    device_sequence=sequence,
                    occurred_at=datetime.now(UTC),
                    apply_status="APPLIED",
                    request_hash=b"s" * 32,
                )
            )
    return identifier


def _documents(pair: PairingHarness) -> list[dict[str, Any]]:
    with Session(pair.engine) as session:
        rows = session.execute(text("SELECT payload,payload_sha256 FROM ml.sona_native_capture"))
        result = []
        for payload, digest in rows:
            assert sha256(payload).digest() == digest
            document = json.loads(payload)
            assert rfc8785.dumps(document) == payload
            result.append(document)
        return result


@pytest.mark.parametrize("surface", list(RecommendationSurface))
def test_original_inputs_and_all_surfaces_are_collected(
    pair: PairingHarness, surface: RecommendationSurface
) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    recording = _track(pair)
    event_id = _fact(pair)
    recommendations, _ = _runtime(pair)
    response = recommendations.recommend(RecommendationQuery(pair.actor.user_id, surface, limit=3))
    [document] = _documents(pair)
    assert document["collection_status"] == "COLLECTED"
    assert document["surface"] == surface.value
    assert document["quality_eligible"] is False
    assert document["candidate_count"] == 1
    assert (
        document["input_snapshot_sha256"]
        == sha256(rfc8785.dumps(document["input_snapshot"])).hexdigest()
    )
    assert document["request_sha256"] == response.request.request_sha256
    assert document["ranking"] == recommendation_ranking_document(response.items)
    # This fixture has a Vault copy only: offline P11 correctly excludes it.
    expected = [] if surface is RecommendationSurface.OFFLINE_PACK else [recording]
    assert [item.recording_id for item in response.items] == expected
    assert document["source_facts"][0]["event_id"] == str(event_id)
    assert (
        recommendations.exact_replay(
            pair.actor.user_id, response.request.recommendation_request_id
        ).items
        == response.items
    )
    assert len(_documents(pair)) == 1


@pytest.mark.parametrize("decision", [None, "DENIED", "WITHDRAWN"])
def test_no_collection_without_current_grant(pair: PairingHarness, decision: str | None) -> None:
    if decision is not None:
        service(pair).decide(pair.actor, command(pair, decision))
    recommendations, _ = _runtime(pair)
    recommendations.recommend(RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME))
    assert not _documents(pair)


def test_disabled_collection_preserves_existing_path(pair: PairingHarness) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    recommendations, _ = _runtime(pair, enabled=False)
    recommendations.recommend(RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME))
    assert not _documents(pair)


def test_capture_failure_rolls_back_snapshot_trace_and_items(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    _track(pair)
    recommendations, capture = _runtime(pair)
    original = capture.save

    def fail(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise RuntimeError("injected_capture_failure")

    monkeypatch.setattr(capture, "save", fail)
    with pytest.raises(RuntimeError, match="injected_capture_failure"):
        recommendations.recommend(
            RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME)
        )
    with Session(pair.engine) as session:
        for table in (
            "sona_native_capture",
            "recommendation_request",
            "recommendation_item",
            "recommendation_input_snapshot",
        ):
            assert session.scalar(text(f"SELECT count(*) FROM ml.{table}")) == 0


def test_independent_evidence_is_required(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    recommendations, _ = _runtime(pair)

    def unavailable() -> None:
        raise TrainingConsentEvidenceError

    monkeypatch.setattr(pair.consent_ledger, "read", unavailable)
    with pytest.raises(TrainingConsentEvidenceError):
        recommendations.recommend(
            RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME)
        )
    assert not _documents(pair)


def test_withdrawal_erases_and_prevents_recollection(pair: PairingHarness) -> None:
    policy = service(pair)
    policy.decide(pair.actor, command(pair, "GRANTED"))
    recommendations, _ = _runtime(pair)
    query = RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME)
    recommendations.recommend(query)
    assert len(_documents(pair)) == 1
    policy.decide(pair.actor, command(pair, "WITHDRAWN", 1))
    assert not _documents(pair)
    recommendations.recommend(query)
    assert not _documents(pair)


def test_concurrent_withdrawal_waits_then_erases_original_capture(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = service(pair)
    policy.decide(pair.actor, command(pair, "GRANTED"))
    recommendations, capture = _runtime(pair)
    entered, release = Event(), Event()
    original = capture.save

    def wait(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(capture, "save", wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        serving = pool.submit(
            recommendations.recommend,
            RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME),
        )
        assert entered.wait(10)
        withdrawal = pool.submit(policy.decide, pair.actor, command(pair, "WITHDRAWN", 1))
        try:
            assert not withdrawal.done()
        finally:
            release.set()
        serving.result(timeout=15)
        withdrawal.result(timeout=15)
    assert not _documents(pair)


def test_limits_and_missing_provenance_are_explicit(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = service(pair)
    policy.decide(pair.actor, command(pair, "GRANTED"))
    _fact(pair, proven=False)
    recommendations, _ = _runtime(pair)
    query = RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME)
    recommendations.recommend(query)
    assert _documents(pair)[0]["collection_status"] == "SOURCE_PROVENANCE_GAP"
    monkeypatch.setattr("autplay.adapters.postgresql.sona_native_capture.MAX_SOURCE_FACTS", 1)
    _fact(pair, sequence=2)
    recommendations.recommend(query)
    limited = next(doc for doc in _documents(pair) if doc["collection_status"] == "SOURCE_LIMIT")
    assert limited["source_facts"] == []
    assert limited["source_count_lower_bound"] == 2


def test_payload_is_immutable_and_account_disable_erases(pair: PairingHarness) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    recommendations, _ = _runtime(pair)
    recommendations.recommend(RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME))
    with Session(pair.engine) as session:
        with pytest.raises(DBAPIError, match="immutable"):
            session.execute(text("UPDATE ml.sona_native_capture SET payload=payload"))
        session.rollback()
        session.execute(
            text("UPDATE account.user_account SET status='DISABLED' WHERE user_id=:u"),
            {"u": pair.actor.user_id},
        )
        session.commit()
    assert not _documents(pair)


def test_15001_available_tracks_preserve_the_entire_p11_5000_snapshot(pair: PairingHarness) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    with Session(pair.engine) as session, session.begin():
        _seed_many_candidates(session, pair.actor, 15_001)
    recommendations, _ = _runtime(pair)
    response = recommendations.recommend(
        RecommendationQuery(pair.actor.user_id, RecommendationSurface.OFFLINE_PACK, limit=30)
    )
    [document] = _documents(pair)
    assert document["collection_status"] == "COLLECTED"
    assert document["candidate_count"] == 5_000
    assert len(document["input_snapshot"]["tracks"]) == 5_000
    assert document["input_snapshot_sha256"] == response.request.snapshot.input_snapshot_sha256
    assert document["ranking"] == recommendation_ranking_document(response.items)


def test_late_commits_do_not_enter_original_mvcc_capture(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    before = _fact(pair)
    recommendations, capture = _runtime(pair)
    original = capture.save

    def commit_late(*args: Any, **kwargs: Any) -> None:
        _fact(pair, sequence=2)
        original(*args, **kwargs)

    monkeypatch.setattr(capture, "save", commit_late)
    recommendations.recommend(RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME))
    [document] = _documents(pair)
    assert [fact["event_id"] for fact in document["source_facts"]] == [str(before)]


def test_cleanup_runs_without_new_serving_requests(pair: PairingHarness) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    recommendations, _ = _runtime(pair)
    response = recommendations.recommend(
        RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME)
    )
    # Seed an expired immutable row through its normal INSERT guard, instead of
    # disabling immutability or changing a production clock.
    with Session(pair.engine) as session, session.begin():
        row = session.execute(text("SELECT * FROM ml.sona_native_capture")).mappings().one()
        session.execute(text("DELETE FROM ml.sona_native_capture"))
        created = datetime.now(UTC) - timedelta(days=181)
        session.execute(
            text(
                "INSERT INTO ml.sona_native_capture "
                "(request_id,user_id,consent_revision,surface,collection_status,created_at,"
                "retained_until,payload,payload_sha256) "
                "VALUES (:r,:u,1,'home','COLLECTED',:c,:e,:p,:h)"
            ),
            {
                "r": response.request.recommendation_request_id,
                "u": pair.actor.user_id,
                "c": created,
                "e": created + timedelta(days=180),
                "p": row["payload"],
                "h": row["payload_sha256"],
            },
        )
    assert cleanup_native_capture(sessionmaker(pair.engine)) == 1
    assert cleanup_native_capture(sessionmaker(pair.engine)) == 0


def test_deployed_head_upgrade_preserves_existing_requests(
    pair: PairingHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    policy = service(pair)
    policy.decide(pair.actor, command(pair, "GRANTED"))
    database_harness.downgrade(database_name, "0066_portable_local_account")
    recommendations, _ = _runtime(pair, enabled=False)
    _track(pair)
    query = RecommendationQuery(pair.actor.user_id, RecommendationSurface.RECOMMENDATIONS)
    before = recommendations.recommend(query)
    database_harness.upgrade(database_name)
    enabled, _ = _runtime(pair)
    after = enabled.exact_replay(pair.actor.user_id, before.request.recommendation_request_id)
    assert after == before
    assert not _documents(pair)
    enabled.recommend(query)
    assert len(_documents(pair)) == 1
    assert policy.get(pair.actor)["revision"] == 1


def test_rollback_refuses_to_discard_collected_data(
    pair: PairingHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    recommendations, _ = _runtime(pair)
    recommendations.recommend(RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME))
    before = _documents(pair)
    with Session(pair.engine) as session:
        before_revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(DBAPIError, match="requires an empty collection"):
        database_harness.downgrade(database_name, "0066_portable_local_account")
    assert _documents(pair) == before
    with Session(pair.engine) as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == before_revision


def test_history_identity_is_frozen_outside_the_p11_candidate_list(pair: PairingHarness) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    with Session(pair.engine) as session, session.begin():
        artist_id, recording_id = _recording(session)
        track_ref = session.scalar(
            text(
                "INSERT INTO library.user_track_ref(user_id,raw_title) "
                "VALUES (:u,'History fixture') RETURNING user_track_ref_id"
            ),
            {"u": pair.actor.user_id},
        )
    event_id = _fact(pair, recording_id=recording_id)
    with Session(pair.engine) as session, session.begin():
        session.execute(
            text(
                "INSERT INTO library.listening_event(listening_event_id,user_id,device_id,"
                "user_track_ref_id,recording_id,started_at,played_ms) "
                "VALUES (:e,:u,:d,:ref,:r,now(),1000)"
            ),
            {
                "e": event_id,
                "u": pair.actor.user_id,
                "d": pair.actor.device_id,
                "ref": track_ref,
                "r": recording_id,
            },
        )
    recommendations, _ = _runtime(pair)
    recommendations.recommend(RecommendationQuery(pair.actor.user_id, RecommendationSurface.HOME))
    [document] = _documents(pair)
    assert document["candidate_count"] == 0
    [fact] = document["source_facts"]
    assert fact["listening_projection"]["played_ms"] == 1000
    assert fact["recording_context"]["recording_id"] == str(recording_id)
    assert fact["recording_context"]["artist_key"] == str(artist_id)
