"""PostgreSQL repository for P06 owner-scoped Vault runtime operations."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select, text
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models.audit import AuditEventRow
from autplay.adapters.postgresql.models.catalog import RecordingRow
from autplay.adapters.postgresql.models.identity import RecordingRedirectRow
from autplay.adapters.postgresql.models.jobs import JobRow
from autplay.adapters.postgresql.models.library import LibraryEntryRow, UserTrackRefRow
from autplay.adapters.postgresql.models.vault import (
    AudioFingerprintRow,
    AudioVariantRow,
    UploadChunkRow,
    UploadSessionRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.application.vault_ingest import IngestSession
from autplay.application.vault_reconciliation import ReconcileReport
from autplay.application.vault_streaming import AuthorizedStream
from autplay.application.vault_uploads import (
    CreateUploadCommand,
    UploadConflictError,
    UploadInfo,
    UploadRepository,
    UploadStateError,
    VaultNotFoundError,
    VaultPrincipal,
)
from autplay.domain.jobs import JobKey
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    ChunkWriteResult,
    OpaqueStorageKey,
    Sha256Digest,
    StorageOperationError,
    StorageSafetyError,
    VaultInventory,
    VaultLimits,
    VerifiedStagedFile,
)
from autplay.ports.jobs import EnqueueJob
from autplay.ports.vault import VaultStorage


class PostgresVaultRuntime(UploadRepository):
    """Short-transaction upload state operations backed by PostgreSQL rows."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def authorize_target(self, principal: VaultPrincipal, recording_id: UUID) -> bool:
        statement = (
            select(LibraryEntryRow.library_entry_id)
            .join(
                UserTrackRefRow,
                UserTrackRefRow.user_track_ref_id == LibraryEntryRow.user_track_ref_id,
            )
            .join(RecordingRow, RecordingRow.recording_id == UserTrackRefRow.recording_id)
            .where(
                LibraryEntryRow.user_id == principal.user_id,
                LibraryEntryRow.removed_at.is_(None),
                UserTrackRefRow.user_id == principal.user_id,
                UserTrackRefRow.deleted_at.is_(None),
                UserTrackRefRow.resolution_status == "RESOLVED",
                UserTrackRefRow.recording_id == recording_id,
                RecordingRow.deleted_at.is_(None),
                ~exists().where(RecordingRedirectRow.source_recording_id == recording_id),
            )
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none() is not None

    def create_or_replay(
        self,
        principal: VaultPrincipal,
        command: CreateUploadCommand,
        staging_key: OpaqueStorageKey,
        expires_at: datetime,
        limits: VaultLimits,
    ) -> tuple[UploadInfo, bool]:
        existing = self._session.execute(
            select(UploadSessionRow)
            .where(
                UploadSessionRow.user_id == principal.user_id,
                UploadSessionRow.idempotency_key == command.idempotency_key,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_hash != command.request_hash:
                raise UploadConflictError()
            return _info(existing), False
        row = UploadSessionRow(
            user_id=principal.user_id,
            device_id=principal.device_id,
            target_recording_id=command.recording_id,
            idempotency_key=command.idempotency_key,
            request_hash=command.request_hash,
            declared_sha256=None
            if command.declared_sha256 is None
            else command.declared_sha256.value,
            expected_size=command.expected_size,
            received_size=0,
            chunk_size=limits.max_chunk_bytes,
            max_chunks=limits.max_chunks,
            chunk_count=0,
            staging_key=staging_key.value,
            state="OPEN",
            expires_at=expires_at,
        )
        self._session.add(row)
        self._session.flush()
        return _info(row), True

    def get_owned_for_update(
        self, principal: VaultPrincipal, upload_session_id: UUID
    ) -> UploadInfo:
        return _info(self._owned_row(principal, upload_session_id, lock=True))

    def staging_key_for_owned(
        self, principal: VaultPrincipal, upload_session_id: UUID
    ) -> OpaqueStorageKey:
        return OpaqueStorageKey(
            self._owned_row(principal, upload_session_id, lock=True).staging_key
        )

    def record_chunk(
        self,
        principal: VaultPrincipal,
        upload_session_id: UUID,
        *,
        offset: int,
        chunk_index: int,
        byte_size: int,
        sha256: Sha256Digest,
    ) -> ChunkWriteResult:
        row = self._owned_row(principal, upload_session_id, lock=True)
        if row.state != "OPEN" or row.expires_at <= datetime.now(UTC):
            raise UploadStateError()
        existing = self._session.execute(
            select(UploadChunkRow).where(
                UploadChunkRow.upload_session_id == row.upload_session_id,
                UploadChunkRow.chunk_index == chunk_index,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.start_offset == offset
                and existing.byte_size == byte_size
                and existing.sha256 == sha256.value
            ):
                return ChunkWriteResult(next_offset=row.received_size, idempotent=True)
            raise UploadStateError()
        if (
            offset != row.received_size
            or byte_size < 1
            or byte_size > row.chunk_size
            or row.chunk_count >= row.max_chunks
            or offset + byte_size > row.expected_size
        ):
            raise UploadStateError()
        self._session.add(
            UploadChunkRow(
                upload_session_id=row.upload_session_id,
                chunk_index=chunk_index,
                start_offset=offset,
                byte_size=byte_size,
                sha256=sha256.value,
            )
        )
        row.received_size += byte_size
        row.chunk_count += 1
        self._session.flush()
        return ChunkWriteResult(next_offset=row.received_size, idempotent=False)

    def seal_and_enqueue(self, principal: VaultPrincipal, upload_session_id: UUID) -> UploadInfo:
        row = self._owned_row(principal, upload_session_id, lock=True)
        if row.state in {"SEALED", "PROCESSING", "COMMIT_PREPARED", "COMMITTED", "REUSED"}:
            return _info(row)
        if row.state != "OPEN" or row.received_size != row.expected_size:
            raise UploadStateError()
        job = PostgresJobRepository(self._session).enqueue(
            EnqueueJob(
                key=JobKey("vault.ingest", 1),
                user_id=principal.user_id,
                priority=3,
                payload={"upload_session_id": str(row.upload_session_id)},
                idempotency_scope="vault.ingest",
                idempotency_key=str(row.upload_session_id),
            )
        )
        row.job_id = job.job_id
        row.state = "SEALED"
        row.sealed_at = datetime.now(UTC)
        self._session.flush()
        return _info(row)

    def expire_open(
        self, principal: VaultPrincipal, upload_session_id: UUID
    ) -> tuple[UploadInfo, OpaqueStorageKey]:
        """Make an elapsed OPEN session terminal without deleting its recoverable bytes."""

        row = self._owned_row(principal, upload_session_id, lock=True)
        if row.state == "OPEN":
            row.state = "EXPIRED"
            row.error_code = "upload_session_expired"
            row.completed_at = datetime.now(UTC)
            self._audit(row, "vault.upload_expired")
            self._session.flush()
        return _info(row), OpaqueStorageKey(row.staging_key)

    def cancel(self, principal: VaultPrincipal, upload_session_id: UUID) -> None:
        row = self._owned_row(principal, upload_session_id, lock=True)
        if row.state in {"OPEN", "SEALED"}:
            row.state = "CANCELLED"
            row.error_code = "upload_cancelled"
            row.completed_at = datetime.now(UTC)
            self._session.flush()
            return
        if row.state == "CANCELLED":
            return
        raise UploadStateError()

    def _owned_row(
        self, principal: VaultPrincipal, upload_session_id: UUID, *, lock: bool
    ) -> UploadSessionRow:
        statement = select(UploadSessionRow).where(
            UploadSessionRow.upload_session_id == upload_session_id,
            UploadSessionRow.user_id == principal.user_id,
            UploadSessionRow.device_id == principal.device_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = self._session.execute(statement).scalar_one_or_none()
        if row is None:
            raise VaultNotFoundError()
        return row

    def acquire_sha_lock(self, digest: Sha256Digest) -> None:
        """Serialize one exact-byte CAS decision without exposing the digest."""

        lock_key = int.from_bytes(digest.value[:8], "big", signed=True)
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )

    def start_ingest(self, upload_session_id: UUID, job_id: UUID) -> IngestSession | None:
        """Claim a sealed upload; terminal/replayed sessions require no work."""

        row = self._session.execute(
            select(UploadSessionRow)
            .where(UploadSessionRow.upload_session_id == upload_session_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None or row.job_id != job_id:
            return None
        if row.state in {"COMMITTED", "REUSED", "QUARANTINED", "FAILED", "CANCELLED"}:
            return None
        if row.state == "SEALED":
            row.state = "PROCESSING"
        if row.state not in {"PROCESSING", "COMMIT_PREPARED"}:
            return None
        self._session.flush()
        return IngestSession(
            row.upload_session_id,
            row.target_recording_id,
            OpaqueStorageKey(row.staging_key),
            row.expected_size,
            row.declared_sha256,
        )

    def prepare_commit(
        self,
        ingest: IngestSession,
        verified: VerifiedStagedFile,
        metadata: AudioTechnicalMetadata,
        evidence: ChromaprintEvidence,
    ) -> str:
        """Record a pre-publish intent after the SHA advisory fence is acquired."""

        del evidence
        self.acquire_sha_lock(verified.sha256)
        session = self._session.execute(
            select(UploadSessionRow)
            .where(UploadSessionRow.upload_session_id == ingest.upload_session_id)
            .with_for_update()
        ).scalar_one()
        existing = self._session.execute(
            select(VaultObjectRow)
            .where(VaultObjectRow.sha256 == verified.sha256.value)
            .with_for_update()
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.vault_object_id == session.vault_object_id
                and existing.commit_status == "STAGING"
                and existing.byte_size == verified.byte_size
            ):
                prepared_replica = self._session.execute(
                    select(VaultReplicaRow.vault_replica_id)
                    .where(
                        VaultReplicaRow.vault_object_id == existing.vault_object_id,
                        VaultReplicaRow.storage_backend == "LOCAL_FILESYSTEM",
                        VaultReplicaRow.storage_key == verified.sha256.hex,
                        VaultReplicaRow.replica_status == "COPYING",
                    )
                    .limit(1)
                ).scalar_one_or_none()
                return "PUBLISH" if prepared_replica is not None else "CONFLICT"
            if existing.commit_status == "STAGING":
                return "WAIT"
            variant = self._eligible_reuse_variant(
                existing.vault_object_id, ingest.target_recording_id
            )
            replicas = (
                self._session.execute(
                    select(VaultReplicaRow)
                    .where(
                        VaultReplicaRow.vault_object_id == existing.vault_object_id,
                        VaultReplicaRow.storage_backend == "LOCAL_FILESYSTEM",
                        VaultReplicaRow.replica_status == "AVAILABLE",
                    )
                    .with_for_update()
                )
                .scalars()
                .all()
            )
            if (
                existing.commit_status == "COMMITTED"
                and existing.byte_size == verified.byte_size
                and variant is not None
                and len(replicas) == 1
                and replicas[0].storage_key == verified.sha256.hex
            ):
                session.vault_object_id = existing.vault_object_id
                session.computed_sha256 = verified.sha256.value
                session.state = "COMMIT_PREPARED"
                self._session.flush()
                return "REUSED"
            return "CONFLICT"
        created = VaultObjectRow(
            sha256=verified.sha256.value,
            byte_size=verified.byte_size,
            detected_mime_type=_mime(metadata.container),
            commit_status="STAGING",
        )
        self._session.add(created)
        self._session.flush()
        self._session.add(
            VaultReplicaRow(
                vault_object_id=created.vault_object_id,
                storage_backend="LOCAL_FILESYSTEM",
                storage_key=verified.sha256.hex,
                replica_status="COPYING",
            )
        )
        session.vault_object_id = created.vault_object_id
        session.computed_sha256 = verified.sha256.value
        session.state = "COMMIT_PREPARED"
        self._session.flush()
        return "PUBLISH"

    def finalize_published(
        self,
        ingest: IngestSession,
        storage_key: OpaqueStorageKey,
        metadata: AudioTechnicalMetadata,
        evidence: ChromaprintEvidence,
        *,
        reused: bool,
    ) -> bool:
        """Make the prepared CAS metadata streamable only after a file publish."""

        session = self._session.execute(
            select(UploadSessionRow)
            .where(UploadSessionRow.upload_session_id == ingest.upload_session_id)
            .with_for_update()
        ).scalar_one()
        if session.state in {"COMMITTED", "REUSED"}:
            return True
        if session.vault_object_id is None or session.computed_sha256 is None:
            raise UploadStateError()
        obj = self._session.get(VaultObjectRow, session.vault_object_id)
        if obj is None:
            raise UploadStateError()
        existing_variants = (
            self._session.execute(
                select(AudioVariantRow)
                .where(AudioVariantRow.vault_object_id == obj.vault_object_id)
                .with_for_update()
            )
            .scalars()
            .all()
        )
        if reused:
            variant = self._eligible_reuse_variant(obj.vault_object_id, ingest.target_recording_id)
            if variant is None or len(existing_variants) != 1:
                self._quarantine_finalize_conflict(session)
                return False
        elif existing_variants:
            self._quarantine_finalize_conflict(session)
            return False
        else:
            variant = AudioVariantRow(
                recording_id=ingest.target_recording_id,
                vault_object_id=obj.vault_object_id,
                codec=metadata.codec,
                container=metadata.container,
                bitrate_bps=metadata.bitrate_bps,
                bit_depth=metadata.bit_depth,
                sample_rate_hz=metadata.sample_rate_hz,
                channels=metadata.channels,
                duration_ms=metadata.duration_ms,
                validation_status="VALID",
            )
            self._session.add(variant)
            self._session.flush()
            self._session.add(
                AudioFingerprintRow(
                    audio_variant_id=variant.audio_variant_id,
                    algorithm=evidence.algorithm,
                    algorithm_version=evidence.algorithm_version,
                    duration_ms=evidence.duration_ms,
                    fingerprint_payload=evidence.payload,
                )
            )
        obj.commit_status = "COMMITTED"
        obj.committed_at = datetime.now(UTC)
        replica = self._session.execute(
            select(VaultReplicaRow).where(
                VaultReplicaRow.vault_object_id == obj.vault_object_id,
                VaultReplicaRow.storage_backend == "LOCAL_FILESYSTEM",
                VaultReplicaRow.storage_key == storage_key.value,
            )
        ).scalar_one()
        replica.replica_status = "AVAILABLE"
        replica.verified_at = datetime.now(UTC)
        session.audio_variant_id = variant.audio_variant_id
        session.state = "REUSED" if reused else "COMMITTED"
        session.completed_at = datetime.now(UTC)
        self._audit(session, "vault.ingest_reused" if reused else "vault.ingest_committed")
        self._session.flush()
        return True

    def quarantine(self, ingest: IngestSession, code: str) -> None:
        row = self._session.execute(
            select(UploadSessionRow)
            .where(UploadSessionRow.upload_session_id == ingest.upload_session_id)
            .with_for_update()
        ).scalar_one()
        if row.state not in {"COMMITTED", "REUSED", "CANCELLED"}:
            self._detach_uncommitted_object(row)
            row.state = "QUARANTINED"
            row.error_code = code[:100]
            row.completed_at = datetime.now(UTC)
            self._audit(row, "vault.ingest_quarantined")
            self._session.flush()

    def resolve_stream(self, principal: VaultPrincipal, audio_variant_id: UUID) -> AuthorizedStream:
        """Authorize by owner projection before returning a filesystem replica."""

        statement = (
            select(
                VaultReplicaRow.storage_key,
                VaultObjectRow.sha256,
                VaultObjectRow.byte_size,
                VaultObjectRow.detected_mime_type,
                VaultReplicaRow.verified_at,
            )
            .join(VaultObjectRow, VaultObjectRow.vault_object_id == VaultReplicaRow.vault_object_id)
            .join(
                AudioVariantRow, AudioVariantRow.vault_object_id == VaultObjectRow.vault_object_id
            )
            .join(RecordingRow, RecordingRow.recording_id == AudioVariantRow.recording_id)
            .join(UserTrackRefRow, UserTrackRefRow.recording_id == AudioVariantRow.recording_id)
            .join(
                LibraryEntryRow,
                LibraryEntryRow.user_track_ref_id == UserTrackRefRow.user_track_ref_id,
            )
            .where(
                AudioVariantRow.audio_variant_id == audio_variant_id,
                AudioVariantRow.validation_status == "VALID",
                AudioVariantRow.deleted_at.is_(None),
                RecordingRow.deleted_at.is_(None),
                ~exists().where(
                    RecordingRedirectRow.source_recording_id == AudioVariantRow.recording_id
                ),
                VaultObjectRow.commit_status == "COMMITTED",
                VaultReplicaRow.storage_backend == "LOCAL_FILESYSTEM",
                VaultReplicaRow.replica_status == "AVAILABLE",
                VaultReplicaRow.verified_at.is_not(None),
                UserTrackRefRow.user_id == principal.user_id,
                UserTrackRefRow.resolution_status == "RESOLVED",
                UserTrackRefRow.deleted_at.is_(None),
                LibraryEntryRow.user_id == principal.user_id,
                LibraryEntryRow.removed_at.is_(None),
            )
            .limit(1)
        )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            raise VaultNotFoundError()
        return AuthorizedStream(
            OpaqueStorageKey(row[0]), Sha256Digest(row[1]), row[2], row[3], row[4]
        )

    def _recording_is_active(self, recording_id: UUID) -> bool:
        return (
            self._session.execute(
                select(RecordingRow.recording_id)
                .where(
                    RecordingRow.recording_id == recording_id,
                    RecordingRow.deleted_at.is_(None),
                    ~exists().where(RecordingRedirectRow.source_recording_id == recording_id),
                )
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    def _eligible_reuse_variant(
        self, vault_object_id: UUID, target_recording_id: UUID
    ) -> AudioVariantRow | None:
        """Return the sole Variant-A candidate, otherwise fail closed without guessing."""

        variants = (
            self._session.execute(
                select(AudioVariantRow)
                .join(RecordingRow, RecordingRow.recording_id == AudioVariantRow.recording_id)
                .where(
                    AudioVariantRow.vault_object_id == vault_object_id,
                    AudioVariantRow.validation_status == "VALID",
                    AudioVariantRow.deleted_at.is_(None),
                    RecordingRow.deleted_at.is_(None),
                    ~exists().where(
                        RecordingRedirectRow.source_recording_id == AudioVariantRow.recording_id
                    ),
                )
                .with_for_update(of=AudioVariantRow)
            )
            .scalars()
            .all()
        )
        if len(variants) != 1 or variants[0].recording_id != target_recording_id:
            return None
        return variants[0]

    def _quarantine_finalize_conflict(self, session: UploadSessionRow) -> None:
        self._detach_uncommitted_object(session)
        session.state = "QUARANTINED"
        session.error_code = "vault.integrity_conflict"
        session.completed_at = datetime.now(UTC)
        self._audit(session, "vault.ingest_quarantined")
        self._session.flush()

    def reconcile_inventory(
        self,
        inventory: VaultInventory,
        storage: VaultStorage,
        *,
        apply: bool,
        limit: int,
    ) -> ReconcileReport:
        """Mark absent/corrupt replicas unservable and quarantine orphan final CAS files."""

        known_keys = set(
            self._session.execute(
                select(VaultReplicaRow.storage_key).where(
                    VaultReplicaRow.storage_backend == "LOCAL_FILESYSTEM"
                )
            ).scalars()
        )
        final_keys = {key.value for key in inventory.object_keys}
        inspected = repaired = quarantined = 0
        orphan_candidates = sorted(final_keys - known_keys)
        orphan_keys = orphan_candidates[:limit]
        for raw_key in orphan_keys:
            inspected += 1
            if apply:
                storage.quarantine_object(
                    OpaqueStorageKey(raw_key), OpaqueStorageKey(f"orphan-{raw_key}")
                )
                quarantined += 1
        remaining_capacity = limit - inspected
        terminal = {"COMMITTED", "REUSED", "QUARANTINED", "FAILED", "CANCELLED", "EXPIRED"}
        staging_values = [key.value for key in inventory.staging_keys]
        actionable_staging: list[
            tuple[OpaqueStorageKey, UploadSessionRow | None, str | None, bool, bool]
        ] = []
        if remaining_capacity and staging_values:
            staging_rows = self._session.execute(
                select(UploadSessionRow, JobRow.state)
                .outerjoin(JobRow, JobRow.job_id == UploadSessionRow.job_id)
                .where(UploadSessionRow.staging_key.in_(staging_values))
                .with_for_update(of=UploadSessionRow)
            ).all()
            by_staging_key = {row.staging_key: (row, job_state) for row, job_state in staging_rows}
            for key in inventory.staging_keys:
                tracked = by_staging_key.get(key.value)
                session = None if tracked is None else tracked[0]
                job_state = None if tracked is None else tracked[1]
                expired = (
                    session is not None
                    and session.state == "OPEN"
                    and session.expires_at <= datetime.now(UTC)
                )
                stranded = (
                    session is not None
                    and session.state not in terminal
                    and job_state in {"FAILED", "CANCELLED", "COMPLETED"}
                )
                if session is None or session.state in terminal or stranded or expired:
                    actionable_staging.append((key, session, job_state, expired, stranded))
            for key, session, job_state, expired, stranded in actionable_staging[
                :remaining_capacity
            ]:
                inspected += 1
                if apply:
                    if session is not None and expired:
                        session.state = "EXPIRED"
                        session.error_code = "upload_session_expired"
                        session.completed_at = datetime.now(UTC)
                        repaired += 1
                    elif session is not None and stranded:
                        self._detach_uncommitted_object(session)
                        session.state = "CANCELLED" if job_state == "CANCELLED" else "FAILED"
                        session.error_code = "vault_ingest_job_terminal"
                        session.completed_at = datetime.now(UTC)
                        repaired += 1
                    storage.quarantine(key, OpaqueStorageKey(f"staging-{key.value}"))
                    quarantined += 1
                    if session is not None:
                        self._audit(session, "vault.staging_quarantined")
        remaining_capacity = limit - inspected
        missing_total = missing_processed = 0
        if remaining_capacity:
            base_missing = UploadSessionRow.state.not_in(terminal)
            missing_condition = (
                and_(base_missing, UploadSessionRow.staging_key.not_in(staging_values))
                if staging_values
                else and_(base_missing)
            )
            missing_total = self._session.execute(
                select(func.count()).select_from(UploadSessionRow).where(missing_condition)
            ).scalar_one()
            missing_rows = (
                self._session.execute(
                    select(UploadSessionRow)
                    .outerjoin(JobRow, JobRow.job_id == UploadSessionRow.job_id)
                    .where(missing_condition)
                    .order_by(UploadSessionRow.created_at, UploadSessionRow.upload_session_id)
                    .with_for_update(of=UploadSessionRow)
                    .limit(remaining_capacity)
                )
                .scalars()
                .all()
            )
            missing_processed = len(missing_rows)
            for session in missing_rows:
                inspected += 1
                if not apply:
                    continue
                self._detach_uncommitted_object(session)
                if session.state == "OPEN" and session.expires_at <= datetime.now(UTC):
                    session.state = "EXPIRED"
                    session.error_code = "upload_session_expired"
                elif session.job_id is None:
                    session.state = "CANCELLED"
                    session.error_code = "vault_staging_missing"
                else:
                    session.state = "FAILED"
                    session.error_code = "vault_staging_missing"
                session.completed_at = datetime.now(UTC)
                self._audit(session, "vault.staging_missing")
                repaired += 1
        remaining_capacity = limit - inspected
        replica_total = replica_processed = 0
        if remaining_capacity:
            base_actionable = VaultReplicaRow.replica_status == "AVAILABLE"
            replica_actionable = (
                or_(base_actionable, VaultReplicaRow.storage_key.in_(final_keys))
                if final_keys
                else or_(base_actionable)
            )
            replica_condition = (
                VaultReplicaRow.storage_backend == "LOCAL_FILESYSTEM"
            ) & replica_actionable
            replica_total = self._session.execute(
                select(func.count()).select_from(VaultReplicaRow).where(replica_condition)
            ).scalar_one()
            rows = self._session.execute(
                select(VaultReplicaRow, VaultObjectRow)
                .join(
                    VaultObjectRow,
                    VaultObjectRow.vault_object_id == VaultReplicaRow.vault_object_id,
                )
                .where(replica_condition)
                .order_by(
                    VaultReplicaRow.verified_at.asc().nulls_first(),
                    VaultReplicaRow.vault_replica_id,
                )
                .with_for_update()
                .limit(remaining_capacity)
            ).all()
            replica_processed = len(rows)
            for replica, obj in rows:
                inspected += 1
                if replica.storage_key not in final_keys:
                    if apply and replica.replica_status == "AVAILABLE":
                        replica.replica_status = "MISSING"
                        obj.verification_error = "vault_replica_missing"
                        repaired += 1
                    continue
                if replica.replica_status == "QUARANTINED":
                    if apply:
                        storage.quarantine_object(
                            OpaqueStorageKey(replica.storage_key),
                            OpaqueStorageKey(f"object-{replica.storage_key}"),
                        )
                        quarantined += 1
                    continue
                try:
                    observed = storage.verify_object(OpaqueStorageKey(replica.storage_key))
                except StorageOperationError, StorageSafetyError:
                    if apply:
                        replica.replica_status = "CORRUPT"
                        replica.verified_at = datetime.now(UTC)
                        obj.verification_error = "vault_replica_corrupt"
                        repaired += 1
                    continue
                if not apply:
                    continue
                if observed.sha256.value != obj.sha256 or observed.byte_size != obj.byte_size:
                    replica.replica_status = "QUARANTINED"
                    obj.commit_status = "QUARANTINED"
                    obj.verification_error = "vault_replica_integrity_mismatch"
                    storage.quarantine_object(
                        OpaqueStorageKey(replica.storage_key),
                        OpaqueStorageKey(f"corrupt-{replica.storage_key}"),
                    )
                    repaired += 1
                    quarantined += 1
                    continue
                if replica.replica_status in {"MISSING", "CORRUPT"}:
                    replica.replica_status = "AVAILABLE"
                    repaired += 1
                replica.verified_at = datetime.now(UTC)
                obj.verification_error = None
        if apply:
            self._session.flush()
        return ReconcileReport(
            inspected,
            repaired,
            quarantined,
            len(orphan_candidates)
            - len(orphan_keys)
            + len(actionable_staging)
            - min(len(actionable_staging), max(0, limit - len(orphan_keys)))
            + missing_total
            - missing_processed
            + replica_total
            - replica_processed,
        )

    def _audit(self, session: UploadSessionRow, action: str) -> None:
        """Append a redacted operational event; payload/paths/digests stay out."""

        self._session.add(
            AuditEventRow(
                actor_type="WORKER",
                actor_user_id=session.user_id,
                actor_device_id=session.device_id,
                action=action,
                target_type="UPLOAD_SESSION",
                target_id=session.upload_session_id,
                metadata_sanitized={"phase": "P06"},
            )
        )

    def _detach_uncommitted_object(self, session: UploadSessionRow) -> None:
        if session.vault_object_id is None:
            return
        obj = self._session.get(VaultObjectRow, session.vault_object_id)
        if obj is not None and obj.commit_status != "COMMITTED":
            obj.commit_status = "QUARANTINED"
            replicas = self._session.execute(
                select(VaultReplicaRow).where(
                    VaultReplicaRow.vault_object_id == obj.vault_object_id
                )
            ).scalars()
            for replica in replicas:
                replica.replica_status = "QUARANTINED"
        session.vault_object_id = None
        session.computed_sha256 = None


def _info(row: UploadSessionRow) -> UploadInfo:
    return UploadInfo(
        row.upload_session_id,
        row.expected_size,
        row.received_size,
        row.state,
        row.expires_at,
        row.chunk_size,
        row.job_id,
    )


def _mime(container: str) -> str:
    return {
        "aac": "audio/aac",
        "adts": "audio/aac",
        "flac": "audio/flac",
        "m4a": "audio/mp4",
        "matroska": "audio/x-matroska",
        "mov": "audio/mp4",
        "mp3": "audio/mpeg",
        "mp4": "audio/mp4",
        "ogg": "audio/ogg",
        "webm": "audio/webm",
    }[container.lower()]


__all__ = ("PostgresVaultRuntime",)
