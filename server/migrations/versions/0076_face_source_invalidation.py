"""Durable bounded retirement after a canonical Face source becomes invalid.

Revision ID: 0076_face_source_invalidation
Revises: 0075_gpu_sona_binding
"""

from alembic import op

revision = "0076_face_source_invalidation"
down_revision = "0075_gpu_sona_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.face_source_invalidation (
 face_source_invalidation_id uuid PRIMARY KEY DEFAULT uuidv7(),
 recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
 audio_variant_id uuid NOT NULL REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
 validation_status text NOT NULL
  CONSTRAINT ck_face_source_invalidation_status
  CHECK(validation_status IN ('SUSPECT','INVALID','QUARANTINED')),
 state text NOT NULL DEFAULT 'PENDING'
  CONSTRAINT ck_face_source_invalidation_state CHECK(state IN ('PENDING','COMPLETE')),
 retired_count bigint NOT NULL DEFAULT 0
  CONSTRAINT ck_face_source_invalidation_retired CHECK(retired_count>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 CONSTRAINT ck_face_source_invalidation_completion CHECK(
  (state='PENDING' AND completed_at IS NULL)
  OR (state='COMPLETE' AND completed_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_face_source_invalidation_pending
 ON ml.face_source_invalidation(recording_id) WHERE state='PENDING';
CREATE INDEX ix_face_source_invalidation_pending
 ON ml.face_source_invalidation(created_at,face_source_invalidation_id)
 WHERE state='PENDING';
CREATE INDEX ix_face_sponsor_source_live
 ON ml.face_analysis_sponsor(recording_id,audio_variant_id,face_sponsor_id)
 WHERE state='LIVE';
REVOKE ALL ON ml.face_source_invalidation FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DROP INDEX ml.ix_face_sponsor_source_live;
DROP TABLE ml.face_source_invalidation;
""")
