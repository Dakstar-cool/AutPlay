"""D1 immutable full recovery originals, with empty-only v2 downgrade preflight.

Frozen DDL; no live model imports, legacy backfill, proof rewrite or secret copy.
"""

from alembic import op

revision = "0073_device_recovery_v2"
down_revision = "0072_device_self_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE FUNCTION app_private.valid_recovery_commit_v2(doc jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE AS $$
    DECLARE member text; value jsonb; decoded bytea;
      origin_parts text[]; origin_host text; host_label text;
      names text[] := ARRAY['contract_version','schema_version','platform','operation_id','account_id',
        'requested_at','request_sha256','device_public_key_spki_b64','device_key_thumbprint_sha256',
        'device_name','app_version','device_signature_b64url','expected_server_instance_id',
        'expected_identity_epoch','expected_identity_thumbprint_sha256','expected_api_origin',
        'expected_stream_origin','expected_code_generation','next_code_verifier_sha256',
        'next_refresh_token_sha256','binding_commit_id','confirmed_account_id'];
    BEGIN
      IF doc IS NULL OR jsonb_typeof(doc)<>'object' OR octet_length(doc::text)>8192
        OR NOT doc ?& names OR (SELECT count(*) FROM jsonb_object_keys(doc))<>cardinality(names)
        THEN RETURN false; END IF;
      FOR member,value IN SELECT * FROM jsonb_each(doc) LOOP
        IF member=ANY(ARRAY['schema_version','expected_identity_epoch','expected_code_generation']) THEN
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
        OR doc->>'operation_id'=doc->>'binding_commit_id'
        OR doc->>'requested_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z$'
        OR length(doc->>'device_name')>120 OR length(doc->>'app_version')>32
        OR doc->>'device_name' ~ '[\x00-\x1f\x7f]' OR doc->>'app_version' ~ '[\x00-\x1f\x7f]'
        THEN RETURN false; END IF;
      PERFORM (doc->>'requested_at')::timestamptz;
      FOREACH member IN ARRAY ARRAY['expected_api_origin','expected_stream_origin'] LOOP
        -- Frozen domain _origin predicate; retain the exact original spelling.
        origin_parts := regexp_match((doc->>member) COLLATE "C",
          '\Ahttps?://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?\Z');
        IF origin_parts IS NULL THEN RETURN false; END IF;
        IF origin_parts[2] IS NOT NULL AND origin_parts[2]::integer NOT BETWEEN 1 AND 65535
          THEN RETURN false; END IF;
        origin_host := origin_parts[1];
        IF left(origin_host,1)='[' THEN
          origin_host := substr(origin_host,2,length(origin_host)-2);
          -- inet validates hextets/compression. Its IPv4 suffix parser can accept
          -- empty octets; Python IPv6Address requires exactly four strict octets.
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

    ALTER TABLE account.account_recovery_operation
      ADD COLUMN contract_version text NOT NULL DEFAULT 'v1',
      ADD COLUMN platform text,
      ADD COLUMN original_document_sha256 bytea,
      ADD COLUMN original_request_document jsonb,
      ADD COLUMN signature_algorithm text,
      ADD COLUMN proof_domain text,
      ADD CONSTRAINT recovery_operation_v2_check CHECK (
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
          AND operation_id<>binding_commit_id),false));

    CREATE OR REPLACE FUNCTION account.guard_recovery_operation() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      -- Preserve the exact owner-purge exception installed by 0053; no new authority.
      IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(OLD.user_id)
        THEN RETURN OLD; END IF;
      IF TG_OP='DELETE' AND OLD.expires_at<clock_timestamp() THEN
        IF OLD.contract_version='v2' AND EXISTS (
          SELECT 1 FROM account.user_session s JOIN account.device d
            ON (d.user_id,d.device_id)=(s.user_id,s.device_id)
          WHERE s.session_id=OLD.result_session_id AND s.user_id=OLD.user_id
            AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() AND d.revoked_at IS NULL
        ) THEN RAISE EXCEPTION 'live recovery v2 result retains original proof'; END IF;
        RETURN OLD;
      END IF;
      RAISE EXCEPTION 'recovery operation receipt is immutable until expiry';
    END $$;
    REVOKE ALL ON FUNCTION app_private.valid_recovery_commit_v2(jsonb) FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
    LOCK TABLE account.account_recovery_operation, account.user_session, account.device
      IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM account.account_recovery_operation
        WHERE contract_version IS DISTINCT FROM 'v1' OR platform IS NOT NULL
          OR original_document_sha256 IS NOT NULL OR original_request_document IS NOT NULL
          OR signature_algorithm IS NOT NULL OR proof_domain IS NOT NULL)
        THEN RAISE EXCEPTION 'Refusing to discard retained recovery v2 evidence'; END IF;
    END $$;
    CREATE OR REPLACE FUNCTION account.guard_recovery_operation() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' AND app_private.privacy_owner_allowed(OLD.user_id)
        THEN RETURN OLD; END IF;
      IF TG_OP='DELETE' AND OLD.expires_at<clock_timestamp() THEN RETURN OLD; END IF;
      RAISE EXCEPTION 'recovery operation receipt is immutable until expiry';
    END $$;
    ALTER TABLE account.account_recovery_operation DROP CONSTRAINT recovery_operation_v2_check;
    ALTER TABLE account.account_recovery_operation DROP COLUMN proof_domain,
      DROP COLUMN signature_algorithm, DROP COLUMN original_request_document,
      DROP COLUMN original_document_sha256, DROP COLUMN platform, DROP COLUMN contract_version;
    DROP FUNCTION app_private.valid_recovery_commit_v2(jsonb);
    """)
