"""Add non-activating native Sona capture and retryable shadow lineage storage."""

from alembic import op

revision = "0063_sona_capture_authority"
down_revision = "0062_gpu_admission_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.sona_capture_bundle (
 recommendation_request_id uuid PRIMARY KEY,
 user_id uuid NOT NULL,
 baseline_snapshot_sha256 bytea NOT NULL CHECK(octet_length(baseline_snapshot_sha256)=32),
 temporal_snapshot_sha256 bytea NOT NULL CHECK(octet_length(temporal_snapshot_sha256)=32),
 candidate_membership_sha256 bytea NOT NULL CHECK(octet_length(candidate_membership_sha256)=32),
 p11_ranking_sha256 bytea NOT NULL CHECK(octet_length(p11_ranking_sha256)=32),
 bundle_sha256 bytea NOT NULL CHECK(octet_length(bundle_sha256)=32),
 consent_receipt_sha256 bytea NOT NULL CHECK(octet_length(consent_receipt_sha256)=32),
 consent_generation bigint NOT NULL CHECK(consent_generation>=1),
 cutoff_at_ms bigint NOT NULL CHECK(cutoff_at_ms>=0),
 interaction_watermark bigint NOT NULL CHECK(interaction_watermark>=0),
 universe_count integer NOT NULL CHECK(universe_count BETWEEN 0 AND 5000),
 eligible_count integer NOT NULL CHECK(eligible_count BETWEEN 0 AND 5000),
 ineligibility_reason text CHECK(ineligibility_reason='UNIVERSE_OVER_CAP'),
 bundle_document bytea NOT NULL CHECK(octet_length(bundle_document) BETWEEN 1 AND 16777216),
 created_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,
 CONSTRAINT fk_sona_capture_request_owner FOREIGN KEY(user_id,recommendation_request_id)
  REFERENCES ml.recommendation_request(user_id,recommendation_request_id) ON DELETE CASCADE,
 CONSTRAINT uq_sona_capture_owner UNIQUE(user_id,recommendation_request_id),
 CONSTRAINT ck_sona_capture_counts CHECK(eligible_count<=universe_count),
 CONSTRAINT ck_sona_capture_document_hash CHECK(bundle_sha256=sha256(bundle_document)),
 CONSTRAINT ck_sona_capture_eligibility CHECK(
  (universe_count<=1024 AND ineligibility_reason IS NULL)
  OR (universe_count>1024 AND ineligibility_reason='UNIVERSE_OVER_CAP')),
 CONSTRAINT ck_sona_capture_expiry CHECK(expires_at=created_at+interval '180 days')
);
CREATE INDEX ix_sona_capture_owner_expiry
 ON ml.sona_capture_bundle(user_id,expires_at);

CREATE TABLE ml.sona_capture_lineage_cursor (
 recommendation_request_id uuid PRIMARY KEY,
 user_id uuid NOT NULL,
 state text NOT NULL DEFAULT 'ACTIVE' CHECK(state IN ('ACTIVE','EXPIRED','CANCELLED')),
 last_seen_registry_generation bigint NOT NULL DEFAULT 0
  CHECK(last_seen_registry_generation>=0),
 state_generation bigint NOT NULL DEFAULT 1 CHECK(state_generation>=1),
 expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT fk_sona_cursor_bundle_owner FOREIGN KEY(user_id,recommendation_request_id)
  REFERENCES ml.sona_capture_bundle(user_id,recommendation_request_id) ON DELETE CASCADE
);
CREATE INDEX ix_sona_cursor_active_owner_expiry
 ON ml.sona_capture_lineage_cursor(user_id,expires_at,recommendation_request_id)
 WHERE state='ACTIVE';

