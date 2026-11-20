"""Closed mature outcome SQL candidate; cost/column review is not complete.

This family is deliberately absent from C's qualified runtime census until its
full nested validator cost derivation, column grants and compiler peak pass an
independent review. No SQL is installed by importing these source constants.
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

_KERNEL: Final = "native_v3_b_kernel"
SOURCE3_OUTCOME_VALIDATION_COST_STATE: Final = "CLOSED_TRANSITIVE_COST_GATE_REQUIRED_DO_NOT_INSTALL"
_SPECS: list[tuple[str, str]] = []
_BODIES: dict[str, str] = {}
_SCOPE: Final = """
 permission:=app_private._native_b_source3_payload_scope_v3(p_window);
 v_fault_scope:=app_private._native_b_data_scope_v3();
 SELECT p.* INTO STRICT pin FROM native_b_private_v3.source3_read_pin p
 WHERE p.singleton AND p.window_id=permission.window_id
  AND p.transaction_id=pg_current_xact_id() AND p.documents_mask=63 AND NOT p.root_consumed;
 binding:=app_private._native_b_current_v3(); actual_head:=pin.head;
 IF binding.request_ordinal<>pin.request_ordinal THEN
  RAISE EXCEPTION 'B_SOURCE3_OUTCOME_READ_PIN'; END IF;
"""


def _define(name: str, args: str, types: str, result: str, declarations: str, target: str) -> str:
    signature = f"app_private.{name}({types})"
    body = (
        "\nDECLARE " + _FAULT_DECL + " permission native_b_private_v3.source3_permission%ROWTYPE;"
        " pin native_b_private_v3.source3_read_pin%ROWTYPE;"
        " actual_head native_b_private_v3.source3_head;"
        " binding native_b_private_v3.binding%ROWTYPE;\n"
        + declarations
        + "\nBEGIN\n"
        + _ACTOR
        + _SCOPE
        + target
        + _FAULT.replace(
            "PERFORM app_private._native_b_poison_v3("
            "v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid);",
            "IF v_fault_scope IS NOT NULL THEN PERFORM app_private._native_b_poison_v3("
            "v_fault_scope,v_fault_role,v_fault_pid,v_fault_xid); END IF;",
        )
    )
    _SPECS.append((signature, result))
    _BODIES[signature] = body
    return (
        f"CREATE FUNCTION app_private.{name}({args}) RETURNS {result}\n"
        "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS"
        " $source3_outcome$\n"
        + body
        + "\n$source3_outcome$;\n"
        + f"ALTER FUNCTION {signature} OWNER TO {_KERNEL};\n"
        + f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,native_v3_b_issuer;\n"
    )


_BEGIN: Final = _define(
    "native_b_source3_outcomes_begin_v3",
    "p_window uuid",
    "uuid",
    "TABLE(current_at_us bigint,retained_until_us bigint,total_count bigint)",
    " v_count bigint; v_now bigint; v_retained bigint;",
    """
 PERFORM app_private._native_b_debit_v3(65536+256*5001,4096+5001,0,4::smallint);
 SELECT count(*) INTO v_count FROM (
  SELECT a.association_id FROM ml.recommendation_attribution_association a
  WHERE a.owner_user_id=permission.owner_user_id
   AND a.recommendation_request_id=permission.served_request_id
   AND a.kind IN('FEEDBACK','LISTEN')
  ORDER BY coalesce(a.interaction_id,a.listening_event_id) LIMIT 5001 FOR SHARE
 ) bounded;
 IF v_count>5000 THEN RAISE EXCEPTION 'B_SOURCE3_OUTCOME_COUNT'; END IF;
 v_now:=(extract(epoch FROM clock_timestamp())*1000000)::bigint;
 v_retained:=least(actual_head.original_expires_at_us,actual_head.replay_expires_at_us,
  actual_head.served_truth_expires_at_us,
  (extract(epoch FROM permission.expires_at)*1000000)::bigint);
 IF v_now<actual_head.served_created_at_us+604800000000 OR v_now>=v_retained THEN
  RAISE EXCEPTION 'B_SOURCE3_OUTCOME_MATURITY_OR_RETENTION'; END IF;
 RETURN QUERY SELECT v_now,v_retained,v_count;
