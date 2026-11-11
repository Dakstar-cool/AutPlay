"""D1 full nonsecret deletion history and exact cancellation, source only.

No legacy rewrite, independent ledger change, pgcrypto, or new retention term.
"""

from alembic import op

revision = "0074_device_deletion_v2"
down_revision = "0073_device_recovery_v2"
branch_labels = None
depends_on = None


# Frozen 0073 predicate. Only DELETE_CANCEL is added by upgrade; downgrade
# restores this exact recovery-only predicate after all-table evidence preflight.
RECOVERY_V2_CHECK = r"""
 (contract_version='v1' AND platform IS NULL AND original_document_sha256 IS NULL
  AND original_request_document IS NULL AND signature_algorithm IS NULL AND proof_domain IS NULL)
 OR COALESCE((contract_version='v2' AND kind='RECOVER' AND platform IN ('ANDROID','MACOS')
  AND octet_length(original_document_sha256)=32 AND signature_algorithm='ES256-P1363'
  AND proof_domain=E'autplay:device-compatibility:recovery:recover:v2\n'
  AND app_private.valid_recovery_commit_v2(original_request_document)
  AND original_request_document->>'platform'=platform
  AND original_request_document->>'operation_id'=operation_id::text
  AND original_request_document->>'binding_commit_id'=binding_commit_id::text
  AND original_request_document->>'account_id'=user_id::text
  AND original_request_document->>'expected_server_instance_id'=server_instance_id::text
  AND original_request_document->>'expected_identity_epoch'=identity_epoch::text
  AND original_request_document->>'expected_code_generation'=previous_generation::text
  AND original_request_document->>'request_sha256'=encode(request_sha256,'hex')
  AND original_request_document->>'next_code_verifier_sha256'=encode(next_verifier_sha256,'hex')
  AND operation_id<>binding_commit_id),false)
"""


