"""C1 live owner closure, SQL batches <=100, no catalog cache or DDL.

Revision recipe is frozen in MACOS_CATALOG_DETAILS_PROPOSAL.md. This module
authors hashing for future serving; source implementation runs no vectors.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.domain.auth import Principal
from autplay.domain.catalog_view import (
    MAX_CREDIT_MEMBERS,
    MAX_REPLY_BYTES,
    CatalogPositionCursor,
    CatalogViewError,
    CatalogViewQuery,
)

_RECORDING = (
    "recording_id",
    "row_version",
    "title",
    "duration_ms",
    "version_text",
    "disambiguation",
    "recording_kind",
    "identity_status",
    "artist_credit_id",
    "deleted_at",
)
_RELEASE = (
    "release_id",
    "row_version",
    "title",
    "release_date",
    "date_precision",
    "country_code",
    "status",
    "artist_credit_id",
    "deleted_at",
)
_MEDIUM = ("medium_id", "release_id", "position", "row_version", "deleted_at")
_POSITION = (
    "release_track_id",
    "medium_id",
    "recording_id",
    "artist_credit_id",
    "sequence_no",
    "number_text",
    "title",
    "duration_ms",
    "hidden",
    "row_version",
    "deleted_at",
)
_ARTIST = (
    "artist_id",
    "row_version",
    "name",
    "sort_name",
    "artist_type",
    "disambiguation",
    "country_code",
    "identity_status",
    "deleted_at",
)
_CREDIT = ("artist_credit_id", "row_version", "display_name", "deleted_at")
_MEMBER = ("artist_credit_id", "position", "artist_id", "credited_name", "join_phrase", "role")
_REF = (
    "user_track_ref_id",
    "user_id",
    "recording_id",
    "row_version",
    "resolution_status",
    "deleted_at",
)

# The reachability relation is the same live owner Recording relation used by
# catalog sync. Client owner_scope_id, display labels and provider IDs are absent.
_OWNED = """EXISTS (SELECT 1 FROM library.user_track_ref own
    WHERE own.user_id=:owner AND own.recording_id=r.recording_id
      AND own.deleted_at IS NULL AND own.resolution_status='RESOLVED')"""
_CURRENT = """r.deleted_at IS NULL AND r.identity_status IN ('ACTIVE','PROVISIONAL')
    AND NOT EXISTS (SELECT 1 FROM identity.recording_redirect redirect
                    WHERE redirect.source_recording_id=r.recording_id)"""
_JOINS = """FROM catalog.release_track rt
    JOIN catalog.medium m ON m.medium_id=rt.medium_id
    JOIN catalog.release rel ON rel.release_id=m.release_id
    JOIN catalog.recording r ON r.recording_id=rt.recording_id"""
_POSITIONS = f"""{_JOINS} WHERE rt.deleted_at IS NULL AND m.deleted_at IS NULL
    AND rel.deleted_at IS NULL AND {_CURRENT} AND {_OWNED}"""


def _wire(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _wire_size(value: Any) -> int:
    size = 0
    for fragment in json.JSONEncoder(
        ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).iterencode(value):
        size += len(fragment.encode("utf-8"))
        if size > MAX_REPLY_BYTES:
            raise CatalogViewError("catalog_view_capacity")
    return size


class _Revision:
    def __init__(self, check: Callable[[], None]) -> None:
        self._digest = hashlib.sha256()
        self._digest.update(b"AutPlay.CatalogView.C1.rev1\x00")
        self._check = check

    def record(self, tag: str, *values: Any) -> None:
        self._check()
        fields = (tag, *values)
        self._digest.update(len(fields).to_bytes(4, "big"))
        for value in fields:
            if value is None:
                self._digest.update(b"\x00")
            else:
                rendered = _wire(value)
                if isinstance(rendered, bool):
                    rendered = "true" if rendered else "false"
                encoded = str(rendered).encode("utf-8", "strict")
                self._digest.update(b"\x01" + len(encoded).to_bytes(8, "big") + encoded)

    def row(self, tag: str, row: Mapping[str, Any], names: tuple[str, ...]) -> None:
        self.record(tag, *(row[name] for name in names))

    def finish(self) -> str:
        self.record("end")
        return self._digest.hexdigest()


class PostgresCatalogViewRead:
    def __init__(self, session: Session, principal: Principal, check: Callable[[], None]) -> None:
        self._session, self._principal, self._check = session, principal, check
        self._revision = _Revision(check)

    def _rows(self, sql: str, **parameters: Any) -> Iterator[Mapping[str, Any]]:
        # All callers supply a fixed ORDER BY over unique keys. OFFSET is only
        # an internal bounded-memory full scan in one repeatable-read snapshot.
        offset = 0
        while True:
            self._check()
            with self._session.execute(
                text(sql + " LIMIT 100 OFFSET :batch_offset"),
                {"owner": self._principal.user_id, **parameters, "batch_offset": offset},
            ) as result:
                batch = list(result.mappings())
            for row in batch:
                self._check()
                yield dict(row)
            if len(batch) < 100:
                return
            offset += 100

    def _one(self, sql: str, **parameters: Any) -> Mapping[str, Any] | None:
        return next(self._rows(sql, **parameters), None)

    def _text_capacity(
        self, table: str, key: str, identifier: UUID, fields: tuple[str, ...]
    ) -> None:
        # Literal call sites only. Refuse unbounded Text values in PostgreSQL
        # before transferring their contents into a Python row batch.
        expression = " OR ".join(
            "coalesce(octet_length(" + field + "),0)>262144" for field in fields
        )
        row = self._one(
            "SELECT ("
            + expression
            + ") AS oversized FROM catalog."
            + table
            + " WHERE "
            + key
            + "=:id ORDER BY "
            + key,
            id=identifier,
        )
        if row is not None and row["oversized"]:
            raise CatalogViewError("catalog_view_capacity")

    def _credit(self, credit_id: UUID) -> tuple[dict[str, Any] | None, bool]:
        credit = self._one(
            "SELECT artist_credit_id,row_version,display_name,deleted_at "
            "FROM catalog.artist_credit WHERE artist_credit_id=:id ORDER BY artist_credit_id",
            id=credit_id,
        )
        if credit is None:
            self._revision.record("missing_credit", credit_id)
            return None, False
        self._revision.row("credit", credit, _CREDIT)
        valid = credit["deleted_at"] is None
        members: list[dict[str, Any]] = []
        retained_bytes = _wire_size(credit["display_name"]) + 128
        for member in self._rows(
            "SELECT artist_credit_id,position,artist_id,credited_name,join_phrase,role "
            "FROM catalog.artist_credit_name WHERE artist_credit_id=:id ORDER BY position",
            id=credit_id,
        ):
            if len(members) >= MAX_CREDIT_MEMBERS:
                raise CatalogViewError("catalog_view_capacity")
            self._revision.row("member", member, _MEMBER)
            self._text_capacity("artist", "artist_id", member["artist_id"], ("disambiguation",))
            artist = self._one(
                "SELECT "
                + ",".join(_ARTIST)
                + " FROM catalog.artist WHERE artist_id=:id ORDER BY artist_id",
                id=member["artist_id"],
            )
            if artist is None:
                valid = False
                self._revision.record("missing_artist", member["artist_id"])
            else:
                self._revision.row("artist", artist, _ARTIST)
                valid = (
                    valid
                    and artist["deleted_at"] is None
                    and artist["identity_status"] in ("ACTIVE", "PROVISIONAL")
                )
            item = {name: _wire(member[name]) for name in _MEMBER if name != "artist_credit_id"}
            retained_bytes += _wire_size(item) + 1
            if retained_bytes > MAX_REPLY_BYTES:
                raise CatalogViewError("catalog_view_capacity")
            members.append(item)
        return {
            "credit_id": str(credit_id),
            "row_version": credit["row_version"],
            "display_name": credit["display_name"],
            "members": members,
        }, valid

    def _entity(
        self, table: str, fields: tuple[str, ...], identifier: UUID
    ) -> tuple[Mapping[str, Any], dict[str, Any], bool]:
        # table/fields come exclusively from the two literal call sites below.
        key = fields[0]
        if table == "recording":
            self._text_capacity(table, key, identifier, ("version_text", "disambiguation"))
        row = self._one(
            "SELECT " + ",".join(fields) + f" FROM catalog.{table} WHERE {key}=:id ORDER BY {key}",
            id=identifier,
        )
        if row is None:
            raise CatalogViewError("not_found", 404)
        self._revision.row(table, row, fields)
        credit, visible = self._credit(row["artist_credit_id"])
        visible = visible and row["deleted_at"] is None
        if table == "recording":
            visible = visible and row["identity_status"] in ("ACTIVE", "PROVISIONAL")
        detail = {
            name: _wire(row[name])
            for name in fields
            if name not in ("artist_credit_id", "deleted_at")
        }
        detail["credit"] = credit
        _wire_size(detail)
        return row, detail, visible

    def read(self, query: CatalogViewQuery) -> dict[str, Any]:
        recording_view = query.kind.startswith("recording_")
        condition = "r.recording_id=:subject" if recording_view else "rel.release_id=:subject"
        parameters = {"subject": query.subject_id}
        owned = (
            self._one(
                "SELECT r.recording_id FROM catalog.recording r WHERE r.recording_id=:subject AND "
                + _CURRENT
                + " AND "
                + _OWNED
                + " ORDER BY r.recording_id",
                **parameters,
            )
            if recording_view
            else self._one(
                "SELECT rt.release_track_id "
                + _POSITIONS
                + " AND "
                + condition
                + " ORDER BY rt.release_track_id",
                **parameters,
            )
        )
        if owned is None:
            raise CatalogViewError("not_found", 404)
        self._revision.record(
            "view", query.kind, query.subject_id, self._principal.user_id, query.limit
        )
        owner = self._one(
            "SELECT user_id,row_version,status,role,authority_generation,deleted_at "
            "FROM account.user_account WHERE user_id=:owner ORDER BY user_id"
        )
        if owner is None or owner["status"] != "ACTIVE" or owner["deleted_at"] is not None:
            raise CatalogViewError("authentication_required", 401)
        self._revision.row(
            "owner",
            owner,
            ("user_id", "row_version", "status", "role", "authority_generation", "deleted_at"),
        )
        _, detail, subject_visible = self._entity(
            "recording" if recording_view else "release",
            _RECORDING if recording_view else _RELEASE,
            query.subject_id,
        )
        if not subject_visible:
            raise CatalogViewError("not_found", 404)
        items: list[dict[str, Any]] = []
        retained_bytes = 512
        scanned: CatalogPositionCursor | None = None
        consumed = 0
        exhausted = True
        after_seen = query.after is None
        position_sql = (
            "SELECT rt.release_track_id,m.medium_id,rel.release_id,r.recording_id,"
            "m.position AS medium_position,rt.sequence_no "
            + _POSITIONS
            + " AND "
            + condition
            + " ORDER BY rel.release_id,m.position,rt.sequence_no,rt.release_track_id"
        )
        for position in self._rows(position_sql, **parameters):
            release, _, release_visible = self._entity("release", _RELEASE, position["release_id"])
            medium = self._one(
                "SELECT "
                + ",".join(_MEDIUM)
                + " FROM catalog.medium WHERE medium_id=:id ORDER BY medium_id",
                id=position["medium_id"],
            )
            self._text_capacity(
                "release_track", "release_track_id", position["release_track_id"], ("number_text",)
            )
            track = self._one(
                "SELECT "
                + ",".join(_POSITION)
                + (
                    " FROM catalog.release_track WHERE release_track_id=:id ORDER BY "
                    "release_track_id"
                ),
                id=position["release_track_id"],
            )
            if medium is None or track is None:
                raise CatalogViewError()
            self._revision.row("medium", medium, _MEDIUM)
            self._revision.row("position", track, _POSITION)
            _, _, recording_visible = self._entity(
                "recording", _RECORDING, position["recording_id"]
            )
            credit, credit_visible = self._credit(track["artist_credit_id"])
            cursor = CatalogPositionCursor(
                position["release_id"] if recording_view else None,
                position["medium_position"],
                position["sequence_no"],
                position["release_track_id"],
            )
            if not after_seen:
                if cursor == query.after:
                    after_seen = True
                continue
            if query.kind.endswith("detail"):
                continue
            if consumed == query.limit:
                exhausted = False
                continue
            consumed += 1
            scanned = cursor
            # Invisible/unsupported credit rows still advance this owner-only
            # raw cursor. They never become a truncated successful detail.
            if not (release_visible and recording_visible and credit_visible):
                continue
            item = {
                name: _wire(track[name])
                for name in (
                    "release_track_id",
                    "recording_id",
                    "sequence_no",
                    "number_text",
                    "title",
                    "duration_ms",
                    "row_version",
                )
            }
            item.update(
                {
                    "medium_id": str(medium["medium_id"]),
                    "medium_position": medium["position"],
                    "release_id": str(release["release_id"]),
                    "release_title": release["title"],
                    "release_date": _wire(release["release_date"]),
                    "date_precision": release["date_precision"],
                    "credit": credit,
                }
            )
            retained_bytes += _wire_size(item) + 1
            if retained_bytes > MAX_REPLY_BYTES:
                raise CatalogViewError("catalog_view_capacity")
            items.append(item)
        # Exact complete current owner Recording proof, never a first-page set.
        proof_sql = (
            (
                "SELECT r.recording_id FROM catalog.recording r WHERE "
                + _CURRENT
                + " AND "
                + _OWNED
                + " AND r.recording_id=:subject ORDER BY r.recording_id"
            )
            if recording_view
            else (
                "SELECT DISTINCT r.recording_id "
                + _POSITIONS
                + " AND rel.release_id=:subject ORDER BY r.recording_id"
            )
        )
        for proof in self._rows(proof_sql, **parameters):
            self._revision.record("proof", proof["recording_id"])
            for ref in self._rows(
                "SELECT " + ",".join(_REF) + " FROM library.user_track_ref "
                "WHERE user_id=:owner AND recording_id=:recording AND deleted_at IS NULL "
                "AND resolution_status='RESOLVED' ORDER BY user_track_ref_id",
                recording=proof["recording_id"],
            ):
                self._revision.row("ref", ref, _REF)
        revision = self._revision.finish()
        if query.revision is not None and (revision != query.revision or not after_seen):
            raise CatalogViewError("catalog_view_changed", 409)
        result: dict[str, Any] = {
            "schema": 1,
            "kind": query.kind,
            "subject_id": str(query.subject_id),
            "owner_id": str(self._principal.user_id),
            "view_revision": revision,
        }
        if query.kind.endswith("detail"):
            result["detail"] = detail
        else:
            result.update(
                {
                    "items": items,
                    "scanned_through": scanned.wire() if scanned is not None else None,
                    "exhausted": exhausted,
                    "owner_visible_subset": True,
                }
            )
        return result