CREATE TABLE ml.sona_capture_target_dispatch (
 target_dispatch_id uuid PRIMARY KEY DEFAULT uuidv7(),
 recommendation_request_id uuid NOT NULL,
 user_id uuid NOT NULL,
 model_manifest_sha256 bytea NOT NULL CHECK(octet_length(model_manifest_sha256)=32),
 tokenizer_sha256 bytea NOT NULL CHECK(octet_length(tokenizer_sha256)=32),
 pipeline_manifest_sha256 bytea NOT NULL CHECK(octet_length(pipeline_manifest_sha256)=32),
 execution_profile_sha256 bytea NOT NULL CHECK(octet_length(execution_profile_sha256)=32),
 registry_generation bigint NOT NULL CHECK(registry_generation>=1),
 state text NOT NULL DEFAULT 'WAITING' CHECK(state IN
  ('WAITING','READY','CONSUMED','TERMINAL_INELIGIBLE','EXPIRED','CANCELLED')),
 state_generation bigint NOT NULL DEFAULT 1 CHECK(state_generation>=1),
 reason_code text,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT fk_sona_dispatch_bundle_owner FOREIGN KEY(user_id,recommendation_request_id)
  REFERENCES ml.sona_capture_bundle(user_id,recommendation_request_id) ON DELETE CASCADE,
 CONSTRAINT uq_sona_dispatch_exact UNIQUE(recommendation_request_id,
  model_manifest_sha256,tokenizer_sha256,pipeline_manifest_sha256,
  execution_profile_sha256)
);
CREATE INDEX ix_sona_dispatch_owner_state
 ON ml.sona_capture_target_dispatch(user_id,state,created_at);

CREATE TABLE ml.sona_shadow_work (
 sona_shadow_work_id uuid PRIMARY KEY DEFAULT uuidv7(),
 target_dispatch_id uuid NOT NULL UNIQUE
  REFERENCES ml.sona_capture_target_dispatch(target_dispatch_id) ON DELETE CASCADE,
 recommendation_request_id uuid NOT NULL,
 user_id uuid NOT NULL,
 model_manifest_sha256 bytea NOT NULL CHECK(octet_length(model_manifest_sha256)=32),
 tokenizer_sha256 bytea NOT NULL CHECK(octet_length(tokenizer_sha256)=32),
 pipeline_manifest_sha256 bytea NOT NULL CHECK(octet_length(pipeline_manifest_sha256)=32),
 execution_profile_sha256 bytea NOT NULL CHECK(octet_length(execution_profile_sha256)=32),
 lineage_sha256 bytea NOT NULL CHECK(octet_length(lineage_sha256)=32),
 state text NOT NULL DEFAULT 'PENDING' CHECK(state IN
  ('PENDING','CLAIMED','SUCCEEDED','RETRY_WAIT','TERMINAL_INELIGIBLE',
   'TERMINAL_FAILED','RETRY_EXHAUSTED','CANCELLED','SUPERSEDED')),
 state_generation bigint NOT NULL DEFAULT 1 CHECK(state_generation>=1),
 claim_generation bigint NOT NULL DEFAULT 0 CHECK(claim_generation>=0),
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 16),
 lease_until timestamptz,
 next_retry_at timestamptz,
 reason_code text,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT fk_sona_work_bundle_owner FOREIGN KEY(user_id,recommendation_request_id)
  REFERENCES ml.sona_capture_bundle(user_id,recommendation_request_id) ON DELETE CASCADE,
 CONSTRAINT uq_sona_work_exact UNIQUE(recommendation_request_id,model_manifest_sha256,
  tokenizer_sha256,pipeline_manifest_sha256,execution_profile_sha256),
 CONSTRAINT uq_sona_work_owner UNIQUE(sona_shadow_work_id,user_id,recommendation_request_id),
 CONSTRAINT ck_sona_work_lease CHECK((state='CLAIMED')=(lease_until IS NOT NULL)),
 CONSTRAINT ck_sona_work_retry CHECK((state='RETRY_WAIT')=(next_retry_at IS NOT NULL))
);
CREATE INDEX ix_sona_work_claim_owner
 ON ml.sona_shadow_work(user_id,state,next_retry_at,created_at)
 WHERE state IN ('PENDING','RETRY_WAIT','CLAIMED');

