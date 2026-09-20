"""Real database proof for quota bounds, owner/fence integrity and migration evidence."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from psycopg import Connection
from psycopg.errors import CheckViolation, ForeignKeyViolation
from sqlalchemy.exc import DBAPIError

from .conftest import DatabaseHarness


def test_quota_seed_requires_measured_budget_and_rejects_partial_or_excessive_values(
    database_connection: Connection[Any],
) -> None:
    connection = database_connection
    assert connection.execute(
        "SELECT revision,default_devices,default_playbacks,default_transfers,global_playbacks "
        "FROM account.resource_quota_policy"
    ).fetchone() == (1, 5, 2, 2, None)
    connection.commit()
    for update in (
        "default_devices=0",
        "global_playbacks=2",
        "global_playbacks=3,global_transfers=1,playback_ceiling=2,transfer_ceiling=1,"
        "budget_evidence='synthetic-test'",
    ):
        with pytest.raises(CheckViolation), connection.transaction():
            connection.execute("UPDATE account.resource_quota_policy SET " + update)


def test_permit_cannot_reference_another_activation_and_device_owner_is_enforced(
    database_connection: Connection[Any],
) -> None:
    connection = database_connection
    user, other, device, operation, activation = (uuid4() for _ in range(5))
    connection.execute(
        "INSERT INTO account.user_account (user_id,display_name,role) "
        "VALUES (%s,'One','USER'),(%s,'Two','USER')",
        (user, other),
    )
    connection.execute(
        "INSERT INTO account.device (device_id,user_id,device_name,platform,app_version) "
        "VALUES (%s,%s,'Phone','ANDROID','fixture')",
        (device, user),
    )
    connection.execute(
        "INSERT INTO account.resource_admission "
        "(operation_id,user_id,authority_generation,authority_kind,device_id,session_family_id,"
        "session_mode,kind,resource_type,resource_id,request_sha256,state,activation_id,generation,"
        "created_at,updated_at,enqueued_at,lease_until,claim_until,attachment_revision) VALUES "
        "(%s,%s,1,'DEVICE_SESSION',%s,%s,'V2','PLAYBACK','PLAY_INSTANCE',%s,%s,'ACTIVE',%s,1,"
        "now(),now(),now(),now()+interval '30 seconds',now()+interval '15 seconds',0)",
        (operation, user, device, uuid4(), uuid4(), b"x" * 32, activation),
    )
    connection.commit()
    with pytest.raises(ForeignKeyViolation), connection.transaction():
        connection.execute(
            "UPDATE account.resource_admission SET user_id=%s WHERE operation_id=%s",
            (other, operation),
        )
    with pytest.raises(ForeignKeyViolation), connection.transaction():
        connection.execute(
            "INSERT INTO account.resource_io_permit "
            "(permit_id,operation_id,activation_id,generation,target_id,opened_at,renewed_at,"
            "expires_at) VALUES (%s,%s,%s,2,%s,now(),now(),now()+interval '5 seconds')",
            (uuid4(), operation, activation, uuid4()),
        )
    with pytest.raises(CheckViolation), connection.transaction():
        connection.execute(
            "INSERT INTO account.resource_io_permit "
            "(permit_id,operation_id,activation_id,generation,target_id,opened_at,renewed_at,"
            "expires_at) VALUES (%s,%s,%s,1,%s,now(),now(),now()+interval '60 seconds')",
            (uuid4(), operation, activation, uuid4()),
        )


def test_edited_policy_blocks_downgrade_even_with_no_admissions(
    database_harness: DatabaseHarness,
    empty_database_name: str,
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        connection.execute("UPDATE account.resource_quota_policy SET revision=2,default_devices=3")
        connection.commit()
    with pytest.raises(DBAPIError, match="Refusing to discard resource quota"):
        database_harness.downgrade(empty_database_name, "0034_self_device_pairing")
    with database_harness.connect(empty_database_name) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0060_local_bridge_authority",
        )
        assert connection.execute(
            "SELECT default_devices FROM account.resource_quota_policy"
        ).fetchone() == (3,)


def test_resource_admission_migration_matches_runtime_metadata(
    database_harness: DatabaseHarness,
    empty_database_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_harness.upgrade(empty_database_name)
    monkeypatch.setenv("AUTPLAY_DATABASE_URL", database_harness.database_url(empty_database_name))
    command.check(database_harness.alembic_config(empty_database_name))
