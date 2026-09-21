"""Positive inventory pages preserve live writers and recheck orphan claims."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_inventory import FilesystemVaultInventoryCursor
from autplay.adapters.postgresql.models import (
    OrphanObjectClaimRow,
    ProviderMaintenanceRow,
    UploadSessionRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.adapters.postgresql.orphan_object_retirement import PostgresOrphanObjectRepository
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.adapters.postgresql.vault_inventory import PostgresVaultInventoryRepository
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.application.orphan_object_retirement import (
    OrphanObjectClaim,
    OrphanObjectRetirementService,
)
from autplay.application.vault_inventory import (
    BoundedVaultInventoryService,
    InventoryArea,
    InventoryEntry,
    InventoryObservation,
    InventoryOwnership,
    InventoryPage,
)
from autplay.application.vault_uploads import (
    CreateUploadCommand,
    VaultPrincipal,
    VaultUploadService,
)
from autplay.domain.vault import OpaqueStorageKey
from autplay.runtime.provider_maintenance import ProcessProviderMaintenanceStorage
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool

from .test_internet_vault_publication import EVIDENCE, METADATA
from .test_orphan_object_retirement import Draft, draft, ingest_repository
from .test_provider_staging import writer
from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_vault_runtime import _publish

__all__ = ["admission", "draft"]


def observed_object(draft: Draft) -> InventoryPage:
    return InventoryPage((InventoryEntry(InventoryArea.OBJECT, draft.claim.storage_key),), 1, True)


@pytest.mark.parametrize("state", ["STAGING", "QUARANTINED", "COMMITTED"])
def test_all_object_metadata_is_protected_without_replica(
    admission: AdmissionHarness, draft: Draft, state: str
) -> None:
    assert (
        ingest_repository(admission).prepare_commit(
            draft.ingest, draft.verified, METADATA, EVIDENCE
        )
        == "PUBLISH"
    )
    with admission.sessions.begin() as session:
        session.execute(delete(VaultReplicaRow))
        obj = present(session.scalar(select(VaultObjectRow)))
        obj.commit_status = state
        if state == "COMMITTED":
            obj.committed_at = session.scalar(select(func.clock_timestamp()))
    observations = PostgresVaultInventoryRepository(admission.sessions).observe(
        observed_object(draft)
    )
    assert len(observations) == 1
    assert observations[0].ownership == InventoryOwnership.REGISTERED_OBJECT
    assert draft.source.exists()


def test_replica_registration_protects_a_different_object_key(
    admission: AdmissionHarness, draft: Draft
) -> None:
    with admission.sessions.begin() as session:
        obj = VaultObjectRow(
            sha256=b"x" * 32, byte_size=1, detected_mime_type="audio/flac", commit_status="STAGING"
        )
        session.add(obj)
        session.flush()
        session.add(
            VaultReplicaRow(
                vault_object_id=obj.vault_object_id,
                storage_backend="LOCAL_FILESYSTEM",
                storage_key=draft.claim.storage_key.value,
                replica_status="QUARANTINED",
            )
        )
    observations = PostgresVaultInventoryRepository(admission.sessions).observe(
        observed_object(draft)
    )
    assert observations[0].ownership == InventoryOwnership.REGISTERED_OBJECT


def test_uncommitted_upload_and_legacy_discovery_staging_never_grant_cleanup(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    actor = admission.actor()
    recording = admission.recording(actor)
    key, legacy = OpaqueStorageKey(uuid4().hex), OpaqueStorageKey(f"disc-{uuid4().hex}")
    storage = FilesystemVaultStorage(tmp_path)
    storage.create_staging(legacy)
    repository = PostgresVaultInventoryRepository(admission.sessions)
    cursor = FilesystemVaultInventoryCursor(tmp_path)
    maintenance = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    try:
        with admission.sessions.begin() as creator:
            info, created = VaultUploadService(repository=PostgresVaultRuntime(creator)).create(
                VaultPrincipal(actor.user_id, actor.device_id),
                CreateUploadCommand(recording, 100, "uncommitted-inventory"),
                now=datetime.now(UTC),
                staging_key=key,
            )
            assert created and not (tmp_path / "staging" / key.value).exists()
            page = InventoryPage(
                tuple(InventoryEntry(InventoryArea.STAGING, item) for item in (key, legacy)),
                2,
                False,
            )
            assert {item.ownership for item in repository.observe(page)} == {
                InventoryOwnership.UNREGISTERED_STAGING
            }
            batch = BoundedVaultInventoryService(
                cursor,
                repository,
                orphan_retirement=OrphanObjectRetirementService(
                    PostgresOrphanObjectRepository(admission.sessions), maintenance
                ),
            ).run_batch()
            assert batch.observed == 1 and batch.retired == batch.orphan_candidates == 0
            assert not (tmp_path / "staging" / key.value).exists()
            assert (tmp_path / "staging" / legacy.value).exists()
        assert repository.observe(page)[0].ownership == InventoryOwnership.UPLOAD_STAGING
        with admission.sessions() as session:
            assert present(session.get(UploadSessionRow, info.upload_session_id)).state == "OPEN"
            assert session.scalar(select(func.count()).select_from(ProviderMaintenanceRow)) == 0
    finally:
        cursor.close()
        assert not maintenance.shutdown()


@pytest.mark.parametrize("closed", [False, True])
def test_provider_receipt_remains_protected_after_accounting_removal(
    admission: AdmissionHarness, tmp_path: Path, closed: bool
) -> None:
    owned = writer(admission)
    FilesystemVaultStorage(tmp_path).create_staging(owned.key)
    if closed:
        admission.service.confirm_execution_exit(owned.ticket, owned.proof)
        admission.service.close_io(owned.ticket.permit)
    page = InventoryPage((InventoryEntry(InventoryArea.STAGING, owned.key),), 1, True)
    observations = PostgresVaultInventoryRepository(admission.sessions).observe(page)
    assert observations[0].ownership == InventoryOwnership.PROVIDER_STAGING
    assert (tmp_path / "staging" / owned.key.value).exists()


def test_publication_after_observation_wins_before_retirement_claim(
    admission: AdmissionHarness, draft: Draft, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = PostgresVaultInventoryRepository(admission.sessions)
    original = repository.observe

    def observe(page: InventoryPage) -> tuple[InventoryObservation, ...]:
        result = original(page)
        assert any(item.ownership == InventoryOwnership.ORPHAN_OBJECT_CANDIDATE for item in result)
        assert (
            ingest_repository(admission).prepare_commit(
                draft.ingest, draft.verified, METADATA, EVIDENCE
            )
            == "PUBLISH"
        )
        return result

    monkeypatch.setattr(repository, "observe", observe)
    cursor = FilesystemVaultInventoryCursor(tmp_path)
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    try:
        batch = BoundedVaultInventoryService(
            cursor,
            repository,
            orphan_retirement=OrphanObjectRetirementService(
                PostgresOrphanObjectRepository(admission.sessions), storage
            ),
        ).run_batch()
        assert batch.orphan_candidates == 1 and batch.retired == 0
        assert draft.source.exists()
        with admission.sessions() as session:
            assert session.scalar(select(func.count()).select_from(OrphanObjectClaimRow)) == 0
            assert session.scalar(select(func.count()).select_from(ProviderMaintenanceRow)) == 0
    finally:
        cursor.close()
        assert not storage.shutdown()


def test_bounded_discovery_retires_only_orphan_after_exact_process_exit(
    admission: AdmissionHarness, draft: Draft, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cursor = FilesystemVaultInventoryCursor(tmp_path)
    original = cursor.next_page

    def scan(*, maximum: int) -> InventoryPage:
        assert isinstance(admission.engine.pool, QueuePool)
        assert admission.engine.pool.checkedout() == 0
        return original(maximum=maximum)

    monkeypatch.setattr(cursor, "next_page", scan)
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    original_retire = storage.retire_orphan_object

    def retire(claim: OrphanObjectClaim) -> UUID:
        assert isinstance(admission.engine.pool, QueuePool)
        assert admission.engine.pool.checkedout() == 0
        return original_retire(claim)

    monkeypatch.setattr(storage, "retire_orphan_object", retire)
    service = BoundedVaultInventoryService(
        cursor,
        PostgresVaultInventoryRepository(admission.sessions),
        orphan_retirement=OrphanObjectRetirementService(
            PostgresOrphanObjectRepository(admission.sessions), storage
        ),
    )
    try:
        retired = observed = 0
        for _ in range(30):
            batch = service.run_batch(maximum=1)
            retired += batch.retired
            observed += batch.observed
            if batch.exhausted:
                break
        else:
            pytest.fail("cursor failed to finish the fixture")
        assert retired == 1 and observed == 2 and not draft.source.exists()
        with admission.sessions() as session:
            claim = present(session.scalar(select(OrphanObjectClaimRow)))
            run = present(session.get(ProviderMaintenanceRow, claim.completed_execution_id))
            assert run.closed_at is not None and present(claim.completed_at) >= run.closed_at
            assert run.closure_kind == "PROCESS_EXIT" and run.exit_code == 0
            assert run.child_pid is not None
            assert (
                present(session.get(UploadSessionRow, draft.ingest.upload_session_id)).state
                == "PROCESSING"
            )
        assert (tmp_path / "staging" / draft.ingest.staging_key.value).exists()
    finally:
        service.close()
        assert not storage.shutdown()


def test_absent_staging_observation_never_fails_processing_upload(
    admission: AdmissionHarness, draft: Draft, tmp_path: Path
) -> None:
    draft.storage.cleanup_staging(draft.ingest.staging_key)
    cursor = FilesystemVaultInventoryCursor(tmp_path)
    try:
        batch = BoundedVaultInventoryService(
            cursor, PostgresVaultInventoryRepository(admission.sessions)
        ).run_batch()
        assert batch.exhausted and batch.observed == 1 and batch.retired == 0
        with admission.sessions() as session:
            row = present(session.get(UploadSessionRow, draft.ingest.upload_session_id))
            assert row.state == "PROCESSING" and row.error_code is None
    finally:
        cursor.close()


@pytest.mark.parametrize(
    "fault", ["observation", "second_completion_reply", "existing_claim_reply"]
)
def test_failed_batch_keeps_page_and_exact_claim_ids_until_retry_finishes(
    admission: AdmissionHarness,
    draft: Draft,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    second = _publish(draft.storage, "second-orphan", b"different orphan bytes")
    cursor = FilesystemVaultInventoryCursor(tmp_path)
    repository = PostgresVaultInventoryRepository(admission.sessions)
    claims = PostgresOrphanObjectRepository(admission.sessions)
    if fault == "existing_claim_reply":
        assert claims.claim(draft.claim.storage_key, uuid4()) is not None
        assert claims.claim(second, uuid4()) is not None
    original_scan, original_observe, original_complete = (
        cursor.next_page,
        repository.observe,
        claims.complete,
    )
    scans = observations = completions = 0

    def scan(*, maximum: int) -> InventoryPage:
        nonlocal scans
        scans += 1
        return original_scan(maximum=maximum)

    def observe(page: InventoryPage) -> tuple[InventoryObservation, ...]:
        nonlocal observations
        observations += 1
        if observations == 1 and fault == "observation":
            raise SQLAlchemyError("synthetic metadata interruption")
        return original_observe(page)

    def complete(claim: OrphanObjectClaim, execution_id: UUID) -> None:
        nonlocal completions
        original_complete(claim, execution_id)
        completions += 1
        if completions == 2 and fault in {"second_completion_reply", "existing_claim_reply"}:
            raise SQLAlchemyError("synthetic lost completion response")

    monkeypatch.setattr(cursor, "next_page", scan)
    monkeypatch.setattr(repository, "observe", observe)
    monkeypatch.setattr(claims, "complete", complete)
    storage = ProcessProviderMaintenanceStorage(
        PostgresProviderMaintenanceRepository(admission.sessions), tmp_path
    )
    service = BoundedVaultInventoryService(
        cursor,
        repository,
        orphan_retirement=OrphanObjectRetirementService(claims, storage),
    )
    try:
        with pytest.raises(SQLAlchemyError, match="synthetic"):
            service.run_batch()
        assert scans == 1
        batch = service.run_batch()
        assert scans == 1 and batch.exhausted
        assert batch.retired == batch.orphan_candidates == 2
        assert not draft.source.exists()
        assert not (
            tmp_path / "objects" / second.value[:2] / second.value[2:4] / second.value
        ).exists()
        with admission.sessions() as session:
            assert session.scalar(select(func.count()).select_from(OrphanObjectClaimRow)) == 2
            assert session.scalar(select(func.count()).select_from(ProviderMaintenanceRow)) == 2
        assert service.run_batch().retired == 0
    finally:
        service.close()
        assert not storage.shutdown()


pytestmark = pytest.mark.usefixtures("internal_io_budget")
