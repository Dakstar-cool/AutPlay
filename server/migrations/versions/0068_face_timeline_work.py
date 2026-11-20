"""Reserve dormant Face v2 timeline, coalesced work, and owner sponsor identity.

Revision ID: 0068_face_timeline_work
Revises: 0067_release_watch_auto_download
"""

from alembic import op

revision = "0068_face_timeline_work"
down_revision = "0067_release_watch_auto_download"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.face_timeline (
 face_timeline_id uuid PRIMARY KEY DEFAULT uuidv7(),
 semantic_key_sha256 bytea NOT NULL UNIQUE CHECK(octet_length(semantic_key_sha256)=32),
 result_sha256 bytea NOT NULL CHECK(octet_length(result_sha256)=32),
 identity_document bytea NOT NULL CHECK(octet_length(identity_document) BETWEEN 1 AND 65536),
 source_presentation_map_document bytea NOT NULL
  CHECK(octet_length(source_presentation_map_document) BETWEEN 1 AND 65536),
 source_presentation_map_sha256 bytea NOT NULL
  CHECK(octet_length(source_presentation_map_sha256)=32),
 canonical_timeline bytea NOT NULL
  CHECK(octet_length(canonical_timeline) BETWEEN 1 AND 1048576),
 canonical_byte_size integer NOT NULL
  CHECK(canonical_byte_size BETWEEN 1 AND 1048576),
 recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
 audio_variant_id uuid NOT NULL REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
 source_sha256 bytea NOT NULL CHECK(octet_length(source_sha256)=32),
 decoded_sample_rate integer NOT NULL CHECK(decoded_sample_rate BETWEEN 8000 AND 384000),
 decoded_sample_count bigint NOT NULL CHECK(decoded_sample_count BETWEEN 1 AND 33177600000),
 embedding_model_id uuid NOT NULL
  REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
 face_semantic_interpreter_id uuid NOT NULL
  REFERENCES ml.face_semantic_interpreter(face_semantic_interpreter_id) ON DELETE RESTRICT,
 preprocessing_sha256 bytea NOT NULL CHECK(octet_length(preprocessing_sha256)=32),
 execution_profile_sha256 bytea NOT NULL
  REFERENCES ml.face_execution_profile(execution_profile_sha256) ON DELETE RESTRICT,
 keyframe_count integer NOT NULL CHECK(keyframe_count BETWEEN 0 AND 4096),
 event_count integer NOT NULL CHECK(event_count BETWEEN 0 AND 4096),
 state text NOT NULL DEFAULT 'STAGED' CHECK(state IN ('STAGED','PUBLISHED')),
 state_generation bigint NOT NULL DEFAULT 1 CHECK(state_generation IN (1,2)),
 created_at timestamptz NOT NULL DEFAULT now(),
 published_at timestamptz,
 revoked_at timestamptz,
 retained_until timestamptz,
 CONSTRAINT ck_face_timeline_semantic_digest CHECK(
  semantic_key_sha256=sha256(
   convert_to('autplay.face.semantic-key.v2','UTF8') || decode('00','hex') || identity_document)),
 CONSTRAINT ck_face_timeline_result_digest CHECK(
 result_sha256=sha256(
   convert_to('autplay.face.timeline-result.v2','UTF8')
   || decode('00','hex') || canonical_timeline)),
 CONSTRAINT ck_face_timeline_map_digest CHECK(
  source_presentation_map_sha256=sha256(source_presentation_map_document)),
 CONSTRAINT ck_face_timeline_byte_size CHECK(
  canonical_byte_size=octet_length(canonical_timeline)),
 CONSTRAINT ck_face_timeline_identity_binding CHECK(
  convert_from(identity_document,'UTF8')::jsonb @> jsonb_build_object(
   'recording_id',recording_id::text,
   'audio_variant_id',audio_variant_id::text,
   'source_sha256',encode(source_sha256,'hex'),
   'decoded_sample_rate',decoded_sample_rate,
   'decoded_sample_count',decoded_sample_count,
   'source_presentation_map_sha256',encode(source_presentation_map_sha256,'hex'),
   'embedding_model_id',embedding_model_id::text,
   'semantic_interpreter_id',face_semantic_interpreter_id::text,
   'preprocessing_sha256',encode(preprocessing_sha256,'hex'),
   'execution_profile_sha256',encode(execution_profile_sha256,'hex'))),
 CONSTRAINT ck_face_timeline_body_binding CHECK(
  convert_from(canonical_timeline,'UTF8')::jsonb -> 'identity'
   = convert_from(identity_document,'UTF8')::jsonb
  AND convert_from(canonical_timeline,'UTF8')::jsonb -> 'source_presentation_map'
   = convert_from(source_presentation_map_document,'UTF8')::jsonb),
 CONSTRAINT ck_face_timeline_state_times CHECK(
  (state='STAGED' AND state_generation=1 AND published_at IS NULL AND revoked_at IS NULL)
  OR (state='PUBLISHED' AND state_generation=2 AND published_at IS NOT NULL
      AND revoked_at IS NULL)),
 CONSTRAINT ck_face_timeline_retention CHECK(
  retained_until IS NULL OR retained_until>created_at)
);
CREATE INDEX ix_face_timeline_source_lookup ON ml.face_timeline(
 recording_id,audio_variant_id,source_sha256,embedding_model_id,
 face_semantic_interpreter_id,preprocessing_sha256);
