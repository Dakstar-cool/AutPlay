"""Short-lived SQLAlchemy unit of work for browser-authority application services."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from autplay.ports.web_admin import WebAdminRepository, WebAdminUnitOfWork

from .web_admin import SqlAlchemyWebAdminRepository


class SqlAlchemyWebAdminUnitOfWork:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions
        self._session: Session | None = None
        self.web_admin: WebAdminRepository

    def __enter__(self) -> SqlAlchemyWebAdminUnitOfWork:
        self._session = self._sessions()
        self.web_admin = SqlAlchemyWebAdminRepository(self._session)
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("web admin unit of work is not active")
        self._session.commit()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        if self._session is not None:
            self._session.rollback()
            self._session.close()
            self._session = None


class SqlAlchemyWebAdminUnitOfWorkFactory:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def __call__(self) -> WebAdminUnitOfWork:
        return SqlAlchemyWebAdminUnitOfWork(self._sessions)


__all__ = ("SqlAlchemyWebAdminUnitOfWork", "SqlAlchemyWebAdminUnitOfWorkFactory")
