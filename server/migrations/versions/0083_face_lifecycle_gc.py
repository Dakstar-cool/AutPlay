"""Bounded Face payload disposal, rollback retention and restore quarantine.

Revision ID: 0083_face_lifecycle_gc
Revises: 0082_face_source_mutation
"""

from alembic import op
from sqlalchemy import text

revision = "0083_face_lifecycle_gc"
down_revision = "0082_face_source_mutation"
branch_labels = None
depends_on = None

# Statement locks protect absence checks as well as existing rows. GC never
# locks owner/reference/work rows after taking this exclusive lock.
_FENCED = (
    "library.user_track_ref",
    "library.library_entry",
    "identity.recording_redirect",
    "vault.recording_canonical_variant",
    "vault.audio_variant",
    "vault.vault_object",
    "ml.face_analysis_sponsor",
    "ml.face_analysis_work",
    "ml.face_timeline",
    "ml.face_activation_current",
    "ml.artifact_license_current",
    "ml.face_projection",
    "ml.face_projection_fanout",
    "ml.face_projection_intent",
    "ml.face_projection_applied_receipt",
    "ml.face_projection_signed_upsert",
    "ml.face_restore_quarantine",
)


def upgrade() -> None:
    previous = op.get_bind().scalar(
        text(
            "SELECT pg_get_functiondef('app_private.fence_face_timeline_mutation()'::regprocedure)"
        )
    )
    op.get_bind().execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": "0083:timeline", "ddl": previous},
    )
    op.execute("""
CREATE TABLE ml.face_timeline_gc_receipt (
 face_timeline_id uuid PRIMARY KEY REFERENCES ml.face_timeline(face_timeline_id) ON DELETE RESTRICT,
 semantic_key_sha256 bytea NOT NULL CHECK(octet_length(semantic_key_sha256)=32),
 result_sha256 bytea NOT NULL CHECK(octet_length(result_sha256)=32),
 prior_generation bigint NOT NULL CHECK(prior_generation IN (1,2)),
 reason text NOT NULL CHECK(reason IN ('LAST_REFERENCE','RETENTION_EXPIRED')),
 removed_bytes integer NOT NULL CHECK(removed_bytes BETWEEN 1 AND 1048576),
 purged_at timestamptz NOT NULL
);
CREATE TABLE ml.face_rollback_retention (
 predecessor_activation_id uuid PRIMARY KEY
  REFERENCES ml.face_timeline_activation(face_timeline_activation_id) ON DELETE RESTRICT,
 successor_activation_id uuid NOT NULL UNIQUE
  REFERENCES ml.face_timeline_activation(face_timeline_activation_id) ON DELETE RESTRICT,
 retain_until timestamptz NOT NULL
);
CREATE TABLE ml.face_restore_quarantine (
 singleton smallint PRIMARY KEY CHECK(singleton=1),
 generation bigint NOT NULL CHECK(generation BETWEEN 0 AND 9007199254740990),
 quarantined_at timestamptz,
 restore_id uuid,
 CONSTRAINT ck_face_restore_quarantine_state CHECK(
  (generation=0 AND quarantined_at IS NULL AND restore_id IS NULL) OR
  (generation>0 AND quarantined_at IS NOT NULL AND restore_id IS NOT NULL))
);
INSERT INTO ml.face_restore_quarantine VALUES(1,0,NULL,NULL);

CREATE FUNCTION app_private.lock_face_lifecycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'face_lifecycle_requires_read_committed' USING ERRCODE='25001'; END IF;
 IF TG_TABLE_NAME='face_restore_quarantine' THEN
  IF NOT pg_try_advisory_xact_lock(4707761689340236883) THEN
   RAISE EXCEPTION 'face_lifecycle_contended' USING ERRCODE='55P03'; END IF;
 ELSE
  PERFORM pg_advisory_xact_lock_shared(4707761689340236883);
 END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION app_private.fence_face_restore_quarantine() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'face_lifecycle_requires_read_committed' USING ERRCODE='25001'; END IF;
 IF TG_OP<>'UPDATE' OR NEW.singleton<>OLD.singleton OR OLD.generation<>0
  OR NEW.generation<>1 OR NEW.quarantined_at IS NULL OR NEW.restore_id IS NULL THEN
  RAISE EXCEPTION 'face_restore_release_not_authorized' USING ERRCODE='55000'; END IF;
 PERFORM pg_advisory_xact_lock(4707761689340236883);
 NEW.quarantined_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_restore_quarantine BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_restore_quarantine FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_restore_quarantine();

CREATE FUNCTION app_private.capture_face_rollback_retention() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO ml.face_rollback_retention
 SELECT p.face_timeline_activation_id,n.face_timeline_activation_id,n.created_at+interval '30 days'
 FROM ml.face_timeline_activation n JOIN ml.face_timeline_activation p
  ON p.face_timeline_activation_id=n.previous_activation_id
 WHERE n.face_timeline_activation_id=NEW.face_timeline_activation_id
  AND n.action<>'DEACTIVATE' AND p.action<>'DEACTIVATE'
 ON CONFLICT DO NOTHING;
 RETURN NEW;
END $$;
-- Existing histories receive their original deadline, never a migration-time extension.
INSERT INTO ml.face_rollback_retention
 SELECT p.face_timeline_activation_id,n.face_timeline_activation_id,n.created_at+interval '30 days'
 FROM ml.face_timeline_activation n JOIN ml.face_timeline_activation p
 ON p.face_timeline_activation_id=n.previous_activation_id
 WHERE n.action<>'DEACTIVATE' AND p.action<>'DEACTIVATE';
CREATE TRIGGER z_face_rollback_retention AFTER UPDATE ON ml.face_activation_current
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_rollback_retention();
CREATE FUNCTION app_private.fence_face_lifecycle_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'face_lifecycle_evidence_immutable' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='face_rollback_retention' THEN
  IF NOT EXISTS(SELECT 1 FROM ml.face_timeline_activation n
   JOIN ml.face_timeline_activation p ON p.face_timeline_activation_id=n.previous_activation_id
   JOIN ml.face_activation_current c ON c.face_timeline_activation_id=n.face_timeline_activation_id
   WHERE n.face_timeline_activation_id=NEW.successor_activation_id
    AND p.face_timeline_activation_id=NEW.predecessor_activation_id
    AND n.action<>'DEACTIVATE' AND p.action<>'DEACTIVATE'
    AND NEW.retain_until=n.created_at+interval '30 days') THEN
   RAISE EXCEPTION 'face_retention_chain_invalid' USING ERRCODE='55000'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM ml.face_timeline t WHERE t.face_timeline_id=NEW.face_timeline_id
   AND t.state='PURGED' AND t.state_generation=NEW.prior_generation+2
   AND t.semantic_key_sha256=NEW.semantic_key_sha256 AND t.result_sha256=NEW.result_sha256
   AND t.canonical_byte_size=NEW.removed_bytes AND t.revoked_at=NEW.purged_at) THEN
   RAISE EXCEPTION 'face_gc_receipt_invalid' USING ERRCODE='55000'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_gc_receipt BEFORE INSERT OR UPDATE OR DELETE ON ml.face_timeline_gc_receipt
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_lifecycle_evidence();
CREATE TRIGGER z_face_retention_evidence BEFORE INSERT OR UPDATE OR DELETE ON ml.face_rollback_retention
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_lifecycle_evidence();

-- A NULL body is a permanent tombstone, with the original result digest and
-- byte count retained for audit. It is never an empty valid timeline.
ALTER TABLE ml.face_timeline ALTER COLUMN canonical_timeline DROP NOT NULL;
ALTER TABLE ml.face_timeline DROP CONSTRAINT face_timeline_state_check;
ALTER TABLE ml.face_timeline ADD CONSTRAINT face_timeline_state_check
 CHECK(state IN ('STAGED','PUBLISHED','PURGED'));
ALTER TABLE ml.face_timeline DROP CONSTRAINT face_timeline_state_generation_check;
ALTER TABLE ml.face_timeline ADD CONSTRAINT face_timeline_state_generation_check
 CHECK(state_generation IN (1,2,3,4));
ALTER TABLE ml.face_timeline DROP CONSTRAINT ck_face_timeline_state_times;
ALTER TABLE ml.face_timeline ADD CONSTRAINT ck_face_timeline_state_times CHECK(
 (state='STAGED' AND state_generation=1 AND published_at IS NULL AND revoked_at IS NULL
  AND canonical_timeline IS NOT NULL) OR
 (state='PUBLISHED' AND state_generation=2 AND published_at IS NOT NULL AND revoked_at IS NULL
  AND canonical_timeline IS NOT NULL) OR
 (state='PURGED' AND revoked_at IS NOT NULL AND canonical_timeline IS NULL AND
  ((state_generation=3 AND published_at IS NULL) OR (state_generation=4 AND published_at IS NOT NULL))));
CREATE INDEX ix_face_timeline_gc_pending ON ml.face_timeline(face_timeline_id) WHERE state<>'PURGED';

CREATE FUNCTION app_private.face_timeline_gc_reason(t ml.face_timeline) RETURNS text
 LANGUAGE plpgsql AS $$
DECLARE live_ref boolean; retained boolean; active boolean;
BEGIN
 IF t.face_timeline_id IS NULL OR t.state='PURGED' THEN RETURN NULL; END IF;
 -- No LIVE sponsor may be erased, including an owner whose policy needs reconciliation.
 IF EXISTS(SELECT 1 FROM ml.face_analysis_sponsor WHERE source_sha256=t.source_sha256
  AND state='LIVE') THEN RETURN NULL; END IF;
 -- Protect exact source bytes through direct and redirected references. An
 -- unresolved/cyclic path associated with this source is conservatively retained.
 SELECT EXISTS(SELECT 1 FROM library.user_track_ref r
  JOIN library.library_entry e ON e.user_track_ref_id=r.user_track_ref_id
   AND e.user_id=r.user_id AND e.removed_at IS NULL
  WHERE r.deleted_at IS NULL AND r.recording_id IS NOT NULL AND (
   r.recording_id=t.recording_id OR cardinality(app_private.face_redirect_path(r.recording_id))>17
   OR EXISTS(
    SELECT 1 FROM vault.audio_variant v JOIN vault.vault_object o USING(vault_object_id)
    WHERE o.sha256=t.source_sha256 AND (v.recording_id=r.recording_id OR
     v.recording_id=ANY(app_private.face_redirect_path(r.recording_id)))))) INTO live_ref;
 -- These are re-read while lifecycle writers are excluded, even when
 -- last-reference loss overrides all positive retention bases.
 SELECT EXISTS(SELECT 1 FROM ml.face_activation_current c JOIN ml.face_timeline_activation a
  USING(face_timeline_activation_id) WHERE a.action<>'DEACTIVATE'
  AND a.embedding_model_id=t.embedding_model_id
  AND a.face_semantic_interpreter_id=t.face_semantic_interpreter_id
  AND a.preprocessing_sha256=t.preprocessing_sha256
  AND a.execution_profile_sha256=t.execution_profile_sha256) INTO active;
 SELECT EXISTS(SELECT 1 FROM ml.face_rollback_retention r JOIN ml.face_timeline_activation a
  ON a.face_timeline_activation_id=r.predecessor_activation_id
  WHERE r.retain_until>clock_timestamp() AND a.embedding_model_id=t.embedding_model_id
  AND a.face_semantic_interpreter_id=t.face_semantic_interpreter_id
  AND a.preprocessing_sha256=t.preprocessing_sha256
  AND a.execution_profile_sha256=t.execution_profile_sha256) INTO retained;
 PERFORM c.effective_generation FROM ml.artifact_license_current c
  JOIN ml.face_activation_required_artifact a USING(artifact_sha256)
  JOIN ml.face_timeline_activation x USING(face_timeline_activation_id)
  WHERE x.embedding_model_id=t.embedding_model_id
   AND x.face_semantic_interpreter_id=t.face_semantic_interpreter_id
   AND x.preprocessing_sha256=t.preprocessing_sha256
   AND x.execution_profile_sha256=t.execution_profile_sha256;
 IF NOT live_ref THEN RETURN 'LAST_REFERENCE'; END IF;
 -- Absence of a retention record is not proof of an expired record.
 IF NOT active AND NOT retained AND EXISTS(SELECT 1 FROM ml.face_activation_current c
  JOIN ml.face_timeline_activation a USING(face_timeline_activation_id))
 AND EXISTS(SELECT 1 FROM ml.face_activation_required_artifact r
  JOIN ml.face_timeline_activation a USING(face_timeline_activation_id)
  WHERE a.embedding_model_id=t.embedding_model_id
   AND a.face_semantic_interpreter_id=t.face_semantic_interpreter_id
   AND a.preprocessing_sha256=t.preprocessing_sha256
   AND a.execution_profile_sha256=t.execution_profile_sha256)
 AND NOT EXISTS(SELECT 1 FROM ml.face_timeline_activation a
  LEFT JOIN ml.face_activation_artifact_provenance p USING(face_timeline_activation_id)
  WHERE a.embedding_model_id=t.embedding_model_id
   AND a.face_semantic_interpreter_id=t.face_semantic_interpreter_id
   AND a.preprocessing_sha256=t.preprocessing_sha256
   AND a.execution_profile_sha256=t.execution_profile_sha256
   AND (p.face_timeline_activation_id IS NULL OR
    (SELECT count(*) FROM ml.face_activation_required_artifact r
     WHERE r.face_timeline_activation_id=a.face_timeline_activation_id) IS DISTINCT FROM
    (SELECT sum(value::integer) FROM jsonb_each_text(p.required_role_cardinality)) OR
    EXISTS(SELECT 1 FROM jsonb_each_text(p.required_role_cardinality) role
     WHERE role.value::integer<>(SELECT count(*) FROM ml.face_activation_required_artifact r
      WHERE r.face_timeline_activation_id=a.face_timeline_activation_id AND r.role=role.key))))
 AND NOT EXISTS(SELECT 1 FROM ml.face_activation_required_artifact r
  JOIN ml.face_timeline_activation a USING(face_timeline_activation_id)
  LEFT JOIN ml.artifact_license_current c USING(artifact_sha256)
  WHERE a.embedding_model_id=t.embedding_model_id
   AND a.face_semantic_interpreter_id=t.face_semantic_interpreter_id
   AND a.preprocessing_sha256=t.preprocessing_sha256
   AND a.execution_profile_sha256=t.execution_profile_sha256
   AND (c.state IS DISTINCT FROM 'APPROVED' OR c.decision_sequence IS DISTINCT FROM r.decision_sequence OR c.effective_generation IS DISTINCT FROM r.decision_generation))
 AND EXISTS(SELECT 1 FROM ml.face_rollback_retention r
  JOIN ml.face_timeline_activation a ON a.face_timeline_activation_id=r.predecessor_activation_id
  WHERE r.retain_until<=clock_timestamp() AND a.embedding_model_id=t.embedding_model_id
   AND a.face_semantic_interpreter_id=t.face_semantic_interpreter_id
   AND a.preprocessing_sha256=t.preprocessing_sha256
   AND a.execution_profile_sha256=t.execution_profile_sha256) THEN RETURN 'RETENTION_EXPIRED'; END IF;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION app_private.fence_face_timeline_mutation() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND OLD.state='STAGED' AND NEW.state='PUBLISHED'
  AND NEW.state_generation=2 AND NEW.published_at IS NOT NULL AND NEW.revoked_at IS NULL
  AND to_jsonb(NEW)-ARRAY['state','state_generation','published_at'] IS NOT DISTINCT FROM
   to_jsonb(OLD)-ARRAY['state','state_generation','published_at'] THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND OLD.state IN ('STAGED','PUBLISHED') AND NEW.state='PURGED'
  AND NEW.state_generation=OLD.state_generation+2 AND NEW.canonical_timeline IS NULL
  AND NEW.revoked_at IS NOT NULL AND to_jsonb(NEW)-ARRAY[
   'state','state_generation','canonical_timeline','revoked_at'] IS NOT DISTINCT FROM
   to_jsonb(OLD)-ARRAY['state','state_generation','canonical_timeline','revoked_at'] THEN
  IF NOT pg_try_advisory_xact_lock(4707761689340236883) THEN
   RAISE EXCEPTION 'face_lifecycle_contended' USING ERRCODE='55P03'; END IF;
  IF app_private.face_timeline_gc_reason(OLD) IS NULL THEN
   RAISE EXCEPTION 'face_gc_authority_live' USING ERRCODE='55000'; END IF;
  NEW.revoked_at:=clock_timestamp(); RETURN NEW;
 END IF;
 RAISE EXCEPTION 'face_timeline_immutable' USING ERRCODE='55000';
END $$;
CREATE FUNCTION app_private.receipt_face_timeline_gc() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state='PURGED' AND OLD.state<>'PURGED' THEN
  INSERT INTO ml.face_timeline_gc_receipt VALUES(NEW.face_timeline_id,NEW.semantic_key_sha256,
   NEW.result_sha256,OLD.state_generation,
   app_private.face_timeline_gc_reason(OLD),NEW.canonical_byte_size,NEW.revoked_at);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_timeline_gc_receipt AFTER UPDATE ON ml.face_timeline
 FOR EACH ROW EXECUTE FUNCTION app_private.receipt_face_timeline_gc();
CREATE FUNCTION app_private.fence_face_lifecycle_positive() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='face_timeline' AND TG_OP='INSERT' THEN
  IF NEW.state<>'STAGED' OR NEW.state_generation<>1 THEN
   RAISE EXCEPTION 'face_timeline_initial_state_invalid' USING ERRCODE='55000'; END IF;
 END IF;
 IF TG_TABLE_NAME='face_analysis_work' AND TG_OP='UPDATE' THEN
  IF NEW.live_sponsor_count<OLD.live_sponsor_count
   AND NEW.work_authority_generation=OLD.work_authority_generation+1
   AND to_jsonb(NEW)-ARRAY['live_sponsor_count','work_authority_generation'] IS NOT DISTINCT FROM
       to_jsonb(OLD)-ARRAY['live_sponsor_count','work_authority_generation'] THEN RETURN NEW; END IF;
 END IF;
 IF (coalesce(to_jsonb(NEW)->>'state','') NOT IN ('PURGED','WITHDRAWN','SUPERSEDED','TOMBSTONED',
   'TERMINAL_INELIGIBLE','TERMINAL_FAILED','RETRY_EXHAUSTED','CANCELLED')
  AND coalesce(to_jsonb(NEW)->>'outcome','')<>'CANCELLED')
  AND EXISTS(SELECT 1 FROM ml.face_restore_quarantine WHERE generation>0) THEN
  RAISE EXCEPTION 'face_restore_quarantined' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON ml.face_timeline_gc_receipt,ml.face_rollback_retention,ml.face_restore_quarantine FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.lock_face_lifecycle(),app_private.fence_face_restore_quarantine(),
 app_private.capture_face_rollback_retention(),app_private.fence_face_lifecycle_evidence(),
 app_private.face_timeline_gc_reason(ml.face_timeline),app_private.receipt_face_timeline_gc(),
 app_private.fence_face_lifecycle_positive() FROM PUBLIC;
""")
    for table in _FENCED:
        op.execute(
            f"CREATE TRIGGER a_face_lifecycle_lock BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION app_private.lock_face_lifecycle()"
        )
    for table in (
        "face_timeline",
        "face_analysis_sponsor",
        "face_analysis_work",
        "face_projection",
        "face_projection_fanout",
        "face_projection_intent",
        "face_projection_applied_receipt",
        "face_projection_signed_upsert",
    ):
        op.execute(
            f"CREATE TRIGGER b_face_lifecycle_positive BEFORE INSERT OR UPDATE ON ml.{table} "
            "FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_lifecycle_positive()"
        )


