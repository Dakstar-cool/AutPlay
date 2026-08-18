"""Add durable P09 sync runtime state.

Revision ID: 0012_sync_runtime
Revises: 0011_vault_runtime
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_sync_runtime"
down_revision: str | None = "0011_vault_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install additive storage for epochs, terminal ACKs and snapshots."""
    op.execute("ALTER TABLE sync.device_sync_cursor ADD COLUMN journal_epoch uuid")
    op.execute("ALTER TABLE sync.device_sync_cursor ADD COLUMN last_successful_sync_at timestamptz")
    op.execute("ALTER TABLE sync.device_event_inbox ADD COLUMN aggregate_local_id uuid")
    op.execute("ALTER TABLE sync.device_event_inbox ADD COLUMN idempotency_key text")
    op.execute("ALTER TABLE sync.device_event_inbox ADD COLUMN base_server_row_version bigint")
    op.execute("ALTER TABLE sync.device_event_inbox ADD COLUMN terminal_ack jsonb")
    op.execute("ALTER TABLE sync.sync_event ADD COLUMN operation text NOT NULL DEFAULT 'UPSERT'")
    op.execute("ALTER TABLE sync.sync_event ADD COLUMN server_row_version bigint")
    op.execute(
        "ALTER TABLE sync.sync_event ADD CONSTRAINT ck_sync_event_operation "
        "CHECK (operation IN ('UPSERT', 'DELETE', 'REDIRECT'))"
    )
    op.execute("""
        CREATE TABLE sync.bootstrap_session (
            snapshot_id uuid PRIMARY KEY DEFAULT uuidv7(),
            user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
            device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
            journal_epoch uuid NOT NULL,
            high_water_server_sequence bigint NOT NULL CHECK (high_water_server_sequence >= 0),
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_bootstrap_expiry CHECK (expires_at > created_at),
            CONSTRAINT fk_bootstrap_owner FOREIGN KEY (user_id, device_id)
                REFERENCES account.device(user_id, device_id) ON DELETE RESTRICT
        )
    """)
    op.execute("CREATE INDEX ix_bootstrap_session_expiry ON sync.bootstrap_session (expires_at)")
    op.execute("""
        CREATE TABLE sync.bootstrap_snapshot_item (
            snapshot_id uuid NOT NULL REFERENCES sync.bootstrap_session(snapshot_id)
                ON DELETE CASCADE,
            ordinal bigint NOT NULL CHECK (ordinal >= 1),
            aggregate_type text NOT NULL,
            aggregate_id uuid NOT NULL,
            server_row_version bigint NOT NULL CHECK (server_row_version >= 1),
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (snapshot_id, ordinal)
        )
    """)
    op.execute("""
        CREATE TABLE library.user_interaction_event (
            interaction_id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
            device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
            event_type text NOT NULL,
            recommendation_request_id uuid,
            recording_id uuid,
            source_rank integer,
            presentation_id uuid,
            impression_interaction_id uuid,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_interaction_type CHECK (event_type IN (
                'LISTENING_EVENT_RECORDED', 'RECOMMENDATION_IMPRESSION_RECORDED',
                'RECOMMENDATION_FEEDBACK_RECORDED')),
            CONSTRAINT fk_interaction_owner FOREIGN KEY (user_id, device_id)
                REFERENCES account.device(user_id, device_id) ON DELETE RESTRICT,
            CONSTRAINT ck_interaction_presentation CHECK (
                presentation_id IS NOT NULL OR event_type <> 'RECOMMENDATION_IMPRESSION_RECORDED')
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_interaction_presentation
        ON library.user_interaction_event
            (user_id, presentation_id, recommendation_request_id, source_rank)
        WHERE event_type = 'RECOMMENDATION_IMPRESSION_RECORDED'
    """)


def downgrade() -> None:
    """Remove only P09-owned additive objects."""
    op.execute("DROP TABLE library.user_interaction_event")
    op.execute("DROP TABLE sync.bootstrap_snapshot_item")
    op.execute("DROP TABLE sync.bootstrap_session")
    op.execute("ALTER TABLE sync.sync_event DROP CONSTRAINT ck_sync_event_operation")
    op.execute("ALTER TABLE sync.sync_event DROP COLUMN server_row_version")
    op.execute("ALTER TABLE sync.sync_event DROP COLUMN operation")
    op.execute("ALTER TABLE sync.device_event_inbox DROP COLUMN terminal_ack")
    op.execute("ALTER TABLE sync.device_event_inbox DROP COLUMN base_server_row_version")
    op.execute("ALTER TABLE sync.device_event_inbox DROP COLUMN idempotency_key")
    op.execute("ALTER TABLE sync.device_event_inbox DROP COLUMN aggregate_local_id")
    op.execute("ALTER TABLE sync.device_sync_cursor DROP COLUMN last_successful_sync_at")
    op.execute("ALTER TABLE sync.device_sync_cursor DROP COLUMN journal_epoch")
