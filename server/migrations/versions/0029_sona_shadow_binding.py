"""Bind immutable Sona shadow evidence to an existing P11 request.

Revision ID: 0029_sona_shadow_binding
Revises: 0028_adaptive_recommend_shadow
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029_sona_shadow_binding"
down_revision: str | None = "0028_adaptive_recommend_shadow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install one-time model evidence columns; no route or serving policy changes."""

    op.execute(
        """
        ALTER TABLE ml.recommendation_request
          ADD COLUMN sona_shadow_pipeline_key text,
          ADD COLUMN sona_shadow_pipeline_version text,
          ADD COLUMN sona_shadow_pipeline_manifest_sha256 bytea,
          ADD COLUMN sona_tokenizer_sha256 bytea,
          ADD COLUMN sona_model_manifest_sha256 bytea,
          ADD COLUMN sona_request_sha256 bytea,
          ADD COLUMN sona_output_sha256 bytea,
          ADD COLUMN sona_shadow_evidence_sha256 bytea,
          ADD COLUMN sona_shadow_status text,
          ADD COLUMN sona_shadow_reason text,
          ADD COLUMN sona_shadow_document jsonb;

        ALTER TABLE ml.recommendation_request
          ADD CONSTRAINT fk_recommendation_request_sona_shadow_pipeline
          FOREIGN KEY(sona_shadow_pipeline_key,sona_shadow_pipeline_version)
          REFERENCES ml.recommendation_pipeline_version(pipeline_key,version)
          ON DELETE RESTRICT;

        ALTER TABLE ml.recommendation_request
          ADD CONSTRAINT ck_recommendation_request_sona_hashes CHECK(
            (sona_shadow_pipeline_manifest_sha256 IS NULL OR
              octet_length(sona_shadow_pipeline_manifest_sha256)=32)
            AND (sona_tokenizer_sha256 IS NULL OR octet_length(sona_tokenizer_sha256)=32)
            AND (sona_model_manifest_sha256 IS NULL OR
              octet_length(sona_model_manifest_sha256)=32)
            AND (sona_request_sha256 IS NULL OR octet_length(sona_request_sha256)=32)
            AND (sona_output_sha256 IS NULL OR octet_length(sona_output_sha256)=32)
            AND (sona_shadow_evidence_sha256 IS NULL OR
              octet_length(sona_shadow_evidence_sha256)=32)
          );

        ALTER TABLE ml.recommendation_request
          ADD CONSTRAINT ck_recommendation_request_sona_binding CHECK(
            (sona_shadow_status IS NULL
              AND sona_shadow_pipeline_key IS NULL
              AND sona_shadow_pipeline_version IS NULL
              AND sona_shadow_pipeline_manifest_sha256 IS NULL
              AND sona_tokenizer_sha256 IS NULL
              AND sona_model_manifest_sha256 IS NULL
              AND sona_request_sha256 IS NULL
              AND sona_output_sha256 IS NULL
              AND sona_shadow_evidence_sha256 IS NULL
              AND sona_shadow_reason IS NULL
              AND sona_shadow_document IS NULL)
            OR
            (sona_shadow_status='SUCCEEDED'
              AND sona_shadow_reason IS NULL
              AND sona_shadow_pipeline_key IS NOT NULL
              AND sona_shadow_pipeline_version IS NOT NULL
              AND sona_shadow_pipeline_manifest_sha256 IS NOT NULL
              AND sona_tokenizer_sha256 IS NOT NULL
              AND sona_model_manifest_sha256 IS NOT NULL
              AND sona_request_sha256 IS NOT NULL
              AND sona_output_sha256 IS NOT NULL
              AND sona_shadow_evidence_sha256 IS NOT NULL
              AND sona_shadow_document IS NOT NULL
              AND temporal_snapshot_sha256 IS NOT NULL
              AND adaptive_feature_policy_sha256 IS NOT NULL)
            OR
            (sona_shadow_status='DEGRADED'
              AND sona_shadow_reason IN ('SONA_UNAVAILABLE','SONA_OUTPUT_UNBOUND')
              AND sona_shadow_pipeline_key IS NOT NULL
              AND sona_shadow_pipeline_version IS NOT NULL
              AND sona_shadow_pipeline_manifest_sha256 IS NOT NULL
              AND sona_tokenizer_sha256 IS NOT NULL
              AND sona_model_manifest_sha256 IS NOT NULL
              AND sona_output_sha256 IS NULL
              AND sona_shadow_evidence_sha256 IS NOT NULL
              AND sona_shadow_document IS NOT NULL
              AND temporal_snapshot_sha256 IS NOT NULL
              AND adaptive_feature_policy_sha256 IS NOT NULL)
          );

        CREATE FUNCTION app_private.protect_sona_shadow_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            IF OLD.sona_shadow_status IS NOT NULL AND EXISTS(
              SELECT 1
              FROM ml.recommendation_temporal_snapshot snapshot
              WHERE snapshot.user_id=OLD.user_id
                AND snapshot.recommendation_temporal_snapshot_id=
                  OLD.recommendation_temporal_snapshot_id
                AND snapshot.retained_until>now()
            ) THEN
              RAISE EXCEPTION 'Sona shadow evidence retention is active';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.sona_shadow_status IS NULL THEN
            IF NEW.sona_shadow_status IS NOT NULL THEN
              IF to_jsonb(NEW) - ARRAY[
                'recommendation_temporal_snapshot_id',
                'temporal_snapshot_sha256',
                'adaptive_feature_policy_sha256',
                'sona_shadow_pipeline_key',
                'sona_shadow_pipeline_version',
                'sona_shadow_pipeline_manifest_sha256',
                'sona_tokenizer_sha256',
                'sona_model_manifest_sha256',
                'sona_request_sha256',
                'sona_output_sha256',
                'sona_shadow_evidence_sha256',
                'sona_shadow_status',
                'sona_shadow_reason',
                'sona_shadow_document'
              ] IS DISTINCT FROM to_jsonb(OLD) - ARRAY[
                'recommendation_temporal_snapshot_id',
                'temporal_snapshot_sha256',
                'adaptive_feature_policy_sha256',
                'sona_shadow_pipeline_key',
                'sona_shadow_pipeline_version',
                'sona_shadow_pipeline_manifest_sha256',
                'sona_tokenizer_sha256',
                'sona_model_manifest_sha256',
                'sona_request_sha256',
                'sona_output_sha256',
                'sona_shadow_evidence_sha256',
                'sona_shadow_status',
                'sona_shadow_reason',
                'sona_shadow_document'
              ] THEN
                RAISE EXCEPTION 'Sona attach cannot change persisted P11 truth';
              END IF;
              IF NEW.recommendation_temporal_snapshot_id IS NULL THEN
                RAISE EXCEPTION 'Sona attach requires a retained temporal snapshot';
              END IF;
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.recommendation_temporal_snapshot_id IS NOT NULL
            AND NEW.recommendation_temporal_snapshot_id IS NULL
            AND to_jsonb(NEW) - 'recommendation_temporal_snapshot_id'
              IS NOT DISTINCT FROM
                to_jsonb(OLD) - 'recommendation_temporal_snapshot_id'
            AND NOT EXISTS(
              SELECT 1
              FROM ml.recommendation_temporal_snapshot snapshot
              WHERE snapshot.user_id=OLD.user_id
                AND snapshot.recommendation_temporal_snapshot_id=
                  OLD.recommendation_temporal_snapshot_id
                AND snapshot.retained_until>now()
            ) THEN
            RETURN NEW;
          END IF;
          IF OLD.recommendation_input_snapshot_id IS NOT NULL
            AND NEW.recommendation_input_snapshot_id IS NULL
            AND OLD.recommendation_temporal_snapshot_id IS NULL
            AND to_jsonb(NEW) - 'recommendation_input_snapshot_id'
              IS NOT DISTINCT FROM
                to_jsonb(OLD) - 'recommendation_input_snapshot_id'
            AND NOT EXISTS(
              SELECT 1
              FROM ml.recommendation_input_snapshot snapshot
              WHERE snapshot.user_id=OLD.user_id
                AND snapshot.recommendation_input_snapshot_id=
                  OLD.recommendation_input_snapshot_id
                AND snapshot.retained_until>now()
            ) THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'Sona shadow evidence is immutable';
        END; $$;

        CREATE TRIGGER tr_recommendation_request_sona_shadow_immutable
          BEFORE UPDATE OR DELETE ON ml.recommendation_request
          FOR EACH ROW EXECUTE FUNCTION app_private.protect_sona_shadow_binding();

        REVOKE ALL ON FUNCTION app_private.protect_sona_shadow_binding() FROM PUBLIC;
        """
    )


