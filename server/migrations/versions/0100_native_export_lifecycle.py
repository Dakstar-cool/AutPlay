"""Dormant managed native export ownership and retained cleanup obligations."""

from alembic import op

revision = "0100_native_export_lifecycle"
down_revision = "0099_favorite_refresh_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.native_export (
 export_id uuid NOT NULL, generation uuid NOT NULL, kind text NOT NULL,
 allocation jsonb NOT NULL, allocation_sha256 text NOT NULL,
 expires_at timestamptz NOT NULL, state text NOT NULL, inventory jsonb NOT NULL,
 manifest_sha256 text, reason text,
 PRIMARY KEY(export_id,generation),
 CONSTRAINT ck_native_export_kind CHECK(kind IN ('PACKET','AGGREGATE')),
 CONSTRAINT ck_native_export_state CHECK(state IN ('ALLOCATED','READY','INVALIDATED','DELETED')),
 CONSTRAINT ck_native_export_hash CHECK(allocation_sha256 ~ '^[0-9a-f]{64}$' AND
  (manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$')),
 CONSTRAINT ck_native_export_metadata CHECK(jsonb_typeof(allocation)='object' AND
  octet_length(allocation::text)<=524288 AND jsonb_typeof(inventory)='array' AND
  jsonb_array_length(inventory)<=11),
 CONSTRAINT ck_native_export_ready CHECK(state<>'READY' OR
  (manifest_sha256 IS NOT NULL AND jsonb_array_length(inventory)>0))
);
CREATE TABLE ml.native_export_owner (
 export_id uuid NOT NULL, generation uuid NOT NULL, user_id uuid NOT NULL, owner_tag text NOT NULL,
 PRIMARY KEY(export_id,generation,user_id),
 FOREIGN KEY(export_id,generation) REFERENCES ml.native_export(export_id,generation) ON DELETE RESTRICT,
 CONSTRAINT ck_native_export_owner_tag CHECK(owner_tag ~ '^[0-9a-f]{64}$')
);
CREATE TABLE ml.native_export_dependency (
 export_id uuid NOT NULL, generation uuid NOT NULL,
 packet_id uuid NOT NULL, packet_generation uuid NOT NULL, manifest_sha256 text NOT NULL,
 PRIMARY KEY(export_id,generation,packet_id,packet_generation),
 FOREIGN KEY(export_id,generation) REFERENCES ml.native_export(export_id,generation) ON DELETE RESTRICT,
 FOREIGN KEY(packet_id,packet_generation) REFERENCES ml.native_export(export_id,generation) ON DELETE RESTRICT,
 CONSTRAINT ck_native_export_dependency_hash CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_native_export_dependency_self CHECK(export_id<>packet_id)
);
CREATE INDEX ix_native_export_work ON ml.native_export(state,expires_at,export_id,generation);
CREATE INDEX ix_native_export_owner_lookup ON ml.native_export_owner(user_id,export_id,generation);
CREATE INDEX ix_native_export_dependency_parent ON ml.native_export_dependency(packet_id,packet_generation);
REVOKE ALL ON ml.native_export,ml.native_export_owner,ml.native_export_dependency FROM PUBLIC;

CREATE FUNCTION app_private.guard_native_export() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'native_export_cleanup_obligation_retained' USING ERRCODE='55000';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'ALLOCATED' OR NEW.inventory<>'[]'::jsonb OR NEW.manifest_sha256 IS NOT NULL
   OR NEW.expires_at<=clock_timestamp() THEN
   RAISE EXCEPTION 'native_export_allocation_required' USING ERRCODE='55000';
  END IF;
 ELSE
  IF (NEW.export_id,NEW.generation,NEW.kind,NEW.allocation,NEW.allocation_sha256,NEW.expires_at)
    IS DISTINCT FROM
    (OLD.export_id,OLD.generation,OLD.kind,OLD.allocation,OLD.allocation_sha256,OLD.expires_at)
   OR NOT (NEW.state=OLD.state OR (OLD.state='ALLOCATED' AND NEW.state IN ('READY','INVALIDATED'))
    OR (OLD.state='READY' AND NEW.state='INVALIDATED')
    OR (OLD.state='INVALIDATED' AND NEW.state='DELETED'))
   OR (OLD.state<>'ALLOCATED' AND (NEW.inventory,NEW.manifest_sha256)
       IS DISTINCT FROM (OLD.inventory,OLD.manifest_sha256)) THEN
   RAISE EXCEPTION 'native_export_transition_invalid' USING ERRCODE='55000';
  END IF;
 END IF;
 IF NEW.state='READY' THEN
  IF NEW.expires_at<=clock_timestamp() OR
   (SELECT count(*) FROM ml.native_export_owner o WHERE
    (o.export_id,o.generation)=(NEW.export_id,NEW.generation))
     IS DISTINCT FROM jsonb_array_length(NEW.allocation->'owners') OR
   jsonb_array_length(NEW.allocation->'owners') NOT BETWEEN 1 AND 1024 OR
   jsonb_array_length(NEW.inventory)<>(CASE WHEN NEW.kind='PACKET' THEN 11 ELSE 1 END) OR
   (SELECT count(*) FROM ml.native_export_dependency d WHERE
    (d.export_id,d.generation)=(NEW.export_id,NEW.generation))
     IS DISTINCT FROM jsonb_array_length(NEW.allocation->'dependencies') OR
   EXISTS(SELECT 1 FROM ml.native_export_dependency d JOIN ml.native_export p
    ON (p.export_id,p.generation)=(d.packet_id,d.packet_generation)
    WHERE (d.export_id,d.generation)=(NEW.export_id,NEW.generation) AND
     (p.kind<>'PACKET' OR p.state<>'READY' OR p.manifest_sha256<>d.manifest_sha256
      OR p.expires_at<NEW.expires_at)) THEN
   RAISE EXCEPTION 'native_export_authority_unavailable' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER native_export_guard BEFORE INSERT OR UPDATE OR DELETE ON ml.native_export
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_native_export();

CREATE FUNCTION app_private.guard_native_export_link() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent ml.native_export; packet ml.native_export;
BEGIN
 IF TG_OP='UPDATE' THEN
  RAISE EXCEPTION 'native_export_membership_immutable' USING ERRCODE='55000';
 ELSIF TG_OP='DELETE' THEN
  IF TG_TABLE_NAME='native_export_dependency' THEN
   RAISE EXCEPTION 'native_export_membership_retained' USING ERRCODE='55000';
  END IF;
  -- A restored ACTIVE account is still visible during its BEFORE DELETE trigger.
  -- Only the existing owner- and transaction-bound private purge context authorizes removal.
  IF EXISTS(
   SELECT 1 FROM account.user_account WHERE user_id=OLD.user_id AND status='ACTIVE' AND deleted_at IS NULL
  ) AND NOT app_private.privacy_owner_allowed(OLD.user_id) THEN
   RAISE EXCEPTION 'native_export_membership_retained' USING ERRCODE='55000';
  END IF;
  RETURN OLD;
 END IF;
 SELECT * INTO parent FROM ml.native_export
  WHERE (export_id,generation)=(NEW.export_id,NEW.generation) FOR UPDATE;
 IF parent.state IS DISTINCT FROM 'ALLOCATED' THEN
  RAISE EXCEPTION 'native_export_allocation_required' USING ERRCODE='55000';
 END IF;
 IF TG_TABLE_NAME='native_export_owner' THEN
  IF NOT EXISTS(SELECT 1 FROM account.user_account WHERE user_id=NEW.user_id AND status='ACTIVE') OR
   NOT EXISTS(SELECT 1 FROM jsonb_array_elements(parent.allocation->'owners') item
     WHERE item->>'owner_tag'=NEW.owner_tag) OR EXISTS(
     SELECT 1 FROM ml.native_export_owner WHERE (export_id,generation)=(NEW.export_id,NEW.generation)
      AND owner_tag=NEW.owner_tag) THEN
   RAISE EXCEPTION 'native_export_owner_mismatch' USING ERRCODE='55000';
  END IF;
 ELSE
  SELECT * INTO packet FROM ml.native_export
   WHERE (export_id,generation)=(NEW.packet_id,NEW.packet_generation) FOR UPDATE;
  IF parent.kind<>'AGGREGATE' OR packet.kind IS DISTINCT FROM 'PACKET' OR packet.state<>'READY'
   OR packet.manifest_sha256<>NEW.manifest_sha256 OR packet.expires_at<parent.expires_at OR
   packet.allocation->'root' IS DISTINCT FROM parent.allocation->'root' OR NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(parent.allocation->'dependencies') item
    WHERE item->'ref'->>'export_id'=NEW.packet_id::text AND
     item->'ref'->>'generation'=NEW.packet_generation::text AND
     item->>'manifest_sha256'=NEW.manifest_sha256) THEN
   RAISE EXCEPTION 'native_export_dependency_mismatch' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER native_export_owner_guard BEFORE INSERT OR UPDATE OR DELETE ON ml.native_export_owner
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_native_export_link();
CREATE TRIGGER native_export_dependency_guard BEFORE INSERT OR UPDATE OR DELETE ON ml.native_export_dependency
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_native_export_link();

