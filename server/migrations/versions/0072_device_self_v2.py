"""D1 SELF immutable nonsecret originals; legacy v1 rows remain untouched."""

from alembic import op

revision = "0072_device_self_v2"
down_revision = "0071_device_admission_v2"
branch_labels = None
depends_on = None


def _ceremony_check() -> str:
    # Frozen copy of the ORM constraint, never import a live application model.
    return """
 (contract_version='v1' AND source_platform IS NULL AND recipient_platform IS NULL
  AND source_public_key_spki IS NULL AND source_key_thumbprint_sha256 IS NULL
  AND start_request_sha256 IS NULL AND start_original_document_sha256 IS NULL
  AND claim_request_sha256 IS NULL AND claim_original_document_sha256 IS NULL
  AND exchange_request_sha256 IS NULL AND exchange_original_document_sha256 IS NULL AND binding_commit_id IS NULL)
 OR COALESCE((contract_version='v2' AND source_platform IN ('ANDROID','MACOS')
  AND octet_length(source_public_key_spki)=91 AND octet_length(source_key_thumbprint_sha256)=32
  AND octet_length(start_request_sha256)=32 AND octet_length(start_original_document_sha256)=32
  AND app_private.valid_self_document_v2(start_document,'start')
  AND start_document->>'platform'=source_platform AND start_document->>'ceremony_id'=ceremony_id::text
  AND start_document->>'operation_id'=start_operation_id::text
  AND start_document->>'expected_server_instance_id'=server_instance_id::text
  AND start_document->>'request_sha256'=encode(start_request_sha256,'hex')
  AND expires_at=created_at+interval '900 seconds'
  AND (state<>'OPEN' OR claim_document IS NULL)
  AND ((claim_document IS NULL AND recipient_platform IS NULL AND claim_request_sha256 IS NULL AND claim_original_document_sha256 IS NULL)
   OR (claim_document IS NOT NULL AND recipient_platform IN ('ANDROID','MACOS')
    AND octet_length(claim_request_sha256)=32 AND octet_length(claim_original_document_sha256)=32
    AND app_private.valid_self_document_v2(claim_document,'claim')
    AND claim_document->>'platform'=recipient_platform AND claim_document->>'claim_id'=claim_id::text
    AND claim_document->>'request_sha256'=encode(claim_request_sha256,'hex')
    AND claim_document->>'ceremony_id'=ceremony_id::text
    AND (claim_document - ARRAY['platform','requested_at','request_sha256','device_signature_b64url','claim_id','poll_secret_sha256','device_public_key_spki_b64','device_key_thumbprint_sha256','device_name','app_version'])
      = (start_document - ARRAY['platform','requested_at','request_sha256','operation_id','rendezvous_secret_sha256'])))
  AND ((exchange_document IS NULL AND exchange_request_sha256 IS NULL AND exchange_original_document_sha256 IS NULL AND binding_commit_id IS NULL)
   OR (exchange_document IS NOT NULL AND octet_length(exchange_request_sha256)=32 AND octet_length(exchange_original_document_sha256)=32
    AND app_private.valid_self_document_v2(exchange_document,'exchange')
    AND exchange_document->>'platform'=recipient_platform AND exchange_document->>'exchange_id'=exchange_id::text
    AND exchange_document->>'binding_commit_id'=binding_commit_id::text
    AND exchange_document->>'request_sha256'=encode(exchange_request_sha256,'hex')
    AND exchange_document->>'claim_id'=claim_id::text
    AND exchange_document->>'claim_request_sha256'=encode(claim_request_sha256,'hex')
    AND exchange_document->>'approval_operation_id'=approval_operation_id::text
    AND exchange_document->>'confirmed_account_id'=user_id::text
    AND (exchange_document - ARRAY['platform','requested_at','request_sha256','device_signature_b64url','exchange_id','binding_commit_id','claim_id','claim_request_sha256','approval_operation_id','confirmed_account_id','next_refresh_token_sha256'])
      = (start_document - ARRAY['platform','requested_at','request_sha256','operation_id','rendezvous_secret_sha256'])))),false)
"""


