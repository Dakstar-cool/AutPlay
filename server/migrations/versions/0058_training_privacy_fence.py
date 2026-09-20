"""Irreversible serving fence for privacy-deleted training participants."""

from alembic import op
from sqlalchemy import text

revision = "0058_training_privacy_fence"
down_revision = "0057_training_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    previous = connection.scalar(
        text("SELECT pg_get_functiondef('ml.guard_training_participant()'::regprocedure)")
    )
    if not isinstance(previous, str):
        raise RuntimeError("unexpected training participant guard")
    connection.execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": "0058:ml.guard_training_participant", "ddl": previous},
    )
    op.execute("""
CREATE TABLE ml.training_publication_revocation (
 run_id uuid NOT NULL REFERENCES ml.training_run(run_id),
 owner_tag bytea NOT NULL,deletion_request_id uuid NOT NULL,revoked_at timestamptz NOT NULL,
 PRIMARY KEY(run_id,owner_tag),
 CONSTRAINT training_publication_revocation_owner_tag_check
 CHECK(octet_length(owner_tag)=32));
CREATE FUNCTION app_private.privacy_request_for_owner(owner_id uuid) RETURNS uuid
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT request_id FROM app_private.privacy_purge_context
 WHERE transaction_id=pg_current_xact_id() AND owner_id=$1 $$;
CREATE FUNCTION ml.guard_training_publication_revocation() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE owner_id uuid;
BEGIN
 IF TG_OP='INSERT' THEN
  SELECT user_id INTO owner_id FROM ml.training_participant
   WHERE run_id=NEW.run_id AND owner_tag=NEW.owner_tag;
  IF owner_id IS NOT NULL
   AND NEW.deletion_request_id=app_private.privacy_request_for_owner(owner_id)
   AND NEW.revoked_at>=transaction_timestamp() AND NEW.revoked_at<=clock_timestamp()
   AND EXISTS(SELECT 1 FROM ml.training_run
     WHERE run_id=NEW.run_id AND phase='PUBLISHED') THEN
   RETURN NEW;
  END IF;
 END IF;
 RAISE EXCEPTION 'training_publication_revocation_immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER guard_training_publication_revocation BEFORE INSERT OR UPDATE OR DELETE
 ON ml.training_publication_revocation FOR EACH ROW
 EXECUTE FUNCTION ml.guard_training_publication_revocation();
CREATE OR REPLACE FUNCTION ml.guard_training_participant() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE deletion_request uuid;
BEGIN
 IF TG_OP='INSERT' AND EXISTS(SELECT 1 FROM ml.training_run
   WHERE run_id=NEW.run_id AND phase='PREPARING'
   AND registration_xid=pg_current_xact_id()::text) THEN RETURN NEW; END IF;
 IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(OLD.user_id)
   AND EXISTS(SELECT 1 FROM ml.training_cleanup_claim
     WHERE run_id=OLD.run_id AND phase='COMPLETE') THEN
  deletion_request:=app_private.privacy_request_for_owner(OLD.user_id);
  INSERT INTO ml.training_publication_revocation(
    run_id,owner_tag,deletion_request_id,revoked_at)
   SELECT OLD.run_id,OLD.owner_tag,deletion_request,clock_timestamp()
   FROM ml.training_run WHERE run_id=OLD.run_id AND phase='PUBLISHED'
   ON CONFLICT(run_id,owner_tag) DO NOTHING;
  RETURN OLD;
 END IF;
 RAISE EXCEPTION 'training_participant_evidence_retained' USING ERRCODE='55000';
END $$;
REVOKE ALL ON ml.training_publication_revocation FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.privacy_request_for_owner(uuid),
 ml.guard_training_publication_revocation() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
LOCK TABLE ml.training_participant,ml.training_publication_revocation
 IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.training_publication_revocation) THEN
  RAISE EXCEPTION 'training_publication_revocation_evidence_retained' USING ERRCODE='55000';
 END IF;
END $$;
""")
    connection = op.get_bind()
    previous = connection.scalar(
        text(
            "SELECT definition FROM app_private.privacy_prior_definition "
            "WHERE name='0058:ml.guard_training_participant'"
        )
    )
    if not isinstance(previous, str):
        raise RuntimeError("missing training participant guard inventory")
    op.execute(previous)
    op.execute("""
DELETE FROM app_private.privacy_prior_definition
 WHERE name='0058:ml.guard_training_participant';
DROP TRIGGER guard_training_publication_revocation
 ON ml.training_publication_revocation;
DROP FUNCTION ml.guard_training_publication_revocation();
DROP TABLE ml.training_publication_revocation;
DROP FUNCTION app_private.privacy_request_for_owner(uuid);
""")
