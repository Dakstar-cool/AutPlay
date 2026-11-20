"""Bind dormant Android registration preflight, signed payload and receipt.

Revision ID: 0072_ml_step_up_register_hash
Revises: 0071_face_signing_authority
"""

from alembic import op

revision = "0072_ml_step_up_register_hash"
down_revision = "0071_face_signing_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE ml.control_step_up_credential
 ADD COLUMN registration_challenge_id uuid
  CONSTRAINT fk_ml_step_up_credential_challenge
  REFERENCES ml.control_step_up_challenge(challenge_id) ON DELETE RESTRICT;
ALTER TABLE ml.control_step_up_credential
 ADD CONSTRAINT uq_ml_step_up_credential_challenge UNIQUE(registration_challenge_id);

ALTER TABLE ml.control_step_up_challenge
 ADD COLUMN signed_request_sha256 bytea;
ALTER TABLE ml.control_step_up_challenge
 ADD CONSTRAINT ck_ml_step_up_signed_request_len CHECK(
  signed_request_sha256 IS NULL OR octet_length(signed_request_sha256)=32);
ALTER TABLE ml.control_step_up_challenge
 ADD CONSTRAINT ck_ml_step_up_signed_request_state CHECK(
  (purpose='ML_CONSENT_STEP_UP_REGISTER_V1'
   AND (signed_request_sha256 IS NULL)=(consumed_at IS NULL))
  OR (purpose<>'ML_CONSENT_STEP_UP_REGISTER_V1'
      AND signed_request_sha256 IS NULL));

ALTER TABLE ml.control_step_up_receipt
 ADD COLUMN preflight_sha256 bytea,
 ADD COLUMN registration_credential_id uuid
  CONSTRAINT fk_ml_step_up_receipt_credential
  REFERENCES ml.control_step_up_credential(credential_id) ON DELETE RESTRICT;
ALTER TABLE ml.control_step_up_receipt
 ADD CONSTRAINT ck_ml_step_up_receipt_preflight_len CHECK(
  preflight_sha256 IS NULL OR octet_length(preflight_sha256)=32);
ALTER TABLE ml.control_step_up_receipt
 ADD CONSTRAINT ck_ml_step_up_receipt_registration_pair CHECK(
  (preflight_sha256 IS NULL)=(registration_credential_id IS NULL));

CREATE FUNCTION app_private.check_ml_step_up_register_receipt()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE challenge ml.control_step_up_challenge%ROWTYPE;
        credential ml.control_step_up_credential%ROWTYPE;
BEGIN
 SELECT * INTO challenge FROM ml.control_step_up_challenge
  WHERE challenge_id=NEW.challenge_id;
 IF NOT FOUND OR challenge.actor_user_id<>NEW.actor_user_id
  OR challenge.operation_id<>NEW.operation_id
  OR challenge.consumed_at IS NULL
  OR challenge.result_sha256 IS DISTINCT FROM NEW.result_sha256 THEN
  RAISE EXCEPTION 'ml_step_up_receipt_scope_invalid' USING ERRCODE='23514';
 END IF;
 IF challenge.purpose='ML_CONSENT_STEP_UP_REGISTER_V1' THEN
  SELECT * INTO credential FROM ml.control_step_up_credential
   WHERE credential_id=NEW.registration_credential_id;
  IF NEW.preflight_sha256 IS NULL OR NEW.registration_credential_id IS NULL
   OR challenge.request_sha256 IS DISTINCT FROM NEW.preflight_sha256
   OR challenge.signed_request_sha256 IS DISTINCT FROM NEW.request_sha256
   OR NOT FOUND
   OR credential.registration_challenge_id IS DISTINCT FROM NEW.challenge_id
   OR credential.actor_user_id IS DISTINCT FROM NEW.actor_user_id
   OR credential.device_id IS DISTINCT FROM challenge.device_id
   OR credential.parent_device_key_generation IS DISTINCT FROM
      challenge.parent_device_key_generation THEN
   RAISE EXCEPTION 'ml_step_up_registration_receipt_invalid' USING ERRCODE='23514';
  END IF;
 ELSIF NEW.preflight_sha256 IS NOT NULL
  OR NEW.registration_credential_id IS NOT NULL
  OR challenge.request_sha256 IS DISTINCT FROM NEW.request_sha256 THEN
  RAISE EXCEPTION 'ml_step_up_receipt_hash_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER z_ml_step_up_register_receipt
 AFTER INSERT ON ml.control_step_up_receipt
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION app_private.check_ml_step_up_register_receipt();
REVOKE ALL ON FUNCTION app_private.check_ml_step_up_register_receipt() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.control_step_up_credential
           WHERE registration_challenge_id IS NOT NULL)
  OR EXISTS(SELECT 1 FROM ml.control_step_up_challenge
            WHERE signed_request_sha256 IS NOT NULL)
  OR EXISTS(SELECT 1 FROM ml.control_step_up_receipt
            WHERE preflight_sha256 IS NOT NULL
               OR registration_credential_id IS NOT NULL) THEN
  RAISE EXCEPTION 'refusing registered step-up evidence downgrade' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER z_ml_step_up_register_receipt ON ml.control_step_up_receipt;
DROP FUNCTION app_private.check_ml_step_up_register_receipt();
ALTER TABLE ml.control_step_up_receipt
 DROP COLUMN registration_credential_id,
 DROP COLUMN preflight_sha256;
ALTER TABLE ml.control_step_up_challenge DROP COLUMN signed_request_sha256;
ALTER TABLE ml.control_step_up_credential DROP COLUMN registration_challenge_id;
""")
