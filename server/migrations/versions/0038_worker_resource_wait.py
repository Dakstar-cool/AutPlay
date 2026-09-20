"""Persist resource waits separately from job failures and device heartbeat expiry."""

from alembic import op

revision = "0038_worker_resource_wait"
down_revision = "0037_acquisition_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE jobs.job
  ADD COLUMN resource_wait_count integer NOT NULL DEFAULT 0,
  ADD COLUMN resource_waiting boolean NOT NULL DEFAULT false,
  ADD COLUMN resource_wake_until timestamptz,
  ADD CONSTRAINT job_resource_wait_count_check
    CHECK (resource_wait_count >= 0 AND resource_wait_count <= attempt_count),
  ADD CONSTRAINT job_resource_waiting_check
    CHECK (NOT resource_waiting OR state = 'RETRY_WAIT'),
  ADD CONSTRAINT job_resource_wake_check
    CHECK (resource_wake_until IS NULL OR resource_waiting);
ALTER TABLE jobs.job_attempt DROP CONSTRAINT ck_job_attempt_outcome,
  ADD CONSTRAINT ck_job_attempt_outcome CHECK (outcome IS NULL OR outcome IN
    ('SUCCESS','RETRYABLE_ERROR','TERMINAL_ERROR','LEASE_EXPIRED','CANCELLED','RESOURCE_WAIT'));
ALTER TABLE account.resource_admission DROP CONSTRAINT admission_waiting_shape_check;
UPDATE account.resource_admission SET waiting_until = NULL
  WHERE state = 'WAITING' AND job_id IS NOT NULL;
ALTER TABLE account.resource_admission
  ADD CONSTRAINT admission_waiting_shape_check CHECK (state <> 'WAITING' OR
    (job_id IS NULL AND waiting_until IS NOT NULL) OR
    (job_id IS NOT NULL AND waiting_until IS NULL));
    """)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM jobs.job WHERE resource_wait_count > 0)
     OR EXISTS(SELECT 1 FROM jobs.job_attempt WHERE outcome = 'RESOURCE_WAIT')
     OR EXISTS(SELECT 1 FROM account.resource_admission
               WHERE state = 'WAITING' AND waiting_until IS NULL)
  THEN
    RAISE EXCEPTION 'Refusing to discard durable resource wait evidence';
  END IF;
END $$;
ALTER TABLE account.resource_admission DROP CONSTRAINT admission_waiting_shape_check,
  ADD CONSTRAINT admission_waiting_shape_check
    CHECK (state <> 'WAITING' OR waiting_until IS NOT NULL);
ALTER TABLE jobs.job_attempt DROP CONSTRAINT ck_job_attempt_outcome,
  ADD CONSTRAINT ck_job_attempt_outcome CHECK (outcome IS NULL OR outcome IN
    ('SUCCESS','RETRYABLE_ERROR','TERMINAL_ERROR','LEASE_EXPIRED','CANCELLED'));
ALTER TABLE jobs.job DROP CONSTRAINT job_resource_wait_count_check,
  DROP CONSTRAINT job_resource_waiting_check,
  DROP CONSTRAINT job_resource_wake_check,
  DROP COLUMN resource_wait_count, DROP COLUMN resource_waiting, DROP COLUMN resource_wake_until;
    """)
