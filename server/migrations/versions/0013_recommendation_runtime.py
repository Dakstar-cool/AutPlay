"""Add replay-complete P11 recommendation runtime state.

Revision ID: 0013_recommendation_runtime
Revises: 0012_sync_runtime
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_recommendation_runtime"
down_revision: str | None = "0012_sync_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install additive immutable manifests, retained inputs and trace details."""
    op.execute("""
        CREATE TABLE ml.recommendation_pipeline_version (
            pipeline_key text NOT NULL,
            version text NOT NULL,
            implementation_revision text NOT NULL,
            request_schema_version integer NOT NULL CHECK (request_schema_version >= 1),
            canonicalization_version integer NOT NULL CHECK (canonicalization_version >= 1),
            manifest jsonb NOT NULL,
            manifest_sha256 bytea NOT NULL CHECK (octet_length(manifest_sha256) = 32),
            lifecycle_status text NOT NULL DEFAULT 'ACTIVE',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT recommendation_pipeline_version_pkey PRIMARY KEY (pipeline_key, version),
            CONSTRAINT ck_recommendation_pipeline_key
                CHECK (length(pipeline_key) BETWEEN 1 AND 100),
            CONSTRAINT ck_recommendation_pipeline_version CHECK (length(version) BETWEEN 1 AND 100),
            CONSTRAINT ck_recommendation_pipeline_revision
                CHECK (length(implementation_revision) BETWEEN 1 AND 200),
            CONSTRAINT ck_recommendation_pipeline_status
                CHECK (lifecycle_status IN ('ACTIVE', 'SHADOW', 'RETIRED'))
        )
    """)
    op.execute("""
        CREATE FUNCTION app_private.protect_recommendation_pipeline_manifest()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'recommendation pipeline manifests are immutable';
            END IF;
            IF ROW(
                NEW.pipeline_key, NEW.version, NEW.implementation_revision,
                NEW.request_schema_version, NEW.canonicalization_version,
                NEW.manifest, NEW.manifest_sha256, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.pipeline_key, OLD.version, OLD.implementation_revision,
                OLD.request_schema_version, OLD.canonicalization_version,
                OLD.manifest, OLD.manifest_sha256, OLD.created_at
            ) THEN
                RAISE EXCEPTION 'recommendation pipeline manifest identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER tr_recommendation_pipeline_manifest_immutable
        BEFORE UPDATE OR DELETE ON ml.recommendation_pipeline_version
        FOR EACH ROW EXECUTE FUNCTION app_private.protect_recommendation_pipeline_manifest()
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION app_private.protect_recommendation_pipeline_manifest() FROM PUBLIC"
    )
    op.execute("""
        CREATE TABLE ml.recommendation_input_snapshot (
            recommendation_input_snapshot_id uuid PRIMARY KEY DEFAULT uuidv7(),
            user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
            input_snapshot_sha256 bytea NOT NULL CHECK (octet_length(input_snapshot_sha256) = 32),
            interaction_watermark bigint NOT NULL CHECK (interaction_watermark >= 0),
            catalog_snapshot bigint NOT NULL CHECK (catalog_snapshot >= 0),
            availability_snapshot text NOT NULL,
            policy_snapshot_sha256 bytea NOT NULL CHECK (octet_length(policy_snapshot_sha256) = 32),
            snapshot_document jsonb NOT NULL,
            retained_until timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_recommendation_input_snapshot_owner
                UNIQUE (user_id, recommendation_input_snapshot_id),
            CONSTRAINT ck_recommendation_input_snapshot_retention
                CHECK (retained_until > created_at)
        )
    """)
    op.execute("""
        CREATE INDEX ix_recommendation_snapshot_user_retention
        ON ml.recommendation_input_snapshot (user_id, retained_until DESC)
    """)
    op.execute("""
        CREATE FUNCTION app_private.protect_recommendation_input_snapshot()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'recommendation input snapshots are immutable';
            END IF;
            IF OLD.retained_until > now() THEN
                RAISE EXCEPTION 'recommendation input snapshot retention is active';
            END IF;
            RETURN OLD;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER tr_recommendation_input_snapshot_immutable
        BEFORE UPDATE OR DELETE ON ml.recommendation_input_snapshot
        FOR EACH ROW EXECUTE FUNCTION app_private.protect_recommendation_input_snapshot()
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION app_private.protect_recommendation_input_snapshot() FROM PUBLIC"
    )

    for statement in (
        "ALTER TABLE ml.recommendation_request ADD COLUMN surface text",
        "ALTER TABLE ml.recommendation_request ADD COLUMN pipeline_key text",
        "ALTER TABLE ml.recommendation_request ADD COLUMN pipeline_version text",
        "ALTER TABLE ml.recommendation_request ADD COLUMN pipeline_manifest_sha256 bytea",
        "ALTER TABLE ml.recommendation_request ADD COLUMN request_schema_version integer",
        "ALTER TABLE ml.recommendation_request ADD COLUMN request_canonicalization_version integer",
        "ALTER TABLE ml.recommendation_request ADD COLUMN request_sha256 bytea",
        "ALTER TABLE ml.recommendation_request ADD COLUMN recommendation_input_snapshot_id uuid",
        "ALTER TABLE ml.recommendation_request ADD COLUMN input_snapshot_sha256 bytea",
        "ALTER TABLE ml.recommendation_request ADD COLUMN interaction_watermark bigint",
        "ALTER TABLE ml.recommendation_request ADD COLUMN catalog_snapshot bigint",
        "ALTER TABLE ml.recommendation_request ADD COLUMN availability_snapshot_ref text",
        "ALTER TABLE ml.recommendation_request ADD COLUMN policy_snapshot_sha256 bytea",
        "ALTER TABLE ml.recommendation_request ADD COLUMN request_document jsonb",
        "ALTER TABLE ml.recommendation_request ADD COLUMN shadow boolean NOT NULL DEFAULT false",
    ):
        op.execute(statement)
    op.execute("""
        ALTER TABLE ml.recommendation_request
        ADD CONSTRAINT fk_recommendation_request_pipeline
        FOREIGN KEY (pipeline_key, pipeline_version)
        REFERENCES ml.recommendation_pipeline_version(pipeline_key, version) ON DELETE RESTRICT
    """)
    op.execute("""
        ALTER TABLE ml.recommendation_request
        ADD CONSTRAINT fk_recommendation_request_input_owner
        FOREIGN KEY (user_id, recommendation_input_snapshot_id)
        REFERENCES ml.recommendation_input_snapshot(user_id, recommendation_input_snapshot_id)
        ON DELETE SET NULL (recommendation_input_snapshot_id)
    """)
    op.execute("""
        ALTER TABLE ml.recommendation_request
        ADD CONSTRAINT uq_recommendation_request_owner
        UNIQUE (user_id, recommendation_request_id)
    """)
    op.execute("""
        ALTER TABLE ml.recommendation_request
        ADD CONSTRAINT ck_recommendation_request_surface
        CHECK (surface IS NULL OR surface IN ('recommendations', 'home', 'offline_pack'))
    """)
    op.execute("""
        ALTER TABLE ml.recommendation_request
        ADD CONSTRAINT ck_recommendation_request_replay_hashes CHECK (
            (pipeline_manifest_sha256 IS NULL OR octet_length(pipeline_manifest_sha256) = 32)
            AND (request_sha256 IS NULL OR octet_length(request_sha256) = 32)
            AND (input_snapshot_sha256 IS NULL OR octet_length(input_snapshot_sha256) = 32)
            AND (policy_snapshot_sha256 IS NULL OR octet_length(policy_snapshot_sha256) = 32)
        )
    """)
    op.execute("""
        ALTER TABLE ml.recommendation_request
        ADD CONSTRAINT ck_recommendation_request_replay_versions CHECK (
            (request_schema_version IS NULL OR request_schema_version >= 1)
            AND (request_canonicalization_version IS NULL OR request_canonicalization_version >= 1)
            AND (interaction_watermark IS NULL OR interaction_watermark >= 0)
            AND (catalog_snapshot IS NULL OR catalog_snapshot >= 0)
        )
    """)
    op.execute(
        "ALTER TABLE ml.recommendation_item ADD COLUMN contributions jsonb NOT NULL "
        "DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE ml.recommendation_item ADD COLUMN reason_codes text[] NOT NULL "
        "DEFAULT ARRAY[]::text[]"
    )
    op.execute(
        "ALTER TABLE ml.recommendation_item ADD COLUMN item_provenance jsonb NOT NULL "
        "DEFAULT '{}'::jsonb"
    )

    op.execute(
        "ALTER TABLE ml.offline_recommendation_pack DROP CONSTRAINT ck_offline_pack_encoding"
    )
    op.execute("""
        ALTER TABLE ml.offline_recommendation_pack
        ADD CONSTRAINT ck_offline_pack_encoding
        CHECK (payload_encoding IN ('RAW_JSON', 'JSON_ZSTD', 'PROTOBUF_ZSTD'))
    """)
    for statement in (
        "ALTER TABLE ml.offline_recommendation_pack ADD COLUMN recommendation_request_id uuid",
        "ALTER TABLE ml.offline_recommendation_pack ADD COLUMN pipeline_key text",
        "ALTER TABLE ml.offline_recommendation_pack ADD COLUMN pipeline_version text",
        "ALTER TABLE ml.offline_recommendation_pack ADD COLUMN input_snapshot_sha256 bytea",
    ):
        op.execute(statement)
    op.execute("""
        ALTER TABLE ml.offline_recommendation_pack
        ADD CONSTRAINT fk_offline_pack_request_owner
        FOREIGN KEY (user_id, recommendation_request_id)
        REFERENCES ml.recommendation_request(user_id, recommendation_request_id)
        ON DELETE RESTRICT
    """)
    op.execute("""
        ALTER TABLE ml.offline_recommendation_pack
        ADD CONSTRAINT fk_offline_pack_pipeline
        FOREIGN KEY (pipeline_key, pipeline_version)
        REFERENCES ml.recommendation_pipeline_version(pipeline_key, version) ON DELETE RESTRICT
    """)
    op.execute("""
        ALTER TABLE ml.offline_recommendation_pack
        ADD CONSTRAINT ck_offline_pack_snapshot_hash
        CHECK (input_snapshot_sha256 IS NULL OR octet_length(input_snapshot_sha256) = 32)
    """)


