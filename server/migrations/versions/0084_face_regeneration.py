"""Exact deterministic Face payload regeneration without reopening completed work."""

from alembic import op
from sqlalchemy import text

revision = "0084_face_regeneration"
down_revision = "0083_face_lifecycle_gc"
branch_labels = None
depends_on = None

_FUNCTIONS = (
    "fence_face_timeline_mutation",
    "fence_face_work_incarnation",
    "verify_face_work_incarnation",
)


def upgrade() -> None:
    connection = op.get_bind()
    for name in _FUNCTIONS:
        definition = connection.scalar(
            text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"),
            {"name": f"app_private.{name}()"},
        )
        connection.execute(
            text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
            {"key": f"0084:{name}", "ddl": definition},
        )
    op.execute("""
ALTER TABLE ml.face_timeline_gc_receipt DROP CONSTRAINT face_timeline_gc_receipt_pkey,
 ADD PRIMARY KEY(face_timeline_id,prior_generation),
 DROP CONSTRAINT face_timeline_gc_receipt_prior_generation_check,
 ADD CONSTRAINT face_timeline_gc_receipt_prior_generation_check
 CHECK(prior_generation BETWEEN 1 AND 9007199254740986 AND prior_generation%4 IN (1,2));
ALTER TABLE ml.face_analysis_work
 ADD COLUMN regeneration_timeline_id uuid,
 ADD COLUMN regeneration_prior_generation bigint,
 ADD CONSTRAINT fk_face_work_regeneration_receipt
 FOREIGN KEY(regeneration_timeline_id,regeneration_prior_generation)
 REFERENCES ml.face_timeline_gc_receipt(face_timeline_id,prior_generation) ON DELETE RESTRICT,
 ADD CONSTRAINT ck_face_work_regeneration_pair CHECK(
 (regeneration_timeline_id IS NULL)=(regeneration_prior_generation IS NULL)),
 DROP CONSTRAINT ck_face_work_incarnation_origin,
 ADD CONSTRAINT ck_face_work_incarnation_origin CHECK(
 (execution_incarnation=1 AND predecessor_work_id IS NULL AND
  incarnation_predecessor_sponsor_id IS NULL AND incarnation_source_generation IS NULL
  AND regeneration_timeline_id IS NULL) OR
 (execution_incarnation>1 AND predecessor_work_id IS NOT NULL AND (
  (regeneration_timeline_id IS NULL AND incarnation_predecessor_sponsor_id IS NOT NULL
   AND incarnation_source_generation BETWEEN 2 AND 9007199254740990) OR
  (regeneration_timeline_id IS NOT NULL AND incarnation_predecessor_sponsor_id IS NULL
   AND incarnation_source_generation IS NULL AND incarnation_mutation_id IS NULL))));
CREATE UNIQUE INDEX uq_face_work_regeneration ON ml.face_analysis_work(
 regeneration_timeline_id,regeneration_prior_generation) WHERE regeneration_timeline_id IS NOT NULL;
DROP INDEX ml.uq_face_work_current_execution;
CREATE UNIQUE INDEX uq_face_work_current_execution ON ml.face_analysis_work(
 recording_id,audio_variant_id,source_sha256,lineage_sha256)
 WHERE state IN ('PENDING','CLAIMED','RETRY_WAIT');
ALTER TABLE ml.face_projection_fanout DROP CONSTRAINT uq_face_fanout_timeline_authority;
ALTER TABLE ml.face_timeline
 ADD COLUMN payload_work_id uuid REFERENCES ml.face_analysis_work(face_work_id) ON DELETE RESTRICT,
 ADD COLUMN payload_claim_token uuid,
 DROP CONSTRAINT face_timeline_state_generation_check,
 ADD CONSTRAINT face_timeline_state_generation_check
 CHECK(state_generation BETWEEN 1 AND 9007199254740988),
 DROP CONSTRAINT ck_face_timeline_state_times,
 ADD CONSTRAINT ck_face_timeline_state_times CHECK(
 (state='STAGED' AND state_generation%4=1 AND published_at IS NULL AND revoked_at IS NULL
  AND canonical_timeline IS NOT NULL) OR
 (state='PUBLISHED' AND state_generation%4=2 AND published_at IS NOT NULL AND revoked_at IS NULL
  AND canonical_timeline IS NOT NULL) OR
 (state='PURGED' AND revoked_at IS NOT NULL AND canonical_timeline IS NULL AND
  ((state_generation%4=3 AND published_at IS NULL) OR
   (state_generation%4=0 AND published_at IS NOT NULL)))),
 ADD CONSTRAINT ck_face_timeline_payload_claim CHECK(
 (state_generation<=4 AND payload_work_id IS NULL AND payload_claim_token IS NULL) OR
 (state_generation>4 AND payload_work_id IS NOT NULL AND payload_claim_token IS NOT NULL));
CREATE TABLE ml.face_timeline_regeneration_receipt (
 face_timeline_id uuid NOT NULL REFERENCES ml.face_timeline(face_timeline_id) ON DELETE RESTRICT,
 restored_generation bigint NOT NULL,
 face_work_id uuid NOT NULL UNIQUE REFERENCES ml.face_analysis_work(face_work_id) ON DELETE
  RESTRICT,
 claim_token uuid NOT NULL,
 result_sha256 bytea NOT NULL CHECK(octet_length(result_sha256)=32),
 previous_published_at timestamptz,
 previous_purged_at timestamptz NOT NULL,
 restored_at timestamptz NOT NULL,
 PRIMARY KEY(face_timeline_id,restored_generation),
 CHECK(restored_generation BETWEEN 5 AND 9007199254740985 AND restored_generation%4=1)
);
CREATE FUNCTION app_private.face_regeneration_work_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior ml.face_analysis_work%ROWTYPE; timeline ml.face_timeline%ROWTYPE;
BEGIN
 IF NEW.regeneration_timeline_id IS NULL THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('autplay.face.work.v1:' ||
  encode(NEW.source_sha256,'hex') || ':' || encode(NEW.lineage_sha256,'hex'),0));
 SELECT * INTO prior FROM ml.face_analysis_work WHERE recording_id=NEW.recording_id
  AND audio_variant_id=NEW.audio_variant_id AND source_sha256=NEW.source_sha256
  AND lineage_sha256=NEW.lineage_sha256 ORDER BY execution_incarnation DESC LIMIT 1 FOR UPDATE;
 SELECT * INTO timeline FROM ml.face_timeline
  WHERE face_timeline_id=NEW.regeneration_timeline_id FOR SHARE;
 IF prior.face_work_id IS DISTINCT FROM NEW.predecessor_work_id OR prior.state<>'SUCCEEDED'
  OR prior.execution_incarnation+1 IS DISTINCT FROM NEW.execution_incarnation
  OR prior.semantic_key_sha256 IS DISTINCT FROM NEW.semantic_key_sha256
  OR prior.identity_document IS DISTINCT FROM NEW.identity_document
  OR prior.artifact_policy_list_sha256 IS DISTINCT FROM NEW.artifact_policy_list_sha256
  OR timeline.state IS DISTINCT FROM 'PURGED'
  OR timeline.semantic_key_sha256 IS DISTINCT FROM NEW.semantic_key_sha256
  OR timeline.state_generation-2 IS DISTINCT FROM NEW.regeneration_prior_generation THEN
  RAISE EXCEPTION 'face_regeneration_origin_invalid' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER x_face_regeneration_origin BEFORE INSERT ON ml.face_analysis_work
 FOR EACH ROW EXECUTE FUNCTION app_private.face_regeneration_work_insert();
CREATE FUNCTION app_private.face_regeneration_work_verify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.regeneration_timeline_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM ml.face_analysis_sponsor WHERE face_work_id=NEW.face_work_id AND state='LIVE') THEN
  RAISE EXCEPTION 'face_regeneration_sponsor_required' USING ERRCODE='55000'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER z_face_regeneration_sponsor AFTER INSERT ON ml.face_analysis_work
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.face_regeneration_work_verify();
CREATE FUNCTION app_private.face_regeneration_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state='PURGED' AND NEW.state='STAGED' THEN
  INSERT INTO ml.face_timeline_regeneration_receipt VALUES(NEW.face_timeline_id,
   NEW.state_generation,NEW.payload_work_id,NEW.payload_claim_token,NEW.result_sha256,
   OLD.published_at,OLD.revoked_at,clock_timestamp());
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_regeneration_receipt AFTER UPDATE ON ml.face_timeline
 FOR EACH ROW EXECUTE FUNCTION app_private.face_regeneration_receipt();
CREATE FUNCTION app_private.fence_face_regeneration_receipt() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' OR pg_trigger_depth()<>2 THEN
  RAISE EXCEPTION 'face_regeneration_receipt_immutable' USING ERRCODE='55000'; END IF;
 IF NOT EXISTS(SELECT 1 FROM ml.face_timeline t JOIN ml.face_analysis_work w
  ON w.face_work_id=t.payload_work_id JOIN ml.face_timeline_gc_receipt g
  ON g.face_timeline_id=w.regeneration_timeline_id
   AND g.prior_generation=w.regeneration_prior_generation
  WHERE t.face_timeline_id=NEW.face_timeline_id AND t.state='STAGED'
   AND t.state_generation=NEW.restored_generation AND t.payload_work_id=NEW.face_work_id
   AND t.payload_claim_token=NEW.claim_token AND t.result_sha256=NEW.result_sha256
   AND g.purged_at=NEW.previous_purged_at) THEN
  RAISE EXCEPTION 'face_regeneration_receipt_invalid' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_regeneration_receipt_immutable BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_timeline_regeneration_receipt FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_regeneration_receipt();
REVOKE ALL ON ml.face_timeline_regeneration_receipt FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.face_regeneration_work_insert(),
 app_private.face_regeneration_work_verify(),app_private.face_regeneration_receipt(),
 app_private.fence_face_regeneration_receipt() FROM PUBLIC;
""")
    for name in ("fence_face_work_incarnation", "verify_face_work_incarnation"):
        definition = connection.scalar(
            text("SELECT definition FROM app_private.privacy_prior_definition WHERE name=:key"),
            {"key": f"0084:{name}"},
        )
        guard = (
            "IF NEW.regeneration_timeline_id IS NOT NULL THEN RETURN "
            + ("NULL" if name.startswith("verify") else "NEW")
            + "; END IF;"
        )
        op.execute(definition.replace("BEGIN", "BEGIN\n " + guard, 1))
    definition = connection.scalar(
        text("SELECT definition FROM app_private.privacy_prior_definition WHERE name=:key"),
        {"key": "0084:fence_face_timeline_mutation"},
    )
    definition = definition.replace(
        "NEW.state_generation=2", "NEW.state_generation=OLD.state_generation+1"
    )
    regeneration = """
 IF TG_OP='UPDATE' AND OLD.state='PURGED' AND NEW.state='STAGED'
  AND NEW.state_generation=((OLD.state_generation-1)/4+1)*4+1
  AND NEW.published_at IS NULL AND NEW.revoked_at IS NULL
  AND NEW.canonical_timeline IS NOT NULL
  AND to_jsonb(NEW)-ARRAY['state','state_generation','published_at','revoked_at',
   'canonical_timeline','payload_work_id','payload_claim_token'] IS NOT DISTINCT FROM
   to_jsonb(OLD)-ARRAY['state','state_generation','published_at','revoked_at',
   'canonical_timeline','payload_work_id','payload_claim_token']
  AND EXISTS(SELECT 1 FROM ml.face_analysis_work w
   WHERE w.face_work_id=NEW.payload_work_id AND w.state='CLAIMED'
    AND w.claim_token=NEW.payload_claim_token AND w.live_sponsor_count>0
    AND w.claimed_authority_generation=w.work_authority_generation
    AND w.lease_expires_at>clock_timestamp()
    AND w.regeneration_timeline_id=OLD.face_timeline_id
    AND w.regeneration_prior_generation=OLD.state_generation-2
    AND w.semantic_key_sha256=OLD.semantic_key_sha256) THEN RETURN NEW; END IF;
"""
    op.execute(definition.replace("BEGIN", "BEGIN\n" + regeneration, 1))


