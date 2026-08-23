"""Add owner-scoped A1B manual discovery operations and candidate state.

Revision ID: 0020_a1b_discovery_runtime
Revises: 0019_m6_web_admin_runtime
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020_a1b_discovery_runtime"
down_revision: str | None = "0019_m6_web_admin_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS discovery")
    op.execute(
        """
        DO $$
        DECLARE present_id uuid;
        BEGIN
          SELECT provider_id INTO present_id FROM identity.source_provider WHERE provider_key = 'jamendo';
          IF present_id IS NOT NULL AND present_id <> '426dc183-ab26-5a6e-9350-3f8bb57cd575'::uuid THEN
            RAISE EXCEPTION 'jamendo provider key already has another immutable provider id';
          END IF;
          IF present_id IS NULL THEN
            INSERT INTO identity.source_provider(
              provider_id, provider_key, display_name, adapter_id, adapter_version, capabilities, enabled
            ) VALUES (
              '426dc183-ab26-5a6e-9350-3f8bb57cd575', 'jamendo', 'Jamendo',
              'autplay.jamendo.manual', '1.0.0', ARRAY['SEARCH', 'DOWNLOAD']::text[], true
            );
          END IF;
        END $$
        """
    )
    op.execute(
        """
        CREATE TABLE discovery.bulk_operation (
          bulk_operation_id uuid DEFAULT uuidv7(),
          user_id uuid NOT NULL,
          import_job_id uuid,
          operation_id uuid NOT NULL,
          request_sha256 bytea NOT NULL,
          start_operation_id uuid,
          start_request_sha256 bytea,
          state text NOT NULL DEFAULT 'PREVIEW',
          selected_artist_count integer NOT NULL,
          planned_candidate_count integer NOT NULL,
          queued_count integer NOT NULL DEFAULT 0,
          ready_count integer NOT NULL DEFAULT 0,
          failed_count integer NOT NULL DEFAULT 0,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          started_at timestamptz,
          completed_at timestamptz,
          row_version bigint NOT NULL DEFAULT 1,
          CONSTRAINT bulk_operation_pkey PRIMARY KEY(bulk_operation_id),
          CONSTRAINT bulk_operation_user_id_fkey FOREIGN KEY(user_id) REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          CONSTRAINT bulk_operation_import_job_id_fkey FOREIGN KEY(import_job_id) REFERENCES importing.import_job(import_job_id) ON DELETE RESTRICT,
          CONSTRAINT uq_bulk_operation_owner_operation UNIQUE(user_id, operation_id),
          CONSTRAINT uq_bulk_operation_owner_start_operation UNIQUE(user_id, start_operation_id),
          CONSTRAINT ck_bulk_operation_hash CHECK(octet_length(request_sha256) = 32),
          CONSTRAINT ck_bulk_operation_start_hash CHECK(start_request_sha256 IS NULL OR octet_length(start_request_sha256) = 32),
          CONSTRAINT ck_bulk_operation_start_pair CHECK((start_operation_id IS NULL) = (start_request_sha256 IS NULL)),
          CONSTRAINT ck_bulk_operation_state CHECK(state IN ('PREVIEW', 'QUEUED', 'RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED_TERMINAL', 'CANCELLED')),
          CONSTRAINT ck_bulk_operation_artist_count CHECK(selected_artist_count BETWEEN 1 AND 20),
          CONSTRAINT ck_bulk_operation_candidate_count CHECK(planned_candidate_count BETWEEN 1 AND 200),
          CONSTRAINT ck_bulk_operation_counts CHECK(
            queued_count BETWEEN 0 AND planned_candidate_count AND
            ready_count BETWEEN 0 AND planned_candidate_count AND
            failed_count BETWEEN 0 AND planned_candidate_count
          ),
          CONSTRAINT ck_bulk_operation_row_version CHECK(row_version >= 1)
        );
        CREATE TABLE discovery.candidate (
          candidate_id uuid DEFAULT uuidv7(),
          user_id uuid NOT NULL,
          provider_id uuid NOT NULL,
          market_scope text NOT NULL DEFAULT 'GLOBAL',
          provider_track_id text NOT NULL,
          provider_artist_id text NOT NULL,
          title text NOT NULL,
          artist text NOT NULL,
          album text,
          duration_seconds integer NOT NULL,
          license_url text NOT NULL,
          share_url text NOT NULL,
          disposition text NOT NULL,
          acquisition_state text NOT NULL,
          analysis_state text,
          source_authorization_revision bigint NOT NULL,
          job_id uuid,
          staging_key text,
          external_reference_id uuid,
          recording_id uuid,
          user_track_ref_id uuid,
          library_entry_id uuid,
          audio_variant_id uuid,
          error_code text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          row_version bigint NOT NULL DEFAULT 1,
          CONSTRAINT discovery_candidate_pkey PRIMARY KEY(candidate_id),
          CONSTRAINT discovery_candidate_user_id_fkey FOREIGN KEY(user_id) REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          CONSTRAINT discovery_candidate_provider_id_fkey FOREIGN KEY(provider_id) REFERENCES identity.source_provider(provider_id) ON DELETE RESTRICT,
          CONSTRAINT discovery_candidate_job_id_fkey FOREIGN KEY(job_id) REFERENCES jobs.job(job_id) ON DELETE RESTRICT,
          CONSTRAINT discovery_candidate_external_reference_id_fkey FOREIGN KEY(external_reference_id) REFERENCES identity.external_reference(external_reference_id) ON DELETE RESTRICT,
          CONSTRAINT discovery_candidate_recording_id_fkey FOREIGN KEY(recording_id) REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
          CONSTRAINT discovery_candidate_user_track_ref_id_fkey FOREIGN KEY(user_track_ref_id) REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
          CONSTRAINT discovery_candidate_library_entry_id_fkey FOREIGN KEY(library_entry_id) REFERENCES library.library_entry(library_entry_id) ON DELETE RESTRICT,
          CONSTRAINT discovery_candidate_audio_variant_id_fkey FOREIGN KEY(audio_variant_id) REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
          CONSTRAINT uq_discovery_candidate_owner_provider_track UNIQUE(user_id, provider_id, market_scope, provider_track_id)
          ,CONSTRAINT ck_candidate_market CHECK(length(market_scope) BETWEEN 1 AND 100)
          ,CONSTRAINT ck_candidate_track_id CHECK(provider_track_id ~ '^[0-9]{1,20}$')
          ,CONSTRAINT ck_candidate_artist_id CHECK(provider_artist_id ~ '^[0-9]{1,20}$')
          ,CONSTRAINT ck_candidate_title CHECK(length(title) BETWEEN 1 AND 500)
          ,CONSTRAINT ck_candidate_artist CHECK(length(artist) BETWEEN 1 AND 500)
          ,CONSTRAINT ck_candidate_album CHECK(album IS NULL OR length(album) BETWEEN 1 AND 500)
          ,CONSTRAINT ck_candidate_duration CHECK(duration_seconds BETWEEN 1 AND 86400)
          ,CONSTRAINT ck_candidate_license_url CHECK(length(license_url) BETWEEN 1 AND 1000)
          ,CONSTRAINT ck_candidate_share_url CHECK(length(share_url) BETWEEN 1 AND 1000)
          ,CONSTRAINT ck_candidate_disposition CHECK(disposition IN ('SELECTABLE', 'SELECTED', 'UNAVAILABLE', 'ALREADY_IN_LIBRARY', 'IDENTITY_REVIEW_REQUIRED', 'IGNORED'))
          ,CONSTRAINT ck_candidate_acquisition_state CHECK(acquisition_state IN ('NOT_REQUESTED', 'QUEUED', 'ACQUIRING', 'INGESTING', 'MATERIALIZING', 'READY', 'RETRY_WAIT', 'FAILED_TERMINAL', 'CANCELLED'))
          ,CONSTRAINT ck_candidate_analysis_state CHECK(analysis_state IS NULL OR analysis_state IN ('QUEUED', 'RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED_RETRYABLE', 'FAILED_TERMINAL'))
          ,CONSTRAINT ck_candidate_auth_revision CHECK(source_authorization_revision >= 1)
          ,CONSTRAINT ck_candidate_staging_key CHECK(staging_key IS NULL OR staging_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$')
          ,CONSTRAINT ck_candidate_error_code CHECK(error_code IS NULL OR length(error_code) BETWEEN 1 AND 100)
          ,CONSTRAINT ck_candidate_row_version CHECK(row_version >= 1)
        );
        CREATE TABLE discovery.bulk_operation_item (
          bulk_operation_id uuid NOT NULL,
          candidate_id uuid NOT NULL,
          ordinal integer NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT bulk_operation_item_pkey PRIMARY KEY(bulk_operation_id, candidate_id),
          CONSTRAINT bulk_operation_item_operation_id_fkey FOREIGN KEY(bulk_operation_id) REFERENCES discovery.bulk_operation(bulk_operation_id) ON DELETE CASCADE,
          CONSTRAINT bulk_operation_item_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES discovery.candidate(candidate_id) ON DELETE RESTRICT,
          CONSTRAINT uq_bulk_operation_item_ordinal UNIQUE(bulk_operation_id, ordinal),
          CONSTRAINT ck_bulk_operation_item_ordinal CHECK(ordinal BETWEEN 0 AND 199)
        );
        CREATE INDEX ix_bulk_operation_owner_time ON discovery.bulk_operation(user_id, created_at DESC);
        CREATE INDEX ix_discovery_candidate_owner_state ON discovery.candidate(user_id, acquisition_state, updated_at);
        CREATE INDEX ix_bulk_operation_item_candidate ON discovery.bulk_operation_item(candidate_id);
        ALTER TABLE vault.upload_session ALTER COLUMN device_id DROP NOT NULL;
        ALTER TABLE vault.upload_session ADD COLUMN actor_kind text NOT NULL DEFAULT 'DEVICE';
        ALTER TABLE vault.upload_session ADD COLUMN source_candidate_id uuid;
        ALTER TABLE vault.upload_session ADD CONSTRAINT upload_session_source_candidate_id_fkey FOREIGN KEY(source_candidate_id) REFERENCES discovery.candidate(candidate_id) ON DELETE RESTRICT;
        ALTER TABLE vault.upload_session ADD CONSTRAINT uq_upload_session_source_candidate UNIQUE(source_candidate_id);
        ALTER TABLE vault.upload_session ADD CONSTRAINT ck_upload_session_actor CHECK(
          (actor_kind = 'DEVICE' AND device_id IS NOT NULL AND source_candidate_id IS NULL) OR
          (actor_kind = 'PROVIDER' AND device_id IS NULL AND source_candidate_id IS NOT NULL)
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM discovery.bulk_operation) OR EXISTS (SELECT 1 FROM discovery.candidate) OR EXISTS (SELECT 1 FROM vault.upload_session WHERE actor_kind = 'PROVIDER') THEN
            RAISE EXCEPTION 'refusing A1B downgrade with discovery evidence';
          END IF;
        END $$;
        ALTER TABLE vault.upload_session DROP CONSTRAINT ck_upload_session_actor;
        ALTER TABLE vault.upload_session DROP CONSTRAINT uq_upload_session_source_candidate;
        ALTER TABLE vault.upload_session DROP CONSTRAINT upload_session_source_candidate_id_fkey;
        ALTER TABLE vault.upload_session DROP COLUMN source_candidate_id;
        ALTER TABLE vault.upload_session DROP COLUMN actor_kind;
        ALTER TABLE vault.upload_session ALTER COLUMN device_id SET NOT NULL;
        DROP TABLE discovery.bulk_operation_item;
        DROP TABLE discovery.candidate;
        DROP TABLE discovery.bulk_operation;
        DROP SCHEMA discovery;
        """
    )
