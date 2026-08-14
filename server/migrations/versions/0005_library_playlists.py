"""Create user-library and playlist relations.

Revision ID: 0005_library_playlists
Revises: 0004_sync_jobs
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_tables, execute_reference

revision: str = "0005_library_playlists"
down_revision: str | None = "0004_sync_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "user_track_ref",
    "user_track_ref_external_reference",
    "library_entry",
    "user_track_preference",
    "playlist",
    "playlist_entry",
    "smart_playlist_rule",
)
QUALIFIED_TABLES = (
    "library.user_track_ref",
    "library.user_track_ref_external_reference",
    "library.library_entry",
    "library.user_track_preference",
    "playlist.playlist",
    "playlist.playlist_entry",
    "playlist.smart_playlist_rule",
)


def upgrade() -> None:
    execute_reference("table", TABLES)


def downgrade() -> None:
    drop_tables(tuple(reversed(QUALIFIED_TABLES)))
