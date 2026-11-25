"""Attach corroborated public images without changing track identity or edition metadata."""

import hashlib
import re
from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models.track_metadata import MetadataArtworkRow, TrackMetadataRow
from autplay.domain.catalog_artwork import ArtMatch, album_label, label
from autplay.domain.original_artwork import ORIGINAL_BASES, original_artwork_source
from autplay.domain.track_names import track_names


def store_image(session: Session, data: bytes) -> str:
    sha = hashlib.sha256(data).hexdigest()
    session.execute(insert(MetadataArtworkRow).values(sha256=sha, content=data).on_conflict_do_nothing())
    return sha


def attach_images(session: Session, row: TrackMetadataRow, match: ArtMatch, cover: bytes | None,
                  portrait: bytes | None) -> bool:
    document = dict(row.document)
    fields = document.get("fields") or {}
    for key, value in (("artist", match.credit), ("title", match.title)):
        if key in fields and label(fields.get(key) or "") != label(value):
            return False
    tagged_album = album_label(fields.get("album"))
    original = match.basis in ORIGINAL_BASES
    if original:
        portrait = None
    if not match.cover or (not original and tagged_album and tagged_album != label(match.album)):
        cover = None
    changed = False
    verified_group = (match.provider == "COVER_ART_ARCHIVE" and match.entity == "release-group"
                      and match.basis == "selected_release_group" and fields.get("mb_release_id")
                      and fields.get("mb_recording_id") == match.track_id and fields.get("mb_release_group_id") == match.album_id)
    verified_release = (match.provider == "COVER_ART_ARCHIVE" and match.entity == "release"
                        and fields.get("mb_release_id") == match.album_id)
    edition_locked = any((document.get("provenance", {}).get(key) or {}).get("locked")
                         for key in ("album", "mb_release_id"))
    selected = edition_locked or (original and document.get("selection")) or (
        fields.get("mb_release_id") and not (verified_group or verified_release or original))
    previous = document.get("artwork_source") or {}
    replaceable = row.artwork_sha256 is None or (not original and original_artwork_source(previous))
    if cover and replaceable and not selected and not previous.get("locked"):
        row.artwork_sha256 = store_image(session, cover)
        entity = f"{match.entity}:{match.album_id}" if match.album_id else f"track:{match.track_id}"
        document["artwork_source"] = {"source": "PUBLIC_CATALOG", "source_id": f"{match.provider}:{entity}",
                                      "release_title": match.album, "match_basis": match.basis,
                                      "observed_at": datetime.now(UTC).isoformat(), "locked": False}
        if original:
            document["artwork_source"].update(original_track_id=str(match.track_id), original_artist=match.artist)
        if match.source_page and match.license_url:
            document["artwork_source"].update(source_page=match.source_page, license_url=match.license_url, credit=match.artist)
        changed = True
    if portrait:
        entry = {"provider": match.provider, "artist_id": str(match.artist_id), "name": match.artist, "credit": match.credit,
                 "sha256": hashlib.sha256(portrait).hexdigest()}
        previous = document.get("catalog_artist_image_v1") or {}
        if previous != entry and (not previous or previous.get("provider") == match.provider or
                                  label(previous.get("credit") or previous.get("name") or "") != label(match.credit)):
            store_image(session, portrait)
            document["catalog_artist_image_v1"] = entry
            changed = True
    if changed:
        row.document = document
    return changed


def catalog_image_evidence(session: Session, recording_ids: list[UUID]) -> dict:
    """Use public provider images or art embedded in the exact published audio object."""
    evidence = defaultdict(list)
    if not recording_ids:
        return evidence
    rows = session.execute(text("""
        SELECT u.recording_id, m.artwork_sha256, m.document->'artwork_source' AS source,
               m.document->'catalog_artist_image_v1' AS portrait, v.audio_variant_id,
               coalesce(m.document#>>'{fields,title}',u.raw_title) AS title,
               coalesce(m.document#>>'{fields,artist}',u.raw_artist) AS artist,
               'sha256:' || encode(o.sha256,'hex') AS audio_digest
        FROM library.user_track_ref u JOIN library.track_metadata m USING(user_track_ref_id)
        JOIN vault.recording_canonical_variant c USING(recording_id)
        JOIN vault.audio_variant v USING(recording_id,audio_variant_id) JOIN vault.vault_object o USING(vault_object_id)
        WHERE u.recording_id=ANY(:ids) AND u.deleted_at IS NULL AND
          (m.artwork_sha256 IS NOT NULL OR m.document ? 'catalog_artist_image_v1')
        """), {"ids": list(set(recording_ids))})
    for row in rows:
        evidence[row.recording_id].append(row)
    return evidence


