"""Inert, closed Source3 fact SQL transport; explicit coherent install only.

C's actual paid RR source check must already have validated the complete graph
and independent ledgers before the authentic reader/HEAD is issued. These
functions do not repeat or impersonate that authority. Every call additionally
requires the original payload permission and the protected six-document pin.
Only one bounded actual row is returned; caller UUIDs cannot select an owner.
Driver/compiler costs and all transitive C work remain original G/F precharges.
"""

from __future__ import annotations

from hashlib import sha256
from types import MappingProxyType
from typing import Final

from autplay.adapters.postgresql.native_b_source_permission_schema_v3 import (
    _ACTOR,
    _FAULT,
    _FAULT_DECL,
)

_OWNER: Final = "native_v3_b_catalog_owner"
_KERNEL: Final = "native_v3_b_kernel"
_RUNTIME: Final = "native_v3_b_runtime"
_HEAD: Final = "native_b_private_v3.source3_head"
_PIN: Final = "native_b_private_v3.source3_read_pin"
_ROW_TYPE: Final = "native_b_private_v3.source3_fact_row"

SOURCE3_FACT_STEPS: Final = ("FACTS_BEGIN", "FACTS_NEXT", "FACTS_AUTHORITY_NEXT")
SOURCE3_FACT_COLUMNS: Final = (
    ("index_original", "uuid"),
    ("index_owner", "uuid"),
    ("index_event", "uuid"),
    ("index_request_sha", "bytea"),
    ("index_normalized_sha", "bytea"),
    ("index_device", "uuid"),
    ("index_epoch", "uuid"),
    ("index_device_sequence", "bigint"),
    ("index_server_sequence", "bigint"),
    ("index_event_type", "text"),
    ("index_association", "uuid"),
    ("index_request", "uuid"),
    ("index_impression", "uuid"),
    ("index_retained_us", "bigint"),
    ("event_type", "text"),
    ("payload", "text"),
    ("server_sequence", "bigint"),
    ("device_id", "uuid"),
    ("journal_epoch", "uuid"),
    ("device_sequence", "bigint"),
    ("occurred_at", "timestamp with time zone"),
    ("received_at", "timestamp with time zone"),
    ("request_hash", "bytea"),
    ("normalized_payload_sha256", "bytea"),
    ("recording_id", "uuid"),
    ("event_origin", "text"),
    ("played_ms", "bigint"),
    ("completion_ratio", "double precision"),
    ("excluded_from_taste", "boolean"),
    ("explicit_feedback", "text"),
    ("attribution_contract_version", "integer"),
    ("attribution_closed", "boolean"),
    ("feedback", "text"),
    ("a_id", "uuid"),
    ("a_kind", "text"),
    ("a_recording", "uuid"),
    ("a_expires", "timestamp with time zone"),
    ("a_sha", "bytea"),
    ("a_request", "uuid"),
    ("a_rank", "bigint"),
    ("a_decision", "bytea"),
    ("a_item", "bytea"),
    ("a_provenance", "text"),
    ("a_surface", "text"),
    ("a_presentation", "uuid"),
    ("a_source", "text"),
    ("a_presentation_surface", "text"),
    ("a_parent", "uuid"),
    ("a_parent_event", "uuid"),
)
SOURCE3_AUTHORITY_COLUMNS: Final = (
    "request_id",
    "serving_contract_version",
    "hash_format_version",
    "authority_document_sha256",
    "decision_sha256",
    "served_items_sha256",
    "pipeline_manifest_sha256",
)
SOURCE3_FACT_REQUIRED_SQL: Final = (
    "app_private._native_b_source3_payload_scope_v3(uuid)",
    "app_private._native_b_data_scope_v3()",
    "app_private._native_b_current_v3()",
    "app_private._native_b_debit_v3(bigint,bigint,bigint,smallint)",
    "app_private._native_b_poison_v3(native_b_private_v3.slot,oid,integer,xid8)",
    "app_private.native_v3_compiler_enter(bigint)",
    "app_private.native_v3_compiler_exit(integer)",
    "app_private.native_v3_assert_flat_scalar_resource(jsonb)",
    "app_private.validate_normalized_sync_payload_v2(text,jsonb)",
    "app_private.sync_payload_sha256_pg_v1(jsonb)",
    "app_private.assert_native_atomic_current_rr_v3(uuid,uuid,bigint)",
    "app_private.assert_native_atomic_graph_v3(uuid)",
    "app_private.assert_normalized_listen_acceptance(uuid)",
)
SOURCE3_FACT_QUALIFICATION_HOLDS: Final = (
    "Actual installed0087 immutable guards, fixed ACK constructors and C graph/body census",
    "Actual original C current/FS/policy source fence before reader HEAD; "
    "no scalar flag substitutes",
    "G before-effect read profile covering SQL/compiler/libpq/RX/driver/Python aliases "
    "on SAME B ledger",
    "Exact kernel fixed-column privileges and runtime EXECUTE-only census",
    "Actual PostgreSQL keyset/EOF, hostile Numeric, failure and terminal cleanup proof",
)

