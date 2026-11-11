"""C1 process-only owner-visible catalog query contract; no media authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

CatalogViewKind = Literal[
    "recording_detail", "recording_releases", "release_detail", "release_tracks"
]
MAX_REPLY_BYTES = 262_144
MAX_CREDIT_MEMBERS = 1_000
DEADLINE_SECONDS = 10


class CatalogViewError(Exception):
    def __init__(self, code: str = "catalog_view_unavailable", status: int = 503) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def canonical_id(value: str) -> UUID:
    try:
        result = UUID(value)
        if str(result) != value:
            raise ValueError()
        return result
    except ValueError, TypeError, AttributeError:
        raise CatalogViewError("request_validation_failed", 400) from None


def positive(value: str, maximum: int = 2_147_483_647) -> int:
    if not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= 10:
        raise CatalogViewError("request_validation_failed", 400)
    number = int(value)
    if not 1 <= number <= maximum or str(number) != value:
        raise CatalogViewError("request_validation_failed", 400)
    return number


@dataclass(frozen=True, slots=True)
class CatalogPositionCursor:
    release_id: UUID | None
    medium_position: int
    sequence_no: int
    release_track_id: UUID

    def wire(self) -> dict[str, str | int]:
        result: dict[str, str | int] = {
            "medium_position": self.medium_position,
            "sequence_no": self.sequence_no,
            "release_track_id": str(self.release_track_id),
        }
        if self.release_id is not None:
            result["release_id"] = str(self.release_id)
        return result


@dataclass(frozen=True, slots=True)
class CatalogViewQuery:
    kind: CatalogViewKind
    subject_id: UUID
    limit: int = 100
    after: CatalogPositionCursor | None = None
    revision: str | None = None

    @classmethod
    def parse(
        cls, kind: CatalogViewKind, subject: str, pairs: list[tuple[str, str]]
    ) -> CatalogViewQuery:
        subject_id = canonical_id(subject)
        if len(pairs) > 7 or len(dict(pairs)) != len(pairs):
            raise CatalogViewError("request_validation_failed", 400)
        values = dict(pairs)
        if kind.endswith("detail"):
            if values:
                raise CatalogViewError("request_validation_failed", 400)
            return cls(kind, subject_id)
        cursor_keys = {
            "view_revision",
            "after_medium_position",
            "after_sequence_no",
            "after_release_track_id",
        }
        if kind == "recording_releases":
            cursor_keys.add("after_release_id")
        if values.keys() - {"limit"} not in (set(), cursor_keys):
            raise CatalogViewError("request_validation_failed", 400)
        limit = positive(values.get("limit", "100"), 100)
        if "view_revision" not in values:
            return cls(kind, subject_id, limit)
        revision = values["view_revision"]
        if len(revision) != 64 or any(char not in "0123456789abcdef" for char in revision):
            raise CatalogViewError("request_validation_failed", 400)
        if "limit" not in values:
            raise CatalogViewError("request_validation_failed", 400)
        return cls(
            kind,
            subject_id,
            limit,
            CatalogPositionCursor(
                canonical_id(values["after_release_id"]) if kind == "recording_releases" else None,
                positive(values["after_medium_position"]),
                positive(values["after_sequence_no"]),
                canonical_id(values["after_release_track_id"]),
            ),
            revision,
        )
