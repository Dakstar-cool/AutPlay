"""Application transaction boundary."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from .jobs import JobRepository


class VaultUnitOfWork(Protocol):
    """Transaction boundary for P06 Vault persistence operations."""

    @property
    def vault(self) -> object:
        """Return the transaction-bound Vault runtime repository."""

        ...

    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class VaultUnitOfWorkFactory(Protocol):
    """Create one isolated Vault application transaction."""

    def __call__(self) -> VaultUnitOfWork: ...


class JobUnitOfWork(Protocol):
    """One caller-owned transaction exposing the jobs repository."""

    @property
    def jobs(self) -> JobRepository:
        """Return the transaction-bound jobs repository."""

        ...

    def __enter__(self) -> Self:
        """Open the transaction resources."""

        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Roll back uncommitted work and release resources."""

        ...

    def commit(self) -> None:
        """Commit the transaction."""

        ...

    def rollback(self) -> None:
        """Roll back the transaction."""

        ...


class JobUnitOfWorkFactory(Protocol):
    """Create an isolated application transaction."""

    def __call__(self) -> JobUnitOfWork:
        """Return a new unopened unit of work."""

        ...


__all__ = ("JobUnitOfWork", "JobUnitOfWorkFactory", "VaultUnitOfWork", "VaultUnitOfWorkFactory")