# The flat gate's1MiB child is additional to this actual retained parent V.
SOURCE3_FACT_MAX_PARENT_COMPILER: Final = 8 * (8192 + 4092 + 8188 + 12 + 8192 + 64) + 65536
# Local resource/normalization work only. C's complete prior graph/validators,
# physical plan/page IO and transport are separately mandatory G/F admission.
SOURCE3_FACT_ROW_BYTE_WORK: Final = (
    32768 + 3 * (128 * 8192 + 65536) + 8 * (8192 + 4092 + 8188 + 12 + 8192 + 64) + 65536 + 16 * 8192
)
SOURCE3_FACT_ROW_NODE_VISITS: Final = 4096 + 3 * (32 * (8192 + 1) + 1024) + 16 * 8192

_COMMON: Final = r"""
 permission:=app_private._native_b_source3_payload_scope_v3(p_window);
 v_fault_scope:=app_private._native_b_data_scope_v3();
 SELECT p.* INTO STRICT pin FROM native_b_private_v3.source3_read_pin AS p
 WHERE p.singleton AND p.window_id=permission.window_id
  AND p.transaction_id=pg_current_xact_id() FOR UPDATE;
 binding:=app_private._native_b_current_v3();
 actual_head:=pin.head;
 IF pin.request_ordinal IS DISTINCT FROM binding.request_ordinal
  OR pin.documents_mask<>63 OR pin.root_consumed
  OR actual_head.owner_user_id IS DISTINCT FROM permission.owner_user_id
  OR actual_head.original_request_id IS DISTINCT FROM permission.original_request_id
  OR actual_head.served_request_id IS DISTINCT FROM permission.served_request_id
  OR actual_head.source_generation IS DISTINCT FROM permission.source_generation THEN
  RAISE EXCEPTION 'B_SOURCE3_FACT_PIN' USING ERRCODE='23514'; END IF;
"""
_SPECS: list[tuple[str, str]] = []
_BODIES: dict[str, str] = {}


def _define(name: str, args: str, types: str, returns: str, declaration: str, body: str) -> str:
    signature = f"app_private.{name}({types})"
    full = (
        "\nDECLARE "
        + _FAULT_DECL
        + " permission native_b_private_v3.source3_permission%ROWTYPE;"
        + f" pin {_PIN}%ROWTYPE; actual_head {_HEAD};"
        + " binding native_b_private_v3.binding%ROWTYPE;\n"
        + declaration
        + "\nBEGIN\n"
        + _ACTOR
        + _COMMON
        + body
        + _FAULT
    )
    _SPECS.append((signature, returns))
    _BODIES[signature] = full
    return (
        f"CREATE FUNCTION app_private.{name}({args}) RETURNS {returns}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog "
        "AS $source3_facts$\n" + full + "\n$source3_facts$;\n"
        f"ALTER FUNCTION {signature} OWNER TO {_KERNEL};\n"
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,native_v3_b_issuer;\n"
    )