def downgrade() -> None:
    """Remove only P11-owned additive objects and columns."""
    op.execute(
        "ALTER TABLE ml.offline_recommendation_pack DROP CONSTRAINT ck_offline_pack_snapshot_hash"
    )
    op.execute(
        "ALTER TABLE ml.offline_recommendation_pack DROP CONSTRAINT fk_offline_pack_pipeline"
    )
    op.execute(
        "ALTER TABLE ml.offline_recommendation_pack DROP CONSTRAINT fk_offline_pack_request_owner"
    )
    for column in (
        "input_snapshot_sha256",
        "pipeline_version",
        "pipeline_key",
        "recommendation_request_id",
    ):
        op.execute(f"ALTER TABLE ml.offline_recommendation_pack DROP COLUMN {column}")
    op.execute(
        "ALTER TABLE ml.offline_recommendation_pack DROP CONSTRAINT ck_offline_pack_encoding"
    )
    op.execute("""
        ALTER TABLE ml.offline_recommendation_pack
        ADD CONSTRAINT ck_offline_pack_encoding
        CHECK (payload_encoding IN ('JSON_ZSTD', 'PROTOBUF_ZSTD'))
    """)
    for column in ("item_provenance", "reason_codes", "contributions"):
        op.execute(f"ALTER TABLE ml.recommendation_item DROP COLUMN {column}")
    op.execute(
        "ALTER TABLE ml.recommendation_request DROP CONSTRAINT "
        "ck_recommendation_request_replay_versions"
    )
    op.execute(
        "ALTER TABLE ml.recommendation_request DROP CONSTRAINT "
        "ck_recommendation_request_replay_hashes"
    )
    op.execute(
        "ALTER TABLE ml.recommendation_request DROP CONSTRAINT ck_recommendation_request_surface"
    )
    op.execute(
        "ALTER TABLE ml.recommendation_request DROP CONSTRAINT "
        "fk_recommendation_request_input_owner"
    )
    op.execute(
        "ALTER TABLE ml.recommendation_request DROP CONSTRAINT uq_recommendation_request_owner"
    )
    op.execute(
        "ALTER TABLE ml.recommendation_request DROP CONSTRAINT fk_recommendation_request_pipeline"
    )
    for column in (
        "shadow",
        "request_document",
        "policy_snapshot_sha256",
        "availability_snapshot_ref",
        "catalog_snapshot",
        "interaction_watermark",
        "input_snapshot_sha256",
        "recommendation_input_snapshot_id",
        "request_sha256",
        "request_canonicalization_version",
        "request_schema_version",
        "pipeline_manifest_sha256",
        "pipeline_version",
        "pipeline_key",
        "surface",
    ):
        op.execute(f"ALTER TABLE ml.recommendation_request DROP COLUMN {column}")
    op.execute("DROP TABLE ml.recommendation_input_snapshot")
    op.execute("DROP FUNCTION app_private.protect_recommendation_input_snapshot()")
    op.execute("DROP TABLE ml.recommendation_pipeline_version")
    op.execute("DROP FUNCTION app_private.protect_recommendation_pipeline_manifest()")
