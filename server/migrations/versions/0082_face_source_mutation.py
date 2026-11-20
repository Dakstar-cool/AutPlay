"""Transactional source mutation capture and bounded redirect authority.

Revision ID: 0082_face_source_mutation
Revises: 0081_face_work_incarnation
"""

from alembic import op

revision = "0082_face_source_mutation"
down_revision = "0081_face_work_incarnation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE SEQUENCE ml.face_source_mutation_generation_seq AS bigint
 MINVALUE 1 MAXVALUE 9007199254740990;
CREATE TABLE ml.face_redirect_authority (
 recording_id uuid PRIMARY KEY REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
 redirect_generation bigint NOT NULL DEFAULT 0,
 CONSTRAINT ck_face_redirect_generation CHECK(redirect_generation BETWEEN 0 AND 9007199254740990)
);
CREATE TABLE ml.face_source_mutation (
 face_source_mutation_id uuid PRIMARY KEY DEFAULT uuidv7(),
 mutation_generation bigint NOT NULL DEFAULT nextval('ml.face_source_mutation_generation_seq'),
 recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
 recording_ids uuid[] NOT NULL,
 kind text NOT NULL,
 expected_audio_variant_id uuid REFERENCES vault.audio_variant(audio_variant_id) ON DELETE
  RESTRICT,
 target_recording_id uuid REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
 target_audio_variant_id uuid REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
 target_source_sha256 bytea,
 replacement_id uuid REFERENCES ml.face_source_replacement(face_source_replacement_id) ON
  DELETE RESTRICT,
 invalidation_id uuid REFERENCES ml.face_source_invalidation(face_source_invalidation_id) ON
  DELETE RESTRICT,
 highwater_reference_id uuid,
 cursor_reference_id uuid,
 state text NOT NULL DEFAULT 'PENDING',
 retired_count bigint NOT NULL DEFAULT 0,
 successor_count bigint NOT NULL DEFAULT 0,
 scanned_count bigint NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 CONSTRAINT uq_face_source_mutation_generation UNIQUE(mutation_generation),
 CONSTRAINT ck_face_source_mutation_generation CHECK(mutation_generation BETWEEN 1 AND
  9007199254740990),
 CONSTRAINT ck_face_source_mutation_scope CHECK(cardinality(recording_ids) BETWEEN 1 AND 35
  AND array_position(recording_ids,NULL) IS NULL AND recording_id=ANY(recording_ids)),
 CONSTRAINT ck_face_source_mutation_kind CHECK(kind IN ('CANONICAL','VARIANT','OBJECT',
  'RECORDING','REPLICA','REDIRECT')),
 CONSTRAINT ck_face_source_mutation_target CHECK((target_audio_variant_id IS NULL AND
  target_source_sha256 IS NULL)
   OR (target_audio_variant_id IS NOT NULL AND target_recording_id IS NOT NULL AND
    target_source_sha256 IS NOT NULL AND octet_length(target_source_sha256)=32)),
 CONSTRAINT ck_face_source_mutation_counts CHECK(retired_count>=0 AND successor_count>=0 AND
  scanned_count>=0),
 CONSTRAINT ck_face_source_mutation_delegate CHECK(replacement_id IS NULL OR invalidation_id
  IS NULL),
 CONSTRAINT ck_face_source_mutation_completion CHECK((state='PENDING' AND completed_at IS NULL)
   OR (state='COMPLETE' AND completed_at IS NOT NULL))
);
CREATE INDEX ix_face_source_mutation_pending ON ml.face_source_mutation(created_at,
 face_source_mutation_id)
 WHERE state='PENDING';
CREATE TABLE ml.face_source_mutation_candidate (
 face_source_mutation_id uuid NOT NULL REFERENCES
  ml.face_source_mutation(face_source_mutation_id) ON DELETE RESTRICT,
 predecessor_sponsor_id uuid NOT NULL REFERENCES ml.face_analysis_sponsor(face_sponsor_id) ON
  DELETE RESTRICT,
 candidate_document bytea NOT NULL,
 candidate_sha256 bytea NOT NULL,
 state text NOT NULL DEFAULT 'PENDING',
 successor_sponsor_id uuid REFERENCES ml.face_analysis_sponsor(face_sponsor_id) ON DELETE
  RESTRICT,
 reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 PRIMARY KEY(face_source_mutation_id,predecessor_sponsor_id),
 CONSTRAINT ck_face_mutation_candidate_bytes CHECK(octet_length(candidate_document) BETWEEN 1
  AND 131072 AND candidate_sha256=sha256(candidate_document)),
 CONSTRAINT ck_face_mutation_candidate_completion CHECK(
  (state='PENDING' AND successor_sponsor_id IS NULL AND reason IS NULL AND completed_at IS NULL)
  OR (state='APPLIED' AND successor_sponsor_id IS NOT NULL AND reason IS NULL AND completed_at
   IS NOT NULL)
  OR (state='CANCELLED' AND successor_sponsor_id IS NULL AND reason IS NOT NULL AND reason ~
   '^[A-Z][A-Z0-9_]{0,63}$' AND completed_at IS NOT NULL))
);
CREATE FUNCTION app_private.fence_face_redirect_authority() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (TG_OP='INSERT' AND NEW.redirect_generation<>0)
  OR (TG_OP='UPDATE' AND (NEW.recording_id IS DISTINCT FROM OLD.recording_id
    OR NEW.redirect_generation<=OLD.redirect_generation)) THEN
  RAISE EXCEPTION 'face_redirect_authority_immutable' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION app_private.verify_face_redirect_authority() RETURNS trigger LANGUAGE plpgsql
 AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ml.face_source_mutation
  WHERE kind='REDIRECT' AND recording_id=NEW.recording_id AND
   mutation_generation=NEW.redirect_generation) THEN
  RAISE EXCEPTION 'face_redirect_generation_receipt_required' USING ERRCODE='55000'; END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER z_face_redirect_authority BEFORE INSERT OR UPDATE OR DELETE ON
 ml.face_redirect_authority
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_redirect_authority();
CREATE CONSTRAINT TRIGGER z_face_redirect_authority_receipt AFTER UPDATE ON
 ml.face_redirect_authority
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
  app_private.verify_face_redirect_authority();