""",
)
_NEXT: Final = _define(
    "native_b_source3_outcome_next_v3",
    "p_window uuid,p_after uuid",
    "uuid,uuid",
    "TABLE(child_event uuid,parent_event uuid,outcome_kind text)",
    " selected record;",
    """
 PERFORM app_private._native_b_debit_v3(32768,4096,0,4::smallint);
 PERFORM set_config('TimeZone','UTC',true);
 SELECT coalesce(a.interaction_id,a.listening_event_id) AS child_event,
  p.interaction_id AS parent_event,a.kind AS outcome_kind,p.kind AS parent_kind,
  p.owner_user_id AS parent_owner,p.recommendation_request_id AS parent_request
 INTO selected FROM ml.recommendation_attribution_association a
 LEFT JOIN ml.recommendation_attribution_association p
  ON p.association_id=a.impression_association_id
 WHERE a.owner_user_id=permission.owner_user_id
  AND a.recommendation_request_id=permission.served_request_id
  AND a.kind IN('FEEDBACK','LISTEN')
  AND (p_after IS NULL OR coalesce(a.interaction_id,a.listening_event_id)>p_after)
 ORDER BY coalesce(a.interaction_id,a.listening_event_id) LIMIT 1 FOR SHARE OF a;
 IF NOT FOUND THEN RETURN; END IF;
 IF selected.child_event IS NULL OR selected.parent_event IS NULL
  OR selected.parent_kind IS DISTINCT FROM 'IMPRESSION'
  OR selected.parent_owner IS DISTINCT FROM permission.owner_user_id
  OR selected.parent_request IS DISTINCT FROM permission.served_request_id THEN
  RAISE EXCEPTION 'B_SOURCE3_OUTCOME_PARENT'; END IF;
 RETURN QUERY SELECT selected.child_event,selected.parent_event,selected.outcome_kind;
""",
)

_FACT_KEYS: Final = (
    "association_id",
    "owner_user_id",
    "recommendation_request_id",
    "kind",
    "served_rank",
    "recording_id",
    "serving_provenance",
    "request_surface",
    "presentation_source",
    "presentation_surface",
    "wire_attribution_version",
    "presentation_id",
    "interaction_id",
    "listening_event_id",
    "impression_association_id",
    "original_request_sha256",
    "acceptance_sha256",
    "decision_sha256",
    "served_item_sha256",
    "association_sha256",
)
_JSON_FIELDS: Final = ",\n".join(
    f" '{name}'," + (f"encode(a.{name},'hex')" if name.endswith("_sha256") else f"a.{name}")
    for name in _FACT_KEYS
)
_FACT: Final = _define(
    "native_b_source3_outcome_fact_v3",
    "p_window uuid,p_child uuid,p_parent boolean",
    "uuid,uuid,boolean",
    "bytea",
    " selected_event uuid; selected_kind text; meta record; source_payload jsonb; ack jsonb;"
    " target_payload jsonb; v_bytes bigint; v_work bigint; v_nodes bigint; "
    "depth integer; raw bytea;",
    """
 PERFORM app_private._native_b_debit_v3(32768,4096,0,4::smallint);
 IF p_child IS NULL OR p_parent IS NULL THEN RAISE EXCEPTION 'B_SOURCE3_OUTCOME_LOCATOR'; END IF;
 -- Actual full child/recursive-parent/whole-row physical gate FIRST. Its
 -- current source body refuses before ANY legacy hydration until the complete
 -- causal/served cost and overlapping compiler peak are independently proved.
 -- There is no direct legacy-dispatch fallback below this call.
 PERFORM app_private.native_b_source3_validate_outcome_v3(p_window,p_child,p_parent);
 SELECT CASE WHEN p_parent THEN p.interaction_id
  ELSE coalesce(a.interaction_id,a.listening_event_id) END,
  CASE WHEN p_parent THEN p.kind ELSE a.kind END
 INTO STRICT selected_event,selected_kind FROM ml.recommendation_attribution_association a
 JOIN ml.recommendation_attribution_association p ON p.association_id=a.impression_association_id
 WHERE a.owner_user_id=permission.owner_user_id
  AND a.recommendation_request_id=permission.served_request_id
  AND coalesce(a.interaction_id,a.listening_event_id)=p_child AND a.kind IN('FEEDBACK','LISTEN')
  AND p.owner_user_id=a.owner_user_id AND p.recommendation_request_id=a.recommendation_request_id
  AND p.kind='IMPRESSION' FOR SHARE OF a,p;
 SELECT pg_column_compression(s.payload) AS pc,pg_column_size(s.payload) AS ps,
  pg_column_compression(i.terminal_ack) AS ac,pg_column_size(i.terminal_ack) AS az,
  pg_column_compression(i.payload) AS ic,pg_column_size(i.payload) AS iz,
  pg_column_compression(t.payload) AS tc,pg_column_size(t.payload) AS tz
 INTO STRICT meta FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
 LEFT JOIN library.user_interaction_event t ON t.interaction_id=s.event_id
  AND t.user_id=permission.owner_user_id
 WHERE s.event_id=selected_event AND s.user_id=permission.owner_user_id
  AND i.user_id=permission.owner_user_id;
 IF meta.pc IS NOT NULL OR meta.ac IS NOT NULL OR meta.ic IS NOT NULL OR meta.tc IS NOT NULL
  OR meta.ps IS NULL OR meta.ps NOT BETWEEN 1 AND 8192
  OR meta.az IS NULL OR meta.az NOT BETWEEN 1 AND 4092
  OR meta.iz IS NULL OR meta.iz NOT BETWEEN 1 AND 64
  OR (meta.tz IS NOT NULL AND meta.tz NOT BETWEEN 1 AND 8188) THEN
  RAISE EXCEPTION 'B_SOURCE3_OUTCOME_PHYSICAL_INPUT'; END IF;
 v_bytes:=8*(meta.ps::bigint+meta.az+coalesce(meta.tz,0)+12+8192+64)+65536;
 v_work:=128*(meta.ps::bigint+meta.az+coalesce(meta.tz,0))
  +65536*(2+CASE WHEN meta.tz IS NULL THEN 0 ELSE 1 END)+v_bytes+16*8192;
 v_nodes:=32*(meta.ps::bigint+meta.az+coalesce(meta.tz,0))
  +1056*(2+CASE WHEN meta.tz IS NULL THEN 0 ELSE 1 END)+16*8192;
 PERFORM app_private._native_b_debit_v3(v_work,v_nodes,0,4::smallint);
 depth:=app_private.native_v3_compiler_enter(v_bytes);
 SELECT s.payload,i.terminal_ack,t.payload INTO source_payload,ack,target_payload
 FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
 LEFT JOIN library.user_interaction_event t ON t.interaction_id=s.event_id
  AND t.user_id=permission.owner_user_id WHERE s.event_id=selected_event;
 PERFORM app_private.native_v3_assert_flat_scalar_resource(source_payload);
 PERFORM app_private.native_v3_assert_flat_scalar_resource(ack);
 IF target_payload IS NOT NULL THEN
  PERFORM app_private.native_v3_assert_flat_scalar_resource(target_payload); END IF;
 SELECT convert_to(jsonb_build_object(
"""
    + _JSON_FIELDS
    + """,
 'event_type',s.event_type,'server_sequence',s.server_sequence,
 'sync_created_at',s.created_at,'payload',source_payload,
 'device_id',i.device_id,'journal_epoch',i.journal_epoch,'device_sequence',i.device_sequence,
 'occurred_at',i.occurred_at,'received_at',i.received_at,
 'attribution_expires_at',a.attribution_expires_at,
 'source_request_sha256',encode(i.request_hash,'hex'),
 'normalized_payload_sha256',encode(i.normalized_payload_sha256,'hex'),
 'ingress_validation_version',2,'wire_schema_version',1,'presentation_labels_authoritative',false,
 'retained_until_ms',least(
  floor(extract(epoch FROM i.received_at)*1000)::bigint+180::bigint*86400000,
  floor(extract(epoch FROM a.attribution_expires_at)*1000)::bigint)
 )::text,'UTF8') INTO STRICT raw
 FROM ml.recommendation_attribution_association a
 JOIN sync.sync_event s ON s.event_id=coalesce(a.interaction_id,a.listening_event_id)
 JOIN sync.device_event_inbox i ON i.event_id=s.event_id
 WHERE a.owner_user_id=permission.owner_user_id
  AND a.recommendation_request_id=permission.served_request_id
  AND coalesce(a.interaction_id,a.listening_event_id)=selected_event
  AND s.user_id=permission.owner_user_id AND i.user_id=permission.owner_user_id
  AND s.ingress_validation_version=2 AND i.ingress_validation_version=2
  AND i.apply_status='APPLIED' AND i.error_code IS NULL
  AND NOT EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt er
   WHERE er.user_id=permission.owner_user_id AND er.source_event_id=selected_event);
 IF octet_length(raw)>16384 THEN RAISE EXCEPTION 'B_SOURCE3_OUTCOME_FACT_BYTES'; END IF;
 source_payload:=NULL; ack:=NULL; target_payload:=NULL;
 PERFORM app_private.native_v3_compiler_exit(depth); RETURN raw;
