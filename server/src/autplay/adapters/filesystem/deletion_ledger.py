"""Separately provisioned SQLite evidence store with EXTRA durability and signed history.

The file and key must be retained independently of PostgreSQL backup generations.
Opening never initializes an absent store. All evidence is retained indefinitely;
this adapter intentionally has no expiry, reset, repair or deletion operation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from autplay.domain.privacy_deletion import (
    DeletionEvidence,
    DeletionEvidenceError,
    DeletionRequestEvidence,
)

_ZERO = "0" * 64
_MAX_EVENTS = 200_000
_SCHEMA = """
CREATE TABLE ledger_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 key_id TEXT NOT NULL, signature TEXT NOT NULL);
CREATE TABLE deletion_event (sequence INTEGER PRIMARY KEY CHECK(sequence>0),
 document TEXT NOT NULL CHECK(length(document)<=2048), digest TEXT NOT NULL);
CREATE TABLE ledger_head (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 sequence INTEGER NOT NULL, digest TEXT NOT NULL, signature TEXT NOT NULL);
CREATE TRIGGER immutable_identity_update BEFORE UPDATE ON ledger_identity
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_identity_delete BEFORE DELETE ON ledger_identity
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_event_update BEFORE UPDATE ON deletion_event
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_event_delete BEFORE DELETE ON deletion_event
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TABLE request_event (sequence INTEGER PRIMARY KEY CHECK(sequence>0),
 document TEXT NOT NULL CHECK(length(document)<=2048), digest TEXT NOT NULL);
