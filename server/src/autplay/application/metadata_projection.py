"""The additive v1 sync payload shared by bootstrap and incremental publication."""

from typing import Any

from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from autplay.domain.track_metadata import album_group_document, normalized, parse_album_group


def coherent_album_group(document: dict[str, Any]) -> dict[str, object] | None:
    """Publish membership only when effective description still names that edition."""
    raw = document.get("album_group_v1")
    if raw is None:
        return None
    try:
        group = parse_album_group(raw)
    except ValueError, TypeError:
        return None
    fields, provenance = document.get("fields", {}), document.get("provenance", {})
    for field, expected in (("album", group.title), ("album_artist", group.album_artist)):
        if expected is None:
            continue
        if (
            field == "album_artist"
            and (provenance.get(field) or {}).get("source") == "USER"
            and fields.get(field) is None
        ):
            continue
        actual = fields.get(field)
        if not isinstance(actual, str) or normalized(actual) != normalized(expected):
            return None
    if group.provider == "MUSICBRAINZ":
        if fields.get("mb_release_id") != group.release_id:
            return None
    elif fields.get("mb_release_id"):
        # Native membership cannot contradict embedded or selected MB edition evidence.
        return None
    result = album_group_document(group)
    if (provenance.get("album_artist") or {}).get("source") == "USER":
        result["album_artist"] = fields.get("album_artist")
    for field in ("release_date", "track_number", "disc_number"):
        if (provenance.get(field) or {}).get("source") == "USER":
            result[field] = fields.get(field)
        elif (
            field == "release_date"
            and isinstance(fields.get(field), str)
            and isinstance(result[field], str)
        ):
            actual_date, edition_date = str(fields[field]), str(result[field])
            if not (actual_date.startswith(edition_date) or edition_date.startswith(actual_date)):
                return None
            result[field] = actual_date
        elif fields.get(field) is not None and fields[field] != result[field]:
            return None
    return result


def metadata_view(row: TrackMetadataRow) -> dict[str, Any]:
    return {
        "revision": row.revision,
        "state": row.state,
        "error_code": row.error_code,
        "fields": row.document.get("fields", {}),
        "provenance": row.document.get("provenance", {}),
        "artwork_sha256": row.artwork_sha256,
        "artwork_source": row.document.get("artwork_source"),
        "candidates": row.candidates,
        "updated_at": row.updated_at.isoformat(),
        "album_group_v1": coherent_album_group(row.document),
    }
