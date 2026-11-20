"""Original accepted-event APNI3 and immutable lineage CURSOR DATA readers.

These are not the batch membership cursor and do not discover selected P11s.
Full accepted-source/causal/current/kernel gates remain independently required.
Physical F/G query profiles and exact kernel ACL/cost review precede use.
"""

from hashlib import sha256
from types import MappingProxyType
from typing import Final

from autplay.adapters.postgresql.native_b_source_permission_schema_v3 import (
    _ACTOR,
    _FAULT,
    _FAULT_DECL,
)
from autplay.domain.native_source_descriptors_v3 import SOURCE_INDEX_ENTRY_DOMAIN_V3

_KERNEL: Final = "native_v3_b_kernel"
SOURCE3_INDEX_COLUMNS: Final = (
    ("original_request_id", "uuid"),
    ("owner_user_id", "uuid"),
    ("source_event_id", "uuid"),
    ("source_request_sha256", "bytea"),
    ("normalized_payload_sha256", "bytea"),
    ("device_id", "uuid"),
    ("journal_epoch", "uuid"),
    ("device_sequence", "bigint"),
    ("server_sequence", "bigint"),
    ("event_type", "text"),
    ("association_id", "uuid"),
    ("causal_request_id", "uuid"),
    ("impression_event_id", "uuid"),
    ("retained_until_us", "bigint"),
)
SOURCE3_ORIGINAL_CURSOR_COLUMNS: Final = (
    ("original_request_id", "uuid"),
    ("owner_user_id", "uuid"),
    ("source_event_count", "integer"),
    ("source_membership_sha256", "bytea"),
    ("cutoff_at_ms", "bigint"),
    ("native_interaction_watermark", "bigint"),
    ("source_generation", "bigint"),
    ("created_transaction", "text"),
    ("created_at_us", "bigint"),
    ("expires_at_us", "bigint"),
)
SOURCE3_INDEX_NATIVE_INPUT_BYTES: Final = MappingProxyType(
    {"INDEX_MEASURE": 16, "INDEX_NEXT": 248, "ORIGINAL_CURSOR": 132}
)
_STORE: Final = """
CREATE TABLE native_b_private_v3.source3_index_progress(
 singleton boolean PRIMARY KEY CHECK(singleton),window_id uuid NOT NULL,
 transaction_id xid8 NOT NULL,
 request_ordinal smallint NOT NULL CHECK(request_ordinal BETWEEN 1 AND 16),
 expected_count integer NOT NULL CHECK(expected_count BETWEEN 0 AND 10000),
 byte_length bigint NOT NULL CHECK(byte_length BETWEEN 10 AND 2097152),
 seen integer NOT NULL DEFAULT 0 CHECK(seen BETWEEN 0 AND expected_count),
 after_event uuid,finished boolean NOT NULL DEFAULT false,cursor_read boolean NOT NULL DEFAULT false
);
ALTER TABLE native_b_private_v3.source3_index_progress OWNER TO native_v3_b_catalog_owner;
REVOKE ALL ON native_b_private_v3.source3_index_progress
 FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer;
GRANT SELECT,INSERT,UPDATE,DELETE ON native_b_private_v3.source3_index_progress
 TO native_v3_b_kernel;
"""
_SPECS: list[tuple[str, str, str]] = []


def _define(name: str, result: str, declarations: str, target: str, step: str) -> str:
    signature = f"app_private.{name}(uuid)"
    body = (
        "DECLARE " + _FAULT_DECL + " permission native_b_private_v3.source3_permission%ROWTYPE;"
        " pin native_b_private_v3.source3_read_pin%ROWTYPE; binding "
        "native_b_private_v3.binding%ROWTYPE;"
        + declarations
        + " BEGIN\n"
        + _ACTOR
        + " permission:=app_private._native_b_source3_payload_scope_v3(p_window);\n"
        " v_fault_scope:=app_private._native_b_data_scope_v3(); "
        "binding:=app_private._native_b_current_v3();\n"
        " SELECT p.* INTO STRICT pin FROM native_b_private_v3.source3_read_pin p"
        " WHERE p.singleton AND p.window_id=p_window AND p.transaction_id=pg_current_xact_id()"
        " AND p.request_ordinal=binding.request_ordinal AND p.documents_mask=63 "
        "AND NOT p.root_consumed;\n"
        # Same RPC, existing exact SOURCE phase/reason3/caps/serial. Neither
        # caller nor F/G quote supplies the native scalar carrier width.
        + " PERFORM app_private.native_b_admit_input_v3("
        + str(SOURCE3_INDEX_NATIVE_INPUT_BYTES[step])
        + ");\n"
        + target
        + _FAULT
    )
    _SPECS.append((signature, result, body))
    return (
        f"CREATE FUNCTION app_private.{name}(p_window uuid) RETURNS {result}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog\n"
        f"AS $source3_index${body}$source3_index$;\n"
        f"ALTER FUNCTION {signature} OWNER TO {_KERNEL};\n"
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,native_v3_b_issuer;\n"
    )


