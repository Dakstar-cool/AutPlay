"""Cache approved MusicBrainz edition and recording pages independently of audio."""

from alembic import op

revision = "0077_catalog_browse_pages"
down_revision = "0076_internet_search_25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE catalog.browse_page (
        page_key varchar PRIMARY KEY, document jsonb NOT NULL, observed_at timestamptz NOT NULL,
        CONSTRAINT browse_page_document_check
            CHECK (length(page_key) BETWEEN 1 AND 200 AND jsonb_typeof(document)='object')
    )""")

    op.execute("""CREATE TABLE discovery.catalog_download (
        operation_id uuid PRIMARY KEY, user_id uuid NOT NULL
          REFERENCES account.user_account(user_id)
            ON DELETE CASCADE, document jsonb NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT catalog_download_document_check CHECK (jsonb_typeof(document)='object')
    )""")
    op.execute(
        "CREATE INDEX catalog_download_owner_idx ON discovery.catalog_download(user_id, updated_at)"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT FROM discovery.catalog_download) THEN
            RAISE EXCEPTION 'catalog download history exists' USING ERRCODE = '23514';
        END IF;
    END $$""")
    op.drop_table("catalog_download", schema="discovery")
    op.drop_table("browse_page", schema="catalog")