CREATE INDEX ix_face_timeline_retention ON ml.face_timeline(retained_until)
 WHERE retained_until IS NOT NULL;

CREATE TABLE ml.face_analysis_work (
 face_work_id uuid PRIMARY KEY DEFAULT uuidv7(),
 semantic_key_sha256 bytea NOT NULL CHECK(octet_length(semantic_key_sha256)=32),
 identity_document bytea NOT NULL CHECK(octet_length(identity_document) BETWEEN 1 AND 65536),
 lineage_sha256 bytea NOT NULL CHECK(octet_length(lineage_sha256)=32),
 artifact_policy_list_sha256 bytea NOT NULL CHECK(octet_length(artifact_policy_list_sha256)=32),
 recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
 audio_variant_id uuid NOT NULL REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
 source_sha256 bytea NOT NULL CHECK(octet_length(source_sha256)=32),
 decoded_sample_rate integer NOT NULL CHECK(decoded_sample_rate BETWEEN 8000 AND 384000),
 decoded_sample_count bigint NOT NULL CHECK(decoded_sample_count BETWEEN 1 AND 33177600000),
 source_presentation_map_sha256 bytea NOT NULL
  CHECK(octet_length(source_presentation_map_sha256)=32),
 embedding_model_id uuid NOT NULL
  REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
 face_semantic_interpreter_id uuid NOT NULL
  REFERENCES ml.face_semantic_interpreter(face_semantic_interpreter_id) ON DELETE RESTRICT,
 preprocessing_sha256 bytea NOT NULL CHECK(octet_length(preprocessing_sha256)=32),
 execution_profile_sha256 bytea NOT NULL
  REFERENCES ml.face_execution_profile(execution_profile_sha256) ON DELETE RESTRICT,
 state text NOT NULL DEFAULT 'PENDING' CHECK(state IN (
  'PENDING','CLAIMED','RETRY_WAIT','SUCCEEDED','TERMINAL_INELIGIBLE',
  'TERMINAL_FAILED','RETRY_EXHAUSTED','CANCELLED','SUPERSEDED')),
 work_authority_generation bigint NOT NULL DEFAULT 1 CHECK(work_authority_generation>=1),
 live_sponsor_count bigint NOT NULL DEFAULT 0 CHECK(live_sponsor_count>=0),
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 8),
 claim_generation bigint NOT NULL DEFAULT 0 CHECK(claim_generation BETWEEN 0 AND 9007199254740990),
 claim_token uuid,
 claimed_authority_generation bigint,
 lease_expires_at timestamptz,
 next_retry_at timestamptz,
 last_reason text CHECK(last_reason ~ '^[A-Z][A-Z0-9_]{0,63}$'),
 state_changed_at timestamptz NOT NULL DEFAULT now(),
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT ck_face_work_claim_shape CHECK(
  (state='CLAIMED' AND claim_token IS NOT NULL
   AND claimed_authority_generation IS NOT NULL AND lease_expires_at IS NOT NULL
   AND next_retry_at IS NULL AND attempt_count>0)
  OR (state<>'CLAIMED' AND claim_token IS NULL
   AND claimed_authority_generation IS NULL AND lease_expires_at IS NULL)),
 CONSTRAINT ck_face_work_retry_shape CHECK(
  (state='RETRY_WAIT' AND next_retry_at IS NOT NULL AND last_reason IS NOT NULL)
  OR (state<>'RETRY_WAIT' AND next_retry_at IS NULL)),
 CONSTRAINT ck_face_work_initial_shape CHECK(
  state<>'PENDING' OR attempt_count>0 OR
  (claim_generation=0 AND last_reason IS NULL)),
 CONSTRAINT uq_face_work_exact_key UNIQUE(
  recording_id,audio_variant_id,source_sha256,lineage_sha256),
 CONSTRAINT ck_face_work_semantic_digest CHECK(
  semantic_key_sha256=sha256(convert_to('autplay.face.semantic-key.v2','UTF8')
   || decode('00','hex') || identity_document)),
 CONSTRAINT ck_face_work_identity_binding CHECK(
  convert_from(identity_document,'UTF8')::jsonb @> jsonb_build_object(
   'recording_id',recording_id::text,'audio_variant_id',audio_variant_id::text,
   'source_sha256',encode(source_sha256,'hex'),
   'decoded_sample_rate',decoded_sample_rate,
   'decoded_sample_count',decoded_sample_count,
   'source_presentation_map_sha256',encode(source_presentation_map_sha256,'hex'),
   'embedding_model_id',embedding_model_id::text,
   'semantic_interpreter_id',face_semantic_interpreter_id::text,
   'preprocessing_sha256',encode(preprocessing_sha256,'hex'),
   'execution_profile_sha256',encode(execution_profile_sha256,'hex')))
);
CREATE INDEX ix_face_work_source ON ml.face_analysis_work(
 recording_id,audio_variant_id,source_sha256,state);
