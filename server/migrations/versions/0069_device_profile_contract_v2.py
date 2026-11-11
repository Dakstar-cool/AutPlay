"""Add bounded, immutable D1 evidence to the two existing M5 receipt tables."""

from alembic import op

revision = "0069_device_profile_contract_v2"
down_revision = "0068_device_capability_revision"
branch_labels = None
depends_on = None


def _evidence_check(operation: str) -> str:
    # Frozen migration-local projection, deliberately independent from live ORM.
    enrollment = operation == "enrollment.exchange"
    credential = "invitation_secret_sha256" if enrollment else "current_refresh_token_sha256"
    keys = (
        "'operation','contract_version','schema_version','platform','server_instance_id','identity_epoch',"
        "'identity_thumbprint_sha256','api_origin','stream_origin','device_public_key_spki_hex',"
        "'device_key_thumbprint_sha256','matched_body_key','domain','signature_algorithm','proof_digest',"
        "'original_document_sha256','signature_hex','fields','credential_hashes'"
    )
    field_names = (
        (
            "exchange_id",
            "binding_commit_id",
            "invitation_id",
            "expected_user_id",
            "device_name",
            "app_version",
            "next_refresh_token_sha256",
            "client_nonce_b64url",
            "request_sha256",
        )
        if enrollment
        else (
            "rotation_id",
            "device_id",
            "parent_session_id",
            "current_generation",
            "next_refresh_token_sha256",
            "request_sha256",
        )
    )
    fields = ",".join(f"'{field}'" for field in field_names)
    scalar_types = (
        "".join(
            f"AND jsonb_typeof(original_request_evidence->'{field}')='string' "
            for field in (
                "operation",
                "contract_version",
                "platform",
                "server_instance_id",
                "identity_thumbprint_sha256",
                "api_origin",
                "stream_origin",
                "device_public_key_spki_hex",
                "device_key_thumbprint_sha256",
                "domain",
                "signature_algorithm",
                "proof_digest",
                "original_document_sha256",
                "signature_hex",
            )
        )
        + "AND jsonb_typeof(original_request_evidence->'identity_epoch')='number' "
    )
    field_types = "".join(
        f"AND jsonb_typeof(original_request_evidence->'fields'->'{field}')="
        f"'{'number' if field == 'current_generation' else 'string'}' "
        for field in field_names
    )
    return (
        "(contract_version = 'v1' AND platform IS NULL "
        "AND original_document_sha256 IS NULL AND original_request_evidence IS NULL "
        "AND authority_generation IS NULL) OR COALESCE((contract_version = 'v2' "
        "AND platform IN ('ANDROID', 'MACOS') "
        "AND octet_length(original_document_sha256) = 32 "
        "AND authority_generation >= 1 "
        "AND jsonb_typeof(original_request_evidence) = 'object' "
        "AND octet_length(original_request_evidence::text) <= 16384 "
        f"AND original_request_evidence->>'operation' = '{operation}' "
        "AND original_request_evidence->>'contract_version' = 'v2' "
        "AND original_request_evidence->'schema_version' = '2'::jsonb "
        "AND original_request_evidence->>'platform' = platform "
        "AND original_request_evidence->>'original_document_sha256' = encode(original_document_sha256,'hex') "
        "AND original_request_evidence->>'proof_digest' = encode(request_sha256,'hex') "
        "AND original_request_evidence->>'device_key_thumbprint_sha256' = encode(device_key_thumbprint_sha256,'hex') "
        "AND original_request_evidence->>'signature_algorithm' = 'ES256-P1363' "
        "AND original_request_evidence->>'signature_hex' ~ '^[0-9a-f]{128}$' "
        f"AND original_request_evidence ?& ARRAY[{keys}] "
        f"AND original_request_evidence - ARRAY[{keys}] = '{{}}'::jsonb "
        f"{scalar_types}"
        f"AND original_request_evidence->>'domain' = E'autplay:device-compatibility:{operation.replace('.', ':')}:v2\\n' "
        f"AND original_request_evidence->'matched_body_key' = '{'true' if enrollment else 'false'}'::jsonb "
        "AND original_request_evidence->>'device_public_key_spki_hex' ~ '^[0-9a-f]{182}$' "
        "AND original_request_evidence->>'identity_thumbprint_sha256' ~ '^[0-9a-f]{64}$' "
        "AND jsonb_typeof(original_request_evidence->'fields') = 'object' "
        f"AND (original_request_evidence->'fields') ?& ARRAY[{fields}] "
        f"AND (original_request_evidence->'fields') - ARRAY[{fields}] = '{{}}'::jsonb "
        f"{field_types}"
        "AND jsonb_typeof(original_request_evidence->'credential_hashes') = 'object' "
        f"AND (original_request_evidence->'credential_hashes') - '{credential}' = '{{}}'::jsonb "
        f"AND original_request_evidence->'credential_hashes'->>'{credential}' ~ '^[0-9a-f]{{64}}$'), false)"
    )