CREATE TABLE ml.sona_shadow_attempt (
 sona_shadow_attempt_id uuid PRIMARY KEY DEFAULT uuidv7(),
 sona_shadow_work_id uuid NOT NULL,
 user_id uuid NOT NULL,
 recommendation_request_id uuid NOT NULL,
 attempt_no integer NOT NULL CHECK(attempt_no BETWEEN 1 AND 16),
 claim_generation bigint NOT NULL CHECK(claim_generation>=1),
 outcome text NOT NULL CHECK(outcome IN
  ('SUCCEEDED','RETRY_WAIT','TERMINAL_INELIGIBLE','TERMINAL_FAILED',
   'RETRY_EXHAUSTED','CANCELLED','SUPERSEDED')),
 reason_code text,
 attempt_document bytea NOT NULL CHECK(octet_length(attempt_document) BETWEEN 1 AND 1048576),
 attempt_sha256 bytea NOT NULL CHECK(octet_length(attempt_sha256)=32),
 started_at timestamptz NOT NULL,
 finished_at timestamptz NOT NULL CHECK(finished_at>=started_at),
 CONSTRAINT fk_sona_attempt_work_owner
  FOREIGN KEY(sona_shadow_work_id,user_id,recommendation_request_id)
  REFERENCES ml.sona_shadow_work(sona_shadow_work_id,user_id,recommendation_request_id)
  ON DELETE CASCADE,
 CONSTRAINT uq_sona_attempt_number UNIQUE(sona_shadow_work_id,attempt_no),
 CONSTRAINT ck_sona_attempt_document_hash CHECK(attempt_sha256=sha256(attempt_document))
);
CREATE INDEX ix_sona_attempt_owner_work
 ON ml.sona_shadow_attempt(user_id,sona_shadow_work_id,attempt_no);

CREATE TABLE ml.sona_shadow_evidence (
 sona_shadow_work_id uuid PRIMARY KEY,
 user_id uuid NOT NULL,
 recommendation_request_id uuid NOT NULL,
 evidence_sha256 bytea NOT NULL CHECK(octet_length(evidence_sha256)=32),
 evidence_document bytea NOT NULL CHECK(octet_length(evidence_document) BETWEEN 1 AND 16777216),
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT fk_sona_evidence_work_owner
  FOREIGN KEY(sona_shadow_work_id,user_id,recommendation_request_id)
  REFERENCES ml.sona_shadow_work(sona_shadow_work_id,user_id,recommendation_request_id)
  ON DELETE CASCADE,
 CONSTRAINT ck_sona_evidence_document_hash CHECK(evidence_sha256=sha256(evidence_document))
);
CREATE INDEX ix_sona_evidence_owner_request
 ON ml.sona_shadow_evidence(user_id,recommendation_request_id);

CREATE FUNCTION app_private.validate_sona_capture_bundle() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE request_row ml.recommendation_request%ROWTYPE;
        baseline_row ml.recommendation_input_snapshot%ROWTYPE;
BEGIN
 SELECT * INTO request_row FROM ml.recommendation_request
  WHERE recommendation_request_id=NEW.recommendation_request_id FOR KEY SHARE;
 SELECT * INTO baseline_row FROM ml.recommendation_input_snapshot
  WHERE recommendation_input_snapshot_id=request_row.recommendation_input_snapshot_id
  FOR KEY SHARE;
 IF request_row.user_id IS DISTINCT FROM NEW.user_id
  OR request_row.created_at IS DISTINCT FROM NEW.created_at
  OR request_row.input_snapshot_sha256 IS DISTINCT FROM NEW.baseline_snapshot_sha256
  OR request_row.interaction_watermark IS DISTINCT FROM NEW.interaction_watermark
  OR baseline_row.user_id IS DISTINCT FROM NEW.user_id
  OR baseline_row.input_snapshot_sha256 IS DISTINCT FROM NEW.baseline_snapshot_sha256
  OR jsonb_array_length(baseline_row.snapshot_document->'tracks')
     IS DISTINCT FROM NEW.universe_count THEN
  RAISE EXCEPTION 'sona_capture_baseline_mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_sona_bundle_validate BEFORE INSERT ON ml.sona_capture_bundle
 FOR EACH ROW EXECUTE FUNCTION app_private.validate_sona_capture_bundle();

CREATE FUNCTION app_private.validate_sona_evidence_publication() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE work_row ml.sona_shadow_work%ROWTYPE;
BEGIN
 SELECT * INTO work_row FROM ml.sona_shadow_work
  WHERE sona_shadow_work_id=NEW.sona_shadow_work_id FOR UPDATE;
 IF work_row.state<>'SUCCEEDED' OR work_row.user_id<>NEW.user_id
  OR work_row.recommendation_request_id<>NEW.recommendation_request_id THEN
  RAISE EXCEPTION 'sona_evidence_unbound' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_sona_evidence_validate BEFORE INSERT ON ml.sona_shadow_evidence
 FOR EACH ROW EXECUTE FUNCTION app_private.validate_sona_evidence_publication();

