"""Terminal training invalidation and exact publication authority registry."""

from alembic import op
from sqlalchemy import text

revision = "0055_training_work"
down_revision = "0054_training_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.training_run (
 run_id uuid PRIMARY KEY,
 server_instance_id uuid NOT NULL REFERENCES account.server_instance(server_instance_id),
 identity_epoch bigint NOT NULL,lineage_key_id text NOT NULL,
 source_sha256 bytea NOT NULL,dataset_sha256 bytea,request_sha256 bytea NOT NULL,
 participant_count integer NOT NULL,
 registration_xid text NOT NULL DEFAULT pg_current_xact_id()::text,
 phase text NOT NULL,revision bigint NOT NULL,created_at timestamptz NOT NULL,
 changed_at timestamptz NOT NULL,publication_operation_id uuid,publication_sha256 bytea,
 publication_hashes jsonb,
 CONSTRAINT training_run_state_check CHECK
 (phase IN ('PREPARING','READY','RUNNING','INVALIDATED','PUBLISHED') AND revision>=1
 AND identity_epoch>=1 AND length(lineage_key_id) BETWEEN 1 AND 128
 AND participant_count BETWEEN 1 AND 4096),
 CONSTRAINT training_run_hash_check CHECK(octet_length(source_sha256)=32
 AND octet_length(request_sha256)=32
 AND (dataset_sha256 IS NULL OR octet_length(dataset_sha256)=32)),
 CONSTRAINT training_run_publication_check CHECK
 ((phase='PUBLISHED' AND dataset_sha256 IS NOT NULL AND publication_operation_id IS NOT NULL
 AND publication_sha256 IS NOT NULL AND octet_length(publication_sha256)=32
 AND publication_hashes IS NOT NULL AND jsonb_typeof(publication_hashes)='object') OR
 (phase<>'PUBLISHED' AND publication_operation_id IS NULL AND publication_sha256 IS NULL
 AND publication_hashes IS NULL)),
 CONSTRAINT training_run_dataset_check CHECK
 (phase NOT IN ('READY','RUNNING','PUBLISHED') OR dataset_sha256 IS NOT NULL),
 CONSTRAINT training_run_publication_operation_unique UNIQUE(publication_operation_id));
CREATE TABLE ml.training_participant (
 run_id uuid NOT NULL REFERENCES ml.training_run(run_id),
 user_id uuid NOT NULL REFERENCES account.user_account(user_id),
 consent_revision bigint NOT NULL,owner_tag bytea NOT NULL,PRIMARY KEY(run_id,user_id),
 CONSTRAINT training_participant_consent_check
 CHECK(consent_revision>=1 AND octet_length(owner_tag)=32),
 CONSTRAINT training_participant_tag_unique UNIQUE(run_id,owner_tag));
CREATE INDEX ix_training_participant_owner ON ml.training_participant(user_id,run_id);
CREATE TABLE ml.training_cleanup_claim (
 run_id uuid PRIMARY KEY REFERENCES ml.training_run(run_id),phase text NOT NULL,
 requested_at timestamptz NOT NULL,completed_at timestamptz,
 CONSTRAINT training_cleanup_claim_state_check CHECK
 ((phase='PENDING' AND completed_at IS NULL) OR
 (phase='COMPLETE' AND completed_at IS NOT NULL AND completed_at>=requested_at)));

