"""Get ordered performers from the recording's own catalog attribution."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.domain.artist_credits import RecordingCredit, recording_credit

# The enclosing query binds r to the recording. Partial and provisional credit lists
# cannot establish aliases, even when two of their strings happen to look alike.
CATALOG_MEMBERS = """
    (SELECT jsonb_agg(jsonb_build_object(
        'mbid', ar.artist_id, 'name', ar.name, 'credited_name', n.credited_name,
        'join_phrase', n.join_phrase) ORDER BY n.position)
     FROM catalog.artist_credit_name n JOIN catalog.artist ar USING(artist_id)
     WHERE n.artist_credit_id=r.artist_credit_id
     HAVING bool_and(ar.identity_status='ACTIVE' AND ar.deleted_at IS NULL) AND count(*)<=100)
"""


def credit_for_recording(session: Session, recording: UUID | None) -> RecordingCredit | None:
    if recording is None:
        return None
    members = session.scalar(text(f"SELECT {CATALOG_MEMBERS} FROM catalog.recording r "
                                  "WHERE r.recording_id=:id AND r.deleted_at IS NULL"), {"id": recording})
    return recording_credit(members)
