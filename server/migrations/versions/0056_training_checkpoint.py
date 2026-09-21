"""Immutable live-trainer checkpoint seal; portable metadata alone cannot publish."""

from alembic import op

revision = "0056_training_checkpoint"
down_revision = "0055_training_work"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.training_checkpoint (
 run_id uuid PRIMARY KEY REFERENCES ml.training_run(run_id),
 manifest_sha256 bytea NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT training_checkpoint_hash_check CHECK(octet_length(manifest_sha256)=32));
CREATE FUNCTION ml.guard_training_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE run ml.training_run%ROWTYPE; owner uuid;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'training_checkpoint_evidence_retained' USING ERRCODE='55000';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'training_work_isolation_required' USING ERRCODE='55000';
 END IF;
 SELECT * INTO run FROM ml.training_run WHERE run_id=NEW.run_id;
 PERFORM 1 FROM account.server_instance WHERE server_instance_id=run.server_instance_id
  AND identity_epoch=run.identity_epoch FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'training_identity_changed' USING ERRCODE='55000'; END IF;
 FOR owner IN SELECT user_id FROM ml.training_participant
  WHERE run_id=NEW.run_id ORDER BY user_id LOOP
  PERFORM 1 FROM account.user_account WHERE user_id=owner FOR UPDATE;
  PERFORM 1 FROM account.training_consent WHERE user_id=owner FOR UPDATE;
 END LOOP;
 SELECT * INTO run FROM ml.training_run WHERE run_id=NEW.run_id FOR UPDATE;
 IF run.phase IS DISTINCT FROM 'RUNNING'
  OR (SELECT count(*) FROM ml.training_participant WHERE run_id=run.run_id)<>run.participant_count
  OR EXISTS(SELECT 1 FROM ml.training_participant p
    LEFT JOIN account.user_account a ON a.user_id=p.user_id
    LEFT JOIN account.training_consent c ON c.user_id=p.user_id
    WHERE p.run_id=run.run_id AND (a.user_id IS NULL OR a.status<>'ACTIVE'
    OR a.deleted_at IS NOT NULL OR c.user_id IS NULL OR c.decision<>'GRANTED'
    OR c.revision<>p.consent_revision OR c.policy_version<>1)) THEN
  RAISE EXCEPTION 'training_checkpoint_authority_required' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_training_checkpoint BEFORE INSERT OR UPDATE OR DELETE
 ON ml.training_checkpoint FOR EACH ROW EXECUTE FUNCTION ml.guard_training_checkpoint();
CREATE FUNCTION ml.require_training_checkpoint_publication() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.phase='PUBLISHED' AND (NEW.publication_hashes->>'checkpoint_sha256') IS DISTINCT FROM
   (SELECT encode(manifest_sha256,'hex') FROM ml.training_checkpoint WHERE run_id=NEW.run_id) THEN
  RAISE EXCEPTION 'training_checkpoint_seal_required' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER require_training_checkpoint_publication BEFORE UPDATE ON ml.training_run
 FOR EACH ROW EXECUTE FUNCTION ml.require_training_checkpoint_publication();
REVOKE ALL ON FUNCTION ml.guard_training_checkpoint(),
 ml.require_training_checkpoint_publication() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
LOCK TABLE account.server_instance,account.user_account,account.training_consent,
 ml.training_run,ml.training_checkpoint IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.training_checkpoint) THEN
  RAISE EXCEPTION 'training_checkpoint_evidence_retained' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER require_training_checkpoint_publication ON ml.training_run;
DROP FUNCTION ml.require_training_checkpoint_publication();
DROP TABLE ml.training_checkpoint;
DROP FUNCTION ml.guard_training_checkpoint();
""")
