"""Add isolated P12 GPU enrichment provenance and activation state.

Revision ID: 0014_gpu_enrichment
Revises: 0013_recommendation_runtime
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014_gpu_enrichment"
down_revision: str | None = "0013_recommendation_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install additive immutable enrichment evidence and rollback history."""
    op.execute("""
        ALTER TABLE ml.embedding_model
            ADD COLUMN source text NOT NULL DEFAULT 'legacy://blocked',
            ADD COLUMN source_revision text NOT NULL DEFAULT 'legacy',
            ADD COLUMN artifact_filename text NOT NULL DEFAULT 'legacy.bin',
            ADD COLUMN artifact_format text NOT NULL DEFAULT 'UNKNOWN',
            ADD COLUMN artifact_byte_size bigint NOT NULL DEFAULT 1,
            ADD COLUMN artifact_manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN manifest_sha256 bytea NOT NULL
                DEFAULT decode(repeat('00', 32), 'hex'),
            ADD COLUMN runtime_revision text NOT NULL DEFAULT 'legacy',
            ADD COLUMN preprocessing_manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN preprocessing_sha256 bytea NOT NULL
                DEFAULT decode(repeat('00', 32), 'hex'),
            ADD COLUMN license_review_reference text
    """)
    # Existing models cannot satisfy the newly introduced provenance fields. Keep
    # them unusable for P12 enrichment; the downgrade guard below refuses to erase
    # that lifecycle change whenever any registry row exists.
    op.execute("UPDATE ml.embedding_model SET status = 'BLOCKED'")
    for column in (
        "source",
        "source_revision",
        "artifact_filename",
        "artifact_format",
        "artifact_byte_size",
        "artifact_manifest",
        "manifest_sha256",
        "runtime_revision",
        "preprocessing_manifest",
        "preprocessing_sha256",
    ):
        op.execute(f"ALTER TABLE ml.embedding_model ALTER COLUMN {column} DROP DEFAULT")
    for name, expression in (
        ("ck_embedding_model_source", "length(source) BETWEEN 1 AND 500"),
        (
            "ck_embedding_model_source_revision",
            "length(source_revision) BETWEEN 1 AND 300",
        ),
        (
            "ck_embedding_model_artifact_filename",
            "length(artifact_filename) BETWEEN 1 AND 300",
        ),
        (
            "ck_embedding_model_artifact_format",
            "length(artifact_format) BETWEEN 1 AND 100",
        ),
        ("ck_embedding_model_artifact_byte_size", "artifact_byte_size > 0"),
        ("ck_embedding_model_manifest_hash_len", "octet_length(manifest_sha256) = 32"),
        (
            "ck_embedding_model_preprocessing_hash_len",
            "octet_length(preprocessing_sha256) = 32",
        ),
        (
            "ck_embedding_model_runtime_revision",
            "length(runtime_revision) BETWEEN 1 AND 200",
        ),
        (
            "ck_embedding_model_license_review_reference",
            "license_review_reference IS NULL OR "
            "length(license_review_reference) BETWEEN 1 AND 500",
        ),
        (
            "ck_embedding_model_review_required",
            "status = 'BLOCKED' OR license_review_reference IS NOT NULL",
        ),
    ):
        op.execute(f"ALTER TABLE ml.embedding_model ADD CONSTRAINT {name} CHECK ({expression})")
    op.execute("""
        CREATE FUNCTION app_private.protect_embedding_model_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'embedding model registry rows are immutable';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status = 'ACTIVE' THEN
                    RAISE EXCEPTION 'embedding model must be benchmarked before activation';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.status = 'ACTIVE' AND OLD.status <> 'ACTIVE' AND NOT EXISTS (
                SELECT 1
                FROM ml.embedding_model_activation activation
                JOIN ml.embedding_benchmark_report report
                  ON report.report_sha256 = activation.benchmark_report_sha256
                WHERE activation.target_embedding_model_id = NEW.embedding_model_id
                  AND activation.task = NEW.task
                  AND activation.created_at = transaction_timestamp()
                  AND report.embedding_model_id = NEW.embedding_model_id
                  AND report.decision = 'APPROVED'
            ) THEN
                RAISE EXCEPTION 'embedding model activation requires current activation history';
            END IF;
            IF OLD.status = 'ACTIVE' AND NEW.status <> 'ACTIVE' AND NOT EXISTS (
                SELECT 1
                FROM ml.embedding_model_activation activation
                WHERE activation.previous_embedding_model_id = OLD.embedding_model_id
                  AND activation.task = OLD.task
                  AND activation.created_at = transaction_timestamp()
            ) THEN
                RAISE EXCEPTION 'embedding model deactivation requires current activation history';
            END IF;
            IF ROW(
                NEW.embedding_model_id, NEW.model_key, NEW.version, NEW.task,
                NEW.source, NEW.source_revision, NEW.artifact_filename,
                NEW.artifact_format, NEW.artifact_byte_size, NEW.artifact_manifest,
                NEW.manifest_sha256, NEW.weights_sha256, NEW.license_id,
                NEW.runtime, NEW.runtime_revision, NEW.inference_precision,
                NEW.input_sample_rate_hz, NEW.segment_duration_ms,
                NEW.preprocessing_version, NEW.preprocessing_manifest,
                NEW.preprocessing_sha256, NEW.pooling_strategy, NEW.dimension,
                NEW.license_review_reference, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.embedding_model_id, OLD.model_key, OLD.version, OLD.task,
                OLD.source, OLD.source_revision, OLD.artifact_filename,
                OLD.artifact_format, OLD.artifact_byte_size, OLD.artifact_manifest,
                OLD.manifest_sha256, OLD.weights_sha256, OLD.license_id,
                OLD.runtime, OLD.runtime_revision, OLD.inference_precision,
                OLD.input_sample_rate_hz, OLD.segment_duration_ms,
                OLD.preprocessing_version, OLD.preprocessing_manifest,
                OLD.preprocessing_sha256, OLD.pooling_strategy, OLD.dimension,
                OLD.license_review_reference, OLD.created_at
            ) THEN
                RAISE EXCEPTION 'embedding model provenance is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER tr_embedding_model_provenance_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON ml.embedding_model
        FOR EACH ROW EXECUTE FUNCTION app_private.protect_embedding_model_provenance()
    """)

    op.execute("""
        ALTER TABLE ml.recording_embedding
            ADD COLUMN preprocessing_input_sha256 bytea,
            ADD COLUMN vector_sha256 bytea,
            ADD COLUMN producing_job_id uuid REFERENCES jobs.job(job_id) ON DELETE RESTRICT,
            ADD COLUMN producing_attempt_no integer,
            ADD COLUMN retired_at timestamptz,
            ADD CONSTRAINT ck_recording_embedding_input_hash CHECK (
                preprocessing_input_sha256 IS NULL
                OR octet_length(preprocessing_input_sha256) = 32
            ),
            ADD CONSTRAINT ck_recording_embedding_vector_hash CHECK (
                vector_sha256 IS NULL OR octet_length(vector_sha256) = 32
            ),
            ADD CONSTRAINT ck_recording_embedding_attempt CHECK (
                producing_attempt_no IS NULL OR producing_attempt_no >= 1
            )
    """)

    op.execute("""
        CREATE TABLE ml.embedding_benchmark_report (
            report_sha256 bytea PRIMARY KEY,
            embedding_model_id uuid NOT NULL
                REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
            dataset_id text NOT NULL,
            dataset_version text NOT NULL,
            dataset_snapshot_sha256 bytea NOT NULL,
            interaction_schema_version integer NOT NULL,
            interaction_watermark bigint NOT NULL,
            decision text NOT NULL,
            report_document jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_embedding_benchmark_report_hash
                CHECK (octet_length(report_sha256) = 32),
            CONSTRAINT ck_embedding_benchmark_dataset_hash
                CHECK (octet_length(dataset_snapshot_sha256) = 32),
            CONSTRAINT ck_embedding_benchmark_interaction_identity
                CHECK (interaction_schema_version >= 1 AND interaction_watermark >= 0),
            CONSTRAINT ck_embedding_benchmark_decision CHECK (
                decision IN ('EXPERIMENTAL', 'APPROVED', 'REJECTED', 'UNAVAILABLE')
            )
        )
    """)
    op.execute("""
        CREATE INDEX ix_embedding_benchmark_model_time
        ON ml.embedding_benchmark_report (embedding_model_id, created_at DESC)
    """)
    op.execute("""
        CREATE TABLE ml.embedding_model_activation (
            embedding_model_activation_id uuid PRIMARY KEY DEFAULT uuidv7(),
            task text NOT NULL CHECK (length(task) BETWEEN 1 AND 100),
            activation_sequence bigint NOT NULL CHECK (activation_sequence >= 1),
            target_embedding_model_id uuid
                REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
            previous_embedding_model_id uuid
                REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
            action text NOT NULL CHECK (action IN ('ACTIVATE', 'ROLLBACK', 'DEACTIVATE')),
            benchmark_report_sha256 bytea NOT NULL
                REFERENCES ml.embedding_benchmark_report(report_sha256) ON DELETE RESTRICT,
            rollback_until timestamptz,
            actor_user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_embedding_activation_sequence UNIQUE (task, activation_sequence),
            CONSTRAINT ck_embedding_activation_benchmark_hash
                CHECK (octet_length(benchmark_report_sha256) = 32)
        )
    """)
    op.execute("""
        CREATE INDEX ix_embedding_activation_task_time
        ON ml.embedding_model_activation (task, activation_sequence DESC)
    """)
    op.execute("""
        CREATE TABLE ml.enrichment_job (
            enrichment_job_id uuid PRIMARY KEY DEFAULT uuidv7(),
            job_id uuid NOT NULL UNIQUE REFERENCES jobs.job(job_id) ON DELETE RESTRICT,
            job_kind text NOT NULL CHECK (job_kind = 'AUDIO_EMBEDDING'),
            recording_id uuid NOT NULL
                REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
            audio_variant_id uuid NOT NULL
                REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
            embedding_model_id uuid NOT NULL
                REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
            expected_weights_sha256 bytea NOT NULL,
            expected_preprocessing_sha256 bytea NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_enrichment_job_weights_hash
                CHECK (octet_length(expected_weights_sha256) = 32),
            CONSTRAINT ck_enrichment_job_preprocessing_hash
                CHECK (octet_length(expected_preprocessing_sha256) = 32)
        )
    """)
    op.execute("""
        CREATE INDEX ix_enrichment_job_model_recording
        ON ml.enrichment_job (embedding_model_id, recording_id)
    """)
    op.execute("""
        CREATE TABLE ml.recording_tag_set (
            recording_tag_set_id uuid PRIMARY KEY DEFAULT uuidv7(),
            recording_id uuid NOT NULL
                REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
            embedding_model_id uuid NOT NULL
                REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
            audio_variant_id uuid NOT NULL
                REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
            output_schema_version integer NOT NULL CHECK (output_schema_version >= 1),
            tag_document jsonb NOT NULL,
            result_sha256 bytea NOT NULL,
            preprocessing_input_sha256 bytea NOT NULL,
            producing_job_id uuid NOT NULL
                REFERENCES jobs.job(job_id) ON DELETE RESTRICT,
            producing_attempt_no integer NOT NULL CHECK (producing_attempt_no >= 1),
            retired_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_recording_tag_set_source UNIQUE (
                recording_id, embedding_model_id, audio_variant_id, output_schema_version
            ),
            CONSTRAINT ck_recording_tag_set_hashes CHECK (
                octet_length(result_sha256) = 32
                AND octet_length(preprocessing_input_sha256) = 32
            )
        )
    """)
    op.execute("""
        CREATE INDEX ix_recording_tag_set_model_recording
        ON ml.recording_tag_set (embedding_model_id, recording_id)
    """)

    op.execute("""
        CREATE FUNCTION app_private.enforce_enrichment_target_integrity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            variant_recording_id uuid;
            model_weights bytea;
            model_preprocessing bytea;
            model_status text;
            model_task text;
            durable_job_type text;
            durable_job_schema_version integer;
        BEGIN
            SELECT av.recording_id INTO variant_recording_id
            FROM vault.audio_variant av
            JOIN vault.vault_object object ON object.vault_object_id = av.vault_object_id
            WHERE av.audio_variant_id = NEW.audio_variant_id
              AND av.validation_status = 'VALID' AND av.deleted_at IS NULL
              AND object.commit_status = 'COMMITTED';
            SELECT weights_sha256, preprocessing_sha256, status, task
            INTO model_weights, model_preprocessing, model_status, model_task
            FROM ml.embedding_model
            WHERE embedding_model_id = NEW.embedding_model_id;
            SELECT job_type, schema_version
            INTO durable_job_type, durable_job_schema_version
            FROM jobs.job
            WHERE job_id = NEW.job_id;
            IF variant_recording_id IS DISTINCT FROM NEW.recording_id THEN
                RAISE EXCEPTION 'enrichment source variant Recording mismatch';
            END IF;
            IF model_task IS DISTINCT FROM NEW.job_kind
               OR durable_job_schema_version IS DISTINCT FROM 1
               OR durable_job_type IS DISTINCT FROM 'ml.audio-embedding' THEN
                RAISE EXCEPTION 'enrichment job kind mismatch';
            END IF;
            IF model_status NOT IN ('BENCHMARK', 'ACTIVE')
               OR model_weights IS DISTINCT FROM NEW.expected_weights_sha256
               OR model_preprocessing IS DISTINCT FROM NEW.expected_preprocessing_sha256 THEN
                RAISE EXCEPTION 'enrichment model provenance mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER tr_enrichment_job_integrity
        BEFORE INSERT OR UPDATE ON ml.enrichment_job
        FOR EACH ROW EXECUTE FUNCTION app_private.enforce_enrichment_target_integrity()
    """)
    op.execute("""
        CREATE FUNCTION app_private.enforce_recording_tag_integrity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            variant_recording_id uuid;
        BEGIN
            SELECT recording_id INTO variant_recording_id
            FROM vault.audio_variant WHERE audio_variant_id = NEW.audio_variant_id;
            IF variant_recording_id IS DISTINCT FROM NEW.recording_id THEN
                RAISE EXCEPTION 'tag source variant Recording mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER tr_recording_tag_integrity
        BEFORE INSERT OR UPDATE ON ml.recording_tag_set
        FOR EACH ROW EXECUTE FUNCTION app_private.enforce_recording_tag_integrity()
    """)
    op.execute("""
        CREATE FUNCTION app_private.protect_ml_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND TG_TABLE_NAME = 'recording_tag_set'
               AND current_setting('autplay.allow_derived_retirement', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'ML evidence rows are immutable';
        END;
        $$
    """)
    for table, trigger in (
        ("embedding_benchmark_report", "tr_embedding_benchmark_immutable"),
        ("embedding_model_activation", "tr_embedding_activation_immutable"),
        ("recording_tag_set", "tr_recording_tag_set_immutable"),
    ):
        op.execute(f"""
            CREATE TRIGGER {trigger}
            BEFORE UPDATE OR DELETE ON ml.{table}
            FOR EACH ROW EXECUTE FUNCTION app_private.protect_ml_evidence()
        """)
    for function in (
        "protect_embedding_model_provenance",
        "enforce_enrichment_target_integrity",
        "enforce_recording_tag_integrity",
        "protect_ml_evidence",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION app_private.{function}() FROM PUBLIC")
    op.execute("""
        REVOKE ALL ON ml.embedding_benchmark_report, ml.embedding_model_activation,
            ml.enrichment_job, ml.recording_tag_set FROM PUBLIC
    """)


def downgrade() -> None:
    """Remove only P12-owned additive state without destructive fallback."""
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM ml.embedding_benchmark_report)
               OR EXISTS (SELECT 1 FROM ml.embedding_model_activation)
               OR EXISTS (SELECT 1 FROM ml.enrichment_job)
               OR EXISTS (SELECT 1 FROM ml.recording_tag_set)
               OR EXISTS (
                    SELECT 1 FROM ml.recording_embedding
                    WHERE preprocessing_input_sha256 IS NOT NULL
                       OR vector_sha256 IS NOT NULL
                       OR producing_job_id IS NOT NULL
                       OR producing_attempt_no IS NOT NULL
                       OR retired_at IS NOT NULL
               )
               OR EXISTS (SELECT 1 FROM ml.embedding_model) THEN
                RAISE EXCEPTION 'refusing destructive P12 downgrade with enrichment data';
            END IF;
        END;
        $$
    """)
    for table, trigger in (
        ("recording_tag_set", "tr_recording_tag_set_immutable"),
        ("embedding_model_activation", "tr_embedding_activation_immutable"),
        ("embedding_benchmark_report", "tr_embedding_benchmark_immutable"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON ml.{table}")
    op.execute("DROP FUNCTION app_private.protect_ml_evidence()")
    op.execute("DROP TRIGGER tr_recording_tag_integrity ON ml.recording_tag_set")
    op.execute("DROP FUNCTION app_private.enforce_recording_tag_integrity()")
    op.execute("DROP TRIGGER tr_enrichment_job_integrity ON ml.enrichment_job")
    op.execute("DROP FUNCTION app_private.enforce_enrichment_target_integrity()")
    op.execute("DROP TABLE ml.recording_tag_set")
    op.execute("DROP TABLE ml.enrichment_job")
    op.execute("DROP TABLE ml.embedding_model_activation")
    op.execute("DROP TABLE ml.embedding_benchmark_report")
    op.execute("""
        ALTER TABLE ml.recording_embedding
            DROP CONSTRAINT ck_recording_embedding_attempt,
            DROP CONSTRAINT ck_recording_embedding_vector_hash,
            DROP CONSTRAINT ck_recording_embedding_input_hash,
            DROP COLUMN retired_at,
            DROP COLUMN producing_attempt_no,
            DROP COLUMN producing_job_id,
            DROP COLUMN vector_sha256,
            DROP COLUMN preprocessing_input_sha256
    """)
    op.execute("DROP TRIGGER tr_embedding_model_provenance_immutable ON ml.embedding_model")
    op.execute("DROP FUNCTION app_private.protect_embedding_model_provenance()")
    for constraint in (
        "ck_embedding_model_review_required",
        "ck_embedding_model_license_review_reference",
        "ck_embedding_model_runtime_revision",
        "ck_embedding_model_preprocessing_hash_len",
        "ck_embedding_model_manifest_hash_len",
        "ck_embedding_model_artifact_byte_size",
        "ck_embedding_model_artifact_format",
        "ck_embedding_model_artifact_filename",
        "ck_embedding_model_source_revision",
        "ck_embedding_model_source",
    ):
        op.execute(f"ALTER TABLE ml.embedding_model DROP CONSTRAINT {constraint}")
    for column in (
        "license_review_reference",
        "preprocessing_sha256",
        "preprocessing_manifest",
        "runtime_revision",
        "manifest_sha256",
        "artifact_manifest",
        "artifact_byte_size",
        "artifact_format",
        "artifact_filename",
        "source_revision",
        "source",
    ):
        op.execute(f"ALTER TABLE ml.embedding_model DROP COLUMN {column}")
