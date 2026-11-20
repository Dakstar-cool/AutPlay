"""Atomic private Source3 SQL assembly on the deployed F/B/C predecessor.

The four exclusive B roles must already be provisioned. Installation owns no
commit and grants no runtime/issuer entry point. Physical admission, measured
profiles and the source/process composition remain separate requirements.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Connection, text

from . import native_b_counter_schema_v3 as counter
from . import native_b_source_current_metadata_schema_v3 as current_metadata
from . import native_b_source_fact_schema_v3 as facts
from . import native_b_source_index_schema_v3 as index
from . import native_b_source_membership_schema_v3 as membership
from . import native_b_source_metadata_schema_v3 as metadata
from . import native_b_source_outcome_schema_v3 as outcomes
from . import native_b_source_outcome_validation_schema_v3 as outcome_validation
from . import native_b_source_permission_schema_v3 as permission
from . import native_b_source_read_schema_v3 as reads
from . import native_b_source_registration_gate_schema_v3 as registration_gate
from . import native_b_source_registration_schema_v3 as registration
from . import native_b_source_terminal_schema_v3 as terminal
from . import native_fbc_schema as fbc
from . import native_source3_rr_barrier_schema_v3 as barrier
from . import native_source3_rr_current_schema_v3 as current

_ROLES = (
    "native_v3_b_catalog_owner",
    "native_v3_b_kernel",
    "native_v3_b_runtime",
    "native_v3_b_issuer",
)


def _require_roles(connection: Connection) -> None:
    rows = connection.execute(
        text(
            "SELECT rolname,rolcanlogin,rolsuper,rolinherit,rolcreaterole,"
            "rolcreatedb,rolreplication,rolbypassrls FROM pg_roles "
            "WHERE rolname=ANY(:roles) ORDER BY rolname"
        ),
        {"roles": list(_ROLES)},
    ).all()
    expected = sorted(
        (name, name in _ROLES[2:], False, False, False, False, False, False) for name in _ROLES
    )
    if [tuple(row) for row in rows] != expected:
        raise RuntimeError("native_source3_exclusive_roles_required")
    memberships = connection.execute(
        text(
            "SELECT r.rolname,m.rolname,a.inherit_option,a.set_option,a.admin_option "
            "FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid "
            "JOIN pg_roles m ON m.oid=a.member "
            "WHERE r.rolname=ANY(:roles) OR m.rolname=ANY(:roles)"
        ),
        {"roles": list(_ROLES)},
    ).all()
    if [tuple(row) for row in memberships] != [
        ("native_v3_b_runtime", "native_v3_b_catalog_owner", True, False, False)
    ]:
        raise RuntimeError("native_source3_exclusive_membership_required")


def install(connection: Connection, predecessor_sha256: Mapping[str, str]) -> None:
    """Install all SQL bodies atomically, with external entry points quarantined."""
    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise RuntimeError("native_source3_install_transaction_required")
    with connection.begin_nested():
        _require_roles(connection)
        fbc.install(connection, predecessor_sha256)
        previous = set(connection.scalars(text("SELECT oid FROM pg_proc")))
        previous_relations = set(connection.scalars(text("SELECT oid FROM pg_class")))
        for sql in (
            counter.INSTALL_SQL,
            counter.GRANTS_SQL,
            barrier.NATIVE_SOURCE3_RR_BARRIER_INSTALL_SQL_V3,
            current.NATIVE_SOURCE3_RR_CURRENT_INSTALL_SQL_V3,
            permission.INSTALL_SQL,
            reads.INSTALL_SQL,
            membership.INSTALL_SQL,
            registration.NATIVE_B_SOURCE_REGISTRATION_V3_INSTALL_SQL,
            registration_gate.INSTALL_SQL,
            metadata.INSTALL_SQL,
            current_metadata.INSTALL_SQL,
            facts.INSTALL_SQL,
            outcome_validation.INSTALL_SQL,
            outcomes.INSTALL_SQL,
            index.INSTALL_SQL,
            terminal.INSTALL_SQL,
        ):
            connection.exec_driver_sql(sql, execution_options={"no_parameters": True})
        # The candidate definitions include their intended explicit grants.
        # Compilation is not authority to expose them. Remove entry-point
        # grants before the outer transaction can publish the assembled graph.
        functions = connection.execute(
            text("SELECT p.oid,p.oid::regprocedure::text FROM pg_proc p ORDER BY p.oid")
        ).all()
        for oid, signature in functions:
            if oid not in previous:
                connection.exec_driver_sql(
                    f"REVOKE ALL ON FUNCTION {signature} "
                    "FROM PUBLIC,native_v3_b_runtime,native_v3_b_issuer"
                )
        unexpected = connection.execute(
            text(
                "SELECT p.oid,p.oid::regprocedure::text FROM pg_proc p "
                "CROSS JOIN LATERAL "
                "aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE a.grantee<>p.proowner AND a.grantee NOT IN "
                "('native_v3_b_kernel'::regrole,'native_v3_b_catalog_owner'::regrole) "
                "AND a.privilege_type='EXECUTE'"
            )
        ).all()
        if any(oid not in previous for oid, _ in unexpected):
            raise RuntimeError("native_source3_unexpected_function_acl")
        unexpected_relations = connection.execute(
            text(
                "SELECT c.oid FROM pg_class c CROSS JOIN LATERAL "
                "aclexplode(c.relacl) a WHERE a.grantee<>c.relowner AND a.grantee NOT IN "
                "('native_v3_b_kernel'::regrole,'native_v3_b_catalog_owner'::regrole) "
                "UNION ALL SELECT c.oid FROM pg_class c JOIN pg_attribute col "
                "ON col.attrelid=c.oid CROSS JOIN LATERAL aclexplode(col.attacl) a "
                "WHERE a.grantee<>c.relowner AND a.grantee NOT IN "
                "('native_v3_b_kernel'::regrole,'native_v3_b_catalog_owner'::regrole)"
            )
        ).scalars()
        if any(oid not in previous_relations for oid in unexpected_relations):
            raise RuntimeError("native_source3_unexpected_relation_acl")


__all__ = ("install",)
