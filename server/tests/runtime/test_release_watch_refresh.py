"""CPU startup and favorite sweep control-flow regressions without provider I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from autplay.adapters.postgresql.release_watch import FavoriteEvidenceOverflow
from autplay.application.release_watch_refresh import FavoriteSweep, ScheduledFavoriteRefresh
from autplay.entrypoints import privacy_deletion, worker_cpu
from autplay.runtime.settings import WorkerSettings


@pytest.mark.parametrize("once", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_normal_cpu_worker_refreshes_before_dispatch_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, once: bool, enabled: bool
) -> None:
    settings = WorkerSettings(
        database_url=SecretStr("postgresql+psycopg://unused@unused.invalid:5432/unused"),
        vault_root=tmp_path / "vault",
        jamendo_enabled=enabled,
        discovery_automation_enabled=enabled,
        jamendo_client_id=SecretStr("fixture-client") if enabled else None,
        jamendo_staging_root=tmp_path / "staging" if enabled else None,
    )
    monkeypatch.setattr(worker_cpu, "load_worker_settings", lambda: settings)
    monkeypatch.setattr(worker_cpu, "create_runtime_engine", MagicMock())
    monkeypatch.setattr(privacy_deletion, "enforce_privacy_restore_guard", MagicMock())
    monkeypatch.setattr(privacy_deletion, "build_privacy_deletion_service", lambda *_: None)
    probe = MagicMock()
    probe.check.return_value.ready = True
    monkeypatch.setattr(worker_cpu, "PostgreSQLReadinessProbe", lambda *_: probe)
    ingest = MagicMock()
    ingest.shutdown.return_value = ()
    monkeypatch.setattr(worker_cpu, "IngestWorkerRuntime", lambda *_args, **_kwargs: ingest)
    provider = MagicMock()
    provider.shutdown = AsyncMock(return_value=())
    monkeypatch.setattr(worker_cpu, "ResourceIoRuntime", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(worker_cpu, "JamendoProvider", MagicMock())
    for name in (
        "cleanup_expired_pairing_receipts",
        "cleanup_expired_recovery_operations",
        "cleanup_expired_device_admissions",
        "cleanup_expired_public_access",
    ):
        monkeypatch.setattr(worker_cpu, name, lambda *_args, **_kwargs: 0)
    for name in (
        "SqlAlchemyWebAdminRepository",
        "BulkDiscoveryService",
        "SocialService",
    ):
        cleanup = MagicMock()
        cleanup.cleanup_expired.return_value = 0
        cleanup.cleanup.return_value = 0
        monkeypatch.setattr(worker_cpu, name, MagicMock(return_value=cleanup))
    events: list[str] = []
    watch, automation = MagicMock(), MagicMock()
    watch.refresh_due_from_history.side_effect = lambda **_: events.append("refresh")

    def dispatch(**_: object) -> int:
        events.append("dispatch")
        return 0

    automation.dispatch_due.side_effect = dispatch
    automation.cleanup_expired.return_value = 0
    monkeypatch.setattr(worker_cpu, "ReleaseWatchService", MagicMock(return_value=watch))
    monkeypatch.setattr(
        worker_cpu, "DiscoveryAutomationService", MagicMock(return_value=automation)
    )
    worker = MagicMock()
    worker.run_once.side_effect = lambda: events.append("poll")
    monkeypatch.setattr(worker_cpu, "build_cpu_worker", lambda **_: worker)
    if not once:
        # Exercise the callback wired by main; the loop cadence is covered by
        # test_discovery_automation's actual run_cpu_worker one-tick test.
        def run(*_args: object, **kwargs: object) -> None:
            callback = kwargs["discovery_dispatch"]
            assert callable(callback)
            callback()
            worker.run_once()

        monkeypatch.setattr(worker_cpu, "run_cpu_worker", run)
    assert worker_cpu.main(["--once"] if once else []) == 0
    assert events == (["refresh", "dispatch", "poll"] if enabled else ["poll"])
    if enabled:
        watch.refresh_due_from_history.assert_called_once_with(limit=20)
        automation.dispatch_due.assert_called_once()
    else:
        watch.refresh_due_from_history.assert_not_called()
        automation.dispatch_due.assert_not_called()


def test_sweep_rejection_rolls_back_only_evidence_and_commits_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    first, second = UUID(int=1), UUID(int=2)
    sessions = [MagicMock(), MagicMock()]
    for session, owner in zip(sessions, (first, second), strict=True):
        session.__enter__.return_value = session
        session.scalar.side_effect = [now, owner, now]
    repository = MagicMock()
    repository.refresh_favorites.side_effect = [FavoriteEvidenceOverflow("bounded"), None]
    factory = MagicMock(return_value=repository)
    monkeypatch.setattr("autplay.application.release_watch_refresh.ReleaseWatchRepository", factory)
    session_factory = MagicMock(side_effect=sessions)
    sweep = ScheduledFavoriteRefresh(session_factory)
    assert sweep.run(limit=2) == FavoriteSweep(2, 1, 1)
    first_exit = sessions[0].begin_nested.return_value.__exit__
    assert first_exit.call_args.args[0] is FavoriteEvidenceOverflow
    assert sessions[0].begin.return_value.__exit__.call_args.args[0] is None
    assert "INSERT INTO library.favorite_artist_refresh_retry" in str(
        sessions[0].execute.call_args.args[0]
    )
    assert sessions[1].scalar.call_args_list[1].args[1]["attempted"] == [first, second]
    assert repository.refresh_favorites.call_args_list[1].args == (second,)
    assert sessions[1].begin.return_value.__exit__.call_args.args[0] is None


@pytest.mark.parametrize("limit", [0, 21])
def test_sweep_rejects_unbounded_work_before_database_access(limit: int) -> None:
    sessions = MagicMock()
    with pytest.raises(ValueError, match=r"1\.\.20"):
        ScheduledFavoriteRefresh(sessions).run(limit=limit)
    sessions.assert_not_called()


def test_sweep_database_failure_does_not_report_success() -> None:
    sessions = MagicMock()
    sessions.return_value.__enter__.side_effect = OperationalError(
        "fixture", {}, OSError("fixture")
    )
    with pytest.raises(OperationalError):
        ScheduledFavoriteRefresh(sessions).run()
