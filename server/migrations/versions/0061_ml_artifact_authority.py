"""Add non-activating generic ML artifact, license, and step-up authority."""

from alembic import op

revision = "0061_ml_artifact_authority"
down_revision = "0060_local_bridge_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.artifact (
 artifact_sha256 bytea PRIMARY KEY CHECK(octet_length(artifact_sha256)=32),
 artifact_byte_size bigint NOT NULL CHECK(artifact_byte_size>0),
 artifact_format text NOT NULL CHECK(length(artifact_format) BETWEEN 1 AND 100),
 source text NOT NULL CHECK(length(source) BETWEEN 1 AND 500),
 source_revision text NOT NULL CHECK(length(source_revision) BETWEEN 1 AND 300),
 manifest_sha256 bytea NOT NULL CHECK(octet_length(manifest_sha256)=32),
 artifact_manifest jsonb NOT NULL CHECK(octet_length(artifact_manifest::text)<=65536),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION app_private.protect_ml_artifact_immutable() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'ml_artifact_immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER z_ml_artifact_immutable BEFORE UPDATE OR DELETE ON ml.artifact
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_ml_artifact_immutable();
REVOKE ALL ON ml.artifact FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_ml_artifact_immutable() FROM PUBLIC;

CREATE TABLE ml.artifact_migration_issue (
 embedding_model_id uuid PRIMARY KEY REFERENCES ml.embedding_model(embedding_model_id)
  ON DELETE RESTRICT,
 artifact_sha256 bytea NOT NULL CHECK(octet_length(artifact_sha256)=32),
 reason text NOT NULL CHECK(reason IN ('CONTENT_METADATA_CONFLICT','MANIFEST_OVERSIZE',
  'LICENSE_CLAIM_CONFLICT')),
 detected_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION app_private.lock_ml_artifact_for_issue() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 -- Some migration issues have no artifact row. When one exists, serialize
 -- a new issue with review and activation reads that lock that exact row.
 PERFORM 1 FROM ml.artifact WHERE artifact_sha256=NEW.artifact_sha256 FOR KEY SHARE;
 RETURN NEW;
END $$;
CREATE TRIGGER a_artifact_migration_issue_lock BEFORE INSERT
 ON ml.artifact_migration_issue FOR EACH ROW
 EXECUTE FUNCTION app_private.lock_ml_artifact_for_issue();
CREATE TRIGGER z_artifact_migration_issue_immutable BEFORE UPDATE OR DELETE
 ON ml.artifact_migration_issue FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_ml_artifact_immutable();
REVOKE ALL ON ml.artifact_migration_issue FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.lock_ml_artifact_for_issue() FROM PUBLIC;

ALTER TABLE ml.embedding_model ADD COLUMN artifact_sha256 bytea;
ALTER TABLE ml.embedding_model ADD CONSTRAINT embedding_artifact_sha256_check
 CHECK(artifact_sha256 IS NULL OR octet_length(artifact_sha256)=32);
ALTER TABLE ml.embedding_model ADD CONSTRAINT embedding_artifact_fkey
 FOREIGN KEY(artifact_sha256) REFERENCES ml.artifact(artifact_sha256) ON DELETE RESTRICT;

INSERT INTO ml.artifact(artifact_sha256,artifact_byte_size,artifact_format,source,
 source_revision,manifest_sha256,artifact_manifest,created_at)
SELECT DISTINCT ON (weights_sha256) weights_sha256,artifact_byte_size,artifact_format,
 source,source_revision,manifest_sha256,artifact_manifest,created_at
FROM ml.embedding_model
WHERE octet_length(artifact_manifest::text)<=65536
ORDER BY weights_sha256,created_at,embedding_model_id;

UPDATE ml.embedding_model e SET artifact_sha256=e.weights_sha256
FROM ml.artifact a
WHERE a.artifact_sha256=e.weights_sha256
 AND ROW(a.artifact_byte_size,a.artifact_format,a.source,a.source_revision,
         a.manifest_sha256,a.artifact_manifest)
 IS NOT DISTINCT FROM
     ROW(e.artifact_byte_size,e.artifact_format,e.source,e.source_revision,
         e.manifest_sha256,e.artifact_manifest);

INSERT INTO ml.artifact_migration_issue(embedding_model_id,artifact_sha256,reason)
SELECT e.embedding_model_id,e.weights_sha256,
 CASE WHEN octet_length(e.artifact_manifest::text)>65536
  THEN 'MANIFEST_OVERSIZE' ELSE 'CONTENT_METADATA_CONFLICT' END
FROM ml.embedding_model e WHERE e.artifact_sha256 IS NULL;

CREATE FUNCTION app_private.protect_embedding_artifact_binding() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND NEW.artifact_sha256 IS DISTINCT FROM OLD.artifact_sha256 THEN
  RAISE EXCEPTION 'embedding_artifact_binding_immutable' USING ERRCODE='55000';
 END IF;
 IF NEW.artifact_sha256 IS NOT NULL AND NEW.artifact_sha256 IS DISTINCT FROM NEW.weights_sha256 THEN
  RAISE EXCEPTION 'embedding_artifact_hash_mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_embedding_artifact_binding BEFORE INSERT OR UPDATE ON ml.embedding_model
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_embedding_artifact_binding();
REVOKE ALL ON FUNCTION app_private.protect_embedding_artifact_binding() FROM PUBLIC;

CREATE TABLE ml.artifact_license_decision (
 artifact_sha256 bytea NOT NULL REFERENCES ml.artifact(artifact_sha256) ON DELETE RESTRICT,
 decision_sequence bigint NOT NULL CHECK(decision_sequence>=1),
 effective_generation bigint NOT NULL CHECK(effective_generation>=1),
 superseded_sequence bigint CHECK(superseded_sequence IS NULL OR superseded_sequence>=1),
 state text NOT NULL CHECK(state IN ('LEGACY_UNREVIEWED','APPROVED','DENIED','REVOKED')),
 license_identifier text CHECK(license_identifier IS NULL OR
  length(license_identifier) BETWEEN 1 AND 200),
 license_text_sha256 bytea CHECK(license_text_sha256 IS NULL OR
  octet_length(license_text_sha256)=32),
 use_restrictions jsonb NOT NULL DEFAULT '{}'::jsonb
  CHECK(octet_length(use_restrictions::text)<=8192),
 redistribution_decision text CHECK(redistribution_decision IS NULL OR
  redistribution_decision IN ('PERMITTED','DENIED','SEPARATE_INSTALL_ONLY')),
 modification_decision text CHECK(modification_decision IS NULL OR
  modification_decision IN ('PERMITTED','DENIED','UNREVIEWED')),
 attribution_payload jsonb NOT NULL DEFAULT '{}'::jsonb
  CHECK(octet_length(attribution_payload::text)<=8192),
 reviewer_user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 reviewed_at timestamptz,
 review_reference text CHECK(review_reference IS NULL OR
  length(review_reference) BETWEEN 1 AND 500),
 max_offline_revocation_lag_ms bigint NOT NULL DEFAULT 0
  CHECK(max_offline_revocation_lag_ms BETWEEN 0 AND 9007199254740991),
 derived_output_disposition text NOT NULL DEFAULT 'DELETE_AFTER_LEASE'
  CHECK(derived_output_disposition IN ('DELETE_AFTER_LEASE','RETAIN_NON_DISTRIBUTABLE')),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(artifact_sha256,decision_sequence),
 CONSTRAINT artifact_license_review_shape CHECK(
  (state='LEGACY_UNREVIEWED' AND decision_sequence=1 AND effective_generation=1) OR
  (license_identifier IS NOT NULL AND license_text_sha256 IS NOT NULL
   AND reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL
   AND review_reference IS NOT NULL AND redistribution_decision IS NOT NULL
   AND modification_decision IS NOT NULL)),
 CONSTRAINT artifact_license_approval_lag CHECK(
  state<>'APPROVED' OR max_offline_revocation_lag_ms>0)
);
CREATE TABLE ml.artifact_license_current (
 artifact_sha256 bytea PRIMARY KEY REFERENCES ml.artifact(artifact_sha256) ON DELETE RESTRICT,
 decision_sequence bigint NOT NULL CHECK(decision_sequence>=1),
 state text NOT NULL CHECK(state IN ('LEGACY_UNREVIEWED','APPROVED','DENIED','REVOKED')),
 effective_generation bigint NOT NULL CHECK(effective_generation>=1),
 updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(artifact_sha256,decision_sequence)
  REFERENCES ml.artifact_license_decision(artifact_sha256,decision_sequence)
  ON DELETE RESTRICT
);

CREATE FUNCTION app_private.enforce_artifact_license_sequence() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE old_sequence bigint; old_generation bigint;
BEGIN
 SELECT decision_sequence,effective_generation INTO old_sequence,old_generation
 FROM ml.artifact_license_current WHERE artifact_sha256=NEW.artifact_sha256 FOR UPDATE;
 IF NOT FOUND THEN
  IF NEW.decision_sequence<>1 OR NEW.effective_generation<>1
     OR NEW.superseded_sequence IS NOT NULL THEN
   RAISE EXCEPTION 'artifact_license_sequence_invalid' USING ERRCODE='23514';
  END IF;
 ELSIF NEW.decision_sequence<>old_sequence+1
       OR NEW.effective_generation<>old_generation+1
       OR NEW.superseded_sequence IS DISTINCT FROM old_sequence THEN
  RAISE EXCEPTION 'artifact_license_sequence_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION app_private.advance_artifact_license_current() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO ml.artifact_license_current(
  artifact_sha256,decision_sequence,state,effective_generation,updated_at)
 VALUES(NEW.artifact_sha256,NEW.decision_sequence,NEW.state,
  NEW.effective_generation,NEW.created_at)
 ON CONFLICT(artifact_sha256) DO UPDATE SET
  decision_sequence=EXCLUDED.decision_sequence,
  state=EXCLUDED.state,effective_generation=EXCLUDED.effective_generation,
  updated_at=EXCLUDED.updated_at
 WHERE ml.artifact_license_current.decision_sequence=EXCLUDED.decision_sequence-1
   AND ml.artifact_license_current.effective_generation=EXCLUDED.effective_generation-1;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'artifact_license_current_conflict' USING ERRCODE='40001';
 END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION app_private.protect_artifact_license_current() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF pg_trigger_depth()<2 THEN
  RAISE EXCEPTION 'artifact_license_current_is_derived' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_artifact_license_sequence BEFORE INSERT ON ml.artifact_license_decision
 FOR EACH ROW EXECUTE FUNCTION app_private.enforce_artifact_license_sequence();
