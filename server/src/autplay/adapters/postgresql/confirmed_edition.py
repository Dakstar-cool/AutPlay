"""Project verified MusicBrainz credits and exact disc positions into the catalog."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models import (
    ArtistCreditNameRow,
    ArtistCreditRow,
    ArtistRow,
    ExternalReferenceRow,
    MediumRow,
    RecordingRow,
    ReleaseGroupRow,
    ReleaseRow,
    ReleaseTrackRow,
    SourceProviderRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from autplay.domain.import_identity import normalize_text
from autplay.domain.track_metadata import MetadataCandidate


def _key(kind: str, identity: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://musicbrainz.org/{kind}/{identity}")


def _provider(session: Session) -> UUID:
    # Serialize external identity creation across overlapping recording and release credits.
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended("autplay:musicbrainz-catalog", 0)))
    )
    session.execute(
        insert(SourceProviderRow)
        .values(
            provider_key="musicbrainz",
            display_name="MusicBrainz",
            adapter_id="musicbrainz",
            adapter_version="1",
            capabilities=["METADATA"],
        )
        .on_conflict_do_nothing(index_elements=["provider_key"])
    )
    return session.scalars(
        select(SourceProviderRow.provider_id).where(SourceProviderRow.provider_key == "musicbrainz")
    ).one()


def _external(session: Session, provider: UUID, kind: str, mbid: str) -> ExternalReferenceRow:
    values = dict(
        provider_id=provider, external_entity_type=kind, external_id=mbid, market_scope="GLOBAL"
    )
    session.execute(
        insert(ExternalReferenceRow)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_external_reference_namespace")
    )
    return session.scalars(select(ExternalReferenceRow).filter_by(**values).with_for_update()).one()


def musicbrainz_reference(session: Session, kind: str, mbid: str) -> ExternalReferenceRow:
    return _external(session, _provider(session), kind, mbid)


def _credit(session: Session, provider: UUID, members: list[dict[str, str]]) -> UUID:
    names: list[dict[str, Any]] = []
    for item in members:
        ref = _external(session, provider, "ARTIST", item["mbid"])
        if ref.artist_id is None:
            artist_id = _key("artist", item["mbid"])
            session.execute(
                insert(ArtistRow)
                .values(
                    artist_id=artist_id,
                    name=item["name"],
                    sort_name=item["name"],
                    normalized_name=normalize_text(item["name"]),
                    identity_status="ACTIVE",
                )
                .on_conflict_do_nothing()
            )
            ref.artist_id = artist_id
        names.append(
            dict(
                position=len(names),
                artist_id=ref.artist_id,
                credited_name=item["credited_name"],
                join_phrase=item["join_phrase"],
                role=item["role"],
            )
        )
    digest = hashlib.sha256(json.dumps(names, default=str, sort_keys=True).encode()).hexdigest()
    identity = _key("credit", digest)
    label = "".join(n["credited_name"] + n["join_phrase"] for n in names)
    created = session.scalar(
        insert(ArtistCreditRow)
        .values(
            artist_credit_id=identity,
            display_name=label,
            normalized_name=normalize_text(label),
        )
        .on_conflict_do_nothing()
        .returning(ArtistCreditRow.artist_credit_id)
    )
    if created:
        session.execute(
            insert(ArtistCreditNameRow), [dict(n, artist_credit_id=identity) for n in names]
        )
    return identity


def current_snapshot(row: TrackMetadataRow | None) -> dict[str, Any] | None:
    from autplay.application.metadata_projection import coherent_album_group

    if row is None:
        return None
    snapshot = row.document.get("catalog_identity_v1")
    if not isinstance(snapshot, dict):
        return None
    group = coherent_album_group(row.document)
    fields = snapshot.get("fields")
    if not isinstance(fields, dict):
        return None
    actual = row.document.get("fields", {})
    provenance = row.document.get("provenance", {})
    if any(
        (provenance.get(key) or {}).get("source") == "USER" and actual.get(key) != fields.get(key)
        for key in ("artist", "album_artist", "disc_number", "track_number", "title", "album")
    ):
        return None
    if (
        not group
        or group["provider"] != "MUSICBRAINZ"
        or group["release_id"] != fields["mb_release_id"]
        or row.document.get("fields", {}).get("mb_recording_id") != fields["mb_recording_id"]
    ):
        return None
    return snapshot


def confirmed_credit(session: Session, snapshot: dict[str, Any]) -> UUID:
    return _credit(session, _provider(session), snapshot["recording_members"])


def store_confirmed_edition(
    session: Session,
    ref: UserTrackRefRow,
    metadata: TrackMetadataRow,
    candidate: MetadataCandidate,
) -> None:
    fields = candidate.fields
    if (
        not candidate.release_hydrated
        or candidate.release_position_ambiguous
        or not candidate.mb_release_track_id
        or not candidate.release_members
        or not all(
            fields.get(k)
            for k in (
                "mb_recording_id",
                "mb_release_id",
                "mb_release_group_id",
                "album",
                "title",
                "disc_number",
                "track_number",
            )
        )
    ):
        return
    snapshot = {
        "fields": fields,
        "release_track": candidate.mb_release_track_id,
        "type": candidate.release_primary_type,
        "status": candidate.release_status,
        "recording_members": list(candidate.recording_members),
        "track_members": list(candidate.track_members),
        "release_members": list(candidate.release_members),
    }
    document = {**metadata.document, "catalog_identity_v1": snapshot}
    if len(json.dumps(document, ensure_ascii=False).encode()) > 60_000:
        return
    metadata.document = document
    if ref.recording_id and current_snapshot(metadata) and project_edition(session, ref, snapshot):
        from autplay.application.sync import CatalogArtistSyncPublisher

        session.flush()
        CatalogArtistSyncPublisher().publish(session, ref.user_id, ref_ids=(ref.user_track_ref_id,))


def project_edition(session: Session, ref: UserTrackRefRow, snapshot: dict[str, Any]) -> bool:
    provider = _provider(session)
    recording = session.get(RecordingRow, ref.recording_id, with_for_update=True)
    if recording is None:
        return False
    fields = snapshot["fields"]
    record_ref = _external(session, provider, "RECORDING", fields["mb_recording_id"])
    if record_ref.recording_id not in (None, recording.recording_id):
        return False
    credit = _credit(session, provider, snapshot["recording_members"])
    credit_changed = recording.artist_credit_id != credit
    if credit_changed:
        old = session.get(ArtistCreditRow, recording.artist_credit_id)
        # The membership trigger increments a freshly seeded one-name credit to version two.
        members = session.execute(
            select(ArtistRow.identity_status, ArtistCreditNameRow.credited_name)
            .join(ArtistCreditNameRow, ArtistCreditNameRow.artist_id == ArtistRow.artist_id)
            .where(ArtistCreditNameRow.artist_credit_id == recording.artist_credit_id)
        ).all()
        if (
            [(status, name) for status, name in members]
            != [("PROVISIONAL", ref.raw_artist or "Unknown artist")]
            or recording.identity_status != "PROVISIONAL"
            or recording.row_version != 1
            or old is None
            or old.row_version != 2
            or session.scalar(
                select(UserTrackRefRow.user_track_ref_id)
                .where(
                    UserTrackRefRow.recording_id == recording.recording_id,
                    UserTrackRefRow.user_id != ref.user_id,
                    UserTrackRefRow.deleted_at.is_(None),
                )
                .limit(1)
            )
            is not None
        ):
            return False
        recording.artist_credit_id = credit
        recording.row_version += 1
    record_ref.recording_id = recording.recording_id
    album_credit = _credit(session, provider, snapshot["release_members"])
    group_ref = _external(session, provider, "RELEASE_GROUP", fields["mb_release_group_id"])
    if group_ref.release_group_id is None:
        group_id = _key("release-group", fields["mb_release_group_id"])
        release_type = str(snapshot.get("type") or "OTHER").upper()
        session.execute(
            insert(ReleaseGroupRow)
            .values(
                release_group_id=group_id,
                artist_credit_id=album_credit,
                title=fields["album"],
                normalized_title=normalize_text(fields["album"]),
                primary_type=release_type
                if release_type in {"ALBUM", "SINGLE", "EP", "BROADCAST"}
                else "OTHER",
            )
            .on_conflict_do_nothing()
        )
        group_ref.release_group_id = group_id
    release_ref = _external(session, provider, "RELEASE", fields["mb_release_id"])
    if release_ref.release_id is None:
        release_id = _key("release", fields["mb_release_id"])
        stamp = fields.get("release_date")
        precision = {4: "YEAR", 7: "MONTH", 10: "DAY"}.get(len(stamp or ""))
        session.execute(
            insert(ReleaseRow)
            .values(
                release_id=release_id,
                release_group_id=group_ref.release_group_id,
                artist_credit_id=album_credit,
                title=fields["album"],
                release_date=date.fromisoformat((stamp + "-01-01")[:10]) if precision else None,
                date_precision=precision,
                status="OFFICIAL"
                if str(snapshot.get("status")).lower() == "official"
                else "UNKNOWN",
            )
            .on_conflict_do_nothing()
        )
        release_ref.release_id = release_id
    medium = int(fields["disc_number"])
    session.execute(
        insert(MediumRow)
        .values(
            medium_id=_key("medium", f"{release_ref.release_id}/{medium}"),
            release_id=release_ref.release_id,
            position=medium,
        )
        .on_conflict_do_nothing()
    )
    medium_id = session.scalars(
        select(MediumRow.medium_id).where(
            MediumRow.release_id == release_ref.release_id, MediumRow.position == medium
        )
    ).one()
    track_credit = (
        _credit(session, provider, snapshot["track_members"])
        if snapshot["track_members"]
        else credit
    )
    created = session.scalar(
        insert(ReleaseTrackRow)
        .values(
            release_track_id=_key("track", snapshot["release_track"]),
            medium_id=medium_id,
            recording_id=recording.recording_id,
            artist_credit_id=track_credit,
            sequence_no=int(fields["track_number"]),
            title=fields["title"],
            duration_ms=recording.duration_ms,
        )
        .on_conflict_do_nothing()
        .returning(ReleaseTrackRow.release_track_id)
    )
    return created is not None or credit_changed
