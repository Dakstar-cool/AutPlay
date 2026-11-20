"""Dormant Source3 outcome metadata and scalar admission, never a cost grant.

The executable preflight closes the child/recursive-parent physical-size seam.
The scalar pass owns its one bounded JSONB carrier on the actual C compiler
stack, including the existing scalar helper's separate1MiB frame. The complete
legacy validator remains blocked: neither its work nor its surviving ROWTYPE
locals have a proved bound. No original validator or counter source is changed.
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
_TYPE: Final = "native_b_private_v3.source3_outcome_shape"
SOURCE3_OUTCOME_VALIDATION_STATE: Final = "METADATA_AND_SCALARS_ONLY_LEGACY_COST_UNQUALIFIED"
SOURCE3_OUTCOME_METADATA_COST_STATE: Final = "CANDIDATE_FIXED_QUANTUM_REQUIRES_PLAN_COST_REVIEW"
SOURCE3_OUTCOME_VALIDATION_MISSING: Final = (
    "actual installed legacy/generic/LP/role/fused-measurer body pins",
    "complete transitive work and refusal-path derivation",
    "live legacy child/parent/q/served-row compiler allocation derivation",
    "nested served root/baseline/fused-measurer aggregate compiler admission",
)

# Caps are physical uncompressed column sizes, including varlena headers. A zero
# cap means SQL NULL only. Every variable-length whole-row column must occur in
# this literal manifest; the catalog gate refuses a newly added varlena column.
# These are refusal limits, not an assertion of semantic validity or work cost.
_COLUMNS: Final = {
    "sync.sync_event": {
        "event_type": 804,
        "aggregate_type": 404,
        "operation": 36,
        "payload": 8192,
        "normalized_payload_sha256": 36,
    },
    "sync.device_event_inbox": {
        "event_type": 804,
        "aggregate_type": 404,
        "idempotency_key": 132,
        "payload": 64,
        "apply_status": 36,
        "error_code": 132,
        "request_hash": 36,
        "terminal_ack": 4092,
        "normalized_payload_sha256": 36,
    },
    "library.user_interaction_event": {"event_type": 804, "payload": 8192},
    "library.listening_event": {
        "completion_ratio": 64,
        "event_origin": 68,
        "context": 68,
        "explicit_feedback": 68,
    },
    "ml.recommendation_attribution_association": {
        "kind": 36,
        "serving_provenance": 68,
        "request_surface": 68,
        "presentation_source": 68,
        "presentation_surface": 68,
        "original_request_sha256": 36,
        "acceptance_sha256": 36,
        "decision_sha256": 36,
        "served_item_sha256": 36,
        "association_sha256": 36,
        "impression_association_kind": 36,
        "pack_served_items_sha256": 36,
        "pack_payload_sha256": 36,
    },
    "ml.recommendation_request": {
        "context": 68,
        "surface": 68,
        "pipeline_key": 4100,
        "pipeline_version": 4100,
        "pipeline_manifest_sha256": 36,
        "request_sha256": 36,
        "input_snapshot_sha256": 36,
        "availability_snapshot_ref": 68,
        "policy_snapshot_sha256": 36,
        "temporal_snapshot_sha256": 36,
        "adaptive_feature_policy_sha256": 36,
        "sona_shadow_pipeline_key": 0,
        "sona_shadow_pipeline_version": 0,
        "sona_shadow_pipeline_manifest_sha256": 0,
        "sona_tokenizer_sha256": 0,
        "sona_model_manifest_sha256": 0,
        "sona_request_sha256": 0,
        "sona_output_sha256": 0,
        "sona_shadow_evidence_sha256": 0,
        "sona_shadow_status": 0,
        "sona_shadow_reason": 0,
        "sona_shadow_document": 0,
        "request_document": 0,
        "model_bundle_version": 4100,
        "candidate_policy_version": 4100,
        "filter_policy_version": 4100,
        "reranker_version": 4100,
        "request_features": 8,
    },
    "ml.recommendation_serving_decision": {
        "state": 68,
        "serving_provenance": 68,
        "p11_ranking_sha256": 36,
        "inference_evidence_sha256": 0,
        "fallback_reason": 0,
        "p11_items_sha256": 36,
        "served_items_sha256": 36,
        "decision_sha256": 36,
    },
    "ml.recommendation_served_item": {
        "score_kind": 68,
        "attribution_source": 68,
        "origin": 68,
        "reasons": 65536,
        "reasons_sha256": 36,
        "contributions": 65536,
        "contributions_sha256": 36,
        "served_item_sha256": 36,
    },
    "ml.recommendation_item": {
        "score": 64,
        "candidate_sources": 65536,
        "explanation_code": 4100,
        "availability_snapshot": 8192,
        "contributions": 65536,
        "reason_codes": 65536,
        "item_provenance": 16384,
        "candidate_sources_sha256": 36,
        "availability_snapshot_sha256": 36,
        "contributions_sha256": 36,
        "reason_codes_sha256": 36,
        "item_provenance_sha256": 36,
        "baseline_item_sha256": 36,
    },
    "ml.recommendation_request_root_v3": {
        "root_document": 2097156,
        "source_codec_sha256": 36,
        "root_proof_sha256": 36,
        "request_root_sha256": 36,
        "input_origin_sha256": 36,
        "original_request_sha256": 36,
        "original_ranking_sha256": 36,
        "capture_sha256": 36,
        "eligibility_policy_sha256": 36,
        "baseline_snapshot_sha256": 36,
        "temporal_snapshot_sha256": 36,
        "candidate_membership_sha256": 36,
        "original_policy_snapshot_sha256": 36,
        "original_availability_snapshot_sha256": 36,
        "consent_receipt_sha256": 36,
    },
    "ml.recommendation_pipeline_version": {
        "pipeline_key": 404,
        "version": 404,
        "implementation_revision": 804,
        "manifest": 65536,
        "manifest_sha256": 36,
        "lifecycle_status": 68,
    },
}
SOURCE3_OUTCOME_VALIDATION_COLUMNS: Final = MappingProxyType(
    {relation: MappingProxyType(columns) for relation, columns in _COLUMNS.items()}
)

SOURCE3_OUTCOME_VALIDATION_REQUIRED_FUNCTIONS: Final = (
    "app_private._native_b_source3_payload_scope_v3(uuid)",
    "app_private._native_b_data_scope_v3()",
    "app_private._native_b_current_v3()",
    "app_private._native_b_debit_v3(bigint,bigint,bigint,smallint)",
    "app_private._native_b_poison_v3(native_b_private_v3.slot,oid,integer,xid8)",
    "app_private.native_v3_compiler_enter(bigint)",
    "app_private.native_v3_compiler_exit(integer)",
    "app_private.native_v3_assert_flat_scalar_resource(jsonb)",
)
SOURCE3_OUTCOME_VALIDATION_REQUIRED_RELATIONS: Final = (
    *_COLUMNS,
    "native_b_private_v3.frame",
    "native_b_private_v3.source3_read_pin",
    "app_private.native_v3_compiler_frame",
    "ml.native_attribution_purge_receipt",
    "ml.sona_event_erasure_receipt",
    "ml.recommendation_request_replay_payload",
    "ml.native_served_request_hash_format",
    "ml.native_original_capture_v3",
    "ml.recommendation_input_snapshot",
)

_SPECS: list[tuple[str, str]] = []
_BODIES: dict[str, str] = {}


def _define(name: str, result: str, declarations: str, target: str) -> str:
    signature = f"app_private.{name}(uuid,uuid,boolean)"
    body = (
        "\nDECLARE " + _FAULT_DECL + " permission native_b_private_v3.source3_permission%ROWTYPE;"
        " pin native_b_private_v3.source3_read_pin%ROWTYPE;"
        " binding native_b_private_v3.binding%ROWTYPE; control_depth integer;\n"
        + declarations
        + "\nBEGIN\n"
        + _ACTOR
        + " permission:=app_private._native_b_source3_payload_scope_v3(p_window);\n"
        " v_fault_scope:=app_private._native_b_data_scope_v3();\n"
        " binding:=app_private._native_b_current_v3();\n"
        " IF p_child IS NULL OR p_parent IS NULL THEN "
        "RAISE EXCEPTION 'B_SOURCE3_OUTCOME_LOCATOR'; END IF;\n"
        " -- Independent S2 and C compiler stores may not each own8MiB.\n"
        " IF EXISTS(SELECT depth FROM native_b_private_v3.frame LIMIT 1) THEN\n"
        " RAISE EXCEPTION 'B_SOURCE3_OUTCOME_S2_STACK_NOT_EMPTY'; END IF;\n"
        " control_depth:=app_private.native_v3_compiler_enter(65536);\n"
        " SELECT p.* INTO STRICT pin FROM native_b_private_v3.source3_read_pin p\n"
        " WHERE p.singleton AND p.window_id=permission.window_id\n"
        " AND p.transaction_id=pg_current_xact_id() AND p.documents_mask=63\n"
        " AND NOT p.root_consumed;\n"
        " IF pin.request_ordinal<>binding.request_ordinal THEN\n"
        " RAISE EXCEPTION 'B_SOURCE3_OUTCOME_READ_PIN'; END IF;\n"
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
        f"CREATE FUNCTION app_private.{name}(p_window uuid,p_child uuid,p_parent boolean)"
        f" RETURNS {result}\nLANGUAGE plpgsql VOLATILE SECURITY DEFINER"
        " SET search_path=pg_catalog AS $source3_outcome_validation$\n"
        + body
        + "\n$source3_outcome_validation$;\n"
        + f"ALTER FUNCTION {signature} OWNER TO {_KERNEL};\n"
        + f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,native_v3_b_runtime,"
        "native_v3_b_issuer,native_v3_b_catalog_owner;\n"
    )


def _metadata(relation: str, alias: str, predicate: str, expected: str) -> str:
    """Emit fixed column metadata only; never SELECT/to_jsonb a legacy row."""
    columns = _COLUMNS[relation]
    terms = [f"coalesce(pg_column_size({alias}.{name}),0)::bigint" for name in columns]
    checks = [
        f"{alias}.{name} IS NULL"
        if cap == 0
        else f"({alias}.{name} IS NULL OR (pg_column_compression({alias}.{name}) IS NULL "
        f"AND pg_column_size({alias}.{name})<={cap}))"
        for name, cap in columns.items()
    ]
    return (
        " measured_count:=0;\n FOR meta IN SELECT "
        + "+".join(terms)
        + " AS physical_bytes,"
        + " AND ".join(checks)
        + " AS valid\n"
        + f" FROM {relation} {alias} WHERE {predicate} LOOP\n"
        "  measured_count:=measured_count+1;\n"
        "  IF meta.valid IS DISTINCT FROM true THEN\n"
        "   RAISE EXCEPTION 'B_SOURCE3_OUTCOME_PHYSICAL_COLUMN'; END IF;\n"
        "  shape.measured_column_bytes:=shape.measured_column_bytes+meta.physical_bytes;\n"
        " END LOOP;\n" + f" IF measured_count<>({expected}) THEN\n"
        " RAISE EXCEPTION 'B_SOURCE3_OUTCOME_ROW_CARDINALITY'; END IF;\n"
    )


_CATALOG_VALUES: Final = ",\n".join(
    f"('{relation}'::regclass,'{name}')"
    for relation, columns in _COLUMNS.items()
    for name in columns
)
_RELATIONS: Final = ",".join(f"'{relation}'::regclass" for relation in _COLUMNS)
_CATALOG_GATE: Final = (
    " -- Catalog-only closed inventory before any whole-row value hydration.\n"
    f" IF EXISTS(SELECT 1 FROM pg_class WHERE oid IN ({_RELATIONS}) AND relnatts>128) THEN\n"
    " RAISE EXCEPTION 'B_SOURCE3_OUTCOME_ATTRIBUTE_COUNT'; END IF;\n"
    " IF EXISTS(SELECT 1 FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid\n"
    f" WHERE a.attrelid IN ({_RELATIONS}) AND a.attnum>0 AND NOT a.attisdropped\n"
    " AND (t.typlen=-1 OR t.typtype='d') AND NOT EXISTS(SELECT 1 FROM (VALUES\n"
    + _CATALOG_VALUES
    + ") expected(relation_id,column_name)\n"
    " WHERE expected.relation_id=a.attrelid AND expected.column_name=a.attname))\n"
    " OR EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attnum>0 AND NOT a.attisdropped\n"
    f" AND a.attrelid IN ({_RELATIONS}) GROUP BY a.attrelid HAVING count(*)>128) THEN\n"
    " RAISE EXCEPTION 'B_SOURCE3_OUTCOME_COLUMN_MANIFEST_CHANGED'; END IF;\n"
)

_PREFLIGHT_TARGET: str = r"""
 -- Metadata quantum only: no normalized hash/legacy validator is covered.
 -- Fixed <=11 relations, <=128 attributes each, two event rows, <=100 items.
 PERFORM app_private._native_b_debit_v3(1048576,262144,0,4::smallint);
 metadata_depth:=app_private.native_v3_compiler_enter(524288);
 shape.window_id:=permission.window_id; shape.owner_user_id:=permission.owner_user_id;
 shape.served_request_id:=permission.served_request_id; shape.child_event_id:=p_child;
 shape.measured_column_bytes:=0;
 SELECT a.kind,p.interaction_id INTO STRICT shape.child_kind,shape.parent_event_id
 FROM ml.recommendation_attribution_association a
 JOIN ml.recommendation_attribution_association p ON p.association_id=a.impression_association_id
 WHERE a.owner_user_id=permission.owner_user_id
  AND a.recommendation_request_id=permission.served_request_id
  AND (a.interaction_id=p_child OR a.listening_event_id=p_child)
  AND CASE WHEN pg_column_compression(a.kind) IS NULL AND pg_column_size(a.kind)<=36
   THEN a.kind END IN ('FEEDBACK','LISTEN')
  AND CASE WHEN pg_column_compression(p.kind) IS NULL AND pg_column_size(p.kind)<=36
   THEN p.kind END='IMPRESSION'
  AND p.owner_user_id=a.owner_user_id AND p.recommendation_request_id=a.recommendation_request_id
  AND p.interaction_id IS NOT NULL AND p.interaction_id<>p_child FOR SHARE OF a,p;
 shape.selected_event_id:=CASE WHEN p_parent THEN shape.parent_event_id ELSE p_child END;
 shape.selected_kind:=CASE WHEN p_parent THEN 'IMPRESSION' ELSE shape.child_kind END;
 shape.served_pass_count:=CASE WHEN p_parent THEN 1 ELSE 2 END;
 -- Neither a purge row nor an erasure row is needed by this retained live slice.
 -- Refuse them before the legacy SELECT * rather than hydrate an unused receipt.
 IF EXISTS(SELECT 1 FROM ml.native_attribution_purge_receipt
  WHERE interaction_id IN(p_child,shape.parent_event_id))
 OR EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt
  WHERE source_event_id IN(p_child,shape.parent_event_id))
 OR EXISTS(SELECT 1 FROM ml.recommendation_request_replay_payload
  WHERE recommendation_request_id=permission.served_request_id) THEN
  RAISE EXCEPTION 'B_SOURCE3_OUTCOME_RETIRED_OR_LEGACY_ROW'; END IF;
 SELECT r.baseline_snapshot_id,r.referenced_snapshot_id IS NOT NULL,
  r.source_snapshot_track_count,r.candidate_universe_count,
  r.p11_item_count,octet_length(r.root_document)
 INTO STRICT baseline_id,referenced,shape.track_count,shape.candidate_count,
  shape.item_count,shape.root_bytes
 FROM ml.recommendation_request_root_v3 r
 JOIN ml.native_served_request_hash_format f ON f.served_request_id=r.served_request_id
 WHERE r.served_request_id=permission.served_request_id AND r.owner_user_id=permission.owner_user_id
  AND r.original_request_id=permission.original_request_id
  AND r.request_hash_format_version=3 AND r.original_capture_format_version=3
  AND f.owner_user_id=r.owner_user_id AND f.request_hash_format_version=3
 FOR SHARE OF r,f;
 IF shape.track_count NOT BETWEEN 0 AND 5000
 OR shape.candidate_count NOT BETWEEN 0 AND shape.track_count
 OR shape.item_count NOT BETWEEN 0 AND 100 OR shape.root_bytes NOT BETWEEN 1 AND 2097152 THEN
  RAISE EXCEPTION 'B_SOURCE3_OUTCOME_ROOT_COUNTS'; END IF;
 -- All item scans are bounded before projection of any item JSON values.
 IF (SELECT count(*) FROM (SELECT 1 FROM ml.recommendation_item
  WHERE recommendation_request_id=permission.served_request_id LIMIT 101) bounded)<>shape.item_count
 OR (SELECT count(*) FROM (SELECT 1 FROM ml.recommendation_served_item
  WHERE recommendation_request_id=permission.served_request_id LIMIT 101) bounded)
  <>shape.item_count THEN
  RAISE EXCEPTION 'B_SOURCE3_OUTCOME_ITEM_COUNT'; END IF;
