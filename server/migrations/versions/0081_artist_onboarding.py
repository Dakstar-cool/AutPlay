"""Artist choices for new server accounts. NULL means onboarding is pending."""

from alembic import op

revision = "0081_artist_onboarding"
down_revision = "0080_external_collections"
branch_labels = depends_on = None


def upgrade() -> None:
    # Existing accounts have already been welcomed. New accounts start with NULL.
    op.execute("ALTER TABLE account.user_account ADD COLUMN onboarding_artists uuid[] DEFAULT '{}'")
    op.execute("ALTER TABLE account.user_account ALTER COLUMN onboarding_artists DROP DEFAULT")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT FROM account.user_account
        WHERE cardinality(onboarding_artists)>0) THEN RAISE EXCEPTION 'artist choices exist'
        USING ERRCODE='23514'; END IF; END $$""")
    op.drop_column("user_account", "onboarding_artists", schema="account")
