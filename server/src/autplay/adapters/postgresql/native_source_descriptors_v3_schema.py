"""Explicit, bounded APNI3 row projections; these functions confer no authority.

The owner prepays each call and streams its output into an already admitted
dependency. No function scans source rows or builds a complete index preimage.
Actual source association, generation and deadlines remain writer checks.
"""

from autplay.domain.native_source_descriptors_v3 import (
    SOURCE_CURSOR_DOMAIN_V3,
    SOURCE_INDEX_ENTRY_DOMAIN_V3,
)

# At most fourteen fixed fields, each <=32 bytes except the closed ASCII event
# name. The old LP loop copies <=512 bytes at most fourteen times; 16KiB includes
# these prefixes, raw conversions, final copies and the caller's output read.
NATIVE_SOURCE_DESCRIPTOR_SQL_MAX_ROW_BYTES = 512
NATIVE_SOURCE_DESCRIPTOR_SQL_BYTE_WORK = 16_384
NATIVE_SOURCE_DESCRIPTOR_SQL_NODE_VISITS = 256

NATIVE_SOURCE_DESCRIPTORS_V3_INSTALL_SQL = (
    r"""
CREATE FUNCTION app_private.native_v3_source_index_prefix(p_count integer) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
 IF p_count IS NULL OR p_count NOT BETWEEN 0 AND 10000 THEN
  RAISE EXCEPTION 'NATIVE_V3_INDEX_COUNT' USING ERRCODE='23514'; END IF;
 RETURN convert_to('APNI3','UTF8')||decode('00','hex')||int4send(p_count);
END $$;

CREATE FUNCTION app_private.native_v3_source_index_entry(
 p_original uuid,p_owner uuid,p_event uuid,p_request_sha bytea,p_normalized_sha bytea,
 p_device uuid,p_journal uuid,p_device_sequence bigint,p_server_sequence bigint,
 p_event_type text,p_association uuid,p_causal_request uuid,p_impression uuid,
 p_retained_until_us bigint) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
 IF p_original IS NULL OR p_owner IS NULL OR p_event IS NULL OR p_device IS NULL
  OR p_journal IS NULL OR p_request_sha IS NULL OR p_normalized_sha IS NULL
  OR octet_length(p_request_sha)<>32 OR octet_length(p_normalized_sha)<>32
  OR p_device_sequence IS NULL OR p_server_sequence IS NULL
  OR p_device_sequence NOT BETWEEN 1 AND 9007199254740991
  OR p_server_sequence NOT BETWEEN 1 AND 9007199254740991
  OR p_retained_until_us IS NULL OR p_retained_until_us<0
  OR p_retained_until_us%1000<>0 THEN
  RAISE EXCEPTION 'NATIVE_V3_INDEX_SCALAR' USING ERRCODE='23514'; END IF;
 IF p_event_type IS NULL OR NOT (
  (p_event_type='LISTENING_EVENT_RECORDED' AND
   ((p_association IS NULL AND p_causal_request IS NULL AND p_impression IS NULL)
    OR (p_association IS NOT NULL AND p_causal_request IS NOT NULL AND p_impression IS NOT NULL)))
  OR (p_event_type='RECOMMENDATION_IMPRESSION_RECORDED'
   AND p_association IS NOT NULL AND p_causal_request IS NOT NULL AND p_impression IS NULL)
  OR (p_event_type='RECOMMENDATION_FEEDBACK_RECORDED'
   AND p_association IS NOT NULL AND p_causal_request IS NOT NULL
   AND p_impression IS NOT NULL)) THEN
  RAISE EXCEPTION 'NATIVE_V3_INDEX_CAUSALITY' USING ERRCODE='23514'; END IF;
 RETURN app_private.native_served_lp('"""
    + SOURCE_INDEX_ENTRY_DOMAIN_V3
    + r"""',ARRAY[
  uuid_send(p_original),uuid_send(p_owner),uuid_send(p_event),p_request_sha,p_normalized_sha,
  uuid_send(p_device),uuid_send(p_journal),int8send(p_device_sequence),int8send(p_server_sequence),
  convert_to(p_event_type,'UTF8'),uuid_send(p_association),uuid_send(p_causal_request),
  uuid_send(p_impression),int8send(p_retained_until_us)]);
END $$;

CREATE FUNCTION app_private.native_v3_source_cursor(
 p_original uuid,p_owner uuid,p_count bigint,p_membership_sha bytea,p_cutoff_ms bigint,
 p_native_watermark bigint,p_generation bigint,p_created_transaction text,
 p_created_us bigint,p_expires_us bigint) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
 IF p_original IS NULL OR p_owner IS NULL OR p_count IS NULL
  OR p_count NOT BETWEEN 0 AND 10000 OR p_membership_sha IS NULL
  OR octet_length(p_membership_sha)<>32 OR p_cutoff_ms IS NULL
  OR p_cutoff_ms NOT BETWEEN 0 AND 9007199254740991 OR p_native_watermark IS NULL
  OR p_native_watermark NOT BETWEEN 0 AND 9007199254740991 OR p_generation IS NULL
  OR p_generation NOT BETWEEN 1 AND 9007199254740991
  OR p_created_us IS NULL OR p_created_us<0 OR p_expires_us IS NULL THEN
  RAISE EXCEPTION 'NATIVE_V3_CURSOR_SCALAR' USING ERRCODE='23514'; END IF;
 IF p_created_transaction IS NULL OR octet_length(p_created_transaction) NOT BETWEEN 1 AND 20 THEN
  RAISE EXCEPTION 'NATIVE_V3_CURSOR_XID' USING ERRCODE='23514'; END IF;
 IF p_created_transaction !~ '^[1-9][0-9]*$' THEN
  RAISE EXCEPTION 'NATIVE_V3_CURSOR_XID' USING ERRCODE='23514'; END IF;
 IF p_created_transaction::numeric>18446744073709551615::numeric
  OR p_expires_us-p_created_us<>15552000000000 OR p_cutoff_ms<>p_created_us/1000 THEN
  RAISE EXCEPTION 'NATIVE_V3_CURSOR_BINDING' USING ERRCODE='23514'; END IF;
 RETURN app_private.native_served_lp('"""
    + SOURCE_CURSOR_DOMAIN_V3
    + r"""',ARRAY[uuid_send(p_original),uuid_send(p_owner),convert_to('ACTIVE','UTF8'),
  int8send(p_count),p_membership_sha,int8send(p_cutoff_ms),int8send(p_native_watermark),
  int8send(p_generation),convert_to(p_created_transaction,'UTF8'),int8send(p_created_us),
  int8send(p_expires_us)]);
END $$;

REVOKE ALL ON FUNCTION app_private.native_v3_source_index_prefix(integer),
 app_private.native_v3_source_index_entry(uuid,uuid,uuid,bytea,bytea,uuid,uuid,bigint,bigint,
  text,uuid,uuid,uuid,bigint),
 app_private.native_v3_source_cursor(uuid,uuid,bigint,bytea,bigint,bigint,bigint,text,bigint,bigint)
 FROM PUBLIC;
"""
)

NATIVE_SOURCE_DESCRIPTORS_V3_DROP_SQL = r"""
DROP FUNCTION app_private.native_v3_source_cursor(uuid,uuid,bigint,bytea,bigint,bigint,bigint,
 text,bigint,bigint);
DROP FUNCTION app_private.native_v3_source_index_entry(uuid,uuid,uuid,bytea,bytea,uuid,uuid,
 bigint,bigint,text,uuid,uuid,uuid,bigint);
DROP FUNCTION app_private.native_v3_source_index_prefix(integer);
"""
