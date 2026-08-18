"""Real PostgreSQL P12 registry, fencing, rollout and owner-safe retrieval evidence."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.postgresql.enrichment import (
    EnrichmentPersistenceError,
    SqlAlchemyEnrichmentRuntime,
    SqlAlchemyTrackEmbeddingReader,
)
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import UserAccountRow
from autplay.adapters.postgresql.models.ml import EmbeddingModelRow, RecordingEmbeddingRow
from autplay.adapters.postgresql.models.types import JsonValue
from autplay.application.enrichment import EmbeddingJobHandler
from autplay.application.job_worker import (
    JobHandlerRegistry,
    JobWorker,
    JobWorkerSettings,
    WorkerOutcome,
)
from autplay.domain.enrichment import (
    ApprovedEmbeddingModel,
    DecodedAudioSegment,
    EmbeddingJobTarget,
    EmbeddingResult,
    GpuBenchmarkReport,
    vector_sha256,
)
from autplay.domain.jobs import JobKey, RetryPolicy
from autplay.ports.jobs import EnqueueJob
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

JOB_KEY = JobKey("ml.audio-embedding", 1)


def _model(
    version: str,
) -> tuple[ApprovedEmbeddingModel, dict[str, JsonValue], dict[str, JsonValue]]:
    artifact: dict[str, JsonValue] = {"artifact": f"fixture-{version}.bin", "schema": 1}
    preprocessing: dict[str, JsonValue] = {
        "channels": 1,
        "sample_rate_hz": 16_000,
        "segment_duration_ms": 10_000,
        "version": version,
    }
    return (
        ApprovedEmbeddingModel(
            embedding_model_id=uuid4(),
            model_key="p12-fixture",
            version=version,
            task="AUDIO_EMBEDDING",
            source="fixture://reviewed-model",
            source_revision=f"revision-{version}",
            artifact_filename=f"fixture-{version}.bin",
            artifact_format="fixture",
            artifact_byte_size=100,
            weights_sha256=hashlib.sha256(f"weights-{version}".encode()).digest(),
            manifest_sha256=hashlib.sha256(rfc8785.dumps(artifact)).digest(),
            preprocessing_sha256=hashlib.sha256(rfc8785.dumps(preprocessing)).digest(),
            license_id="fixture-license",
            runtime="fixture-runtime",
            runtime_revision="1",
            inference_precision="fp32",
            input_sample_rate_hz=16_000,
            segment_duration_ms=10_000,
            preprocessing_version=version,
            pooling_strategy="mean-normalized",
            dimension=3,
            status="BENCHMARK",
        ),
        artifact,
        preprocessing,
    )


def _vault_recording(
    session: Session, user_id: UUID, provider_id: UUID, *, label: str
) -> tuple[UUID, UUID]:
    credit_id, recording_id, object_id, variant_id = uuid4(), uuid4(), uuid4(), uuid4()
    session.execute(
        text(
            "INSERT INTO catalog.artist_credit "
            "(artist_credit_id, display_name, normalized_name) VALUES (:id, :label, :label)"
        ),
        {"id": credit_id, "label": f"artist-{label}"},
    )
    session.execute(
        text(
            "INSERT INTO catalog.recording "
            "(recording_id, artist_credit_id, title, normalized_title, duration_ms, "
            "identity_status) VALUES (:id, :credit, :label, :label, 180000, 'ACTIVE')"
        ),
        {"id": recording_id, "credit": credit_id, "label": label},
    )
    session.execute(
        text(
            "INSERT INTO vault.vault_object "
            "(vault_object_id, sha256, byte_size, detected_mime_type, commit_status, "
            "committed_at) VALUES (:id, :digest, 1024, 'audio/flac', 'COMMITTED', now())"
        ),
        {"id": object_id, "digest": hashlib.sha256(label.encode()).digest()},
    )
    session.execute(
        text(
            "INSERT INTO vault.vault_replica "
            "(vault_object_id, storage_backend, storage_key, replica_status, verified_at) "
            "VALUES (:object, 'FILESYSTEM', :key, 'AVAILABLE', now())"
        ),
        {"object": object_id, "key": f"p12/{label}"},
    )
    session.execute(
        text(
            "INSERT INTO vault.audio_variant "
            "(audio_variant_id, recording_id, vault_object_id, codec, container, "
            "sample_rate_hz, channels, duration_ms, validation_status) "
            "VALUES (:variant, :recording, :object, 'flac', 'flac', 44100, 2, "
            "180000, 'VALID')"
        ),
        {"variant": variant_id, "recording": recording_id, "object": object_id},
    )
    session.execute(
        text(
            "INSERT INTO vault.acquisition_record "
            "(audio_variant_id, provider_id, authorized_by_user_id, rights_capability) "
            "VALUES (:variant, :provider, :user, 'USER_UPLOAD')"
        ),
        {"variant": variant_id, "provider": provider_id, "user": user_id},
    )
    return recording_id, variant_id


def _report(model: ApprovedEmbeddingModel, *, label: str) -> GpuBenchmarkReport:
    return GpuBenchmarkReport(
        report_version=1,
        status="COMPLETE",
        dataset_id="p11-shadow-fixture",
        dataset_version="1",
        dataset_snapshot_sha256=hashlib.sha256(b"same-p11-dataset").hexdigest(),
        interaction_schema_version=1,
        interaction_watermark=100,
        model_manifest_sha256=model.manifest_sha256.hex(),
        preprocessing_sha256=model.preprocessing_sha256.hex(),
        environment={"device_uuid": "GPU-fixture", "selector": "auto"},
        metrics={"quality_delta": 0.1, "tracks_per_hour": 1000, "label": None},
        created_at=datetime.now(UTC),
    )


class _FixturePreprocessor:
    def decode(
        self, target: EmbeddingJobTarget, model: ApprovedEmbeddingModel
    ) -> tuple[DecodedAudioSegment, ...]:
        del target, model
        return (
            DecodedAudioSegment(0, 0, struct.pack("<f", 0.25)),
            DecodedAudioSegment(1, 10_000, struct.pack("<f", 0.5)),
        )


class _FixtureEmbedder:
    def __init__(self, model: ApprovedEmbeddingModel) -> None:
        self._model = model
        self.batch_sizes: list[int] = []

    @property
    def model_id(self) -> UUID:
        return self._model.embedding_model_id

    @property
    def weights_sha256(self) -> bytes:
        return self._model.weights_sha256

    def embed(
        self, segments: Sequence[DecodedAudioSegment], *, batch_size: int
    ) -> tuple[tuple[float, ...], tuple[tuple[str, float], ...]]:
        assert len(segments) == 2
        self.batch_sizes.append(batch_size)
        return (1.0, 0.0, 0.0), ()


def test_enrichment_worker_restart_recovers_checkpoint_and_publishes_once(
    database_url: str,
) -> None:
    """A new worker resumes durable OOM state after an expired mid-job lease."""

    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    try:
        runtime = SqlAlchemyEnrichmentRuntime(sessions)
        model, artifact, preprocessing = _model("restart")
        assert runtime.register(
            model,
            artifact_manifest=artifact,
            preprocessing_manifest=preprocessing,
            license_review_reference="review://p12/restart",
        )
        owner_id, provider_id = uuid4(), uuid4()
        with sessions() as session, session.begin():
            session.add(
                UserAccountRow(
                    user_id=owner_id,
                    display_name="p12-restart-owner",
                    role="USER",
                    status="ACTIVE",
                )
            )
            session.flush()
            session.execute(
                text(
                    "INSERT INTO identity.source_provider "
                    "(provider_id, provider_key, display_name, adapter_id, adapter_version) "
                    "VALUES (:id, 'p12.restart', 'P12 restart', 'fixture', '1')"
                ),
                {"id": provider_id},
            )
            recording_id, variant_id = _vault_recording(
                session, owner_id, provider_id, label="restart"
            )
        enrichment_job_id = uuid4()
        jobs = SqlAlchemyJobUnitOfWorkFactory(sessions)
        with jobs() as unit:
            enqueued = unit.jobs.enqueue(
                EnqueueJob(
                    key=JOB_KEY,
                    user_id=owner_id,
                    payload={"enrichment_job_id": str(enrichment_job_id)},
                )
            )
            unit.commit()
        with sessions() as session, session.begin():
            session.execute(
                text(
                    "INSERT INTO ml.enrichment_job "
                    "(enrichment_job_id, job_id, job_kind, recording_id, audio_variant_id, "
                    "embedding_model_id, expected_weights_sha256, "
                    "expected_preprocessing_sha256) VALUES "
                    "(:id, :job, 'AUDIO_EMBEDDING', :recording, :variant, :model, "
                    ":weights, :preprocessing)"
                ),
                {
                    "id": enrichment_job_id,
                    "job": enqueued.job_id,
                    "recording": recording_id,
                    "variant": variant_id,
                    "model": model.embedding_model_id,
                    "weights": model.weights_sha256,
                    "preprocessing": model.preprocessing_sha256,
                },
            )
        with jobs() as unit:
            abandoned = unit.jobs.claim(
                worker_id="gpu-crashed-process",
                supported=(JOB_KEY,),
                lease_interval=timedelta(seconds=10),
                limit=1,
            )[0]
            saved = unit.jobs.save_checkpoint(
                abandoned.fence,
                {"stage": "OOM_REDUCED", "batch_size": 2, "oom_reductions": 2},
                progress_current=None,
                progress_total=None,
            )
            assert saved is not None
            unit.commit()
        with sessions() as session, session.begin():
            session.execute(
                text(
                    "UPDATE jobs.job SET lease_deadline = now() - interval '1 second' "
                    "WHERE job_id = :job"
                ),
                {"job": enqueued.job_id},
            )

        embedder = _FixtureEmbedder(model)
        handler = EmbeddingJobHandler(
            jobs=runtime,
            models=runtime,
            preprocessor=_FixturePreprocessor(),
            embedder=embedder,
            writer=runtime,
            initial_batch_size=8,
            maximum_oom_reductions=3,
        )
        worker = JobWorker(
            uow_factory=jobs,
            worker_id="gpu-restarted-process",
            registry=JobHandlerRegistry({JOB_KEY: handler}),
            settings=JobWorkerSettings(
                lease_interval=timedelta(seconds=10),
                heartbeat_interval=timedelta(seconds=1),
                idle_poll_interval=timedelta(milliseconds=10),
                retry_policy=RetryPolicy(
                    max_attempts=4,
                    base_delay=timedelta(seconds=1),
                    max_delay=timedelta(seconds=1),
                    jitter_ratio=0,
                ),
            ),
        )
        recovered = worker.run_once()
        assert recovered.outcome is WorkerOutcome.IDLE
        assert recovered.recovered_count == 1
        with sessions() as session, session.begin():
            session.execute(
                text(
                    "UPDATE jobs.job SET scheduled_at = now() - interval '1 second' "
                    "WHERE job_id = :job"
                ),
                {"job": enqueued.job_id},
            )
        completed = worker.run_once()
        assert completed.outcome is WorkerOutcome.COMPLETED
        assert embedder.batch_sizes == [2]

        with sessions() as session:
            job = session.execute(
                text(
                    "SELECT state, attempt_count, checkpoint->>'stage' "
                    "FROM jobs.job WHERE job_id = :job"
                ),
                {"job": enqueued.job_id},
            ).one()
            attempts = session.execute(
                text(
                    "SELECT attempt_no, outcome FROM jobs.job_attempt "
                    "WHERE job_id = :job ORDER BY attempt_no"
                ),
                {"job": enqueued.job_id},
            ).all()
            published = session.scalar(
                select(func.count())
                .select_from(RecordingEmbeddingRow)
                .where(RecordingEmbeddingRow.recording_id == recording_id)
            )
        assert job == ("COMPLETED", 2, "PUBLISHED")
        assert [tuple(row) for row in attempts] == [(1, "LEASE_EXPIRED"), (2, "SUCCESS")]
        assert published == 1
    finally:
        engine.dispose()


def test_parallel_models_fenced_writer_rollout_and_owner_exact_retrieval(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    try:
        runtime = SqlAlchemyEnrichmentRuntime(sessions)
        model_a, artifact_a, preprocessing_a = _model("a")
        model_b, artifact_b, preprocessing_b = _model("b")
        model_c, artifact_c, preprocessing_c = _model("c")
        assert runtime.register(
            model_a,
            artifact_manifest=artifact_a,
            preprocessing_manifest=preprocessing_a,
            license_review_reference="review://p12/a",
        )
        assert not runtime.register(
            model_a,
            artifact_manifest=artifact_a,
            preprocessing_manifest=preprocessing_a,
            license_review_reference="review://p12/a",
        )
        assert runtime.register(
            model_b,
            artifact_manifest=artifact_b,
            preprocessing_manifest=preprocessing_b,
            license_review_reference="review://p12/b",
        )
        assert runtime.register(
            model_c,
            artifact_manifest=artifact_c,
            preprocessing_manifest=preprocessing_c,
            license_review_reference="review://p12/c",
        )
        with sessions() as session:
            model_c_row = session.get(EmbeddingModelRow, model_c.embedding_model_id)
            assert model_c_row is not None
            model_c_row.status = "ACTIVE"
            with pytest.raises(DBAPIError, match="current activation history"):
                session.flush()
            session.rollback()

        owner_id, other_id, provider_id = uuid4(), uuid4(), uuid4()
        with sessions() as session, session.begin():
            session.add_all(
                (
                    UserAccountRow(
                        user_id=owner_id, display_name="p12-owner", role="USER", status="ACTIVE"
                    ),
                    UserAccountRow(
                        user_id=other_id, display_name="p12-other", role="USER", status="ACTIVE"
                    ),
                )
            )
            session.flush()
            session.execute(
                text(
                    "INSERT INTO identity.source_provider "
                    "(provider_id, provider_key, display_name, adapter_id, adapter_version) "
                    "VALUES (:id, 'p12.fixture', 'P12 fixture', 'fixture', '1')"
                ),
                {"id": provider_id},
            )
            seed = _vault_recording(session, owner_id, provider_id, label="seed")
            near = _vault_recording(session, owner_id, provider_id, label="near")
            far = _vault_recording(session, owner_id, provider_id, label="far")
            foreign = _vault_recording(session, other_id, provider_id, label="foreign")

        jobs = SqlAlchemyJobUnitOfWorkFactory(sessions)
        with jobs() as unit:
            enqueued = unit.jobs.enqueue(
                EnqueueJob(key=JOB_KEY, user_id=owner_id, payload={"enrichment_job_id": "pending"})
            )
            unit.commit()
        with jobs() as unit:
            lease = unit.jobs.claim(
                worker_id="p12-worker",
                supported=(JOB_KEY,),
                lease_interval=timedelta(minutes=5),
                limit=1,
            )[0]
            unit.commit()
        enrichment_job_id = uuid4()
        target = EmbeddingJobTarget(
            enrichment_job_id,
            "AUDIO_EMBEDDING",
            seed[0],
            seed[1],
            model_a.embedding_model_id,
            model_a.weights_sha256,
            model_a.preprocessing_sha256,
        )
        with sessions() as session:
            with pytest.raises(DBAPIError, match="enrichment job kind mismatch"):
                session.execute(
                    text(
                        "INSERT INTO ml.enrichment_job "
                        "(enrichment_job_id, job_id, job_kind, recording_id, audio_variant_id, "
                        "embedding_model_id, expected_weights_sha256, "
                        "expected_preprocessing_sha256) "
                        "VALUES (:id, :job, 'AUDIO_TAGS', :recording, :variant, :model, "
                        ":weights, :preprocessing)"
                    ),
                    {
                        "id": enrichment_job_id,
                        "job": enqueued.job_id,
                        "recording": seed[0],
                        "variant": seed[1],
                        "model": model_a.embedding_model_id,
                        "weights": model_a.weights_sha256,
                        "preprocessing": model_a.preprocessing_sha256,
                    },
                )
            session.rollback()
        with sessions() as session:
            wrong_kind_job_id = session.scalar(
                text(
                    "INSERT INTO jobs.job (job_type, schema_version) "
                    "VALUES ('ml.audio-tags', 1) RETURNING job_id"
                )
            )
            assert wrong_kind_job_id is not None
            with pytest.raises(DBAPIError, match="enrichment job kind mismatch"):
                session.execute(
                    text(
                        "INSERT INTO ml.enrichment_job "
                        "(job_id, job_kind, recording_id, audio_variant_id, "
                        "embedding_model_id, expected_weights_sha256, "
                        "expected_preprocessing_sha256) "
                        "VALUES (:job, 'AUDIO_EMBEDDING', :recording, :variant, :model, "
                        ":weights, :preprocessing)"
                    ),
                    {
                        "job": wrong_kind_job_id,
                        "recording": seed[0],
                        "variant": seed[1],
                        "model": model_a.embedding_model_id,
                        "weights": model_a.weights_sha256,
                        "preprocessing": model_a.preprocessing_sha256,
                    },
                )
            session.rollback()
        with sessions() as session, session.begin():
            session.execute(
                text(
                    "INSERT INTO ml.enrichment_job "
                    "(enrichment_job_id, job_id, job_kind, recording_id, audio_variant_id, "
                    "embedding_model_id, expected_weights_sha256, expected_preprocessing_sha256) "
                    "VALUES (:id, :job, 'AUDIO_EMBEDDING', :recording, :variant, :model, "
                    ":weights, :preprocessing)"
                ),
                {
                    "id": enrichment_job_id,
                    "job": enqueued.job_id,
                    "recording": seed[0],
                    "variant": seed[1],
                    "model": model_a.embedding_model_id,
                    "weights": model_a.weights_sha256,
                    "preprocessing": model_a.preprocessing_sha256,
                },
            )
        result = EmbeddingResult(
            target=target,
            preprocessing_input_sha256=b"i" * 32,
            vector_sha256=vector_sha256((1.0, 0.0, 0.0)),
            vector=(1.0, 0.0, 0.0),
            normalized=True,
            tags=(("fixture", 0.9),),
        )
        assert runtime.put(lease.fence, model_a, result)
        assert not runtime.put(lease.fence, model_a, result)
        conflicting_tags = EmbeddingResult(
            target=target,
            preprocessing_input_sha256=result.preprocessing_input_sha256,
            vector_sha256=result.vector_sha256,
            vector=result.vector,
            normalized=True,
            tags=(("fixture", 0.1),),
        )
        with pytest.raises(EnrichmentPersistenceError, match="tag_result_conflict"):
            runtime.put(lease.fence, model_a, conflicting_tags)

        with sessions() as session:
            bad_job_id = session.scalar(
                text(
                    "INSERT INTO jobs.job (job_type, schema_version) "
                    "VALUES ('ml.audio-embedding', 1) RETURNING job_id"
                )
            )
            assert bad_job_id is not None
            with pytest.raises(DBAPIError, match="provenance mismatch"):
                session.execute(
                    text(
                        "INSERT INTO ml.enrichment_job "
                        "(job_id, job_kind, recording_id, audio_variant_id, embedding_model_id, "
                        "expected_weights_sha256, expected_preprocessing_sha256) VALUES "
                        "(:job, 'AUDIO_EMBEDDING', :recording, :variant, :model, :bad, "
                        ":preprocessing)"
                    ),
                    {
                        "job": bad_job_id,
                        "recording": seed[0],
                        "variant": seed[1],
                        "model": model_a.embedding_model_id,
                        "bad": b"x" * 32,
                        "preprocessing": model_a.preprocessing_sha256,
                    },
                )
            session.rollback()

        with sessions() as session, session.begin():
            for (recording_id, variant_id), vector in (
                (near, [0.9, 0.1, 0.0]),
                (far, [0.0, 1.0, 0.0]),
                (foreign, [0.99, 0.01, 0.0]),
            ):
                session.add(
                    RecordingEmbeddingRow(
                        recording_id=recording_id,
                        embedding_model_id=model_a.embedding_model_id,
                        audio_variant_id=variant_id,
                        embedding=vector,
                    )
                )
            session.add(
                RecordingEmbeddingRow(
                    recording_id=seed[0],
                    embedding_model_id=model_b.embedding_model_id,
                    audio_variant_id=seed[1],
                    embedding=[1.0, 0.0, 0.0],
                )
            )

        reader = SqlAlchemyTrackEmbeddingReader(sessions)
        assert reader.exact_neighbors(
            owner_id,
            (seed[0],),
            embedding_model_id=model_a.embedding_model_id,
            limit=10,
        ) == (near[0], far[0])
        assert (
            reader.exact_neighbors(
                other_id,
                (foreign[0],),
                embedding_model_id=model_a.embedding_model_id,
                limit=10,
            )
            == ()
        )

        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(RecordingEmbeddingRow)) == 5
            with pytest.raises(DBAPIError, match="dimension"):
                session.add(
                    RecordingEmbeddingRow(
                        recording_id=near[0],
                        embedding_model_id=model_b.embedding_model_id,
                        audio_variant_id=near[1],
                        embedding=[1.0, 0.0],
                    )
                )
                session.flush()
            session.rollback()

        report_a = runtime.save_benchmark(
            model_a.embedding_model_id, _report(model_a, label="a"), decision="APPROVED"
        )
        report_b = runtime.save_benchmark(
            model_b.embedding_model_id, _report(model_b, label="b"), decision="APPROVED"
        )
        report_c = runtime.save_benchmark(
            model_c.embedding_model_id, _report(model_c, label="c"), decision="APPROVED"
        )
        with sessions() as session:
            direct_target = session.get(EmbeddingModelRow, model_c.embedding_model_id)
            assert direct_target is not None
            direct_target.status = "ACTIVE"
            with pytest.raises(DBAPIError, match="current activation history"):
                session.flush()
            session.rollback()
        deadline = datetime.now(UTC) + timedelta(days=7)
        assert (
            runtime.activate(
                task="AUDIO_EMBEDDING",
                target_model_id=model_a.embedding_model_id,
                benchmark_report_sha256=report_a,
                rollback_until=deadline,
            )
            == 1
        )
        assert (
            runtime.activate(
                task="AUDIO_EMBEDDING",
                target_model_id=model_b.embedding_model_id,
                benchmark_report_sha256=report_b,
                rollback_until=deadline,
            )
            == 2
        )
        with sessions() as session:
            direct_active = session.get(EmbeddingModelRow, model_b.embedding_model_id)
            assert direct_active is not None
            direct_active.status = "RETIRED"
            with pytest.raises(DBAPIError, match="current activation history"):
                session.flush()
            session.rollback()
        with pytest.raises(EnrichmentPersistenceError, match="rollback_gate_failed"):
            runtime.activate(
                task="AUDIO_EMBEDDING",
                target_model_id=model_c.embedding_model_id,
                benchmark_report_sha256=report_c,
                rollback_until=deadline,
                action="ROLLBACK",
            )
        assert (
            runtime.activate(
                task="AUDIO_EMBEDDING",
                target_model_id=model_a.embedding_model_id,
                benchmark_report_sha256=report_a,
                rollback_until=deadline,
                action="ROLLBACK",
            )
            == 3
        )
        with sessions() as session:
            assert session.get(EmbeddingModelRow, model_a.embedding_model_id).status == "ACTIVE"  # type: ignore[union-attr]
            assert session.get(EmbeddingModelRow, model_b.embedding_model_id).status == "BENCHMARK"  # type: ignore[union-attr]

        stale = lease.fence.__class__(lease.fence.job_id, "other-worker", lease.fence.attempt_no)
        with pytest.raises(EnrichmentPersistenceError, match="stale_job_lease"):
            runtime.put(stale, model_a, result)
    finally:
        engine.dispose()
