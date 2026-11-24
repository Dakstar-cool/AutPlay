"""Keep explicitly published playlist versions independently of their source."""

from alembic import op

revision = "0078_playlist_snapshots"
down_revision = "0077_catalog_browse_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE playlist.shared_snapshot (
        token uuid PRIMARY KEY,
        version_key bytea UNIQUE NOT NULL CHECK (octet_length(version_key) = 32),
        document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object')
    )""")
    op.execute("""CREATE TABLE playlist.imported_track_reference (
        user_track_ref_id uuid PRIMARY KEY REFERENCES library.user_track_ref ON DELETE CASCADE,
        source_recording_id uuid
    )""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT FROM playlist.shared_snapshot)
           OR EXISTS (SELECT FROM playlist.imported_track_reference) THEN
            RAISE EXCEPTION 'published playlist links exist' USING ERRCODE = '23514';
        END IF;
    END $$""")
    op.drop_table("imported_track_reference", schema="playlist")
    op.drop_table("shared_snapshot", schema="playlist")
