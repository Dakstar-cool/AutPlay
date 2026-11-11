"""Add bounded immutable hash-only portable ROOT evidence; preserve legacy bytes."""

from alembic import op

revision = "0070_device_portable_contract_v2"
down_revision = "0069_device_profile_contract_v2"
branch_labels = None
depends_on = None


def _evidence_check() -> str:
    # Frozen migration-local SQL. Never import current models/policies/crypto.
    fields = (
        "'purpose','contract_version','schema_version','platform','domain',"
        "'signature_algorithm','signature_encoding','request_sha256',"
        "'original_document_sha256','root_proof_sha256','account_id','public_id',"
        "'root_spki_hex','root_thumbprint_sha256','device_spki_hex',"
        "'device_thumbprint_sha256','binding_commit_id','next_refresh_token_sha256',"
        "'device_name','app_version','account_role'"
    )
    receipt_fields = (
        "'contract_version','schema_version','platform','account_id','device_id',"
        "'session_id','session_family_id','generation','binding_commit_id','token_id',"
        "'issued_at','access_expires_at','refresh_expires_at'"
    )
    challenge_fields = (
        "'contract_version','schema_version','platform','challenge_id','purpose','account_id',"
        "'document_sha256','server_instance_id','identity_epoch','identity_thumbprint_sha256',"
        "'api_origin','stream_origin','nonce','expires_at','actor_device_id','actor_session_id',"
        "'action','operation_id','target_device_id'"
    )
    return (
        "(contract_version='v1' AND platform IS NULL AND original_document_sha256 IS NULL "
        "AND root_proof_sha256 IS NULL AND original_request_evidence IS NULL "
        "AND binding_commit_id IS NULL AND authority_generation IS NULL AND root_revision IS NULL) "
        "OR COALESCE((contract_version='v2' AND platform IN ('ANDROID','MACOS') "
        "AND purpose IN ('REGISTER','LOGIN') AND actor_device_id IS NULL AND actor_session_id IS NULL "
        "AND document->>'contract_version'='v2' AND document->'schema_version'='2'::jsonb "
        "AND document->>'platform'=platform AND document->>'purpose'=purpose "
        "AND document->>'challenge_id'=challenge_id::text AND document->>'account_id'=account_id::text "
        f"AND document ?& ARRAY[{challenge_fields}] "
        f"AND document - ARRAY[{challenge_fields}]='{{}}'::jsonb "
        "AND document->'actor_device_id'='null'::jsonb AND document->'actor_session_id'='null'::jsonb "
        "AND document->'action'='null'::jsonb AND document->'operation_id'='null'::jsonb "
        "AND document->'target_device_id'='null'::jsonb "
        "AND document->>'document_sha256' ~ '^[0-9a-f]{64}$' "
        "AND document->>'identity_thumbprint_sha256' ~ '^[0-9a-f]{64}$' "
        "AND document->>'nonce' ~ '^[A-Za-z0-9_-]{43}$' "
        "AND jsonb_typeof(document->'api_origin')='string' "
        "AND jsonb_typeof(document->'stream_origin')='string' "
        "AND jsonb_typeof(document->'server_instance_id')='string' "
        "AND jsonb_typeof(document->'identity_epoch')='number' "
        "AND document->>'identity_epoch' ~ '^[1-9][0-9]{0,15}$' "
        "AND jsonb_typeof(document->'expires_at')='string' "
        "AND expires_at=created_at+interval '120 seconds' AND ("
        "(receipt IS NULL AND request_hash IS NULL AND receipt_expires_at IS NULL "
        "AND original_document_sha256 IS NULL AND root_proof_sha256 IS NULL "
        "AND original_request_evidence IS NULL AND binding_commit_id IS NULL "
        "AND authority_generation IS NULL AND root_revision IS NULL) OR "
        "(receipt IS NOT NULL AND request_hash IS NOT NULL AND receipt_expires_at IS NOT NULL "
        "AND octet_length(original_document_sha256)=32 AND octet_length(root_proof_sha256)=32 "
        "AND binding_commit_id IS NOT NULL AND authority_generation>=1 AND root_revision>=1 "
        "AND jsonb_typeof(original_request_evidence)='object' "
        "AND octet_length(original_request_evidence::text)<=8192 "
        f"AND original_request_evidence ?& ARRAY[{fields}] "
        f"AND original_request_evidence - ARRAY[{fields}]='{{}}'::jsonb "
        "AND original_request_evidence->>'purpose'=purpose "
        "AND original_request_evidence->>'contract_version'='v2' "
        "AND original_request_evidence->'schema_version'='2'::jsonb "
        "AND original_request_evidence->>'platform'=platform "
        "AND original_request_evidence->>'account_id'=account_id::text "
        "AND original_request_evidence->>'binding_commit_id'=binding_commit_id::text "
        "AND original_request_evidence->>'request_sha256'=encode(request_hash,'hex') "
        "AND original_request_evidence->>'original_document_sha256'=encode(original_document_sha256,'hex') "
        "AND original_request_evidence->>'root_proof_sha256'=encode(root_proof_sha256,'hex') "
        "AND original_request_evidence->>'signature_algorithm'='ECDSA-P256-SHA256' "
        "AND original_request_evidence->>'signature_encoding'='DER/standard-padded-base64' "
        "AND original_request_evidence->>'domain'=E'autplay:portable-account:'||lower(purpose)||E'\\:v2\\n' "
        "AND original_request_evidence->>'root_spki_hex' ~ '^[0-9a-f]{182}$' "
        "AND original_request_evidence->>'device_spki_hex' ~ '^[0-9a-f]{182}$' "
        "AND original_request_evidence->>'root_thumbprint_sha256' ~ '^[0-9a-f]{64}$' "
        "AND original_request_evidence->>'device_thumbprint_sha256' ~ '^[0-9a-f]{64}$' "
        "AND original_request_evidence->>'next_refresh_token_sha256' ~ '^[0-9a-f]{64}$' "
        "AND original_request_evidence->>'public_id' ~ '^[a-z0-9_]{3,24}$' "
        "AND original_request_evidence->>'account_role' IN ('USER','ADMIN','OWNER') "
        "AND jsonb_typeof(original_request_evidence->'device_name')='string' "
        "AND length(original_request_evidence->>'device_name') BETWEEN 1 AND 120 "
        "AND jsonb_typeof(original_request_evidence->'app_version')='string' "
        "AND length(original_request_evidence->>'app_version') BETWEEN 1 AND 32 "
        "AND jsonb_typeof(receipt)='object' AND octet_length(receipt::text)<=4096 "
        f"AND receipt ?& ARRAY[{receipt_fields}] "
        f"AND receipt - ARRAY[{receipt_fields}]='{{}}'::jsonb "
        "AND receipt->>'contract_version'='v2' AND receipt->'schema_version'='2'::jsonb "
        "AND receipt->>'platform'=platform AND receipt->>'account_id'=account_id::text "
        "AND receipt->>'binding_commit_id'=binding_commit_id::text "
        "AND receipt->>'session_family_id'=receipt->>'session_id' "
        "AND receipt->>'session_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
        "AND receipt->>'device_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
        "AND receipt->>'token_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
        "AND jsonb_typeof(receipt->'issued_at')='string' AND length(receipt->>'issued_at')<=30 "
        "AND jsonb_typeof(receipt->'access_expires_at')='string' AND length(receipt->>'access_expires_at')<=30 "
        "AND jsonb_typeof(receipt->'refresh_expires_at')='string' AND length(receipt->>'refresh_expires_at')<=30 "
        "AND receipt->'generation'='0'::jsonb))),false)"
    )