CREATE TRIGGER z_artifact_license_current AFTER INSERT ON ml.artifact_license_decision
 FOR EACH ROW EXECUTE FUNCTION app_private.advance_artifact_license_current();
CREATE TRIGGER z_artifact_license_immutable BEFORE UPDATE OR DELETE ON ml.artifact_license_decision
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_ml_artifact_immutable();
CREATE TRIGGER z_artifact_license_current_derived BEFORE INSERT OR UPDATE OR DELETE
 ON ml.artifact_license_current FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_artifact_license_current();
REVOKE ALL ON ml.artifact_license_decision,ml.artifact_license_current FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.enforce_artifact_license_sequence() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.advance_artifact_license_current() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_artifact_license_current() FROM PUBLIC;

INSERT INTO ml.artifact_license_decision(
 artifact_sha256,decision_sequence,effective_generation,state,license_identifier)
SELECT a.artifact_sha256,1,1,'LEGACY_UNREVIEWED',
 (SELECT min(e.license_id) FROM ml.embedding_model e
  WHERE e.weights_sha256=a.artifact_sha256)
FROM ml.artifact a;

INSERT INTO ml.artifact_migration_issue(embedding_model_id,artifact_sha256,reason)
SELECT e.embedding_model_id,e.weights_sha256,'LICENSE_CLAIM_CONFLICT'
FROM ml.embedding_model e
WHERE e.artifact_sha256 IS NOT NULL
 AND EXISTS(SELECT 1 FROM ml.embedding_model other
  WHERE other.weights_sha256=e.weights_sha256 AND other.license_id<>e.license_id)
