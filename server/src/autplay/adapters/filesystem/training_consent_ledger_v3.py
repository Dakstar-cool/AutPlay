"""One bounded SQLite snapshot, reusing the unchanged signed-ledger verifier.

This adapter provisions nothing during reads. Global file/table admission and
all repeated scans, copies and cryptographic work use the existing G meter.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import os
import sqlite3
import struct
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, cast
from uuid import UUID

from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.domain.native_original_capture_v3 import NativeOriginalCaptureV3DigestMeter
from autplay.domain.training_consent import (
    CompactedErasureFloor,
    SonaEventErasureIntent,
    TrainingConsentEvidenceError,
    TrainingConsentHistory,
)
from autplay.domain.training_consent_v3 import (
    MAX_NATIVE_CONSENT_SNAPSHOT_BYTES_V3,
    NativeConsentLedgerDescriptorV3,
    NativeConsentSnapshotPreludeV3,
    NativeConsentStreamDescriptorV3,
    NativeTrainingConsentSnapshotV3,
)
from autplay.ports.training_consent_v3 import (
    NativeConsentSnapshotAdmitterV3,
    NativeConsentSnapshotSinkV3,
)

_PREFIX = b"AUTPLAY_NATIVE_CONSENT_SNAPSHOT_V3\0"
_I64 = struct.Struct(">q")
_U32 = struct.Struct(">I")
_TABLES = (
    ("identity", ("key_id", "signature"), False),
    ("head", ("sequence", "digest", "signature"), False),
    ("event", ("sequence", "document", "digest"), True),
    ("sona_erasure_head", ("sequence", "digest", "signature"), False),
    ("sona_erasure_event", ("sequence", "document", "digest"), True),
    ("erasure_floor_head", ("sequence", "digest", "signature"), False),
    ("erasure_floor_event", ("sequence", "document", "digest"), True),
)


class _MeterRefusal(RuntimeError):
    def __init__(self, cause: Exception) -> None:
        super().__init__("native_consent_meter_refusal")
        self.cause = cause


class _Work:
    """Preserve meter refusals through the old connection's error sanitization."""

    def __init__(self, meter: NativeOriginalCaptureV3DigestMeter) -> None:
        self.meter = meter

    def reserve(self, byte_work: int, nodes: int) -> None:
        try:
            self.meter.reserve(byte_work_bound=byte_work, node_visit_bound=nodes)
        except Exception as error:
            raise _MeterRefusal(error) from None

    def retain(self, amount: int) -> None:
        try:
            self.meter.retain_digests(amount)
        except Exception as error:
            raise _MeterRefusal(error) from None

    def release(self, amount: int) -> None:
        self.meter.release_digests(amount)

    def charge(self, *, read: int = 0, write: int = 0, sha: int = 0, nodes: int = 0) -> None:
        try:
            self.meter.charge(
                bytes_read=read, bytes_written=write, sha_input_bytes=sha, node_visits=nodes
            )
        except Exception as error:
            raise _MeterRefusal(error) from None


def _synchronous(value: Any) -> Any:
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TrainingConsentEvidenceError()
    return value


def _call(callback: Any, *arguments: object, **keywords: object) -> Any:
    try:
        return _synchronous(callback(*arguments, **keywords))
    except Exception as error:
        raise _MeterRefusal(error) from None


class _Verifier(FilesystemTrainingConsentLedger):
    def __init__(self, original: FilesystemTrainingConsentLedger, work: _Work) -> None:
        # Bytes are immutable and shared; no key or operator provisioning is
        # cloned into the returned snapshot or a persistent second ledger.
        work.reserve(len(original._key), 1)
        work.charge(read=len(original._key), nodes=1)
        super().__init__(original.path, original._key, original.key_id)
        self._work = work

    def _mac(self, purpose: str, message: bytes) -> str:
        size, purpose_size = len(message), len(purpose)
        key_hash = len(self._key) if len(self._key) > 64 else 0
        # Includes caller message encoding, prefix/concatenation writes, pads,
        # digest/hex output and both SHA256 passes (plus a long key's hash).
        writes = 2 * size + 3 * purpose_size + 226
        reads = size + purpose_size + 161 + key_hash
        self._work.reserve(writes + reads, 16)
        self._work.charge(write=writes, sha=reads, nodes=16)
        return super()._mac(purpose, message)


