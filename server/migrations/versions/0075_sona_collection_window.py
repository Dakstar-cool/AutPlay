"""Persist the server-wide Sona skip collection window."""

import os
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision = "0075_sona_collection_window"
down_revision = "0074_device_deletion_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    start = int(os.environ.get("AUTPLAY_SONA_SKIP_COLLECTION_START_MS") or "0")
    end = int(os.environ.get("AUTPLAY_SONA_SKIP_COLLECTION_END_MS") or "0")
    if (start or end) and not 0 < start < end <= 253402300799999:
        raise ValueError("invalid existing Sona collection window")
    op.execute("""
        CREATE TABLE ml.sona_collection_window (
            singleton boolean PRIMARY KEY CONSTRAINT sona_window_singleton_check CHECK(singleton),
            starts_at timestamptz NOT NULL,
            ends_at timestamptz,
            stopped_at timestamptz,
            revision bigint NOT NULL CONSTRAINT sona_window_revision_check CHECK(revision >= 0),
            CONSTRAINT sona_window_end_check CHECK(ends_at IS NULL OR ends_at > starts_at)
        )
    """)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    op.get_bind().execute(
        sa.text("""
        INSERT INTO ml.sona_collection_window (singleton, starts_at, ends_at, revision)
        VALUES (true, coalesce(CAST(:start AS timestamptz), date_trunc('milliseconds', clock_timestamp())),
                :end, 0)
    """),
        {
            "start": epoch + timedelta(milliseconds=start) if start else None,
            "end": epoch + timedelta(milliseconds=end) if end else None,
        },
    )


def downgrade() -> None:
    op.execute("""
        LOCK TABLE ml.sona_collection_window IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT FROM ml.sona_collection_window WHERE revision > 0) THEN
                RAISE EXCEPTION 'cannot discard an administered Sona collection window';
            END IF;
        END $$;
        DROP TABLE ml.sona_collection_window;
    """)
