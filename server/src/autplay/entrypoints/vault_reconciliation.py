"""Compose retained maintenance outside any caller-owned Vault transaction."""

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.orphan_object_retirement import PostgresOrphanObjectRepository
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.adapters.postgresql.vault_inventory import PostgresVaultInventoryRepository
from autplay.application.vault_reconciliation import VaultReconciliationService
from autplay.runtime.provider_maintenance import ProcessProviderMaintenanceStorage
from autplay.runtime.vault_inventory import ProcessVaultInventoryCursor


def build_vault_reconciliation_service(
    sessions: sessionmaker[Session], root: Path
) -> VaultReconciliationService:
    maintenance = PostgresProviderMaintenanceRepository(sessions)
    return VaultReconciliationService(
        cursor=ProcessVaultInventoryCursor(maintenance, root),
        inventory=PostgresVaultInventoryRepository(sessions),
        claims=PostgresOrphanObjectRepository(sessions),
        storage=ProcessProviderMaintenanceStorage(maintenance, root),
    )