_BEGIN_SQL: Final = _define(
    "native_b_source3_facts_begin_v3",
    "p_window uuid",
    "uuid",
    "TABLE(source_count bigint,causal_count bigint,authority_count bigint)",
    " n bigint; c bigint; a bigint; depth integer;",
    r"""
 -- Prior actual C source admission verified the complete graph and0087
 -- immutable guards. This is a bounded independent metadata re-read, not a
 -- made-up fixed price for another full graph assertion.
 PERFORM app_private._native_b_debit_v3(
  65536+128*(actual_head.source_event_count+1),
  4096+64*(actual_head.source_event_count+1),0,4::smallint);
 depth:=app_private.native_v3_compiler_enter(65536+128*(actual_head.source_event_count+1));
 SELECT count(*),count(*) FILTER(WHERE q.association_id IS NOT NULL) INTO n,c
 FROM (SELECT e.association_id FROM ml.native_capture_source_event_v3 AS e
  WHERE e.owner_user_id=actual_head.owner_user_id
   AND e.original_request_id=actual_head.original_request_id
  ORDER BY e.source_event_id LIMIT 10001 FOR SHARE OF e) AS q;
 SELECT count(*) INTO a FROM (
  SELECT DISTINCT e.causal_request_id FROM ml.native_capture_source_event_v3 AS e
  WHERE e.owner_user_id=actual_head.owner_user_id
   AND e.original_request_id=actual_head.original_request_id
   AND e.causal_request_id IS NOT NULL ORDER BY e.causal_request_id LIMIT 65) AS q;
 IF n<>actual_head.source_event_count OR n>10000 OR c>n OR a>64 THEN
  RAISE EXCEPTION 'B_SOURCE3_FACT_COUNTS' USING ERRCODE='23514'; END IF;
 PERFORM app_private.native_v3_compiler_exit(depth);
 RETURN QUERY SELECT n,c,a;
""",
)

