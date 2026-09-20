"""Charge every unclosed internal byte execution against reviewed global capacity."""

from alembic import op

revision = "0049_internal_io_budget"
down_revision = "0048_ingest_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE account.internal_io_policy (
 singleton_id SMALLINT CONSTRAINT internal_io_policy_pkey PRIMARY KEY,
 revision BIGINT NOT NULL DEFAULT 1,
 active_limit INTEGER, measured_ceiling INTEGER,
 server_instance_id UUID CONSTRAINT internal_io_policy_server_instance_id_fkey
   REFERENCES account.server_instance(server_instance_id),
 identity_epoch BIGINT, environment_sha256 TEXT, workload_sha256 TEXT, report_sha256 TEXT,
 playback_ceiling INTEGER, transfer_ceiling INTEGER,
 initialized_at TIMESTAMP WITH TIME ZONE,
 updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
 CONSTRAINT internal_io_policy_identity_check CHECK (singleton_id=1 AND revision>=1),
 CONSTRAINT internal_io_policy_measurement_check CHECK (
   (active_limit IS NULL AND measured_ceiling IS NULL AND server_instance_id IS NULL
    AND identity_epoch IS NULL AND environment_sha256 IS NULL AND workload_sha256 IS NULL
    AND report_sha256 IS NULL AND playback_ceiling IS NULL AND transfer_ceiling IS NULL
    AND initialized_at IS NULL) OR
   (active_limit IS NOT NULL AND measured_ceiling IS NOT NULL AND server_instance_id IS NOT NULL
    AND identity_epoch IS NOT NULL AND environment_sha256 IS NOT NULL
    AND workload_sha256 IS NOT NULL
    AND report_sha256 IS NOT NULL AND playback_ceiling IS NOT NULL AND transfer_ceiling IS NOT NULL
    AND initialized_at IS NOT NULL AND active_limit BETWEEN 1 AND measured_ceiling
    AND measured_ceiling BETWEEN 1 AND 1000000 AND identity_epoch>=1
    AND environment_sha256 ~ '^[a-f0-9]{64}$' AND workload_sha256 ~ '^[a-f0-9]{64}$'
    AND report_sha256 ~ '^[a-f0-9]{64}$' AND playback_ceiling BETWEEN 1 AND 1000000
    AND transfer_ceiling BETWEEN 1 AND 1000000 AND updated_at>=initialized_at))
);
INSERT INTO account.internal_io_policy(singleton_id) VALUES(1);
REVOKE ALL ON TABLE account.internal_io_policy FROM PUBLIC;
CREATE FUNCTION app_private.admit_internal_io() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE capacity INTEGER; occupied BIGINT;
BEGIN
 PERFORM pg_advisory_xact_lock(4707761689340236801::BIGINT);
 SELECT p.active_limit INTO capacity FROM account.internal_io_policy p
 JOIN account.server_instance s ON s.server_instance_id=p.server_instance_id
   AND s.identity_epoch=p.identity_epoch
 JOIN account.resource_quota_policy q ON q.singleton_id=1
   AND q.playback_ceiling<=p.playback_ceiling AND q.transfer_ceiling<=p.transfer_ceiling
 WHERE p.singleton_id=1;
 IF capacity IS NULL THEN
   RAISE EXCEPTION 'internal_io_budget_unconfigured' USING ERRCODE='55000';
 END IF;
 SELECT (SELECT count(*) FROM vault.ingest_execution WHERE closed_at IS NULL)
      + (SELECT count(*) FROM vault.ingest_cleanup_execution WHERE closed_at IS NULL)
      + (SELECT count(*) FROM vault.provider_maintenance WHERE closed_at IS NULL) INTO occupied;
 IF occupied>=capacity THEN
   RAISE EXCEPTION 'internal_io_busy' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_internal_io_admission BEFORE INSERT ON vault.ingest_execution
 FOR EACH ROW EXECUTE FUNCTION app_private.admit_internal_io();
CREATE TRIGGER a_internal_io_admission BEFORE INSERT ON vault.ingest_cleanup_execution
 FOR EACH ROW EXECUTE FUNCTION app_private.admit_internal_io();
CREATE TRIGGER a_internal_io_admission BEFORE INSERT ON vault.provider_maintenance
 FOR EACH ROW EXECUTE FUNCTION app_private.admit_internal_io();
REVOKE ALL ON FUNCTION app_private.admit_internal_io() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
LOCK TABLE account.internal_io_policy, vault.ingest_execution,
 vault.ingest_cleanup_execution, vault.provider_maintenance IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM account.internal_io_policy WHERE active_limit IS NOT NULL)
    OR EXISTS (SELECT 1 FROM vault.ingest_execution WHERE closed_at IS NULL)
    OR EXISTS (SELECT 1 FROM vault.ingest_cleanup_execution WHERE closed_at IS NULL)
    OR EXISTS (SELECT 1 FROM vault.provider_maintenance WHERE closed_at IS NULL) THEN
   RAISE EXCEPTION 'Refusing to discard internal I/O budget or occupied capacity';
 END IF;
END $$;
DROP TRIGGER a_internal_io_admission ON vault.ingest_execution;
DROP TRIGGER a_internal_io_admission ON vault.ingest_cleanup_execution;
DROP TRIGGER a_internal_io_admission ON vault.provider_maintenance;
DROP FUNCTION app_private.admit_internal_io();
DROP TABLE account.internal_io_policy;
    """)
