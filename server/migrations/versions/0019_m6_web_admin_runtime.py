"""Add isolated M6 administrative browser authority state.

Revision ID: 0019_m6_web_admin_runtime
Revises: 0018_profile_lifecycle_cleanup
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019_m6_web_admin_runtime"
down_revision: str | None = "0018_profile_lifecycle_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE account.web_session_invitation (
      invitation_id uuid PRIMARY KEY, server_instance_id uuid NOT NULL REFERENCES account.server_instance(server_instance_id) ON DELETE RESTRICT,
      user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT, issuer_kind text NOT NULL CHECK(length(issuer_kind) BETWEEN 1 AND 64),
      secret_sha256 bytea NOT NULL UNIQUE CHECK(octet_length(secret_sha256)=32), issued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
      consumed_at timestamptz, cancelled_at timestamptz, CHECK(expires_at > issued_at));
    CREATE TABLE account.web_login_challenge (
      challenge_id uuid PRIMARY KEY, login_operation_id uuid NOT NULL UNIQUE, cookie_sha256 bytea NOT NULL UNIQUE CHECK(octet_length(cookie_sha256)=32),
      nonce_sha256 bytea NOT NULL CHECK(octet_length(nonce_sha256)=32), expires_at timestamptz NOT NULL, consumed_at timestamptz);
    CREATE TABLE account.web_session (
      web_session_id uuid PRIMARY KEY, family_id uuid NOT NULL, server_instance_id uuid NOT NULL REFERENCES account.server_instance(server_instance_id) ON DELETE RESTRICT,
      user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT, token_generation bigint NOT NULL CHECK(token_generation>=0),
      token_sha256 bytea NOT NULL UNIQUE CHECK(octet_length(token_sha256)=32), csrf_sha256 bytea NOT NULL CHECK(octet_length(csrf_sha256)=32),
      issued_at timestamptz NOT NULL, token_issued_at timestamptz NOT NULL, last_activity_at timestamptz NOT NULL, idle_expires_at timestamptz NOT NULL,
      absolute_expires_at timestamptz NOT NULL, revoked_at timestamptz, CHECK(absolute_expires_at > issued_at));
    CREATE TABLE account.web_session_rotation_evidence (
      evidence_id uuid PRIMARY KEY, web_session_id uuid NOT NULL REFERENCES account.web_session(web_session_id) ON DELETE RESTRICT,
      predecessor_token_sha256 bytea NOT NULL UNIQUE CHECK(octet_length(predecessor_token_sha256)=32), expires_at timestamptz NOT NULL);
    CREATE TABLE account.web_terminal_receipt (
      operation_id uuid PRIMARY KEY, server_instance_id uuid NOT NULL REFERENCES account.server_instance(server_instance_id) ON DELETE RESTRICT,
      user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT, web_session_id uuid NOT NULL REFERENCES account.web_session(web_session_id) ON DELETE RESTRICT,
      token_generation bigint NOT NULL, token_sha256 bytea NOT NULL CHECK(octet_length(token_sha256)=32), action text NOT NULL, target_type text NOT NULL,
      target_id uuid NOT NULL, reason_code text, request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32), outcome text NOT NULL,
      login_challenge_id uuid, login_cookie_sha256 bytea CHECK(login_cookie_sha256 IS NULL OR octet_length(login_cookie_sha256)=32),
      login_invitation_sha256 bytea CHECK(login_invitation_sha256 IS NULL OR octet_length(login_invitation_sha256)=32),
      terminal_at timestamptz NOT NULL, receipt_expires_at timestamptz NOT NULL);
    CREATE TABLE account.web_login_rate_window (
      rate_key_sha256 bytea PRIMARY KEY CHECK(octet_length(rate_key_sha256)=32), window_started_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
      attempt_count integer NOT NULL CHECK(attempt_count>=0));
    CREATE INDEX ix_web_invitation_user_active ON account.web_session_invitation(user_id, expires_at) WHERE consumed_at IS NULL AND cancelled_at IS NULL;
    CREATE INDEX ix_web_invitation_expiry ON account.web_session_invitation(expires_at);
    CREATE INDEX ix_web_login_challenge_expiry ON account.web_login_challenge(expires_at);
    CREATE INDEX ix_web_session_user_active ON account.web_session(user_id, absolute_expires_at) WHERE revoked_at IS NULL;
    CREATE INDEX ix_web_session_expiry ON account.web_session(absolute_expires_at);
    CREATE INDEX ix_web_session_rotation_evidence_expiry ON account.web_session_rotation_evidence(expires_at);
    CREATE INDEX ix_web_terminal_receipt_expiry ON account.web_terminal_receipt(receipt_expires_at);
    CREATE INDEX ix_web_login_rate_window_expiry ON account.web_login_rate_window(expires_at);
    """)


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN IF EXISTS (SELECT 1 FROM account.web_session_invitation) OR EXISTS (SELECT 1 FROM account.web_session) THEN RAISE EXCEPTION 'refusing M6 downgrade with web authority evidence'; END IF; END $$"""
    )
    op.execute(
        "DROP TABLE account.web_login_rate_window; DROP TABLE account.web_terminal_receipt; DROP TABLE account.web_session_rotation_evidence; DROP TABLE account.web_session; DROP TABLE account.web_login_challenge; DROP TABLE account.web_session_invitation"
    )
