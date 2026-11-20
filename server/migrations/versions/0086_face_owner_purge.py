"""Erase the personal Face graph through the existing account purge authority."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0086_face_owner_purge"
down_revision = "0085_ml_serving_consent"
branch_labels = None
depends_on = None

# Each expression resolves ownership before its parent is removed. These are
# fixed deletion edges, never permission to traverse and delete shared content.
_DELETE_GUARDS = {
    "fence_face_projection_mutation": "app_private.privacy_owner_allowed(OLD.owner_user_id)",
    "fence_face_projection_receipt": """EXISTS(SELECT 1 FROM ml.face_projection p
 WHERE p.face_projection_id=OLD.face_projection_id
 AND app_private.privacy_owner_allowed(p.owner_user_id))""",
    "protect_face_signed_upsert": """EXISTS(SELECT 1 FROM ml.face_projection p
 WHERE p.face_projection_id=OLD.face_projection_id
 AND app_private.privacy_owner_allowed(p.owner_user_id))""",
    "fence_face_projection_tombstone_outbox": (
        "app_private.privacy_owner_allowed(OLD.owner_user_id)"
    ),
    "fence_face_projection_tombstone_audit": """EXISTS(
 SELECT 1 FROM ml.face_projection_tombstone_outbox o WHERE o.outbox_id=OLD.outbox_id
 AND app_private.privacy_owner_allowed(o.owner_user_id))""",
    "fence_face_projection_intent": """EXISTS(SELECT 1 FROM ml.face_analysis_sponsor s
 WHERE s.face_sponsor_id=OLD.face_sponsor_id
 AND app_private.privacy_owner_allowed(s.owner_user_id))""",
    "fence_face_sponsor_mutation": (
        "OLD.state<>'LIVE' AND app_private.privacy_owner_allowed(OLD.owner_user_id)"
    ),
    "fence_face_replacement_remediation": """EXISTS(
 SELECT 1 FROM ml.face_analysis_sponsor s WHERE s.face_sponsor_id=OLD.predecessor_sponsor_id
 AND app_private.privacy_owner_allowed(s.owner_user_id))""",
    "fence_face_signed_sync_event": "app_private.privacy_owner_allowed(OLD.user_id)",
}
_CANDIDATE_GUARDS = {
    "fence_face_source_replacement": "face_source_replacement_candidate",
    "fence_face_source_mutation": "face_source_mutation_candidate",
}
_PRIOR_FUNCTIONS = (
    *[f"app_private.{name}()" for name in _DELETE_GUARDS],
    *[f"app_private.{name}()" for name in _CANDIDATE_GUARDS],
    "app_private.fence_face_work_mutation()",
    "app_private.fence_face_lifecycle_positive()",
    "app_private.fence_face_restore_quarantine()",
    "app_private.protect_ml_artifact_immutable()",
    "account.purge_account(uuid,uuid)",
)


def _prior(name: str) -> str:
    definition = op.get_bind().scalar(
        text("SELECT definition FROM app_private.privacy_prior_definition WHERE name=:key"),
        {"key": f"0086:{name}"},
    )
    if not isinstance(definition, str):
        raise RuntimeError("missing Face owner purge definition")
    return definition


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        text("""
        INSERT INTO app_private.privacy_prior_definition VALUES
        ('0086:pgcrypto',CASE WHEN EXISTS(SELECT 1 FROM pg_extension WHERE extname='pgcrypto')
         THEN 'preexisting' ELSE 'created' END)
    """)
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
    for name in _PRIOR_FUNCTIONS:
        definition = connection.scalar(
            text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": name}
        )
        connection.execute(
            text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
            {"key": f"0086:{name}", "ddl": definition},
        )
    op.execute("""
