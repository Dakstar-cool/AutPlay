"""Add A1C policy, bounded run, and acquisition-attempt lineage.

Revision ID: 0025_a1c_automation_runtime
Revises: 0024_a1b_auth_closure
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025_a1c_automation_runtime"
down_revision: str | None = "0024_a1b_auth_closure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create additive A1C state without changing legacy A1B semantics."""
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM identity.source_provider
            WHERE provider_id = '426dc183-ab26-5a6e-9350-3f8bb57cd575'::uuid
              AND provider_key = 'jamendo'
              AND adapter_id = 'autplay.jamendo.manual'
              AND adapter_version = '1.0.0'
              AND capabilities = ARRAY['SEARCH', 'DOWNLOAD']::text[]
          ) THEN
            RAISE EXCEPTION 'refusing A1C capability upgrade for unexpected Jamendo provider binding';
          END IF;
          UPDATE identity.source_provider
          SET capabilities = ARRAY['SEARCH', 'DOWNLOAD', 'RELEASE_WATCH']::text[]
          WHERE provider_id = '426dc183-ab26-5a6e-9350-3f8bb57cd575'::uuid;
        END $$;
        CREATE TABLE discovery.artist_policy (
          policy_id uuid DEFAULT uuidv7(), user_id uuid NOT NULL,
          canonical_artist_id uuid NOT NULL, provider_id uuid NOT NULL,
          provider_artist_id text NOT NULL, discovery_mode text NOT NULL,
          import_mode text NOT NULL, automation_enabled boolean NOT NULL DEFAULT false,
          last_checked_at timestamptz, next_eligible_at timestamptz,
          current_revision bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(), row_version bigint NOT NULL DEFAULT 1,
          CONSTRAINT artist_policy_pkey PRIMARY KEY(policy_id),
          CONSTRAINT artist_policy_user_id_fkey FOREIGN KEY(user_id) REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          CONSTRAINT artist_policy_canonical_artist_id_fkey FOREIGN KEY(canonical_artist_id) REFERENCES catalog.artist(artist_id) ON DELETE RESTRICT,
          CONSTRAINT artist_policy_provider_id_fkey FOREIGN KEY(provider_id) REFERENCES identity.source_provider(provider_id) ON DELETE RESTRICT,
          CONSTRAINT uq_artist_policy_owner_lookup UNIQUE(policy_id, user_id),
          CONSTRAINT uq_artist_policy_owner_artist UNIQUE(user_id, canonical_artist_id),
          CONSTRAINT ck_artist_policy_provider CHECK(provider_id = '426dc183-ab26-5a6e-9350-3f8bb57cd575'::uuid),
          CONSTRAINT ck_artist_policy_provider_artist CHECK(provider_artist_id ~ '^[0-9]{1,20}$'),
          CONSTRAINT ck_artist_policy_discovery_mode CHECK(discovery_mode IN ('MANUAL_ONLY', 'SCHEDULED', 'DISABLED')),
          CONSTRAINT ck_artist_policy_import_mode CHECK(import_mode IN ('REVIEW_REQUIRED', 'AUTO_IMPORT')),
          CONSTRAINT ck_artist_policy_next_eligible CHECK(
            (discovery_mode IN ('MANUAL_ONLY', 'DISABLED') AND next_eligible_at IS NULL) OR
            (discovery_mode = 'SCHEDULED' AND next_eligible_at IS NOT NULL)
          ),
          CONSTRAINT ck_artist_policy_automation_mode CHECK(automation_enabled = (discovery_mode = 'SCHEDULED')),
          CONSTRAINT ck_artist_policy_current_revision CHECK(current_revision >= 1),
          CONSTRAINT ck_artist_policy_row_version CHECK(row_version >= 1)
        );
        CREATE TABLE discovery.artist_policy_revision (
          policy_id uuid NOT NULL, revision bigint NOT NULL, owner_user_id uuid NOT NULL, discovery_mode text NOT NULL,
          import_mode text NOT NULL, automation_enabled boolean NOT NULL, change_kind text NOT NULL,
          confirmation_code text, operation_id uuid, request_sha256 bytea,
          last_checked_at timestamptz, next_eligible_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT artist_policy_revision_pkey PRIMARY KEY(policy_id, revision),
          CONSTRAINT artist_policy_revision_policy_id_fkey FOREIGN KEY(policy_id, owner_user_id) REFERENCES discovery.artist_policy(policy_id, user_id) ON DELETE RESTRICT,
          CONSTRAINT uq_artist_policy_revision_operation UNIQUE(owner_user_id, operation_id),
          CONSTRAINT ck_artist_policy_revision CHECK(revision >= 1),
          CONSTRAINT ck_artist_policy_revision_discovery_mode CHECK(discovery_mode IN ('MANUAL_ONLY', 'SCHEDULED', 'DISABLED')),
          CONSTRAINT ck_artist_policy_revision_automation_mode CHECK(automation_enabled = (discovery_mode = 'SCHEDULED')),
          CONSTRAINT ck_artist_policy_revision_import_mode CHECK(import_mode IN ('REVIEW_REQUIRED', 'AUTO_IMPORT')),
          CONSTRAINT ck_artist_policy_revision_change_kind CHECK(change_kind IN ('SAFE_DEFAULT', 'OWNER_CONFIRMED', 'DISABLED')),
          CONSTRAINT ck_artist_policy_revision_confirmation CHECK(
            (operation_id IS NULL AND request_sha256 IS NULL AND change_kind = 'SAFE_DEFAULT' AND confirmation_code IS NULL) OR
            (operation_id IS NOT NULL AND octet_length(request_sha256) = 32 AND
              ((import_mode = 'AUTO_IMPORT' AND confirmation_code = 'AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1') OR
               (import_mode = 'REVIEW_REQUIRED' AND confirmation_code IS NULL)))
          )
        );
        ALTER TABLE discovery.source_authorization
          ADD COLUMN purpose text NOT NULL DEFAULT 'MANUAL',
          ADD COLUMN policy_id uuid,
          ADD COLUMN policy_revision bigint,
          ADD CONSTRAINT source_authorization_policy_revision_fkey FOREIGN KEY(policy_id, policy_revision)
            REFERENCES discovery.artist_policy_revision(policy_id, revision) ON DELETE RESTRICT,
          ADD CONSTRAINT ck_source_auth_purpose CHECK(purpose IN ('MANUAL', 'AUTO_IMPORT')),
          ADD CONSTRAINT ck_source_auth_policy_lineage CHECK(
            (purpose = 'MANUAL' AND policy_id IS NULL AND policy_revision IS NULL) OR
            (purpose = 'AUTO_IMPORT' AND policy_id IS NOT NULL AND policy_revision >= 1)
          );
        ALTER TABLE discovery.source_authorization
          DROP CONSTRAINT uq_source_authorization_owner_scope_revision;
        ALTER TABLE discovery.source_authorization
          ADD CONSTRAINT uq_source_authorization_owner_scope_revision
            UNIQUE(user_id, provider_id, market_scope, canonical_artist_id, purpose, revision);
        DROP INDEX discovery.uq_source_authorization_current_scope;
        CREATE UNIQUE INDEX uq_source_authorization_current_scope
          ON discovery.source_authorization(user_id, provider_id, market_scope, canonical_artist_id, purpose)
          WHERE revoked_at IS NULL;

        CREATE TABLE discovery.run (
          run_id uuid DEFAULT uuidv7(), user_id uuid NOT NULL, policy_id uuid NOT NULL,
          policy_revision bigint NOT NULL, provider_id uuid NOT NULL, provider_artist_id text NOT NULL,
          adapter_id text NOT NULL, adapter_version text NOT NULL, canonical_query_sha256 bytea NOT NULL,
          due_slot_at timestamptz NOT NULL, operation_id uuid, request_sha256 bytea,
          state text NOT NULL DEFAULT 'QUEUED', job_id uuid, observed_count integer NOT NULL DEFAULT 0,
          auto_selected_count integer NOT NULL DEFAULT 0, page_count integer NOT NULL DEFAULT 0,
          checkpoint text, error_code text, started_at timestamptz, completed_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
          row_version bigint NOT NULL DEFAULT 1,
          CONSTRAINT discovery_run_pkey PRIMARY KEY(run_id),
          CONSTRAINT discovery_run_user_id_fkey FOREIGN KEY(user_id) REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          CONSTRAINT discovery_run_policy_revision_fkey FOREIGN KEY(policy_id, policy_revision) REFERENCES discovery.artist_policy_revision(policy_id, revision) ON DELETE RESTRICT,
          CONSTRAINT discovery_run_provider_id_fkey FOREIGN KEY(provider_id) REFERENCES identity.source_provider(provider_id) ON DELETE RESTRICT,
          CONSTRAINT discovery_run_job_id_fkey FOREIGN KEY(job_id) REFERENCES jobs.job(job_id) ON DELETE RESTRICT,
          CONSTRAINT uq_discovery_run_due_slot UNIQUE(policy_id, policy_revision, due_slot_at),
          CONSTRAINT uq_discovery_run_owner_operation UNIQUE(user_id, operation_id),
          CONSTRAINT ck_discovery_run_provider_artist CHECK(provider_artist_id ~ '^[0-9]{1,20}$'),
          CONSTRAINT ck_discovery_run_adapter_id CHECK(adapter_id = 'autplay.jamendo.manual'),
          CONSTRAINT ck_discovery_run_adapter_version CHECK(adapter_version = '1.0.0'),
          CONSTRAINT ck_discovery_run_query_hash CHECK(octet_length(canonical_query_sha256) = 32),
          CONSTRAINT ck_discovery_run_state CHECK(state IN ('QUEUED', 'RUNNING', 'PARTIAL', 'RETRY_WAIT', 'COMPLETED', 'FAILED_TERMINAL', 'CANCELLED')),
          CONSTRAINT ck_discovery_run_operation CHECK((operation_id IS NULL AND request_sha256 IS NULL) OR (operation_id IS NOT NULL AND octet_length(request_sha256) = 32)),
          CONSTRAINT ck_discovery_run_observed_count CHECK(observed_count BETWEEN 0 AND 50),
          CONSTRAINT ck_discovery_run_auto_selected_count CHECK(auto_selected_count BETWEEN 0 AND 10),
          CONSTRAINT ck_discovery_run_page_count CHECK(page_count BETWEEN 0 AND 2),
          CONSTRAINT ck_discovery_run_checkpoint CHECK(checkpoint IS NULL OR octet_length(convert_to(checkpoint, 'UTF8')) <= 2048),
          CONSTRAINT ck_discovery_run_error_code CHECK(error_code IS NULL OR length(error_code) BETWEEN 1 AND 100),
          CONSTRAINT ck_discovery_run_row_version CHECK(row_version >= 1)
        );
        CREATE TABLE discovery.run_page (
          run_id uuid NOT NULL, ordinal integer NOT NULL, page_offset integer NOT NULL,
          response_sha256 bytea NOT NULL, observed_count integer NOT NULL, checkpoint text, next_offset integer,
          received_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT discovery_run_page_pkey PRIMARY KEY(run_id, ordinal),
          CONSTRAINT discovery_run_page_run_id_fkey FOREIGN KEY(run_id) REFERENCES discovery.run(run_id) ON DELETE RESTRICT,
          CONSTRAINT ck_discovery_run_page_ordinal CHECK(ordinal BETWEEN 0 AND 1),
          CONSTRAINT ck_discovery_run_page_offset CHECK(page_offset IN (0, 25)),
          CONSTRAINT ck_discovery_run_page_offset_ordinal CHECK(page_offset = ordinal * 25),
          CONSTRAINT ck_discovery_run_page_response_hash CHECK(octet_length(response_sha256) = 32),
          CONSTRAINT ck_discovery_run_page_observed_count CHECK(observed_count BETWEEN 0 AND 25),
          CONSTRAINT ck_discovery_run_page_checkpoint CHECK(checkpoint IS NULL OR octet_length(convert_to(checkpoint, 'UTF8')) <= 2048)
          ,CONSTRAINT ck_discovery_run_page_next_offset CHECK(
            (ordinal = 0 AND next_offset IN (25)) OR (ordinal IN (0, 1) AND next_offset IS NULL)
          )
        );
        ALTER TABLE discovery.candidate
          ADD COLUMN released_at timestamptz,
          ADD COLUMN selection_origin text NOT NULL DEFAULT 'MANUAL',
          ADD COLUMN policy_id uuid,
          ADD COLUMN policy_revision bigint,
          ADD CONSTRAINT discovery_candidate_policy_revision_fkey FOREIGN KEY(policy_id, policy_revision)
            REFERENCES discovery.artist_policy_revision(policy_id, revision) ON DELETE RESTRICT,
          ADD CONSTRAINT ck_candidate_selection_origin CHECK(selection_origin IN ('MANUAL', 'AUTOMATIC')),
          ADD CONSTRAINT ck_candidate_policy_lineage CHECK(
            (selection_origin = 'MANUAL' AND policy_id IS NULL AND policy_revision IS NULL) OR
            (selection_origin = 'AUTOMATIC' AND policy_id IS NOT NULL AND policy_revision >= 1)
          );
        CREATE TABLE discovery.run_candidate (
          run_id uuid NOT NULL, candidate_id uuid NOT NULL, selected_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT discovery_run_candidate_pkey PRIMARY KEY(run_id, candidate_id),
          CONSTRAINT discovery_run_candidate_run_id_fkey FOREIGN KEY(run_id) REFERENCES discovery.run(run_id) ON DELETE RESTRICT,
          CONSTRAINT discovery_run_candidate_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES discovery.candidate(candidate_id) ON DELETE RESTRICT
        );
        CREATE TABLE discovery.acquisition_attempt (
          acquisition_attempt_id uuid DEFAULT uuidv7(), candidate_id uuid NOT NULL, origin text NOT NULL,
          policy_id uuid, policy_revision bigint, source_authorization_id uuid NOT NULL,
          source_authorization_revision bigint NOT NULL, job_id uuid, state text NOT NULL,
          error_code text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz, row_version bigint NOT NULL DEFAULT 1,
          CONSTRAINT acquisition_attempt_pkey PRIMARY KEY(acquisition_attempt_id),
          CONSTRAINT acquisition_attempt_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES discovery.candidate(candidate_id) ON DELETE RESTRICT,
          CONSTRAINT acquisition_attempt_policy_revision_fkey FOREIGN KEY(policy_id, policy_revision) REFERENCES discovery.artist_policy_revision(policy_id, revision) ON DELETE RESTRICT,
          CONSTRAINT acquisition_attempt_source_auth_fkey FOREIGN KEY(source_authorization_id, source_authorization_revision) REFERENCES discovery.source_authorization(authorization_id, revision) ON DELETE RESTRICT,
          CONSTRAINT acquisition_attempt_job_id_fkey FOREIGN KEY(job_id) REFERENCES jobs.job(job_id) ON DELETE RESTRICT,
          CONSTRAINT ck_acquisition_attempt_origin CHECK(origin IN ('MANUAL', 'AUTOMATIC')),
          CONSTRAINT ck_acquisition_attempt_policy_lineage CHECK((origin = 'MANUAL' AND policy_id IS NULL AND policy_revision IS NULL) OR (origin = 'AUTOMATIC' AND policy_id IS NOT NULL AND policy_revision >= 1)),
          CONSTRAINT ck_acquisition_attempt_state CHECK(state IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')),
          CONSTRAINT ck_acquisition_attempt_error_code CHECK(error_code IS NULL OR length(error_code) BETWEEN 1 AND 100),
          CONSTRAINT ck_acquisition_attempt_row_version CHECK(row_version >= 1)
        );
        CREATE TABLE discovery.candidate_action_receipt (
          action_receipt_id uuid NOT NULL DEFAULT uuidv7(), user_id uuid NOT NULL,
          candidate_id uuid NOT NULL, action text NOT NULL, operation_id uuid NOT NULL,
          request_sha256 bytea NOT NULL, result_disposition text NOT NULL,
          result_acquisition_state text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT candidate_action_receipt_pkey PRIMARY KEY(action_receipt_id),
          CONSTRAINT candidate_action_receipt_user_id_fkey FOREIGN KEY(user_id)
            REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          CONSTRAINT candidate_action_receipt_candidate_id_fkey FOREIGN KEY(candidate_id)
            REFERENCES discovery.candidate(candidate_id) ON DELETE RESTRICT,
          CONSTRAINT uq_candidate_action_receipt_owner_operation UNIQUE(user_id, operation_id),
          CONSTRAINT ck_candidate_action_receipt_action CHECK(action IN ('SELECT', 'RETRY', 'IGNORE')),
          CONSTRAINT ck_candidate_action_receipt_hash CHECK(octet_length(request_sha256) = 32),
          CONSTRAINT ck_candidate_action_receipt_disposition CHECK(result_disposition IN ('SELECTABLE', 'SELECTED', 'UNAVAILABLE', 'ALREADY_IN_LIBRARY', 'IDENTITY_REVIEW_REQUIRED', 'IGNORED')),
          CONSTRAINT ck_candidate_action_receipt_acquisition_state CHECK(result_acquisition_state IN ('NOT_REQUESTED', 'QUEUED', 'ACQUIRING', 'INGESTING', 'MATERIALIZING', 'READY', 'RETRY_WAIT', 'FAILED_TERMINAL', 'CANCELLED'))
        );
        ALTER TABLE discovery.candidate ADD COLUMN current_acquisition_attempt_id uuid;
        ALTER TABLE discovery.candidate ADD CONSTRAINT discovery_candidate_current_attempt_fkey
          FOREIGN KEY(current_acquisition_attempt_id) REFERENCES discovery.acquisition_attempt(acquisition_attempt_id) ON DELETE RESTRICT;

        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM vault.upload_session u LEFT JOIN discovery.candidate c
              ON c.candidate_id = u.source_candidate_id
            WHERE u.actor_kind = 'PROVIDER' AND (u.source_candidate_id IS NULL OR c.source_authorization_id IS NULL)
          ) THEN RAISE EXCEPTION 'refusing A1C lineage upgrade for provider upload without A1B authorization'; END IF;
          IF EXISTS (
            SELECT 1 FROM discovery.candidate
            WHERE acquisition_state <> 'NOT_REQUESTED' AND source_authorization_id IS NULL
          ) THEN RAISE EXCEPTION 'refusing A1C lineage upgrade for active candidate without authorization'; END IF;
        END $$;
        INSERT INTO discovery.acquisition_attempt(
          candidate_id, origin, source_authorization_id, source_authorization_revision,
          job_id, state, created_at, updated_at, completed_at
        )
        SELECT c.candidate_id, 'MANUAL', c.source_authorization_id,
          c.source_authorization_revision, c.job_id,
          CASE WHEN c.acquisition_state = 'CANCELLED' THEN 'CANCELLED'
               WHEN c.acquisition_state = 'FAILED_TERMINAL' THEN 'FAILED'
               WHEN c.acquisition_state = 'READY' THEN 'COMPLETED'
               WHEN c.acquisition_state = 'QUEUED' THEN 'QUEUED' ELSE 'RUNNING' END,
          c.created_at, c.updated_at,
          CASE WHEN c.acquisition_state IN ('CANCELLED', 'FAILED_TERMINAL', 'READY') THEN c.updated_at END
        FROM discovery.candidate c
        WHERE c.acquisition_state <> 'NOT_REQUESTED' AND c.source_authorization_id IS NOT NULL;
        UPDATE discovery.candidate c
          SET current_acquisition_attempt_id = a.acquisition_attempt_id
        FROM discovery.acquisition_attempt a
        WHERE a.candidate_id = c.candidate_id AND a.origin = 'MANUAL';
        ALTER TABLE vault.upload_session ADD COLUMN source_acquisition_attempt_id uuid;
        UPDATE vault.upload_session u SET source_acquisition_attempt_id = a.acquisition_attempt_id
        FROM discovery.acquisition_attempt a
        WHERE u.actor_kind = 'PROVIDER' AND a.candidate_id = u.source_candidate_id AND a.origin = 'MANUAL';
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM vault.upload_session WHERE actor_kind = 'PROVIDER' AND source_acquisition_attempt_id IS NULL) THEN
            RAISE EXCEPTION 'refusing A1C lineage upgrade with unresolved provider upload';
          END IF;
        END $$;
        ALTER TABLE vault.upload_session ADD CONSTRAINT upload_session_source_acquisition_attempt_id_fkey
          FOREIGN KEY(source_acquisition_attempt_id) REFERENCES discovery.acquisition_attempt(acquisition_attempt_id) ON DELETE RESTRICT;
        ALTER TABLE vault.upload_session DROP CONSTRAINT uq_upload_session_source_candidate;
        ALTER TABLE vault.upload_session ADD CONSTRAINT uq_upload_session_source_acquisition_attempt UNIQUE(source_acquisition_attempt_id);
        ALTER TABLE vault.upload_session DROP CONSTRAINT ck_upload_session_actor;
        ALTER TABLE vault.upload_session ADD CONSTRAINT ck_upload_session_actor CHECK(
          (actor_kind = 'DEVICE' AND device_id IS NOT NULL AND source_candidate_id IS NULL AND source_acquisition_attempt_id IS NULL) OR
          (actor_kind = 'PROVIDER' AND device_id IS NULL AND source_candidate_id IS NOT NULL AND source_acquisition_attempt_id IS NOT NULL)
        );
        CREATE INDEX ix_artist_policy_due ON discovery.artist_policy(automation_enabled, next_eligible_at);
        CREATE INDEX ix_discovery_run_policy_slot ON discovery.run(policy_id, due_slot_at);
        CREATE INDEX ix_acquisition_attempt_candidate ON discovery.acquisition_attempt(candidate_id, created_at);
        CREATE INDEX ix_candidate_action_receipt_candidate ON discovery.candidate_action_receipt(candidate_id, created_at);
        CREATE UNIQUE INDEX uq_acquisition_attempt_active_candidate ON discovery.acquisition_attempt(candidate_id) WHERE state IN ('QUEUED', 'RUNNING');
        CREATE FUNCTION app_private.reject_artist_policy_binding_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.user_id IS DISTINCT FROM OLD.user_id
             OR NEW.canonical_artist_id IS DISTINCT FROM OLD.canonical_artist_id
             OR NEW.provider_id IS DISTINCT FROM OLD.provider_id
             OR NEW.provider_artist_id IS DISTINCT FROM OLD.provider_artist_id THEN
            RAISE EXCEPTION 'artist_policy binding is immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_artist_policy_binding_immutable
          BEFORE UPDATE ON discovery.artist_policy
          FOR EACH ROW EXECUTE FUNCTION app_private.reject_artist_policy_binding_mutation();
        CREATE FUNCTION app_private.reject_artist_policy_revision_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'artist_policy_revision is immutable';
        END $$;
        CREATE TRIGGER trg_artist_policy_revision_immutable
          BEFORE UPDATE OR DELETE ON discovery.artist_policy_revision
          FOR EACH ROW EXECUTE FUNCTION app_private.reject_artist_policy_revision_mutation();
        REVOKE ALL ON FUNCTION app_private.reject_artist_policy_revision_mutation() FROM PUBLIC;
        REVOKE ALL ON FUNCTION app_private.reject_artist_policy_binding_mutation() FROM PUBLIC;
        REVOKE ALL ON discovery.artist_policy, discovery.artist_policy_revision, discovery.run,
          discovery.run_page, discovery.run_candidate, discovery.acquisition_attempt,
          discovery.candidate_action_receipt FROM PUBLIC;
        """
    )


def downgrade() -> None:
    """Refuse an evidence-destroying rollback, then restore the A1B shape."""
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM discovery.artist_policy) OR EXISTS (SELECT 1 FROM discovery.artist_policy_revision)
             OR EXISTS (SELECT 1 FROM discovery.run) OR EXISTS (SELECT 1 FROM discovery.run_page)
             OR EXISTS (SELECT 1 FROM discovery.run_candidate) OR EXISTS (SELECT 1 FROM discovery.acquisition_attempt)
             OR EXISTS (SELECT 1 FROM discovery.candidate_action_receipt)
             OR EXISTS (SELECT 1 FROM vault.upload_session WHERE source_acquisition_attempt_id IS NOT NULL) THEN
            RAISE EXCEPTION 'refusing A1C downgrade with retained automation or acquisition lineage';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM identity.source_provider
            WHERE provider_id = '426dc183-ab26-5a6e-9350-3f8bb57cd575'::uuid
              AND provider_key = 'jamendo'
              AND adapter_id = 'autplay.jamendo.manual'
              AND adapter_version = '1.0.0'
              AND capabilities = ARRAY['SEARCH', 'DOWNLOAD', 'RELEASE_WATCH']::text[]
          ) THEN
            RAISE EXCEPTION 'refusing A1C capability downgrade for unexpected Jamendo provider binding';
          END IF;
        END $$;
        ALTER TABLE vault.upload_session DROP CONSTRAINT ck_upload_session_actor;
        ALTER TABLE vault.upload_session DROP CONSTRAINT uq_upload_session_source_acquisition_attempt;
        ALTER TABLE vault.upload_session DROP CONSTRAINT upload_session_source_acquisition_attempt_id_fkey;
        ALTER TABLE vault.upload_session DROP COLUMN source_acquisition_attempt_id;
        ALTER TABLE vault.upload_session ADD CONSTRAINT uq_upload_session_source_candidate UNIQUE(source_candidate_id);
        ALTER TABLE vault.upload_session ADD CONSTRAINT ck_upload_session_actor CHECK(
          (actor_kind = 'DEVICE' AND device_id IS NOT NULL AND source_candidate_id IS NULL) OR
          (actor_kind = 'PROVIDER' AND device_id IS NULL AND source_candidate_id IS NOT NULL)
        );
        ALTER TABLE discovery.candidate DROP CONSTRAINT discovery_candidate_current_attempt_fkey;
        ALTER TABLE discovery.candidate DROP COLUMN current_acquisition_attempt_id;
        DROP TRIGGER trg_artist_policy_binding_immutable ON discovery.artist_policy;
        DROP FUNCTION app_private.reject_artist_policy_binding_mutation();
        DROP TRIGGER trg_artist_policy_revision_immutable ON discovery.artist_policy_revision;
        DROP FUNCTION app_private.reject_artist_policy_revision_mutation();
        DROP TABLE discovery.candidate_action_receipt;
        DROP TABLE discovery.acquisition_attempt;
        DROP TABLE discovery.run_candidate;
        ALTER TABLE discovery.candidate DROP CONSTRAINT ck_candidate_policy_lineage;
        ALTER TABLE discovery.candidate DROP CONSTRAINT ck_candidate_selection_origin;
        ALTER TABLE discovery.candidate DROP CONSTRAINT discovery_candidate_policy_revision_fkey;
        ALTER TABLE discovery.candidate DROP COLUMN policy_revision, DROP COLUMN policy_id,
          DROP COLUMN selection_origin, DROP COLUMN released_at;
        DROP TABLE discovery.run_page;
        DROP TABLE discovery.run;
        DROP INDEX discovery.uq_source_authorization_current_scope;
        ALTER TABLE discovery.source_authorization
          DROP CONSTRAINT uq_source_authorization_owner_scope_revision;
        ALTER TABLE discovery.source_authorization DROP CONSTRAINT ck_source_auth_policy_lineage;
        ALTER TABLE discovery.source_authorization DROP CONSTRAINT ck_source_auth_purpose;
        ALTER TABLE discovery.source_authorization DROP CONSTRAINT source_authorization_policy_revision_fkey;
        ALTER TABLE discovery.source_authorization DROP COLUMN policy_revision, DROP COLUMN policy_id,
          DROP COLUMN purpose;
        CREATE UNIQUE INDEX uq_source_authorization_current_scope
          ON discovery.source_authorization(user_id, provider_id, market_scope, canonical_artist_id)
          WHERE revoked_at IS NULL;
        ALTER TABLE discovery.source_authorization
          ADD CONSTRAINT uq_source_authorization_owner_scope_revision
            UNIQUE(user_id, provider_id, market_scope, canonical_artist_id, revision);
        DROP TABLE discovery.artist_policy_revision;
        DROP TABLE discovery.artist_policy;
        UPDATE identity.source_provider
        SET capabilities = ARRAY['SEARCH', 'DOWNLOAD']::text[]
        WHERE provider_id = '426dc183-ab26-5a6e-9350-3f8bb57cd575'::uuid;
        """
    )
