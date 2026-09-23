"""PostgreSQL receipt writer and live-fence reader for GPU processes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.gpu_admission import (
    GPU_LEASE_TTL,
    GpuAdmissionError,
    GpuLease,
    GpuReleaseProof,
    GpuReservationKind,
)

_T = TypeVar("_T")


class SqlAlchemyGpuAdmissionAuthority:
    """One transaction per event; no CUDA session is loaded inside a DB transaction."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def acquire(
        self,
        *,
        device_uuid: UUID,
        kind: GpuReservationKind,
        holder_id: UUID,
        model_identity_sha256: bytes,
        requested_vram_bytes: int,
    ) -> GpuLease:
        if len(model_identity_sha256) != 32 or requested_vram_bytes < 1:
            raise GpuAdmissionError("gpu_request_invalid")

        def transaction(session: Session) -> GpuLease:
            authority = _lock_device(session, device_uuid)
            previous = _current(session, device_uuid, kind)
            now = authority["database_now"]
            _append(
                session,
                device_uuid=device_uuid,
                kind=kind,
                event="ACQUIRED",
                authority_generation=authority["generation"] + 1,
                reservation_generation=(
                    1 if previous is None else previous["reservation_generation"] + 1
                ),
                holder_id=holder_id,
                model_hash=model_identity_sha256,
                requested=requested_vram_bytes,
                measured=0,
                cancellation_generation=(
                    authority["face_cancellation_generation"]
                    if kind == GpuReservationKind.FACE
                    else 0
                ),
                lease_until=now + GPU_LEASE_TTL,
            )
            row = _current(session, device_uuid, kind)
            assert row is not None
            return _lease(row)

        return self._transaction(transaction)

    def require_live(self, lease: GpuLease) -> GpuLease:
        def transaction(session: Session) -> GpuLease:
            row = (
                session.execute(
                    text("""
                    SELECT c.*,a.face_cancellation_generation,clock_timestamp() AS database_now
                    FROM ml.gpu_reservation_current c
                    JOIN ml.gpu_device_authority a USING(device_uuid)
                    WHERE c.device_uuid=:device_uuid AND c.reservation_kind=:kind
                """),
                    {"device_uuid": lease.device_uuid, "kind": lease.kind.value},
                )
                .mappings()
                .first()
            )
            if (
                row is None
                or row["state"] != "ACTIVE"
                or row["lease_until"] <= row["database_now"]
                or row["holder_id"] != lease.holder_id
                or row["reservation_generation"] != lease.reservation_generation
                or bytes(row["model_identity_sha256"]) != lease.model_identity_sha256
                or (
                    lease.kind == GpuReservationKind.FACE
                    and (
                        row["cancellation_generation"] != lease.cancellation_generation
                        or row["face_cancellation_generation"] != lease.cancellation_generation
                    )
                )
            ):
                raise GpuAdmissionError("gpu_lease_fenced")
            return _lease(row)

        return self._transaction(transaction)

    def heartbeat(self, lease: GpuLease, *, measured_vram_bytes: int) -> GpuLease:
        if measured_vram_bytes < 0 or measured_vram_bytes > lease.requested_vram_bytes:
            raise GpuAdmissionError("gpu_measurement_invalid")

        def transaction(session: Session) -> GpuLease:
            authority = _lock_device(session, lease.device_uuid)
            current = _require_holder(session, lease)
            _append(
                session,
                device_uuid=lease.device_uuid,
                kind=lease.kind,
                event="HEARTBEAT",
                authority_generation=authority["generation"] + 1,
                reservation_generation=lease.reservation_generation,
                holder_id=lease.holder_id,
                model_hash=lease.model_identity_sha256,
                requested=lease.requested_vram_bytes,
                measured=measured_vram_bytes,
                cancellation_generation=current["cancellation_generation"],
                lease_until=authority["database_now"] + GPU_LEASE_TTL,
            )
            row = _current(session, lease.device_uuid, lease.kind)
            assert row is not None
            return _lease(row)

        return self._transaction(transaction)

    def cancel_face(self, device_uuid: UUID) -> GpuLease:
        def transaction(session: Session) -> GpuLease:
            authority = _lock_device(session, device_uuid)
            current = _current(session, device_uuid, GpuReservationKind.FACE)
            if current is None:
                raise GpuAdmissionError("gpu_face_holder_missing")
            _append(
                session,
                device_uuid=device_uuid,
                kind=GpuReservationKind.FACE,
                event="CANCEL_FACE",
                authority_generation=authority["generation"] + 1,
                reservation_generation=current["reservation_generation"],
                holder_id=current["holder_id"],
                model_hash=bytes(current["model_identity_sha256"]),
                requested=current["requested_vram_bytes"],
                measured=current["measured_vram_bytes"],
                cancellation_generation=authority["face_cancellation_generation"] + 1,
                lease_until=current["lease_until"],
            )
            row = _current(session, device_uuid, GpuReservationKind.FACE)
            assert row is not None
            return _lease(row)

        return self._transaction(transaction)

    def release(self, lease: GpuLease, proof: GpuReleaseProof) -> None:
        def transaction(session: Session) -> None:
            authority = _lock_device(session, lease.device_uuid)
            proof.require_fresh_release(authority["database_now"])
            current = _require_holder(session, lease)
            _append(
                session,
                device_uuid=lease.device_uuid,
                kind=lease.kind,
                event="RELEASED",
                authority_generation=authority["generation"] + 1,
                reservation_generation=lease.reservation_generation,
                holder_id=lease.holder_id,
                model_hash=lease.model_identity_sha256,
                requested=lease.requested_vram_bytes,
                measured=0,
                cancellation_generation=current["cancellation_generation"],
                lease_until=current["lease_until"],
                nvml_release=True,
                process_exit=proof.process_exit_confirmed,
                session_unloaded=proof.session_unloaded,
            )

        self._transaction(transaction)

    def _transaction(self, action: Callable[[Session], _T]) -> _T:
        try:
            with self._sessions.begin() as session:
                return action(session)
        except GpuAdmissionError:
            raise
        except SQLAlchemyError as error:
            if isinstance(error, DBAPIError) and error.connection_invalidated:
                code = "gpu_authority_unavailable"
            elif isinstance(error, DBAPIError) and getattr(error.orig, "sqlstate", None) == "40001":
                code = "gpu_generation_conflict"
            else:
                code = "gpu_admission_rejected"
            raise GpuAdmissionError(code) from error


