"""Dormant append-only cancellation of one exact replacement candidate.

Revision ID: 0080_face_replacement_cancel
Revises: 0079_face_source_replacement
"""

from alembic import op

revision = "0080_face_replacement_cancel"
down_revision = "0079_face_source_replacement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.face_source_replacement_remediation (
 remediation_id uuid PRIMARY KEY,
 face_source_replacement_id uuid NOT NULL,
 replacement_generation bigint NOT NULL,
 predecessor_sponsor_id uuid NOT NULL,
 candidate_sha256 bytea NOT NULL,
 operator_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 reason text NOT NULL,
 request_sha256 bytea NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT fk_face_remediation_candidate FOREIGN KEY
   (face_source_replacement_id,predecessor_sponsor_id) REFERENCES
   ml.face_source_replacement_candidate(face_source_replacement_id,predecessor_sponsor_id)
   ON DELETE RESTRICT,
 CONSTRAINT uq_face_remediation_candidate UNIQUE(face_source_replacement_id,predecessor_sponsor_id),
 CONSTRAINT ck_face_remediation_generation CHECK(
   replacement_generation BETWEEN 1 AND 9007199254740990),
 CONSTRAINT ck_face_remediation_hashes CHECK(
   octet_length(candidate_sha256)=32 AND octet_length(request_sha256)=32),
 CONSTRAINT ck_face_remediation_reason CHECK(reason ~ '^[A-Z][A-Z0-9_]{0,63}$'
   AND reason NOT IN ('PREDECESSOR_RETIRED','SUCCESSOR_INELIGIBLE'))
);
CREATE FUNCTION app_private.fence_face_replacement_remediation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE root ml.face_source_replacement%ROWTYPE;
        item ml.face_source_replacement_candidate%ROWTYPE;
        prior ml.face_analysis_sponsor%ROWTYPE;
        actor account.user_account%ROWTYPE;
        current_variant uuid;
