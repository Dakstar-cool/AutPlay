"""Forward private feedback SQL; installed impression definitions remain authoritative."""

from __future__ import annotations

from sqlalchemy import Connection, text

PRIOR_FUNCTIONS = (
    "app_private.assert_native_impression_v2(uuid)",
    "app_private.native_impression_erasure_allowed(uuid,uuid)",
    "app_private.purge_recommendation_attribution_v2(uuid,uuid,text)",
    "app_private.purge_native_request_attributions_v2(uuid,uuid,text,integer)",
    "app_private.accept_native_impression_v2("
    "uuid,uuid,uuid,uuid,bigint,timestamptz,bytea,uuid,integer,uuid,uuid,text,text,integer)",
    "app_private.assert_normalized_listen_acceptance(uuid)",
    "app_private.guard_normalized_sync_ingress()",
)

SCHEMA_SQL = r"""
CREATE TABLE app_private.native_feedback_prior_definition(name text PRIMARY KEY,ddl text NOT NULL);
REVOKE ALL ON app_private.native_feedback_prior_definition FROM PUBLIC;
ALTER TABLE ml.recommendation_attribution_association
 DROP CONSTRAINT native_attr_kind_check,
 ADD CONSTRAINT native_attr_kind_check CHECK(kind IN ('IMPRESSION','FEEDBACK')),
 ADD COLUMN impression_association_id uuid,
 ADD COLUMN impression_association_kind text,
 ADD CONSTRAINT native_attr_impression_parent_check CHECK(
  (kind='IMPRESSION' AND impression_association_id IS NULL AND impression_association_kind IS NULL)
  OR (kind='FEEDBACK' AND impression_association_id IS NOT NULL
   AND impression_association_kind IS NOT NULL AND impression_association_kind='IMPRESSION')),
 ADD CONSTRAINT uq_attribution_impression_parent UNIQUE(
  association_id,owner_user_id,presentation_id,recommendation_request_id,served_rank,
  recording_id,serving_provenance,request_surface,kind),
 ADD CONSTRAINT fk_attribution_impression_parent FOREIGN KEY(
  impression_association_id,owner_user_id,presentation_id,recommendation_request_id,
  served_rank,recording_id,serving_provenance,request_surface,impression_association_kind)
  REFERENCES ml.recommendation_attribution_association(
   association_id,owner_user_id,presentation_id,recommendation_request_id,served_rank,
   recording_id,serving_provenance,request_surface,kind) ON DELETE RESTRICT;
CREATE INDEX ix_attribution_feedback_parent ON ml.recommendation_attribution_association
 (impression_association_id,association_id) WHERE kind='FEEDBACK';
ALTER TABLE library.user_interaction_event DROP CONSTRAINT ck_interaction_attribution_v2,
 ADD CONSTRAINT ck_interaction_attribution_v2 CHECK(attribution_contract_version=1 OR
  (recording_id IS NOT NULL AND presentation_id IS NOT NULL AND recommendation_request_id IS NULL
   AND source_rank IS NULL AND impression_interaction_id IS NULL AND (
    (event_type='RECOMMENDATION_IMPRESSION_RECORDED'
     AND payload='{"interaction_type":"RECOMMENDATION_IMPRESSION_RECORDED"}'::jsonb)
    OR (event_type='RECOMMENDATION_FEEDBACK_RECORDED' AND payload IN (
     '{"interaction_type":"RECOMMENDATION_FEEDBACK_RECORDED","feedback_type":"SELECTED"}'::jsonb,
     '{"interaction_type":"RECOMMENDATION_FEEDBACK_RECORDED","feedback_type":"DISMISSED"}'::jsonb)))));
"""

