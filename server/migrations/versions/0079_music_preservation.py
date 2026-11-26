"""Owner-scoped preservation evidence and scan history."""

from alembic import op

revision = "0079_music_preservation"
down_revision = "0078_playlist_snapshots"
branch_labels = depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE discovery.preservation_case (
        owner uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE CASCADE,
        event text NOT NULL, document jsonb NOT NULL,
        discovered_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(owner,event))""")
    op.execute("""CREATE TABLE discovery.preservation_run (
        owner uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE CASCADE,
        run_id uuid NOT NULL, requested_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        sources jsonb NOT NULL DEFAULT '{}', PRIMARY KEY(owner,run_id))""")
    op.execute("""CREATE UNIQUE INDEX preservation_pending ON discovery.preservation_run(owner)
                  WHERE finished_at IS NULL""")
    op.execute("""CREATE TABLE discovery.preservation_check (
        owner uuid NOT NULL, run_id uuid NOT NULL, event text NOT NULL,
        document jsonb NOT NULL, is_new boolean NOT NULL, outcome text NOT NULL, job_id uuid,
        PRIMARY KEY(owner,run_id,event),
        FOREIGN KEY(owner,run_id) REFERENCES discovery.preservation_run ON DELETE CASCADE,
        FOREIGN KEY(owner,event) REFERENCES discovery.preservation_case ON DELETE CASCADE)""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT FROM discovery.preservation_run) OR
        EXISTS(SELECT FROM discovery.preservation_case) THEN
        RAISE EXCEPTION 'preservation history exists' USING ERRCODE='23514'; END IF; END $$""")
    for name in ("preservation_check", "preservation_run", "preservation_case"):
        op.drop_table(name, schema="discovery")
