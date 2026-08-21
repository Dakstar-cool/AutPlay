"""Transaction-owned catalog Artist mutations and sync fanout.

This is intentionally not a public browse API.  It is the only application seam
needed by catalog writers that must make a canonical Artist/credit change and its
owner-visible sync facts atomic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import (
    ArtistCreditNameRow,
    ArtistCreditRow,
    ArtistRow,
    MediumRow,
    RecordingRow,
    ReleaseRow,
    ReleaseTrackRow,
    UserTrackRefRow,
)
from autplay.application.sync import CatalogArtistSyncPublisher


@dataclass(frozen=True)
class ArtistCreditMember:
    """One ordered Artist credit member; UUID identity is never inferred from text."""

    artist_id: UUID
    credited_name: str
    join_phrase: str
    role: str


_MAX_MEMBERS: Final = 1000


class CatalogArtistMutationService:
    """Apply catalog identity changes and sync them in the same database transaction."""

    def __init__(
        self, engine: Engine, *, publisher: CatalogArtistSyncPublisher | None = None
    ) -> None:
        self._sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        self._publisher = publisher or CatalogArtistSyncPublisher()

    def rename_artist(
        self,
        artist_id: UUID,
        *,
        name: str,
        sort_name: str,
        normalized_name: str,
    ) -> int:
        """Rename an existing Artist without allocating or replacing its UUID."""
        with self._sessions.begin() as session:
            artist = session.get(ArtistRow, artist_id)
            if artist is None:
                raise ValueError("artist not found")
            artist.name = _text(name, 1000)
            artist.sort_name = _text(sort_name, 1000)
            artist.normalized_name = _text(normalized_name, 1000)
            session.flush()
            return self._publish_affected(session, _owners_for_artist(session, artist_id))

    def replace_credit_members(
        self, artist_credit_id: UUID, members: tuple[ArtistCreditMember, ...]
    ) -> int:
        """Replace one credit's ordered members, preserving the parent credit UUID."""
        if len(members) > _MAX_MEMBERS:
            raise ValueError("too many credit members")
        with self._sessions.begin() as session:
            credit = session.get(ArtistCreditRow, artist_credit_id)
            if credit is None:
                raise ValueError("artist credit not found")
            existing = list(
                session.scalars(
                    select(ArtistCreditNameRow).where(
                        ArtistCreditNameRow.artist_credit_id == artist_credit_id
                    )
                )
            )
            for row in existing:
                session.delete(row)
            # PostgreSQL must observe removals before equal positions are inserted again.
            session.flush()
            for position, member in enumerate(members):
                session.add(
                    ArtistCreditNameRow(
                        artist_credit_id=artist_credit_id,
                        position=position,
                        artist_id=member.artist_id,
                        credited_name=_text(member.credited_name, 1000),
                        join_phrase=member.join_phrase,
                        role=member.role,
                    )
                )
            session.flush()
            return self._publish_affected(session, _owners_for_credits(session, {artist_credit_id}))

    def update_artist_metadata(
        self,
        artist_id: UUID,
        *,
        artist_type: str,
        disambiguation: str | None,
        country_code: str | None,
        identity_status: str,
    ) -> int:
        """Change non-name Artist metadata while retaining the canonical UUID."""
        with self._sessions.begin() as session:
            artist = session.get(ArtistRow, artist_id)
            if artist is None:
                raise ValueError("artist not found")
            artist.artist_type = _text(artist_type, 100)
            artist.disambiguation = disambiguation
            artist.country_code = country_code
            artist.identity_status = _text(identity_status, 100)
            session.flush()
            return self._publish_affected(session, _owners_for_artist(session, artist_id))

    def update_credit_display_name(
        self, artist_credit_id: UUID, *, display_name: str, normalized_name: str
    ) -> int:
        """Update credit wording and publish the full ordered credit snapshot."""
        with self._sessions.begin() as session:
            credit = session.get(ArtistCreditRow, artist_credit_id)
            if credit is None:
                raise ValueError("artist credit not found")
            credit.display_name = _text(display_name, 2000)
            credit.normalized_name = _text(normalized_name, 2000)
            session.flush()
            return self._publish_affected(session, _owners_for_credits(session, {artist_credit_id}))

    def reassign_recording_credit(self, recording_id: UUID, artist_credit_id: UUID) -> int:
        """Change a recording link and fan out the replacement edge atomically."""
        with self._sessions.begin() as session:
            recording = session.get(RecordingRow, recording_id)
            if recording is None or session.get(ArtistCreditRow, artist_credit_id) is None:
                raise ValueError("recording or artist credit not found")
            owners = _owners_for_recording(session, recording_id)
            recording.artist_credit_id = artist_credit_id
            session.flush()
            return self._publish_affected(session, owners)

    def reassign_release_credit(self, release_id: UUID, artist_credit_id: UUID) -> int:
        """Change a release link and fan out the replacement edge atomically."""
        with self._sessions.begin() as session:
            release = session.get(ReleaseRow, release_id)
            if release is None or session.get(ArtistCreditRow, artist_credit_id) is None:
                raise ValueError("release or artist credit not found")
            owners = _owners_for_release(session, release_id)
            release.artist_credit_id = artist_credit_id
            session.flush()
            return self._publish_affected(session, owners)

    def move_credit_member(
        self,
        *,
        source_credit_id: UUID,
        source_position: int,
        target_credit_id: UUID,
        target_position: int,
    ) -> int:
        """Move a member and atomically republish the union of old/new credit owners."""
        if source_position < 0 or target_position < 0:
            raise ValueError("credit position must be non-negative")
        with self._sessions.begin() as session:
            row = session.get(ArtistCreditNameRow, (source_credit_id, source_position))
            if row is None or session.get(ArtistCreditRow, target_credit_id) is None:
                raise ValueError("artist credit member not found")
            session.delete(row)
            session.add(
                ArtistCreditNameRow(
                    artist_credit_id=target_credit_id,
                    position=target_position,
                    artist_id=row.artist_id,
                    credited_name=row.credited_name,
                    join_phrase=row.join_phrase,
                    role=row.role,
                )
            )
            session.flush()
            return self._publish_affected(
                session, _owners_for_credits(session, {source_credit_id, target_credit_id})
            )

    def _publish_affected(self, session: Session, owners: set[UUID]) -> int:
        return sum(self._publisher.publish(session, owner) for owner in sorted(owners, key=str))


