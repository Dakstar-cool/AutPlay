"""Add hash-only S1B device-admission and exact-key lifecycle state.

Revision ID: 0021_s1b_device_admission
Revises: 0020_a1b_discovery_runtime
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021_s1b_device_admission"
down_revision: str | None = "0020_a1b_discovery_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE account.device_admission (
          request_id uuid PRIMARY KEY,
          request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256) = 32),
          server_instance_id uuid NOT NULL,
          identity_epoch bigint NOT NULL CHECK(identity_epoch >= 1),
          identity_thumbprint_sha256 bytea NOT NULL CHECK(octet_length(identity_thumbprint_sha256) = 32),
          device_public_key_spki bytea NOT NULL,
          device_key_thumbprint_sha256 bytea NOT NULL CHECK(octet_length(device_key_thumbprint_sha256) = 32),
          nickname text NOT NULL CHECK(length(nickname) BETWEEN 1 AND 120),
          device_model_hint text CHECK(device_model_hint IS NULL OR length(device_model_hint) <= 96),
          platform text NOT NULL CHECK(platform = 'ANDROID'),
          app_version text NOT NULL CHECK(length(app_version) BETWEEN 1 AND 32),
          api_major integer NOT NULL CHECK(api_major = 1),
          requested_at timestamptz NOT NULL,
          state text NOT NULL CHECK(state IN ('PENDING', 'APPROVED', 'REJECTED', 'BLOCKED', 'EXPIRED', 'CANCELLED', 'EXCHANGED')),
          expires_at timestamptz NOT NULL,
          review_locator_hash bytea NOT NULL CHECK(octet_length(review_locator_hash) = 32),
          review_binding_hash bytea CHECK(review_binding_hash IS NULL OR octet_length(review_binding_hash) = 32),
          review_web_session_id uuid REFERENCES account.web_session(web_session_id) ON DELETE RESTRICT,
          poll_bearer_hash bytea NOT NULL CHECK(octet_length(poll_bearer_hash) = 32),
          last_poll_at timestamptz,
          secret_generation bigint NOT NULL DEFAULT 1 CHECK(secret_generation BETWEEN 1 AND 4),
          recovery_count bigint NOT NULL DEFAULT 0 CHECK(recovery_count BETWEEN 0 AND 3),
          last_recovery_at timestamptz,
          approved_user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          enrolled_device_id uuid REFERENCES account.device(device_id) ON DELETE RESTRICT,
          enrolled_session_id uuid REFERENCES account.user_session(session_id) ON DELETE RESTRICT,
          decision_action text CHECK(decision_action IS NULL OR decision_action IN ('APPROVE_ONCE', 'TRUST_DEVICE', 'REJECT', 'BLOCK_DEVICE')),
          created_at timestamptz NOT NULL DEFAULT now(),
          decided_at timestamptz,
          CONSTRAINT ck_device_admission_expiry CHECK(expires_at > created_at),
          CONSTRAINT ck_device_admission_decision CHECK((state IN ('PENDING', 'EXPIRED', 'CANCELLED') AND approved_user_id IS NULL) OR state NOT IN ('PENDING', 'EXPIRED', 'CANCELLED'))
        );
        CREATE UNIQUE INDEX uq_device_admission_pending_key ON account.device_admission(device_key_thumbprint_sha256)
          WHERE state = 'PENDING';
        CREATE UNIQUE INDEX uq_device_admission_locator ON account.device_admission(review_locator_hash)
          WHERE state = 'PENDING';
        CREATE INDEX ix_device_admission_poll_expiry ON account.device_admission(poll_bearer_hash, expires_at);
        CREATE INDEX ix_device_admission_cleanup ON account.device_admission(expires_at) WHERE state IN ('PENDING', 'APPROVED');

        CREATE TABLE account.device_admission_nonce (
          request_id uuid NOT NULL REFERENCES account.device_admission(request_id) ON DELETE CASCADE,
          scope text NOT NULL CHECK(scope IN ('POLL', 'RECOVERY')),
          nonce_sha256 bytea NOT NULL CHECK(octet_length(nonce_sha256) = 32),
          used_at timestamptz NOT NULL,
          PRIMARY KEY(request_id, scope, nonce_sha256)
        );

        CREATE TABLE account.trusted_device_key (
          user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          device_key_thumbprint_sha256 bytea NOT NULL CHECK(octet_length(device_key_thumbprint_sha256) = 32),
          device_public_key_spki bytea NOT NULL,
          approved_request_id uuid NOT NULL,
          key_reference uuid NOT NULL UNIQUE,
          revision bigint NOT NULL CHECK(revision >= 1),
          created_at timestamptz NOT NULL DEFAULT now(), removed_at timestamptz,
          PRIMARY KEY(user_id, device_key_thumbprint_sha256)
        );
        CREATE TABLE account.device_key_block (
          user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          device_key_thumbprint_sha256 bytea NOT NULL CHECK(octet_length(device_key_thumbprint_sha256) = 32),
          blocked_at timestamptz NOT NULL DEFAULT now(), unblocked_at timestamptz,
          request_id uuid,
          PRIMARY KEY(user_id, device_key_thumbprint_sha256)
        );
        CREATE INDEX ix_device_key_block_active ON account.device_key_block(device_key_thumbprint_sha256) WHERE unblocked_at IS NULL;
        CREATE TABLE account.trusted_device_reenrollment_challenge (
          challenge_id uuid PRIMARY KEY,
          user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          device_key_thumbprint_sha256 bytea NOT NULL CHECK(octet_length(device_key_thumbprint_sha256) = 32),
          request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256) = 32),
          client_nonce_sha256 bytea NOT NULL CHECK(octet_length(client_nonce_sha256) = 32),
          challenge_hash bytea NOT NULL CHECK(octet_length(challenge_hash) = 32),
          expires_at timestamptz NOT NULL, consumed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_trusted_reenrollment_challenge_expiry ON account.trusted_device_reenrollment_challenge(expires_at);
        CREATE TABLE account.device_admission_exchange_receipt (
          exchange_id uuid PRIMARY KEY, request_or_challenge_id uuid NOT NULL,
          request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
          device_key_thumbprint_sha256 bytea NOT NULL CHECK(octet_length(device_key_thumbprint_sha256)=32),
          device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
          session_id uuid NOT NULL REFERENCES account.user_session(session_id) ON DELETE RESTRICT,
          binding_commit_id uuid NOT NULL, receipt_expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_device_admission_receipt_expiry ON account.device_admission_exchange_receipt(receipt_expires_at);
        CREATE TABLE account.device_admission_web_operation_receipt (
          operation_id uuid PRIMARY KEY,
          actor_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
          web_session_id uuid NOT NULL REFERENCES account.web_session(web_session_id) ON DELETE RESTRICT,
          action text NOT NULL CHECK(length(action) BETWEEN 1 AND 80),
          target_id uuid,
          target_sha256 bytea NOT NULL CHECK(octet_length(target_sha256) = 32),
          request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256) = 32),
          terminal_at timestamptz NOT NULL,
          receipt_expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_device_admission_web_operation_receipt_expiry
          ON account.device_admission_web_operation_receipt(receipt_expires_at);
        CREATE TABLE account.device_admission_rate_window (
          rate_key_sha256 bytea PRIMARY KEY CHECK(octet_length(rate_key_sha256) = 32),
          scope text NOT NULL CHECK(scope IN ('KEY_DAY', 'SOURCE_15M', 'TRUSTED_KEY_15M', 'TRUSTED_ACCOUNT_15M')),
          window_started_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          attempt_count integer NOT NULL CHECK(attempt_count >= 1),
          CHECK(expires_at > window_started_at)
        );
        CREATE INDEX ix_device_admission_rate_window_expiry ON account.device_admission_rate_window(expires_at);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM account.device_admission)
             OR EXISTS (SELECT 1 FROM account.device_admission_nonce)
             OR EXISTS (SELECT 1 FROM account.trusted_device_key)
             OR EXISTS (SELECT 1 FROM account.device_key_block)
             OR EXISTS (SELECT 1 FROM account.trusted_device_reenrollment_challenge)
             OR EXISTS (SELECT 1 FROM account.device_admission_exchange_receipt)
             OR EXISTS (SELECT 1 FROM account.device_admission_web_operation_receipt)
             OR EXISTS (SELECT 1 FROM account.device_admission_rate_window) THEN
            RAISE EXCEPTION 'refusing S1B downgrade with device admission or trust evidence';
          END IF;
        END $$;
        DROP TABLE account.device_admission_rate_window;
        DROP TABLE account.device_admission_web_operation_receipt;
        DROP TABLE account.device_admission_exchange_receipt;
        DROP TABLE account.trusted_device_reenrollment_challenge;
        DROP TABLE account.device_key_block;
        DROP TABLE account.trusted_device_key;
        DROP TABLE account.device_admission_nonce;
        DROP TABLE account.device_admission;
        """
    )
