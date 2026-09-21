"""Independent history verifies exact intents, durable ordering and authentic heads."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.domain.training_consent import TrainingConsentEvidenceError, TrainingConsentIntent


def ledger(tmp_path: Path) -> FilesystemTrainingConsentLedger:
    result = FilesystemTrainingConsentLedger(tmp_path / "consent.sqlite3", b"c" * 32, "fixture-v1")
    result.initialize()
    return result


def intent(store: FilesystemTrainingConsentLedger) -> TrainingConsentIntent:
    return TrainingConsentIntent(
        store.owner_tag(uuid4()),
        uuid4(),
        store.actor_tag(uuid4()),
        "a" * 64,
        "b" * 64,
        "GRANTED",
        1,
        datetime.now(UTC),
    )


def test_missing_store_never_initializes_and_existing_provisioning_never_overwrites(
    tmp_path: Path,
) -> None:
    store = FilesystemTrainingConsentLedger(tmp_path / "consent.sqlite3", b"c" * 32, "fixture-v1")
    with pytest.raises(TrainingConsentEvidenceError):
        store.read()
    assert not store.path.exists()
    store.initialize()
    with pytest.raises(TrainingConsentEvidenceError):
        store.initialize()
    assert not store.read().operations


def test_exact_replay_and_independent_sequence_survive_reused_database_revision(
    tmp_path: Path,
) -> None:
    store = ledger(tmp_path)
    first = intent(store)
    assert store.record(first) == store.record(first)
    private = replace(first, operation_id=uuid4(), decision="WITHDRAWN", revision=2)
    store.record(private)
    # A restore may repeat revision 1. Its explicit new grant is ordered by independent sequence.
    new_grant = replace(first, operation_id=uuid4())
    store.record(new_grant)
    assert store.read().latest[first.owner_tag] == new_grant
    assert len(store.read().operations) == 3
    assert store.record(first) == first
    assert store.read().latest[first.owner_tag] == new_grant
    with pytest.raises(TrainingConsentEvidenceError):
        store.record(replace(first, decision="DENIED"))
    wrong_key = FilesystemTrainingConsentLedger(store.path, b"x" * 32, "fixture-v1")
    with pytest.raises(TrainingConsentEvidenceError):
        wrong_key.read()
    wrong_id = FilesystemTrainingConsentLedger(store.path, b"c" * 32, "fixture-v2")
    with pytest.raises(TrainingConsentEvidenceError):
        wrong_id.read()


@pytest.mark.parametrize("fault", ["payload", "tail", "head", "identity"])
def test_tampering_and_truncation_fail_closed(tmp_path: Path, fault: str) -> None:
    store = ledger(tmp_path)
    store.record(intent(store))
    with sqlite3.connect(store.path) as connection:
        if fault == "payload":
            connection.execute("DROP TRIGGER immutable_event_update")
            connection.execute("UPDATE event SET document=replace(document,'GRANTED','DENIED')")
        elif fault == "tail":
            connection.execute("DROP TRIGGER immutable_event_delete")
            connection.execute("DELETE FROM event")
        elif fault == "head":
            connection.execute("UPDATE head SET sequence=0,digest=?", ("0" * 64,))
        else:
            connection.execute("DROP TRIGGER immutable_identity_update")
            connection.execute("UPDATE identity SET key_id='different'")
    with pytest.raises(TrainingConsentEvidenceError):
        store.read()


def test_concurrent_connections_keep_global_sequence_and_owner_isolation(tmp_path: Path) -> None:
    store = ledger(tmp_path)
    intents = [intent(store) for _ in range(12)]

    def write(item: TrainingConsentIntent) -> None:
        FilesystemTrainingConsentLedger(store.path, b"c" * 32, "fixture-v1").record(item)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, intents))
    history = store.read()
    assert len(history.operations) == len(history.latest) == 12
    assert all(history.latest[item.owner_tag] == item for item in intents)


@pytest.mark.parametrize("fault", ["actor", "revision", "digest", "time"])
def test_invalid_append_is_atomic(tmp_path: Path, fault: str) -> None:
    store = ledger(tmp_path)
    item = intent(store)
    if fault == "actor":
        item = replace(item, actor_tag=None)
    elif fault == "revision":
        item = replace(item, revision=True)
    elif fault == "digest":
        item = replace(item, previous_policy_sha256="z" * 64)
    else:
        item = replace(item, changed_at=datetime(2026, 1, 1))
    with pytest.raises(TrainingConsentEvidenceError):
        store.record(item)
    assert not store.read().operations


def test_capacity_reserves_each_granted_owners_future_private_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autplay.adapters.filesystem import training_consent_ledger as adapter

    monkeypatch.setattr(adapter, "_MAX_EVENTS", 4)
    store = ledger(tmp_path)
    first, second = intent(store), intent(store)
    store.record(first)
    store.record(second)
    with pytest.raises(TrainingConsentEvidenceError):
        store.record(intent(store))
    first_private = replace(first, operation_id=uuid4(), decision="WITHDRAWN", revision=2)
    store.record(first_private)
    with pytest.raises(TrainingConsentEvidenceError):
        store.record(replace(first_private, operation_id=uuid4(), revision=3))
    second_private = replace(second, operation_id=uuid4(), decision="WITHDRAWN", revision=2)
    store.record(second_private)
    assert len(store.read().operations) == 4
    assert all(item.decision == "WITHDRAWN" for item in store.read().latest.values())