CREATE TABLE request_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 coverage_started_at TEXT NOT NULL, signature TEXT NOT NULL);
CREATE TABLE request_head (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 sequence INTEGER NOT NULL, digest TEXT NOT NULL, signature TEXT NOT NULL);
CREATE TRIGGER immutable_request_update BEFORE UPDATE ON request_event
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_request_delete BEFORE DELETE ON request_event
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_request_identity_update BEFORE UPDATE ON request_identity
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER immutable_request_identity_delete BEFORE DELETE ON request_identity
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
"""


class FilesystemDeletionLedger:
    def __init__(self, path: Path, key: bytes, key_id: str) -> None:
        if (
            not path.is_absolute()
            or len(key) < 32
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id)
        ):
            raise DeletionEvidenceError()
        self.path, self._key, self.key_id = path, key, key_id

    def _mac(self, purpose: str, message: bytes) -> str:
        return hmac.new(
            self._key, purpose.encode("ascii") + b"\0" + message, hashlib.sha256
        ).hexdigest()

    def owner_tag(self, user_id: UUID) -> str:
        return self._mac("privacy-delete-v1", user_id.bytes)

    def initialize(self, *, coverage_started_at: datetime | None = None) -> None:
        """Offline provisioning after prior acceptors stop; never replace existing history.

        The default samples this trusted cutover. An explicit time is for controlled
        provisioning/fixtures and must not predate the last unjournalled acceptor.
        """
        try:
            # Initial provisioning only: never overwrite an existing file or replace lost history.
            with self.path.open("xb"):
                pass
            with self._connection() as connection:
                connection.executescript(_SCHEMA)
                connection.execute(
                    "INSERT INTO ledger_identity VALUES(1,?,?)",
                    (self.key_id, self._mac("privacy-ledger-identity-v1", self.key_id.encode())),
                )
                connection.execute(
                    "INSERT INTO ledger_head VALUES(1,0,?,?)", (_ZERO, self._head_mac(0, _ZERO))
                )
                connection.execute(
                    "INSERT INTO request_head VALUES(1,0,?,?)",
                    (_ZERO, self._request_head_mac(0, _ZERO)),
                )
                coverage = self._time(coverage_started_at or datetime.now(UTC))
                connection.execute(
                    "INSERT INTO request_identity VALUES(1,?,?)",
                    (coverage, self._request_identity_mac(coverage)),
                )
                connection.commit()
            self._sync_parent()
            self.read()
        except OSError, sqlite3.Error, ValueError:
            raise DeletionEvidenceError() from None

    def _sync_parent(self) -> None:
        # Initial creation also persists the new database directory entry on Linux.
        import os

        if os.name != "nt":
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            if self.path.is_symlink() or not self.path.is_file():
                raise DeletionEvidenceError()
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA synchronous=EXTRA")
            if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",) or (
                connection.execute("PRAGMA synchronous").fetchone() != (3,)
            ):
                raise DeletionEvidenceError()
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
            raise DeletionEvidenceError() from None
        finally:
            if connection is not None:
                connection.close()

    def _head_mac(self, sequence: int, digest: str) -> str:
        return self._mac("privacy-ledger-head-v1", f"{sequence}:{digest}".encode("ascii"))

    @staticmethod
    def _time(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise DeletionEvidenceError()
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def _read(self, connection: sqlite3.Connection) -> tuple[dict[str, DeletionEvidence], int, str]:
        identity = connection.execute("SELECT key_id,signature FROM ledger_identity").fetchall()
        if identity != [
            (self.key_id, self._mac("privacy-ledger-identity-v1", self.key_id.encode()))
        ]:
            raise DeletionEvidenceError()
        head = connection.execute("SELECT sequence,digest,signature FROM ledger_head").fetchall()
        if len(head) != 1 or not hmac.compare_digest(head[0][2], self._head_mac(*head[0][:2])):
            raise DeletionEvidenceError()
        expected_sequence, digest = 0, _ZERO
        records: dict[str, DeletionEvidence] = {}
        for sequence, raw, stored_digest in connection.execute(
            "SELECT sequence,document,digest FROM deletion_event ORDER BY sequence"
        ):
            expected_sequence += 1
            if sequence != expected_sequence or sequence > _MAX_EVENTS or len(raw) > 2048:
                raise DeletionEvidenceError()
            expected = self._mac("privacy-ledger-event-v1", raw.encode("utf-8"))
            if not hmac.compare_digest(stored_digest, expected):
                raise DeletionEvidenceError()
            event = json.loads(raw)
            if (
                set(event)
                != {
                    "v",
                    "sequence",
                    "previous",
                    "kind",
                    "owner_tag",
                    "request_id",
                    "at",
                    "removed_rows",
                }
                or event["v"] != 1
                or event["sequence"] != sequence
                or event["previous"] != digest
                or not re.fullmatch(r"[0-9a-f]{64}", event["owner_tag"])
            ):
                raise DeletionEvidenceError()
            at = datetime.fromisoformat(event["at"])
            if self._time(at) != event["at"]:
                raise DeletionEvidenceError()
            tag, request_id = event["owner_tag"], UUID(event["request_id"])
            prior = records.get(tag)
            if event["kind"] == "PREPARED" and prior is None and event["removed_rows"] is None:
                records[tag] = DeletionEvidence(tag, request_id, at)
            elif (
                event["kind"] == "COMPLETED"
                and prior is not None
                and prior.request_id == request_id
                and prior.completed_at is None
                and at >= prior.accepted_at
                and type(event["removed_rows"]) is int
                and 0 <= event["removed_rows"] <= 2**63 - 1
            ):
                records[tag] = replace(prior, completed_at=at, removed_rows=event["removed_rows"])
            else:
                raise DeletionEvidenceError()
            digest = stored_digest
        if head[0][:2] != (expected_sequence, digest):
            raise DeletionEvidenceError()
        return records, expected_sequence, digest

    def read(self) -> tuple[DeletionEvidence, ...]:
        with self._connection() as connection:
            records, _, _ = self._read(connection)
            self._request_read(connection)
            return tuple(records.values())

    def _append(
        self,
        connection: sqlite3.Connection,
        sequence: int,
        previous: str,
        *,
        kind: str,
        tag: str,
        request_id: UUID,
        at: datetime,
        removed_rows: int | None,
    ) -> None:
        if sequence >= _MAX_EVENTS:
            raise DeletionEvidenceError()
        event: dict[str, Any] = {
            "v": 1,
            "sequence": sequence + 1,
            "previous": previous,
            "kind": kind,
            "owner_tag": tag,
            "request_id": str(request_id),
            "at": self._time(at),
            "removed_rows": removed_rows,
        }
        raw = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = self._mac("privacy-ledger-event-v1", raw.encode("utf-8"))
        connection.execute("INSERT INTO deletion_event VALUES(?,?,?)", (sequence + 1, raw, digest))
        connection.execute(
            "UPDATE ledger_head SET sequence=?,digest=?,signature=? WHERE singleton=1",
            (sequence + 1, digest, self._head_mac(sequence + 1, digest)),
        )
        self._read(connection)
        connection.commit()

    def prepare(self, user_id: UUID, request_id: UUID, accepted_at: datetime) -> DeletionEvidence:
        tag = self.owner_tag(user_id)
        with self._connection() as connection:
            records, sequence, digest = self._read(connection)
            self._request_read(connection)
            if prior := records.get(tag):
                if prior.request_id != request_id or prior.accepted_at != accepted_at:
                    raise DeletionEvidenceError()
                return prior
            pending = sum(item.completed_at is None for item in records.values())
            if sequence + pending + 2 > _MAX_EVENTS:
                # Every accepted intent reserves the future durable completion event.
                raise DeletionEvidenceError()
            self._append(
                connection,
                sequence,
                digest,
                kind="PREPARED",
                tag=tag,
                request_id=request_id,
                at=accepted_at,
                removed_rows=None,
            )
            return DeletionEvidence(tag, request_id, accepted_at)

    def complete(
        self, owner_tag: str, request_id: UUID, completed_at: datetime, removed_rows: int
    ) -> DeletionEvidence:
        with self._connection() as connection:
            records, sequence, digest = self._read(connection)
            self._request_read(connection)
            prior = records.get(owner_tag)
            if prior is None or prior.request_id != request_id:
                raise DeletionEvidenceError()
            if prior.completed_at is not None:
                return prior
            self._append(
                connection,
                sequence,
                digest,
                kind="COMPLETED",
                tag=owner_tag,
                request_id=request_id,
                at=completed_at,
                removed_rows=removed_rows,
            )
            return replace(prior, completed_at=completed_at, removed_rows=removed_rows)

    def _request_head_mac(self, sequence: int, digest: str) -> str:
        return self._mac("privacy-request-head-v1", f"{sequence}:{digest}".encode("ascii"))

    def _request_identity_mac(self, coverage: str) -> str:
        return self._mac("privacy-request-identity-v1", f"{self.key_id}:{coverage}".encode("ascii"))

    def _request_coverage(self, connection: sqlite3.Connection) -> datetime:
        identity = connection.execute(
            "SELECT coverage_started_at,signature FROM request_identity"
        ).fetchall()
        if len(identity) != 1 or not hmac.compare_digest(
            identity[0][1], self._request_identity_mac(identity[0][0])
        ):
            raise DeletionEvidenceError()
        coverage = datetime.fromisoformat(identity[0][0])
        if self._time(coverage) != identity[0][0]:
            raise DeletionEvidenceError()
        return coverage

    def request_coverage_started_at(self) -> datetime:
        with self._connection() as connection:
            self._read(connection)
            self._request_read(connection)
            return self._request_coverage(connection)

    def _request_read(
        self, connection: sqlite3.Connection
    ) -> tuple[dict[UUID, DeletionRequestEvidence], int, str]:
        self._request_coverage(connection)
        head = connection.execute("SELECT sequence,digest,signature FROM request_head").fetchall()
        if len(head) != 1 or not hmac.compare_digest(
            head[0][2], self._request_head_mac(*head[0][:2])
        ):
            raise DeletionEvidenceError()
        expected_sequence, digest = 0, _ZERO
        records: dict[UUID, DeletionRequestEvidence] = {}
        cancel_operations: set[UUID] = set()
        for sequence, raw, stored_digest in connection.execute(
            "SELECT sequence,document,digest FROM request_event ORDER BY sequence"
        ):
            expected_sequence += 1
            if sequence != expected_sequence or sequence > _MAX_EVENTS or len(raw) > 2048:
                raise DeletionEvidenceError()
            expected = self._mac("privacy-request-event-v1", raw.encode("utf-8"))
            if not hmac.compare_digest(stored_digest, expected):
                raise DeletionEvidenceError()
            event = json.loads(raw)
            if (
                set(event)
                != {
                    "v",
                    "sequence",
                    "previous",
                    "owner_tag",
                    "request_id",
                    "request_sha256",
                    "receipt_sha256",
                    "decision",
                    "decided_at",
                    "cancel_operation_id",
                    "cancel_request_sha256",
                    "cancelled_at",
                }
                or type(event["v"]) is not int
                or event["v"] != 1
                or type(event["sequence"]) is not int
                or event["sequence"] != sequence
                or event["previous"] != digest
                or any(
                    not re.fullmatch(r"[0-9a-f]{64}", event[name])
                    for name in ("owner_tag", "request_sha256", "receipt_sha256")
                )
                or event["decision"] not in {"ATTEMPTED", "SEALED"}
                or event["receipt_sha256"] == _ZERO
            ):
                raise DeletionEvidenceError()
            operation = UUID(event["request_id"])
            at = datetime.fromisoformat(event["decided_at"])
            if str(operation) != event["request_id"] or self._time(at) != event["decided_at"]:
                raise DeletionEvidenceError()
            if operation in cancel_operations:
                raise DeletionEvidenceError()
            cancel = event["cancel_operation_id"]
            cancel_hash, cancel_at = event["cancel_request_sha256"], event["cancelled_at"]
            if cancel is None:
                if cancel_hash is not None or cancel_at is not None:
                    raise DeletionEvidenceError()
                cancelled = None
            else:
                cancelled = datetime.fromisoformat(cancel_at)
                if (
                    event["decision"] != "ATTEMPTED"
                    or str(UUID(cancel)) != cancel
                    or not re.fullmatch(r"[0-9a-f]{64}", cancel_hash)
                    or self._time(cancelled) != cancel_at
                    or not at <= cancelled < at + timedelta(days=30)
                    or UUID(cancel) == operation
                    or UUID(cancel) in records
                    or UUID(cancel) in cancel_operations
                ):
                    raise DeletionEvidenceError()
                cancel_operations.add(UUID(cancel))
            item = DeletionRequestEvidence(
                event["owner_tag"],
                operation,
                event["request_sha256"],
                event["receipt_sha256"],
                event["decision"],
                at,
                UUID(cancel) if cancel is not None else None,
                cancel_hash,
                cancelled,
            )
            prior = records.get(operation)
            if prior is None:
                if cancel is not None:
                    raise DeletionEvidenceError()
            elif (
                prior.decision != "ATTEMPTED"
                or prior.cancel_operation_id is not None
                or cancel is None
                or replace(
                    item, cancel_operation_id=None, cancel_request_sha256=None, cancelled_at=None
                )
                != prior
            ):
                raise DeletionEvidenceError()
            records[operation] = item
            digest = stored_digest
        if head[0][:2] != (expected_sequence, digest):
            raise DeletionEvidenceError()
        return records, expected_sequence, digest

    def request_read(self) -> tuple[DeletionRequestEvidence, ...]:
        with self._connection() as connection:
            self._read(connection)
            records, _, _ = self._request_read(connection)
            return tuple(records.values())

    def request_record(self, evidence: DeletionRequestEvidence) -> DeletionRequestEvidence:
        """An attempt never becomes a negative; cancellation preserves its original proof."""
        with self._connection() as connection:
            self._read(connection)
            records, sequence, previous = self._request_read(connection)
            if records.get(evidence.request_id) == evidence:
                return evidence
            reserves = sum(
                item.decision == "ATTEMPTED" and item.cancel_operation_id is None
                for item in records.values()
            )
            if evidence.request_id not in records:
                reserves += int(evidence.decision == "ATTEMPTED")
            elif evidence.cancel_operation_id is not None:
                reserves -= 1
            if sequence + 1 + reserves > _MAX_EVENTS:
                raise DeletionEvidenceError()
            event = {
                "v": 1,
                "sequence": sequence + 1,
                "previous": previous,
                "owner_tag": evidence.owner_tag,
                "request_id": str(evidence.request_id),
                "request_sha256": evidence.request_sha256,
                "receipt_sha256": evidence.receipt_sha256,
                "decision": evidence.decision,
                "decided_at": self._time(evidence.decided_at),
                "cancel_operation_id": str(evidence.cancel_operation_id)
                if evidence.cancel_operation_id is not None
                else None,
                "cancel_request_sha256": evidence.cancel_request_sha256,
                "cancelled_at": self._time(evidence.cancelled_at)
                if evidence.cancelled_at is not None
                else None,
            }
            raw = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            digest = self._mac("privacy-request-event-v1", raw.encode("utf-8"))
            connection.execute(
                "INSERT INTO request_event VALUES(?,?,?)", (sequence + 1, raw, digest)
            )
            connection.execute(
                "UPDATE request_head SET sequence=?,digest=?,signature=? WHERE singleton=1",
                (sequence + 1, digest, self._request_head_mac(sequence + 1, digest)),
            )
            self._request_read(connection)
            connection.commit()
            return evidence
