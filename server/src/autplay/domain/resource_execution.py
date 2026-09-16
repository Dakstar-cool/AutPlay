"""Durable process identities and explicit exit evidence; clocks cannot prove exit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from .resource_admission import IoPermit, ResourceAdmissionError

EXECUTION_HEARTBEAT_TTL = timedelta(seconds=15)


class ExecutionKind(StrEnum):
    VAULT_STREAM = "VAULT_STREAM"
    VAULT_UPLOAD = "VAULT_UPLOAD"
    PROVIDER = "PROVIDER"


class ExecutionState(StrEnum):
    # Internal reconciliation projection only: no historical exit evidence remains.
    ABSENT = "ABSENT"
    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ORPHANED = "ORPHANED"
    CLOSED = "CLOSED"


class ExitKind(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PROCESS_EXIT = "PROCESS_EXIT"
    SUPERVISOR_EXIT = "SUPERVISOR_EXIT"


@dataclass(frozen=True, slots=True)
class ExecutionTicket:
    execution_id: UUID
    owner_run_id: UUID
    permit: IoPermit
    kind: ExecutionKind
    actual_target_id: UUID


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    identity_sha256: bytes

    def __post_init__(self) -> None:
        if (
            type(self.pid) is not int
            or not 1 <= self.pid < 2**63
            or len(self.identity_sha256) != 32
        ):
            raise ResourceAdmissionError("resource_execution_invalid")


@dataclass(frozen=True, slots=True)
class ProcessExitEvidence:
    """Trusted adapter evidence, never a client claim or an HTTP request body.

    PROCESS_EXIT requires a waited process handle and its registered identity.
    NOT_STARTED is valid only when the owning adapter proves spawning never occurred.
    SUPERVISOR_EXIT requires independent verified death, including unknown-spawn orphans.
    The digest references bounded supervisor evidence; it does not itself verify death.
    """

    kind: ExitKind
    evidence_sha256: bytes
    exit_code: int | None = None
    child: ProcessIdentity | None = None

    def __post_init__(self) -> None:
        if len(self.evidence_sha256) != 32:
            raise ResourceAdmissionError("resource_execution_invalid")
        if self.kind == ExitKind.NOT_STARTED:
            valid = self.exit_code is None and self.child is None
        else:
            valid = (
                type(self.exit_code) is int
                and -(2**63) <= self.exit_code < 2**63
                and (self.kind == ExitKind.SUPERVISOR_EXIT or self.child is not None)
            )
        if not valid:
            raise ResourceAdmissionError("resource_execution_invalid")


@dataclass(frozen=True, slots=True)
class ExecutionStatus:
    ticket: ExecutionTicket
    state: ExecutionState
    heartbeat_at: datetime | None
    child: ProcessIdentity | None
    closed_at: datetime | None
