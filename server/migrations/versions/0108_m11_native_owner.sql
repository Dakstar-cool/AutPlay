CREATE TABLE discovery.m11_native_owner (
 execution_id uuid PRIMARY KEY REFERENCES discovery.m11_priority_execution(execution_id) ON DELETE CASCADE,
 owner_run_id uuid NOT NULL,
 host_sha256 bytea NOT NULL CHECK(octet_length(host_sha256)=32),
 owner_pid bigint NOT NULL CHECK(owner_pid>0),
 owner_created bigint NOT NULL CHECK(owner_created>0),
 tree_name text NOT NULL CHECK(tree_name='autplay-ingest-'||replace(execution_id::text,'-','')),
 tree_device numeric(39,0) NOT NULL CHECK(tree_device>=0),
 tree_inode numeric(39,0) NOT NULL CHECK(tree_inode>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 child_pid bigint,
 child_created bigint,
 child_identity_sha256 bytea,
 child_registered_at timestamptz,
 closed_at timestamptz,
 closure_sha256 bytea,
 cleaned_at timestamptz,
 checked_at timestamptz,
 inspection_count bigint NOT NULL DEFAULT 0 CHECK(inspection_count>=0),
 CHECK((child_pid IS NULL AND child_created IS NULL AND child_identity_sha256 IS NULL AND child_registered_at IS NULL)
   OR (child_pid IS NOT NULL AND child_created IS NOT NULL
     AND child_identity_sha256 IS NOT NULL AND child_registered_at IS NOT NULL
     AND child_pid>0 AND child_created>0 AND octet_length(child_identity_sha256)=32
     AND child_registered_at>=created_at)),
 CHECK((closed_at IS NULL AND closure_sha256 IS NULL AND cleaned_at IS NULL)
   OR (closed_at IS NOT NULL AND closure_sha256 IS NOT NULL
     AND closed_at>=created_at AND octet_length(closure_sha256)=32
     AND (cleaned_at IS NULL OR cleaned_at>=closed_at)))
);
CREATE INDEX ix_m11_native_owner_recovery ON discovery.m11_native_owner(host_sha256,checked_at NULLS FIRST,execution_id)
 WHERE cleaned_at IS NULL;
CREATE FUNCTION app_private.guard_m11_native_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   IF OLD.closed_at IS NULL OR OLD.cleaned_at IS NULL THEN
     RAISE EXCEPTION 'm11_native_owner_retained' USING ERRCODE='55000';
   END IF;
   RETURN OLD;
 END IF;
 IF TG_OP='INSERT' THEN
   IF NEW.child_pid IS NOT NULL OR NEW.closed_at IS NOT NULL OR NEW.cleaned_at IS NOT NULL
     OR NOT EXISTS(SELECT 1 FROM account.resource_io_execution i
       JOIN discovery.m11_priority_execution p USING(execution_id)
       WHERE i.execution_id=NEW.execution_id AND i.owner_run_id=NEW.owner_run_id
       AND i.kind='PROVIDER' AND i.state='PREPARED' AND i.child_pid IS NULL
       AND p.started_at IS NULL AND p.closed_at IS NULL) THEN
     RAISE EXCEPTION 'm11_native_owner_original_required' USING ERRCODE='23514';
   END IF;
 ELSE
   IF OLD.child_pid IS NULL AND NEW.child_pid IS NOT NULL AND (
     OLD.closed_at IS NOT NULL OR NOT EXISTS(
       SELECT 1 FROM account.resource_io_execution i
       JOIN discovery.m11_priority_execution p USING(execution_id)
       WHERE i.execution_id=NEW.execution_id AND i.owner_run_id=NEW.owner_run_id
         AND i.state='PREPARED' AND p.started_at IS NULL AND p.closed_at IS NULL
     )) THEN
     RAISE EXCEPTION 'm11_native_child_original_required' USING ERRCODE='23514';
   END IF;
   IF ROW(NEW.execution_id,NEW.owner_run_id,NEW.host_sha256,NEW.owner_pid,NEW.owner_created,
      NEW.tree_name,NEW.tree_device,NEW.tree_inode,NEW.created_at)
     IS DISTINCT FROM ROW(OLD.execution_id,OLD.owner_run_id,OLD.host_sha256,OLD.owner_pid,OLD.owner_created,
      OLD.tree_name,OLD.tree_device,OLD.tree_inode,OLD.created_at)
     OR (OLD.child_pid IS NOT NULL AND ROW(NEW.child_pid,NEW.child_created,NEW.child_identity_sha256,NEW.child_registered_at)
       IS DISTINCT FROM ROW(OLD.child_pid,OLD.child_created,OLD.child_identity_sha256,OLD.child_registered_at))
     OR (OLD.closed_at IS NOT NULL AND ROW(NEW.closed_at,NEW.closure_sha256) IS DISTINCT FROM ROW(OLD.closed_at,OLD.closure_sha256))
     OR (OLD.cleaned_at IS NOT NULL AND NEW.cleaned_at IS DISTINCT FROM OLD.cleaned_at)
     OR NEW.inspection_count<OLD.inspection_count
     OR (OLD.checked_at IS NOT NULL AND (NEW.checked_at IS NULL OR NEW.checked_at<OLD.checked_at)) THEN
     RAISE EXCEPTION 'm11_native_owner_immutable' USING ERRCODE='23514';
   END IF;
 END IF;
 IF NEW.closed_at IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM discovery.m11_priority_execution p WHERE p.execution_id=NEW.execution_id
   AND p.closed_at=NEW.closed_at AND p.closure_sha256=NEW.closure_sha256
   AND p.settled_revision IS NOT NULL AND NOT p.quarantined
 ) THEN
   RAISE EXCEPTION 'm11_native_owner_accounting_required' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_m11_native_owner_guard BEFORE INSERT OR UPDATE OR DELETE ON discovery.m11_native_owner
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_m11_native_owner();
REVOKE ALL ON discovery.m11_native_owner FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.guard_m11_native_owner() FROM PUBLIC;