def downgrade() -> None:
    """Refuse to discard attached evidence; remove only an unused installation."""

    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS(
            SELECT 1 FROM ml.recommendation_request WHERE sona_shadow_status IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'refusing Sona downgrade with shadow evidence';
          END IF;
        END $$;

        DROP TRIGGER tr_recommendation_request_sona_shadow_immutable
          ON ml.recommendation_request;
        DROP FUNCTION app_private.protect_sona_shadow_binding();
        ALTER TABLE ml.recommendation_request
          DROP CONSTRAINT ck_recommendation_request_sona_binding,
          DROP CONSTRAINT ck_recommendation_request_sona_hashes,
          DROP CONSTRAINT fk_recommendation_request_sona_shadow_pipeline,
          DROP COLUMN sona_shadow_document,
          DROP COLUMN sona_shadow_reason,
          DROP COLUMN sona_shadow_status,
          DROP COLUMN sona_shadow_evidence_sha256,
          DROP COLUMN sona_output_sha256,
          DROP COLUMN sona_request_sha256,
          DROP COLUMN sona_model_manifest_sha256,
          DROP COLUMN sona_tokenizer_sha256,
          DROP COLUMN sona_shadow_pipeline_manifest_sha256,
          DROP COLUMN sona_shadow_pipeline_version,
          DROP COLUMN sona_shadow_pipeline_key;
        """
    )
