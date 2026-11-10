"""Staged owner consent and independent operator gate for watch auto-download.

Revision ID: 0067_release_watch_auto_download
Revises: 0066_discovery_scan_schedule
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0067_release_watch_auto_download"
down_revision = "0066_discovery_scan_schedule"
branch_labels = None
depends_on = None

_PURGE_ANCHOR = "$q$DELETE FROM library.artist_watch_choice WHERE user_id=$1$q$"
_PURGE_STEPS = (
    "$q$DELETE FROM discovery.watch_auto_download_consent_revision WHERE user_id=$1$q$,\n "
    "$q$DELETE FROM discovery.watch_auto_download_consent WHERE user_id=$1$q$,\n " + _PURGE_ANCHOR
)
_PRIOR_KEY = "0067:account.purge_account"


def upgrade() -> None:
    op.execute("""
CREATE TABLE discovery.watch_auto_download_gate (
 singleton_id integer PRIMARY KEY CHECK(singleton_id=1),
 enabled boolean NOT NULL DEFAULT false,
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>=1),
 changed_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO discovery.watch_auto_download_gate(singleton_id,enabled,revision)
 VALUES (1,false,1);
CREATE FUNCTION discovery.bump_watch_auto_download_gate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.enabled IS DISTINCT FROM OLD.enabled THEN
  NEW.revision := OLD.revision+1;
  NEW.changed_at := clock_timestamp();
 ELSE
  NEW.revision := OLD.revision;
  NEW.changed_at := OLD.changed_at;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER watch_auto_download_gate_bump BEFORE UPDATE
 ON discovery.watch_auto_download_gate FOR EACH ROW
 EXECUTE FUNCTION discovery.bump_watch_auto_download_gate();
CREATE TABLE discovery.watch_auto_download_consent (
 user_id uuid PRIMARY KEY REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 enabled boolean NOT NULL,
 revision bigint NOT NULL CHECK(revision>=1),
 gate_revision bigint,
 confirmation_code text,
 changed_at timestamptz NOT NULL,
 CONSTRAINT ck_watch_auto_consent_confirmation CHECK(
  (enabled AND gate_revision IS NOT NULL AND
   confirmation_code='AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1')
  OR (NOT enabled AND gate_revision IS NULL AND confirmation_code IS NULL)
 )
);
CREATE TABLE discovery.watch_auto_download_consent_revision (
 user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 revision bigint NOT NULL CHECK(revision>=1),
 enabled boolean NOT NULL,
 gate_revision bigint,
 confirmation_code text,
 changed_at timestamptz NOT NULL,
 PRIMARY KEY(user_id,revision),
 CONSTRAINT ck_watch_auto_consent_revision_confirmation CHECK(
  (enabled AND gate_revision IS NOT NULL AND
   confirmation_code='AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1')
  OR (NOT enabled AND gate_revision IS NULL AND confirmation_code IS NULL)
 )
);
CREATE FUNCTION app_private.reject_watch_auto_consent_revision_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(OLD.user_id) THEN
  RETURN OLD;
 END IF;
 RAISE EXCEPTION 'watch_auto_download_consent_revision is immutable';
END $$;
CREATE TRIGGER watch_auto_consent_revision_immutable BEFORE UPDATE OR DELETE
 ON discovery.watch_auto_download_consent_revision FOR EACH ROW
 EXECUTE FUNCTION app_private.reject_watch_auto_consent_revision_mutation();
REVOKE ALL ON discovery.watch_auto_download_gate,
 discovery.watch_auto_download_consent,
 discovery.watch_auto_download_consent_revision FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.reject_watch_auto_consent_revision_mutation()
 FROM PUBLIC;
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
 IF EXISTS(SELECT 1 FROM discovery.watch_auto_download_consent)
 OR EXISTS(SELECT 1 FROM discovery.watch_auto_download_consent_revision)
 OR EXISTS(SELECT 1 FROM discovery.watch_auto_download_gate
              WHERE enabled OR revision<>1) THEN
  RAISE EXCEPTION 'watch_auto_download_evidence_retained' USING ERRCODE='55000';
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
        "DELETE FROM app_private.privacy_prior_definition WHERE name='0067:account.purge_account'"
    )
    op.execute("DROP TABLE discovery.watch_auto_download_consent_revision")
    op.execute("DROP TABLE discovery.watch_auto_download_consent")
    op.execute("DROP TABLE discovery.watch_auto_download_gate")
    op.execute("DROP FUNCTION app_private.reject_watch_auto_consent_revision_mutation()")
    op.execute("DROP FUNCTION discovery.bump_watch_auto_download_gate()")
