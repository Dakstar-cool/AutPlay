"""Dormant Android grant bindings and independent serving-ledger projection."""

from alembic import op

revision = "0085_ml_serving_consent"
down_revision = "0084_face_regeneration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ml.registration_session_binding (
 challenge_id uuid PRIMARY KEY
 REFERENCES ml.control_step_up_challenge(challenge_id) ON DELETE RESTRICT,
 session_generation bigint, session_rotated_at timestamptz NOT NULL);
CREATE TABLE ml.android_grant_binding (
 challenge_id uuid PRIMARY KEY
 REFERENCES ml.control_step_up_challenge(challenge_id) ON DELETE RESTRICT,
 document jsonb NOT NULL, grant_sha256 bytea NOT NULL,
 session_generation bigint, session_rotated_at timestamptz NOT NULL,
 CONSTRAINT android_grant_binding_hash_check CHECK(octet_length(grant_sha256)=32),
 CONSTRAINT android_grant_binding_document_check CHECK(jsonb_typeof(document)='object'
 AND NOT (document ? 'nonce') AND octet_length(document::text)<=8192));
CREATE TABLE ml.serving_consent_current (
 owner_tag text NOT NULL, purpose text NOT NULL, operation_id uuid NOT NULL,
 owner_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 decision text NOT NULL, revision bigint NOT NULL, changed_at timestamptz NOT NULL,
 PRIMARY KEY(owner_tag,purpose),
 CONSTRAINT serving_consent_current_scope_check CHECK(owner_tag ~ '^[0-9a-f]{64}$'
 AND purpose='SONA_R1C_DESCENDANT_MODEL_SERVING_V1'),
 CONSTRAINT serving_consent_current_result_check CHECK(decision IN ('GRANTED','DENIED','WITHDRAWN')
 AND revision BETWEEN 1 AND 9007199254740991));
CREATE TABLE ml.serving_consent_receipt (
 owner_tag text NOT NULL, operation_id uuid NOT NULL, actor_tag text,
 owner_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
 request_sha256 bytea NOT NULL, decision text NOT NULL, revision bigint NOT NULL,
 changed_at timestamptz NOT NULL,
 challenge_id uuid REFERENCES ml.control_step_up_receipt(challenge_id) ON DELETE RESTRICT,
 PRIMARY KEY(owner_tag,operation_id),
 CONSTRAINT serving_consent_receipt_scope_check
 CHECK(owner_tag ~ '^[0-9a-f]{64}$' AND octet_length(request_sha256)=32),
 CONSTRAINT serving_consent_receipt_result_check
 CHECK(decision IN ('GRANTED','DENIED','WITHDRAWN')
 AND revision BETWEEN 1 AND 9007199254740991),
 CONSTRAINT serving_consent_receipt_proof_check
 CHECK((decision='GRANTED')=(challenge_id IS NOT NULL)));
CREATE FUNCTION app_private.freeze_ml_serving_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_id uuid; evidence_id uuid;
BEGIN
 IF TG_OP='DELETE' THEN
  IF TG_TABLE_NAME IN ('serving_consent_current','serving_consent_receipt') THEN
   owner_id:=OLD.owner_user_id; evidence_id:=OLD.operation_id;
  ELSE
   SELECT actor_user_id INTO owner_id FROM ml.control_step_up_challenge
    WHERE challenge_id=OLD.challenge_id;
   evidence_id:=OLD.challenge_id;
  END IF;
  IF owner_id IS NOT NULL AND app_private.privacy_owner_allowed(owner_id)
   AND app_private.privacy_entity_allowed('ml.'||TG_TABLE_NAME,evidence_id)
  THEN RETURN OLD; END IF;
 END IF;
 RAISE EXCEPTION 'immutable ML serving evidence';
END $$;
CREATE TRIGGER freeze_android_grant_binding BEFORE UPDATE OR DELETE ON ml.android_grant_binding
 FOR EACH ROW EXECUTE FUNCTION app_private.freeze_ml_serving_evidence();
CREATE TRIGGER freeze_registration_session_binding
 BEFORE UPDATE OR DELETE ON ml.registration_session_binding
 FOR EACH ROW EXECUTE FUNCTION app_private.freeze_ml_serving_evidence();
CREATE TRIGGER freeze_serving_consent_receipt BEFORE UPDATE OR DELETE ON ml.serving_consent_receipt
 FOR EACH ROW EXECUTE FUNCTION app_private.freeze_ml_serving_evidence();
CREATE TRIGGER guard_serving_consent_current_delete BEFORE DELETE ON ml.serving_consent_current
 FOR EACH ROW EXECUTE FUNCTION app_private.freeze_ml_serving_evidence();
CREATE FUNCTION app_private.check_serving_grant_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.decision='GRANTED' AND NOT EXISTS (
  SELECT 1 FROM ml.control_step_up_receipt r JOIN ml.control_step_up_challenge c USING(challenge_id)
  JOIN ml.android_grant_binding b USING(challenge_id)
  WHERE r.challenge_id=NEW.challenge_id AND r.operation_id=NEW.operation_id
   AND r.actor_user_id=NEW.owner_user_id
   AND r.request_sha256=NEW.request_sha256 AND c.request_sha256=NEW.request_sha256
   AND c.purpose='ML_CONSENT_STEP_UP_GRANT_V1' AND c.action='GRANT_SONA_SERVING_CONSENT'
   AND c.consumed_at IS NOT NULL AND r.result_code='GRANTED'
   AND c.expected_generation=NEW.revision-1
 ) THEN RAISE EXCEPTION 'serving grant receipt authority missing'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER check_serving_grant_receipt AFTER INSERT ON ml.serving_consent_receipt
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.check_serving_grant_receipt();
REVOKE ALL ON FUNCTION app_private.freeze_ml_serving_evidence(),
 app_private.check_serving_grant_receipt() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE ml.serving_consent_current;
DROP TABLE ml.serving_consent_receipt;
DROP TABLE ml.android_grant_binding;
DROP TABLE ml.registration_session_binding;
DROP FUNCTION app_private.check_serving_grant_receipt();
DROP FUNCTION app_private.freeze_ml_serving_evidence();
""")