_NULLS: Final = (
    "((e.association_id IS NULL)::integer+(e.causal_request_id IS NULL)::integer+"
    "(e.impression_event_id IS NULL)::integer)"
)
_MEASURE: Final = _define(
    "native_b_source3_index_measure_v3",
    "TABLE(event_count bigint,byte_length bigint)",
    " n bigint; measured_count bigint; measured_bytes bigint; valid boolean;",
    " n:=(pin.head).source_event_count;\n"
    " IF n IS NULL OR n NOT BETWEEN 0 AND 10000 OR EXISTS(SELECT 1 FROM "
    "native_b_private_v3.source3_index_progress) THEN"
    " RAISE EXCEPTION 'B_SOURCE3_INDEX_MEASURE_ONCE'; END IF;\n"
    " -- Two fixed scalar passes, count+1 sentinel. No full JSON/event payload.\n"
    " PERFORM app_private._native_b_debit_v3(32+2*96*(n+1),96*(n+1)+8,0,4::smallint);\n"
    " SELECT coalesce(bool_and(e.compression IS NULL AND e.physical_bytes "
    "BETWEEN 1 AND 64),true) INTO valid"
    " FROM(SELECT pg_column_compression(s.event_type) AS "
    "compression,pg_column_size(s.event_type) AS physical_bytes"
    " FROM ml.native_capture_source_event_v3 s WHERE s.owner_user_id=permission.owner_user_id"
    " AND s.original_request_id=permission.original_request_id ORDER BY "
    "s.source_event_id LIMIT n+1 FOR SHARE OF s)e;\n"
    " IF valid IS DISTINCT FROM true THEN RAISE EXCEPTION "
    "'B_SOURCE3_INDEX_EVENT_PHYSICAL'; END IF;\n"
    f" SELECT count(*)::bigint,10+coalesce(sum({len(SOURCE_INDEX_ENTRY_DOMAIN_V3)}+5"
    f"+5*(14-{_NULLS})+{_NULLS}"
    f"+5*16+2*32+3*8+octet_length(e.event_type)+(3-{_NULLS})*16),0)::bigint"
    " INTO measured_count,measured_bytes FROM(SELECT "
    "s.association_id,s.causal_request_id,s.impression_event_id,s.event_type"
    " FROM ml.native_capture_source_event_v3 s WHERE s.owner_user_id=permission.owner_user_id"
    " AND s.original_request_id=permission.original_request_id ORDER BY "
    "s.source_event_id LIMIT n+1 FOR SHARE OF s)e;\n"
    " IF measured_count<>n OR "
    "measured_bytes<>(pin.head).source_index_byte_length OR measured_bytes "
    "NOT BETWEEN 10 AND 2097152 THEN"
    " RAISE EXCEPTION 'B_SOURCE3_INDEX_MEASURED_COUNT_LENGTH'; END IF;\n"
    " INSERT INTO "
    "native_b_private_v3.source3_index_progress(singleton,window_id,transaction_id,request_ordinal,expected_count,byte_length)"
    " "
    "VALUES(true,p_window,pg_current_xact_id(),binding.request_ordinal,n::integer,measured_bytes);"
    "\n"
    " RETURN QUERY SELECT measured_count,measured_bytes;\n",
    "INDEX_MEASURE",
)
_NEXT: Final = _define(
    "native_b_source3_index_next_v3",
    "TABLE(" + ",".join(f"{name} {kind}" for name, kind in SOURCE3_INDEX_COLUMNS) + ")",
    " progress native_b_private_v3.source3_index_progress%ROWTYPE; next_event uuid;",
    " PERFORM app_private._native_b_debit_v3(32768,512,0,4::smallint);\n"
    " SELECT p.* INTO STRICT progress FROM "
    "native_b_private_v3.source3_index_progress p WHERE p.singleton FOR "
    "UPDATE;\n"
    " IF progress.window_id<>p_window OR progress.transaction_id<>pg_current_xact_id()"
    " OR progress.request_ordinal<>binding.request_ordinal OR "
    "progress.finished THEN RAISE EXCEPTION 'B_SOURCE3_INDEX_NEXT_SCOPE'; END"
    " IF;\n"
    " SELECT e.source_event_id INTO next_event FROM ml.native_capture_source_event_v3 e"
    " WHERE e.owner_user_id=permission.owner_user_id AND "
    "e.original_request_id=permission.original_request_id"
    " AND(progress.after_event IS NULL OR e.source_event_id>progress.after_event)"
    " ORDER BY e.source_event_id LIMIT 1 FOR SHARE OF e;\n"
    " IF NOT FOUND THEN\n"
    " IF progress.seen<>progress.expected_count THEN RAISE EXCEPTION "
    "'B_SOURCE3_INDEX_EARLY_EOF'; END IF;\n"
    " UPDATE native_b_private_v3.source3_index_progress SET finished=true "
    "WHERE singleton; RETURN; END IF;\n"
    " IF progress.seen>=progress.expected_count THEN RAISE EXCEPTION "
    "'B_SOURCE3_INDEX_EXTRA_ROW'; END IF;\n"
    " UPDATE native_b_private_v3.source3_index_progress SET "
    "seen=seen+1,after_event=next_event WHERE singleton;\n"
    " RETURN QUERY SELECT "
    "e.original_request_id,e.owner_user_id,e.source_event_id,e.source_request_sha256,"
    "e.normalized_payload_sha256,e.device_id,e.journal_epoch,e.device_sequence,e.server_sequence,e.event_type,"
    "e.association_id,e.causal_request_id,e.impression_event_id,(extract(epoch"
    " FROM e.retained_until)*1000000)::bigint"
    " FROM ml.native_capture_source_event_v3 e WHERE e.owner_user_id=permission.owner_user_id"
    " AND e.original_request_id=permission.original_request_id AND e.source_event_id=next_event;\n",
    "INDEX_NEXT",
)
_CURSOR: Final = _define(
    "native_b_source3_original_cursor_v3",
    "TABLE(" + ",".join(f"{name} {kind}" for name, kind in SOURCE3_ORIGINAL_CURSOR_COLUMNS) + ")",
    " progress native_b_private_v3.source3_index_progress%ROWTYPE;",
    " PERFORM app_private._native_b_debit_v3(32768,512,0,4::smallint);\n"
    " SELECT p.* INTO STRICT progress FROM "
    "native_b_private_v3.source3_index_progress p WHERE p.singleton FOR "
    "UPDATE;\n"
    " IF progress.window_id<>p_window OR "
    "progress.transaction_id<>pg_current_xact_id() OR "
    "progress.request_ordinal<>binding.request_ordinal"
    " OR NOT progress.finished OR progress.seen<>progress.expected_count OR "
    "progress.cursor_read THEN RAISE EXCEPTION "
    "'B_SOURCE3_ORIGINAL_CURSOR_ONCE'; END IF;\n"
    " UPDATE native_b_private_v3.source3_index_progress SET cursor_read=true WHERE singleton;\n"
    " RETURN QUERY SELECT "
    "k.original_request_id,k.owner_user_id,k.source_event_count,k.source_membership_sha256,"
    " "
    "k.cutoff_at_ms,k.native_interaction_watermark,k.source_generation,k.created_transaction::text,"
    " (extract(epoch FROM k.created_at)*1000000)::bigint,(extract(epoch FROM "
    "k.expires_at)*1000000)::bigint"
    " FROM ml.native_capture_lineage_cursor_v3 k WHERE k.owner_user_id=permission.owner_user_id"
    " AND k.original_request_id=permission.original_request_id AND k.state='ACTIVE'"
    " AND k.source_generation=permission.source_generation FOR SHARE OF k;\n",
    "ORIGINAL_CURSOR",
)

