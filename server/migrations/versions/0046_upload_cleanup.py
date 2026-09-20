"""Persist terminal-upload cleanup intent and retain exact maintenance exit evidence."""

from alembic import op

revision = "0046_upload_cleanup"
down_revision = "0045_orphan_missing"
branch_labels = None
depends_on = None


def _constraints(*, upload: bool) -> str:
    exclusion = " AND upload_claim_id IS NULL" if upload else ""
    action = ",'UPLOAD_CLEANUP'" if upload else ""
    target = (
        """ OR (action='UPLOAD_CLEANUP' AND provider_execution_id IS NULL
    AND orphan_claim_id IS NULL AND upload_claim_id IS NOT NULL
    AND upload_claim_id=claim_id AND storage_key IS NOT NULL)"""
        if upload
        else ""
    )
    return f"""
ALTER TABLE vault.provider_maintenance
  DROP CONSTRAINT provider_maintenance_target_check,
  DROP CONSTRAINT provider_maintenance_state_check,
  ADD CONSTRAINT provider_maintenance_target_check CHECK (
    (action IN ('CLEANUP','SCRATCH') AND provider_execution_id IS NOT NULL
    AND orphan_claim_id IS NULL{exclusion} AND storage_key IS NULL) OR
    (action IN ('ORPHAN_OBJECT','ORPHAN_MISSING') AND provider_execution_id IS NULL
    AND orphan_claim_id IS NOT NULL{exclusion} AND orphan_claim_id=claim_id
    AND storage_key IS NOT NULL) OR
    (action='INVENTORY' AND provider_execution_id IS NULL
    AND orphan_claim_id IS NULL{exclusion} AND storage_key IS NULL
    AND claim_id=execution_id){target}),
  ADD CONSTRAINT provider_maintenance_state_check CHECK (
    singleton_id=1 AND action IN
    ('CLEANUP','SCRATCH','ORPHAN_OBJECT','ORPHAN_MISSING','INVENTORY'{action})
    AND state IN ('PREPARED','RUNNING','CLOSED'));
"""