def _file_version(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _table_prefix(table: str, rows: int) -> bytes:
    encoded = table.encode("ascii")
    return b"T" + _U32.pack(len(encoded)) + encoded + _I64.pack(rows)


def _bounded_json_depth(raw: str) -> None:
    # Old valid records are flat objects. This resource guard does not decode or
    # replace their exact field, time, ordering, HMAC or parent validation.
    quoted = escaped = False
    depth = 0
    for character in raw:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > 12:
                raise TrainingConsentEvidenceError()
        elif character in "]}":
            depth -= 1


class _Rows(Iterator[tuple[Any, ...]]):
    def __init__(
        self, cursor: sqlite3.Cursor, stream: NativeConsentStreamDescriptorV3, owner: _Reads
    ) -> None:
        self.cursor, self.stream, self.owner = cursor, stream, owner
        self.count = 0

    def __iter__(self) -> _Rows:
        return self

    def __next__(self) -> tuple[Any, ...]:
        try:
            row = cast(tuple[Any, ...], next(self.cursor))
        except StopIteration:
            if self.count != self.stream.rows:
                raise TrainingConsentEvidenceError() from None
            self.owner.complete.add(self.stream.table)
            raise
        self.count += 1
        if self.count > self.stream.rows:
            raise TrainingConsentEvidenceError()
        for value in row:
            if type(value) is int:
                self.owner.work.charge(write=17, sha=9, nodes=1)
                self.owner.emit(b"i" + _I64.pack(value))
            elif type(value) is str:
                # The table-wide admission has bounded every payload before the
                # first cursor is fetched. Encoding/framing/hash work is charged
                # for each actual retained column, including invalid records.
                self.owner.work.reserve(3 * len(value) * 4 + 10, 1)
                encoded = value.encode("utf-8")
                self.owner.work.charge(write=2 * len(encoded) + 10, sha=len(encoded) + 5, nodes=1)
                self.owner.emit(b"s" + _U32.pack(len(encoded)) + encoded)
            else:
                raise TrainingConsentEvidenceError()
        if self.stream.document_bytes:
            _bounded_json_depth(row[1])
        return row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self)


class _Reads:
    def __init__(
        self,
        connection: sqlite3.Connection,
        streams: tuple[NativeConsentStreamDescriptorV3, ...],
        work: _Work,
        sink: NativeConsentSnapshotSinkV3 | None = None,
    ) -> None:
        self.connection, self.work = connection, work
        self.streams = {value.table: value for value in streams}
        self.complete: set[str] = set()
        self.started: set[str] = set()
        work.charge(write=len(_PREFIX), sha=len(_PREFIX), nodes=1)
        self.digest = hashlib.sha256()
        self.framed = 0
        self.sink = sink
        self.emit(_PREFIX)
        self.queries = {
            "SELECT "
            + ",".join(columns)
            + " FROM "
            + table
            + (" ORDER BY sequence" if events else ""): table
            for table, columns, events in _TABLES
        }

    def emit(self, raw: bytes) -> None:
        if type(raw) is not bytes or len(raw) > 128 * 1024:
            raise TrainingConsentEvidenceError()
        self.digest.update(raw)
        self.framed += len(raw)
        if self.sink is not None and _call(self.sink.update_bytes, raw) is not None:
            raise TrainingConsentEvidenceError()

    def execute(self, query: str) -> _Rows:
        table = self.queries.get(" ".join(query.split()))
        if table is None or table in self.started:
            raise TrainingConsentEvidenceError()
        stream = self.streams[table]
        self.started.add(table)
        # This runs before sqlite3.execute/fetch allocates any Python payload.
        # JSON visits are conservatively bounded by bytes; no JSON is hydrated
        # in the SQL descriptor phase. All global owners remain in the scan.
        node_bound = 4 * (stream.document_bytes + 1) + 256 * stream.rows
        self.work.reserve(16 * stream.framed_bytes + 4096 * stream.rows, node_bound)
        self.work.charge(
            read=stream.utf8_bytes + 8 * stream.integer_fields,
            write=stream.document_bytes,
            nodes=node_bound,
        )
        prefix = _table_prefix(table, stream.rows)
        self.work.charge(write=3 * len(table) + 35, sha=len(prefix), nodes=1)
        self.emit(prefix)
        return _Rows(self.connection.execute(query), stream, self)


