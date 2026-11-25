"""Refresh descriptive labels for committed Vault recordings and their account references."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models import CatalogBrowsePageRow, UserTrackRefRow
from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from autplay.adapters.postgresql.shared_catalog import ACCOUNT_CATALOG, OWNER_AUDIO
from autplay.adapters.postgresql.stage_credits import CATALOG_MEMBERS, credit_for_recording
from autplay.application.metadata_publication import save_name_revision
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.artist_credits import recording_credit
from autplay.domain.track_names import project_names, track_names

_RECORDING_LABELS = f"""
    SELECT r.recording_id, r.title, a.display_name AS artist, r.identity_status,
    {CATALOG_MEMBERS} AS members
    FROM catalog.recording r
    JOIN catalog.artist_credit a USING (artist_credit_id)
"""
_ACTIVE_RECORDING = """
    WHERE r.deleted_at IS NULL AND NOT EXISTS (SELECT FROM identity.recording_redirect rr
        WHERE rr.source_recording_id=r.recording_id)
"""
_RECORDINGS = (
    _RECORDING_LABELS
    + """
    JOIN vault.recording_canonical_variant cv USING (recording_id)
    JOIN vault.audio_variant v ON v.audio_variant_id=cv.audio_variant_id
      AND v.recording_id=r.recording_id AND v.deleted_at IS NULL AND v.validation_status='VALID'
    JOIN vault.vault_object o ON o.vault_object_id=v.vault_object_id AND o.commit_status='COMMITTED'
"""
    + _ACTIVE_RECORDING
)
_OWNER_REFS = f"""
    SELECT DISTINCT ref.user_id, ref.user_track_ref_id, ref.recording_id
    FROM library.user_track_ref ref
    JOIN library.library_entry le USING(user_id,user_track_ref_id)
    JOIN ({OWNER_AUDIO}) owned USING(user_id,recording_id)
    WHERE ref.deleted_at IS NULL AND le.removed_at IS NULL
"""


def recording_card(
    title: str, artist: str, status: str, previous: dict[str, Any] | None, members: object = None
) -> dict[str, Any] | None:
    name, credit = track_names(
        title, artist, filename=status == "PROVISIONAL", catalog=recording_credit(members)
    )
    if previous is None and (name, credit) == (title, artist):
        return None
    card = {
        "title": name,
        "artist": credit,
        "source_title": title,
        "source_artist": artist,
        "identity_status": status,
    }
    return None if card == previous else card


def targets(session: Session) -> Iterator[tuple[str, str, str, str]]:
    # Filter unchanged rows in a single streaming snapshot, without opening write transactions.
    rows = session.execute(
        text(f"""
        SELECT 'recording', r.recording_id::text, '', r.title, r.artist, r.identity_status,
        p.document, r.members
        FROM ({_RECORDINGS}) r LEFT JOIN catalog.browse_page p
          ON p.page_key='vault-display-v1:' || r.recording_id::text
        UNION ALL
        SELECT 'owner', u.user_track_ref_id::text, u.user_id::text, u.raw_title, u.raw_artist,
               NULL, m.document, {CATALOG_MEMBERS}
        FROM (
            SELECT DISTINCT ON (user_id,user_track_ref_id) user_id,user_track_ref_id,recording_id
            FROM (
                SELECT user_id,user_track_ref_id,recording_id FROM ({_OWNER_REFS}) owned
                UNION ALL SELECT user_id,user_track_ref_id,recording_id
                FROM ({ACCOUNT_CATALOG}) linked
            ) sources ORDER BY user_id,user_track_ref_id,recording_id
        ) refs JOIN library.user_track_ref u USING(user_id,user_track_ref_id)
        LEFT JOIN library.track_metadata m USING(user_track_ref_id)
        LEFT JOIN catalog.recording r ON r.recording_id=coalesce(u.recording_id,refs.recording_id)
        AND r.deleted_at IS NULL
    """).execution_options(yield_per=256)
    )
    for kind, target, owner, title, artist, status, doc, members in rows:
        if kind == "owner":
            before = doc or {}
            changed = project_names(before, title, artist, recording_credit(members)) is not before
        else:
            changed = recording_card(title, artist, status, doc, members) is not None
        yield kind, target, owner, "pending" if changed else "unchanged"


def refresh_owner(session: Session, ref_id: UUID, owner: UUID, apply: bool) -> str:
    if apply:
        _acquire_sync_owner_publish_lock(session, owner)
    ref = session.get(UserTrackRefRow, ref_id, with_for_update=apply)
    if ref is None or ref.user_id != owner or ref.deleted_at is not None:
        return "skipped"
    params = {"owner": owner, "ref": ref_id}
    scope = " WHERE user_id=:owner AND user_track_ref_id=:ref"
    recording = session.scalar(
        text(f"SELECT recording_id FROM ({_OWNER_REFS}) own {scope} LIMIT 1"), params
    )
    if recording is None:
        recording = session.scalar(
            text(
                f"SELECT recording_id FROM ({ACCOUNT_CATALOG}) linked "
                f"{scope} ORDER BY recording_id LIMIT 1"
            ),
            params,
        )
        if recording is None:
            return "skipped"
    return project_owner_names(session, ref, recording, apply=apply)


def project_owner_names(
    session: Session, ref: UserTrackRefRow, recording: UUID, *, apply: bool = True
) -> str:
    """The caller holds the owner publication lock and owns the reference row."""
    row = session.get(TrackMetadataRow, ref.user_track_ref_id, with_for_update=apply)
    before = row.document if row else {}
    after = project_names(
        before, ref.raw_title, ref.raw_artist, credit_for_recording(session, recording)
    )
    if after is before:
        return "unchanged"
    if apply:
        save_name_revision(session, ref, row, after, recording)
    return "changed"


def refresh_recording(
    session: Session, recording_id: UUID, apply: bool, *, ingest: bool = False
) -> str:
    # Ingest already verified the bytes. A canonical variant can be selected later.
    query = (
        _RECORDING_LABELS + _ACTIVE_RECORDING if ingest else _RECORDINGS
    ) + " AND r.recording_id=:id"
    if apply and not ingest:
        query += " FOR UPDATE OF r"
    record = session.execute(text(query), {"id": recording_id}).mappings().one_or_none()
    if record is None:
        return "skipped"
    key = f"vault-display-v1:{recording_id}"
    page = session.get(CatalogBrowsePageRow, key)
    card = recording_card(
        record["title"],
        record["artist"],
        record["identity_status"],
        page.document if page is not None else None,
        record["members"],
    )
    if card is None:
        return "unchanged"
    if apply:
        write = insert(CatalogBrowsePageRow).values(
            page_key=key, document=card, observed_at=datetime.now(UTC)
        )
        session.execute(
            write.on_conflict_do_update(
                index_elements=[CatalogBrowsePageRow.page_key],
                set_={
                    "document": write.excluded.document,
                    "observed_at": write.excluded.observed_at,
                },
                where=CatalogBrowsePageRow.document.is_distinct_from(write.excluded.document),
            )
        )
    return "changed"
