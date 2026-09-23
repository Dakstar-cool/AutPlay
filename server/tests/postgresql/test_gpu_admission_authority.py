"""Non-activating GPU admission has exact generation and release fences."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.gpu_admission import SqlAlchemyGpuAdmissionAuthority
from autplay.application.gpu_admission import require_gpu_batch
from autplay.domain.gpu_admission import (
    GpuAdmissionError,
    GpuLease,
    GpuReleaseProof,
    GpuReservationKind,
)
from autplay.ports.gpu_admission import GpuAdmissionAuthority

from .conftest import DatabaseHarness

GIB = 1024**3
FACE_MODEL = b"f" * 32
SONA_MODEL = b"s" * 32


class _GpuEvent(TypedDict):
    device_uuid: UUID
    kind: str
    event: str
    authority_generation: int
    reservation_generation: int
    holder_id: UUID
    model_hash: bytes
    requested: int
    lease_until: datetime


def _provision(connection: Connection[Any]) -> UUID:
    device_uuid = uuid4()
    connection.execute(
        """
        INSERT INTO ml.gpu_device_authority(
          device_uuid,reviewed_total_vram_bytes,safety_margin_bytes,
          reserved_sona_vram_bytes)
        VALUES(%s,%s,%s,%s)
        """,
        (device_uuid, 12 * GIB, 2 * GIB, 6 * GIB),
    )
    return device_uuid


def _event(
    connection: Connection[Any],
    *,
    device_uuid: UUID,
    kind: str,
    event: str,
    authority_generation: int,
    reservation_generation: int,
    holder_id: UUID,
    model_hash: bytes,
    requested: int,
    lease_until: datetime,
    measured: int = 0,
    cancellation_generation: int = 0,
    nvml_release: bool = False,
    process_exit: bool = False,
    session_unloaded: bool = False,
) -> None:
    connection.execute(
        """
        INSERT INTO ml.gpu_admission_receipt(
          device_uuid,reservation_kind,event_kind,authority_generation,
          reservation_generation,holder_id,model_identity_sha256,priority,
          requested_vram_bytes,measured_vram_bytes,cancellation_generation,
          lease_until,nvml_release_confirmed,process_exit_confirmed,session_unloaded)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            device_uuid,
            kind,
            event,
            authority_generation,
            reservation_generation,
            holder_id,
            model_hash,
            10 if kind == "FACE" else 100,
            requested,
            measured,
            cancellation_generation,
            lease_until,
            nvml_release,
            process_exit,
            session_unloaded,
        ),
    )


def _lease() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=20)