CREATE FUNCTION ml.guard_training_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'training_work_isolation_required' USING ERRCODE='55000';
 END IF;
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'training_run_evidence_retained' USING ERRCODE='55000';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.phase='PREPARING' AND NEW.revision=1 AND NEW.dataset_sha256 IS NULL
    AND NEW.registration_xid=pg_current_xact_id()::text THEN
   RETURN NEW;
  END IF;
 ELSE
  IF (NEW.run_id,NEW.server_instance_id,NEW.identity_epoch,NEW.lineage_key_id,
      NEW.source_sha256,NEW.request_sha256,NEW.created_at,NEW.participant_count,NEW.registration_xid)
    IS DISTINCT FROM
     (OLD.run_id,OLD.server_instance_id,OLD.identity_epoch,OLD.lineage_key_id,
      OLD.source_sha256,OLD.request_sha256,OLD.created_at,OLD.participant_count,OLD.registration_xid)
    OR NEW.revision<>OLD.revision+1 OR NEW.changed_at<OLD.changed_at
    OR (OLD.dataset_sha256 IS NOT NULL AND NEW.dataset_sha256 IS DISTINCT FROM OLD.dataset_sha256)
    OR NOT ((OLD.phase='PREPARING' AND NEW.phase IN ('READY','INVALIDATED'))
      OR (OLD.phase='READY' AND NEW.phase IN ('RUNNING','INVALIDATED'))
      OR (OLD.phase='RUNNING' AND NEW.phase IN ('PUBLISHED','INVALIDATED'))) THEN
   RAISE EXCEPTION 'training_run_transition_rejected' USING ERRCODE='55000';
  END IF;
  IF NEW.phase IN ('READY','RUNNING','PUBLISHED') AND
   ((SELECT count(*) FROM ml.training_participant WHERE run_id=OLD.run_id)<>OLD.participant_count
    OR EXISTS(SELECT 1 FROM ml.training_participant p
      LEFT JOIN account.user_account a ON a.user_id=p.user_id
      LEFT JOIN account.training_consent c ON c.user_id=p.user_id
      WHERE p.run_id=OLD.run_id AND (a.user_id IS NULL OR a.status<>'ACTIVE'
      OR a.deleted_at IS NOT NULL OR c.user_id IS NULL OR c.decision<>'GRANTED'
      OR c.revision<>p.consent_revision OR c.policy_version<>1))) THEN
   RAISE EXCEPTION 'training_consent_required' USING ERRCODE='55000';
  END IF;
  IF NEW.phase='PUBLISHED' AND
   ((SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(NEW.publication_hashes) key)
     IS DISTINCT FROM ARRAY['artifact_sha256','checkpoint_sha256','manifest_sha256',
       'tokenizer_manifest_sha256','tokenizer_sha256']::text[]
    OR EXISTS(SELECT 1 FROM jsonb_each(NEW.publication_hashes)
      WHERE jsonb_typeof(value)<>'string' OR (value #>> '{}')!~'^[0-9a-f]{64}$')) THEN
   RAISE EXCEPTION 'training_publication_hashes_rejected' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 RAISE EXCEPTION 'training_run_transition_rejected' USING ERRCODE='55000';
END $$;
CREATE TRIGGER guard_training_run BEFORE INSERT OR UPDATE OR DELETE ON ml.training_run
 FOR EACH ROW EXECUTE FUNCTION ml.guard_training_run();

CREATE FUNCTION ml.guard_training_participant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' AND EXISTS(SELECT 1 FROM ml.training_run
   WHERE run_id=NEW.run_id AND phase='PREPARING'
   AND registration_xid=pg_current_xact_id()::text) THEN RETURN NEW; END IF;
 IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(OLD.user_id)
   AND EXISTS(SELECT 1 FROM ml.training_cleanup_claim
     WHERE run_id=OLD.run_id AND phase='COMPLETE') THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'training_participant_evidence_retained' USING ERRCODE='55000';
END $$;
CREATE TRIGGER guard_training_participant BEFORE INSERT OR UPDATE OR DELETE
 ON ml.training_participant FOR EACH ROW EXECUTE FUNCTION ml.guard_training_participant();
CREATE FUNCTION ml.verify_training_registration() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM ml.training_participant WHERE run_id=NEW.run_id)
   <> NEW.participant_count THEN
  RAISE EXCEPTION 'training_participant_set_incomplete' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER verify_training_registration AFTER INSERT ON ml.training_run
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ml.verify_training_registration();

CREATE FUNCTION ml.guard_training_cleanup_claim() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' AND NEW.phase='PENDING' AND EXISTS(SELECT 1 FROM ml.training_run
   WHERE run_id=NEW.run_id AND phase IN ('INVALIDATED','PUBLISHED')) THEN RETURN NEW; END IF;
 -- Completion requires the subsequent retained object/writer cleanup protocol.
 -- No direct COMPLETE insert/update or expiry-based acknowledgement is allowed.
 RAISE EXCEPTION 'training_cleanup_evidence_required' USING ERRCODE='55000';
END $$;
CREATE TRIGGER guard_training_cleanup_claim BEFORE INSERT OR UPDATE OR DELETE
 ON ml.training_cleanup_claim FOR EACH ROW EXECUTE FUNCTION ml.guard_training_cleanup_claim();

CREATE FUNCTION account.invalidate_training_contributions(owner uuid) RETURNS void
 LANGUAGE plpgsql AS $$