"""

_PREFLIGHT_TARGET += _CATALOG_GATE
for _relation, _alias, _predicate, _expected in (
    ("sync.sync_event", "s", "s.event_id IN(p_child,shape.parent_event_id)", "2"),
    ("sync.device_event_inbox", "i", "i.event_id IN(p_child,shape.parent_event_id)", "2"),
    (
        "library.user_interaction_event",
        "t",
        "t.interaction_id IN(p_child,shape.parent_event_id)",
        "CASE WHEN shape.child_kind='FEEDBACK' THEN 2 ELSE 1 END",
    ),
    (
        "library.listening_event",
        "l",
        "l.listening_event_id IN(p_child,shape.parent_event_id)",
        "CASE WHEN shape.child_kind='LISTEN' THEN 1 ELSE 0 END",
    ),
    (
        "ml.recommendation_attribution_association",
        "a",
        "a.interaction_id IN(p_child,shape.parent_event_id) OR a.listening_event_id=p_child",
        "2",
    ),
    (
        "ml.recommendation_request",
        "q",
        "q.recommendation_request_id=permission.served_request_id "
        "AND q.user_id=permission.owner_user_id",
        "1",
    ),
    (
        "ml.recommendation_serving_decision",
        "d",
        "d.recommendation_request_id=permission.served_request_id",
        "1",
    ),
    (
        "ml.recommendation_item",
        "i",
        "i.recommendation_request_id=permission.served_request_id",
        "shape.item_count",
    ),
    (
        "ml.recommendation_served_item",
        "i",
        "i.recommendation_request_id=permission.served_request_id",
        "shape.item_count",
    ),
    (
        "ml.recommendation_request_root_v3",
        "r",
        "r.served_request_id=permission.served_request_id",
        "1",
    ),
    (
        "ml.recommendation_pipeline_version",
        "p",
        "(p.pipeline_key,p.version)=(SELECT q.pipeline_key,q.pipeline_version "
        "FROM ml.recommendation_request q "
        "WHERE q.recommendation_request_id=permission.served_request_id)",
        "1",
    ),
):
    _PREFLIGHT_TARGET += _metadata(_relation, _alias, _predicate, _expected)

_PREFLIGHT_TARGET += r"""
 shape.baseline_bytes:=0;
 IF referenced OR EXISTS(SELECT 1 FROM ml.native_original_capture_v3
  WHERE original_request_id=permission.original_request_id
   AND owner_user_id=permission.owner_user_id) THEN
  SELECT CASE WHEN pg_column_compression(b.snapshot_document) IS NULL
   THEN pg_column_size(b.snapshot_document) END INTO STRICT shape.baseline_bytes
  FROM ml.recommendation_input_snapshot b WHERE b.recommendation_input_snapshot_id=baseline_id
   AND b.user_id=permission.owner_user_id FOR SHARE;
  IF shape.baseline_bytes IS NULL OR shape.baseline_bytes NOT BETWEEN 1 AND
   (CASE WHEN referenced THEN 4194300 ELSE 2097152 END) THEN
   RAISE EXCEPTION 'B_SOURCE3_OUTCOME_BASELINE_PHYSICAL'; END IF;
 END IF;
 SELECT pg_column_size(s.payload),pg_column_size(i.terminal_ack),
  coalesce(pg_column_size(t.payload),0)
 INTO STRICT shape.child_payload_bytes,shape.child_ack_bytes,shape.child_target_bytes
 FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
 LEFT JOIN library.user_interaction_event t ON t.interaction_id=s.event_id WHERE s.event_id=p_child;
 SELECT pg_column_size(s.payload),pg_column_size(i.terminal_ack),pg_column_size(t.payload)
 INTO STRICT shape.parent_payload_bytes,shape.parent_ack_bytes,shape.parent_target_bytes
 FROM sync.sync_event s JOIN sync.device_event_inbox i USING(event_id)
 JOIN library.user_interaction_event t ON t.interaction_id=s.event_id
 WHERE s.event_id=shape.parent_event_id;
 -- A scalar-only descriptor: no actual JSONB/whole legacy row escapes.
 meta:=NULL;
 PERFORM app_private.native_v3_compiler_exit(metadata_depth);
 pin:=NULL; permission:=NULL; binding:=NULL;
 PERFORM app_private.native_v3_compiler_exit(control_depth);
 RETURN shape;
