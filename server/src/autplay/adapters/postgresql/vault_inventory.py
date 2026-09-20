"""Classify only a bounded page of positive observations in a short transaction."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from autplay.application.vault_inventory import (
    InventoryArea,
    InventoryObservation,
    InventoryOwnership,
    InventoryPage,
)

from .models.provider_staging import ProviderStagingRow
from .models.vault import UploadSessionRow, VaultObjectRow, VaultReplicaRow


class PostgresVaultInventoryRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def observe(self, page: InventoryPage) -> tuple[InventoryObservation, ...]:
        objects = {item.key.value for item in page.entries if item.area == InventoryArea.OBJECT}
        staging = {item.key.value for item in page.entries if item.area == InventoryArea.STAGING}
        registered: set[str] = set()
        providers: set[str] = set()
        uploads: set[str] = set()
        if not page.entries:
            return ()
        with self._sessions() as session:
            if objects:
                registered.update(
                    digest.hex()
                    for digest in session.scalars(
                        select(VaultObjectRow.sha256).where(
                            VaultObjectRow.sha256.in_(bytes.fromhex(key) for key in objects)
                        )
                    )
                )
                registered.update(
                    session.scalars(
                        select(VaultReplicaRow.storage_key).where(
                            VaultReplicaRow.storage_backend == "LOCAL_FILESYSTEM",
                            VaultReplicaRow.storage_key.in_(objects),
                        )
                    )
                )
            if staging:
                providers.update(
                    session.scalars(
                        select(ProviderStagingRow.staging_key).where(
                            ProviderStagingRow.staging_key.in_(staging)
                        )
                    )
                )
                uploads.update(
                    session.scalars(
                        select(UploadSessionRow.staging_key).where(
                            UploadSessionRow.staging_key.in_(staging)
                        )
                    )
                )
        observations: list[InventoryObservation] = []
        for item in page.entries:
            key = item.key.value
            if item.area == InventoryArea.OBJECT:
                ownership = (
                    InventoryOwnership.REGISTERED_OBJECT
                    if key in registered
                    else InventoryOwnership.ORPHAN_OBJECT_CANDIDATE
                )
            elif key in providers:
                ownership = InventoryOwnership.PROVIDER_STAGING
            elif key in uploads:
                ownership = InventoryOwnership.UPLOAD_STAGING
            else:
                # Absence may be an uncommitted API upload or legacy disc-*
                # writer. It never establishes that a staging file is orphaned.
                ownership = InventoryOwnership.UNREGISTERED_STAGING
            observations.append(InventoryObservation(item, ownership))
        return tuple(observations)
