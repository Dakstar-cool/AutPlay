"""D1 admission/trusted versioned immutable evidence; no legacy backfill."""

from alembic import op

revision = "0071_device_admission_v2"
down_revision = "0070_device_portable_contract_v2"
branch_labels = None
depends_on = None


def _evidence_check(operations: tuple[str, ...]) -> str:
    # Frozen migration-local SQL; deliberately no live ORM/domain imports.
    keys = (
        "'operation','contract_version','schema_version','platform','server_instance_id','identity_epoch',"
        "'identity_thumbprint_sha256','api_origin','stream_origin','device_public_key_spki_hex',"
        "'device_key_thumbprint_sha256','matched_body_key','domain','signature_algorithm','proof_digest',"
        "'original_document_sha256','signature_hex','fields','credential_hashes'"
    )
    # Frozen safe_evidence scalar types, not merely an allowlist of key names.
    fields = {
        "admission.submit": (
            ("request_id", "string"),
            ("client_nonce_b64url", "string"),
            ("api_major", "number"),
            ("nickname", "string"),
            ("app_version", "string"),
            ("requested_at", "string"),
        ),
        "trusted.challenge": (
            ("challenge_request_id", "string"),
            ("account_id", "string"),
            ("client_nonce_b64url", "string"),
        ),
        "admission.exchange": (
            ("request_id", "string"),
            ("request_sha256", "string"),
            ("exchange_id", "string"),
            ("binding_commit_id", "string"),
            ("poll_bearer_sha256", "string"),
            ("approved_account_id", "string"),
            ("client_nonce_b64url", "string"),
            ("next_refresh_token_sha256", "string"),
        ),
        "trusted.exchange": (
            ("challenge_id", "string"),
            ("exchange_id", "string"),
            ("binding_commit_id", "string"),
            ("account_id", "string"),
            ("next_refresh_token_sha256", "string"),
            ("client_nonce_b64url", "string"),
        ),
    }
    variants = []
    for operation in operations:
        required = ",".join(f"'{field}'" for field, _ in fields[operation])
        allowed = required + (",'device_model_hint'" if operation == "admission.submit" else "")
        scalar_types = "".join(
            f"AND jsonb_typeof(original_request_evidence->'fields'->'{field}')='{kind}' "
            for field, kind in fields[operation]
        )
        if operation == "admission.submit":
            scalar_types += (
                "AND original_request_evidence->'fields'->'api_major'='1'::jsonb "
                "AND (NOT (original_request_evidence->'fields' ? 'device_model_hint') "
                "OR jsonb_typeof(original_request_evidence->'fields'->'device_model_hint') IN ('string','null')) "
            )
        credentials = (
            "AND original_request_evidence->'credential_hashes' ? 'challenge_sha256' "
            "AND (original_request_evidence->'credential_hashes') - 'challenge_sha256' = '{}'::jsonb "
            "AND original_request_evidence->'credential_hashes'->>'challenge_sha256' ~ '^[0-9a-f]{64}$' "
            if operation == "trusted.exchange"
            else "AND original_request_evidence->'credential_hashes' = '{}'::jsonb "
        )
        variants.append(
            f"(original_request_evidence->>'operation' = '{operation}' "
            f"AND original_request_evidence->>'domain' = E'autplay:device-compatibility:{operation.replace('.', ':')}:v2\\n' "
            f"AND (original_request_evidence->'fields') ?& ARRAY[{required}] "
            f"AND (original_request_evidence->'fields') - ARRAY[{allowed}] = '{{}}'::jsonb "
            f"{scalar_types}{credentials})"
        )
    return (
        "(contract_version='v1' AND original_document_sha256 IS NULL AND original_request_evidence IS NULL) "
        "OR COALESCE((contract_version='v2' AND platform IN ('ANDROID','MACOS') "
        "AND octet_length(original_document_sha256)=32 "
        "AND jsonb_typeof(original_request_evidence)='object' "
        "AND octet_length(original_request_evidence::text)<=16384 "
        f"AND original_request_evidence ?& ARRAY[{keys}] "
        f"AND original_request_evidence - ARRAY[{keys}]='{{}}'::jsonb "
        "AND original_request_evidence->>'contract_version'='v2' "
        "AND original_request_evidence->'schema_version'='2'::jsonb "
        "AND original_request_evidence->>'platform'=platform "
        "AND jsonb_typeof(original_request_evidence->'server_instance_id')='string' "
        "AND jsonb_typeof(original_request_evidence->'identity_epoch')='number' "
        "AND jsonb_typeof(original_request_evidence->'api_origin')='string' "
        "AND jsonb_typeof(original_request_evidence->'stream_origin')='string' "
        "AND original_request_evidence->>'original_document_sha256'=encode(original_document_sha256,'hex') "
        "AND original_request_evidence->>'proof_digest'=encode(request_sha256,'hex') "
        "AND original_request_evidence->>'device_key_thumbprint_sha256'=encode(device_key_thumbprint_sha256,'hex') "
        "AND original_request_evidence->>'device_public_key_spki_hex' ~ '^[0-9a-f]{182}$' "
        "AND original_request_evidence->>'identity_thumbprint_sha256' ~ '^[0-9a-f]{64}$' "
        "AND original_request_evidence->>'signature_algorithm'='ES256-P1363' "
        "AND original_request_evidence->>'signature_hex' ~ '^[0-9a-f]{128}$' "
        "AND original_request_evidence->'matched_body_key'='true'::jsonb "
        "AND jsonb_typeof(original_request_evidence->'fields')='object' "
        "AND jsonb_typeof(original_request_evidence->'credential_hashes')='object' "
        "AND (" + " OR ".join(variants) + ")),false)"
    )