HASH_SQL = r"""
CREATE FUNCTION app_private.native_feedback_command_hash(
 owner_id uuid,device uuid,epoch uuid,eid uuid,seq bigint,happened timestamptz,
 original_hash bytea,request_id uuid,rank integer,recording uuid,presentation uuid,
 source_label text,surface_label text,impression uuid,feedback text,wire_version integer)
 RETURNS bytea
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT app_private.native_attribution_hash_v1('autplay.native-feedback-command.v1',ARRAY[
 uuid_send(owner_id),uuid_send(device),uuid_send(epoch),uuid_send(eid),int8send(seq),
 timestamptz_send(happened),original_hash,uuid_send(request_id),int8send(rank::bigint),
 uuid_send(recording),uuid_send(presentation),convert_to(source_label,'UTF8'),
 convert_to(surface_label,'UTF8'),uuid_send(impression),convert_to(feedback,'UTF8'),
 int8send(wire_version::bigint)]) $$;
CREATE FUNCTION app_private.native_feedback_target_hash(t library.user_interaction_event)
 RETURNS bytea LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT app_private.native_attribution_hash_v1('autplay.native-feedback-target.v1',ARRAY[
 uuid_send(t.interaction_id),uuid_send(t.user_id),uuid_send(t.device_id),
 convert_to(t.event_type,'UTF8'),uuid_send(t.recording_id),uuid_send(t.presentation_id),
 timestamptz_send(t.created_at),int8send(t.attribution_contract_version::bigint),
 boolsend(t.attribution_closed),convert_to(t.payload->>'feedback_type','UTF8')]) $$;
CREATE FUNCTION app_private.native_feedback_association_hash(
 a ml.recommendation_attribution_association) RETURNS bytea
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT app_private.native_attribution_hash_v1('autplay.native-feedback-association.v1',ARRAY[
 uuid_send(a.association_id),uuid_send(a.owner_user_id),uuid_send(a.recommendation_request_id),
 convert_to(a.kind,'UTF8'),int8send(a.served_rank::bigint),uuid_send(a.recording_id),
 convert_to(a.serving_provenance,'UTF8'),convert_to(a.request_surface,'UTF8'),
 convert_to(a.presentation_source,'UTF8'),convert_to(a.presentation_surface,'UTF8'),
 int8send(a.wire_attribution_version::bigint),uuid_send(a.presentation_id),uuid_send(a.interaction_id),
 timestamptz_send(a.attribution_expires_at),a.original_request_sha256,a.acceptance_sha256,
 a.decision_sha256,a.served_item_sha256,uuid_send(a.impression_association_id),
 convert_to(a.impression_association_kind,'UTF8')]) $$;
CREATE FUNCTION app_private.assert_native_feedback_parent(
 a ml.recommendation_attribution_association,happened timestamptz,admission boolean) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE parent ml.recommendation_attribution_association%ROWTYPE;
 target library.user_interaction_event%ROWTYPE;
BEGIN
 SELECT * INTO parent FROM ml.recommendation_attribution_association
  WHERE association_id=a.impression_association_id FOR SHARE;
 IF a.kind IS DISTINCT FROM 'FEEDBACK'
  OR a.impression_association_kind IS DISTINCT FROM 'IMPRESSION'
  OR parent.association_id IS NULL OR parent.kind IS DISTINCT FROM 'IMPRESSION'
  OR parent.owner_user_id IS DISTINCT FROM a.owner_user_id
  OR parent.presentation_id IS DISTINCT FROM a.presentation_id
  OR parent.recommendation_request_id IS DISTINCT FROM a.recommendation_request_id
  OR parent.served_rank IS DISTINCT FROM a.served_rank
  OR parent.recording_id IS DISTINCT FROM a.recording_id
  OR parent.serving_provenance IS DISTINCT FROM a.serving_provenance
  OR parent.request_surface IS DISTINCT FROM a.request_surface
  OR parent.attribution_expires_at IS DISTINCT FROM a.attribution_expires_at THEN
  RAISE EXCEPTION 'NATIVE_FEEDBACK_PARENT_BINDING' USING ERRCODE='23514';
 END IF;
 PERFORM app_private.assert_native_impression_v2(parent.interaction_id);
 SELECT * INTO target FROM library.user_interaction_event
  WHERE interaction_id=parent.interaction_id AND user_id=a.owner_user_id FOR SHARE;
 IF target.interaction_id IS NULL OR target.attribution_closed OR happened<target.created_at
  OR (admission AND EXISTS(SELECT 1 FROM ml.sona_event_erasure_receipt
   WHERE user_id=a.owner_user_id AND source_event_id=parent.interaction_id)) THEN
  RAISE EXCEPTION 'NATIVE_FEEDBACK_PARENT_UNAVAILABLE' USING ERRCODE='23514';
 END IF;
END $$;
"""

_FEEDBACK_DISPATCH = """ -- 0094: exact feedback dispatch, including retained ERASED inboxes.
 IF EXISTS(SELECT 1 FROM sync.device_event_inbox WHERE event_id=eid
  AND ingress_validation_version=2 AND event_type='RECOMMENDATION_FEEDBACK_RECORDED')
  OR EXISTS(SELECT 1 FROM library.user_interaction_event WHERE interaction_id=eid
   AND attribution_contract_version=2 AND event_type='RECOMMENDATION_FEEDBACK_RECORDED')
  OR EXISTS(SELECT 1 FROM ml.recommendation_attribution_association
   WHERE interaction_id=eid AND kind='FEEDBACK') THEN
  PERFORM app_private.assert_native_feedback_v2(eid);
  RETURN;
 END IF;
"""

