"""Reviewed recording pages; never accept arbitrary media URLs or credentials."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .matching import recording_identity
from .models import PlaylistItem

type RecordingKey = tuple[frozenset[str], str]


def track_url(value: object, provider: str) -> str | None:
    """Accept only canonical public recording URLs for a known built-in extractor."""
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\x00-\x1f]", value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or parsed.fragment
        ):
            return None
        if provider == "yt_dlp":
            valid = (
                parsed.netloc in {"www.youtube.com", "music.youtube.com", "youtube.com"}
                and parsed.path == "/watch"
                and re.fullmatch(r"v=[A-Za-z0-9_-]{11}", parsed.query)
            )
            if valid:
                return "https://www.youtube.com/watch?" + parsed.query
            if (
                parsed.netloc == "youtu.be"
                and not parsed.query
                and re.fullmatch(r"/[A-Za-z0-9_-]{11}", parsed.path)
            ):
                return "https://www.youtube.com/watch?v=" + parsed.path[1:]
            return None
        if parsed.query:
            return None
        if provider == "soundcloud":
            valid = parsed.netloc == "soundcloud.com" and re.fullmatch(
                r"/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+", parsed.path
            )
            if parsed.path.split("/")[-1] in {"sets", "tracks", "albums", "likes", "reposts"}:
                return None
        elif provider == "bandcamp":
            valid = re.fullmatch(r"[a-z0-9-]+\.bandcamp\.com", parsed.netloc) and re.fullmatch(
                r"/track/[a-zA-Z0-9_-]+", parsed.path
            )
        else:
            return None
        return value if valid else None
    except ValueError:
        return None


def _reference_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)
            or host.endswith((".local", ".localhost", ".internal"))
            or re.search(r"[\s\x00-\x1f]", value)
        ):
            return False
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return True
        return False
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ReviewedSource:
    provider: str
    url: str
    duration_seconds: float | None
    fingerprint: str


class SourceCatalog:
    def __init__(self, path: Path | None = None) -> None:
        self._downloads: dict[tuple[str, RecordingKey], ReviewedSource] = {}
        self.reference_count = 0
        if path is None:
            return
        try:
            with path.open("rb") as handle:
                payload = handle.read(2 * 1024 * 1024 + 1)
            if len(payload) > 2 * 1024 * 1024:
                raise ValueError
            document = json.loads(payload)
            entries = document["entries"]
            if document.get("schema_version") != 1 or not isinstance(entries, list):
                raise ValueError
            if len(entries) > 10000:
                raise ValueError
            fingerprint = hashlib.sha256(payload).hexdigest()
            owners: dict[str, RecordingKey] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError
                for name in ("artist", "title", "url", "evidence", "kind"):
                    value = entry.get(name)
                    if (
                        not isinstance(value, str)
                        or not value.strip()
                        or len(value) > 2048
                        or re.search(r"[\r\n\t\x00]", value)
                    ):
                        raise ValueError
                duration = entry.get("duration_seconds")
                if duration is not None and (
                    type(duration) not in {int, float}
                    or not 0 < duration <= 86400
                    or not math.isfinite(duration)
                ):
                    raise ValueError
                if entry["kind"] == "reference":
                    if not _reference_url(entry["url"]):
                        raise ValueError
                    self.reference_count += 1
                    continue
                if entry["kind"] != "download":
                    raise ValueError
                provider = entry.get("provider")
                if not isinstance(provider, str):
                    raise ValueError
                url = track_url(entry["url"], provider)
                if url is None:
                    raise ValueError
                identity = recording_identity(entry["artist"], entry["title"])
                key = provider, identity
                source = ReviewedSource(provider, url, duration, fingerprint)
                if key in self._downloads and self._downloads[key] != source:
                    raise ValueError
                if url in owners and owners[url] != identity:
                    raise ValueError
                owners[url] = identity
                self._downloads[key] = source
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError("source_catalog_invalid") from error

    def find(self, provider: str, item: PlaylistItem) -> ReviewedSource | None:
        return self._downloads.get((provider, recording_identity(item.artist, item.title)))

    def summary(self) -> dict[str, int]:
        return {"download_links": len(self._downloads), "references": self.reference_count}
