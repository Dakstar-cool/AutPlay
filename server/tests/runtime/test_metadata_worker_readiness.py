"""Metadata health probe must verify policy without claiming a job."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from autplay.adapters.postgresql.readiness import ReadinessResult
from autplay.entrypoints import metadata_worker


@pytest.mark.parametrize("workload_version,expected", [(3, 0), (1, 3)])
def test_readiness_requires_metadata_budget_without_starting_work(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workload_version: int,
    expected: int,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(metadata_worker, "load_worker_settings", lambda: object())
    monkeypatch.setattr(metadata_worker, "create_runtime_engine", lambda *_: engine)
    monkeypatch.setattr(metadata_worker, "enforce_privacy_restore_guard", lambda *_: None)
    monkeypatch.setattr(
        metadata_worker,
        "PostgreSQLReadinessProbe",
        lambda *_: SimpleNamespace(check=lambda: ReadinessResult(True, "postgresql")),
    )
    monkeypatch.setattr(
        metadata_worker,
        "internal_io_policy",
        lambda *_: SimpleNamespace(workload_version=workload_version),
    )
    monkeypatch.setattr(
        metadata_worker,
        "MetadataWorkerRuntime",
        lambda *_args, **_kwargs: pytest.fail("readiness started worker runtime"),
    )

    assert metadata_worker.main(["--check-readiness"]) == expected
    output = capsys.readouterr()
    if expected == 0:
        assert output.out == '{"status":"ready","service":"autplay-worker-metadata"}\n'
    else:
        assert output.err == '{"event": "metadata_budget_unconfigured"}\n'