def _lock_device(session: Session, device_uuid: UUID) -> Any:
    row = (
        session.execute(
            text("""
            SELECT generation,face_cancellation_generation,clock_timestamp() AS database_now
            FROM ml.gpu_device_authority WHERE device_uuid=:device_uuid FOR UPDATE
        """),
            {"device_uuid": device_uuid},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise GpuAdmissionError("gpu_device_unknown")
    return row


def _current(session: Session, device_uuid: UUID, kind: GpuReservationKind) -> Any | None:
    return (
        session.execute(
            text("""
            SELECT * FROM ml.gpu_reservation_current
            WHERE device_uuid=:device_uuid AND reservation_kind=:kind
        """),
            {"device_uuid": device_uuid, "kind": kind.value},
        )
        .mappings()
        .first()
    )


def _require_holder(session: Session, lease: GpuLease) -> Any:
    row = _current(session, lease.device_uuid, lease.kind)
    if (
        row is None
        or row["holder_id"] != lease.holder_id
        or row["reservation_generation"] != lease.reservation_generation
        or bytes(row["model_identity_sha256"]) != lease.model_identity_sha256
        or row["requested_vram_bytes"] != lease.requested_vram_bytes
    ):
        raise GpuAdmissionError("gpu_holder_generation_mismatch")
    return row


def _lease(row: Any) -> GpuLease:
    return GpuLease(
        device_uuid=row["device_uuid"],
        kind=GpuReservationKind(row["reservation_kind"]),
        holder_id=row["holder_id"],
        model_identity_sha256=bytes(row["model_identity_sha256"]),
        reservation_generation=row["reservation_generation"],
        authority_generation=row["authority_generation"],
        cancellation_generation=row["cancellation_generation"],
        requested_vram_bytes=row["requested_vram_bytes"],
        lease_until=row["lease_until"],
    )


def _append(
    session: Session,
    *,
    device_uuid: UUID,
    kind: GpuReservationKind,
    event: str,
    authority_generation: int,
    reservation_generation: int,
    holder_id: UUID,
    model_hash: bytes,
    requested: int,
    measured: int,
    cancellation_generation: int,
    lease_until: datetime,
    nvml_release: bool = False,
    process_exit: bool = False,
    session_unloaded: bool = False,
) -> None:
    session.execute(
        text("""
            INSERT INTO ml.gpu_admission_receipt(
              device_uuid,reservation_kind,event_kind,authority_generation,
              reservation_generation,holder_id,model_identity_sha256,priority,
              requested_vram_bytes,measured_vram_bytes,cancellation_generation,
              lease_until,nvml_release_confirmed,process_exit_confirmed,session_unloaded)
            VALUES(:device_uuid,:kind,:event,:authority_generation,:reservation_generation,
              :holder_id,:model_hash,:priority,:requested,:measured,:cancellation_generation,
              :lease_until,:nvml_release,:process_exit,:session_unloaded)
        """),
        {
            "device_uuid": device_uuid,
            "kind": kind.value,
            "event": event,
            "authority_generation": authority_generation,
            "reservation_generation": reservation_generation,
            "holder_id": holder_id,
            "model_hash": model_hash,
            "priority": 10 if kind == GpuReservationKind.FACE else 100,
            "requested": requested,
            "measured": measured,
            "cancellation_generation": cancellation_generation,
            "lease_until": lease_until,
            "nvml_release": nvml_release,
            "process_exit": process_exit,
            "session_unloaded": session_unloaded,
        },
    )
