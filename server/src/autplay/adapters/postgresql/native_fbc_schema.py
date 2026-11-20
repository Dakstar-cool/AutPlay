"""Atomic private F/B/C assembly after the deployed 0108 migration.

This explicit building block neither changes Alembic ancestry nor activates a
launcher. Installation leaves C quarantined; live independent reconciliation
and the complete source/process composition are still required.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from sqlalchemy import Connection, text

from . import native_request_v3_install as native
from . import native_training_schema as training
from . import sona_r1c_authority_schema as serving
from .native_source3_compatible_pins import COMPATIBLE_HEAD, COMPATIBLE_ROUTE_PINS, PURGE_READY_SHA256

PREDECESSOR = "0108_m11_native_owner"


def install(connection: Connection, predecessor_sha256: Mapping[str, str]) -> None:
    """Keep F, all B routes, privacy integration and C in one rollback boundary.

    Pins must come from reviewed predecessor source, not the target database.
    The caller owns the outer commit. Even a caught installation failure cannot
    leave a committable F/B prefix or replace existing legacy routes.
    """
    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise RuntimeError("native_fbc_install_transaction_required")
    with connection.begin_nested():
        revisions = list(connection.scalars(text("SELECT version_num FROM alembic_version")))
        if revisions == [COMPATIBLE_HEAD]:
            if predecessor_sha256 != COMPATIBLE_ROUTE_PINS:
                raise RuntimeError("native_source3_compatible_route_pins_required")
            ready = connection.scalar(
                text("SELECT pg_get_functiondef('account.verify_account_purge_ready(uuid)'::regprocedure)")
            )
            if not isinstance(ready, str) or sha256(ready.encode()).hexdigest() != PURGE_READY_SHA256:
                raise RuntimeError("native_source3_compatible_purge_ready_mismatch")
        elif revisions != [PREDECESSOR]:
            raise RuntimeError("native_fbc_predecessor_required")
        functions_before = list(connection.scalars(text("SELECT oid FROM pg_proc")))
        tables_before = list(connection.scalars(text("SELECT oid FROM pg_class")))
        training.install(connection)
        native.install(connection, predecessor_sha256)
        serving.install(connection)
        native._require_private_new_objects(connection, functions_before, tables_before)


__all__ = ("PREDECESSOR", "install")
