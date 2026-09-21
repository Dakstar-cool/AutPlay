"""Wire bounded terminal-upload cleanup without parent filesystem access."""

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.adapters.postgresql.upload_cleanup import PostgresUploadCleanupRepository
from autplay.application.upload_cleanup import UploadCleanupService
from autplay.runtime.provider_maintenance import ProcessProviderMaintenanceStorage


def build_upload_cleanup_service(
    sessions: sessionmaker[Session], root: Path
) -> UploadCleanupService:
    return UploadCleanupService(
        PostgresUploadCleanupRepository(sessions),
        ProcessProviderMaintenanceStorage(PostgresProviderMaintenanceRepository(sessions), root),
    )