def downgrade() -> None:
    op.execute("""
LOCK TABLE ml.face_timeline,ml.face_timeline_gc_receipt,ml.face_rollback_retention,
 ml.face_restore_quarantine IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_timeline_gc_receipt)
 OR EXISTS(SELECT 1 FROM ml.face_rollback_retention)
 OR EXISTS(SELECT 1 FROM ml.face_restore_quarantine WHERE generation>0) THEN
 RAISE EXCEPTION 'face_lifecycle_evidence_retained' USING ERRCODE='55000'; END IF;
END $$;
""")
    for table in _FENCED:
        op.execute(f"DROP TRIGGER a_face_lifecycle_lock ON {table}")
    for table in (
        "face_timeline",
        "face_analysis_sponsor",
        "face_analysis_work",
        "face_projection",
        "face_projection_fanout",
        "face_projection_intent",
        "face_projection_applied_receipt",
        "face_projection_signed_upsert",
    ):
        op.execute(f"DROP TRIGGER b_face_lifecycle_positive ON ml.{table}")
    op.execute("""
DROP TRIGGER z_face_timeline_gc_receipt ON ml.face_timeline;
DROP TRIGGER z_face_rollback_retention ON ml.face_activation_current;
DROP TABLE ml.face_timeline_gc_receipt;
DROP TABLE ml.face_rollback_retention;
DROP TABLE ml.face_restore_quarantine;
DROP FUNCTION app_private.receipt_face_timeline_gc();
DROP FUNCTION app_private.face_timeline_gc_reason(ml.face_timeline);
DROP FUNCTION app_private.fence_face_lifecycle_positive();
DROP FUNCTION app_private.fence_face_lifecycle_evidence();
DROP FUNCTION app_private.capture_face_rollback_retention();
DROP FUNCTION app_private.fence_face_restore_quarantine();
DROP FUNCTION app_private.lock_face_lifecycle();
DROP INDEX ml.ix_face_timeline_gc_pending;
ALTER TABLE ml.face_timeline ALTER COLUMN canonical_timeline SET NOT NULL;
ALTER TABLE ml.face_timeline DROP CONSTRAINT face_timeline_state_check;
ALTER TABLE ml.face_timeline ADD CONSTRAINT face_timeline_state_check CHECK(state IN ('STAGED','PUBLISHED'));
ALTER TABLE ml.face_timeline DROP CONSTRAINT face_timeline_state_generation_check;
ALTER TABLE ml.face_timeline ADD CONSTRAINT face_timeline_state_generation_check CHECK(state_generation IN (1,2));
ALTER TABLE ml.face_timeline DROP CONSTRAINT ck_face_timeline_state_times;
ALTER TABLE ml.face_timeline ADD CONSTRAINT ck_face_timeline_state_times CHECK(
 (state='STAGED' AND state_generation=1 AND published_at IS NULL AND revoked_at IS NULL) OR
 (state='PUBLISHED' AND state_generation=2 AND published_at IS NOT NULL AND revoked_at IS NULL));
""")
    previous = op.get_bind().scalar(
        text(
            "SELECT definition FROM app_private.privacy_prior_definition WHERE name='0083:timeline'"
        )
    )
    if not isinstance(previous, str):
        raise RuntimeError("missing Face timeline definition")
    op.execute(previous)
    op.execute("DELETE FROM app_private.privacy_prior_definition WHERE name='0083:timeline'")