ON CONFLICT(embedding_model_id) DO NOTHING;

CREATE TABLE ml.face_artifact_release (
 face_artifact_release_id uuid PRIMARY KEY DEFAULT uuidv7(),
 role text NOT NULL CHECK(role IN ('INTERPRETER_EXPORT','CALIBRATION',
  'PREPROCESSING_EXECUTABLE','DECODER_PROBE','TIMELINE_CODEC')),
 release_key text NOT NULL CHECK(length(release_key) BETWEEN 1 AND 200),
 version text NOT NULL CHECK(length(version) BETWEEN 1 AND 100),
 manifest_sha256 bytea NOT NULL CHECK(octet_length(manifest_sha256)=32),
 artifact_sha256 bytea NOT NULL REFERENCES ml.artifact(artifact_sha256) ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_face_artifact_release_version
  UNIQUE(role,release_key,version,manifest_sha256)
);
CREATE TABLE ml.sona_artifact_release (
 sona_artifact_release_id uuid PRIMARY KEY DEFAULT uuidv7(),
 role text NOT NULL CHECK(role IN ('SONA_MODEL','SONA_TOKENIZER')),
 release_key text NOT NULL CHECK(length(release_key) BETWEEN 1 AND 200),
 version text NOT NULL CHECK(length(version) BETWEEN 1 AND 100),
 manifest_sha256 bytea NOT NULL CHECK(octet_length(manifest_sha256)=32),
 artifact_sha256 bytea NOT NULL REFERENCES ml.artifact(artifact_sha256) ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_sona_artifact_release_version
  UNIQUE(role,release_key,version,manifest_sha256)
);
CREATE TRIGGER z_face_artifact_release_immutable BEFORE UPDATE OR DELETE
 ON ml.face_artifact_release FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_ml_artifact_immutable();
