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

A1B_TABLES = frozenset(
    {
        ("discovery", "bulk_operation"),
        ("discovery", "bulk_operation_item"),
        ("discovery", "candidate"),
    }
)
A1B_INDEXES = frozenset(
    {
        "ix_bulk_operation_item_candidate",
        "ix_bulk_operation_owner_time",
        "ix_discovery_candidate_owner_state",
    }
)
S1B_TABLES = frozenset(
    {
        ("account", "device_admission"),
        ("account", "device_admission_nonce"),
        ("account", "device_admission_exchange_receipt"),
        ("account", "device_admission_rate_window"),
        ("account", "device_admission_web_operation_receipt"),
        ("account", "device_key_block"),
        ("account", "trusted_device_key"),
        ("account", "trusted_device_reenrollment_challenge"),
    }
)
S1B_INDEXES = frozenset(
    {
        "ix_device_admission_cleanup",
        "ix_device_admission_poll_expiry",
        "uq_device_admission_locator",
        "uq_device_admission_pending_key",
        "ix_device_key_block_active",
        "ix_trusted_reenrollment_challenge_expiry",
        "ix_device_admission_receipt_expiry",
        "ix_device_admission_web_operation_receipt_expiry",
        "ix_device_admission_rate_window_expiry",
    }
)
S1C_TABLES = frozenset(
    {
        ("social", "friend_request"),
        ("social", "friendship"),
        ("social", "user_block"),
        ("social", "presence_settings"),
        ("social", "presence_heartbeat"),
        ("social", "friend_room_invitation"),
        ("social", "operation_receipt"),
        ("social", "rate_window"),
    }
)
S1C_INDEXES = frozenset(
    {
        "uq_social_pending_friend_request",
        "ix_social_friend_request_target",
        "ix_social_user_block_active",
        "ix_social_presence_fresh",
        "ix_social_room_invitation_target",
        "uq_social_pending_room_target",
        "ix_social_operation_receipt_expiry",
        "ix_social_rate_window_expiry",
    }
)
S1C_TRIGGERS = frozenset({"trg_social_account_retire"})
S2_TABLES = frozenset({("social", "profile_statistics_settings")})


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
    assert table_names == expected.tables | A1B_TABLES | S1B_TABLES | S1C_TABLES | S2_TABLES
    assert index_names == expected.indexes | A1B_INDEXES | S1B_INDEXES | S1C_INDEXES
    assert function_names == expected.functions
    assert trigger_names == expected.triggers | S1C_TRIGGERS
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
    database_harness.downgrade(database_name, "0019_m6_web_admin_runtime")
    with database_harness.connect(database_name) as migrated_connection:
        migrated = snapshot_schema(migrated_connection)
    with database_harness.connect(reference_database_name) as reference_connection:
        reference = snapshot_schema(reference_connection)

    assert migrated == reference
