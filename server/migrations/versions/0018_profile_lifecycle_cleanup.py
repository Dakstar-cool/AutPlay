"""Persist M5B lifecycle idempotency and receipt-cleanup indexes.

Revision ID: 0018_profile_lifecycle_cleanup
Revises: 0017_profile_pairing_runtime
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018_profile_lifecycle_cleanup"
down_revision: str | None = "0017_profile_pairing_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add only terminal command facts and bounded-expiry access paths."""
    op.execute("""
        CREATE TABLE account.profile_lifecycle_command (
            operation_id uuid PRIMARY KEY,
            actor_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
            actor_device_id uuid NOT NULL,
            actor_session_id uuid NOT NULL,
            actor_access_token_id uuid,
            action text NOT NULL CHECK (length(action) BETWEEN 1 AND 200),
            target_type text NOT NULL CHECK (length(target_type) BETWEEN 1 AND 100),
            target_id uuid NOT NULL,
            reason_code text CHECK (reason_code IS NULL OR reason_code ~ '^[a-z][a-z0-9_]{0,63}$'),
            outcome text NOT NULL CHECK (outcome IN ('PENDING', 'APPLIED', 'ALREADY_TERMINAL')),
            terminal_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_enrollment_exchange_receipt_expiry
          ON account.enrollment_exchange_receipt(receipt_expires_at);
        CREATE INDEX ix_session_rotation_receipt_expiry
          ON account.session_rotation_receipt(receipt_expires_at);
    """)


def downgrade() -> None:
    """Refuse to erase durable lifecycle-operation evidence."""
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM account.profile_lifecycle_command) THEN
            RAISE EXCEPTION 'refusing M5B lifecycle downgrade with command evidence';
          END IF;
        END $$
    """)
    op.execute("DROP INDEX account.ix_session_rotation_receipt_expiry")
    op.execute("DROP INDEX account.ix_enrollment_exchange_receipt_expiry")
    op.execute("DROP TABLE account.profile_lifecycle_command")