def _descriptor_streams(
    connection: sqlite3.Connection, work: _Work, extent: int
) -> tuple[NativeConsentStreamDescriptorV3, ...]:
    result: list[NativeConsentStreamDescriptorV3] = []
    total = len(_PREFIX)
    # Seven bounded schema records, plus each fixed aggregate scan. File/page
    # extent bounds every scan before SQLite reads documents to count UTF8.
    work.reserve(32 * extent + 8192, 16 * (extent + 1))
    for table, columns, events in _TABLES:
        # Include SQLite's bounded UTF8/BLOB casts and schema upper() copies,
        # not only the row bytes that eventually reach the Python verifier.
        work.charge(read=2 * extent, write=2 * extent, nodes=extent + 1)
        ordinary = connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name=? AND type='table' "
            "AND upper(sql) LIKE 'CREATE TABLE %' AND upper(sql) NOT LIKE '%VIRTUAL%'",
            (table,),
        ).fetchone()
        if ordinary != (1,):
            raise TrainingConsentEvidenceError()
        text_columns = tuple(name for name in columns if name != "sequence")
        utf8 = "+".join("coalesce(length(CAST(" + name + " AS BLOB)),0)" for name in text_columns)
        types = " OR ".join("typeof(" + name + ")<>'text'" for name in text_columns)
        if "sequence" in columns:
            types += " OR typeof(sequence)<>'integer' OR sequence<0 OR sequence>200000"
        invalid = "sum(CASE WHEN " + types + " THEN 1 ELSE 0 END)"
        constraints = []
        for name in text_columns:
            maximum = 8192 if name == "document" else 64
            constraints.append(
                "coalesce(max(length(CAST(" + name + " AS BLOB))),0)>" + str(maximum)
            )
        if events:
            constraints.append("coalesce(max(length(document)),0)>2048")
        query = (
            "SELECT count(*),coalesce(sum("
            + utf8
            + "),0),coalesce("
            + invalid
            + ",0),"
            + " OR ".join(constraints)
            + (
                ",coalesce(sum(length(CAST(document AS BLOB))),0),"
                "coalesce(max(length(CAST(document AS BLOB))),0)"
                if events
                else ",0,0"
            )
            + " FROM "
            + table
        )
        count, size, invalid_count, oversized, document_bytes, maximum = connection.execute(
            query
        ).fetchone()
        if invalid_count or oversized or (not events and count != 1) or count > 200_000:
            raise TrainingConsentEvidenceError()
        integers = count if "sequence" in columns else 0
        texts = count * len(text_columns)
        framed = 9 * integers + 5 * texts + size
        total += len(_table_prefix(table, count)) + framed
        if total > MAX_NATIVE_CONSENT_SNAPSHOT_BYTES_V3:
            raise TrainingConsentEvidenceError()
        result.append(
            NativeConsentStreamDescriptorV3(
                table, count, size, integers, texts, document_bytes, framed, maximum
            )
        )
    return tuple(result)


