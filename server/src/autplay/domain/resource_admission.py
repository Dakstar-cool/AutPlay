"""Account limits and fenced network-operation identities; no transport or storage behavior."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from autplay.domain.jobs import LeaseFence

# Wire integers remain bounded independently of deployment-specific measured ceilings.
MAX_LIMIT = 1_000_000
LEASE_TTL = timedelta(seconds=30)
RESERVATION_TTL = timedelta(seconds=15)
WAITING_TTL = timedelta(seconds=45)
IO_PERMIT_TTL = timedelta(seconds=5)
TERMINAL_RETENTION = timedelta(days=7)
MAX_WAITING_PER_ACCOUNT = 20


class ResourceKind(StrEnum):
    PLAYBACK = "PLAYBACK"
    TRANSFER = "TRANSFER"


class AdmissionState(StrEnum):
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class AuthorityKind(StrEnum):
    DEVICE_SESSION = "DEVICE_SESSION"
    SERVER_ACQUISITION = "SERVER_ACQUISITION"
    LOCAL_BRIDGE = "LOCAL_BRIDGE"


class ResourceAdmissionError(Exception):
    def __init__(self, code: str = "resource_admission_unavailable") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AcquisitionClaim:
    """Internal worker input; repository derives owner and authority from durable rows."""

    fence: LeaseFence
    resource_type: str
    acquisition_id: UUID

    def __post_init__(self) -> None:
        if self.resource_type not in {"INTERNET_ACQUISITION", "DISCOVERY_ACQUISITION"}:
            raise ResourceAdmissionError("resource_request_invalid")

    @property
    def request(self) -> ResourceRequest:
        operation_id = uuid5(
            NAMESPACE_URL, f"autplay:resource-worker:v1:{self.resource_type}:{self.acquisition_id}"
        )
        return ResourceRequest(
            operation_id,
            ResourceKind.TRANSFER,
            self.resource_type,
            self.acquisition_id,
            self.acquisition_id,
        )


@dataclass(frozen=True, slots=True)
class LocalBridgeClaim:
    """Explicit local operator authority for the deterministic acquisition bridge device."""

    user_id: UUID
    device_id: UUID

    def __post_init__(self) -> None:
        expected = uuid5(self.user_id, "autplay-acquisition-vault-bridge-v1")
        if self.device_id != expected:
            raise ResourceAdmissionError("resource_request_invalid")


def positive_limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_LIMIT:
        raise ResourceAdmissionError("resource_limit_invalid")
    return value


@dataclass(frozen=True, slots=True)
class AccountLimits:
    devices: int = 5
    playbacks: int = 2
    transfers: int = 2

    def __post_init__(self) -> None:
        for value in (self.devices, self.playbacks, self.transfers):
            positive_limit(value)

    def for_kind(self, kind: ResourceKind) -> int:
        return self.playbacks if kind == ResourceKind.PLAYBACK else self.transfers


@dataclass(frozen=True, slots=True)
class AccountLimitOverride:
    devices: int | None = None
    playbacks: int | None = None
    transfers: int | None = None

    def __post_init__(self) -> None:
        for value in (self.devices, self.playbacks, self.transfers):
            if value is not None:
                positive_limit(value)

    def effective(self, defaults: AccountLimits) -> AccountLimits:
        return AccountLimits(
            defaults.devices if self.devices is None else self.devices,
            defaults.playbacks if self.playbacks is None else self.playbacks,
            defaults.transfers if self.transfers is None else self.transfers,
        )


@dataclass(frozen=True, slots=True)
class ActivationFence:
    operation_id: UUID
    activation_id: UUID
    generation: int

    def __post_init__(self) -> None:
        if type(self.generation) is not int or not 1 <= self.generation <= 2**53 - 1:
            raise ResourceAdmissionError("resource_activation_invalid")


@dataclass(frozen=True, slots=True)
class MeasuredGlobalBudget:
    """Configured by deployment evidence, never inferred from account counts."""

    playbacks: int
    transfers: int
    playback_ceiling: int
    transfer_ceiling: int
    evidence_reference: str

    def __post_init__(self) -> None:
        for value in (self.playbacks, self.transfers, self.playback_ceiling, self.transfer_ceiling):
            positive_limit(value)
        if self.playbacks > self.playback_ceiling or self.transfers > self.transfer_ceiling:
            raise ResourceAdmissionError("resource_budget_exceeded")
        if not 1 <= len(self.evidence_reference) <= 240 or any(
            ord(char) < 32 for char in self.evidence_reference
        ):
            raise ResourceAdmissionError("resource_budget_evidence_required")


@dataclass(frozen=True, slots=True)
class ResourceAuthority:
    """A repository-validated account generation and application or worker lineage."""

    user_id: UUID
    authority_generation: int
    authority_kind: AuthorityKind
    device_id: UUID | None = None
    session_family_id: UUID | None = None
    session_mode: str | None = None
    job_id: UUID | None = None
    job_worker_id: str | None = None
    job_attempt: int | None = None
    acquisition_attempt_id: UUID | None = None
    source_authorization_id: UUID | None = None
    source_authorization_revision: int | None = None
    policy_id: UUID | None = None
    policy_revision: int | None = None


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    operation_id: UUID
    kind: ResourceKind
    resource_type: str
    resource_id: UUID
    target_id: UUID | None = None

    def __post_init__(self) -> None:
        playback = (
            self.kind == ResourceKind.PLAYBACK
            and self.resource_type == "PLAY_INSTANCE"
            and self.target_id is None
        )
        transfer = (
            self.kind == ResourceKind.TRANSFER
            and self.resource_type
            in {
                "DOWNLOAD_INTENT",
                "UPLOAD_INTENT",
                "INTERNET_ACQUISITION",
                "DISCOVERY_ACQUISITION",
            }
            and isinstance(self.target_id, UUID)
        )
        if not (playback or transfer):
            raise ResourceAdmissionError("resource_request_invalid")

    def digest(self, authority: ResourceAuthority) -> bytes:
        # All input values are internally typed UUIDs, enums, bounded integers or ASCII tags.
        document = {"request": asdict(self), "authority": asdict(authority)}
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
        return sha256(encoded.encode("ascii")).digest()


@dataclass(slots=True)
class ResourceAdmission:
    authority: ResourceAuthority
    request: ResourceRequest
    request_sha256: bytes
    state: AdmissionState
    activation_id: UUID | None
    generation: int
    created_at: datetime
    updated_at: datetime
    enqueued_at: datetime
    waiting_until: datetime | None = None
    lease_until: datetime | None = None
    claim_until: datetime | None = None
    claimed_at: datetime | None = None
    terminal_at: datetime | None = None
    attachment_revision: int = 0
    current_recording_id: UUID | None = None
    next_recording_id: UUID | None = None

    @property
    def fence(self) -> ActivationFence | None:
        if self.activation_id is None:
            return None
        return ActivationFence(self.request.operation_id, self.activation_id, self.generation)

    def active_at(self, now: datetime) -> bool:
        return (
            self.state == AdmissionState.ACTIVE
            and self.lease_until is not None
            and self.lease_until > now
            and (
                self.claimed_at is not None
                or (self.claim_until is not None and self.claim_until > now)
            )
        )

    def expire(self, now: datetime) -> None:
        if self.state in {AdmissionState.WAITING, AdmissionState.ACTIVE} and (
            not self.active_at(now)
            if self.state == AdmissionState.ACTIVE
            else (
                self.authority.job_id is None
                and (self.waiting_until is None or self.waiting_until <= now)
            )
        ):
            self.state, self.terminal_at, self.updated_at = AdmissionState.EXPIRED, now, now


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    account: int
    device: int
    server: int


@dataclass(frozen=True, slots=True)
class AdmissionStatus:
    operation: ResourceAdmission
    limits: AccountLimits
    usage: ResourceUsage
    server_limit: int
    waiting_reason: str | None
    retry_after_seconds: int = 5


@dataclass(frozen=True, slots=True)
class IoPermit:
    permit_id: UUID
    fence: ActivationFence
    target_id: UUID
    expires_at: datetime
