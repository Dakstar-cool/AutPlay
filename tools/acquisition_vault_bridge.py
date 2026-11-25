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
import sys
import tempfile
import threading
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
    source_metadata: dict[str, object] | None = None
    policy_reasons: tuple[str, ...] = ()

    @property
    def metadata_sha256(self) -> str:
        payload = json.dumps(
            self.source_metadata or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def metadata_evidence(raw: object, provider: str) -> dict[str, object] | None:
    """Use the application contract; malformed optional metadata never rejects audio."""
    if raw is None:
        return None
    from autplay.domain.track_metadata import sanitize_source_metadata, source_metadata_document

    try:
        source = sanitize_source_metadata(raw)
        content_provider = {
            "jamendo": "JAMENDO",
            "yandex": "YANDEX",
            "bandcamp": "BANDCAMP",
            "soundcloud": "SOUNDCLOUD",
            "yt_dlp": "YOUTUBE",
            "hitmo": "HITMO",
        }.get(provider, provider.upper())
        if source.provider != content_provider:
            return None
        return source_metadata_document(source)
    except ValueError, TypeError, UnicodeError:
        return None


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
    title, artist = fields["title"], fields["artist"]
    if (
        not isinstance(title, str)
        or not isinstance(artist, str)
        or not title.strip()
        or not artist.strip()
    ):
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
    from autplay.domain.acquisition_policy import track_review_reasons

    source = metadata_evidence(doc.get("source_metadata"), doc["provider"])
    native_fields = source.get("fields") if source else None
    labels = [title, fields["album"]]
    if isinstance(native_fields, dict):
        labels.extend([native_fields.get("title"), native_fields.get("album")])
    reasons = track_review_reasons(seconds, *labels)
    if doc["provider"] == "yt_dlp" and (
        not isinstance(native_fields, dict)
        or not all(native_fields.get(key) for key in ("title", "artist", "album"))
    ):
        reasons += ("unverified_youtube_recording",)
    return Receipt(
        path,
        audio,
        identity,
        digest,
        size,
        payload,
        (stat.st_size, stat.st_mtime_ns),
        path.stat().st_mtime_ns,
        source,
        reasons,
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
    if sys.platform == "win32":
        return  # Directory fsync is unavailable; the file and atomic replace are durable.
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
        self.playlist_downloads_enabled = settings.internet_music_enabled
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

    def schedule_metadata(self, receipt: Receipt, checkpoint: dict[str, Any]) -> None:
        """A separate transaction schedules existing deferred work without provider I/O."""
        from autplay.application.track_metadata import TrackMetadataService

        operation_id = uuid5(
            NAMESPACE_URL,
            f"acquisition-metadata-v1:{self.owner}:{receipt.identity}:{receipt.metadata_sha256}",
        )
        TrackMetadataService(self.sessions).accept_acquisition(
            self.principal,
            UUID(checkpoint["ref_id"]),
            operation_id=operation_id,
            evidence=receipt.source_metadata or {},
        )

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

    def advance(self, receipt: Receipt, checkpoint: dict[str, Any], *, preservation: bool = False) -> dict[str, Any]:
        from autplay.adapters.postgresql.library_runtime import LibraryRepository
        from autplay.adapters.postgresql.models import ImportEntryRow, LibraryEntryRow, UserTrackRefRow
        from autplay.application.sync import _acquire_sync_owner_publish_lock
        from autplay.application.vault_names import project_owner_names
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
            predecessor = entry.decision_id
            if predecessor is None:
                return state
            with self.sessions() as session:
                stored = session.get(ImportEntryRow, entry.import_entry_id)
                if stored is None:
                    raise ReceiptError("import_entry_missing")
                recording = stored.selected_recording_id
            if recording is None:
                reviewed = self.imports.review(
                    self.principal,
                    import_id,
                    entry.import_entry_id,
                    predecessor_decision_id=predecessor,
                    action="CREATE_RECORDING",
                    selected_rank=None,
                    idempotency_key="acquisition:" + receipt.identity,
                )
                recording = reviewed.recording_id
            if recording is None:
                raise ReceiptError("import_recording_missing")
            with self.sessions() as session:
                stored = session.get(ImportEntryRow, entry.import_entry_id)
                if stored is None or stored.user_track_ref_id is None:
                    raise ReceiptError("import_ref_missing")
                ref_id = stored.user_track_ref_id
                _acquire_sync_owner_publish_lock(session, self.owner)
                ref = session.get(UserTrackRefRow, ref_id, with_for_update=True)
                if ref is None or ref.user_id != self.owner or ref.recording_id != recording:
                    raise ReceiptError("import_ref_missing")
                project_owner_names(session, ref, recording)
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
                committed is None
                or committed.user_id != self.owner
                or committed.device_id != self.device
                or committed.target_recording_id != UUID(state["recording_id"])
                or committed.state not in {"COMMITTED", "REUSED"}
                or committed.computed_sha256 is None
                or committed.computed_sha256.hex() != receipt.sha256
                or committed.audio_variant_id is None
            ):
                raise ReceiptError("committed_upload_mismatch")
            # Only the verified result of this owned upload can supply a new selection.
            # Existing canonical choices are preserved; DB enforces recording identity.
            session.execute(
                insert(RecordingCanonicalVariantRow)
                .values(
                    recording_id=committed.target_recording_id,
                    audio_variant_id=committed.audio_variant_id,
                    policy_version="preservation-verified-upload-v1" if preservation else "acquisition-verified-upload-v1",
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
            ref = session.get(UserTrackRefRow, row.user_track_ref_id, with_for_update=True)
            if ref is None or ref.user_id != self.owner or ref.recording_id != UUID(state["recording_id"]):
                raise ReceiptError("import_ref_missing")
            project_owner_names(session, ref, ref.recording_id)
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
        from autplay.entrypoints.composition import _DirectVaultHttpService
        from autplay.entrypoints.vault_http import AdmittedChunk

        vault = self.vault
        if isinstance(vault, _DirectVaultHttpService):
            raise ReceiptError("bridge_requires_admitted_vault")

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
            return await io.perform(lambda: vault.append_admitted(command, io))
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
        if checkpoints.get(key, {}).get("state") != "NEEDS_REVIEW"
        and (
            checkpoints.get(key, {}).get("state") != "PUBLISHED"
            or (
                metadata_pending(r, checkpoints[key])
                and (
                    checkpoints[key].get("metadata_sha256") != r.metadata_sha256
                    or checkpoints[key].get("metadata_retry_at", 0) <= time.time()
                )
            )
        )
    )
    return [
        r.identity
        for r in sorted(
            pending,
            key=lambda r: (
                checkpoints.get(r.identity, {}).get("state") == "PUBLISHED",
                -(r.completed_at_ns or r.path.stat().st_mtime_ns),
            ),
        )[:limit]
    ]


def metadata_pending(receipt: Receipt, checkpoint: dict[str, Any]) -> bool:
    if checkpoint.get("metadata_sha256") != receipt.metadata_sha256:
        return True
    return checkpoint.get("metadata_state") not in {"SCHEDULED", "FAILED"}


def advance_metadata_checkpoint(
    backend: Backend, receipt: Receipt, previous: dict[str, Any]
) -> dict[str, Any]:
    """Only metadata retries change here; published audio is a permanent success."""
    digest = receipt.metadata_sha256
    if not metadata_pending(receipt, previous):
        return previous
    changed = previous.get("metadata_sha256") != digest
    state = {**previous, "metadata_sha256": digest, "metadata_state": "PENDING"}
    if changed:
        for key in ("metadata_retry_at", "metadata_failures", "metadata_error"):
            state.pop(key, None)
    if state.get("metadata_retry_at", 0) > time.time():
        return state
    try:
        if state["signature"] != list(receipt.signature):
            raise ReceiptError("source_changed")
        backend.schedule_metadata(receipt, state)
    except Exception as error:
        attempts = state.get("metadata_failures", 0) + 1
        return {
            **state,
            "metadata_state": "FAILED" if attempts >= 5 else "PENDING",
            "metadata_failures": attempts,
            "metadata_error": str(error)
            if isinstance(error, ReceiptError)
            else type(error).__name__,
            "metadata_retry_at": time.time() + min(300, 10 * 2 ** min(attempts, 5)),
        }
    for key in ("metadata_retry_at", "metadata_failures", "metadata_error"):
        state.pop(key, None)
    return {**state, "metadata_state": "SCHEDULED", "metadata_scheduled_at": time.time()}


def advance_checkpoint(
    backend: Backend, receipt: Receipt, previous: dict[str, Any]
) -> dict[str, Any]:
    """Advance an isolated identity; retry state never mutates another receipt."""
    if previous.get("state") == "PUBLISHED":
        return advance_metadata_checkpoint(backend, receipt, previous)
    if receipt.policy_reasons:
        return {**previous, "state": "NEEDS_REVIEW", "error": receipt.policy_reasons[0]}
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
        if updated["state"] == "PUBLISHED":
            return advance_metadata_checkpoint(backend, receipt, updated)
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


class PlaylistCatalogPublisher:
    """Use one background lane and the operator's ordinary verified import path."""

    def __init__(self, backend: Backend, root: Path, stop: threading.Event):
        from autplay.adapters.internet_music import InternetMusicProvider

        self.backend, self.root, self.stop = backend, root, stop
        self.provider = InternetMusicProvider()

    def search(self, query):
        self.backend.authorize()
        return self.provider.search(query)

    def acquire(self, candidate, context, *, supplemental, original_guard=None):
        from autplay.adapters.internet_music import InternetMusicProviderError
        from autplay.application.playlist_vault import catalog_match, in_vault
        from autplay.adapters.postgresql.shared_catalog import publish_bridge_catalog, PUBLISHED_AUDIO
        from autplay.domain.import_identity import normalize_text
        from autplay.domain.jobs import RetryableJobError, TerminalJobError
        from autplay.domain.resource_admission import ResourceAdmissionError
        from autplay.domain.vault import VaultError
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        artist, title = candidate["artist"], candidate["title"]
        identity = json.dumps([normalize_text(artist), normalize_text(title)], ensure_ascii=False)
        lock_key = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], signed=True)
        # A session lock also serializes publishers using different staging directories.
        with self.backend.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
            if not lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}):
                raise RetryableJobError("playlist_vault_busy")
            try:
                if original_guard:
                    original_guard()
                else:
                    with self.backend.sessions() as session:
                        found = catalog_match(session, artist, title, candidate["duration_ms"])
                        if found or supplemental and in_vault(session, artist, title):
                            return found
                self.backend.authorize()
                cache_identity = candidate["candidate_id"]
                if original_guard:
                    cache_identity += ":" + candidate["sha256"] + ":" + candidate["version"]
                key = hashlib.sha256(cache_identity.encode()).hexdigest()
                self.root.mkdir(parents=True, exist_ok=True)
                work = self.root / key
                if not work.exists():
                    # Commit complete source + receipt together. A crash can only leave scratch data.
                    with tempfile.TemporaryDirectory(dir=self.root) as scratch:
                        staged = Path(scratch) / key
                        try:
                            self._download(staged, candidate, original=bool(original_guard))
                        except InternetMusicProviderError as error:
                            if error.retryable:
                                raise RetryableJobError(error.code) from error
                            if supplemental:
                                return None
                            raise TerminalJobError(error.code) from error
                        os.replace(staged, work)
                checkpoint = work / "publication.json"
                state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
                receipt = None
                progress_at = time.monotonic()
                while state.get("state") != "PUBLISHED":
                    context.raise_if_cancelled()
                    if self.stop.is_set():
                        raise RetryableJobError("playlist_publisher_stopping")
                    if receipt is None:
                        receipt = read_receipt(work / "receipt.json", self.root)
                        if receipt.policy_reasons:
                            if supplemental:
                                return None
                            raise TerminalJobError(receipt.policy_reasons[0])
                    if original_guard:
                        original_guard()
                        if receipt.sha256 != candidate["sha256"]:
                            raise TerminalJobError("preservation.original_mismatch")
                        if state.get("recording_id") and not state.get("upload_id"):
                            with self.backend.sessions() as session:
                                conflict = session.scalar(text("""SELECT EXISTS(SELECT FROM vault.audio_variant v
                                    JOIN vault.vault_object o USING(vault_object_id)
                                    WHERE v.recording_id=:recording AND v.deleted_at IS NULL
                                    AND o.sha256<>decode(:sha,'hex') AND o.commit_status='COMMITTED')"""),
                                    {"recording": UUID(state["recording_id"]), "sha": candidate["sha256"]})
                            if conflict:
                                raise TerminalJobError("preservation.edition_conflict")
                    updated = (self.backend.advance(receipt, state, preservation=True) if original_guard
                               else self.backend.advance(receipt, state))
                    if updated != state:
                        save(checkpoint, updated)
                        state = updated
                        progress_at = time.monotonic()
                    elif time.monotonic() - progress_at > 120:
                        raise RetryableJobError("playlist_publication_pending")
                    else:
                        self.stop.wait(2)
                recording = UUID(state["recording_id"])
                if original_guard:
                    original_guard()
                    with self.backend.sessions() as session:
                        published = session.scalar(text("""SELECT v.recording_id FROM vault.audio_variant v
                            JOIN vault.vault_object o USING(vault_object_id) WHERE v.audio_variant_id=:variant
                            AND v.recording_id=:recording AND v.validation_status='VALID' AND v.deleted_at IS NULL
                            AND o.commit_status='COMMITTED' AND o.sha256=decode(:digest,'hex')"""),
                            {"variant": UUID(state["variant_id"]), "recording": recording, "digest": candidate["sha256"]})
                        if not published:
                            raise RetryableJobError("preservation.vault_pending")
                else:
                    with self.backend.sessions.begin() as session:
                        publish_bridge_catalog(session, recording)
                        published = session.scalar(text(f"SELECT recording_id FROM ({PUBLISHED_AUDIO}) p "
                            "WHERE recording_id=:recording AND audio_variant_id=:variant"),
                            {"recording": recording, "variant": UUID(state["variant_id"])})
                        if not published:
                            raise RetryableJobError("playlist_catalog_unavailable")
                if receipt and receipt.source_metadata:
                    self.backend.schedule_metadata(receipt, state)
                # A verified Vault replica now owns the bytes. Keep the small replay receipt.
                (work / "yt_dlp" / "audio.media").unlink(missing_ok=True)
                return published
            except ReceiptError as error:
                raise TerminalJobError("playlist_receipt_invalid") from error
            except (OSError, OperationalError, ResourceAdmissionError, VaultError) as error:
                raise RetryableJobError("playlist_publication_unavailable") from error
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})

    def _download(self, work, candidate, *, original=False):
        from autplay.adapters.filesystem.provider_child import read_source_metadata
        from autplay.adapters.media.tools import FfprobeInspector

        directory = work / "yt_dlp"
        path = self.provider.download(candidate["candidate_id"], directory)
        with path.open("rb") as audio:
            digest = hashlib.file_digest(audio, "sha256").hexdigest()
        detail = FfprobeInspector("ffprobe").inspect(path, single_audio=True)
        evidence = read_source_metadata(directory, candidate["candidate_id"])
        title = candidate["title"]
        if original:
            from autplay.domain.import_identity import normalize_text
            from autplay.domain.jobs import TerminalJobError

            fields = (evidence or {}).get("fields") or {}
            if (digest != candidate["sha256"] or abs(detail.duration_ms - candidate["duration_ms"]) > 2000
                    or any(normalize_text(fields.get(name) or "") != normalize_text(candidate[name])
                           for name in ("title", "artist"))):
                raise TerminalJobError("preservation.original_mismatch")
            title = f"{title} [{candidate['version']}]"
        save(work / "receipt.json", {
            "schema_version": 1, "key": work.name, "provider": "yt_dlp", "filename": path.name,
            "bytes": path.stat().st_size, "sha256": digest, "duration_seconds": detail.duration_ms / 1000,
            "item": {"artist": candidate["artist"], "title": title,
                     "album": ((evidence or {}).get("fields") or {}).get("album")},
            "source_metadata": evidence,
        })


