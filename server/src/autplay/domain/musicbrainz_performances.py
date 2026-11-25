"""Interpret recording-to-work performance links from the MusicBrainz JSON service."""

from typing import Any
from uuid import UUID

PERFORMANCE = "a3005666-a872-32c3-ad06-98af558e99b0"
REMIX = "bfbdb55a-b857-473a-8f2e-a9c09e45c3f5"


def _id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _performance(link: object) -> bool:
    return isinstance(link, dict) and link.get("type-id") == PERFORMANCE


def recording_work(recording: dict[str, Any]) -> tuple[str | None, str | None]:
    """A medley or partial performance cannot identify a single original recording."""
    raw = recording.get("relations")
    if not isinstance(raw, list):
        return None, None
    links = [link for link in raw if _performance(link)]
    works: set[str] = set()
    covered = False
    simple = bool(links)
    for link in links:
        work = link.get("work")
        attrs = link.get("attributes")
        if link.get("direction") != "forward" or link.get("target-type") != "work":
            simple = False
            continue
        work_id = _id(work.get("id")) if isinstance(work, dict) else None
        if work_id:
            works.add(work_id)
        if isinstance(attrs, list) and "cover" in attrs:
            covered = True
        if not work_id or not isinstance(attrs, list) or "medley" in attrs or "partial" in attrs:
            simple = False
    work_id = works.pop() if simple and len(works) == 1 else None
    if covered:
        return "COVER", work_id
    if work_id and all(link.get("attributes") == [] for link in links):
        return "PERFORMANCE", work_id
    if work_id:
        return "VERSION", work_id
    return None, None


def remix_sources(recording: dict[str, Any]) -> set[str | None]:
    """Only forward remix-of edges describe the recording being enriched."""
    links = recording.get("relations")
    sources: set[str | None] = set()
    for link in links if isinstance(links, list) else ():
        if (
            not isinstance(link, dict)
            or link.get("type-id") != REMIX
            or link.get("direction") != "forward"
            or link.get("target-type") != "recording"
        ):
            continue
        target = link.get("recording")
        source = _id(target.get("id")) if isinstance(target, dict) else None
        sources.add(source if source != recording.get("id") else None)
    return sources


def cover_original(work: dict[str, Any], work_id: str, cover_id: str) -> str | None:
    """Require a reciprocal cover and exactly one unqualified audio recording."""
    raw = work.get("relations")
    if work.get("id") != work_id or not isinstance(raw, list):
        return None
    plain: set[str] = set()
    qualified: set[str] = set()
    reciprocal = False
    for link in raw:
        if not isinstance(link, dict):
            return None
        if not _performance(link):
            continue
        recording, attrs = link.get("recording"), link.get("attributes")
        if (
            link.get("direction") != "backward"
            or link.get("target-type") != "recording"
            or not isinstance(recording, dict)
            or not isinstance(attrs, list)
            or (recording_id := _id(recording.get("id"))) is None
        ):
            return None
        if attrs:
            qualified.add(recording_id)
            reciprocal |= recording_id == cover_id and "cover" in attrs
        elif recording.get("video") is False:
            plain.add(recording_id)
        elif recording.get("video") is not True:
            return None
    candidates = plain - qualified
    return candidates.pop() if reciprocal and len(candidates) == 1 else None
