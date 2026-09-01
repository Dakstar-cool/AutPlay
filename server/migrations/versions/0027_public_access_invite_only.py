# ruff: noqa: E501
"""Add PA2 invite-only account provisioning evidence.

Revision ID: 0027_public_access_invite_only
Revises: 0026_s1d_guest_room_access
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027_public_access_invite_only"
down_revision: str | None = "0026_s1d_guest_room_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE account.account_invitation (
          invitation_id uuid PRIMARY KEY,
          issued_by_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          display_name text NOT NULL,
          secret_sha256 bytea NOT NULL UNIQUE,
          issued_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          cancelled_at timestamptz,
          consumed_at timestamptz,
          CONSTRAINT account_invitation_secret_hash_check CHECK(octet_length(secret_sha256)=32),
          CONSTRAINT account_invitation_name_check CHECK(length(display_name) BETWEEN 1 AND 120),
          CONSTRAINT account_invitation_expiry_check CHECK(expires_at>issued_at)
        );
        CREATE INDEX ix_account_invitation_expiry ON account.account_invitation(expires_at);
        CREATE TABLE account.account_provisioning_link (
          user_id uuid PRIMARY KEY REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          invitation_id uuid NOT NULL UNIQUE REFERENCES account.account_invitation(invitation_id) ON DELETE RESTRICT,
          issued_by_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          created_at timestamptz NOT NULL
        );
        CREATE TABLE account.account_registration_receipt (
          registration_id uuid PRIMARY KEY,
          invitation_id uuid NOT NULL REFERENCES account.account_invitation(invitation_id) ON DELETE RESTRICT,
          invitation_secret_sha256 bytea NOT NULL,
          request_sha256 bytea NOT NULL,
          device_key_thumbprint_sha256 bytea NOT NULL,
          user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
          session_id uuid NOT NULL REFERENCES account.user_session(session_id) ON DELETE RESTRICT,
          binding_commit_id uuid NOT NULL,
          receipt_expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL,
          CONSTRAINT account_registration_secret_hash_check CHECK(octet_length(invitation_secret_sha256)=32),
          CONSTRAINT account_registration_request_hash_check CHECK(octet_length(request_sha256)=32),
          CONSTRAINT account_registration_key_hash_check CHECK(octet_length(device_key_thumbprint_sha256)=32),
          CONSTRAINT account_registration_receipt_expiry_check CHECK(receipt_expires_at>created_at)
        );
        CREATE INDEX ix_account_registration_receipt_expiry ON account.account_registration_receipt(receipt_expires_at);
        CREATE TABLE account.account_provisioning_operation_receipt (
          operation_id uuid PRIMARY KEY,
          actor_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          action text NOT NULL,
          target_id uuid NOT NULL,
          command_sha256 bytea NOT NULL,
          outcome text NOT NULL,
          result_json text NOT NULL,
          created_at timestamptz NOT NULL,
          CONSTRAINT account_provisioning_operation_action_check CHECK(action IN ('CREATE','CANCEL','DISABLE')),
          CONSTRAINT account_provisioning_operation_hash_check CHECK(octet_length(command_sha256)=32),
          CONSTRAINT account_provisioning_operation_result_check CHECK(length(result_json)<=4096)
        );
        CREATE TABLE account.account_provisioning_rate_window (
          rate_key_sha256 bytea PRIMARY KEY,
          scope text NOT NULL,
          window_started_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          attempt_count integer NOT NULL,
          CONSTRAINT account_provisioning_rate_key_check CHECK(octet_length(rate_key_sha256)=32),
          CONSTRAINT account_provisioning_rate_scope_check CHECK(scope IN ('ISSUE_OWNER','REDEEM_INVITATION','REDEEM_SOURCE','REDEEM_SERVER')),
          CONSTRAINT account_provisioning_rate_attempt_check CHECK(attempt_count>=1),
          CONSTRAINT account_provisioning_rate_expiry_check CHECK(expires_at>window_started_at)
        );
        CREATE INDEX ix_account_provisioning_rate_expiry ON account.account_provisioning_rate_window(expires_at);
        REVOKE ALL ON ALL TABLES IN SCHEMA account FROM PUBLIC;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM account.account_invitation)
             OR EXISTS(SELECT 1 FROM account.account_provisioning_link)
             OR EXISTS(SELECT 1 FROM account.account_registration_receipt)
             OR EXISTS(SELECT 1 FROM account.account_provisioning_operation_receipt)
             OR EXISTS(SELECT 1 FROM account.account_provisioning_rate_window) THEN
            RAISE EXCEPTION 'refusing PA2 downgrade with public-access evidence';
          END IF;
        END $$;
        DROP TABLE account.account_provisioning_rate_window;
        DROP TABLE account.account_provisioning_operation_receipt;
        DROP TABLE account.account_registration_receipt;
        DROP TABLE account.account_provisioning_link;
        DROP TABLE account.account_invitation;
        """
    )