def upgrade() -> None:
    for table, operations, constraint in (
        ("device_admission", ("admission.submit",), "admission_v2_evidence_check"),
        (
            "trusted_device_reenrollment_challenge",
            ("trusted.challenge",),
            "trusted_challenge_v2_evidence_check",
        ),
        (
            "device_admission_exchange_receipt",
            ("admission.exchange", "trusted.exchange"),
            "admission_receipt_v2_evidence_check",
        ),
    ):
        op.execute(f"""
ALTER TABLE account.{table}
 ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'v1',
 ADD COLUMN original_document_sha256 BYTEA,
 ADD COLUMN original_request_evidence JSONB;
""")
        if table != "device_admission":
            op.execute(f"""
ALTER TABLE account.{table}
 ADD COLUMN platform TEXT, ADD COLUMN authority_generation BIGINT, ADD COLUMN trust_revision BIGINT, ADD COLUMN account_role TEXT;
""")
        op.execute(
            f"ALTER TABLE account.{table} ADD CONSTRAINT {constraint} CHECK ({_evidence_check(operations)})"
        )
    op.execute("""
ALTER TABLE account.device_admission
 ADD COLUMN approved_authority_generation BIGINT, ADD COLUMN approved_account_role TEXT,
 ADD CONSTRAINT admission_v2_authority_check CHECK (
  COALESCE(((contract_version='v1' AND approved_authority_generation IS NULL AND approved_account_role IS NULL) OR
   (contract_version='v2' AND expires_at=created_at+interval '900 seconds'
    AND ((approved_user_id IS NULL AND approved_authority_generation IS NULL AND approved_account_role IS NULL)
     OR (approved_user_id IS NOT NULL AND approved_authority_generation>=1 AND approved_account_role IN ('OWNER','ADMIN','USER'))))),false));
ALTER TABLE account.trusted_device_key
 ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'v1', ADD COLUMN platform TEXT,
 ADD CONSTRAINT trusted_key_v2_platform_check CHECK (
  COALESCE(((contract_version='v1' AND platform IS NULL) OR
   (contract_version='v2' AND platform IN ('ANDROID','MACOS'))),false));
ALTER TABLE account.trusted_device_reenrollment_challenge
 ADD CONSTRAINT trusted_challenge_v2_authority_check CHECK (
  COALESCE(((contract_version='v1' AND platform IS NULL AND authority_generation IS NULL AND trust_revision IS NULL AND account_role IS NULL)
   OR (contract_version='v2' AND authority_generation>=1 AND trust_revision>=1 AND account_role IN ('OWNER','ADMIN','USER')
    AND expires_at=created_at+interval '300 seconds')),false));
ALTER TABLE account.device_admission_exchange_receipt
 ADD CONSTRAINT admission_receipt_v2_authority_check CHECK (
  COALESCE(((contract_version='v1' AND platform IS NULL AND authority_generation IS NULL AND trust_revision IS NULL AND account_role IS NULL)
   OR (contract_version='v2' AND authority_generation>=1 AND (trust_revision IS NULL OR trust_revision>=1) AND account_role IN ('OWNER','ADMIN','USER')
    AND (original_request_evidence->>'operation'<>'trusted.exchange' OR trust_revision>=1)
    AND receipt_expires_at=created_at+interval '30 days')),false));
CREATE UNIQUE INDEX admission_v2_binding_commit ON account.device_admission_exchange_receipt(binding_commit_id)
 WHERE contract_version='v2';
ALTER TABLE account.device_admission_web_operation_receipt
 ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'v1',
 ADD CONSTRAINT admission_web_v2_version_check CHECK (contract_version IN ('v1','v2'));
CREATE FUNCTION app_private.guard_device_admission_v2() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE mutable_columns text[];
BEGIN
 IF OLD.contract_version<>'v2' AND NEW.contract_version<>'v2' THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME IN ('device_admission_exchange_receipt','device_admission_web_operation_receipt') THEN
  IF NEW IS DISTINCT FROM OLD THEN
   RAISE EXCEPTION 'admission_v2_receipt_immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 ELSIF TG_TABLE_NAME='trusted_device_key' THEN
  mutable_columns:=ARRAY['approved_request_id','revision','removed_at','contract_version','platform'];
  IF OLD.contract_version='v2' AND
   ROW(NEW.contract_version,NEW.platform) IS DISTINCT FROM ROW(OLD.contract_version,OLD.platform) THEN
   RAISE EXCEPTION 'trusted_v2_platform_immutable' USING ERRCODE='23514';
  END IF;
 ELSIF TG_TABLE_NAME='trusted_device_reenrollment_challenge' THEN
  mutable_columns:=ARRAY['consumed_at'];
  IF OLD.consumed_at IS NOT NULL AND NEW.consumed_at IS DISTINCT FROM OLD.consumed_at THEN
   RAISE EXCEPTION 'trusted_v2_challenge_spent' USING ERRCODE='23514';
  END IF;
 ELSE
  mutable_columns:=ARRAY['state','review_locator_hash','review_binding_hash','review_web_session_id',
   'poll_bearer_hash','last_poll_at','secret_generation','recovery_count','last_recovery_at',
   'approved_user_id','approved_authority_generation','approved_account_role','approved_device_name','decision_action','decided_at',
   'enrolled_device_id','enrolled_session_id'];
  IF OLD.state<>'PENDING' AND ROW(NEW.approved_user_id,NEW.approved_authority_generation,NEW.approved_account_role,
    NEW.approved_device_name,NEW.decision_action,NEW.decided_at)
   IS DISTINCT FROM ROW(OLD.approved_user_id,OLD.approved_authority_generation,OLD.approved_account_role,
    OLD.approved_device_name,OLD.decision_action,OLD.decided_at) THEN
   RAISE EXCEPTION 'admission_v2_decision_immutable' USING ERRCODE='23514';
  END IF;
  IF OLD.state NOT IN ('PENDING','APPROVED') AND
   ROW(NEW.state,NEW.enrolled_device_id,NEW.enrolled_session_id)
    IS DISTINCT FROM ROW(OLD.state,OLD.enrolled_device_id,OLD.enrolled_session_id) THEN
   RAISE EXCEPTION 'admission_v2_terminal' USING ERRCODE='23514';
  END IF;
  IF OLD.state='APPROVED' AND NEW.state NOT IN ('APPROVED','EXCHANGED') THEN
   RAISE EXCEPTION 'admission_v2_approval_terminal' USING ERRCODE='23514';
  END IF;
 END IF;
 IF (to_jsonb(NEW)-mutable_columns) IS DISTINCT FROM (to_jsonb(OLD)-mutable_columns) THEN
  RAISE EXCEPTION 'admission_v2_evidence_immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER admission_v2_guard BEFORE UPDATE ON account.device_admission
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_device_admission_v2();
CREATE TRIGGER trusted_key_v2_guard BEFORE UPDATE ON account.trusted_device_key
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_device_admission_v2();
CREATE TRIGGER trusted_challenge_v2_guard BEFORE UPDATE ON account.trusted_device_reenrollment_challenge
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_device_admission_v2();
CREATE TRIGGER admission_receipt_v2_guard BEFORE UPDATE ON account.device_admission_exchange_receipt
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_device_admission_v2();
CREATE TRIGGER admission_web_v2_guard BEFORE UPDATE ON account.device_admission_web_operation_receipt
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_device_admission_v2();
REVOKE ALL ON FUNCTION app_private.guard_device_admission_v2() FROM PUBLIC;
""")