ALTER TABLE ml.face_analysis_work ADD COLUMN erased_incarnation_origin_sha256 bytea,
 ADD CONSTRAINT ck_face_work_erased_origin CHECK(erased_incarnation_origin_sha256 IS NULL OR
  (octet_length(erased_incarnation_origin_sha256)=32 AND execution_incarnation>1
   AND incarnation_predecessor_sponsor_id IS NULL AND regeneration_timeline_id IS NULL)),
 DROP CONSTRAINT ck_face_work_incarnation_origin,
 ADD CONSTRAINT ck_face_work_incarnation_origin CHECK(
 (execution_incarnation=1 AND predecessor_work_id IS NULL AND
  incarnation_predecessor_sponsor_id IS NULL AND incarnation_source_generation IS NULL
  AND regeneration_timeline_id IS NULL) OR
 (execution_incarnation>1 AND predecessor_work_id IS NOT NULL AND (
  (regeneration_timeline_id IS NULL AND
   ((incarnation_predecessor_sponsor_id IS NOT NULL AND erased_incarnation_origin_sha256 IS NULL)
    OR (incarnation_predecessor_sponsor_id IS NULL
     AND erased_incarnation_origin_sha256 IS NOT NULL))
   AND incarnation_source_generation BETWEEN 2 AND 9007199254740990) OR
  (regeneration_timeline_id IS NOT NULL AND incarnation_predecessor_sponsor_id IS NULL
   AND incarnation_source_generation IS NULL AND incarnation_mutation_id IS NULL))));
""")
    for column in ("predecessor_id", "successor_id", "mutation_predecessor_sponsor_id"):
        op.execute(f"""
ALTER TABLE ml.face_analysis_sponsor
 DROP CONSTRAINT face_analysis_sponsor_{column}_fkey,
 ADD CONSTRAINT face_analysis_sponsor_{column}_fkey FOREIGN KEY({column})
 REFERENCES ml.face_analysis_sponsor(face_sponsor_id) ON DELETE NO ACTION
 DEFERRABLE INITIALLY DEFERRED;
""")
    for name, predicate in _DELETE_GUARDS.items():
        definition = _prior(f"app_private.{name}()")
        guard = f"IF TG_OP='DELETE' AND ({predicate}) THEN RETURN OLD; END IF;"
        op.execute(definition.replace("BEGIN", f"BEGIN\n {guard}", 1))
    for name, table in _CANDIDATE_GUARDS.items():
        guard = f"""
 IF TG_OP='DELETE' AND TG_TABLE_NAME='{table}' THEN
  IF EXISTS(SELECT 1 FROM ml.face_analysis_sponsor s
   WHERE s.face_sponsor_id=OLD.predecessor_sponsor_id
   AND app_private.privacy_owner_allowed(s.owner_user_id)) THEN RETURN OLD; END IF;
 END IF;
"""
        op.execute(_prior(f"app_private.{name}()").replace("BEGIN", "BEGIN\n" + guard, 1))
    erasure = """
 IF TG_OP='INSERT' AND NEW.erased_incarnation_origin_sha256 IS NOT NULL THEN
  RAISE EXCEPTION 'face_work_erased_origin_forged' USING ERRCODE='55000'; END IF;
 IF TG_OP='UPDATE' AND OLD.incarnation_predecessor_sponsor_id IS NOT NULL
  AND OLD.erased_incarnation_origin_sha256 IS NULL
  AND NEW.incarnation_predecessor_sponsor_id IS NULL
  AND to_jsonb(NEW)-ARRAY['incarnation_predecessor_sponsor_id','erased_incarnation_origin_sha256']
   IS NOT DISTINCT FROM
   to_jsonb(OLD)-ARRAY['incarnation_predecessor_sponsor_id','erased_incarnation_origin_sha256']
  AND EXISTS(SELECT 1 FROM ml.face_analysis_sponsor s
   WHERE s.face_sponsor_id=OLD.incarnation_predecessor_sponsor_id AND s.state<>'LIVE'
    AND app_private.privacy_owner_allowed(s.owner_user_id)
    AND NEW.erased_incarnation_origin_sha256=sha256(convert_to(to_jsonb(s)::text,'UTF8')))
 THEN RETURN NEW; END IF;
"""
    op.execute(
        _prior("app_private.fence_face_work_mutation()").replace("BEGIN", "BEGIN" + erasure, 1)
    )
    # This exact historical redaction is also allowed while restore is quarantined.
    op.execute(
        _prior("app_private.fence_face_lifecycle_positive()").replace(
            "BEGIN",
            "BEGIN\n IF TG_TABLE_NAME='face_analysis_work' THEN\n" + erasure + " END IF;\n",
            1,
        )
    )
    op.execute("""
