"""P14 real-PostgreSQL offline-to-online second-device release scenario."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import rfc8785
from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow
from autplay.application.sync import SyncService
from autplay.domain.auth import AccountRole, Principal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def _event(
    principal: Principal,
    event_id: UUID,
    sequence: int,
    kind: str,
    aggregate: str,
    payload: dict[str, object],
    *,
    aggregate_id: UUID | None = None,
    base: int | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "event_id": str(event_id),
        "idempotency_key": str(event_id),
        "user_id": str(principal.user_id),
        "device_id": str(principal.device_id),
        "device_sequence": sequence,
        "event_type": kind,
        "schema_version": 1,
        "aggregate_type": aggregate,
        "aggregate_local_id": str(aggregate_id or event_id),
        "aggregate_server_id": None,
        "base_server_row_version": base,
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }
    value["request_hash"] = hashlib.sha256(rfc8785.dumps(value)).hexdigest()  # type: ignore[arg-type]
    return value


def _bind(
    service: SyncService,
    principal: Principal,
    *,
    server_profile_id: UUID,
    epoch: UUID,
) -> dict[str, object]:
    body: dict[str, object] = {
        "protocol_version": 1,
        "user_id": str(principal.user_id),
        "device_id": str(principal.device_id),
        "server_profile_id": str(server_profile_id),
        "journal_epoch": str(epoch),
        "device_name": "p14-release-device",
        "platform": "ANDROID",
        "app_version": "p14-rc1",
    }
    service.bind(principal, body)
    return body


def test_offline_edit_reaches_second_device_and_survives_receive_process_death(
    database_url: str,
) -> None:
    """One joined scenario crosses offline Journal, server truth and a second device."""

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        user_id, first_device_id, second_device_id = uuid4(), uuid4(), uuid4()
        with Session(engine) as session:
            session.add(
                UserAccountRow(
                    user_id=user_id,
                    display_name="P14 E2E Owner",
                    role="USER",
                    status="ACTIVE",
                )
            )
            session.flush()
            session.add_all(
                [
                    DeviceRow(
                        device_id=first_device_id,
                        user_id=user_id,
                        device_name="P14 Offline Device",
                        platform="ANDROID",
                        app_version="p14-rc1",
                    ),
                    DeviceRow(
                        device_id=second_device_id,
                        user_id=user_id,
                        device_name="P14 Projection Device",
                        platform="ANDROID",
                        app_version="p14-rc1",
                    ),
                ]
            )
            session.commit()

        first = Principal(user_id, first_device_id, uuid4(), AccountRole.USER)
        second = Principal(user_id, second_device_id, uuid4(), AccountRole.USER)
        profile_id = uuid4()
        first_epoch, second_epoch = uuid4(), uuid4()

        # These mutations are constructed before the server service exists, representing
        # one committed offline Journal batch which must retain its exact IDs and order.
        track_id, playlist_id, entry_id = uuid4(), uuid4(), uuid4()
        offline_events = [
            _event(
                first,
                track_id,
                1,
                "USER_TRACK_REF_CREATED",
                "USER_TRACK_REF",
                {"title": "P14 Offline Track", "artist": "P14 Artist"},
            ),
            _event(
                first,
                playlist_id,
                2,
                "PLAYLIST_CREATED",
                "PLAYLIST",
                {"name": "P14 Offline Playlist", "description": None},
            ),
            _event(
                first,
                entry_id,
                3,
                "PLAYLIST_ENTRY_UPSERTED",
                "PLAYLIST_ENTRY",
                {
                    "local_playlist_entry_id": str(entry_id),
                    "local_playlist_id": str(playlist_id),
                    "local_user_track_ref_id": str(track_id),
                    "before_local_playlist_entry_id": None,
                    "attribution": None,
                },
            ),
        ]

        service = SyncService(engine, cursor_secret=b"p14-release-cursor-secret-32bytes")
        first_binding = _bind(
            service,
            first,
            server_profile_id=profile_id,
            epoch=first_epoch,
        )
        second_binding = _bind(
            service,
            second,
            server_profile_id=profile_id,
            epoch=second_epoch,
        )
        pushed = service.push(
            first,
            {**first_binding, "events": offline_events},
            uuid4(),
        )
        assert [ack["outcome"] for ack in pushed["acks"]] == ["APPLIED"] * 3

        first_delivery = service.pull(
            second,
            {**second_binding, "cursor": None, "limit": 100},
        )
        assert [row["event_id"] for row in first_delivery["events"]] == [
            str(row["event_id"]) for row in offline_events
        ]

        # Simulate process death after bytes arrive but before the cursor is durably ACKed.
        del service
        restarted = SyncService(engine, cursor_secret=b"p14-release-cursor-secret-32bytes")
        replayed = restarted.pull(
            second,
            {**second_binding, "cursor": None, "limit": 100},
        )
        assert [row["event_id"] for row in replayed["events"]] == [
            row["event_id"] for row in first_delivery["events"]
        ]
        acknowledged = restarted.pull(
            second,
            {
                **second_binding,
                "cursor": first_delivery["next_cursor"],
                "limit": 100,
            },
        )
        assert acknowledged["events"] == []

        projection = restarted.bootstrap(
            second,
            {
                **second_binding,
                "reason": "FIRST_SYNC",
                "snapshot_id": None,
                "page_token": None,
                "pending_local_event_count": 0,
            },
        )
        aggregates = {
            (row["aggregate_type"], row["aggregate_server_id"]): row
            for row in projection["aggregates"]
        }
        assert ("USER_TRACK_REF", str(track_id)) in aggregates
        assert ("PLAYLIST", str(playlist_id)) in aggregates
        entry = aggregates[("PLAYLIST_ENTRY", str(entry_id))]
        assert entry["payload"]["server_playlist_id"] == str(playlist_id)
        assert entry["payload"]["server_user_track_ref_id"] == str(track_id)

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM sync.device_event_inbox "
                    "WHERE user_id = :user_id AND device_id = :device_id"
                ),
                {"user_id": user_id, "device_id": first_device_id},
            ).scalar_one() == len(offline_events)
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM playlist.playlist_entry AS entry "
                        "JOIN playlist.playlist AS playlist USING (playlist_id) "
                        "WHERE entry.playlist_entry_id = :entry_id "
                        "AND playlist.owner_user_id = :user_id"
                    ),
                    {"entry_id": entry_id, "user_id": user_id},
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()
