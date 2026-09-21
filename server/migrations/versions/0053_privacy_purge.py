"""Restricted owner purge, retained holds, redacted attribution and closure fences."""

from __future__ import annotations

import re
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0053_privacy_purge"
down_revision = "0052_account_deletion"
branch_labels = None
depends_on = None

# These guards keep their previous implementation verbatim after the narrow exception.
_OWNER_DELETE = (
    "app_private.protect_recommendation_input_snapshot",
    "app_private.protect_recommendation_temporal_event",
    "app_private.protect_recommendation_temporal_snapshot",
    "app_private.protect_sona_shadow_binding",
    "account.guard_recovery_credential",
    "account.guard_recovery_operation",
    "account.guard_deletion_request",
)
_ENTITY_DELETE = {
    "app_private.reject_artist_policy_revision_mutation": "OLD.owner_user_id",
    "app_private.protect_ingest_execution": "OLD.execution_id",
    "app_private.protect_ingest_cleanup_execution": "OLD.execution_id",
    "app_private.protect_ingest_cleanup_claim": "OLD.claim_id",
    "app_private.protect_upload_cleanup_claim": "OLD.claim_id",
    "app_private.protect_provider_maintenance": "OLD.execution_id",
    "app_private.protect_metadata_execution": "OLD.execution_id",
    "app_private.protect_metadata_evidence": "(to_jsonb(OLD)->>'user_track_ref_id')::uuid",
}
_CYCLES = (
    ("library.user_track_ref", "fk_user_track_ref_current_match_decision"),
    ("importing.import_entry", "fk_import_entry_current_match_decision"),
    ("identity.match_decision", "fk_match_decision_reviewed_evidence"),
    ("identity.match_decision", "match_decision_supersedes_decision_id_fkey"),
    ("discovery.internet_acquisition", "internet_acquisition_upload_id_fkey"),
    ("vault.ingest_cleanup_claim", "ingest_cleanup_claim_completed_execution_id_fkey"),
    ("vault.upload_cleanup_claim", "upload_cleanup_claim_completed_execution_id_fkey"),
)
_ACTORS = (
    "identity.match_policy_activation",
    "identity.match_decision",
    "audit.catalog_change_set",
)


def _extend_guard(name: str, exception: str) -> None:
    connection = op.get_bind()
    original = connection.scalar(
        text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": name + "()"}
    )
    if not isinstance(original, str) or not re.search(r"\bBEGIN\b", original, re.IGNORECASE):
        raise RuntimeError("unexpected privacy guard definition")
    connection.execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": name, "ddl": original},
    )
    changed = re.sub(r"\bBEGIN\b", "BEGIN\n" + exception, original, count=1, flags=re.IGNORECASE)
    op.execute(changed)


def _replace_constraint(table: str, name: str, definition: str) -> None:
    connection = op.get_bind()
    previous = connection.scalar(
        text("""
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid=CAST(:table AS regclass) AND conname=:name
    """),
        {"table": table, "name": name},
    )
    if not isinstance(previous, str):
        raise RuntimeError("unexpected privacy constraint definition")
    connection.execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": table + ":" + name, "ddl": previous},
    )
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}")