def upgrade() -> None:
    op.execute(f"""
ALTER TABLE account.portable_challenge
 ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'v1',
 ADD COLUMN platform TEXT,
 ADD COLUMN original_document_sha256 BYTEA,
 ADD COLUMN root_proof_sha256 BYTEA,
 ADD COLUMN original_request_evidence JSONB,
 ADD COLUMN binding_commit_id UUID,
 ADD COLUMN authority_generation BIGINT,
 ADD COLUMN root_revision BIGINT,
 ADD CONSTRAINT portable_challenge_v2_evidence_check CHECK ({_evidence_check()});
CREATE UNIQUE INDEX portable_challenge_binding_commit
 ON account.portable_challenge(binding_commit_id);
""")
    # Legacy platform stays NULL. No passport/signed request/hash backfill.
    op.execute("""
CREATE FUNCTION app_private.guard_portable_v2_challenge() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.contract_version='v2' OR NEW.contract_version='v2' THEN
  IF ROW(NEW.challenge_id,NEW.purpose,NEW.source_key,NEW.account_id,
         NEW.actor_device_id,NEW.actor_session_id,NEW.document,NEW.created_at,
         NEW.expires_at,NEW.contract_version,NEW.platform)
     IS DISTINCT FROM
     ROW(OLD.challenge_id,OLD.purpose,OLD.source_key,OLD.account_id,
         OLD.actor_device_id,OLD.actor_session_id,OLD.document,OLD.created_at,
         OLD.expires_at,OLD.contract_version,OLD.platform)
     OR (OLD.receipt IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
   RAISE EXCEPTION 'portable_v2_evidence_immutable' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER portable_challenge_v2_guard BEFORE UPDATE
 ON account.portable_challenge FOR EACH ROW
 EXECUTE FUNCTION app_private.guard_portable_v2_challenge();
REVOKE ALL ON FUNCTION app_private.guard_portable_v2_challenge() FROM PUBLIC;
""")
    # Existing bounded prune, family retirement and authorized privacy purge
    # remain the only deletion authorities. This trigger does not block them.


def downgrade() -> None:
    # Preflight every retained v2/partial new field BEFORE any schema removal.
    # Expiry does not authorize erasure/rewrite to make a downgrade possible.
    op.execute("""
LOCK TABLE account.portable_challenge IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM account.portable_challenge
  WHERE contract_version IS DISTINCT FROM 'v1' OR platform IS NOT NULL
   OR original_document_sha256 IS NOT NULL OR root_proof_sha256 IS NOT NULL
   OR original_request_evidence IS NOT NULL OR binding_commit_id IS NOT NULL
   OR authority_generation IS NOT NULL OR root_revision IS NOT NULL) THEN
  RAISE EXCEPTION 'portable_v2_retention_blocks_downgrade' USING ERRCODE='23514';
 END IF;
END $$;
""")
    op.execute("""
DROP TRIGGER portable_challenge_v2_guard ON account.portable_challenge;
DROP FUNCTION app_private.guard_portable_v2_challenge();
DROP INDEX account.portable_challenge_binding_commit;
ALTER TABLE account.portable_challenge
 DROP CONSTRAINT portable_challenge_v2_evidence_check,
 DROP COLUMN root_revision, DROP COLUMN authority_generation,
 DROP COLUMN binding_commit_id, DROP COLUMN original_request_evidence,
 DROP COLUMN root_proof_sha256, DROP COLUMN original_document_sha256,
 DROP COLUMN platform, DROP COLUMN contract_version;
""")
