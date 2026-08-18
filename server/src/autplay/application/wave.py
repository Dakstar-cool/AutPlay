"""Short-transaction Wave application service with idempotent command semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from autplay.domain.wave import Availability, WaveConflict, WaveRoom


@dataclass(slots=True)
class InMemoryWaveService:
    """Reference service used by the HTTP adapter; repository replacement preserves its policy."""

    rooms: dict[UUID, WaveRoom] = field(default_factory=dict)
    commands: dict[tuple[UUID, str], dict[str, object]] = field(default_factory=dict)
    presence: dict[tuple[UUID, UUID], datetime] = field(default_factory=dict)
    availability: dict[tuple[UUID, UUID, UUID], Availability] = field(default_factory=dict)

    def create(self, user_id: UUID, now: datetime) -> WaveRoom:
        room = WaveRoom.create(user_id, now)
        self.rooms[room.room_id] = room
        return room

    def get(self, room_id: UUID, user_id: UUID, now: datetime) -> WaveRoom:
        room = self.rooms[room_id]
        room.require_member(user_id, now)
        return room

    def join(self, room_id: UUID, user_id: UUID, now: datetime) -> WaveRoom:
        room = self.rooms[room_id]
        room.join(user_id, now)
        return room

    def command(
        self,
        room_id: UUID,
        user_id: UUID,
        kind: str,
        idempotency_key: str,
        base_version: int,
        recording_id: UUID | None,
        now: datetime,
    ) -> dict[str, object]:
        key = (room_id, idempotency_key)
        if key in self.commands:
            return self.commands[key]
        room = self.rooms[room_id]
        if kind == "enqueue":
            if recording_id is None:
                raise ValueError("recording is required")
            entry = room.enqueue(user_id, recording_id, base_version, now)
            result = {
                "sequence": room.command_sequence + 1,
                "version": room.version,
                "queue_entry_id": str(entry.queue_entry_id),
            }
        elif kind in {"play", "pause", "seek", "skip"}:
            room.require_host(user_id, now)
            if base_version != room.version:
                raise WaveConflict()
            room.command_sequence += 1
            result = {"sequence": room.command_sequence, "version": room.version}
        else:
            raise ValueError("unsupported Wave command")
        self.commands[key] = result
        return result

    def report_availability(
        self, room_id: UUID, user_id: UUID, recording_id: UUID, value: Availability, now: datetime
    ) -> None:
        self.get(room_id, user_id, now)
        self.availability[(room_id, user_id, recording_id)] = value
        self.presence[(room_id, user_id)] = now

    def schedule(
        self,
        room_id: UUID,
        user_id: UUID,
        recording_id: UUID,
        rtts: list[int],
        now: datetime,
        uncertainty_ms: int = 0,
    ) -> dict[str, object]:
        room = self.get(room_id, user_id, now)
        room.require_host(user_id, now)
        # Captured participant set is strict: absent status or an explicit unavailable
        # result aborts before any scheduled command becomes durable.
        active = [
            member
            for member in room.members
            if self.presence.get((room_id, member), datetime.min.replace(tzinfo=UTC)).timestamp()
            >= now.timestamp() - 30
        ]
        if len(active) != len(room.members) or any(
            self.availability.get((room_id, m, recording_id), Availability.UNAVAILABLE)
            is Availability.UNAVAILABLE
            for m in active
        ):
            raise WaveConflict()
        if not rtts or len(rtts) > 7 or uncertainty_ms > 100:
            raise WaveConflict()
        p95 = max(rtts)
        lead = min(8_000, max(2_000, 3 * p95 + 2 * uncertainty_ms + 250))
        room.command_sequence += 1
        return {
            "sequence": room.command_sequence,
            "scheduled_at": now.timestamp() + lead / 1000,
            "lead_ms": lead,
        }
