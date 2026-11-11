"""Control an explicit SQL clock return while retaining the real database sample."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from autplay.application import profile_pairing
from sqlalchemy.orm import Session


def control_database_clock(
    monkeypatch: pytest.MonkeyPatch, now: Callable[[], datetime], *modules: str
) -> None:
    sample = profile_pairing._database_now

    def controlled(session: Session) -> datetime:
        actual = sample(session)
        assert isinstance(actual, datetime) and actual.utcoffset() == UTC.utcoffset(actual)
        value = now()
        assert value.utcoffset() == UTC.utcoffset(value)
        return value

    for module in modules:
        monkeypatch.setattr(module + "._database_now", controlled)