CREATE FUNCTION app_private.validate_sona_capture_child() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE bundle ml.sona_capture_bundle%ROWTYPE;
BEGIN
 SELECT * INTO bundle FROM ml.sona_capture_bundle
  WHERE recommendation_request_id=NEW.recommendation_request_id FOR KEY SHARE;
 IF NOT FOUND OR bundle.user_id<>NEW.user_id
  OR bundle.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'sona_capture_child_expired_or_unbound' USING ERRCODE='23514';
 END IF;
 IF TG_TABLE_NAME='sona_capture_lineage_cursor' THEN
  IF NEW.expires_at<>bundle.expires_at
   OR NEW.state<>'ACTIVE' OR NEW.state_generation<>1
   OR NEW.last_seen_registry_generation<>0 THEN
   RAISE EXCEPTION 'sona_capture_cursor_expiry_mismatch' USING ERRCODE='23514';
  END IF;
 ELSIF TG_TABLE_NAME='sona_capture_target_dispatch' THEN
  IF NEW.state<>'WAITING' OR NEW.state_generation<>1 THEN
   RAISE EXCEPTION 'sona_dispatch_initial_state_invalid' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_sona_cursor_validate BEFORE INSERT ON ml.sona_capture_lineage_cursor
 FOR EACH ROW EXECUTE FUNCTION app_private.validate_sona_capture_child();
CREATE TRIGGER a_sona_dispatch_validate BEFORE INSERT ON ml.sona_capture_target_dispatch
 FOR EACH ROW EXECUTE FUNCTION app_private.validate_sona_capture_child();

CREATE FUNCTION app_private.protect_sona_capture_update() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'sona_capture_immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER z_sona_bundle_immutable BEFORE UPDATE ON ml.sona_capture_bundle
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_sona_capture_update();
CREATE TRIGGER z_sona_attempt_immutable BEFORE UPDATE ON ml.sona_shadow_attempt
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_sona_capture_update();
CREATE TRIGGER z_sona_evidence_immutable BEFORE UPDATE ON ml.sona_shadow_evidence
 FOR EACH ROW EXECUTE FUNCTION app_private.protect_sona_capture_update();

