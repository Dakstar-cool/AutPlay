"""Signed all-decision sequence independent of database revisions and backup branches.

Every intent precedes its PostgreSQL commit. A grant is usable only with its exact
current database receipt AND its position as the latest independent owner intent.
Neither timestamps nor reused database revision numbers can supersede a barrier.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

from autplay.domain.training_consent import (
    CompactedErasureFloor,
    SonaEventErasureIntent,
    TrainingConsentEvidenceError,
    TrainingConsentHistory,
    TrainingConsentIntent,
)

_ZERO = "0" * 64
_MAX_EVENTS = 200_000
_SCHEMA = """
CREATE TABLE identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 key_id TEXT NOT NULL, signature TEXT NOT NULL);
CREATE TABLE event (sequence INTEGER PRIMARY KEY CHECK(sequence>0),
 document TEXT NOT NULL CHECK(length(document)<=2048), digest TEXT NOT NULL);
CREATE TABLE head (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 sequence INTEGER NOT NULL, digest TEXT NOT NULL, signature TEXT NOT NULL);
CREATE TRIGGER immutable_identity_update BEFORE UPDATE ON identity
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_identity_delete BEFORE DELETE ON identity
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_event_update BEFORE UPDATE ON event
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_event_delete BEFORE DELETE ON event
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
"""
_LIFECYCLE_SCHEMA = """
CREATE TABLE sona_erasure_event (sequence INTEGER PRIMARY KEY CHECK(sequence>0),
 document TEXT NOT NULL CHECK(length(document)<=2048), digest TEXT NOT NULL);
CREATE TABLE sona_erasure_head (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 sequence INTEGER NOT NULL, digest TEXT NOT NULL, signature TEXT NOT NULL);
CREATE TRIGGER immutable_sona_erasure_update BEFORE UPDATE ON sona_erasure_event
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_sona_erasure_delete BEFORE DELETE ON sona_erasure_event
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TABLE erasure_floor_event (sequence INTEGER PRIMARY KEY CHECK(sequence>0),
 document TEXT NOT NULL CHECK(length(document)<=2048), digest TEXT NOT NULL);
