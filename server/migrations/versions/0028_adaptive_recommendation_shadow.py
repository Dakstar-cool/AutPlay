"""Add R1B owner-scoped temporal recommendation shadow state.

Revision ID: 0028_adaptive_recommend_shadow
Revises: 0027_public_access_invite_only
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028_adaptive_recommend_shadow"
down_revision: str | None = "0027_public_access_invite_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install only additive shadow persistence; no serving route is changed."""
    op.execute(
        """
        CREATE TABLE ml.recommendation_temporal_event (
          recommendation_temporal_event_id uuid PRIMARY KEY,
          user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          server_profile_id uuid NOT NULL,
          device_id uuid NOT NULL,
          source_event_id uuid NOT NULL,
          source_request_sha256 bytea NOT NULL,
          device_sequence bigint NOT NULL,
          server_sequence bigint NOT NULL,
          source_event_type text NOT NULL,
          signal_key text NOT NULL,
          derivation_key text NOT NULL,
          recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
          dimensions jsonb NOT NULL,
          occurred_at_ms bigint NOT NULL,
          received_at_ms bigint NOT NULL,
          effective_at_ms bigint NOT NULL,
          time_classification text NOT NULL,
          origin_lane text NOT NULL,
          signed_strength numeric(8,7) NOT NULL,
          quality_weight numeric(8,7) NOT NULL,
          excluded_from_taste boolean NOT NULL,
          recommendation_request_id uuid,
          impression_event_id uuid,
          recommendation_source_rank integer,
          normalized_evidence_sha256 bytea NOT NULL,
          evidence_document jsonb NOT NULL,
          retained_until timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT fk_recommendation_temporal_event_device_owner
            FOREIGN KEY (user_id, device_id) REFERENCES account.device(user_id, device_id)
            ON DELETE RESTRICT,
          CONSTRAINT uq_recommendation_temporal_event_owner
            UNIQUE (user_id, recommendation_temporal_event_id),
          CONSTRAINT uq_recommendation_temporal_event_derivation
            UNIQUE (user_id, source_event_id, signal_key, derivation_key),
          CONSTRAINT ck_recommendation_temporal_event_hashes CHECK (
            octet_length(source_request_sha256)=32
            AND octet_length(normalized_evidence_sha256)=32
          ),
          CONSTRAINT ck_recommendation_temporal_event_sequences CHECK (
            device_sequence>=1 AND server_sequence>=1
          ),
          CONSTRAINT ck_recommendation_temporal_event_times CHECK (
            occurred_at_ms>=0 AND received_at_ms>=0 AND effective_at_ms>=0
          ),
          CONSTRAINT ck_recommendation_temporal_event_source_type CHECK (
            source_event_type IN (
              'USER_TRACK_PREFERENCE_SET','LISTENING_EVENT_RECORDED',
              'RECOMMENDATION_FEEDBACK_RECORDED'
            )
          ),
          CONSTRAINT ck_recommendation_temporal_event_signal CHECK (
            signal_key IN (
              'EXPLICIT_LIKE','EXPLICIT_DISLIKE','EXCLUDE_FROM_TASTE',
              'FINALIZED_ORGANIC_LISTEN','FINALIZED_RECOMMENDATION_LISTEN',
              'RECOMMENDATION_SELECTED','RECOMMENDATION_DISMISSED',
              'FINALIZED_COMPLETION','FINALIZED_SHORT_LISTEN_SKIP'
            )
          ),
          CONSTRAINT ck_recommendation_temporal_event_derivation CHECK (
            derivation_key IN (
              'PREFERENCE_TRANSITION_V1','EXCLUSION_PROJECTION_V1','BASE_LISTEN_V1',
              'OUTCOME_CLASSIFIER_V1','RECOMMENDATION_FEEDBACK_V1'
            )
          ),
          CONSTRAINT ck_recommendation_temporal_event_time_class CHECK (
            time_classification IN (
              'TRUSTED_EVENT_TIME','DELAYED_WITHIN_POLICY','FUTURE_SKEW_CLAMPED',
              'PAST_SKEW_RECENT_DISABLED','RECEIPT_TIME_ONLY'
            )
          ),
          CONSTRAINT ck_recommendation_temporal_event_origin CHECK (
            origin_lane IN ('EXPLICIT','ORGANIC','SOURCE_QUEUE','RECOMMENDATION','EXCLUSION')
          ),
          CONSTRAINT ck_recommendation_temporal_event_weights CHECK (
            signed_strength BETWEEN -1 AND 1 AND quality_weight BETWEEN 0 AND 1
          ),
          CONSTRAINT ck_recommendation_temporal_event_causal_refs CHECK (
            (recommendation_request_id IS NULL AND impression_event_id IS NULL
              AND recommendation_source_rank IS NULL AND origin_lane<>'RECOMMENDATION')
            OR
            (recommendation_request_id IS NOT NULL AND impression_event_id IS NOT NULL
              AND recommendation_source_rank BETWEEN 1 AND 1000
              AND origin_lane='RECOMMENDATION')
          ),
          CONSTRAINT ck_recommendation_temporal_event_retention
            CHECK (retained_until>created_at)
        );
        CREATE INDEX ix_recommendation_temporal_event_owner_watermark
          ON ml.recommendation_temporal_event(user_id,server_sequence,effective_at_ms,
            recommendation_temporal_event_id);
        CREATE INDEX ix_recommendation_temporal_event_retention
          ON ml.recommendation_temporal_event(retained_until,recommendation_temporal_event_id);

        CREATE TABLE ml.recommendation_adaptive_profile (
          recommendation_adaptive_profile_id uuid PRIMARY KEY DEFAULT uuidv7(),
          user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          cutoff_at_ms bigint NOT NULL,
          interaction_watermark bigint NOT NULL,
          feature_policy_key text NOT NULL,
          feature_policy_version text NOT NULL,
          feature_policy_sha256 bytea NOT NULL,
          profile_sha256 bytea NOT NULL,
          profile_document jsonb NOT NULL,
          source_event_count integer NOT NULL,
          dimension_count integer NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_recommendation_adaptive_profile_owner
            UNIQUE(user_id,recommendation_adaptive_profile_id),
          CONSTRAINT uq_recommendation_adaptive_profile_cutoff
            UNIQUE(user_id,cutoff_at_ms,interaction_watermark,feature_policy_key,
              feature_policy_version),
          CONSTRAINT ck_recommendation_adaptive_profile_watermark
            CHECK(cutoff_at_ms>=0 AND interaction_watermark>=0),
          CONSTRAINT ck_recommendation_adaptive_profile_hashes CHECK(
            octet_length(feature_policy_sha256)=32 AND octet_length(profile_sha256)=32
          ),
          CONSTRAINT ck_recommendation_adaptive_profile_policy CHECK(
            feature_policy_key='adaptive-taste'
            AND feature_policy_version ~ '^[1-9][0-9]{0,8}$'
          ),
          CONSTRAINT ck_recommendation_adaptive_profile_bounds CHECK(
            source_event_count BETWEEN 0 AND 10000 AND dimension_count BETWEEN 0 AND 512
          )
        );
        CREATE INDEX ix_recommendation_adaptive_profile_owner_cutoff
          ON ml.recommendation_adaptive_profile(user_id,cutoff_at_ms DESC,
            interaction_watermark DESC);

        CREATE TABLE ml.recommendation_temporal_snapshot (
          recommendation_temporal_snapshot_id uuid PRIMARY KEY DEFAULT uuidv7(),
          user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          recommendation_input_snapshot_id uuid,
          recommendation_adaptive_profile_id uuid,
          cutoff_at_ms bigint NOT NULL,
          interaction_watermark bigint NOT NULL,
          catalog_snapshot bigint NOT NULL,
          availability_snapshot_sha256 bytea NOT NULL,
          baseline_input_snapshot_sha256 bytea NOT NULL,
          feature_policy_key text NOT NULL,
          feature_policy_version text NOT NULL,
          feature_policy_sha256 bytea NOT NULL,
          event_time_policy_sha256 bytea NOT NULL,
          derived_features_sha256 bytea NOT NULL,
          source_evidence_sha256 bytea NOT NULL,
          snapshot_sha256 bytea NOT NULL,
          source_event_count integer NOT NULL,
          dimension_count integer NOT NULL,
          snapshot_document jsonb NOT NULL,
          retained_until timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_recommendation_temporal_snapshot_owner
            UNIQUE(user_id,recommendation_temporal_snapshot_id),
          CONSTRAINT uq_recommendation_temporal_snapshot_baseline
            UNIQUE(user_id,recommendation_input_snapshot_id,feature_policy_key,
              feature_policy_version),
          CONSTRAINT fk_recommendation_temporal_snapshot_input_owner
            FOREIGN KEY(user_id,recommendation_input_snapshot_id)
            REFERENCES ml.recommendation_input_snapshot(user_id,recommendation_input_snapshot_id)
            ON DELETE SET NULL (recommendation_input_snapshot_id),
          CONSTRAINT fk_recommendation_temporal_snapshot_profile_owner
            FOREIGN KEY(user_id,recommendation_adaptive_profile_id)
            REFERENCES ml.recommendation_adaptive_profile(user_id,
              recommendation_adaptive_profile_id)
            ON DELETE SET NULL (recommendation_adaptive_profile_id),
          CONSTRAINT ck_recommendation_temporal_snapshot_watermark CHECK(
            cutoff_at_ms>=0 AND interaction_watermark>=0 AND catalog_snapshot>=0
          ),
          CONSTRAINT ck_recommendation_temporal_snapshot_hashes CHECK(
            octet_length(availability_snapshot_sha256)=32
            AND octet_length(baseline_input_snapshot_sha256)=32
            AND octet_length(feature_policy_sha256)=32
            AND octet_length(event_time_policy_sha256)=32
            AND octet_length(derived_features_sha256)=32
            AND octet_length(source_evidence_sha256)=32
            AND octet_length(snapshot_sha256)=32
          ),
          CONSTRAINT ck_recommendation_temporal_snapshot_policy CHECK(
            feature_policy_key='adaptive-taste'
            AND feature_policy_version ~ '^[1-9][0-9]{0,8}$'
          ),
          CONSTRAINT ck_recommendation_temporal_snapshot_bounds CHECK(
            source_event_count BETWEEN 0 AND 10000 AND dimension_count BETWEEN 0 AND 512
          ),
          CONSTRAINT ck_recommendation_temporal_snapshot_retention
            CHECK(retained_until>created_at)
        );
        CREATE INDEX ix_recommendation_temporal_snapshot_owner_retention
          ON ml.recommendation_temporal_snapshot(user_id,retained_until DESC,
            recommendation_temporal_snapshot_id);
        """
    )
    op.execute(
        """
        CREATE FUNCTION app_private.protect_recommendation_temporal_event()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' THEN
            RAISE EXCEPTION 'recommendation temporal events are immutable';
          END IF;
          IF OLD.retained_until>now() THEN
            RAISE EXCEPTION 'recommendation temporal event retention is active';
          END IF;
          RETURN OLD;
        END; $$;
        CREATE TRIGGER tr_recommendation_temporal_event_immutable
          BEFORE UPDATE OR DELETE ON ml.recommendation_temporal_event
          FOR EACH ROW EXECUTE FUNCTION app_private.protect_recommendation_temporal_event();

        CREATE FUNCTION app_private.protect_recommendation_adaptive_profile()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' THEN
            RAISE EXCEPTION 'recommendation adaptive profiles are immutable';
          END IF;
          RETURN OLD;
        END; $$;
        CREATE TRIGGER tr_recommendation_adaptive_profile_immutable
          BEFORE UPDATE ON ml.recommendation_adaptive_profile
          FOR EACH ROW EXECUTE FUNCTION app_private.protect_recommendation_adaptive_profile();

        CREATE FUNCTION app_private.protect_recommendation_temporal_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' THEN
            RAISE EXCEPTION 'recommendation temporal snapshots are immutable';
          END IF;
          IF OLD.retained_until>now() THEN
            RAISE EXCEPTION 'recommendation temporal snapshot retention is active';
          END IF;
          RETURN OLD;
        END; $$;
        CREATE TRIGGER tr_recommendation_temporal_snapshot_immutable
          BEFORE UPDATE OR DELETE ON ml.recommendation_temporal_snapshot
          FOR EACH ROW EXECUTE FUNCTION app_private.protect_recommendation_temporal_snapshot();
        REVOKE ALL ON FUNCTION app_private.protect_recommendation_temporal_event() FROM PUBLIC;
        REVOKE ALL ON FUNCTION app_private.protect_recommendation_adaptive_profile() FROM PUBLIC;
        REVOKE ALL ON FUNCTION app_private.protect_recommendation_temporal_snapshot() FROM PUBLIC;
        """
    )
    for statement in (
        "ALTER TABLE ml.recommendation_request ADD COLUMN recommendation_temporal_snapshot_id uuid",
        "ALTER TABLE ml.recommendation_request ADD COLUMN temporal_snapshot_sha256 bytea",
        "ALTER TABLE ml.recommendation_request ADD COLUMN adaptive_feature_policy_sha256 bytea",
    ):
        op.execute(statement)
    op.execute(
        """
        ALTER TABLE ml.recommendation_request
          ADD CONSTRAINT fk_recommendation_request_temporal_snapshot_owner
          FOREIGN KEY(user_id,recommendation_temporal_snapshot_id)
          REFERENCES ml.recommendation_temporal_snapshot(user_id,
            recommendation_temporal_snapshot_id)
          ON DELETE SET NULL (recommendation_temporal_snapshot_id);
        ALTER TABLE ml.recommendation_request
          ADD CONSTRAINT ck_recommendation_request_temporal_hashes CHECK(
            (temporal_snapshot_sha256 IS NULL OR octet_length(temporal_snapshot_sha256)=32)
            AND (adaptive_feature_policy_sha256 IS NULL
              OR octet_length(adaptive_feature_policy_sha256)=32)
          );
        REVOKE ALL ON ml.recommendation_temporal_event FROM PUBLIC;
        REVOKE ALL ON ml.recommendation_adaptive_profile FROM PUBLIC;
        REVOKE ALL ON ml.recommendation_temporal_snapshot FROM PUBLIC;
        """
    )


