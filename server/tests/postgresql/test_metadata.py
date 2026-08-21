"""Structural tests for the complete typed PostgreSQL metadata mapping."""

from __future__ import annotations

import hashlib

from autplay.adapters.postgresql.metadata import (
    EXPECTED_COLUMN_COUNT,
    EXPECTED_EXPLICIT_INDEX_NAMES,
    EXPECTED_SCHEMAS,
    EXPECTED_TABLE_COLUMN_COUNTS,
    EXPECTED_TABLE_KEYS,
    MAPPED_ROWS,
    metadata,
)
from pgvector.sqlalchemy import VECTOR
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable


def test_complete_table_and_column_inventory() -> None:
    """Every reference table and column is present exactly once."""
    assert set(metadata.tables) == set(EXPECTED_TABLE_KEYS)
    assert len(metadata.tables) == 86
    assert sum(len(table.columns) for table in metadata.tables.values()) == EXPECTED_COLUMN_COUNT
    assert EXPECTED_COLUMN_COUNT == 946
    assert {
        key: len(table.columns) for key, table in metadata.tables.items()
    } == EXPECTED_TABLE_COLUMN_COUNTS
    assert {table.schema for table in metadata.tables.values()} == EXPECTED_SCHEMAS


def test_all_rows_are_typed_mappers_without_relationship_behavior() -> None:
    """Mappings are storage rows, not a second domain model."""
    configure_mappers()
    assert len(MAPPED_ROWS) == 86
    assert {str(row.__table__) for row in MAPPED_ROWS} == set(EXPECTED_TABLE_KEYS)
    assert all(not list(sa_inspect(row).relationships) for row in MAPPED_ROWS)


def test_constraint_and_foreign_key_names_are_explicit_and_qualified() -> None:
    """Metadata retains deterministic names and cross-schema targets."""
    constraints = [
        constraint for table in metadata.tables.values() for constraint in table.constraints
    ]
    assert all(constraint.name for constraint in constraints)
    foreign_keys = [
        constraint for constraint in constraints if isinstance(constraint, ForeignKeyConstraint)
    ]
    assert all(
        len(element.target_fullname.split(".")) == 3
        for constraint in foreign_keys
        for element in constraint.elements
    )
    assert {constraint.name for constraint in foreign_keys if constraint.use_alter} == {
        "fk_import_entry_current_match_decision",
        "fk_match_decision_reviewed_evidence",
        "fk_user_track_ref_current_match_decision",
    }


def test_explicit_index_inventory_and_no_python_defaults() -> None:
    """All reference indexes are mapped and defaults remain database-owned."""
    indexes = {index.name for table in metadata.tables.values() for index in table.indexes}
    assert indexes == EXPECTED_EXPLICIT_INDEX_NAMES
    assert len(indexes) == 79
    assert all(
        column.default is None for table in metadata.tables.values() for column in table.columns
    )


def test_unbounded_vector_columns_match_reference_contract() -> None:
    """Vector dimension stays registry-controlled rather than fixed in metadata."""
    vector_columns = [
        column
        for table in metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, VECTOR)
    ]
    assert {(column.table.key, column.name) for column in vector_columns} == {
        ("ml.recording_embedding", "embedding"),
        ("ml.taste_cluster", "centroid"),
    }
    assert all(getattr(column.type, "dim", object()) is None for column in vector_columns)


def test_complete_mapping_definition_fingerprint() -> None:
    """The full typed table/constraint/index definition stays byte-for-byte stable."""
    dialect = PGDialect()  # type: ignore[no-untyped-call]
    statements = [
        " ".join(str(CreateTable(table).compile(dialect=dialect)).split())
        for table in sorted(metadata.tables.values(), key=lambda item: item.key)
    ]
    statements.extend(
        " ".join(str(AddConstraint(constraint).compile(dialect=dialect)).split())
        for table in sorted(metadata.tables.values(), key=lambda item: item.key)
        for constraint in sorted(
            (
                item
                for item in table.constraints
                if isinstance(item, ForeignKeyConstraint) and item.use_alter
            ),
            key=lambda item: str(item.name),
        )
    )
    statements.extend(
        " ".join(str(CreateIndex(index).compile(dialect=dialect)).split())
        for table in sorted(metadata.tables.values(), key=lambda item: item.key)
        for index in sorted(table.indexes, key=lambda item: str(item.name))
    )
    fingerprint = hashlib.sha256("\n".join(statements).encode()).hexdigest()

    assert fingerprint == "c3386c9fb1aa097f5d5db167bbe8ae019fd7ebe792428a7324d8f1dbe9209da4"