CREATE INDEX ix_face_mutation_candidate_pending ON
 ml.face_source_mutation_candidate(face_source_mutation_id,predecessor_sponsor_id)
 WHERE state='PENDING';
CREATE INDEX ix_face_sponsor_live_reference ON ml.face_analysis_sponsor(user_track_ref_id)
 WHERE state='LIVE';

CREATE FUNCTION app_private.face_redirect_path(source_id uuid) RETURNS uuid[]
LANGUAGE plpgsql STABLE AS $$
DECLARE path uuid[]:=ARRAY[source_id]; current_id uuid:=source_id; target_id uuid;
BEGIN
 IF source_id IS NULL THEN RETURN ARRAY[]::uuid[]; END IF;
 FOR step IN 1..17 LOOP
  SELECT target_recording_id INTO target_id FROM identity.recording_redirect WHERE
   source_recording_id=current_id;
  IF NOT FOUND THEN RETURN path; END IF;
  path:=array_append(path,target_id);
  IF target_id=ANY(path[1:cardinality(path)-1]) THEN RETURN path; END IF;
  current_id:=target_id;
 END LOOP;
 RETURN path;
END $$;
INSERT INTO ml.face_redirect_authority(recording_id)
 SELECT DISTINCT id FROM ml.face_analysis_sponsor s
 JOIN library.user_track_ref ref ON ref.user_track_ref_id=s.user_track_ref_id
 CROSS JOIN LATERAL unnest(ARRAY[s.recording_id] ||
  app_private.face_redirect_path(ref.recording_id)) id
 WHERE id IS NOT NULL ON CONFLICT DO NOTHING;
CREATE FUNCTION app_private.face_mutation_affects(mutation_id uuid,recording uuid,origin uuid)
 RETURNS boolean
LANGUAGE sql STABLE AS $$
 SELECT COALESCE(recording=root.recording_id
   OR root.recording_id=ANY(app_private.face_redirect_path(origin)),false)
 FROM ml.face_source_mutation root WHERE root.face_source_mutation_id=mutation_id
$$;
CREATE FUNCTION app_private.face_mutation_has_remaining(mutation_id uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT 1 FROM ml.face_source_mutation_candidate
   WHERE face_source_mutation_id=mutation_id AND state='PENDING')
 OR EXISTS(SELECT 1 FROM ml.face_analysis_sponsor s
   JOIN library.user_track_ref ref ON ref.user_track_ref_id=s.user_track_ref_id
   WHERE s.state='LIVE' AND app_private.face_mutation_affects(mutation_id,s.recording_id,
    ref.recording_id)
     AND NOT EXISTS(SELECT 1 FROM ml.face_source_mutation_candidate item
       WHERE item.face_source_mutation_id=mutation_id AND item.state='APPLIED'
         AND item.successor_sponsor_id=s.face_sponsor_id))
$$;

