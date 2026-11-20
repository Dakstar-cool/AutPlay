"""Reserve dormant Face interpreter, qualification, activation and owner-policy authority."""

from alembic import op

revision = "0064_face_artifact_activation"
down_revision = "0063_sona_capture_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.face_execution_profile (
 execution_profile_sha256 bytea PRIMARY KEY CHECK(octet_length(execution_profile_sha256)=32),
 profile_document bytea NOT NULL CHECK(octet_length(profile_document) BETWEEN 1 AND 65536),
 runtime text NOT NULL CHECK(length(runtime) BETWEEN 1 AND 200),
 provider text NOT NULL CHECK(length(provider) BETWEEN 1 AND 100),
 precision text NOT NULL CHECK(length(precision) BETWEEN 1 AND 50),
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT ck_face_profile_digest CHECK(execution_profile_sha256=sha256(profile_document))
);

CREATE TABLE ml.face_semantic_interpreter (
 face_semantic_interpreter_id uuid PRIMARY KEY DEFAULT uuidv7(),
 interpreter_key text NOT NULL CHECK(length(interpreter_key) BETWEEN 1 AND 200),
 version text NOT NULL CHECK(length(version) BETWEEN 1 AND 100),
 manifest_sha256 bytea NOT NULL CHECK(octet_length(manifest_sha256)=32),
 embedding_model_id uuid NOT NULL
  REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
 interpreter_artifact_release_id uuid NOT NULL
  REFERENCES ml.face_artifact_release(face_artifact_release_id) ON DELETE RESTRICT,
 calibration_artifact_release_id uuid
  REFERENCES ml.face_artifact_release(face_artifact_release_id) ON DELETE RESTRICT,
 axis_schema_sha256 bytea NOT NULL CHECK(octet_length(axis_schema_sha256)=32),
 preprocessing_sha256 bytea NOT NULL CHECK(octet_length(preprocessing_sha256)=32),
 calibration_evidence_sha256 bytea NOT NULL CHECK(octet_length(calibration_evidence_sha256)=32),
 runtime_revision text NOT NULL CHECK(length(runtime_revision) BETWEEN 1 AND 200),
 status text NOT NULL CHECK(status IN ('CANDIDATE','REVIEWED','BLOCKED')),
 reviewer_user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 reviewed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_face_interpreter_version UNIQUE(interpreter_key,version,manifest_sha256),
 CONSTRAINT ck_face_interpreter_review CHECK(
  (status='CANDIDATE' AND reviewer_user_id IS NULL AND reviewed_at IS NULL)
  OR (status IN ('REVIEWED','BLOCKED')
   AND reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE INDEX ix_face_interpreter_encoder
 ON ml.face_semantic_interpreter(embedding_model_id,status,preprocessing_sha256);

CREATE TABLE ml.face_qualification_set (
 face_qualification_set_id uuid PRIMARY KEY DEFAULT uuidv7(),
 manifest_sha256 bytea NOT NULL UNIQUE CHECK(octet_length(manifest_sha256)=32),
 collection_kind text NOT NULL CHECK(collection_kind IN ('SMOKE','DEVELOPMENT','FINAL')),
 fixture_authority_sha256 bytea NOT NULL CHECK(octet_length(fixture_authority_sha256)=32),
 fixture_authority_generation bigint NOT NULL CHECK(fixture_authority_generation>=1),
 rater_authority_sha256 bytea NOT NULL CHECK(octet_length(rater_authority_sha256)=32),
 rater_authority_generation bigint NOT NULL CHECK(rater_authority_generation>=1),
 source_count integer NOT NULL CHECK(source_count BETWEEN 1 AND 1000),
 segment_count integer NOT NULL CHECK(segment_count BETWEEN 0 AND 12000),
 rater_count integer NOT NULL CHECK(rater_count BETWEEN 0 AND 20),
 retain_until timestamptz NOT NULL,
 sealed_at timestamptz NOT NULL,
 state text NOT NULL DEFAULT 'SEALED'
  CHECK(state IN ('SEALED','INVALIDATED','EXPIRED','DELETED')),
 state_generation bigint NOT NULL DEFAULT 1 CHECK(state_generation>=1),
 invalidated_at timestamptz,
 CONSTRAINT ck_face_qualification_counts CHECK(
  (collection_kind='SMOKE' AND source_count BETWEEN 10 AND 20)
  OR (collection_kind='DEVELOPMENT' AND source_count>=30 AND rater_count>=3)
  OR (collection_kind='FINAL' AND source_count>=15 AND rater_count>=3
   AND segment_count>=12*source_count)),
 CONSTRAINT ck_face_qualification_state CHECK(
  (state='SEALED' AND invalidated_at IS NULL)
  OR (state<>'SEALED' AND invalidated_at IS NOT NULL)),
 CONSTRAINT ck_face_qualification_retention CHECK(retain_until>sealed_at)
);
CREATE INDEX ix_face_qualification_state_expiry
 ON ml.face_qualification_set(state,retain_until) WHERE state='SEALED';

CREATE TABLE ml.face_qualification_approval (
 face_qualification_approval_id uuid PRIMARY KEY DEFAULT uuidv7(),
 face_qualification_set_id uuid NOT NULL
  REFERENCES ml.face_qualification_set(face_qualification_set_id) ON DELETE RESTRICT,
 face_semantic_interpreter_id uuid NOT NULL
  REFERENCES ml.face_semantic_interpreter(face_semantic_interpreter_id) ON DELETE RESTRICT,
 embedding_model_id uuid NOT NULL
  REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
 execution_profile_sha256 bytea NOT NULL
  REFERENCES ml.face_execution_profile(execution_profile_sha256) ON DELETE RESTRICT,
 qualification_manifest_sha256 bytea NOT NULL
  CHECK(octet_length(qualification_manifest_sha256)=32),
 report_sha256 bytea NOT NULL CHECK(octet_length(report_sha256)=32),
 approval_document bytea NOT NULL CHECK(octet_length(approval_document) BETWEEN 1 AND 1048576),
 approval_sha256 bytea NOT NULL CHECK(octet_length(approval_sha256)=32),
 signature_p1363 bytea NOT NULL CHECK(octet_length(signature_p1363)=64),
 signer_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 fixture_authority_generation bigint NOT NULL CHECK(fixture_authority_generation>=1),
 rater_authority_generation bigint NOT NULL CHECK(rater_authority_generation>=1),
 signed_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,
 state text NOT NULL DEFAULT 'APPROVED'
  CHECK(state IN ('APPROVED','INVALIDATED','EXPIRED')),
 state_generation bigint NOT NULL DEFAULT 1 CHECK(state_generation>=1),
 invalidated_at timestamptz,
 CONSTRAINT uq_face_approval_report_candidate UNIQUE(face_qualification_set_id,
  face_semantic_interpreter_id,execution_profile_sha256,report_sha256),
 CONSTRAINT ck_face_approval_digest CHECK(approval_sha256=sha256(approval_document)),
 CONSTRAINT ck_face_approval_expiry CHECK(expires_at>signed_at
  AND expires_at<=signed_at+interval '365 days'),
 CONSTRAINT ck_face_approval_state CHECK(
  (state='APPROVED' AND invalidated_at IS NULL)
  OR (state<>'APPROVED' AND invalidated_at IS NOT NULL))
);
CREATE INDEX ix_face_approval_state_expiry
 ON ml.face_qualification_approval(state,expires_at) WHERE state='APPROVED';

CREATE TABLE ml.face_timeline_activation (
 face_timeline_activation_id uuid PRIMARY KEY DEFAULT uuidv7(),
 activation_epoch bigint NOT NULL UNIQUE CHECK(activation_epoch>=1),
 previous_activation_id uuid
  REFERENCES ml.face_timeline_activation(face_timeline_activation_id) ON DELETE RESTRICT,
 action text NOT NULL CHECK(action IN ('ACTIVATE','ROLLBACK','DEACTIVATE')),
 embedding_model_id uuid REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
 face_semantic_interpreter_id uuid
  REFERENCES ml.face_semantic_interpreter(face_semantic_interpreter_id) ON DELETE RESTRICT,
 execution_profile_sha256 bytea
  REFERENCES ml.face_execution_profile(execution_profile_sha256) ON DELETE RESTRICT,
 face_qualification_approval_id uuid
  REFERENCES ml.face_qualification_approval(face_qualification_approval_id) ON DELETE RESTRICT,
 preprocessing_sha256 bytea CHECK(preprocessing_sha256 IS NULL
  OR octet_length(preprocessing_sha256)=32),
 artifact_policy_list_sha256 bytea CHECK(artifact_policy_list_sha256 IS NULL
  OR octet_length(artifact_policy_list_sha256)=32),
 evidence_sha256 bytea NOT NULL CHECK(octet_length(evidence_sha256)=32),
 actor_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 operation_id uuid NOT NULL,
 step_up_receipt_sha256 bytea NOT NULL CHECK(octet_length(step_up_receipt_sha256)=32),
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_face_activation_operation UNIQUE(actor_user_id,operation_id),
 CONSTRAINT ck_face_activation_target CHECK(
  (action='DEACTIVATE' AND embedding_model_id IS NULL
   AND face_semantic_interpreter_id IS NULL AND execution_profile_sha256 IS NULL
   AND face_qualification_approval_id IS NULL AND preprocessing_sha256 IS NULL
   AND artifact_policy_list_sha256 IS NULL)
  OR (action IN ('ACTIVATE','ROLLBACK') AND embedding_model_id IS NOT NULL
   AND face_semantic_interpreter_id IS NOT NULL AND execution_profile_sha256 IS NOT NULL
   AND face_qualification_approval_id IS NOT NULL AND preprocessing_sha256 IS NOT NULL
   AND artifact_policy_list_sha256 IS NOT NULL))
);
CREATE TABLE ml.face_activation_current (
 singleton integer PRIMARY KEY DEFAULT 1 CHECK(singleton=1),
 activation_epoch bigint NOT NULL DEFAULT 0 CHECK(activation_epoch>=0),
 face_timeline_activation_id uuid
  REFERENCES ml.face_timeline_activation(face_timeline_activation_id) ON DELETE RESTRICT,
 updated_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT ck_face_activation_current_identity CHECK(
  (activation_epoch=0 AND face_timeline_activation_id IS NULL)
  OR (activation_epoch>0 AND face_timeline_activation_id IS NOT NULL))
);
INSERT INTO ml.face_activation_current(singleton,activation_epoch) VALUES(1,0);

CREATE TABLE ml.face_analysis_policy_history (
 face_analysis_policy_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
 owner_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE CASCADE,
 policy_generation bigint NOT NULL CHECK(policy_generation>=1),
 desired_enabled boolean NOT NULL,
 admin_inhibited boolean NOT NULL,
 effective_enabled boolean GENERATED ALWAYS AS
  (desired_enabled AND NOT admin_inhibited) STORED,
 enable_watermark timestamptz,
 activation_epoch bigint NOT NULL CHECK(activation_epoch>=0),
 actor_user_id uuid REFERENCES account.user_account(user_id) ON DELETE SET NULL,
 actor_role text NOT NULL CHECK(actor_role IN ('OWNER','ADMIN','USER')),
 scope text NOT NULL CHECK(scope IN ('SELF','SAFETY')),
 reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 200),
 operation_id uuid NOT NULL,
 request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_face_policy_owner_generation UNIQUE(owner_user_id,policy_generation),
 CONSTRAINT ck_face_policy_watermark CHECK(
  (desired_enabled AND enable_watermark IS NOT NULL)
  OR (NOT desired_enabled AND enable_watermark IS NULL)),
 CONSTRAINT ck_face_policy_actor_scope CHECK(
  (scope='SELF' AND actor_user_id=owner_user_id)
  OR (scope='SAFETY' AND actor_role IN ('OWNER','ADMIN')))
);
CREATE UNIQUE INDEX uq_face_policy_actor_operation
 ON ml.face_analysis_policy_history(actor_user_id,operation_id)
 WHERE actor_user_id IS NOT NULL;
CREATE INDEX ix_face_policy_owner_created
 ON ml.face_analysis_policy_history(owner_user_id,created_at DESC);

CREATE TABLE ml.face_analysis_policy_current (
 owner_user_id uuid PRIMARY KEY REFERENCES account.user_account(user_id) ON DELETE CASCADE,
 policy_generation bigint NOT NULL CHECK(policy_generation>=1),
 desired_enabled boolean NOT NULL,
 admin_inhibited boolean NOT NULL,
 effective_enabled boolean GENERATED ALWAYS AS
  (desired_enabled AND NOT admin_inhibited) STORED,
 enable_watermark timestamptz,
 activation_epoch bigint NOT NULL CHECK(activation_epoch>=0),
 face_analysis_policy_event_id uuid NOT NULL UNIQUE
  REFERENCES ml.face_analysis_policy_history(face_analysis_policy_event_id) ON DELETE CASCADE,
 updated_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT fk_face_policy_current_history FOREIGN KEY(owner_user_id,policy_generation)
  REFERENCES ml.face_analysis_policy_history(owner_user_id,policy_generation)
  ON DELETE CASCADE,
 CONSTRAINT ck_face_policy_current_watermark CHECK(
  (desired_enabled AND enable_watermark IS NOT NULL)
  OR (NOT desired_enabled AND enable_watermark IS NULL))
);
CREATE INDEX ix_face_policy_current_effective
 ON ml.face_analysis_policy_current(owner_user_id,activation_epoch)
 WHERE effective_enabled;

CREATE FUNCTION app_private.protect_face_activation_immutable() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'face_activation_evidence_immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER z_face_profile_immutable BEFORE UPDATE ON ml.face_execution_profile
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_face_activation_immutable();
CREATE TRIGGER z_face_profile_no_delete BEFORE DELETE ON ml.face_execution_profile
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_face_activation_immutable();
CREATE TRIGGER z_face_interpreter_immutable BEFORE UPDATE ON ml.face_semantic_interpreter
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_face_activation_immutable();
CREATE TRIGGER z_face_interpreter_no_delete BEFORE DELETE ON ml.face_semantic_interpreter
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_face_activation_immutable();
CREATE TRIGGER z_face_qualification_no_delete BEFORE DELETE ON ml.face_qualification_set
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_face_activation_immutable();
CREATE TRIGGER z_face_approval_no_delete BEFORE DELETE ON ml.face_qualification_approval
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_face_activation_immutable();
CREATE TRIGGER z_face_activation_immutable BEFORE UPDATE ON ml.face_timeline_activation
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_face_activation_immutable();
CREATE TRIGGER z_face_activation_no_delete BEFORE DELETE ON ml.face_timeline_activation
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_face_activation_immutable();
CREATE TRIGGER z_face_activation_current_no_delete BEFORE DELETE ON ml.face_activation_current
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_face_activation_immutable();
CREATE FUNCTION app_private.fence_face_policy_actor_purge() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.actor_user_id IS NOT NULL
  OR (to_jsonb(NEW)-'actor_user_id') IS DISTINCT FROM (to_jsonb(OLD)-'actor_user_id') THEN
  RAISE EXCEPTION 'face_policy_history_immutable' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_policy_history_immutable BEFORE UPDATE ON ml.face_analysis_policy_history
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_policy_actor_purge();
CREATE FUNCTION app_private.fence_face_policy_history_insert() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE actor account.user_account%ROWTYPE;
DECLARE owner_row account.user_account%ROWTYPE;
DECLARE current_epoch bigint;
BEGIN
 IF NEW.actor_user_id IS NULL THEN
  RAISE EXCEPTION 'face_policy_actor_forbidden' USING ERRCODE='42501';
 END IF;
 SELECT * INTO actor FROM account.user_account
  WHERE user_id=NEW.actor_user_id FOR SHARE;
 SELECT * INTO owner_row FROM account.user_account
  WHERE user_id=NEW.owner_user_id FOR SHARE;
 IF actor.user_id IS NULL OR owner_row.user_id IS NULL
  OR actor.status<>'ACTIVE' OR actor.deleted_at IS NOT NULL
  OR owner_row.status<>'ACTIVE' OR owner_row.deleted_at IS NOT NULL
  OR NEW.actor_role<>actor.role
  OR (NEW.scope='SELF' AND NEW.actor_user_id<>NEW.owner_user_id)
  OR (NEW.scope='SAFETY' AND actor.role NOT IN ('OWNER','ADMIN')) THEN
  RAISE EXCEPTION 'face_policy_actor_forbidden' USING ERRCODE='42501';
 END IF;
 SELECT activation_epoch INTO current_epoch FROM ml.face_activation_current
  WHERE singleton=1 FOR SHARE;
 IF current_epoch IS NULL OR NEW.activation_epoch<>current_epoch THEN
  RAISE EXCEPTION 'face_policy_activation_epoch_conflict' USING ERRCODE='40001';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_face_policy_history_authority BEFORE INSERT ON ml.face_analysis_policy_history
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_policy_history_insert();

CREATE FUNCTION app_private.fence_face_qualification_insert() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE source_set ml.face_qualification_set%ROWTYPE;
DECLARE interpreter ml.face_semantic_interpreter%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='face_qualification_set' THEN
  IF NEW.state<>'SEALED' OR NEW.state_generation<>1
   OR NEW.invalidated_at IS NOT NULL OR NEW.retain_until<=clock_timestamp() THEN
   RAISE EXCEPTION 'face_qualification_initial_state_invalid' USING ERRCODE='23514';
  END IF;
 ELSE
  IF NEW.state<>'APPROVED' OR NEW.state_generation<>1
   OR NEW.invalidated_at IS NOT NULL OR NEW.expires_at<=clock_timestamp() THEN
   RAISE EXCEPTION 'face_approval_initial_state_invalid' USING ERRCODE='23514';
  END IF;
  SELECT * INTO source_set FROM ml.face_qualification_set
   WHERE face_qualification_set_id=NEW.face_qualification_set_id FOR SHARE;
  SELECT * INTO interpreter FROM ml.face_semantic_interpreter
   WHERE face_semantic_interpreter_id=NEW.face_semantic_interpreter_id FOR SHARE;
  IF source_set.face_qualification_set_id IS NULL
   OR interpreter.face_semantic_interpreter_id IS NULL
   OR interpreter.status<>'REVIEWED'
   OR interpreter.embedding_model_id<>NEW.embedding_model_id
   OR source_set.state<>'SEALED' OR source_set.collection_kind<>'FINAL'
   OR source_set.retain_until<=clock_timestamp()
   OR source_set.manifest_sha256<>NEW.qualification_manifest_sha256
   OR source_set.fixture_authority_generation<>NEW.fixture_authority_generation
   OR source_set.rater_authority_generation<>NEW.rater_authority_generation THEN
   RAISE EXCEPTION 'face_approval_authority_mismatch' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_qualification_insert BEFORE INSERT ON ml.face_qualification_set
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_qualification_insert();
CREATE TRIGGER z_face_approval_insert BEFORE INSERT ON ml.face_qualification_approval
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_qualification_insert();

CREATE FUNCTION app_private.fence_face_qualification_state() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state_generation<>OLD.state_generation+1
  OR NEW.invalidated_at IS NULL
  OR NEW.invalidated_at<clock_timestamp()-interval '1 hour'
  OR NEW.invalidated_at>clock_timestamp()+interval '1 hour' THEN
  RAISE EXCEPTION 'face_qualification_state_conflict' USING ERRCODE='40001';
 END IF;
 IF TG_TABLE_NAME='face_qualification_set' THEN
  IF NOT ((OLD.state='SEALED' AND NEW.state IN ('INVALIDATED','EXPIRED','DELETED'))
   OR (OLD.state IN ('INVALIDATED','EXPIRED') AND NEW.state='DELETED')) THEN
   RAISE EXCEPTION 'face_qualification_state_conflict' USING ERRCODE='40001';
  END IF;
  IF EXISTS(SELECT 1 FROM ml.face_qualification_approval
   WHERE face_qualification_set_id=NEW.face_qualification_set_id AND state='APPROVED') THEN
   RAISE EXCEPTION 'face_qualification_has_live_approval' USING ERRCODE='23514';
  END IF;
  IF NEW.face_qualification_set_id<>OLD.face_qualification_set_id
   OR NEW.manifest_sha256<>OLD.manifest_sha256
   OR NEW.collection_kind<>OLD.collection_kind
   OR NEW.fixture_authority_sha256<>OLD.fixture_authority_sha256
   OR NEW.fixture_authority_generation<>OLD.fixture_authority_generation
   OR NEW.rater_authority_sha256<>OLD.rater_authority_sha256
   OR NEW.rater_authority_generation<>OLD.rater_authority_generation
   OR NEW.source_count<>OLD.source_count OR NEW.segment_count<>OLD.segment_count
   OR NEW.rater_count<>OLD.rater_count OR NEW.retain_until<>OLD.retain_until
   OR NEW.sealed_at<>OLD.sealed_at THEN
   RAISE EXCEPTION 'face_qualification_metadata_immutable' USING ERRCODE='23514';
  END IF;
 ELSE
  IF OLD.state<>'APPROVED' OR NEW.state NOT IN ('INVALIDATED','EXPIRED') THEN
   RAISE EXCEPTION 'face_approval_state_conflict' USING ERRCODE='40001';
  END IF;
  IF NEW.face_qualification_approval_id<>OLD.face_qualification_approval_id
   OR NEW.face_qualification_set_id<>OLD.face_qualification_set_id
   OR NEW.face_semantic_interpreter_id<>OLD.face_semantic_interpreter_id
   OR NEW.embedding_model_id<>OLD.embedding_model_id
   OR NEW.execution_profile_sha256<>OLD.execution_profile_sha256
   OR NEW.qualification_manifest_sha256<>OLD.qualification_manifest_sha256
   OR NEW.report_sha256<>OLD.report_sha256
   OR NEW.approval_document<>OLD.approval_document
   OR NEW.approval_sha256<>OLD.approval_sha256
   OR NEW.signature_p1363<>OLD.signature_p1363
   OR NEW.signer_user_id<>OLD.signer_user_id
   OR NEW.fixture_authority_generation<>OLD.fixture_authority_generation
   OR NEW.rater_authority_generation<>OLD.rater_authority_generation
   OR NEW.signed_at<>OLD.signed_at OR NEW.expires_at<>OLD.expires_at THEN
   RAISE EXCEPTION 'face_approval_metadata_immutable' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_qualification_state BEFORE UPDATE ON ml.face_qualification_set
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_qualification_state();
CREATE TRIGGER z_face_approval_state BEFORE UPDATE ON ml.face_qualification_approval
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_qualification_state();

CREATE FUNCTION app_private.fence_face_activation_current() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE selected ml.face_timeline_activation%ROWTYPE;
DECLARE approval ml.face_qualification_approval%ROWTYPE;
DECLARE source_set ml.face_qualification_set%ROWTYPE;
DECLARE interpreter ml.face_semantic_interpreter%ROWTYPE;
BEGIN
 IF NEW.singleton<>1 OR NEW.activation_epoch<>OLD.activation_epoch+1
  OR NEW.face_timeline_activation_id IS NULL THEN
  RAISE EXCEPTION 'face_activation_epoch_conflict' USING ERRCODE='40001';
 END IF;
 SELECT * INTO selected FROM ml.face_timeline_activation
  WHERE face_timeline_activation_id=NEW.face_timeline_activation_id FOR KEY SHARE;
 IF NOT FOUND OR selected.activation_epoch<>NEW.activation_epoch
  OR selected.previous_activation_id IS DISTINCT FROM OLD.face_timeline_activation_id THEN
  RAISE EXCEPTION 'face_activation_chain_conflict' USING ERRCODE='23514';
 END IF;
 IF selected.action<>'DEACTIVATE' THEN
  SELECT * INTO approval FROM ml.face_qualification_approval
   WHERE face_qualification_approval_id=selected.face_qualification_approval_id FOR SHARE;
  SELECT * INTO source_set FROM ml.face_qualification_set
   WHERE face_qualification_set_id=approval.face_qualification_set_id FOR SHARE;
  SELECT * INTO interpreter FROM ml.face_semantic_interpreter
   WHERE face_semantic_interpreter_id=selected.face_semantic_interpreter_id FOR SHARE;
  IF approval.face_qualification_approval_id IS NULL
   OR source_set.face_qualification_set_id IS NULL
   OR interpreter.face_semantic_interpreter_id IS NULL
   OR approval.state<>'APPROVED'
   OR approval.expires_at<=clock_timestamp()
   OR source_set.state<>'SEALED' OR source_set.retain_until<=clock_timestamp()
   OR interpreter.status<>'REVIEWED'
   OR approval.face_semantic_interpreter_id<>selected.face_semantic_interpreter_id
   OR approval.embedding_model_id<>selected.embedding_model_id
   OR approval.execution_profile_sha256<>selected.execution_profile_sha256
   OR interpreter.preprocessing_sha256<>selected.preprocessing_sha256 THEN
   RAISE EXCEPTION 'face_activation_approval_not_current' USING ERRCODE='23514';
  END IF;
 END IF;
 NEW.updated_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_activation_current BEFORE UPDATE ON ml.face_activation_current
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_activation_current();

CREATE FUNCTION app_private.fence_face_policy_current() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE event_row ml.face_analysis_policy_history%ROWTYPE;
BEGIN
 SELECT * INTO event_row FROM ml.face_analysis_policy_history
  WHERE face_analysis_policy_event_id=NEW.face_analysis_policy_event_id FOR KEY SHARE;
 IF NOT FOUND OR event_row.owner_user_id<>NEW.owner_user_id
  OR event_row.policy_generation<>NEW.policy_generation
  OR event_row.desired_enabled<>NEW.desired_enabled
  OR event_row.admin_inhibited<>NEW.admin_inhibited
  OR event_row.enable_watermark IS DISTINCT FROM NEW.enable_watermark
  OR event_row.activation_epoch<>NEW.activation_epoch THEN
  RAISE EXCEPTION 'face_policy_projection_mismatch' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.policy_generation<>1
   OR (event_row.scope='SELF' AND NEW.admin_inhibited)
   OR (event_row.scope='SAFETY' AND NEW.desired_enabled) THEN
   RAISE EXCEPTION 'face_policy_initial_generation' USING ERRCODE='23514';
  END IF;
 ELSE
  IF NEW.owner_user_id<>OLD.owner_user_id
   OR NEW.policy_generation<>OLD.policy_generation+1
   OR (event_row.scope='SELF' AND NEW.admin_inhibited<>OLD.admin_inhibited)
   OR (event_row.scope='SAFETY' AND NEW.desired_enabled<>OLD.desired_enabled)
   OR (event_row.scope='SAFETY'
     AND NEW.enable_watermark IS DISTINCT FROM OLD.enable_watermark) THEN
   RAISE EXCEPTION 'face_policy_generation_or_scope_conflict' USING ERRCODE='40001';
  END IF;
 END IF;
 NEW.updated_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_policy_current BEFORE INSERT OR UPDATE ON ml.face_analysis_policy_current
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_policy_current();

REVOKE ALL ON ml.face_execution_profile,ml.face_semantic_interpreter,
 ml.face_qualification_set,ml.face_qualification_approval,
 ml.face_timeline_activation,ml.face_activation_current,
 ml.face_analysis_policy_history,ml.face_analysis_policy_current FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_face_activation_immutable() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_policy_actor_purge() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_policy_history_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_qualification_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_qualification_state() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_activation_current() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_policy_current() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_execution_profile)
  OR EXISTS(SELECT 1 FROM ml.face_semantic_interpreter)
  OR EXISTS(SELECT 1 FROM ml.face_qualification_set)
  OR EXISTS(SELECT 1 FROM ml.face_qualification_approval)
  OR EXISTS(SELECT 1 FROM ml.face_timeline_activation)
  OR EXISTS(SELECT 1 FROM ml.face_analysis_policy_history)
  OR EXISTS(SELECT 1 FROM ml.face_analysis_policy_current)
  OR EXISTS(SELECT 1 FROM ml.face_activation_current WHERE activation_epoch<>0) THEN
  RAISE EXCEPTION 'refusing Face activation downgrade with evidence' USING ERRCODE='55000';
 END IF;
END $$;
DROP TABLE ml.face_analysis_policy_current;
DROP TABLE ml.face_analysis_policy_history;
DROP TABLE ml.face_activation_current;
DROP TABLE ml.face_timeline_activation;
DROP TABLE ml.face_qualification_approval;
DROP TABLE ml.face_qualification_set;
DROP TABLE ml.face_semantic_interpreter;
DROP TABLE ml.face_execution_profile;
DROP FUNCTION app_private.fence_face_policy_current();
DROP FUNCTION app_private.fence_face_activation_current();
DROP FUNCTION app_private.fence_face_qualification_state();
DROP FUNCTION IF EXISTS app_private.fence_face_qualification_insert();
DROP FUNCTION IF EXISTS app_private.fence_face_policy_actor_purge();
DROP FUNCTION IF EXISTS app_private.fence_face_policy_history_insert();
DROP FUNCTION app_private.protect_face_activation_immutable();
""")
