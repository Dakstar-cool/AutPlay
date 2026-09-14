"""Reusable, auditable normalization before provider lookup and queue deduplication."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .models import PlaylistItem

_TYPOGRAPHY = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2013": "-",
        "\u2014": "-",
    }
)


def normalize_text(value: str) -> str:
    """Normalize presentation only; never guess truncated words or recording versions."""
    value = unicodedata.normalize("NFKC", value).translate(_TYPOGRAPHY)
    value = "".join(character for character in value if unicodedata.category(character) != "Cf")
    return " ".join(value.split())


def normalize_artist(value: str) -> str:
    """Treat a spaced vs./versus credit as collaboration, preserving every artist."""
    return re.sub(r"\s+(?:vs\.?|versus)\s+", ", ", normalize_text(value), flags=re.I)


def _identity(artist: str, title: str) -> tuple[str, str]:
    return normalize_artist(artist).casefold(), normalize_text(title).casefold()


def load_catalog(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    """Read a local reviewed correction catalog; reject conflicts and unbounded data."""
    if path is None:
        return {}
    try:
        with path.open("rb") as handle:
            payload = handle.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError
        document = json.loads(payload)
        entries = document["corrections"]
        if (
            document.get("schema_version") != 1
            or not isinstance(entries, list)
            or len(entries) > 10000
        ):
            raise ValueError
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError
            for field in ("source_artist", "source_title", "artist", "title", "evidence"):
                value = entry.get(field)
                if not isinstance(value, str) or not value.strip() or len(value) > 4000:
                    raise ValueError
                if re.search(r"[\r\n\t\x00]", value):
                    raise ValueError
            duration = entry.get("duration_seconds")
            if duration is not None and (
                type(duration) not in {int, float} or not math.isfinite(duration) or duration <= 0
            ):
                raise ValueError
            key = _identity(entry["source_artist"], entry["source_title"])
            if key in result and result[key] != entry:
                raise ValueError
            result[key] = entry
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError("normalization_catalog_invalid") from error


def normalize_items(
    rows: tuple[PlaylistItem, ...], *, catalog_path: Path | None = None
) -> tuple[tuple[PlaylistItem, ...], list[dict[str, Any]]]:
    catalog = load_catalog(catalog_path)
    normalized, changes = [], []
    for item in rows:
        if item.error_code:
            normalized.append(item)
            continue
        current = replace(
            item,
            artist=normalize_artist(item.artist),
            title=normalize_text(item.title),
            album=normalize_text(item.album) if item.album else None,
        )
        correction = catalog.get(_identity(current.artist, current.title))
        if correction:
            current = replace(
                current,
                artist=normalize_artist(correction["artist"]),
                title=normalize_text(correction["title"]),
                expected_duration_seconds=correction.get("duration_seconds"),
            )
        if not current.artist or not current.title:
            current = replace(current, error_code="import.identity_missing")
        if current != item:
            changes.append(
                {
                    "row_number": item.row_number,
                    "original": asdict(item),
                    "normalized": asdict(current),
                    "evidence": correction["evidence"] if correction else "typography-v1",
                }
            )
        normalized.append(current)
    return tuple(normalized), changes