def downgrade() -> None:
    # Acquire ALL affected tables and preflight ALL retained/partial evidence
    # before dropping even one trigger, index, constraint or column. No deletion.
    op.execute("""
LOCK TABLE account.device_admission, account.trusted_device_key,
 account.trusted_device_reenrollment_challenge, account.device_admission_exchange_receipt,
 account.device_admission_web_operation_receipt
 IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM account.device_admission WHERE contract_version<>'v1'
  OR original_document_sha256 IS NOT NULL OR original_request_evidence IS NOT NULL
  OR approved_authority_generation IS NOT NULL OR approved_account_role IS NOT NULL)
 OR EXISTS(SELECT 1 FROM account.trusted_device_key WHERE contract_version<>'v1' OR platform IS NOT NULL)
 OR EXISTS(SELECT 1 FROM account.trusted_device_reenrollment_challenge WHERE contract_version<>'v1'
  OR platform IS NOT NULL OR original_document_sha256 IS NOT NULL OR original_request_evidence IS NOT NULL
  OR authority_generation IS NOT NULL OR trust_revision IS NOT NULL OR account_role IS NOT NULL)
 OR EXISTS(SELECT 1 FROM account.device_admission_exchange_receipt WHERE contract_version<>'v1'
  OR platform IS NOT NULL OR original_document_sha256 IS NOT NULL OR original_request_evidence IS NOT NULL
  OR authority_generation IS NOT NULL OR trust_revision IS NOT NULL OR account_role IS NOT NULL)
 OR EXISTS(SELECT 1 FROM account.device_admission_web_operation_receipt WHERE contract_version<>'v1') THEN
  RAISE EXCEPTION 'refusing to discard admission v2 evidence' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER admission_v2_guard ON account.device_admission;
DROP TRIGGER trusted_key_v2_guard ON account.trusted_device_key;
DROP TRIGGER trusted_challenge_v2_guard ON account.trusted_device_reenrollment_challenge;
DROP TRIGGER admission_receipt_v2_guard ON account.device_admission_exchange_receipt;
DROP TRIGGER admission_web_v2_guard ON account.device_admission_web_operation_receipt;
DROP FUNCTION app_private.guard_device_admission_v2();
DROP INDEX account.admission_v2_binding_commit;
ALTER TABLE account.device_admission_web_operation_receipt
 DROP CONSTRAINT admission_web_v2_version_check, DROP COLUMN contract_version;
ALTER TABLE account.device_admission
 DROP CONSTRAINT admission_v2_evidence_check, DROP CONSTRAINT admission_v2_authority_check,
 DROP COLUMN contract_version, DROP COLUMN original_document_sha256,
 DROP COLUMN original_request_evidence, DROP COLUMN approved_authority_generation, DROP COLUMN approved_account_role;
ALTER TABLE account.trusted_device_key
 DROP CONSTRAINT trusted_key_v2_platform_check, DROP COLUMN contract_version, DROP COLUMN platform;
ALTER TABLE account.trusted_device_reenrollment_challenge
 DROP CONSTRAINT trusted_challenge_v2_evidence_check, DROP CONSTRAINT trusted_challenge_v2_authority_check,
 DROP COLUMN contract_version, DROP COLUMN platform, DROP COLUMN original_document_sha256,
 DROP COLUMN original_request_evidence, DROP COLUMN authority_generation, DROP COLUMN trust_revision, DROP COLUMN account_role;
ALTER TABLE account.device_admission_exchange_receipt
 DROP CONSTRAINT admission_receipt_v2_evidence_check, DROP CONSTRAINT admission_receipt_v2_authority_check,
 DROP COLUMN contract_version, DROP COLUMN platform, DROP COLUMN original_document_sha256,
 DROP COLUMN original_request_evidence, DROP COLUMN authority_generation, DROP COLUMN trust_revision, DROP COLUMN account_role;
""")
