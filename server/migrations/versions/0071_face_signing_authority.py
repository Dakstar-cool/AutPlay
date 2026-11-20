"""Reserve immutable activation artifact provenance; leave profile binding dormant.

Revision ID: 0071_face_signing_authority
Revises: 0070_sync_erasure_floor
"""

from alembic import op

revision = "0071_face_signing_authority"
down_revision = "0070_sync_erasure_floor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql("""
CREATE TABLE ml.face_activation_artifact_provenance (
 face_timeline_activation_id uuid PRIMARY KEY
  REFERENCES ml.face_timeline_activation(face_timeline_activation_id) ON DELETE RESTRICT,
 required_artifact_set_sha256 bytea NOT NULL
  CONSTRAINT ck_face_provenance_required_hash
  CHECK(octet_length(required_artifact_set_sha256)=32),
 required_role_cardinality jsonb NOT NULL
  CONSTRAINT ck_face_provenance_cardinality_type
  CHECK(jsonb_typeof(required_role_cardinality)='object'),
 created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ml.face_activation_required_artifact (
 face_timeline_activation_id uuid NOT NULL
  REFERENCES ml.face_activation_artifact_provenance(face_timeline_activation_id)
  ON DELETE RESTRICT,
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 256),
 role text NOT NULL CHECK(role IN (
  'ENCODER_WEIGHTS','INTERPRETER_EXPORT','CALIBRATION',
  'PREPROCESSING_EXECUTABLE','DECODER_PROBE','TIMELINE_CODEC')),
 artifact_sha256 bytea NOT NULL REFERENCES ml.artifact(artifact_sha256) ON DELETE RESTRICT,
 face_artifact_release_id uuid
  REFERENCES ml.face_artifact_release(face_artifact_release_id) ON DELETE RESTRICT,
 decision_sequence bigint NOT NULL CHECK(decision_sequence BETWEEN 1 AND 9007199254740991),
 decision_generation bigint NOT NULL CHECK(decision_generation BETWEEN 1 AND 9007199254740991),
 max_offline_revocation_lag_ms bigint NOT NULL
  CONSTRAINT ck_face_required_lag
  CHECK(max_offline_revocation_lag_ms BETWEEN 1 AND 9007199254740991),
 disposition text NOT NULL
  CHECK(disposition IN ('DELETE_AFTER_LEASE','RETAIN_NON_DISTRIBUTABLE')),
 CONSTRAINT fk_face_activation_required_decision
  FOREIGN KEY(artifact_sha256,decision_sequence)
  REFERENCES ml.artifact_license_decision(artifact_sha256,decision_sequence)
  ON DELETE RESTRICT,
 PRIMARY KEY(face_timeline_activation_id,ordinal),
 CONSTRAINT uq_face_activation_required_identity
  UNIQUE(face_timeline_activation_id,role,artifact_sha256),
 CONSTRAINT ck_face_activation_required_release_shape CHECK(
  (role='ENCODER_WEIGHTS' AND face_artifact_release_id IS NULL)
  OR (role<>'ENCODER_WEIGHTS' AND face_artifact_release_id IS NOT NULL))
);

CREATE FUNCTION app_private.verify_face_activation_artifact_provenance() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
 a ml.face_timeline_activation%%ROWTYPE;
 p ml.face_activation_artifact_provenance%%ROWTYPE;
 entry record;
 release_row ml.face_artifact_release%%ROWTYPE;
 decision_row ml.artifact_license_decision%%ROWTYPE;
 current_row ml.artifact_license_current%%ROWTYPE;
 encoder_hash bytea;
 interpreter_release_id uuid;
 calibration_release_id uuid;
 interpreter_seen boolean := false;
 calibration_seen boolean := false;
 activation_xmin xid;
 provenance_xmin xid;
 required_json text := '{"entries":[';
 policy_json text := '{"entries":[';
 previous_role text;
 previous_hash bytea;
 seen_count integer := 0;
 counts jsonb;