def downgrade() -> None:
    """Refuse to discard R1B evidence; remove only an unused installation."""
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM ml.recommendation_temporal_event)
             OR EXISTS(SELECT 1 FROM ml.recommendation_adaptive_profile)
             OR EXISTS(SELECT 1 FROM ml.recommendation_temporal_snapshot) THEN
            RAISE EXCEPTION 'refusing R1B downgrade with adaptive recommendation evidence';
          END IF;
        END $$;
        ALTER TABLE ml.recommendation_request
          DROP CONSTRAINT ck_recommendation_request_temporal_hashes;
        ALTER TABLE ml.recommendation_request
          DROP CONSTRAINT fk_recommendation_request_temporal_snapshot_owner;
        ALTER TABLE ml.recommendation_request DROP COLUMN adaptive_feature_policy_sha256;
        ALTER TABLE ml.recommendation_request DROP COLUMN temporal_snapshot_sha256;
        ALTER TABLE ml.recommendation_request DROP COLUMN recommendation_temporal_snapshot_id;
        DROP TABLE ml.recommendation_temporal_snapshot;
        DROP FUNCTION app_private.protect_recommendation_temporal_snapshot();
        DROP TABLE ml.recommendation_adaptive_profile;
        DROP FUNCTION app_private.protect_recommendation_adaptive_profile();
        DROP TABLE ml.recommendation_temporal_event;
        DROP FUNCTION app_private.protect_recommendation_temporal_event();
        """
    )