def playlist_worker(backend, root, stop, watcher):
    from autplay.application.music_preservation import PRESERVE
    from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
    from autplay.adapters.system import Uuid7Generator
    from autplay.application.playlist_vault import PLAYLIST_ARTIST, PLAYLIST_PLAN, PLAYLIST_TRACK, PlaylistVaultHandler
    from autplay.entrypoints.worker_cpu import build_cpu_worker
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    publisher = PlaylistCatalogPublisher(backend, root, stop)
    handler = PlaylistVaultHandler(backend.sessions, publisher.search, publisher.acquire)
    handlers = {key: handler for key in (PLAYLIST_TRACK, PLAYLIST_ARTIST, PLAYLIST_PLAN)}
    worker = build_cpu_worker(uow_factory=SqlAlchemyJobUnitOfWorkFactory(backend.sessions),
                              ids=Uuid7Generator(), handlers=handlers)
    originals_worker = build_cpu_worker(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(backend.sessions, claim_owner=backend.owner),
        ids=Uuid7Generator(), handlers={PRESERVE: lambda ctx, lease: watcher.acquire(ctx, lease, publisher)},
    )
    while not stop.is_set():
        enabled = False
        try:
            with backend.sessions() as session:
                enabled = session.scalar(text("""SELECT EXISTS(SELECT FROM identity.source_provider
                    WHERE provider_key='autplay.server-catalog' AND enabled AND deleted_at IS NULL AND 'STREAM'=ANY(capabilities))"""))
            if originals_worker.run_once().outcome.value == "IDLE" and enabled:
                worker.run_once()
        except OperationalError:
            enabled = False
            print("playlist catalog database unavailable", file=sys.stderr, flush=True)
        stop.wait(2 if enabled else 30)