CREATE FUNCTION app_private.fence_sona_capture_state() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state_generation<>OLD.state_generation+1
  OR NEW.user_id<>OLD.user_id
  OR NEW.recommendation_request_id<>OLD.recommendation_request_id THEN
  RAISE EXCEPTION 'sona_state_generation_conflict' USING ERRCODE='40001';
 END IF;
 IF TG_TABLE_NAME='sona_capture_lineage_cursor' THEN
  IF OLD.state<>'ACTIVE' OR NEW.state NOT IN ('ACTIVE','EXPIRED','CANCELLED')
   OR NEW.last_seen_registry_generation<OLD.last_seen_registry_generation
   OR NEW.expires_at<>OLD.expires_at
   OR (NEW.state='EXPIRED' AND OLD.expires_at>clock_timestamp()) THEN
   RAISE EXCEPTION 'sona_cursor_terminal_or_regressed' USING ERRCODE='23514';
  END IF;
 ELSIF TG_TABLE_NAME='sona_capture_target_dispatch' THEN
  IF NEW.target_dispatch_id<>OLD.target_dispatch_id
   OR NEW.model_manifest_sha256<>OLD.model_manifest_sha256
   OR NEW.tokenizer_sha256<>OLD.tokenizer_sha256
   OR NEW.pipeline_manifest_sha256<>OLD.pipeline_manifest_sha256
   OR NEW.execution_profile_sha256<>OLD.execution_profile_sha256
   OR NEW.registry_generation<>OLD.registry_generation
   OR NOT ((OLD.state='WAITING' AND NEW.state IN
    ('READY','TERMINAL_INELIGIBLE','EXPIRED','CANCELLED'))
   OR (OLD.state='READY' AND NEW.state IN
    ('CONSUMED','TERMINAL_INELIGIBLE','EXPIRED','CANCELLED'))) THEN
   RAISE EXCEPTION 'sona_dispatch_transition_invalid' USING ERRCODE='23514';
  END IF;
 ELSIF TG_TABLE_NAME='sona_shadow_work' THEN
  IF NEW.sona_shadow_work_id<>OLD.sona_shadow_work_id
   OR NEW.target_dispatch_id<>OLD.target_dispatch_id
   OR NEW.model_manifest_sha256<>OLD.model_manifest_sha256
   OR NEW.tokenizer_sha256<>OLD.tokenizer_sha256
   OR NEW.pipeline_manifest_sha256<>OLD.pipeline_manifest_sha256
   OR NEW.execution_profile_sha256<>OLD.execution_profile_sha256
   OR NEW.lineage_sha256<>OLD.lineage_sha256
   OR NEW.claim_generation<OLD.claim_generation
   OR NEW.attempt_count<OLD.attempt_count
   OR NOT ((OLD.state='PENDING' AND NEW.state IN
    ('CLAIMED','CANCELLED','SUPERSEDED'))
   OR (OLD.state='CLAIMED' AND NEW.state IN
    ('SUCCEEDED','RETRY_WAIT','TERMINAL_INELIGIBLE','TERMINAL_FAILED',
     'RETRY_EXHAUSTED','CANCELLED','SUPERSEDED'))
   OR (OLD.state='RETRY_WAIT' AND NEW.state IN
    ('PENDING','CANCELLED','SUPERSEDED'))) THEN
   RAISE EXCEPTION 'sona_work_transition_invalid' USING ERRCODE='23514';
  END IF;
  IF OLD.state='PENDING' AND NEW.state='CLAIMED' THEN
   IF NEW.claim_generation<>OLD.claim_generation+1
    OR NEW.attempt_count<>OLD.attempt_count+1
    OR NEW.lease_until<=clock_timestamp() THEN
    RAISE EXCEPTION 'sona_work_claim_invalid' USING ERRCODE='23514';
   END IF;
  ELSIF NEW.claim_generation<>OLD.claim_generation
   OR NEW.attempt_count<>OLD.attempt_count THEN
   RAISE EXCEPTION 'sona_work_attempt_generation_invalid' USING ERRCODE='23514';
  END IF;
  IF OLD.state='CLAIMED' AND NEW.state='SUCCEEDED'
   AND OLD.lease_until<=clock_timestamp() THEN
   RAISE EXCEPTION 'sona_work_lease_expired' USING ERRCODE='23514';
  END IF;
 END IF;
 NEW.updated_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER z_sona_cursor_fence BEFORE UPDATE ON ml.sona_capture_lineage_cursor
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_sona_capture_state();
CREATE TRIGGER z_sona_dispatch_fence BEFORE UPDATE ON ml.sona_capture_target_dispatch
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_sona_capture_state();
CREATE TRIGGER z_sona_work_fence BEFORE UPDATE ON ml.sona_shadow_work
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_sona_capture_state();

CREATE FUNCTION app_private.validate_sona_work_target() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE target ml.sona_capture_target_dispatch%ROWTYPE;
        bundle ml.sona_capture_bundle%ROWTYPE;
BEGIN
 SELECT * INTO target FROM ml.sona_capture_target_dispatch
  WHERE target_dispatch_id=NEW.target_dispatch_id FOR UPDATE;
 SELECT * INTO bundle FROM ml.sona_capture_bundle
  WHERE recommendation_request_id=NEW.recommendation_request_id FOR UPDATE;
 IF NOT FOUND OR target.state<>'READY' OR bundle.ineligibility_reason IS NOT NULL
  OR bundle.eligible_count NOT BETWEEN 1 AND 1024
  OR bundle.expires_at<=clock_timestamp()
  OR NEW.state<>'PENDING' OR NEW.state_generation<>1
  OR NEW.claim_generation<>0 OR NEW.attempt_count<>0
  OR NEW.lease_until IS NOT NULL OR NEW.next_retry_at IS NOT NULL
  OR target.user_id<>NEW.user_id
  OR target.recommendation_request_id<>NEW.recommendation_request_id
  OR target.model_manifest_sha256<>NEW.model_manifest_sha256
  OR target.tokenizer_sha256<>NEW.tokenizer_sha256
  OR target.pipeline_manifest_sha256<>NEW.pipeline_manifest_sha256
  OR target.execution_profile_sha256<>NEW.execution_profile_sha256 THEN
  RAISE EXCEPTION 'sona_work_target_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_sona_work_target BEFORE INSERT ON ml.sona_shadow_work
 FOR EACH ROW EXECUTE FUNCTION app_private.validate_sona_work_target();