_NEXT_SQL: Final = _define(
    "native_b_source3_fact_next_v3",
    "p_window uuid,p_after uuid",
    "uuid,uuid",
    f"SETOF {_ROW_TYPE}",
    " e ml.native_capture_source_event_v3%ROWTYPE; meta record;"
    " source_payload jsonb; ack jsonb; target_payload jsonb;"
    f" answer {_ROW_TYPE}; depth integer; v_depth integer;"
    " v_bytes bigint; v_work bigint; v_nodes bigint;",
    " PERFORM app_private._native_b_debit_v3(32768,4096,0,4::smallint);\n"
    + r"""
 depth:=app_private.native_v3_compiler_enter(131072);
 SELECT x.original_request_id,x.owner_user_id,x.source_event_id,x.source_request_sha256,
  x.normalized_payload_sha256,x.device_id,x.journal_epoch,x.device_sequence,x.server_sequence,
  x.event_type,x.association_id,x.causal_request_id,x.impression_event_id,x.retained_until
 INTO e FROM ml.native_capture_source_event_v3 AS x
 WHERE x.owner_user_id=actual_head.owner_user_id
  AND x.original_request_id=actual_head.original_request_id
  AND (p_after IS NULL OR x.source_event_id>p_after)
 ORDER BY x.source_event_id LIMIT 1 FOR SHARE OF x;
 IF NOT FOUND THEN
  e:=NULL; PERFORM app_private.native_v3_compiler_exit(depth); RETURN;
 END IF;
 -- Metadata first: never text-expand or hydrate JSONB in this query.
 SELECT pg_column_compression(s.payload) AS pc,pg_column_size(s.payload) AS ps,
  pg_column_compression(i.terminal_ack) AS ac,pg_column_size(i.terminal_ack) AS az,
  pg_column_compression(i.payload) AS ic,pg_column_size(i.payload) AS iz,
  pg_column_compression(t.payload) AS tc,pg_column_size(t.payload) AS tz,
  coalesce(octet_length(s.event_type),0)::bigint+coalesce(octet_length(s.aggregate_type),0)
   +coalesce(octet_length(s.operation),0)+coalesce(octet_length(i.event_type),0)
   +coalesce(octet_length(i.aggregate_type),0)+coalesce(octet_length(i.idempotency_key),0)
   +coalesce(octet_length(i.error_code),0)+coalesce(octet_length(i.apply_status),0)
   +coalesce(octet_length(t.event_type),0)+coalesce(octet_length(l.event_origin),0)
   +coalesce(octet_length(l.explicit_feedback),0) AS scalars,
  octet_length(s.normalized_payload_sha256) AS sh,
  octet_length(i.normalized_payload_sha256) AS ih,octet_length(i.request_hash) AS rh
 INTO meta FROM sync.sync_event AS s JOIN sync.device_event_inbox AS i USING(event_id)
 LEFT JOIN library.user_interaction_event AS t ON t.interaction_id=s.event_id
  AND t.user_id=actual_head.owner_user_id
 LEFT JOIN library.listening_event AS l ON l.listening_event_id=s.event_id
  AND l.user_id=actual_head.owner_user_id
 WHERE s.event_id=e.source_event_id AND s.user_id=actual_head.owner_user_id
  AND i.user_id=actual_head.owner_user_id AND i.apply_status='APPLIED'
  AND s.ingress_validation_version=2 AND i.ingress_validation_version=2;
 IF NOT FOUND OR meta.pc IS NOT NULL OR meta.ac IS NOT NULL OR meta.tc IS NOT NULL
  OR meta.ic IS NOT NULL OR meta.ps IS NULL OR meta.ps NOT BETWEEN 1 AND 8192
  OR meta.az IS NULL OR meta.az NOT BETWEEN 1 AND 4092
  OR (meta.tz IS NOT NULL AND meta.tz NOT BETWEEN 1 AND 8188)
  OR meta.iz IS NULL OR meta.iz NOT BETWEEN 1 AND 64 OR meta.scalars>8192
  OR meta.sh IS DISTINCT FROM 32 OR meta.ih IS DISTINCT FROM 32
  OR meta.rh IS DISTINCT FROM 32 THEN
  RAISE EXCEPTION 'B_SOURCE3_FACT_PAYLOAD_BOUND' USING ERRCODE='23514'; END IF;
 v_bytes:=8*(meta.ps::bigint+meta.az+coalesce(meta.tz,0)+12+8192+64)+65536;
 -- Derive the already-proven flat resource/normalization cost from actual
 -- physical sizes. EOF never prepays payload operations it cannot perform.
 v_work:=128*(meta.ps::bigint+meta.az+coalesce(meta.tz,0))
  +65536*(2+CASE WHEN meta.tz IS NULL THEN 0 ELSE 1 END)+v_bytes+16*8192;
 v_nodes:=32*(meta.ps::bigint+meta.az+coalesce(meta.tz,0))
  +1056*(2+CASE WHEN meta.tz IS NULL THEN 0 ELSE 1 END)+16*8192;
 PERFORM app_private._native_b_debit_v3(v_work,v_nodes,0,4::smallint);
 v_depth:=app_private.native_v3_compiler_enter(v_bytes);
 SELECT s.payload,i.terminal_ack,t.payload INTO source_payload,ack,target_payload
 FROM sync.sync_event AS s JOIN sync.device_event_inbox AS i USING(event_id)
 LEFT JOIN library.user_interaction_event AS t ON t.interaction_id=s.event_id
  AND t.user_id=actual_head.owner_user_id
 WHERE s.event_id=e.source_event_id AND s.user_id=actual_head.owner_user_id
  AND i.user_id=actual_head.owner_user_id AND i.apply_status='APPLIED';
 PERFORM app_private.native_v3_assert_flat_scalar_resource(source_payload);
 PERFORM app_private.native_v3_assert_flat_scalar_resource(ack);
 IF target_payload IS NOT NULL THEN
  PERFORM app_private.native_v3_assert_flat_scalar_resource(target_payload);
 END IF;
 PERFORM app_private.validate_normalized_sync_payload_v2(e.event_type,source_payload);
 IF app_private.sync_payload_sha256_pg_v1(source_payload)
  IS DISTINCT FROM e.normalized_payload_sha256 THEN
  RAISE EXCEPTION 'B_SOURCE3_FACT_NORMALIZED_HASH' USING ERRCODE='23514'; END IF;
 SELECT e.original_request_id,e.owner_user_id,e.source_event_id,e.source_request_sha256,
  e.normalized_payload_sha256,e.device_id,e.journal_epoch,e.device_sequence,e.server_sequence,
  e.event_type,e.association_id,e.causal_request_id,e.impression_event_id,
  (extract(epoch FROM e.retained_until)*1000000)::bigint,
  s.event_type,source_payload::text,s.server_sequence,i.device_id,i.journal_epoch,
  i.device_sequence,i.occurred_at,i.received_at,i.request_hash,i.normalized_payload_sha256,
  CASE WHEN s.event_type='LISTENING_EVENT_RECORDED' THEN l.recording_id ELSE t.recording_id END,
  l.event_origin,l.played_ms,l.completion_ratio::double precision,
  l.excluded_from_taste,l.explicit_feedback,
  coalesce(l.attribution_contract_version,t.attribution_contract_version),
  coalesce(l.attribution_closed,t.attribution_closed),target_payload->>'feedback_type',
  a.association_id,a.kind,a.recording_id,a.attribution_expires_at,a.association_sha256,
  a.recommendation_request_id,a.served_rank,a.decision_sha256,a.served_item_sha256,
  a.serving_provenance,a.request_surface,a.presentation_id,a.presentation_source,
  a.presentation_surface,a.impression_association_id,parent.interaction_id
 INTO STRICT answer FROM sync.sync_event AS s JOIN sync.device_event_inbox AS i USING(event_id)
 LEFT JOIN library.listening_event AS l ON l.listening_event_id=s.event_id
  AND l.user_id=actual_head.owner_user_id
 LEFT JOIN library.user_interaction_event AS t ON t.interaction_id=s.event_id
  AND t.user_id=actual_head.owner_user_id
 LEFT JOIN ml.recommendation_attribution_association AS a
  ON a.owner_user_id=actual_head.owner_user_id
  AND (a.interaction_id=s.event_id OR a.listening_event_id=s.event_id)
 LEFT JOIN ml.recommendation_attribution_association AS parent
  ON parent.association_id=a.impression_association_id AND parent.owner_user_id=a.owner_user_id
 WHERE s.event_id=e.source_event_id AND s.user_id=actual_head.owner_user_id
  AND i.user_id=actual_head.owner_user_id AND i.apply_status='APPLIED' AND i.error_code IS NULL
  AND s.ingress_validation_version=2 AND i.ingress_validation_version=2
  AND s.operation='UPSERT' AND s.server_row_version=1 AND s.schema_version=1 AND i.schema_version=1
  AND s.origin_device_id=i.device_id AND s.event_type=i.event_type AND s.event_type=e.event_type
  AND s.aggregate_type=i.aggregate_type AND s.aggregate_id=i.aggregate_id
  AND s.aggregate_id=s.event_id
  AND s.ingress_journal_epoch=i.journal_epoch AND s.ingress_device_sequence=i.device_sequence
  AND s.normalized_payload_sha256=i.normalized_payload_sha256 AND i.payload='{}'::jsonb
  AND ((s.event_type='LISTENING_EVENT_RECORDED' AND l.listening_event_id IS NOT NULL
    AND NOT l.attribution_closed)
   OR (s.event_type IN ('RECOMMENDATION_IMPRESSION_RECORDED','RECOMMENDATION_FEEDBACK_RECORDED')
    AND t.interaction_id IS NOT NULL AND t.attribution_contract_version=2
    AND NOT t.attribution_closed))
  AND NOT EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt AS er
   WHERE er.user_id=actual_head.owner_user_id AND er.source_event_id=s.event_id);
 IF NOT FOUND OR answer.payload IS NULL OR octet_length(answer.payload)>8192 THEN
  RAISE EXCEPTION 'B_SOURCE3_FACT_ACCEPTED' USING ERRCODE='23514'; END IF;
 source_payload:=NULL; ack:=NULL; target_payload:=NULL; meta:=NULL;
 PERFORM app_private.native_v3_compiler_exit(v_depth);
 -- Caller G transport owns the bounded returned-row slot before invocation.
 -- RETURN NEXT copies it; local row is dropped before local compiler exit.
 RETURN NEXT answer;
 answer:=NULL; e:=NULL;
 PERFORM app_private.native_v3_compiler_exit(depth);
 RETURN;
""",
)