BEGIN
 IF TG_TABLE_NAME='face_timeline_activation' THEN
  SELECT * INTO a FROM ml.face_timeline_activation
   WHERE face_timeline_activation_id=NEW.face_timeline_activation_id;
 ELSE
  SELECT * INTO a FROM ml.face_timeline_activation
   WHERE face_timeline_activation_id=NEW.face_timeline_activation_id FOR KEY SHARE;
 END IF;
 SELECT * INTO p FROM ml.face_activation_artifact_provenance
  WHERE face_timeline_activation_id=a.face_timeline_activation_id;
 IF a.action='DEACTIVATE' THEN
  IF p.face_timeline_activation_id IS NOT NULL THEN
   RAISE EXCEPTION 'face_activation_provenance_inactive' USING ERRCODE='23514';
  END IF;
  RETURN NULL;
 END IF;
 SELECT xmin INTO activation_xmin FROM ml.face_timeline_activation
  WHERE face_timeline_activation_id=a.face_timeline_activation_id;
 SELECT xmin INTO provenance_xmin FROM ml.face_activation_artifact_provenance
  WHERE face_timeline_activation_id=a.face_timeline_activation_id;
 IF p.face_timeline_activation_id IS NULL OR
    activation_xmin IS DISTINCT FROM provenance_xmin THEN
  RAISE EXCEPTION 'face_activation_provenance_missing_or_legacy' USING ERRCODE='23514';
 END IF;
 SELECT artifact_sha256 INTO encoder_hash FROM ml.embedding_model
  WHERE embedding_model_id=a.embedding_model_id FOR KEY SHARE;
 IF encoder_hash IS NULL THEN
  RAISE EXCEPTION 'face_activation_encoder_artifact_unknown' USING ERRCODE='23514';
 END IF;
 SELECT interpreter_artifact_release_id,calibration_artifact_release_id
  INTO interpreter_release_id,calibration_release_id
  FROM ml.face_semantic_interpreter
  WHERE face_semantic_interpreter_id=a.face_semantic_interpreter_id FOR KEY SHARE;
 FOR entry IN
  SELECT * FROM ml.face_activation_required_artifact
   WHERE face_timeline_activation_id=a.face_timeline_activation_id ORDER BY ordinal
 LOOP
  seen_count := seen_count+1;
  IF entry.ordinal<>seen_count OR
   (previous_role IS NOT NULL AND
    (entry.role COLLATE "C",entry.artifact_sha256)
     <= (previous_role COLLATE "C",previous_hash)) THEN
   RAISE EXCEPTION 'face_activation_artifact_order_invalid' USING ERRCODE='23514';
  END IF;
  IF entry.role='ENCODER_WEIGHTS' THEN
   IF entry.artifact_sha256<>encoder_hash THEN
    RAISE EXCEPTION 'face_activation_encoder_artifact_mismatch' USING ERRCODE='23514';
   END IF;
  ELSE
   SELECT * INTO release_row FROM ml.face_artifact_release
    WHERE face_artifact_release_id=entry.face_artifact_release_id FOR KEY SHARE;
   IF release_row.role IS DISTINCT FROM entry.role OR
      release_row.artifact_sha256 IS DISTINCT FROM entry.artifact_sha256 THEN
    RAISE EXCEPTION 'face_activation_release_mismatch' USING ERRCODE='23514';
   END IF;
  END IF;
  IF entry.role='INTERPRETER_EXPORT' AND
     entry.face_artifact_release_id=interpreter_release_id THEN
   interpreter_seen:=true;
  END IF;
  IF entry.role='CALIBRATION' AND
     entry.face_artifact_release_id=calibration_release_id THEN
   calibration_seen:=true;
  END IF;
  SELECT * INTO decision_row FROM ml.artifact_license_decision
   WHERE artifact_sha256=entry.artifact_sha256
    AND decision_sequence=entry.decision_sequence FOR KEY SHARE;
  SELECT * INTO current_row FROM ml.artifact_license_current
   WHERE artifact_sha256=entry.artifact_sha256 FOR SHARE;
  IF decision_row.state IS DISTINCT FROM 'APPROVED' OR
     decision_row.effective_generation IS DISTINCT FROM entry.decision_generation OR
     decision_row.max_offline_revocation_lag_ms IS DISTINCT FROM
      entry.max_offline_revocation_lag_ms OR
     decision_row.derived_output_disposition IS DISTINCT FROM entry.disposition OR
     current_row.state IS DISTINCT FROM 'APPROVED' OR
     current_row.decision_sequence IS DISTINCT FROM entry.decision_sequence OR
     current_row.effective_generation IS DISTINCT FROM entry.decision_generation THEN
   RAISE EXCEPTION 'face_activation_license_decision_mismatch' USING ERRCODE='23514';
  END IF;
  IF seen_count>1 THEN
   required_json:=required_json||',';
   policy_json:=policy_json||',';
  END IF;
  required_json:=required_json||'{"artifact_sha256":"'||
   encode(entry.artifact_sha256,'hex')||'","role":"'||entry.role||'"}';
  policy_json:=policy_json||'{"artifact_sha256":"'||
   encode(entry.artifact_sha256,'hex')||'","decision_generation":'||
   entry.decision_generation::text||',"decision_sequence":'||
   entry.decision_sequence::text||',"disposition":"'||entry.disposition||
   '","max_offline_revocation_lag_ms":'||
   entry.max_offline_revocation_lag_ms::text||',"role":"'||entry.role||'"}';
  previous_role:=entry.role;
  previous_hash:=entry.artifact_sha256;
 END LOOP;
 IF seen_count<2 OR seen_count>256 OR NOT interpreter_seen OR
    (calibration_release_id IS NOT NULL AND NOT calibration_seen) THEN
  RAISE EXCEPTION 'face_activation_artifact_count_invalid' USING ERRCODE='23514';
 END IF;
 required_json:=required_json||'],"v":1}';
 policy_json:=policy_json||'],"v":1}';
 SELECT jsonb_build_object(
  'ENCODER_WEIGHTS',count(*) FILTER (WHERE role='ENCODER_WEIGHTS'),
  'INTERPRETER_EXPORT',count(*) FILTER (WHERE role='INTERPRETER_EXPORT'),
  'CALIBRATION',count(*) FILTER (WHERE role='CALIBRATION'),
  'PREPROCESSING_EXECUTABLE',count(*) FILTER (WHERE role='PREPROCESSING_EXECUTABLE'),
  'DECODER_PROBE',count(*) FILTER (WHERE role='DECODER_PROBE'),
  'TIMELINE_CODEC',count(*) FILTER (WHERE role='TIMELINE_CODEC')) INTO counts
 FROM ml.face_activation_required_artifact
 WHERE face_timeline_activation_id=a.face_timeline_activation_id;
 IF (counts->>'ENCODER_WEIGHTS')::integer<1 OR
    (counts->>'INTERPRETER_EXPORT')::integer<1 OR
    p.required_role_cardinality<>counts OR
    p.required_artifact_set_sha256<>sha256(
     convert_to('autplay.face.required-artifact-set.v1','UTF8') ||
     decode('00','hex') || convert_to(required_json,'UTF8')) OR
    a.artifact_policy_list_sha256<>sha256(
     convert_to('autplay.face.artifact-policy-list.v1','UTF8') ||
     decode('00','hex') || convert_to(policy_json,'UTF8')) THEN
  RAISE EXCEPTION 'face_activation_artifact_provenance_mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER z_face_activation_provenance_insert
 AFTER INSERT ON ml.face_timeline_activation DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app_private.verify_face_activation_artifact_provenance();