"""

_TYPE_SQL: Final = f"""
CREATE TYPE {_TYPE} AS (
 window_id uuid,owner_user_id uuid,served_request_id uuid,child_event_id uuid,parent_event_id uuid,
 selected_event_id uuid,child_kind text,selected_kind text,served_pass_count integer,
 measured_column_bytes bigint,root_bytes bigint,baseline_bytes bigint,
 track_count integer,candidate_count integer,item_count integer,
 child_payload_bytes integer,child_ack_bytes integer,child_target_bytes integer,
 parent_payload_bytes integer,parent_ack_bytes integer,parent_target_bytes integer
);
ALTER TYPE {_TYPE} OWNER TO {_KERNEL};
REVOKE ALL ON TYPE {_TYPE} FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer;
"""

_PREFLIGHT_SQL: Final = _define(
    "native_b_source3_outcome_preflight_v3",
    _TYPE,
    f" shape {_TYPE}; meta record; measured_count integer; baseline_id uuid; referenced boolean;"
    " metadata_depth integer;",
    _PREFLIGHT_TARGET,
)

_SCALARS_SQL: Final = _define(
    "native_b_source3_outcome_scalars_v3",
    "void",
    f" shape {_TYPE}; depth integer; carrier record; value jsonb; selected_event uuid;",
    r"""
 shape:=app_private.native_b_source3_outcome_preflight_v3(p_window,p_child,p_parent);
 -- No two JSON values are retained here. The32KiB carrier frame covers two
 -- <=8192-byte binary carrier copies plus <=16KiB scalar/control overhead.
 -- The existing scalar helper enters its own1MiB frame on this SAME stack.
 depth:=app_private.native_v3_compiler_enter(32768);
 FOR carrier IN SELECT * FROM (VALUES
  (1,shape.child_payload_bytes),(2,shape.child_ack_bytes),(3,shape.child_target_bytes),
  (4,shape.parent_payload_bytes),(5,shape.parent_ack_bytes),(6,shape.parent_target_bytes)
 ) admitted(kind,physical_bytes) LOOP
  IF carrier.physical_bytes IS NULL OR carrier.physical_bytes NOT BETWEEN 0 AND 8192 THEN
   RAISE EXCEPTION 'B_SOURCE3_OUTCOME_SCALAR_BYTES'; END IF;
  IF carrier.physical_bytes=0 THEN CONTINUE; END IF;
  -- Exact existing normalized-scalar bound; excludes all legacy hash/graph work.
  PERFORM app_private._native_b_debit_v3(128*carrier.physical_bytes+65536,
   32*(carrier.physical_bytes+1)+1024,0,4::smallint);
  selected_event:=CASE WHEN carrier.kind<=3 THEN p_child ELSE shape.parent_event_id END;
  CASE carrier.kind
   WHEN 1,4 THEN SELECT s.payload INTO STRICT value FROM sync.sync_event s
    WHERE s.event_id=selected_event;
   WHEN 2,5 THEN SELECT i.terminal_ack INTO STRICT value FROM sync.device_event_inbox i
    WHERE i.event_id=selected_event;
   WHEN 3,6 THEN SELECT t.payload INTO STRICT value FROM library.user_interaction_event t
    WHERE t.interaction_id=selected_event;
   ELSE RAISE EXCEPTION 'B_SOURCE3_OUTCOME_SCALAR_KIND';
  END CASE;
  PERFORM app_private.native_v3_assert_flat_scalar_resource(value);
  value:=NULL;
 END LOOP;
 value:=NULL; carrier:=NULL; shape:=NULL;
 PERFORM app_private.native_v3_compiler_exit(depth);
 pin:=NULL; permission:=NULL; binding:=NULL;
 PERFORM app_private.native_v3_compiler_exit(control_depth);
 RETURN;
