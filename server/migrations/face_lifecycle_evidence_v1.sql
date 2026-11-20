-- Only hashes and irreversible facts survive account erasure in this journal.
CREATE TABLE ml.face_lifecycle_ledger_binding (
 singleton smallint PRIMARY KEY CHECK(singleton=1), ledger_id uuid NOT NULL UNIQUE,
 server_profile_id uuid NOT NULL, coverage_started_at timestamptz NOT NULL,
 bound_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ml.face_lifecycle_fact (
 fact_id uuid PRIMARY KEY DEFAULT uuidv7(), transaction_id bigint NOT NULL,
 kind text NOT NULL CHECK(kind IN ('REFERENCE_REMOVED','SPONSOR_RETIRED',
  'PROJECTION_TOMBSTONED','TIMELINE_PURGED','REFERENCE_RESTORED')),
 entity_sha256 bytea NOT NULL CHECK(octet_length(entity_sha256)=32),
 generation bigint NOT NULL CHECK(generation BETWEEN 1 AND 9007199254740990),
 result_sha256 bytea CHECK(result_sha256 IS NULL OR octet_length(result_sha256)=32),
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT ck_face_fact_result CHECK(
  (kind IN ('TIMELINE_PURGED','REFERENCE_RESTORED'))=(result_sha256 IS NOT NULL)),
 CONSTRAINT uq_face_fact_entity UNIQUE(kind,entity_sha256,generation)
);
CREATE INDEX ix_face_lifecycle_fact_transaction ON ml.face_lifecycle_fact(transaction_id,fact_id);
CREATE TABLE ml.face_lifecycle_fact_receipt (
 fact_id uuid PRIMARY KEY REFERENCES ml.face_lifecycle_fact(fact_id) ON DELETE RESTRICT,
 ledger_id uuid NOT NULL, ledger_sequence bigint NOT NULL CHECK(ledger_sequence>0),
 ledger_digest bytea NOT NULL CHECK(octet_length(ledger_digest)=32),
 admission_mac bytea NOT NULL CHECK(octet_length(admission_mac)=32),
 CONSTRAINT uq_face_fact_ledger_sequence UNIQUE(ledger_id,ledger_sequence)
);
CREATE TABLE app_private.face_lifecycle_verifier (
 ledger_id uuid PRIMARY KEY REFERENCES ml.face_lifecycle_ledger_binding(ledger_id),
 verifier_key bytea NOT NULL CHECK(octet_length(verifier_key)=32)
);
CREATE TABLE app_private.face_lifecycle_restore_authorization (
 transaction_id bigint PRIMARY KEY, restore_id uuid NOT NULL,
 quarantine_generation bigint NOT NULL, ledger_id uuid NOT NULL,
 ledger_sequence bigint NOT NULL, ledger_digest bytea NOT NULL,
 privacy_heads_sha256 bytea NOT NULL, training_heads_sha256 bytea NOT NULL,
 serving_head_sha256 bytea NOT NULL
);
CREATE TABLE app_private.face_library_restore_authorization (
 transaction_id bigint NOT NULL, library_entry_id uuid NOT NULL,
 prior_generation bigint NOT NULL, authority_sha256 bytea NOT NULL,
 PRIMARY KEY(transaction_id,library_entry_id)
);
CREATE FUNCTION app_private.face_lifecycle_entity_tag(kind text,identifier uuid) RETURNS bytea
 LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path=pg_catalog AS $$
 SELECT sha256(convert_to('autplay.face.lifecycle-entity.v1:'||kind,'UTF8')||uuid_send(identifier)) $$;
CREATE FUNCTION app_private.face_library_reference_authority(identifier uuid) RETURNS bytea
 LANGUAGE sql STABLE STRICT SET search_path=pg_catalog AS $$
 -- A committed library successor authorizes this logical owner/reference only.
 -- Source validity and Face authority have their own independently replayed fences.
 SELECT sha256(convert_to('autplay.face.library-authority.v2','UTF8') ||
  convert_to(jsonb_build_array(e.user_id,e.library_entry_id,e.user_track_ref_id)::text,'UTF8'))
 FROM library.library_entry e
 WHERE e.library_entry_id=identifier $$;
CREATE FUNCTION app_private.admit_face_library_restore(
 identifier uuid,owner_id uuid,prior bigint,authority bytea,proof bytea) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE payload text; ledger uuid; verifier bytea;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(4707761689340236883);
 SELECT b.ledger_id,v.verifier_key INTO ledger,verifier
 FROM ml.face_lifecycle_ledger_binding b JOIN app_private.face_lifecycle_verifier v USING(ledger_id);
 payload:='library-restore-v1|'||pg_current_xact_id()::text||'|'||ledger::text||'|'||
  identifier::text||'|'||owner_id::text||'|'||prior::text||'|'||encode(authority,'hex');
 IF NOT EXISTS(SELECT 1 FROM library.library_entry e
  JOIN account.user_account u ON u.user_id=e.user_id AND u.status='ACTIVE' AND u.deleted_at IS NULL
  JOIN library.user_track_ref r ON r.user_track_ref_id=e.user_track_ref_id
   AND r.user_id=e.user_id AND r.deleted_at IS NULL
  WHERE e.library_entry_id=identifier AND e.user_id=owner_id AND e.row_version=prior
   AND e.removed_at IS NOT NULL AND (
    EXISTS(SELECT 1 FROM ml.face_lifecycle_fact f
     JOIN ml.face_lifecycle_fact_receipt receipt USING(fact_id)
     WHERE f.kind='REFERENCE_REMOVED' AND f.generation=prior AND receipt.ledger_id=ledger
      AND f.entity_sha256=app_private.face_lifecycle_entity_tag('REFERENCE_REMOVED',identifier))
    OR e.removed_at<(SELECT coverage_started_at FROM ml.face_lifecycle_ledger_binding)))
  OR authority IS DISTINCT FROM app_private.face_library_reference_authority(identifier)
  OR proof IS DISTINCT FROM public.hmac(convert_to(payload,'UTF8'),verifier,'sha256') THEN
  RAISE EXCEPTION 'face_library_restore_unproven' USING ERRCODE='55000'; END IF;
 INSERT INTO app_private.face_library_restore_authorization VALUES
  (pg_current_xact_id()::text::bigint,identifier,prior,authority);
END $$;
CREATE FUNCTION app_private.verify_face_library_restore_consumed() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM app_private.face_library_restore_authorization a
  WHERE a.transaction_id=NEW.transaction_id AND a.library_entry_id=NEW.library_entry_id) THEN
  RAISE EXCEPTION 'face_library_restore_not_consumed' USING ERRCODE='55000'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER z_face_library_restore_consumed AFTER INSERT
 ON app_private.face_library_restore_authorization DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION app_private.verify_face_library_restore_consumed();
