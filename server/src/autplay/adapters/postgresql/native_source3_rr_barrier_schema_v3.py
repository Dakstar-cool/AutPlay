"""Fixed writer-side enforcement for the Source3 pre-snapshot owner barrier.

Private migration candidate. Importing never installs SQL or changes grants.
The lock namespace is the existing sync/native-capture publication namespace.
Every changing old/new owner is covered, including absent floor/membership rows.
Ordinary DML gets a nonblocking transaction lock; the source holds the conflicting
session lock on its detached backend across the RC control COMMIT and RR read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from sqlalchemy import Connection
    from sqlalchemy.orm import Session

OWNER_KEY_NAMESPACE_V3: Final = b"autplay-sync-owner-publish-v1"
OWNER_TABLES_V3: Final = (
    ("account.user_account", "user_id"),
    ("account.device", "user_id"),
    ("account.training_consent", "user_id"),
    ("account.training_consent_operation", "user_id"),
    ("account.account_deletion_request", "user_id"),
    ("sync.sync_event", "user_id"),
    ("sync.device_event_inbox", "user_id"),
    ("sync.device_erasure_floor", "user_id"),
    ("sync.device_erasure_invalidated_epoch", "user_id"),
    ("sync.owner_erasure_floor", "user_id"),
    ("ml.sona_event_erasure_receipt", "user_id"),
    ("ml.native_original_capture_format", "owner_user_id"),
    ("ml.native_served_request_hash_format", "owner_user_id"),
    ("ml.native_original_capture_v3", "owner_user_id"),
    ("ml.native_capture_source_event_v3", "owner_user_id"),
    ("ml.native_capture_lineage_cursor_v3", "owner_user_id"),
    ("ml.native_capture_replay_v3", "owner_user_id"),
    ("ml.recommendation_request_root_v3", "owner_user_id"),
    ("ml.native_atomic_capture_connection_v3", "owner_user_id"),
    ("ml.recommendation_request", "user_id"),
    ("ml.recommendation_input_snapshot", "user_id"),
    ("ml.recommendation_attribution_association", "owner_user_id"),
    ("ml.recommendation_request_replay_payload", "owner_user_id"),
    ("library.listening_event", "user_id"),
    ("library.user_interaction_event", "user_id"),
)
REQUEST_CHILD_TABLES_V3: Final = (
    "ml.recommendation_serving_decision",
    "ml.recommendation_item",
    "ml.recommendation_served_item",
)
PROTECTED_TABLES_V3: Final = tuple(t for t, _ in OWNER_TABLES_V3) + REQUEST_CHILD_TABLES_V3
_TRIGGER: Final = "native_source3_rr_owner_barrier_v3"
_TRUNCATE_TRIGGER: Final = "native_source3_rr_no_truncate_v3"
_FUNCTION_PREFIX: Final = "app_private.native_source3_rr_"


def _try_owner_sql(owner: str) -> str:
    # All callers below are fixed source literals, never identifiers/SQL from a
    # request, TG_ARGV, configuration, Session.info or a publication descriptor.
    return (
        "pg_try_advisory_xact_lock(('x'||substr(encode(sha256("
        "convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send("
        + owner
        + ")), 'hex'),1,16))::bit(64)::bigint)"
    )


def _direct_body(column: str) -> str:
    assert column in ("user_id", "owner_user_id")
    return """
DECLARE owners uuid[]; owner uuid;
BEGIN
 IF TG_OP='INSERT' THEN owners:=ARRAY[NEW.COLUMN];
 ELSIF TG_OP='DELETE' THEN owners:=ARRAY[OLD.COLUMN];
 ELSE owners:=ARRAY[OLD.COLUMN,NEW.COLUMN]; END IF;
 IF array_position(owners,NULL) IS NOT NULL THEN
  RAISE EXCEPTION 'NATIVE_SOURCE3_RR_OWNER_REQUIRED' USING ERRCODE='23514'; END IF;
 FOR owner IN SELECT DISTINCT x FROM unnest(owners) AS selected(x) ORDER BY x LOOP
  IF NOT TRY_OWNER THEN
   RAISE EXCEPTION 'NATIVE_SOURCE3_RR_OWNER_BUSY' USING ERRCODE='55P03'; END IF;
 END LOOP;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END;
