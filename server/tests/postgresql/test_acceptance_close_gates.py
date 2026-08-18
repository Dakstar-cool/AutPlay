"""Final disposable-database and restricted-role close gates for P02."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from psycopg import Connection, sql

from .conftest import DatabaseHarness
from .schema_contract import MODULE_SCHEMAS, SchemaSnapshot, snapshot_schema

APPLICATION_SCHEMAS = frozenset((*MODULE_SCHEMAS, "app_private"))
REFERENCE_EXTENSIONS = frozenset(("pg_trgm", "vector"))
EMPTY_SNAPSHOT = SchemaSnapshot((), (), (), (), (), ())


def _application_schemas(connection: Connection[Any]) -> frozenset[str]:
    rows = connection.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s) ORDER BY nspname",
        (list(APPLICATION_SCHEMAS),),
    ).fetchall()
    return frozenset(str(row[0]) for row in rows)


def _reference_extensions(connection: Connection[Any]) -> dict[str, str]:
    return {
        str(name): str(version)
        for name, version in connection.execute(
            """
            SELECT extname, extversion
            FROM pg_extension
            WHERE extname = ANY(%s)
            ORDER BY extname
            """,
            (list(REFERENCE_EXTENSIONS),),
        ).fetchall()
    }


def _alembic_revision(connection: Connection[Any]) -> str | None:
    exists = connection.execute(
        "SELECT to_regclass('public.alembic_version') IS NOT NULL"
    ).fetchone()
    if exists is None or not bool(exists[0]):
        return None
    row = connection.execute("SELECT version_num FROM public.alembic_version").fetchone()
    return None if row is None else str(row[0])


def test_full_head_snapshot_is_restored_after_clean_downgrade_to_base(
    database_harness: DatabaseHarness,
    empty_database_name: str,
) -> None:
    """A full clean downgrade removes every P02 object before exact reconstruction."""

    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        first_head = snapshot_schema(connection)
        first_schemas = _application_schemas(connection)
        first_extensions = _reference_extensions(connection)
        first_revision = _alembic_revision(connection)

    assert len(first_head.tables) == 68
    assert len(first_head.explicit_indexes) == 64
    assert len(first_head.functions) == 19
    assert len(first_head.triggers) == 49
    assert first_schemas == APPLICATION_SCHEMAS
    assert first_extensions == {"pg_trgm": "1.6", "vector": "0.8.6"}
    assert first_revision == "0015_wave_runtime"

    database_harness.downgrade(empty_database_name, "base")
    with database_harness.connect(empty_database_name) as connection:
        assert snapshot_schema(connection) == EMPTY_SNAPSHOT
        assert _application_schemas(connection) == frozenset()
        assert _reference_extensions(connection) == {}
        assert _alembic_revision(connection) is None

    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        second_head = snapshot_schema(connection)
        second_schemas = _application_schemas(connection)
        second_extensions = _reference_extensions(connection)
        second_revision = _alembic_revision(connection)

    assert second_head == first_head
    assert second_schemas == first_schemas
    assert second_extensions == first_extensions
    assert second_revision == first_revision


def test_restricted_direct_dml_cannot_bypass_cross_owner_constraint(
    database_connection: Connection[Any],
) -> None:
    """A non-owner with only one INSERT grant still hits the named owner invariant."""

    role_name = f"autplay_p02_dml_{uuid.uuid4().hex[:16]}"
    role = sql.Identifier(role_name)
    first_user_id = database_connection.execute(
        """
        INSERT INTO account.user_account (display_name)
        VALUES (%s) RETURNING user_id
        """,
        (f"restricted-role-owner-{uuid.uuid4().hex}",),
    ).fetchone()
    second_user_id = database_connection.execute(
        """
        INSERT INTO account.user_account (display_name)
        VALUES (%s) RETURNING user_id
        """,
        (f"restricted-role-other-{uuid.uuid4().hex}",),
    ).fetchone()
    if first_user_id is None or second_user_id is None:
        raise AssertionError("restricted-role user fixtures returned no identifiers")

    event_id = uuid.uuid4()
    database_connection.execute(
        """
        INSERT INTO sync.sync_event (
            event_id, user_id, event_type, schema_version,
            aggregate_type, aggregate_id
        ) VALUES (%s, %s, 'P02_RESTRICTED_DML', 1, 'TEST', %s)
        """,
        (event_id, first_user_id[0], uuid.uuid4()),
    )

    role_created = False
    try:
        database_connection.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(role)
        )
        role_created = True
        database_connection.execute(sql.SQL("GRANT USAGE ON SCHEMA sync TO {}").format(role))
        database_connection.execute(
            sql.SQL("GRANT INSERT ON TABLE sync.tombstone TO {}").format(role)
        )

        role_attributes = database_connection.execute(
            """
            SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin,
                   rolreplication, rolbypassrls
            FROM pg_roles WHERE rolname = %s
            """,
            (role_name,),
        ).fetchone()
        assert role_attributes == (False, False, False, False, False, False)

        privileges = database_connection.execute(
            """
            SELECT has_schema_privilege(%s, 'sync', 'USAGE'),
                   has_schema_privilege(%s, 'sync', 'CREATE'),
                   has_table_privilege(%s, 'sync.tombstone', 'INSERT'),
                   has_table_privilege(%s, 'sync.tombstone', 'SELECT'),
                   pg_get_userbyid(c.relowner) = %s
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'sync' AND c.relname = 'tombstone'
            """,
            (role_name, role_name, role_name, role_name, role_name),
        ).fetchone()
        assert privileges == (True, False, True, False, False)

        with (
            pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_info,
            database_connection.transaction(),
        ):
            database_connection.execute(sql.SQL("SET LOCAL ROLE {}").format(role))
            database_connection.execute(
                """
                INSERT INTO sync.tombstone (
                    user_id, aggregate_type, aggregate_id, deleted_by_event_id,
                    deleted_at, retain_until
                ) VALUES (%s, 'TEST', %s, %s, now(), now() + interval '1 day')
                """,
                (second_user_id[0], uuid.uuid4(), event_id),
            )
        assert exc_info.value.diag.constraint_name == "fk_tombstone_event_owner"
    finally:
        if role_created:
            database_connection.execute("RESET ROLE")
            database_connection.execute(
                sql.SQL("REVOKE INSERT ON TABLE sync.tombstone FROM {}").format(role)
            )
            database_connection.execute(sql.SQL("REVOKE USAGE ON SCHEMA sync FROM {}").format(role))
            database_connection.execute(sql.SQL("DROP ROLE {}").format(role))

    assert database_connection.execute(
        "SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
        (role_name,),
    ).fetchone() == (True,)
