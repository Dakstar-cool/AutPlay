"""Dormant new executions for a proven exact-source return.

Revision ID: 0081_face_work_incarnation
Revises: 0080_face_replacement_cancel
"""

from alembic import op

revision = "0081_face_work_incarnation"
down_revision = "0080_face_replacement_cancel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE ml.face_analysis_work
 ADD COLUMN execution_incarnation bigint NOT NULL DEFAULT 1,
 ADD COLUMN predecessor_work_id uuid REFERENCES ml.face_analysis_work(face_work_id)
   ON DELETE RESTRICT,
 ADD COLUMN incarnation_predecessor_sponsor_id uuid REFERENCES
   ml.face_analysis_sponsor(face_sponsor_id) ON DELETE RESTRICT,
 ADD COLUMN incarnation_source_generation bigint,
 ADD CONSTRAINT ck_face_work_incarnation CHECK(
   execution_incarnation BETWEEN 1 AND 9007199254740990),
 ADD CONSTRAINT ck_face_work_incarnation_origin CHECK(
   (execution_incarnation=1 AND predecessor_work_id IS NULL
     AND incarnation_predecessor_sponsor_id IS NULL AND incarnation_source_generation IS NULL)
   OR (execution_incarnation>1 AND predecessor_work_id IS NOT NULL
     AND incarnation_predecessor_sponsor_id IS NOT NULL
     AND incarnation_source_generation BETWEEN 2 AND 9007199254740990)),
 DROP CONSTRAINT uq_face_work_exact_key,
 ADD CONSTRAINT uq_face_work_exact_key UNIQUE(
   recording_id,audio_variant_id,source_sha256,lineage_sha256,execution_incarnation);
CREATE UNIQUE INDEX uq_face_work_current_execution ON ml.face_analysis_work(
 recording_id,audio_variant_id,source_sha256,lineage_sha256)
 WHERE state IN ('PENDING','CLAIMED','RETRY_WAIT','SUCCEEDED');
CREATE FUNCTION app_private.fence_face_work_incarnation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE previous ml.face_analysis_work%ROWTYPE;
        origin ml.face_analysis_sponsor%ROWTYPE;
BEGIN
 IF NEW.execution_incarnation=1 THEN
  RETURN NEW;
 END IF;
 SELECT * INTO origin FROM ml.face_analysis_sponsor
  WHERE face_sponsor_id=NEW.incarnation_predecessor_sponsor_id;
 IF origin.state IS DISTINCT FROM 'LIVE' THEN
  RAISE EXCEPTION 'face_work_incarnation_origin_invalid' USING ERRCODE='55000';
 END IF;
 -- Match the supported source/sponsor writer order before taking work locks.
 PERFORM user_track_ref_id FROM library.user_track_ref
  WHERE user_track_ref_id=origin.user_track_ref_id FOR UPDATE;
 PERFORM recording_id FROM catalog.recording WHERE recording_id=NEW.recording_id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'autplay.face.work.v1:' || encode(NEW.source_sha256,'hex') || ':' ||
  encode(NEW.lineage_sha256,'hex'),0));
 SELECT * INTO previous FROM ml.face_analysis_work
  WHERE recording_id=NEW.recording_id AND audio_variant_id=NEW.audio_variant_id
    AND source_sha256=NEW.source_sha256 AND lineage_sha256=NEW.lineage_sha256
  ORDER BY execution_incarnation DESC LIMIT 1 FOR UPDATE;
 SELECT * INTO origin FROM ml.face_analysis_sponsor
  WHERE face_sponsor_id=NEW.incarnation_predecessor_sponsor_id FOR SHARE;
 IF previous.face_work_id IS DISTINCT FROM NEW.predecessor_work_id
  OR previous.state IS NULL OR previous.state NOT IN (
    'TERMINAL_INELIGIBLE','TERMINAL_FAILED','RETRY_EXHAUSTED','CANCELLED','SUPERSEDED')
  OR previous.execution_incarnation+1 IS DISTINCT FROM NEW.execution_incarnation
  OR previous.identity_document IS DISTINCT FROM NEW.identity_document
  OR previous.semantic_key_sha256 IS DISTINCT FROM NEW.semantic_key_sha256
  OR previous.artifact_policy_list_sha256 IS DISTINCT FROM NEW.artifact_policy_list_sha256
  OR origin.state IS DISTINCT FROM 'LIVE'
  OR NEW.incarnation_source_generation IS DISTINCT FROM origin.source_generation+1
  OR (origin.audio_variant_id=NEW.audio_variant_id AND origin.source_sha256=NEW.source_sha256)
  OR NOT EXISTS(SELECT 1 FROM ml.face_analysis_sponsor historical
    WHERE historical.face_work_id=previous.face_work_id
      AND historical.owner_user_id=origin.owner_user_id
      AND historical.user_track_ref_id=origin.user_track_ref_id
      AND historical.source_generation<origin.source_generation) THEN
  RAISE EXCEPTION 'face_work_incarnation_origin_invalid' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION app_private.verify_face_work_incarnation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.execution_incarnation>1 AND NOT EXISTS(
  SELECT 1 FROM ml.face_analysis_sponsor successor
  JOIN ml.face_analysis_sponsor origin ON origin.face_sponsor_id=successor.predecessor_id
  WHERE successor.face_work_id=NEW.face_work_id
    AND successor.predecessor_id=NEW.incarnation_predecessor_sponsor_id
    AND successor.source_generation=NEW.incarnation_source_generation
    AND origin.state='SUPERSEDED' AND origin.successor_id=successor.face_sponsor_id
    AND successor.owner_user_id=origin.owner_user_id
    AND successor.user_track_ref_id=origin.user_track_ref_id) THEN
  RAISE EXCEPTION 'face_work_incarnation_sponsor_required' USING ERRCODE='55000';
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER y_face_work_incarnation BEFORE INSERT ON ml.face_analysis_work
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_work_incarnation();
CREATE CONSTRAINT TRIGGER z_face_work_incarnation_link AFTER INSERT ON ml.face_analysis_work
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.verify_face_work_incarnation();
REVOKE ALL ON FUNCTION app_private.fence_face_work_incarnation(),
 app_private.verify_face_work_incarnation() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
LOCK TABLE ml.face_analysis_work IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_analysis_work WHERE execution_incarnation>1) THEN
  RAISE EXCEPTION 'face_work_incarnation_downgrade_requires_empty_evidence' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER z_face_work_incarnation_link ON ml.face_analysis_work;
DROP TRIGGER y_face_work_incarnation ON ml.face_analysis_work;
DROP FUNCTION app_private.verify_face_work_incarnation();
DROP FUNCTION app_private.fence_face_work_incarnation();
DROP INDEX ml.uq_face_work_current_execution;
ALTER TABLE ml.face_analysis_work DROP CONSTRAINT uq_face_work_exact_key,
 ADD CONSTRAINT uq_face_work_exact_key UNIQUE(
   recording_id,audio_variant_id,source_sha256,lineage_sha256),
 DROP COLUMN incarnation_source_generation,
 DROP COLUMN incarnation_predecessor_sponsor_id,
 DROP COLUMN predecessor_work_id,
 DROP COLUMN execution_incarnation;
""")
