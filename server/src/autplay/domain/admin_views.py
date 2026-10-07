"""Bounded, redacted M6 read-model values; these values never carry secrets or paths."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AdminPage:
    items: tuple[object, ...]
    next_after: str | None


@dataclass(frozen=True, slots=True)
class AdminConfirmationTarget:
    target_id: UUID
    kind: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class AdminWorkerHealth:
    observed_at: datetime
    fresh: bool
    busy: bool
    cpu_percent: float | None
    memory_bytes: int | None
    memory_limit_bytes: int | None
    active_jobs: int
    queued_jobs: int
    fresh_for_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class AdminDashboard:
    label: str
    api_ready: bool
    capability_revision: int
    recovery_available: bool
    build_version: str = "0.0.0"
    postgresql_ready: bool = True
    worker_status: str = "UNKNOWN"
    vault_status: str = "UNKNOWN"
    worker: AdminWorkerHealth | None = None
    vault: AdminVaultStatus | None = None


@dataclass(frozen=True, slots=True)
class AdminAuditItem:
    occurred_at: datetime
    action: str
    target_type: str
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class AdminDeviceItem:
    device_id: UUID
    label: str
    platform: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AdminInvitationItem:
    invitation_id: UUID
    issued_at: datetime
    expires_at: datetime
    terminal: bool


@dataclass(frozen=True, slots=True)
class AdminSessionItem:
    session_id: UUID
    kind: str
    state: str
    created_at: datetime
    expires_at: datetime
    current: bool


@dataclass(frozen=True, slots=True)
class AdminJobItem:
    job_id: UUID
    kind: str
    state: str
    created_at: datetime
    progress_current: int | None
    progress_total: int | None


@dataclass(frozen=True, slots=True)
class AdminImportItem:
    import_job_id: UUID
    adapter_id: str
    mode: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AdminReviewItem:
    import_entry_id: UUID
    import_job_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class AdminUnavailable:
    code: str
    cli_guidance: bool = True


@dataclass(frozen=True, slots=True)
class AdminRecord:
    identifier: UUID
    state: str
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AdminDiskUsage:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class AdminVaultIssue:
    scope: str
    state: str
    error_code: str
    count: int
    last_occurred_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdminVaultStatus:
    object_count: int
    committed_bytes: int
    quarantined_objects: int
    available_replicas: int
    unhealthy_replicas: int
    uploads_open: int
    uploads_quarantined: int
    last_verified_at: datetime | None
    reconciliation_available: bool
    missing_replicas: int = 0
    corrupt_replicas: int = 0
    quarantined_replicas: int = 0
    uploads_failed: int = 0
    issues: tuple[AdminVaultIssue, ...] = ()
    issues_truncated: bool = False
    disk: AdminDiskUsage | None = None


__all__ = (
    "AdminAuditItem",
    "AdminConfirmationTarget",
    "AdminDashboard",
    "AdminDeviceItem",
    "AdminDiskUsage",
    "AdminImportItem",
    "AdminInvitationItem",
    "AdminJobItem",
    "AdminPage",
    "AdminRecord",
    "AdminReviewItem",
    "AdminSessionItem",
    "AdminUnavailable",
    "AdminVaultIssue",
    "AdminVaultStatus",
    "AdminWorkerHealth",
)
