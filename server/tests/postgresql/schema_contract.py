"""Reference-DDL parsing and PostgreSQL catalog snapshots for P02 tests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection

MODULE_SCHEMAS = (
    "account",
    "audit",
    "catalog",
    "identity",
    "importing",
    "jobs",
    "library",
    "ml",
    "playlist",
    "sync",
    "vault",
)

EXPECTED_TABLE_COUNT = 57
EXPECTED_EXPLICIT_INDEX_COUNT = 53
EXPECTED_FUNCTION_COUNT = 13
EXPECTED_TRIGGER_COUNT = 40

TABLE_PATTERN = re.compile(r"(?m)^CREATE TABLE ([a-z_]+)\.([a-z_]+) \(")
INDEX_PATTERN = re.compile(r"(?m)^CREATE (?:UNIQUE )?INDEX ([a-z_]+)")
FUNCTION_PATTERN = re.compile(r"(?m)^CREATE FUNCTION app_private\.([a-z_]+)\(")
TRIGGER_PATTERN = re.compile(r"(?m)^CREATE (?:CONSTRAINT )?TRIGGER ([a-z_]+)")


@dataclass(frozen=True)
class ReferenceNames:
    """Exact object names extracted from the reviewed physical contract."""

    tables: frozenset[tuple[str, str]]
    indexes: frozenset[str]
    functions: frozenset[str]
    triggers: frozenset[str]


@dataclass(frozen=True)
class SchemaSnapshot:
    """Comparable structural snapshot of contract-owned PostgreSQL objects."""

    tables: tuple[tuple[Any, ...], ...]
    columns: tuple[tuple[Any, ...], ...]
    constraints: tuple[tuple[Any, ...], ...]
    explicit_indexes: tuple[tuple[Any, ...], ...]
    functions: tuple[tuple[Any, ...], ...]
    triggers: tuple[tuple[Any, ...], ...]


def parse_reference_names(reference_path: Path) -> ReferenceNames:
    """Parse only stable CREATE-object declarations from the normative SQL."""
    ddl = reference_path.read_text(encoding="utf-8")
    return ReferenceNames(
        tables=frozenset(TABLE_PATTERN.findall(ddl)),
        indexes=frozenset(INDEX_PATTERN.findall(ddl)),
        functions=frozenset(FUNCTION_PATTERN.findall(ddl)),
        triggers=frozenset(TRIGGER_PATTERN.findall(ddl)),
    )


def snapshot_schema(connection: Connection[Any]) -> SchemaSnapshot:
    """Read a deterministic catalog snapshot without extension/internal objects."""
    schemas = list(MODULE_SCHEMAS)
    tables = tuple(
        connection.execute(
            """
            SELECT n.nspname, c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p')
            ORDER BY n.nspname, c.relname
            """,
            (schemas,),
        ).fetchall()
    )
    columns = tuple(
        connection.execute(
            """
            SELECT n.nspname, c.relname, a.attnum, a.attname,
                   pg_catalog.format_type(a.atttypid, a.atttypmod), a.attnotnull,
                   pg_get_expr(ad.adbin, ad.adrelid)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE n.nspname = ANY(%s)
              AND c.relkind IN ('r', 'p')
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY n.nspname, c.relname, a.attnum
            """,
            (schemas,),
        ).fetchall()
    )
    constraints = tuple(
        connection.execute(
            """
            SELECT n.nspname, c.relname, con.conname, con.contype,
                   pg_get_constraintdef(con.oid, false)
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY(%s)
            ORDER BY n.nspname, c.relname, con.conname
            """,
            (schemas,),
        ).fetchall()
    )
    explicit_indexes = tuple(
        connection.execute(
            """
            SELECT n.nspname, c.relname, pg_get_indexdef(c.oid, 0, false)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_index i ON i.indexrelid = c.oid
            LEFT JOIN pg_constraint con ON con.conindid = c.oid
            WHERE n.nspname = ANY(%s)
              AND c.relkind = 'i'
              AND con.oid IS NULL
            ORDER BY n.nspname, c.relname
            """,
            (schemas,),
        ).fetchall()
    )
    functions = tuple(
        connection.execute(
            """
            SELECT p.proname, pg_get_function_identity_arguments(p.oid),
                   pg_get_function_result(p.oid), p.provolatile, p.prosecdef
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'app_private'
            ORDER BY p.proname, pg_get_function_identity_arguments(p.oid)
            """
        ).fetchall()
    )
    triggers = tuple(
        connection.execute(
            """
            SELECT n.nspname, c.relname, t.tgname,
                   pg_get_triggerdef(t.oid, false), t.tgdeferrable, t.tginitdeferred
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY(%s) AND NOT t.tgisinternal
            ORDER BY n.nspname, c.relname, t.tgname
            """,
            (schemas,),
        ).fetchall()
    )
    return SchemaSnapshot(
        tables=tables,
        columns=columns,
        constraints=constraints,
        explicit_indexes=explicit_indexes,
        functions=functions,
        triggers=triggers,
    )