CREATE TABLE erasure_floor_head (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 sequence INTEGER NOT NULL, digest TEXT NOT NULL, signature TEXT NOT NULL);
CREATE TRIGGER immutable_erasure_floor_update BEFORE UPDATE ON erasure_floor_event
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_erasure_floor_delete BEFORE DELETE ON erasure_floor_event
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
"""


class FilesystemTrainingConsentLedger:
    _require_lifecycle_streams = False

    def __init__(self, path: Path, key: bytes, key_id: str) -> None:
        if (
            not path.is_absolute()
            or len(key) < 32
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id)
        ):
            raise TrainingConsentEvidenceError()
        self.path, self._key, self.key_id = path, bytes(key), key_id

    def _mac(self, purpose: str, message: bytes) -> str:
        return hmac.new(
            self._key, purpose.encode("ascii") + b"\0" + message, hashlib.sha256
        ).hexdigest()

    def owner_tag(self, user_id: UUID) -> str:
        return self._mac("training-consent-owner-v1", user_id.bytes)

    def actor_tag(self, device_id: UUID) -> str:
        return self._mac("training-consent-actor-v1", device_id.bytes)

    def _head_mac(self, sequence: int, digest: str) -> str:
        return self._mac("training-consent-head-v1", f"{sequence}:{digest}".encode("ascii"))

    def initialize(self) -> None:
        """Explicit offline operator provisioning; never replace missing or existing history."""
        try:
            with self.path.open("xb"):
                pass
            with self._connection() as connection:
                connection.executescript(
                    _SCHEMA + (_LIFECYCLE_SCHEMA if self._require_lifecycle_streams else "")
                )
                connection.execute(
                    "INSERT INTO identity VALUES(1,?,?)",
                    (self.key_id, self._mac("training-consent-identity-v1", self.key_id.encode())),
                )
                connection.execute(
                    "INSERT INTO head VALUES(1,0,?,?)", (_ZERO, self._head_mac(0, _ZERO))
                )
                if self._require_lifecycle_streams:
                    connection.execute(
                        "INSERT INTO sona_erasure_head VALUES(1,0,?,?)",
                        (_ZERO, self._erasure_head_mac(0, _ZERO)),
                    )
                    connection.execute(
                        "INSERT INTO erasure_floor_head VALUES(1,0,?,?)",
                        (_ZERO, self._floor_head_mac(0, _ZERO)),
                    )
                connection.commit()
            if os.name != "nt":
                descriptor = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            self.read()
        except OSError, sqlite3.Error, ValueError:
            raise TrainingConsentEvidenceError() from None

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            if self.path.is_symlink() or not self.path.is_file():
                raise TrainingConsentEvidenceError()
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA synchronous=EXTRA")
            if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",) or (
                connection.execute("PRAGMA synchronous").fetchone() != (3,)
            ):
                raise TrainingConsentEvidenceError()
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except (
            OSError,
            sqlite3.Error,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            AttributeError,
        ):
            raise TrainingConsentEvidenceError() from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _time(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise TrainingConsentEvidenceError()
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def _read(self, connection: sqlite3.Connection) -> tuple[TrainingConsentHistory, int, str]:
        identity = connection.execute("SELECT key_id,signature FROM identity").fetchall()
        if identity != [
            (self.key_id, self._mac("training-consent-identity-v1", self.key_id.encode()))
        ]:
            raise TrainingConsentEvidenceError()
        head = connection.execute("SELECT sequence,digest,signature FROM head").fetchall()
        if len(head) != 1 or not hmac.compare_digest(head[0][2], self._head_mac(*head[0][:2])):
            raise TrainingConsentEvidenceError()
        expected_sequence, digest = 0, _ZERO
        operations: dict[UUID, TrainingConsentIntent] = {}
        latest: dict[str, TrainingConsentIntent] = {}
        for sequence, raw, stored_digest in connection.execute(
            "SELECT sequence,document,digest FROM event ORDER BY sequence"
        ):
            expected_sequence += 1
            if sequence != expected_sequence or sequence > _MAX_EVENTS or len(raw) > 2048:
                raise TrainingConsentEvidenceError()
            expected = self._mac("training-consent-event-v1", raw.encode("utf-8"))
            if not hmac.compare_digest(stored_digest, expected):
                raise TrainingConsentEvidenceError()
            event = json.loads(raw)
            if (
                set(event)
                != {
                    "v",
                    "sequence",
                    "previous",
                    "owner_tag",
                    "operation_id",
                    "actor_tag",
                    "request_sha256",
                    "previous_policy_sha256",
                    "decision",
                    "revision",
                    "changed_at",
                }
                or type(event["v"]) is not int
                or event["v"] != 1
                or type(event["sequence"]) is not int
                or event["sequence"] != sequence
                or event["previous"] != digest
                or not re.fullmatch(r"[0-9a-f]{64}", event["owner_tag"])
                or not re.fullmatch(r"[0-9a-f]{64}", event["request_sha256"])
                or not re.fullmatch(r"[0-9a-f]{64}", event["previous_policy_sha256"])
                or (
                    event["actor_tag"] is not None
                    and not re.fullmatch(r"[0-9a-f]{64}", event["actor_tag"])
                )
                or event["decision"] not in {"GRANTED", "DENIED", "WITHDRAWN"}
                or (event["actor_tag"] is None and event["decision"] == "GRANTED")
                or type(event["revision"]) is not int
                or not 1 <= event["revision"] <= 9_007_199_254_740_991
                or (event["decision"] == "GRANTED" and event["revision"] >= 9_007_199_254_740_991)
            ):
                raise TrainingConsentEvidenceError()
            at = datetime.fromisoformat(event["changed_at"])
            operation = UUID(event["operation_id"])
            if self._time(at) != event["changed_at"] or str(operation) != event["operation_id"]:
                raise TrainingConsentEvidenceError()
            if operation in operations:
                raise TrainingConsentEvidenceError()
            intent = TrainingConsentIntent(
                event["owner_tag"],
                operation,
                event["actor_tag"],
                event["request_sha256"],
                event["previous_policy_sha256"],
                event["decision"],
                event["revision"],
                at,
            )
            operations[operation] = latest[intent.owner_tag] = intent
            digest = stored_digest
        if head[0][:2] != (expected_sequence, digest):
            raise TrainingConsentEvidenceError()
        return (
            TrainingConsentHistory(MappingProxyType(operations), MappingProxyType(latest)),
            expected_sequence,
            digest,
        )

    def read(self) -> TrainingConsentHistory:
        with self._connection() as connection:
            history, _, _ = self._read(connection)
            if self._require_lifecycle_streams:
                self._read_erasure(connection)
                self._read_floor(connection)
            return history

    def provision_floor_stream(self) -> None:
        """Explicitly upgrade verified evidence before any typed compaction."""
        with self._connection() as connection:
            self._read(connection)
            self._read_erasure(connection)
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE name IN ("
                    "'erasure_floor_event','erasure_floor_head',"
                    "'immutable_erasure_floor_update','immutable_erasure_floor_delete')"
                )
            }
            required = {
                "erasure_floor_event",
                "erasure_floor_head",
                "immutable_erasure_floor_update",
                "immutable_erasure_floor_delete",
            }
            if names == required:
                self._read_floor(connection)
                return
            if names:
                raise TrainingConsentEvidenceError()
            connection.execute(
                "CREATE TABLE erasure_floor_event (sequence INTEGER PRIMARY KEY CHECK(sequence>0), "
                "document TEXT NOT NULL CHECK(length(document)<=2048), digest TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE erasure_floor_head (singleton INTEGER PRIMARY KEY "
                "CHECK(singleton=1), "
                "sequence INTEGER NOT NULL, digest TEXT NOT NULL, signature TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TRIGGER immutable_erasure_floor_update BEFORE UPDATE "
                "ON erasure_floor_event "
                "BEGIN SELECT RAISE(ABORT,'immutable evidence'); END"
            )
            connection.execute(
                "CREATE TRIGGER immutable_erasure_floor_delete BEFORE DELETE "
                "ON erasure_floor_event "
                "BEGIN SELECT RAISE(ABORT,'immutable evidence'); END"
            )
            connection.execute(
                "INSERT INTO erasure_floor_head VALUES(1,0,?,?)",
                (_ZERO, self._floor_head_mac(0, _ZERO)),
            )
            self._read_floor(connection)
            connection.commit()

    def _floor_head_mac(self, sequence: int, digest: str) -> str:
        return self._mac("erasure-floor-head-v1", f"{sequence}:{digest}".encode("ascii"))

    def _read_floor(
        self, connection: sqlite3.Connection
    ) -> tuple[tuple[CompactedErasureFloor, ...], int, str]:
        head = connection.execute(
            "SELECT sequence,digest,signature FROM erasure_floor_head"
        ).fetchall()
        if len(head) != 1 or not hmac.compare_digest(
            head[0][2], self._floor_head_mac(*head[0][:2])
        ):
            raise TrainingConsentEvidenceError()
        sequence, previous = 0, _ZERO
        latest: dict[str, int] = {}
        floors: list[CompactedErasureFloor] = []
        for number, raw, digest in connection.execute(
            "SELECT sequence,document,digest FROM erasure_floor_event ORDER BY sequence"
        ):
            sequence += 1
            if number != sequence or sequence > _MAX_EVENTS or len(raw) > 2048:
                raise TrainingConsentEvidenceError()
            if not hmac.compare_digest(
                digest, self._mac("erasure-floor-event-v1", raw.encode("utf-8"))
            ):
                raise TrainingConsentEvidenceError()
            event = json.loads(raw)
            if (
                type(event) is not dict
                or set(event)
                != {"v", "sequence", "previous", "owner_tag", "floor_sequence", "recorded_at"}
                or type(event["v"]) is not int
                or event["v"] != 1
                or type(event["sequence"]) is not int
                or event["sequence"] != sequence
                or event["previous"] != previous
                or type(event["owner_tag"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", event["owner_tag"])
                or type(event["floor_sequence"]) is not int
                or not latest.get(event["owner_tag"], 0)
                < event["floor_sequence"]
                <= 9_223_372_036_854_775_806
            ):
                raise TrainingConsentEvidenceError()
            at = datetime.fromisoformat(event["recorded_at"])
            if self._time(at) != event["recorded_at"]:
                raise TrainingConsentEvidenceError()
            floor = CompactedErasureFloor(
                event["owner_tag"], event["floor_sequence"], at, sequence, digest
            )
            floors.append(floor)
            latest[floor.owner_tag] = floor.floor_sequence
            previous = digest
        if head[0][:2] != (sequence, previous):
            raise TrainingConsentEvidenceError()
        return tuple(floors), sequence, previous

    def floor_read(self) -> tuple[CompactedErasureFloor, ...]:
        with self._connection() as connection:
            self._read(connection)
            self._read_erasure(connection)
            return self._read_floor(connection)[0]

    def record_floor(self, intent: CompactedErasureFloor) -> CompactedErasureFloor:
        with self._connection() as connection:
            self._read(connection)
            self._read_erasure(connection)
            history, sequence, previous = self._read_floor(connection)
            for prior in reversed(history):
                if prior.owner_tag == intent.owner_tag:
                    if prior.floor_sequence >= intent.floor_sequence:
                        return prior
                    break
            if (
                sequence >= _MAX_EVENTS
                or not re.fullmatch(r"[0-9a-f]{64}", intent.owner_tag)
                or type(intent.floor_sequence) is not int
                or not 0 < intent.floor_sequence <= 9_223_372_036_854_775_806
            ):
                raise TrainingConsentEvidenceError()
            event = {
                "v": 1,
                "sequence": sequence + 1,
                "previous": previous,
                "owner_tag": intent.owner_tag,
                "floor_sequence": intent.floor_sequence,
                "recorded_at": self._time(intent.recorded_at),
            }
            raw = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            digest = self._mac("erasure-floor-event-v1", raw.encode("utf-8"))
            connection.execute(
                "INSERT INTO erasure_floor_event VALUES(?,?,?)", (sequence + 1, raw, digest)
            )
            connection.execute(
                "UPDATE erasure_floor_head SET sequence=?,digest=?,signature=? WHERE singleton=1",
                (sequence + 1, digest, self._floor_head_mac(sequence + 1, digest)),
            )
            self._read_floor(connection)
            connection.commit()
            return CompactedErasureFloor(
                intent.owner_tag, intent.floor_sequence, intent.recorded_at, sequence + 1, digest
            )

    def provision_erasure_stream(self) -> None:
        """Explicit one-time upgrade of a verified pre-erasure consent ledger."""
        with self._connection() as connection:
            self._read(connection)
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE name IN ("
                    "'sona_erasure_event','sona_erasure_head',"
                    "'immutable_sona_erasure_update','immutable_sona_erasure_delete')"
                )
            }
            required = {
                "sona_erasure_event",
                "sona_erasure_head",
                "immutable_sona_erasure_update",
                "immutable_sona_erasure_delete",
            }
            if names == required:
                self._read_erasure(connection)
                return
            if names:
                raise TrainingConsentEvidenceError()
            connection.execute(
                "CREATE TABLE sona_erasure_event (sequence INTEGER PRIMARY KEY CHECK(sequence>0), "
                "document TEXT NOT NULL CHECK(length(document)<=2048), digest TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE sona_erasure_head (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "sequence INTEGER NOT NULL, digest TEXT NOT NULL, signature TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TRIGGER immutable_sona_erasure_update BEFORE UPDATE ON sona_erasure_event "
                "BEGIN SELECT RAISE(ABORT,'immutable evidence'); END"
            )
            connection.execute(
                "CREATE TRIGGER immutable_sona_erasure_delete BEFORE DELETE ON sona_erasure_event "
                "BEGIN SELECT RAISE(ABORT,'immutable evidence'); END"
            )
            connection.execute(
                "INSERT INTO sona_erasure_head VALUES(1,0,?,?)",
                (_ZERO, self._erasure_head_mac(0, _ZERO)),
            )
            self._read_erasure(connection)
            connection.commit()

    def _erasure_head_mac(self, sequence: int, digest: str) -> str:
        return self._mac("sona-erasure-head-v1", f"{sequence}:{digest}".encode("ascii"))

    def _read_erasure(
        self, connection: sqlite3.Connection
    ) -> tuple[tuple[SonaEventErasureIntent, ...], int, str]:
        head = connection.execute(
            "SELECT sequence,digest,signature FROM sona_erasure_head"
        ).fetchall()
        if len(head) != 1 or not hmac.compare_digest(
            head[0][2], self._erasure_head_mac(*head[0][:2])
        ):
            raise TrainingConsentEvidenceError()
        sequence, previous = 0, _ZERO
        seen: set[tuple[str, UUID]] = set()
        intents: list[SonaEventErasureIntent] = []
        by_source: dict[tuple[str, UUID], SonaEventErasureIntent] = {}
        for number, raw, digest in connection.execute(
            "SELECT sequence,document,digest FROM sona_erasure_event ORDER BY sequence"
        ):
            sequence += 1
            if number != sequence or sequence > _MAX_EVENTS or len(raw) > 2048:
                raise TrainingConsentEvidenceError()
            event = json.loads(raw)
            if type(event) is not dict or type(event.get("v")) is not int:
                raise TrainingConsentEvidenceError()
            domain = "sona-erasure-event-v2" if event["v"] == 2 else "sona-erasure-event-v1"
            if not hmac.compare_digest(digest, self._mac(domain, raw.encode("utf-8"))):
                raise TrainingConsentEvidenceError()
            fields = {
                "v",
                "sequence",
                "previous",
                "owner_tag",
                "source_event_id",
                "source_server_sequence",
                "source_watermark",
                "erasure_event_id",
                "erased_at",
            }
            parent_fields = {
                "feedback_parent_source_event_id",
                "feedback_parent_ledger_sequence",
                "feedback_parent_ledger_digest",
                "source_event_type",
            }
            if (
                type(event["v"]) is not int
                or event["v"] not in {1, 2}
                or set(event) != fields | (parent_fields if event["v"] == 2 else set())
                or event["sequence"] != sequence
                or event["previous"] != previous
                or not re.fullmatch(r"[0-9a-f]{64}", event["owner_tag"])
                or type(event["source_server_sequence"]) is not int
                or type(event["source_watermark"]) is not int
                or not 0 < event["source_server_sequence"] <= event["source_watermark"]
            ):
                raise TrainingConsentEvidenceError()
            source_id, erasure_id = UUID(event["source_event_id"]), UUID(event["erasure_event_id"])
            at = datetime.fromisoformat(event["erased_at"])
            if (
                str(source_id) != event["source_event_id"]
                or str(erasure_id) != event["erasure_event_id"]
                or source_id == erasure_id
                or self._time(at) != event["erased_at"]
                or (event["owner_tag"], source_id) in seen
            ):
                raise TrainingConsentEvidenceError()
            seen.add((event["owner_tag"], source_id))
            parent_id = None
            parent_sequence = 0
            parent_digest = ""
            if event["v"] == 2:
                parent_id = UUID(event["feedback_parent_source_event_id"])
                parent_sequence = event["feedback_parent_ledger_sequence"]
                parent_digest = event["feedback_parent_ledger_digest"]
                parent = by_source.get((event["owner_tag"], parent_id))
                if (
                    str(parent_id) != event["feedback_parent_source_event_id"]
                    or type(parent_sequence) is not int
                    or parent is None
                    or parent.feedback_parent_source_event_id is not None
                    or parent.ledger_sequence != parent_sequence
                    or parent.ledger_digest != parent_digest
                    or at < parent.erased_at
                    or event["source_event_type"]
                    not in {"RECOMMENDATION_FEEDBACK_RECORDED", "LISTENING_EVENT_RECORDED"}
                ):
                    raise TrainingConsentEvidenceError()
            intents.append(
                SonaEventErasureIntent(
                    event["owner_tag"],
                    source_id,
                    event["source_server_sequence"],
                    event["source_watermark"],
                    erasure_id,
                    at,
                    sequence,
                    digest,
                    parent_id,
                    parent_sequence,
                    parent_digest,
                    event.get("source_event_type"),
                )
            )
            by_source[(event["owner_tag"], source_id)] = intents[-1]
            previous = digest
        if head[0][:2] != (sequence, previous):
            raise TrainingConsentEvidenceError()
        return tuple(intents), sequence, previous

    def erasure_read(self) -> tuple[SonaEventErasureIntent, ...]:
        with self._connection() as connection:
            self._read(connection)
            self._read_floor(connection)
            return self._read_erasure(connection)[0]

    def record_erasure(self, intent: SonaEventErasureIntent) -> SonaEventErasureIntent:
        with self._connection() as connection:
            self._read(connection)
            self._read_floor(connection)
            history, sequence, previous = self._read_erasure(connection)
            for prior in history:
                if (prior.owner_tag, prior.source_event_id) == (
                    intent.owner_tag,
                    intent.source_event_id,
                ):
                    if prior != SonaEventErasureIntent(
                        intent.owner_tag,
                        intent.source_event_id,
                        intent.source_server_sequence,
                        intent.source_watermark,
                        intent.erasure_event_id,
                        intent.erased_at,
                        prior.ledger_sequence,
                        prior.ledger_digest,
                        intent.feedback_parent_source_event_id,
                        intent.feedback_parent_ledger_sequence,
                        intent.feedback_parent_ledger_digest,
                        intent.source_event_type,
                    ):
                        raise TrainingConsentEvidenceError()
                    return prior
            if sequence >= _MAX_EVENTS or not re.fullmatch(r"[0-9a-f]{64}", intent.owner_tag):
                raise TrainingConsentEvidenceError()
            if not 0 < intent.source_server_sequence <= intent.source_watermark:
                raise TrainingConsentEvidenceError()
            event = {
                "v": 1,
                "sequence": sequence + 1,
                "previous": previous,
                "owner_tag": intent.owner_tag,
                "source_event_id": str(intent.source_event_id),
                "source_server_sequence": intent.source_server_sequence,
                "source_watermark": intent.source_watermark,
                "erasure_event_id": str(intent.erasure_event_id),
                "erased_at": self._time(intent.erased_at),
            }
            if intent.feedback_parent_source_event_id is not None:
                event.update(
                    {
                        "v": 2,
                        "feedback_parent_source_event_id": str(
                            intent.feedback_parent_source_event_id
                        ),
                        "feedback_parent_ledger_sequence": intent.feedback_parent_ledger_sequence,
                        "feedback_parent_ledger_digest": intent.feedback_parent_ledger_digest,
                        "source_event_type": intent.source_event_type,
                    }
                )
            elif (
                intent.feedback_parent_ledger_sequence != 0
                or intent.feedback_parent_ledger_digest
                or intent.source_event_type is not None
            ):
                raise TrainingConsentEvidenceError()
            raw = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            domain = "sona-erasure-event-v2" if event["v"] == 2 else "sona-erasure-event-v1"
            digest = self._mac(domain, raw.encode("utf-8"))
            connection.execute(
                "INSERT INTO sona_erasure_event VALUES(?,?,?)", (sequence + 1, raw, digest)
            )
            connection.execute(
                "UPDATE sona_erasure_head SET sequence=?,digest=?,signature=? WHERE singleton=1",
                (sequence + 1, digest, self._erasure_head_mac(sequence + 1, digest)),
            )
            self._read_erasure(connection)
            connection.commit()
            return SonaEventErasureIntent(
                intent.owner_tag,
                intent.source_event_id,
                intent.source_server_sequence,
                intent.source_watermark,
                intent.erasure_event_id,
                intent.erased_at,
                sequence + 1,
                digest,
                intent.feedback_parent_source_event_id,
                intent.feedback_parent_ledger_sequence,
                intent.feedback_parent_ledger_digest,
                intent.source_event_type,
            )

    def record(self, intent: TrainingConsentIntent) -> TrainingConsentIntent:
        with self._connection() as connection:
            history, sequence, previous = self._read(connection)
            if self._require_lifecycle_streams:
                self._read_erasure(connection)
                self._read_floor(connection)
            if prior := history.operations.get(intent.operation_id):
                if prior != intent:
                    raise TrainingConsentEvidenceError()
                return prior
            reserves = sum(item.decision == "GRANTED" for item in history.latest.values())
            previous_owner = history.latest.get(intent.owner_tag)
            reserves += int(intent.decision == "GRANTED") - int(
                previous_owner is not None and previous_owner.decision == "GRANTED"
            )
            # Each latest grant (even before its PG commit) reserves its future private barrier.
            if sequence + 1 + reserves > _MAX_EVENTS:
                raise TrainingConsentEvidenceError()
            event = {
                "v": 1,
                "sequence": sequence + 1,
                "previous": previous,
                "owner_tag": intent.owner_tag,
                "operation_id": str(intent.operation_id),
                "actor_tag": intent.actor_tag,
                "request_sha256": intent.request_sha256,
                "previous_policy_sha256": intent.previous_policy_sha256,
                "decision": intent.decision,
                "revision": intent.revision,
                "changed_at": self._time(intent.changed_at),
            }
            raw = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            digest = self._mac("training-consent-event-v1", raw.encode("utf-8"))
            connection.execute("INSERT INTO event VALUES(?,?,?)", (sequence + 1, raw, digest))
            connection.execute(
                "UPDATE head SET sequence=?,digest=?,signature=? WHERE singleton=1",
                (sequence + 1, digest, self._head_mac(sequence + 1, digest)),
            )
            self._read(connection)
            connection.commit()
            return intent
