"""Publish verified acquisition receipts through AutPlay's existing import/Vault services.

Privileged local operator utility, with no listener or bearer credentials. PostgreSQL owns
imports, upload offsets, ingest jobs and library entries; the private JSON files only cache
their identifiers. Run one instance with a read-only music mount and the normal Vault mount.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import signal
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5


class ReceiptError(ValueError):
    """A completed receipt cannot safely be published."""


@dataclass(frozen=True)
class Receipt:
    path: Path
    audio: Path
    identity: str
    sha256: str
    byte_size: int
    payload: bytes
    signature: tuple[int, int]
    completed_at_ns: int = 0


def read_receipt(path: Path, root: Path) -> Receipt:
    """Accept only complete, bounded receipts and regular files inside an explicit root."""
    if path.is_symlink() or path.stat().st_size > 32_768:
        raise ReceiptError("receipt_invalid")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(doc, dict)
        or doc.get("schema_version") != 1
        or doc.get("key") != path.parent.name
    ):
        raise ReceiptError("receipt_schema_invalid")
    for field in ("provider", "filename"):
        value = doc.get(field)
        if not isinstance(value, str) or not value or Path(value).name != value:
            raise ReceiptError("receipt_path_invalid")
        if value in {".", ".."} or "\\" in value or "/" in value:
            raise ReceiptError("receipt_path_invalid")
    audio = path.parent / doc["provider"] / doc["filename"]
    if any(p.is_symlink() for p in (path.parent, audio.parent, audio)):
        raise ReceiptError("receipt_symlink")
    if not audio.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
        raise ReceiptError("receipt_path_invalid")
    stat = audio.stat()
    size, digest = doc.get("bytes"), doc.get("sha256")
    if not isinstance(size, int) or not 0 < size <= 2 * 1024**3 or stat.st_size != size:
        raise ReceiptError("receipt_size_invalid")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ReceiptError("receipt_hash_invalid")
    if any(c not in "0123456789abcdef" for c in digest):
        raise ReceiptError("receipt_hash_invalid")
    item = doc.get("item", {})
    if not isinstance(item, dict):
        raise ReceiptError("receipt_metadata_invalid")
    fields = {key: item.get(key) for key in ("title", "artist", "album")}
    for key, value in fields.items():
        if (value is None and key != "album") or (
            value is not None and (not isinstance(value, str) or len(value) > 1_000)
        ):
            raise ReceiptError("receipt_metadata_invalid")
    if not fields["title"].strip() or not fields["artist"].strip():
        raise ReceiptError("receipt_metadata_invalid")
    seconds = doc.get("duration_seconds")
    if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise ReceiptError("receipt_duration_invalid")
    identity = hashlib.sha256(
        json.dumps([digest, fields], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    # Exact bytes and exact declared metadata are replay identity, never a fuzzy match.
    payload = json.dumps(
        [
            {
                **fields,
                "duration_ms": round(seconds * 1000),
                "external_id": "acquisition:" + identity,
                "provider": "verified-local-acquisition",
            }
        ],
        ensure_ascii=False,
        sort_keys=True,
    ).encode()
    return Receipt(
        path,
        audio,
        identity,
        digest,
        size,
        payload,
        (stat.st_size, stat.st_mtime_ns),
        path.stat().st_mtime_ns,
    )


def verify_audio(receipt: Receipt) -> None:
    with receipt.audio.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != receipt.sha256:
            raise ReceiptError("receipt_integrity_mismatch")
    stat = receipt.audio.stat()
    if (stat.st_size, stat.st_mtime_ns) != receipt.signature:
        raise ReceiptError("receipt_changed_during_validation")


def save(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class Backend:
    """Reuse owner-scoped application commands, fenced ingest and sync publication."""

    def __init__(self, owner: UUID, *, provision: bool = False) -> None:
        from autplay.application.imports import ImportService
        from autplay.domain.resource_admission import LocalBridgeClaim
        from autplay.entrypoints.composition import build_vault_http_service
        from autplay.entrypoints.resource_composition import ResourceIoRuntime
        from autplay.runtime.settings import load_api_settings
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        settings = load_api_settings()
        self.engine = create_engine(
            settings.database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=8,
            max_overflow=0,
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.owner = owner
        self.device = uuid5(owner, "autplay-acquisition-vault-bridge-v1")
        self.imports = ImportService(self.sessions)
        self.vault = build_vault_http_service(settings, self.engine)
        self.principal = self.authorize(provision=provision)
        self.bridge = LocalBridgeClaim(self.owner, self.device)
        self.resource = ResourceIoRuntime(settings, maximum=16)
        self.resource.start()

    def close(self) -> None:
        pending = asyncio.run(self.resource.shutdown())
        if pending:
            raise ReceiptError("resource_execution_unconfirmed")
        self.engine.dispose()

    def authorize(self, *, provision: bool = False) -> Any:
        from autplay.adapters.postgresql.models import AuditEventRow, DeviceRow, UserAccountRow
        from autplay.domain.auth import AccountRole, Principal

        with self.sessions() as session:
            user = session.get(UserAccountRow, self.owner)
            if user is None or user.deleted_at or user.status != "ACTIVE" or user.role != "OWNER":
                raise ReceiptError("owner_not_active")
            device = session.get(DeviceRow, self.device)
            if device is None and provision:
                device = DeviceRow(
                    device_id=self.device,
                    user_id=self.owner,
                    device_name="Automatic Vault Importer",
                    platform="OTHER",
                    app_version="acquisition-bridge-1",
                )
                session.add(device)
                session.flush()
                session.add(
                    AuditEventRow(
                        actor_type="ADMIN",
                        actor_user_id=self.owner,
                        action="acquisition_bridge.enabled",
                        target_type="device",
                        target_id=self.device,
                        reason_code="OWNER_AUTHORIZED_COMPLETED_DOWNLOAD_IMPORT",
                        metadata_sanitized={"schema_version": 1},
                    )
                )
                session.commit()
            if device is None or device.user_id != self.owner or device.revoked_at:
                raise ReceiptError("bridge_device_not_active")
        # Local operator authority, never a fabricated network token or login session.
        return Principal(self.owner, self.device, UUID(int=0), AccountRole.OWNER)

    def advance(self, receipt: Receipt, checkpoint: dict[str, Any]) -> dict[str, Any]:
        from autplay.adapters.postgresql.library_runtime import LibraryRepository
        from autplay.adapters.postgresql.models import ImportEntryRow, LibraryEntryRow
        from sqlalchemy import select

        state = dict(checkpoint)
        if "import_id" not in state:
            verify_audio(receipt)
            result = self.imports.start(
                self.principal,
                payload=receipt.payload,
                format_name="JSON",
                schema_version="1",
                mode="LIBRARY_ONLY",
            )
            return {**state, "import_id": str(result.import_job_id), "state": "IMPORTING"}
        import_id = UUID(state["import_id"])
        if "recording_id" not in state:
            report = self.imports.report(self.principal, import_id)
            if report.state in {"FAILED", "CANCELLED", "DEAD_LETTER"}:
                raise ReceiptError("import_failed")
            if not report.entries or not report.entries[0].decision_id:
                return state
            entry = report.entries[0]
            with self.sessions() as session:
                stored = session.get(ImportEntryRow, entry.import_entry_id)
                recording = stored.selected_recording_id
            if recording is None:
                result = self.imports.review(
                    self.principal,
                    import_id,
                    entry.import_entry_id,
                    predecessor_decision_id=entry.decision_id,
                    action="CREATE_RECORDING",
                    selected_rank=None,
                    idempotency_key="acquisition:" + receipt.identity,
                )
                recording = result.recording_id
            with self.sessions() as session:
                stored = session.get(ImportEntryRow, entry.import_entry_id)
                ref_id = stored.user_track_ref_id
                library_id = LibraryRepository(session).add_library_entry(
                    self.principal,
                    library_entry_id=uuid5(self.owner, receipt.identity),
                    user_track_ref_id=ref_id,
                    source="IMPORT",
                    availability_status="PENDING",
                    now=datetime.now(UTC),
                )
                session.commit()
            return {
                **state,
                "recording_id": str(recording),
                "ref_id": str(ref_id),
                "library_id": str(library_id),
                "state": "UPLOADING",
            }
        if "upload_id" not in state:
            upload, _ = self.vault.create(
                self.principal,
                recording_id=UUID(state["recording_id"]),
                expected_size=receipt.byte_size,
                declared_sha256=receipt.sha256,
                idempotency_key="acquisition:" + receipt.identity,
            )
            return {**state, "upload_id": str(upload.upload_id)}
        upload_id = UUID(state["upload_id"])
        upload = self.vault.status(self.principal, upload_id)
        if upload.state == "OPEN":
            verify_audio(receipt)
            offset = upload.offset
            with receipt.audio.open("rb") as handle:
                handle.seek(offset)
                # Bounded work keeps newly completed tracks responsive during backfill.
                for _ in range(32):
                    payload = handle.read(1024 * 1024)
                    if not payload:
                        break
                    offset = asyncio.run(
                        self._append_chunk(
                            upload_id,
                            offset=offset,
                            chunk_index=offset // (1024 * 1024),
                            payload=payload,
                        )
                    )
            if offset == receipt.byte_size:
                self.vault.complete(self.principal, upload_id)
                state["state"] = "INGESTING"
            return state
        if upload.state in {"SEALED", "PROCESSING", "COMMIT_PREPARED"}:
            return {**state, "state": "INGESTING"}
        if upload.state not in {"COMMITTED", "REUSED"}:
            raise ReceiptError("upload_" + upload.state.lower())
        from autplay.adapters.postgresql.models import (
            RecordingCanonicalVariantRow,
            SyncEventRow,
            UploadSessionRow,
        )
        from autplay.application.sync import CatalogArtistSyncPublisher
        from sqlalchemy.dialects.postgresql import insert

        with self.sessions() as session:
            committed = session.get(UploadSessionRow, upload_id)
            if (
                committed.user_id != self.owner
                or committed.device_id != self.device
                or committed.target_recording_id != UUID(state["recording_id"])
                or committed.state not in {"COMMITTED", "REUSED"}
                or committed.computed_sha256.hex() != receipt.sha256
            ):
                raise ReceiptError("committed_upload_mismatch")
            # Only the verified result of this owned upload can supply a new selection.
            # Existing canonical choices are preserved; DB enforces recording identity.
            session.execute(
                insert(RecordingCanonicalVariantRow)
                .values(
                    recording_id=committed.target_recording_id,
                    audio_variant_id=committed.audio_variant_id,
                    policy_version="acquisition-verified-upload-v1",
                    reason={"upload_session_id": str(upload_id)},
                )
                .on_conflict_do_nothing(index_elements=[RecordingCanonicalVariantRow.recording_id])
            )
            session.commit()
        variant = self.vault.resolve_playback_variant(self.principal, UUID(state["ref_id"]))
        with self.sessions() as session:
            # Match SyncService's lock order: owner publication fence before library row.
            CatalogArtistSyncPublisher().publish(
                session, self.owner, ref_ids=(UUID(state["ref_id"]),)
            )
            row = session.scalar(
                select(LibraryEntryRow)
                .where(
                    LibraryEntryRow.library_entry_id == UUID(state["library_id"]),
                    LibraryEntryRow.user_id == self.owner,
                )
                .with_for_update()
            )
            if row is None or row.removed_at or row.user_track_ref_id != UUID(state["ref_id"]):
                raise ReceiptError("library_entry_removed")
            if row.availability_status != "VAULT":
                row.availability_status = "VAULT"
                row.row_version += 1
                row.updated_at = datetime.now(UTC)
            session.execute(
                insert(SyncEventRow)
                .values(
                    event_id=uuid5(
                        NAMESPACE_URL, f"acquisition-library:{self.owner}:{receipt.identity}"
                    ),
                    user_id=self.owner,
                    origin_device_id=None,
                    event_type="LIBRARY_ENTRY_UPSERTED",
                    schema_version=1,
                    aggregate_type="LIBRARY_ENTRY",
                    aggregate_id=row.library_entry_id,
                    payload={
                        "server_user_track_ref_id": str(row.user_track_ref_id),
                        "source": row.source,
                        "availability_status": "VAULT",
                    },
                    operation="UPSERT",
                    server_row_version=row.row_version,
                )
                .on_conflict_do_nothing(index_elements=[SyncEventRow.event_id])
            )
            session.commit()
        return {
            **state,
            "state": "PUBLISHED",
            "variant_id": str(variant),
            "published_at": time.time(),
        }

    async def _append_chunk(
        self,
        upload_id: UUID,
        *,
        offset: int,
        chunk_index: int,
        payload: bytes,
    ) -> int:
        from autplay.domain.resource_admission import (
            AdmissionState,
            ResourceAdmissionError,
            ResourceKind,
            ResourceRequest,
        )
        from autplay.entrypoints.vault_http import AdmittedChunk

        operation_id = uuid5(
            NAMESPACE_URL,
            f"autplay:acquisition-bridge-upload:v1:{upload_id}:{chunk_index}",
        )
        request = ResourceRequest(
            operation_id,
            ResourceKind.TRANSFER,
            "UPLOAD_INTENT",
            upload_id,
            upload_id,
        )
        status = self.resource.service.acquire_bridge(self.bridge, request)
        if status.operation.state != AdmissionState.ACTIVE or status.operation.fence is None:
            raise ResourceAdmissionError("resource_execution_busy")
        fence = status.operation.fence
        io = await self.resource.coordinator.open_bridge(
            self.bridge,
            fence,
            target_id=upload_id,
        )
        try:
            digest = hashlib.sha256(payload).hexdigest()
            command = AdmittedChunk(
                self.principal,
                upload_id,
                offset,
                chunk_index,
                payload,
                digest,
            )
            return await io.perform(lambda: self.vault.append_admitted(command, io))
        finally:
            try:
                await io.close()
            finally:
                self.resource.service.release(self.bridge, fence)


def select_work(receipts: dict[str, Receipt], checkpoints: dict[str, Any], limit: int) -> list[str]:
    """New completions preempt backfill without cancelling its durable operations."""
    pending = (
        r
        for key, r in receipts.items()
        if checkpoints.get(key, {}).get("state") not in {"PUBLISHED", "NEEDS_REVIEW"}
    )
    return [
        r.identity
        for r in sorted(
            pending, key=lambda r: r.completed_at_ns or r.path.stat().st_mtime_ns, reverse=True
        )[:limit]
    ]


def advance_checkpoint(
    backend: Backend, receipt: Receipt, previous: dict[str, Any]
) -> dict[str, Any]:
    """Advance an isolated identity; retry state never mutates another receipt."""
    if previous.get("retry_at", 0) > time.time():
        return previous
    try:
        if previous["signature"] != list(receipt.signature):
            raise ReceiptError("source_changed")
        updated = previous
        for _ in range(6):
            next_state = backend.advance(receipt, updated)
            if next_state == updated or next_state["state"] == "PUBLISHED":
                updated = next_state
                break
            updated = next_state
        updated.pop("error", None)
        updated.pop("retry_at", None)
        updated.pop("failures", None)
        return updated
    except ReceiptError as error:
        return {**previous, "state": "NEEDS_REVIEW", "error": str(error)}
    except Exception as error:
        attempts = previous.get("failures", 0) + 1
        return {
            **previous,
            "state": "NEEDS_REVIEW" if attempts >= 5 else previous["state"],
            "failures": attempts,
            "error": type(error).__name__,
            "retry_at": time.time() + min(300, 10 * 2 ** min(attempts, 5)),
        }


def completed_work(in_flight: dict[str, Future[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Drain ready identities without waiting for an unrelated slow upload."""
    ready = {key: future.result() for key, future in in_flight.items() if future.done()}
    for key in ready:
        del in_flight[key]
    return ready