CREATE FUNCTION app_private.purge_face_owner(target uuid) RETURNS bigint
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE work ml.face_analysis_work%ROWTYPE; affected bigint; total bigint:=0; step text;
BEGIN
 IF NOT app_private.privacy_owner_allowed(target) THEN
  RAISE EXCEPTION 'privacy_purge_not_authorized' USING ERRCODE='55000'; END IF;
 PERFORM pg_advisory_xact_lock_shared(4707761689340236883);
 -- Use the same source/lineage serialization as admission before changing counts.
 FOR work IN SELECT w.* FROM ml.face_analysis_work w WHERE EXISTS(
  SELECT 1 FROM ml.face_analysis_sponsor s WHERE s.face_work_id=w.face_work_id
  AND s.owner_user_id=target) ORDER BY w.source_sha256,w.lineage_sha256,w.face_work_id LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended('autplay.face.work.v1:' ||
   encode(work.source_sha256,'hex') || ':' || encode(work.lineage_sha256,'hex'),0));
  PERFORM 1 FROM ml.face_analysis_work WHERE face_work_id=work.face_work_id FOR UPDATE;
 END LOOP;
 UPDATE ml.face_analysis_sponsor SET state='WITHDRAWN',sponsor_generation=2,
  retired_at=clock_timestamp() WHERE owner_user_id=target AND state='LIVE';
 UPDATE ml.face_analysis_work w SET incarnation_predecessor_sponsor_id=NULL,
  erased_incarnation_origin_sha256=sha256(convert_to(to_jsonb(s)::text,'UTF8'))
 FROM ml.face_analysis_sponsor s WHERE s.face_sponsor_id=w.incarnation_predecessor_sponsor_id
  AND s.owner_user_id=target;
 SET CONSTRAINTS ml.face_analysis_sponsor_predecessor_id_fkey,
  ml.face_analysis_sponsor_successor_id_fkey,
  ml.face_analysis_sponsor_mutation_predecessor_sponsor_id_fkey DEFERRED;
 FOREACH step IN ARRAY ARRAY[
 $q$DELETE FROM ml.face_projection_signed_upsert WHERE face_projection_id IN
  (SELECT face_projection_id FROM ml.face_projection WHERE owner_user_id=$1)$q$,
 $q$DELETE FROM ml.face_projection_applied_receipt WHERE face_projection_id IN
  (SELECT face_projection_id FROM ml.face_projection WHERE owner_user_id=$1)$q$,
 $q$DELETE FROM ml.face_projection_tombstone_audit WHERE outbox_id IN
  (SELECT outbox_id FROM ml.face_projection_tombstone_outbox WHERE owner_user_id=$1)$q$,
 $q$DELETE FROM ml.face_projection_tombstone_outbox WHERE owner_user_id=$1$q$,
 $q$DELETE FROM ml.face_projection WHERE owner_user_id=$1$q$,
 $q$DELETE FROM ml.face_projection_intent WHERE face_sponsor_id IN
  (SELECT face_sponsor_id FROM ml.face_analysis_sponsor WHERE owner_user_id=$1)$q$,
 $q$DELETE FROM ml.face_source_replacement_remediation WHERE predecessor_sponsor_id IN
  (SELECT face_sponsor_id FROM ml.face_analysis_sponsor WHERE owner_user_id=$1)$q$,
 $q$DELETE FROM ml.face_source_replacement_candidate WHERE predecessor_sponsor_id IN
  (SELECT face_sponsor_id FROM ml.face_analysis_sponsor WHERE owner_user_id=$1)$q$,
 $q$DELETE FROM ml.face_source_mutation_candidate WHERE predecessor_sponsor_id IN
  (SELECT face_sponsor_id FROM ml.face_analysis_sponsor WHERE owner_user_id=$1)$q$,
 $q$DELETE FROM ml.face_analysis_sponsor WHERE owner_user_id=$1$q$,
 $q$DELETE FROM ml.face_analysis_policy_current WHERE owner_user_id=$1$q$,
 $q$DELETE FROM ml.face_analysis_policy_history WHERE owner_user_id=$1$q$
 ] LOOP
  EXECUTE step USING target;
  GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION app_private.purge_face_owner(uuid) FROM PUBLIC;
