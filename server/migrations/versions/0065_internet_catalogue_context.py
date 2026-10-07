"""Retain validated lookup context without changing immutable source snapshots."""

from alembic import op
from sqlalchemy import text

revision = "0065_internet_catalogue_context"
down_revision = "0064_metadata_catalog_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE discovery.internet_catalogue_context (
 context_id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES account.user_account(user_id),
 card JSONB NOT NULL, context_sha256 BYTEA NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
 CONSTRAINT internet_catalogue_context_owner_key UNIQUE(user_id,context_id),
 CONSTRAINT internet_catalogue_context_card_check CHECK (
  jsonb_typeof(card)='object' AND octet_length(card::text)<=16384 AND card->>'schema_version'='1'),
 CONSTRAINT internet_catalogue_context_sha_check CHECK(octet_length(context_sha256)=32),
 CONSTRAINT internet_catalogue_context_expiry_check
 CHECK(expires_at=observed_at+interval '24 hours')
);
CREATE INDEX internet_catalogue_context_owner_time
 ON discovery.internet_catalogue_context(user_id,observed_at DESC);
CREATE TABLE discovery.internet_search_context (
 search_id UUID PRIMARY KEY, user_id UUID NOT NULL, context_id UUID, request_sha256 BYTEA NOT NULL,
 CONSTRAINT internet_search_context_search_fkey FOREIGN KEY(user_id,search_id)
 REFERENCES discovery.internet_search(user_id,search_id),
 CONSTRAINT internet_search_context_owner_fkey FOREIGN KEY(user_id,context_id)
 REFERENCES discovery.internet_catalogue_context(user_id,context_id),
 CONSTRAINT internet_search_context_sha_check CHECK(octet_length(request_sha256)=32)
);
CREATE FUNCTION app_private.protect_catalogue_context() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(OLD.user_id)
 AND app_private.privacy_entity_allowed(TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,
   CASE WHEN TG_TABLE_NAME='internet_search_context' THEN (to_jsonb(OLD)->>'search_id')::uuid
   ELSE (to_jsonb(OLD)->>'context_id')::uuid END)
 THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'catalogue_context_immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER internet_catalogue_context_immutable BEFORE UPDATE OR DELETE
 ON discovery.internet_catalogue_context FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_catalogue_context();
CREATE TRIGGER internet_search_context_immutable BEFORE UPDATE OR DELETE
 ON discovery.internet_search_context FOR EACH ROW
 EXECUTE FUNCTION app_private.protect_catalogue_context();
REVOKE ALL ON discovery.internet_catalogue_context,discovery.internet_search_context FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.protect_catalogue_context() FROM PUBLIC;
""")
    connection = op.get_bind()
    original = connection.scalar(
        text("SELECT pg_get_functiondef('account.purge_account(uuid,uuid)'::regprocedure)")
    )
    entity = " UNION ALL SELECT transaction_key,'library.metadata_execution',execution_id"
    delete = "$q$DELETE FROM discovery.internet_search WHERE user_id=$1$q$"
    if not isinstance(original, str) or original.count(entity) != 1 or original.count(delete) != 1:
        raise RuntimeError("unexpected catalogue context privacy definition")
    connection.execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": "0065:account.purge_account(uuid,uuid)", "ddl": original},
    )
    changed = original.replace(
        entity,
        " UNION ALL SELECT transaction_key,'discovery.internet_catalogue_context',context_id\n"
        " FROM discovery.internet_catalogue_context WHERE user_id=target\n"
        " UNION ALL SELECT transaction_key,'discovery.internet_search_context',\n"
        " search_id FROM discovery.internet_search_context WHERE user_id=target\n" + entity,
    ).replace(
        delete,
        "$q$DELETE FROM discovery.internet_search_context WHERE user_id=$1$q$,\n "
        "$q$DELETE FROM discovery.internet_catalogue_context WHERE user_id=$1$q$,\n " + delete,
    )
    op.execute(changed)


def downgrade() -> None:
    op.execute("""
LOCK TABLE discovery.internet_catalogue_context,discovery.internet_search_context
 IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM discovery.internet_catalogue_context)
 OR EXISTS(SELECT 1 FROM discovery.internet_search_context) THEN
  RAISE EXCEPTION 'refusing to discard catalogue context or search request receipts'
   USING ERRCODE='55000';
 END IF;
END $$;
""")
    connection = op.get_bind()
    original = connection.scalar(
        text(
            "SELECT definition FROM app_private.privacy_prior_definition "
            "WHERE name='0065:account.purge_account(uuid,uuid)'"
        )
    )
    if not isinstance(original, str):
        raise RuntimeError("missing catalogue context privacy rollback definition")
    op.execute(original)
    op.execute("""
DELETE FROM app_private.privacy_prior_definition WHERE name='0065:account.purge_account(uuid,uuid)';
DROP TABLE discovery.internet_search_context;
DROP TABLE discovery.internet_catalogue_context;
DROP FUNCTION app_private.protect_catalogue_context();
""")