def upgrade() -> None:
    for table, operation, constraint in (
        (
            "enrollment_exchange_receipt",
            "enrollment.exchange",
            "enrollment_receipt_contract_evidence_check",
        ),
        ("session_rotation_receipt", "session.rotate", "rotation_receipt_contract_evidence_check"),
    ):
        # Constant identifiers only. No backfill of old platform/hash/proof data.
        op.execute(f"""
ALTER TABLE account.{table}
 ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'v1',
 ADD COLUMN platform TEXT,
 ADD COLUMN original_document_sha256 BYTEA,
 ADD COLUMN original_request_evidence JSONB,
 ADD COLUMN authority_generation BIGINT,
 ADD CONSTRAINT {constraint} CHECK ({_evidence_check(operation)});
""")
    op.execute("""
CREATE FUNCTION app_private.guard_device_profile_receipt() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF (OLD.contract_version='v2' OR NEW.contract_version='v2')
 AND NEW IS DISTINCT FROM OLD THEN
  RAISE EXCEPTION 'device_profile_receipt_immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER enrollment_receipt_contract_guard BEFORE UPDATE
 ON account.enrollment_exchange_receipt FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_device_profile_receipt();
CREATE TRIGGER rotation_receipt_contract_guard BEFORE UPDATE
 ON account.session_rotation_receipt FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_device_profile_receipt();
REVOKE ALL ON FUNCTION app_private.guard_device_profile_receipt() FROM PUBLIC;
""")
    # Existing bounded expiry cleanup and privacy erasure remain authoritative.
    # No new delete/truncate path and no change to original receipt retention.


def downgrade() -> None:
    # Lock BOTH tables before either preflight or any destructive schema step.
    # Refuse any retained v2/partial new evidence, including expired receipts;
    # never erase evidence or rewrite old hashes to make rollback fit.
    op.execute("""
LOCK TABLE account.enrollment_exchange_receipt, account.session_rotation_receipt
 IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM account.enrollment_exchange_receipt
  WHERE contract_version <> 'v1' OR platform IS NOT NULL
   OR original_document_sha256 IS NOT NULL OR original_request_evidence IS NOT NULL
   OR authority_generation IS NOT NULL)
 OR EXISTS(SELECT 1 FROM account.session_rotation_receipt
  WHERE contract_version <> 'v1' OR platform IS NOT NULL
   OR original_document_sha256 IS NOT NULL OR original_request_evidence IS NOT NULL
   OR authority_generation IS NOT NULL) THEN
  RAISE EXCEPTION 'refusing to discard device profile v2 evidence' USING ERRCODE='55000';
 END IF;
END $$;
DROP TRIGGER enrollment_receipt_contract_guard ON account.enrollment_exchange_receipt;
DROP TRIGGER rotation_receipt_contract_guard ON account.session_rotation_receipt;
DROP FUNCTION app_private.guard_device_profile_receipt();
ALTER TABLE account.enrollment_exchange_receipt
 DROP CONSTRAINT enrollment_receipt_contract_evidence_check,
 DROP COLUMN contract_version, DROP COLUMN platform,
 DROP COLUMN original_document_sha256, DROP COLUMN original_request_evidence,
 DROP COLUMN authority_generation;
ALTER TABLE account.session_rotation_receipt
 DROP CONSTRAINT rotation_receipt_contract_evidence_check,
 DROP COLUMN contract_version, DROP COLUMN platform,
 DROP COLUMN original_document_sha256, DROP COLUMN original_request_evidence,
 DROP COLUMN authority_generation;
""")