""".replace("COLUMN", column).replace("TRY_OWNER", _try_owner_sql("owner"))


_CHILD_BODY: Final = """
DECLARE requests uuid[]; request_id uuid; owner uuid; owners uuid[]:=ARRAY[]::uuid[];
BEGIN
 IF TG_OP='INSERT' THEN requests:=ARRAY[NEW.recommendation_request_id];
 ELSIF TG_OP='DELETE' THEN requests:=ARRAY[OLD.recommendation_request_id];
 ELSE requests:=ARRAY[OLD.recommendation_request_id,NEW.recommendation_request_id]; END IF;
 FOR request_id IN SELECT DISTINCT x FROM unnest(requests) AS selected(x) ORDER BY x LOOP
  SELECT r.user_id INTO owner FROM ml.recommendation_request r
   WHERE r.recommendation_request_id=request_id FOR KEY SHARE OF r NOWAIT;
  IF NOT FOUND THEN
   -- A genuine FK cascade can run after the parent tuple has been removed.
   -- The protected parent's BEFORE DELETE already acquired its owner key.
   -- A direct child DELETE with a missing parent is never accepted.
   IF TG_OP='DELETE' AND pg_trigger_depth()>1 THEN CONTINUE; END IF;
   RAISE EXCEPTION 'NATIVE_SOURCE3_RR_PARENT_REQUIRED' USING ERRCODE='23514';
  END IF;
  IF owner IS NULL THEN
   RAISE EXCEPTION 'NATIVE_SOURCE3_RR_OWNER_REQUIRED' USING ERRCODE='23514'; END IF;
  owners:=array_append(owners,owner);
 END LOOP;
 FOR owner IN SELECT DISTINCT x FROM unnest(owners) AS selected(x) ORDER BY x LOOP
  IF NOT TRY_OWNER THEN
   RAISE EXCEPTION 'NATIVE_SOURCE3_RR_OWNER_BUSY' USING ERRCODE='55P03'; END IF;
 END LOOP;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END;
""".replace("TRY_OWNER", _try_owner_sql("owner"))
_TRUNCATE_BODY: Final = """
BEGIN
 RAISE EXCEPTION 'NATIVE_SOURCE3_RR_TRUNCATE_FORBIDDEN' USING ERRCODE='0A000';
END;
"""
FUNCTION_BODIES_V3: Final = {
    "user_id_v3()": _direct_body("user_id"),
    "owner_user_id_v3()": _direct_body("owner_user_id"),
    "request_child_v3()": _CHILD_BODY,
    "no_truncate_v3()": _TRUNCATE_BODY,
}


def _function(table: str) -> str:
    columns = dict(OWNER_TABLES_V3)
    suffix = columns.get(table, "request_child")
    return _FUNCTION_PREFIX + suffix + "_v3()"


def _install_sql() -> str:
    functions = "\n".join(
        f"CREATE FUNCTION {_FUNCTION_PREFIX}{name} RETURNS trigger "
        "LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog "
        f"AS $source3_rr${body}$source3_rr$;\n"
        f"REVOKE ALL ON FUNCTION {_FUNCTION_PREFIX}{name} FROM PUBLIC;"
        for name, body in FUNCTION_BODIES_V3.items()
    )
    triggers = "\n".join(
        f"CREATE TRIGGER {_TRIGGER} BEFORE INSERT OR UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {_function(table)};\n"
        f"ALTER TABLE {table} ENABLE ALWAYS TRIGGER {_TRIGGER};\n"
        f"CREATE TRIGGER {_TRUNCATE_TRIGGER} BEFORE TRUNCATE ON {table} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {_FUNCTION_PREFIX}no_truncate_v3();\n"
        f"ALTER TABLE {table} ENABLE ALWAYS TRIGGER {_TRUNCATE_TRIGGER};"
        for table in PROTECTED_TABLES_V3
    )
    return functions + "\n" + triggers


NATIVE_SOURCE3_RR_BARRIER_INSTALL_SQL_V3: Final = _install_sql()
_AUDIT_SQL: Final = """
SELECT t.tgname,t.tgtype::int,t.tgenabled,t.tgisinternal,t.tgconstraint::bigint,
       t.tgdeferrable,t.tginitdeferred,t.tgnargs,t.tgargs,t.tgqual IS NULL,
       t.tgattr::text,p.oid::bigint,p.prosecdef,p.provolatile,p.proconfig,p.prosrc,
       l.lanname,p.prorettype='pg_catalog.trigger'::regtype,
       p.pronargs,p.prokind,p.proleakproof,p.prosupport::oid::bigint