def _command_check() -> str:
    return """
 (contract_version='v1' AND source_platform IS NULL AND original_document IS NULL AND original_document_sha256 IS NULL)
 OR COALESCE((contract_version='v2' AND source_platform IN ('ANDROID','MACOS')
  AND octet_length(original_document_sha256)=32 AND app_private.valid_self_document_v2(original_document,'decision')
  AND original_document->>'operation_id'=operation_id::text AND original_document->>'ceremony_id'=ceremony_id::text
  AND original_document->>'platform'=source_platform AND original_document->>'request_sha256'=encode(request_hash,'hex')
  AND app_private.valid_self_document_v2(result,'status') AND result->>'ceremony_id'=ceremony_id::text
  AND result->>'source_platform'=source_platform),false)
"""


def upgrade() -> None:
    op.execute(r"""
CREATE FUNCTION app_private.valid_self_document_v2(document jsonb, kind text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
 common text[] := ARRAY['contract_version','schema_version','platform','ceremony_id','requested_at','request_sha256',
  'expected_server_instance_id','expected_identity_epoch','expected_identity_thumbprint_sha256','expected_api_origin','expected_stream_origin'];
 required text[]; allowed text[]; item record; scalar text; typ text;
BEGIN
 IF document IS NULL OR jsonb_typeof(document)<>'object' OR octet_length(document::text)>8192 THEN RETURN false; END IF;
 CASE kind
 WHEN 'start' THEN required:=common||ARRAY['operation_id','rendezvous_secret_sha256'];
 WHEN 'claim' THEN required:=common||ARRAY['claim_id','poll_secret_sha256','device_public_key_spki_b64',
  'device_key_thumbprint_sha256','device_name','app_version','device_signature_b64url'];
 WHEN 'poll' THEN required:=common||ARRAY['claim_id','claim_request_sha256','device_signature_b64url'];
 WHEN 'decision' THEN required:=common||ARRAY['operation_id','action','expected_revision','claim_id','claim_request_sha256','comparison_code'];
 WHEN 'exchange' THEN required:=common||ARRAY['exchange_id','binding_commit_id','claim_id','claim_request_sha256',
  'approval_operation_id','confirmed_account_id','next_refresh_token_sha256','device_signature_b64url'];
 WHEN 'status' THEN required:=ARRAY['contract_version','schema_version','ceremony_id','state','revision','expires_at',
  'retry_after_seconds','source_platform','recipient_platform'];
 ELSE RETURN false;
 END CASE;
 allowed:=required;
 IF kind='status' THEN allowed:=allowed||ARRAY['claim_id','claim_request_sha256','device_name',
  'device_key_thumbprint_sha256','comparison_code','account_id','account_label','approval_operation_id','device_id']; END IF;
 IF NOT document ?& required OR document-allowed<>'{}'::jsonb THEN RETURN false; END IF;
 IF document->>'contract_version'<>'v2' OR document->'schema_version'<>'2'::jsonb THEN RETURN false; END IF;
 FOR item IN SELECT key,value FROM jsonb_each(document) LOOP
  typ:=jsonb_typeof(item.value); scalar:=item.value #>> '{}';
  IF typ='null' THEN
   IF NOT ((kind='decision' AND item.key IN ('claim_id','claim_request_sha256','comparison_code'))
     OR (kind='status' AND item.key='recipient_platform')) THEN RETURN false; END IF;
   CONTINUE;
  END IF;
  IF item.key IN ('schema_version','expected_identity_epoch','expected_revision','revision','retry_after_seconds') THEN
   IF typ<>'number' OR scalar !~ '^[1-9][0-9]{0,15}$' OR scalar::numeric>9007199254740991 THEN RETURN false; END IF;
  ELSE
   -- Reject objects/arrays even under an allowed public name. No nested credential alias.
   IF typ<>'string' OR length(scalar) NOT BETWEEN 1 AND 2048 THEN RETURN false; END IF;
   IF item.key LIKE '%\_id' ESCAPE '\' AND scalar !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
   IF item.key LIKE '%\_sha256' ESCAPE '\' AND scalar !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
   IF item.key IN ('platform','source_platform','recipient_platform') AND scalar NOT IN ('ANDROID','MACOS') THEN RETURN false; END IF;
   IF item.key='device_signature_b64url' AND scalar !~ '^[A-Za-z0-9_-]{86}$' THEN RETURN false; END IF;
   IF item.key='device_public_key_spki_b64' AND (length(scalar)<>124 OR scalar !~ '^[A-Za-z0-9+/]+==$') THEN RETURN false; END IF;
   IF item.key IN ('device_name','account_label','app_version') AND scalar ~ '[[:cntrl:]]' THEN RETURN false; END IF;
   IF item.key='device_name' AND length(scalar)>120 THEN RETURN false; END IF;
   IF item.key='app_version' AND length(scalar)>32 THEN RETURN false; END IF;
   IF item.key='account_label' AND length(scalar)>240 THEN RETURN false; END IF;
   IF item.key IN ('requested_at','expires_at') THEN
    IF scalar !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z$' THEN RETURN false; END IF;
    PERFORM scalar::timestamptz;
   END IF;
   IF item.key IN ('expected_api_origin','expected_stream_origin') AND
    scalar !~ '^https?://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(:[0-9]{1,5})?$' THEN RETURN false; END IF;
   IF item.key='comparison_code' AND scalar !~ '^[0-9]{12}$' THEN RETURN false; END IF;
  END IF;
 END LOOP;
 IF kind='decision' THEN
  IF document->>'action' NOT IN ('APPROVE','REJECT','CANCEL')
   OR ((document->'claim_id'='null'::jsonb)<>(document->'claim_request_sha256'='null'::jsonb)) THEN RETURN false; END IF;
  IF document->>'action' IN ('APPROVE','REJECT') AND document->'claim_id'='null'::jsonb THEN RETURN false; END IF;
  IF document->>'action'='APPROVE' THEN
   IF jsonb_typeof(document->'comparison_code')<>'string' THEN RETURN false; END IF;
  ELSIF document->'comparison_code'<>'null'::jsonb THEN RETURN false;
  END IF;
 ELSIF kind='status' THEN
  IF document->>'state' NOT IN ('OPEN','CLAIMED','APPROVED','EXCHANGED','CANCELLED','REJECTED','EXPIRED')
   OR document->'retry_after_seconds'<>'2'::jsonb THEN RETURN false; END IF;
  IF document ? 'claim_id' THEN
   IF NOT document ?& ARRAY['claim_request_sha256','device_name','device_key_thumbprint_sha256','comparison_code']
    OR jsonb_typeof(document->'recipient_platform')<>'string' THEN RETURN false; END IF;
  ELSE
   IF document ?| ARRAY['claim_request_sha256','device_name','device_key_thumbprint_sha256','comparison_code']
    OR document->'recipient_platform'<>'null'::jsonb THEN RETURN false; END IF;
  END IF;
  IF (document ? 'account_id')<>(document ? 'account_label') THEN RETURN false; END IF;
  IF document->>'state' IN ('CLAIMED','APPROVED','EXCHANGED','REJECTED') AND NOT document ? 'claim_id' THEN RETURN false; END IF;
  IF document->>'state' IN ('APPROVED','EXCHANGED') AND NOT document ? 'approval_operation_id' THEN RETURN false; END IF;
  IF document->>'state'='EXCHANGED' AND NOT document ? 'device_id' THEN RETURN false; END IF;
 END IF;
 RETURN true;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN RETURN false;
END $$;
ALTER TABLE account.self_device_pairing
 ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'v1', ADD COLUMN source_platform TEXT, ADD COLUMN recipient_platform TEXT,
 ADD COLUMN source_public_key_spki BYTEA, ADD COLUMN source_key_thumbprint_sha256 BYTEA,
 ADD COLUMN start_request_sha256 BYTEA, ADD COLUMN start_original_document_sha256 BYTEA,
 ADD COLUMN claim_request_sha256 BYTEA, ADD COLUMN claim_original_document_sha256 BYTEA,
 ADD COLUMN exchange_request_sha256 BYTEA, ADD COLUMN exchange_original_document_sha256 BYTEA,
 ADD COLUMN binding_commit_id UUID;
ALTER TABLE account.self_pairing_command
 ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'v1', ADD COLUMN source_platform TEXT,
 ADD COLUMN original_document JSONB, ADD COLUMN original_document_sha256 BYTEA;
""")
    op.execute(
        f"ALTER TABLE account.self_device_pairing ADD CONSTRAINT self_pairing_v2_evidence_check CHECK ({_ceremony_check()})"
    )
    op.execute(
        f"ALTER TABLE account.self_pairing_command ADD CONSTRAINT self_pairing_command_v2_evidence_check CHECK ({_command_check()})"
    )
    op.execute("""
CREATE UNIQUE INDEX self_pairing_v2_binding_commit ON account.self_device_pairing(binding_commit_id) WHERE contract_version='v2';
CREATE FUNCTION app_private.guard_self_device_v2() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE mutable_columns text[];
BEGIN
 IF OLD.contract_version='v1' AND NEW.contract_version='v1' THEN RETURN NEW; END IF;
 IF OLD.contract_version IS DISTINCT FROM NEW.contract_version THEN
  RAISE EXCEPTION 'self_v2_version_immutable' USING ERRCODE='23514';
 END IF;
 IF TG_TABLE_NAME='self_pairing_command' THEN
  IF NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'self_v2_command_immutable' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 mutable_columns:=ARRAY['state','revision','claim_id','claim_document','recipient_platform','claim_request_sha256',
  'claim_original_document_sha256','approval_operation_id','last_polled_at','exchange_id','exchange_document',
  'exchange_request_sha256','exchange_original_document_sha256','binding_commit_id','result_device_id','result_session_id','receipt_expires_at'];
 IF to_jsonb(NEW)-mutable_columns IS DISTINCT FROM to_jsonb(OLD)-mutable_columns THEN
  RAISE EXCEPTION 'self_v2_source_immutable' USING ERRCODE='23514';
 END IF;
 IF ROW(NEW.claim_id,NEW.claim_document,NEW.recipient_platform,NEW.claim_request_sha256,NEW.claim_original_document_sha256)
  IS DISTINCT FROM ROW(OLD.claim_id,OLD.claim_document,OLD.recipient_platform,OLD.claim_request_sha256,OLD.claim_original_document_sha256) THEN
  IF OLD.claim_id IS NOT NULL THEN
   RAISE EXCEPTION 'self_v2_claim_immutable' USING ERRCODE='23514';
  END IF;
  -- The entire first claim belongs only to the OPEN -> CLAIMED state change.
  IF NOT (OLD.state='OPEN' AND NEW.state='CLAIMED' AND NEW.revision=OLD.revision+1
   AND NEW.claim_id IS NOT NULL AND NEW.claim_document IS NOT NULL AND NEW.recipient_platform IS NOT NULL
   AND NEW.claim_request_sha256 IS NOT NULL AND NEW.claim_original_document_sha256 IS NOT NULL) THEN
   RAISE EXCEPTION 'self_v2_claim_transition' USING ERRCODE='23514';
  END IF;
 END IF;
 IF NEW.approval_operation_id IS DISTINCT FROM OLD.approval_operation_id THEN
  IF OLD.approval_operation_id IS NOT NULL THEN
   RAISE EXCEPTION 'self_v2_approval_immutable' USING ERRCODE='23514';
  END IF;
  IF NOT (OLD.state='CLAIMED' AND NEW.state='APPROVED' AND NEW.revision=OLD.revision+1
   AND NEW.approval_operation_id IS NOT NULL) THEN
   RAISE EXCEPTION 'self_v2_approval_transition' USING ERRCODE='23514';
  END IF;
 END IF;
 IF ROW(NEW.exchange_id,NEW.exchange_document,NEW.exchange_request_sha256,NEW.exchange_original_document_sha256,
  NEW.binding_commit_id,NEW.result_device_id,NEW.result_session_id,NEW.receipt_expires_at) IS DISTINCT FROM
  ROW(OLD.exchange_id,OLD.exchange_document,OLD.exchange_request_sha256,OLD.exchange_original_document_sha256,
   OLD.binding_commit_id,OLD.result_device_id,OLD.result_session_id,OLD.receipt_expires_at) THEN
  IF OLD.exchange_id IS NOT NULL THEN
   RAISE EXCEPTION 'self_v2_exchange_immutable' USING ERRCODE='23514';
  END IF;
  IF NOT (OLD.state='APPROVED' AND NEW.state='EXCHANGED' AND NEW.revision=OLD.revision+1
   AND NEW.exchange_id IS NOT NULL AND NEW.exchange_document IS NOT NULL AND NEW.exchange_request_sha256 IS NOT NULL
   AND NEW.exchange_original_document_sha256 IS NOT NULL AND NEW.binding_commit_id IS NOT NULL
   AND NEW.result_device_id IS NOT NULL AND NEW.result_session_id IS NOT NULL AND NEW.receipt_expires_at IS NOT NULL) THEN
   RAISE EXCEPTION 'self_v2_exchange_transition' USING ERRCODE='23514';
  END IF;
 END IF;
 IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
  (OLD.state='OPEN' AND NEW.state IN ('CLAIMED','CANCELLED')) OR
  (OLD.state='CLAIMED' AND NEW.state IN ('APPROVED','REJECTED','CANCELLED')) OR
  (OLD.state='APPROVED' AND NEW.state IN ('EXCHANGED','CANCELLED'))) THEN
  RAISE EXCEPTION 'self_v2_terminal' USING ERRCODE='23514';
 END IF;
 IF NEW.revision <> OLD.revision + (CASE WHEN NEW.state IS DISTINCT FROM OLD.state THEN 1 ELSE 0 END) THEN
  RAISE EXCEPTION 'self_v2_revision' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER self_pairing_v2_guard BEFORE UPDATE ON account.self_device_pairing
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_self_device_v2();
CREATE TRIGGER self_command_v2_guard BEFORE UPDATE ON account.self_pairing_command
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_self_device_v2();
REVOKE ALL ON FUNCTION app_private.guard_self_device_v2() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_private.valid_self_document_v2(jsonb,text) FROM PUBLIC;
""")