""",
)
SOURCE3_OUTCOME_SIGNATURES: Final = tuple(signature for signature, _ in _SPECS)
SOURCE3_OUTCOME_RESULT_TYPES: Final = MappingProxyType(dict(_SPECS))
SOURCE3_OUTCOME_RESULT_SETS: Final = MappingProxyType(
    {key: result.startswith("TABLE(") for key, result in _SPECS}
)
SOURCE3_OUTCOME_REQUIRED_FUNCTIONS: Final = (
    "app_private.native_b_source3_validate_outcome_v3(uuid,uuid,boolean)",
)
SOURCE3_OUTCOME_BODY_DEFINITIONS: Final = MappingProxyType(dict(_BODIES))
SOURCE3_OUTCOME_BODY_SHA256: Final = MappingProxyType(
    {key: sha256(value.encode()).hexdigest() for key, value in _BODIES.items()}
)
SOURCE3_OUTCOME_OWNERS: Final = MappingProxyType({key: _KERNEL for key in _BODIES})
GRANTS_SQL: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO native_v3_b_runtime;"
        for signature in SOURCE3_OUTCOME_SIGNATURES
    )
    + "\n"
)
INSTALL_SQL: Final = _BEGIN + _NEXT + _FACT + GRANTS_SQL
DROP_SQL: Final = (
    "\n".join(f"DROP FUNCTION {signature};" for signature in reversed(SOURCE3_OUTCOME_SIGNATURES))
    + "\n"
)
