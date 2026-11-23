"""Allow up to 25 selectable tracks in a single immutable internet search."""

from alembic import op

revision = "0076_internet_search_25"
down_revision = "0075_sona_collection_window"
branch_labels = None
depends_on = None


def _set_limit(limit: int) -> None:
    op.execute(f"""
        ALTER TABLE discovery.internet_search
        DROP CONSTRAINT internet_search_candidates_check,
        ADD CONSTRAINT internet_search_candidates_check
        CHECK (jsonb_typeof(candidates)='array' AND jsonb_array_length(candidates)<={limit})
    """)


def upgrade() -> None:
    _set_limit(25)


def downgrade() -> None:
    # PostgreSQL refuses a downgrade while any saved selection exceeds the old limit.
    _set_limit(5)
