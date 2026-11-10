"""Owner-scoped release-watch choices and versioned listening-history projection."""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0065_release_watch_follows"
down_revision = "0064_face_artifact_activation"
branch_labels = None
depends_on = None

_PURGE_ANCHOR = "$q$DELETE FROM library.listening_event WHERE user_id=$1$q$"
_PURGE_STEPS = (
    "$q$DELETE FROM library.favorite_artist_projection WHERE user_id=$1$q$,\n "
    "$q$DELETE FROM library.favorite_artist_refresh WHERE user_id=$1$q$,\n "
    "$q$DELETE FROM library.artist_watch_choice WHERE user_id=$1$q$,\n " + _PURGE_ANCHOR
)
_PRIOR_KEY = "0065:account.purge_account"


def upgrade() -> None:
    op.execute("""
CREATE TABLE library.artist_watch_choice (
 user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 artist_id uuid NOT NULL REFERENCES catalog.artist(artist_id) ON DELETE RESTRICT,
 manual_follow boolean NOT NULL DEFAULT false,
 explicit_exclusion boolean NOT NULL DEFAULT false,
 revision bigint NOT NULL DEFAULT 1 CHECK(revision >= 1),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(user_id,artist_id),
 CONSTRAINT ck_artist_watch_choice_exclusive CHECK(NOT (manual_follow AND explicit_exclusion))
);
CREATE INDEX ix_artist_watch_choice_followed ON library.artist_watch_choice(user_id,artist_id)
 WHERE manual_follow;
CREATE TABLE library.favorite_artist_refresh (
 user_id uuid PRIMARY KEY REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 generation bigint NOT NULL CHECK(generation >= 1),
 algorithm_version text NOT NULL CHECK(length(algorithm_version) BETWEEN 1 AND 80),
 evidence_window_start timestamptz NOT NULL,
 evidence_window_end timestamptz NOT NULL,
 evidence_event_count integer NOT NULL CHECK(evidence_event_count BETWEEN 0 AND 50000),
 evidence_sha256 bytea NOT NULL CHECK(octet_length(evidence_sha256)=32),
 refreshed_at timestamptz NOT NULL,
 CONSTRAINT ck_favorite_refresh_window CHECK(evidence_window_start < evidence_window_end)
);
CREATE TABLE library.favorite_artist_projection (
 user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 artist_id uuid NOT NULL REFERENCES catalog.artist(artist_id) ON DELETE RESTRICT,
 generation bigint NOT NULL CHECK(generation >= 1),
 algorithm_version text NOT NULL CHECK(length(algorithm_version) BETWEEN 1 AND 80),
 score_milli integer NOT NULL CHECK(score_milli BETWEEN 1 AND 1000000),
 qualified_play_count integer NOT NULL CHECK(qualified_play_count BETWEEN 3 AND 50000),
 distinct_recording_count integer NOT NULL CHECK(distinct_recording_count BETWEEN 2 AND 50000),
 last_played_at timestamptz NOT NULL,
 evidence_sha256 bytea NOT NULL CHECK(octet_length(evidence_sha256)=32),
 projected_at timestamptz NOT NULL,
 PRIMARY KEY(user_id,artist_id),
 CONSTRAINT fk_favorite_projection_refresh FOREIGN KEY(user_id)
  REFERENCES library.favorite_artist_refresh(user_id) ON DELETE RESTRICT
);
CREATE INDEX ix_favorite_projection_rank ON library.favorite_artist_projection
 (user_id,score_milli DESC,artist_id);
REVOKE ALL ON library.artist_watch_choice,library.favorite_artist_refresh,
 library.favorite_artist_projection FROM PUBLIC;
""")
    connection = op.get_bind()
    previous = connection.scalar(
        text("SELECT pg_get_functiondef('account.purge_account(uuid,uuid)'::regprocedure)")
    )
    if not isinstance(previous, str) or previous.count(_PURGE_ANCHOR) != 1:
        raise RuntimeError("unexpected privacy purge inventory")
    connection.execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": _PRIOR_KEY, "ddl": previous},
    )
    op.execute(previous.replace(_PURGE_ANCHOR, _PURGE_STEPS))


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM library.artist_watch_choice)
 OR EXISTS(SELECT 1 FROM library.favorite_artist_refresh)
 OR EXISTS(SELECT 1 FROM library.favorite_artist_projection) THEN
  RAISE EXCEPTION 'release_watch_owner_evidence_retained' USING ERRCODE='55000';
 END IF;
END $$;
""")
    previous = op.get_bind().scalar(
        text("SELECT definition FROM app_private.privacy_prior_definition WHERE name=:key"),
        {"key": _PRIOR_KEY},
    )
    if not isinstance(previous, str):
        raise RuntimeError("missing privacy purge inventory")
    op.execute(previous)
    op.execute(
        "DELETE FROM app_private.privacy_prior_definition WHERE name='0065:account.purge_account'"
    )
    op.execute("DROP TABLE library.favorite_artist_projection")
    op.execute("DROP TABLE library.favorite_artist_refresh")
    op.execute("DROP TABLE library.artist_watch_choice")
