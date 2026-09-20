"""The optional worker cannot resume restored owner jobs before the deletion gate."""

from pathlib import Path

import pytest
from autplay.adapters.postgresql.readiness import PostgreSQLReadinessProbe
from autplay.runtime.settings import WorkerSettings
from autplay_gpu import entrypoint
from autplay_gpu.devices import NvidiaSmiInventory
from pydantic import SecretStr
from test_devices import _runner


@pytest.mark.parametrize("arguments", [["--once"], ["--serve-sona-shadow"]])
def test_missing_ledger_blocks_gpu_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    selected = NvidiaSmiInventory(runner=_runner).select("auto")
    monkeypatch.setattr(NvidiaSmiInventory, "select", lambda self, selector: selected)
    settings = WorkerSettings(
        database_url=SecretStr("postgresql+psycopg://fixture:secret@127.0.0.1:1/autplay"),
        privacy_ledger_path=tmp_path / "missing.sqlite3",
        privacy_ledger_key=SecretStr("fixture-privacy-key-at-least-32-bytes"),
        privacy_ledger_key_id="v1",
    )
    monkeypatch.setattr(entrypoint, "load_worker_settings", lambda: settings)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("GPU job handlers constructed before deletion evidence was verified")

    monkeypatch.setattr(PostgreSQLReadinessProbe, "check", forbidden)
    assert entrypoint.main(arguments) == 3
    assert "deletion_evidence_unavailable" in capsys.readouterr().err
    assert settings.privacy_ledger_path is not None
    assert not settings.privacy_ledger_path.exists()


@pytest.mark.parametrize("arguments", [["--once"], ["--serve-sona-shadow"]])
def test_missing_consent_ledger_blocks_gpu_before_readiness_and_handlers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    selected = NvidiaSmiInventory(runner=_runner).select("auto")
    monkeypatch.setattr(NvidiaSmiInventory, "select", lambda self, selector: selected)
    settings = WorkerSettings(
        database_url=SecretStr("postgresql+psycopg://fixture:secret@127.0.0.1:1/autplay"),
        training_consent_ledger_path=tmp_path / "missing-consent.sqlite3",
        training_consent_ledger_key=SecretStr("fixture-consent-key-at-least-32-bytes"),
        training_consent_ledger_key_id="v1",
    )
    monkeypatch.setattr(entrypoint, "load_worker_settings", lambda: settings)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("GPU readiness/handlers reached before consent evidence")

    monkeypatch.setattr(PostgreSQLReadinessProbe, "check", forbidden)
    assert entrypoint.main(arguments) == 3
    assert "training_consent_evidence_unavailable" in capsys.readouterr().err
    assert settings.training_consent_ledger_path is not None
    assert not settings.training_consent_ledger_path.exists()
