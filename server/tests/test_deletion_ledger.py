"""Durability, replay, independent restore evidence and corruption refusal."""

import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.domain.privacy_deletion import DeletionEvidenceError

_KEY = b"deletion-ledger-fixture-only-secret-32bytes"


@pytest.fixture
def ledger(tmp_path: Path) -> FilesystemDeletionLedger:
    result = FilesystemDeletionLedger(tmp_path / "independent.sqlite3", _KEY, "fixture-v1")
    result.initialize()
    return result


def test_restart_replay_and_completion_retain_only_keyed_owner_evidence(
    ledger: FilesystemDeletionLedger,
) -> None:
    owner, operation = uuid4(), uuid4()
    now = datetime.now(UTC)
    prepared = ledger.prepare(owner, operation, now)
    assert prepared.protect_until is None and prepared.completed_at is None
    restarted = FilesystemDeletionLedger(ledger.path, _KEY, "fixture-v1")
    assert restarted.prepare(owner, operation, now) == prepared
    complete = restarted.complete(prepared.owner_tag, operation, now + timedelta(seconds=1), 123)
    assert (
        restarted.complete(prepared.owner_tag, operation, now + timedelta(seconds=2), 0) == complete
    )
    assert ledger.read() == (complete,)
    content = ledger.path.read_bytes()
    assert str(owner).encode() not in content and owner.bytes not in content
    assert _KEY not in content
    assert complete.removed_rows == 123 and complete.protect_until is None
    with pytest.raises(DeletionEvidenceError):
        restarted.prepare(owner, uuid4(), now)


def test_independent_processes_serialize_complete_evidence(
    ledger: FilesystemDeletionLedger,
) -> None:
    script = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
ledger = FilesystemDeletionLedger(Path(sys.argv[1]),
    b'deletion-ledger-fixture-only-secret-32bytes', 'fixture-v1')
owner, operation = UUID(sys.argv[2]), UUID(sys.argv[3])
now = datetime.now(UTC)
item = ledger.prepare(owner, operation, now)
ledger.complete(item.owner_tag, operation, now, 1)
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(ledger.path), str(uuid4()), str(uuid4())],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        for child in children:
            output, error = child.communicate(timeout=30)
            assert child.returncode == 0, error
            assert output == ""
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
    evidence = ledger.read()
    assert len(evidence) == 2 and all(item.completed_at is not None for item in evidence)


def test_absent_ledger_and_wrong_key_cannot_become_empty_history(
    ledger: FilesystemDeletionLedger, tmp_path: Path
) -> None:
    for candidate in (
        FilesystemDeletionLedger(tmp_path / "absent.sqlite3", _KEY, "fixture-v1"),
        FilesystemDeletionLedger(ledger.path, b"x" * 32, "fixture-v1"),
        FilesystemDeletionLedger(ledger.path, _KEY, "other-key-id"),
    ):
        with pytest.raises(DeletionEvidenceError):
            candidate.read()
    with pytest.raises(DeletionEvidenceError):
        ledger.initialize()
    assert ledger.read() == ()


def test_concurrent_process_style_connections_do_not_branch_chain(
    ledger: FilesystemDeletionLedger,
) -> None:
    owners = [uuid4() for _ in range(12)]
    now = datetime.now(UTC)

    def write(index: int) -> None:
        independent = FilesystemDeletionLedger(ledger.path, _KEY, "fixture-v1")
        evidence = independent.prepare(owners[index], uuid4(), now)
        independent.complete(evidence.owner_tag, evidence.request_id, now, index)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(len(owners))))
    assert len(ledger.read()) == len(owners)
    assert all(item.completed_at == now for item in ledger.read())


@pytest.mark.parametrize("corruption", ["payload", "tail", "head", "identity"])
def test_corruption_or_missing_signed_tail_fails_closed(
    ledger: FilesystemDeletionLedger, corruption: str
) -> None:
    prepared = ledger.prepare(uuid4(), uuid4(), datetime.now(UTC))
    with sqlite3.connect(ledger.path) as connection:
        if corruption == "payload":
            connection.execute("DROP TRIGGER immutable_event_update")
            connection.execute("UPDATE deletion_event SET document='{}'")
        elif corruption == "tail":
            connection.execute("DROP TRIGGER immutable_event_delete")
            connection.execute("DELETE FROM deletion_event")
        elif corruption == "head":
            connection.execute("UPDATE ledger_head SET sequence=0")
        else:
            connection.execute("DROP TRIGGER immutable_identity_delete")
            connection.execute("DELETE FROM ledger_identity")
    with pytest.raises(DeletionEvidenceError):
        ledger.read()
    with pytest.raises(DeletionEvidenceError):
        ledger.complete(prepared.owner_tag, prepared.request_id, datetime.now(UTC), 1)


def test_failed_completion_is_atomic_and_pending_intent_survives(
    ledger: FilesystemDeletionLedger,
) -> None:
    prepared = ledger.prepare(uuid4(), uuid4(), datetime.now(UTC))
    with pytest.raises(DeletionEvidenceError):
        ledger.complete(
            prepared.owner_tag, prepared.request_id, prepared.accepted_at - timedelta(seconds=1), 2
        )
    assert ledger.read() == (prepared,)
    with pytest.raises(DeletionEvidenceError):
        ledger.complete(prepared.owner_tag, prepared.request_id, prepared.accepted_at, -1)
    assert ledger.read() == (prepared,)
    with sqlite3.connect(ledger.path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM deletion_event")


def test_capacity_reserves_completion_for_every_accepted_intent(
    ledger: FilesystemDeletionLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autplay.adapters.filesystem.deletion_ledger._MAX_EVENTS", 4)
    first = ledger.prepare(uuid4(), uuid4(), datetime.now(UTC))
    second = ledger.prepare(uuid4(), uuid4(), datetime.now(UTC))
    with pytest.raises(DeletionEvidenceError):
        ledger.prepare(uuid4(), uuid4(), datetime.now(UTC))
    for evidence in (first, second):
        ledger.complete(evidence.owner_tag, evidence.request_id, datetime.now(UTC), 1)
    assert all(item.completed_at is not None for item in ledger.read())


def test_ambiguous_commit_is_recovered_by_exact_replay(
    ledger: FilesystemDeletionLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_connect = sqlite3.connect

    class CommitThenThrow(sqlite3.Connection):
        def commit(self) -> None:
            super().commit()
            raise sqlite3.OperationalError("synthetic lost commit response")

    def faulty_connect(database: str, *, uri: bool, timeout: float) -> sqlite3.Connection:
        return original_connect(database, uri=uri, timeout=timeout, factory=CommitThenThrow)

    owner, operation, now = uuid4(), uuid4(), datetime.now(UTC)
    with monkeypatch.context() as fault:
        fault.setattr(sqlite3, "connect", faulty_connect)
        with pytest.raises(DeletionEvidenceError):
            ledger.prepare(owner, operation, now)
    prepared = ledger.prepare(owner, operation, now)
    with monkeypatch.context() as fault:
        fault.setattr(sqlite3, "connect", faulty_connect)
        with pytest.raises(DeletionEvidenceError):
            ledger.complete(prepared.owner_tag, operation, now, 17)
    assert ledger.complete(prepared.owner_tag, operation, now, 17).removed_rows == 17
    with original_connect(ledger.path) as connection:
        assert connection.execute("SELECT count(*) FROM deletion_event").fetchone() == (2,)
