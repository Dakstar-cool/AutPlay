"""The additive v1 sync payload shared by bootstrap and incremental publication."""

from typing import Any

from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow


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
    }
