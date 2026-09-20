"""Internal ingest ownership is independent of audio TRANSFER permits."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from .ingest_cleanup import IngestCleanupTicket
from .jobs import LeaseFence
from .metadata_execution import MetadataExecutionTicket
from .resource_execution import ExecutionState, ProcessIdentity
from .training_execution import TrainingExecutionTicket
from .vault import OpaqueStorageKey

INGEST_IO_TTL = timedelta(seconds=5)
MAX_INGEST_COORDINATOR_ENTRIES = 100


@dataclass(frozen=True, slots=True)
class IngestExecutionTicket:
    execution_id: UUID
    owner_run_id: UUID
    upload_session_id: UUID
    staging_key: OpaqueStorageKey
    fence: LeaseFence

    @property
    def kind(self) -> str:
        return "VAULT_INGEST"


@dataclass(frozen=True, slots=True)
class IngestExecutionStatus[
    Ticket: IngestExecutionTicket
    | IngestCleanupTicket
    | MetadataExecutionTicket
    | TrainingExecutionTicket = IngestExecutionTicket
]:
    ticket: Ticket
    state: ExecutionState
    child: ProcessIdentity | None
    io_deadline_at: datetime | None
    heartbeat_at: datetime | None = None

    @property
    def authorized_seconds(self) -> float:
        """Server-relative interval; the caller anchors it before starting its RPC."""
        if self.heartbeat_at is None or self.io_deadline_at is None:
            return 0.0
        return max(
            0.0,
            min(
                INGEST_IO_TTL.total_seconds(),
                (self.io_deadline_at - self.heartbeat_at).total_seconds(),
            ),
        )
