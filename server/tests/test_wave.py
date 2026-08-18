from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from autplay.application.wave import InMemoryWaveService
from autplay.domain.wave import Availability, WaveConflict, new_room_code, schedule_lead_ms


def test_code_is_crockford_50_bit_and_command_is_idempotent() -> None:
    assert len(new_room_code()) == 10
    now = datetime(2026, 8, 17, tzinfo=UTC)
    service = InMemoryWaveService()
    room = service.create(uuid4(), now)
    result = service.command(room.room_id, room.host_user_id, "enqueue", "one", 1, uuid4(), now)
    assert result == service.command(
        room.room_id, room.host_user_id, "enqueue", "one", 1, uuid4(), now
    )
    with pytest.raises(WaveConflict):
        service.command(room.room_id, room.host_user_id, "enqueue", "two", 1, uuid4(), now)


def test_strict_preflight_and_bounded_lead() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    service = InMemoryWaveService()
    host, member, recording = uuid4(), uuid4(), uuid4()
    room = service.create(host, now)
    service.join(room.room_id, member, now)
    service.report_availability(room.room_id, host, recording, Availability.LOCAL, now)
    with pytest.raises(WaveConflict):
        service.schedule(room.room_id, host, recording, [100], now)
    service.report_availability(room.room_id, member, recording, Availability.VAULT_STREAMABLE, now)
    assert service.schedule(room.room_id, host, recording, [100], now)["lead_ms"] == 2_000
    assert schedule_lead_ms(1_000) <= 8_000
    with pytest.raises(WaveConflict):
        service.schedule(room.room_id, host, recording, [100], now + timedelta(seconds=31))
