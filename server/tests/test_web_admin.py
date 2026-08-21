"""Unit coverage for the M6 application-owned cleanup boundary."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.application.web_admin import WebAdminService
from autplay.domain.web_admin import WebAdminError
from autplay.ports.web_admin import WebAdminUnitOfWork, WebAdminUnitOfWorkFactory


class _Repository:
    def __init__(self) -> None:
        self.limit: int | None = None
        self.receipt_committed = True
        self.login_receipt_args: tuple[UUID, bytes, datetime] | None = None

    def cleanup_expired(self, limit: int) -> int:
        self.limit = limit
        return 3

    def login_receipt(self, operation_id: UUID, request_sha256: bytes, now: datetime) -> bool:
        self.login_receipt_args = (operation_id, request_sha256, now)
        return self.receipt_committed


class _Unit:
    def __init__(self, repository: _Repository) -> None:
        self.web_admin = repository
        self.committed = False

    def __enter__(self) -> _Unit:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        return None

    def commit(self) -> None:
        self.committed = True


def _factory(unit: _Unit) -> WebAdminUnitOfWorkFactory:
    return lambda: cast(WebAdminUnitOfWork, unit)


def test_cleanup_is_bounded_and_committed() -> None:
    repository = _Repository()
    unit = _Unit(repository)
    service = WebAdminService(_factory(unit))

    assert service.cleanup_expired(17) == 3
    assert repository.limit == 17
    assert unit.committed is True


@pytest.mark.parametrize("request_hash", [b"x" * 32, b"y" * 32])
def test_login_retry_never_replays_cookie_bearer(request_hash: bytes) -> None:
    repository = _Repository()
    service = WebAdminService(_factory(_Unit(repository)))

    with pytest.raises(WebAdminError, match="browser_login_outcome_unknown"):
        service.login_retry_outcome(uuid4(), request_hash)
    assert repository.login_receipt_args is not None
    assert repository.login_receipt_args[1] == request_hash


def test_unknown_login_retry_is_not_reported_as_committed() -> None:
    repository = _Repository()
    repository.receipt_committed = False
    service = WebAdminService(_factory(_Unit(repository)))
    with pytest.raises(WebAdminError, match="browser_invitation_unavailable"):
        service.login_retry_outcome(uuid4(), b"z" * 32)


@pytest.mark.parametrize("limit", [0, 10_001])
def test_cleanup_rejects_unbounded_batches(limit: int) -> None:
    service = WebAdminService(_factory(_Unit(_Repository())))

    with pytest.raises(ValueError, match=r"within 1\.\.10000"):
        service.cleanup_expired(limit)