CREATE INDEX ix_face_work_claimable ON ml.face_analysis_work(created_at,face_work_id)
 WHERE state='PENDING' AND live_sponsor_count>0;
CREATE INDEX ix_face_work_expired_lease ON ml.face_analysis_work(lease_expires_at,face_work_id)
 WHERE state='CLAIMED';
CREATE INDEX ix_face_work_retry_due ON ml.face_analysis_work(next_retry_at,face_work_id)
 WHERE state='RETRY_WAIT';

CREATE TABLE ml.face_analysis_sponsor (
 face_sponsor_id uuid PRIMARY KEY DEFAULT uuidv7(),
 face_work_id uuid NOT NULL REFERENCES ml.face_analysis_work(face_work_id) ON DELETE RESTRICT,
 owner_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 user_track_ref_id uuid NOT NULL
  REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
 recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
 audio_variant_id uuid NOT NULL REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
 source_sha256 bytea NOT NULL CHECK(octet_length(source_sha256)=32),
 lineage_sha256 bytea NOT NULL CHECK(octet_length(lineage_sha256)=32),
 artifact_policy_list_sha256 bytea NOT NULL
  CHECK(octet_length(artifact_policy_list_sha256)=32),
 source_generation bigint NOT NULL CHECK(source_generation BETWEEN 1 AND 9007199254740990),
 policy_generation bigint NOT NULL CHECK(policy_generation BETWEEN 1 AND 9007199254740990),
 activation_epoch bigint NOT NULL CHECK(activation_epoch BETWEEN 1 AND 9007199254740990),
 redirect_generation bigint NOT NULL CHECK(redirect_generation BETWEEN 0 AND 9007199254740990),
 sponsor_generation bigint NOT NULL DEFAULT 1 CHECK(sponsor_generation IN (1,2)),
 predecessor_id uuid REFERENCES ml.face_analysis_sponsor(face_sponsor_id) ON DELETE RESTRICT,
 successor_id uuid REFERENCES ml.face_analysis_sponsor(face_sponsor_id)
  ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
 state text NOT NULL DEFAULT 'LIVE' CHECK(state IN ('LIVE','WITHDRAWN','SUPERSEDED')),
 created_at timestamptz NOT NULL DEFAULT now(),
 retired_at timestamptz,
 CONSTRAINT uq_face_sponsor_predecessor UNIQUE(predecessor_id),
 CONSTRAINT uq_face_sponsor_successor UNIQUE(successor_id),
 CONSTRAINT ck_face_sponsor_link CHECK(
  face_sponsor_id IS DISTINCT FROM predecessor_id
  AND face_sponsor_id IS DISTINCT FROM successor_id
  AND ((state='LIVE' AND successor_id IS NULL AND retired_at IS NULL)
   OR (state='WITHDRAWN' AND successor_id IS NULL AND retired_at IS NOT NULL)
   OR (state='SUPERSEDED' AND successor_id IS NOT NULL AND retired_at IS NOT NULL)))
);
CREATE UNIQUE INDEX uq_face_sponsor_live_ref
 ON ml.face_analysis_sponsor(
  owner_user_id,user_track_ref_id,policy_generation,lineage_sha256) WHERE state='LIVE';
CREATE INDEX ix_face_sponsor_work_live
 ON ml.face_analysis_sponsor(face_work_id,face_sponsor_id) WHERE state='LIVE';
CREATE INDEX ix_face_sponsor_owner_history
 ON ml.face_analysis_sponsor(owner_user_id,user_track_ref_id,created_at);

CREATE TABLE ml.face_projection_fanout (
 face_projection_fanout_id uuid PRIMARY KEY DEFAULT uuidv7(),
 face_timeline_id uuid NOT NULL REFERENCES ml.face_timeline(face_timeline_id) ON DELETE RESTRICT,
 face_work_id uuid NOT NULL REFERENCES ml.face_analysis_work(face_work_id) ON DELETE RESTRICT,
 work_authority_generation bigint NOT NULL CHECK(work_authority_generation>=1),
 claim_token uuid NOT NULL,
 claim_generation bigint NOT NULL CHECK(claim_generation BETWEEN 1 AND 9007199254740990),
 sponsor_high_watermark uuid NOT NULL,
 sponsor_cursor uuid,
 verification_cursor uuid,
 batch_limit integer NOT NULL DEFAULT 100 CHECK(batch_limit=100),
 discovered_count bigint NOT NULL DEFAULT 0 CHECK(discovered_count>=0),
 cancelled_count bigint NOT NULL DEFAULT 0 CHECK(cancelled_count>=0),
 state text NOT NULL DEFAULT 'PENDING'
  CHECK(state IN ('PENDING','SCANNING','VERIFYING','COMPLETE')),
 lease_token uuid,
 lease_expires_at timestamptz,
 restart_count integer NOT NULL DEFAULT 0 CHECK(restart_count>=0),
 created_at timestamptz NOT NULL DEFAULT now(),
 completed_at timestamptz,
 CONSTRAINT uq_face_fanout_work UNIQUE(face_work_id),
 CONSTRAINT uq_face_fanout_timeline_authority
  UNIQUE(face_timeline_id,work_authority_generation),
 CONSTRAINT ck_face_fanout_cursor CHECK(
  (sponsor_cursor IS NULL OR sponsor_cursor<=sponsor_high_watermark)
  AND (verification_cursor IS NULL OR verification_cursor<=sponsor_high_watermark)),
 CONSTRAINT ck_face_fanout_initial_shape CHECK(
  state<>'PENDING' OR (sponsor_cursor IS NULL AND verification_cursor IS NULL
  AND discovered_count=0 AND cancelled_count=0 AND lease_token IS NULL
  AND lease_expires_at IS NULL AND restart_count=0 AND completed_at IS NULL))
);
CREATE INDEX ix_face_fanout_pending ON ml.face_projection_fanout(created_at,face_projection_fanout_id)
 WHERE state='PENDING';