def upgrade() -> None:
    op.execute("""
CREATE TABLE vault.upload_cleanup_claim (
  claim_id UUID CONSTRAINT upload_cleanup_claim_pkey PRIMARY KEY
    CONSTRAINT upload_cleanup_claim_claim_id_fkey
    REFERENCES vault.upload_session(upload_session_id),
  storage_key TEXT NOT NULL,
  terminal_state TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  completed_at TIMESTAMP WITH TIME ZONE,
  completed_execution_id UUID CONSTRAINT upload_cleanup_claim_completed_execution_id_fkey
    REFERENCES vault.provider_maintenance(execution_id),
  CONSTRAINT upload_cleanup_claim_identity_key UNIQUE(claim_id,storage_key),
  CONSTRAINT upload_cleanup_claim_storage_key UNIQUE(storage_key),
  CONSTRAINT upload_cleanup_claim_target_check CHECK (
    storage_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$'
    AND terminal_state IN ('CANCELLED','EXPIRED')),
  CONSTRAINT upload_cleanup_claim_completion_check CHECK (
    (completed_at IS NULL AND completed_execution_id IS NULL) OR
    (completed_at IS NOT NULL AND completed_execution_id IS NOT NULL AND completed_at>=created_at))
);
CREATE INDEX ix_upload_cleanup_pending ON vault.upload_cleanup_claim(claim_id)
  WHERE completed_at IS NULL;
ALTER TABLE vault.provider_maintenance ADD COLUMN upload_claim_id UUID,
  ADD CONSTRAINT provider_maintenance_upload_claim_fkey FOREIGN KEY(upload_claim_id,storage_key)
    REFERENCES vault.upload_cleanup_claim(claim_id,storage_key);

CREATE FUNCTION app_private.protect_upload_cleanup_claim() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Upload cleanup ownership cannot be deleted' USING ERRCODE='23514';
  END IF;
  IF TG_OP='INSERT' THEN
    PERFORM 1 FROM vault.upload_session u WHERE u.upload_session_id=NEW.claim_id FOR UPDATE;
    IF NEW.completed_at IS NOT NULL THEN
      RAISE EXCEPTION 'Upload cleanup must start pending' USING ERRCODE='23514';
    END IF;
  ELSE
    IF ROW(NEW.claim_id,NEW.storage_key,NEW.terminal_state,NEW.created_at) IS DISTINCT FROM
       ROW(OLD.claim_id,OLD.storage_key,OLD.terminal_state,OLD.created_at)
       OR (OLD.completed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
      RAISE EXCEPTION 'Upload cleanup ownership is immutable' USING ERRCODE='23514';
    END IF;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM vault.upload_session u WHERE u.upload_session_id=NEW.claim_id
    AND u.actor_kind='DEVICE' AND u.state=NEW.terminal_state AND u.staging_key=NEW.storage_key
    AND u.vault_object_id IS NULL AND u.audio_variant_id IS NULL AND u.computed_sha256 IS NULL
    AND u.source_candidate_id IS NULL AND u.source_acquisition_attempt_id IS NULL
    AND u.source_internet_acquisition_id IS NULL) THEN
    RAISE EXCEPTION 'Upload cleanup target is protected' USING ERRCODE='23514';
  END IF;
  IF NEW.completed_at IS NOT NULL AND (
    NOT EXISTS (SELECT 1 FROM vault.provider_maintenance m
      WHERE m.execution_id=NEW.completed_execution_id AND m.action='UPLOAD_CLEANUP'
      AND m.upload_claim_id=NEW.claim_id AND m.storage_key=NEW.storage_key
      AND m.state='CLOSED' AND m.closure_kind='PROCESS_EXIT' AND m.exit_code=0
      AND m.child_pid IS NOT NULL AND m.closed_at<=NEW.completed_at)
    OR EXISTS (SELECT 1 FROM vault.provider_maintenance m
      WHERE m.upload_claim_id=NEW.claim_id AND m.closed_at IS NULL)
    OR EXISTS (SELECT 1 FROM account.resource_io_execution e
      WHERE e.kind='VAULT_UPLOAD' AND e.actual_target_id=NEW.claim_id AND e.closed_at IS NULL)
  ) THEN
    RAISE EXCEPTION 'Upload cleanup exit is unconfirmed' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER upload_cleanup_claim_guard BEFORE INSERT OR UPDATE OR DELETE
  ON vault.upload_cleanup_claim FOR EACH ROW
  EXECUTE FUNCTION app_private.protect_upload_cleanup_claim();

CREATE FUNCTION app_private.protect_upload_cleanup_owner() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP='DELETE' THEN
    IF EXISTS (SELECT 1 FROM vault.upload_cleanup_claim c WHERE c.claim_id=OLD.upload_session_id)
    THEN RAISE EXCEPTION 'Claimed upload cannot be deleted' USING ERRCODE='23514'; END IF;
    RETURN OLD;
  END IF;
  IF NEW.actor_kind IS DISTINCT FROM OLD.actor_kind THEN
    RAISE EXCEPTION 'Upload actor identity is immutable' USING ERRCODE='23514';
  END IF;
  IF OLD.actor_kind='DEVICE' AND (
    (NEW.state='OPEN' AND OLD.state<>'OPEN') OR
    (NEW.state='SEALED' AND OLD.state NOT IN ('OPEN','SEALED')) OR
    (NEW.state='CANCELLED' AND OLD.state NOT IN ('OPEN','SEALED','CANCELLED')) OR
    (NEW.state='EXPIRED' AND OLD.state NOT IN ('OPEN','EXPIRED')) OR
    (OLD.state IN ('CANCELLED','EXPIRED') AND NEW.state<>OLD.state)
  ) THEN
    RAISE EXCEPTION 'Upload cannot rewind writer ownership' USING ERRCODE='23514';
  END IF;
  IF EXISTS (SELECT 1 FROM vault.upload_cleanup_claim c WHERE c.claim_id=OLD.upload_session_id)
    AND ROW(NEW.upload_session_id,NEW.user_id,NEW.device_id,NEW.actor_kind,NEW.staging_key,
            NEW.state,NEW.source_candidate_id,NEW.source_acquisition_attempt_id,
            NEW.source_internet_acquisition_id,NEW.vault_object_id,NEW.audio_variant_id,
            NEW.computed_sha256,NEW.received_size,NEW.chunk_count) IS DISTINCT FROM
        ROW(OLD.upload_session_id,OLD.user_id,OLD.device_id,OLD.actor_kind,OLD.staging_key,
            OLD.state,OLD.source_candidate_id,OLD.source_acquisition_attempt_id,
            OLD.source_internet_acquisition_id,OLD.vault_object_id,OLD.audio_variant_id,
            OLD.computed_sha256,OLD.received_size,OLD.chunk_count)
  THEN RAISE EXCEPTION 'Claimed upload ownership is immutable' USING ERRCODE='23514'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER upload_cleanup_owner_guard BEFORE UPDATE OR DELETE ON vault.upload_session
  FOR EACH ROW EXECUTE FUNCTION app_private.protect_upload_cleanup_owner();

CREATE FUNCTION app_private.protect_upload_maintenance_target() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF NEW.upload_claim_id IS DISTINCT FROM OLD.upload_claim_id THEN
    RAISE EXCEPTION 'Maintenance upload target is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER upload_maintenance_target_guard BEFORE UPDATE ON vault.provider_maintenance
  FOR EACH ROW EXECUTE FUNCTION app_private.protect_upload_maintenance_target();
REVOKE ALL ON TABLE vault.upload_cleanup_claim FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_upload_cleanup_claim() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_upload_cleanup_owner() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_upload_maintenance_target() FROM PUBLIC;
    """)
    op.execute(_constraints(upload=True))


def downgrade() -> None:
    op.execute("""
LOCK TABLE vault.upload_session, vault.upload_cleanup_claim, vault.provider_maintenance
  IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM vault.upload_cleanup_claim)
    OR EXISTS (SELECT 1 FROM vault.provider_maintenance WHERE action='UPLOAD_CLEANUP') THEN
    RAISE EXCEPTION 'Refusing to discard upload cleanup ownership';
  END IF;
END $$;
DROP TRIGGER upload_cleanup_owner_guard ON vault.upload_session;
DROP FUNCTION app_private.protect_upload_cleanup_owner();
DROP TRIGGER upload_maintenance_target_guard ON vault.provider_maintenance;
DROP FUNCTION app_private.protect_upload_maintenance_target();
    """)
    op.execute(_constraints(upload=False))
    op.execute("""
ALTER TABLE vault.provider_maintenance DROP CONSTRAINT provider_maintenance_upload_claim_fkey,
  DROP COLUMN upload_claim_id;
DROP TABLE vault.upload_cleanup_claim;
DROP FUNCTION app_private.protect_upload_cleanup_claim();
    """)