_FEEDBACK_ACK = """ -- 0094: exact feedback APPLIED transition under both protected contexts.
 IF TG_OP='UPDATE' AND TG_TABLE_NAME='device_event_inbox' THEN
 IF OLD.ingress_validation_version=2 AND NEW.ingress_validation_version=2
  AND OLD.event_type='RECOMMENDATION_FEEDBACK_RECORDED'
  AND (to_jsonb(NEW)-ARRAY['apply_status','terminal_ack','idempotency_key'])
   = (to_jsonb(OLD)-ARRAY['apply_status','terminal_ack','idempotency_key'])
  AND OLD.apply_status='RECEIVED' AND NEW.apply_status='APPLIED'
  AND NEW.idempotency_key='native-feedback-v2:'||NEW.event_id
  AND NEW.terminal_ack=app_private.native_impression_ack(NEW.event_id,NEW.device_sequence)
  AND EXISTS(SELECT 1 FROM app_private.native_attribution_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=NEW.event_id
    AND c.owner_id=NEW.user_id AND c.action='ACCEPT')
  AND EXISTS(SELECT 1 FROM app_private.normalized_ingress_write_context c
   WHERE c.transaction_id=pg_current_xact_id() AND c.event_id=NEW.event_id) THEN RETURN NEW; END IF;
 END IF;
"""


def replace_exact(source: str, anchor: str, replacement: str, *, count: int = 1) -> str:
    """Fail closed when the installed predecessor no longer has the reviewed shape."""
    if source.count(anchor) != count:
        raise RuntimeError("native_feedback_prior_function_changed")
    return source.replace(anchor, replacement)


def feedback_validator(source: str) -> str:
    """Retain every predecessor binding and lifecycle check in the typed branch."""
    result = replace_exact(
        source, "app_private.assert_native_impression_v2(", "app_private.assert_native_feedback_v2("
    )
    result = result.replace(
        "RECOMMENDATION_IMPRESSION_RECORDED", "RECOMMENDATION_FEEDBACK_RECORDED"
    )
    result = result.replace("NATIVE_IMPRESSION_", "NATIVE_FEEDBACK_")
    result = result.replace("native-impression-v2:", "native-feedback-v2:")
    for original, new in (
        ("native_impression_target_hash", "native_feedback_target_hash"),
        ("native_impression_association_hash", "native_feedback_association_hash"),
        ("native_impression_command_hash", "native_feedback_command_hash"),
    ):
        result = replace_exact(result, original, new)
    result = replace_exact(
        result,
        "OR s.payload IS DISTINCT FROM "
        '\'{"interaction_type":"RECOMMENDATION_FEEDBACK_RECORDED"}\'::jsonb',
        "OR (s.payload IS DISTINCT FROM jsonb_build_object("
        "'interaction_type','RECOMMENDATION_FEEDBACK_RECORDED','feedback_type',t.payload->>'feedback_type'))"
        "\n  OR t.payload->>'feedback_type' IS NULL"
        " OR t.payload->>'feedback_type' NOT IN ('SELECTED','DISMISSED')",
    )
    result = replace_exact(
        result,
        "a.wire_attribution_version) THEN",
        "a.impression_association_id,t.payload->>'feedback_type',a.wire_attribution_version) THEN",
    )
    result = replace_exact(
        result,
        " SELECT * INTO q FROM ml.recommendation_request",
        " PERFORM app_private.assert_native_feedback_parent(a,t.created_at,false);"
        "\n SELECT * INTO q FROM ml.recommendation_request",
    )
    return result


