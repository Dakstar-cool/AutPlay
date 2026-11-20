"""Owner-only recommendation outcomes, aggregated across devices within a rolling window."""

import re
from datetime import datetime, timedelta
from typing import TypedDict
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.domain.adaptive_recommendations import DEFAULT_ADAPTIVE_FEATURE_POLICY

KINDS = {"COVER", "REMIX", "LIVE"}
_LABEL = re.compile(r"(?:\s*[\[(]([^\[\]()]+)[])]|\s+[-\u2013\u2014]\s+([^\u2013\u2014-]+))\s*$")
_LABELS = (
    ("COVER", re.compile(r"(?:.+\s+)?(?:cover|кавер)|(?:cover|кавер|originally by)\s+.+", re.I)),
    ("REMIX", re.compile(r"(?:.+\s+)?(?:remix|ремикс)", re.I)),
    ("LIVE", re.compile(r"live(?: version|\s+(?:at|in|from)\s+.+)?|концертная(?: версия)?", re.I)),
)


def variation_kind(
    role: str | None, live: bool, kind: str | None, version: str | None, title: str | None
) -> str | None:
    if role in {"REMIX", "COVER"}:
        return role
    if role == "VERSION" and live:
        return "LIVE"
    if kind in KINDS:
        return kind
    labels = [version or ""]
    base = (title or "").strip()
    while match := _LABEL.search(base):
        labels.append((match[1] or match[2]).strip())
        base = base[: match.start()].rstrip()
    return next((k for k, pattern in _LABELS if any(pattern.fullmatch(s) for s in labels)), None)


class VariationCount(TypedDict):
    surface: str
    kind: str
    plays: int
    skips: int


def variation_counts(
    session: Session, user: UUID, through: datetime, *, since: datetime | None = None
) -> list[VariationCount]:
    policy = DEFAULT_ADAPTIVE_FEATURE_POLICY
    rows = session.execute(
        text(_OUTCOMES),
        {
            "user_id": user,
            "through": through,
            "since": through - timedelta(days=30) if since is None else since,
            "include_start": since is not None,
            "short_ms": policy.skip_played_ms_threshold,
            "short_ratio": policy.skip_completion_threshold,
        },
    ).mappings()
    counts: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        kind = row["frozen_kind"] or variation_kind(
            row["role"], row["live"], row["recording_kind"], row["version_text"], row["title"]
        )
        if kind in KINDS:
            total = counts.setdefault((row["surface"], kind), [0, 0])
            total[0] += row["plays"]
            total[1] += row["skips"]
    return [
        {"surface": surface, "kind": kind, "plays": plays, "skips": skips}
        for (surface, kind), (plays, skips) in sorted(counts.items())
    ]


_OUTCOMES = """
WITH events AS (
    SELECT e.*, j.payload, CASE
        WHEN e.event_origin = 'RECOMMENDED' THEN CASE j.payload #>> '{recommendation,surface}'
            WHEN 'home' THEN 'home' WHEN 'offline_pack' THEN 'home'
            WHEN 'personal_wave' THEN 'personal_wave' END
        WHEN e.event_origin = 'WAVE' AND j.payload->>'variation_surface' = 'personal_wave'
            THEN 'personal_wave' END AS surface
    FROM library.listening_event e JOIN sync.sync_event j
        ON j.event_id=e.listening_event_id AND j.user_id=e.user_id
        AND j.event_type='LISTENING_EVENT_RECORDED'
    WHERE e.user_id=:user_id AND NOT e.excluded_from_taste
      AND (e.started_at>:since OR (:include_start AND e.started_at=:since))
      AND e.started_at<=:through AND e.event_origin IN ('RECOMMENDED','WAVE')
), facts AS (
    SELECT DISTINCT ON (coalesce(p.record_id, e.listening_event_id)) e.*
    FROM events e LEFT JOIN LATERAL (
        SELECT record_id FROM account.portable_record WHERE user_id=:user_id AND kind='LISTENING'
            AND (document->'event_ids') ? e.listening_event_id::text ORDER BY record_id LIMIT 1
    ) p ON true WHERE e.surface IS NOT NULL
    ORDER BY coalesce(p.record_id, e.listening_event_id), e.started_at, e.listening_event_id
), classified AS (
    SELECT f.surface, f.payload->>'variation_kind' AS frozen_kind,
        CASE WHEN m.document #>> '{fields,mb_recording_id}' =
                  m.document #>> '{musicbrainz_performance_v1,recording_id}'
             THEN m.document #>> '{musicbrainz_performance_v1,role}' END AS role,
        m.document #>> '{musicbrainz_performance_v1,live}' = 'true' AS live,
        r.recording_kind, r.version_text,
        coalesce(m.document #>> '{fields,title}',r.title,u.raw_title) AS title,
        f.played_ms < :short_ms OR f.completion_ratio <= :short_ratio AS skipped
    FROM facts f JOIN library.user_track_ref u
      ON u.user_track_ref_id=f.user_track_ref_id AND u.user_id=f.user_id
    LEFT JOIN library.track_metadata m ON m.user_track_ref_id=u.user_track_ref_id
    LEFT JOIN catalog.recording r ON r.recording_id=coalesce(f.recording_id,u.recording_id)
)
SELECT surface, frozen_kind, role, live, recording_kind, version_text, title,
       count(*) AS plays, count(*) FILTER (WHERE skipped) AS skips
FROM classified GROUP BY surface, frozen_kind, role, live, recording_kind, version_text, title
"""
