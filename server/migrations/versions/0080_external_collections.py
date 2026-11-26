"""Owner-scoped collection previews and source-page checkpoints."""

from alembic import op

revision = "0080_external_collections"
down_revision = "0079_music_preservation"
branch_labels = depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE importing.collection_import (
        owner uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE CASCADE,
        operation_id uuid NOT NULL, collection_id uuid NOT NULL, provider text NOT NULL,
        source_key text NOT NULL, kind text NOT NULL, action text NOT NULL,
        state text NOT NULL, revision integer NOT NULL DEFAULT 0,
        snapshot jsonb NOT NULL DEFAULT '{}', import_job_id uuid REFERENCES importing.import_job
        ON DELETE SET NULL, created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (owner,operation_id))""")
    op.execute("""CREATE INDEX collection_import_owner_collection ON importing.collection_import
                  (owner,collection_id,created_at)""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT FROM importing.collection_import) THEN
        RAISE EXCEPTION 'collection import history exists' USING ERRCODE='23514'; END IF; END $$""")
    op.drop_table("collection_import", schema="importing")
