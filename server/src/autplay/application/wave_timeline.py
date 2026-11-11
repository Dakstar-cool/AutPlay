"""Read the current durable playback intent for authoritative room recovery."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def playback_command(session: Session, room_id: UUID) -> dict[str, object] | None:
    row = (
        session.execute(
            text(
                "SELECT command_sequence,command_kind,command_document FROM wave.command "
                "WHERE room_id=:room AND command_kind IN "
                "('PLAY','PAUSE','SKIP','START_ABORTED','HOST_LOST','CLOSE','EXPIRE') "
                "ORDER BY command_sequence DESC LIMIT 1"
            ),
            {"room": room_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return {
        "sequence": row.command_sequence,
        "kind": row.command_kind,
        "payload": row.command_document,
    }