def _digest_bound(streams: tuple[NativeConsentStreamDescriptorV3, ...]) -> int:
    counts = {value.table: value.rows for value in streams}
    events, erasures, floors = (
        counts["event"],
        counts["sona_erasure_event"],
        counts["erasure_floor_event"],
    )
    maximum = max(value.maximum_document_bytes for value in streams)
    # Typed persistent fields: <=4N+3R+2F, descriptor:1. Raw SQL digest/signature
    # columns:7+N+R+F. One JSON map at a time: every decoded 64-character string
    # needs at least66 UTF8 wire bytes, including its quotes. Crypto512 covers
    # overlapping MAC/raw digest/hex temporaries. All seven global streams count.
    return 64 * (5 * events + 4 * erasures + 3 * floors + 8 + maximum // 66) + 512


def _persistent_digest_bytes(history: Any, erasures: Any, floors: Any) -> int:
    fields = sum(3 + (item.actor_tag is not None) for item in history.operations.values())
    fields += sum(2 + bool(item.feedback_parent_ledger_digest) for item in erasures)
    return 64 * (fields + 2 * len(floors) + 1)


class FilesystemTrainingConsentLedgerV3(FilesystemTrainingConsentLedger):
    """Opt-in source3 reader; old methods and signed-ledger validation stay unchanged."""

    _require_lifecycle_streams = True

    @contextmanager
    def _tag_v3(
        self, identity: UUID, *, meter: NativeOriginalCaptureV3DigestMeter, actor: bool
    ) -> Iterator[str]:
        work = _Work(meter)
        owned = 0
        tag = ""
        error: BaseException | None = None
        try:
            work.retain(576)
            owned = 576
            verifier = _Verifier(self, work)
            tag = verifier.actor_tag(identity) if actor else verifier.owner_tag(identity)
            work.release(512)
            owned = 64
            yield tag
        except _MeterRefusal as refusal:
            error = refusal.cause
        except BaseException as caught:
            error = caught
        finally:
            tag = ""
            if error is not None:
                error.__traceback__ = error.__context__ = error.__cause__ = None
            if owned:
                work.release(owned)
        if error is not None:
            raise error

    def owner_tag_v3(
        self, user_id: UUID, *, meter: NativeOriginalCaptureV3DigestMeter
    ) -> AbstractContextManager[str]:
        return self._tag_v3(user_id, meter=meter, actor=False)

    def actor_tag_v3(
        self, device_id: UUID, *, meter: NativeOriginalCaptureV3DigestMeter
    ) -> AbstractContextManager[str]:
        return self._tag_v3(device_id, meter=meter, actor=True)

    def read_snapshot_v3(
        self,
        *,
        meter: NativeOriginalCaptureV3DigestMeter,
        expected: NativeConsentLedgerDescriptorV3 | None = None,
        admit_snapshot: NativeConsentSnapshotAdmitterV3 | None = None,
    ) -> NativeTrainingConsentSnapshotV3:
        """Read one global snapshot; a missing hook is a read-only audit only.

        The hook receives actual aggregate bytes under the same BEGIN IMMEDIATE,
        before any payload SELECT. Both independent stream hashes consume the
        caller's existing allowance. Consumers close the returned digest owner.
        """
        work = _Work(meter)
        owned = 0
        sink: NativeConsentSnapshotSinkV3 | None = None
        snapshot: NativeTrainingConsentSnapshotV3 | None = None
        error: BaseException | None = None
        history: TrainingConsentHistory | None = None
        erasures: tuple[SonaEventErasureIntent, ...] | None = None
        floors: tuple[CompactedErasureFloor, ...] | None = None
        reads: _Reads | None = None
        verifier: _Verifier | None = None
        descriptor: NativeConsentLedgerDescriptorV3 | None = None
        raw_digest: bytes | None = None
        external: Any = None
        proxy: sqlite3.Connection | None = None
        try:
            if self.path.is_symlink() or any(
                self.path.with_name(self.path.name + suffix).exists()
                for suffix in ("-wal", "-shm", "-journal")
            ):
                raise TrainingConsentEvidenceError()
            before = self.path.stat()
            if not 1 <= before.st_size <= MAX_NATIVE_CONSENT_SNAPSHOT_BYTES_V3:
                raise TrainingConsentEvidenceError()
            work.reserve(32 * before.st_size + 65_536, 16 * (before.st_size + 1))
            work.charge(read=before.st_size, nodes=16)
            with self._connection() as connection:
                if _file_version(self.path.stat()) != _file_version(before):
                    raise TrainingConsentEvidenceError()
                page_count = connection.execute("PRAGMA page_count").fetchone()[0]
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
                extent = page_count * page_size
                if not 1 <= extent <= MAX_NATIVE_CONSENT_SNAPSHOT_BYTES_V3:
                    raise TrainingConsentEvidenceError()
                work.reserve(32 * extent + 65_536, 16 * (extent + 1))
                work.charge(read=extent, nodes=16)
                if expected is not None and (
                    _file_version(before)
                    != (
                        expected.device,
                        expected.inode,
                        expected.file_size,
                        expected.modified_ns,
                        expected.changed_ns,
                    )
                    or (page_count, page_size, schema_version)
                    != (expected.page_count, expected.page_size, expected.schema_version)
                ):
                    raise TrainingConsentEvidenceError()
                streams = _descriptor_streams(connection, work, extent)
                exact_bytes = len(_PREFIX) + sum(
                    len(_table_prefix(value.table, value.rows)) + value.framed_bytes
                    for value in streams
                )
                prelude = NativeConsentSnapshotPreludeV3(
                    exact_bytes,
                    streams,
                    (*_file_version(before), page_count, page_size, schema_version),
                )
                if admit_snapshot is not None:
                    sink = _call(admit_snapshot, prelude)
                    if sink is None or any(
                        not callable(getattr(sink, name, None))
                        for name in ("update_bytes", "finish_stream", "abort")
                    ):
                        raise TrainingConsentEvidenceError()
                wanted = _digest_bound(streams)
                work.retain(wanted)
                owned = wanted
                reads = _Reads(connection, streams, work, sink)
                verifier = _Verifier(self, work)
                proxy = cast(sqlite3.Connection, reads)
                history, _, _ = verifier._read(proxy)
                erasures, _, _ = verifier._read_erasure(proxy)
                floors, _, _ = verifier._read_floor(proxy)
                if reads.complete != set(reads.streams) or reads.framed != exact_bytes:
                    raise TrainingConsentEvidenceError()
                if _file_version(self.path.stat()) != _file_version(before):
                    raise TrainingConsentEvidenceError()
                work.charge(write=96, nodes=1)
                raw_digest = reads.digest.digest()
                if sink is not None:
                    external = _call(sink.finish_stream)
                    if (
                        type(external) is not bytes
                        or len(external) != 32
                        or not hmac.compare_digest(raw_digest, external)
                    ):
                        raise TrainingConsentEvidenceError()
                descriptor = NativeConsentLedgerDescriptorV3(
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                    page_count,
                    page_size,
                    schema_version,
                    reads.framed,
                    raw_digest.hex(),
                    streams,
                )
                if expected is not None and descriptor != expected:
                    raise TrainingConsentEvidenceError()
                if _file_version(self.path.stat()) != _file_version(before):
                    raise TrainingConsentEvidenceError()
                persistent = _persistent_digest_bytes(history, erasures, floors)
                snapshot = _call(
                    NativeTrainingConsentSnapshotV3._from_verified,
                    history,
                    erasures,
                    floors,
                    descriptor,
                    meter=meter,
                    retained=persistent,
                )
                work.release(owned - persistent)
                owned = 0
        except _MeterRefusal as refusal:
            error = refusal.cause
        except OSError, sqlite3.Error, ValueError, TypeError, OverflowError, AttributeError:
            error = TrainingConsentEvidenceError()
        except BaseException as caught:
            error = caught
        finally:
            if error is not None and sink is not None:
                try:
                    if _call(sink.abort) is not None:
                        raise TrainingConsentEvidenceError()
                except BaseException as cleanup_error:
                    cleanup_error.__traceback__ = cleanup_error.__context__ = None
            # Drop decoded/crypto references before releasing their payloads.
            history = erasures = floors = reads = verifier = descriptor = sink = None
            raw_digest = external = proxy = _ = None
            if error is not None:
                error.__traceback__ = error.__context__ = error.__cause__ = None
            if owned:
                work.release(owned)
        if error is not None:
            if snapshot is not None:
                snapshot.close()
            raise error
        assert snapshot is not None
        return snapshot