def upgrade() -> None:
    op.execute("""
CREATE TABLE account.account_deletion_hold (
 user_id uuid PRIMARY KEY REFERENCES account.user_account(user_id),
 reason_code text NOT NULL CHECK(reason_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
 created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE account.account_purge_receipt (
 request_id uuid PRIMARY KEY, completed_at timestamptz NOT NULL,
 removed_rows bigint NOT NULL CHECK(removed_rows>=1));
CREATE TABLE app_private.privacy_prior_definition (name text PRIMARY KEY, definition text NOT NULL);
CREATE TABLE app_private.privacy_purge_context (
 transaction_id xid8 PRIMARY KEY, owner_id uuid NOT NULL, request_id uuid NOT NULL);
CREATE TABLE app_private.privacy_restore_authorization (
 transaction_id xid8 PRIMARY KEY, owner_id uuid NOT NULL, request_id uuid NOT NULL);
CREATE TABLE app_private.privacy_purge_entity (
 transaction_id xid8 NOT NULL REFERENCES app_private.privacy_purge_context(transaction_id),
 entity text NOT NULL, entity_id uuid NOT NULL, PRIMARY KEY(transaction_id,entity,entity_id));
CREATE FUNCTION app_private.privacy_owner_allowed(owner_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app_private.privacy_purge_context c
 WHERE c.transaction_id=pg_current_xact_id() AND c.owner_id=$1) $$;
CREATE FUNCTION app_private.privacy_entity_allowed(entity text, entity_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app_private.privacy_purge_entity e
 WHERE e.transaction_id=pg_current_xact_id() AND e.entity=$1 AND e.entity_id=$2) $$;
REVOKE ALL ON account.account_deletion_hold,account.account_purge_receipt,
 app_private.privacy_prior_definition,app_private.privacy_purge_context,
 app_private.privacy_purge_entity,app_private.privacy_restore_authorization FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.privacy_owner_allowed(uuid),
 app_private.privacy_entity_allowed(text,uuid) FROM PUBLIC;
""")
    for table in _ACTORS:
        op.execute(f"ALTER TABLE {table} ADD COLUMN actor_erased_at timestamptz")
    op.execute("ALTER TABLE identity.match_policy_activation ALTER actor_user_id DROP NOT NULL")
    op.execute("""ALTER TABLE identity.match_policy_activation ADD CONSTRAINT
        match_policy_actor_erasure_check CHECK (
        (actor_user_id IS NOT NULL AND actor_erased_at IS NULL) OR
        (actor_user_id IS NULL AND actor_erased_at IS NOT NULL))""")
    _replace_constraint(
        "identity.match_decision",
        "ck_match_decision_actor",
        """CHECK (
        actor_type IN ('SYSTEM','USER','ADMIN') AND
        ((actor_type='SYSTEM' AND actor_user_id IS NULL AND actor_erased_at IS NULL) OR
         (actor_type IN ('USER','ADMIN') AND
           ((actor_user_id IS NOT NULL AND actor_erased_at IS NULL) OR
            (actor_user_id IS NULL AND actor_erased_at IS NOT NULL)))))""",
    )
    _replace_constraint(
        "audit.catalog_change_set",
        "ck_catalog_change_set_actor_user",
        """CHECK (
        (actor_type='SYSTEM' AND actor_erased_at IS NULL) OR
        (actor_type IN ('USER','ADMIN') AND
         ((actor_user_id IS NOT NULL AND actor_erased_at IS NULL) OR
          (actor_user_id IS NULL AND actor_erased_at IS NOT NULL))))""",
    )
    for table in (
        "account.account_invitation",
        "account.account_provisioning_link",
        "account.enrollment_invitation",
    ):
        op.execute(f"ALTER TABLE {table} ALTER issued_by_user_id DROP NOT NULL")
    for table, name in _CYCLES:
        definition = op.get_bind().scalar(
            text("""
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid=CAST(:table AS regclass) AND conname=:name
        """),
            {"table": table, "name": name},
        )
        if not isinstance(definition, str) or "DEFERRABLE" in definition:
            raise RuntimeError("unexpected privacy cycle definition")
        _replace_constraint(
            table,
            name,
            definition.replace("ON DELETE RESTRICT", "ON DELETE NO ACTION")
            + " DEFERRABLE INITIALLY IMMEDIATE",
        )
    for name in _OWNER_DELETE:
        _extend_guard(
            name,
            """IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(OLD.user_id)
            THEN RETURN OLD; END IF;""",
        )
    for name, identifier in _ENTITY_DELETE.items():
        predicate = (
            f"app_private.privacy_owner_allowed({identifier})"
            if "artist_policy" in name
            else (
                "app_private.privacy_entity_allowed(TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,"
                f"{identifier})"
            )
        )
        _extend_guard(name, f"IF TG_OP='DELETE' AND {predicate} THEN RETURN OLD; END IF;")
    _extend_guard(
        "app_private.validate_match_decision",
        """
        IF TG_OP='DELETE' AND TG_TABLE_NAME IN ('match_decision','match_candidate_evidence')
        AND app_private.privacy_entity_allowed(TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,
            CASE WHEN TG_TABLE_NAME='match_decision' THEN (to_jsonb(OLD)->>'decision_id')::uuid
            ELSE (to_jsonb(OLD)->>'match_candidate_evidence_id')::uuid END) THEN RETURN OLD; END IF;
        IF TG_OP='UPDATE' AND TG_TABLE_NAME='match_decision'
        AND app_private.privacy_owner_allowed((to_jsonb(OLD)->>'actor_user_id')::uuid)
        AND (to_jsonb(NEW)->>'actor_user_id') IS NULL
        AND (to_jsonb(NEW)->>'actor_erased_at') IS NOT NULL
        AND to_jsonb(NEW)->>'idempotency_scope'='privacy-erased-review:'||
            (to_jsonb(NEW)->>'decision_id')
        AND to_jsonb(NEW)->>'idempotency_key'=to_jsonb(NEW)->>'decision_id'
        AND to_jsonb(OLD)-ARRAY[
            'actor_user_id','actor_erased_at','idempotency_scope','idempotency_key'] =
            to_jsonb(NEW)-ARRAY['actor_user_id','actor_erased_at','idempotency_scope','idempotency_key']
        THEN RETURN NEW; END IF;
    """,
    )
    _extend_guard(
        "app_private.validate_match_policy_activation",
        """
        IF TG_OP='UPDATE' AND app_private.privacy_owner_allowed(OLD.actor_user_id)
        AND NEW.actor_user_id IS NULL AND NEW.actor_erased_at IS NOT NULL
        AND NEW.reason='PRIVACY_ERASED'
        AND to_jsonb(OLD)-ARRAY['actor_user_id','actor_erased_at','reason'] =
            to_jsonb(NEW)-ARRAY['actor_user_id','actor_erased_at','reason'] THEN RETURN NEW; END IF;
    """,
    )
    _extend_guard(
        "app_private.protect_ml_evidence",
        """
        IF TG_OP='UPDATE' AND TG_TABLE_NAME='embedding_model_activation'
        AND app_private.privacy_owner_allowed((to_jsonb(OLD)->>'actor_user_id')::uuid)
        AND (to_jsonb(NEW)->>'actor_user_id') IS NULL
        AND to_jsonb(OLD)-'actor_user_id'=to_jsonb(NEW)-'actor_user_id' THEN RETURN NEW; END IF;
    """,
    )
    op.execute((Path(__file__).parents[1] / "privacy_purge_v1.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("""
LOCK TABLE account.account_purge_receipt,account.account_deletion_hold IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM account.account_purge_receipt)
 OR EXISTS(SELECT 1 FROM account.account_deletion_hold)
 OR EXISTS(SELECT 1 FROM identity.match_policy_activation WHERE actor_erased_at IS NOT NULL)
 OR EXISTS(SELECT 1 FROM identity.match_decision WHERE actor_erased_at IS NOT NULL)
 OR EXISTS(SELECT 1 FROM audit.catalog_change_set WHERE actor_erased_at IS NOT NULL)
 OR EXISTS(SELECT 1 FROM account.account_invitation WHERE issued_by_user_id IS NULL)
 OR EXISTS(SELECT 1 FROM account.account_provisioning_link WHERE issued_by_user_id IS NULL)
 OR EXISTS(SELECT 1 FROM account.enrollment_invitation WHERE issued_by_user_id IS NULL) THEN
 RAISE EXCEPTION 'Refusing to discard privacy purge evidence'; END IF;
END $$;
DROP FUNCTION account.purge_account(uuid,uuid);
DROP FUNCTION account.verify_account_purge_ready(uuid);
DROP FUNCTION account.verify_owner_absent(uuid,uuid[]);
DROP TRIGGER guard_purge_receipt ON account.account_purge_receipt;
DROP FUNCTION app_private.guard_purge_receipt();
""")
    definitions = (
        op.get_bind()
        .execute(
            text("SELECT name,definition FROM app_private.privacy_prior_definition ORDER BY name")
        )
        .all()
    )
    for name, definition in definitions:
        if ":" in name:
            table, constraint = name.split(":")
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
            op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {constraint} {definition}")
        else:
            op.execute(definition)
    op.execute("""
DROP TRIGGER guard_catalog_actor_erasure ON audit.catalog_change_set;
DROP FUNCTION app_private.guard_catalog_actor_erasure();
DROP FUNCTION app_private.privacy_entity_allowed(text,uuid);
DROP FUNCTION app_private.privacy_owner_allowed(uuid);
DROP TABLE app_private.privacy_purge_entity;
DROP TABLE app_private.privacy_purge_context;
DROP TABLE app_private.privacy_restore_authorization;
DROP TABLE app_private.privacy_prior_definition;
DROP TABLE account.account_purge_receipt;
DROP TABLE account.account_deletion_hold;
ALTER TABLE identity.match_policy_activation DROP CONSTRAINT match_policy_actor_erasure_check;
ALTER TABLE identity.match_policy_activation ALTER actor_user_id SET NOT NULL;
""")
    for table in _ACTORS:
        op.execute(f"ALTER TABLE {table} DROP COLUMN actor_erased_at")
    for table in (
        "account.account_invitation",
        "account.account_provisioning_link",
        "account.enrollment_invitation",
    ):
        op.execute(f"ALTER TABLE {table} ALTER issued_by_user_id SET NOT NULL")