def upgrade() -> None:
    op.execute(r"""
    CREATE FUNCTION app_private.valid_deletion_document_v2(doc jsonb, operation text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE AS $$
    DECLARE member text; value jsonb; decoded bytea;
      origin_parts text[]; origin_host text; host_label text;
      names text[] := ARRAY['contract_version','schema_version','platform','operation_id','account_id',
        'requested_at','request_sha256','device_public_key_spki_b64','device_key_thumbprint_sha256',
        'device_name','app_version','device_signature_b64url','expected_server_instance_id',
        'expected_identity_epoch','expected_identity_thumbprint_sha256','expected_api_origin',
        'expected_stream_origin','expected_code_generation','confirmed_account_id'];
    BEGIN
      IF operation IS NULL OR operation NOT IN ('REQUEST','CANCEL') THEN RETURN false; END IF;
      IF operation='REQUEST' THEN names:=names||ARRAY['expected_authority_generation'];
      ELSE names:=names||ARRAY['next_code_verifier_sha256','next_refresh_token_sha256',
        'binding_commit_id','deletion_request_id','expected_revision']; END IF;
      IF doc IS NULL OR jsonb_typeof(doc)<>'object' OR octet_length(doc::text)>8192
        OR NOT doc ?& names OR (SELECT count(*) FROM jsonb_object_keys(doc))<>cardinality(names)
        THEN RETURN false; END IF;
      FOR member,value IN SELECT * FROM jsonb_each(doc) LOOP
        IF member=ANY(ARRAY['schema_version','expected_identity_epoch','expected_code_generation',
          'expected_authority_generation','expected_revision']) THEN
          IF jsonb_typeof(value)<>'number' OR value::text !~ '^[0-9]+$'
            OR (value::text)::numeric NOT BETWEEN 1 AND 9007199254740991 THEN RETURN false; END IF;
        ELSE
          IF jsonb_typeof(value)<>'string' OR length(doc->>member) NOT BETWEEN 1 AND 2048 THEN RETURN false; END IF;
          IF member LIKE '%_id' AND (doc->>member)::uuid::text<>doc->>member THEN RETURN false; END IF;
          IF member LIKE '%_sha256' AND doc->>member !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
        END IF;
      END LOOP;
      IF doc->>'contract_version'<>'v2' OR doc->>'schema_version'<>'2'
        OR doc->>'platform' NOT IN ('ANDROID','MACOS')
        OR doc->>'confirmed_account_id'<>doc->>'account_id'
        OR (doc->>'requested_at') COLLATE "C" !~ '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z\Z'
        OR length(doc->>'device_name')>120 OR length(doc->>'app_version')>32
        OR doc->>'device_name' ~ '[\x00-\x1f\x7f]' OR doc->>'app_version' ~ '[\x00-\x1f\x7f]'
        THEN RETURN false; END IF;
      -- Match the family's datetime.fromisoformat of the first nineteen characters calendar/clock,
      -- retaining 1..9 fractional digits and literal UTC Z exactly as signed.
      -- PostgreSQL time input also admits 24:00:00 and second 60; Python does not.
      -- Primary: postgres REL_18_STABLE src/backend/utils/adt/date.c.
      IF substring(doc->>'requested_at',1,4)::integer NOT BETWEEN 1 AND 9999
        OR substring(doc->>'requested_at',12,2)::integer NOT BETWEEN 0 AND 23
        OR substring(doc->>'requested_at',15,2)::integer NOT BETWEEN 0 AND 59
        OR substring(doc->>'requested_at',18,2)::integer NOT BETWEEN 0 AND 59
        THEN RETURN false; END IF;
      IF operation='CANCEL' AND (doc->>'operation_id'=doc->>'binding_commit_id'
        OR doc->>'operation_id'=doc->>'deletion_request_id'
        OR doc->>'binding_commit_id'=doc->>'deletion_request_id') THEN RETURN false; END IF;
      -- Fractional rounding (e.g. .999999999 at year 9999) must not carry into
      -- another second/year: Python validates only the unchanged first 19 bytes.
      PERFORM (left(doc->>'requested_at',19)||'Z')::timestamptz;
      FOREACH member IN ARRAY ARRAY['expected_api_origin','expected_stream_origin'] LOOP
        -- Match Python's raw family origin spelling, including trailing DNS dot.
        origin_parts := regexp_match((doc->>member) COLLATE "C",
          '\Ahttps?://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?\Z');
        IF origin_parts IS NULL THEN RETURN false; END IF;
        IF origin_parts[2] IS NOT NULL AND origin_parts[2]::integer NOT BETWEEN 1 AND 65535
          THEN RETURN false; END IF;
        origin_host := origin_parts[1];
        IF left(origin_host,1)='[' THEN
          origin_host := substr(origin_host,2,length(origin_host)-2);
          IF strpos(origin_host,'.')>0 AND split_part(origin_host,':',-1) COLLATE "C"
            !~ '\A(0|[1-9][0-9]{0,2})(\.(0|[1-9][0-9]{0,2})){3}\Z'
            THEN RETURN false; END IF;
          IF family(origin_host::inet)<>6 THEN RETURN false; END IF;
        ELSE
          IF right(origin_host,1)='.' THEN origin_host := left(origin_host,length(origin_host)-1); END IF;
          IF length(origin_host) NOT BETWEEN 1 AND 253 THEN RETURN false; END IF;
          FOREACH host_label IN ARRAY string_to_array(origin_host,'.') LOOP
            IF host_label COLLATE "C" !~ '\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z'
              THEN RETURN false; END IF;
          END LOOP;
        END IF;
      END LOOP;
      IF length(doc->>'device_public_key_spki_b64') NOT BETWEEN 32 AND 256
        OR doc->>'device_public_key_spki_b64' !~ '^[A-Za-z0-9+/]+={0,2}$'
        OR doc->>'device_signature_b64url' !~ '^[A-Za-z0-9_-]{86}$' THEN RETURN false; END IF;
      decoded := decode(doc->>'device_public_key_spki_b64','base64');
      IF octet_length(decoded)<>91 OR replace(encode(decoded,'base64'),E'\n','')<>doc->>'device_public_key_spki_b64'
        OR encode(pg_catalog.sha256(decoded),'hex')<>doc->>'device_key_thumbprint_sha256'
        THEN RETURN false; END IF;
      decoded := decode(translate(doc->>'device_signature_b64url','-_','+/')||'==','base64');
      RETURN octet_length(decoded)=64 AND translate(rtrim(replace(encode(decoded,'base64'),E'\n',''),'='),'+/','-_')=doc->>'device_signature_b64url';
    EXCEPTION WHEN OTHERS THEN RETURN false;
    END $$;

    CREATE TABLE account.account_deletion_proof (
      operation_id uuid PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE CASCADE,
      server_instance_id uuid NOT NULL REFERENCES account.server_instance(server_instance_id),
      identity_epoch bigint NOT NULL,
      actor_device_id uuid NOT NULL,
      actor_public_key_spki bytea NOT NULL,
      actor_key_thumbprint_sha256 bytea NOT NULL,
      code_verifier_sha256 bytea NOT NULL,
      contract_version text NOT NULL,
      platform text NOT NULL,
      request_sha256 bytea NOT NULL,
      original_document_sha256 bytea NOT NULL,
      original_request_document jsonb NOT NULL,
      signature_algorithm text NOT NULL,
      proof_domain text NOT NULL,
      decision text NOT NULL,
      decided_at timestamptz NOT NULL,
      CONSTRAINT deletion_proof_actor_fkey FOREIGN KEY(user_id,actor_device_id)
        REFERENCES account.device(user_id,device_id) ON DELETE CASCADE,
      CONSTRAINT deletion_proof_v2_check CHECK(COALESCE((
        contract_version='v2' AND platform IN ('ANDROID','MACOS')
        AND decision IN ('ATTEMPTED','SEALED') AND identity_epoch>=1
        AND signature_algorithm='ES256-P1363'
        AND proof_domain=E'autplay:device-compatibility:deletion:request:v2\n'
        AND octet_length(request_sha256)=32 AND octet_length(original_document_sha256)=32
        AND octet_length(code_verifier_sha256)=32 AND octet_length(actor_public_key_spki)=91
        AND octet_length(actor_key_thumbprint_sha256)=32
        AND pg_catalog.sha256(actor_public_key_spki)=actor_key_thumbprint_sha256
        AND app_private.valid_deletion_document_v2(original_request_document,'REQUEST')
        AND original_request_document->>'operation_id'=operation_id::text
        AND original_request_document->>'account_id'=user_id::text
        AND original_request_document->>'expected_server_instance_id'=server_instance_id::text
        AND original_request_document->>'expected_identity_epoch'=identity_epoch::text
        AND original_request_document->>'platform'=platform
        AND original_request_document->>'request_sha256'=encode(request_sha256,'hex')
        AND original_request_document->>'device_key_thumbprint_sha256'=encode(actor_key_thumbprint_sha256,'hex')
        AND decode(original_request_document->>'device_public_key_spki_b64','base64')=actor_public_key_spki),false))
    );
    CREATE INDEX ix_deletion_proof_user ON account.account_deletion_proof(user_id,decided_at);
    ALTER TABLE account.account_deletion_request
      ADD COLUMN contract_version text NOT NULL DEFAULT 'v1', ADD COLUMN platform text,
      ADD CONSTRAINT deletion_request_v2_check CHECK(
        (contract_version='v1' AND platform IS NULL)
        OR COALESCE((contract_version='v2' AND platform IN ('ANDROID','MACOS')),false));

    CREATE FUNCTION app_private.guard_deletion_proof_v2() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(OLD.user_id) THEN RETURN OLD; END IF;
      RAISE EXCEPTION 'deletion original proof is immutable';
    END $$;
    CREATE TRIGGER deletion_proof_v2_guard BEFORE UPDATE OR DELETE ON account.account_deletion_proof
      FOR EACH ROW EXECUTE FUNCTION app_private.guard_deletion_proof_v2();
    CREATE TRIGGER deletion_proof_v2_no_truncate BEFORE TRUNCATE ON account.account_deletion_proof
      FOR EACH STATEMENT EXECUTE FUNCTION app_private.guard_deletion_proof_v2();

    CREATE FUNCTION app_private.check_deletion_v2() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE proof account.account_deletion_proof%ROWTYPE;
      parent account.account_deletion_request%ROWTYPE;
      result_device account.device%ROWTYPE;
      result_binding account.user_session%ROWTYPE;
    BEGIN
      IF TG_TABLE_NAME='account_deletion_proof' THEN
        SELECT * INTO result_device FROM account.device WHERE device_id=NEW.actor_device_id;
        IF result_device.device_id IS NULL OR result_device.user_id<>NEW.user_id
          OR result_device.platform IS DISTINCT FROM NEW.platform
          OR result_device.public_key IS DISTINCT FROM NEW.actor_public_key_spki
          OR result_device.public_key_thumbprint_sha256 IS DISTINCT FROM NEW.actor_key_thumbprint_sha256
          THEN RAISE EXCEPTION 'deletion actor evidence mismatch'; END IF;
        SELECT * INTO parent FROM account.account_deletion_request WHERE deletion_request_id=NEW.operation_id;
        IF (NEW.decision='SEALED' AND parent.deletion_request_id IS NOT NULL)
          OR (NEW.decision='ATTEMPTED' AND (parent.deletion_request_id IS NULL OR parent.contract_version<>'v2'))
          THEN RAISE EXCEPTION 'deletion proof decision has no exact result'; END IF;
        RETURN NULL;
      END IF;
      IF NEW.contract_version='v1' THEN RETURN NEW; END IF;
      IF TG_TABLE_NAME='account_deletion_request' THEN
        SELECT * INTO proof FROM account.account_deletion_proof WHERE operation_id=NEW.deletion_request_id;
        IF proof.operation_id IS NULL OR proof.decision<>'ATTEMPTED'
          OR ROW(proof.user_id,proof.server_instance_id,proof.identity_epoch,proof.platform,
            proof.actor_device_id,proof.actor_public_key_spki,proof.request_sha256,proof.code_verifier_sha256,proof.decided_at)
            IS DISTINCT FROM ROW(NEW.user_id,NEW.server_instance_id,NEW.identity_epoch,NEW.platform,
            NEW.actor_device_id,NEW.actor_public_key_spki,NEW.request_sha256,NEW.code_verifier_sha256,NEW.requested_at)
          OR proof.original_request_document->>'expected_identity_thumbprint_sha256'<>encode(NEW.identity_thumbprint_sha256,'hex')
          OR (proof.original_request_document->>'expected_code_generation')::bigint<>NEW.code_generation
          OR (proof.original_request_document->>'expected_authority_generation')::bigint+1<>NEW.authority_generation
          THEN RAISE EXCEPTION 'deletion request original mismatch'; END IF;
      ELSIF TG_TABLE_NAME='account_recovery_operation' AND NEW.kind='DELETE_CANCEL' THEN
        SELECT * INTO parent FROM account.account_deletion_request
          WHERE deletion_request_id=(NEW.original_request_document->>'deletion_request_id')::uuid;
        SELECT * INTO result_device FROM account.device WHERE device_id=NEW.result_device_id;
        SELECT * INTO result_binding FROM account.user_session WHERE session_id=NEW.result_session_id;
        IF parent.deletion_request_id IS NULL OR parent.state IS DISTINCT FROM 'PENDING'
          OR parent.user_id<>NEW.user_id OR parent.server_instance_id<>NEW.server_instance_id
          OR parent.identity_epoch<>NEW.identity_epoch
          OR parent.identity_thumbprint_sha256 IS DISTINCT FROM decode(NEW.original_request_document->>'expected_identity_thumbprint_sha256','hex')
          OR parent.revision<>(NEW.original_request_document->>'expected_revision')::bigint
          OR parent.code_generation<>NEW.previous_generation
          OR parent.code_verifier_sha256 IS DISTINCT FROM NEW.spent_verifier_sha256
          OR parent.authority_generation+1<>NEW.authority_generation
          OR NEW.created_at<parent.requested_at OR NEW.created_at>=parent.cancel_before
          OR result_device.device_id IS NULL OR result_device.user_id<>NEW.user_id
          OR result_device.platform<>NEW.platform
          OR result_device.public_key IS DISTINCT FROM decode(NEW.original_request_document->>'device_public_key_spki_b64','base64')
          OR result_device.public_key_thumbprint_sha256 IS DISTINCT FROM decode(NEW.original_request_document->>'device_key_thumbprint_sha256','hex')
          OR result_binding.session_id IS NULL OR result_binding.user_id<>NEW.user_id
          OR result_binding.device_id<>NEW.result_device_id OR result_binding.session_mode IS DISTINCT FROM 'V2'
          OR result_binding.family_id IS DISTINCT FROM result_binding.session_id OR result_binding.generation IS DISTINCT FROM 0
          OR result_binding.revoked_at IS NOT NULL OR result_device.revoked_at IS NOT NULL
          OR result_binding.issued_at<>NEW.created_at OR result_binding.expires_at<=NEW.created_at
          OR result_binding.refresh_token_hash IS DISTINCT FROM decode(NEW.original_request_document->>'next_refresh_token_sha256','hex')
          THEN RAISE EXCEPTION 'deletion cancel original mismatch'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE CONSTRAINT TRIGGER deletion_proof_v2_result AFTER INSERT ON account.account_deletion_proof
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app_private.check_deletion_v2();
    CREATE TRIGGER deletion_request_v2_original BEFORE INSERT OR UPDATE ON account.account_deletion_request
      FOR EACH ROW EXECUTE FUNCTION app_private.check_deletion_v2();
    CREATE TRIGGER deletion_cancel_v2_original BEFORE INSERT ON account.account_recovery_operation
      FOR EACH ROW EXECUTE FUNCTION app_private.check_deletion_v2();
    REVOKE ALL ON account.account_deletion_proof FROM PUBLIC;
    REVOKE ALL ON FUNCTION app_private.valid_deletion_document_v2(jsonb,text),
      app_private.guard_deletion_proof_v2(),app_private.check_deletion_v2() FROM PUBLIC;
    """)
    # Preserve the 0073 recovery branch verbatim; add only this closed cancel branch.
    cancel_check = (
        RECOVERY_V2_CHECK.replace("kind='RECOVER'", "kind='DELETE_CANCEL'")
        .replace("recovery:recover:v2", "deletion:cancel:v2")
        .replace(
            "app_private.valid_recovery_commit_v2(original_request_document)",
            "app_private.valid_deletion_document_v2(original_request_document,'CANCEL')",
        )
    )
    op.execute(
        "ALTER TABLE account.account_recovery_operation DROP CONSTRAINT recovery_operation_v2_check"
    )
    op.execute(
        "ALTER TABLE account.account_recovery_operation ADD CONSTRAINT recovery_operation_v2_check CHECK (("
        + RECOVERY_V2_CHECK
        + ") OR ("
        + cancel_check
        + "))"
    )