_AUTHORITY_SQL: Final = _define(
    "native_b_source3_fact_authority_next_v3",
    "p_window uuid,p_after uuid",
    "uuid,uuid",
    "TABLE(request_id uuid,serving_contract_version bigint,hash_format_version bigint,"
    "authority_document_sha256 bytea,decision_sha256 bytea,served_items_sha256 bytea,"
    "pipeline_manifest_sha256 bytea)",
    " selected_id uuid; answer record; depth integer;",
    r"""
 PERFORM app_private._native_b_debit_v3(
  65536+128*(actual_head.source_event_count+1),4096+64*(actual_head.source_event_count+1),0,4::smallint);
 depth:=app_private.native_v3_compiler_enter(65536+128*(actual_head.source_event_count+1));
 SELECT e.causal_request_id INTO selected_id FROM ml.native_capture_source_event_v3 AS e
 WHERE e.owner_user_id=actual_head.owner_user_id
  AND e.original_request_id=actual_head.original_request_id AND e.causal_request_id IS NOT NULL
  AND (p_after IS NULL OR e.causal_request_id>p_after)
 ORDER BY e.causal_request_id LIMIT 1;
 IF NOT FOUND THEN
  PERFORM app_private.native_v3_compiler_exit(depth); RETURN;
 END IF;
 SELECT q.recommendation_request_id AS id,q.serving_contract_version::bigint AS version,
  f.request_hash_format_version::bigint AS format,
  CASE f.request_hash_format_version WHEN 2 THEN replay.document_sha256
   WHEN 3 THEN root.root_proof_sha256 END AS document_sha,
  d.decision_sha256 AS decision_sha,d.served_items_sha256 AS items_sha,
  q.pipeline_manifest_sha256 AS pipeline_sha INTO answer
 FROM ml.recommendation_request AS q
 JOIN ml.native_served_request_hash_format AS f ON f.served_request_id=q.recommendation_request_id
  AND f.owner_user_id=q.user_id
 JOIN ml.recommendation_serving_decision AS d USING(recommendation_request_id)
 LEFT JOIN ml.recommendation_request_replay_payload AS replay
  ON replay.recommendation_request_id=q.recommendation_request_id AND replay.owner_user_id=q.user_id
 LEFT JOIN ml.recommendation_request_root_v3 AS root
  ON root.served_request_id=q.recommendation_request_id AND root.owner_user_id=q.user_id
 WHERE q.recommendation_request_id=selected_id AND q.user_id=actual_head.owner_user_id
  AND q.recommendation_request_id<>actual_head.original_request_id
  AND q.recommendation_request_id<>actual_head.served_request_id
  AND q.serving_contract_version=2 AND f.request_hash_format_version IN (2,3)
  AND d.state='COMMITTED_P11' AND d.serving_provenance='P11'
  AND d.inference_evidence_sha256 IS NULL AND d.fallback_reason IS NULL
  AND d.served_truth_expires_at>clock_timestamp()
  AND ((f.request_hash_format_version=2 AND replay.replay_expires_at>clock_timestamp())
   OR (f.request_hash_format_version=3 AND root.expires_at>clock_timestamp()));
 IF NOT FOUND OR octet_length(answer.document_sha) IS DISTINCT FROM 32
  OR octet_length(answer.decision_sha) IS DISTINCT FROM 32
  OR octet_length(answer.items_sha) IS DISTINCT FROM 32
  OR octet_length(answer.pipeline_sha) IS DISTINCT FROM 32 THEN
  RAISE EXCEPTION 'B_SOURCE3_FACT_AUTHORITY' USING ERRCODE='23514'; END IF;
 RETURN QUERY SELECT answer.id,answer.version,answer.format,answer.document_sha,
  answer.decision_sha,answer.items_sha,answer.pipeline_sha;
 answer:=NULL;
 PERFORM app_private.native_v3_compiler_exit(depth);
 RETURN;
""",
)