CREATE TABLE ml.face_projection_intent (
 face_projection_intent_id uuid PRIMARY KEY DEFAULT uuidv7(),
 face_projection_fanout_id uuid NOT NULL REFERENCES ml.face_projection_fanout(face_projection_fanout_id) ON DELETE RESTRICT,
 face_timeline_id uuid NOT NULL REFERENCES ml.face_timeline(face_timeline_id) ON DELETE RESTRICT,
 face_work_id uuid NOT NULL REFERENCES ml.face_analysis_work(face_work_id) ON DELETE RESTRICT,
 face_sponsor_id uuid NOT NULL REFERENCES ml.face_analysis_sponsor(face_sponsor_id) ON DELETE RESTRICT,
 sponsor_generation bigint NOT NULL CHECK(sponsor_generation IN (1,2)),
 source_generation bigint NOT NULL CHECK(source_generation>=1),
 policy_generation bigint NOT NULL CHECK(policy_generation>=1),
 activation_epoch bigint NOT NULL CHECK(activation_epoch>=1),
 redirect_generation bigint NOT NULL CHECK(redirect_generation>=0),
 work_authority_generation bigint NOT NULL CHECK(work_authority_generation>=1),
 outcome text NOT NULL CHECK(outcome IN ('INTENT','CANCELLED')),
 reason text,
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_face_projection_intent_sponsor UNIQUE(face_projection_fanout_id,face_sponsor_id),
 CONSTRAINT ck_face_projection_intent_reason CHECK(
  (outcome='INTENT' AND reason IS NULL AND sponsor_generation=1)
  OR (outcome='CANCELLED' AND reason ~ '^[A-Z][A-Z0-9_]{0,63}$'))
);
CREATE INDEX ix_face_projection_intent_pending
 ON ml.face_projection_intent(face_projection_fanout_id,face_sponsor_id)
 WHERE outcome='INTENT';

CREATE TABLE ml.face_projection_materialization_cursor (
 cursor_id integer PRIMARY KEY DEFAULT 1 CHECK(cursor_id=1),
 last_intent_id uuid,
 batch_limit integer NOT NULL DEFAULT 100 CHECK(batch_limit=100),
 applied_count bigint NOT NULL DEFAULT 0 CHECK(applied_count>=0),
 tombstoned_count bigint NOT NULL DEFAULT 0 CHECK(tombstoned_count>=0),
 updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO ml.face_projection_materialization_cursor(cursor_id) VALUES(1);

CREATE TABLE ml.face_projection (
 face_projection_id uuid PRIMARY KEY DEFAULT uuidv7(),
 face_sponsor_id uuid NOT NULL UNIQUE REFERENCES ml.face_analysis_sponsor(face_sponsor_id) ON DELETE RESTRICT,
 face_timeline_id uuid NOT NULL REFERENCES ml.face_timeline(face_timeline_id) ON DELETE RESTRICT,
 owner_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 user_track_ref_id uuid NOT NULL REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
 source_generation bigint NOT NULL CHECK(source_generation>=1),
 policy_generation bigint NOT NULL CHECK(policy_generation>=1),
 activation_epoch bigint NOT NULL CHECK(activation_epoch>=1),
 redirect_generation bigint NOT NULL CHECK(redirect_generation>=0),
 work_authority_generation bigint NOT NULL CHECK(work_authority_generation>=1),
 projection_generation bigint NOT NULL DEFAULT 1 CHECK(projection_generation IN (1,2)),
 state text NOT NULL CHECK(state IN ('ACTIVE','TOMBSTONED')),
 reason text CHECK(reason IS NULL OR reason ~ '^[A-Z][A-Z0-9_]{0,63}$'),
 last_intent_id uuid NOT NULL REFERENCES ml.face_projection_intent(face_projection_intent_id) ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT now(),
 tombstoned_at timestamptz,
 CONSTRAINT ck_face_projection_state CHECK(
  (state='ACTIVE' AND projection_generation=1 AND reason IS NULL AND tombstoned_at IS NULL)
  OR (state='TOMBSTONED' AND reason IS NOT NULL AND tombstoned_at IS NOT NULL))
);
CREATE INDEX ix_face_projection_owner_state
 ON ml.face_projection(owner_user_id,user_track_ref_id) WHERE state='ACTIVE';

CREATE FUNCTION app_private.fence_face_projection_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND OLD.state='ACTIVE' AND NEW.state='TOMBSTONED'
  AND NEW.projection_generation=2
  AND (to_jsonb(NEW)-ARRAY['state','projection_generation','reason','tombstoned_at'])
   IS NOT DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['state','projection_generation','reason','tombstoned_at'])
 THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'face_projection_mutation_closed' USING ERRCODE='55000';