def preservation_scans(watcher, stop):
    from sqlalchemy.exc import SQLAlchemyError
    while not stop.is_set():
        try:
            watcher.tick()
        except SQLAlchemyError:
            print("preservation database unavailable", file=sys.stderr, flush=True)
        stop.wait(60)


def main() -> int:
    if sys.platform == "win32":
        raise ReceiptError("bridge_platform_unsupported")
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
    playlist_stop = threading.Event()
    from autplay.application.music_preservation import PreservationWatcher

    watcher = PreservationWatcher(backend.sessions, backend.owner, args.state / "preservation")
    playlist_executor = ThreadPoolExecutor(max_workers=2)
    monitor = None if args.once else playlist_executor.submit(preservation_scans, watcher, playlist_stop)
    playlist_task = None
    if backend.playlist_downloads_enabled and not args.once:
        playlist_task = playlist_executor.submit(playlist_worker, backend, args.state / "playlist-audio", playlist_stop, watcher)
    stopping = False

    def stop(_signal: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True
        playlist_stop.set()

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
        if monitor is not None and monitor.done():
            monitor.result()
        if playlist_task is not None and playlist_task.done():
            playlist_task.result()
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
                "metadata_states": dict(
                    Counter(
                        v.get("metadata_state", "PENDING")
                        for v in checkpoints.values()
                        if v["state"] == "PUBLISHED"
                    )
                ),
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
    playlist_stop.set()
    playlist_executor.shutdown(wait=True)
    backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