def feedback_acceptance(source: str) -> str:
    """Clone only the installed private impression seam, preserving its fencing."""
    result = replace_exact(
        source, "app_private.accept_native_impression_v2(", "app_private.accept_native_feedback_v2("
    )
    result = replace_exact(
        result,
        "p_wire_version integer",
        "p_impression uuid, p_feedback text, p_wire_version integer",
    )
    result = result.replace(
        "RECOMMENDATION_IMPRESSION_RECORDED", "RECOMMENDATION_FEEDBACK_RECORDED"
    )
    result = result.replace("NATIVE_IMPRESSION_", "NATIVE_FEEDBACK_")
    result = result.replace("native-impression-v2:", "native-feedback-v2:")
    result = result.replace("native_impression_command_hash", "native_feedback_command_hash")
    result = result.replace(
        "native_impression_association_hash", "native_feedback_association_hash"
    )
    result = result.replace("assert_native_impression_v2", "assert_native_feedback_v2")
    result = replace_exact(
        result,
        "OR p_request IS NULL OR p_recording IS NULL OR p_presentation IS NULL",
        "OR p_request IS NULL OR p_recording IS NULL OR p_presentation IS NULL"
        "\n  OR p_impression IS NULL OR p_feedback IS NULL"
        " OR p_feedback NOT IN ('SELECTED','DISMISSED')",
    )
    result = replace_exact(
        result,
        "p_source,p_surface,p_wire_version);",
        "p_source,p_surface,p_impression,p_feedback,p_wire_version);",
    )
    result = replace_exact(
        result,
        "WHERE owner_user_id=p_owner AND presentation_id=p_presentation\n"
        "   AND recommendation_request_id=p_request AND served_rank=p_rank FOR UPDATE;",
        "WHERE owner_user_id=p_owner AND interaction_id=p_event AND kind='FEEDBACK' FOR UPDATE;",
    )
    result = replace_exact(
        result,
        'payload:=\'{"interaction_type":"RECOMMENDATION_FEEDBACK_RECORDED"}\'::jsonb;',
        "payload:=jsonb_build_object('interaction_type','RECOMMENDATION_FEEDBACK_RECORDED',"
        "'feedback_type',p_feedback);",
    )
    result = replace_exact(result, "a.kind:='IMPRESSION';", "a.kind:='FEEDBACK';")
    result = replace_exact(
        result,
        "a.association_sha256:=app_private.native_feedback_association_hash(a);",
        "a.impression_association_id:=p_impression;"
        " a.impression_association_kind:='IMPRESSION';\n"
        " PERFORM app_private.assert_native_feedback_parent(a,p_occurred,true);\n"
        " a.association_sha256:=app_private.native_feedback_association_hash(a);",
    )
    return result


def install_native_feedback(connection: Connection) -> None:
    """Capture installed definitions once, then apply bounded forward extensions."""
    connection.execute(text(SCHEMA_SQL))
    prior: dict[str, str] = {}
    for signature in PRIOR_FUNCTIONS:
        definition = connection.scalar(
            text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": signature}
        )
        if not isinstance(definition, str):
            raise RuntimeError("native_feedback_prior_function_missing")
        prior[signature] = definition
        connection.execute(
            text("INSERT INTO app_private.native_feedback_prior_definition VALUES(:name,:ddl)"),
            {"name": signature, "ddl": definition},
        )
    connection.execute(text(HASH_SQL))
    connection.execute(text(feedback_validator(prior[PRIOR_FUNCTIONS[0]])))
    connection.execute(text(feedback_acceptance(prior[PRIOR_FUNCTIONS[4]])))
    for signature in (PRIOR_FUNCTIONS[0], PRIOR_FUNCTIONS[5]):
        connection.execute(
            text(replace_exact(prior[signature], "BEGIN\n", "BEGIN\n" + _FEEDBACK_DISPATCH))
        )
    connection.execute(
        text(replace_exact(prior[PRIOR_FUNCTIONS[6]], "BEGIN\n", "BEGIN\n" + _FEEDBACK_ACK))
    )
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[1]],
                "s.event_type='RECOMMENDATION_IMPRESSION_RECORDED'",
                "s.event_type IN ('RECOMMENDATION_IMPRESSION_RECORDED',"
                "'RECOMMENDATION_FEEDBACK_RECORDED')",
            )
        )
    )
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[2]],
                "app_private.native_impression_target_hash(t),p_reason",
                "CASE WHEN a.kind='FEEDBACK' THEN app_private.native_feedback_target_hash(t)"
                " ELSE app_private.native_impression_target_hash(t) END,p_reason",
            )
        )
    )
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[3]],
                "ORDER BY association_id LIMIT p_limit",
                "ORDER BY CASE WHEN kind='FEEDBACK' THEN 0 ELSE 1 END,association_id LIMIT p_limit",
            )
        )
    )
    connection.execute(
        text(
            replace_exact(
                prior[PRIOR_FUNCTIONS[4]],
                "WHERE owner_user_id=p_owner AND presentation_id=p_presentation",
                "WHERE kind='IMPRESSION' AND owner_user_id=p_owner "
                "AND presentation_id=p_presentation",
            )
        )
    )
    connection.execute(
        text("""
REVOKE ALL ON FUNCTION
 app_private.native_feedback_command_hash(uuid,uuid,uuid,uuid,bigint,timestamptz,bytea,uuid,integer,uuid,uuid,text,text,uuid,text,integer),
 app_private.native_feedback_target_hash(library.user_interaction_event),
 app_private.native_feedback_association_hash(ml.recommendation_attribution_association),
 app_private.assert_native_feedback_parent(ml.recommendation_attribution_association,timestamptz,boolean),
 app_private.assert_native_feedback_v2(uuid),
 app_private.accept_native_feedback_v2(uuid,uuid,uuid,uuid,bigint,timestamptz,bytea,uuid,integer,uuid,uuid,text,text,uuid,text,integer)
 FROM PUBLIC;
""")
    )


