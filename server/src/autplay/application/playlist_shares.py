"""Capture the sender's ordered metadata independently of private library ownership."""

import hashlib
import json
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


class SnapshotTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: UUID | None = None
    title: str | None = Field(default=None, max_length=2048)
    artist: str | None = Field(default=None, max_length=2048)
    duration_ms: int | None = Field(default=None, ge=0, le=2**63 - 1)


class SnapshotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    playlist_id: UUID
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    tracks: list[SnapshotTrack] = Field(max_length=10_000)
    entries: list[Annotated[int, Field(strict=True, ge=0)]] = Field(max_length=10_000)

    @model_validator(mode="after")
    def validate_entries(self) -> SnapshotBody:
        if not self.name.strip() or set(self.entries) != set(range(len(self.tracks))):
            raise ValueError("Invalid playlist name or track indices")
        if (
            len(self.name.encode("utf-16-le")) > 240
            or len((self.description or "").encode("utf-16-le")) > 1000
        ):
            raise ValueError("Playlist metadata exceeds the Android editor limit")
        fields = [self.name, self.description or ""]
        for track in self.tracks:
            fields.extend((track.title or "", track.artist or ""))
        if any("\x00" in field for field in fields):
            raise ValueError("Invalid metadata")
        sizes = [
            len((track.title or "").encode()) + len((track.artist or "").encode())
            for track in self.tracks
        ]
        if sum(sizes[index] for index in self.entries) > 4 * 1024 * 1024:
            raise ValueError("Playlist text exceeds the sharing limit")
        return self


class SnapshotTooLarge(ValueError):
    """The saved version exceeds the portable document limit."""


class PlaylistShares:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def publish(self, owner: UUID, body: SnapshotBody) -> UUID:
        with self.sessions.begin() as session:
            refs = [track.ref for track in body.tracks if track.ref is not None]
            identities = (
                {
                    ref: (str(recording) if recording else None, mbid)
                    for ref, recording, mbid in session.execute(
                        text("""
                    WITH owned AS (
                        SELECT u.user_track_ref_id, coalesce(u.recording_id, i.source_recording_id)
                            AS recording_id
                        FROM library.user_track_ref u
                        LEFT JOIN playlist.imported_track_reference i USING(user_track_ref_id)
                        WHERE u.user_id=:owner AND u.user_track_ref_id=ANY(CAST(:refs AS uuid[]))
                          AND u.deleted_at IS NULL
                    ), candidates AS (
                        SELECT o.user_track_ref_id, x.external_reference_id
                        FROM owned o JOIN identity.external_reference x USING(recording_id)
                        UNION ALL
                        SELECT o.user_track_ref_id, l.external_reference_id
                        FROM owned o JOIN library.user_track_ref_external_reference l
                          USING(user_track_ref_id)
                        WHERE l.relation_role IN ('PRIMARY_SOURCE','IMPORT_EVIDENCE')
                    ), mbids AS (
                        SELECT c.user_track_ref_id, min(x.external_id) AS mbid
                        FROM candidates c JOIN identity.external_reference x
                          USING(external_reference_id)
                        JOIN identity.source_provider sp USING(provider_id)
                        WHERE sp.provider_key='musicbrainz' AND sp.deleted_at IS NULL
                          AND x.external_entity_type='RECORDING' AND x.deleted_at IS NULL
                        GROUP BY c.user_track_ref_id HAVING count(DISTINCT x.external_id)=1
                    )
                    SELECT o.user_track_ref_id, o.recording_id, mbids.mbid
                    FROM owned o LEFT JOIN mbids USING(user_track_ref_id)
                """),
                        {"owner": owner, "refs": refs},
                    )
                }
                if refs
                else {}
            )
            tracks = []
            for track in body.tracks:
                recording, mbid = identities.get(track.ref, (None, None))
                tracks.append(
                    {
                        **track.model_dump(exclude={"ref"}),
                        "recording_id": recording,
                        "recording_mbid": mbid,
                    }
                )
            document = {
                "format": "autplay-playlist-v1",
                "name": body.name,
                "description": body.description,
                "tracks": tracks,
                "entries": body.entries,
            }
            encoded = json.dumps(
                document, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
            if len(encoded.encode()) > 8 * 1024 * 1024:
                raise SnapshotTooLarge
            # Two phone-only recordings can have identical labels. Their private reference IDs
            # distinguish the source versions without exposing the identifiers to a recipient.
            digest = hashlib.sha256(owner.bytes + body.playlist_id.bytes)
            for track in body.tracks:
                digest.update(b"\x01" + track.ref.bytes if track.ref else b"\x00")
            digest.update(encoded.encode())
            key = digest.digest()
            token = session.scalar(
                text("""
                INSERT INTO playlist.shared_snapshot(token, version_key, document)
                VALUES (:token, :key, CAST(:document AS jsonb))
                ON CONFLICT (version_key) DO NOTHING RETURNING token
            """),
                {"token": uuid4(), "key": key, "document": encoded},
            )
            if token is None:
                token = session.scalar(
                    text("SELECT token FROM playlist.shared_snapshot WHERE version_key=:key"),
                    {"key": key},
                )
            assert isinstance(token, UUID)
            return token

    def snapshot(self, token: UUID) -> dict[str, Any] | None:
        with self.sessions() as session:
            document = session.scalar(
                text("SELECT document FROM playlist.shared_snapshot WHERE token=:token"),
                {"token": token},
            )
            if document is not None and not isinstance(document, dict):
                raise ValueError("Invalid stored playlist snapshot")
            return document
