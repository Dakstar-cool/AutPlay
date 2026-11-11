"""Collect original P11 facts on the portable-account production branch."""

from alembic import op

revision = "0067_sona_native_collection"
down_revision = "0066_portable_local_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.sona_native_capture (
 request_id UUID PRIMARY KEY
  REFERENCES ml.recommendation_request(recommendation_request_id) ON DELETE CASCADE,
 user_id UUID NOT NULL REFERENCES account.user_account(user_id) ON DELETE CASCADE,
 consent_revision BIGINT NOT NULL CHECK(consent_revision>=1),
 surface TEXT NOT NULL CHECK(surface IN ('recommendations','home','offline_pack')),
 collection_status TEXT NOT NULL
  CHECK(collection_status IN ('COLLECTED','SOURCE_LIMIT','SOURCE_PROVENANCE_GAP')),
 created_at TIMESTAMPTZ NOT NULL, retained_until TIMESTAMPTZ NOT NULL,
 payload BYTEA NOT NULL, payload_sha256 BYTEA NOT NULL,
 CONSTRAINT sona_native_capture_retention CHECK(retained_until>created_at
  AND retained_until<=created_at+interval '180 days'),
 CONSTRAINT sona_native_capture_payload CHECK(octet_length(payload) BETWEEN 1 AND 16777216
  AND octet_length(payload_sha256)=32 AND sha256(payload)=payload_sha256)
);
CREATE INDEX sona_native_capture_owner_time ON ml.sona_native_capture(user_id,created_at);
CREATE INDEX sona_native_capture_expiry ON ml.sona_native_capture(retained_until,request_id);
CREATE FUNCTION ml.guard_sona_native_capture() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'native capture is immutable'; END IF;
 PERFORM 1 FROM account.user_account WHERE user_id=NEW.user_id
  AND status='ACTIVE' AND deleted_at IS NULL FOR NO KEY UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'native capture owner unavailable'; END IF;
 PERFORM 1 FROM account.training_consent WHERE user_id=NEW.user_id
  AND decision='GRANTED' AND revision=NEW.consent_revision FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'native capture consent unavailable'; END IF;
 PERFORM 1 FROM ml.recommendation_request WHERE recommendation_request_id=NEW.request_id
  AND user_id=NEW.user_id AND surface=NEW.surface AND NOT shadow;
 IF NOT FOUND THEN RAISE EXCEPTION 'native capture request mismatch'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_sona_native_capture BEFORE INSERT OR UPDATE ON ml.sona_native_capture
 FOR EACH ROW EXECUTE FUNCTION ml.guard_sona_native_capture();
CREATE FUNCTION ml.erase_sona_native_capture() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 DELETE FROM ml.sona_native_capture WHERE user_id=NEW.user_id;
 RETURN NEW;
END $$;
CREATE TRIGGER erase_sona_native_capture_consent AFTER UPDATE ON account.training_consent
 FOR EACH ROW WHEN (NEW.decision<>'GRANTED' OR NEW.revision<>OLD.revision)
 EXECUTE FUNCTION ml.erase_sona_native_capture();
CREATE TRIGGER erase_sona_native_capture_account AFTER UPDATE ON account.user_account
 FOR EACH ROW WHEN (NEW.status<>'ACTIVE' OR NEW.deleted_at IS NOT NULL)
 EXECUTE FUNCTION ml.erase_sona_native_capture();
REVOKE ALL ON FUNCTION ml.guard_sona_native_capture(),ml.erase_sona_native_capture() FROM PUBLIC;
""")


def downgrade() -> None:
    # Reject lossy rollback; an empty disposable copy can exercise reversibility.
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.sona_native_capture) THEN
  RAISE EXCEPTION 'native capture rollback requires an empty collection';
 END IF;
END $$;
DROP TRIGGER erase_sona_native_capture_account ON account.user_account;
DROP TRIGGER erase_sona_native_capture_consent ON account.training_consent;
DROP TABLE ml.sona_native_capture;
DROP FUNCTION ml.erase_sona_native_capture();
DROP FUNCTION ml.guard_sona_native_capture();
""")