CREATE FUNCTION app_private.capture_face_source_mutation(subject uuid,mutation_kind text,
 old_variant uuid DEFAULT NULL,new_variant uuid DEFAULT NULL,old_target uuid DEFAULT NULL,
  new_target uuid DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE ids uuid[]; path uuid[]; result_id uuid; generation bigint; target_id uuid; variant_id
 uuid; source_hash bytea; enrolled boolean;
BEGIN
 SELECT array_agg(DISTINCT id ORDER BY id) INTO ids FROM unnest(
  ARRAY[subject] || app_private.face_redirect_path(subject)
    || app_private.face_redirect_path(old_target) ||
     app_private.face_redirect_path(new_target)) id WHERE id IS NOT NULL;
 SELECT EXISTS(SELECT 1 FROM ml.face_redirect_authority WHERE recording_id=ANY(ids))
   OR EXISTS(SELECT 1 FROM ml.face_analysis_sponsor WHERE recording_id=ANY(ids)) INTO enrolled;
 IF NOT enrolled THEN RETURN NULL; END IF;
 -- There is no owner/reference/work lock or population walk in this writer.
 INSERT INTO ml.face_redirect_authority(recording_id) SELECT unnest(ids) ON CONFLICT DO NOTHING;
 PERFORM recording_id FROM ml.face_redirect_authority WHERE recording_id=ANY(ids) ORDER BY
  recording_id FOR UPDATE;
 generation:=nextval('ml.face_source_mutation_generation_seq');
 IF mutation_kind='REDIRECT' THEN
  UPDATE ml.face_redirect_authority SET redirect_generation=generation WHERE recording_id=subject;
 END IF;
 path:=app_private.face_redirect_path(subject);
 IF cardinality(path)<=17 AND cardinality(path)=(SELECT count(DISTINCT id) FROM unnest(path)
  id) THEN
  target_id:=path[cardinality(path)];
  SELECT c.audio_variant_id,o.sha256 INTO variant_id,source_hash
  FROM vault.recording_canonical_variant c JOIN vault.audio_variant v ON
   v.audio_variant_id=c.audio_variant_id
   JOIN vault.vault_object o ON o.vault_object_id=v.vault_object_id JOIN catalog.recording r
    ON r.recording_id=c.recording_id
  WHERE c.recording_id=target_id AND v.recording_id=c.recording_id AND v.validation_status='VALID'
   AND v.deleted_at IS NULL AND r.deleted_at IS NULL AND o.commit_status='COMMITTED' AND
    o.committed_at IS NOT NULL
   AND EXISTS(SELECT 1 FROM vault.vault_replica replica WHERE
    replica.vault_object_id=o.vault_object_id
     AND replica.storage_backend='LOCAL_FILESYSTEM' AND replica.replica_status='AVAILABLE' AND
      replica.verified_at IS NOT NULL);
 END IF;
 INSERT INTO ml.face_source_mutation(mutation_generation,recording_id,recording_ids,kind,
   expected_audio_variant_id,target_recording_id,target_audio_variant_id,target_source_sha256,
    highwater_reference_id)
 VALUES(generation,subject,ids,mutation_kind,old_variant,target_id,variant_id,source_hash,
   (SELECT user_track_ref_id FROM ml.face_analysis_sponsor WHERE state='LIVE' ORDER BY
    user_track_ref_id DESC LIMIT 1))
 RETURNING face_source_mutation_id INTO result_id;
 RETURN result_id;
END $$;

CREATE FUNCTION app_private.capture_face_source_dml() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE subject uuid; variant uuid; object_id uuid;
BEGIN
 IF TG_TABLE_NAME='recording_canonical_variant' THEN
  IF TG_OP='INSERT' OR TG_OP='DELETE' OR OLD.audio_variant_id IS DISTINCT FROM
   NEW.audio_variant_id THEN
   PERFORM app_private.capture_face_source_mutation(COALESCE(NEW.recording_id,
    OLD.recording_id),'CANONICAL',
     CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.audio_variant_id END,
     CASE WHEN TG_OP='DELETE' THEN NULL ELSE NEW.audio_variant_id END);
  END IF;
 ELSIF TG_TABLE_NAME='recording_redirect' THEN
  IF TG_OP='INSERT' OR TG_OP='DELETE' OR OLD.target_recording_id IS DISTINCT FROM
   NEW.target_recording_id THEN
   PERFORM app_private.capture_face_source_mutation(COALESCE(NEW.source_recording_id,
    OLD.source_recording_id),'REDIRECT',NULL,NULL,
     CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.target_recording_id END,
     CASE WHEN TG_OP='DELETE' THEN NULL ELSE NEW.target_recording_id END);
  END IF;
 ELSIF TG_TABLE_NAME='audio_variant' THEN
  IF TG_OP='DELETE' OR OLD.validation_status IS DISTINCT FROM NEW.validation_status OR
   OLD.deleted_at IS DISTINCT FROM NEW.deleted_at THEN
   IF EXISTS(SELECT 1 FROM vault.recording_canonical_variant WHERE
    audio_variant_id=OLD.audio_variant_id) THEN
    PERFORM app_private.capture_face_source_mutation(OLD.recording_id,'VARIANT',
     OLD.audio_variant_id);
   END IF;
  END IF;
 ELSIF TG_TABLE_NAME='recording' THEN
  IF TG_OP='DELETE' OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at THEN
   SELECT audio_variant_id INTO variant FROM vault.recording_canonical_variant WHERE
    recording_id=OLD.recording_id;
   PERFORM app_private.capture_face_source_mutation(OLD.recording_id,'RECORDING',variant);
  END IF;
 ELSE
  IF TG_TABLE_NAME='vault_object' THEN
   IF TG_OP<>'DELETE' AND OLD.commit_status IS NOT DISTINCT FROM NEW.commit_status THEN RETURN
    NULL; END IF;
   object_id:=OLD.vault_object_id;
  ELSE
   IF TG_OP='UPDATE' AND OLD.vault_object_id IS DISTINCT FROM NEW.vault_object_id THEN
    PERFORM authority.recording_id FROM ml.face_redirect_authority authority
      WHERE authority.recording_id IN (
        SELECT id FROM vault.recording_canonical_variant c
        JOIN vault.audio_variant v ON v.audio_variant_id=c.audio_variant_id
        CROSS JOIN LATERAL unnest(app_private.face_redirect_path(c.recording_id)) id
        WHERE v.vault_object_id=ANY(ARRAY[OLD.vault_object_id,NEW.vault_object_id]))
      ORDER BY authority.recording_id FOR UPDATE;
    FOR object_id IN SELECT DISTINCT id
      FROM unnest(ARRAY[OLD.vault_object_id,NEW.vault_object_id]) id ORDER BY id LOOP
     IF ((object_id=OLD.vault_object_id AND OLD.storage_backend='LOCAL_FILESYSTEM'
          AND OLD.replica_status='AVAILABLE' AND OLD.verified_at IS NOT NULL)
       OR (object_id=NEW.vault_object_id AND NEW.storage_backend='LOCAL_FILESYSTEM'
          AND NEW.replica_status='AVAILABLE' AND NEW.verified_at IS NOT NULL))
       AND NOT EXISTS(SELECT 1 FROM vault.vault_replica replica
         WHERE replica.vault_object_id=object_id
           AND replica.storage_backend='LOCAL_FILESYSTEM'
           AND replica.replica_status='AVAILABLE' AND replica.verified_at IS NOT NULL
           AND replica.vault_replica_id<>NEW.vault_replica_id) THEN
      FOR subject,variant IN SELECT c.recording_id,c.audio_variant_id
        FROM vault.recording_canonical_variant c
        JOIN vault.audio_variant v ON v.audio_variant_id=c.audio_variant_id
        WHERE v.vault_object_id=object_id LOOP
       PERFORM app_private.capture_face_source_mutation(subject,'REPLICA',variant);
      END LOOP;
     END IF;
    END LOOP;
    RETURN NULL;
   END IF;
   IF TG_OP='INSERT' THEN
    IF NOT (NEW.storage_backend='LOCAL_FILESYSTEM' AND NEW.replica_status='AVAILABLE' AND
     NEW.verified_at IS NOT NULL)
      THEN RETURN NULL; END IF;
    object_id:=NEW.vault_object_id;
   ELSE
    IF TG_OP='DELETE' AND NOT (OLD.storage_backend='LOCAL_FILESYSTEM' AND
     OLD.replica_status='AVAILABLE' AND OLD.verified_at IS NOT NULL)
      THEN RETURN NULL; END IF;
    IF TG_OP<>'DELETE' AND
      (OLD.storage_backend='LOCAL_FILESYSTEM' AND OLD.replica_status='AVAILABLE' AND
       OLD.verified_at IS NOT NULL)
      IS NOT DISTINCT FROM
      (NEW.storage_backend='LOCAL_FILESYSTEM' AND NEW.replica_status='AVAILABLE' AND
       NEW.verified_at IS NOT NULL)
      THEN RETURN NULL; END IF;
    object_id:=OLD.vault_object_id;
   END IF;
   IF EXISTS(SELECT 1 FROM vault.vault_replica replica WHERE replica.vault_object_id=object_id
     AND replica.storage_backend='LOCAL_FILESYSTEM' AND replica.replica_status='AVAILABLE' AND
      replica.verified_at IS NOT NULL
     AND replica.vault_replica_id IS DISTINCT FROM COALESCE(NEW.vault_replica_id,
      OLD.vault_replica_id)) THEN RETURN NULL; END IF;
  END IF;
  FOR subject,variant IN SELECT c.recording_id,c.audio_variant_id FROM
   vault.recording_canonical_variant c
    JOIN vault.audio_variant v ON v.audio_variant_id=c.audio_variant_id WHERE
     v.vault_object_id=object_id LOOP
   PERFORM app_private.capture_face_source_mutation(subject,
    CASE WHEN TG_TABLE_NAME='vault_object' THEN 'OBJECT' ELSE 'REPLICA' END,variant);
  END LOOP;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER z_face_canonical_capture AFTER INSERT OR UPDATE OR DELETE ON
 vault.recording_canonical_variant
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_source_dml();
CREATE TRIGGER z_face_variant_capture AFTER UPDATE OR DELETE ON vault.audio_variant
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_source_dml();
CREATE TRIGGER z_face_object_capture AFTER UPDATE OR DELETE ON vault.vault_object
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_source_dml();
CREATE TRIGGER z_face_replica_capture AFTER INSERT OR UPDATE OR DELETE ON vault.vault_replica
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_source_dml();
CREATE TRIGGER z_face_recording_capture AFTER UPDATE OR DELETE ON catalog.recording
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_source_dml();
CREATE TRIGGER z_face_redirect_capture AFTER INSERT OR UPDATE OR DELETE ON
 identity.recording_redirect
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_source_dml();

CREATE FUNCTION app_private.fence_face_source_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE root ml.face_source_mutation%ROWTYPE; prior ml.face_analysis_sponsor%ROWTYPE; document
 jsonb; successor ml.face_analysis_sponsor%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'face_mutation_immutable' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='face_source_mutation' THEN
  IF TG_OP='INSERT' THEN
   IF pg_trigger_depth()<2 OR NEW.state<>'PENDING' OR NEW.retired_count<>0 OR
    NEW.successor_count<>0 OR NEW.scanned_count<>0
     OR NEW.cursor_reference_id IS NOT NULL OR NEW.completed_at IS NOT NULL
     OR NEW.replacement_id IS NOT NULL OR NEW.invalidation_id IS NOT NULL THEN
    RAISE EXCEPTION 'face_mutation_insert_invalid' USING ERRCODE='55000';
   END IF;
  ELSE
   IF OLD.state<>'PENDING' OR NEW.retired_count<OLD.retired_count OR
    NEW.successor_count<OLD.successor_count OR NEW.scanned_count<OLD.scanned_count
     OR (to_jsonb(NEW)-ARRAY['state','retired_count','successor_count','scanned_count',
      'cursor_reference_id','completed_at','replacement_id','invalidation_id'])
       IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','retired_count','successor_count',
        'scanned_count','cursor_reference_id','completed_at','replacement_id','invalidation_id'])
     OR (OLD.replacement_id IS NOT NULL AND NEW.replacement_id IS DISTINCT FROM
      OLD.replacement_id)
     OR (OLD.invalidation_id IS NOT NULL AND NEW.invalidation_id IS DISTINCT FROM
      OLD.invalidation_id) THEN
    RAISE EXCEPTION 'face_mutation_immutable' USING ERRCODE='55000';
   END IF;
   IF NEW.replacement_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM ml.face_source_replacement r
     WHERE r.face_source_replacement_id=NEW.replacement_id AND r.recording_id=NEW.recording_id
       AND r.expected_audio_variant_id=NEW.expected_audio_variant_id AND
        r.audio_variant_id=NEW.target_audio_variant_id
       AND r.source_sha256=NEW.target_source_sha256 AND NEW.kind='CANONICAL') THEN
    RAISE EXCEPTION 'face_mutation_delegate_invalid' USING ERRCODE='55000';
   END IF;
   IF NEW.invalidation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM ml.face_source_invalidation i
     WHERE i.face_source_invalidation_id=NEW.invalidation_id AND i.recording_id=NEW.recording_id
       AND i.audio_variant_id=NEW.expected_audio_variant_id AND NEW.kind='VARIANT') THEN
    RAISE EXCEPTION 'face_mutation_delegate_invalid' USING ERRCODE='55000';
   END IF;
   IF NEW.state='COMPLETE' AND NEW.target_audio_variant_id IS NOT NULL
     AND NEW.replacement_id IS NULL AND NEW.invalidation_id IS NULL AND (
       EXISTS(SELECT 1 FROM ml.face_source_mutation older
         WHERE older.state='PENDING' AND older.mutation_generation<NEW.mutation_generation
           AND older.recording_ids && NEW.recording_ids)
       OR EXISTS(SELECT 1 FROM ml.face_source_invalidation older
         WHERE older.state='PENDING' AND older.created_at<=NEW.created_at
           AND older.recording_id=ANY(NEW.recording_ids))
       OR EXISTS(SELECT 1 FROM ml.face_source_replacement older
         WHERE older.state='PENDING' AND older.created_at<=NEW.created_at
           AND older.recording_id=ANY(NEW.recording_ids))
     ) THEN
    RAISE EXCEPTION 'face_mutation_pending_dependency' USING ERRCODE='55000';
   END IF;
   IF NEW.state='COMPLETE' AND (
    (NEW.replacement_id IS NULL AND NEW.invalidation_id IS NULL AND
     app_private.face_mutation_has_remaining(NEW.face_source_mutation_id))
    OR (NEW.replacement_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM ml.face_source_replacement
     WHERE face_source_replacement_id=NEW.replacement_id AND state='COMPLETE'))
    OR (NEW.invalidation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM
     ml.face_source_invalidation WHERE face_source_invalidation_id=NEW.invalidation_id AND
      state='COMPLETE'))
   ) THEN RAISE EXCEPTION 'face_mutation_incomplete' USING ERRCODE='55000'; END IF;
  END IF;
 ELSE
  SELECT * INTO root FROM ml.face_source_mutation WHERE
   face_source_mutation_id=NEW.face_source_mutation_id FOR SHARE;
  IF root.state IS DISTINCT FROM 'PENDING' OR root.replacement_id IS NOT NULL OR
   root.invalidation_id IS NOT NULL THEN
   RAISE EXCEPTION 'face_mutation_not_pending' USING ERRCODE='55000'; END IF;
  document:=convert_from(NEW.candidate_document,'UTF8')::jsonb;
  SELECT * INTO prior FROM ml.face_analysis_sponsor WHERE
   face_sponsor_id=NEW.predecessor_sponsor_id;
  IF TG_OP='INSERT' THEN
   IF NEW.state<>'PENDING' OR prior.state NOT IN ('LIVE','WITHDRAWN')
     OR jsonb_typeof(document->'expected_policy_generation') IS DISTINCT FROM 'number'
     OR jsonb_typeof(document->'expected_activation_epoch') IS DISTINCT FROM 'number'
     OR jsonb_typeof(document->'expected_redirect_generation') IS DISTINCT FROM 'number'
     OR NOT app_private.face_mutation_affects(root.face_source_mutation_id,prior.recording_id,
       (SELECT recording_id FROM library.user_track_ref WHERE
        user_track_ref_id=prior.user_track_ref_id))
     OR root.target_audio_variant_id IS NULL OR NOT document @> jsonb_build_object(
       'owner_user_id',prior.owner_user_id::text,'user_track_ref_id',
        prior.user_track_ref_id::text,
       'expected_current_sponsor_id',prior.face_sponsor_id::text,'expected_source_generation',
        prior.source_generation+1,
       'identity',jsonb_build_object('recording_id',root.target_recording_id::text,
         'audio_variant_id',root.target_audio_variant_id::text,'source_sha256',
          encode(root.target_source_sha256,'hex')))
     OR EXISTS(SELECT 1 FROM ml.face_analysis_sponsor later WHERE
      later.owner_user_id=prior.owner_user_id
       AND later.user_track_ref_id=prior.user_track_ref_id AND
        later.source_generation>prior.source_generation) THEN
    RAISE EXCEPTION 'face_mutation_candidate_invalid' USING ERRCODE='55000';
   END IF;
  ELSE
   IF OLD.state<>'PENDING' OR NEW.state NOT IN ('APPLIED','CANCELLED') OR
     (to_jsonb(NEW)-ARRAY['state','successor_sponsor_id','reason','completed_at']) IS DISTINCT
      FROM
     (to_jsonb(OLD)-ARRAY['state','successor_sponsor_id','reason','completed_at']) THEN
    RAISE EXCEPTION 'face_mutation_candidate_immutable' USING ERRCODE='55000'; END IF;
   IF NEW.state='CANCELLED' AND prior.state='LIVE' THEN
    RAISE EXCEPTION 'face_mutation_predecessor_live' USING ERRCODE='55000'; END IF;
   IF NEW.state='APPLIED' THEN
    SELECT * INTO successor FROM ml.face_analysis_sponsor WHERE
     face_sponsor_id=NEW.successor_sponsor_id;
    IF successor.state IS DISTINCT FROM 'LIVE' OR prior.state NOT IN ('SUPERSEDED','WITHDRAWN')
      OR successor.owner_user_id IS DISTINCT FROM prior.owner_user_id OR
       successor.user_track_ref_id IS DISTINCT FROM prior.user_track_ref_id
      OR successor.source_generation<>prior.source_generation+1
      OR successor.recording_id IS DISTINCT FROM root.target_recording_id
      OR successor.audio_variant_id IS DISTINCT FROM root.target_audio_variant_id
      OR successor.source_sha256 IS DISTINCT FROM root.target_source_sha256
      OR successor.policy_generation IS DISTINCT FROM
       (document->>'expected_policy_generation')::bigint
      OR successor.activation_epoch IS DISTINCT FROM
       (document->>'expected_activation_epoch')::bigint
      OR successor.redirect_generation IS DISTINCT FROM
       (document->>'expected_redirect_generation')::bigint
      OR NOT EXISTS(SELECT 1 FROM ml.face_analysis_work w WHERE
       w.face_work_id=successor.face_work_id
        AND convert_from(w.identity_document,'UTF8')::jsonb=document->'identity') THEN
     RAISE EXCEPTION 'face_mutation_successor_invalid' USING ERRCODE='55000'; END IF;
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_source_mutation BEFORE INSERT OR UPDATE OR DELETE ON ml.face_source_mutation
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_source_mutation();
CREATE TRIGGER z_face_mutation_candidate BEFORE INSERT OR UPDATE OR DELETE ON
 ml.face_source_mutation_candidate
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_source_mutation();
CREATE FUNCTION app_private.pair_face_source_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='face_source_replacement' THEN
  IF TG_OP='INSERT' THEN
   UPDATE ml.face_source_mutation SET replacement_id=NEW.face_source_replacement_id
    WHERE state='PENDING' AND kind='CANONICAL' AND recording_id=NEW.recording_id
      AND expected_audio_variant_id=NEW.expected_audio_variant_id AND
       target_audio_variant_id=NEW.audio_variant_id
      AND target_source_sha256=NEW.source_sha256 AND replacement_id IS NULL AND
       invalidation_id IS NULL;
  ELSIF NEW.state='COMPLETE' THEN
   UPDATE ml.face_source_mutation SET state='COMPLETE',completed_at=clock_timestamp()
    WHERE state='PENDING' AND replacement_id=NEW.face_source_replacement_id;
  END IF;
 ELSE
  IF TG_OP='INSERT' THEN
   UPDATE ml.face_source_mutation SET invalidation_id=NEW.face_source_invalidation_id
    WHERE state='PENDING' AND kind='VARIANT' AND recording_id=NEW.recording_id
      AND expected_audio_variant_id=NEW.audio_variant_id AND replacement_id IS NULL AND
       invalidation_id IS NULL;
  ELSIF NEW.state='COMPLETE' THEN
   UPDATE ml.face_source_mutation SET state='COMPLETE',completed_at=clock_timestamp()
    WHERE state='PENDING' AND invalidation_id=NEW.face_source_invalidation_id;
  END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER z_face_replacement_mutation_pair AFTER INSERT OR UPDATE ON
 ml.face_source_replacement
 FOR EACH ROW EXECUTE FUNCTION app_private.pair_face_source_mutation();