def remove_native_feedback(connection: Connection) -> None:
    """Refuse feedback evidence, then restore exact captured predecessor functions."""
    connection.execute(
        text("""
LOCK TABLE ml.recommendation_request,ml.recommendation_serving_decision,
 ml.recommendation_served_item,library.user_interaction_event,
 ml.recommendation_attribution_association,ml.native_attribution_purge_receipt,
 sync.sync_event,sync.device_event_inbox,app_private.native_attribution_context,
 library.listening_event,ml.recommendation_temporal_event,ml.offline_recommendation_pack
 IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ml.recommendation_attribution_association WHERE kind='FEEDBACK')
  OR EXISTS(SELECT 1 FROM library.user_interaction_event WHERE attribution_contract_version=2
   AND event_type='RECOMMENDATION_FEEDBACK_RECORDED')
  OR EXISTS(SELECT 1 FROM sync.device_event_inbox WHERE ingress_validation_version=2
   AND event_type='RECOMMENDATION_FEEDBACK_RECORDED' AND apply_status<>'RECEIVED') THEN
  RAISE EXCEPTION 'refusing native feedback downgrade with evidence' USING ERRCODE='55000';
 END IF;
END $$;
""")
    )
    for signature in PRIOR_FUNCTIONS:
        definition = connection.scalar(
            text("SELECT ddl FROM app_private.native_feedback_prior_definition WHERE name=:name"),
            {"name": signature},
        )
        if not isinstance(definition, str):
            raise RuntimeError("native_feedback_prior_function_missing")
        connection.execute(text(definition))
    connection.execute(
        text(r"""
DROP FUNCTION app_private.accept_native_feedback_v2(
 uuid,uuid,uuid,uuid,bigint,timestamptz,bytea,uuid,integer,uuid,uuid,text,text,uuid,text,integer);
DROP FUNCTION app_private.assert_native_feedback_v2(uuid);
DROP FUNCTION app_private.assert_native_feedback_parent(
 ml.recommendation_attribution_association,timestamptz,boolean);
DROP FUNCTION app_private.native_feedback_association_hash(
 ml.recommendation_attribution_association);
DROP FUNCTION app_private.native_feedback_target_hash(library.user_interaction_event);
DROP FUNCTION app_private.native_feedback_command_hash(
 uuid,uuid,uuid,uuid,bigint,timestamptz,bytea,uuid,integer,uuid,uuid,text,text,uuid,text,integer);
ALTER TABLE ml.recommendation_attribution_association
 DROP CONSTRAINT fk_attribution_impression_parent,
 DROP CONSTRAINT uq_attribution_impression_parent,
 DROP CONSTRAINT native_attr_impression_parent_check,
 DROP CONSTRAINT native_attr_kind_check,
 ADD CONSTRAINT native_attr_kind_check CHECK(kind='IMPRESSION'),
 DROP COLUMN impression_association_id,
 DROP COLUMN impression_association_kind;
ALTER TABLE library.user_interaction_event DROP CONSTRAINT ck_interaction_attribution_v2,
 ADD CONSTRAINT ck_interaction_attribution_v2 CHECK(attribution_contract_version=1 OR
  (event_type='RECOMMENDATION_IMPRESSION_RECORDED' AND recording_id IS NOT NULL
   AND presentation_id IS NOT NULL AND recommendation_request_id IS NULL
   AND source_rank IS NULL AND impression_interaction_id IS NULL
   AND payload='{"interaction_type":"RECOMMENDATION_IMPRESSION_RECORDED"}'::jsonb));
DROP TABLE app_private.native_feedback_prior_definition;
""")
    )
