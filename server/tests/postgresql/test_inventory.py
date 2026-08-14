"""Exact PostgreSQL object inventory and reference-drift tests."""

from __future__ import annotations

from .conftest import REFERENCE_DDL_PATH, DatabaseHarness
from .schema_contract import (
    EXPECTED_EXPLICIT_INDEX_COUNT,
    EXPECTED_FUNCTION_COUNT,
    EXPECTED_TABLE_COUNT,
    EXPECTED_TRIGGER_COUNT,
    parse_reference_names,
    snapshot_schema,
)


def test_migrated_database_has_exact_named_inventory(
    database_harness: DatabaseHarness, database_name: str
) -> None:
    """Compare exact migration object names with the independent reference SQL."""
    expected = parse_reference_names(REFERENCE_DDL_PATH)
    with database_harness.connect(database_name) as connection:
        snapshot = snapshot_schema(connection)
        table_names = {(str(row[0]), str(row[1])) for row in snapshot.tables}
        index_names = {str(row[1]) for row in snapshot.explicit_indexes}
        function_names = {str(row[0]) for row in snapshot.functions}
        trigger_names = {str(row[2]) for row in snapshot.triggers}
        activation_row = connection.execute(
            "SELECT count(*) FROM identity.match_policy_activation"
        ).fetchone()
        if activation_row is None or not isinstance(activation_row[0], int):
            raise AssertionError("activation count query returned no integer")
        activation_count = activation_row[0]
        extensions = dict(
            connection.execute(
                """
                SELECT extname, extversion FROM pg_extension
                WHERE extname IN ('pg_trgm', 'vector')
                """
            ).fetchall()
        )
        ann_row = connection.execute(
            """
            SELECT count(*)
            FROM pg_class index_class
            JOIN pg_index i ON i.indexrelid = index_class.oid
            JOIN pg_am am ON am.oid = index_class.relam
            WHERE am.amname IN ('hnsw', 'ivfflat')
            """
        ).fetchone()
        if ann_row is None or not isinstance(ann_row[0], int):
            raise AssertionError("ANN index count query returned no integer")
        ann_count = ann_row[0]

    assert len(table_names) == EXPECTED_TABLE_COUNT
    assert len(index_names) == EXPECTED_EXPLICIT_INDEX_COUNT
    assert len(function_names) == EXPECTED_FUNCTION_COUNT
    assert len(trigger_names) == EXPECTED_TRIGGER_COUNT
    assert table_names == expected.tables
    assert index_names == expected.indexes
    assert function_names == expected.functions
    assert trigger_names == expected.triggers
    assert ("importing", "match_candidate") not in table_names
    assert activation_count == 0
    assert extensions == {"pg_trgm": "1.6", "vector": "0.8.6"}
    assert ann_count == 0


def test_migration_schema_has_no_unexplained_reference_ddl_drift(
    database_harness: DatabaseHarness,
    database_name: str,
    reference_database_name: str,
) -> None:
    """Compare catalog structure from migrations and normative reference DDL."""
    with database_harness.connect(database_name) as migrated_connection:
        migrated = snapshot_schema(migrated_connection)
    with database_harness.connect(reference_database_name) as reference_connection:
        reference = snapshot_schema(reference_connection)

    assert migrated == reference