CREATE TRIGGER z_sona_artifact_release_immutable BEFORE UPDATE OR DELETE
 ON ml.sona_artifact_release FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_ml_artifact_immutable();
REVOKE ALL ON ml.face_artifact_release,ml.sona_artifact_release FROM PUBLIC;
""")

    op.execute("""
ALTER TABLE account.device ADD COLUMN device_key_generation bigint NOT NULL DEFAULT 0;
UPDATE account.device SET device_key_generation=1 WHERE public_key IS NOT NULL;
ALTER TABLE account.device ADD CONSTRAINT device_key_generation_shape CHECK(
 device_key_generation>=0
 AND (device_key_generation<>0 OR public_key IS NULL)
 AND (public_key IS NULL OR device_key_generation>=1));
CREATE FUNCTION app_private.enforce_device_key_generation() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE key_changed boolean; newly_revoked boolean;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.public_key IS NOT NULL AND NEW.device_key_generation=0 THEN
   NEW.device_key_generation:=1;
  END IF;
  IF (NEW.public_key IS NOT NULL AND NEW.device_key_generation<>1)
    OR (NEW.public_key IS NULL AND NEW.device_key_generation<>0) THEN
   RAISE EXCEPTION 'device_key_generation_invalid' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END IF;
 key_changed:=NEW.public_key IS DISTINCT FROM OLD.public_key OR
  NEW.public_key_thumbprint_sha256 IS DISTINCT FROM OLD.public_key_thumbprint_sha256;
 newly_revoked:=OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL;
 IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NULL THEN
  RAISE EXCEPTION 'device_key_revocation_immutable' USING ERRCODE='23514';
 END IF;
 IF key_changed THEN
  IF NEW.device_key_generation<>OLD.device_key_generation+1 THEN
   RAISE EXCEPTION 'device_key_generation_invalid' USING ERRCODE='23514';
  END IF;
 ELSIF newly_revoked THEN
  IF NEW.device_key_generation=OLD.device_key_generation THEN
   NEW.device_key_generation:=OLD.device_key_generation+1;
  ELSIF NEW.device_key_generation<>OLD.device_key_generation+1 THEN
   RAISE EXCEPTION 'device_key_generation_invalid' USING ERRCODE='23514';
  END IF;
 ELSIF NEW.device_key_generation<>OLD.device_key_generation THEN
  RAISE EXCEPTION 'device_key_generation_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_device_key_generation BEFORE INSERT OR UPDATE ON account.device
 FOR EACH ROW EXECUTE FUNCTION app_private.enforce_device_key_generation();
