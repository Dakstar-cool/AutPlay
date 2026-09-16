"""Execution persistence within the existing admission transaction/lock order."""

from datetime import datetime
from typing import Protocol

from autplay.domain.resource_execution import (
    ExecutionStatus,
    ExecutionTicket,
    ProcessExitEvidence,
    ProcessIdentity,
)


class ResourceExecutionRepository(Protocol):
    def prepare(self, ticket: ExecutionTicket, now: datetime) -> ExecutionStatus: ...
    def inspect(self, ticket: ExecutionTicket) -> ExecutionStatus: ...
    def started(
        self, ticket: ExecutionTicket, child: ProcessIdentity, now: datetime
    ) -> ExecutionStatus: ...
    def heartbeat(self, ticket: ExecutionTicket, now: datetime) -> ExecutionStatus: ...
    def stop(self, ticket: ExecutionTicket, now: datetime) -> ExecutionStatus: ...
    def orphan_stale(self, now: datetime, maximum: int) -> int: ...
    def confirm_exit(
        self, ticket: ExecutionTicket, proof: ProcessExitEvidence, now: datetime
    ) -> ExecutionStatus: ...