BEGIN
 IF TG_TABLE_NAME='face_source_replacement_candidate' THEN
  IF NEW.state='CANCELLED' AND NEW.reason NOT IN ('PREDECESSOR_RETIRED','SUCCESSOR_INELIGIBLE')
   AND NOT EXISTS(SELECT 1 FROM ml.face_source_replacement_remediation r
     WHERE r.face_source_replacement_id=NEW.face_source_replacement_id
       AND r.predecessor_sponsor_id=NEW.predecessor_sponsor_id
       AND r.candidate_sha256=NEW.candidate_sha256 AND r.reason=NEW.reason) THEN
   RAISE EXCEPTION 'face_remediation_receipt_required' USING ERRCODE='55000';
  END IF;
  IF EXISTS(SELECT 1 FROM ml.face_source_replacement_remediation r
     WHERE r.face_source_replacement_id=NEW.face_source_replacement_id
       AND r.predecessor_sponsor_id=NEW.predecessor_sponsor_id
       AND (NEW.state<>'CANCELLED' OR NEW.reason IS DISTINCT FROM r.reason
         OR NEW.candidate_sha256 IS DISTINCT FROM r.candidate_sha256)) THEN
   RAISE EXCEPTION 'face_remediation_candidate_conflict' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'face_remediation_immutable' USING ERRCODE='55000';
 END IF;
 SELECT * INTO root FROM ml.face_source_replacement
  WHERE face_source_replacement_id=NEW.face_source_replacement_id FOR UPDATE;
 IF root.state IS DISTINCT FROM 'PENDING'
   OR root.replacement_generation IS DISTINCT FROM NEW.replacement_generation THEN
  RAISE EXCEPTION 'face_remediation_root_conflict' USING ERRCODE='55000';
 END IF;
 SELECT * INTO actor FROM account.user_account WHERE user_id=NEW.operator_user_id FOR SHARE;
 IF actor.role IS NULL OR actor.role NOT IN ('OWNER','ADMIN') OR actor.status<>'ACTIVE'
  OR actor.deleted_at IS NOT NULL THEN
  RAISE EXCEPTION 'face_remediation_operator_denied' USING ERRCODE='55000';
 END IF;
 SELECT * INTO item FROM ml.face_source_replacement_candidate
  WHERE face_source_replacement_id=NEW.face_source_replacement_id
    AND predecessor_sponsor_id=NEW.predecessor_sponsor_id;
 SELECT * INTO prior FROM ml.face_analysis_sponsor
  WHERE face_sponsor_id=NEW.predecessor_sponsor_id;
 IF item.state IS DISTINCT FROM 'PENDING'
   OR item.candidate_sha256 IS DISTINCT FROM NEW.candidate_sha256
   OR prior.state IS NULL OR prior.state NOT IN ('WITHDRAWN','SUPERSEDED')
   OR prior.recording_id IS DISTINCT FROM root.recording_id
   OR prior.audio_variant_id IS DISTINCT FROM root.expected_audio_variant_id
   OR prior.source_sha256 IS DISTINCT FROM root.expected_source_sha256 THEN
  RAISE EXCEPTION 'face_remediation_candidate_conflict' USING ERRCODE='55000';
 END IF;
 PERFORM user_track_ref_id FROM library.user_track_ref
  WHERE user_track_ref_id=prior.user_track_ref_id FOR UPDATE;
 PERFORM recording_id FROM catalog.recording WHERE recording_id=root.recording_id FOR UPDATE;
 SELECT audio_variant_id INTO current_variant FROM vault.recording_canonical_variant
  WHERE recording_id=root.recording_id FOR UPDATE;
 IF current_variant IS DISTINCT FROM root.audio_variant_id OR root.replacement_generation <>
  (SELECT max(replacement_generation) FROM ml.face_source_replacement
   WHERE recording_id=root.recording_id) THEN
  RAISE EXCEPTION 'face_remediation_generation_conflict' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION app_private.verify_face_remediation_pair() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ml.face_source_replacement_candidate c
   JOIN ml.face_source_replacement r ON r.face_source_replacement_id=c.face_source_replacement_id
   JOIN ml.face_analysis_sponsor s ON s.face_sponsor_id=c.predecessor_sponsor_id
   WHERE c.face_source_replacement_id=NEW.face_source_replacement_id
     AND c.predecessor_sponsor_id=NEW.predecessor_sponsor_id
     AND c.state='CANCELLED' AND c.successor_sponsor_id IS NULL
     AND c.reason=NEW.reason AND c.candidate_sha256=NEW.candidate_sha256
     AND r.replacement_generation=NEW.replacement_generation
     AND s.state IN ('WITHDRAWN','SUPERSEDED')) THEN
  RAISE EXCEPTION 'face_remediation_terminal_pair_required' USING ERRCODE='55000';
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER z_face_replacement_remediation BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_source_replacement_remediation FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_replacement_remediation();
CREATE TRIGGER y_face_replacement_candidate_remediation BEFORE UPDATE
 ON ml.face_source_replacement_candidate FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_replacement_remediation();
CREATE CONSTRAINT TRIGGER z_face_remediation_terminal_pair AFTER INSERT
 ON ml.face_source_replacement_remediation DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app_private.verify_face_remediation_pair();
REVOKE ALL ON ml.face_source_replacement_remediation FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_replacement_remediation(),
 app_private.verify_face_remediation_pair() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
LOCK TABLE ml.face_source_replacement_remediation IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_source_replacement_remediation) THEN
  RAISE EXCEPTION 'face_remediation_downgrade_requires_empty_authority' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER y_face_replacement_candidate_remediation ON ml.face_source_replacement_candidate;
DROP TABLE ml.face_source_replacement_remediation;
DROP FUNCTION app_private.verify_face_remediation_pair();
DROP FUNCTION app_private.fence_face_replacement_remediation();
""")