def downgrade() -> None:
    # LOCK and preflight BOTH affected tables before any DROP. Also refuse partial
    # retained additions even if a future writer labels such a row as legacy v1.
    op.execute("""
LOCK TABLE account.self_device_pairing, account.self_pairing_command IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM account.self_device_pairing WHERE contract_version<>'v1'
  OR source_platform IS NOT NULL OR recipient_platform IS NOT NULL OR source_public_key_spki IS NOT NULL
  OR source_key_thumbprint_sha256 IS NOT NULL OR start_request_sha256 IS NOT NULL OR start_original_document_sha256 IS NOT NULL
  OR claim_request_sha256 IS NOT NULL OR claim_original_document_sha256 IS NOT NULL
  OR exchange_request_sha256 IS NOT NULL OR exchange_original_document_sha256 IS NOT NULL OR binding_commit_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM account.self_device_pairing WHERE start_document->>'contract_version'='v2'
  OR start_document->'schema_version'='2'::jsonb OR claim_document->>'contract_version'='v2'
  OR claim_document->'schema_version'='2'::jsonb OR exchange_document->>'contract_version'='v2'
  OR exchange_document->'schema_version'='2'::jsonb)
 OR EXISTS(SELECT 1 FROM account.self_pairing_command WHERE contract_version<>'v1'
  OR source_platform IS NOT NULL OR original_document IS NOT NULL OR original_document_sha256 IS NOT NULL
  OR result->>'contract_version'='v2' OR result->'schema_version'='2'::jsonb) THEN
  RAISE EXCEPTION 'Refusing to discard retained SELF v2 evidence';
 END IF;
END $$;
DROP TRIGGER self_command_v2_guard ON account.self_pairing_command;
DROP TRIGGER self_pairing_v2_guard ON account.self_device_pairing;
DROP FUNCTION app_private.guard_self_device_v2();
DROP INDEX account.self_pairing_v2_binding_commit;
ALTER TABLE account.self_pairing_command DROP CONSTRAINT self_pairing_command_v2_evidence_check;
ALTER TABLE account.self_device_pairing DROP CONSTRAINT self_pairing_v2_evidence_check;
ALTER TABLE account.self_pairing_command DROP COLUMN original_document_sha256, DROP COLUMN original_document,
 DROP COLUMN source_platform, DROP COLUMN contract_version;
ALTER TABLE account.self_device_pairing DROP COLUMN binding_commit_id, DROP COLUMN exchange_original_document_sha256,
 DROP COLUMN exchange_request_sha256, DROP COLUMN claim_original_document_sha256, DROP COLUMN claim_request_sha256,
 DROP COLUMN start_original_document_sha256, DROP COLUMN start_request_sha256,
 DROP COLUMN source_key_thumbprint_sha256, DROP COLUMN source_public_key_spki,
 DROP COLUMN recipient_platform, DROP COLUMN source_platform, DROP COLUMN contract_version;
DROP FUNCTION app_private.valid_self_document_v2(jsonb,text);
""")
