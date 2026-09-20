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


class FilesystemTrainingConsentLedger:
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
                connection.executescript(_SCHEMA)
                connection.execute(
                    "INSERT INTO identity VALUES(1,?,?)",
                    (self.key_id, self._mac("training-consent-identity-v1", self.key_id.encode())),
                )
                connection.execute(
                    "INSERT INTO head VALUES(1,0,?,?)", (_ZERO, self._head_mac(0, _ZERO))
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
            return history

    def record(self, intent: TrainingConsentIntent) -> TrainingConsentIntent:
        with self._connection() as connection:
            history, sequence, previous = self._read(connection)
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