END $$;
CREATE TRIGGER z_face_projection_mutation BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_projection FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_projection_mutation();

CREATE TABLE ml.face_projection_applied_receipt (
 face_projection_intent_id uuid PRIMARY KEY REFERENCES ml.face_projection_intent(face_projection_intent_id) ON DELETE RESTRICT,
 face_projection_id uuid NOT NULL REFERENCES ml.face_projection(face_projection_id) ON DELETE RESTRICT,
 outcome text NOT NULL CHECK(outcome IN ('APPLIED','TOMBSTONED')),
 reason text CHECK(reason IS NULL OR reason ~ '^[A-Z][A-Z0-9_]{0,63}$'),
 applied_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT ck_face_projection_receipt_outcome CHECK(
  (outcome='APPLIED' AND reason IS NULL) OR (outcome='TOMBSTONED' AND reason IS NOT NULL))
);
CREATE INDEX ix_face_projection_intent_unapplied
 ON ml.face_projection_intent(created_at,face_projection_intent_id);

CREATE FUNCTION app_private.fence_face_projection_receipt() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' AND EXISTS(
  SELECT 1 FROM ml.face_projection p JOIN ml.face_projection_intent i
   ON i.face_projection_intent_id=NEW.face_projection_intent_id
  WHERE p.face_projection_id=NEW.face_projection_id
   AND p.face_sponsor_id=i.face_sponsor_id
   AND p.face_timeline_id=i.face_timeline_id
   AND ((NEW.outcome='APPLIED' AND p.state='ACTIVE')
    OR (NEW.outcome='TOMBSTONED' AND p.state='TOMBSTONED'))
 ) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'face_projection_receipt_immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER z_face_projection_receipt_mutation BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_projection_applied_receipt FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_projection_receipt();

CREATE FUNCTION app_private.tombstone_retired_face_projection() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state='LIVE' AND NEW.state<>'LIVE' THEN
  UPDATE ml.face_projection SET state='TOMBSTONED',projection_generation=2,
   reason='SPONSOR_RETIRED',tombstoned_at=clock_timestamp()
  WHERE face_sponsor_id=NEW.face_sponsor_id AND state='ACTIVE';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_face_projection_sponsor_retired AFTER UPDATE ON ml.face_analysis_sponsor
 FOR EACH ROW EXECUTE FUNCTION app_private.tombstone_retired_face_projection();

CREATE FUNCTION app_private.fence_face_work_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE now_at timestamptz := clock_timestamp();
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'PENDING' OR NEW.work_authority_generation<>1
   OR NEW.live_sponsor_count<>0 OR NEW.attempt_count<>0
   OR NEW.claim_generation<>0 OR NEW.claim_token IS NOT NULL
   OR NEW.claimed_authority_generation IS NOT NULL
   OR NEW.lease_expires_at IS NOT NULL OR NEW.next_retry_at IS NOT NULL
   OR NEW.last_reason IS NOT NULL THEN
   RAISE EXCEPTION 'face_work_insert_invalid' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'face_work_immutable' USING ERRCODE='55000';
 END IF;
 IF pg_trigger_depth()=2 THEN
  IF (to_jsonb(NEW) - ARRAY['work_authority_generation','live_sponsor_count'])
    IS DISTINCT FROM
    (to_jsonb(OLD) - ARRAY['work_authority_generation','live_sponsor_count'])
   OR NEW.work_authority_generation<>OLD.work_authority_generation+1
   OR NEW.live_sponsor_count NOT IN
    (OLD.live_sponsor_count-1,OLD.live_sponsor_count+1) THEN
   RAISE EXCEPTION 'face_work_authority_mutation_invalid' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 IF pg_trigger_depth()<>1
  OR (to_jsonb(NEW) - ARRAY['state','attempt_count','claim_generation',
        'claim_token','claimed_authority_generation','lease_expires_at',
        'next_retry_at','last_reason','state_changed_at'])
   IS DISTINCT FROM
   (to_jsonb(OLD) - ARRAY['state','attempt_count','claim_generation',
        'claim_token','claimed_authority_generation','lease_expires_at',
        'next_retry_at','last_reason','state_changed_at'])
  OR NEW.state_changed_at<OLD.state_changed_at
  OR NEW.attempt_count<OLD.attempt_count
  OR NEW.claim_generation<OLD.claim_generation THEN
  RAISE EXCEPTION 'face_work_immutable' USING ERRCODE='55000';
 END IF;
 IF OLD.state='PENDING' AND NEW.state='CLAIMED' THEN
  IF OLD.live_sponsor_count<1 OR OLD.attempt_count>=8
   OR NEW.attempt_count<>OLD.attempt_count+1
   OR NEW.claim_generation<>OLD.claim_generation+1
   OR NEW.claimed_authority_generation<>OLD.work_authority_generation
   OR NEW.lease_expires_at<=now_at
   OR NEW.lease_expires_at>now_at+interval '15 minutes'
   OR NEW.last_reason IS DISTINCT FROM OLD.last_reason THEN
   RAISE EXCEPTION 'face_work_claim_invalid' USING ERRCODE='55000';
  END IF;
 ELSIF OLD.state='CLAIMED' AND NEW.state IN (
  'SUCCEEDED','RETRY_WAIT','TERMINAL_INELIGIBLE','TERMINAL_FAILED',
  'RETRY_EXHAUSTED','CANCELLED','SUPERSEDED') THEN
  IF NEW.attempt_count<>OLD.attempt_count
   OR NEW.claim_generation<>OLD.claim_generation
   OR (NEW.state='SUCCEEDED' AND (OLD.lease_expires_at<=now_at
      OR OLD.claimed_authority_generation<>OLD.work_authority_generation
      OR OLD.live_sponsor_count<1 OR NEW.last_reason IS NOT NULL))
   OR (NEW.state<>'SUCCEEDED' AND NEW.last_reason IS NULL)
   OR (NEW.state='RETRY_WAIT' AND
       (NEW.next_retry_at<=now_at OR NEW.next_retry_at>now_at+interval '24 hours')) THEN
   RAISE EXCEPTION 'face_work_completion_invalid' USING ERRCODE='55000';
  END IF;
 ELSIF OLD.state='RETRY_WAIT' AND NEW.state='PENDING' THEN
  IF OLD.next_retry_at>now_at OR NEW.attempt_count<>OLD.attempt_count
   OR NEW.claim_generation<>OLD.claim_generation
   OR NEW.last_reason IS DISTINCT FROM OLD.last_reason THEN
   RAISE EXCEPTION 'face_work_retry_invalid' USING ERRCODE='55000';
  END IF;
 ELSE
  RAISE EXCEPTION 'face_work_transition_invalid' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_work_mutation BEFORE INSERT OR UPDATE OR DELETE ON ml.face_analysis_work
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_work_mutation();