""")
    definition = _prior("account.purge_account(uuid,uuid)")
    anchor = "-- This frozen list is a deletion order, not recursive foreign-key cascade discovery."
    if definition.count(anchor) != 1:
        raise RuntimeError("unexpected account purge inventory")
    definition = definition.replace(
        "BEGIN", "BEGIN\n PERFORM pg_advisory_xact_lock_shared(4707761689340236883);", 1
    )
    # The independent evidence listener runs just before transaction commit.
    # Existing privacy guards need their checks before private purge context is
    # removed. Only the external receipt waits for the independent commit hook.
    if definition.count("SET CONSTRAINTS ALL IMMEDIATE;") != 1:
        raise RuntimeError("unexpected account purge constraint boundary")
    definition = definition.replace(
        "SET CONSTRAINTS ALL IMMEDIATE;",
        """SELECT string_agg(DISTINCT format('%I.%I',n.nspname,c.conname),',') INTO step
 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
 WHERE c.condeferrable AND c.conname<>'z_face_lifecycle_independent_commit'
 AND n.nspname IN ('account','audit','discovery','identity','importing','jobs','library',
  'ml','playlist','social','sync','vault','wave','app_private');
 IF step IS NOT NULL THEN EXECUTE 'SET CONSTRAINTS '||step||' IMMEDIATE'; END IF;""",
    )
    op.execute(
        definition.replace(
            anchor,
            "total:=total+app_private.purge_ml_serving_owner(target);\n "
            "total:=total+app_private.purge_face_owner(target);\n " + anchor,
        )
    )
    definition = _prior("app_private.protect_ml_artifact_immutable()")
    op.execute(
        definition.replace(
            "BEGIN",
            """BEGIN
 IF TG_OP='DELETE' AND TG_TABLE_NAME='control_step_up_receipt' THEN
  IF app_private.privacy_owner_allowed(OLD.actor_user_id) AND
   app_private.privacy_entity_allowed('ml.control_step_up_receipt',OLD.challenge_id)
  THEN RETURN OLD; END IF;
 END IF;""",
            1,
        )
    )
    op.execute((Path(__file__).parents[1] / "ml_serving_owner_purge_v1.sql").read_text())
    op.execute((Path(__file__).parents[1] / "face_lifecycle_evidence_v1.sql").read_text())
    definition = connection.scalar(
        text(
            "SELECT pg_get_functiondef('app_private.fence_face_lifecycle_positive()'::regprocedure)"
        )
    )
    op.execute(
        definition.replace("WHERE generation>0", "WHERE generation>0 AND released_at IS NULL")
    )


def downgrade() -> None:
    op.execute("""
LOCK TABLE ml.face_lifecycle_ledger_binding,ml.face_lifecycle_fact,
 ml.face_lifecycle_fact_receipt IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_lifecycle_ledger_binding)
 OR EXISTS(SELECT 1 FROM ml.face_lifecycle_fact) THEN
  RAISE EXCEPTION 'face_lifecycle_independent_evidence_retained' USING ERRCODE='55000'; END IF;
END $$;
DROP TRIGGER zz_face_reference_fact ON library.library_entry;
DROP TRIGGER zzz_face_sponsor_fact ON ml.face_analysis_sponsor;
DROP TRIGGER zzz_face_projection_fact ON ml.face_projection;
DROP TRIGGER zzz_face_timeline_fact ON ml.face_timeline;
DROP TABLE ml.face_lifecycle_fact_receipt;
DROP TABLE ml.face_lifecycle_fact;
DROP TABLE app_private.face_lifecycle_verifier;
DROP TABLE ml.face_lifecycle_ledger_binding;
DROP TABLE app_private.face_lifecycle_restore_authorization;
DROP TABLE app_private.face_library_restore_authorization;
DROP TABLE ml.face_restore_reconciliation;
DROP FUNCTION app_private.fence_face_restore_reconciliation();
ALTER TABLE ml.face_restore_quarantine DROP CONSTRAINT ck_face_restore_release_time,
 DROP COLUMN released_at;
