"""V2 uses the real ingest identity and playback ACL, retaining replay evidence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4, uuid7

from autplay.adapters.postgresql.models import LibraryEntryRow, UserAccountRow, UserTrackRefRow
from autplay.adapters.postgresql.recommendations import SqlAlchemyRecommendationRuntime
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.application.music_library import MusicLibraryService
from autplay.application.recommendations import (
    RecommendationPipelineRunner,
    RecommendationService,
    StaticRecommendationVersionRegistry,
    baseline_pipeline_definition,
)
from autplay.domain.recommendations import RecommendationQuery, RecommendationSurface
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def test_owner_review_verified_audio_serving_and_frozen_replay(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    owner, other, ref_id, object_id, variant_id = (uuid4() for _ in range(5))
    try:
        with sessions.begin() as session:
            for user in (owner, other):
                session.add(
                    UserAccountRow(user_id=user, display_name="Policy fixture", role="USER")
                )
            session.flush()
            session.add(
                UserTrackRefRow(
                    user_track_ref_id=ref_id,
                    user_id=owner,
                    raw_title="Verified upload",
                    raw_artist="Fixture",
                )
            )
            session.flush()
            session.add(
                LibraryEntryRow(
                    user_id=owner,
                    user_track_ref_id=ref_id,
                    source="SEARCH",
                    availability_status="VAULT",
                )
            )
            session.flush()
            recording = MusicLibraryService.prepare_in_transaction(session, owner, ref_id)
            values = {"recording": recording, "object": object_id, "variant": variant_id}
            session.execute(
                text("""
                INSERT INTO vault.vault_object(vault_object_id,sha256,byte_size,
                    detected_mime_type,commit_status,committed_at)
                VALUES (:object,decode(repeat('ab',32),'hex'),1024,'audio/flac','COMMITTED',now())
            """),
                values,
            )
            session.execute(
                text("""
                INSERT INTO vault.audio_variant(audio_variant_id,recording_id,vault_object_id,
                    codec,container,sample_rate_hz,channels,duration_ms,validation_status)
                VALUES (:variant,:recording,:object,'flac','flac',48000,2,180000,'VALID')
            """),
                values,
            )
            session.execute(
                text("""
                INSERT INTO vault.recording_canonical_variant(recording_id,audio_variant_id,
                    policy_version) VALUES (:recording,:variant,'owner-verified-upload-v1')
            """),
                values,
            )
            session.execute(
                text("""
                INSERT INTO vault.vault_replica(vault_object_id,storage_backend,storage_key,
                    replica_status,verified_at)
                VALUES (:object,'LOCAL_FILESYSTEM','synthetic-policy-fixture','AVAILABLE',now())
            """),
                values,
            )
            assert PostgresVaultRuntime(session).resolve_owner_playback_variant(owner, ref_id) == (
                variant_id
            )
        runtime = SqlAlchemyRecommendationRuntime(sessions, snapshot_policy_version="2")
        service = RecommendationService(
            snapshots=runtime,
            traces=runtime,
            registry=StaticRecommendationVersionRegistry(baseline_pipeline_definition("2")),
            ids=uuid7,
            clock=lambda: datetime.now(UTC),
        )
        query = RecommendationQuery(owner, RecommendationSurface.OFFLINE_PACK)
        response = service.recommend(query)
        assert [item.recording_id for item in response.items] == [recording]
        snapshot = runtime.load(owner, response.request.snapshot.snapshot_id)
        assert snapshot is not None and snapshot.policy_version == "2"
        assert snapshot.tracks[0].identity_status == "PROVISIONAL"
        assert snapshot.tracks[0].owner_identity_decision_id is not None
        assert snapshot.tracks[0].verified_audio_variant_id == variant_id
        assert (
            RecommendationPipelineRunner().run(query, snapshot, baseline_pipeline_definition())
            == ()
        )
        assert (
            service.recommend(RecommendationQuery(other, RecommendationSurface.OFFLINE_PACK)).items
            == ()
        )
        with sessions.begin() as session:
            session.execute(
                text("UPDATE vault.vault_replica SET verified_at=NULL WHERE vault_object_id=:id"),
                {"id": object_id},
            )
        assert service.recommend(query).items == ()
        replay = service.algorithmic_replay(owner, response.request.recommendation_request_id)
        assert replay.items == response.items
        loaded = runtime.load(owner, snapshot.reference.snapshot_id)
        assert loaded == snapshot
        with sessions.begin() as session:
            session.execute(
                text("UPDATE vault.vault_replica SET verified_at=now() WHERE vault_object_id=:id"),
                {"id": object_id},
            )
            session.execute(
                text(
                    "UPDATE library.library_entry SET removed_at=now() WHERE user_track_ref_id=:id"
                ),
                {"id": ref_id},
            )
        assert (
            runtime.capture(owner, retained_until=datetime.now(UTC) + timedelta(days=1)).tracks
            == ()
        )
    finally:
        engine.dispose()
