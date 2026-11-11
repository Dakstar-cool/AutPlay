"""M1 read boundaries. Every returned value is detached from storage ownership."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from autplay.domain.auth import AccessTokenClaims, Principal


@dataclass(frozen=True, slots=True)
class ConfiguredM5Identity:
    """Already initialized public M5 config, never reconstructed from this query.

    The integration owner supplies this through its nonblocking read-only capture
    seam. It must not invoke _instance/current_device_contract_identity here.
    M5 singleton revision is observed in SQL, not scoped D1/B1 state.
    """

    server_instance_id: UUID
    identity_epoch: int
    public_key_spki: bytes = field(repr=False)
    thumbprint_sha256: bytes = field(repr=False)
    api_origin: str = field(repr=False)
    stream_origin: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class MediaSourcePolicy:
    media_source_snapshot_v1_enabled: bool = False
    identity: ConfiguredM5Identity | None = None
    access_token_ttl_seconds: int = 900
    # Only the committed M5 singleton revision. Never a D1/B1 counter or guard.
    m5_capability_revision: int | None = None


@dataclass(frozen=True, slots=True)
class ActorFacts:
    user_id: UUID
    device_id: UUID
    session_id: UUID
    device_user_id: UUID
    session_user_id: UUID
    session_device_id: UUID
    role: str
    account_status: str
    account_deleted_at: datetime | None
    device_created_at: datetime
    device_revoked_at: datetime | None
    session_issued_at: datetime
    session_expires_at: datetime
    session_revoked_at: datetime | None
    device_key_blocked: bool


@dataclass(frozen=True, slots=True)
class SourceFacts:
    user_track_ref_id: UUID
    user_track_ref_row_version: int
    recording_id: UUID
    audio_variant_id: UUID
    audio_variant_row_version: int
    sha256: bytes = field(repr=False)
    total_bytes: int
    media_type: str
    codec: str
    container: str


@dataclass(frozen=True, slots=True)
class RelationshipCapture:
    actor: ActorFacts | None
    identity: ConfiguredM5Identity | None
    m5_capability_revision: int | None
    source: SourceFacts | None


@dataclass(frozen=True, slots=True)
class ElapsedMark:
    monotonic_ns: int
    suspend_watch_ns: int


@dataclass(frozen=True, slots=True)
class DatabaseClockSample:
    db_utc: datetime
    before: ElapsedMark
    after: ElapsedMark


class ElapsedClock(Protocol):
    def mark(self) -> ElapsedMark:
        """Sample elapsed clocks only; the watch detects jumps/suspension."""
        ...


class CurrentMediaSourcePolicy(Protocol):
    def __call__(self) -> MediaSourcePolicy:
        """Nonblocking atomic immutable-reference read, including at final send."""
        ...


class MediaSourceReader(Protocol):
    def read_relationship(
        self, principal: Principal, user_track_ref_id: UUID, *, started: ElapsedMark
    ) -> RelationshipCapture:
        """One SELECT in a fresh READ ONLY transaction, settled before return."""
        ...

    def final_database_clock(self, *, started: ElapsedMark) -> DatabaseClockSample:
        """Last blocking read, same authoritative DB, autocommit READ ONLY.

        m0 precedes dispatch; m1 follows scalar fetch, settlement and connection
        release. Pool/transport/statement/settlement waits must all be bounded.
        """
        ...


class MediaSourceAuthenticator(Protocol):
    def __call__(self, token: str) -> tuple[Principal, AccessTokenClaims]:
        """Use the actual authenticate_access_with_claims, preserving TTL policy."""
        ...
