"""Add portable account authority without rewriting existing accounts/history."""

from alembic import op
from sqlalchemy import text

revision = "0066_portable_local_account"
down_revision = "0065_internet_catalogue_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE account.portable_identity (
 user_id UUID PRIMARY KEY REFERENCES account.user_account(user_id) ON DELETE CASCADE,
 public_key BYTEA NOT NULL, key_thumbprint BYTEA NOT NULL, document JSONB NOT NULL,
 revision BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
 CONSTRAINT portable_identity_key_check
  CHECK(octet_length(public_key)=91 AND octet_length(key_thumbprint)=32),
 CONSTRAINT portable_identity_document_check
  CHECK(revision>=1 AND jsonb_typeof(document)='object' AND octet_length(document::text)<=4096)
);
CREATE TABLE account.portable_challenge (
 challenge_id UUID PRIMARY KEY, purpose TEXT NOT NULL, source_key BYTEA NOT NULL,
 account_id UUID NOT NULL, actor_device_id UUID, actor_session_id UUID,
 document JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
 request_hash BYTEA, receipt JSONB, receipt_expires_at TIMESTAMPTZ,
 CONSTRAINT portable_challenge_purpose_check
  CHECK(purpose IN ('REGISTER','LOGIN','CLAIM','REAUTH','BACKUP','PASSWORD_CHANGE','ROTATE')),
 CONSTRAINT portable_challenge_hash_check
  CHECK(octet_length(source_key)=32 AND (request_hash IS NULL OR octet_length(request_hash)=32)),
 CONSTRAINT portable_challenge_expiry_check
  CHECK(expires_at>created_at AND expires_at<=created_at+interval '5 minutes'),
 CONSTRAINT portable_challenge_document_check
  CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=16384)
);
CREATE INDEX portable_challenge_source_time ON account.portable_challenge(source_key,created_at);
CREATE INDEX portable_challenge_expiry ON account.portable_challenge(expires_at);
CREATE TABLE account.portable_record (
 user_id UUID NOT NULL REFERENCES account.user_account(user_id) ON DELETE CASCADE,
 record_id UUID NOT NULL, kind TEXT NOT NULL, document JSONB NOT NULL, content_hash BYTEA NOT NULL,
 created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(user_id,record_id),
 CONSTRAINT portable_record_kind_check
  CHECK(kind IN ('LISTENING','PREFERENCE','PLAYLIST','PLAYLIST_ENTRY','TASTE')),
 CONSTRAINT portable_record_document_check CHECK(octet_length(content_hash)=32
  AND jsonb_typeof(document)='object' AND octet_length(document::text)<=16384)
);
CREATE INDEX portable_record_account_cursor
 ON account.portable_record(user_id,updated_at,record_id);
CREATE INDEX portable_record_listening_aliases
 ON account.portable_record USING gin ((document->'event_ids'));
CREATE TABLE library.portable_taste_signal (
 user_id UUID NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 record_id UUID NOT NULL,
 user_track_ref_id UUID NOT NULL
  REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
 interaction TEXT NOT NULL, weight SMALLINT NOT NULL, occurred_at_ms BIGINT NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL, row_version BIGINT NOT NULL DEFAULT 1,
 PRIMARY KEY(user_id,record_id),
 CONSTRAINT portable_taste_signal_interaction_check
  CHECK(interaction IN ('LIKE','DISLIKE','SKIP','COMPLETE','EXCLUDE')),
 CONSTRAINT portable_taste_signal_weight_check CHECK(weight BETWEEN -1000 AND 1000),
 CONSTRAINT portable_taste_signal_revision_check
  CHECK(occurred_at_ms BETWEEN 0 AND 8640000000000000 AND row_version>=1)
);
CREATE INDEX portable_taste_signal_account_cursor
 ON library.portable_taste_signal(user_id,updated_at,record_id);