""",
)

_VALIDATE_SQL: Final = _define(
    "native_b_source3_validate_outcome_v3",
    "void",
    "",
    r"""
 PERFORM app_private.native_b_source3_outcome_scalars_v3(p_window,p_child,p_parent);
 -- Deliberately before ANY legacy ROWTYPE/hash/served-validator allocation.
 -- No flag, cost integer, caller DTO or function-existence claim can remove it.
 -- Source-only missing-software gate; replacement requires root/C review.
 RAISE EXCEPTION 'B_SOURCE3_OUTCOME_TRANSITIVE_COST_PROOF_REQUIRED' USING ERRCODE='55000';
""",
)

# NOT installed: the exact dispatch predecessor for a future qualified body.
# Before using it, that body must own the full surviving legacy locals in the
# actual C compiler store AND prepay every nested pass. No invented bytes grant.
SOURCE3_OUTCOME_LEGACY_DISPATCH_AFTER_QUALIFICATION: Final = """
IF shape.selected_kind='LISTEN' THEN
 PERFORM app_private.assert_native_listen_v2(shape.selected_event_id);
ELSIF shape.selected_kind='FEEDBACK' THEN
 PERFORM app_private.assert_native_feedback_v2(shape.selected_event_id);
ELSIF shape.selected_kind='IMPRESSION' THEN
 PERFORM app_private.assert_native_impression_v2(shape.selected_event_id);