CREATE FUNCTION app_private.invalidate_native_export_owner() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid; negative_reason text;
BEGIN
 IF TG_OP='INSERT' THEN target:=NEW.user_id; ELSE target:=OLD.user_id; END IF;
 negative_reason:='SOURCE_REMOVED';
 IF TG_TABLE_SCHEMA='account' AND TG_TABLE_NAME='user_account' THEN
  IF TG_OP='UPDATE' AND NEW.status='ACTIVE' AND NEW.deleted_at IS NULL THEN RETURN NEW; END IF;
  IF TG_OP='DELETE' THEN negative_reason:='OWNER_PURGED';
  ELSIF NEW.status='DELETION_PENDING' THEN negative_reason:='OWNER_DELETION_PENDING';
  ELSE negative_reason:='OWNER_ACCESS_CLOSED'; END IF;
 ELSIF TG_TABLE_NAME='training_consent' THEN
  IF TG_OP='UPDATE' AND NEW.decision=OLD.decision AND NEW.revision=OLD.revision THEN RETURN NEW; END IF;
  negative_reason:='CONSENT_CHANGED';
 ELSIF TG_TABLE_NAME='sona_event_erasure_receipt' THEN negative_reason:='OWNER_ERASURE_CHANGED';
 END IF;
 UPDATE ml.native_export e SET state='INVALIDATED',reason=negative_reason
  FROM ml.native_export_owner o WHERE (e.export_id,e.generation)=(o.export_id,o.generation)
   AND o.user_id=target AND (e.state IN ('ALLOCATED','READY') OR
    (e.state='INVALIDATED' AND e.reason IN
      ('OWNER_DELETION_PENDING','OWNER_ACCESS_CLOSED','OWNER_REMOVED','DEPENDENCY_INVALIDATED')
     AND negative_reason IN
      ('SOURCE_REMOVED','OWNER_PURGED','CONSENT_CHANGED','OWNER_ERASURE_CHANGED')));
 IF TG_TABLE_SCHEMA='account' AND TG_TABLE_NAME='user_account' AND TG_OP='DELETE' THEN
  DELETE FROM ml.native_export_owner WHERE user_id=target;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