def inherit_catalog_images(evidence, document, existing_cover, raw_artist, variant, raw_title):
    """An owner handle can inherit images proven for its recording while retaining manual edition choices."""
    if not evidence:
        return document, None
    covers, portraits = {}, {}
    fields = document.get("fields") or {}
    title, name = track_names(fields.get("title", raw_title), fields.get("artist", raw_artist))
    labels = label(title or ""), label(name or "")
    for item in evidence:
        if item.audio_variant_id != variant:
            continue
        source_title, source_artist = track_names(item.title, item.artist)
        same_credit = labels == (label(source_title or ""), label(source_artist or "")) and all(labels)
        source = item.source or {}
        kind, identity = source.get("source"), source.get("source_id") or ""
        if (kind == "EMBEDDED" and identity == item.audio_digest or
                same_credit and kind == "PUBLIC_CATALOG" and re.fullmatch(r"(?:(?:(?:DEEZER|YANDEX|BANDCAMP):album|BANDCAMP:track):[1-9][0-9]*|COVER_ART_ARCHIVE:release(?:-group)?:[a-f0-9-]{36}|INTERNET_ARCHIVE:item:[A-Za-z0-9][A-Za-z0-9._-]{0,99})", identity) or
                same_credit and kind == "MUSICBRAINZ" and not source.get("locked") and identity.startswith("musicbrainz:release:")):
            if item.artwork_sha256:
                inherited = {"source": kind, "source_id": identity, "observed_at": source.get("observed_at"), "locked": False}
                if kind == "PUBLIC_CATALOG":
                    for key in ("release_title", "match_basis", "source_page", "license_url", "credit", "original_track_id", "original_artist"):
                        if isinstance(source.get(key), str):
                            inherited[key] = source[key]
                tagged_album = album_label(fields.get("album"))
                original = original_artwork_source(source)
                if original or kind != "PUBLIC_CATALOG" or not tagged_album or tagged_album == album_label(source.get("release_title")):
                    covers.setdefault("ORIGINAL" if original else kind, {})[item.artwork_sha256] = inherited
        photo = item.portrait or {}
        if (same_credit and photo.get("provider") in {"DEEZER", "YANDEX"} and label(photo.get("credit") or photo.get("name") or "") == labels[1]
                and re.fullmatch(r"[1-9][0-9]*", str(photo.get("artist_id")))
                and re.fullmatch(r"[0-9a-f]{64}", str(photo.get("sha256")))):
            portraits.setdefault(photo["provider"], {}).setdefault(str(photo["artist_id"]), {})[photo["sha256"]] = {
                k: photo[k] for k in ("provider", "artist_id", "sha256", "name", "credit") if k in photo}
    updated = dict(document)
    if portraits and all(len(profiles) == 1 for profiles in portraits.values()) and not document.get("catalog_artist_image_v1"):
        for provider in ("DEEZER", "YANDEX"):
            profiles = portraits.get(provider)
            if profiles:
                images = next(iter(profiles.values()))
                updated["catalog_artist_image_v1"] = images[min(images)]
                break
    selected = (document.get("artwork_source") or {}).get("locked") or any(
        (document.get("provenance", {}).get(key) or {}).get("locked") for key in ("album", "mb_release_id"))
    if (existing_cover and not original_artwork_source(document.get("artwork_source"))) or selected:
        return updated, None
    for kind in ("EMBEDDED", "MUSICBRAINZ", "PUBLIC_CATALOG", "ORIGINAL"):
        if kind == "ORIGINAL" and (existing_cover or document.get("selection")):
            break
        choices = covers.get(kind, {})
        if (release := fields.get("mb_release_id")) and kind != "ORIGINAL":
            allowed = {f"musicbrainz:release:{release}", f"COVER_ART_ARCHIVE:release:{release}"}
            if group := fields.get("mb_release_group_id"):
                allowed.add(f"COVER_ART_ARCHIVE:release-group:{group}")
            choices = {sha: source for sha, source in choices.items() if source["source_id"] in allowed}
        if choices:
            if len(choices) == 1:
                sha, source = next(iter(choices.items()))
                updated["artwork_source"] = source
                return updated, sha
            break
    return updated, None