DECLARE affected uuid;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'training_invalidation_isolation_required' USING ERRCODE='55000';
 END IF;
 FOR affected IN SELECT run_id FROM ml.training_participant
  WHERE user_id=owner ORDER BY run_id LOOP
  PERFORM 1 FROM ml.training_run WHERE run_id=affected FOR UPDATE;
  UPDATE ml.training_run SET phase='INVALIDATED',revision=revision+1,changed_at=clock_timestamp()
   WHERE run_id=affected AND phase IN ('PREPARING','READY','RUNNING');
  INSERT INTO ml.training_cleanup_claim VALUES(affected,'PENDING',clock_timestamp(),NULL)
   ON CONFLICT(run_id) DO NOTHING;
 END LOOP;
END $$;
CREATE FUNCTION account.training_consent_work_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'training_consent_isolation_required' USING ERRCODE='55000';
 END IF;
 -- Every accepted decision changes the exact revision captured by existing work.
 PERFORM account.invalidate_training_contributions(NEW.user_id);
 RETURN NEW;
END $$;
CREATE TRIGGER training_consent_work_changed AFTER INSERT OR UPDATE ON account.training_consent
 FOR EACH ROW EXECUTE FUNCTION account.training_consent_work_changed();
CREATE FUNCTION account.training_owner_unavailable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status<>'ACTIVE' OR NEW.deleted_at IS NOT NULL THEN
  PERFORM account.invalidate_training_contributions(NEW.user_id);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER training_owner_unavailable AFTER UPDATE ON account.user_account
 FOR EACH ROW EXECUTE FUNCTION account.training_owner_unavailable();
REVOKE ALL ON FUNCTION ml.guard_training_run(),ml.guard_training_participant(),
 ml.guard_training_cleanup_claim(),ml.verify_training_registration(),
 account.invalidate_training_contributions(uuid),
 account.training_consent_work_changed(),account.training_owner_unavailable() FROM PUBLIC;
ALTER TABLE account.training_consent ADD CONSTRAINT training_consent_terminal_revision_check
 CHECK(revision<=9007199254740991 AND (decision<>'GRANTED' OR revision<9007199254740991));
""")
    connection = op.get_bind()
    previous = connection.scalar(
        text("SELECT pg_get_functiondef('account.purge_account(uuid,uuid)'::regprocedure)")
    )
    anchor = "$q$DELETE FROM account.training_consent_operation WHERE user_id=$1$q$"
    if not isinstance(previous, str) or previous.count(anchor) != 1:
        raise RuntimeError("unexpected privacy purge inventory")
    connection.execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": "0055:account.purge_account", "ddl": previous},
    )
    # Participant DELETE checks durable cleanup completion before removing owner linkage.
    op.execute(
        previous.replace(
            anchor, "$q$DELETE FROM ml.training_participant WHERE user_id=$1$q$,\n " + anchor
        )
    )


def downgrade() -> None:
    op.execute("""
LOCK TABLE account.user_account,account.training_consent,
 ml.training_run,ml.training_participant,ml.training_cleanup_claim
 IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.training_run) OR EXISTS(SELECT 1 FROM ml.training_participant)
 OR EXISTS(SELECT 1 FROM ml.training_cleanup_claim) THEN
  RAISE EXCEPTION 'training_work_evidence_retained' USING ERRCODE='55000';
 END IF;
END $$;
""")
    connection = op.get_bind()
    previous = connection.scalar(
        text(
            "SELECT definition FROM app_private.privacy_prior_definition "
            "WHERE name='0055:account.purge_account'"
        )
    )
    if not isinstance(previous, str):
        raise RuntimeError("missing privacy purge inventory")
    op.execute(previous)
    op.execute("""
DELETE FROM app_private.privacy_prior_definition WHERE name='0055:account.purge_account';
DROP TRIGGER training_owner_unavailable ON account.user_account;
DROP TRIGGER training_consent_work_changed ON account.training_consent;
DROP FUNCTION account.training_owner_unavailable();
DROP FUNCTION account.training_consent_work_changed();
DROP FUNCTION account.invalidate_training_contributions(uuid);
DROP TABLE ml.training_cleanup_claim;
DROP TABLE ml.training_participant;
DROP TABLE ml.training_run;
DROP FUNCTION ml.guard_training_cleanup_claim();
DROP FUNCTION ml.guard_training_participant();
DROP FUNCTION ml.guard_training_run();
DROP FUNCTION ml.verify_training_registration();
ALTER TABLE account.training_consent DROP CONSTRAINT training_consent_terminal_revision_check;
""")