CREATE TRIGGER z_face_invalidation_mutation_pair AFTER INSERT OR UPDATE ON
 ml.face_source_invalidation
 FOR EACH ROW EXECUTE FUNCTION app_private.pair_face_source_mutation();
ALTER TABLE ml.face_analysis_sponsor
 ADD COLUMN source_mutation_id uuid REFERENCES
  ml.face_source_mutation(face_source_mutation_id) ON DELETE RESTRICT,
 ADD COLUMN mutation_predecessor_sponsor_id uuid REFERENCES
  ml.face_analysis_sponsor(face_sponsor_id) ON DELETE RESTRICT,
 ADD CONSTRAINT ck_face_sponsor_mutation_origin CHECK((source_mutation_id IS NULL AND
  mutation_predecessor_sponsor_id IS NULL)
   OR (source_mutation_id IS NOT NULL AND mutation_predecessor_sponsor_id IS NOT NULL));
ALTER TABLE ml.face_analysis_work ADD COLUMN incarnation_mutation_id uuid
 REFERENCES ml.face_source_mutation(face_source_mutation_id) ON DELETE RESTRICT;
CREATE FUNCTION app_private.face_mutation_origin_valid(mutation_id uuid,origin_id uuid,
 identity bytea,source_generation bigint)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT 1 FROM ml.face_source_mutation root
  JOIN ml.face_source_mutation_candidate item USING(face_source_mutation_id)
  JOIN ml.face_analysis_sponsor origin ON origin.face_sponsor_id=item.predecessor_sponsor_id
  WHERE root.face_source_mutation_id=mutation_id AND root.state='PENDING' AND item.state='PENDING'
    AND item.predecessor_sponsor_id=origin_id AND origin.state IN ('LIVE','WITHDRAWN')
    AND $4=origin.source_generation+1
    AND convert_from(item.candidate_document,'UTF8')::jsonb->'identity'=convert_from(identity,
     'UTF8')::jsonb)