CREATE FUNCTION app_private.fence_face_lifecycle_journal() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'face_lifecycle_journal_immutable' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='face_lifecycle_ledger_binding' THEN
  PERFORM pg_advisory_xact_lock(4707761689340236883);
  IF EXISTS(SELECT 1 FROM ml.face_analysis_sponsor) OR EXISTS(SELECT 1 FROM ml.face_timeline)
   OR NOT EXISTS(SELECT 1 FROM account.server_instance
    WHERE server_instance_id=NEW.server_profile_id)
   OR NEW.coverage_started_at>clock_timestamp() THEN
   RAISE EXCEPTION 'face_lifecycle_cutover_not_empty' USING ERRCODE='55000'; END IF;
 ELSIF TG_TABLE_NAME='face_lifecycle_fact' THEN
  IF pg_trigger_depth()<2 OR NEW.transaction_id<>pg_current_xact_id()::text::bigint THEN
   RAISE EXCEPTION 'face_lifecycle_fact_unproven' USING ERRCODE='55000'; END IF;
  IF NEW.kind='REFERENCE_RESTORED' AND NOT EXISTS(
   SELECT 1 FROM app_private.face_library_restore_authorization a
   JOIN library.library_entry e ON e.library_entry_id=a.library_entry_id
   WHERE a.transaction_id=NEW.transaction_id AND e.removed_at IS NULL
    AND e.row_version=NEW.generation AND a.prior_generation+1=NEW.generation
    AND NEW.entity_sha256=app_private.face_lifecycle_entity_tag('REFERENCE_REMOVED',a.library_entry_id)
    AND NEW.result_sha256=a.authority_sha256
    AND NEW.result_sha256=app_private.face_library_reference_authority(a.library_entry_id)) THEN
   RAISE EXCEPTION 'face_library_restore_unproven' USING ERRCODE='55000'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM app_private.face_lifecycle_verifier v,
    ml.face_lifecycle_fact f WHERE v.ledger_id=NEW.ledger_id AND f.fact_id=NEW.fact_id
     AND NEW.admission_mac=public.hmac(convert_to('receipt-v1|'||NEW.ledger_id::text||'|'||
      f.fact_id::text||'|'||f.kind||'|'||encode(f.entity_sha256,'hex')||'|'||f.generation::text||'|'||
      coalesce(encode(f.result_sha256,'hex'),'-')||'|'||
      to_char(f.occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')||'|'||
      NEW.ledger_sequence::text||'|'||encode(NEW.ledger_digest,'hex'),'UTF8'),
      v.verifier_key,'sha256')) THEN
   RAISE EXCEPTION 'face_lifecycle_receipt_unproven' USING ERRCODE='55000'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_ledger_binding BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_lifecycle_ledger_binding FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_lifecycle_journal();
