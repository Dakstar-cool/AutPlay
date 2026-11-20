"""Dormant durable bounded canonical Face source replacement.

Revision ID: 0079_face_source_replacement
Revises: 0078_face_ref_lease
"""

from alembic import op

revision = "0079_face_source_replacement"
down_revision = "0078_face_ref_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.face_source_replacement (
 face_source_replacement_id uuid PRIMARY KEY,
 recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
 expected_audio_variant_id uuid NOT NULL REFERENCES vault.audio_variant(audio_variant_id) ON
DELETE RESTRICT,
 audio_variant_id uuid NOT NULL REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
 expected_source_sha256 bytea NOT NULL,
 source_sha256 bytea NOT NULL,
 replacement_generation bigint NOT NULL,
 request_sha256 bytea NOT NULL,
 state text NOT NULL DEFAULT 'PENDING',
 retired_count bigint NOT NULL DEFAULT 0,
 successor_count bigint NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 CONSTRAINT uq_face_source_replacement_generation UNIQUE(recording_id,replacement_generation),
 CONSTRAINT ck_face_source_replacement_source CHECK(
   expected_audio_variant_id<>audio_variant_id AND octet_length(expected_source_sha256)=32
   AND octet_length(source_sha256)=32 AND octet_length(request_sha256)=32),
 CONSTRAINT ck_face_source_replacement_generation CHECK(replacement_generation BETWEEN 1 AND
9007199254740990),
 CONSTRAINT ck_face_source_replacement_counts CHECK(retired_count>=0 AND successor_count BETWEEN
0 AND retired_count),
 CONSTRAINT ck_face_source_replacement_completion CHECK(
   (state='PENDING' AND completed_at IS NULL) OR (state='COMPLETE' AND completed_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_face_source_replacement_pending
 ON ml.face_source_replacement(recording_id) WHERE state='PENDING';
CREATE INDEX ix_face_source_replacement_pending
 ON ml.face_source_replacement(created_at,face_source_replacement_id) WHERE state='PENDING';
CREATE TABLE ml.face_source_replacement_candidate (
 face_source_replacement_id uuid NOT NULL REFERENCES
ml.face_source_replacement(face_source_replacement_id) ON DELETE RESTRICT,
 predecessor_sponsor_id uuid NOT NULL REFERENCES ml.face_analysis_sponsor(face_sponsor_id) ON
DELETE RESTRICT,
 candidate_document bytea NOT NULL,
 candidate_sha256 bytea NOT NULL,
 state text NOT NULL DEFAULT 'PENDING',
 successor_sponsor_id uuid REFERENCES ml.face_analysis_sponsor(face_sponsor_id) ON DELETE RESTRICT,
 reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 PRIMARY KEY(face_source_replacement_id,predecessor_sponsor_id),
 CONSTRAINT ck_face_replacement_candidate_bytes CHECK(
   octet_length(candidate_document) BETWEEN 1 AND 131072 AND
candidate_sha256=sha256(candidate_document)),
 CONSTRAINT ck_face_replacement_candidate_completion CHECK(
   (state='PENDING' AND successor_sponsor_id IS NULL AND reason IS NULL AND completed_at IS NULL)
   OR (state='APPLIED' AND successor_sponsor_id IS NOT NULL AND reason IS NULL AND completed_at
IS NOT NULL)
   OR (state='CANCELLED' AND successor_sponsor_id IS NULL
     AND reason IS NOT NULL AND reason ~ '^[A-Z][A-Z0-9_]{0,63}$'
     AND completed_at IS NOT NULL))
);
CREATE INDEX ix_face_replacement_candidate_pending
 ON ml.face_source_replacement_candidate(face_source_replacement_id,predecessor_sponsor_id)
 WHERE state='PENDING';
CREATE FUNCTION app_private.fence_face_source_replacement() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE root ml.face_source_replacement%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'face_replacement_immutable' USING ERRCODE='55000';
 END IF;
 IF TG_TABLE_NAME='face_source_replacement' THEN
  IF TG_OP='INSERT' THEN
   IF NEW.state<>'PENDING' OR NEW.retired_count<>0 OR NEW.successor_count<>0
    OR NEW.completed_at IS NOT NULL OR NOT EXISTS(
      SELECT 1 FROM vault.recording_canonical_variant c
      JOIN vault.audio_variant v ON v.audio_variant_id=c.audio_variant_id
        AND v.recording_id=c.recording_id
      JOIN vault.vault_object o ON o.vault_object_id=v.vault_object_id
      JOIN vault.audio_variant prior_variant
        ON prior_variant.audio_variant_id=NEW.expected_audio_variant_id
        AND prior_variant.recording_id=c.recording_id
      JOIN vault.vault_object prior_object
        ON prior_object.vault_object_id=prior_variant.vault_object_id
      WHERE c.recording_id=NEW.recording_id AND c.audio_variant_id=NEW.audio_variant_id
        AND o.sha256=NEW.source_sha256 AND prior_object.sha256=NEW.expected_source_sha256)
    OR NEW.replacement_generation<>(SELECT COALESCE(max(replacement_generation),0)+1
      FROM ml.face_source_replacement WHERE recording_id=NEW.recording_id) THEN
    RAISE EXCEPTION 'face_replacement_insert_invalid' USING ERRCODE='55000';
   END IF;
  ELSE
   IF OLD.state<>'PENDING' OR NEW.retired_count<OLD.retired_count
    OR NEW.successor_count<OLD.successor_count
    OR (to_jsonb(NEW)-ARRAY['state','retired_count','successor_count','completed_at'])
      IS DISTINCT FROM
      (to_jsonb(OLD)-ARRAY['state','retired_count','successor_count','completed_at']) THEN
    RAISE EXCEPTION 'face_replacement_immutable' USING ERRCODE='55000';
   END IF;
   IF NEW.state='COMPLETE' AND (EXISTS(SELECT 1 FROM ml.face_analysis_sponsor
      WHERE recording_id=NEW.recording_id AND state='LIVE' AND
audio_variant_id<>NEW.audio_variant_id)
     OR EXISTS(SELECT 1 FROM ml.face_source_replacement_candidate
      WHERE face_source_replacement_id=NEW.face_source_replacement_id AND state='PENDING')) THEN
    RAISE EXCEPTION 'face_replacement_incomplete' USING ERRCODE='55000';
   END IF;
  END IF;
 ELSE
  SELECT * INTO root FROM ml.face_source_replacement
   WHERE face_source_replacement_id=NEW.face_source_replacement_id FOR SHARE;
  IF root.state IS DISTINCT FROM 'PENDING' THEN
   RAISE EXCEPTION 'face_replacement_not_pending' USING ERRCODE='55000';
  END IF;
  IF TG_OP='INSERT' THEN
   IF NEW.state<>'PENDING' OR NOT EXISTS(SELECT 1 FROM ml.face_analysis_sponsor s
     WHERE s.face_sponsor_id=NEW.predecessor_sponsor_id AND s.state='LIVE'
       AND s.recording_id=root.recording_id AND s.audio_variant_id=root.expected_audio_variant_id
       AND s.source_sha256=root.expected_source_sha256
       AND convert_from(NEW.candidate_document,'UTF8')::jsonb @> jsonb_build_object(
         'owner_user_id',s.owner_user_id::text,'user_track_ref_id',s.user_track_ref_id::text,
         'expected_current_sponsor_id',s.face_sponsor_id::text,
         'expected_source_generation',s.source_generation+1,
         'identity',jsonb_build_object('recording_id',root.recording_id::text,
'audio_variant_id',root.audio_variant_id::text,'source_sha256',encode(root.source_sha256,'hex'))))
THEN
    RAISE EXCEPTION 'face_replacement_candidate_invalid' USING ERRCODE='55000';
   END IF;
  ELSE
   IF OLD.state<>'PENDING' OR NEW.state NOT IN ('APPLIED','CANCELLED')
    OR (to_jsonb(NEW)-ARRAY['state','successor_sponsor_id','reason','completed_at'])
      IS DISTINCT FROM
      (to_jsonb(OLD)-ARRAY['state','successor_sponsor_id','reason','completed_at']) THEN
    RAISE EXCEPTION 'face_replacement_candidate_immutable' USING ERRCODE='55000';
   END IF;
   IF NEW.state='APPLIED' AND NOT EXISTS(SELECT 1 FROM ml.face_analysis_sponsor successor
     JOIN ml.face_analysis_sponsor predecessor ON
predecessor.face_sponsor_id=NEW.predecessor_sponsor_id
     JOIN ml.face_analysis_work work ON work.face_work_id=successor.face_work_id
     WHERE successor.face_sponsor_id=NEW.successor_sponsor_id
       AND predecessor.state='SUPERSEDED' AND predecessor.successor_id=successor.face_sponsor_id
       AND successor.predecessor_id=predecessor.face_sponsor_id AND successor.state='LIVE'
       AND successor.owner_user_id=predecessor.owner_user_id
       AND successor.user_track_ref_id=predecessor.user_track_ref_id
       AND successor.recording_id=root.recording_id
       AND successor.source_generation=predecessor.source_generation+1
       AND successor.policy_generation=(convert_from(NEW.candidate_document,'UTF8')::jsonb
         ->>'expected_policy_generation')::bigint
       AND successor.activation_epoch=(convert_from(NEW.candidate_document,'UTF8')::jsonb
         ->>'expected_activation_epoch')::bigint
       AND successor.redirect_generation=(convert_from(NEW.candidate_document,'UTF8')::jsonb
         ->>'expected_redirect_generation')::bigint
       AND convert_from(work.identity_document,'UTF8')::jsonb=
         convert_from(NEW.candidate_document,'UTF8')::jsonb->'identity'
       AND successor.audio_variant_id=root.audio_variant_id AND
successor.source_sha256=root.source_sha256) THEN
    RAISE EXCEPTION 'face_replacement_successor_invalid' USING ERRCODE='55000';
   END IF;
   IF NEW.state='CANCELLED' AND EXISTS(SELECT 1 FROM ml.face_analysis_sponsor
     WHERE face_sponsor_id=NEW.predecessor_sponsor_id AND state='LIVE') THEN
    RAISE EXCEPTION 'face_replacement_predecessor_live' USING ERRCODE='55000';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_source_replacement BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_source_replacement FOR EACH ROW EXECUTE FUNCTION
app_private.fence_face_source_replacement();
CREATE TRIGGER z_face_replacement_candidate BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_source_replacement_candidate FOR EACH ROW EXECUTE FUNCTION
app_private.fence_face_source_replacement();
REVOKE ALL ON ml.face_source_replacement,ml.face_source_replacement_candidate FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_source_replacement() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_source_replacement) THEN
  RAISE EXCEPTION 'face_replacement_downgrade_requires_empty_authority' USING ERRCODE='55000';
 END IF;
END $$;
DROP TABLE ml.face_source_replacement_candidate;
DROP TABLE ml.face_source_replacement;
DROP FUNCTION app_private.fence_face_source_replacement();
""")