$$;
-- Retain the original source/work and immutability guards. Only exact durable
-- mutation evidence can use their history/unchanged-source continuation arm.
DO $$ DECLARE definition text; BEGIN
 definition:=pg_get_functiondef('app_private.fence_face_sponsor_mutation()'::regprocedure);
 definition:=replace(definition,'IF EXISTS(SELECT 1 FROM ml.face_analysis_sponsor',
   'IF NEW.source_mutation_id IS NULL AND EXISTS(SELECT 1 FROM ml.face_analysis_sponsor');
 EXECUTE definition;
 definition:=pg_get_functiondef('app_private.fence_face_work_incarnation()'::regprocedure);
 definition:=replace(definition,
  'FROM catalog.recording WHERE recording_id=NEW.recording_id FOR UPDATE',
   'FROM catalog.recording WHERE recording_id=NEW.recording_id FOR NO KEY UPDATE');
 definition:=replace(definition,'origin.state IS DISTINCT FROM ''LIVE''',
   '(origin.state IS DISTINCT FROM ''LIVE'' AND NOT (NEW.incarnation_mutation_id IS NOT NULL ' ||
    'AND origin.state=''WITHDRAWN''))');
 definition:=replace(definition,
  'OR (origin.audio_variant_id=NEW.audio_variant_id AND origin.source_sha256=NEW.source_sha256)',
   'OR (NEW.incarnation_mutation_id IS NULL AND origin.audio_variant_id=NEW.audio_variant_id ' ||
    'AND origin.source_sha256=NEW.source_sha256)');
 definition:=replace(definition,'historical.source_generation<origin.source_generation',
   '(historical.source_generation<origin.source_generation OR (NEW.incarnation_mutation_id IS ' ||
    'NOT NULL AND historical.source_generation=origin.source_generation))');
 EXECUTE definition;
 definition:=pg_get_functiondef('app_private.verify_face_work_incarnation()'::regprocedure);
 definition:=replace(definition,'IF NEW.execution_incarnation>1 AND NOT EXISTS(',
   'IF NEW.execution_incarnation>1 AND NEW.incarnation_mutation_id IS NULL AND NOT EXISTS(');
 EXECUTE definition;