CREATE TRIGGER z_face_lifecycle_fact BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_lifecycle_fact FOR EACH ROW EXECUTE FUNCTION app_private.fence_face_lifecycle_journal();
CREATE TRIGGER z_face_lifecycle_fact_receipt BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_lifecycle_fact_receipt FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_lifecycle_journal();
CREATE FUNCTION app_private.verify_face_lifecycle_independent_commit() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ml.face_lifecycle_fact_receipt r
  JOIN ml.face_lifecycle_ledger_binding b ON b.ledger_id=r.ledger_id WHERE r.fact_id=NEW.fact_id) THEN
  RAISE EXCEPTION 'face_lifecycle_independent_receipt_required' USING ERRCODE='55000'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER z_face_lifecycle_independent_commit AFTER INSERT
 ON ml.face_lifecycle_fact DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.verify_face_lifecycle_independent_commit();
CREATE FUNCTION app_private.capture_face_lifecycle_fact() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE fact_kind text; identifier uuid; fact_generation bigint; result_hash bytea;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ml.face_lifecycle_ledger_binding) OR EXISTS(
  SELECT 1 FROM app_private.face_lifecycle_restore_authorization
  WHERE transaction_id=pg_current_xact_id()::text::bigint) THEN RETURN NULL; END IF;
 IF TG_TABLE_NAME='library_entry' THEN
  IF TG_OP='DELETE' THEN
   IF OLD.removed_at IS NOT NULL THEN RETURN NULL; END IF;
   fact_generation:=OLD.row_version+1; identifier:=OLD.library_entry_id;
  ELSE
   IF OLD.removed_at IS NOT NULL AND NEW.removed_at IS NULL THEN
    SELECT a.authority_sha256 INTO result_hash
    FROM app_private.face_library_restore_authorization a
    WHERE a.transaction_id=pg_current_xact_id()::text::bigint
     AND a.library_entry_id=NEW.library_entry_id AND a.prior_generation=OLD.row_version;
    IF result_hash IS NULL OR NEW.row_version<>OLD.row_version+1
     OR result_hash IS DISTINCT FROM app_private.face_library_reference_authority(NEW.library_entry_id)
     THEN RAISE EXCEPTION 'face_library_restore_unproven' USING ERRCODE='55000'; END IF;
    fact_kind:='REFERENCE_RESTORED';
   ELSIF OLD.removed_at IS NOT NULL OR NEW.removed_at IS NULL THEN RETURN NULL; END IF;
   fact_generation:=NEW.row_version; identifier:=NEW.library_entry_id;
  END IF;
  fact_kind:=coalesce(fact_kind,'REFERENCE_REMOVED');
 ELSIF TG_TABLE_NAME='face_analysis_sponsor' THEN
  IF OLD.state<>'LIVE' OR NEW.state='LIVE' THEN RETURN NULL; END IF;
  fact_kind:='SPONSOR_RETIRED'; identifier:=NEW.face_sponsor_id;
  fact_generation:=NEW.sponsor_generation;
 ELSIF TG_TABLE_NAME='face_projection' THEN
  IF OLD.state<>'ACTIVE' OR NEW.state<>'TOMBSTONED' THEN RETURN NULL; END IF;
  fact_kind:='PROJECTION_TOMBSTONED'; identifier:=NEW.face_projection_id;
  fact_generation:=NEW.projection_generation;
 ELSE
  IF NEW.state<>'PURGED' OR OLD.state='PURGED' THEN RETURN NULL; END IF;
  fact_kind:='TIMELINE_PURGED'; identifier:=NEW.face_timeline_id;
  fact_generation:=NEW.state_generation; result_hash:=NEW.result_sha256;
 END IF;
 INSERT INTO ml.face_lifecycle_fact(transaction_id,kind,entity_sha256,generation,result_sha256)
 VALUES(pg_current_xact_id()::text::bigint,fact_kind,
  app_private.face_lifecycle_entity_tag(
   CASE WHEN fact_kind='REFERENCE_RESTORED' THEN 'REFERENCE_REMOVED' ELSE fact_kind END,identifier),
  fact_generation,result_hash)
 ON CONFLICT(kind,entity_sha256,generation) DO NOTHING;
 IF fact_kind='REFERENCE_RESTORED' THEN
  DELETE FROM app_private.face_library_restore_authorization
   WHERE transaction_id=pg_current_xact_id()::text::bigint AND library_entry_id=identifier;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER zz_face_reference_fact AFTER UPDATE OR DELETE ON library.library_entry
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_lifecycle_fact();
CREATE TRIGGER zzz_face_sponsor_fact AFTER UPDATE ON ml.face_analysis_sponsor
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_lifecycle_fact();
CREATE TRIGGER zzz_face_projection_fact AFTER UPDATE ON ml.face_projection
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_lifecycle_fact();
CREATE TRIGGER zzz_face_timeline_fact AFTER UPDATE ON ml.face_timeline
 FOR EACH ROW EXECUTE FUNCTION app_private.capture_face_lifecycle_fact();
