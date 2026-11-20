"""Forward original native capture closure to typed causal listening facts."""

from pathlib import Path

from alembic import op

revision = "0098_native_listen_capture"
down_revision = "0097_native_atomic_capture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    # Serialize root insertion against refusal and predecessor restoration.
    # No retained original byte document is rewritten by either direction.
    op.execute("""
LOCK TABLE ml.sona_capture_bundle IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.sona_capture_bundle b,
  LATERAL jsonb_array_elements(CASE
   WHEN jsonb_typeof(convert_from(b.bundle_document,'UTF8')::jsonb
    ->'temporal_snapshot'->'facts')='array'
   THEN convert_from(b.bundle_document,'UTF8')::jsonb->'temporal_snapshot'->'facts'
   ELSE '[]'::jsonb END) f
  WHERE convert_from(b.bundle_document,'UTF8')::jsonb->'schema_version'='2'::jsonb
   AND f->>'event_type'='LISTENING_EVENT_RECORDED'
   AND f->'causality' IS DISTINCT FROM 'null'::jsonb) THEN
  RAISE EXCEPTION 'refusing native listen capture downgrade with retained causal evidence'
   USING ERRCODE='55000'; END IF;
END $$;
""")
    op.execute(
        Path(__file__)
        .with_name("0098_native_listen_capture_rollback.sql")
        .read_text(encoding="utf-8")
    )