REVOKE ALL ON FUNCTION app_private.enforce_device_key_generation() FROM PUBLIC;

CREATE TABLE ml.control_step_up_credential (
 credential_id uuid PRIMARY KEY DEFAULT uuidv7(),
 actor_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
 parent_device_key_generation bigint NOT NULL CHECK(parent_device_key_generation>=1),
 credential_generation bigint NOT NULL DEFAULT 1 CHECK(credential_generation>=1),
 public_key_spki bytea NOT NULL CHECK(octet_length(public_key_spki) BETWEEN 64 AND 256),
 public_key_thumbprint_sha256 bytea NOT NULL CHECK(octet_length(public_key_thumbprint_sha256)=32),
 attestation_chain_sha256 bytea NOT NULL CHECK(octet_length(attestation_chain_sha256)=32),
 attestation_summary jsonb NOT NULL CHECK(octet_length(attestation_summary::text)<=8192),
 registered_at timestamptz NOT NULL DEFAULT now(),
 revoked_at timestamptz,
 CONSTRAINT uq_ml_step_up_credential_generation
  UNIQUE(actor_user_id,device_id,credential_generation),
 FOREIGN KEY(actor_user_id,device_id)
  REFERENCES account.device(user_id,device_id) ON DELETE RESTRICT
);
CREATE INDEX ix_control_credential_actor ON ml.control_step_up_credential(actor_user_id,device_id)
 WHERE revoked_at IS NULL;

CREATE TABLE ml.control_step_up_challenge (
 challenge_id uuid PRIMARY KEY DEFAULT uuidv7(),
 nonce_sha256 bytea NOT NULL CHECK(octet_length(nonce_sha256)=32),
 actor_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 device_id uuid REFERENCES account.device(device_id) ON DELETE RESTRICT,
 session_id uuid REFERENCES account.user_session(session_id) ON DELETE RESTRICT,
 web_session_id uuid REFERENCES account.web_session(web_session_id) ON DELETE RESTRICT,
 credential_id uuid REFERENCES ml.control_step_up_credential(credential_id) ON DELETE RESTRICT,
 parent_device_key_generation bigint CHECK(parent_device_key_generation IS NULL OR
  parent_device_key_generation>=1),
 purpose text NOT NULL CHECK(purpose IN ('ML_CONSENT_STEP_UP_REGISTER_V1',
  'ML_CONSENT_STEP_UP_GRANT_V1','ML_ADMIN_OPERATION_V1')),
 action text NOT NULL CHECK(length(action) BETWEEN 1 AND 100),
 operation_id uuid NOT NULL,
 request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
 expected_generation bigint NOT NULL CHECK(expected_generation>=0),
 issued_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,
 consumed_at timestamptz,
 result_sha256 bytea CHECK(result_sha256 IS NULL OR octet_length(result_sha256)=32),
 CHECK(expires_at>issued_at AND expires_at<=issued_at+interval '5 minutes'),
 CHECK((session_id IS NOT NULL AND web_session_id IS NULL AND device_id IS NOT NULL)
  OR (web_session_id IS NOT NULL AND session_id IS NULL AND device_id IS NULL)),
 CHECK((consumed_at IS NULL AND result_sha256 IS NULL) OR
  (consumed_at IS NOT NULL AND result_sha256 IS NOT NULL)),
 CHECK((device_id IS NULL AND parent_device_key_generation IS NULL)
  OR (device_id IS NOT NULL AND parent_device_key_generation IS NOT NULL))
);
CREATE INDEX ix_control_step_up_expiry ON ml.control_step_up_challenge(expires_at)
 WHERE consumed_at IS NULL;