ELSE RAISE EXCEPTION 'B_SOURCE3_OUTCOME_FACT_KIND'; END IF;
"""

SOURCE3_OUTCOME_VALIDATION_SIGNATURES: Final = tuple(signature for signature, _ in _SPECS)
SOURCE3_OUTCOME_VALIDATION_RESULT_TYPES: Final = MappingProxyType(dict(_SPECS))
SOURCE3_OUTCOME_VALIDATION_BODY_DEFINITIONS: Final = MappingProxyType(dict(_BODIES))
SOURCE3_OUTCOME_VALIDATION_BODY_SHA256: Final = MappingProxyType(
    {signature: sha256(body.encode()).hexdigest() for signature, body in _BODIES.items()}
)
SOURCE3_OUTCOME_VALIDATION_OWNERS: Final = MappingProxyType({key: _KERNEL for key in _BODIES})
_KEY_COLUMNS: Final = {
    "sync.sync_event": ("event_id",),
    "sync.device_event_inbox": ("event_id",),
    "library.user_interaction_event": ("interaction_id",),
    "library.listening_event": ("listening_event_id",),
    "ml.recommendation_attribution_association": (
        "association_id",
        "owner_user_id",
        "recommendation_request_id",
        "interaction_id",
        "listening_event_id",
        "impression_association_id",
    ),
    "ml.recommendation_request": ("recommendation_request_id", "user_id"),
    "ml.recommendation_serving_decision": ("recommendation_request_id",),
    "ml.recommendation_served_item": ("recommendation_request_id",),
    "ml.recommendation_item": ("recommendation_request_id",),
    "ml.recommendation_request_root_v3": (
        "served_request_id",
        "owner_user_id",
        "original_request_id",
        "baseline_snapshot_id",
        "request_hash_format_version",
        "original_capture_format_version",
        "source_snapshot_track_count",
        "candidate_universe_count",
        "p11_item_count",
    ),
    "ml.recommendation_pipeline_version": (),
    "ml.native_attribution_purge_receipt": ("interaction_id",),
    "ml.sona_event_erasure_receipt": ("source_event_id",),
    "ml.recommendation_request_replay_payload": ("recommendation_request_id",),
    "ml.native_served_request_hash_format": (
        "served_request_id",
        "owner_user_id",
        "request_hash_format_version",
    ),
    "ml.native_original_capture_v3": ("original_request_id", "owner_user_id"),
    "ml.recommendation_input_snapshot": (
        "recommendation_input_snapshot_id",
        "user_id",
        "snapshot_document",
    ),
    "native_b_private_v3.frame": ("depth",),
    "native_b_private_v3.source3_read_pin": (
        "singleton",
        "window_id",
        "transaction_id",
        "request_ordinal",
        "head",
        "documents_mask",
        "root_consumed",
    ),
}
SOURCE3_OUTCOME_VALIDATION_REQUIRED_COLUMN_GRANTS: Final = MappingProxyType(
    {
        relation: tuple(sorted(set(columns) | set(_COLUMNS.get(relation, {}))))
        for relation, columns in _KEY_COLUMNS.items()
    }
)
# Reviewable exact application grants, intentionally NOT appended to INSTALL_SQL.
# No role/table/sequence membership or runtime SELECT is conferred here.
REQUIRED_COLUMN_GRANTS_SQL: Final = (
    "\n".join(
        f"GRANT SELECT ({','.join(columns)}) ON {relation} TO {_KERNEL};"
        for relation, columns in SOURCE3_OUTCOME_VALIDATION_REQUIRED_COLUMN_GRANTS.items()
    )
    + "\n"
)
REQUIRED_HELPER_GRANTS_SQL: Final = (
    "\n".join(
        f"GRANT EXECUTE ON FUNCTION {signature} TO {_KERNEL};"
        for signature in SOURCE3_OUTCOME_VALIDATION_REQUIRED_FUNCTIONS
    )
    + "\n"
)
SOURCE3_OUTCOME_VALIDATION_PROCONFIG: Final = ("search_path=pg_catalog",)
SOURCE3_OUTCOME_VALIDATION_SECURITY_DEFINER: Final = True
SOURCE3_OUTCOME_VALIDATION_RUNTIME_GRANTS: Final = ()

_PREREQUISITES: Final = (
    "DO $$ BEGIN\n"
    + "\n".join(
        f"IF to_regclass('{relation}') IS NULL THEN "
        "RAISE EXCEPTION 'B_SOURCE3_OUTCOME_VALIDATION_RELATION_REQUIRED' "
        "USING ERRCODE='55000'; END IF;"
        for relation in SOURCE3_OUTCOME_VALIDATION_REQUIRED_RELATIONS
    )
    + "\n"
    + "\n".join(
        f"IF to_regprocedure('{signature}') IS NULL THEN "
        "RAISE EXCEPTION 'B_SOURCE3_OUTCOME_VALIDATION_HELPER_REQUIRED' "
        "USING ERRCODE='55000'; END IF;"
        for signature in SOURCE3_OUTCOME_VALIDATION_REQUIRED_FUNCTIONS
    )
    + "\nEND $$;\n"
)
INSTALL_SQL: Final = _PREREQUISITES + _TYPE_SQL + _PREFLIGHT_SQL + _SCALARS_SQL + _VALIDATE_SQL
DROP_SQL: Final = (
    "\n".join(
        f"DROP FUNCTION {signature};"
        for signature in reversed(SOURCE3_OUTCOME_VALIDATION_SIGNATURES)
    )
    + f"\nDROP TYPE {_TYPE};\n"
)
