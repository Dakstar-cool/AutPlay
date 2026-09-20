"""Shared-training account policy; default-private and monotonic decisions."""

from alembic import op
from sqlalchemy import text

revision = "0054_training_consent"
down_revision = "0053_privacy_purge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE account.training_consent (
 user_id uuid PRIMARY KEY REFERENCES account.user_account(user_id) ON DELETE CASCADE,
 decision text NOT NULL CONSTRAINT training_consent_decision_check
 CHECK(decision IN ('GRANTED','DENIED','WITHDRAWN')),
 revision bigint NOT NULL, policy_version bigint NOT NULL, changed_at timestamptz NOT NULL,
 CONSTRAINT training_consent_versions_check CHECK(revision>=1 AND policy_version=1));
CREATE TABLE account.training_consent_operation (
 operation_id uuid PRIMARY KEY,
 user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE CASCADE,
 actor_device_id uuid NOT NULL, request_sha256 bytea NOT NULL,
 applied_decision text NOT NULL, applied_revision bigint NOT NULL, created_at timestamptz NOT NULL,
 CONSTRAINT training_consent_operation_hash_check CHECK(octet_length(request_sha256)=32),
 CONSTRAINT training_consent_operation_result_check
 CHECK(applied_decision IN ('GRANTED','DENIED','WITHDRAWN') AND applied_revision>=1));
CREATE FUNCTION account.guard_training_consent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  IF app_private.privacy_owner_allowed(OLD.user_id) THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'training_consent_history_retained' USING ERRCODE='55000';
 END IF;
 IF TG_OP='UPDATE' AND (NEW.user_id<>OLD.user_id OR NEW.revision<>OLD.revision+1
   OR NEW.changed_at<OLD.changed_at OR NEW.policy_version<>OLD.policy_version) THEN
  RAISE EXCEPTION 'training_consent_revision_conflict' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_training_consent BEFORE UPDATE OR DELETE ON account.training_consent
 FOR EACH ROW EXECUTE FUNCTION account.guard_training_consent();
CREATE FUNCTION account.guard_training_consent_operation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(OLD.user_id) THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'training_consent_operation_immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER guard_training_consent_operation
 BEFORE UPDATE OR DELETE ON account.training_consent_operation
 FOR EACH ROW EXECUTE FUNCTION account.guard_training_consent_operation();
REVOKE ALL ON FUNCTION account.guard_training_consent(),
 account.guard_training_consent_operation() FROM PUBLIC;
""")
    connection = op.get_bind()
    previous = connection.scalar(
        text("SELECT pg_get_functiondef('account.purge_account(uuid,uuid)'::regprocedure)")
    )
    anchor = "$q$DELETE FROM account.user_account WHERE user_id=$1$q$"
    if not isinstance(previous, str) or previous.count(anchor) != 1:
        raise RuntimeError("unexpected privacy purge inventory")
    connection.execute(
        text("INSERT INTO app_private.privacy_prior_definition VALUES(:key,:ddl)"),
        {"key": "0054:account.purge_account", "ddl": previous},
    )
    op.execute(
        previous.replace(
            anchor,
            "$q$DELETE FROM account.training_consent_operation WHERE user_id=$1$q$,\n"
            " $q$DELETE FROM account.training_consent WHERE user_id=$1$q$,\n " + anchor,
        )
    )


def downgrade() -> None:
    op.execute("""
LOCK TABLE account.training_consent,account.training_consent_operation IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM account.training_consent)
 OR EXISTS(SELECT 1 FROM account.training_consent_operation) THEN
  RAISE EXCEPTION 'training_consent_evidence_retained' USING ERRCODE='55000';
 END IF;
END $$;
""")
    connection = op.get_bind()
    previous = connection.scalar(
        text(
            "SELECT definition FROM app_private.privacy_prior_definition "
            "WHERE name='0054:account.purge_account'"
        )
    )
    if not isinstance(previous, str):
        raise RuntimeError("missing privacy purge inventory")
    op.execute(previous)
    op.execute(
        "DELETE FROM app_private.privacy_prior_definition WHERE name='0054:account.purge_account'"
    )
    op.execute("""
DROP TABLE account.training_consent_operation;
DROP TABLE account.training_consent;
DROP FUNCTION account.guard_training_consent_operation();
DROP FUNCTION account.guard_training_consent();
""")
