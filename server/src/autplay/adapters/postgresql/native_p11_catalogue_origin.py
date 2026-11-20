"""Bind the complete P11 v2 catalogue to its original Source3 transaction."""

CATALOGUE_ORIGIN_SQL = """
ALTER TABLE ml.native_original_capture_v3 ADD COLUMN catalogue_payload_sha256 bytea
 CHECK (catalogue_payload_sha256 IS NULL OR octet_length(catalogue_payload_sha256)=32);

CREATE FUNCTION app_private.bind_native_p11_catalogue_origin()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.catalogue_payload_sha256 IS NOT NULL THEN
  RAISE EXCEPTION 'NATIVE_CATALOGUE_CALLER_DIGEST' USING ERRCODE='23514';
 END IF;
 IF app_private.native_p11_source_version(NEW.pipeline_manifest_sha256)=2 THEN
  -- Check xmin only at creation, before this transaction commits. Current use
  -- subsequently compares the sealed digest and never depends on tuple age.
  SELECT c.payload_sha256 INTO NEW.catalogue_payload_sha256 FROM ml.sona_native_capture c
   WHERE c.request_id=NEW.original_request_id AND c.user_id=NEW.owner_user_id
    AND c.consent_revision=NEW.consent_generation AND c.surface='recommendations'
    AND c.xmin=pg_current_xact_id()::xid AND c.retained_until>=NEW.expires_at;
  IF NEW.catalogue_payload_sha256 IS NULL THEN
   RAISE EXCEPTION 'NATIVE_CATALOGUE_ORIGINAL_REQUIRED' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION app_private.bind_native_p11_catalogue_origin() FROM PUBLIC;
CREATE TRIGGER bind_native_p11_catalogue_origin BEFORE INSERT ON ml.native_original_capture_v3
 FOR EACH ROW EXECUTE FUNCTION app_private.bind_native_p11_catalogue_origin();
"""
