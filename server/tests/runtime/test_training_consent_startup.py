"""Consent evidence is mandatory before restored HTTP and worker work resumes."""

from pathlib import Path
from typing import Any, cast

import pytest
from autplay.application.auth import AuthService
from autplay.domain.training_consent import TrainingConsentEvidenceError
from autplay.entrypoints import metadata_worker, music_worker, worker_cpu
from autplay.entrypoints.api import create_app
from autplay.entrypoints.stream import create_stream_app
from autplay.ports.vault import VaultStorage
from autplay.runtime.settings import ApiSettings, RuntimeProfile, StreamSettings, WorkerSettings
from pydantic import SecretStr, ValidationError
from starlette.testclient import TestClient

from .test_privacy_startup import api_values
from .test_stream_api import Auth, Lookup, Storage


def values(tmp_path: Path) -> dict[str, Any]:
    return {
        "database_url": SecretStr("postgresql+psycopg://fixture:secret@127.0.0.1:1/autplay"),
        "training_consent_ledger_path": tmp_path / "missing-consent.sqlite3",
        "training_consent_ledger_key": SecretStr("consent-fixture-key-at-least-32-bytes"),
        "training_consent_ledger_key_id": "fixture-v1",
    }


@pytest.mark.parametrize("kind", ["api", "stream", "worker"])
def test_production_cannot_omit_consent_ledger_even_with_feature_off(
    kind: str, tmp_path: Path
) -> None:
    data = api_values(tmp_path)
    data["profile"] = RuntimeProfile.PRODUCTION
    if kind != "api":
        del data["public_access_source_hmac_secret"]
    if kind == "worker":
        del data["auth_signing_secret"]
    factory = {"api": ApiSettings, "stream": StreamSettings, "worker": WorkerSettings}[kind]
    with pytest.raises(ValidationError, match="independent training consent evidence"):
        factory(**data)


@pytest.mark.parametrize("kind", ["api", "stream"])
def test_missing_consent_evidence_blocks_http_before_database(kind: str, tmp_path: Path) -> None:
    data = values(tmp_path)
    data["auth_signing_secret"] = SecretStr("auth-fixture-secret-at-least-32-bytes")
    if kind == "api":
        data["public_access_source_hmac_secret"] = SecretStr(
            "source-fixture-secret-at-least-32-bytes"
        )
        app = create_app(ApiSettings(**data))
    else:
        app = create_stream_app(
            StreamSettings(**data),
            lookup=Lookup(),
            auth_service=cast(AuthService, Auth()),
            storage=cast(VaultStorage, Storage()),
        )
    with pytest.raises(TrainingConsentEvidenceError), TestClient(app):
        pytest.fail("HTTP server started without consent evidence")
    assert not (tmp_path / "missing-consent.sqlite3").exists()


@pytest.mark.parametrize("kind", ["cpu", "metadata", "music"])
def test_missing_consent_evidence_blocks_worker_before_handlers(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("worker constructed handlers before checking consent evidence")

    if kind == "music":
        data = values(tmp_path)
        data["auth_signing_secret"] = SecretStr("auth-fixture-secret-at-least-32-bytes")
        data["public_access_source_hmac_secret"] = SecretStr(
            "source-fixture-secret-at-least-32-bytes"
        )
        settings = ApiSettings(**data, internet_music_enabled=True)
        monkeypatch.setattr(music_worker, "load_api_settings", lambda: settings)
        monkeypatch.setattr(music_worker, "build_vault_http_service", forbidden)
        with pytest.raises(TrainingConsentEvidenceError):
            music_worker.main()
    else:
        module = worker_cpu if kind == "cpu" else metadata_worker
        runtime_name = "IngestWorkerRuntime" if kind == "cpu" else "MetadataWorkerRuntime"
        worker_settings = WorkerSettings(**values(tmp_path))
        monkeypatch.setattr(module, "load_worker_settings", lambda: worker_settings)
        monkeypatch.setattr(module, runtime_name, forbidden)
        assert module.main(["--once"]) == 3
        assert "training_consent_evidence_unavailable" in capsys.readouterr().err
    assert not (tmp_path / "missing-consent.sqlite3").exists()


@pytest.mark.parametrize("fault", ["partial", "vault", "file", "key"])
def test_ledger_configuration_rejects_unsafe_storage_and_key_reuse(
    tmp_path: Path, fault: str
) -> None:
    data = {**api_values(tmp_path), **values(tmp_path)}
    if fault == "partial":
        del data["training_consent_ledger_key"]
    elif fault == "vault":
        data["vault_root"] = tmp_path
    elif fault == "file":
        data["training_consent_ledger_path"] = data["privacy_ledger_path"]
    else:
        data["training_consent_ledger_key"] = data["privacy_ledger_key"]
    with pytest.raises(ValidationError):
        ApiSettings(**data)


def test_explicit_offline_provisioning_never_overwrites_and_missing_runtime_never_creates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autplay.entrypoints import training_consent_restore

    settings = WorkerSettings(**values(tmp_path))
    monkeypatch.setattr(training_consent_restore, "load_worker_settings", lambda: settings)
    assert training_consent_restore.main(["restore-guard"]) == 2
    assert not (tmp_path / "missing-consent.sqlite3").exists()
    assert training_consent_restore.main(["initialize-ledger"]) == 0
    assert training_consent_restore.main(["initialize-ledger"]) == 2
    assert "fixture-key" not in capsys.readouterr().out