def downgrade() -> None:
    op.execute("""
LOCK TABLE ml.face_timeline,ml.face_analysis_work IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_analysis_work WHERE regeneration_timeline_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM ml.face_timeline_regeneration_receipt) THEN
 RAISE EXCEPTION 'face_regeneration_evidence_retained' USING ERRCODE='55000'; END IF;
END $$;
DROP TRIGGER z_face_regeneration_receipt ON ml.face_timeline;
DROP TRIGGER z_face_regeneration_sponsor ON ml.face_analysis_work;
DROP TRIGGER x_face_regeneration_origin ON ml.face_analysis_work;
DROP TABLE ml.face_timeline_regeneration_receipt;
DROP FUNCTION app_private.fence_face_regeneration_receipt();
DROP FUNCTION app_private.face_regeneration_receipt();
DROP FUNCTION app_private.face_regeneration_work_verify();
DROP FUNCTION app_private.face_regeneration_work_insert();
""")
    for name in _FUNCTIONS:
        definition = op.get_bind().scalar(
            text("SELECT definition FROM app_private.privacy_prior_definition WHERE name=:key"),
            {"key": f"0084:{name}"},
        )
        op.execute(definition)
    op.execute("""
ALTER TABLE ml.face_timeline DROP CONSTRAINT ck_face_timeline_payload_claim,
 DROP COLUMN payload_work_id,DROP COLUMN payload_claim_token,
 DROP CONSTRAINT face_timeline_state_generation_check,
 ADD CONSTRAINT face_timeline_state_generation_check CHECK(state_generation IN (1,2,3,4)),
 DROP CONSTRAINT ck_face_timeline_state_times,
 ADD CONSTRAINT ck_face_timeline_state_times CHECK(
 (state='STAGED' AND state_generation=1 AND published_at IS NULL AND revoked_at IS NULL
  AND canonical_timeline IS NOT NULL) OR
 (state='PUBLISHED' AND state_generation=2 AND published_at IS NOT NULL AND revoked_at IS NULL
  AND canonical_timeline IS NOT NULL) OR
 (state='PURGED' AND revoked_at IS NOT NULL AND canonical_timeline IS NULL AND
  ((state_generation=3 AND published_at IS NULL) OR (state_generation=4 AND published_at IS NOT
  NULL))));
DROP INDEX ml.uq_face_work_regeneration;
DROP INDEX ml.uq_face_work_current_execution;
CREATE UNIQUE INDEX uq_face_work_current_execution ON ml.face_analysis_work(
 recording_id,audio_variant_id,source_sha256,lineage_sha256)
 WHERE state IN ('PENDING','CLAIMED','RETRY_WAIT','SUCCEEDED');
ALTER TABLE ml.face_analysis_work DROP CONSTRAINT fk_face_work_regeneration_receipt,
 DROP CONSTRAINT ck_face_work_regeneration_pair,DROP CONSTRAINT ck_face_work_incarnation_origin,
 DROP COLUMN regeneration_timeline_id,DROP COLUMN regeneration_prior_generation,
 ADD CONSTRAINT ck_face_work_incarnation_origin CHECK(
 (execution_incarnation=1 AND predecessor_work_id IS NULL AND incarnation_predecessor_sponsor_id
  IS NULL
  AND incarnation_source_generation IS NULL) OR (execution_incarnation>1 AND predecessor_work_id
  IS NOT NULL
  AND incarnation_predecessor_sponsor_id IS NOT NULL AND incarnation_source_generation BETWEEN 2
  AND 9007199254740990));
ALTER TABLE ml.face_projection_fanout ADD CONSTRAINT uq_face_fanout_timeline_authority
 UNIQUE(face_timeline_id,work_authority_generation);
ALTER TABLE ml.face_timeline_gc_receipt DROP CONSTRAINT face_timeline_gc_receipt_pkey,
 ADD PRIMARY KEY(face_timeline_id),DROP CONSTRAINT face_timeline_gc_receipt_prior_generation_check,
 ADD CONSTRAINT face_timeline_gc_receipt_prior_generation_check CHECK(prior_generation IN (1,2));
DELETE FROM app_private.privacy_prior_definition WHERE name LIKE '0084:%';
""")
