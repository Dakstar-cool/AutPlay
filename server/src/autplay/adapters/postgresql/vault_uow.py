"""Short SQLAlchemy unit of work for Vault application operations."""

from __future__ import annotations

from types import TracebackType
from typing import cast
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.application.vault_ingest import IngestRepository, IngestSession
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    OpaqueStorageKey,
    VerifiedStagedFile,
)
from autplay.ports.transactions import VaultUnitOfWorkFactory


class SqlAlchemyVaultUnitOfWork:
    """Own one transaction and expose a transaction-bound Vault repository."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._vault: PostgresVaultRuntime | None = None
        self._finished = False

    @property
    def vault(self) -> PostgresVaultRuntime:
        if self._vault is None:
            raise RuntimeError("unit of work has not been entered")
        return self._vault

    def __enter__(self) -> SqlAlchemyVaultUnitOfWork:
        if self._session is not None:
            raise RuntimeError("unit of work cannot be entered twice")
        self._session = self._session_factory()
        self._vault = PostgresVaultRuntime(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        session = self._require_session()
        try:
            if not self._finished:
                session.rollback()
        finally:
            session.close()
            self._session = None
            self._vault = None
        return None

    def commit(self) -> None:
        if self._finished:
            raise RuntimeError("unit of work is already finished")
        self._require_session().commit()
        self._finished = True

    def rollback(self) -> None:
        if self._finished:
            raise RuntimeError("unit of work is already finished")
        self._require_session().rollback()
        self._finished = True

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        return self._session


class SqlAlchemyVaultUnitOfWorkFactory:
    """Create isolated Vault units from the configured session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> SqlAlchemyVaultUnitOfWork:
        return SqlAlchemyVaultUnitOfWork(self._session_factory)


class TransactionalIngestRepository(IngestRepository):
    """Commit each ingest checkpoint in its own short Vault transaction.

    Filesystem publication happens between ``prepare_commit`` and
    ``finalize_published``. Persisting each operation separately makes every
    resulting crash window observable and recoverable by reconciliation.
    """

    def __init__(self, uow_factory: VaultUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def start_ingest(self, upload_session_id: UUID, job_id: UUID) -> IngestSession | None:
        with self._uow_factory() as unit:
            result = unit.vault.start_ingest(upload_session_id, job_id)  # type: ignore[attr-defined]
            unit.commit()
        return cast(IngestSession | None, result)

    def prepare_commit(
        self,
        session: IngestSession,
        verified: VerifiedStagedFile,
        metadata: AudioTechnicalMetadata,
        evidence: ChromaprintEvidence,
    ) -> str:
        with self._uow_factory() as unit:
            result = unit.vault.prepare_commit(session, verified, metadata, evidence)  # type: ignore[attr-defined]
            unit.commit()
        return cast(str, result)

    def finalize_published(
        self,
        session: IngestSession,
        storage_key: OpaqueStorageKey,
        metadata: AudioTechnicalMetadata,
        evidence: ChromaprintEvidence,
        *,
        reused: bool,
    ) -> bool:
        with self._uow_factory() as unit:
            result = unit.vault.finalize_published(  # type: ignore[attr-defined]
                session, storage_key, metadata, evidence, reused=reused
            )
            unit.commit()
        return cast(bool, result)

    def quarantine(self, session: IngestSession, code: str) -> None:
        with self._uow_factory() as unit:
            unit.vault.quarantine(session, code)  # type: ignore[attr-defined]
            unit.commit()


__all__ = (
    "SqlAlchemyVaultUnitOfWork",
    "SqlAlchemyVaultUnitOfWorkFactory",
    "TransactionalIngestRepository",
)