CREATE INDEX ix_control_step_up_actor ON ml.control_step_up_challenge(actor_user_id,operation_id);
CREATE TABLE ml.control_step_up_receipt (
 challenge_id uuid PRIMARY KEY REFERENCES ml.control_step_up_challenge(challenge_id)
  ON DELETE RESTRICT,
 actor_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 operation_id uuid NOT NULL,
 request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
 result_sha256 bytea NOT NULL CHECK(octet_length(result_sha256)=32),
 result_code text NOT NULL CHECK(length(result_code) BETWEEN 1 AND 80),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(actor_user_id,operation_id)
);
CREATE TRIGGER z_control_step_up_receipt_immutable BEFORE UPDATE OR DELETE
 ON ml.control_step_up_receipt FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_ml_artifact_immutable();
REVOKE ALL ON ml.control_step_up_credential,ml.control_step_up_challenge,
 ml.control_step_up_receipt FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.artifact_migration_issue)
 OR EXISTS(SELECT 1 FROM ml.artifact a WHERE NOT EXISTS(
  SELECT 1 FROM ml.embedding_model e WHERE e.weights_sha256=a.artifact_sha256))
 OR EXISTS(SELECT 1 FROM ml.face_artifact_release)
 OR EXISTS(SELECT 1 FROM ml.sona_artifact_release)
 OR EXISTS(SELECT 1 FROM ml.control_step_up_credential)
 OR EXISTS(SELECT 1 FROM ml.control_step_up_challenge)
 OR EXISTS(SELECT 1 FROM ml.control_step_up_receipt)
 OR EXISTS(SELECT 1 FROM ml.artifact_license_decision
  WHERE state<>'LEGACY_UNREVIEWED' OR decision_sequence<>1)
 THEN
  RAISE EXCEPTION 'refusing ml artifact authority downgrade with evidence'
   USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER z_control_step_up_receipt_immutable ON ml.control_step_up_receipt;
DROP TABLE ml.control_step_up_receipt;
DROP TABLE ml.control_step_up_challenge;
DROP TABLE ml.control_step_up_credential;
DROP TRIGGER z_device_key_generation ON account.device;
DROP FUNCTION app_private.enforce_device_key_generation();
ALTER TABLE account.device DROP CONSTRAINT device_key_generation_shape;
ALTER TABLE account.device DROP COLUMN device_key_generation;
DROP TRIGGER z_sona_artifact_release_immutable ON ml.sona_artifact_release;
DROP TRIGGER z_face_artifact_release_immutable ON ml.face_artifact_release;
DROP TABLE ml.sona_artifact_release;
DROP TABLE ml.face_artifact_release;
DROP TRIGGER z_artifact_license_current_derived ON ml.artifact_license_current;
DROP TRIGGER z_artifact_license_immutable ON ml.artifact_license_decision;
DROP TRIGGER z_artifact_license_current ON ml.artifact_license_decision;
DROP TRIGGER a_artifact_license_sequence ON ml.artifact_license_decision;
DROP TABLE ml.artifact_license_current;
DROP TABLE ml.artifact_license_decision;
DROP FUNCTION app_private.protect_artifact_license_current();
DROP FUNCTION app_private.advance_artifact_license_current();
DROP FUNCTION app_private.enforce_artifact_license_sequence();
DROP TRIGGER z_embedding_artifact_binding ON ml.embedding_model;
DROP FUNCTION app_private.protect_embedding_artifact_binding();
ALTER TABLE ml.embedding_model DROP CONSTRAINT embedding_artifact_fkey;
ALTER TABLE ml.embedding_model DROP CONSTRAINT embedding_artifact_sha256_check;
ALTER TABLE ml.embedding_model DROP COLUMN artifact_sha256;
DROP TRIGGER z_artifact_migration_issue_immutable ON ml.artifact_migration_issue;
DROP TRIGGER a_artifact_migration_issue_lock ON ml.artifact_migration_issue;
DROP TABLE ml.artifact_migration_issue;
DROP FUNCTION app_private.lock_ml_artifact_for_issue();
DROP TRIGGER z_ml_artifact_immutable ON ml.artifact;
DROP TABLE ml.artifact;
DROP FUNCTION app_private.protect_ml_artifact_immutable();
""")
