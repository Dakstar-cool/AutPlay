"""Scratch-only ownership survives upload completion, revocation and accounting GC."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.provider_scratch import ProviderScratchClaim, provider_scratch_id
from autplay.domain.resource_admission import ResourceAdmissionError

from .models import ProviderStagingRow
from .models.resource_admission import ResourceIoExecutionRow
from .resource_limits import lock_resource_admission


class PostgresProviderScratchRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise ResourceAdmissionError("provider_scratch_unavailable")
        return now

    @staticmethod
    def _receipt(row: ProviderStagingRow) -> ProviderScratchClaim:
        if row.scratch_claim_id is None:
            raise ResourceAdmissionError("provider_scratch_conflict")
        return ProviderScratchClaim(
            row.execution_id, row.scratch_claim_id, row.scratch_retired_at is not None
        )

    @staticmethod
    def _eligible(session: Session, row: ProviderStagingRow) -> bool:
        # HANDED_OFF and its binding/closure fields are immutable. Neither live
        # account authority nor a still-present upload staging file is required.
        return (
            row.state == "HANDED_OFF"
            and row.closed_at is not None
            and row.exit_code == 0
            and row.closure_kind in {"PROCESS_EXIT", "SUPERVISOR_EXIT"}
            and row.handed_off_at is not None
            and row.upload_session_id is not None
            and not session.scalar(
                select(
                    exists().where(
                        ResourceIoExecutionRow.execution_id == row.execution_id,
                        ResourceIoExecutionRow.closed_at.is_(None),
                    )
                )
            )
        )

    def pending(self, *, maximum: int = 20, after: UUID | None = None) -> tuple[UUID, ...]:
        """A bounded keyset page; start a new scan after reaching the end."""
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise ResourceAdmissionError("provider_scratch_request_invalid")
        statement = select(ProviderStagingRow.execution_id).where(
            ProviderStagingRow.state == "HANDED_OFF",
            ProviderStagingRow.scratch_retired_at.is_(None),
        )
        if after is not None:
            statement = statement.where(ProviderStagingRow.execution_id > after)
        with self._sessions() as session:
            return tuple(
                session.scalars(statement.order_by(ProviderStagingRow.execution_id).limit(maximum))
            )

    def claim(self, execution_id: UUID) -> ProviderScratchClaim | None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = session.scalar(
                select(ProviderStagingRow)
                .where(ProviderStagingRow.execution_id == execution_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None or not self._eligible(session, row):
                return None
            if row.scratch_claim_id is None:
                now = self._now(session)
                row.scratch_claim_id = provider_scratch_id(execution_id)
                row.scratch_claimed_at = row.updated_at = now
                session.flush()
            return self._receipt(row)

    def complete(self, claim: ProviderScratchClaim) -> None:
        with self._sessions.begin() as session:
            lock_resource_admission(session)
            row = session.scalar(
                select(ProviderStagingRow)
                .where(ProviderStagingRow.execution_id == claim.execution_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                row is None
                or row.scratch_claim_id != claim.claim_id
                or not self._eligible(session, row)
            ):
                raise ResourceAdmissionError("provider_scratch_conflict")
            if row.scratch_retired_at is None:
                row.scratch_retired_at = row.updated_at = self._now(session)