def downgrade() -> None:
    op.execute("""
    LOCK TABLE account.account_deletion_proof, account.account_deletion_request,
      account.account_recovery_operation, account.user_account, account.device,
      account.user_session, account.server_instance IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM account.account_deletion_proof)
        OR EXISTS(SELECT 1 FROM account.account_deletion_request
          WHERE contract_version IS DISTINCT FROM 'v1' OR platform IS NOT NULL)
        OR EXISTS(SELECT 1 FROM account.account_recovery_operation
          WHERE (kind IS DISTINCT FROM 'RECOVER'
            OR proof_domain IS DISTINCT FROM E'autplay:device-compatibility:recovery:recover:v2\n')
          AND (contract_version IS DISTINCT FROM 'v1' OR platform IS NOT NULL
            OR original_document_sha256 IS NOT NULL OR original_request_document IS NOT NULL
            OR signature_algorithm IS NOT NULL OR proof_domain IS NOT NULL))
        THEN RAISE EXCEPTION 'Refusing to discard retained deletion v2 evidence'; END IF;
    END $$;
    DROP TRIGGER deletion_cancel_v2_original ON account.account_recovery_operation;
    DROP TRIGGER deletion_request_v2_original ON account.account_deletion_request;
    DROP TABLE account.account_deletion_proof;
    DROP FUNCTION app_private.check_deletion_v2();
    DROP FUNCTION app_private.guard_deletion_proof_v2();
    ALTER TABLE account.account_deletion_request DROP CONSTRAINT deletion_request_v2_check;
    ALTER TABLE account.account_deletion_request DROP COLUMN platform, DROP COLUMN contract_version;
    ALTER TABLE account.account_recovery_operation DROP CONSTRAINT recovery_operation_v2_check;
    """)
    op.execute(
        "ALTER TABLE account.account_recovery_operation ADD CONSTRAINT recovery_operation_v2_check CHECK ("
        + RECOVERY_V2_CHECK
        + ")"
    )
    op.execute("DROP FUNCTION app_private.valid_deletion_document_v2(jsonb,text)")