CREATE CONSTRAINT TRIGGER z_face_activation_provenance_complete
 AFTER INSERT ON ml.face_activation_artifact_provenance DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app_private.verify_face_activation_artifact_provenance();
CREATE CONSTRAINT TRIGGER z_face_activation_required_complete
 AFTER INSERT ON ml.face_activation_required_artifact DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app_private.verify_face_activation_artifact_provenance();
CREATE TRIGGER z_face_activation_provenance_immutable BEFORE UPDATE OR DELETE
 ON ml.face_activation_artifact_provenance FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_face_activation_immutable();
CREATE TRIGGER z_face_activation_required_immutable BEFORE UPDATE OR DELETE
 ON ml.face_activation_required_artifact FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_face_activation_immutable();

-- No profile can be supplied by a caller until a transaction-bound runtime
-- identity proof and guarded binding writer have been implemented.
ALTER TABLE ml.face_projection ADD COLUMN server_profile_id uuid
 REFERENCES account.server_instance(server_instance_id) ON DELETE RESTRICT;
ALTER TABLE ml.face_projection ADD CONSTRAINT ck_face_projection_profile_unproven
 CHECK(server_profile_id IS NULL);
REVOKE ALL ON ml.face_activation_artifact_provenance,
 ml.face_activation_required_artifact FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.verify_face_activation_artifact_provenance()
 FROM PUBLIC;
""")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_activation_artifact_provenance)
  OR EXISTS(SELECT 1 FROM ml.face_activation_required_artifact)
  OR EXISTS(SELECT 1 FROM ml.face_projection)
  OR EXISTS(SELECT 1 FROM ml.face_timeline_activation) THEN
  RAISE EXCEPTION 'face_signing_authority_downgrade_has_data' USING ERRCODE='55000';
 END IF;
END $$;
ALTER TABLE ml.face_projection DROP CONSTRAINT ck_face_projection_profile_unproven;
ALTER TABLE ml.face_projection DROP COLUMN server_profile_id;
DROP TRIGGER z_face_activation_provenance_insert ON ml.face_timeline_activation;
DROP TABLE ml.face_activation_required_artifact;
DROP TABLE ml.face_activation_artifact_provenance;
DROP FUNCTION app_private.verify_face_activation_artifact_provenance();
""")
