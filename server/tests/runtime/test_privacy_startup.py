"""Deletion evidence is checked before any server or worker can resume work."""

from pathlib import Path
from typing import Any

import pytest
from autplay.domain.privacy_deletion import DeletionEvidenceError
from autplay.entrypoints import metadata_worker, music_worker, worker_cpu
from autplay.entrypoints.api import create_app
from autplay.entrypoints.stream import create_stream_app
from autplay.runtime.settings import ApiSettings, RuntimeProfile, StreamSettings, WorkerSettings
from pydantic import SecretStr, ValidationError
from starlette.testclient import TestClient

from .test_stream_api import Auth, Lookup, Storage

DATABASE = "postgresql+psycopg://fixture:private-password@127.0.0.1:1/autplay"
KEY = "independent-fixture-key-of-at-least-32-bytes"


def values(tmp_path: Path) -> dict[str, Any]:
    return {
        "database_url": SecretStr(DATABASE),
        "privacy_ledger_path": tmp_path / "missing.sqlite3",
        "privacy_ledger_key": SecretStr(KEY),
        "privacy_ledger_key_id": "fixture-v1",
    }


def api_values(tmp_path: Path) -> dict[str, Any]:
    return {
        **values(tmp_path),
        "auth_signing_secret": SecretStr("auth-fixture-secret-at-least-32-bytes"),
        "public_access_source_hmac_secret": SecretStr("source-fixture-secret-at-least-32-bytes"),
    }


@pytest.mark.parametrize("kind", ["api", "stream", "worker"])
def test_production_cannot_remove_ledger_configuration(kind: str, tmp_path: Path) -> None:
    data = api_values(tmp_path) if kind == "api" else values(tmp_path)
    if kind == "stream":
        data["auth_signing_secret"] = SecretStr("auth-fixture-secret-at-least-32-bytes")
    for key in ("privacy_ledger_path", "privacy_ledger_key", "privacy_ledger_key_id"):
        del data[key]
    data["profile"] = RuntimeProfile.PRODUCTION
    data.update(
        {
            "profile_api_origin": "https://api.example.test",
            "profile_stream_origin": "https://stream.example.test",
        }
        if kind == "api"
        else {}
    )
    factory = {"api": ApiSettings, "stream": StreamSettings, "worker": WorkerSettings}[kind]
    with pytest.raises(ValidationError, match="independent deletion evidence"):
        factory(**data)


@pytest.mark.parametrize("kind", ["api", "stream"])
def test_missing_evidence_blocks_http_lifespan(kind: str, tmp_path: Path) -> None:
    if kind == "api":
        app = create_app(ApiSettings(**api_values(tmp_path)))
    else:
        data = values(tmp_path)
        data["auth_signing_secret"] = SecretStr("auth-fixture-secret-at-least-32-bytes")
        app = create_stream_app(
            StreamSettings(**data),
            lookup=Lookup(),
            auth_service=Auth(),  # type: ignore[arg-type]
            storage=Storage(),  # type: ignore[arg-type]
        )
    with pytest.raises(DeletionEvidenceError), TestClient(app):
        pytest.fail("HTTP server started without its independent evidence")
    assert not (tmp_path / "missing.sqlite3").exists()


@pytest.mark.parametrize("kind", ["cpu", "metadata", "music"])
def test_missing_evidence_blocks_worker_before_handlers(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("worker constructed handlers before checking deletion evidence")

    if kind == "music":
        settings = ApiSettings(**api_values(tmp_path), internet_music_enabled=True)
        monkeypatch.setattr(music_worker, "load_api_settings", lambda: settings)
        monkeypatch.setattr(music_worker, "build_vault_http_service", forbidden)
        with pytest.raises(DeletionEvidenceError):
            music_worker.main()
    else:
        module = worker_cpu if kind == "cpu" else metadata_worker
        runtime_name = "IngestWorkerRuntime" if kind == "cpu" else "MetadataWorkerRuntime"
        worker_settings = WorkerSettings(**values(tmp_path))
        monkeypatch.setattr(module, "load_worker_settings", lambda: worker_settings)
        monkeypatch.setattr(module, runtime_name, forbidden)
        assert module.main(["--once"]) == 3
        assert "deletion_evidence_unavailable" in capsys.readouterr().err
    assert not (tmp_path / "missing.sqlite3").exists()
