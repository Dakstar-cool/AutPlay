"""Account-owned metadata copies; credentials, media locations and server authority never port."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid5

import rfc8785
from sqlalchemy import Text, func, literal, select, text, tuple_
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import (
    ListeningEventRow,
    PlaylistEntryRow,
    PlaylistRow,
    PortableAccountRecordRow,
    PortableAccountRow,
    UserTrackPreferenceRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.portable_taste import PortableTasteSignalRow
from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.application.portable_account import PortableAccountService
from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import ProfilePairingError, iso8601

MAX_BYTES = 67_108_864
MAX_RECORDS = 200_000


def _uuid(value: Any) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("record uuid")
    return value


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("record integer")
    return value


def _text(value: Any, maximum: int, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("record text")
    return value


def validate_portable_record(record: dict[str, Any]) -> tuple[UUID, str, dict[str, Any], bytes]:
    if not isinstance(record, dict) or set(record) != {"record_id", "kind", "document"}:
        raise ValueError("record members")
    record_id = UUID(_uuid(record["record_id"]))
    kind, document = record["kind"], record["document"]
    if not isinstance(document, dict) or len(rfc8785.dumps(document)) > 16384:
        raise ValueError("record document")
    _integer(document.get("version"), 1, 1)
    _integer(document.get("updated_at_ms"), 0, 8_640_000_000_000_000)
    common = {"version", "updated_at_ms"}
    if kind == "LISTENING":
        expected = common | {
            "event_ids",
            "track_key",
            "title",
            "artist",
            "genres",
            "started_at_ms",
            "played_ms",
            "duration_ms",
            "excluded_from_taste",
            "event_origin",
            "context",
        }
        aliases = document.get("event_ids")
        if (
            not isinstance(aliases, list)
            or not 1 <= len(aliases) <= 32
            or any(not isinstance(alias, str) for alias in aliases)
            or len(set(aliases)) != len(aliases)
        ):
            raise ValueError("event ids")
        for alias in aliases:
            _uuid(alias)
        if str(record_id) not in aliases:
            raise ValueError("event identity")
        _uuid(document.get("track_key"))
        _text(document.get("title"), 2000, nullable=True)
        _text(document.get("artist"), 2000, nullable=True)
        genres = document.get("genres")
        if not isinstance(genres, list) or len(genres) > 12:
            raise ValueError("genres")
        for genre in genres:
            _text(genre, 100)
        _integer(document.get("started_at_ms"), 0, 8_640_000_000_000_000)
        _integer(document.get("played_ms"), 0, 604_800_000)
        if document.get("duration_ms") is not None:
            _integer(document["duration_ms"], 1, 604_800_000)
        if type(document.get("excluded_from_taste")) is not bool:
            raise ValueError("taste flag")
        for name, maximum in (("event_origin", 100), ("context", 50)):
            if (
                re.fullmatch(r"[A-Z][A-Z0-9_]{0," + str(maximum - 1) + "}", str(document.get(name)))
                is None
            ):
                raise ValueError("listening enum")
    elif kind == "PREFERENCE":
        expected = common | {
            "track_key",
            "title",
            "artist",
            "preference",
            "rating",
            "excluded_from_taste",
        }
        _uuid(document.get("track_key"))
        _text(document.get("title"), 2000, nullable=True)
        _text(document.get("artist"), 2000, nullable=True)
        if (
            document.get("preference") not in ("NEUTRAL", "LIKED", "DISLIKED")
            or type(document.get("excluded_from_taste")) is not bool
        ):
            raise ValueError("preference")
        if document.get("rating") is not None:
            _integer(document["rating"], 1, 5)
    elif kind == "PLAYLIST":
        expected = common | {"name", "description", "deleted"}
        _text(document.get("name"), 500)
        _text(document.get("description"), 2000, nullable=True)
        if type(document.get("deleted")) is not bool:
            raise ValueError("playlist")
    elif kind == "PLAYLIST_ENTRY":
        expected = common | {
            "playlist_id",
            "track_key",
            "title",
            "artist",
            "position_key",
            "deleted",
        }
        _uuid(document.get("playlist_id"))
        _uuid(document.get("track_key"))
        _text(document.get("title"), 2000, nullable=True)
        _text(document.get("artist"), 2000, nullable=True)
        _text(document.get("position_key"), 128)
        if type(document.get("deleted")) is not bool:
            raise ValueError("playlist entry")
    elif kind == "TASTE":
        expected = common | {"track_key", "interaction", "weight"}
        _uuid(document.get("track_key"))
        if document.get("interaction") not in ("LIKE", "DISLIKE", "SKIP", "COMPLETE", "EXCLUDE"):
            raise ValueError("taste interaction")
        _integer(document.get("weight"), -1000, 1000)
    else:
        raise ValueError("record kind")
    if set(document) != expected:
        raise ValueError("record document members")
    document = dict(document)
    if kind == "LISTENING":
        document["event_ids"] = sorted(document["event_ids"])
        document["genres"] = sorted(set(document["genres"]))
    return record_id, kind, document, hashlib.sha256(rfc8785.dumps(document)).digest()


class PortableAccountDataService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    @staticmethod
    def _owner(session: Session, actor: Principal, now: datetime) -> None:
        lock_resource_admission(session)
        PortableAccountService._actor(session, actor, now)
        if session.get(PortableAccountRow, actor.user_id) is None:
            raise ProfilePairingError("account_password_setup_required")

    def put(self, actor: Principal, body: dict[str, Any]) -> dict[str, object]:
        if set(body) != {"account_id", "records"} or body["account_id"] != str(actor.user_id):
            raise ProfilePairingError("account_identity_conflict")
        records = body["records"]
        if not isinstance(records, list) or not 1 <= len(records) <= 32:
            raise ValueError("record batch")
        validated = [validate_portable_record(record) for record in records]
        if len({item[0] for item in validated}) != len(validated):
            raise ValueError("duplicate record")
        now = datetime.now(UTC)
        with self.sessions.begin() as session:
            self._owner(session, actor, now)
            latest = session.scalar(
                select(func.max(PortableAccountRecordRow.updated_at)).where(
                    PortableAccountRecordRow.user_id == actor.user_id
                )
            )
            # _actor holds the account row lock. Never let a changed row fall behind a cursor,
            # including clock rollback and two changes to the same UUID in one microsecond.
            changed_at = max(now, latest + timedelta(microseconds=1) if latest else now)
            accepted = []
            for record_id, kind, document, digest in validated:
                row = session.get(
                    PortableAccountRecordRow, (actor.user_id, record_id), with_for_update=True
                )
                if row is not None and row.kind != kind:
                    raise ProfilePairingError("account_record_identity_conflict")
                previous_synced_at = row.updated_at if row is not None else None
                changed_record = False
                identity_changed = False
                if kind == "LISTENING":
                    aliases = document["event_ids"]
                    foreign = session.scalar(
                        select(ListeningEventRow.listening_event_id)
                        .where(
                            ListeningEventRow.listening_event_id.in_([UUID(v) for v in aliases]),
                            ListeningEventRow.user_id != actor.user_id,
                        )
                        .limit(1)
                    )
                    if foreign is not None:
                        raise ProfilePairingError("account_record_identity_conflict")
                    matches = session.scalars(
                        select(PortableAccountRecordRow)
                        .where(
                            PortableAccountRecordRow.user_id == actor.user_id,
                            PortableAccountRecordRow.kind == "LISTENING",
                            PortableAccountRecordRow.document["event_ids"].op("?|")(
                                literal(aliases, type_=ARRAY(Text()))
                            ),
                        )
                        .with_for_update()
                    ).all()
                    if row is not None and row not in matches:
                        matches = [row, *matches]
                    if len(matches) > 1:
                        matches = sorted(matches, key=lambda item: item.record_id)
                        for extra in matches[1:]:
                            if extra.document["played_ms"] != document["played_ms"]:
                                raise ProfilePairingError("account_record_identity_conflict")
                            document = self._merge_listening(extra.document, document)
                            session.delete(extra)
                    row = matches[0] if matches else row
                    if row is not None:
                        if row.kind != kind or row.document["played_ms"] != document["played_ms"]:
                            raise ProfilePairingError("account_record_identity_conflict")
                        document = self._merge_listening(row.document, document)
                        digest = hashlib.sha256(rfc8785.dumps(document)).digest()
                    record_id = min(UUID(value) for value in document["event_ids"])
                    collision = session.get(PortableAccountRecordRow, (actor.user_id, record_id))
                    if collision is not None and collision is not row and collision not in matches:
                        raise ProfilePairingError("account_record_identity_conflict")
                    if row is not None and row.record_id != record_id:
                        row.record_id = record_id
                        identity_changed = True
                if row is None:
                    row = PortableAccountRecordRow(
                        user_id=actor.user_id,
                        record_id=record_id,
                        kind=kind,
                        document=document,
                        content_hash=digest,
                        created_at=changed_at,
                        updated_at=changed_at,
                    )
                    session.add(row)
                    changed_record = True
                    changed_at += timedelta(microseconds=1)
                elif row.kind != kind:
                    raise ProfilePairingError("account_record_identity_conflict")
                elif identity_changed or (
                    row.content_hash != digest
                    and (
                        kind == "LISTENING"
                        or (document["updated_at_ms"], digest)
                        > (row.document["updated_at_ms"], row.content_hash)
                    )
                ):
                    row.document, row.content_hash, row.updated_at = document, digest, changed_at
                    changed_record = True
                    changed_at += timedelta(microseconds=1)
                self._apply(session, actor, row, row.updated_at, previous_synced_at)
                accepted.append(str(row.record_id))
                session.flush()
                if changed_record:
                    # Domain UPDATE triggers stamp clock_timestamp(), even if a portable logical
                    # timestamp was supplied. Remember the applied boundary on the same DB clock;
                    # later native mutations still take precedence and export as new revisions.
                    applied_at = session.scalar(select(func.clock_timestamp()))
                    assert applied_at is not None
                    row.updated_at = max(row.updated_at, applied_at)
                    changed_at = row.updated_at + timedelta(microseconds=1)
                    session.flush()
            usage = session.execute(
                select(
                    func.count(),
                    func.coalesce(
                        func.sum(func.octet_length(PortableAccountRecordRow.document.cast(Text()))),
                        0,
                    ),
                ).where(PortableAccountRecordRow.user_id == actor.user_id)
            ).one()
            if usage[0] > MAX_RECORDS or usage[1] > MAX_BYTES:
                raise ProfilePairingError("account_data_limit_reached")
            return {"account_id": str(actor.user_id), "accepted": accepted}

    @staticmethod
    def _merge_listening(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        if old["played_ms"] != new["played_ms"]:
            raise ProfilePairingError("account_record_identity_conflict")
        # Converge independently of upload order; ties cannot keep oscillating metadata.
        result = dict(old)
        result["event_ids"] = sorted(set(old["event_ids"]) | set(new["event_ids"]))
        if len(result["event_ids"]) > 32:
            raise ValueError("alias limit")
        result["started_at_ms"] = min(old["started_at_ms"], new["started_at_ms"])
        result["updated_at_ms"] = max(old["updated_at_ms"], new["updated_at_ms"])
        result["genres"] = sorted(set(old["genres"]) | set(new["genres"]))[:12]
        result["track_key"] = min(old["track_key"], new["track_key"])
        result["excluded_from_taste"] = old["excluded_from_taste"] or new["excluded_from_taste"]
        for name in ("context", "event_origin"):
            result[name] = min(old[name], new[name])
        for name in ("title", "artist", "duration_ms"):
            values = [value[name] for value in (old, new) if value[name] is not None]
            result[name] = min(values) if values else None
        return result

    @staticmethod
    def _track(session: Session, actor: Principal, document: dict[str, Any], now: datetime) -> UUID:
        # Namespaced unresolved metadata never grants Vault access or silently resolves a recording.
        track_id = uuid5(actor.user_id, "autplay:portable-track:v1:" + document["track_key"])
        owned = session.scalar(
            select(UserTrackRefRow)
            .where(
                UserTrackRefRow.user_id == actor.user_id,
                (UserTrackRefRow.user_track_ref_id == UUID(document["track_key"]))
                | (UserTrackRefRow.recording_id == UUID(document["track_key"])),
                UserTrackRefRow.deleted_at.is_(None),
            )
            .order_by(UserTrackRefRow.user_track_ref_id)
            .limit(1)
        )
        if owned is not None:
            return owned.user_track_ref_id
        row = session.get(UserTrackRefRow, track_id)
        if row is None:
            session.add(
                UserTrackRefRow(
                    user_track_ref_id=track_id,
                    user_id=actor.user_id,
                    raw_title=document.get("title"),
                    raw_artist=document.get("artist"),
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            session.add(
                TrackMetadataRow(
                    user_track_ref_id=track_id,
                    document={
                        "portable_track_key": document["track_key"],
                        "fields": {
                            "title": document.get("title"),
                            "artist": document.get("artist"),
                            "genres": document.get("genres", []),
                        },
                    },
                    state="READY",
                    updated_at=now,
                )
            )
        elif row.user_id != actor.user_id:
            raise ProfilePairingError("account_record_identity_conflict")
        else:
            # A taste-only record may create this reference before a descriptive record arrives.
            # Fill missing descriptions without replacing a later native metadata decision.
            changed = False
            for attribute, field in (("raw_title", "title"), ("raw_artist", "artist")):
                if getattr(row, attribute) is None and document.get(field) is not None:
                    setattr(row, attribute, document[field])
                    changed = True
            if changed:
                row.updated_at = now
                row.row_version += 1
        return track_id

    @classmethod
    def _apply(
        cls,
        session: Session,
        actor: Principal,
        row: PortableAccountRecordRow,
        now: datetime,
        previous_synced_at: datetime | None = None,
    ) -> None:
        document = cast(dict[str, Any], row.document)
        effective_at = datetime.fromtimestamp(cast(int, document["updated_at_ms"]) / 1000, UTC)
        if row.kind == "LISTENING":
            ids = [UUID(value) for value in cast(list[str], document["event_ids"])]
            existing = session.scalar(
                select(ListeningEventRow)
                .where(
                    ListeningEventRow.user_id == actor.user_id,
                    ListeningEventRow.listening_event_id.in_(ids),
                )
                .limit(1)
            )
            if existing is None:
                track = cls._track(session, actor, document, now)
                session.add(
                    ListeningEventRow(
                        listening_event_id=row.record_id,
                        user_id=actor.user_id,
                        device_id=actor.device_id,
                        user_track_ref_id=track,
                        started_at=datetime.fromtimestamp(
                            cast(int, document["started_at_ms"]) / 1000, UTC
                        ),
                        played_ms=document["played_ms"],
                        track_duration_ms=document["duration_ms"],
                        excluded_from_taste=document["excluded_from_taste"],
                        event_origin=document["event_origin"]
                        if document["event_origin"] in {"ORGANIC", "PLAYLIST", "SEARCH", "WAVE"}
                        else "ORGANIC",
                        context=document["context"]
                        if document["context"]
                        in {"GENERAL", "WORKOUT", "CYCLING", "WORK", "SLEEP", "PARTY"}
                        else "GENERAL",
                    )
                )
        elif row.kind == "PREFERENCE":
            track = cls._track(session, actor, document, now)
            preference = session.get(UserTrackPreferenceRow, track)
            if preference is None:
                session.add(
                    UserTrackPreferenceRow(
                        user_track_ref_id=track,
                        preference=document["preference"],
                        rating=document["rating"],
                        excluded_from_taste=document["excluded_from_taste"],
                        updated_at=effective_at,
                    )
                )
            elif (
                preference.updated_at <= effective_at
                or (previous_synced_at is not None and preference.updated_at <= previous_synced_at)
            ) and (
                preference.updated_at < effective_at
                or (preference.preference, preference.rating, preference.excluded_from_taste)
                != (document["preference"], document["rating"], document["excluded_from_taste"])
            ):
                preference.preference, preference.rating = (
                    str(document["preference"]),
                    cast(int | None, document["rating"]),
                )
                preference.excluded_from_taste, preference.updated_at = (
                    bool(document["excluded_from_taste"]),
                    effective_at,
                )
                preference.row_version += 1
        elif row.kind == "PLAYLIST":
            playlist = cls._playlist(session, actor, row.record_id, now)
            deleted_at = now if document["deleted"] else None
            if (
                playlist.updated_at <= effective_at
                or (previous_synced_at is not None and playlist.updated_at <= previous_synced_at)
            ) and (
                playlist.updated_at < effective_at
                or (playlist.name, playlist.description, playlist.deleted_at is not None)
                != (document["name"], document["description"], document["deleted"])
            ):
                playlist.name = document["name"]
                playlist.description = document["description"]
                playlist.deleted_at, playlist.updated_at = deleted_at, effective_at
                playlist.row_version += 1
        elif row.kind == "PLAYLIST_ENTRY":
            playlist_id = UUID(document["playlist_id"])
            cls._playlist(session, actor, playlist_id, now)
            track = cls._track(session, actor, document, now)
            entry = session.get(PlaylistEntryRow, row.record_id)
            if entry is not None:
                parent = session.get(PlaylistRow, entry.playlist_id)
                if parent is None or parent.owner_user_id != actor.user_id:
                    raise ProfilePairingError("account_record_identity_conflict")
            # Stable suffix allows independently created entries at the same fractional position.
            position = document["position_key"][:91] + ":" + str(row.record_id)
            if entry is None:
                session.add(
                    PlaylistEntryRow(
                        playlist_entry_id=row.record_id,
                        playlist_id=playlist_id,
                        user_track_ref_id=track,
                        added_by_user_id=actor.user_id,
                        position_key=position,
                        removed_at=now if document["deleted"] else None,
                        created_at=now,
                        updated_at=effective_at,
                    )
                )
            elif (
                entry.updated_at <= effective_at
                or (previous_synced_at is not None and entry.updated_at <= previous_synced_at)
            ) and (
                entry.updated_at < effective_at
                or (
                    entry.playlist_id,
                    entry.user_track_ref_id,
                    entry.position_key,
                    entry.removed_at is not None,
                )
                != (playlist_id, track, position, document["deleted"])
            ):
                entry.playlist_id, entry.user_track_ref_id, entry.position_key = (
                    playlist_id,
                    track,
                    position,
                )
                entry.removed_at, entry.updated_at = (
                    now if document["deleted"] else None,
                    effective_at,
                )
                entry.row_version += 1
        elif row.kind == "TASTE":
            track = cls._track(session, actor, document, now)
            signal = session.get(PortableTasteSignalRow, (actor.user_id, row.record_id))
            if signal is None:
                session.add(
                    PortableTasteSignalRow(
                        user_id=actor.user_id,
                        record_id=row.record_id,
                        user_track_ref_id=track,
                        interaction=document["interaction"],
                        weight=document["weight"],
                        occurred_at_ms=document["updated_at_ms"],
                        updated_at=effective_at,
                    )
                )
            elif signal.updated_at <= effective_at and (
                signal.updated_at < effective_at
                or (signal.user_track_ref_id, signal.interaction, signal.weight)
                != (track, document["interaction"], document["weight"])
            ):
                signal.user_track_ref_id, signal.interaction, signal.weight = (
                    track,
                    document["interaction"],
                    document["weight"],
                )
                signal.occurred_at_ms, signal.updated_at = document["updated_at_ms"], effective_at
                signal.row_version += 1

    @staticmethod
    def _playlist(
        session: Session, actor: Principal, playlist_id: UUID, now: datetime
    ) -> PlaylistRow:
        row = session.get(PlaylistRow, playlist_id)
        if row is None:
            # An entry can arrive before its playlist. The later playlist record fills the title.
            row = PlaylistRow(
                playlist_id=playlist_id,
                owner_user_id=actor.user_id,
                name="Imported playlist",
                visibility="PRIVATE",
                playlist_type="MANUAL",
                created_at=now,
                updated_at=datetime(1970, 1, 1, tzinfo=UTC),
            )
            session.add(row)
            session.flush()
        elif row.owner_user_id != actor.user_id:
            raise ProfilePairingError("account_record_identity_conflict")
        return row

    def get(
        self, actor: Principal, source: str, after_time: datetime, after_id: UUID
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        with self.sessions.begin() as session:
            self._owner(session, actor, now)
            records: list[dict[str, Any]] = []
            cursor = None
            if source == "records":
                rows = session.scalars(
                    select(PortableAccountRecordRow)
                    .where(
                        PortableAccountRecordRow.user_id == actor.user_id,
                        tuple_(
                            PortableAccountRecordRow.updated_at, PortableAccountRecordRow.record_id
                        )
                        > tuple_(literal(after_time), literal(after_id)),
                    )
                    .order_by(
                        PortableAccountRecordRow.updated_at, PortableAccountRecordRow.record_id
                    )
                    .limit(32)
                ).all()
                for row in rows:
                    records.append(
                        {
                            "record_id": str(row.record_id),
                            "kind": row.kind,
                            "document": row.document,
                        }
                    )
                    cursor = {"time": iso8601(row.updated_at), "id": str(row.record_id)}
            else:
                records, cursor = self._legacy_page(session, actor, source, after_time, after_id)
            return {
                "account_id": str(actor.user_id),
                "records": records,
                "next": cursor if len(records) == 32 else None,
                "cursor": cursor,
            }

    @staticmethod
    def _track_document(session: Session, actor: Principal, track_id: UUID) -> dict[str, Any]:
        track = session.get(UserTrackRefRow, track_id)
        if track is None or track.user_id != actor.user_id:
            raise ProfilePairingError("account_record_identity_conflict")
        metadata = session.get(TrackMetadataRow, track_id)
        fields = metadata.document.get("fields", {}) if metadata else {}
        if not isinstance(fields, dict):
            fields = {}

        def safe_text(value: Any, limit: int) -> str | None:
            if not isinstance(value, str):
                return None
            return "".join(c for c in value if ord(c) >= 32).strip()[:limit] or None

        raw_genres = fields.get("genres", [])
        genres = (
            sorted({cleaned for value in raw_genres if (cleaned := safe_text(value, 100))})[:12]
            if isinstance(raw_genres, list)
            else []
        )
        key = str(track.recording_id or track.user_track_ref_id)
        if metadata and isinstance(metadata.document.get("portable_track_key"), str):
            key = _uuid(metadata.document["portable_track_key"])
        return {
            "track_key": key,
            "title": safe_text(fields.get("title") or track.raw_title, 2000),
            "artist": safe_text(fields.get("artist") or track.raw_artist, 2000),
            "genres": genres,
        }

    @classmethod
    def _legacy_page(
        cls,
        session: Session,
        actor: Principal,
        source: str,
        after_time: datetime,
        after_id: UUID,
    ) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
        # These fixed sources project only portable fields, never source paths, grants or evidence.
        queries = {
            "history": """SELECT e.listening_event_id AS id,e.created_at AS time,
                e.user_track_ref_id AS track_id,'LISTENING' AS kind,
                jsonb_build_object('event_ids',jsonb_build_array(e.listening_event_id::text),
                'started_at_ms',floor(extract(epoch FROM e.started_at)*1000)::bigint,
                'played_ms',e.played_ms,'duration_ms',e.track_duration_ms,
                'excluded_from_taste',e.excluded_from_taste,'event_origin',e.event_origin,
                'context',e.context) AS document
                FROM library.listening_event e WHERE e.user_id=:owner""",
            "preferences": """SELECT p.user_track_ref_id AS id,p.updated_at AS time,
                p.user_track_ref_id AS track_id,'PREFERENCE' AS kind,
                jsonb_build_object('preference',p.preference,'rating',p.rating,
                'excluded_from_taste',p.excluded_from_taste) AS document
                FROM library.user_track_preference p JOIN library.user_track_ref u
                ON u.user_track_ref_id=p.user_track_ref_id WHERE u.user_id=:owner""",
            "playlists": """SELECT p.playlist_id AS id,p.updated_at AS time,
                NULL::uuid AS track_id,'PLAYLIST' AS kind,
                jsonb_build_object('name',p.name,'description',p.description,
                'deleted',p.deleted_at IS NOT NULL) AS document
                FROM playlist.playlist p WHERE p.owner_user_id=:owner""",
            "playlist_entries": """SELECT e.playlist_entry_id AS id,e.updated_at AS time,
                e.user_track_ref_id AS track_id,'PLAYLIST_ENTRY' AS kind,
                jsonb_build_object('playlist_id',e.playlist_id::text,'position_key',e.position_key,
                'deleted',e.removed_at IS NOT NULL) AS document
                FROM playlist.playlist_entry e
                JOIN playlist.playlist p ON p.playlist_id=e.playlist_id
                JOIN library.user_track_ref u ON u.user_track_ref_id=e.user_track_ref_id
                WHERE p.owner_user_id=:owner AND u.user_id=:owner""",
            "tastes": """SELECT t.record_id AS id,t.updated_at AS time,
                t.user_track_ref_id AS track_id,'TASTE' AS kind,
                jsonb_build_object('interaction',t.interaction,'weight',t.weight) AS document
                FROM library.portable_taste_signal t WHERE t.user_id=:owner
                UNION ALL SELECT e.listening_event_id AS id,e.created_at AS time,
                e.user_track_ref_id AS track_id,'TASTE' AS kind,
                jsonb_build_object('interaction',CASE
                    WHEN e.explicit_feedback='LIKE' THEN 'LIKE'
                    WHEN e.explicit_feedback='DISLIKE' THEN 'DISLIKE'
                    WHEN e.excluded_from_taste THEN 'EXCLUDE'
                    WHEN e.completion_ratio>=0.9 THEN 'COMPLETE' ELSE 'SKIP' END,
                    'weight',CASE WHEN e.explicit_feedback='DISLIKE' THEN -1000
                    WHEN e.explicit_feedback='LIKE' THEN 1000
                    WHEN e.excluded_from_taste THEN 0
                    WHEN e.completion_ratio>=0.9 THEN 1000 ELSE -500 END,
                    'legacy_listen',true) AS document
                FROM library.listening_event e WHERE e.user_id=:owner AND
                    (e.explicit_feedback<>'NONE' OR e.excluded_from_taste
                    OR e.completion_ratio>=0.9 OR e.played_ms BETWEEN 1 AND 29999)
                UNION ALL SELECT i.interaction_id AS id,i.created_at AS time,
                NULL::uuid AS track_id,'TASTE' AS kind,
                jsonb_build_object('track_key',i.recording_id::text,
                    'interaction',CASE WHEN i.payload->>'feedback_type'='SELECTED'
                        THEN 'LIKE' ELSE 'DISLIKE' END,
                    'weight',CASE WHEN i.payload->>'feedback_type'='SELECTED'
                        THEN 1000 ELSE -1000 END) AS document
                FROM library.user_interaction_event i WHERE i.user_id=:owner
                    AND i.event_type='RECOMMENDATION_FEEDBACK_RECORDED'
                    AND i.recording_id IS NOT NULL
                    AND i.payload->>'feedback_type' IN ('SELECTED','DISMISSED')""",
        }
        if source not in queries:
            raise ValueError("record source")
        rows = (
            session.execute(
                text(
                    "SELECT * FROM (" + queries[source] + ") AS portable_source "
                    "WHERE (time,id)>(:after_time,:after_id) ORDER BY time,id LIMIT 32"
                ),
                {"owner": actor.user_id, "after_time": after_time, "after_id": after_id},
            )
            .mappings()
            .all()
        )
        records: list[dict[str, Any]] = []
        cursor = None
        for value in rows:
            record_id, kind, changed = value["id"], value["kind"], value["time"]
            document = dict(value["document"])
            legacy_taste = document.pop("legacy_listen", False)
            document.update(version=1, updated_at_ms=int(changed.timestamp() * 1000))
            if value["track_id"] is not None:
                metadata = cls._track_document(session, actor, value["track_id"])
                if kind != "LISTENING":
                    metadata.pop("genres")
                if kind == "TASTE":
                    metadata = {"track_key": metadata["track_key"]}
                document.update(metadata)
            if kind == "PREFERENCE":
                record_id = uuid5(
                    actor.user_id, "autplay:portable-preference:v1:" + document["track_key"]
                )
            elif legacy_taste:
                record_id = uuid5(record_id, "autplay:portable-taste:v1:" + document["interaction"])
            stored = session.get(PortableAccountRecordRow, (actor.user_id, record_id))
            if kind == "LISTENING":
                stored = session.scalar(
                    select(PortableAccountRecordRow)
                    .where(
                        PortableAccountRecordRow.user_id == actor.user_id,
                        PortableAccountRecordRow.kind == kind,
                        PortableAccountRecordRow.document["event_ids"].op("?")(str(value["id"])),
                    )
                    .limit(1)
                )
            elif kind == "PREFERENCE":
                stored = session.scalar(
                    select(PortableAccountRecordRow)
                    .where(
                        PortableAccountRecordRow.user_id == actor.user_id,
                        PortableAccountRecordRow.kind == kind,
                        PortableAccountRecordRow.document["track_key"].astext
                        == document["track_key"],
                        PortableAccountRecordRow.updated_at >= changed,
                    )
                    .order_by(PortableAccountRecordRow.record_id)
                    .limit(1)
                )
            # Preserve the exact portable revision/hash when exporting a previously applied copy.
            if (
                stored is not None
                and stored.kind == kind
                and (kind == "LISTENING" or stored.updated_at >= changed)
            ):
                record_id, document = stored.record_id, dict(stored.document)
            records.append({"record_id": str(record_id), "kind": kind, "document": document})
            cursor = {"time": iso8601(changed), "id": str(value["id"])}
        return records, cursor