SOURCE3_INDEX_SQL_SIGNATURES: Final = tuple(s for s, _, _ in _SPECS)
SOURCE3_INDEX_BODY_DEFINITIONS: Final = MappingProxyType({s: b for s, _, b in _SPECS})
SOURCE3_INDEX_BODY_SHA256: Final = MappingProxyType(
    {s: sha256(b.encode()).hexdigest() for s, _, b in _SPECS}
)
SOURCE3_INDEX_RESULT_TYPES: Final = MappingProxyType({s: r for s, r, _ in _SPECS})
SOURCE3_INDEX_RESULT_SETS: Final = MappingProxyType({s: True for s, _, _ in _SPECS})
SOURCE3_INDEX_SQL_OWNERS: Final = MappingProxyType({s: _KERNEL for s, _, _ in _SPECS})
SOURCE3_INDEX_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_INDEX_SECURITY_DEFINER: Final = True
INSTALL_SQL: Final = _STORE + _MEASURE + _NEXT + _CURSOR
GRANTS_SQL_CANDIDATE: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {s} TO native_v3_b_runtime;"
        for s in SOURCE3_INDEX_SQL_SIGNATURES
    )
    + "\n"
)
REQUIRED_COLUMN_GRANTS_SQL: Final = (
    "GRANT "
    "SELECT(original_request_id,owner_user_id,source_event_id,source_request_sha256,normalized_payload_sha256,device_id,journal_epoch,device_sequence,server_sequence,event_type,association_id,causal_request_id,impression_event_id,retained_until),UPDATE(source_event_id)"
    " ON ml.native_capture_source_event_v3 TO native_v3_b_kernel;\n"
    "GRANT "
    "SELECT(original_request_id,owner_user_id,state,source_event_count,source_membership_sha256,cutoff_at_ms,native_interaction_watermark,source_generation,created_transaction,created_at,expires_at),UPDATE(original_request_id)"
    " ON ml.native_capture_lineage_cursor_v3 TO native_v3_b_kernel;\n"
)
