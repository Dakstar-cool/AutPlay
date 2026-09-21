"""Orphan claims and all CAS preparations serialize on the same digest lock."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.orphan_object_retirement import OrphanObjectClaim, OrphanObjectOutcome
from autplay.domain.provider_maintenance import MaintenanceAction
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, StorageOperationError

from .models.orphan_object_claim import OrphanObjectClaimRow
from .models.provider_maintenance import ProviderMaintenanceRow
from .models.vault import VaultObjectRow, VaultReplicaRow
from .resource_limits import lock_resource_admission


def lock_cas_digest(session: Session, digest: Sha256Digest) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": int.from_bytes(digest.value[:8], "big", signed=True)},
    )


def require_cas_unclaimed(session: Session, digest: Sha256Digest) -> None:
    """Call after the digest lock and hold it until publication intent commits."""
    if session.scalar(
        select(
            exists().where(
                OrphanObjectClaimRow.storage_key == digest.hex,
                OrphanObjectClaimRow.completed_at.is_(None),
            )
        )
    ):
        raise StorageOperationError()


def object_key_is_registered(session: Session, storage_key: str) -> bool:
    return bool(
        session.scalar(select(exists().where(VaultObjectRow.sha256 == bytes.fromhex(storage_key))))
        or session.scalar(
            select(
                exists().where(
                    VaultReplicaRow.storage_backend == "LOCAL_FILESYSTEM",
                    VaultReplicaRow.storage_key == storage_key,
                )
            )
        )
    )


class PostgresOrphanObjectRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise ResourceAdmissionError("orphan_object_unavailable")
        return now

    @staticmethod
    def _receipt(session: Session, row: OrphanObjectClaimRow) -> OrphanObjectClaim:
        outcome = None
        if row.completed_at is not None:
            run = session.get(ProviderMaintenanceRow, row.completed_execution_id)
            if run is None or run.action not in {"ORPHAN_OBJECT", "ORPHAN_MISSING"}:
                raise ResourceAdmissionError("orphan_object_execution_unconfirmed")
            outcome = (
                OrphanObjectOutcome.RETIRED
                if run.action == "ORPHAN_OBJECT"
                else OrphanObjectOutcome.MISSING
            )
        return OrphanObjectClaim(row.claim_id, OpaqueStorageKey(row.storage_key), outcome)

    def claim(self, storage_key: OpaqueStorageKey, claim_id: UUID) -> OrphanObjectClaim | None:
        requested = OrphanObjectClaim(claim_id, storage_key)
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            lock_cas_digest(session, Sha256Digest(bytes.fromhex(storage_key.value)))
            existing = session.get(OrphanObjectClaimRow, claim_id)
            if existing is not None:
                if existing.storage_key != storage_key.value:
                    raise ResourceAdmissionError("orphan_object_claim_conflict")
                return self._receipt(session, existing)
            active = session.scalar(
                select(OrphanObjectClaimRow).where(
                    OrphanObjectClaimRow.storage_key == storage_key.value,
                    OrphanObjectClaimRow.completed_at.is_(None),
                )
            )
            if active is not None:
                return self._receipt(session, active)
            # STAGING/QUARANTINED objects also protect an older publisher. A missing
            # replica or terminal job does not turn registered CAS into an orphan.
            if object_key_is_registered(session, storage_key.value):
                return None
            session.add(
                OrphanObjectClaimRow(
                    claim_id=claim_id, storage_key=storage_key.value, created_at=self._now(session)
                )
            )
            session.flush()
            return requested

    def pending(
        self, *, maximum: int = 20, after: UUID | None = None
    ) -> tuple[OrphanObjectClaim, ...]:
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise ResourceAdmissionError("orphan_object_request_invalid")
        statement = select(OrphanObjectClaimRow).where(OrphanObjectClaimRow.completed_at.is_(None))
        if after is not None:
            statement = statement.where(OrphanObjectClaimRow.claim_id > after)
        with self._sessions() as session:
            return tuple(
                self._receipt(session, row)
                for row in session.scalars(
                    statement.order_by(OrphanObjectClaimRow.claim_id).limit(maximum)
                )
            )

    def complete(self, claim: OrphanObjectClaim, execution_id: UUID) -> None:
        self._complete(claim, execution_id, MaintenanceAction.ORPHAN_OBJECT)

    def resolve_missing(self, claim: OrphanObjectClaim, execution_id: UUID) -> None:
        self._complete(claim, execution_id, MaintenanceAction.ORPHAN_MISSING)

    def _complete(
        self, claim: OrphanObjectClaim, execution_id: UUID, action: MaintenanceAction
    ) -> None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            lock_cas_digest(session, Sha256Digest(bytes.fromhex(claim.storage_key.value)))
            row = session.get(OrphanObjectClaimRow, claim.claim_id, with_for_update=True)
            if row is None or row.storage_key != claim.storage_key.value:
                raise ResourceAdmissionError("orphan_object_claim_conflict")
            run = session.get(ProviderMaintenanceRow, execution_id)
            if run is None or run.action != action:
                raise ResourceAdmissionError("orphan_object_execution_unconfirmed")
            if row.completed_at is not None:
                if row.completed_execution_id != execution_id:
                    raise ResourceAdmissionError("orphan_object_claim_conflict")
                return
            if (
                run.orphan_claim_id != claim.claim_id
                or run.storage_key != row.storage_key
                or run.state != "CLOSED"
                or run.closure_kind != "PROCESS_EXIT"
                or run.exit_code != 0
                or run.child_pid is None
                or run.closed_at is None
                or session.scalar(
                    select(
                        exists().where(
                            ProviderMaintenanceRow.orphan_claim_id == claim.claim_id,
                            ProviderMaintenanceRow.closed_at.is_(None),
                        )
                    )
                )
                or object_key_is_registered(session, row.storage_key)
            ):
                raise ResourceAdmissionError("orphan_object_execution_unconfirmed")
            row.completed_at, row.completed_execution_id = self._now(session), execution_id
