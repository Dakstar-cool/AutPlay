"""Catalog network receipts neither bypass process closure nor block final erasure."""

from uuid import uuid4

import pytest
from autplay.adapters.postgresql.catalog_execution import PostgresCatalogExecutionRepository
from autplay.domain.resource_execution import ExitKind, ProcessExitEvidence
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from .test_account_deletion import PairingHarness
from .test_account_deletion import base_pair as base_pair
from .test_account_deletion import pair as pair
from .test_music_library import owner
from .test_privacy_purge import overdue


def test_live_catalog_blocks_purge_then_closed_receipt_erased(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    other = owner(sessions)
    repo = PostgresCatalogExecutionRepository(sessions)
    ticket = repo.plan(pair.actor, uuid4())
    repo.prepare(ticket)
    operation = overdue(pair, monkeypatch)
    with (
        pytest.raises(DBAPIError, match="privacy_process_closure_required"),
        pair.engine.begin() as c,
    ):
        c.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
    repo.confirm(ticket, ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32))
    with pytest.raises(DBAPIError, match="catalog_execution_immutable"), pair.engine.begin() as c:
        c.execute(text("DELETE FROM library.catalog_execution"))
    with pair.engine.begin() as c:
        spacing = c.scalar(text("SELECT next_request_at FROM library.metadata_provider_gate"))
        c.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
        assert c.scalar(text("SELECT count(*) FROM library.catalog_execution")) == 0
        assert (
            c.scalar(
                text("SELECT count(*) FROM account.user_session WHERE user_id=:owner"),
                {"owner": pair.actor.user_id},
            )
            == 0
        )
        assert (
            c.scalar(
                text("SELECT count(*) FROM account.user_account WHERE user_id=:other"),
                {"other": other.user_id},
            )
            == 1
        )
        assert (
            c.scalar(text("SELECT next_request_at FROM library.metadata_provider_gate")) == spacing
        )
