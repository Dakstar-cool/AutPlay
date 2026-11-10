"""Deterministic owner-local Home feed; reads never enqueue acquisition work."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from autplay.adapters.postgresql.release_feed import (
    FeedPosition,
    ReleaseFeedItem,
    ReleaseFeedRepository,
)
from autplay.domain.auth import Principal

CONTRACT_VERSION = "release-feed-v1"
RANKING_VERSION = "owner-local-v1"
CURSOR_LIFETIME = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class ReleaseFeedPage:
    items: tuple[ReleaseFeedItem, ...]
    next_cursor: str | None


class InvalidReleaseFeedCursor(ValueError):
    pass


def _position(item: ReleaseFeedItem) -> FeedPosition:
    return FeedPosition(
        item.diversity_round,
        item.released_at,
        item.affinity_rank,
        item.availability_rank,
        item.score_milli,
        item.candidate_id,
    )


def _encode_cursor(owner: UUID, as_of: datetime, item: ReleaseFeedItem) -> str:
    position = _position(item)
    payload = [
        1,
        str(owner),
        as_of.isoformat(),
        position.diversity_round,
        position.released_at.isoformat(),
        position.affinity_rank,
        position.availability_rank,
        position.score_milli,
        str(position.candidate_id),
    ]
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _decode_cursor(value: str, owner: UUID, now: datetime) -> tuple[datetime, FeedPosition]:
    if not value or len(value) > 512:
        raise InvalidReleaseFeedCursor("invalid release-feed cursor")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) != 9 or values[0] != 1:
            raise ValueError
        if UUID(values[1]) != owner:
            raise ValueError
        as_of = datetime.fromisoformat(values[2])
        released_at = datetime.fromisoformat(values[4])
        if as_of.tzinfo is None or released_at.tzinfo is None:
            raise ValueError
        if not as_of <= now or now - as_of > CURSOR_LIFETIME:
            raise ValueError
        for index in (3, 5, 6, 7):
            if type(values[index]) is not int or values[index] < 0:
                raise ValueError
        if values[3] > 2**63 - 1 or values[5] > 1 or values[6] > 5 or values[7] > 1_000_000:
            raise ValueError
        return as_of, FeedPosition(
            values[3], released_at, values[5], values[6], values[7], UUID(values[8])
        )
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, binascii.Error) as error:
        raise InvalidReleaseFeedCursor("invalid release-feed cursor") from error


class ReleaseFeedService:
    def __init__(self, sessions: Callable[[], Session], clock: Callable[[], datetime]) -> None:
        self._sessions = sessions
        self._clock = clock

    def page(
        self, principal: Principal, *, limit: int = 20, cursor: str | None = None
    ) -> ReleaseFeedPage:
        if not 1 <= limit <= 50:
            raise ValueError("release-feed limit must be 1..50")
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("release-feed clock must be timezone aware")
        as_of, after = (
            (now, None) if cursor is None else _decode_cursor(cursor, principal.user_id, now)
        )
        with self._sessions() as session:
            rows = ReleaseFeedRepository(session).list_page(
                principal.user_id, as_of=as_of, authority_now=now, limit=limit + 1, after=after
            )
        items = tuple(rows[:limit])
        next_cursor = (
            _encode_cursor(principal.user_id, as_of, items[-1])
            if len(rows) > limit and items
            else None
        )
        return ReleaseFeedPage(items, next_cursor)


__all__ = (
    "CONTRACT_VERSION",
    "RANKING_VERSION",
    "InvalidReleaseFeedCursor",
    "ReleaseFeedPage",
    "ReleaseFeedService",
)
