"""Independent Sona serving-purpose consent history remains private after restore."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from autplay.adapters.filesystem.serving_consent_ledger import FilesystemServingConsentLedger
from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.domain.serving_consent import (
    SONA_SERVING_PURPOSE,
    ServingConsentEvidenceError,
    ServingConsentIntent,
)
from autplay.entrypoints import serving_consent_admin
from autplay.runtime.settings import SettingsLoadError, load_worker_settings


def test_r1c_purpose_matches_the_approved_contract() -> None:
    assert SONA_SERVING_PURPOSE == "SONA_R1C_DESCENDANT_MODEL_SERVING_V1"


def _ledger(tmp_path: Path) -> FilesystemServingConsentLedger:
    ledger = FilesystemServingConsentLedger(
        tmp_path / "serving-consent.sqlite3", b"s" * 32, "serving-fixture-v1"
    )
    ledger.initialize()
    return ledger


def _grant(ledger: FilesystemServingConsentLedger) -> ServingConsentIntent:
    return ServingConsentIntent(
        ledger.owner_tag(uuid4()),
        uuid4(),
        ledger.actor_tag(uuid4()),
        "a" * 64,
        "b" * 64,
        "GRANTED",
        1,
        datetime.now(UTC),
    )


def test_provisioning_is_explicit_and_domains_are_separate(tmp_path: Path) -> None:
    path = tmp_path / "serving-consent.sqlite3"
    ledger = FilesystemServingConsentLedger(path, b"s" * 32, "serving-fixture-v1")
    with pytest.raises(ServingConsentEvidenceError):
        ledger.read()
    assert not path.exists()
    ledger.initialize()
    with pytest.raises(ServingConsentEvidenceError):
        ledger.initialize()
    user_id = uuid4()
    training = FilesystemTrainingConsentLedger(
        tmp_path / "training-consent.sqlite3", b"s" * 32, "training-fixture-v1"
    )
    training.initialize()
    assert ledger.owner_tag(user_id) != training.owner_tag(user_id)
    with pytest.raises(ServingConsentEvidenceError):
        FilesystemServingConsentLedger(path, b"x" * 32, "serving-fixture-v1").read()


def test_monotonic_purpose_generation_and_operation_idempotency(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    granted = _grant(ledger)
    assert ledger.record(granted) == ledger.record(granted)
    key = (granted.owner_tag, SONA_SERVING_PURPOSE)
    withdrawn = replace(granted, operation_id=uuid4(), decision="WITHDRAWN", generation=2)
    ledger.record(withdrawn)
    assert ledger.read().latest[key] == withdrawn
    with pytest.raises(ServingConsentEvidenceError):
        ledger.record(replace(granted, operation_id=uuid4()))
    with pytest.raises(ServingConsentEvidenceError):
        ledger.record(replace(granted, decision="DENIED"))
    with pytest.raises(ServingConsentEvidenceError):
        ledger.record(replace(withdrawn, operation_id=uuid4(), purpose="WRONG", generation=3))
    assert len(ledger.read().operations) == 2


@pytest.mark.parametrize("fault", ["event", "tail", "head", "identity"])
def test_tampered_or_truncated_history_fails_closed(tmp_path: Path, fault: str) -> None:
    ledger = _ledger(tmp_path)
    ledger.record(_grant(ledger))
    with sqlite3.connect(ledger.path) as connection:
        if fault == "event":
            connection.execute("DROP TRIGGER immutable_event_update")
            connection.execute("UPDATE event SET document=replace(document,'GRANTED','DENIED')")
        elif fault == "tail":
            connection.execute("DROP TRIGGER immutable_event_delete")
            connection.execute("DELETE FROM event")
        elif fault == "head":
            connection.execute("UPDATE head SET sequence=0,digest=?", ("0" * 64,))
        else:
            connection.execute("DROP TRIGGER immutable_identity_update")
            connection.execute("UPDATE identity SET key_id='wrong'")
    with pytest.raises(ServingConsentEvidenceError):
        ledger.read()


def test_concurrent_owner_intents_have_one_global_order(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    grants = [_grant(ledger) for _ in range(8)]

    def write(item: ServingConsentIntent) -> None:
        FilesystemServingConsentLedger(ledger.path, b"s" * 32, "serving-fixture-v1").record(item)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, grants))
    history = ledger.read()
    assert len(history.operations) == len(history.latest) == 8
    assert all(history.latest[(item.owner_tag, item.purpose)] == item for item in grants)


def test_explicit_settings_require_a_distinct_file_and_key(tmp_path: Path) -> None:
    base = {
        "database_url": "postgresql+psycopg://autplay:fixture@127.0.0.1:5432/autplay",
        "serving_consent_ledger_path": tmp_path / "serving.sqlite3",
        "serving_consent_ledger_key": SecretStr("s" * 32),
        "serving_consent_ledger_key_id": "serving-fixture",
    }
    settings = load_worker_settings(overrides=base, environ={})
    ledger = serving_consent_admin.build_serving_consent_ledger(settings)
    assert ledger is not None
    ledger.initialize()
    assert ledger.read().operations == {}
    with pytest.raises(SettingsLoadError):
        load_worker_settings(
            overrides={
                **base,
                "training_consent_ledger_path": tmp_path / "serving.sqlite3",
                "training_consent_ledger_key": SecretStr("t" * 32),
                "training_consent_ledger_key_id": "training-fixture",
            },
            environ={},
        )
    with pytest.raises(SettingsLoadError):
        load_worker_settings(
            overrides={
                **base,
                "training_consent_ledger_path": tmp_path / "training.sqlite3",
                "training_consent_ledger_key": SecretStr("s" * 32),
                "training_consent_ledger_key_id": "training-fixture",
            },
            environ={},
        )


def test_admin_provisioning_and_head_check_are_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = load_worker_settings(
        overrides={
            "database_url": "postgresql+psycopg://autplay:fixture@127.0.0.1:5432/autplay",
            "serving_consent_ledger_path": tmp_path / "serving.sqlite3",
            "serving_consent_ledger_key": SecretStr("s" * 32),
            "serving_consent_ledger_key_id": "serving-fixture",
        },
        environ={},
    )
    monkeypatch.setattr(serving_consent_admin, "load_worker_settings", lambda: settings)
    assert serving_consent_admin.main(["verify-head"]) == 2
    assert "serving_consent_evidence_unavailable" in capsys.readouterr().out
    assert serving_consent_admin.main(["initialize-ledger"]) == 0
    assert '"initialized":true' in capsys.readouterr().out
    assert serving_consent_admin.main(["verify-head"]) == 0
    assert '"head_valid":true' in capsys.readouterr().out
    assert serving_consent_admin.main(["initialize-ledger"]) == 2
