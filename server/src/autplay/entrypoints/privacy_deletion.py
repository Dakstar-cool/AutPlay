"""One restore gate shared by API, streaming and worker processes."""

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.privacy_deletion import PrivacyDeletionService
from autplay.domain.privacy_deletion import DeletionEvidenceError
from autplay.runtime.settings import ApiSettings, StreamSettings, WorkerSettings

type PrivacySettings = ApiSettings | StreamSettings | WorkerSettings


def build_deletion_ledger(settings: PrivacySettings) -> FilesystemDeletionLedger | None:
    if settings.privacy_ledger_path is None:
        return None
    if settings.privacy_ledger_key is None or settings.privacy_ledger_key_id is None:
        raise DeletionEvidenceError()
    return FilesystemDeletionLedger(
        settings.privacy_ledger_path,
        settings.privacy_ledger_key.get_secret_value().encode(),
        settings.privacy_ledger_key_id,
    )


def build_privacy_deletion_service(
    settings: PrivacySettings, engine: Engine
) -> PrivacyDeletionService | None:
    ledger = build_deletion_ledger(settings)
    if ledger is None:
        return None
    return PrivacyDeletionService(
        sessionmaker(engine, class_=Session, expire_on_commit=False), ledger
    )


def enforce_privacy_restore_guard(settings: PrivacySettings, engine: Engine | None = None) -> None:
    from autplay.entrypoints.training_consent_restore import enforce_training_consent_restore_guard

    if settings.privacy_ledger_path is None:
        enforce_training_consent_restore_guard(settings, engine)
        return
    owned_engine = engine is None
    resolved_engine = engine or create_runtime_engine(settings)
    try:
        service = build_privacy_deletion_service(settings, resolved_engine)
        if service is None:
            raise DeletionEvidenceError()
        service.restore_guard()
        enforce_training_consent_restore_guard(settings, resolved_engine)
    except SQLAlchemyError:
        raise DeletionEvidenceError() from None
    finally:
        if owned_engine:
            resolved_engine.dispose()