REVOKE ALL ON ml.face_lifecycle_ledger_binding,ml.face_lifecycle_fact,
 ml.face_lifecycle_fact_receipt,app_private.face_lifecycle_verifier,
 app_private.face_lifecycle_restore_authorization FROM PUBLIC;
REVOKE ALL ON app_private.face_library_restore_authorization FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.face_library_reference_authority(uuid),
 app_private.admit_face_library_restore(uuid,uuid,bigint,bytea,bytea),
 app_private.verify_face_library_restore_consumed() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.face_lifecycle_entity_tag(text,uuid),
 app_private.fence_face_lifecycle_journal(),app_private.verify_face_lifecycle_independent_commit(),
 app_private.capture_face_lifecycle_fact() FROM PUBLIC;

ALTER TABLE ml.face_restore_quarantine ADD COLUMN released_at timestamptz,
 ADD CONSTRAINT ck_face_restore_release_time CHECK(released_at IS NULL OR
  (generation>0 AND released_at>=quarantined_at));
CREATE TABLE ml.face_restore_reconciliation (
 restore_id uuid PRIMARY KEY, quarantine_generation bigint NOT NULL UNIQUE,
 ledger_id uuid NOT NULL, ledger_sequence bigint NOT NULL CHECK(ledger_sequence>=0),
 ledger_digest bytea NOT NULL CHECK(octet_length(ledger_digest)=32),
 privacy_heads_sha256 bytea NOT NULL CHECK(octet_length(privacy_heads_sha256)=32),
 training_heads_sha256 bytea NOT NULL CHECK(octet_length(training_heads_sha256)=32),
 serving_head_sha256 bytea NOT NULL CHECK(octet_length(serving_head_sha256)=32),
 reconciled_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- Only the independent-ledger holder can admit this exact transaction and head set.
-- The caller may EXECUTE these functions, but may never write/read private verifier state.
CREATE FUNCTION app_private.begin_face_restore(
 operation uuid,generation bigint,ledger uuid,sequence bigint,digest bytea,
 privacy bytea,training bytea,serving bytea,proof bytea) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE payload text;
BEGIN
 payload:='restore-v1|'||pg_current_xact_id()::text||'|'||operation::text||'|'||
  generation::text||'|'||ledger::text||'|'||sequence::text||'|'||encode(digest,'hex')||'|'||
  encode(privacy,'hex')||'|'||encode(training,'hex')||'|'||encode(serving,'hex');
 IF NOT EXISTS(SELECT 1 FROM app_private.face_lifecycle_verifier v
  JOIN ml.face_restore_quarantine q ON q.restore_id=operation
   AND q.generation=begin_face_restore.generation
   AND q.released_at IS NULL WHERE v.ledger_id=ledger
   AND proof=public.hmac(convert_to(payload,'UTF8'),v.verifier_key,'sha256')) THEN
  RAISE EXCEPTION 'face_restore_admission_unproven' USING ERRCODE='55000'; END IF;
 INSERT INTO app_private.face_lifecycle_restore_authorization VALUES
  (pg_current_xact_id()::text::bigint,operation,generation,ledger,sequence,digest,
   privacy,training,serving);
END $$;
CREATE FUNCTION app_private.end_face_restore() RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 DELETE FROM app_private.face_lifecycle_restore_authorization
 WHERE transaction_id=pg_current_xact_id()::text::bigint $$;
CREATE FUNCTION app_private.fence_face_restore_reconciliation() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP<>'INSERT' OR NOT EXISTS(SELECT 1 FROM app_private.face_lifecycle_restore_authorization a
  JOIN ml.face_restore_quarantine q ON q.restore_id=a.restore_id
  JOIN ml.face_lifecycle_ledger_binding b ON b.ledger_id=NEW.ledger_id
  WHERE a.transaction_id=pg_current_xact_id()::text::bigint AND a.restore_id=NEW.restore_id
   AND q.released_at IS NULL AND q.generation=NEW.quarantine_generation
   AND (a.quarantine_generation,a.ledger_id,a.ledger_sequence,a.ledger_digest,
    a.privacy_heads_sha256,a.training_heads_sha256,a.serving_head_sha256)=
    (NEW.quarantine_generation,NEW.ledger_id,NEW.ledger_sequence,NEW.ledger_digest,
     NEW.privacy_heads_sha256,NEW.training_heads_sha256,NEW.serving_head_sha256)) THEN
  RAISE EXCEPTION 'face_restore_reconciliation_unproven' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_face_restore_reconciliation BEFORE INSERT OR UPDATE OR DELETE
 ON ml.face_restore_reconciliation FOR EACH ROW
 EXECUTE FUNCTION app_private.fence_face_restore_reconciliation();
CREATE OR REPLACE FUNCTION app_private.fence_face_restore_quarantine() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'face_lifecycle_requires_read_committed' USING ERRCODE='25001'; END IF;
 IF TG_OP='UPDATE' AND NEW.singleton=OLD.singleton THEN
  IF (OLD.generation=0 OR OLD.released_at IS NOT NULL)
   AND NEW.generation=OLD.generation+1 AND NEW.restore_id IS NOT NULL
   AND NEW.restore_id IS DISTINCT FROM OLD.restore_id AND NEW.released_at IS NULL THEN
   NEW.quarantined_at:=clock_timestamp(); RETURN NEW;
  END IF;
  IF OLD.generation>0 AND OLD.released_at IS NULL AND NEW.released_at IS NOT NULL
   AND to_jsonb(NEW)-'released_at' IS NOT DISTINCT FROM to_jsonb(OLD)-'released_at'
   AND EXISTS(SELECT 1 FROM ml.face_restore_reconciliation r
    JOIN app_private.face_lifecycle_restore_authorization a ON a.restore_id=r.restore_id
    WHERE r.restore_id=OLD.restore_id AND r.quarantine_generation=OLD.generation
     AND a.transaction_id=pg_current_xact_id()::text::bigint) THEN
   NEW.released_at:=clock_timestamp(); RETURN NEW;
  END IF;
 END IF;
 RAISE EXCEPTION 'face_restore_release_not_authorized' USING ERRCODE='55000';
END $$;
REVOKE ALL ON ml.face_restore_reconciliation FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.fence_face_restore_reconciliation() FROM PUBLIC;
REVOKE ALL ON FUNCTION
 app_private.begin_face_restore(uuid,bigint,uuid,bigint,bytea,bytea,bytea,bytea,bytea),
 app_private.end_face_restore() FROM PUBLIC;