FROM pg_catalog.pg_trigger t
JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
JOIN pg_catalog.pg_language l ON l.oid=p.prolang
WHERE t.tgrelid=pg_catalog.to_regclass(:table)
 AND t.tgname IN ('native_source3_rr_owner_barrier_v3','native_source3_rr_no_truncate_v3')
ORDER BY t.tgname
"""


def audit_writer_barriers_v3(session: Session) -> None:
    """Exact fixed coverage/body check; catalog visibility adds no authority.

    Caller must already have C's original no-owner/no-DDL/principal audit and a
    paid original F control scope. Missing visibility, tables or triggers refuses.
    No alternate trigger names, WHEN, UPDATE OF, disabled/replica-only trigger,
    definer body, untrusted language, TG_ARGV or user support function is adopted.
    """
    from sqlalchemy import text

    for table in PROTECTED_TABLES_V3:
        ordinary = session.scalar(
            text("""
            SELECT c.relkind='r' AND NOT c.relispartition AND NOT c.relrowsecurity
             AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i
                            WHERE i.inhparent=c.oid OR i.inhrelid=c.oid)
            FROM pg_catalog.pg_class c WHERE c.oid=to_regclass(:table)
        """),
            {"table": table},
        )
        if ordinary is not True:
            raise RuntimeError("NATIVE_SOURCE3_RR_TABLE_SHAPE")
        rows = session.execute(text(_AUDIT_SQL), {"table": table}).all()
        if len(rows) != 2:
            raise RuntimeError("NATIVE_SOURCE3_RR_WRITER_COVERAGE")
        for row in rows:
            truncate = row[0] == _TRUNCATE_TRIGGER
            signature = _FUNCTION_PREFIX + "no_truncate_v3()" if truncate else _function(table)
            expected_oid = session.scalar(
                text("SELECT to_regprocedure(:function)::oid::bigint"), {"function": signature}
            )
            body = FUNCTION_BODIES_V3[signature.removeprefix(_FUNCTION_PREFIX)]
            if (
                row[1] != (34 if truncate else 31)
                or row[2] != "A"
                or row[3]
                or row[4] != 0
                or row[5]
                or row[6]
                or row[7] != 0
                or bytes(row[8]) != b""
                or not row[9]
                or row[10] != ""
                or expected_oid is None
                or row[11] != expected_oid
                or row[12]
                or row[13] != "v"
                or row[14] != ["search_path=pg_catalog"]
                or row[15] != body
                or row[16] != "plpgsql"
                or row[17] is not True
                or row[18] != 0
                or row[19] != "f"
                or row[20]
                or row[21] != 0
            ):
                raise RuntimeError("NATIVE_SOURCE3_RR_WRITER_DEFINITION")


def install(connection: Connection) -> None:
    """Explicit migration only; creates neither roles nor runtime ACL grants."""
    if (
        connection.dialect.name != "postgresql"
        or not connection.in_transaction()
        or connection.in_nested_transaction()
    ):
        raise RuntimeError("NATIVE_SOURCE3_RR_INSTALL_TRANSACTION")
    # Every table/column is fixed here. Failure rolls back the caller's DDL UoW.
    for table, column in (
        *OWNER_TABLES_V3,
        *((t, "recommendation_request_id") for t in REQUEST_CHILD_TABLES_V3),
    ):
        connection.exec_driver_sql(f"SELECT {column} FROM {table} WHERE false")
    connection.exec_driver_sql(NATIVE_SOURCE3_RR_BARRIER_INSTALL_SQL_V3)