CREATE FUNCTION app_private.fence_face_sponsor_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE work_row ml.face_analysis_work%ROWTYPE;
DECLARE prior_row ml.face_analysis_sponsor%ROWTYPE;
DECLARE reference_owner uuid;
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'face_sponsor_immutable' USING ERRCODE='55000';
 END IF;
 SELECT * INTO work_row FROM ml.face_analysis_work
  WHERE face_work_id=NEW.face_work_id FOR UPDATE;
 IF NOT FOUND
  OR ROW(NEW.recording_id,NEW.audio_variant_id,NEW.source_sha256,
         NEW.lineage_sha256,NEW.artifact_policy_list_sha256)
   IS DISTINCT FROM
     ROW(work_row.recording_id,work_row.audio_variant_id,work_row.source_sha256,
         work_row.lineage_sha256,work_row.artifact_policy_list_sha256) THEN
  RAISE EXCEPTION 'face_sponsor_work_mismatch' USING ERRCODE='55000';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'LIVE' OR NEW.sponsor_generation<>1
   OR NEW.successor_id IS NOT NULL OR NEW.retired_at IS NOT NULL THEN
   RAISE EXCEPTION 'face_sponsor_insert_state' USING ERRCODE='55000';
  END IF;
  SELECT user_id INTO reference_owner FROM library.user_track_ref
   WHERE user_track_ref_id=NEW.user_track_ref_id FOR UPDATE;
  IF reference_owner IS DISTINCT FROM NEW.owner_user_id THEN
   RAISE EXCEPTION 'face_sponsor_owner_mismatch' USING ERRCODE='55000';
  END IF;
  IF NEW.predecessor_id IS NULL THEN
   IF EXISTS(SELECT 1 FROM ml.face_analysis_sponsor
    WHERE owner_user_id=NEW.owner_user_id AND user_track_ref_id=NEW.user_track_ref_id
      AND policy_generation=NEW.policy_generation
      AND lineage_sha256=NEW.lineage_sha256) THEN
    RAISE EXCEPTION 'face_sponsor_predecessor_required' USING ERRCODE='55000';
   END IF;
  ELSE
   SELECT * INTO prior_row FROM ml.face_analysis_sponsor
    WHERE face_sponsor_id=NEW.predecessor_id FOR UPDATE;
   IF NOT FOUND OR prior_row.state<>'SUPERSEDED'
    OR prior_row.successor_id IS DISTINCT FROM NEW.face_sponsor_id
    OR prior_row.owner_user_id<>NEW.owner_user_id
    OR prior_row.user_track_ref_id<>NEW.user_track_ref_id
    OR NEW.source_generation<prior_row.source_generation
    OR NEW.policy_generation<prior_row.policy_generation
    OR NEW.activation_epoch<prior_row.activation_epoch
    OR NEW.redirect_generation<prior_row.redirect_generation
    OR ((NEW.source_sha256 IS DISTINCT FROM prior_row.source_sha256
         OR NEW.audio_variant_id IS DISTINCT FROM prior_row.audio_variant_id)
        AND NEW.source_generation<=prior_row.source_generation)
    OR ROW(NEW.face_work_id,NEW.source_generation,NEW.policy_generation,
           NEW.activation_epoch,NEW.redirect_generation)
       IS NOT DISTINCT FROM
       ROW(prior_row.face_work_id,prior_row.source_generation,prior_row.policy_generation,
           prior_row.activation_epoch,prior_row.redirect_generation) THEN
    RAISE EXCEPTION 'face_sponsor_successor_invalid' USING ERRCODE='55000';
   END IF;
  END IF;
 ELSE
  IF OLD.state<>'LIVE' OR NEW.state NOT IN ('WITHDRAWN','SUPERSEDED')
   OR (to_jsonb(NEW) - ARRAY['state','successor_id','retired_at','sponsor_generation'])
      IS DISTINCT FROM
      (to_jsonb(OLD) - ARRAY['state','successor_id','retired_at','sponsor_generation'])
   OR OLD.sponsor_generation<>1 OR NEW.sponsor_generation<>2
   OR (NEW.state='SUPERSEDED' AND NEW.successor_id IS NULL)
   OR (NEW.state='WITHDRAWN' AND NEW.successor_id IS NOT NULL)
   OR NEW.retired_at IS NULL OR NEW.retired_at<OLD.created_at THEN
   RAISE EXCEPTION 'face_sponsor_transition_invalid' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_sponsor_mutation BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_analysis_sponsor FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_sponsor_mutation();
