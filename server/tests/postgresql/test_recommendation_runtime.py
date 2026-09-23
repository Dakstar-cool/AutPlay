"""Real PostgreSQL P11 snapshot, atomic trace, replay and pack evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import perf_counter, sleep
from uuid import UUID, uuid4, uuid7

import pytest
from psycopg import Error as PsycopgError
from sqlalchemy import create_engine, func, insert, select, text, update
from sqlalchemy import event as sa_event
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.adaptive_recommendations import (
    SqlAlchemyAdaptiveRecommendationRepository,
)
from autplay.adapters.postgresql.models import (
    DeviceRow,
    OfflineRecommendationPackRow,
    RecommendationInputSnapshotRow,
    RecommendationItemRow,
    RecommendationPipelineVersionRow,
    RecommendationRequestRow,
    SonaCaptureBundleRow,
    SonaCaptureLineageCursorRow,
    UserAccountRow,
)
from autplay.adapters.postgresql.recommendations import (
    SqlAlchemyOfflinePackRepository,
    SqlAlchemyRecommendationRuntime,
)
from autplay.adapters.postgresql.sona_capture import SqlAlchemySonaCaptureWriter
from autplay.application.recommendations import (
    RecommendationService,
    StaticRecommendationVersionRegistry,
)
from autplay.application.sona_capture import SonaCaptureBundleV1
from autplay.application.training_consent import TrainingCaptureGrant
from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    project_temporal_profile,
)
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.recommendations import (
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationResponse,
    RecommendationSurface,
    ReplayInputUnavailable,
)


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
            atomic_writer=runtime,
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
            atomic_writer=runtime,
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


def test_atomic_cpu_failure_rolls_back_captured_snapshot(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions() as session:
            owner = _principal(session, "atomic-rollback-owner")
            session.commit()
        runtime = SqlAlchemyRecommendationRuntime(sessions)
        pipeline = StaticRecommendationVersionRegistry().resolve("cpu-baseline")

        def fail_after_capture(_snapshot: object) -> RecommendationResponse:
            raise RuntimeError("CPU failpoint after snapshot capture")

        with pytest.raises(RuntimeError, match="CPU failpoint"):
            runtime.capture_run_save(
                user_id=owner.user_id,
                request_time=datetime.now(UTC),
                capture_eligible=False,
                retained_until=datetime.now(UTC) + timedelta(days=30),
                pipeline=pipeline,
                build_response=fail_after_capture,
            )
        with sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RecommendationInputSnapshotRow)
                    .where(RecommendationInputSnapshotRow.user_id == owner.user_id)
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RecommendationRequestRow)
                    .where(RecommendationRequestRow.user_id == owner.user_id)
                )
                == 0
            )
    finally:
        engine.dispose()


def test_native_capture_hook_commits_only_with_bundle_and_cursor(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions() as session:
            owner = _principal(session, "native-hook-owner")
            session.commit()
        query = RecommendationQuery(owner.user_id, RecommendationSurface.RECOMMENDATIONS)

        def service_for(
            capture: Callable[
                [
                    Session,
                    RecommendationInputSnapshot,
                    RecommendationResponse,
                    TrainingCaptureGrant,
                ],
                SonaCaptureBundleV1 | None,
            ],
        ) -> RecommendationService:
            runtime = SqlAlchemyRecommendationRuntime(
                sessions,
                native_capture=capture,
                native_capture_gate=lambda _session, _owner: TrainingCaptureGrant(
                    1, sha256(b"fixture").hexdigest()
                ),
            )
            return RecommendationService(
                snapshots=runtime,
                traces=runtime,
                registry=StaticRecommendationVersionRegistry(),
                ids=uuid7,
                clock=lambda: datetime.now(UTC),
                atomic_writer=runtime,
                snapshot_retention=timedelta(days=180, seconds=1),
            )

        def missing(
            _session: Session,
            _snapshot: RecommendationInputSnapshot,
            _response: RecommendationResponse,
            _grant: TrainingCaptureGrant,
        ) -> None:
            pass

        with pytest.raises(ValueError, match="both consent gate and writer"):
            SqlAlchemyRecommendationRuntime(sessions, native_capture=missing)
        with pytest.raises(ValueError, match="bundle and cursor are required"):
            service_for(missing).recommend(query)

        def crash(
            _session: Session,
            _snapshot: RecommendationInputSnapshot,
            _response: RecommendationResponse,
            _grant: TrainingCaptureGrant,
        ) -> None:
            raise RuntimeError("capture failpoint after request flush")

        with pytest.raises(RuntimeError, match="capture failpoint"):
            service_for(crash).recommend(query)
        with sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RecommendationInputSnapshotRow)
                    .where(RecommendationInputSnapshotRow.user_id == owner.user_id)
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RecommendationRequestRow)
                    .where(RecommendationRequestRow.user_id == owner.user_id)
                )
                == 0
            )

        def capture(
            session: Session,
            snapshot: RecommendationInputSnapshot,
            response: RecommendationResponse,
            _grant: TrainingCaptureGrant,
        ) -> None:
            request_id, created_at = (
                response.request.recommendation_request_id,
                response.request.created_at,
            )
            digest = sha256(b"fixture").digest()
            session.execute(
                insert(SonaCaptureBundleRow).values(
                    recommendation_request_id=request_id,
                    user_id=owner.user_id,
                    baseline_snapshot_sha256=bytes.fromhex(
                        snapshot.reference.input_snapshot_sha256
                    ),
                    temporal_snapshot_sha256=digest,
                    candidate_membership_sha256=digest,
                    p11_ranking_sha256=digest,
                    bundle_sha256=sha256(b"{}").digest(),
                    consent_receipt_sha256=digest,
                    consent_generation=1,
                    cutoff_at_ms=0,
                    interaction_watermark=snapshot.reference.interaction_watermark,
                    universe_count=len(snapshot.tracks),
                    eligible_count=0,
                    bundle_document=b"{}",
                    created_at=created_at,
                    expires_at=created_at + timedelta(days=180),
                )
            )
            session.execute(
                insert(SonaCaptureLineageCursorRow).values(
                    recommendation_request_id=request_id,
                    user_id=owner.user_id,
                    expires_at=created_at + timedelta(days=180),
                )
            )

        service = service_for(capture)
        with pytest.raises(ValueError, match="prepared native capture bundle is required"):
            service.recommend(query)
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(RecommendationRequestRow)) == 0
            assert session.scalar(select(func.count()).select_from(SonaCaptureBundleRow)) == 0
    finally:
        engine.dispose()


def test_native_capture_writer_joins_p11_and_temporal_transaction(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions.begin() as session:
            owner = _principal(session, "native-writer-owner")
            provider_id = uuid7()
            session.execute(
                text(
                    "INSERT INTO identity.source_provider "
                    "(provider_id,provider_key,display_name,adapter_id,adapter_version) "
                    "VALUES (:id,'sona.writer.fixture','Sona writer fixture','fixture','1')"
                ),
                {"id": provider_id},
            )
            recording_id = _vault_recording(
                session, owner.user_id, index=41, provider_id=provider_id
            )
        adaptive = SqlAlchemyAdaptiveRecommendationRepository(sessions)
        writer = SqlAlchemySonaCaptureWriter()
        fail_after_write = True
        tamper_return = False
        tamper_grant = False

        def capture(
            session: Session,
            snapshot: RecommendationInputSnapshot,
            response: RecommendationResponse,
            grant: TrainingCaptureGrant,
        ) -> SonaCaptureBundleV1:
            profile = project_temporal_profile(
                owner_user_id=owner.user_id,
                cutoff_at_ms=int(response.request.created_at.timestamp() * 1000),
                interaction_watermark=snapshot.reference.interaction_watermark,
                evidence=(),
            )
            reference = adaptive.save_snapshot(
                baseline=snapshot,
                profile=profile,
                evidence=(),
                retained_until=snapshot.retained_until,
                policy=DEFAULT_ADAPTIVE_FEATURE_POLICY,
                session=session,
            )
            temporal = adaptive.load_sona_snapshot(
                owner.user_id, reference.snapshot_id, session=session
            )
            assert temporal is not None
            bundle = writer.write(
                session,
                baseline=snapshot,
                response=response,
                temporal=temporal,
                consent_receipt_sha256=grant.receipt_sha256,
                consent_generation=grant.revision,
            )
            assert bundle.universe_count == bundle.eligible_count == 1
            assert str(recording_id) in bundle.document.decode("utf-8")
            if fail_after_write:
                raise RuntimeError("crash after complete native capture")
            if tamper_grant:
                return replace(bundle, consent_receipt_sha256="f" * 64)
            if tamper_return:
                return replace(bundle, p11_ranking_sha256="f" * 64)
            return bundle

        runtime = SqlAlchemyRecommendationRuntime(
            sessions,
            native_capture=capture,
            native_capture_gate=lambda _session, _owner: TrainingCaptureGrant(
                1, sha256(b"independent-consent-fixture").hexdigest()
            ),
        )
        service = RecommendationService(
            snapshots=runtime,
            traces=runtime,
            registry=StaticRecommendationVersionRegistry(),
            ids=uuid7,
            clock=lambda: datetime.now(UTC),
            atomic_writer=runtime,
            snapshot_retention=timedelta(days=180, seconds=1),
        )
        query = RecommendationQuery(owner.user_id, RecommendationSurface.RECOMMENDATIONS)
        with pytest.raises(RuntimeError, match="crash after complete native capture"):
            service.recommend(query)
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(RecommendationRequestRow)) == 0
            assert session.scalar(select(func.count()).select_from(SonaCaptureBundleRow)) == 0
            assert (
                session.scalar(text("SELECT count(*) FROM ml.recommendation_temporal_snapshot"))
                == 0
            )

        fail_after_write = False
        tamper_return = True
        with pytest.raises(ValueError, match="native capture does not bind"):
            service.recommend(query)
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(RecommendationRequestRow)) == 0
            assert session.scalar(select(func.count()).select_from(SonaCaptureBundleRow)) == 0

        tamper_return = False
        tamper_grant = True
        with pytest.raises(ValueError, match="native capture consent receipt mismatch"):
            service.recommend(query)
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(RecommendationRequestRow)) == 0
            assert session.scalar(select(func.count()).select_from(SonaCaptureBundleRow)) == 0

        tamper_grant = False
        response = service.recommend(query)
        with sessions() as session:
            bundle = session.get(SonaCaptureBundleRow, response.request.recommendation_request_id)
            cursor = session.get(
                SonaCaptureLineageCursorRow, response.request.recommendation_request_id
            )
            assert bundle is not None and cursor is not None
            assert bundle.eligible_count == 1
            assert bundle.bundle_sha256 == sha256(bundle.bundle_document).digest()
            assert (
                session.scalar(text("SELECT count(*) FROM ml.recommendation_temporal_snapshot"))
                == 1
            )
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