def main() -> int:
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--owner", type=UUID, required=True)
    parser.add_argument("--provision-device", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-active", type=int, default=16, choices=range(1, 65))
    args = parser.parse_args()
    os.umask(0o077)
    args.state.mkdir(parents=True, exist_ok=True)
    lock = (args.state / "bridge.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    backend = Backend(args.owner, provision=args.provision_device)
    # Each command owns its SQLAlchemy session; at most eight independent identities
    # use I/O concurrently. PostgreSQL still owns leases, replay and publish ordering.
    executor = ThreadPoolExecutor(max_workers=8)
    stopping = False

    def stop(_signal: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    checkpoints = {
        p.stem: json.loads(p.read_text()) for p in args.state.glob("*.json") if p.stem != "status"
    }
    receipt_cache: dict[Path, tuple[tuple[int, int], Receipt]] = {}
    receipts: dict[str, Receipt] = {}
    in_flight: dict[str, Future[dict[str, Any]]] = {}
    ready_at: dict[str, float] = {}
    once_submitted: set[str] = set()
    once_keys: list[str] | None = None
    next_scan = next_summary = scan_seconds = 0.0
    invalid = 0
    while not stopping or in_flight:
        now = time.monotonic()
        for key, updated in completed_work(in_flight).items():
            # Persist each result immediately. A slow sibling cannot delay playback
            # publication, checkpoint durability, or an already finished import.
            unchanged = checkpoints[key] == updated
            checkpoints[key] = updated
            save(args.state / (key + ".json"), updated)
            ready_at[key] = now + (1.0 if unchanged else 0.0)
        if not stopping and now >= next_scan:
            scan_started = time.monotonic()
            backend.authorize()
            receipts = {}
            invalid = 0
            for root in args.root:
                for path in root.glob("tracks/*/receipt.json"):
                    try:
                        stat = path.stat()
                        signature = (stat.st_size, stat.st_mtime_ns)
                        cached = receipt_cache.get(path)
                        if cached is not None and cached[0] == signature:
                            receipt = cached[1]
                        else:
                            receipt = read_receipt(path, root)
                            receipt_cache[path] = (signature, receipt)
                        receipts.setdefault(receipt.identity, receipt)
                    except OSError, ValueError, KeyError, TypeError:
                        invalid += 1
            scan_seconds = time.monotonic() - scan_started
            next_scan = time.monotonic() + 2.0
        active = select_work(receipts, checkpoints, args.max_active)
        if args.once:
            if once_keys is None:
                once_keys = active
            active = [key for key in once_keys if key not in once_submitted]
        for key in active:
            if stopping or len(in_flight) >= 8:
                break
            if key in in_flight or ready_at.get(key, 0) > now:
                continue
            receipt = receipts[key]
            if key not in checkpoints:
                checkpoints[key] = {
                    "state": "DISCOVERED",
                    "sha256": receipt.sha256,
                    "signature": list(receipt.signature),
                    "discovered_at": time.time(),
                }
            if checkpoints[key].get("retry_at", 0) > time.time():
                if args.once:
                    once_submitted.add(key)
                continue
            in_flight[key] = executor.submit(advance_checkpoint, backend, receipt, checkpoints[key])
            if args.once:
                once_submitted.add(key)
        if args.once and once_keys is not None and set(once_keys) <= once_submitted:
            stopping = True
        if now >= next_summary or stopping:
            summary = {
                "observed_at": time.time(),
                "receipts": len(receipts),
                "invalid_receipts": invalid,
                "waiting": sum(key not in checkpoints for key in receipts),
                "states": dict(Counter(v["state"] for v in checkpoints.values())),
                "scan_seconds": round(scan_seconds, 3),
                "in_flight": len(in_flight),
            }
            save(args.state / "status.json", summary)
            print(json.dumps(summary), flush=True)
            next_summary = now + 2.0
        if in_flight:
            wait(tuple(in_flight.values()), timeout=0.25, return_when=FIRST_COMPLETED)
        elif not stopping:
            time.sleep(0.25)
    executor.shutdown(wait=True)
    backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
