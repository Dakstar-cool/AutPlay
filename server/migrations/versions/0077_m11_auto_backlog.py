"""Indexes for bounded automatic A1C backlog admission.

Revision ID: 0077_m11_auto_backlog
Revises: 0076_face_source_invalidation
"""

from alembic import op

revision = "0077_m11_auto_backlog"
down_revision = "0076_face_source_invalidation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE INDEX ix_acquisition_attempt_auto_job
 ON discovery.acquisition_attempt(job_id) WHERE origin='AUTOMATIC';
CREATE INDEX ix_m11_auto_backlog_job
 ON jobs.job(user_id,job_id)
 WHERE job_type='discovery.acquire'
  AND state IN ('QUEUED','RUNNING','RETRY_WAIT','PAUSED');
""")


def downgrade() -> None:
    op.execute("""
DROP INDEX jobs.ix_m11_auto_backlog_job;
DROP INDEX discovery.ix_acquisition_attempt_auto_job;
""")
