"""Migration preserves device liveness and refuses to discard durable worker wait evidence."""

from __future__ import annotations

from uuid import uuid4

import pytest
from psycopg.errors import CheckViolation
from sqlalchemy.exc import DBAPIError

from .conftest import DatabaseHarness


def test_upgrade_converts_worker_waits_and_downgrade_protects_wait_evidence(
    database_harness: DatabaseHarness,
    empty_database_name: str,
) -> None:
    database_harness.upgrade(empty_database_name, "0037_acquisition_authority")
    owner, device, job, worker_wait, device_wait = (uuid4() for _ in range(5))
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            "INSERT INTO account.user_account(user_id,display_name,role) VALUES (%s,'Wait','USER')",
            (owner,),
        )
        connection.execute(
            "INSERT INTO account.device(device_id,user_id,device_name,platform,app_version) "
            "VALUES (%s,%s,'Phone','ANDROID','fixture')",
            (device, owner),
        )
        connection.execute(
            "INSERT INTO jobs.job(job_id,job_type,schema_version,user_id,attempt_count) "
            "VALUES (%s,'music.internet.acquire',1,%s,1)",
            (job, owner),
        )
        for operation, worker in ((worker_wait, True), (device_wait, False)):
            target = uuid4() if worker else None
            connection.execute(
                "INSERT INTO account.resource_admission "
                "(operation_id,user_id,authority_generation,authority_kind,device_id,"
                "session_family_id,session_mode,job_id,job_worker_id,job_attempt,kind,"
                "resource_type,resource_id,target_id,request_sha256,state,generation,"
                "created_at,updated_at,enqueued_at,waiting_until,attachment_revision) "
                "VALUES (%s,%s,1,'DEVICE_SESSION',%s,%s,'V2',%s,%s,%s,%s,%s,%s,%s,%s,"
                "'WAITING',0,now(),now(),now(),now()+interval '45 seconds',0)",
                (
                    operation,
                    owner,
                    device,
                    uuid4(),
                    job if worker else None,
                    "old-worker" if worker else None,
                    1 if worker else None,
                    "TRANSFER" if worker else "PLAYBACK",
                    "INTERNET_ACQUISITION" if worker else "PLAY_INSTANCE",
                    target or uuid4(),
                    target,
                    b"d" * 32,
                ),
            )
        connection.commit()
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        assert connection.execute(
            "SELECT resource_wait_count,resource_waiting,resource_wake_until "
            "FROM jobs.job WHERE job_id=%s",
            (job,),
        ).fetchone() == (0, False, None)
        assert connection.execute(
            "SELECT waiting_until IS NULL FROM account.resource_admission WHERE operation_id=%s",
            (worker_wait,),
        ).fetchone() == (True,)
        assert connection.execute(
            "SELECT waiting_until IS NOT NULL FROM account.resource_admission "
            "WHERE operation_id=%s",
            (device_wait,),
        ).fetchone() == (True,)
        connection.commit()
        with pytest.raises(CheckViolation), connection.transaction():
            connection.execute(
                "UPDATE account.resource_admission SET waiting_until=NULL WHERE operation_id=%s",
                (device_wait,),
            )
        with pytest.raises(CheckViolation), connection.transaction():
            connection.execute("UPDATE jobs.job SET resource_wait_count=2 WHERE job_id=%s", (job,))
        with pytest.raises(CheckViolation), connection.transaction():
            connection.execute(
                "UPDATE jobs.job SET resource_wake_until=now() WHERE job_id=%s", (job,)
            )
    with pytest.raises(DBAPIError, match="Refusing to discard durable resource wait"):
        database_harness.downgrade(empty_database_name, "0037_acquisition_authority")
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            "UPDATE account.resource_admission SET state='EXPIRED',terminal_at=now() "
            "WHERE operation_id=%s",
            (worker_wait,),
        )
        connection.execute("UPDATE jobs.job SET resource_wait_count=1 WHERE job_id=%s", (job,))
        connection.commit()
    with pytest.raises(DBAPIError, match="Refusing to discard durable resource wait"):
        database_harness.downgrade(empty_database_name, "0037_acquisition_authority")
