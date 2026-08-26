"""Close A1B owner authorization, artist eligibility, and Web replay gaps.

Revision ID: 0024_a1b_auth_closure
Revises: 0023_s2_profile_stats
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024_a1b_auth_closure"
down_revision: str | None = "0023_s2_profile_stats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE discovery.source_authorization (
          authorization_id uuid DEFAULT uuidv7(),
          user_id uuid NOT NULL,
          provider_id uuid NOT NULL,
          canonical_artist_id uuid NOT NULL,
          adapter_id text NOT NULL,
          adapter_version text NOT NULL,
          market_scope text NOT NULL,
          rights_capability text NOT NULL,
          revision bigint NOT NULL,
          policy_reference text NOT NULL,
          granted_by_bulk_operation_id uuid,
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          granted_at timestamptz NOT NULL DEFAULT now(),
          row_version bigint NOT NULL DEFAULT 1,
          CONSTRAINT source_authorization_pkey PRIMARY KEY(authorization_id),
          CONSTRAINT source_authorization_user_id_fkey FOREIGN KEY(user_id)
            REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          CONSTRAINT source_authorization_provider_id_fkey FOREIGN KEY(provider_id)
            REFERENCES identity.source_provider(provider_id) ON DELETE RESTRICT,
          CONSTRAINT source_authorization_canonical_artist_id_fkey FOREIGN KEY(canonical_artist_id)
            REFERENCES catalog.artist(artist_id) ON DELETE RESTRICT,
          CONSTRAINT source_authorization_bulk_operation_id_fkey
            FOREIGN KEY(granted_by_bulk_operation_id)
            REFERENCES discovery.bulk_operation(bulk_operation_id) ON DELETE SET NULL,
          CONSTRAINT uq_source_authorization_revision UNIQUE(authorization_id, revision),
          CONSTRAINT uq_source_authorization_owner_scope_revision
            UNIQUE(user_id, provider_id, market_scope, canonical_artist_id, revision),
          CONSTRAINT ck_source_auth_adapter CHECK(length(adapter_id) BETWEEN 1 AND 200),
          CONSTRAINT ck_source_auth_version CHECK(length(adapter_version) BETWEEN 1 AND 100),
          CONSTRAINT ck_source_auth_market CHECK(length(market_scope) BETWEEN 1 AND 100),
          CONSTRAINT ck_source_auth_rights CHECK(rights_capability = 'AUTHORIZED_DOWNLOAD'),
          CONSTRAINT ck_source_auth_revision CHECK(revision >= 1),
          CONSTRAINT ck_source_auth_policy CHECK(length(policy_reference) BETWEEN 1 AND 200),
          CONSTRAINT ck_source_auth_expiry CHECK(expires_at > granted_at),
          CONSTRAINT ck_source_auth_revocation
            CHECK(revoked_at IS NULL OR revoked_at >= granted_at),
          CONSTRAINT ck_source_auth_row_version CHECK(row_version >= 1)
        );
        CREATE INDEX ix_source_authorization_owner_expiry
          ON discovery.source_authorization(user_id, expires_at);
        CREATE UNIQUE INDEX uq_source_authorization_current_scope
          ON discovery.source_authorization(user_id, provider_id, market_scope, canonical_artist_id)
          WHERE revoked_at IS NULL;
        REVOKE ALL ON discovery.source_authorization FROM PUBLIC;

        ALTER TABLE discovery.candidate
          ADD COLUMN canonical_artist_id uuid,
          ADD COLUMN source_authorization_id uuid,
          ADD CONSTRAINT discovery_candidate_canonical_artist_id_fkey
            FOREIGN KEY(canonical_artist_id) REFERENCES catalog.artist(artist_id)
            ON DELETE RESTRICT,
          ADD CONSTRAINT discovery_candidate_source_authorization_fkey
            FOREIGN KEY(source_authorization_id, source_authorization_revision)
            REFERENCES discovery.source_authorization(authorization_id, revision)
            ON DELETE RESTRICT;

        CREATE TABLE importing.web_import_operation_receipt (
          user_id uuid NOT NULL,
          operation_id uuid NOT NULL,
          request_sha256 bytea NOT NULL,
          import_job_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT web_import_operation_receipt_pkey PRIMARY KEY(user_id, operation_id),
          CONSTRAINT web_import_operation_receipt_user_id_fkey FOREIGN KEY(user_id)
            REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          CONSTRAINT web_import_operation_receipt_import_job_id_fkey FOREIGN KEY(import_job_id)
            REFERENCES importing.import_job(import_job_id) ON DELETE CASCADE,
          CONSTRAINT ck_web_import_operation_receipt_hash
            CHECK(octet_length(request_sha256) = 32)
        );
        REVOKE ALL ON importing.web_import_operation_receipt FROM PUBLIC;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM discovery.source_authorization)
             OR EXISTS (SELECT 1 FROM importing.web_import_operation_receipt) THEN
            RAISE EXCEPTION 'refusing A1B authorization closure downgrade with retained state';
          END IF;
        END $$;

        DROP TABLE importing.web_import_operation_receipt;

        ALTER TABLE discovery.candidate
          DROP CONSTRAINT discovery_candidate_source_authorization_fkey,
          DROP CONSTRAINT discovery_candidate_canonical_artist_id_fkey,
          DROP COLUMN source_authorization_id,
          DROP COLUMN canonical_artist_id;

        DROP TABLE discovery.source_authorization;
        """
    )