END $$;
CREATE FUNCTION app_private.fence_face_mutation_continuation() RETURNS trigger LANGUAGE
 plpgsql AS $$
DECLARE work ml.face_analysis_work%ROWTYPE; origin ml.face_analysis_sponsor%ROWTYPE; item
 ml.face_source_mutation_candidate%ROWTYPE; document jsonb; root ml.face_source_mutation%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='face_analysis_work' THEN
  IF NEW.incarnation_mutation_id IS NOT NULL THEN
   SELECT * INTO origin FROM ml.face_analysis_sponsor WHERE
    face_sponsor_id=NEW.incarnation_predecessor_sponsor_id;
   SELECT * INTO root FROM ml.face_source_mutation WHERE
    face_source_mutation_id=NEW.incarnation_mutation_id;
   SELECT convert_from(candidate_document,'UTF8')::jsonb INTO document FROM
    ml.face_source_mutation_candidate
     WHERE face_source_mutation_id=NEW.incarnation_mutation_id AND
      predecessor_sponsor_id=origin.face_sponsor_id;
   IF origin.audio_variant_id=NEW.audio_variant_id AND origin.source_sha256=NEW.source_sha256
     AND origin.redirect_generation IS NOT DISTINCT FROM
      (document->>'expected_redirect_generation')::bigint
     AND (origin.state='LIVE' OR NOT EXISTS(SELECT 1 FROM ml.face_source_mutation loss
       WHERE loss.recording_id=origin.recording_id AND loss.state='COMPLETE'
         AND loss.kind IN ('VARIANT','OBJECT','REPLICA','RECORDING') AND
          loss.target_audio_variant_id IS NULL
         AND loss.mutation_generation<root.mutation_generation AND
          origin.retired_at>=loss.created_at)) THEN
    RAISE EXCEPTION 'face_work_mutation_unchanged_source' USING ERRCODE='55000'; END IF;
  END IF;
  IF NEW.incarnation_mutation_id IS NOT NULL AND (NEW.execution_incarnation<=1 OR NOT
    app_private.face_mutation_origin_valid(NEW.incarnation_mutation_id,
     NEW.incarnation_predecessor_sponsor_id,
      NEW.identity_document,NEW.incarnation_source_generation)) THEN
   RAISE EXCEPTION 'face_work_mutation_origin_invalid' USING ERRCODE='55000'; END IF;
 ELSE
  IF NEW.source_mutation_id IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO work FROM ml.face_analysis_work WHERE face_work_id=NEW.face_work_id;
  SELECT * INTO origin FROM ml.face_analysis_sponsor WHERE
   face_sponsor_id=NEW.mutation_predecessor_sponsor_id;
  SELECT * INTO item FROM ml.face_source_mutation_candidate WHERE
   face_source_mutation_id=NEW.source_mutation_id
    AND predecessor_sponsor_id=NEW.mutation_predecessor_sponsor_id;
  document:=convert_from(item.candidate_document,'UTF8')::jsonb;
  IF NOT app_private.face_mutation_origin_valid(NEW.source_mutation_id,origin.face_sponsor_id,
   work.identity_document,NEW.source_generation)
    AND NOT (origin.state='SUPERSEDED' AND origin.successor_id=NEW.face_sponsor_id AND
     item.state='PENDING') THEN
   RAISE EXCEPTION 'face_sponsor_mutation_origin_invalid' USING ERRCODE='55000'; END IF;
  IF NEW.owner_user_id IS DISTINCT FROM origin.owner_user_id OR NEW.user_track_ref_id IS
   DISTINCT FROM origin.user_track_ref_id
    OR NEW.source_generation IS DISTINCT FROM origin.source_generation+1
    OR NEW.policy_generation IS DISTINCT FROM (document->>'expected_policy_generation')::bigint
    OR NEW.activation_epoch IS DISTINCT FROM (document->>'expected_activation_epoch')::bigint
    OR NEW.redirect_generation IS DISTINCT FROM
     (document->>'expected_redirect_generation')::bigint
    OR convert_from(work.identity_document,'UTF8')::jsonb IS DISTINCT FROM document->'identity'
    OR (origin.state='WITHDRAWN' AND NEW.predecessor_id IS NOT NULL)
    OR (origin.state='SUPERSEDED' AND NEW.predecessor_id IS DISTINCT FROM
     origin.face_sponsor_id) THEN
   RAISE EXCEPTION 'face_sponsor_mutation_origin_invalid' USING ERRCODE='55000'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION app_private.verify_face_mutation_continuation() RETURNS trigger LANGUAGE
 plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='face_analysis_work' THEN
  IF NEW.incarnation_mutation_id IS NULL THEN RETURN NULL; END IF;
  IF NOT EXISTS(SELECT 1 FROM ml.face_source_mutation_candidate item JOIN
   ml.face_analysis_sponsor s
    ON s.face_sponsor_id=item.successor_sponsor_id WHERE
     item.face_source_mutation_id=NEW.incarnation_mutation_id
      AND item.predecessor_sponsor_id=NEW.incarnation_predecessor_sponsor_id AND
       item.state='APPLIED'
      AND s.source_mutation_id=NEW.incarnation_mutation_id AND s.face_work_id=NEW.face_work_id
      AND s.source_generation=NEW.incarnation_source_generation) THEN
   RAISE EXCEPTION 'face_work_mutation_sponsor_required' USING ERRCODE='55000'; END IF;
 ELSE
  IF NEW.source_mutation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM
   ml.face_source_mutation_candidate item
    WHERE item.face_source_mutation_id=NEW.source_mutation_id AND
     item.predecessor_sponsor_id=NEW.mutation_predecessor_sponsor_id
      AND item.state='APPLIED' AND item.successor_sponsor_id=NEW.face_sponsor_id) THEN
   RAISE EXCEPTION 'face_sponsor_mutation_receipt_required' USING ERRCODE='55000'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER x_face_work_mutation_origin BEFORE INSERT ON ml.face_analysis_work
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_mutation_continuation();
CREATE TRIGGER x_face_sponsor_mutation_origin BEFORE INSERT ON ml.face_analysis_sponsor
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_mutation_continuation();
CREATE CONSTRAINT TRIGGER z_face_work_mutation_link AFTER INSERT ON ml.face_analysis_work
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
  app_private.verify_face_mutation_continuation();