CREATE FUNCTION app_private.advance_face_work_authority() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 UPDATE ml.face_analysis_work SET
  work_authority_generation=work_authority_generation+1,
  live_sponsor_count=live_sponsor_count+CASE WHEN TG_OP='INSERT' THEN 1 ELSE -1 END
  WHERE face_work_id=NEW.face_work_id;
 RETURN NULL;
END $$;
CREATE TRIGGER z_face_sponsor_authority AFTER INSERT OR UPDATE ON ml.face_analysis_sponsor
 FOR EACH ROW EXECUTE FUNCTION app_private.advance_face_work_authority();
CREATE FUNCTION app_private.verify_face_sponsor_link() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS (
  SELECT 1 FROM ml.face_analysis_sponsor s
  LEFT JOIN ml.face_analysis_sponsor successor ON successor.face_sponsor_id=s.successor_id
  LEFT JOIN ml.face_analysis_sponsor predecessor ON predecessor.face_sponsor_id=s.predecessor_id
  WHERE s.face_sponsor_id=NEW.face_sponsor_id
    AND ((s.state='SUPERSEDED' AND (
      successor.predecessor_id IS DISTINCT FROM s.face_sponsor_id
      OR successor.owner_user_id IS DISTINCT FROM s.owner_user_id
      OR successor.user_track_ref_id IS DISTINCT FROM s.user_track_ref_id))
     OR (s.predecessor_id IS NOT NULL AND (
      predecessor.successor_id IS DISTINCT FROM s.face_sponsor_id
      OR predecessor.state IS DISTINCT FROM 'SUPERSEDED')))
 ) THEN
  RAISE EXCEPTION 'face_sponsor_link_not_reciprocal' USING ERRCODE='55000';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER z_face_sponsor_link AFTER INSERT OR UPDATE
 ON ml.face_analysis_sponsor DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.verify_face_sponsor_link();

CREATE FUNCTION app_private.fence_face_timeline_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND OLD.state='STAGED' AND NEW.state='PUBLISHED'
  AND NEW.state_generation=2 AND NEW.published_at IS NOT NULL
  AND NEW.revoked_at IS NULL
  AND (to_jsonb(NEW) - ARRAY['state','state_generation','published_at'])
   IS NOT DISTINCT FROM
   (to_jsonb(OLD) - ARRAY['state','state_generation','published_at']) THEN
  RETURN NEW;
 END IF;
 RAISE EXCEPTION 'face_timeline_immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER z_face_timeline_update BEFORE UPDATE ON ml.face_timeline
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_timeline_mutation();
CREATE TRIGGER z_face_timeline_delete BEFORE DELETE ON ml.face_timeline
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_timeline_mutation();

