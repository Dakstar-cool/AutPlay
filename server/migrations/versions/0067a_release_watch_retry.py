"""Durable bounded favorite refresh refusal backoff and owner purge edge."""

from alembic import op
from sqlalchemy import text

revision = "0067a_release_watch_retry"
down_revision = "0067_release_watch_auto_download"
branch_labels = None
depends_on = None

_KEY = "0067a:account.purge_account"
_ANCHOR = "$q$DELETE FROM library.favorite_artist_projection WHERE user_id=$1$q$"


def upgrade() -> None:
    op.execute("""
CREATE TABLE library.favorite_artist_refresh_retry (
 user_id uuid PRIMARY KEY REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 attempted_at timestamptz NOT NULL,
 retry_after timestamptz NOT NULL,
 CONSTRAINT ck_favorite_retry_interval
  CHECK(retry_after=attempted_at+INTERVAL '24 hours')
);
CREATE INDEX ix_favorite_refresh_retry_due
 ON library.favorite_artist_refresh_retry(retry_after,user_id);
REVOKE ALL ON library.favorite_artist_refresh_retry FROM PUBLIC;
""")
    connection = op.get_bind()
    previous = connection.scalar(
        text("SELECT pg_get_functiondef('account.purge_account(uuid,uuid)'::regprocedure)")
    )
    if not isinstance(previous, str) or previous.count(_ANCHOR) != 1:
        raise RuntimeError("unexpected favorite owner purge inventory")
    connection.execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": _KEY, "ddl": previous},
    )
    op.execute(
        previous.replace(
            _ANCHOR,
            "$q$DELETE FROM library.favorite_artist_refresh_retry WHERE user_id=$1$q$,\n "
            + _ANCHOR,
        )
    )


def downgrade() -> None:
    op.execute("""
LOCK TABLE library.favorite_artist_refresh_retry IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM library.favorite_artist_refresh_retry) THEN
  RAISE EXCEPTION 'favorite_refresh_retry_retained' USING ERRCODE='55000';
 END IF;
END $$;
""")
    connection = op.get_bind()
    previous = connection.scalar(
        text("SELECT definition FROM app_private.privacy_prior_definition WHERE name=:key"),
        {"key": _KEY},
    )
    if not isinstance(previous, str):
        raise RuntimeError("missing favorite owner purge inventory")
    op.execute(previous)
    connection.execute(
        text("DELETE FROM app_private.privacy_prior_definition WHERE name=:key"), {"key": _KEY}
    )
    op.execute("DROP TABLE library.favorite_artist_refresh_retry")