SOURCE3_FACT_SIGNATURES: Final = tuple(item[0] for item in _SPECS)
SOURCE3_FACT_FUNCTION_BODIES: Final = MappingProxyType(dict(_BODIES))
SOURCE3_FACT_BODY_SHA256: Final = MappingProxyType(
    {name: sha256(body.encode()).hexdigest() for name, body in _BODIES.items()}
)
SOURCE3_FACT_OWNERS: Final = MappingProxyType({name: _KERNEL for name in SOURCE3_FACT_SIGNATURES})
SOURCE3_FACT_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_FACT_COLUMN_REQUIREMENTS: Final = MappingProxyType(
    {
        "ml.native_capture_source_event_v3": (
            "original_request_id",
            "owner_user_id",
            "source_event_id",
            "source_request_sha256",
            "normalized_payload_sha256",
            "device_id",
            "journal_epoch",
            "device_sequence",
            "server_sequence",
            "event_type",
            "association_id",
            "causal_request_id",
            "impression_event_id",
            "retained_until",
        ),
        "sync.sync_event": (
            "event_id",
            "user_id",
            "event_type",
            "payload",
            "server_sequence",
            "operation",
            "server_row_version",
            "schema_version",
            "origin_device_id",
            "aggregate_type",
            "aggregate_id",
            "ingress_validation_version",
            "ingress_journal_epoch",
            "ingress_device_sequence",
            "normalized_payload_sha256",
        ),
        "sync.device_event_inbox": (
            "event_id",
            "user_id",
            "device_id",
            "journal_epoch",
            "device_sequence",
            "occurred_at",
            "received_at",
            "request_hash",
            "normalized_payload_sha256",
            "event_type",
            "aggregate_type",
            "aggregate_id",
            "apply_status",
            "error_code",
            "payload",
            "terminal_ack",
            "ingress_validation_version",
            "schema_version",
            "idempotency_key",
        ),
        "library.listening_event": (
            "listening_event_id",
            "user_id",
            "recording_id",
            "event_origin",
            "played_ms",
            "completion_ratio",
            "excluded_from_taste",
            "explicit_feedback",
            "attribution_contract_version",
            "attribution_closed",
        ),
        "library.user_interaction_event": (
            "interaction_id",
            "user_id",
            "recording_id",
            "event_type",
            "payload",
            "attribution_contract_version",
            "attribution_closed",
        ),
        "ml.recommendation_attribution_association": (
            "association_id",
            "owner_user_id",
            "kind",
            "recording_id",
            "attribution_expires_at",
            "association_sha256",
            "recommendation_request_id",
            "served_rank",
            "decision_sha256",
            "served_item_sha256",
            "serving_provenance",
            "request_surface",
            "presentation_id",
            "presentation_source",
            "presentation_surface",
            "impression_association_id",
            "interaction_id",
            "listening_event_id",
        ),
        "ml.sona_event_erasure_receipt": ("user_id", "source_event_id"),
        "ml.recommendation_request": (
            "recommendation_request_id",
            "user_id",
            "serving_contract_version",
            "pipeline_manifest_sha256",
        ),
        "ml.native_served_request_hash_format": (
            "served_request_id",
            "owner_user_id",
            "request_hash_format_version",
        ),
        "ml.recommendation_serving_decision": (
            "recommendation_request_id",
            "state",
            "serving_provenance",
            "decision_sha256",
            "served_items_sha256",
            "inference_evidence_sha256",
            "fallback_reason",
            "served_truth_expires_at",
        ),
        "ml.recommendation_request_replay_payload": (
            "recommendation_request_id",
            "owner_user_id",
            "document_sha256",
            "replay_expires_at",
        ),
        "ml.recommendation_request_root_v3": (
            "served_request_id",
            "owner_user_id",
            "root_proof_sha256",
            "expires_at",
        ),
    }
)
# Separate exact privilege proposal; not silently folded into INSTALL_SQL.
# No LOGIN receives table SELECT; even kernel gets only listed columns.
COLUMN_GRANTS_SQL: Final = (
    "\n".join(
        f"GRANT SELECT ({','.join(columns)}) ON {table} TO {_KERNEL};"
        for table, columns in SOURCE3_FACT_COLUMN_REQUIREMENTS.items()
    )
    + "\n"
)
DEPENDENCY_GRANTS_SQL: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_KERNEL};"
        for signature in SOURCE3_FACT_REQUIRED_SQL
        if signature
        not in (
            "app_private.assert_native_atomic_current_rr_v3(uuid,uuid,bigint)",
            "app_private.assert_native_atomic_graph_v3(uuid)",
            "app_private.assert_normalized_listen_acceptance(uuid)",
        )
    )
    + "\n"
)
GRANTS_SQL: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {name} TO {_RUNTIME};" for name in SOURCE3_FACT_SIGNATURES
    )
    + "\n"
)
INSTALL_SQL: Final = (
    "DO $source3_fact_required$ BEGIN\n"
    + "\n".join(
        f" IF to_regprocedure('{name}') IS NULL THEN RAISE EXCEPTION "
        "'B_SOURCE3_FACT_PREREQUISITE_REQUIRED' USING ERRCODE='55000'; END IF;"
        for name in SOURCE3_FACT_REQUIRED_SQL
    )
    + "\n IF to_regclass('native_b_private_v3.source3_read_pin') IS NULL THEN "
    "RAISE EXCEPTION 'B_SOURCE3_FACT_PIN_REQUIRED' USING ERRCODE='55000'; END IF;\n"
    "END $source3_fact_required$;\n"
    + f"CREATE TYPE {_ROW_TYPE} AS ("
    + ",".join(f"{name} {kind}" for name, kind in SOURCE3_FACT_COLUMNS)
    + ");\n"
    + f"ALTER TYPE {_ROW_TYPE} OWNER TO {_OWNER};\n"
    + f"REVOKE ALL ON TYPE {_ROW_TYPE} FROM PUBLIC,native_v3_b_issuer;\n"
    + f"GRANT USAGE ON TYPE {_ROW_TYPE} TO {_KERNEL},{_RUNTIME};\n"
    + _BEGIN_SQL
    + _NEXT_SQL
    + _AUTHORITY_SQL
    + GRANTS_SQL
)
DROP_SQL: Final = (
    "\n".join(f"DROP FUNCTION {name};" for name in reversed(SOURCE3_FACT_SIGNATURES))
    + f"\nDROP TYPE {_ROW_TYPE};\n"
)
