"""Explicit, atomic assembly of the Source3 schema and legacy format dispatch.

This is a migration building block, not a startup hook or a grant of current
authority. The caller supplies reviewed predecessor body pins; they must never
be learned from the database being upgraded. F/G admission and independent
consent fences remain mandatory for every current operation.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Connection, text


def configure_original_storage(connection: Connection) -> None:
    """Keep freshly written origin columns measurable without decompressing them."""
    for table, columns in (
        ("recommendation_pipeline_version", ("manifest",)),
        ("recommendation_input_snapshot", ("snapshot_document",)),
        ("recommendation_temporal_snapshot", ("snapshot_document",)),
        ("recommendation_request", ("request_document",)),
        (
            "recommendation_item",
            (
                "contributions",
                "availability_snapshot",
                "item_provenance",
                "candidate_sources",
                "reason_codes",
            ),
        ),
        ("recommendation_served_item", ("contributions", "reasons")),
    ):
        clauses = ", ".join(f"ALTER COLUMN {name} SET STORAGE EXTERNAL" for name in columns)
        connection.exec_driver_sql(f"ALTER TABLE ml.{table} {clauses}")

    # An ALTER COLUMN storage policy applies to future writes. Re-encode the
    # single verified historical manifest in place so a compressed
    # predecessor can use the same bounded per-request reader. The immutable
    # registry trigger remains active. No caller-supplied JSON is serialized.
    from .native_request_v3_pipeline_schema import (
        NATIVE_REQUEST_V3_BASELINE_MANIFEST_BYTES,
        NATIVE_REQUEST_V3_BASELINE_MANIFEST_SHA256,
        NATIVE_REQUEST_V3_PIPELINE_MAX_STORED_BYTES,
        P11_V2_MANIFEST_BYTES,
        P11_V2_MANIFEST_SHA256,
    )

    connection.execute(
        text(
            """
            UPDATE ml.recommendation_pipeline_version
            SET manifest=jsonb_set(manifest,'{pipeline_key}',manifest->'pipeline_key',false)
            WHERE pipeline_key='cpu-baseline' AND version=:version
              AND manifest_sha256=:sha AND pg_column_compression(manifest)='pglz'
              AND CASE WHEN pg_column_size(manifest)<=:limit
                       THEN manifest=CAST(:fixed AS jsonb) ELSE false END
            """
        ),
        [
            {
                "version": version,
                "fixed": raw.decode("ascii"),
                "sha": bytes.fromhex(digest),
                "limit": NATIVE_REQUEST_V3_PIPELINE_MAX_STORED_BYTES,
            }
            for version, raw, digest in (
                ("1", NATIVE_REQUEST_V3_BASELINE_MANIFEST_BYTES, NATIVE_REQUEST_V3_BASELINE_MANIFEST_SHA256),
                ("2", P11_V2_MANIFEST_BYTES, P11_V2_MANIFEST_SHA256),
            )
        ],
    )


def install(connection: Connection, predecessor_sha256: Mapping[str, str]) -> None:
    """Install all B helpers and dispatch in one caller-owned transaction.

    A savepoint also removes preparatory DDL if the caller catches a Python
    catalog/pin validation error and later commits its transaction. No prefix
    can therefore survive a refused cutover.
    """
    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise RuntimeError("native_v3_install_transaction_required")

    from . import native_request_v3_dispatch_schema as dispatch
    from . import native_request_v3_privacy_schema as privacy
    from .native_request_v3_writer_schema import NATIVE_REQUEST_V3_WRITER_INSTALL_SQL
    from .native_p11_catalogue_origin import CATALOGUE_ORIGIN_SQL

    with connection.begin_nested():
        configure_original_storage(connection)
        functions_before = list(connection.scalars(text("SELECT oid FROM pg_proc")))
        tables_before = list(connection.scalars(text("SELECT oid FROM pg_class")))
        _install_graph(connection)
        connection.exec_driver_sql(CATALOGUE_ORIGIN_SQL)
        _require_private_new_objects(connection, functions_before, tables_before)
        dispatch.install(
            connection,
            {
                key: value
                for key, value in predecessor_sha256.items()
                if key != privacy.PURGE_SIGNATURE
            },
        )
        privacy.install(connection, predecessor_sha256.get(privacy.PURGE_SIGNATURE, ""))
        connection.exec_driver_sql(
            NATIVE_REQUEST_V3_WRITER_INSTALL_SQL, execution_options={"no_parameters": True}
        )
        _require_private_new_objects(connection, functions_before, tables_before)


def _require_private_new_objects(
    connection: Connection, functions_before: list[int], tables_before: list[int]
) -> None:
    # REVOKE FROM PUBLIC does not remove named-role default privileges.
    # Audit every newly created helper/relation, including the protected
    # privacy phase, before this savepoint can become committable.
    for query, previous in (
        (
            "SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace CROSS JOIN LATERAL "
            "aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE n.nspname='app_private' AND p.oid<>ALL(CAST(:previous AS oid[])) "
            "AND a.grantee<>p.proowner)",
            functions_before,
        ),
        (
            "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace CROSS JOIN LATERAL "
            "aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a "
            "WHERE n.nspname IN ('ml','app_private') AND c.relkind='r' "
            "AND c.oid<>ALL(CAST(:previous AS oid[])) AND a.grantee<>c.relowner)",
            tables_before,
        ),
    ):
        if connection.scalar(text(query), {"previous": previous}) is not False:
            raise RuntimeError("native_v3_install_nonowner_default_privileges")


def _install_graph(connection: Connection) -> None:
    """Prepare the private dependency graph inside install's savepoint."""
    from . import native_request_v3_schema as structural
    from .native_normalized_scalar_v3_schema import NATIVE_NORMALIZED_SCALAR_V3_INSTALL_SQL
    from .native_offline_pack_v3_schema import PACK_V3_DEFINITIONS_SQL
    from .native_original_facts_v3_schema import NATIVE_ORIGINAL_FACTS_V3_INSTALL_SQL
    from .native_original_payloads_v3_schema import NATIVE_ORIGINAL_PAYLOADS_V3_INSTALL_SQL
    from .native_original_profile_v3_schema import NATIVE_ORIGINAL_PROFILE_V3_INSTALL_SQL
    from .native_profile_binary64_v3_schema import NATIVE_PROFILE_BINARY64_V3_INSTALL_SQL
    from .native_request_v3_compiler_schema import NATIVE_REQUEST_V3_COMPILER_INSTALL_SQL
    from .native_request_v3_context_schema import NATIVE_REQUEST_V3_CONTEXT_INSTALL_SQL
    from .native_request_v3_current_schema import NATIVE_CURRENT_INSTALL_SQL_V3
    from .native_request_v3_graph_schema import NATIVE_REQUEST_V3_GRAPH_INSTALL_SQL
    from .native_request_v3_lifecycle_schema import NATIVE_REQUEST_V3_LIFECYCLE_INSTALL_SQL
    from .native_request_v3_privacy_schema import FINAL_SCOPE_SQL
    from .native_request_v3_writer_schema import (
        NATIVE_ORIGINAL_DOCUMENT_V3_INSTALL_SQL,
        NATIVE_REQUEST_V3_ITEM_ROWS_INSTALL_SQL,
    )
    from .native_served_lifecycle_v3_schema import NATIVE_SERVED_LIFECYCLE_V3_INSTALL_SQL
    from .native_served_retirement_v3_schema import NATIVE_SERVED_RETIREMENT_V3_INSTALL_SQL
    from .native_source_descriptors_v3_schema import NATIVE_SOURCE_DESCRIPTORS_V3_INSTALL_SQL

    blocks = (
        NATIVE_REQUEST_V3_COMPILER_INSTALL_SQL,
        NATIVE_PROFILE_BINARY64_V3_INSTALL_SQL,
        NATIVE_SOURCE_DESCRIPTORS_V3_INSTALL_SQL,
        NATIVE_NORMALIZED_SCALAR_V3_INSTALL_SQL,
        NATIVE_ORIGINAL_DOCUMENT_V3_INSTALL_SQL,
        NATIVE_ORIGINAL_PROFILE_V3_INSTALL_SQL,
        NATIVE_ORIGINAL_FACTS_V3_INSTALL_SQL,
        NATIVE_REQUEST_V3_ITEM_ROWS_INSTALL_SQL,
        NATIVE_ORIGINAL_PAYLOADS_V3_INSTALL_SQL,
        NATIVE_REQUEST_V3_GRAPH_INSTALL_SQL,
        NATIVE_CURRENT_INSTALL_SQL_V3,
        NATIVE_REQUEST_V3_CONTEXT_INSTALL_SQL,
        NATIVE_REQUEST_V3_LIFECYCLE_INSTALL_SQL,
        FINAL_SCOPE_SQL,
        NATIVE_SERVED_RETIREMENT_V3_INSTALL_SQL,
        NATIVE_SERVED_LIFECYCLE_V3_INSTALL_SQL,
        PACK_V3_DEFINITIONS_SQL,
    )
    structural.install(connection)
    connection.exec_driver_sql(
        "ALTER TABLE ml.native_capture_replay_v3 "
        "ALTER COLUMN baseline_document SET STORAGE EXTERNAL"
    )
    for statement in blocks:
        connection.exec_driver_sql(statement, execution_options={"no_parameters": True})


__all__ = ("install",)
