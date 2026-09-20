"""Independent early decisions, cancellation reserves and tamper refusal."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.domain.privacy_deletion import DeletionEvidenceError, DeletionRequestEvidence

KEY = b"early-deletion-fixture-key-only-32bytes"


@pytest.fixture
def ledger(tmp_path: Path) -> FilesystemDeletionLedger:
    result = FilesystemDeletionLedger(tmp_path / "evidence.sqlite3", KEY, "fixture-v1")
    result.initialize()
    return result


def attempted(ledger: FilesystemDeletionLedger) -> DeletionRequestEvidence:
    return DeletionRequestEvidence(
        ledger.owner_tag(uuid4()), uuid4(), "a" * 64, "b" * 64, "ATTEMPTED", datetime.now(UTC)
    )


def cancelled(item: DeletionRequestEvidence) -> DeletionRequestEvidence:
    return replace(
        item,
        cancel_operation_id=uuid4(),
        cancel_request_sha256="c" * 64,
        cancelled_at=item.decided_at + timedelta(seconds=1),
    )


def test_exact_replay_and_cancellation_keep_initial_authority_binding(
    ledger: FilesystemDeletionLedger,
) -> None:
    owner = uuid4()
    initial = replace(attempted(ledger), owner_tag=ledger.owner_tag(owner))
    assert ledger.request_record(initial) == initial
    assert ledger.request_record(initial) == initial
    final = ledger.request_record(cancelled(initial))
    assert ledger.request_record(final) == final
    assert ledger.request_read() == (final,) and ledger.read() == ()
    for wrong in (
        replace(initial, decision="SEALED"),
        replace(initial, owner_tag="d" * 64),
        replace(initial, request_sha256="e" * 64),
        replace(initial, receipt_sha256="f" * 64),
        replace(final, cancel_operation_id=uuid4()),
    ):
        with pytest.raises(DeletionEvidenceError):
            ledger.request_record(wrong)
    assert ledger.request_read() == (final,)
    contents = ledger.path.read_bytes()
    assert (
        owner.bytes not in contents and str(owner).encode() not in contents and KEY not in contents
    )


def test_sealed_decision_never_becomes_acceptance_or_cancellation(
    ledger: FilesystemDeletionLedger,
) -> None:
    sealed = replace(attempted(ledger), decision="SEALED")
    ledger.request_record(sealed)
    for wrong in (replace(sealed, decision="ATTEMPTED"), cancelled(sealed)):
        with pytest.raises(DeletionEvidenceError):
            ledger.request_record(wrong)
    assert ledger.request_read() == (sealed,)


def test_capacity_reserves_cancellation_before_allowing_more_negative_decisions(
    ledger: FilesystemDeletionLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autplay.adapters.filesystem.deletion_ledger._MAX_EVENTS", 4)
    first, second = attempted(ledger), attempted(ledger)
    ledger.request_record(first)
    ledger.request_record(second)
    with pytest.raises(DeletionEvidenceError):
        ledger.request_record(replace(attempted(ledger), decision="SEALED"))
    ledger.request_record(cancelled(first))
    ledger.request_record(cancelled(second))
    assert all(item.cancel_operation_id is not None for item in ledger.request_read())


@pytest.mark.parametrize("corruption", ["payload", "tail", "head", "identity", "missing-chain"])
def test_both_purge_and_request_ports_validate_early_signed_head(
    ledger: FilesystemDeletionLedger,
    corruption: str,
) -> None:
    item = ledger.request_record(attempted(ledger))
    purge = ledger.prepare(uuid4(), uuid4(), datetime.now(UTC))
    with sqlite3.connect(ledger.path) as connection:
        if corruption == "payload":
            connection.execute("DROP TRIGGER immutable_request_update")
            connection.execute("UPDATE request_event SET document='{}'")
        elif corruption == "tail":
            connection.execute("DROP TRIGGER immutable_request_delete")
            connection.execute("DELETE FROM request_event")
        elif corruption == "head":
            connection.execute("UPDATE request_head SET sequence=0")
        elif corruption == "identity":
            connection.execute("DROP TRIGGER immutable_request_identity_update")
            connection.execute(
                "UPDATE request_identity SET coverage_started_at='2000-01-01T00:00:00.000000+00:00'"
            )
        else:
            connection.execute("DROP TABLE request_event")
            connection.execute("DROP TABLE request_head")
    for action in (
        ledger.read,
        ledger.request_read,
        ledger.request_coverage_started_at,
        lambda: ledger.prepare(uuid4(), uuid4(), datetime.now(UTC)),
        lambda: ledger.complete(purge.owner_tag, purge.request_id, datetime.now(UTC), 1),
        lambda: ledger.request_record(item),
    ):
        with pytest.raises(DeletionEvidenceError):
            action()


def test_independent_connections_cannot_branch_or_reuse_cancel_operation(
    ledger: FilesystemDeletionLedger,
) -> None:
    items = [attempted(ledger) for _ in range(12)]

    def record(index: int) -> None:
        store = FilesystemDeletionLedger(ledger.path, KEY, "fixture-v1")
        store.request_record(items[index])
        store.request_record(cancelled(items[index]))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record, range(len(items))))
    assert len(ledger.request_read()) == 12
    prior = ledger.request_read()[0]
    cancel_operation = prior.cancel_operation_id
    assert cancel_operation is not None
    with pytest.raises(DeletionEvidenceError):
        ledger.request_record(replace(attempted(ledger), request_id=cancel_operation))


def test_lost_external_commit_reply_replays_one_decision(
    ledger: FilesystemDeletionLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = sqlite3.connect

    class CommitThenThrow(sqlite3.Connection):
        def commit(self) -> None:
            super().commit()
            raise sqlite3.OperationalError("fixture lost commit response")

    def faulty(database: str, *, uri: bool, timeout: float) -> sqlite3.Connection:
        return original(database, uri=uri, timeout=timeout, factory=CommitThenThrow)

    item = attempted(ledger)
    with monkeypatch.context() as fault:
        fault.setattr(sqlite3, "connect", faulty)
        with pytest.raises(DeletionEvidenceError):
            ledger.request_record(item)
    assert ledger.request_record(item) == item
    assert ledger.request_read() == (item,)
