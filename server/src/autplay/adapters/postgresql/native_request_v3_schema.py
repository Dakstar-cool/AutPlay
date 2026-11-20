"""Staged structural foundation for eight explicitly selected native3 tables.

Importing this module activates no models and performs no SQL. This is not the
complete 0104 authority graph: private writers, original/current assertions,
creation contexts, immutable generations and lifecycle dispatch remain pending.
The staged migration is deliberately outside Alembic's live versions directory.
"""

from __future__ import annotations

from sqlalchemy import Connection, Table, create_mock_engine, text
from sqlalchemy.schema import CreateIndex, CreateTable, DropIndex, DropTable, sort_tables

NATIVE_REQUEST_V3_STRUCTURAL_ONLY = True
NATIVE_REQUEST_V3_REQUIRED_SCHEMAS = ("ml", "app_private")
NATIVE_REQUEST_V3_REQUIRED_TABLES = (
    "account.device",
    "account.server_instance",
    "ml.recommendation_input_snapshot",
    "ml.recommendation_pipeline_version",
    "ml.recommendation_request",
)
NATIVE_REQUEST_V3_REQUIRED_FUNCTIONS = (
    "app_private.native_served_json_lp(jsonb,jsonb,text,integer)",
    "app_private.native_served_lp(text,bytea[])",
)
NATIVE_REQUEST_V3_TABLE_NAMES = (
    "native_original_capture_format",
    "native_served_request_hash_format",
    "native_original_capture_v3",
    "native_capture_source_event_v3",
    "native_capture_lineage_cursor_v3",
    "native_capture_replay_v3",
    "recommendation_request_root_v3",
    "native_atomic_capture_connection_v3",
)
NATIVE_REQUEST_V3_HELPER_ORDER = ("root_wire", "root_hash", "source_codec", "pipeline")


def _tables() -> tuple[Table, ...]:
    # Explicit activation occurs only when compiling/installing/removing this
    # staged foundation. Never add these rows to public model/metadata imports.
    from .models.native_request_v3 import NATIVE_REQUEST_V3_TABLES

    tables = NATIVE_REQUEST_V3_TABLES
    if (
        len(tables) != len(NATIVE_REQUEST_V3_TABLE_NAMES)
        or {table.name for table in tables} != set(NATIVE_REQUEST_V3_TABLE_NAMES)
        or any(table.schema != "ml" for table in tables)
    ):
        raise RuntimeError("native_v3_structural_inventory_mismatch")
    return tuple(sort_tables(sorted(tables, key=lambda table: table.name)))


def _helpers() -> tuple[tuple[str, str], ...]:
    # Lazy imports keep both schema constants and model registration out of the
    # normal startup path. Each owning module supplies its own reverse closure.
    from .native_original_capture_v3_schema import (
        NATIVE_ORIGINAL_CAPTURE_V3_DROP_SQL,
        NATIVE_ORIGINAL_CAPTURE_V3_INSTALL_SQL,
    )
    from .native_request_v3_hash_schema import (
        NATIVE_REQUEST_V3_HASH_DROP_SQL,
        NATIVE_REQUEST_V3_HASH_INSTALL_SQL,
    )
    from .native_request_v3_pipeline_schema import (
        NATIVE_REQUEST_V3_PIPELINE_DROP_SQL,
        NATIVE_REQUEST_V3_PIPELINE_INSTALL_SQL,
    )
    from .native_request_v3_wire_schema import (
        NATIVE_REQUEST_V3_WIRE_DROP_SQL,
        NATIVE_REQUEST_V3_WIRE_INSTALL_SQL,
    )

    return (
        (NATIVE_REQUEST_V3_WIRE_INSTALL_SQL, NATIVE_REQUEST_V3_WIRE_DROP_SQL),
        (NATIVE_REQUEST_V3_HASH_INSTALL_SQL, NATIVE_REQUEST_V3_HASH_DROP_SQL),
        (NATIVE_ORIGINAL_CAPTURE_V3_INSTALL_SQL, NATIVE_ORIGINAL_CAPTURE_V3_DROP_SQL),
        (NATIVE_REQUEST_V3_PIPELINE_INSTALL_SQL, NATIVE_REQUEST_V3_PIPELINE_DROP_SQL),
    )


