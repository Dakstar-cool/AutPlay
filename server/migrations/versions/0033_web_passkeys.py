"""Add browser passkeys without replacing M6 session authority."""

from alembic import op

revision = "0033_web_passkeys"
down_revision = "0032_track_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE account.web_passkey (
      passkey_id uuid PRIMARY KEY,
      server_instance_id uuid NOT NULL REFERENCES account.server_instance,
      user_id uuid NOT NULL REFERENCES account.user_account,
      user_handle bytea NOT NULL CHECK(octet_length(user_handle)=32),
      credential_id bytea NOT NULL UNIQUE CHECK(octet_length(credential_id) BETWEEN 1 AND 1024),
      public_key bytea NOT NULL CHECK(octet_length(public_key) BETWEEN 1 AND 2048),
      sign_count bigint NOT NULL CHECK(sign_count BETWEEN 0 AND 4294967295),
      backup_eligible boolean NOT NULL,
      backed_up boolean NOT NULL CHECK(NOT backed_up OR backup_eligible),
      label text NOT NULL CHECK(length(label) BETWEEN 1 AND 80),
      created_at timestamptz NOT NULL,
      last_used_at timestamptz,
      revoked_at timestamptz
    );
    CREATE INDEX ix_web_passkey_user ON account.web_passkey(user_id);
    CREATE TABLE account.web_passkey_ceremony (
      ceremony_id uuid PRIMARY KEY,
      operation_id uuid NOT NULL UNIQUE,
      purpose text NOT NULL CHECK(purpose IN ('REGISTER','LOGIN')),
      challenge bytea NOT NULL CHECK(octet_length(challenge)=32),
      binding_sha256 bytea NOT NULL CHECK(octet_length(binding_sha256)=32),
      origin text NOT NULL CHECK(length(origin) BETWEEN 1 AND 2048),
      rp_id text NOT NULL CHECK(length(rp_id) BETWEEN 1 AND 253),
      expires_at timestamptz NOT NULL,
      user_handle bytea CHECK(octet_length(user_handle)=32),
      user_id uuid REFERENCES account.user_account,
      web_session_id uuid,
      token_generation bigint CHECK(token_generation>=0),
      completed_request_sha256 bytea CHECK(octet_length(completed_request_sha256)=32),
      result_id uuid,
      consumed_at timestamptz,
      created_at timestamptz NOT NULL,
      CHECK(expires_at > created_at AND expires_at <= created_at + interval '5 minutes'),
      CHECK((purpose='REGISTER' AND user_id IS NOT NULL AND user_handle IS NOT NULL
             AND web_session_id IS NOT NULL AND token_generation IS NOT NULL)
         OR (purpose='LOGIN' AND user_id IS NULL AND user_handle IS NULL
             AND web_session_id IS NULL AND token_generation IS NULL)),
      CHECK((consumed_at IS NULL AND completed_request_sha256 IS NULL AND result_id IS NULL)
         OR (consumed_at IS NOT NULL AND completed_request_sha256 IS NOT NULL AND result_id IS NOT NULL))
    );
    CREATE INDEX ix_web_passkey_ceremony_expiry ON account.web_passkey_ceremony(expires_at);
    CREATE INDEX ix_web_passkey_ceremony_user ON account.web_passkey_ceremony(user_id,expires_at);
    CREATE TABLE account.web_passkey_revocation (
      operation_id uuid PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES account.user_account,
      passkey_id uuid NOT NULL REFERENCES account.web_passkey,
      actor_binding bytea NOT NULL CHECK(octet_length(actor_binding)=32),
      created_at timestamptz NOT NULL
    );
    ALTER TABLE account.web_session ADD COLUMN passkey_id uuid REFERENCES account.web_passkey;
    CREATE INDEX ix_web_session_passkey ON account.web_session(passkey_id);
    """)


def downgrade() -> None:
    op.execute("""
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM account.web_passkey)
         OR EXISTS(SELECT 1 FROM account.web_passkey_ceremony)
         OR EXISTS(SELECT 1 FROM account.web_passkey_revocation) THEN
        RAISE EXCEPTION 'Refusing to discard passkey authority or ceremony evidence';
      END IF;
    END $$;
    DROP INDEX account.ix_web_session_passkey;
    ALTER TABLE account.web_session DROP COLUMN passkey_id;
    DROP TABLE account.web_passkey_revocation;
    DROP TABLE account.web_passkey_ceremony;
    DROP TABLE account.web_passkey;
    """)