CREATE FUNCTION app_private.fence_face_fanout_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NOT EXISTS (
   SELECT 1 FROM ml.face_timeline t JOIN ml.face_analysis_work w
    ON w.semantic_key_sha256=t.semantic_key_sha256
   WHERE t.face_timeline_id=NEW.face_timeline_id AND t.state='PUBLISHED'
    AND w.face_work_id=NEW.face_work_id AND w.state='SUCCEEDED'
    AND w.work_authority_generation=NEW.work_authority_generation
    AND w.claim_generation=NEW.claim_generation
    AND NEW.sponsor_high_watermark=(
      SELECT s.face_sponsor_id FROM ml.face_analysis_sponsor s
      WHERE s.face_work_id=w.face_work_id AND s.state='LIVE'
      ORDER BY s.face_sponsor_id DESC LIMIT 1)) THEN
   RAISE EXCEPTION 'face_fanout_publication_mismatch' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='UPDATE' AND
  (to_jsonb(NEW) - ARRAY['state','sponsor_cursor','verification_cursor',
    'discovered_count','cancelled_count','lease_token','lease_expires_at',
    'restart_count','completed_at']) IS NOT DISTINCT FROM
  (to_jsonb(OLD) - ARRAY['state','sponsor_cursor','verification_cursor',
    'discovered_count','cancelled_count','lease_token','lease_expires_at',
    'restart_count','completed_at'])
  AND ((OLD.state='PENDING' AND NEW.state='SCANNING')
    OR (OLD.state='SCANNING' AND NEW.state IN ('SCANNING','VERIFYING'))
    OR (OLD.state='VERIFYING' AND NEW.state IN ('VERIFYING','COMPLETE')))
  AND NEW.discovered_count>=OLD.discovered_count
  AND NEW.cancelled_count>=OLD.cancelled_count
  AND NEW.restart_count>=OLD.restart_count
  AND (OLD.sponsor_cursor IS NULL OR NEW.sponsor_cursor>=OLD.sponsor_cursor)
  AND (OLD.verification_cursor IS NULL OR NEW.verification_cursor>=OLD.verification_cursor)
  AND (NEW.state<>'COMPLETE' OR (NEW.completed_at IS NOT NULL
    AND NEW.lease_token IS NULL AND NEW.lease_expires_at IS NULL))
  AND (NEW.state='COMPLETE' OR (NEW.lease_token IS NOT NULL
    AND NEW.lease_expires_at>clock_timestamp())) THEN
  RETURN NEW;
 END IF;
 RAISE EXCEPTION 'face_fanout_mutation_closed' USING ERRCODE='55000';
END $$;
CREATE TRIGGER z_face_fanout_mutation BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_projection_fanout FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_fanout_mutation();
CREATE FUNCTION app_private.fence_face_projection_intent() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' AND EXISTS (
  SELECT 1 FROM ml.face_projection_fanout f
  JOIN ml.face_analysis_sponsor s ON s.face_sponsor_id=NEW.face_sponsor_id
  WHERE f.face_projection_fanout_id=NEW.face_projection_fanout_id
   AND f.face_timeline_id=NEW.face_timeline_id
   AND f.face_work_id=NEW.face_work_id
   AND s.face_work_id=NEW.face_work_id
   AND s.sponsor_generation=NEW.sponsor_generation
   AND s.source_generation=NEW.source_generation
   AND s.policy_generation=NEW.policy_generation
   AND s.activation_epoch=NEW.activation_epoch
   AND s.redirect_generation=NEW.redirect_generation
 ) THEN
  RETURN NEW;
 END IF;
 RAISE EXCEPTION 'face_projection_intent_immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER z_face_projection_intent_mutation BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_projection_intent FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_projection_intent();

REVOKE ALL ON ml.face_timeline FROM PUBLIC;
REVOKE ALL ON ml.face_analysis_work,ml.face_analysis_sponsor FROM PUBLIC;
REVOKE ALL ON ml.face_projection_fanout FROM PUBLIC;
REVOKE ALL ON ml.face_projection_intent FROM PUBLIC;
REVOKE ALL ON ml.face_projection_materialization_cursor,ml.face_projection,
 ml.face_projection_applied_receipt FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_timeline_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_work_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_sponsor_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.advance_face_work_authority() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.verify_face_sponsor_link() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_fanout_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_projection_intent() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_projection_receipt() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_projection_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.tombstone_retired_face_projection() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_timeline)
 OR EXISTS(SELECT 1 FROM ml.face_analysis_work)
  OR EXISTS(SELECT 1 FROM ml.face_analysis_sponsor)
  OR EXISTS(SELECT 1 FROM ml.face_projection_fanout)
  OR EXISTS(SELECT 1 FROM ml.face_projection_intent)
  OR EXISTS(SELECT 1 FROM ml.face_projection)
  OR EXISTS(SELECT 1 FROM ml.face_projection_applied_receipt) THEN
  RAISE EXCEPTION 'refusing Face timeline downgrade with evidence' USING ERRCODE='55000';
 END IF;
END $$;
DROP TABLE ml.face_projection_applied_receipt;
DROP TABLE ml.face_projection;
DROP TABLE ml.face_projection_materialization_cursor;
DROP TABLE ml.face_projection_intent;
DROP TABLE ml.face_projection_fanout;
DROP TABLE ml.face_timeline;
DROP TRIGGER zz_face_projection_sponsor_retired ON ml.face_analysis_sponsor;
DROP TABLE ml.face_analysis_sponsor;
DROP TABLE ml.face_analysis_work;
DROP FUNCTION app_private.fence_face_fanout_mutation();
DROP FUNCTION app_private.fence_face_projection_intent();
DROP FUNCTION app_private.fence_face_projection_receipt();
DROP FUNCTION app_private.fence_face_projection_mutation();
DROP FUNCTION app_private.tombstone_retired_face_projection();
DROP FUNCTION app_private.fence_face_timeline_mutation();
DROP FUNCTION app_private.advance_face_work_authority();
DROP FUNCTION app_private.verify_face_sponsor_link();
DROP FUNCTION app_private.fence_face_sponsor_mutation();
DROP FUNCTION app_private.fence_face_work_mutation();
""")