def compile_table_ddl() -> tuple[str, ...]:
    """Compile only the selected new table/index DDL without any database IO."""
    target = create_mock_engine("postgresql+psycopg://", executor=None).dialect
    statements: list[str] = []
    for table in _tables():
        statements.append(str(CreateTable(table).compile(dialect=target)))
        statements.extend(
            str(CreateIndex(index).compile(dialect=target))
            for index in sorted(table.indexes, key=lambda item: str(item.name))
        )
        statements.append(f"REVOKE ALL ON ml.{table.name} FROM PUBLIC")
    return tuple(statements)


def _require_predecessor(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        raise RuntimeError("native_v3_structural_postgresql_required")
    for function, targets in (
        ("to_regnamespace", NATIVE_REQUEST_V3_REQUIRED_SCHEMAS),
        ("to_regclass", NATIVE_REQUEST_V3_REQUIRED_TABLES),
        ("to_regprocedure", NATIVE_REQUEST_V3_REQUIRED_FUNCTIONS),
    ):
        for target in targets:
            # Function names and inventories are installed literals; only the
            # catalog lookup value is a bind parameter.
            present = connection.scalar(
                text(f"SELECT {function}(:target) IS NOT NULL"), {"target": target}
            )
            if present is not True:
                raise RuntimeError("native_v3_structural_predecessor_missing")


def install(connection: Connection) -> None:
    """Install the structural subset in the caller's migration transaction only."""
    _require_predecessor(connection)
    for statement in compile_table_ddl():
        connection.exec_driver_sql(statement)
    for forward, _ in _helpers():
        # text() would treat :100/:false inside quoted JSON schema literals as
        # bind parameters. These are complete installed DDL strings, not bound
        # queries; send them through the driver's literal-SQL entry point.
        connection.exec_driver_sql(forward)


def remove(connection: Connection) -> None:
    """Refuse retained records; remove only this staged structural subset."""
    if connection.dialect.name != "postgresql":
        raise RuntimeError("native_v3_structural_postgresql_required")
    tables = _tables()
    relations = ", ".join(f"ml.{table.name}" for table in tables)
    # Lock the complete new branch before checking any row. Keep those locks
    # through the caller's commit so an insert cannot race an empty downgrade.
    connection.exec_driver_sql(f"LOCK TABLE {relations} IN ACCESS EXCLUSIVE MODE")
    for table in tables:
        if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM ml.{table.name})")):
            raise RuntimeError("native_v3_structural_records_retained")
    for _, reverse in reversed(_helpers()):
        connection.exec_driver_sql(reverse)
    for table in reversed(tables):
        for index in sorted(table.indexes, key=lambda item: str(item.name), reverse=True):
            statement = str(DropIndex(index).compile(dialect=connection.dialect))
            connection.exec_driver_sql(statement + " RESTRICT")
        statement = str(DropTable(table).compile(dialect=connection.dialect))
        connection.exec_driver_sql(statement + " RESTRICT")


__all__ = (
    "NATIVE_REQUEST_V3_HELPER_ORDER",
    "NATIVE_REQUEST_V3_REQUIRED_FUNCTIONS",
    "NATIVE_REQUEST_V3_REQUIRED_SCHEMAS",
    "NATIVE_REQUEST_V3_REQUIRED_TABLES",
    "NATIVE_REQUEST_V3_STRUCTURAL_ONLY",
    "NATIVE_REQUEST_V3_TABLE_NAMES",
    "compile_table_ddl",
    "install",
    "remove",
)
