"""SQLAlchemy unit of work for the durable jobs application boundary."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from autplay.ports.jobs import JobRepository

from .jobs_runtime import PostgresJobRepository


class SqlAlchemyJobUnitOfWork:
    """Own one SQLAlchemy session and require an explicit application commit."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._jobs: PostgresJobRepository | None = None
        self._finished = False

    @property
    def jobs(self) -> JobRepository:
        """Return the transaction-bound repository after entering the context."""

        if self._jobs is None:
            raise RuntimeError("unit of work has not been entered")
        return self._jobs

    def __enter__(self) -> SqlAlchemyJobUnitOfWork:
        """Open a fresh session."""

        if self._session is not None:
            raise RuntimeError("unit of work cannot be entered twice")
        self._session = self._session_factory()
        self._jobs = PostgresJobRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Roll back unfinished work and always close the session."""

        session = self._require_session()
        try:
            if not self._finished:
                session.rollback()
        finally:
            session.close()
            self._session = None
            self._jobs = None
        return None

    def commit(self) -> None:
        """Commit exactly once."""

        if self._finished:
            raise RuntimeError("unit of work is already finished")
        self._require_session().commit()
        self._finished = True

    def rollback(self) -> None:
        """Roll back exactly once."""

        if self._finished:
            raise RuntimeError("unit of work is already finished")
        self._require_session().rollback()
        self._finished = True

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        return self._session


class SqlAlchemyJobUnitOfWorkFactory:
    """Create independent job units of work from one configured session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> SqlAlchemyJobUnitOfWork:
        """Return a new unopened unit of work."""

        return SqlAlchemyJobUnitOfWork(self._session_factory)


__all__ = ("SqlAlchemyJobUnitOfWork", "SqlAlchemyJobUnitOfWorkFactory")