CREATE TRIGGER native_export_account_negative BEFORE UPDATE OR DELETE ON account.user_account
 FOR EACH ROW EXECUTE FUNCTION app_private.invalidate_native_export_owner();
CREATE TRIGGER native_export_consent_negative AFTER UPDATE OR DELETE ON account.training_consent
 FOR EACH ROW EXECUTE FUNCTION app_private.invalidate_native_export_owner();
CREATE TRIGGER native_export_source_negative BEFORE DELETE ON ml.sona_capture_bundle
 FOR EACH ROW EXECUTE FUNCTION app_private.invalidate_native_export_owner();
CREATE TRIGGER native_export_source_event_negative BEFORE DELETE ON ml.sona_capture_source_event
 FOR EACH ROW EXECUTE FUNCTION app_private.invalidate_native_export_owner();
CREATE TRIGGER native_export_erasure_negative AFTER INSERT ON ml.sona_event_erasure_receipt
 FOR EACH ROW EXECUTE FUNCTION app_private.invalidate_native_export_owner();

CREATE FUNCTION app_private.invalidate_native_export_dependents() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state IN ('INVALIDATED','DELETED') AND NEW.state<>OLD.state AND NEW.kind='PACKET' THEN
  UPDATE ml.native_export e SET state='INVALIDATED',reason='DEPENDENCY_INVALIDATED'
   FROM ml.native_export_dependency d
   WHERE (d.packet_id,d.packet_generation)=(NEW.export_id,NEW.generation)
    AND (e.export_id,e.generation)=(d.export_id,d.generation) AND e.state IN ('ALLOCATED','READY');
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER native_export_dependents AFTER UPDATE ON ml.native_export
 FOR EACH ROW EXECUTE FUNCTION app_private.invalidate_native_export_dependents();
REVOKE ALL ON FUNCTION app_private.guard_native_export(),app_private.guard_native_export_link(),
 app_private.invalidate_native_export_owner(),app_private.invalidate_native_export_dependents()
 FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
LOCK TABLE ml.native_export IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM ml.native_export) THEN
 RAISE EXCEPTION 'native_export_cleanup_obligation_retained' USING ERRCODE='55000';
END IF; END $$;
DROP TRIGGER native_export_account_negative ON account.user_account;
DROP TRIGGER native_export_consent_negative ON account.training_consent;
DROP TRIGGER native_export_source_negative ON ml.sona_capture_bundle;
DROP TRIGGER native_export_source_event_negative ON ml.sona_capture_source_event;
DROP TRIGGER native_export_erasure_negative ON ml.sona_event_erasure_receipt;
DROP TABLE ml.native_export_dependency,ml.native_export_owner,ml.native_export;
DROP FUNCTION app_private.guard_native_export(),app_private.guard_native_export_link(),app_private.invalidate_native_export_owner(),
 app_private.invalidate_native_export_dependents();
""")