def test_face_cancel_requires_proven_release_before_sona_acquire(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    face_holder, sona_holder = uuid4(), uuid4()
    with database_harness.connect(empty_database_name) as connection:
        device_uuid = _provision(connection)
        _event(
            connection,
            device_uuid=device_uuid,
            kind="FACE",
            event="ACQUIRED",
            authority_generation=1,
            reservation_generation=1,
            holder_id=face_holder,
            model_hash=FACE_MODEL,
            requested=3 * GIB,
            lease_until=_lease(),
        )
        face_lease = connection.execute(
            "SELECT lease_until FROM ml.gpu_reservation_current "
            "WHERE device_uuid=%s AND reservation_kind='FACE'",
            (device_uuid,),
        ).fetchone()
        assert face_lease is not None
        with (
            pytest.raises(psycopg.Error, match="gpu_face_release_required"),
            connection.transaction(),
        ):
            _event(
                connection,
                device_uuid=device_uuid,
                kind="SONA",
                event="ACQUIRED",
                authority_generation=2,
                reservation_generation=1,
                holder_id=sona_holder,
                model_hash=SONA_MODEL,
                requested=6 * GIB,
                lease_until=_lease(),
            )
        _event(
            connection,
            device_uuid=device_uuid,
            kind="FACE",
            event="HEARTBEAT",
            authority_generation=2,
            reservation_generation=1,
            holder_id=face_holder,
            model_hash=FACE_MODEL,
            requested=3 * GIB,
            measured=GIB,
            lease_until=_lease(),
        )
        face_lease = connection.execute(
            "SELECT lease_until FROM ml.gpu_reservation_current "
            "WHERE device_uuid=%s AND reservation_kind='FACE'",
            (device_uuid,),
        ).fetchone()
        assert face_lease is not None
        _event(
            connection,
            device_uuid=device_uuid,
            kind="FACE",
            event="CANCEL_FACE",
            authority_generation=3,
            reservation_generation=1,
            holder_id=face_holder,
            model_hash=FACE_MODEL,
            requested=3 * GIB,
            measured=GIB,
            cancellation_generation=1,
            lease_until=face_lease[0],
        )
        with (
            pytest.raises(psycopg.Error, match="gpu_heartbeat_fenced"),
            connection.transaction(),
        ):
            _event(
                connection,
                device_uuid=device_uuid,
                kind="FACE",
                event="HEARTBEAT",
                authority_generation=4,
                reservation_generation=1,
                holder_id=face_holder,
                model_hash=FACE_MODEL,
                requested=3 * GIB,
                measured=GIB,
                lease_until=_lease(),
            )
        with (
            pytest.raises(psycopg.Error, match="gpu_release_proof_required"),
            connection.transaction(),
        ):
            _event(
                connection,
                device_uuid=device_uuid,
                kind="FACE",
                event="RELEASED",
                authority_generation=4,
                reservation_generation=1,
                holder_id=face_holder,
                model_hash=FACE_MODEL,
                requested=3 * GIB,
                measured=0,
                cancellation_generation=1,
                lease_until=face_lease[0],
            )
        _event(
            connection,
            device_uuid=device_uuid,
            kind="FACE",
            event="RELEASED",
            authority_generation=4,
            reservation_generation=1,
            holder_id=face_holder,
            model_hash=FACE_MODEL,
            requested=3 * GIB,
            cancellation_generation=1,
            lease_until=face_lease[0],
            nvml_release=True,
            session_unloaded=True,
        )
        _event(
            connection,
            device_uuid=device_uuid,
            kind="SONA",
            event="ACQUIRED",
            authority_generation=5,
            reservation_generation=1,
            holder_id=sona_holder,
            model_hash=SONA_MODEL,
            requested=6 * GIB,
            lease_until=_lease(),
        )
        assert connection.execute(
            "SELECT generation,face_cancellation_generation "
            "FROM ml.gpu_device_authority WHERE device_uuid=%s",
            (device_uuid,),
        ).fetchone() == (5, 1)
        assert connection.execute(
            "SELECT state,reservation_generation FROM ml.gpu_reservation_current "
            "WHERE device_uuid=%s AND reservation_kind='SONA'",
            (device_uuid,),
        ).fetchone() == ("ACTIVE", 1)
        with (
            pytest.raises(psycopg.Error, match="gpu_current_is_derived"),
            connection.transaction(),
        ):
            connection.execute(
                "UPDATE ml.gpu_device_authority SET generation=99 WHERE device_uuid=%s",
                (device_uuid,),
            )
        with (
            pytest.raises(psycopg.Error, match="ml_artifact_immutable"),
            connection.transaction(),
        ):
            connection.execute(
                "DELETE FROM ml.gpu_admission_receipt WHERE device_uuid=%s", (device_uuid,)
            )


def test_budget_and_stale_generation_fail_closed(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        device_uuid = _provision(connection)
        face_holder = uuid4()
        with (
            pytest.raises(psycopg.Error, match="gpu_face_budget_invalid"),
            connection.transaction(),
        ):
            _event(
                connection,
                device_uuid=device_uuid,
                kind="FACE",
                event="ACQUIRED",
                authority_generation=1,
                reservation_generation=1,
                holder_id=face_holder,
                model_hash=FACE_MODEL,
                requested=5 * GIB,
                lease_until=_lease(),
            )
        _event(
            connection,
            device_uuid=device_uuid,
            kind="FACE",
            event="ACQUIRED",
            authority_generation=1,
            reservation_generation=1,
            holder_id=face_holder,
            model_hash=FACE_MODEL,
            requested=4 * GIB,
            lease_until=_lease(),
        )
        with (
            pytest.raises(psycopg.Error, match="gpu_authority_generation_conflict"),
            connection.transaction(),
        ):
            _event(
                connection,
                device_uuid=device_uuid,
                kind="FACE",
                event="HEARTBEAT",
                authority_generation=1,
                reservation_generation=1,
                holder_id=face_holder,
                model_hash=FACE_MODEL,
                requested=4 * GIB,
                lease_until=_lease(),
            )
        connection.commit()


def test_expired_lease_keeps_slot_until_measured_release(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        device_uuid = _provision(connection)
        holder = uuid4()
        _event(
            connection,
            device_uuid=device_uuid,
            kind="FACE",
            event="ACQUIRED",
            authority_generation=1,
            reservation_generation=1,
            holder_id=holder,
            model_hash=FACE_MODEL,
            requested=2 * GIB,
            lease_until=datetime.now(UTC) + timedelta(seconds=1),
        )
        old_lease = connection.execute(
            "SELECT lease_until FROM ml.gpu_reservation_current "
            "WHERE device_uuid=%s AND reservation_kind='FACE'",
            (device_uuid,),
        ).fetchone()
        assert old_lease is not None
        connection.execute("SELECT pg_sleep(1.2)")
        with (
            pytest.raises(psycopg.Error, match="gpu_previous_holder_unreleased"),
            connection.transaction(),
        ):
            _event(
                connection,
                device_uuid=device_uuid,
                kind="FACE",
                event="ACQUIRED",
                authority_generation=2,
                reservation_generation=2,
                holder_id=uuid4(),
                model_hash=FACE_MODEL,
                requested=2 * GIB,
                lease_until=_lease(),
            )
        with (
            pytest.raises(psycopg.Error, match="gpu_release_proof_required"),
            connection.transaction(),
        ):
            _event(
                connection,
                device_uuid=device_uuid,
                kind="FACE",
                event="EXPIRED",
                authority_generation=2,
                reservation_generation=1,
                holder_id=holder,
                model_hash=FACE_MODEL,
                requested=2 * GIB,
                lease_until=old_lease[0],
            )
        _event(
            connection,
            device_uuid=device_uuid,
            kind="FACE",
            event="EXPIRED",
            authority_generation=2,
            reservation_generation=1,
            holder_id=holder,
            model_hash=FACE_MODEL,
            requested=2 * GIB,
            lease_until=old_lease[0],
            nvml_release=True,
            process_exit=True,
        )
        _event(
            connection,
            device_uuid=device_uuid,
            kind="FACE",
            event="ACQUIRED",
            authority_generation=3,
            reservation_generation=2,
            holder_id=uuid4(),
            model_hash=FACE_MODEL,
            requested=2 * GIB,
            cancellation_generation=0,
            lease_until=_lease(),
        )
        assert connection.execute(
            "SELECT reservation_generation,state FROM ml.gpu_reservation_current "
            "WHERE device_uuid=%s AND reservation_kind='FACE'",
            (device_uuid,),
        ).fetchone() == (2, "ACTIVE")


def test_competing_acquisitions_lock_device_generation(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        device_uuid = _provision(connection)
        connection.commit()
    holder = uuid4()
    event: _GpuEvent = dict(
        device_uuid=device_uuid,
        kind="FACE",
        event="ACQUIRED",
        authority_generation=1,
        reservation_generation=1,
        holder_id=holder,
        model_hash=FACE_MODEL,
        requested=2 * GIB,
        lease_until=_lease(),
    )
    with (
        database_harness.connect(empty_database_name) as winner,
        database_harness.connect(empty_database_name) as contender,
    ):
        _event(winner, **event)
        contender.execute("SET lock_timeout = '200ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            _event(contender, **event)
        contender.rollback()
        winner.commit()
        with pytest.raises(psycopg.Error, match="gpu_authority_generation_conflict"):
            _event(contender, **event)
        contender.rollback()
        assert contender.execute(
            "SELECT generation FROM ml.gpu_device_authority WHERE device_uuid=%s",
            (device_uuid,),
        ).fetchone() == (1,)


def test_gpu_policy_evidence_blocks_downgrade(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        _provision(connection)
        connection.commit()
    with pytest.raises(DBAPIError, match="refusing gpu admission downgrade with evidence"):
        database_harness.downgrade(empty_database_name, "0061_ml_artifact_authority")


def test_gpu_authority_tables_have_no_public_grants(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    names = {"gpu_device_authority", "gpu_reservation_current", "gpu_admission_receipt"}
    with database_harness.connect(empty_database_name) as connection:
        rows = connection.execute(
            """
            SELECT c.relname,
              EXISTS(SELECT 1 FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl
                WHERE acl.grantee=0) AS public_grant
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='ml' AND c.relname=ANY(%s::text[])
            """,
            (sorted(names),),
        ).fetchall()
    assert {row[0] for row in rows} == names
    assert all(not row[1] for row in rows)


def test_process_adapter_fences_cancelled_face_and_admits_sona_after_release(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        device_uuid = _provision(connection)
        connection.commit()
    engine = create_engine(database_harness.database_url(empty_database_name))
    try:
        authority = SqlAlchemyGpuAdmissionAuthority(sessionmaker(engine, class_=Session))
        face = authority.acquire(
            device_uuid=device_uuid,
            kind=GpuReservationKind.FACE,
            holder_id=uuid4(),
            model_identity_sha256=FACE_MODEL,
            requested_vram_bytes=3 * GIB,
        )
        assert authority.require_live(face).reservation_generation == 1
        face = authority.heartbeat(face, measured_vram_bytes=GIB)
        assert face.authority_generation == 2
        cancelled = authority.cancel_face(device_uuid)
        assert cancelled.cancellation_generation == 1
        with pytest.raises(GpuAdmissionError, match="gpu_lease_fenced"):
            authority.require_live(face)
        with pytest.raises(GpuAdmissionError, match="gpu_release_proof_invalid"):
            authority.release(
                face,
                GpuReleaseProof(GIB, datetime.now(UTC), False, True),
            )
        authority.release(
            face,
            GpuReleaseProof(0, datetime.now(UTC), False, True),
        )
        sona = authority.acquire(
            device_uuid=device_uuid,
            kind=GpuReservationKind.SONA,
            holder_id=uuid4(),
            model_identity_sha256=SONA_MODEL,
            requested_vram_bytes=6 * GIB,
        )
        assert authority.require_live(sona).authority_generation == 5
        with pytest.raises(GpuAdmissionError, match="gpu_admission_rejected"):
            authority.heartbeat(face, measured_vram_bytes=GIB)
    finally:
        engine.dispose()


def test_process_guard_stops_new_work_when_authority_is_unavailable() -> None:
    class BrokenAuthority:
        def require_live(self, _lease: object) -> None:
            raise OSError("database disconnected")

    class Process:
        stopped = False
        unloaded = False

        def stop_new_work(self) -> None:
            self.stopped = True

        def unload_or_exit_after_batch(self) -> None:
            self.unloaded = True

    process = Process()
    lease = GpuLease(
        device_uuid=uuid4(),
        kind=GpuReservationKind.FACE,
        holder_id=uuid4(),
        model_identity_sha256=FACE_MODEL,
        reservation_generation=1,
        authority_generation=1,
        cancellation_generation=0,
        requested_vram_bytes=GIB,
        lease_until=_lease(),
    )
    with pytest.raises(GpuAdmissionError, match="gpu_authority_unavailable"):
        require_gpu_batch(cast(GpuAdmissionAuthority, BrokenAuthority()), process, lease)
    assert process.stopped and process.unloaded