CREATE FUNCTION account.erase_portable_challenges() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE owner_ids UUID[]; challenge_ids UUID[];
BEGIN
 SELECT array_agg(identifier) INTO owner_ids FROM (
  SELECT OLD.user_id AS identifier
  UNION SELECT device_id FROM account.device WHERE user_id=OLD.user_id
  UNION SELECT session_id FROM account.user_session WHERE user_id=OLD.user_id
 ) owned;
 SELECT array_agg(c.challenge_id) INTO challenge_ids FROM account.portable_challenge c
 WHERE c.account_id=OLD.user_id OR c.actor_device_id=ANY(owner_ids)
 OR c.actor_session_id=ANY(owner_ids)
 OR EXISTS(SELECT 1 FROM unnest(owner_ids) identifier WHERE
  strpos(replace(lower(c.document::text),'-',''),replace(identifier::text,'-',''))>0
  OR strpos(replace(lower(c.receipt::text),'-',''),replace(identifier::text,'-',''))>0);
 -- Keep the prior legacy deletion contract when no portable authority/data is involved.
 IF NOT app_private.privacy_owner_allowed(OLD.user_id) AND (
  coalesce(cardinality(challenge_ids),0)>0
  OR EXISTS(SELECT 1 FROM account.portable_identity WHERE user_id=OLD.user_id)
  OR EXISTS(SELECT 1 FROM account.portable_record WHERE user_id=OLD.user_id)
  OR EXISTS(SELECT 1 FROM library.portable_taste_signal WHERE user_id=OLD.user_id)
 ) THEN
  RAISE EXCEPTION 'portable_account_erasure_requires_privacy_authority' USING ERRCODE='55000';
 END IF;
 DELETE FROM account.portable_challenge WHERE challenge_id=ANY(challenge_ids);
 RETURN OLD;
END $$;
CREATE TRIGGER erase_portable_challenges BEFORE DELETE ON account.user_account
 FOR EACH ROW EXECUTE FUNCTION account.erase_portable_challenges();
""")
    connection = op.get_bind()
    original = connection.scalar(
        text("SELECT pg_get_functiondef('account.purge_account(uuid,uuid)'::regprocedure)")
    )
    anchor = "FOREACH step IN ARRAY ARRAY["
    if not isinstance(original, str) or original.count(anchor) != 1:
        raise RuntimeError("unexpected portable account privacy definition")
    connection.execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": "0066:account.purge_account(uuid,uuid)", "ddl": original},
    )
    # The existing purge captures device/session IDs before deletion and supplies them as $2.
    # Match nested JSON as the final privacy verifier does, including unhyphenated UUIDs.
    op.execute(
        original.replace(
            anchor,
            anchor
            + """
 $q$DELETE FROM account.portable_challenge c WHERE c.account_id=$1
 OR c.actor_device_id=ANY($2) OR c.actor_session_id=ANY($2)
 OR EXISTS(SELECT 1 FROM unnest(array_prepend($1,$2)) identifier WHERE
 strpos(replace(lower(c.document::text),'-',''),replace(identifier::text,'-',''))>0
 OR strpos(replace(lower(c.receipt::text),'-',''),replace(identifier::text,'-',''))>0)$q$,
 $q$DELETE FROM library.portable_taste_signal WHERE user_id=$1$q$,
 $q$DELETE FROM account.portable_record WHERE user_id=$1$q$,
 $q$DELETE FROM account.portable_identity WHERE user_id=$1$q$,
""",
        )
    )


def downgrade() -> None:
    op.execute("""
LOCK TABLE account.portable_identity,account.portable_challenge,account.portable_record,
 library.portable_taste_signal IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM account.portable_identity)
 OR EXISTS(SELECT 1 FROM account.portable_challenge)
 OR EXISTS(SELECT 1 FROM account.portable_record)
 OR EXISTS(SELECT 1 FROM library.portable_taste_signal) THEN
  RAISE EXCEPTION 'refusing to discard portable account identity, receipts or personal records'
   USING ERRCODE='55000';
 END IF;
END $$;
""")
    original = op.get_bind().scalar(
        text(
            "SELECT definition FROM app_private.privacy_prior_definition "
            "WHERE name='0066:account.purge_account(uuid,uuid)'"
        )
    )
    if not isinstance(original, str):
        raise RuntimeError("missing portable account privacy rollback definition")
    op.execute(original)
    op.execute("""
DELETE FROM app_private.privacy_prior_definition WHERE name='0066:account.purge_account(uuid,uuid)';
DROP TRIGGER erase_portable_challenges ON account.user_account;
DROP FUNCTION account.erase_portable_challenges();
DROP TABLE library.portable_taste_signal;
DROP TABLE account.portable_record;
DROP TABLE account.portable_challenge;
DROP TABLE account.portable_identity;
""")
