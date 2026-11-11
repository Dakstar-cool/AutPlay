"""One restore gate shared by API, streaming and worker processes."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.postgresql.retained_deletion_reservations import (
    configure_retained_deletion_reservations,
)
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.privacy_deletion import PrivacyDeletionService
from autplay.domain.privacy_deletion import (
    DeletionEvidence,
    DeletionEvidenceError,
    DeletionRequestEvidence,
)
from autplay.ports.privacy_deletion import DeletionLedger
from autplay.runtime.settings import ApiSettings, StreamSettings, WorkerSettings

type PrivacySettings = ApiSettings | StreamSettings | WorkerSettings


class _UnavailableDeletionLedger:
    """Absent configuration is never an empty independent history."""

    def owner_tag(self, user_id: UUID) -> str:
        raise DeletionEvidenceError()

    def read(self) -> tuple[DeletionEvidence, ...]:
        raise DeletionEvidenceError()

    def request_read(self) -> tuple[DeletionRequestEvidence, ...]:
        raise DeletionEvidenceError()

    def request_coverage_started_at(self) -> datetime:
        raise DeletionEvidenceError()

    def request_record(self, evidence: DeletionRequestEvidence) -> DeletionRequestEvidence:
        raise DeletionEvidenceError()

    def prepare(self, user_id: UUID, request_id: UUID, accepted_at: datetime) -> DeletionEvidence:
        raise DeletionEvidenceError()

    def complete(
        self,
        owner_tag: str,
        request_id: UUID,
        completed_at: datetime,
        removed_rows: int,
    ) -> DeletionEvidence:
        raise DeletionEvidenceError()


class DeletionEvidenceRuntime:
    """Explicit process owner; construction and readiness never access DB/files.

    Standalone builders must share this owner and run restore_guard before work.
    A builder without an owner gets an unready dependency, never implicit trust.
    """

    def __init__(self, settings: PrivacySettings, engine: Engine) -> None:
        self._settings, self._engine = settings, engine
        ledger = build_deletion_ledger(settings)
        self._configured = ledger is not None
        self.ledger: DeletionLedger = ledger if ledger is not None else _UnavailableDeletionLedger()
        self._ready = False

    def evidence_ready(self) -> bool:
        """Pure lifecycle snapshot, with no validation or storage side effects."""
        return self._ready

    def reset(self) -> None:
        self._ready = False

    def configure(self, sessions: sessionmaker[Session]) -> None:
        configure_retained_deletion_reservations(
            sessions,
            ledger=self.ledger,
            evidence_ready=self.evidence_ready,
        )

    def restore_guard(self) -> None:
        from autplay.entrypoints.training_consent_restore import (
            enforce_training_consent_restore_guard,
        )

        self.reset()
        try:
            if self._configured:
                sessions = sessionmaker(self._engine, class_=Session, expire_on_commit=False)
                self.configure(sessions)
                PrivacyDeletionService(sessions, self.ledger).restore_guard()
            enforce_training_consent_restore_guard(self._settings, self._engine)
            self._ready = self._configured
        except SQLAlchemyError:
            self.reset()
            raise DeletionEvidenceError() from None
        except BaseException:
            self.reset()
            raise


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