CREATE FUNCTION app_private.validate_sona_attempt_publication() RETURNS trigger
 LANGUAGE plpgsql AS $$
DECLARE work_row ml.sona_shadow_work%ROWTYPE;
BEGIN
 SELECT * INTO work_row FROM ml.sona_shadow_work
  WHERE sona_shadow_work_id=NEW.sona_shadow_work_id FOR UPDATE;
 IF NOT FOUND OR work_row.user_id<>NEW.user_id
  OR work_row.recommendation_request_id<>NEW.recommendation_request_id
  OR work_row.state<>NEW.outcome
  OR work_row.claim_generation<>NEW.claim_generation
  OR work_row.attempt_count<>NEW.attempt_no THEN
  RAISE EXCEPTION 'sona_attempt_unbound' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_sona_attempt_validate BEFORE INSERT ON ml.sona_shadow_attempt
 FOR EACH ROW EXECUTE FUNCTION app_private.validate_sona_attempt_publication();

CREATE FUNCTION app_private.require_sona_success_evidence() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state='SUCCEEDED' AND NOT EXISTS(
  SELECT 1 FROM ml.sona_shadow_evidence
  WHERE sona_shadow_work_id=NEW.sona_shadow_work_id) THEN
  RAISE EXCEPTION 'sona_success_evidence_required' USING ERRCODE='23514';
 END IF;
 IF TG_OP='UPDATE' THEN
  IF OLD.state='CLAIMED' AND NOT EXISTS(
   SELECT 1 FROM ml.sona_shadow_attempt
   WHERE sona_shadow_work_id=NEW.sona_shadow_work_id
    AND attempt_no=NEW.attempt_count
    AND claim_generation=NEW.claim_generation
    AND outcome=NEW.state) THEN
   RAISE EXCEPTION 'sona_claim_attempt_required' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER z_sona_success_evidence
 AFTER INSERT OR UPDATE ON ml.sona_shadow_work
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.require_sona_success_evidence();

REVOKE ALL ON ml.sona_capture_bundle,ml.sona_capture_lineage_cursor,
 ml.sona_capture_target_dispatch,ml.sona_shadow_work,ml.sona_shadow_attempt,
 ml.sona_shadow_evidence FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_sona_capture_update() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_sona_capture_state() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.validate_sona_work_target() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.validate_sona_capture_bundle() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.validate_sona_evidence_publication() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.validate_sona_capture_child() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.validate_sona_attempt_publication() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.require_sona_success_evidence() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.sona_capture_bundle)
  OR EXISTS(SELECT 1 FROM ml.sona_shadow_attempt)
  OR EXISTS(SELECT 1 FROM ml.sona_shadow_evidence) THEN
  RAISE EXCEPTION 'refusing Sona capture downgrade with evidence' USING ERRCODE='55000';
 END IF;
END $$;
DROP TABLE ml.sona_shadow_evidence;
DROP TABLE ml.sona_shadow_attempt;
DROP TABLE ml.sona_shadow_work;
DROP TABLE ml.sona_capture_target_dispatch;
DROP TABLE ml.sona_capture_lineage_cursor;
DROP TABLE ml.sona_capture_bundle;
DROP FUNCTION app_private.fence_sona_capture_state();
DROP FUNCTION app_private.validate_sona_work_target();
DROP FUNCTION app_private.validate_sona_capture_bundle();
DROP FUNCTION app_private.validate_sona_evidence_publication();
DROP FUNCTION app_private.validate_sona_capture_child();
DROP FUNCTION app_private.validate_sona_attempt_publication();
DROP FUNCTION app_private.require_sona_success_evidence();
DROP FUNCTION app_private.protect_sona_capture_update();
""")