CREATE CONSTRAINT TRIGGER z_face_sponsor_mutation_link AFTER INSERT ON ml.face_analysis_sponsor
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
  app_private.verify_face_mutation_continuation();
REVOKE ALL ON ml.face_redirect_authority,ml.face_source_mutation,
 ml.face_source_mutation_candidate FROM PUBLIC;
REVOKE ALL ON SEQUENCE ml.face_source_mutation_generation_seq FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.face_redirect_path(uuid),
 app_private.face_mutation_affects(uuid,uuid,uuid),
 app_private.face_mutation_has_remaining(uuid),app_private.capture_face_source_mutation(uuid,
  text,uuid,uuid,uuid,uuid),
 app_private.capture_face_source_dml(),app_private.fence_face_source_mutation(),
  app_private.pair_face_source_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.face_mutation_origin_valid(uuid,uuid,bytea,bigint),
 app_private.fence_face_mutation_continuation(),app_private.verify_face_mutation_continuation(),
 app_private.fence_face_redirect_authority(),app_private.verify_face_redirect_authority() FROM
  PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
LOCK TABLE ml.face_source_mutation IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM ml.face_source_mutation) THEN
 RAISE EXCEPTION 'face_mutation_downgrade_requires_empty_authority' USING ERRCODE='55000'; END
  IF; END $$;
