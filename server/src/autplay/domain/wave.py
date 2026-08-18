"""Pure Wave room policy.  Transport and storage deliberately stay outside this module."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

ROOM_TTL = timedelta(hours=6)
HOST_GRACE = timedelta(seconds=30)
PRESENCE_TTL = timedelta(seconds=30)
MAX_MEMBERS = 8
MAX_QUEUE = 100
PREFLIGHT_LOOKAHEAD = 3
MAX_RTT_MS = 1_000


class Availability(StrEnum):
    LOCAL = "LOCAL"
    DOWNLOADED = "DOWNLOADED"
    VAULT_STREAMABLE = "VAULT_STREAMABLE"
    UNAVAILABLE = "UNAVAILABLE"


class WaveError(RuntimeError):
    code = "wave_failed"


class WaveConflict(WaveError):
    code = "wave_version_conflict"


class WaveForbidden(WaveError):
    code = "wave_forbidden"


def new_room_code() -> str:
    """Return 10 Crockford-base32 chars (50 bits); ambiguous glyphs are absent."""
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = secrets.randbits(50)
    return "".join(alphabet[(value >> (5 * index)) & 31] for index in range(9, -1, -1))


@dataclass(frozen=True, slots=True)
class QueueEntry:
    queue_entry_id: UUID
    recording_id: UUID
    position: int


@dataclass(slots=True)
class WaveRoom:
    room_id: UUID
    code: str
    host_user_id: UUID
    created_at: datetime
    expires_at: datetime
    members: set[UUID] = field(default_factory=set)
    queue: list[QueueEntry] = field(default_factory=list)
    version: int = 1
    command_sequence: int = 0
    closed_at: datetime | None = None
    host_lost_at: datetime | None = None
    room_epoch: int = 1
    state: str = "OPEN"
    playback_state: str = "IDLE"
    self_role: str = "MEMBER"
    self_preflight: dict[UUID, Availability] = field(default_factory=dict)

    @classmethod
    def create(cls, host_user_id: UUID, now: datetime) -> WaveRoom:
        return cls(uuid4(), new_room_code(), host_user_id, now, now + ROOM_TTL, {host_user_id})

    def active(self, now: datetime) -> bool:
        return self.closed_at is None and now < self.expires_at

    def require_member(self, user_id: UUID, now: datetime) -> None:
        if not self.active(now) or user_id not in self.members:
            raise WaveForbidden()

    def require_host(self, user_id: UUID, now: datetime) -> None:
        self.require_member(user_id, now)
        if user_id != self.host_user_id:
            raise WaveForbidden()

    def join(self, user_id: UUID, now: datetime) -> None:
        if not self.active(now):
            raise WaveForbidden()
        if user_id not in self.members and len(self.members) >= MAX_MEMBERS:
            raise WaveConflict()
        self.members.add(user_id)

    def enqueue(
        self, user_id: UUID, recording_id: UUID, base_version: int, now: datetime
    ) -> QueueEntry:
        self.require_host(user_id, now)
        if base_version != self.version:
            raise WaveConflict()
        if len(self.queue) >= MAX_QUEUE:
            raise WaveConflict()
        entry = QueueEntry(uuid4(), recording_id, len(self.queue))
        self.queue.append(entry)
        self.version += 1
        return entry

    def transfer_host(self, actor: UUID, target: UUID, now: datetime) -> None:
        self.require_host(actor, now)
        if target not in self.members:
            raise WaveForbidden()
        self.host_user_id, self.host_lost_at = target, None
        self.version += 1

    def host_disconnected(self, now: datetime) -> None:
        self.host_lost_at = now

    def expire_host_grace(self, now: datetime) -> bool:
        if self.host_lost_at is not None and now >= self.host_lost_at + HOST_GRACE:
            self.closed_at = now
            self.version += 1
            return True
        return False


def schedule_lead_ms(worst_accepted_rtt_ms: int) -> int:
    if not 0 <= worst_accepted_rtt_ms <= MAX_RTT_MS:
        raise ValueError("wave RTT is outside accepted bounds")
    # Conservative P13 policy: transport calculation uses the p95 accepted RTT
    # and an independently bounded clock uncertainty supplied by the adapter.
    return min(8_000, max(2_000, 3 * worst_accepted_rtt_ms + 250))


__all__ = (
    "HOST_GRACE",
    "MAX_MEMBERS",
    "MAX_QUEUE",
    "MAX_RTT_MS",
    "PREFLIGHT_LOOKAHEAD",
    "PRESENCE_TTL",
    "ROOM_TTL",
    "Availability",
    "QueueEntry",
    "WaveConflict",
    "WaveError",
    "WaveForbidden",
    "WaveRoom",
    "new_room_code",
    "schedule_lead_ms",
)
