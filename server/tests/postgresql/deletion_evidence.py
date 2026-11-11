"""Explicit independent deletion evidence for standalone integration factories."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.postgresql.retained_deletion_reservations import (
    RETAINED_DELETION_RESERVATIONS_INFO_KEY,
    RetainedDeletionReservations,
    configure_retained_deletion_reservations,
)
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

LEDGER_SECRET = "integration-deletion-evidence-secret-32bytes"


class LedgerSettings(TypedDict):
    privacy_ledger_path: Path
    privacy_ledger_key: SecretStr
    privacy_ledger_key_id: str


def ledger_settings(sessions: sessionmaker[Session]) -> LedgerSettings:
    """Let a mounted API use the standalone factory's same initialized evidence."""
    guard = sessions.kw["info"][RETAINED_DELETION_RESERVATIONS_INFO_KEY]
    assert isinstance(guard, RetainedDeletionReservations)
    assert isinstance(guard.ledger, FilesystemDeletionLedger)
    return {
        "privacy_ledger_path": guard.ledger.path,
        "privacy_ledger_key": SecretStr(LEDGER_SECRET),
        "privacy_ledger_key_id": "fixture-v1",
    }


def configured_sessions(engine: Engine, tmp_path: Path) -> sessionmaker[Session]:
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    ledger = FilesystemDeletionLedger(
        tmp_path / f"reservations-{uuid4().hex}.sqlite3",
        LEDGER_SECRET.encode(),
        "fixture-v1",
    )
    ledger.initialize(coverage_started_at=datetime.now(UTC) - timedelta(days=90))
    configure_retained_deletion_reservations(sessions, ledger=ledger, evidence_ready=lambda: True)
    return sessions
