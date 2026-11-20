"""Dedicated NOWAIT current SQL; keep the original RC function byte-for-byte.

This derives from the same real relational-current body and primary graph
assertion. It cannot install on import, mint filesystem consent or change ACLs.
Only an explicit caller-owned migration transaction may install this candidate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from .native_request_v3_current_schema import NATIVE_CURRENT_INSTALL_SQL_V3

if TYPE_CHECKING:
    from sqlalchemy import Connection
    from sqlalchemy.orm import Session

NATIVE_SOURCE3_RR_CURRENT_FUNCTION_V3: Final = (
    "app_private.assert_native_atomic_current_rr_v3(uuid,uuid,bigint)"
)


def _derive_sql() -> str:
    sql = NATIVE_CURRENT_INSTALL_SQL_V3
    changes = (
        ("assert_native_atomic_current_v3", "assert_native_atomic_current_rr_v3", 2),
        ("RETURNS void LANGUAGE plpgsql", "RETURNS timestamptz LANGUAGE plpgsql", 1),
        (
            "FOR SHARE OF b,f,k,p,c,r,sf,original,served,d,baseline;",
            "FOR SHARE OF b,f,k,p,c,r,sf,original,served,d,baseline NOWAIT;",
            1,
        ),
        ("FOR SHARE OF tc;", "FOR SHARE OF tc NOWAIT;", 1),
        ("  FOR SHARE;", "  FOR SHARE NOWAIT;", 1),
        (
            "EXCEPTION WHEN no_data_found OR too_many_rows THEN",
            "RETURN minimum_deadline;\nEXCEPTION WHEN no_data_found OR too_many_rows THEN",
            1,
        ),
    )
    for old, new, count in changes:
        if sql.count(old) != count:
            raise RuntimeError("NATIVE_SOURCE3_RR_CURRENT_BODY_DRIFT")
        sql = sql.replace(old, new)
    return sql


NATIVE_SOURCE3_RR_CURRENT_INSTALL_SQL_V3: Final = _derive_sql()


def audit_current_body_v3(session: Session) -> None:
    """Reject a missing/stub/definer current routine before source work."""
    from sqlalchemy import text

    row = session.execute(
        text("""
        SELECT p.prosrc,p.prosecdef,p.provolatile,p.proconfig,l.lanname,
               p.prorettype='pg_catalog.timestamptz'::regtype,
               p.prokind,p.proleakproof,p.prosupport::oid::bigint,p.pronargs
        FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE p.oid=to_regprocedure(:function)
    """),
        {"function": NATIVE_SOURCE3_RR_CURRENT_FUNCTION_V3},
    ).one_or_none()
    expected_body = NATIVE_SOURCE3_RR_CURRENT_INSTALL_SQL_V3.split("$native_current_v3$")[1]
    if row is None or tuple(row) != (
        expected_body,
        False,
        "v",
        ["search_path=pg_catalog"],
        "plpgsql",
        True,
        "f",
        False,
        0,
        3,
    ):
        raise RuntimeError("NATIVE_SOURCE3_RR_CURRENT_DEFINITION")


def install(connection: Connection) -> None:
    if (
        connection.dialect.name != "postgresql"
        or not connection.in_transaction()
        or connection.in_nested_transaction()
    ):
        raise RuntimeError("NATIVE_SOURCE3_RR_CURRENT_INSTALL_TRANSACTION")
    connection.exec_driver_sql(NATIVE_SOURCE3_RR_CURRENT_INSTALL_SQL_V3)
