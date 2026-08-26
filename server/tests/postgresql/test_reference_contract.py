"""Static inventory assertions for the reviewed reference DDL."""

from __future__ import annotations

from .conftest import REFERENCE_DDL_PATH
from .schema_contract import (
    REFERENCE_P02_EXPLICIT_INDEX_COUNT,
    REFERENCE_P02_FUNCTION_COUNT,
    REFERENCE_P02_TABLE_COUNT,
    REFERENCE_P02_TRIGGER_COUNT,
    parse_reference_names,
)


def test_reference_ddl_declares_exact_p02_inventory() -> None:
    """Keep the executable P02 inventory gate tied to the normative SQL."""
    names = parse_reference_names(REFERENCE_DDL_PATH)

    assert len(names.tables) == REFERENCE_P02_TABLE_COUNT
    assert len(names.indexes) == REFERENCE_P02_EXPLICIT_INDEX_COUNT
    assert len(names.functions) == REFERENCE_P02_FUNCTION_COUNT
    assert len(names.triggers) == REFERENCE_P02_TRIGGER_COUNT
    assert ("importing", "match_candidate") not in names.tables
    assert {
        ("identity", "matcher_release"),
        ("identity", "calibrator_release"),
        ("identity", "threshold_set"),
        ("identity", "match_policy_activation"),
        ("identity", "match_decision"),
        ("identity", "match_candidate_evidence"),
    } <= names.tables