DROP FUNCTION app_private.capture_face_lifecycle_fact();
DROP FUNCTION app_private.verify_face_lifecycle_independent_commit();
DROP FUNCTION app_private.fence_face_lifecycle_journal();
DROP FUNCTION app_private.face_lifecycle_entity_tag(text,uuid);
DROP FUNCTION app_private.begin_face_restore(uuid,bigint,uuid,bigint,bytea,bytea,bytea,bytea,bytea);
DROP FUNCTION app_private.end_face_restore();
DROP FUNCTION app_private.admit_face_library_restore(uuid,uuid,bigint,bytea,bytea);
DROP FUNCTION app_private.face_library_reference_authority(uuid);
DROP FUNCTION app_private.verify_face_library_restore_consumed();
""")
    if _prior("pgcrypto") == "created":
        op.execute("DROP EXTENSION pgcrypto")
    op.execute("""
LOCK TABLE ml.face_analysis_work,ml.face_analysis_sponsor IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.face_analysis_work WHERE erased_incarnation_origin_sha256 IS NOT NULL)
 OR EXISTS(SELECT 1 FROM account.account_purge_receipt) THEN
  RAISE EXCEPTION 'face_owner_purge_evidence_retained' USING ERRCODE='55000'; END IF;
END $$;
""")
    for name in _PRIOR_FUNCTIONS:
        op.execute(_prior(name))
    op.execute("""
DROP FUNCTION app_private.purge_ml_serving_owner(uuid);
DROP TRIGGER z_ml_credential_privacy_delete ON ml.control_step_up_credential;
DROP TRIGGER z_ml_challenge_privacy_delete ON ml.control_step_up_challenge;
DROP FUNCTION app_private.fence_ml_step_up_privacy_delete();
ALTER TABLE ml.control_step_up_credential DROP CONSTRAINT fk_ml_step_up_credential_challenge,
 ADD CONSTRAINT fk_ml_step_up_credential_challenge FOREIGN KEY(registration_challenge_id)
 REFERENCES ml.control_step_up_challenge(challenge_id) ON DELETE RESTRICT;
ALTER TABLE ml.control_step_up_challenge
 DROP CONSTRAINT control_step_up_challenge_credential_id_fkey,
 ADD CONSTRAINT control_step_up_challenge_credential_id_fkey FOREIGN KEY(credential_id)
 REFERENCES ml.control_step_up_credential(credential_id) ON DELETE RESTRICT;
""")
    op.execute("DROP FUNCTION app_private.purge_face_owner(uuid)")
    for column in ("predecessor_id", "successor_id", "mutation_predecessor_sponsor_id"):
        deferred = "DEFERRABLE INITIALLY DEFERRED" if column == "successor_id" else ""
        op.execute(f"""
ALTER TABLE ml.face_analysis_sponsor DROP CONSTRAINT face_analysis_sponsor_{column}_fkey,
 ADD CONSTRAINT face_analysis_sponsor_{column}_fkey FOREIGN KEY({column})
 REFERENCES ml.face_analysis_sponsor(face_sponsor_id) ON DELETE RESTRICT {deferred};
""")
    op.execute("""
ALTER TABLE ml.face_analysis_work DROP CONSTRAINT ck_face_work_erased_origin,
 DROP CONSTRAINT ck_face_work_incarnation_origin,DROP COLUMN erased_incarnation_origin_sha256,
 ADD CONSTRAINT ck_face_work_incarnation_origin CHECK(
 (execution_incarnation=1 AND predecessor_work_id IS NULL AND
  incarnation_predecessor_sponsor_id IS NULL AND incarnation_source_generation IS NULL
  AND regeneration_timeline_id IS NULL) OR
 (execution_incarnation>1 AND predecessor_work_id IS NOT NULL AND (
  (regeneration_timeline_id IS NULL AND incarnation_predecessor_sponsor_id IS NOT NULL
   AND incarnation_source_generation BETWEEN 2 AND 9007199254740990) OR
  (regeneration_timeline_id IS NOT NULL AND incarnation_predecessor_sponsor_id IS NULL
   AND incarnation_source_generation IS NULL AND incarnation_mutation_id IS NULL))));
DELETE FROM app_private.privacy_prior_definition WHERE name LIKE '0086:%';
""")
