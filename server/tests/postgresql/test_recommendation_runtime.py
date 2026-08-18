"""Real PostgreSQL P11 snapshot, atomic trace, replay and pack evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import perf_counter, sleep
from uuid import UUID, uuid4, uuid7

import pytest
from autplay.adapters.postgresql.models import (
    DeviceRow,
    OfflineRecommendationPackRow,
    RecommendationInputSnapshotRow,
    RecommendationItemRow,
    RecommendationPipelineVersionRow,
    RecommendationRequestRow,
    UserAccountRow,
)
from autplay.adapters.postgresql.recommendations import (
    SqlAlchemyOfflinePackRepository,
    SqlAlchemyRecommendationRuntime,
)
from autplay.application.recommendations import (
    RecommendationService,
    StaticRecommendationVersionRegistry,
)
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.recommendations import (
    RecommendationQuery,
    RecommendationSurface,
    ReplayInputUnavailable,
)
from psycopg import Error as PsycopgError
from sqlalchemy import create_engine, select, text, update
from sqlalchemy import event as sa_event
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker


def _principal(session: Session, name: str) -> Principal:
    user_id, device_id = uuid4(), uuid4()
    session.add(UserAccountRow(user_id=user_id, display_name=name, role="USER", status="ACTIVE"))
    session.flush()
    session.add(
        DeviceRow(
            device_id=device_id,
            user_id=user_id,
            device_name=name,
            platform="ANDROID",
            app_version="p11",
        )
    )
    session.flush()
    return Principal(user_id, device_id, uuid4(), AccountRole.USER)


def _vault_recording(session: Session, user_id: UUID, *, index: int, provider_id: UUID) -> UUID:
    credit_id, recording_id, object_id, variant_id = uuid4(), uuid4(), uuid4(), uuid4()
    session.execute(
        text(
            "INSERT INTO catalog.artist_credit (artist_credit_id, display_name, normalized_name) "
            "VALUES (:id, :name, :name)"
        ),
        {"id": credit_id, "name": f"artist-{index}"},
    )
    session.execute(
        text(
            "INSERT INTO catalog.recording "
            "(recording_id, artist_credit_id, title, normalized_title, duration_ms, "
            "identity_status) VALUES (:id, :credit, :title, :title, 180000, 'ACTIVE')"
        ),
        {"id": recording_id, "credit": credit_id, "title": f"recording-{index}"},
    )
    session.execute(
        text(
            "INSERT INTO vault.vault_object "
            "(vault_object_id, sha256, byte_size, detected_mime_type, commit_status, "
            "committed_at) VALUES (:id, :digest, 1024, 'audio/flac', 'COMMITTED', now())"
        ),
        {"id": object_id, "digest": bytes([index + 1]) * 32},
    )
    session.execute(
        text(
            "INSERT INTO vault.vault_replica "
            "(vault_object_id, storage_backend, storage_key, replica_status, verified_at) "
            "VALUES (:object, 'FILESYSTEM', :key, 'AVAILABLE', now())"
        ),
        {"object": object_id, "key": f"p11/{index}"},
    )
    session.execute(
        text(
            "INSERT INTO vault.audio_variant "
            "(audio_variant_id, recording_id, vault_object_id, codec, container, "
            "sample_rate_hz, channels, duration_ms, validation_status) "
            "VALUES (:id, :recording, :object, 'flac', 'flac', 44100, 2, 180000, 'VALID')"
        ),
        {"id": variant_id, "recording": recording_id, "object": object_id},
    )
    session.execute(
        text(
            "INSERT INTO vault.acquisition_record "
            "(audio_variant_id, provider_id, authorized_by_user_id, rights_capability) "
            "VALUES (:variant, :provider, :user, 'USER_UPLOAD')"
        ),
        {"variant": variant_id, "provider": provider_id, "user": user_id},
    )
    return recording_id


def test_owner_snapshot_atomic_trace_replay_pack_and_latency(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions() as session:
            owner, other = _principal(session, "p11-owner"), _principal(session, "p11-other")
            provider_id = uuid4()
            session.execute(
                text(
                    "INSERT INTO identity.source_provider "
                    "(provider_id, provider_key, display_name, adapter_id, adapter_version) "
                    "VALUES (:id, 'p11.fixture', 'P11 fixture', 'fixture', '1')"
                ),
                {"id": provider_id},
            )
            owner_recordings = {
                _vault_recording(session, owner.user_id, index=index, provider_id=provider_id)
                for index in range(4)
            }
            foreign_recording = _vault_recording(
                session, other.user_id, index=10, provider_id=provider_id
            )
            session.commit()

        runtime = SqlAlchemyRecommendationRuntime(sessions)
        service = RecommendationService(
            snapshots=runtime,
            traces=runtime,
            registry=StaticRecommendationVersionRegistry(),
            ids=uuid7,
            clock=lambda: datetime.now(UTC),
            packs=SqlAlchemyOfflinePackRepository(runtime),
        )
        started = perf_counter()
        response = service.recommend(
            RecommendationQuery(
                owner.user_id,
                RecommendationSurface.RECOMMENDATIONS,
                limit=4,
                seed=19,
            )
        )
        latency_ms = (perf_counter() - started) * 1000

        assert {item.recording_id for item in response.items} == owner_recordings
        assert foreign_recording not in {item.recording_id for item in response.items}
        assert latency_ms < 2_000
        exact = service.exact_replay(owner.user_id, response.request.recommendation_request_id)
        replayed = service.algorithmic_replay(
            owner.user_id, response.request.recommendation_request_id
        )
        assert exact.items == response.items == replayed.items
        assert exact.request.request_sha256 == replayed.request.request_sha256

        pack = service.offline_pack(
            RecommendationQuery(
                owner.user_id,
                RecommendationSurface.OFFLINE_PACK,
                limit=4,
                seed=19,
            ),
            device_id=owner.device_id,
            ttl=timedelta(days=7),
        )
        assert pack.payload_encoding == "RAW_JSON"
        with sessions() as session:
            request = session.get(
                RecommendationRequestRow, response.request.recommendation_request_id
            )
            items = list(
                session.scalars(
                    select(RecommendationItemRow).where(
                        RecommendationItemRow.recommendation_request_id
                        == response.request.recommendation_request_id
                    )
                )
            )
            stored_pack = session.get(OfflineRecommendationPackRow, pack.offline_pack_id)
            assert request is not None and request.request_sha256 is not None
            assert len(items) == len(response.items)
            assert all(item.contributions for item in items)
            assert stored_pack is not None
            assert stored_pack.recommendation_request_id == pack.recommendation_request_id
            assert runtime.exact(other.user_id, response.request.recommendation_request_id) is None
            with pytest.raises(IntegrityError) as owner_mismatch:
                session.execute(
                    update(OfflineRecommendationPackRow)
                    .where(OfflineRecommendationPackRow.offline_pack_id == pack.offline_pack_id)
                    .values(user_id=other.user_id, device_id=other.device_id)
                )
                session.flush()
            assert isinstance(owner_mismatch.value.orig, PsycopgError)
            assert owner_mismatch.value.orig.diag.constraint_name == "fk_offline_pack_request_owner"
            session.rollback()
    finally:
        engine.dispose()


def test_manifest_immutability_atomic_failure_and_snapshot_owner(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions() as session:
            owner, other = _principal(session, "atomic-owner"), _principal(session, "atomic-other")
            provider_id = uuid4()
            session.execute(
                text(
                    "INSERT INTO identity.source_provider "
                    "(provider_id, provider_key, display_name, adapter_id, adapter_version) "
                    "VALUES (:id, 'p11.atomic', 'P11 atomic', 'fixture', '1')"
                ),
                {"id": provider_id},
            )
            recording_id = _vault_recording(
                session, owner.user_id, index=20, provider_id=provider_id
            )
            session.commit()
        runtime = SqlAlchemyRecommendationRuntime(sessions)
        service = RecommendationService(
            snapshots=runtime,
            traces=runtime,
            registry=StaticRecommendationVersionRegistry(),
            ids=uuid7,
            clock=lambda: datetime.now(UTC),
        )
        response = service.recommend(
            RecommendationQuery(owner.user_id, RecommendationSurface.RECOMMENDATIONS)
        )
        assert runtime.load(other.user_id, response.request.snapshot.snapshot_id) is None

        bad_request_id = uuid7()
        bad = replace(
            response,
            request=replace(response.request, recommendation_request_id=bad_request_id),
            items=(replace(response.items[0], recording_id=uuid4()),),
        )
        with pytest.raises(IntegrityError):
            runtime.save(bad)
        with sessions() as session:
            assert session.get(RecommendationRequestRow, bad_request_id) is None
            with pytest.raises(DBAPIError, match="immutable"):
                session.execute(
                    update(RecommendationPipelineVersionRow)
                    .where(
                        RecommendationPipelineVersionRow.pipeline_key == "cpu-baseline",
                        RecommendationPipelineVersionRow.version == "1",
                    )
                    .values(manifest={"tampered": True})
                )
                session.flush()
            session.rollback()
            snapshot = session.get(
                RecommendationInputSnapshotRow, response.request.snapshot.snapshot_id
            )
            assert snapshot is not None
            loaded = runtime.load(owner.user_id, snapshot.recommendation_input_snapshot_id)
            assert loaded is not None
            assert recording_id in {track.recording_id for track in loaded.tracks}
    finally:
        engine.dispose()


def test_expired_snapshot_is_purged_without_losing_exact_replay(database_url: str) -> None:
    """Bounded cleanup removes personal inputs while retaining response-only replay."""
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions() as session:
            owner = _principal(session, "retention-owner")
            provider_id = uuid4()
            session.execute(
                text(
                    "INSERT INTO identity.source_provider "
                    "(provider_id, provider_key, display_name, adapter_id, adapter_version) "
                    "VALUES (:id, 'p11.retention', 'P11 retention', 'fixture', '1')"
                ),
                {"id": provider_id},
            )
            _vault_recording(session, owner.user_id, index=30, provider_id=provider_id)
            session.commit()
        runtime = SqlAlchemyRecommendationRuntime(sessions)
        service = RecommendationService(
            snapshots=runtime,
            traces=runtime,
            registry=StaticRecommendationVersionRegistry(),
            ids=uuid7,
            clock=lambda: datetime.now(UTC),
            snapshot_retention=timedelta(seconds=1),
        )
        first = service.recommend(
            RecommendationQuery(owner.user_id, RecommendationSurface.RECOMMENDATIONS)
        )
        sleep(1.1)
        service.recommend(RecommendationQuery(owner.user_id, RecommendationSurface.RECOMMENDATIONS))

        exact = service.exact_replay(owner.user_id, first.request.recommendation_request_id)
        assert exact.items == first.items
        with pytest.raises(ReplayInputUnavailable):
            service.algorithmic_replay(owner.user_id, first.request.recommendation_request_id)
        with sessions() as session:
            assert (
                session.get(RecommendationInputSnapshotRow, first.request.snapshot.snapshot_id)
                is None
            )
            request = session.get(RecommendationRequestRow, first.request.recommendation_request_id)
            assert request is not None
            assert request.recommendation_input_snapshot_id is None
            assert request.input_snapshot_sha256 is not None
    finally:
        engine.dispose()


def test_snapshot_limit_selects_the_same_recent_5000_recordings(database_url: str) -> None:
    """Bounded snapshot truncation has a stable, recorded global selection policy."""
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions() as session:
            owner = _principal(session, "snapshot-limit")
            provider_id, credit_id = uuid4(), uuid4()
            session.execute(
                text(
                    "INSERT INTO identity.source_provider "
                    "(provider_id, provider_key, display_name, adapter_id, adapter_version) "
                    "VALUES (:id, 'p11.limit', 'P11 limit', 'fixture', '1')"
                ),
                {"id": provider_id},
            )
            session.execute(
                text(
                    "INSERT INTO catalog.artist_credit "
                    "(artist_credit_id, display_name, normalized_name) "
                    "VALUES (:id, 'Limit artist', 'limit artist')"
                ),
                {"id": credit_id},
            )
            session.execute(
                text(
                    "CREATE TEMP TABLE p11_candidates ON COMMIT DROP AS "
                    "SELECT n, uuidv7() recording_id, uuidv7() object_id, uuidv7() variant_id "
                    "FROM generate_series(1, 5001) AS value(n)"
                )
            )
            session.execute(
                text(
                    "INSERT INTO catalog.recording "
                    "(recording_id, artist_credit_id, title, normalized_title, duration_ms, "
                    "identity_status) SELECT recording_id, :credit, 'track-' || n, "
                    "'track-' || n, 180000, 'ACTIVE' FROM p11_candidates"
                ),
                {"credit": credit_id},
            )
            session.execute(
                text(
                    "INSERT INTO vault.vault_object "
                    "(vault_object_id, sha256, byte_size, detected_mime_type, commit_status, "
                    "committed_at) SELECT object_id, decode(lpad(to_hex(n), 64, '0'), 'hex'), "
                    "1024, 'audio/flac', 'COMMITTED', now() FROM p11_candidates"
                )
            )
            session.execute(
                text(
                    "INSERT INTO vault.vault_replica "
                    "(vault_object_id, storage_backend, storage_key, replica_status, verified_at) "
                    "SELECT object_id, 'FILESYSTEM', 'p11/limit/' || n, 'AVAILABLE', now() "
                    "FROM p11_candidates"
                )
            )
            session.execute(
                text(
                    "INSERT INTO vault.audio_variant "
                    "(audio_variant_id, recording_id, vault_object_id, codec, container, "
                    "sample_rate_hz, channels, duration_ms, validation_status, created_at) "
                    "SELECT variant_id, recording_id, object_id, 'flac', 'flac', 44100, 2, "
                    "180000, 'VALID', now() - make_interval(secs => 5001 - n) "
                    "FROM p11_candidates"
                )
            )
            session.execute(
                text(
                    "INSERT INTO vault.acquisition_record "
                    "(audio_variant_id, provider_id, authorized_by_user_id, rights_capability) "
                    "SELECT variant_id, :provider, :owner, 'USER_UPLOAD' FROM p11_candidates"
                ),
                {"provider": provider_id, "owner": owner.user_id},
            )
            oldest = session.scalar(text("SELECT recording_id FROM p11_candidates WHERE n = 1"))
            session.commit()

        runtime = SqlAlchemyRecommendationRuntime(sessions)
        first = runtime.capture(owner.user_id, retained_until=datetime.now(UTC) + timedelta(days=1))
        second = runtime.capture(
            owner.user_id, retained_until=datetime.now(UTC) + timedelta(days=1)
        )

        assert len(first.tracks) == len(second.tracks) == 5_000
        assert first.reference.input_snapshot_sha256 == second.reference.input_snapshot_sha256
        assert tuple(track.recording_id for track in first.tracks) == tuple(
            track.recording_id for track in second.tracks
        )
        assert oldest not in {track.recording_id for track in first.tracks}
        with sessions() as session:
            row = session.get(RecommendationInputSnapshotRow, first.reference.snapshot_id)
            assert row is not None
            document = row.snapshot_document
            assert isinstance(document, dict)
            assert document["selection_policy"] == "most_recent_added_then_recording_id_v1"
    finally:
        engine.dispose()


def test_snapshot_watermark_uses_the_same_repeatable_read_view(database_url: str) -> None:
    """An interaction committed between snapshot statements is not falsely watermarked."""
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions() as session:
            owner = _principal(session, "snapshot-mvcc")
            session.commit()
        injected = False

        def inject_interaction(
            connection: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            del connection, cursor, parameters, context, executemany
            nonlocal injected
            if injected or "COALESCE(max(server_sequence), 0)" not in statement:
                return
            injected = True
            with engine.begin() as concurrent:
                concurrent.execute(
                    text(
                        "INSERT INTO sync.sync_event "
                        "(event_id, user_id, event_type, schema_version, aggregate_type, "
                        "aggregate_id) VALUES (:event, :owner, 'LISTENING_EVENT_RECORDED', "
                        "1, 'LISTENING_EVENT', :aggregate)"
                    ),
                    {"event": uuid4(), "owner": owner.user_id, "aggregate": uuid4()},
                )

        sa_event.listen(engine, "before_cursor_execute", inject_interaction)
        try:
            runtime = SqlAlchemyRecommendationRuntime(sessions)
            snapshot = runtime.capture(
                owner.user_id, retained_until=datetime.now(UTC) + timedelta(days=1)
            )
        finally:
            sa_event.remove(engine, "before_cursor_execute", inject_interaction)

        assert injected
        assert snapshot.reference.interaction_watermark == 0
        with sessions() as session:
            assert (
                session.scalar(
                    text("SELECT max(server_sequence) FROM sync.sync_event WHERE user_id = :owner"),
                    {"owner": owner.user_id},
                )
                == 1
            )
    finally:
        engine.dispose()
