"""Add M5B profile pairing evidence and v2 session lineage.

Revision ID: 0017_profile_pairing_runtime
Revises: 0016_artist_id_sync_contract
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017_profile_pairing_runtime"
down_revision: str | None = "0016_artist_id_sync_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only additive M5B tables; historical P03 rows stay valid."""
    op.execute("""
        CREATE TABLE account.server_instance (
            server_instance_id uuid PRIMARY KEY DEFAULT uuidv7(),
            identity_epoch bigint NOT NULL DEFAULT 1 CHECK (identity_epoch >= 1),
            identity_public_key_spki bytea NOT NULL CHECK (octet_length(identity_public_key_spki) BETWEEN 64 AND 256),
            identity_thumbprint_sha256 bytea NOT NULL CHECK (octet_length(identity_thumbprint_sha256) = 32),
            label_hint text NOT NULL CHECK (length(label_hint) BETWEEN 1 AND 80),
            api_origin text NOT NULL CHECK (length(api_origin) BETWEEN 1 AND 2048),
            stream_origin text NOT NULL CHECK (length(stream_origin) BETWEEN 1 AND 2048),
            capability_revision bigint NOT NULL DEFAULT 1 CHECK (capability_revision >= 1),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(identity_thumbprint_sha256)
        )
    """)
    op.execute("""
        ALTER TABLE account.device ADD COLUMN public_key_thumbprint_sha256 bytea;
        ALTER TABLE account.device ADD CONSTRAINT ck_device_public_key_thumbprint_len
        CHECK (public_key_thumbprint_sha256 IS NULL OR octet_length(public_key_thumbprint_sha256) = 32);
        ALTER TABLE account.user_session ADD COLUMN family_id uuid;
        ALTER TABLE account.user_session ADD COLUMN generation bigint;
        ALTER TABLE account.user_session ADD COLUMN session_mode text NOT NULL DEFAULT 'LEGACY';
        ALTER TABLE account.user_session ADD CONSTRAINT ck_user_session_generation
        CHECK (generation IS NULL OR generation >= 0);
        ALTER TABLE account.user_session ADD CONSTRAINT ck_user_session_mode
        CHECK (session_mode IN ('LEGACY', 'V2'));
    """)
    op.execute("""
        CREATE TABLE account.enrollment_invitation (
            invitation_id uuid PRIMARY KEY DEFAULT uuidv7(),
            server_instance_id uuid NOT NULL REFERENCES account.server_instance(server_instance_id) ON DELETE RESTRICT,
            user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
            issued_by_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
            invitation_secret_hash bytea NOT NULL UNIQUE CHECK (octet_length(invitation_secret_hash) = 32),
            issued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
            cancelled_at timestamptz, consumed_at timestamptz,
            CHECK (expires_at > issued_at)
        );
        CREATE INDEX ix_enrollment_invitation_user_active ON account.enrollment_invitation(user_id, expires_at)
        WHERE cancelled_at IS NULL AND consumed_at IS NULL;
        CREATE TABLE account.enrollment_exchange_receipt (
            exchange_id uuid PRIMARY KEY,
            invitation_id uuid NOT NULL REFERENCES account.enrollment_invitation(invitation_id) ON DELETE RESTRICT,
            request_sha256 bytea NOT NULL CHECK (octet_length(request_sha256) = 32),
            device_key_thumbprint_sha256 bytea NOT NULL CHECK (octet_length(device_key_thumbprint_sha256) = 32),
            device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
            session_id uuid NOT NULL REFERENCES account.user_session(session_id) ON DELETE RESTRICT,
            binding_commit_id uuid NOT NULL,
            receipt_expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE account.session_rotation_receipt (
            rotation_id uuid PRIMARY KEY,
            parent_session_id uuid NOT NULL REFERENCES account.user_session(session_id) ON DELETE RESTRICT,
            successor_session_id uuid NOT NULL REFERENCES account.user_session(session_id) ON DELETE RESTRICT,
            request_sha256 bytea NOT NULL CHECK (octet_length(request_sha256) = 32),
            device_key_thumbprint_sha256 bytea NOT NULL CHECK (octet_length(device_key_thumbprint_sha256) = 32),
            receipt_expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    """Refuse data-bearing rollback so replay evidence is never silently erased."""
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM account.enrollment_invitation)
             OR EXISTS (SELECT 1 FROM account.enrollment_exchange_receipt)
             OR EXISTS (SELECT 1 FROM account.session_rotation_receipt)
             OR EXISTS (SELECT 1 FROM account.user_session WHERE family_id IS NOT NULL OR session_mode <> 'LEGACY') THEN
            RAISE EXCEPTION 'refusing M5B downgrade with profile pairing evidence';
          END IF;
        END $$
    """)
    op.execute("DROP TABLE account.session_rotation_receipt")
    op.execute("DROP TABLE account.enrollment_exchange_receipt")
    op.execute("DROP INDEX account.ix_enrollment_invitation_user_active")
    op.execute("DROP TABLE account.enrollment_invitation")
    op.execute("ALTER TABLE account.user_session DROP CONSTRAINT ck_user_session_generation")
    op.execute("ALTER TABLE account.user_session DROP CONSTRAINT ck_user_session_mode")
    op.execute("ALTER TABLE account.user_session DROP COLUMN session_mode")
    op.execute("ALTER TABLE account.user_session DROP COLUMN generation")
    op.execute("ALTER TABLE account.user_session DROP COLUMN family_id")
    op.execute("ALTER TABLE account.device DROP CONSTRAINT ck_device_public_key_thumbprint_len")
    op.execute("ALTER TABLE account.device DROP COLUMN public_key_thumbprint_sha256")
    op.execute("DROP TABLE account.server_instance")
