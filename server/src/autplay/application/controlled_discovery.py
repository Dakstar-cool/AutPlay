"""Typed A1 provider results and durable handoff receipts."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from autplay.domain.discovery import DiscoveryEvidence
from autplay.domain.jobs import LeaseFence
from autplay.domain.resource_admission import AcquisitionClaim, ActivationFence
from autplay.domain.vault import Sha256Digest, VerifiedStagedFile


@dataclass(frozen=True, slots=True)
class DiscoveryAcquisitionTarget:
    candidate_id: UUID
    acquisition_attempt_id: UUID
    user_id: UUID
    provider_track_id: str
    provider_artist_id: str
    origin: str


@dataclass(frozen=True, slots=True)
class DiscoveryHandoffReceipt:
    target: DiscoveryAcquisitionTarget
    execution_id: UUID
    upload_id: UUID
    ingest_job_id: UUID
    recording_id: UUID
    byte_size: int
    sha256: Sha256Digest


@dataclass(frozen=True, slots=True)
class DiscoveryFileReceipt:
    execution_id: UUID
    verified: VerifiedStagedFile
    evidence: DiscoveryEvidence


class DiscoveryAcquisitionRepository(Protocol):
    def prepare(
        self, candidate_id: UUID, owner: UUID, fence: LeaseFence
    ) -> DiscoveryAcquisitionTarget | DiscoveryHandoffReceipt: ...

    def handoff(
        self,
        claim: AcquisitionClaim,
        target: DiscoveryAcquisitionTarget,
        execution_id: UUID,
        verified: VerifiedStagedFile,
        evidence: DiscoveryEvidence,
    ) -> DiscoveryHandoffReceipt: ...

    def fail(
        self, candidate_id: UUID, owner: UUID, fence: LeaseFence, code: str, *, terminal: bool
    ) -> None: ...


class DiscoveryExecutor(Protocol):
    def execute(
        self, claim: AcquisitionClaim, fence: ActivationFence, target: DiscoveryAcquisitionTarget
    ) -> DiscoveryFileReceipt: ...