def _owners_for_artist(session: Session, artist_id: UUID) -> set[UUID]:
    credit_ids = set(
        session.scalars(
            select(ArtistCreditNameRow.artist_credit_id).where(
                ArtistCreditNameRow.artist_id == artist_id
            )
        )
    )
    return _owners_for_credits(session, credit_ids)


def _owners_for_credits(session: Session, credit_ids: set[UUID]) -> set[UUID]:
    if not credit_ids:
        return set()
    recording_owners = session.scalars(
        select(UserTrackRefRow.user_id)
        .join_from(UserTrackRefRow, RecordingRow)
        .where(
            RecordingRow.artist_credit_id.in_(credit_ids),
            UserTrackRefRow.deleted_at.is_(None),
            UserTrackRefRow.recording_id.is_not(None),
        )
        .distinct()
    )
    release_owners = session.scalars(
        select(UserTrackRefRow.user_id)
        .join_from(UserTrackRefRow, RecordingRow)
        .join(ReleaseTrackRow, ReleaseTrackRow.recording_id == UserTrackRefRow.recording_id)
        .join(MediumRow, MediumRow.medium_id == ReleaseTrackRow.medium_id)
        .join(ReleaseRow, ReleaseRow.release_id == MediumRow.release_id)
        .where(
            ReleaseRow.artist_credit_id.in_(credit_ids),
            UserTrackRefRow.deleted_at.is_(None),
            UserTrackRefRow.recording_id.is_not(None),
        )
        .distinct()
    )
    return set(recording_owners) | set(release_owners)


def _owners_for_recording(session: Session, recording_id: UUID) -> set[UUID]:
    return set(
        session.scalars(
            select(UserTrackRefRow.user_id).where(
                UserTrackRefRow.recording_id == recording_id,
                UserTrackRefRow.deleted_at.is_(None),
            )
        )
    )


def _owners_for_release(session: Session, release_id: UUID) -> set[UUID]:
    return set(
        session.scalars(
            select(UserTrackRefRow.user_id)
            .join(ReleaseTrackRow, ReleaseTrackRow.recording_id == UserTrackRefRow.recording_id)
            .join(MediumRow, MediumRow.medium_id == ReleaseTrackRow.medium_id)
            .where(
                MediumRow.release_id == release_id,
                UserTrackRefRow.deleted_at.is_(None),
                UserTrackRefRow.recording_id.is_not(None),
            )
            .distinct()
        )
    )


def _text(value: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("invalid catalog text")
    return value


__all__ = ("ArtistCreditMember", "CatalogArtistMutationService")
