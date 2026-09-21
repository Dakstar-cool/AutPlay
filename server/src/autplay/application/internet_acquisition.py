"""Typed worker handoff, separate from device HTTP principals and byte execution."""

from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from autplay.application.job_worker import JobExecutionContext
from autplay.application.resource_admission import ResourceAdmissionService
from autplay.domain.jobs import JobLease, RetryableJobError, TerminalJobError
from autplay.domain.resource_admission import (
    AcquisitionClaim,
    ActivationFence,
    AdmissionState,
    ResourceAdmissionError,
)
from autplay.domain.vault import Sha256Digest, VerifiedStagedFile


@dataclass(frozen=True, slots=True)
class InternetAcquisitionTarget:
    acquisition_id: UUID
    user_id: UUID
    ref_id: UUID
    recording_id: UUID
    candidate_id: str


@dataclass(frozen=True, slots=True)
class InternetHandoffReceipt:
    target: InternetAcquisitionTarget
    execution_id: UUID
    upload_id: UUID
    ingest_job_id: UUID
    byte_size: int
    sha256: Sha256Digest


class InternetAcquisitionRepository(Protocol):
    def prepare(
        self, claim: AcquisitionClaim
    ) -> InternetAcquisitionTarget | InternetHandoffReceipt:
        """Return a prior handoff before any download or staging mutation."""
        ...

    def handoff(
        self,
        claim: AcquisitionClaim,
        target: InternetAcquisitionTarget,
        execution_id: UUID,
        verified: VerifiedStagedFile,
    ) -> InternetHandoffReceipt:
        """Persist one verified, exited provider result and its ingest job atomically."""
        ...


@dataclass(frozen=True, slots=True)
class ProviderFileReceipt:
    execution_id: UUID
    verified: VerifiedStagedFile


class ProviderExecutor(Protocol):
    def execute(
        self, claim: AcquisitionClaim, fence: ActivationFence, candidate_id: str
    ) -> ProviderFileReceipt:
        """Return only after verified bytes, exact tree exit and durable acknowledgement."""
        ...


class ControlledInternetAcquisitionHandler:
    """One worker authority and TRANSFER through provider bytes and atomic handoff."""

    def __init__(
        self,
        repository: InternetAcquisitionRepository,
        admissions: ResourceAdmissionService,
        executor: ProviderExecutor,
    ) -> None:
        self._repository, self._admissions, self._executor = repository, admissions, executor

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        try:
            self._run(context, lease)
        except SQLAlchemyError as error:
            raise RetryableJobError("database_unavailable") from error
        except ResourceAdmissionError as error:
            raise RetryableJobError(error.code) from error

    def _run(self, context: JobExecutionContext, lease: JobLease) -> None:
        raw = lease.payload.get("acquisition_id")
        if not isinstance(raw, str) or context.fence != lease.fence:
            raise TerminalJobError("music_acquisition_invalid")
        try:
            identity = UUID(raw)
        except ValueError as error:
            raise TerminalJobError("music_acquisition_invalid") from error
        claim = AcquisitionClaim(lease.fence, "INTERNET_ACQUISITION", identity)
        context.raise_if_cancelled()
        target = self._repository.prepare(claim)
        if isinstance(target, InternetHandoffReceipt):
            return  # A lost completion reply never downloads or hashes again.
        admitted: ActivationFence | None = None
        for _ in range(8):
            context.raise_if_cancelled()
            status = self._admissions.acquire_worker(claim)
            if status.operation.state == AdmissionState.ACTIVE:
                admitted = status.operation.fence
                break
            # This returns only for a concurrent grant/recheck; actual waiting
            # commits RESOURCE_WAIT and yields without spending a provider retry.
            context.defer_for_resource(claim.request.operation_id)
        if admitted is None:
            raise RetryableJobError("resource_service_unavailable")
        result = self._executor.execute(claim, admitted, target.candidate_id)
        context.raise_if_cancelled()
        self._repository.handoff(claim, target, result.execution_id, result.verified)
        # RELEASED is terminal for this stable operation ID. Failed attempts must
        # remain rebindable; their executions keep their own charge until exit.
        # Release only after the durable handoff, never from a retry's finally.
        with suppress(ResourceAdmissionError):
            self._admissions.release(claim, admitted)