DROP TRIGGER x_face_work_mutation_origin ON ml.face_analysis_work;
DROP TRIGGER x_face_sponsor_mutation_origin ON ml.face_analysis_sponsor;
DROP TRIGGER z_face_work_mutation_link ON ml.face_analysis_work;
DROP TRIGGER z_face_sponsor_mutation_link ON ml.face_analysis_sponsor;
DROP FUNCTION app_private.fence_face_mutation_continuation();
DROP FUNCTION app_private.verify_face_mutation_continuation();
DROP FUNCTION app_private.face_mutation_origin_valid(uuid,uuid,bytea,bigint);
DO $$ DECLARE definition text; BEGIN
 definition:=pg_get_functiondef('app_private.fence_face_sponsor_mutation()'::regprocedure);
 definition:=replace(definition,
  'IF NEW.source_mutation_id IS NULL AND EXISTS(SELECT 1 FROM ml.face_analysis_sponsor',
   'IF EXISTS(SELECT 1 FROM ml.face_analysis_sponsor'); EXECUTE definition;
 definition:=pg_get_functiondef('app_private.fence_face_work_incarnation()'::regprocedure);
 definition:=replace(definition,
  'FROM catalog.recording WHERE recording_id=NEW.recording_id FOR NO KEY UPDATE',
   'FROM catalog.recording WHERE recording_id=NEW.recording_id FOR UPDATE');
 definition:=replace(definition,
  '(origin.state IS DISTINCT FROM ''LIVE'' AND NOT (NEW.incarnation_mutation_id IS NOT NULL ' ||
   'AND origin.state=''WITHDRAWN''))',
   'origin.state IS DISTINCT FROM ''LIVE''');
 definition:=replace(definition,
  'OR (NEW.incarnation_mutation_id IS NULL AND origin.audio_variant_id=NEW.audio_variant_id ' ||
   'AND origin.source_sha256=NEW.source_sha256)',
   'OR (origin.audio_variant_id=NEW.audio_variant_id AND ' ||
    'origin.source_sha256=NEW.source_sha256)');
 definition:=replace(definition,
  '(historical.source_generation<origin.source_generation OR (NEW.incarnation_mutation_id IS ' ||
   'NOT NULL AND historical.source_generation=origin.source_generation))',
   'historical.source_generation<origin.source_generation'); EXECUTE definition;
 definition:=pg_get_functiondef('app_private.verify_face_work_incarnation()'::regprocedure);
 definition:=replace(definition,
  'IF NEW.execution_incarnation>1 AND NEW.incarnation_mutation_id IS NULL AND NOT EXISTS(',
   'IF NEW.execution_incarnation>1 AND NOT EXISTS('); EXECUTE definition;
END $$;
ALTER TABLE ml.face_analysis_sponsor DROP CONSTRAINT ck_face_sponsor_mutation_origin,
 DROP COLUMN source_mutation_id,DROP COLUMN mutation_predecessor_sponsor_id;
ALTER TABLE ml.face_analysis_work DROP COLUMN incarnation_mutation_id;
DROP TRIGGER z_face_replacement_mutation_pair ON ml.face_source_replacement;
DROP TRIGGER z_face_invalidation_mutation_pair ON ml.face_source_invalidation;
DROP TRIGGER z_face_canonical_capture ON vault.recording_canonical_variant;
DROP TRIGGER z_face_variant_capture ON vault.audio_variant;
DROP TRIGGER z_face_object_capture ON vault.vault_object;
DROP TRIGGER z_face_replica_capture ON vault.vault_replica;
DROP TRIGGER z_face_recording_capture ON catalog.recording;
DROP TRIGGER z_face_redirect_capture ON identity.recording_redirect;
DROP FUNCTION app_private.pair_face_source_mutation();
DROP FUNCTION app_private.capture_face_source_dml();
DROP FUNCTION app_private.capture_face_source_mutation(uuid,text,uuid,uuid,uuid,uuid);
DROP TABLE ml.face_source_mutation_candidate;
DROP TRIGGER z_face_source_mutation ON ml.face_source_mutation;
DROP FUNCTION app_private.fence_face_source_mutation();
DROP FUNCTION app_private.face_mutation_has_remaining(uuid);
DROP FUNCTION app_private.face_mutation_affects(uuid,uuid,uuid);
DROP FUNCTION app_private.face_redirect_path(uuid);
DROP TABLE ml.face_source_mutation;
DROP TABLE ml.face_redirect_authority;
DROP FUNCTION app_private.fence_face_redirect_authority();
DROP FUNCTION app_private.verify_face_redirect_authority();
DROP INDEX ml.ix_face_sponsor_live_reference;
DROP SEQUENCE ml.face_source_mutation_generation_seq;
""")
